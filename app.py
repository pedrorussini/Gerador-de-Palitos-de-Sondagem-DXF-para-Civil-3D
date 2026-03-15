import streamlit as st
import streamlit.components.v1 as components
import pandas as pd
import math
import openseespy.opensees as ops
import pypandoc
import os
import tempfile
import sys
sys.path.insert(0, os.path.dirname(__file__))
try:
    from leitor_sondagem import ler_pdf_sondagem, agrupar_horizontes
    from banco_solos import classificar_solo, ESTADOS_POR_CLASSE, TODOS_ESTADOS, kh_para_classe, estado_para_classe
    from coluna_geologica import gerar_svg as gerar_coluna_svg, gerar_secao_2d, _get_estilo, _abreviar
    from gerar_dxf import gerar_dxf_sondagem, gerar_dxf_multiplas
    MODULOS_OK = True
except ImportError:
    MODULOS_OK = False
    def gerar_coluna_svg(*a, **kw): return ""
    def gerar_dxf_sondagem(*a, **kw): return b""
    def gerar_dxf_multiplas(*a, **kw): return b""

# ==========================================
# SESSION STATE
# ==========================================
if "resultados_calc" not in st.session_state:
    st.session_state["resultados_calc"] = None
if "df_solos_sondagem" not in st.session_state:
    st.session_state["df_solos_sondagem"] = None
if "df_solos_editado" not in st.session_state:
    st.session_state["df_solos_editado"] = None

# ==========================================
# CONFIGURAÇÃO DA PÁGINA
# ==========================================
# ==========================================

# ==========================================
with st.sidebar:
    st.header("1. Geometria e Paramento")
    Espessura_Paramento_h_m = st.number_input("Espessura do Paramento (m)", value=0.15, step=0.01)
    Largura_Placa_bp_m      = st.number_input("Largura da Placa (m)", value=0.30, step=0.05)
    Espacamento_Sh_m        = st.number_input("Espaçamento Sh (m)", value=1.50, step=0.10)
    Espacamento_Sv_m        = st.number_input("Espaçamento Sv (m)", value=1.50, step=0.10)
    Cobrimento_Nominal_cm   = st.number_input("Cobrimento (cm)", value=3.0, step=0.5)

    st.header("2. Propriedades dos Materiais")
    fck_Concreto_MPa = st.number_input("fck do Concreto (MPa)", value=25.0, step=1.0)
    fy_Aco_MPa       = st.number_input("fyk da Tela Soldada (MPa)", value=600.0, step=10.0)

    st.header("3. Grampo e Perfuração")
    Comprimento_Grampo_m      = st.number_input("Comprimento do Grampo L (m)", value=8.0, step=0.5)
    Inclinacao_Grampo_graus   = st.number_input("Inclinação do Grampo α (°)", value=15.0, step=1.0)
    Diametro_Furo_m           = st.number_input("Diâmetro do Furo (m)", value=0.10, step=0.01)
    Diametro_Barra_mm         = st.number_input("Diâmetro da Barra (mm)", value=25.0, step=1.0)
    Aco_fyk_MPa               = st.number_input("fyk da Barra (MPa)", value=500.0, step=10.0)
    Coeficiente_Seguranca_Aco = st.number_input("Coef. Segurança Aço (γs)", value=1.15, step=0.05)

    st.header("4. Fileiras de Grampos")
    Prof_Primeira_Fileira_m = st.number_input(
        "Prof. da 1ª Fileira (m)", value=0.75, step=0.25,
        help="Profundidade do ponto de instalação desde a superfície"
    )
    Numero_Fileiras = st.number_input("Número de Fileiras", value=4, step=1, min_value=1)

    st.header("6. Geometria da Seção")
    Altura_Contencao_m = st.number_input(
        "Altura da contenção H (m)", value=6.0, step=0.5, min_value=1.0,
        help="Altura total da face do paramento"
    )

    # Talude fixo 1H:1V (45°) — não editável pelo usuário (apenas representação visual)
    Talude_H = 1.0
    Talude_V = 1.0

    st.markdown("**Inclinação do paramento**")
    Paramento_incl_H = st.number_input(
        "Paramento H (horiz.)", value=0.0, step=0.1, min_value=0.0,
        help="0 = vertical. Valores positivos inclinam o paramento sobre a escavação."
    )
    Paramento_incl_V = st.number_input(
        "Paramento V (vert.)", value=1.0, step=0.1, min_value=0.1,
        help="Componente vertical da inclinação do paramento"
    )
    _ang_paramento = 90.0 - math.degrees(math.atan2(Paramento_incl_V, Paramento_incl_H)) if Paramento_incl_H > 0 else 90.0
    st.caption(f"Paramento: **{_ang_paramento:.1f}°** da vertical "
               f"({'vertical' if Paramento_incl_H == 0 else f'{Paramento_incl_H:.1f}H:{Paramento_incl_V:.1f}V'})")

    st.header("5. Corrosão (NBR 16920-2)")
    Agressividade_do_Meio = st.selectbox(
        "Agressividade",
        ["Não Agressivo", "Agressivo (PH <= 5 ou Solo Orgânico)"]
    )
    Tipo_de_Solo = st.selectbox(
        "Tipo de Solo",
        ["Solos Naturais Inalterados", "Aterros Compactados", "Aterros Não Compactados"]
    )
    Vida_Util = st.selectbox("Vida Útil", ["50 anos", "25 anos", "5 anos"])

# Banco de Dados de Corrosão (NBR 16920-2)
tabela_corrosao = {
    "Não Agressivo": {
        "Solos Naturais Inalterados":   {"5 anos": 0.00, "25 anos": 0.30, "50 anos": 0.60},
        "Aterros Compactados":          {"5 anos": 0.09, "25 anos": 0.35, "50 anos": 0.60},
        "Aterros Não Compactados":      {"5 anos": 0.18, "25 anos": 0.70, "50 anos": 1.20},
    },
    "Agressivo (PH <= 5 ou Solo Orgânico)": {
        "Solos Naturais Inalterados":   {"5 anos": 0.15, "25 anos": 0.75, "50 anos": 1.50},
        "Aterros Compactados":          {"5 anos": 0.20, "25 anos": 1.00, "50 anos": 1.75},
        "Aterros Não Compactados":      {"5 anos": 0.50, "25 anos": 2.00, "50 anos": 3.25},
    },
}

# ==========================================
# TELA PRINCIPAL — ESTRATIGRAFIA (manual ou via PDF)
# ==========================================
st.subheader("Camadas de Solo (Estratigrafia, NSPT e Kh)")

modo_entrada = st.radio(
    "Como deseja informar a estratigrafia?",
    ["✏️ Preenchimento manual", "📄 Importar de PDF de sondagem SPT"],
    horizontal=True,
    key="modo_entrada_solos",
)

df_padrao = pd.DataFrame([
    {"Classe NBR": "Argilas e siltes argilosos", "Estado NBR": "Rija(o)",
     "Espessura (m)": 4.0, "NSPT Médio": 15, "Kh (kN/m³)": 20000.0, "Origem": "SR"},
    {"Classe NBR": "Areias e siltes arenosos",   "Estado NBR": "Compacta(o)",
     "Espessura (m)": 6.0, "NSPT Médio": 20, "Kh (kN/m³)": 40000.0, "Origem": "SR"},
])

