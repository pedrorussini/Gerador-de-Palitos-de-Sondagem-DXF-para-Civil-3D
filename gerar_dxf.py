"""
gerar_dxf.py — Gera DXF de palito SPT escrevendo o formato diretamente.

Sem dependência de ezdxf — escreve entidades DXF como texto puro.
Compatível com AutoCAD / Civil 3D (formato R2010 / AC1024).

Estrutura baseada no modelo sondagempadrao.dxf:
  - Escala: 10 unidades CAD = 1 metro real
  - Palito: layer furoSondagem, linha vertical em x=0
  - NSPT:   layer BR100, MTEXT à direita (x=+2), fonte ARIAL h=2.0
  - Desc:   layer BR60,  MTEXT à esquerda (x=-2), fonte ARIAL h=0.85
  - Hachura: layer BGEOT-VT, SOLID alternado nos metros ímpares
"""

import io

# ---------------------------------------------------------------------------
# Escala e posições (10 unidades = 1 metro)
# ---------------------------------------------------------------------------
S          = 10.0   # fator de escala

PAL_X      = 0.0
NSPT_X     = 2.0
DESC_X     = -2.0
DESC_LARG  = 24.0
CAB_X      = 2.0
NA_X       = 24.0

H_NSPT     = 2.0
H_DESC     = 0.85
H_CAB      = 1.85
H_NA       = 2.0

LY_PAL   = "furoSondagem"
LY_NSPT  = "BR100"
LY_DESC  = "BR60"
LY_NA    = "Nível D'Água"
LY_IMPEN = "Impenetrável"
LY_HACH  = "BGEOT-VT"


def _y(prof: float) -> float:
    return -(prof * S)


def _agrupar(metros: list) -> list:
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


# ---------------------------------------------------------------------------
# Escritores de entidades DXF
# ---------------------------------------------------------------------------

_handle = [1000]  # contador de handles

def _h():
    _handle[0] += 1
    return f"{_handle[0]:X}"


def line(x1, y1, x2, y2, layer, lw=25):
    return (
        f"  0\nLINE\n  5\n{_h()}\n"
        f"100\nAcDbEntity\n  8\n{layer}\n"
        f"100\nAcDbLine\n"
        f" 10\n{x1:.4f}\n 20\n{y1:.4f}\n 30\n0.0\n"
        f" 11\n{x2:.4f}\n 21\n{y2:.4f}\n 31\n0.0\n"
    )


def mtext(text, x, y, height, layer, width=0.0, attach=7):
    """
    attach: 1=TL 2=TC 3=TR 4=ML 5=MC 6=MR 7=BL 8=BC 9=BR
    """
    w = f" 41\n{width:.4f}\n" if width > 0 else ""
    return (
        f"  0\nMTEXT\n  5\n{_h()}\n"
        f"100\nAcDbEntity\n  8\n{layer}\n"
        f"100\nAcDbMText\n"
        f" 10\n{x:.4f}\n 20\n{y:.4f}\n 30\n0.0\n"
        f" 40\n{height:.4f}\n"
        f"{w}"
        f" 71\n{attach}\n"
        f"  7\nARIAL\n"
        f"  1\n{text}\n"
    )


def hatch_solid(x1, y1, x2, y2, layer):
    """Hachura SOLID para retângulo definido por (x1,y1)-(x2,y2)."""
    return (
        f"  0\nHATCH\n  5\n{_h()}\n"
        f"100\nAcDbEntity\n  8\n{layer}\n 62\n     7\n"
        f"100\nAcDbHatch\n"
        f" 10\n0.0\n 20\n0.0\n 30\n0.0\n"
        f"210\n0.0\n220\n0.0\n230\n1.0\n"
        f"  2\nSOLID\n"
        f" 70\n     1\n"   # solid fill
        f" 71\n     0\n"   # not associative
        f" 91\n     1\n"   # 1 boundary path
        f" 92\n     1\n"   # external boundary
        f" 93\n     4\n"   # 4 vertices
        f" 10\n{x1:.4f}\n 20\n{y1:.4f}\n"
        f" 10\n{x2:.4f}\n 20\n{y1:.4f}\n"
        f" 10\n{x2:.4f}\n 20\n{y2:.4f}\n"
        f" 10\n{x1:.4f}\n 20\n{y2:.4f}\n"
        f" 97\n     0\n"   # no source objects
        f" 75\n     1\n"   # hatch style = normal
        f" 76\n     1\n"   # predefined pattern
        f" 47\n0.0\n"      # pixel size
        f" 98\n     0\n"   # no seed points
    )


