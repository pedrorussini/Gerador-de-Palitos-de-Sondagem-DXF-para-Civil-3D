import streamlit as st
import pandas as pd
import os
import sys
sys.path.insert(0, os.path.dirname(__file__))

try:
    from leitor_sondagem import ler_pdf_sondagem
    from gerar_dxf import gerar_dxf_sondagem, gerar_dxf_multiplas
    MODULOS_OK = True
except ImportError as e:
    MODULOS_OK = False
    _import_erro = str(e)

st.set_page_config(
    page_title="Gerador de Palitos SPT — DXF para Civil 3D",
    page_icon="🗂️",
    layout="centered",
)

st.title("🗂️ Gerador de Palitos de Sondagem SPT")
st.caption("Faça upload dos PDFs de sondagem e baixe os palitos prontos para o Civil 3D.")

if not MODULOS_OK:
    st.error(f"❌ Módulo não encontrado: {_import_erro}")
    st.stop()

st.markdown("---")

pdfs = st.file_uploader(
    "📎 Selecione um ou mais PDFs de sondagem SPT",
    type=["pdf"],
    accept_multiple_files=True,
)

col1, col2 = st.columns(2)
with col1:
    incluir_hachura = st.checkbox("Incluir hachuras de solo", value=True)
with col2:
    espacamento = st.number_input(
        "Espaçamento entre palitos (m)", value=15.0, step=1.0, min_value=5.0,
        help="Distância horizontal entre palitos no DXF quando há múltiplas sondagens"
    )

st.markdown("---")

if not pdfs:
    st.info("⬆️ Faça upload de pelo menos um PDF para continuar.")
    st.stop()

sondagens_raw = []
erros = []

for pdf_file in pdfs:
    try:
        resultado = ler_pdf_sondagem(pdf_file)
        # ler_pdf_sondagem pode retornar objeto único ou lista
        if resultado is None:
            erros.append((pdf_file.name, "Nenhum dado extraído"))
            continue
        if isinstance(resultado, list):
            lista = resultado
        else:
            lista = [resultado]
        for s in lista:
            if hasattr(s, 'metros') and s.metros:
                sondagens_raw.append((pdf_file.name, s))
            else:
                erros.append((pdf_file.name, "Nenhum metro extraído"))
    except Exception as e:
        erros.append((pdf_file.name, str(e)))

for nome, msg in erros:
    st.warning(f"⚠️ {nome}: {msg}")

if not sondagens_raw:
    st.error("❌ Nenhuma sondagem pôde ser lida. Verifique os PDFs enviados.")
    st.stop()

st.subheader(f"✅ {len(sondagens_raw)} sondagem(ns) lida(s) — revise e complete antes de exportar")
st.caption("Os campos em branco não foram encontrados no PDF. Preencha antes de gerar o DXF.")

# ----------------------------------------------------------------
# TELA DE REVISÃO — editável por sondagem
# ----------------------------------------------------------------
sondagens_finais = []
distancias_finais = []

