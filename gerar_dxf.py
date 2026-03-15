"""
gerar_dxf.py — Gera DXF de palito SPT fiel ao modelo padrão brasileiro.

Estrutura baseada na análise do arquivo sondagempadrao.dxf:

  Escala: 1 unidade CAD = 1 metro real (escala 1:100 no papel)

  PAL_X = 0.0  → linha vertical do palito (layer furoSondagem)
  NSPT:  x=+0.2, à DIREITA do palito (layer BR100, h=0.20)
  DESC:  x=-0.2, à ESQUERDA alinhado à direita (layer BR60, h=0.085)
         formato: "ORIG - Descrição.: prof_ini-prof_fim"
  CAB:   MTEXT à direita (x=+0.2), texto "SP-XXX\nALT: X,XXX\nDIST: X,XXX"
  NA:    texto à direita (x=+2.4, layer Nível D'Água)
  Linhas de horizonte: horizontais de x=0 até x=-2.4 (layer furoSondagem)
"""

import io


# ---------------------------------------------------------------------------
# Constantes (unidades CAD = metros)
# ---------------------------------------------------------------------------
PAL_X      = 0.0    # X da linha do palito
PAL_Y0     = 0.0    # Y do topo do palito (prof=0)

NSPT_X     = 0.20   # NSPT à direita do palito
NSPT_H     = 0.20   # altura do texto NSPT

DESC_X     = -0.20  # Descrição à esquerda (alinhamento RIGHT)
DESC_H     = 0.085  # altura do texto de descrição
DESC_LARG  = 2.4    # comprimento das linhas de horizonte

CAB_X      = 0.20   # Cabeçalho à direita do palito
CAB_H_TXT  = 0.185  # altura do texto do cabeçalho

NA_X       = 2.4    # NA bem à direita
NA_H       = 0.20

# Layers (nomes exatos do modelo)
LY_PALITO  = "furoSondagem"
LY_NSPT    = "BR100"
LY_DESC    = "BR60"
LY_NA      = "Nível D'Água"
LY_IMPEN   = "Impenetrável"
LY_GEOT    = "BGEOT-VT"     # hachuras/polígonos de solo

# Hachuras por tipo de solo
_HACHURAS = {
    "argila":        ("ANSI31",  45, 0.05),
    "argiloso":      ("ANSI31",  45, 0.05),
    "silte":         ("ANSI37",  45, 0.04),
    "siltoso":       ("ANSI37",  45, 0.04),
    "areia":         ("AR-SAND",  0, 0.08),
    "arenoso":       ("AR-SAND",  0, 0.08),
    "pedregulho":    ("AR-CONC",  0, 0.05),
    "organico":      ("GRASS",    0, 0.10),
    "orgânico":      ("GRASS",    0, 0.10),
    "aterro":        ("ANSI32",   0, 0.08),
    "rocha":         ("ANSI36",   0, 0.07),
    "impenetrável":  ("SOLID",    0, 1.00),
}


def _y(prof: float) -> float:
    return PAL_Y0 - prof


def _hachura(desc: str) -> tuple:
    d = desc.lower()
    for k, v in _HACHURAS.items():
        if k in d:
            return v
    return ("ANSI31", 45, 0.05)


def _agrupar(metros: list) -> list:
    """Agrupa metros consecutivos com mesma descrição em horizontes."""
    if not metros:
        return []
    hs = []
    dc = (metros[0].descricao or "").strip()
    oc = (metros[0].origem or "").strip()
    ini = metros[0].prof_m - 1.0
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
    for nome, cor, lw in [
        (LY_PALITO, 7,  50),   # branco/preto
        (LY_NSPT,   3,  -3),   # verde
        (LY_DESC,   3,  -3),   # verde
        (LY_NA,     5,  25),   # azul
        (LY_IMPEN,  1,  25),   # vermelho
        (LY_GEOT,   8,  -3),   # cinza
    ]:
        if nome not in doc.layers:
            l = doc.layers.add(nome, color=cor)
            l.dxf.lineweight = lw


def _exportar(doc) -> bytes:
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
    with tempfile.NamedTemporaryFile(suffix=".dxf", delete=False) as tmp:
        tmp_path = tmp.name
    doc.saveas(tmp_path)
    with open(tmp_path, "rb") as f:
        data = f.read()
    os.unlink(tmp_path)
    return data


