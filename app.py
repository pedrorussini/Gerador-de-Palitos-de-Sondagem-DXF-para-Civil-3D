"""
app.py — Gerador de Palitos de Sondagem SPT em DXF para Civil 3D
Versão com seleção visual de região no PDF (layout-agnóstico)
"""

import streamlit as st
import pandas as pd
import io
import zipfile
import base64

import pdfplumber
from pdf2image import convert_from_bytes
from PIL import Image

from leitor_sondagem import (
    ler_pdf_sondagem, extrair_cabecalho_bbox, extrair_tabela_bbox,
    bbox_canvas_para_pdf, SondagemSPT, MetroSPT,
)
from gerar_dxf import gerar_dxf_sondagem

try:
    from streamlit_drawable_canvas import st_canvas
    CANVAS_OK = True
except ImportError:
    CANVAS_OK = False

# ---------------------------------------------------------------------------
# Configuração da página
# ---------------------------------------------------------------------------
st.set_page_config(page_title="Gerador de Palitos SPT", page_icon="🗂️", layout="wide")

CANVAS_W   = 900
DPI_RENDER = 150

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _pdf_para_imagem(pdf_bytes: bytes, pagina: int = 0, dpi: int = DPI_RENDER):
    imgs = convert_from_bytes(pdf_bytes, dpi=dpi, first_page=pagina+1, last_page=pagina+1)
    return imgs[0] if imgs else None

def _img_para_base64(img: Image.Image) -> str:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()

def _rect_valido(rect) -> bool:
    if not rect:
        return False
    return rect.get("width", 0) > 10 and rect.get("height", 0) > 10


# ---------------------------------------------------------------------------
# Revisão + export (função reutilizada pelos dois modos)
# ---------------------------------------------------------------------------

