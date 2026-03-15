"""
gerar_dxf.py — Gera DXF de palito de sondagem SPT para Civil 3D.

Layout fiel ao modelo padrão brasileiro (baseado na imagem de referência):

  ┌─────────────────────────────────┐
  │         SP-33C-4-001            │  ← cabeçalho (amarelo)
  │         ALT: 13.790             │
  │         DIST: 0.000             │
  └─────────────────────────────────┘
  Argila siltoarenosa,  │▓▓│  1      ← descrição esq | palito | prof dir
  marrom mole.          │  │
  ──────────────────────┼──┤
  Areia fina argilosa,  │▓▓│  2
  cinza pouco compacta. │  │  NSPT
                        │  │   7

Escala 1:100 — 1 metro = 1 unidade CAD
"""

import io
from typing import Optional


# ---------------------------------------------------------------------------
# Constantes de layout (unidades CAD — 1 = 1m em escala 1:100)
# ---------------------------------------------------------------------------

CAB_H     = 3.5    # altura do cabeçalho
PAL_X     = 5.5    # X da borda esquerda do palito
PAL_W     = 0.8    # largura do palito
PAL_Y0    = CAB_H  # Y do topo do palito (= base do cabeçalho)

# Coluna de descrição: à esquerda do palito
DESC_X_MAX = PAL_X - 0.3   # borda direita da descrição (alinha à direita do palito)
DESC_LARG  = 5.0            # largura da coluna de descrição

# NSPT: número pequeno à ESQUERDA do palito, entre descrição e palito
NSPT_X     = PAL_X - 0.15  # alinhado à direita da coluna NSPT

# Profundidade: à DIREITA do palito a cada metro
PROF_X     = PAL_X + PAL_W + 0.25

# Cabeçalho centralizado no palito
CAB_CX     = PAL_X + PAL_W / 2.0

# Alturas de texto
TXT_TITULO = 0.45
TXT_NORMAL = 0.25
TXT_DESC   = 0.22
TXT_ORIG   = 0.20

# Hachuras por palavra-chave na descrição
_HACHURAS = {
    "argila":        ("ANSI31",  45, 0.5),
    "argiloso":      ("ANSI31",  45, 0.5),
    "silte":         ("ANSI37",  45, 0.4),
    "siltoso":       ("ANSI37",  45, 0.4),
    "areia":         ("AR-SAND",  0, 0.8),
    "arenoso":       ("AR-SAND",  0, 0.8),
    "pedregulho":    ("AR-CONC",  0, 0.5),
    "organico":      ("GRASS",    0, 1.0),
    "orgânico":      ("GRASS",    0, 1.0),
    "aterro":        ("ANSI32",   0, 0.8),
    "rocha":         ("ANSI36",   0, 0.7),
    "impenetrável":  ("SOLID",    0, 1.0),
}

# Layers e cores AutoCAD
LY_CAB    = "SONDAGEM_CABECALHO"   # 2 = amarelo
LY_PALITO = "SONDAGEM_PALITO"      # 7 = branco/preto
LY_NSPT   = "SONDAGEM_NSPT"        # 5 = azul
LY_TEXTO  = "SONDAGEM_TEXTO"       # 7
LY_HACH   = "SONDAGEM_HACHURA"     # 8 = cinza
LY_NA     = "SONDAGEM_NA"          # 5 = azul
LY_LIM    = "SONDAGEM_LIMITE"      # 3 = verde


def _y(prof: float) -> float:
    """Profundidade em metros → Y no DXF (negativo = para baixo)."""
    return -(PAL_Y0 + prof)


def _hachura(desc: str) -> tuple:
    d = desc.lower()
    for k, v in _HACHURAS.items():
        if k in d:
            return v
    return ("ANSI31", 45, 0.5)


def _quebrar(texto: str, max_c: int = 24) -> list:
    """Quebra texto em linhas de no máximo max_c caracteres."""
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


def _agrupar(metros: list) -> list:
    """Agrupa metros com mesma descrição em horizontes."""
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


def _exportar(doc) -> bytes:
    """Exporta doc ezdxf para bytes — compatível com todas as versões."""
    import tempfile, os
    buf = io.BytesIO()
    try:
        doc.write(buf)
        buf.seek(0)
        data = buf.read()
        if data:
            return data
    except Exception:
        pass
    # Fallback: arquivo temporário
    with tempfile.NamedTemporaryFile(suffix=".dxf", delete=False) as tmp:
        tmp_path = tmp.name
    doc.saveas(tmp_path)
    with open(tmp_path, "rb") as f:
        data = f.read()
    os.unlink(tmp_path)
    return data


