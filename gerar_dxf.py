"""
gerar_dxf.py — Gera DXF de palito SPT para Civil 3D.

Usa sondagempadrao.dxf como template (HEADER+TABLES+BLOCKS+OBJECTS)
e substitui apenas a seção ENTITIES pelas entidades geradas dinamicamente.

Layout baseado no modelo original:
  Escala: 1 unidade CAD = 1 metro
  Palito: x=4.15 a x=4.25 (largura 0.1m), layer furoSondagem
  Hachura SOLID: metros ímpares, layer BGEOT-VT
  NSPT: x=4.40, h=0.2, layer BR100, fonte ARIAL
  Desc: x=4.00, h=0.085, layer BR60, fonte ARIAL
  Cab:  x=4.40, h=0.185, layer BR100
"""

import gzip, base64, os, re

# ---------------------------------------------------------------------------
# Layout — coordenadas exatas do modelo
# ---------------------------------------------------------------------------
PAL_XE   = 4.15
PAL_XD   = 4.25
PAL_XC   = 4.20

NSPT_X   = 4.40
DESC_X   = 4.00
DESC_W   = 4.00
CAB_X    = 4.40
CAB_W    = 5.00
NA_X     = 6.60
PROF_X   = 4.80
BALIZA_X = 4.60
HORIZ_X  = 1.77

H_CAB    = 0.185
H_NSPT   = 0.20
H_DESC   = 0.085
H_NA     = 0.20
H_PROF   = 0.20

LY_PAL   = "furoSondagem"
LY_NSPT  = "BR100"
LY_DESC  = "BR60"
LY_NA    = "Nivel Dagua"
LY_IMPEN = "Impenetravel"
LY_HACH  = "BGEOT-VT"


# ---------------------------------------------------------------------------
# Template (carregado dos arquivos .gz64)
# ---------------------------------------------------------------------------
_CACHE = {}

def _tmpl():
    if _CACHE:
        return _CACHE['b'], _CACHE['a']
    base = os.path.dirname(os.path.abspath(__file__))
    search_dirs = [base, os.getcwd(), os.path.expanduser('~')]
    for key, fname in [('b', 'dxf_before.gz64'), ('a', 'dxf_after.gz64')]:
        loaded = False
        for d in search_dirs:
            path = os.path.join(d, fname)
            if os.path.exists(path):
                with open(path) as f:
                    data = f.read().strip()
                _CACHE[key] = gzip.decompress(base64.b64decode(data)).decode('latin-1')
                loaded = True
                break
        if not loaded:
            raise FileNotFoundError(
                f"{fname} não encontrado. Coloque junto com gerar_dxf.py."
            )
    return _CACHE['b'], _CACHE['a']


# ---------------------------------------------------------------------------
# Handles
# ---------------------------------------------------------------------------
_H = [0x8000]

def _h():
    _H[0] += 1
    return f"{_H[0]:X}"


def _y(prof):
    return _y.topo - prof

_y.topo = 17.116


# ---------------------------------------------------------------------------
# Primitivas DXF (estrutura idêntica ao modelo)
# ---------------------------------------------------------------------------

def _line(x1, y1, x2, y2, layer):
    return (
        f"  0\nLINE\n  5\n{_h()}\n330\n1F\n"
        f"100\nAcDbEntity\n  8\n{layer}\n"
        f"100\nAcDbLine\n"
        f" 10\n{x1}\n 20\n{y1}\n 30\n0.0\n"
        f" 11\n{x2}\n 21\n{y2}\n 31\n0.0\n"
    )


def _esc_mtext(text: str) -> str:
    """Escapa caracteres não-ASCII no texto MTEXT usando notação DXF \\U+XXXX."""
    out = []
    for c in text:
        if ord(c) > 127:
            out.append(f"\\U+{ord(c):04X}")
        else:
            out.append(c)
    return ''.join(out)


def _mtext(text, x, y, height, layer, width=4.0, attach=1):
    n_lines = text.count('^J') + 1
    box_h   = round(height * n_lines * 1.5, 6)
    safe    = _esc_mtext(text)
    return (
        f"  0\nMTEXT\n  5\n{_h()}\n330\n1F\n"
        f"100\nAcDbEntity\n  8\n{layer}\n"
        f"100\nAcDbMText\n"
        f" 10\n{x}\n 20\n{y}\n 30\n0.0\n"
        f" 40\n{height}\n"
        f" 41\n{width}\n"
        f" 46\n{box_h}\n"
        f" 71\n{attach:6d}\n"
        f" 72\n     5\n"
        f"  1\n{safe}\n"
        f"  7\nARIAL\n"
        f" 73\n     1\n"
        f" 44\n1.0\n"
    )


