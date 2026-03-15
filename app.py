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

sondagens = []
distancias = []
erros = []

for pdf_file in pdfs:
    try:
        sond = ler_pdf_sondagem(pdf_file)
        if sond is None or not sond.metros:
            erros.append((pdf_file.name, "Nenhum dado extraído"))
            continue
        sondagens.append(sond)
        distancias.append(0.0)
    except Exception as e:
        erros.append((pdf_file.name, str(e)))

for nome, msg in erros:
    st.warning(f"⚠️ {nome}: {msg}")

if not sondagens:
    st.error("❌ Nenhuma sondagem pôde ser lida. Verifique os PDFs enviados.")
    st.stop()

st.subheader(f"✅ {len(sondagens)} sondagem(ns) lida(s)")

for sond in sondagens:
    with st.expander(f"📋 {sond.nome} — {sond.profundidade_total:.1f} m", expanded=False):
        col_a, col_b, col_c = st.columns(3)
        col_a.metric("Profundidade total", f"{sond.profundidade_total:.1f} m")
        col_b.metric("Altitude (cota boca)", f"{sond.cota_boca:.3f} m")
        col_c.metric("Nível d'água", f"{sond.nivel_dagua:.2f} m" if sond.nivel_dagua else "—")
        df_prev = pd.DataFrame([
            {"Prof. (m)": m.prof_m, "NSPT": m.nspt,
             "Descrição": (m.descricao or "")[:50], "Origem": m.origem or ""}
            for m in sond.metros
        ])
        st.dataframe(df_prev, use_container_width=True, hide_index=True, height=200)

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