def _palito(msp, sond, dist: float, hachura: bool, ox: float = 0.0):
    """Desenha um palito completo no modelspace deslocado por ox."""
    from ezdxf.enums import TextEntityAlignment as TA

    metros   = sond.metros
    prof_max = sond.profundidade_total
    y_topo   = _y(0.0)
    y_fundo  = _y(prof_max)

    def X(v): return v + ox

    # ----------------------------------------------------------------
    # CABEÇALHO
    # ----------------------------------------------------------------
    cab_x0 = X(PAL_X - DESC_LARG - 0.5)
    cab_x1 = X(PAL_X + PAL_W + 2.5)

    # Borda do cabeçalho
    msp.add_lwpolyline(
        [(cab_x0, 0), (cab_x1, 0),
         (cab_x1, -CAB_H), (cab_x0, -CAB_H), (cab_x0, 0)],
        dxfattribs={"layer": LY_CAB, "lineweight": 50, "closed": True}
    )
    msp.add_line((cab_x0, -CAB_H), (cab_x1, -CAB_H),
                 dxfattribs={"layer": LY_CAB, "lineweight": 50})

    # Textos do cabeçalho
    for txt, dy in [
        (sond.nome,                    -0.70),
        (f"ALT: {sond.cota_boca:.3f}", -1.50),
        (f"DIST: {dist:.3f}",          -2.30),
    ]:
        altura = TXT_TITULO if dy == -0.70 else TXT_NORMAL
        t = msp.add_text(txt, dxfattribs={"layer": LY_CAB, "height": altura})
        t.set_placement((X(CAB_CX), dy), align=TA.MIDDLE_CENTER)

    # ----------------------------------------------------------------
    # PALITO — retângulo central
    # ----------------------------------------------------------------
    msp.add_lwpolyline(
        [(X(PAL_X), y_topo), (X(PAL_X + PAL_W), y_topo),
         (X(PAL_X + PAL_W), y_fundo), (X(PAL_X), y_fundo),
         (X(PAL_X), y_topo)],
        dxfattribs={"layer": LY_PALITO, "lineweight": 50, "closed": True}
    )

    # Divisórias a cada metro + cotas de profundidade à direita
    for m in range(1, int(prof_max) + 1):
        ym = _y(float(m))
        # Traço horizontal
        msp.add_line((X(PAL_X), ym), (X(PAL_X + PAL_W), ym),
                     dxfattribs={"layer": LY_PALITO, "lineweight": 13})
        # Cota de profundidade à direita do palito
        t = msp.add_text(
            str(m),
            dxfattribs={"layer": LY_TEXTO, "height": TXT_NORMAL}
        )
        t.set_placement((X(PROF_X), ym + 0.12), align=TA.LEFT)

    # ----------------------------------------------------------------
    # NSPT — número à ESQUERDA do palito, centralizado no metro
    # Posicionado entre a descrição e o palito (pequeno, alinhado à direita)
    # ----------------------------------------------------------------
    for m in metros:
        yc = _y(m.prof_m - 0.5)
        t = msp.add_text(
            str(m.nspt),
            dxfattribs={"layer": LY_NSPT, "height": TXT_NORMAL}
        )
        # Alinhado à direita da coluna NSPT (encostado no palito, à esquerda)
        t.set_placement((X(NSPT_X), yc + 0.12), align=TA.RIGHT)

    # ----------------------------------------------------------------
    # HORIZONTES — descrição + origem à esquerda + hachura no palito
    # ----------------------------------------------------------------
    horizontes = _agrupar(metros)

    for h in horizontes:
        yi  = _y(h["pi"])
        yf  = _y(h["pf"])
        ym  = (yi + yf) / 2.0
        alt = abs(yi - yf)

        # Traço de limite superior (linha horizontal da borda do palito até a esquerda)
        if h["pi"] > 0:
            msp.add_line(
                (X(PAL_X - DESC_LARG), yi),
                (X(PAL_X), yi),
                dxfattribs={"layer": LY_TEXTO, "lineweight": 13}
            )

        # Montar linhas de texto: origem primeiro, depois descrição quebrada
        linhas_txt = []
        if h["orig"]:
            linhas_txt.append((h["orig"], TXT_ORIG, True))   # (texto, altura, negrito)
        for ln in _quebrar(h["desc"], max_c=24):
            linhas_txt.append((ln, TXT_DESC, False))

        # Posicionar texto centralizado verticalmente no horizonte
        n      = len(linhas_txt)
        esp    = TXT_DESC * 1.8
        y_ini  = ym + ((n - 1) * esp) / 2.0

        for j, (ln, altura, _bold) in enumerate(linhas_txt[:6]):
            t = msp.add_text(
                ln,
                dxfattribs={"layer": LY_TEXTO, "height": altura}
            )
            # Alinha à direita, encostando no palito
            t.set_placement(
                (X(PAL_X - 0.25), y_ini - j * esp),
                align=TA.RIGHT
            )

        # Hachura dentro do palito
        if hachura and alt > 0.05:
            pat, ang, sc = _hachura(h["desc"])
            try:
                ha = msp.add_hatch(dxfattribs={"layer": LY_HACH})
                ha.set_pattern_fill(pat, scale=sc, angle=ang)
                ha.paths.add_polyline_path([
                    (X(PAL_X),         yi),
                    (X(PAL_X + PAL_W), yi),
                    (X(PAL_X + PAL_W), yf),
                    (X(PAL_X),         yf),
                ], is_closed=True)
            except Exception:
                pass

    # ----------------------------------------------------------------
    # NÍVEL D'ÁGUA
    # ----------------------------------------------------------------
    if sond.nivel_dagua and sond.nivel_dagua > 0:
        yna    = _y(sond.nivel_dagua)
        na_str = f"NA:{sond.nivel_dagua:.2f}".replace(".", ",")

        # Linha horizontal + seta + texto
        msp.add_line(
            (X(PAL_X + PAL_W), yna),
            (X(PAL_X + PAL_W + 2.0), yna),
            dxfattribs={"layer": LY_NA, "lineweight": 25}
        )
        # Triângulo indicativo (seta)
        msp.add_solid(
            [(X(PAL_X + PAL_W),        yna),
             (X(PAL_X + PAL_W + 0.35), yna + 0.15),
             (X(PAL_X + PAL_W + 0.35), yna - 0.15),
             (X(PAL_X + PAL_W),        yna)],
            dxfattribs={"layer": LY_NA}
        )
        t = msp.add_text(na_str, dxfattribs={"layer": LY_NA, "height": TXT_NORMAL})
        t.set_placement((X(PAL_X + PAL_W + 2.1), yna + 0.12), align=TA.LEFT)

    # ----------------------------------------------------------------
    # LIMITE DE SONDAGEM + rodapé
    # ----------------------------------------------------------------
    # Linha dupla na base
    msp.add_line((X(PAL_X), y_fundo), (X(PAL_X + PAL_W), y_fundo),
                 dxfattribs={"layer": LY_LIM, "lineweight": 70})
    msp.add_line((X(PAL_X), y_fundo - 0.12), (X(PAL_X + PAL_W), y_fundo - 0.12),
                 dxfattribs={"layer": LY_LIM, "lineweight": 25})

    # Texto profundidade total
    t = msp.add_text(
        f"Prof.={prof_max:.2f}m".replace(".", ","),
        dxfattribs={"layer": LY_TEXTO, "height": TXT_NORMAL}
    )
    t.set_placement((X(CAB_CX), y_fundo - 0.55), align=TA.MIDDLE_CENTER)


# ---------------------------------------------------------------------------
# Funções públicas
# ---------------------------------------------------------------------------

def gerar_dxf_sondagem(
    sondagem,
    distancia: float = 0.0,
    incluir_hachura: bool = True,
) -> bytes:
    """Gera DXF de um único palito SPT. Retorna bytes."""
    try:
        import ezdxf
    except ImportError:
        raise ImportError("Adicione 'ezdxf>=1.1' ao requirements.txt")
    doc = ezdxf.new("R2010")
    doc.units = 6  # metros
    _setup_layers(doc)
    _palito(doc.modelspace(), sondagem, distancia, incluir_hachura, ox=0.0)
    return _exportar(doc)


def gerar_dxf_multiplas(
    sondagens: list,
    distancias: list = None,
    espacamento_x: float = 15.0,
    incluir_hachura: bool = True,
) -> bytes:
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
    return _exportar(doc)