if modo_entrada == "📄 Importar de PDF de sondagem SPT":
    if not MODULOS_OK:
        st.error("❌ Módulos de leitura de PDF não encontrados. "
                 "Verifique se leitor_sondagem.py e banco_solos.py estão no repositório.")
    else:
        col_up1, col_up2 = st.columns([2, 1])
        with col_up1:
            pdf_upload = st.file_uploader(
                "Selecione o PDF de sondagem SPT (digital, texto selecionável):",
                type=["pdf"], key="pdf_sondagem"
            )
        with col_up2:
            offset_cota = st.number_input(
                "Deslocamento de cota (m)",
                value=0.0, step=0.1,
                help="Use quando o topo da contenção não coincide com a boca do furo. "
                     "Positivo = contenção abaixo da boca; Negativo = acima.",
                key="offset_cota_sondagem"
            )

        if pdf_upload is not None:
            with st.spinner("Lendo PDF e classificando horizontes..."):
                with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp_pdf:
                    tmp_pdf.write(pdf_upload.read())
                    tmp_pdf_path = tmp_pdf.name

                try:
                    sondagens = ler_pdf_sondagem(tmp_pdf_path)
                    os.remove(tmp_pdf_path)
                except Exception as e:
                    st.error(f"❌ Erro ao ler PDF: {e}")
                    sondagens = []

            if not sondagens:
                st.error("❌ Nenhuma sondagem SPT encontrada no PDF. "
                         "Verifique se o arquivo é digital (texto selecionável) "
                         "e contém perfil individual de sondagem.")
            else:
                # Seleção da sondagem quando há múltiplas
                nomes = [s.nome for s in sondagens]
                if len(sondagens) > 1:
                    nome_sel = st.selectbox(
                        f"PDF contém {len(sondagens)} sondagem(s). Escolha qual usar:",
                        options=nomes, key="sondagem_sel"
                    )
                    sond_sel = next(s for s in sondagens if s.nome == nome_sel)
                else:
                    sond_sel = sondagens[0]

                st.success(
                    f"✅ Sondagem **{sond_sel.nome}** | "
                    f"Cota da boca: **{sond_sel.cota_boca:.2f} m** | "
                    f"N.A.: **{f'{sond_sel.nivel_dagua:.2f} m' if sond_sel.nivel_dagua else 'Ausente'}** | "
                    f"Profundidade total: **{sond_sel.profundidade_total:.2f} m** | "
                    f"Metros extraídos: **{len(sond_sel.metros)}**"
                )

                # Agrupar em horizontes e classificar
                horizontes = agrupar_horizontes(
                    sond_sel.metros, sond_sel.cota_boca, offset_cota
                )

                linhas = []
                avisos_pdf = []
                tem_nao_identificado = False

                for h in horizontes:
                    classif = classificar_solo(h["descricao"], h["nspt_medio"])

                    if classif["confianca"] == "baixa":
                        tem_nao_identificado = True
                    if classif["aviso"]:
                        avisos_pdf.append(
                            f"Trecho {h['prof_ini_m']:.1f}–{h['prof_fim_m']:.1f} m: "
                            f"{classif['aviso']}"
                        )

                    linhas.append({
                        "Classe NBR":   classif["classe_nbr"],
                        "Estado NBR":   classif["estado"],
                        "Espessura (m)": h["espessura_m"],
                        "NSPT Médio":   h["nspt_medio"],
                        "Kh (kN/m³)":  classif["kh_kNm3"],
                        "Origem":       h["origem"],
                        "Descrição original": h["descricao"],
                        "NSPT min":    h["nspt_min"],
                        "NSPT max":    h["nspt_max"],
                        "_confianca":  classif["confianca"],
                        "_bs_zero":    classif["bs_zero"],
                    })

                df_extraido = pd.DataFrame(linhas)

                # Avisos de qualidade
                for av in avisos_pdf:
                    if "NÃO IDENTIFICADO" in av or "não reconhecida" in av:
                        st.error(f"🔴 {av}")
                    else:
                        st.warning(f"⚠️ {av}")

                if tem_nao_identificado:
                    st.error(
                        "🔴 **Há horizontes não identificados acima.** "
                        "O cálculo está bloqueado até que todos os trechos sejam classificados. "
                        "Edite as células marcadas na tabela abaixo antes de prosseguir."
                    )

                # --- ETAPA 1: Tabela inicial com Classe NBR editável ---
                st.caption(
                    "**Etapa 1:** Revise e corrija a **Classe NBR** de cada horizonte se necessário. "
                    "Depois clique em **🔄 Atualizar estados e Kh** para que o Estado NBR e o Kh "
                    "sejam recalculados automaticamente para cada classe."
                )

                _opcoes_classe = list(ESTADOS_POR_CLASSE.keys())
                _opcoes_origem = ["SS","SRM","SRJ","SR","AT","AL","DFL","SAR","RC","SP","SDL","SM","SO",""]

                colunas_etapa1 = ["Classe NBR", "Espessura (m)", "NSPT Médio", "Origem", "Descrição original"]
                df_etapa1 = df_extraido[colunas_etapa1].copy()

                df_etapa1_editado = st.data_editor(
                    df_etapa1,
                    num_rows="dynamic",
                    use_container_width=True,
                    key="df_solos_etapa1",
                    column_config={
                        "Classe NBR": st.column_config.SelectboxColumn(
                            "Classe NBR",
                            options=_opcoes_classe,
                            required=True,
                            help="Selecione a classe NBR 6502. Após corrigir, clique em Atualizar.",
                        ),
                        "Espessura (m)": st.column_config.NumberColumn(
                            "Espessura (m)", min_value=0.1, step=0.5, format="%.2f"
                        ),
                        "NSPT Médio": st.column_config.NumberColumn(
                            "NSPT Médio", min_value=0, max_value=60, step=1, format="%.1f"
                        ),
                        "Origem": st.column_config.SelectboxColumn(
                            "Origem", options=_opcoes_origem, required=False,
                            help="SS=Solo superficial, SR=Residual, AT=Aterro, AL=Aluvião…",
                        ),
                        "Descrição original": st.column_config.TextColumn(
                            "Descrição (PDF)", disabled=True,
                        ),
                    },
                )

                # --- Botão de atualização ---
                if st.button("🔄 Atualizar estados e Kh", type="secondary"):
                    # Recalcular Estado NBR e Kh para cada linha com base na Classe editada
                    linhas_atualizadas = []
                    for _, row in df_etapa1_editado.iterrows():
                        classe  = row["Classe NBR"]
                        nspt    = float(row["NSPT Médio"] or 0)
                        estado  = estado_para_classe(classe, nspt)
                        kh      = kh_para_classe(classe, nspt)
                        linhas_atualizadas.append({
                            "Classe NBR":    classe,
                            "Estado NBR":    estado,
                            "Espessura (m)": row["Espessura (m)"],
                            "NSPT Médio":    nspt,
                            "Kh (kN/m³)":   kh,
                            "Origem":        row.get("Origem", ""),
                            "Descrição original": row.get("Descrição original", ""),
                        })
                    st.session_state["df_solos_editado"] = pd.DataFrame(linhas_atualizadas)
                    st.success("✅ Estados e Kh atualizados. Revise na tabela abaixo.")

                # --- ETAPA 2: widgets por linha — dropdown condicional real ---
                if st.session_state["df_solos_editado"] is not None:
                    df_etapa2 = st.session_state["df_solos_editado"].copy()

                    st.caption(
                        "**Etapa 2:** Ajuste o **Estado NBR** se necessário — "
                        "as opções são filtradas pela classe de cada horizonte."
                    )

                    # Cabeçalho da grade
                    hdr = st.columns([2.2, 1.8, 1.0, 1.0, 1.4, 1.0, 2.2])
                    for col, label in zip(hdr, [
                        "Classe NBR", "Estado NBR", "Espessura (m)",
                        "NSPT Médio", "Kh (kN/m³)", "Origem", "Descrição (PDF)"
                    ]):
                        col.markdown(f"**{label}**")

                    linhas_etapa2 = []
                    erros_compat  = []

                    for idx, row in df_etapa2.iterrows():
                        classe   = row["Classe NBR"]
                        estados_ok = ESTADOS_POR_CLASSE.get(classe, TODOS_ESTADOS)
                        estado_atual = row["Estado NBR"]
                        # Se o estado atual não é válido para a classe, usar o primeiro válido
                        if estado_atual not in estados_ok:
                            estado_atual = estados_ok[0]

                        cols = st.columns([2.2, 1.8, 1.0, 1.0, 1.4, 1.0, 2.2])

                        # Classe (somente leitura — muda na Etapa 1)
                        cols[0].markdown(
                            f"<small style='color:var(--text-color)'>{classe}</small>",
                            unsafe_allow_html=True
                        )

                        # Estado NBR — dropdown filtrado pela classe desta linha
                        est_sel = cols[1].selectbox(
                            f"est_{idx}", estados_ok,
                            index=estados_ok.index(estado_atual),
                            label_visibility="collapsed",
                            key=f"est_nbr_{idx}",
                        )

                        # Espessura
                        esp_val = cols[2].number_input(
                            f"esp_{idx}", value=float(row["Espessura (m)"]),
                            min_value=0.1, step=0.5, format="%.2f",
                            label_visibility="collapsed", key=f"esp_{idx}",
                        )

                        # NSPT — ao mudar recalcula Kh
                        nspt_val = cols[3].number_input(
                            f"nspt_{idx}", value=float(row["NSPT Médio"]),
                            min_value=0.0, max_value=60.0, step=1.0, format="%.1f",
                            label_visibility="collapsed", key=f"nspt_{idx}",
                        )

                        # Kh — recalculado mas editável
                        kh_recalc = kh_para_classe(classe, nspt_val)
                        kh_val = cols[4].number_input(
                            f"kh_{idx}", value=float(kh_recalc),
                            min_value=0.0, step=100.0, format="%.1f",
                            label_visibility="collapsed", key=f"kh_{idx}",
                        )

                        # Origem
                        orig_atual = str(row.get("Origem", "") or "")
                        if orig_atual not in _opcoes_origem:
                            orig_atual = ""
                        orig_sel = cols[5].selectbox(
                            f"orig_{idx}", _opcoes_origem,
                            index=_opcoes_origem.index(orig_atual),
                            label_visibility="collapsed", key=f"orig_{idx}",
                        )

                        # Descrição (somente leitura)
                        cols[6].markdown(
                            f"<small style='color:gray'>{str(row.get('Descrição original',''))[:45]}</small>",
                            unsafe_allow_html=True
                        )

                        linhas_etapa2.append({
                            "Classe NBR":    classe,
                            "Estado NBR":    est_sel,
                            "Espessura (m)": esp_val,
                            "NSPT Médio":    nspt_val,
                            "Kh (kN/m³)":   kh_val,
                            "Origem":        orig_sel,
                        })

                    # Salvar resultado final
                    tem_nao_id = any(r["Classe NBR"] == "NÃO IDENTIFICADO" for r in linhas_etapa2)
                    if tem_nao_id:
                        st.error("🔴 Ainda há horizontes NÃO IDENTIFICADOS. Corrija na Etapa 1.")
                        st.session_state["df_solos_sondagem"] = None
                    else:
                        st.session_state["df_solos_sondagem"] = pd.DataFrame(linhas_etapa2)
                        st.success("✅ Estratigrafia confirmada — pronta para o cálculo.")

                else:
                    st.info("👆 Revise a Classe NBR acima e clique em **🔄 Atualizar estados e Kh** para continuar.")
                    st.session_state["df_solos_sondagem"] = None

    # Usar df do session_state se disponível, senão padrão
    if st.session_state["df_solos_sondagem"] is not None:
        df_solos = st.session_state["df_solos_sondagem"]
    else:
        df_solos = df_padrao.copy()
        if pdf_upload is None:
            st.info("Faça o upload do PDF acima para importar a estratigrafia automaticamente.")

else:
    # ---- Modo manual — mesma lógica de duas etapas do modo PDF ----
    st.write(
        "Informe as camadas do maciço. O **Kh** é calculado automaticamente pelo banco normativo."
    )

    # Session state para o modo manual
    if "df_manual_editado" not in st.session_state:
        st.session_state["df_manual_editado"] = None

    _opcoes_classe_manual = list(ESTADOS_POR_CLASSE.keys())
    _opcoes_origem_manual = ["SS","SRM","SRJ","SR","AT","AL","DFL","SAR","RC","SP","SDL","SM","SO",""]

    # --- Etapa 1 manual: Classe + NSPT + Espessura + Origem ---
    st.caption(
        "**Etapa 1:** Informe a **Classe NBR**, espessura, NSPT e origem de cada camada. "
        "Depois clique em **🔄 Calcular estados e Kh**."
    )

    df_manual_etapa1 = st.data_editor(
        df_padrao[["Classe NBR", "Espessura (m)", "NSPT Médio", "Origem"]].copy()
        if st.session_state["df_manual_editado"] is None
        else st.session_state["df_manual_editado"][
            ["Classe NBR", "Espessura (m)", "NSPT Médio", "Origem"]
        ].copy(),
        num_rows="dynamic",
        use_container_width=True,
        key="df_manual_etapa1",
        column_config={
            "Classe NBR": st.column_config.SelectboxColumn(
                "Classe NBR",
                options=_opcoes_classe_manual,
                required=True,
                help="Selecione a classe — os estados válidos aparecerão na Etapa 2.",
            ),
            "Espessura (m)": st.column_config.NumberColumn(
                "Espessura (m)", min_value=0.1, step=0.5, format="%.2f"
            ),
            "NSPT Médio": st.column_config.NumberColumn(
                "NSPT Médio", min_value=0, max_value=60, step=1
            ),
            "Origem": st.column_config.SelectboxColumn(
                "Origem", options=_opcoes_origem_manual, required=False,
                help="SS=Solo superficial, SR=Residual, AT=Aterro, AL=Aluvião…",
            ),
        },
    )

    if st.button("🔄 Calcular estados e Kh", type="secondary", key="btn_manual_atualizar"):
        linhas_man = []
        for _, row in df_manual_etapa1.iterrows():
            classe = row["Classe NBR"] or "NÃO IDENTIFICADO"
            nspt   = float(row["NSPT Médio"] or 0)
            linhas_man.append({
                "Classe NBR":    classe,
                "Estado NBR":    estado_para_classe(classe, nspt),
                "Espessura (m)": float(row["Espessura (m)"] or 1.0),
                "NSPT Médio":    nspt,
                "Kh (kN/m³)":   kh_para_classe(classe, nspt),
                "Origem":        row.get("Origem", "") or "",
            })
        st.session_state["df_manual_editado"] = pd.DataFrame(linhas_man)
        st.success("✅ Estados e Kh calculados. Revise abaixo.")

    # --- Etapa 2 manual: dropdown condicional por linha ---
    if st.session_state["df_manual_editado"] is not None:
        df_man2 = st.session_state["df_manual_editado"].copy()

        st.caption(
            "**Etapa 2:** Ajuste o **Estado NBR** se necessário — "
            "opções filtradas pela classe de cada camada."
        )

        hdr_m = st.columns([2.2, 1.8, 1.0, 1.0, 1.4, 1.0])
        for col, label in zip(hdr_m, [
            "Classe NBR", "Estado NBR", "Espessura (m)", "NSPT Médio", "Kh (kN/m³)", "Origem"
        ]):
            col.markdown(f"**{label}**")

        linhas_man2 = []
        for idx, row in df_man2.iterrows():
            classe     = row["Classe NBR"]
            estados_ok = ESTADOS_POR_CLASSE.get(classe, TODOS_ESTADOS)
            estado_cur = row["Estado NBR"] if row["Estado NBR"] in estados_ok else estados_ok[0]

            cols_m = st.columns([2.2, 1.8, 1.0, 1.0, 1.4, 1.0])

            cols_m[0].markdown(
                f"<small style='color:var(--text-color)'>{classe}</small>",
                unsafe_allow_html=True
            )
            est_sel = cols_m[1].selectbox(
                f"m_est_{idx}", estados_ok,
                index=estados_ok.index(estado_cur),
                label_visibility="collapsed", key=f"m_est_{idx}",
            )
            esp_val = cols_m[2].number_input(
                f"m_esp_{idx}", value=float(row["Espessura (m)"]),
                min_value=0.1, step=0.5, format="%.2f",
                label_visibility="collapsed", key=f"m_esp_{idx}",
            )
            nspt_val = cols_m[3].number_input(
                f"m_nspt_{idx}", value=float(row["NSPT Médio"]),
                min_value=0.0, max_value=60.0, step=1.0, format="%.1f",
                label_visibility="collapsed", key=f"m_nspt_{idx}",
            )
            kh_val = cols_m[4].number_input(
                f"m_kh_{idx}", value=float(kh_para_classe(classe, nspt_val)),
                min_value=0.0, step=100.0, format="%.1f",
                label_visibility="collapsed", key=f"m_kh_{idx}",
            )
            orig_cur = str(row.get("Origem", "") or "")
            if orig_cur not in _opcoes_origem_manual:
                orig_cur = ""
            orig_sel = cols_m[5].selectbox(
                f"m_orig_{idx}", _opcoes_origem_manual,
                index=_opcoes_origem_manual.index(orig_cur),
                label_visibility="collapsed", key=f"m_orig_{idx}",
            )
            linhas_man2.append({
                "Classe NBR":    classe,
                "Estado NBR":    est_sel,
                "Espessura (m)": esp_val,
                "NSPT Médio":    nspt_val,
                "Kh (kN/m³)":   kh_val,
                "Origem":        orig_sel,
            })

        df_solos = pd.DataFrame(linhas_man2)
    else:
        st.info("👆 Preencha as camadas acima e clique em **🔄 Calcular estados e Kh** para continuar.")
        df_solos = df_padrao.copy()

