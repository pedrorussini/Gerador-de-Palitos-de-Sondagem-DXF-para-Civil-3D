"""
app.py — Gerador de Palitos de Sondagem SPT em DXF para Civil 3D
"""

import streamlit as st
import pandas as pd
import io
import zipfile

from leitor_sondagem import ler_pdf_sondagem, SondagemSPT, MetroSPT
from gerar_dxf import gerar_dxf_sondagem, gerar_dxf_multiplas

st.set_page_config(
    page_title="Gerador de Palitos SPT",
    page_icon="🗂️",
    layout="wide",
)

st.title("🗂️ Gerador de Palitos de Sondagem SPT")
st.caption("Upload de PDFs de boletim → revisão → export DXF para Civil 3D")

# ---------------------------------------------------------------------------
# Upload de PDFs
# ---------------------------------------------------------------------------
uploaded = st.file_uploader(
    "📄 Selecione os PDFs de sondagem",
    type=["pdf"],
    accept_multiple_files=True,
)

if not uploaded:
    st.info("Faça upload de um ou mais PDFs de boletim de sondagem SPT.")
    st.stop()

# ---------------------------------------------------------------------------
# Leitura dos PDFs
# ---------------------------------------------------------------------------
if "sondagens_raw" not in st.session_state or st.button("🔄 Reler PDFs"):
    sondagens_raw = []
    erros = []
    with st.spinner("Lendo PDFs..."):
        for f in uploaded:
            try:
                data = f.read()
                sonds = ler_pdf_sondagem(io.BytesIO(data))
                for s in sonds:
                    sondagens_raw.append((f.name, s))
            except Exception as e:
                erros.append(f"{f.name}: {e}")
    st.session_state["sondagens_raw"] = sondagens_raw
    if erros:
        for e in erros:
            st.error(f"❌ {e}")

sondagens_raw = st.session_state.get("sondagens_raw", [])

if not sondagens_raw:
    st.warning("Nenhuma sondagem encontrada nos PDFs enviados.")
    st.stop()

st.success(f"✅ {len(sondagens_raw)} sondagem(ns) encontrada(s).")

# ---------------------------------------------------------------------------
# Revisão editável por sondagem
# ---------------------------------------------------------------------------
sondagens_editadas: list[SondagemSPT] = []