for idx, (nome_pdf, sond) in enumerate(sondagens_raw):
    # Avaliar qualidade da extração
    metros_com_desc = sum(1 for m in sond.metros if m.descricao)
    pct_desc = metros_com_desc / len(sond.metros) * 100 if sond.metros else 0
    if pct_desc >= 80:
        qualidade = "✅ Boa"
    elif pct_desc >= 50:
        qualidade = "⚠️ Parcial"
    else:
        qualidade = "❌ Incompleta"

    with st.expander(
        f"📋 {sond.nome} — {len(sond.metros)} metros | Extração: {qualidade} ({pct_desc:.0f}% com descrição)",
        expanded=True
    ):
        if pct_desc < 80:
            st.warning(
                f"⚠️ Apenas {metros_com_desc} de {len(sond.metros)} metros têm descrição extraída. "
                "Complete os campos em branco na tabela abaixo antes de exportar."
            )

        # Cabeçalho editável
        col_a, col_b, col_c, col_d = st.columns(4)
        nome_edit = col_a.text_input("Identificação", value=sond.nome,
                                      key=f"nome_{idx}")
        cota_edit = col_b.number_input("Cota boca (m)", value=float(sond.cota_boca or 0.0),
                                        step=0.001, format="%.3f", key=f"cota_{idx}")
        dist_edit = col_c.number_input("Distância ao eixo (m)", value=0.0,
                                        step=0.001, format="%.3f", key=f"dist_{idx}")
        na_edit   = col_d.number_input("Nível d'água (m) — 0 = ausente",
                                        value=float(sond.nivel_dagua or 0.0),
                                        step=0.01, format="%.2f", key=f"na_{idx}")

        st.markdown("**Metros extraídos — complete descrição e origem onde estiver em branco:**")

        # Tabela editável de metros
        df_edit = pd.DataFrame([
            {
                "Prof. (m)":   m.prof_m,
                "NSPT":        m.nspt,
                "g1":          m.golpes_1,
                "g2":          m.golpes_2,
                "g3":          m.golpes_3,
                "Descrição":   m.descricao or "",
                "Origem":      m.origem or "",
            }
            for m in sond.metros
        ])

        df_resultado = st.data_editor(
            df_edit,
            use_container_width=True,
            hide_index=True,
            num_rows="dynamic",
            column_config={
                "Prof. (m)": st.column_config.NumberColumn("Prof. (m)", format="%.2f", min_value=0.0),
                "NSPT":      st.column_config.NumberColumn("NSPT", min_value=0, max_value=999),
                "g1":        st.column_config.NumberColumn("g1", min_value=0),
                "g2":        st.column_config.NumberColumn("g2", min_value=0),
                "g3":        st.column_config.NumberColumn("g3", min_value=0),
                "Descrição": st.column_config.TextColumn("Descrição", width="large"),
                "Origem":    st.column_config.TextColumn("Origem", width="small",
                             help="Ex: SRM, SRJ, SS, AT"),
            },
            key=f"editor_{idx}",
        )

        # Reconstruir SondagemSPT com os dados editados
        from leitor_sondagem import SondagemSPT, MetroSPT
        novos_metros = []
        for _, row in df_resultado.iterrows():
            novos_metros.append(MetroSPT(
                prof_m    = float(row["Prof. (m)"]),
                nspt      = int(row["NSPT"]),
                golpes_1  = int(row.get("g1", 0)),
                golpes_2  = int(row.get("g2", 0)),
                golpes_3  = int(row.get("g3", 0)),
                descricao = str(row["Descrição"]),
                origem    = str(row["Origem"]),
            ))

        sond_final = SondagemSPT(
            nome        = nome_edit,
            cota_boca   = cota_edit,
            nivel_dagua = na_edit if na_edit > 0 else None,
            metros      = novos_metros,
        )
        sondagens_finais.append(sond_final)
        distancias_finais.append(dist_edit)

# Renomear para uso no restante do código
sondagens = sondagens_finais
distancias = distancias_finais

st.markdown("---")
st.subheader("⬇️ Exportar DXF")

if len(sondagens) == 1:
    try:
        dxf_bytes = gerar_dxf_sondagem(
            sondagens[0], distancia=0.0, incluir_hachura=incluir_hachura)
        st.download_button(
            label=f"📥 Baixar {sondagens[0].nome}.dxf",
            data=dxf_bytes,
            file_name=f"{sondagens[0].nome}.dxf",
            mime="application/dxf",
            use_container_width=True,
            type="primary",
        )
    except Exception as e:
        st.error(f"❌ Erro ao gerar DXF: {e}")
else:
    col_b1, col_b2 = st.columns(2)
    with col_b1:
        st.markdown("**Arquivo único com todas as sondagens:**")
        try:
            dxf_todos = gerar_dxf_multiplas(
                sondagens, distancias=distancias,
                espacamento_x=float(espacamento),
                incluir_hachura=incluir_hachura,
            )
            st.download_button(
                label="📥 Baixar sondagens_palitos.dxf",
                data=dxf_todos,
                file_name="sondagens_palitos.dxf",
                mime="application/dxf",
                use_container_width=True,
                type="primary",
            )
        except Exception as e:
            st.error(f"❌ Erro: {e}")
    with col_b2:
        st.markdown("**Arquivos individuais:**")
        for sond in sondagens:
            try:
                dxf_bytes = gerar_dxf_sondagem(
                    sond, distancia=0.0, incluir_hachura=incluir_hachura)
                st.download_button(
                    label=f"📥 {sond.nome}.dxf",
                    data=dxf_bytes,
                    file_name=f"{sond.nome}.dxf",
                    mime="application/dxf",
                    use_container_width=True,
                )
            except Exception as e:
                st.error(f"❌ {sond.nome}: {e}")

st.markdown("---")
st.caption(
    "Escala 1:100 | "
    "Layers: SONDAGEM_PALITO · SONDAGEM_NSPT · SONDAGEM_TEXTO · "
    "SONDAGEM_HACHURA · SONDAGEM_NA · SONDAGEM_LIMITE · SONDAGEM_CABECALHO"
)