def _hatch_solid(x1, y1, x2, y2, layer):
    cx = (x1 + x2) / 2.0
    cy = (y1 + y2) / 2.0
    return (
        f"  0\nHATCH\n  5\n{_h()}\n330\n1F\n"
        f"100\nAcDbEntity\n  8\n{layer}\n"
        f"100\nAcDbHatch\n"
        f" 10\n0.0\n 20\n0.0\n 30\n0.0\n"
        f"210\n0.0\n220\n0.0\n230\n1.0\n"
        f"  2\nSOLID\n"
        f" 70\n     1\n"
        f" 71\n     0\n"
        f" 91\n        1\n"
        f" 92\n        2\n"
        f" 72\n     0\n"
        f" 73\n     1\n"
        f" 93\n        4\n"
        f" 10\n{x1}\n 20\n{y1}\n"
        f" 10\n{x2}\n 20\n{y1}\n"
        f" 10\n{x2}\n 20\n{y2}\n"
        f" 10\n{x1}\n 20\n{y2}\n"
        f" 97\n        0\n"
        f" 75\n     0\n"
        f" 76\n     1\n"
        f" 98\n        1\n"
        f" 10\n{cx}\n 20\n{cy}\n"
        f"450\n        0\n451\n        0\n"
        f"460\n0.0\n461\n0.0\n"
        f"452\n        0\n462\n0.0\n"
        f"453\n        0\n470\n\n"
    )


def _lwpoly(pts, layer):
    n = len(pts)
    verts = "".join(f" 10\n{x}\n 20\n{y}\n" for x, y in pts)
    return (
        f"  0\nLWPOLYLINE\n  5\n{_h()}\n330\n1F\n"
        f"100\nAcDbEntity\n  8\n{layer}\n"
        f"100\nAcDbPolyline\n"
        f" 90\n{n:8d}\n"
        f" 70\n     1\n"
        f" 43\n0.0\n"
        + verts
    )


def _insert(block, x, y, scale, layer):
    return (
        f"  0\nINSERT\n  5\n{_h()}\n330\n1F\n"
        f"100\nAcDbEntity\n  8\n{layer}\n"
        f"100\nAcDbBlockReference\n"
        f"  2\n{block}\n"
        f" 10\n{x}\n 20\n{y}\n 30\n0.0\n"
        f" 41\n{scale}\n 42\n{scale}\n 43\n{scale}\n"
    )


# ---------------------------------------------------------------------------
# Agrupamento
# ---------------------------------------------------------------------------

def _agrupar(metros):
    if not metros:
        return []
    hs  = []
    dc  = (metros[0].descricao or "").strip()
    oc  = (metros[0].origem    or "").strip()
    ini = metros[0].prof_m - 1.0
    for i, m in enumerate(metros):
        d = (m.descricao or "").strip()
        o = (m.origem    or "").strip()
        if d and d != dc and i > 0:
            hs.append({"pi": ini, "pf": m.prof_m - 1.0, "desc": dc, "orig": oc})
            ini = m.prof_m - 1.0
            dc  = d
        if o:
            oc = o
    hs.append({"pi": ini, "pf": metros[-1].prof_m, "desc": dc, "orig": oc})
    return hs


# ---------------------------------------------------------------------------
# Palito
# ---------------------------------------------------------------------------