# ---------------------------------------------------------------------------
# Cabeçalho DXF
# ---------------------------------------------------------------------------

def _header():
    return """\
  0
SECTION
  2
HEADER
  9
$ACADVER
  1
AC1015
  9
$INSUNITS
 70
6
  9
$MEASUREMENT
 70
1
  0
ENDSEC
"""


def _tables():
    return (
        "  0\nSECTION\n  2\nTABLES\n"
        # LAYER table
        "  0\nTABLE\n  2\nLAYER\n  5\n2\n100\nAcDbSymbolTable\n 70\n    20\n"
        + _layer_entry(LY_PAL,   7,  0)
        + _layer_entry(LY_NSPT,  3,  0)   # verde
        + _layer_entry(LY_DESC,  3,  0)   # verde
        + _layer_entry(LY_NA,    5,  0)   # azul
        + _layer_entry(LY_IMPEN, 1,  0)   # vermelho
        + _layer_entry(LY_HACH,  8,  0)   # cinza
        + "  0\nENDTAB\n"
        # STYLE table com ARIAL
        "  0\nTABLE\n  2\nSTYLE\n  5\n3\n100\nAcDbSymbolTable\n 70\n     2\n"
        "  0\nSTYLE\n  5\n11\n100\nAcDbSymbolTableRecord\n100\nAcDbTextStyleTableRecord\n"
        "  2\nStandard\n 70\n     0\n 40\n0.0\n 41\n1.0\n 50\n0.0\n 71\n     0\n"
        "  3\ntxt\n  4\n\n"
        "  0\nSTYLE\n  5\n12\n100\nAcDbSymbolTableRecord\n100\nAcDbTextStyleTableRecord\n"
        "  2\nARIAL\n 70\n     0\n 40\n0.0\n 41\n1.0\n 50\n0.0\n 71\n     0\n"
        "  3\narial.ttf\n  4\n\n"
        "  0\nENDTAB\n"
        "  0\nENDSEC\n"
    )


def _layer_entry(name, color, lw):
    return (
        f"  0\nLAYER\n  5\n{_h()}\n"
        f"100\nAcDbSymbolTableRecord\n"
        f"100\nAcDbLayerTableRecord\n"
        f"  2\n{name}\n"
        f" 70\n     0\n"
        f" 62\n{color:6d}\n"
        f"  6\nContinuous\n"
        f"370\n    25\n"
    )


def _blocks():
    return (
        "  0\nSECTION\n  2\nBLOCKS\n"
        "  0\nBLOCK\n  5\nF0\n100\nAcDbEntity\n  8\n0\n"
        "100\nAcDbBlockBegin\n  2\n*Model_Space\n 70\n     0\n"
        " 10\n0.0\n 20\n0.0\n 30\n0.0\n  3\n*Model_Space\n  1\n\n"
        "  0\nENDBLK\n  5\nF1\n100\nAcDbEntity\n  8\n0\n100\nAcDbBlockEnd\n"
        "  0\nENDSEC\n"
    )


# ---------------------------------------------------------------------------
# Construção do palito
# ---------------------------------------------------------------------------

