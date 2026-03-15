"""
gerar_dxf.py — Gera DXF de palito de sondagem SPT para Civil 3D.

Layout fiel ao modelo padrão brasileiro:
  Cabeçalho: SP-XXX | ALT | DIST
  Palito central com divisórias a cada metro
  NSPT à direita do palito por metro
  Descrição do horizonte à esquerda (texto do boletim, sem classificação)
  Origem geológica (SRM, SRJ, SS...) na primeira linha da descrição
  NA: linha + seta + texto "NA:X,XX"
  Rodapé: "Prof.=X,XXm"

Escala 1:100 — 1 metro = 1 unidade CAD
"""

import io


# ---------------------------------------------------------------------------
# Layout (unidades CAD)
# ---------------------------------------------------------------------------
CAB_H      = 3.0   # altura do cabeçalho
PAL_X      = 5.0   # X borda esquerda do palito
PAL_W      = 1.0   # largura do palito
PAL_Y_TOPO = CAB_H # Y topo do palito
DESC_LARG  = 4.5   # largura da coluna de descrição (à esq do palito)
CAB_CX     = PAL_X + PAL_W / 2.0
NSPT_X     = PAL_X + PAL_W + 0.3  # NSPT à direita
PROF_X     = PAL_X + PAL_W + 1.5  # cotas à direita

TXT_TITULO = 0.40
TXT_NORMAL = 0.25
TXT_DESC   = 0.20
TXT_ORIG   = 0.20

_HACHURAS = {
    "argila":       ("ANSI31",  45, 0.6),
    "argiloso":     ("ANSI31",  45, 0.6),
    "silte":        ("ANSI37",  45, 0.5),
    "siltoso":      ("ANSI37",  45, 0.5),
    "areia":        ("AR-SAND",  0, 1.0),
    "arenoso":      ("AR-SAND",  0, 1.0),
    "organico":     ("GRASS",    0, 1.2),
    "orgânico":     ("GRASS",    0, 1.2),
    "aterro":       ("ANSI32",   0, 1.0),
    "rocha":        ("ANSI36",   0, 0.8),
    "impenetrável": ("SOLID",    0, 1.0),
}

LY_CAB    = "SONDAGEM_CABECALHO"
LY_PALITO = "SONDAGEM_PALITO"
LY_NSPT   = "SONDAGEM_NSPT"
LY_TEXTO  = "SONDAGEM_TEXTO"
LY_HACH   = "SONDAGEM_HACHURA"
LY_NA     = "SONDAGEM_NA"
LY_LIM    = "SONDAGEM_LIMITE"


def _y(prof):
    return -(PAL_Y_TOPO + prof)


def _hachura(desc):
    d = desc.lower()
    for k, v in _HACHURAS.items():
        if k in d:
            return v
    return ("ANSI31", 45, 0.8)


def _quebrar(texto, max_c=26):
    palavras = texto.split()
    linhas, linha = [], ""
    for p in palavras:
        if len(linha) + len(p) + (1 if linha else 0) <= max_c:
            linha += (" " + p if linha else p)
        else:
            if linha:
                linhas.append(linha)
            linha = p
    if linha:
        linhas.append(linha)
    return linhas or [""]


def _agrupar(metros):
    if not metros:
        return []
    hs = []
    dc = (metros[0].descricao or "").strip()
    oc = (metros[0].origem or "").strip()
    ini = 0.0
    for i, m in enumerate(metros):
        d = (m.descricao or "").strip()
        o = (m.origem or "").strip()
        if d and d != dc and i > 0:
            hs.append({"pi": ini, "pf": m.prof_m - 1.0, "desc": dc, "orig": oc})
            ini = m.prof_m - 1.0
            dc = d
        if o:
            oc = o
    hs.append({"pi": ini, "pf": metros[-1].prof_m, "desc": dc, "orig": oc})
    return hs


def _setup_layers(doc):
    for nome, cor in [
        (LY_CAB, 2), (LY_PALITO, 7), (LY_NSPT, 5),
        (LY_TEXTO, 7), (LY_HACH, 8), (LY_NA, 5), (LY_LIM, 3),
    ]:
        if nome not in doc.layers:
            doc.layers.add(nome, color=cor)


