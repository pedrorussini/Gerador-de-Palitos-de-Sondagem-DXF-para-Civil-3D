"""
app.py — Gerador de Palitos de Sondagem SPT em DXF para Civil 3D
Seleção de área no PDF via canvas HTML/JS nativo (sem streamlit-drawable-canvas)
"""

import streamlit as st
import pandas as pd
import io
import zipfile
import base64
import json

import pdfplumber
from pdf2image import convert_from_bytes
from PIL import Image
import streamlit.components.v1 as components

from leitor_sondagem import (
    ler_pdf_sondagem, extrair_cabecalho_bbox, extrair_tabela_bbox,
    bbox_canvas_para_pdf, SondagemSPT, MetroSPT,
)
from gerar_dxf import gerar_dxf_sondagem

# ---------------------------------------------------------------------------
# Configuração
# ---------------------------------------------------------------------------
st.set_page_config(page_title="Gerador de Palitos SPT", page_icon="🗂️", layout="wide")

CANVAS_W   = 880
DPI_RENDER = 150

# ---------------------------------------------------------------------------
# Canvas HTML/JS nativo — sem dependências externas
# ---------------------------------------------------------------------------

_CANVAS_HTML = """
<style>
  body {{ margin:0; padding:0; background:#1e1e1e; }}
  #wrap {{ position:relative; display:inline-block; user-select:none; }}
  #bg   {{ display:block; width:{W}px; height:{H}px; }}
  #cv   {{ position:absolute; top:0; left:0; cursor:crosshair; }}
  .lbl  {{ position:absolute; font:bold 11px monospace; padding:2px 5px;
           border-radius:3px; pointer-events:none; }}
  #info {{ font:13px monospace; color:#ccc; margin:4px 0 0 0; }}
</style>
<div id="wrap">
  <img id="bg" src="data:image/png;base64,{IMG}" draggable="false"/>
  <canvas id="cv" width="{W}" height="{H}"></canvas>
</div>
<p id="info">Clique e arraste para selecionar a região</p>

<script>
const cv   = document.getElementById('cv');
const ctx  = cv.getContext('2d');
const wrap = document.getElementById('wrap');
const info = document.getElementById('info');

const COR_CAB = '#00dd55';
const COR_TAB = '#3399ff';
const cor     = '{COR}';

let rects = {RECTS};   // seleções existentes para overlay
let drag  = false;
let x0=0, y0=0, x1=0, y1=0;

function clamp(v,mn,mx){{ return Math.max(mn,Math.min(mx,v)); }}
function ptCanvas(e){{
  const r = cv.getBoundingClientRect();
  return [clamp(e.clientX-r.left,0,cv.width), clamp(e.clientY-r.top,0,cv.height)];
}}

function draw(){{
  ctx.clearRect(0,0,cv.width,cv.height);
  // overlay
  rects.forEach(r=>{{
    ctx.strokeStyle=r.cor; ctx.lineWidth=2;
    ctx.fillStyle=r.cor+'22';
    ctx.fillRect(r.x,r.y,r.w,r.h);
    ctx.strokeRect(r.x,r.y,r.w,r.h);
    ctx.fillStyle=r.cor;
    ctx.font='bold 11px monospace';
    ctx.fillText(r.lbl, r.x+4, r.y+14);
  }});
  // atual
  if(drag){{
    const w=x1-x0, h=y1-y0;
    ctx.strokeStyle=cor; ctx.lineWidth=2;
    ctx.fillStyle=cor+'22';
    ctx.fillRect(x0,y0,w,h);
    ctx.strokeRect(x0,y0,w,h);
    info.textContent='Selecão: '+Math.abs(w)+'×'+Math.abs(h)+' px  —  solte para confirmar';
  }}
}}

cv.addEventListener('mousedown', e=>{{
  [x0,y0]=ptCanvas(e); x1=x0; y1=y0; drag=true;
}});
cv.addEventListener('mousemove', e=>{{
  if(!drag) return;
  [x1,y1]=ptCanvas(e); draw();
}});
cv.addEventListener('mouseup', e=>{{
  if(!drag) return; drag=false;
  [x1,y1]=ptCanvas(e);
  const left=Math.min(x0,x1), top=Math.min(y0,y1);
  const w=Math.abs(x1-x0), h=Math.abs(y1-y0);
  if(w>10 && h>10){{
    const payload = JSON.stringify({{left,top,width:w,height:h}});
    info.textContent='✅ Região: x='+left+' y='+top+' '+w+'×'+h+' — clique em Confirmar';
    // Envia para Streamlit via query param trick
    window.parent.postMessage({{type:'canvas_rect', rect: payload}}, '*');
  }} else {{
    info.textContent='Região muito pequena, tente novamente.';
  }}
  draw();
}});

// Toque (mobile)
cv.addEventListener('touchstart', e=>{{
  e.preventDefault();
  const t=e.touches[0];
  const r=cv.getBoundingClientRect();
  x0=t.clientX-r.left; y0=t.clientY-r.top; x1=x0; y1=y0; drag=true;
}},{{passive:false}});
cv.addEventListener('touchmove', e=>{{
  e.preventDefault();
  const t=e.touches[0];
  const r=cv.getBoundingClientRect();
  x1=t.clientX-r.left; y1=t.clientY-r.top; draw();
}},{{passive:false}});
cv.addEventListener('touchend', e=>{{
  drag=false;
  const left=Math.min(x0,x1), top=Math.min(y0,y1);
  const w=Math.abs(x1-x0), h=Math.abs(y1-y0);
  if(w>10&&h>10){{
    window.parent.postMessage({{type:'canvas_rect', rect: JSON.stringify({{left,top,width:w,height:h}})}}, '*');
  }}
  draw();
}});

draw();
</script>
"""