def _palito_str(sond, dist: float, hachura: bool, ox: float = 0.0) -> str:
    metros   = sond.metros
    prof_max = sond.profundidade_total
    y_topo   = _y(0.0)
    y_fundo  = _y(prof_max)
    out      = []

    def X(v): return v + ox

    # Cabeçalho: MTEXT multilinha à direita
    cab = f"{sond.nome}\\PALT: {sond.cota_boca:.3f}".replace(".", ",")
    cab += f"\\PDIST: {dist:.3f}".replace(".", ",")
    out.append(mtext(cab, X(CAB_X), y_topo + S*0.5, H_CAB, LY_NSPT, attach=7))

    # Linha vertical do palito
    out.append(line(X(PAL_X), y_topo, X(PAL_X), y_fundo, LY_PAL, lw=50))

    # Divisórias a cada metro + NSPT
    for m in metros:
        ym = _y(float(m.prof_m))
        # Traço curto saindo do palito
        out.append(line(X(PAL_X), ym, X(PAL_X - 5.0), ym, LY_PAL, lw=13))
        # NSPT centralizado no metro, à direita
        yc = _y(m.prof_m - 0.5)
        out.append(mtext(str(m.nspt), X(NSPT_X), yc, H_NSPT, LY_NSPT, attach=5))

    # Horizontes: descrição + linha de limite
    for h in _agrupar(metros):
        yi  = _y(h["pi"])
        yf  = _y(h["pf"])
        ym  = (yi + yf) / 2.0

        # Linha horizontal de limite do horizonte
        out.append(line(X(PAL_X), yi, X(PAL_X - DESC_LARG), yi, LY_PAL, lw=13))

        # Texto: "ORIG - Desc.: pi-pf"
        pi_s = f"{h['pi']:.2f}".replace(".", ",")
        pf_s = f"{h['pf']:.2f}".replace(".", ",")
        txt  = (f"{h['orig']} - {h['desc']}.: {pi_s}-{pf_s}"
                if h["orig"] else f"{h['desc']}.: {pi_s}-{pf_s}")
        # Quebrar linhas longas com \P (MTEXT newline)
        out.append(mtext(txt, X(DESC_X), ym, H_DESC, LY_DESC,
                         width=DESC_LARG, attach=6))

    # Hachura SOLID alternada — metros ímpares
    if hachura:
        for m in range(1, int(prof_max) + 1):
            if m % 2 == 1:
                yt = _y(float(m - 1))
                yb = _y(float(m))
                out.append(hatch_solid(X(PAL_X - 5.0), yb,
                                       X(PAL_X + 5.0), yt, LY_HACH))

    # NA
    if sond.nivel_dagua and sond.nivel_dagua > 0:
        yna    = _y(sond.nivel_dagua)
        na_str = f"NA:{sond.nivel_dagua:.2f}".replace(".", ",")
        out.append(line(X(PAL_X), yna, X(PAL_X + 3.0), yna, LY_NA, lw=25))
        out.append(mtext(na_str, X(NA_X), yna, H_NA, LY_NA, attach=4))

    # Limite de sondagem
    out.append(line(X(PAL_X), y_fundo, X(PAL_X - DESC_LARG), y_fundo, LY_IMPEN, lw=50))
    for dx in [3.0, 6.0, 9.0, 12.0, 15.0]:
        out.append(line(X(PAL_X - dx + 1.0), y_fundo,
                        X(PAL_X - dx - 1.0), y_fundo - 1.5, LY_IMPEN, lw=25))

    # Rodapé
    prof_str = f"Prof.={prof_max:.2f}m".replace(".", ",")
    out.append(mtext(prof_str, X(CAB_X + 4.0), y_fundo - S*0.5, H_CAB, LY_NSPT, attach=5))

    return "".join(out)


# ---------------------------------------------------------------------------
# Montar DXF completo
# ---------------------------------------------------------------------------

def _build_dxf(entidades: str) -> bytes:
    _handle[0] = 100  # reset handle
    dxf = (
        _header()
        + _tables()
        + _blocks()
        + "  0\nSECTION\n  2\nENTITIES\n"
        + entidades
        + "  0\nENDSEC\n"
        + "  0\nEOF\n"
    )
    return dxf.encode("latin-1", errors="replace")


# ---------------------------------------------------------------------------
# Funções públicas
# ---------------------------------------------------------------------------

def gerar_dxf_sondagem(
    sondagem,
    distancia: float = 0.0,
    incluir_hachura: bool = True,
) -> bytes:
    """Gera DXF de um único palito SPT. Retorna bytes."""
    _handle[0] = 100
    ents = _palito_str(sondagem, distancia, incluir_hachura, ox=0.0)
    return _build_dxf(ents)


def gerar_dxf_multiplas(
    sondagens: list,
    distancias: list = None,
    espacamento_x: float = 150.0,
    incluir_hachura: bool = True,
) -> bytes:
    """Gera DXF com múltiplos palitos lado a lado. Retorna bytes."""
    if not sondagens:
        return b""
    if distancias is None:
        distancias = [0.0] * len(sondagens)
    _handle[0] = 100
    ents = ""
    for i, (sond, dist) in enumerate(zip(sondagens, distancias)):
        if sond.metros:
            ents += _palito_str(sond, dist, incluir_hachura, ox=i * espacamento_x)
    return _build_dxf(ents)