def _palito(msp, sond, dist, hachura, ox=0.0):
    from ezdxf.enums import TextEntityAlignment as TA

    metros   = sond.metros
    prof_max = sond.profundidade_total
    y0       = _y(0.0)
    yf       = _y(prof_max)

    def X(v): return v + ox

    # ---- Cabeçalho ----
    cx0 = X(PAL_X - DESC_LARG - 0.5)
    cx1 = X(PAL_X + PAL_W + 3.5)

    msp.add_lwpolyline(
        [(cx0, 0), (cx1, 0), (cx1, -CAB_H), (cx0, -CAB_H), (cx0, 0)],
        dxfattribs={"layer": LY_CAB, "lineweight": 50, "closed": True}
    )
    msp.add_line((cx0, -CAB_H), (cx1, -CAB_H),
                 dxfattribs={"layer": LY_CAB, "lineweight": 50})

    for txt, dy in [
        (sond.nome,                    -0.65),
        (f"ALT: {sond.cota_boca:.3f}", -1.35),
        (f"DIST: {dist:.3f}",          -2.05),
    ]:
        t = msp.add_text(txt, dxfattribs={
            "layer": LY_CAB,
            "height": TXT_TITULO if dy == -0.65 else TXT_NORMAL
        })
        t.set_placement((X(CAB_CX), dy), align=TA.MIDDLE_CENTER)

    # ---- Palito ----
    msp.add_lwpolyline(
        [(X(PAL_X), y0), (X(PAL_X + PAL_W), y0),
         (X(PAL_X + PAL_W), yf), (X(PAL_X), yf), (X(PAL_X), y0)],
        dxfattribs={"layer": LY_PALITO, "lineweight": 50, "closed": True}
    )

    for m in range(1, int(prof_max) + 1):
        ym = _y(float(m))
        msp.add_line((X(PAL_X), ym), (X(PAL_X + PAL_W), ym),
                     dxfattribs={"layer": LY_PALITO, "lineweight": 13})
        t = msp.add_text(str(m), dxfattribs={"layer": LY_TEXTO, "height": TXT_NORMAL})
        t.set_placement((X(PROF_X), ym + 0.1), align=TA.LEFT)

    # ---- NSPT ----
    for m in metros:
        yc = _y(m.prof_m - 0.5)
        t = msp.add_text(str(m.nspt), dxfattribs={"layer": LY_NSPT, "height": TXT_NORMAL})
        t.set_placement((X(NSPT_X), yc + 0.1), align=TA.LEFT)

    # ---- Horizontes ----
    for h in _agrupar(metros):
        yi = _y(h["pi"])
        yhi = _y(h["pf"])
        ym  = (yi + yhi) / 2.0
        alt = abs(yi - yhi)

        # Traço de limite superior do horizonte
        if h["pi"] > 0:
            msp.add_line(
                (X(PAL_X - DESC_LARG), yi), (X(PAL_X), yi),
                dxfattribs={"layer": LY_TEXTO, "lineweight": 13}
            )

        # Texto: origem + descrição (exatamente do boletim)
        linhas = []
        if h["orig"]:
            linhas.append(h["orig"])
        linhas += _quebrar(h["desc"])
        n   = len(linhas)
        esp = TXT_DESC * 1.7
        y_s = ym + (n * esp) / 2.0 - esp / 2.0
        for j, ln in enumerate(linhas[:6]):
            t = msp.add_text(ln, dxfattribs={
                "layer": LY_TEXTO,
                "height": TXT_ORIG if j == 0 and h["orig"] else TXT_DESC
            })
            t.set_placement((X(PAL_X - 0.3), y_s - j * esp), align=TA.RIGHT)

        # Hachura
        if hachura and alt > 0.05:
            pat, ang, sc = _hachura(h["desc"])
            try:
                ha = msp.add_hatch(dxfattribs={"layer": LY_HACH})
                ha.set_pattern_fill(pat, scale=sc, angle=ang)
                ha.paths.add_polyline_path([
                    (X(PAL_X), yi), (X(PAL_X + PAL_W), yi),
                    (X(PAL_X + PAL_W), yhi), (X(PAL_X), yhi),
                ], is_closed=True)
            except Exception:
                pass

    # ---- NA ----
    if sond.nivel_dagua and sond.nivel_dagua > 0:
        yna = _y(sond.nivel_dagua)
        na_str = f"NA:{sond.nivel_dagua:.2f}".replace(".", ",")
        msp.add_line(
            (X(PAL_X + PAL_W), yna), (X(PAL_X + PAL_W + 2.2), yna),
            dxfattribs={"layer": LY_NA, "lineweight": 25}
        )
        msp.add_solid(
            [(X(PAL_X + PAL_W),       yna),
             (X(PAL_X + PAL_W + 0.4), yna + 0.18),
             (X(PAL_X + PAL_W + 0.4), yna - 0.18),
             (X(PAL_X + PAL_W),       yna)],
            dxfattribs={"layer": LY_NA}
        )
        t = msp.add_text(na_str, dxfattribs={"layer": LY_NA, "height": TXT_NORMAL})
        t.set_placement((X(PAL_X + PAL_W + 2.3), yna + 0.1), align=TA.LEFT)

    # ---- Limite + rodapé ----
    msp.add_line((X(PAL_X), yf), (X(PAL_X + PAL_W), yf),
                 dxfattribs={"layer": LY_LIM, "lineweight": 70})
    msp.add_line((X(PAL_X), yf - 0.15), (X(PAL_X + PAL_W), yf - 0.15),
                 dxfattribs={"layer": LY_LIM, "lineweight": 25})
    t = msp.add_text(
        f"Prof.={prof_max:.2f}m".replace(".", ","),
        dxfattribs={"layer": LY_TEXTO, "height": TXT_NORMAL}
    )
    t.set_placement((X(CAB_CX), yf - 0.6), align=TA.MIDDLE_CENTER)


def gerar_dxf_sondagem(sondagem, distancia=0.0, incluir_hachura=True):
    """Gera DXF de um único palito. Retorna bytes."""
    try:
        import ezdxf
    except ImportError:
        raise ImportError("Adicione 'ezdxf>=1.1' ao requirements.txt")
    doc = ezdxf.new("R2010")
    doc.units = 6
    _setup_layers(doc)
    _palito(doc.modelspace(), sondagem, distancia, incluir_hachura, ox=0.0)
    buf = io.BytesIO()
    doc.write(buf)
    return buf.getvalue()


def gerar_dxf_multiplas(sondagens, distancias=None, espacamento_x=15.0, incluir_hachura=True):
    """Gera DXF com múltiplos palitos lado a lado. Retorna bytes."""
    try:
        import ezdxf
    except ImportError:
        raise ImportError("Adicione 'ezdxf>=1.1' ao requirements.txt")
    if not sondagens:
        return b""
    if distancias is None:
        distancias = [0.0] * len(sondagens)
    doc = ezdxf.new("R2010")
    doc.units = 6
    _setup_layers(doc)
    msp = doc.modelspace()
    for i, (sond, dist) in enumerate(zip(sondagens, distancias)):
        if sond.metros:
            _palito(msp, sond, dist, incluir_hachura, ox=i * espacamento_x)
    buf = io.BytesIO()
    doc.write(buf)
    return buf.getvalue()