def _render_canvas(img_pil: Image.Image, canvas_w: int, canvas_h: int,
                   cor: str, overlay_rects: list, key: str):
    """
    Renderiza canvas HTML nativo com a imagem como fundo.
    Retorna o HTML component — a captura do rect é feita via session_state
    através de um input text oculto sincronizado por JS.
    """
    buf = io.BytesIO()
    img_pil.save(buf, format="PNG")
    img_b64 = base64.b64encode(buf.getvalue()).decode()

    rects_js = json.dumps([
        {"x": r["left"], "y": r["top"], "w": r["width"], "h": r["height"],
         "cor": r["cor"], "lbl": r["lbl"]}
        for r in overlay_rects
    ])

    html = _CANVAS_HTML.format(
        W=canvas_w, H=canvas_h,
        IMG=img_b64, COR=cor, RECTS=rects_js,
    )
    # Altura do componente = canvas + margem
    components.html(html, height=canvas_h + 40, scrolling=False)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _pdf_para_imagem(pdf_bytes: bytes, pagina: int = 0, dpi: int = DPI_RENDER):
    imgs = convert_from_bytes(pdf_bytes, dpi=dpi, first_page=pagina+1, last_page=pagina+1)
    return imgs[0] if imgs else None

def _rect_valido(rect) -> bool:
    if not rect:
        return False
    return rect.get("width", 0) > 10 and rect.get("height", 0) > 10