def _renderizar_revisao(sondagens_raw: list):
    sondagens_editadas = []

    for idx, (nome_pdf, sond) in enumerate(sondagens_raw):
        metros_com_desc = sum(1 for m in sond.metros if m.descricao)
        pct = metros_com_desc / len(sond.metros) * 100 if sond.metros else 0
        qualidade = "✅ Boa" if pct >= 80 else ("⚠️ Parcial" if pct >= 50 else "❌ Incompleta")

        with st.expander(
            f"📋 {sond.nome} — {len(sond.metros)} metros | "
            f"Extração: {qualidade} ({pct:.0f}% com descrição)",
            expanded=True,
        ):
            if pct < 80:
                st.warning(
                    f"⚠️ Apenas {metros_com_desc}/{len(sond.metros)} metros com descrição. "
                    "Complete antes de exportar."
                )

            ca, cb, cc, cd = st.columns(4)
            nome_ed = ca.text_input("Nome",       value=sond.nome,                  key=f"nome_{idx}")
            cota_ed = cb.number_input("Cota (m)",  value=float(sond.cota_boca),     step=0.001, format="%.3f", key=f"cota_{idx}")
            na_ed   = cc.number_input("NA (m)",    value=float(sond.nivel_dagua or 0.0), step=0.1, format="%.2f", key=f"na_{idx}")
            dist_ed = cd.number_input("Dist. (m)", value=0.0,                        step=0.001, format="%.3f", key=f"dist_{idx}")

            st.markdown("**Metros extraídos — complete onde necessário:**")
            df = pd.DataFrame([{
                "Prof. (m)": m.prof_m, "NSPT": m.nspt,
                "g1": m.golpes_1, "g2": m.golpes_2, "g3": m.golpes_3,
                "Origem": m.origem, "Descrição": m.descricao,
            } for m in sond.metros])

            df_ed = st.data_editor(
                df, num_rows="dynamic", use_container_width=True, key=f"tabela_{idx}",
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

            metros_ed = []
            for _, row in df_ed.iterrows():
                try:
                    metros_ed.append(MetroSPT(
                        prof_m=float(row["Prof. (m)"]), nspt=int(row["NSPT"]),
                        golpes_1=int(row["g1"]), golpes_2=int(row["g2"]), golpes_3=int(row["g3"]),
                        origem=str(row["Origem"] or "").strip().upper(),
                        descricao=str(row["Descrição"] or "").strip().upper(),
                    ))
                except Exception:
                    pass

            metros_ed.sort(key=lambda m: m.prof_m)
            sond_ed = SondagemSPT(nome=nome_ed, cota_boca=cota_ed,
                                   nivel_dagua=na_ed if na_ed > 0 else None,
                                   metros=metros_ed)
            sond_ed._distancia = dist_ed
            sondagens_editadas.append(sond_ed)

            if metros_ed:
                c_dxf, c_hach = st.columns([3, 1])
                hachura = c_hach.checkbox("Hachura", value=True, key=f"hach_{idx}")
                try:
                    dxf_bytes = gerar_dxf_sondagem(sond_ed, dist_ed, hachura)
                    c_dxf.download_button(
                        f"⬇️ Download {nome_ed}.dxf", data=dxf_bytes,
                        file_name=f"{nome_ed}.dxf", mime="application/dxf", key=f"dl_{idx}",
                    )
                except Exception as e:
                    st.error(f"❌ Erro ao gerar DXF: {e}")

    # Export ZIP
    st.divider()
    st.subheader("📦 Exportar todos os DXF")
    c1, c2 = st.columns(2)
    hach_all = c2.checkbox("Incluir hachura", value=True, key="hach_all")
    if c1.button("⬇️ Baixar todos (.zip)", type="primary"):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            for s in sondagens_editadas:
                if not s.metros: continue
                try:
                    zf.writestr(f"{s.nome}.dxf",
                                gerar_dxf_sondagem(s, getattr(s, "_distancia", 0.0), hach_all))
                except Exception as e:
                    st.error(f"Erro em {s.nome}: {e}")
        buf.seek(0)
        st.download_button("📥 Baixar ZIP", data=buf.read(),
                           file_name="palitos_sondagem.zip", mime="application/zip")

    st.divider()
    st.caption("Escala 1:100 | Layers: furoSondagem · BR100 · BR60 · BGEOT-VT · Nível Dagua · Impenetravel · BLC")


# ---------------------------------------------------------------------------
# Interface principal
# ---------------------------------------------------------------------------

st.title("🗂️ Gerador de Palitos de Sondagem SPT")
st.caption("Upload de PDF → seleção visual de área → revisão → export DXF para Civil 3D")

uploaded = st.file_uploader("📄 Selecione os PDFs de sondagem", type=["pdf"], accept_multiple_files=True)
if not uploaded:
    st.info("Faça upload de um ou mais PDFs de boletim de sondagem SPT.")
    st.stop()

st.divider()
modo = st.radio(
    "**Modo de extração:**",
    ["🤖 Automático (parser por empresa)", "🖱️ Seleção manual de área no PDF"],
    horizontal=True,
)
usar_manual = "manual" in modo.lower()

# ===========================================================================
# MODO AUTOMÁTICO
# ===========================================================================
if not usar_manual:
    if "sondagens_raw" not in st.session_state or st.button("🔄 Reler PDFs"):
        sondagens_raw = []
        erros = []
        with st.spinner("Lendo PDFs..."):
            for f in uploaded:
                try:
                    data = f.read()
                    for s in ler_pdf_sondagem(io.BytesIO(data)):
                        sondagens_raw.append((f.name, s))
                except Exception as e:
                    erros.append(f"{f.name}: {e}")
        st.session_state["sondagens_raw"] = sondagens_raw
        for e in erros:
            st.error(f"❌ {e}")

    sondagens_raw = st.session_state.get("sondagens_raw", [])
    if not sondagens_raw:
        st.warning("Nenhuma sondagem encontrada. Tente o modo de seleção manual.")
        st.stop()

    st.success(f"✅ {len(sondagens_raw)} sondagem(ns) encontrada(s).")
    _renderizar_revisao(sondagens_raw)

# ===========================================================================
# MODO MANUAL
# ===========================================================================
else:
    if not CANVAS_OK:
        st.error("⚠️ Instale `streamlit-drawable-canvas` no requirements.txt e reinicie.")
        st.stop()

    # Inicializar estado
    for key, default in [
        ("paginas_info", {}),
        ("selecoes", {}),
        ("sondagens_manuais", []),
    ]:
        if key not in st.session_state:
            st.session_state[key] = default

    # --- Seletor de arquivo e página ---
    st.subheader("① Selecione o arquivo e a página")
    c_arq, c_pag = st.columns(2)
    nomes_pdf = [f.name for f in uploaded]
    pdf_sel   = c_arq.selectbox("Arquivo PDF", nomes_pdf)
    pdf_idx   = nomes_pdf.index(pdf_sel)

    arquivo = uploaded[pdf_idx]
    arquivo.seek(0)
    pdf_bytes = arquivo.read()

    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf_obj:
        pags_perfil = []
        for i, pag in enumerate(pdf_obj.pages):
            txt = pag.extract_text() or ""
            if (any(k in txt for k in ["Classificação do Material","PERFIL INDIVIDUAL","N-SPT","NSPT"])
                    and not any(k in txt for k in ["Memorial Fotográfico","Registro Fotográfico","Localização"])):
                pags_perfil.append(i)
        if not pags_perfil:
            pags_perfil = list(range(len(pdf_obj.pages)))

    opcoes_pag   = [f"Página {i+1}" for i in pags_perfil]
    pag_label    = c_pag.selectbox("Página com o perfil SPT", opcoes_pag)
    pag_idx      = pags_perfil[opcoes_pag.index(pag_label)]
    chave        = (pdf_idx, pag_idx)

    # Renderizar imagem
    if chave not in st.session_state["paginas_info"]:
        with st.spinner("Renderizando página..."):
            img_pil = _pdf_para_imagem(pdf_bytes, pag_idx)
            if img_pil is None:
                st.error("Não foi possível renderizar a página.")
                st.stop()
            with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf_obj:
                p = pdf_obj.pages[pag_idx]
                pdf_w, pdf_h = p.width, p.height
        st.session_state["paginas_info"][chave] = {
            "img": img_pil, "pdf_w": pdf_w, "pdf_h": pdf_h,
        }

    info    = st.session_state["paginas_info"][chave]
    img_pil = info["img"]
    pdf_w   = info["pdf_w"]
    pdf_h   = info["pdf_h"]
    img_w, img_h = img_pil.size
    canvas_h     = int(CANVAS_W * img_h / img_w)
    img_resized  = img_pil.resize((CANVAS_W, canvas_h))

    # --- Seleção de etapa ---
    st.divider()
    etapa = st.radio(
        "**O que você está selecionando agora?**",
        ["② Cabeçalho  (nome · cota · NA)", "③ Tabela de dados  (golpes + descrição)"],
        horizontal=True,
        key=f"etapa_{chave}",
    )
    sel_cab = "Cabeçalho" in etapa

    selecoes       = st.session_state["selecoes"].get(chave, {})
    rect_cab_exist = selecoes.get("cab")
    rect_tab_exist = selecoes.get("tab")

    if sel_cab:
        st.info("🖱️ **Arraste** para marcar a região do cabeçalho: nome da sondagem, cota e nível d'água.")
    else:
        st.info("🖱️ **Arraste** para marcar toda a tabela de dados: escala de profundidade, golpes SPT e descrição do material.")

    # Overlay de seleções anteriores
    overlay = []
    if rect_cab_exist:
        overlay.append({"type":"rect","left":rect_cab_exist["left"],"top":rect_cab_exist["top"],
                         "width":rect_cab_exist["width"],"height":rect_cab_exist["height"],
                         "stroke":"#00cc44","strokeWidth":2,"fill":"rgba(0,204,68,0.10)"})
    if rect_tab_exist:
        overlay.append({"type":"rect","left":rect_tab_exist["left"],"top":rect_tab_exist["top"],
                         "width":rect_tab_exist["width"],"height":rect_tab_exist["height"],
                         "stroke":"#0066ff","strokeWidth":2,"fill":"rgba(0,102,255,0.10)"})

    cor = "#00cc44" if sel_cab else "#0066ff"

    canvas_result = st_canvas(
        fill_color="rgba(0,0,0,0.04)",
        stroke_width=2, stroke_color=cor,
        background_image=img_resized,
        update_streamlit=True,
        height=canvas_h, width=CANVAS_W,
        drawing_mode="rect",
        initial_drawing={"version":"4.4.0","objects":overlay},
        key=f"canvas_{chave}_{etapa}",
    )

    # Capturar novo retângulo
    novo_rect = None
    if canvas_result.json_data:
        objs = canvas_result.json_data.get("objects", [])
        novos = [o for o in objs if o.get("stroke") == cor and o.get("type") == "rect"]
        if novos:
            o = novos[-1]
            novo_rect = {"left": o.get("left",0), "top": o.get("top",0),
                         "width": o.get("width",0), "height": o.get("height",0)}

    c_sal, c_lim = st.columns([2, 1])
    if c_sal.button("✅ Confirmar seleção", key=f"confirmar_{chave}_{etapa}"):
        if _rect_valido(novo_rect):
            st.session_state["selecoes"].setdefault(chave, {})
            if sel_cab:
                st.session_state["selecoes"][chave]["cab"] = novo_rect
                st.success("Cabeçalho salvo! Agora selecione a tabela.")
            else:
                st.session_state["selecoes"][chave]["tab"] = novo_rect
                st.success("Tabela salva!")
            st.rerun()
        else:
            st.warning("Desenhe um retângulo antes de confirmar.")

    if c_lim.button("🗑️ Limpar seleções", key=f"limpar_{chave}"):
        st.session_state["selecoes"].pop(chave, None)
        st.rerun()

    st.divider()
    col_s1, col_s2 = st.columns(2)
    col_s1.success("✅ Cabeçalho selecionado") if rect_cab_exist else col_s1.warning("⏳ Cabeçalho ainda não selecionado")
    col_s2.success("✅ Tabela selecionada")    if rect_tab_exist else col_s2.warning("⏳ Tabela ainda não selecionada")

    # --- Extração ---
    if rect_cab_exist and rect_tab_exist:
        if st.button("🔍 Extrair dados das regiões selecionadas", type="primary",
                     key=f"extrair_{chave}"):
            with st.spinner("Extraindo dados do PDF..."):
                try:
                    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf_obj:
                        pagina = pdf_obj.pages[pag_idx]
                        bbox_cab = bbox_canvas_para_pdf(rect_cab_exist, CANVAS_W, canvas_h, pdf_w, pdf_h)
                        bbox_tab = bbox_canvas_para_pdf(rect_tab_exist, CANVAS_W, canvas_h, pdf_w, pdf_h)
                        cab    = extrair_cabecalho_bbox(pagina, bbox_cab)
                        metros = extrair_tabela_bbox(pagina, bbox_tab)

                    sond = SondagemSPT(
                        nome=cab["nome"], cota_boca=cab["cota_boca"],
                        nivel_dagua=cab["nivel_dagua"], metros=metros,
                    )
                    chave_str = f"{pdf_sel}_pag{pag_idx+1}"
                    lista = [(n, s) for n, s in st.session_state["sondagens_manuais"]
                             if n != chave_str]
                    lista.append((chave_str, sond))
                    st.session_state["sondagens_manuais"] = lista
                    st.success(f"✅ {len(metros)} metro(s) extraído(s).")

                    # Mostrar texto bruto do cabeçalho para debug
                    with st.expander("🔎 Texto extraído do cabeçalho (debug)"):
                        st.code(cab.get("texto_raw", ""))

                    st.rerun()
                except Exception as e:
                    st.error(f"❌ Erro na extração: {e}")

    # --- Revisão das sondagens já extraídas ---
    sondagens_manuais = st.session_state.get("sondagens_manuais", [])
    if sondagens_manuais:
        st.divider()
        st.subheader("📋 Revisão e Export")
        _renderizar_revisao(sondagens_manuais)