# ==========================================
# COLUNA GEOLÓGICA — exibida após confirmar estratigrafia
# ==========================================
if not df_solos.empty and df_solos["Espessura (m)"].sum() > 0:
    st.subheader("🗺️ Seção transversal — perfil e grampos")

    camadas_col = df_solos.to_dict("records")

    # Gerar fileiras a partir dos inputs da sidebar (tempo real)
    _fileiras_col = [
        {"Prof. instalação (m)": round(Prof_Primeira_Fileira_m + k * Espacamento_Sv_m, 3)}
        for k in range(int(Numero_Fileiras))
    ]
    try:
        if not df_fileiras.empty:
            _fileiras_col = [
                {"Prof. instalação (m)": float(_r["Prof. instalação (m)"])}
                for _, _r in df_fileiras.iterrows()
            ]
    except Exception:
        pass

    if MODULOS_OK:
        svg_secao = gerar_secao_2d(
            camadas=camadas_col,
            fileiras=_fileiras_col,
            comprimento_grampo=Comprimento_Grampo_m,
            inclinacao_grampo_graus=Inclinacao_Grampo_graus,
            altura_contencao=Altura_Contencao_m,
            talude_h=Talude_H,
            talude_v=Talude_V,
            paramento_incl_h=Paramento_incl_H,
            paramento_incl_v=Paramento_incl_V,
            largura_svg=680,
            altura_svg=490,
        )
    else:
        svg_secao = gerar_coluna_svg(camadas_col, largura=200, altura_max=460)

    if svg_secao:
        # Usar components.html com key dinâmica baseada em hash dos parâmetros
        # para forçar re-renderização sempre que qualquer parâmetro muda
        import hashlib
        _secao_key = hashlib.md5(
            f"{Paramento_incl_H}{Paramento_incl_V}{Talude_H}{Talude_V}"
            f"{Altura_Contencao_m}{Comprimento_Grampo_m}{Inclinacao_Grampo_graus}"
            f"{str(df_solos.to_dict())}{_fileiras_col}".encode()
        ).hexdigest()[:8]
        # Injetar key no próprio HTML para forçar re-render
        _html_secao = (
            f'<div id="secao_{_secao_key}" style="overflow-x:auto;border-radius:8px;border:1px solid #ddd;padding:4px">'
            f'{svg_secao}</div>'
        )
        components.html(_html_secao, height=510, scrolling=False)

    # Legenda
    st.markdown("**Camadas:**")
    _classes_unicas = list(df_solos["Classe NBR"].unique())
    _leg_cols = st.columns(min(len(_classes_unicas), 3))
    for _i, _cl in enumerate(_classes_unicas):
        _est = _get_estilo(_cl)
        _leg_cols[_i % 3].markdown(
            f'<span style="display:inline-block;width:14px;height:14px;'
            f'background:{_est["fill"]};border:1px solid #888;'
            f'border-radius:2px;margin-right:5px;vertical-align:middle"></span>'
            f'<span style="font-size:12px">{_cl}</span>',
            unsafe_allow_html=True
        )
    st.caption("Hachuras NBR 6502 | Cotas em metros | Grampos desenhados em escala")

# ==========================================
# TABELA DE FILEIRAS (gerada automaticamente, editável)
# ==========================================
st.subheader("Fileiras de Grampos")
st.caption(
    f"Profundidades geradas automaticamente: 1ª fileira a {Prof_Primeira_Fileira_m:.2f} m, "
    f"Sv = {Espacamento_Sv_m:.2f} m. Edite se necessário."
)
prof_auto = [
    round(Prof_Primeira_Fileira_m + k * Espacamento_Sv_m, 3)
    for k in range(int(Numero_Fileiras))
]
df_fil_padrao = pd.DataFrame({
    "Fileira":               [f"F{k+1}" for k in range(int(Numero_Fileiras))],
    "Prof. instalação (m)":  prof_auto,
})
df_fileiras = st.data_editor(df_fil_padrao, num_rows="fixed",
                              use_container_width=True, disabled=["Fileira"])

# ==========================================
# PAINEL DE AVISOS CONTEXTUAIS
# ==========================================
st.markdown("---")

FAIXA_Sh    = (0.8, 2.0)
FAIXA_Sv    = (0.8, 2.0)
FAIXA_h     = (0.10, 0.20)
FAIXA_fck   = (20.0, 35.0)
FAIXA_Kh    = (5000, 80000)

avisos_info   = []
avisos_alerta = []

if not (FAIXA_h[0] <= Espessura_Paramento_h_m <= FAIXA_h[1]):
    avisos_info.append(
        f"**Espessura do paramento ({Espessura_Paramento_h_m*100:.0f} cm)** fora da faixa usual "
        f"({FAIXA_h[0]*100:.0f}–{FAIXA_h[1]*100:.0f} cm). Confronte o MEF com software dedicado."
    )
if not (FAIXA_Sh[0] <= Espacamento_Sh_m <= FAIXA_Sh[1]):
    avisos_info.append(
        f"**Sh = {Espacamento_Sh_m:.2f} m** fora da faixa validada ({FAIXA_Sh[0]:.1f}–{FAIXA_Sh[1]:.1f} m)."
    )
if not (FAIXA_Sv[0] <= Espacamento_Sv_m <= FAIXA_Sv[1]):
    avisos_info.append(
        f"**Sv = {Espacamento_Sv_m:.2f} m** fora da faixa validada ({FAIXA_Sv[0]:.1f}–{FAIXA_Sv[1]:.1f} m)."
    )
razao_sv = Espacamento_Sh_m / Espacamento_Sv_m if Espacamento_Sv_m > 0 else 1.0
if max(razao_sv, 1/razao_sv) > 2.0:
    avisos_info.append(
        f"**Razão Sh/Sv = {razao_sv:.2f}** — painel muito alongado. Marcus perde precisão para λ > 2,0."
    )
if not (FAIXA_fck[0] <= fck_Concreto_MPa <= FAIXA_fck[1]):
    avisos_info.append(
        f"**fck = {fck_Concreto_MPa:.0f} MPa** fora da faixa típica de concreto projetado "
        f"({FAIXA_fck[0]:.0f}–{FAIXA_fck[1]:.0f} MPa)."
    )
if not df_solos.empty and "Kh (kN/m³)" in df_solos.columns:
    for kh in df_solos["Kh (kN/m³)"].dropna():
        if not (FAIXA_Kh[0] <= kh <= FAIXA_Kh[1]):
            avisos_info.append(
                f"**Kh = {kh:,.0f} kN/m³** fora da faixa usual "
                f"({FAIXA_Kh[0]:,}–{FAIXA_Kh[1]:,} kN/m³)."
            )
            break
cobrimento_m = Cobrimento_Nominal_cm / 100.0
if cobrimento_m / Espessura_Paramento_h_m > 0.25:
    avisos_alerta.append(
        f"**Cobrimento ({Cobrimento_Nominal_cm:.1f} cm) = "
        f"{cobrimento_m/Espessura_Paramento_h_m*100:.0f}% da espessura.** "
        "d útil muito reduzido — verifique NBR 14931."
    )
try:
    sin_a_prev = math.sin(math.radians(float(Inclinacao_Grampo_graus)))
    if sin_a_prev > 0 and not df_solos.empty and not df_fileiras.empty:
        prof_max = df_fileiras["Prof. instalação (m)"].max()
        proj_v   = Comprimento_Grampo_m * sin_a_prev
        total_sp = df_solos["Espessura (m)"].sum()
        if prof_max + proj_v > total_sp:
            avisos_alerta.append(
                f"**Grampo pode ultrapassar o perfil informado.** "
                f"Última fileira ({prof_max:.2f} m) + projeção vertical ({proj_v:.2f} m) "
                f"> espessura total do perfil ({total_sp:.2f} m)."
            )
except Exception:
    pass

avisos_mef = (
    "**Hipóteses do modelo MEF-Winkler:** "
    "_(i)_ Molas independentes de Winkler — sem transferência lateral de carga entre pontos adjacentes. "
    "_(ii)_ Grampo simulado como apoio rígido pontual no nó central. "
    "_(iii)_ Kh usado no MEF = menor Kh das camadas perfuradas pela fileira governante "
    "(critério conservador: menor rigidez → maior deflexão → maior momento). "
    f"_(iv)_ Faixas de validade: Sh e Sv entre {FAIXA_Sh[0]:.1f}–{FAIXA_Sh[1]:.1f} m, "
    f"h entre {FAIXA_h[0]*100:.0f}–{FAIXA_h[1]*100:.0f} cm."
)

with st.expander("ℹ️ Avisos e hipóteses do modelo", expanded=bool(avisos_alerta or avisos_info)):
    st.info(avisos_mef)
    for a in avisos_alerta:
        st.error("🔴 " + a)
    for a in avisos_info:
        st.warning("⚠️ " + a)
    if not avisos_alerta and not avisos_info:
        st.success("✅ Parâmetros dentro das faixas validadas (desvio MEF esperado < 15%).")