def _palito(msp, sond, dist: float, hachura: bool, ox: float = 0.0):
    """Desenha um palito completo deslocado por ox."""
    from ezdxf.enums import TextEntityAlignment as TA

    metros   = sond.metros
    prof_max = sond.profundidade_total
    y_topo   = _y(0.0)
    y_fundo  = _y(prof_max)

    def X(v): return v + ox

    # ----------------------------------------------------------------
    # CABEÇALHO — MTEXT à direita do palito
    # Texto multilinha: "SP-XXX\nALT: X,XXX\nDIST: X,XXX"
    # ----------------------------------------------------------------
    cab_txt = (f"{sond.nome}\\PALT: {sond.cota_boca:.3f}".replace(".", ",")
               + f"\\PDIST: {dist:.3f}".replace(".", ","))

    msp.add_mtext(
        cab_txt,
        dxfattribs={
            "layer":           LY_NSPT,
            "char_height":     CAB_H_TXT,
            "attachment_point": 7,   # BOTTOM_LEFT
            "insert":          (X(CAB_X), y_topo + 0.5),
        }
    )

    # ----------------------------------------------------------------
    # PALITO — linha vertical simples
    # ----------------------------------------------------------------
    msp.add_line(
        (X(PAL_X), y_topo),
        (X(PAL_X), y_fundo),
        dxfattribs={"layer": LY_PALITO, "lineweight": 50}
    )

    # Divisórias a cada metro
    for m in range(1, int(prof_max) + 1):
        ym = _y(float(m))
        msp.add_line(
            (X(PAL_X), ym),
            (X(PAL_X - 0.5), ym),
            dxfattribs={"layer": LY_PALITO, "lineweight": 13}
        )

    # ----------------------------------------------------------------
    # NSPT — MTEXT à direita do palito, centrado no metro
    # ----------------------------------------------------------------
    for m in metros:
        yc = _y(m.prof_m - 0.5)
        msp.add_mtext(
            str(m.nspt),
            dxfattribs={
                "layer":           LY_NSPT,
                "char_height":     NSPT_H,
                "attachment_point": 5,  # MIDDLE_CENTER
                "insert":          (X(NSPT_X), yc),
            }
        )

    # ----------------------------------------------------------------
    # HORIZONTES — descrição à esquerda + linha de horizonte + hachura
    # ----------------------------------------------------------------
    horizontes = _agrupar(metros)

    for h in horizontes:
        yi   = _y(h["pi"])
        yf   = _y(h["pf"])
        ym   = (yi + yf) / 2.0
        alt  = abs(yi - yf)

        # Linha horizontal de limite do horizonte (saindo do palito para esquerda)
        msp.add_line(
            (X(PAL_X), yi),
            (X(PAL_X - DESC_LARG), yi),
            dxfattribs={"layer": LY_PALITO, "lineweight": 13}
        )

        # Descrição: "ORIG - Descrição.: prof_ini-prof_fim"
        pi_str = f"{h['pi']:.2f}".replace(".", ",")
        pf_str = f"{h['pf']:.2f}".replace(".", ",")
        if h["orig"]:
            desc_txt = f"{h['orig']} - {h['desc']}.: {pi_str}-{pf_str}"
        else:
            desc_txt = f"{h['desc']}.: {pi_str}-{pf_str}"

        msp.add_mtext(
            desc_txt,
            dxfattribs={
                "layer":           LY_DESC,
                "char_height":     DESC_H,
                "attachment_point": 6,   # MIDDLE_RIGHT
                "insert":          (X(DESC_X), ym),
                "width":           DESC_LARG,
            }
        )

        # Hachura
        if hachura and alt > 0.05:
            pat, ang, sc = _hachura(h["desc"])
            try:
                ha = msp.add_hatch(dxfattribs={"layer": LY_GEOT})
                ha.set_pattern_fill(pat, scale=sc, angle=ang)
                ha.paths.add_polyline_path([
                    (X(PAL_X),              yi),
                    (X(PAL_X - DESC_LARG),  yi),
                    (X(PAL_X - DESC_LARG),  yf),
                    (X(PAL_X),              yf),
                ], is_closed=True)
            except Exception:
                pass

    # ----------------------------------------------------------------
    # NÍVEL D'ÁGUA
    # ----------------------------------------------------------------
    if sond.nivel_dagua and sond.nivel_dagua > 0:
        yna    = _y(sond.nivel_dagua)
        na_str = f"NA:{sond.nivel_dagua:.2f}".replace(".", ",")
        # Linha indicativa
        msp.add_line(
            (X(PAL_X), yna),
            (X(PAL_X + 0.3), yna),
            dxfattribs={"layer": LY_NA, "lineweight": 25}
        )
        msp.add_mtext(
            na_str,
            dxfattribs={
                "layer":           LY_NA,
                "char_height":     NA_H,
                "attachment_point": 4,   # MIDDLE_LEFT
                "insert":          (X(NA_X), yna),
            }
        )

    # ----------------------------------------------------------------
    # LIMITE DE SONDAGEM
    # ----------------------------------------------------------------
    # Linha final + rodapé
    msp.add_line(
        (X(PAL_X), y_fundo),
        (X(PAL_X - DESC_LARG), y_fundo),
        dxfattribs={"layer": LY_IMPEN, "lineweight": 50}
    )
    # Linhas tracejadas de impenetrável
    for dx in [0.3, 0.6, 0.9, 1.2]:
        msp.add_line(
            (X(PAL_X - dx + 0.1), y_fundo),
            (X(PAL_X - dx - 0.1), y_fundo - 0.15),
            dxfattribs={"layer": LY_IMPEN, "lineweight": 25}
        )

    # Texto rodapé
    prof_str = f"Prof.={prof_max:.2f}m".replace(".", ",")
    msp.add_mtext(
        prof_str,
        dxfattribs={
            "layer":           LY_NSPT,
            "char_height":     CAB_H_TXT,
            "attachment_point": 5,   # MIDDLE_CENTER
            "insert":          (X(CAB_X), y_fundo - 0.5),
        }
    )


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