# ---------------------------------------------------------------------------
# Revisão + export
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
            nome_ed = ca.text_input("Nome",       value=sond.nome,                       key=f"nome_{idx}")
            cota_ed = cb.number_input("Cota (m)",  value=float(sond.cota_boca),          step=0.001, format="%.3f", key=f"cota_{idx}")
            na_ed   = cc.number_input("NA (m)",    value=float(sond.nivel_dagua or 0.0), step=0.1,   format="%.2f", key=f"na_{idx}")
            dist_ed = cd.number_input("Dist. (m)", value=0.0,                             step=0.001, format="%.3f", key=f"dist_{idx}")

            st.markdown("**Metros extraídos — complete onde necessário:**")
            df = pd.DataFrame([{
                "Prof. (m)": m.prof_m, "NSPT": m.nspt,
                "g1": m.golpes_1, "g2": m.golpes_2, "g3": m.golpes_3,
                "Origem": m.origem, "Descrição": m.descricao,
            } for m in sond.metros])

            df_ed = st.data_editor(
                df, num_rows="dynamic", width='stretch', key=f"tabela_{idx}",
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
# MODO MANUAL — canvas HTML nativo
# ===========================================================================
else:
    for key, default in [
        ("paginas_info", {}),
        ("selecoes", {}),
        ("sondagens_manuais", []),
        ("rect_pendente", {}),   # {chave_str: {etapa: rect}}
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

    opcoes_pag = [f"Página {i+1}" for i in pags_perfil]
    pag_label  = c_pag.selectbox("Página com o perfil SPT", opcoes_pag)
    pag_idx    = pags_perfil[opcoes_pag.index(pag_label)]
    chave      = (pdf_idx, pag_idx)
    chave_str  = f"{pdf_sel}_pag{pag_idx+1}"

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
    img_resized  = img_pil.resize((CANVAS_W, canvas_h), Image.LANCZOS)

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
        st.info("🖱️ **Arraste** na imagem abaixo para marcar o **cabeçalho** (nome da sondagem, cota, nível d'água).")
    else:
        st.info("🖱️ **Arraste** na imagem abaixo para marcar toda a **tabela de dados** (escala de profundidade + golpes + descrição).")

    # Montar overlay
    overlay = []
    if rect_cab_exist:
        overlay.append({**rect_cab_exist, "cor": "#00dd55", "lbl": "CABEÇALHO"})
    if rect_tab_exist:
        overlay.append({**rect_tab_exist, "cor": "#3399ff", "lbl": "TABELA"})

    cor = "#00dd55" if sel_cab else "#3399ff"

    # Renderizar canvas
    _render_canvas(img_resized, CANVAS_W, canvas_h, cor, overlay,
                   key=f"cv_{chave}_{etapa}")

    # Input de texto para receber o rect via JavaScript → Streamlit
    # O usuário cola o JSON ou usa o mecanismo de comunicação
    st.markdown("**Cole o resultado da seleção abaixo** (o valor aparece automaticamente após arrastar):")

    col_input, col_btn_conf, col_btn_limpa = st.columns([3, 1, 1])

    rect_json = col_input.text_input(
        "Região selecionada (JSON):",
        value=st.session_state.get(f"rect_json_{chave}_{etapa}", ""),
        placeholder='{"left":100,"top":50,"width":400,"height":80}',
        key=f"rect_input_{chave}_{etapa}",
        label_visibility="collapsed",
    )

    # Script para auto-preencher o campo de texto quando o canvas envia mensagem
    components.html(f"""
    <script>
    window.addEventListener('message', function(e) {{
      if (e.data && e.data.type === 'canvas_rect') {{
        // Tenta preencher o input do Streamlit via DOM
        const inputs = window.parent.document.querySelectorAll('input[type="text"]');
        inputs.forEach(inp => {{
          if (inp.placeholder && inp.placeholder.includes('"left"')) {{
            const nativeInputValueSetter = Object.getOwnPropertyDescriptor(
              window.HTMLInputElement.prototype, 'value').set;
            nativeInputValueSetter.call(inp, e.data.rect);
            inp.dispatchEvent(new Event('input', {{ bubbles: true }}));
          }}
        }});
      }}
    }});
    </script>
    """, height=0)

    if col_btn_conf.button("✅ Confirmar", key=f"confirmar_{chave}_{etapa}"):
        try:
            rect_parsed = json.loads(rect_json)
            if _rect_valido(rect_parsed):
                st.session_state["selecoes"].setdefault(chave, {})
                if sel_cab:
                    st.session_state["selecoes"][chave]["cab"] = rect_parsed
                    st.success("Cabeçalho salvo! Agora selecione a tabela.")
                else:
                    st.session_state["selecoes"][chave]["tab"] = rect_parsed
                    st.success("Tabela salva!")
                st.rerun()
            else:
                st.warning("Rect muito pequeno ou inválido.")
        except (json.JSONDecodeError, TypeError):
            st.warning("JSON inválido. Arraste um retângulo na imagem e aguarde o preenchimento automático.")

    if col_btn_limpa.button("🗑️ Limpar", key=f"limpar_{chave}"):
        st.session_state["selecoes"].pop(chave, None)
        st.rerun()

    # Status
    st.divider()
    cs1, cs2 = st.columns(2)
    cs1.success("✅ Cabeçalho selecionado") if rect_cab_exist else cs1.warning("⏳ Cabeçalho ainda não selecionado")
    cs2.success("✅ Tabela selecionada")    if rect_tab_exist else cs2.warning("⏳ Tabela ainda não selecionada")

    # --- Extração ---
    if rect_cab_exist and rect_tab_exist:
        if st.button("🔍 Extrair dados das regiões selecionadas", type="primary",
                     key=f"extrair_{chave}"):
            with st.spinner("Extraindo dados do PDF..."):
                try:
                    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf_obj:
                        pagina  = pdf_obj.pages[pag_idx]
                        bbox_cab = bbox_canvas_para_pdf(rect_cab_exist, CANVAS_W, canvas_h, pdf_w, pdf_h)
                        bbox_tab = bbox_canvas_para_pdf(rect_tab_exist, CANVAS_W, canvas_h, pdf_w, pdf_h)
                        cab    = extrair_cabecalho_bbox(pagina, bbox_cab)
                        metros = extrair_tabela_bbox(pagina, bbox_tab)

                    sond = SondagemSPT(
                        nome=cab["nome"], cota_boca=cab["cota_boca"],
                        nivel_dagua=cab["nivel_dagua"], metros=metros,
                    )
                    lista = [(n, s) for n, s in st.session_state["sondagens_manuais"]
                             if n != chave_str]
                    lista.append((chave_str, sond))
                    st.session_state["sondagens_manuais"] = lista
                    st.success(f"✅ {len(metros)} metro(s) extraído(s).")

                    with st.expander("🔎 Texto bruto do cabeçalho (debug)"):
                        st.code(cab.get("texto_raw", ""))

                    st.rerun()
                except Exception as e:
                    st.error(f"❌ Erro na extração: {e}")

    # --- Revisão ---
    sondagens_manuais = st.session_state.get("sondagens_manuais", [])
    if sondagens_manuais:
        st.divider()
        st.subheader("📋 Revisão e Export")
        _renderizar_revisao(sondagens_manuais)