for idx, (nome_pdf, sond) in enumerate(sondagens_raw):

    # Indicador de qualidade
    metros_com_desc = sum(1 for m in sond.metros if m.descricao)
    pct_desc = metros_com_desc / len(sond.metros) * 100 if sond.metros else 0
    if pct_desc >= 80:   qualidade = "✅ Boa"
    elif pct_desc >= 50: qualidade = "⚠️ Parcial"
    else:                qualidade = "❌ Incompleta"

    with st.expander(
        f"📋 {sond.nome} — {len(sond.metros)} metros | "
        f"Extração: {qualidade} ({pct_desc:.0f}% com descrição)",
        expanded=True,
    ):
        if pct_desc < 80:
            st.warning(
                f"⚠️ Apenas {metros_com_desc}/{len(sond.metros)} metros com descrição. "
                "Complete os campos em branco antes de exportar."
            )

        # Detectar descrições truncadas
        _truncadas = [
            m for m in sond.metros
            if m.descricao and m.descricao.rstrip().split()[-1].lower()
            in {"com", "e", "de", "a", "em", "ou", "que", "para", "do", "da"}
        ]
        if _truncadas:
            st.warning(
                f"⚠️ {len(_truncadas)} descrição(ões) parecem incompletas "
                "(terminam com 'com', 'e', 'de'...). Complete na tabela."
            )

        # Cabeçalho editável
        col_a, col_b, col_c, col_d = st.columns(4)
        nome_ed  = col_a.text_input("Nome",  value=sond.nome,       key=f"nome_{idx}")
        cota_ed  = col_b.number_input("Cota (m)", value=float(sond.cota_boca),
                                      step=0.001, format="%.3f",    key=f"cota_{idx}")
        na_ed    = col_c.number_input("NA (m)", value=float(sond.nivel_dagua or 0.0),
                                      step=0.1, format="%.2f",      key=f"na_{idx}")
        dist_ed  = col_d.number_input("Dist. (m)", value=0.0,
                                      step=0.001, format="%.3f",    key=f"dist_{idx}")

        # Tabela editável
        st.markdown("**Metros extraídos — complete descrição e origem onde estiver em branco:**")

        df = pd.DataFrame([{
            "Prof. (m)":  m.prof_m,
            "NSPT":       m.nspt,
            "g1":         m.golpes_1,
            "g2":         m.golpes_2,
            "g3":         m.golpes_3,
            "Origem":     m.origem,
            "Descrição":  m.descricao,
        } for m in sond.metros])

        df_ed = st.data_editor(
            df,
            num_rows="dynamic",
            use_container_width=True,
            key=f"tabela_{idx}",
            column_config={
                "Prof. (m)": st.column_config.NumberColumn(format="%.2f"),
                "NSPT":      st.column_config.NumberColumn(min_value=0, max_value=200),
                "g1":        st.column_config.NumberColumn(min_value=0, max_value=60),
                "g2":        st.column_config.NumberColumn(min_value=0, max_value=60),
                "g3":        st.column_config.NumberColumn(min_value=0, max_value=60),
                "Origem":    st.column_config.TextColumn(),
                "Descrição": st.column_config.TextColumn(),
            },
        )

        # Reconstruir sondagem editada
        metros_ed = []
        for _, row in df_ed.iterrows():
            try:
                metros_ed.append(MetroSPT(
                    prof_m    = float(row["Prof. (m)"]),
                    nspt      = int(row["NSPT"]),
                    golpes_1  = int(row["g1"]),
                    golpes_2  = int(row["g2"]),
                    golpes_3  = int(row["g3"]),
                    origem    = str(row["Origem"] or "").strip().upper(),
                    descricao = str(row["Descrição"] or "").strip().upper(),
                ))
            except Exception:
                pass

        metros_ed.sort(key=lambda m: m.prof_m)
        sond_ed = SondagemSPT(
            nome        = nome_ed,
            cota_boca   = cota_ed,
            nivel_dagua = na_ed if na_ed > 0 else None,
            metros      = metros_ed,
        )
        sond_ed._distancia = dist_ed
        sondagens_editadas.append(sond_ed)

        # Download individual
        if metros_ed:
            col_dxf, col_hach = st.columns([3, 1])
            hachura = col_hach.checkbox("Hachura", value=True, key=f"hach_{idx}")
            try:
                dxf_bytes = gerar_dxf_sondagem(sond_ed, dist_ed, hachura)
                col_dxf.download_button(
                    f"⬇️ Download {nome_ed}.dxf",
                    data        = dxf_bytes,
                    file_name   = f"{nome_ed}.dxf",
                    mime        = "application/dxf",
                    key         = f"dl_{idx}",
                )
            except Exception as e:
                st.error(f"❌ Erro ao gerar DXF: {e}")

# ---------------------------------------------------------------------------
# Export de todos em ZIP
# ---------------------------------------------------------------------------
st.divider()
st.subheader("📦 Exportar DXF")

col1, col2 = st.columns(2)
hachura_all = col2.checkbox("Incluir hachura", value=True, key="hach_all")

if col1.button("⬇️ Baixar todos os palitos (.zip)", type="primary"):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for sond_ed in sondagens_editadas:
            if not sond_ed.metros:
                continue
            dist = getattr(sond_ed, '_distancia', 0.0)
            try:
                dxf_bytes = gerar_dxf_sondagem(sond_ed, dist, hachura_all)
                zf.writestr(f"{sond_ed.nome}.dxf", dxf_bytes)
            except Exception as e:
                st.error(f"Erro em {sond_ed.nome}: {e}")
    buf.seek(0)
    st.download_button(
        "📥 Clique para baixar o ZIP",
        data      = buf.read(),
        file_name = "palitos_sondagem.zip",
        mime      = "application/zip",
    )

# ---------------------------------------------------------------------------
# Rodapé
# ---------------------------------------------------------------------------
st.divider()
st.caption(
    "Escala 1:100 | Layers: furoSondagem · BR100 · BR60 · BGEOT-VT · "
    "Nível Dagua · Impenetravel · BLC"
)