def _palito(sond, dist, hachura, ox=0.0):
    metros   = sond.metros
    prof_max = sond.profundidade_total
    _y.topo  = 17.116
    y_topo   = _y(0.0)
    y_fundo  = _y(prof_max)

    def X(v): return v + ox

    out = []

    # Cabeçalho
    cab = (f"{sond.nome}^J"
           f"ALT: {sond.cota_boca:.3f}^J"
           f"DIST: {dist:.3f}").replace(".", ",")
    out.append(_mtext(cab, X(CAB_X), y_topo + 0.6, H_CAB, LY_NSPT,
                      width=CAB_W, attach=4))

    # Linha vertical BR100 acima do cabeçalho
    out.append(_line(X(PAL_XC), y_topo, X(PAL_XC), y_topo + 0.8, LY_NSPT))

    # Borda do palito (LWPOLYLINE)
    out.append(_lwpoly([
        (X(PAL_XE), y_topo),
        (X(PAL_XD), y_topo),
        (X(PAL_XD), y_fundo),
        (X(PAL_XE), y_fundo),
    ], LY_PAL))

    # NSPT por metro
    for m in metros:
        ym = _y(m.prof_m)
        yc = _y(m.prof_m - 0.5)
        out.append(_line(X(PAL_XC), ym, X(PAL_XC), ym, LY_PAL))
        out.append(_mtext(str(m.nspt), X(NSPT_X), yc, H_NSPT, LY_NSPT,
                          width=3.0, attach=1))

    # Horizontes
    for h in _agrupar(metros):
        yi  = _y(h["pi"])
        yf  = _y(h["pf"])
        ym  = (yi + yf) / 2.0

        out.append(_line(X(PAL_XC), yi, X(PAL_XC), yi, LY_PAL))
        out.append(_line(X(PAL_XE), yi, X(HORIZ_X), yi, LY_PAL))

        pi_s = f"{h['pi']:.2f}".replace(".", ",")
        pf_s = f"{h['pf']:.2f}".replace(".", ",")
        txt  = (f"{h['orig']} - {h['desc']}.: {pi_s}-{pf_s}"
                if h["orig"] else f"{h['desc']}.: {pi_s}-{pf_s}")
        out.append(_mtext(txt, X(DESC_X), ym, H_DESC, LY_DESC,
                          width=DESC_W, attach=3))

    # Hachura SOLID metros ímpares + linhas baliza
    if hachura:
        for m in range(1, int(prof_max) + 1):
            yt = _y(m - 1)
            yb = _y(m)
            if m % 2 == 1:
                out.append(_hatch_solid(X(PAL_XE), yb, X(PAL_XD), yt, LY_HACH))
            out.append(_line(X(BALIZA_X), yb, X(PAL_XD), yb, LY_HACH))

    # Nível d'água (INSERT do bloco NA)
    if sond.nivel_dagua and sond.nivel_dagua > 0:
        yna = _y(sond.nivel_dagua)
        out.append(_insert("NA", X(NA_X), yna, 0.2, LY_HACH))
        out.append(_mtext(f"NA:{sond.nivel_dagua:.2f}".replace(".", ","),
                          X(NA_X), yna, H_NA, LY_NSPT, width=2.0, attach=8))

    # Impenetrável (INSERT do bloco)
    out.append(_insert("impenetravel", X(PAL_XC), y_fundo, 0.2, "BLC"))

    # Rodapé
    out.append(_mtext(f"Prof.={prof_max:.2f}m".replace(",", "X").replace(".", ",").replace("X", "."),
                      X(PROF_X), y_fundo - 0.5, H_PROF, LY_NSPT,
                      width=1.0, attach=5))

    return "".join(out)


# ---------------------------------------------------------------------------
# Build DXF
# ---------------------------------------------------------------------------

_LATIN1_SUBS = str.maketrans({
    '\u2019': "'", '\u2018': "'", '\u201c': '"', '\u201d': '"',
    '\u2013': '-', '\u2014': '-', '\u00b0': chr(176),
})

def _para_latin1(txt: str) -> str:
    """Converte string Python para latin-1 seguro, preservando acentos."""
    import unicodedata
    txt = txt.translate(_LATIN1_SUBS)
    # Tentar encode direto
    try:
        txt.encode('latin-1')
        return txt
    except UnicodeEncodeError:
        pass
    # Normalizar NFC → decompõe e recompõe caracteres compostos
    txt = unicodedata.normalize('NFC', txt)
    # Converter char a char
    out = []
    for c in txt:
        try:
            c.encode('latin-1')
            out.append(c)
        except UnicodeEncodeError:
            # Tentar NFKD para extrair base ASCII
            nfkd = unicodedata.normalize('NFKD', c)
            base = ''.join(ch for ch in nfkd if not unicodedata.combining(ch))
            out.append(base if base else '?')
    return ''.join(out)


def _build(entidades):
    _H[0] = 0x8000
    before, after = _tmpl()
    dxf_txt = before + entidades + after

    # Atualizar $HANDSEED para ser maior que o maior handle gerado
    novo_seed = f"{_H[0] + 0x100:X}"
    dxf_txt = re.sub(r'(\$HANDSEED\n\s*5\n)([0-9A-Fa-f]+)',
                     r'\g<1>' + novo_seed, dxf_txt, count=1)

    # Converter para CRLF (o modelo original usa CRLF)
    dxf_txt = dxf_txt.replace('\r\n', '\n').replace('\n', '\r\n')

    return dxf_txt.encode('latin-1', errors='replace')


# ---------------------------------------------------------------------------
# API pública
# ---------------------------------------------------------------------------

def gerar_dxf_sondagem(sondagem, distancia=0.0, incluir_hachura=True):
    """Gera DXF de um único palito SPT. Retorna bytes."""
    return _build(_palito(sondagem, distancia, incluir_hachura, ox=0.0))


def gerar_dxf_multiplas(sondagens, distancias=None,
                        espacamento_x=15.0, incluir_hachura=True):
    """Gera DXF com múltiplos palitos lado a lado. Retorna bytes."""
    if not sondagens:
        return b""
    if distancias is None:
        distancias = [0.0] * len(sondagens)
    ents = "".join(
        _palito(s, d, incluir_hachura, ox=i * espacamento_x)
        for i, (s, d) in enumerate(zip(sondagens, distancias))
        if s.metros
    )
    return _build(ents)