# ==========================================
# MOTOR DE CÁLCULO
# ==========================================
if st.button("🚀 Processar Cálculo e Gerar Memorial (Word)", type="primary", use_container_width=True):
    with st.spinner("Calculando resistência por fileira e executando MEF..."):

        # ----------------------------------------------------------
        # VALIDAÇÕES
        # ----------------------------------------------------------
        erros = []
        if Cobrimento_Nominal_cm / 100.0 >= Espessura_Paramento_h_m:
            erros.append("Cobrimento nominal deve ser menor que a espessura do paramento.")
        if Diametro_Barra_mm / 1000.0 >= Diametro_Furo_m:
            erros.append("Diâmetro da barra deve ser menor que o diâmetro do furo.")
        if df_solos["NSPT Médio"].isnull().any() or (df_solos["NSPT Médio"] < 0).any():
            erros.append("Todos os valores de NSPT devem ser preenchidos e não-negativos.")
        if df_solos["Kh (kN/m³)"].isnull().any() or (df_solos["Kh (kN/m³)"] <= 0).any():
            erros.append("Todos os valores de Kh devem ser preenchidos e positivos.")
        if df_fileiras["Prof. instalação (m)"].isnull().any():
            erros.append("Todas as fileiras devem ter profundidade de instalação informada.")
        if not (0 < Inclinacao_Grampo_graus < 90):
            erros.append("Inclinação do grampo deve estar entre 0° e 90°.")
        if erros:
            for e in erros:
                st.error("⚠️ " + e)
            st.stop()

        # ----------------------------------------------------------
        # 1. MÓDULO GEOTÉCNICO — bs por camada
        # ----------------------------------------------------------
        FSp   = 2.0
        sin_a = math.sin(math.radians(Inclinacao_Grampo_graus))

        camadas = []
        prof_ac = 0.0
        for _, row in df_solos.iterrows():
            nspt     = float(row["NSPT Médio"])
            esp      = float(row["Espessura (m)"])
            kh_cam   = float(row["Kh (kN/m³)"])
            prof_ini = prof_ac
            prof_fim = prof_ac + esp
            prof_ac  = prof_fim

            qs1 = qsd1 = qs2 = qsd2 = qsd = bs = 0.0
            if nspt > 0:
                qs1  = 50 + 7.5 * nspt
                qsd1 = qs1 / FSp
                qs2  = max((45.12 * math.log(nspt)) - 14.99, 0.0) if nspt > 1 else 0.0
                qsd2 = qs2 / FSp
                qsd  = min(qsd1, qsd2)
                bs   = qsd * math.pi * Diametro_Furo_m
            else:
                st.warning(f"⚠️ Camada {prof_ini:.1f}–{prof_fim:.1f} m: NSPT = 0 → bs = 0.")

            camadas.append({
                "classe": row["Classe NBR"], "estado": row["Estado NBR"],
                "camada": f"{prof_ini:.1f}–{prof_fim:.1f} m",
                "nspt": nspt, "esp": esp,
                "prof_ini": prof_ini, "prof_fim": prof_fim,
                "qs1":  round(qs1,  2), "qs2":  round(qs2,  2),
                "qsd1": round(qsd1, 2), "qsd2": round(qsd2, 2),
                "qsd":  round(qsd,  2), "bs":   round(bs,   2),
                "kh": kh_cam,
            })

        tabela_solos_md  = "| Trecho (m) | Solo (NBR) | NSPT | qsd Ortigão (kPa) | qsd Springer (kPa) | qsd Adotado (kPa) | bs (kN/m) |\n"
        tabela_solos_md += "|:---:|---|:---:|:---:|:---:|:---:|:---:|\n"
        for c in camadas:
            tabela_solos_md += (
                f"| {c['prof_ini']:.1f}–{c['prof_fim']:.1f} | {c['classe']} ({c['estado']}) "
                f"| {c['nspt']:.0f} | {c['qsd1']} | {c['qsd2']} "
                f"| **{c['qsd']}** | **{c['bs']}** |\n"
            )

        # ----------------------------------------------------------
        # 2. MÓDULO DO GRAMPO — R_td e T0 por fileira
        # ----------------------------------------------------------
        t_sacrificio  = tabela_corrosao[Agressividade_do_Meio][Tipo_de_Solo][Vida_Util]
        diam_util_mm  = max(Diametro_Barra_mm - 2 * t_sacrificio, 0.0)
        area_util_mm2 = (math.pi * diam_util_mm ** 2) / 4.0
        Rtd_barra_kN  = (area_util_mm2 * Aco_fyk_MPa) / (Coeficiente_Seguranca_Aco * 1000.0)

        s_max           = max(Espacamento_Sh_m, Espacamento_Sv_m)
        fator_clouterre = max(0.60 + 0.20 * (s_max - 1.0), 0.60)

        def calcular_fileira(z_inst: float) -> dict:
            """
            Calcula R_td e T0 para um grampo instalado na profundidade z_inst.
            O grampo desce com inclinação α ao longo do comprimento L.
            Profundidade vertical final: z_inst + L * sin(α).
            Para cada camada intersectada:
                delta_z = sobreposição vertical entre [z_inst, z_fim] e [prof_ini, prof_fim]
                L_i     = delta_z / sin(α)   (comprimento real no eixo do grampo)
                R_i     = bs_i * L_i          (bs já contém FS_p)
            R_td é limitado pelo menor entre R_arr (arrancamento) e Rtd_barra (ruptura da barra).
            """
            z_fim   = z_inst + Comprimento_Grampo_m * sin_a
            trechos = []
            R_arr   = 0.0

            for c in camadas:
                z0 = max(z_inst, c["prof_ini"])
                z1 = min(z_fim,  c["prof_fim"])
                if z1 <= z0:
                    continue
                L_i   = (z1 - z0) / sin_a
                R_i   = c["bs"] * L_i
                R_arr += R_i
                trechos.append({
                    "camada": f"{c['prof_ini']:.1f}–{c['prof_fim']:.1f} m",
                    "classe": c["classe"],
                    "bs":     c["bs"],
                    "L_i":    round(L_i, 3),
                    "R_i":    round(R_i, 2),
                })

            # T0 baseado na capacidade da barra (Rtadm), conforme FHWA (2003, p.91)
            # R_arr é calculado e registrado para informação, mas não limita T0
            T0_fil = Rtd_barra_kN * fator_clouterre

            return {
                "z_inst":  z_inst,
                "z_fim":   round(z_fim, 3),
                "trechos": trechos,
                "R_arr":   round(R_arr, 2),
                "Rtd":     round(Rtd_barra_kN, 2),  # = Rtadm = Tmax
                "T0":      round(T0_fil, 2),
                "governa_barra": True,  # T0 sempre limitado pela barra
            }

        resultados_fileiras = [
            calcular_fileira(float(r["Prof. instalação (m)"]))
            for _, r in df_fileiras.iterrows()
        ]

        # Fileira governante para o paramento = MAIOR T0
        # (maior força no grampo → maior momento → maior área de aço — critério conservador)
        # As demais fileiras têm T0 individual exibido para uso no Slide2 (Plate Capacity)
        idx_gov = max(range(len(resultados_fileiras)),
                      key=lambda i: resultados_fileiras[i]["T0"])
        fil_gov = resultados_fileiras[idx_gov]
        t0_kN   = fil_gov["T0"]
        rtd_kN  = fil_gov["Rtd"]

        tabela_fileiras_md  = "| Fileira | Prof. inst. (m) | Prof. fim (m) | R_td (kN) | T0 (kN) |\n"
        tabela_fileiras_md += "|:---:|:---:|:---:|:---:|:---:|\n"
        for k, f in enumerate(resultados_fileiras):
            gov  = " **← GOVERNANTE**" if k == idx_gov else ""
            tabela_fileiras_md += (
                f"| F{k+1} | {f['z_inst']:.2f} | {f['z_fim']:.2f} "
                f"| {f['Rtd']} | **{f['T0']}**{gov} |\n"
            )

        # Tabela de trechos da fileira governante
        tabela_trechos_md  = "| Camada | Solo | bs (kN/m) | L_i (m) | R_i (kN) |\n"
        tabela_trechos_md += "|:---:|---|:---:|:---:|:---:|\n"
        for t in fil_gov["trechos"]:
            tabela_trechos_md += (
                f"| {t['camada']} | {t['classe']} | {t['bs']} | {t['L_i']} | {t['R_i']} |\n"
            )
        L_total_gov = sum(t["L_i"] for t in fil_gov["trechos"])
        tabela_trechos_md += f"| **Total** | | | **{L_total_gov:.3f}** | **{fil_gov['R_arr']}** |\n"

        # Kh conservador para MEF = menor Kh das camadas da fileira governante
        kh_gov = [
            c["kh"] for c in camadas
            if min(fil_gov["z_fim"], c["prof_fim"]) > max(fil_gov["z_inst"], c["prof_ini"])
        ]
        Kh_MEF = min(kh_gov) if kh_gov else camadas[0]["kh"]

        # ----------------------------------------------------------
        # 3. MÓDULO DO PARAMENTO — QUATRO MÉTODOS DE FLEXÃO
        # ----------------------------------------------------------
        q_pressao_kNm2 = t0_kN / (Espacamento_Sh_m * Espacamento_Sv_m)
        d_util_m       = Espessura_Paramento_h_m - (Cobrimento_Nominal_cm / 100.0)
        bw_m           = 1.0
        fcd_kN_m2      = (fck_Concreto_MPa / 1.4) * 1000.0
        fyd_kN_m2      = (fy_Aco_MPa * 1000.0) / 1.15
        gamma_f        = 1.4
        As_min_cm2     = 0.0015 * bw_m * Espessura_Paramento_h_m * 10_000.0

        def dim_as(Md):
            Kmd = Md / (bw_m * d_util_m**2 * fcd_kN_m2)
            if Kmd <= 0.259:
                kx = (1 - math.sqrt(1 - 2.36*Kmd)) / 1.18
                z  = d_util_m * (1 - 0.4*kx)
                As = (Md / (z * fyd_kN_m2)) * 10_000.0
                st = "OK"
            else:
                As, st = 999.0, "ERRO – seção insuficiente (Kmd > 0.259)"
            return round(max(As, As_min_cm2), 2), st

        Md_FHWA_ap  = gamma_f * q_pressao_kNm2 * Espacamento_Sh_m * Espacamento_Sv_m / 8.0
        As_FHWA_ap,  st_FHWA_ap  = dim_as(Md_FHWA_ap)

        Md_FHWA_eng = gamma_f * q_pressao_kNm2 * Espacamento_Sh_m * Espacamento_Sv_m / 12.0
        As_FHWA_eng, st_FHWA_eng = dim_as(Md_FHWA_eng)

        Md_Clout    = gamma_f * t0_kN * max(Espacamento_Sh_m, Espacamento_Sv_m) / 8.0
        As_Clout,    st_Clout    = dim_as(Md_Clout)

        lx   = min(Espacamento_Sh_m, Espacamento_Sv_m)
        ly   = max(Espacamento_Sh_m, Espacamento_Sv_m)
        lamb = ly / lx
        lambdas  = [1.0,  1.1,   1.2,   1.3,   1.4,   1.5,   1.75,  2.0]
        alphas_x = [0.0513,0.0581,0.0639,0.0687,0.0726,0.0756,0.0812,0.0829]
        alphas_y = [0.0513,0.0430,0.0365,0.0312,0.0271,0.0238,0.0183,0.0158]

        def interp(xv, xs, ys):
            if xv <= xs[0]:  return ys[0]
            if xv >= xs[-1]: return ys[-1]
            for i in range(len(xs)-1):
                if xs[i] <= xv <= xs[i+1]:
                    t = (xv-xs[i])/(xs[i+1]-xs[i])
                    return ys[i] + t*(ys[i+1]-ys[i])
            return ys[-1]

        ax      = interp(lamb, lambdas, alphas_x)
        ay      = interp(lamb, lambdas, alphas_y)
        Mxd_NBR = gamma_f * ax * q_pressao_kNm2 * lx**2
        Myd_NBR = gamma_f * ay * q_pressao_kNm2 * lx**2
        Md_NBR  = max(Mxd_NBR, Myd_NBR)
        As_NBR,  st_NBR  = dim_as(Md_NBR)

        # MEF Winkler com Kh conservador
        ops.wipe()
        ops.model('basic', '-ndm', 3, '-ndf', 6)
        nx, ny = 15, 15
        dx = Espacamento_Sh_m / nx
        dy = Espacamento_Sv_m / ny
        E_c = 4760.0 * math.sqrt(fck_Concreto_MPa) * 1000.0

        node_tag = 1
        for j in range(ny+1):
            for i in range(nx+1):
                ops.node(node_tag, i*dx - Espacamento_Sh_m/2.0,
                         j*dy - Espacamento_Sv_m/2.0, 0.0)
                ops.fix(node_tag, 0, 0, 0, 0, 0, 1)
                node_tag += 1
        n_nos = node_tag - 1

        mat_base = 50000
        for n in range(1, n_nos+1):
            c = ops.nodeCoord(n)
            ops.node(n+mat_base, c[0], c[1], -0.001)
            ops.fix(n+mat_base, 1, 1, 1, 1, 1, 1)
            ops.uniaxialMaterial('Elastic', n, Kh_MEF*dx*dy)
            ops.element('zeroLength', n+mat_base, n, n+mat_base, '-mat', n, '-dir', 3)

        ops.section('ElasticMembranePlateSection', 1, E_c, 0.2, Espessura_Paramento_h_m, 0.0)

        ele_tag = 1
        for j in range(ny):
            for i in range(nx):
                n1 = j*(nx+1)+i+1
                ops.element('ShellMITC4', ele_tag, n1, n1+1, n1+1+(nx+1), n1+(nx+1), 1)
                ele_tag += 1
        n_eles = ele_tag - 1

        center_node = int((ny//2)*(nx+1)+(nx//2)+1)
        ops.fix(center_node, 1, 1, 1, 0, 0, 0)

        ops.timeSeries('Linear', 1)
        ops.pattern('Plain', 1, 1)
        Fz_int = -q_pressao_kNm2 * dx * dy
        for j in range(ny+1):
            for i in range(nx+1):
                nid = j*(nx+1)+i+1
                fx  = 0.5 if (i == 0 or i == nx) else 1.0
                fy  = 0.5 if (j == 0 or j == ny) else 1.0
                ops.load(nid, 0.0, 0.0, Fz_int*fx*fy, 0.0, 0.0, 0.0)

        ops.system('UmfPack');  ops.numberer('RCM');   ops.constraints('Plain')
        ops.integrator('LoadControl', 1.0);  ops.algorithm('Linear')
        ops.analysis('Static');  ops.analyze(1)

        # Limiar mínimo fisicamente plausível para o momento:
        # Para q = 1 kN/m² e painel de 0.5×0.5 m, Md ≈ q·L²/10 ≈ 0.025 kNm/m
        # Qualquer valor abaixo de 1e-4 indica que a extração não funcionou
        M_min_plausivel = 1e-4  # kNm/m — limiar físico mínimo plausível

        # Tentativa 1: extrair momentos via eleResponse('stresses')
        # ShellMITC4: 4 pontos de integração × 8 componentes = 32 valores
        # [Nxx, Nyy, Nxy, Mxx, Myy, Mxy, Vxz, Vyz] por ponto
        M_max_MEF  = 0.0
        n_momentos = 0
        for i in range(1, n_eles+1):
            s = ops.eleResponse(i, 'stresses')
            if s and len(s) >= 32:
                for pt in range(4):
                    mxx = abs(s[pt*8+3])
                    myy = abs(s[pt*8+4])
                    if mxx > M_min_plausivel or myy > M_min_plausivel:
                        n_momentos += 1
                    M_max_MEF = max(M_max_MEF, mxx, myy)

        if M_max_MEF < M_min_plausivel:
            # Tentativa 2: extrair momentos via eleResponse('forces')
            # ShellMITC4 'forces': 4 nós × 6 DOFs = 24 componentes no sistema local
            # [Fx,Fy,Fz,Mx,My,Mz] por nó → índices de Mx: 3,9,15,21 | My: 4,10,16,22
            # Momento por unidade de comprimento: soma dos momentos nodais / comprimento do lado
            try:
                M_forces = 0.0
                n_forces = 0
                for i in range(1, n_eles+1):
                    f = ops.eleResponse(i, 'forces')
                    if f and len(f) >= 24:
                        # Momentos Mx nos 4 nós (índices 3,9,15,21) divididos pelo lado dy
                        Mx_elem = (abs(f[3]) + abs(f[9]) + abs(f[15]) + abs(f[21])) / (2.0 * dy)
                        # Momentos My nos 4 nós (índices 4,10,16,22) divididos pelo lado dx
                        My_elem = (abs(f[4]) + abs(f[10]) + abs(f[16]) + abs(f[22])) / (2.0 * dx)
                        M_forces = max(M_forces, Mx_elem, My_elem)
                        n_forces += 1

                if M_forces > M_min_plausivel:
                    M_max_MEF  = M_forces
                    n_momentos = n_forces
                    st_MEF_obs = (
                        f"✅ MEF: momentos extraídos via forças nodais equivalentes "
                        f"(eleResponse('stresses') retornou zeros). "
                        f"M_max = {M_max_MEF:.4f} kNm/m."
                    )
                else:
                    raise ValueError("forces também retornou momentos nulos")

            except Exception as _e_forces:
                # Tentativa 3: fallback conservador analítico
                M_max_MEF  = q_pressao_kNm2 * max(Espacamento_Sh_m, Espacamento_Sv_m)**2 / 10.0
                st_MEF_obs = (
                    f"⚠️ MEF: nenhum método de extração retornou momentos válidos. "
                    f"Usando estimativa conservadora q·L²/10 = {M_max_MEF:.4f} kNm/m. "
                    "Resultado menos preciso que os demais métodos."
                )
                st.warning(st_MEF_obs)
        else:
            st_MEF_obs = (
                f"✅ MEF: {n_momentos} pontos de integração com momentos válidos "
                f"(M_max = {M_max_MEF:.4f} kNm/m)."
            )

        Md_MEF = M_max_MEF * gamma_f
        As_MEF, st_MEF = dim_as(Md_MEF)

        # ----------------------------------------------------------
        # 4. PUNÇÃO (NBR 6118)
        # ----------------------------------------------------------
        Fsd       = t0_kN * gamma_f
        u_critico = 4*Largura_Placa_bp_m + 2*math.pi*(2*d_util_m)
        tau_Sd    = Fsd / (u_critico * d_util_m)
        k_scale   = min(1 + math.sqrt(20.0/(d_util_m*100.0)), 2.0)

        # ----------------------------------------------------------
        # 5. SALVAR SESSION STATE
        # ----------------------------------------------------------
        st.session_state["resultados_calc"] = {
            "t0_kN": t0_kN, "rtd_kN": rtd_kN,
            "fator_clouterre": fator_clouterre, "s_max": s_max,
            "t_sacrificio": t_sacrificio, "diam_util_mm": diam_util_mm,
            "area_util_mm2": area_util_mm2, "Rtd_barra_kN": Rtd_barra_kN,
            "resultados_fileiras": resultados_fileiras,
            "idx_gov": idx_gov, "fil_gov": fil_gov,
            "tabela_fileiras_md": tabela_fileiras_md,
            "tabela_trechos_md": tabela_trechos_md,
            "L_total_gov": L_total_gov,
            "Kh_MEF": Kh_MEF,
            "M_max_MEF": M_max_MEF, "Md_MEF": Md_MEF,
            "As_MEF": As_MEF, "st_MEF": st_MEF, "st_MEF_obs": st_MEF_obs,
            "nx": nx, "ny": ny,
            "Md_FHWA_ap": Md_FHWA_ap, "As_FHWA_ap": As_FHWA_ap, "st_FHWA_ap": st_FHWA_ap,
            "Md_FHWA_eng": Md_FHWA_eng, "As_FHWA_eng": As_FHWA_eng, "st_FHWA_eng": st_FHWA_eng,
            "Md_Clout": Md_Clout, "As_Clout": As_Clout, "st_Clout": st_Clout,
            "Md_NBR": Md_NBR, "As_NBR": As_NBR, "st_NBR": st_NBR,
            "Mxd_NBR": Mxd_NBR, "Myd_NBR": Myd_NBR,
            "lx": lx, "ly": ly, "lamb": lamb, "ax": ax, "ay": ay,
            "Fsd": Fsd, "u_critico": u_critico, "tau_Sd": tau_Sd, "k_scale": k_scale,
            "q_pressao_kNm2": q_pressao_kNm2, "d_util_m": d_util_m,
            "bw_m": bw_m, "gamma_f": gamma_f, "FSp": FSp,
            "As_min_cm2": As_min_cm2, "tabela_solos_md": tabela_solos_md,
            # variáveis de geometria do grampo necessárias no template Word
            "sin_a": sin_a,
            "Comprimento_Grampo_m": Comprimento_Grampo_m,
            "Inclinacao_Grampo_graus": Inclinacao_Grampo_graus,
            # dados intermediários para equações do memorial
            "camadas_calc": camadas,
        }
        st.success("✅ Cálculos finalizados!")

# ==========================================
# EXIBIÇÃO E GERAÇÃO DO WORD
# ==========================================
res = st.session_state.get("resultados_calc")

if res:
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("T0 governante (maior)", f"{res['t0_kN']:.1f} kN",
                help=f"Fileira F{res['idx_gov']+1} — maior T0 do conjunto (governa o paramento)")
    col2.metric("Rtadm (barra)", f"{res['rtd_kN']:.1f} kN", help="Resistência admissível da barra = Tensile Capacity no Slide2")
    col3.metric("Kh MEF (conservador)", f"{res['Kh_MEF']:,.0f} kN/m³",
                help="Menor Kh das camadas da fileira governante")
    col4.metric("q no paramento", f"{res['q_pressao_kNm2']:.2f} kN/m²")

    # Tabela de fileiras
    st.subheader("📋 Resistência por Fileira de Grampos")
    dados_fil = {
        "Fileira":         [f"F{k+1}" + (" ★" if k == res["idx_gov"] else "")
                            for k in range(len(res["resultados_fileiras"]))],
        "Prof. inst. (m)": [f["z_inst"] for f in res["resultados_fileiras"]],
        "Prof. fim (m)":   [f["z_fim"]  for f in res["resultados_fileiras"]],
        "R_td (kN)":       [f["Rtd"]    for f in res["resultados_fileiras"]],
        "T0 (kN)":         [f["T0"]     for f in res["resultados_fileiras"]],
    }
    st.dataframe(pd.DataFrame(dados_fil), use_container_width=True, hide_index=True)
    st.caption(f"★ Fileira governante (maior T0 = Plate Capacity). T0 = Rtadm × fator | "
               f"Kh conservador para MEF = {res['Kh_MEF']:,.0f} kN/m³.")

    # Expanders de detalhamento por fileira
    st.markdown("**Detalhamento por fileira** — resistência por camada perfurada:")
    for k, fil in enumerate(res["resultados_fileiras"]):
        gov_label = " ★ Governante" if k == res["idx_gov"] else ""
        with st.expander(
            f"F{k+1}{gov_label}  |  z={fil['z_inst']:.2f}→{fil['z_fim']:.2f} m  "
            f"|  R_arr={fil['R_arr']:.1f} kN  |  T0={fil['T0']:.1f} kN",
            expanded=(k == res["idx_gov"])
        ):
            if fil.get("trechos"):
                dados_trechos = {
                    "Camada":        [t["camada"] for t in fil["trechos"]],
                    "Classe NBR":    [t["classe"]  for t in fil["trechos"]],
                    "L trecho (m)":  [f"{t['L_i']:.3f}" for t in fil["trechos"]],
                    "bs (kN/m)":     [f"{t['bs']:.2f}"  for t in fil["trechos"]],
                    "R_i (kN)":      [f"{t['R_i']:.2f}" for t in fil["trechos"]],
                    "% do total":    [
                        f"{t['R_i']/fil['R_arr']*100:.1f}%" if fil['R_arr'] > 0 else "—"
                        for t in fil["trechos"]
                    ],
                }
                st.dataframe(
                    pd.DataFrame(dados_trechos),
                    use_container_width=True, hide_index=True
                )
                # Barra visual de contribuição por camada
                if fil['R_arr'] > 0:
                    st.markdown("Contribuição relativa:")
                    barra_html = '<div style="display:flex;height:18px;border-radius:4px;overflow:hidden;width:100%">'
                    cores = ["#4A90D9","#E8A838","#5BAD6F","#D95B5B","#9B59B6","#1ABC9C"]
                    for ti, t in enumerate(fil["trechos"]):
                        pct = t['R_i'] / fil['R_arr'] * 100
                        cor = cores[ti % len(cores)]
                        barra_html += (
                            f'<div style="width:{pct:.1f}%;background:{cor};'
                            f'display:flex;align-items:center;justify-content:center;'
                            f'font-size:10px;color:white;white-space:nowrap;overflow:hidden">'
                            f'{t["camada"]}: {pct:.0f}%</div>'
                        )
                    barra_html += '</div>'
                    st.markdown(barra_html, unsafe_allow_html=True)
            else:
                st.info("Detalhamento por trecho não disponível para esta fileira.")

    # Comparativo de flexão
    st.subheader("📊 Comparativo – Dimensionamento à Flexão do Paramento")
    st.caption(f"T0 governante = {res['t0_kN']:.1f} kN | γf = 1,4 | As_min = {res['As_min_cm2']:.2f} cm²/m")

    dados_comp = {
        "Método": ["FHWA (apoiada)", "FHWA (engastada)", "Clouterre",
                   "NBR (Marcus 4 bordos)", "MEF (Winkler)"],
        "Hipótese": [
            "M = q·Sh·Sv / 8",
            "M = q·Sh·Sv / 12",
            "M = T₀·max(Sh,Sv) / 8",
            f"Marcus λ={res['lamb']:.2f}, Mx={res['Mxd_NBR']:.2f}, My={res['Myd_NBR']:.2f} kNm/m",
            f"Mindlin-Reissner, Kh={res['Kh_MEF']:,.0f} kN/m³",
        ],
        "Md (kNm/m)": [round(res["Md_FHWA_ap"],2), round(res["Md_FHWA_eng"],2),
                       round(res["Md_Clout"],2),    round(res["Md_NBR"],2),
                       round(res["Md_MEF"],2)],
        "As (cm²/m)": [res["As_FHWA_ap"], res["As_FHWA_eng"],
                       res["As_Clout"],    res["As_NBR"], res["As_MEF"]],
        "Status":     [res["st_FHWA_ap"], res["st_FHWA_eng"],
                       res["st_Clout"],    res["st_NBR"],  res["st_MEF"]],
    }
    df_comp = pd.DataFrame(dados_comp)
    st.dataframe(df_comp, use_container_width=True, hide_index=True)
    st.info(f"**d útil:** {res['d_util_m']*100:.1f} cm  |  "
            f"**Cobrimento:** {Cobrimento_Nominal_cm:.1f} cm  |  "
            f"**As_min:** {res['As_min_cm2']:.2f} cm²/m")

    if res.get("st_MEF_obs"):
        if res["st_MEF_obs"].startswith("⚠️"):
            st.warning(res["st_MEF_obs"])
        else:
            st.caption(res["st_MEF_obs"])

    # Seleção do método
    st.subheader("📝 Seleção para o Memorial")
    metodo_escolhido = st.radio(
        "Método de flexão a destacar como 'Adotado' no Word:",
        options=["FHWA (apoiada)", "FHWA (engastada)", "Clouterre",
                 "NBR (Marcus 4 bordos)", "MEF (Winkler)"],
        horizontal=True,
        key="metodo_radio",
    )
    mapa = {
        "FHWA (apoiada)":        (res["As_FHWA_ap"],  res["Md_FHWA_ap"]),
        "FHWA (engastada)":      (res["As_FHWA_eng"], res["Md_FHWA_eng"]),
        "Clouterre":             (res["As_Clout"],     res["Md_Clout"]),
        "NBR (Marcus 4 bordos)": (res["As_NBR"],       res["Md_NBR"]),
        "MEF (Winkler)":         (res["As_MEF"],       res["Md_MEF"]),
    }
    As_adotado, Md_adotado = mapa[metodo_escolhido]

    rho_adot     = (As_adotado / 10_000.0) / (res["bw_m"] * res["d_util_m"])
    tau_Rd1_adot = (0.13 * res["k_scale"]
                    * (100.0 * rho_adot * fck_Concreto_MPa)**(1.0/3.0) * 1000.0)
    status_puncao = ("✅ OK – Concreto resiste sem estribos"
                     if res["tau_Sd"] <= tau_Rd1_adot
                     else "❌ FALHA NA TRAÇÃO DIAGONAL – Requer Armadura Transversal")

    st.caption(f"**{metodo_escolhido}** → As = **{As_adotado:.2f} cm²/m** | "
               f"Punção: {status_puncao.split('–')[0].strip()}")

    # ----------------------------------------------------------
    # GERAÇÃO DO WORD
    # ----------------------------------------------------------
    tabela_comp_md  = "| Método | Hipótese | Md (kNm/m) | As (cm²/m) | Status |\n"
    tabela_comp_md += "|---|---|:---:|:---:|---|\n"
    for _, row in df_comp.iterrows():
        dest = " *(ADOTADO)*" if row["Método"] == metodo_escolhido else ""
        tabela_comp_md += (
            f"| **{row['Método']}**{dest} | {row['Hipótese']} "
            f"| {row['Md (kNm/m)']} | **{row['As (cm²/m)']}** | {row['Status']} |\n"
        )

    fil = res["fil_gov"]
    import math as _math

    # Legendas definidas fora da f-string para evitar conflito de escape
    _leg_adesao = r"""
**Legenda das variáveis — Seção 1:**

- $N_{SPT}$ = índice de resistência à penetração (NBR 6484)
- $q_{s1}$ = adesão lateral pelo método de Ortigão (1997), em kPa
- $q_{s2}$ = adesão lateral pelo método de Springer (2006), em kPa
- $q_{sd}$ = adesão lateral de projeto = $\min(q_{s1}; q_{s2}) / FS_p$, em kPa
- $FS_p$ = fator de segurança ao arrancamento (adotado = 2,0)
- $D_{furo}$ = diâmetro do furo de perfuração, em m
- $b_s$ = resistência lateral unitária do grampo = $q_{sd} \times \pi \times D_{furo}$, em kN/m
"""

    _leg_corrosao = r"""
**Legenda das variáveis — Seção 2:**

- $t_s$ = espessura de sacrifício por corrosão conforme NBR 16920-2, em mm
- $\phi_{nom}$ = diâmetro nominal da barra de aço, em mm
- $d_{util}$ = diâmetro útil da barra após descontar a corrosão = $\phi_{nom} - 2t_s$, em mm
- $A_{util}$ = área útil da seção transversal da barra = $\pi \cdot d_{util}^2 / 4$, em mm²
- $f_{yk}$ = resistência característica ao escoamento do aço da barra, em MPa
- $\gamma_s$ = coeficiente de ponderação do aço conforme NBR 6118
- $R_{td,barra}$ = resistência de cálculo à tração da barra = $A_{util} \cdot f_{yk} / (\gamma_s \times 1000)$, em kN
"""

    _leg_fileira = r"""
**Legenda das variáveis — Seção 3:**

- $L$ = comprimento total do grampo, em m
- $\alpha$ = inclinação do grampo em relação à horizontal, em graus
- $R_{adm}$ = resistência admissível da barra = $A_{util} \cdot f_{yk} / (\gamma_s \times 1000)$ = Tensile Capacity no Slide2, em kN
- $T_{max}$ = força máxima na barra = $R_{adm}$, em kN
- $S_{max}$ = maior espaçamento entre grampos ($S_h$ ou $S_v$), em m
- $T_0$ = força máxima atuante no paramento = $T_{max} \times fator$ = Plate Capacity no Slide2, em kN
"""

    _leg_flexao = r"""
**Legenda das variáveis — Seção 4:**

- $q$ = pressão distribuída equivalente no paramento = $T_0 / (S_h \cdot S_v)$, em kN/m²
- $\gamma_f$ = coeficiente de ponderação das ações conforme NBR 6118 (adotado = 1,4)
- $M_d$ = momento fletor de cálculo, em kNm/m
- $h$ = espessura total do paramento, em m
- $d$ = altura útil da seção = $h$ menos cobrimento e raio da barra, em m
- $f_{ck}$ = resistência característica do concreto à compressão, em MPa
- $f_{yk}$ = resistência característica do aço da tela soldada, em MPa
- $K_{md}$ = índice de momento adimensional = $M_d / (f_{ck} \cdot d^2 \cdot 1000)$ — deve ser $\leq 0{,}259$ para seção simples armada
- $z$ = braço de alavanca interno da seção, em m
- $A_s$ = área de armadura necessária, em cm²/m
- $A_{s,min}$ = área mínima de armadura = $0{,}15\% \times b_w \times h$, em cm²/m
"""

    _leg_puncao = r"""
**Legenda das variáveis — Seção 5:**

- $F_{sd}$ = força concentrada de cálculo no ponto de fixação do grampo = $\gamma_f \cdot T_0$, em kN
- $b_p$ = largura da placa de distribuição de carga do grampo, em m
- $d$ = altura útil do paramento, em m
- $u$ = perímetro crítico de punção a $2d$ da face da placa = $4b_p + 2\pi \cdot 2d$, em m
- $\tau_{Sd}$ = tensão de cisalhamento de cálculo no perímetro crítico = $F_{sd} / (u \cdot d)$, em kPa
- $k$ = fator de escala (NBR 6118 equação 19-5) = $1 + \sqrt{200/d_{mm}} \leq 2{,}0$
- $\rho$ = taxa geométrica de armadura longitudinal = $A_{s,adot} / (b_w \cdot d) \leq 0{,}02$
- $\tau_{Rd1}$ = resistência de cálculo ao cisalhamento do concreto sem armadura transversal = $0{,}13 \cdot k \cdot (100\rho f_{ck})^{1/3} \times 1000$, em kPa
"""

    # ----------------------------------------------------------
    # Blocos de cálculo passo a passo
    # ----------------------------------------------------------
    _ts   = res['t_sacrificio']
    _dbar = res['diam_util_mm']
    _Abar = res['area_util_mm2']
    _Rtdb = res['Rtd_barra_kN']
    _dbar_nom = Diametro_Barra_mm

    blocos_corrosao = (
        f"$$t_s = {_ts:.2f} \\text{{ mm}}$$\n\n"
        f"$$d_{{util}} = \\phi_{{nom}} - 2 \\cdot t_s = {_dbar_nom:.1f} - 2 \\times {_ts:.2f} = {_dbar:.2f} \\text{{ mm}}$$\n\n"
        f"$$A_{{util}} = \\frac{{\\pi \\cdot d_{{util}}^2}}{{4}} = \\frac{{\\pi \\times {_dbar:.2f}^2}}{{4}} = {_Abar:.2f} \\text{{ mm}}^2$$\n\n"
        f"$$R_{{td,barra}} = \\frac{{A_{{util}} \\cdot f_{{yk}}}}{{\\gamma_s \\times 1000}} = \\frac{{{_Abar:.2f} \\times {Aco_fyk_MPa:.0f}}}{{{Coeficiente_Seguranca_Aco:.2f} \\times 1000}} = {_Rtdb:.2f} \\text{{ kN}}$$"
    )

    blocos_bs = ""
    for row in res.get("camadas_calc", []):
        N    = row['nspt']
        qs1  = row['qs1'];  qs2  = row['qs2']
        qsd1 = row['qsd1']; qsd2 = row['qsd2']
        qsd  = row['qsd'];  bs   = row['bs']
        fsp  = res['FSp'];  Df   = Diametro_Furo_m
        import math as _math2
        qs2_calc = 45.12 * _math2.log(N) - 14.99 if N > 1 else 0.0
        blocos_bs += (
            f"**Camada {row['camada']} — {row['classe']} ($N_{{SPT}} = {N}$):**\n\n"
            f"$$q_{{s1}} = 50 + 7{{,}}5 \\times N = 50 + 7{{,}}5 \\times {N} = {qs1:.1f} \\text{{ kPa}}$$\n\n"
            f"$$q_{{s2}} = 45{{,}}12 \\times \\ln({N}) - 14{{,}}99 = {qs2:.1f} \\text{{ kPa}}$$\n\n"
            f"$$q_{{sd,1}} = \\frac{{q_{{s1}}}}{{FS_p}} = \\frac{{{qs1:.1f}}}{{{fsp:.1f}}} = {qsd1:.1f} \\text{{ kPa}} \\qquad "
            f"q_{{sd,2}} = \\frac{{q_{{s2}}}}{{FS_p}} = \\frac{{{qs2:.1f}}}{{{fsp:.1f}}} = {qsd2:.1f} \\text{{ kPa}}$$\n\n"
            f"$$q_{{sd}} = \\min({qsd1:.1f}\\,;\\,{qsd2:.1f}) = {qsd:.1f} \\text{{ kPa}}$$\n\n"
            f"$$b_s = q_{{sd}} \\times \\pi \\times D_{{furo}} = {qsd:.1f} \\times \\pi \\times {Df:.2f} = {bs:.2f} \\text{{ kN/m}}$$\n\n"
        )

    _Rarr  = fil['R_arr']
    _Rtd   = res['rtd_kN']
    _fc    = res['fator_clouterre']
    _smax  = res['s_max']
    _T0    = res['t0_kN']

    bloco_Rtd = (
        f"Conforme FHWA (2003, p. 91), a força máxima atuante no paramento ($T_0$) é determinada "
        f"pela resistência admissível da barra ($R_{{adm}}$) e pelo espaçamento entre grampos:\n\n"
        f"$$R_{{adm}} = \\frac{{A_{{util}} \\cdot f_{{yk}}}}{{\\gamma_s \\times 1000}} = "
        f"\\frac{{{_Abar:.2f} \\times {Aco_fyk_MPa:.0f}}}{{{Coeficiente_Seguranca_Aco:.2f} \\times 1000}} "
        f"= {_Rtdb:.2f} \\text{{ kN}} \\quad (\\text{{Tensile Capacity no Slide2}})$$\n\n"
        f"$$T_{{max}} = R_{{adm}} = {_Rtdb:.2f} \\text{{ kN}}$$\n\n"
        f"$$S_{{max}} = \\max(S_h\\,;\\,S_v) = \\max({Espacamento_Sh_m:.2f}\\,;\\,{Espacamento_Sv_m:.2f}) = {_smax:.2f} \\text{{ m}}$$\n\n"
        f"$$\\text{{fator}} = \\max\\left(0{{,}}60\\,;\\,0{{,}}60 + 0{{,}}20 \\times (S_{{max}} - 1{{,}}0)\\right) = {_fc:.3f}$$\n\n"
        f"$$T_0 = T_{{max}} \\times \\text{{fator}} = {_Rtdb:.2f} \\times {_fc:.3f} = {_T0:.2f} \\text{{ kN}} "
        f"\\quad (\\text{{Plate Capacity no Slide2}})$$"
    )

    _q   = res['q_pressao_kNm2']
    _gf  = res['gamma_f']
    _d   = res['d_util_m']
    _h   = Espessura_Paramento_h_m
    _Sh  = Espacamento_Sh_m
    _Sv  = Espacamento_Sv_m
    _fck = fck_Concreto_MPa
    _fyk = fy_Aco_MPa
    _Asmin = res['As_min_cm2']
    _bp    = Largura_Placa_bp_m

    def _bloco_As(Md, As, label):
        import math as _m3
        Kmd = Md / (_fck * (_d**2) * 1000)
        disc = max(0.25 - Kmd/1.134, 0)
        z    = _d * (0.5 + _m3.sqrt(disc))
        return (
            f"$$M_d = {Md:.2f} \\text{{ kNm/m}}$$\n\n"
            f"$$K_{{md}} = \\frac{{M_d}}{{f_{{ck}} \\cdot d^2 \\times 1000}} = \\frac{{{Md:.2f}}}{{{_fck:.1f} \\times {_d:.3f}^2 \\times 1000}} = {Kmd:.4f}$$\n\n"
            f"$$z = d \\left(0{{,}}5 + \\sqrt{{0{{,}}25 - \\frac{{K_{{md}}}}{{1{{,}}134}}}}\\right) = {_d:.3f} \\times \\left(0{{,}}5 + \\sqrt{{0{{,}}25 - \\frac{{{Kmd:.4f}}}{{1{{,}}134}}}}\\right) = {z:.4f} \\text{{ m}}$$\n\n"
            f"$$A_s = \\frac{{M_d \\times 10^4}}{{\\gamma_s \\cdot f_{{yk}} \\cdot z}} = \\frac{{{Md:.2f} \\times 10^4}}{{{Coeficiente_Seguranca_Aco:.2f} \\times {_fyk:.0f} \\times {z:.4f}}} = {As:.2f} \\text{{ cm}}^2\\text{{/m}}$$\n\n"
            f"$$A_{{s,adot}} = \\max({As:.2f}\\,;\\,{_Asmin:.2f}) = {max(As,_Asmin):.2f} \\text{{ cm}}^2\\text{{/m}}$$\n\n"
        )

    _Md_ap=res['Md_FHWA_ap'];  _As_ap=res['As_FHWA_ap']
    _Md_eng=res['Md_FHWA_eng'];_As_eng=res['As_FHWA_eng']
    _Md_cl=res['Md_Clout'];    _As_cl=res['As_Clout']
    _Md_nbr=res['Md_NBR'];     _As_nbr=res['As_NBR']
    _Md_mef=res['Md_MEF'];     _As_mef=res['As_MEF']
    _lx=res['lx']; _ly=res['ly']; _ax=res['ax']; _ay=res['ay']; _lamb=res['lamb']

    blocos_flexao = (
        f"$$q = \\frac{{T_0}}{{S_h \\cdot S_v}} = \\frac{{{_T0:.2f}}}{{{_Sh:.2f} \\times {_Sv:.2f}}} = {_q:.2f} \\text{{ kN/m}}^2$$\n\n"
        f"$$A_{{s,min}} = 0{{,}}15\\% \\times 1{{,}}00 \\times {_h:.2f} = {_Asmin:.2f} \\text{{ cm}}^2\\text{{/m}}$$\n\n"
        "### 4.1 FHWA — Vão simples (momento máximo positivo)\n\n"
        f"$$M_d = \\gamma_f \\cdot q \\cdot S_h \\cdot S_v / 8 = {_gf:.1f} \\times {_q:.2f} \\times {_Sh:.2f} \\times {_Sv:.2f} / 8 = {_Md_ap:.2f} \\text{{ kNm/m}}$$\n\n"
        + _bloco_As(_Md_ap, _As_ap, "Vão simples") +
        "### 4.2 FHWA — Laje contínua (momento nos apoios)\n\n"
        f"$$M_d = \\gamma_f \\cdot q \\cdot S_h \\cdot S_v / 12 = {_gf:.1f} \\times {_q:.2f} \\times {_Sh:.2f} \\times {_Sv:.2f} / 12 = {_Md_eng:.2f} \\text{{ kNm/m}}$$\n\n"
        + _bloco_As(_Md_eng, _As_eng, "Laje contínua") +
        "### 4.3 Clouterre (1991)\n\n"
        f"$$M_d = \\gamma_f \\cdot T_0 \\cdot \\max(S_h\\,;\\,S_v) / 8 = {_gf:.1f} \\times {_T0:.2f} \\times {max(_Sh,_Sv):.2f} / 8 = {_Md_cl:.2f} \\text{{ kNm/m}}$$\n\n"
        + _bloco_As(_Md_cl, _As_cl, "Clouterre") +
        "### 4.4 ABNT NBR 6118 — Método de Marcus\n\n"
        "O **método de Marcus** é um procedimento clássico de dimensionamento de lajes retangulares "
        "biapoiadas nos quatro bordos, aceito pela ABNT NBR 6118 como método simplificado. "
        "Os coeficientes $\\alpha_x$ e $\\alpha_y$ são tabelados em função da relação de aspecto "
        f"$\\lambda = S_h / S_v$. O paramento é tratado como laje com $l_x = S_h = {_Sh:.2f}$ m e $l_y = S_v = {_Sv:.2f}$ m.\n\n"
        f"$$\\lambda = S_h / S_v = {_Sh:.2f} / {_Sv:.2f} = {_lamb:.2f}$$\n\n"
        f"$$\\alpha_x = {_ax:.4f} \\qquad \\alpha_y = {_ay:.4f} \\quad (\\text{{tabelados para }} \\lambda = {_lamb:.2f})$$\n\n"
        f"$$M_{{xd}} = \\alpha_x \\cdot q \\cdot S_h^2 = {_ax:.4f} \\times {_q:.2f} \\times {_lx:.2f}^2 = {res['Mxd_NBR']:.2f} \\text{{ kNm/m}}$$\n\n"
        f"$$M_{{yd}} = \\alpha_y \\cdot q \\cdot S_h^2 = {_ay:.4f} \\times {_q:.2f} \\times {_lx:.2f}^2 = {res['Myd_NBR']:.2f} \\text{{ kNm/m}}$$\n\n"
        f"$$M_d = \\max(M_{{xd}}\\,;\\,M_{{yd}}) = {_Md_nbr:.2f} \\text{{ kNm/m}}$$\n\n"
        + _bloco_As(_Md_nbr, _As_nbr, "Marcus") +
        "### 4.5 MEF-Winkler\n\n"
        f"Modelo: {res['nx']}×{res['ny']} elementos ShellMITC4 | $K_h = {res['Kh_MEF']:,.0f}$ kN/m³ (conservador)\n\n"
        f"Resultado numérico: $M_{{max}} = {_Md_mef:.2f}$ kNm/m\n\n"
        + _bloco_As(_Md_mef, _As_mef, "MEF")
    )

    _rho = (As_adotado / 10_000.0) / (res["bw_m"] * _d)
    _k   = res['k_scale']
    _u   = res['u_critico']
    _Fsd = res['Fsd']
    _tauSd = res['tau_Sd']

    bloco_puncao = (
        f"$$F_{{sd}} = \\gamma_f \\cdot T_0 = {_gf:.1f} \\times {_T0:.2f} = {_Fsd:.2f} \\text{{ kN}}$$\n\n"
        f"$$u = 4 \\cdot b_p + 2\\pi \\cdot 2d = 4 \\times {_bp:.2f} + 2\\pi \\times 2 \\times {_d:.3f} = {_u:.2f} \\text{{ m}}$$\n\n"
        f"$$\\tau_{{Sd}} = \\frac{{F_{{sd}}}}{{u \\cdot d}} = \\frac{{{_Fsd:.2f}}}{{{_u:.2f} \\times {_d:.3f}}} = {_tauSd:.1f} \\text{{ kPa}}$$\n\n"
        f"$$k = 1 + \\sqrt{{\\frac{{200}}{{{_d*1000:.0f}}}}} = {_k:.3f} \\leq 2{{,}}0$$\n\n"
        f"$$\\rho = \\frac{{{As_adotado/10000:.6f}}}{{1{{,}}00 \\times {_d:.3f}}} = {_rho:.5f} \\leq 0{{,}}02$$\n\n"
        f"$$\\tau_{{Rd1}} = 0{{,}}13 \\times {_k:.3f} \\times (100 \\times {_rho:.5f} \\times {_fck:.1f})^{{1/3}} \\times 1000 = {tau_Rd1_adot:.1f} \\text{{ kPa}}$$"
    )

    markdown_texto = f"""
# MEMÓRIA DE CÁLCULO: CONTENÇÃO EM SOLO GRAMPEADO

**Normas de referência:** ABNT NBR 6118:2014 | NBR 16920-2:2022 | FHWA GEC 7 (1998) | Clouterre (1991)

**Gerado via:** Software Interativo — Beta 1.2

---

## 1. Perfil Estratigráfico e Adesão Lateral do Grampo

**Métodos de cálculo:** Ortigão (1997) e Springer (2006) | $FS_p = {res['FSp']:.1f}$ | $D_{{furo}} = {Diametro_Furo_m*100:.1f}$ cm

{_leg_adesao}

### 1.1 Equações de adesão lateral

$$q_{{s1}} = 50 + 7{{,}}5 \\cdot N_{{SPT}} \\quad \\text{{(Ortigão, 1997)}}$$

$$q_{{s2}} = 45{{,}}12 \\cdot \\ln(N_{{SPT}}) - 14{{,}}99 \\quad \\text{{(Springer, 2006)}}$$

$$q_{{sd}} = \\frac{{\\min(q_{{s1}}\\,;\\,q_{{s2}})}}{{FS_p}} \\qquad b_s = q_{{sd}} \\cdot \\pi \\cdot D_{{furo}}$$

### 1.2 Desenvolvimento por camada

{blocos_bs}

| Trecho (m) | Solo (NBR) | NSPT | qsd Ortigão (kPa) | qsd Springer (kPa) | qsd Adotado (kPa) | bs (kN/m) |
|:---:|---|:---:|:---:|:---:|:---:|:---:|
{res['tabela_solos_md'].split(chr(10),2)[2] if chr(10) in res['tabela_solos_md'] else res['tabela_solos_md']}

---

## 2. Corrosão e Resistência da Barra (NBR 16920-2)

**Agressividade:** {Agressividade_do_Meio} | **Solo:** {Tipo_de_Solo} | **Vida útil:** {Vida_Util}

**Barra nominal:** $\\phi = {_dbar_nom:.1f}$ mm | $f_{{yk}} = {Aco_fyk_MPa:.0f}$ MPa | $\\gamma_s = {Coeficiente_Seguranca_Aco:.2f}$

{_leg_corrosao}

{blocos_corrosao}

---

## 3. Resistência por Fileira de Grampos

**Grampo:** $L = {res['Comprimento_Grampo_m']:.2f}$ m | $\\alpha = {res['Inclinacao_Grampo_graus']:.1f}°$

{_leg_fileira}

### 3.1 Resumo por fileira

{res['tabela_fileiras_md']}

### 3.2 Desenvolvimento — Fileira governante F{res['idx_gov']+1} (maior T0 — governa o paramento)

Instalada a $z = {fil['z_inst']:.2f}$ m | Extremidade a $z = {fil['z_fim']:.2f}$ m

{bloco_Rtd}

---

## 4. Dimensionamento do Paramento à Flexão

{_leg_flexao}

{blocos_flexao}

### 4.6 Comparativo dos métodos

{tabela_comp_md}

**Método adotado: {metodo_escolhido}** $\\Rightarrow$ $A_s = $ **{As_adotado:.2f} cm²/m**

---

## 5. Verificação de Punção (NBR 6118 — item 19.5)

**Parâmetros:** $b_p = {_bp:.2f}$ m | $d = {_d*100:.1f}$ cm | $A_{{s,adot}} = {As_adotado:.2f}$ cm²/m | $f_{{ck}} = {_fck:.1f}$ MPa

{_leg_puncao}

{bloco_puncao}

**Resultado:** {status_puncao}
"""

    try:
        with tempfile.NamedTemporaryFile(suffix=".docx", delete=False) as tmp:
            arquivo_docx = tmp.name
        pypandoc.convert_text(markdown_texto, 'docx', format='md',
                              outputfile=arquivo_docx, extra_args=['--mathml'])
        with open(arquivo_docx, "rb") as f:
            docx_bytes = f.read()
        os.remove(arquivo_docx)
        st.download_button(
            label="📄 Baixar Memória de Cálculo (.docx)",
            data=docx_bytes,
            file_name="Memoria_Calculo_Solo_Grampeado.docx",
            mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            use_container_width=True,
        )
    except Exception as e:
        st.error(f"❌ Erro ao gerar o Word. Pandoc instalado? Detalhe: {e}")

# ==========================================
# GERADOR DE PALITOS DXF (Civil 3D)
# ==========================================
st.markdown("---")
st.subheader("🗂️ Gerador de Palitos de Sondagem — DXF para Civil 3D")
st.caption(
    "Faça upload de um ou mais PDFs de sondagem SPT. O app lê os dados automaticamente "
    "e gera um arquivo DXF com os palitos prontos para importar no Civil 3D."
)

if not MODULOS_OK:
    st.error("❌ Módulos necessários não encontrados (leitor_sondagem, gerar_dxf).")
else:
    col_dxf1, col_dxf2 = st.columns([3, 1])
    with col_dxf1:
        pdfs_dxf = st.file_uploader(
            "📎 Selecione os PDFs das sondagens",
            type=["pdf"],
            accept_multiple_files=True,
            key="upload_dxf",
        )
    with col_dxf2:
        incluir_hachura_dxf = st.checkbox("Incluir hachuras de solo", value=True,
                                          key="hachura_dxf")
        espacamento_dxf = st.number_input(
            "Espaçamento entre palitos (m)", value=15.0, step=1.0, min_value=5.0,
            key="esp_dxf",
            help="Distância horizontal entre palitos no DXF (unidades CAD)"
        )

    if pdfs_dxf:
        sondagens_dxf = []
        distancias_dxf = []
        erros_dxf = []

        st.markdown("**Sondagens detectadas — confirme ou ajuste os dados:**")

        for i, pdf_file in enumerate(pdfs_dxf):
            with st.expander(f"📄 {pdf_file.name}", expanded=(i == 0)):
                try:
                    sond = ler_pdf_sondagem(pdf_file)
                    if sond is None:
                        st.error(f"❌ Não foi possível ler {pdf_file.name}")
                        erros_dxf.append(pdf_file.name)
                        continue

                    col_a, col_b, col_c = st.columns(3)
                    nome_edit = col_a.text_input(
                        "Identificação", value=sond.nome,
                        key=f"dxf_nome_{i}"
                    )
                    cota_edit = col_b.number_input(
                        "Altitude (m)", value=float(sond.cota_boca),
                        step=0.001, format="%.3f",
                        key=f"dxf_cota_{i}"
                    )
                    dist_edit = col_c.number_input(
                        "Distância ao eixo (m)", value=0.0,
                        step=0.001, format="%.3f",
                        key=f"dxf_dist_{i}"
                    )

                    na_edit = st.number_input(
                        "Nível d'água (m) — deixe 0 se não houver",
                        value=float(sond.nivel_dagua) if sond.nivel_dagua else 0.0,
                        step=0.01, format="%.2f",
                        key=f"dxf_na_{i}"
                    )

                    # Preview dos metros extraídos
                    if sond.metros:
                        df_prev = pd.DataFrame([
                            {"Prof (m)": m.prof_m, "NSPT": m.nspt,
                             "Descrição": m.descricao[:40] if m.descricao else "",
                             "Origem": m.origem}
                            for m in sond.metros
                        ])
                        st.dataframe(df_prev, use_container_width=True,
                                     hide_index=True, height=200)
                        st.caption(f"Total: {len(sond.metros)} metros | "
                                   f"Prof. total: {sond.profundidade_total:.1f} m")

                    # Atualizar com valores editados
                    sond.nome        = nome_edit
                    sond.cota_boca   = cota_edit
                    sond.nivel_dagua = na_edit if na_edit > 0 else None

                    sondagens_dxf.append(sond)
                    distancias_dxf.append(dist_edit)

                except Exception as e:
                    st.error(f"❌ Erro ao processar {pdf_file.name}: {e}")
                    erros_dxf.append(pdf_file.name)

        if sondagens_dxf:
            st.markdown(f"**{len(sondagens_dxf)} sondagem(ns) prontas para exportar.**")

            col_btn1, col_btn2 = st.columns(2)

            # Botão: DXF individual (uma por arquivo)
            with col_btn1:
                if st.button("⬇️ Baixar DXF individual (uma por arquivo)",
                             use_container_width=True):
                    for sond, dist in zip(sondagens_dxf, distancias_dxf):
                        try:
                            dxf_bytes = gerar_dxf_sondagem(
                                sond, distancia=dist,
                                incluir_hachura=incluir_hachura_dxf
                            )
                            st.download_button(
                                label=f"📥 {sond.nome}.dxf",
                                data=dxf_bytes,
                                file_name=f"{sond.nome}.dxf",
                                mime="application/dxf",
                                key=f"dl_dxf_{sond.nome}",
                            )
                        except Exception as e:
                            st.error(f"Erro ao gerar DXF de {sond.nome}: {e}")

            # Botão: DXF único com todas as sondagens lado a lado
            with col_btn2:
                try:
                    dxf_todos = gerar_dxf_multiplas(
                        sondagens_dxf,
                        distancias=distancias_dxf,
                        espacamento_x=float(espacamento_dxf),
                        incluir_hachura=incluir_hachura_dxf,
                    )
                    st.download_button(
                        label="📥 Baixar DXF com todas as sondagens",
                        data=dxf_todos,
                        file_name="sondagens_palitos.dxf",
                        mime="application/dxf",
                        use_container_width=True,
                        type="primary",
                    )
                except Exception as e:
                    st.error(f"Erro ao gerar DXF conjunto: {e}")

        if erros_dxf:
            st.warning(f"⚠️ Não foi possível processar: {', '.join(erros_dxf)}")
