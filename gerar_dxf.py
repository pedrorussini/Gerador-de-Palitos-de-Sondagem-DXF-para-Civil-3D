"""
gerar_dxf.py — Gera arquivo DXF com palito de sondagem SPT para Civil 3D.

Layout baseado no modelo padrão brasileiro:
  - Cabeçalho: identificação, altitude (ALT), distância (DIST)
  - Coluna central: retângulo vertical dividido por metro (o "palito")
  - Esquerda do palito: valores NSPT por metro
  - Direita do palito: descrição dos horizontes com traço de mudança
  - Direita externo: origem geológica (SRM, SRJ, SS...)
  - Marcação de NA (nível d'água) com texto "NA:X,XX"
  - Rodapé: profundidade total

Escala: 1:100 — cada metro = 1 unidade CAD (1 cm no papel 1:100)
Origem do bloco: (0, 0) = canto superior esquerdo do cabeçalho
"""

import math
from typing import Optional


# ---------------------------------------------------------------------------
# Dimensões do bloco (em unidades CAD — escala 1:100)
# ---------------------------------------------------------------------------
ESCALA        = 1.0      # metros → unidades CAD (1m = 1 unidade)
PAL_LARGURA   = 1.2      # largura do palito central (unidades CAD)
PAL_X0        = 4.0      # X esquerda do palito
PAL_Y0        = 3.5      # Y topo do palito (abaixo do cabeçalho)

NSPT_X        = PAL_X0 - 0.3   # X dos números NSPT (à esquerda, centralizado)
NSPT_MAX_PX   = 5.0             # largura máxima da barra NSPT em unidades CAD
NSPT_ESCALA   = NSPT_MAX_PX / 50.0   # 50 golpes → 5 unidades

DESC_X        = PAL_X0 + PAL_LARGURA + 0.5   # X início das descrições
ORIG_X        = DESC_X + 6.5                  # X origem geológica

ALT_TEXT_X    = PAL_X0 + PAL_LARGURA / 2     # X centralizado para rótulos
CAB_ALTURA    = 3.0       # altura do cabeçalho

TXT_H_GRANDE  = 0.35      # altura texto cabeçalho
TXT_H_NORMAL  = 0.25      # altura texto padrão
TXT_H_PEQUENO = 0.20      # altura texto notas

# Hachuras por tipo de solo (padrão AutoCAD)
HACHURA_TIPO = {
    "argila":   ("ANSI31", 45,  0.8),   # linhas diagonais
    "silte":    ("ANSI37", 45,  0.5),   # grade diagonal
    "areia":    ("AR-SAND", 0,  1.0),   # pontilhado areia
    "organico": ("GRASS",   0,  1.5),   # gramado
    "aterro":   ("ANSI32",  0,  1.0),   # tracejado
    "rocha":    ("ANSI36",  0,  1.0),   # rocha
    "default":  ("ANSI31",  45, 1.0),
}

# Layers
LY_PALITO     = "SONDAGEM_PALITO"
LY_NSPT       = "SONDAGEM_NSPT"
LY_TEXTO      = "SONDAGEM_TEXTO"
LY_HACHURA    = "SONDAGEM_HACHURA"
LY_CABECALHO  = "SONDAGEM_CABECALHO"
LY_NA         = "SONDAGEM_NA"
LY_LIMITE     = "SONDAGEM_LIMITE"


def _tipo_solo(descricao: str) -> str:
    d = descricao.lower()
    if any(k in d for k in ["orgânico", "organico", "turfa", "vegetal"]):
        return "organico"
    if any(k in d for k in ["rocha", "gnaisse", "granito", "impenetrável"]):
        return "rocha"
    if any(k in d for k in ["aterro"]):
        return "aterro"
    if "argila" in d:
        return "argila"
    if "silte" in d:
        return "silte"
    if "areia" in d:
        return "areia"
    return "default"


def _y_prof(prof_m: float) -> float:
    """Converte profundidade (metros) para coordenada Y (negativo = para baixo)."""
    return -(PAL_Y0 + prof_m * ESCALA)


def gerar_dxf_sondagem(
    sondagem,             # SondagemSPT
    distancia: float = 0.0,
    incluir_hachura: bool = True,
) -> bytes:
    """
    Gera o DXF de um palito de sondagem SPT.

    Parâmetros:
        sondagem    : SondagemSPT com metros, nome, cota_boca, nivel_dagua
        distancia   : distância ao longo do eixo (para DIST no cabeçalho)
        incluir_hachura: se True, adiciona hachuras por tipo de solo

    Retorna bytes do arquivo DXF.
    """
    try:
        import ezdxf
        from ezdxf import colors
        from ezdxf.enums import TextEntityAlignment
    except ImportError:
        raise ImportError("Instale ezdxf: pip install ezdxf")

    doc  = ezdxf.new("R2010")
    doc.units = 6   # metros
    msp  = doc.modelspace()

    # Criar layers
    for nome, cor in [
        (LY_CABECALHO, 7),   # branco/preto
        (LY_PALITO,    7),
        (LY_NSPT,      5),   # azul
        (LY_TEXTO,     7),
        (LY_HACHURA,   8),   # cinza
        (LY_NA,        5),   # azul
        (LY_LIMITE,    3),   # verde
    ]:
        if nome not in doc.layers:
            doc.layers.add(nome, color=cor)

    metros  = sondagem.metros
    if not metros:
        return b""

    prof_max = sondagem.profundidade_total
    y_fundo  = _y_prof(prof_max)

    # ----------------------------------------------------------------
    # CABEÇALHO
    # ----------------------------------------------------------------
    # Caixa do cabeçalho
    msp.add_lwpolyline(
        [(PAL_X0 - 3.5, 0), (PAL_X0 + PAL_LARGURA + 8.5, 0),
         (PAL_X0 + PAL_LARGURA + 8.5, -PAL_Y0),
         (PAL_X0 - 3.5, -PAL_Y0), (PAL_X0 - 3.5, 0)],
        dxfattribs={"layer": LY_CABECALHO, "lineweight": 50}
    )

    # Identificação da sondagem
    msp.add_text(
        sondagem.nome,
        dxfattribs={
            "layer": LY_CABECALHO,
            "height": TXT_H_GRANDE,
            "style": "Standard",
        }
    ).set_placement(
        (ALT_TEXT_X, -0.5),
        align=TextEntityAlignment.MIDDLE_CENTER
    )

    # ALT e DIST
    msp.add_text(
        f"ALT: {sondagem.cota_boca:.3f}",
        dxfattribs={"layer": LY_CABECALHO, "height": TXT_H_NORMAL}
    ).set_placement(
        (ALT_TEXT_X, -1.1),
        align=TextEntityAlignment.MIDDLE_CENTER
    )
    msp.add_text(
        f"DIST: {distancia:.3f}",
        dxfattribs={"layer": LY_CABECALHO, "height": TXT_H_NORMAL}
    ).set_placement(
        (ALT_TEXT_X, -1.7),
        align=TextEntityAlignment.MIDDLE_CENTER
    )

    # Linha separadora cabeçalho / palito
    msp.add_line(
        (PAL_X0 - 3.5, -PAL_Y0),
        (PAL_X0 + PAL_LARGURA + 8.5, -PAL_Y0),
        dxfattribs={"layer": LY_CABECALHO, "lineweight": 50}
    )

    # ----------------------------------------------------------------
    # PALITO — retângulo externo e divisões por metro
    # ----------------------------------------------------------------
    # Borda externa do palito
    msp.add_lwpolyline(
        [(PAL_X0, _y_prof(0)),
         (PAL_X0 + PAL_LARGURA, _y_prof(0)),
         (PAL_X0 + PAL_LARGURA, y_fundo),
         (PAL_X0, y_fundo),
         (PAL_X0, _y_prof(0))],
        dxfattribs={"layer": LY_PALITO, "lineweight": 50}
    )

    # Linha divisória a cada metro + número de profundidade
    for m in range(1, int(prof_max) + 2):
        y = _y_prof(float(m))
        if y >= y_fundo - 0.01:
            # Traço de divisão
            msp.add_line(
                (PAL_X0, y),
                (PAL_X0 + PAL_LARGURA, y),
                dxfattribs={"layer": LY_PALITO, "lineweight": 13}
            )
            # Número de profundidade (a cada metro par ou total)
            if m % 2 == 0 or m == int(prof_max):
                msp.add_text(
                    str(m),
                    dxfattribs={"layer": LY_TEXTO, "height": TXT_H_NORMAL}
                ).set_placement(
                    (PAL_X0 + PAL_LARGURA + 0.15, y + 0.1),
                    align=TextEntityAlignment.LEFT
                )

    # ----------------------------------------------------------------
    # NSPT — valores e barra horizontal por metro
    # ----------------------------------------------------------------
    for metro in metros:
        y_centro = _y_prof(metro.prof_m - 0.5)  # centro do metro
        nspt = metro.nspt

        # Número NSPT à esquerda do palito
        msp.add_text(
            str(nspt),
            dxfattribs={"layer": LY_NSPT, "height": TXT_H_NORMAL}
        ).set_placement(
            (PAL_X0 - 0.15, y_centro + 0.1),
            align=TextEntityAlignment.RIGHT
        )

        # Barra horizontal de NSPT (dentro do palito)
        larg_barra = min(nspt * NSPT_ESCALA, PAL_LARGURA)
        if larg_barra > 0:
            y_bar = _y_prof(metro.prof_m - 0.5)
            msp.add_lwpolyline(
                [(PAL_X0, y_bar - 0.15),
                 (PAL_X0 + larg_barra, y_bar - 0.15),
                 (PAL_X0 + larg_barra, y_bar + 0.15),
                 (PAL_X0, y_bar + 0.15),
                 (PAL_X0, y_bar - 0.15)],
                dxfattribs={"layer": LY_NSPT, "closed": True}
            )

    # ----------------------------------------------------------------
    # HORIZONTES — descrição, traços de mudança e origem
    # ----------------------------------------------------------------
    # Agrupar metros por horizonte (descrição igual e contígua)
    horizontes = []
    desc_cur = metros[0].descricao if metros else ""
    orig_cur = metros[0].origem if metros else ""
    prof_ini = 0.0

    for i, metro in enumerate(metros):
        desc = metro.descricao or desc_cur
        orig = metro.origem or orig_cur
        if desc != desc_cur and metro.descricao:
            horizontes.append({
                "prof_ini": prof_ini,
                "prof_fim": metro.prof_m - 1.0,
                "desc": desc_cur,
                "orig": orig_cur,
            })
            prof_ini = metro.prof_m - 1.0
            desc_cur = desc
            orig_cur = orig

    # Último horizonte
    horizontes.append({
        "prof_ini": prof_ini,
        "prof_fim": prof_max,
        "desc": desc_cur,
        "orig": orig_cur,
    })

    for h in horizontes:
        y_ini = _y_prof(h["prof_ini"])
        y_fim = _y_prof(h["prof_fim"])
        y_meio = (y_ini + y_fim) / 2

        # Traço de mudança de horizonte (na profundidade de início, exceto topo)
        if h["prof_ini"] > 0:
            msp.add_line(
                (PAL_X0 + PAL_LARGURA, y_ini),
                (DESC_X + 5.5, y_ini),
                dxfattribs={"layer": LY_TEXTO, "lineweight": 13,
                            "linetype": "DASHED"}
            )

        # Texto da descrição — quebrar se longo
        desc = h["desc"]
        # Limitar comprimento e dividir em linhas de ~25 chars
        palavras = desc.split()
        linhas = []
        linha_atual = ""
        for p in palavras:
            if len(linha_atual) + len(p) + 1 <= 25:
                linha_atual += (" " + p if linha_atual else p)
            else:
                if linha_atual:
                    linhas.append(linha_atual)
                linha_atual = p
        if linha_atual:
            linhas.append(linha_atual)

        # Inserir linhas de texto centralizadas verticalmente no horizonte
        n_linhas = len(linhas)
        espaco_disponivel = abs(y_fim - y_ini)
        espaco_texto = n_linhas * TXT_H_NORMAL * 1.5
        y_txt_start = y_meio + (espaco_texto / 2) - TXT_H_NORMAL * 0.75

        for j, linha in enumerate(linhas[:4]):  # máximo 4 linhas
            y_txt = y_txt_start - j * TXT_H_NORMAL * 1.5
            msp.add_text(
                linha,
                dxfattribs={"layer": LY_TEXTO, "height": TXT_H_PEQUENO}
            ).set_placement(
                (DESC_X, y_txt),
                align=TextEntityAlignment.LEFT
            )

        # Origem geológica
        if h["orig"]:
            msp.add_text(
                h["orig"],
                dxfattribs={"layer": LY_TEXTO, "height": TXT_H_PEQUENO}
            ).set_placement(
                (ORIG_X, y_meio + 0.1),
                align=TextEntityAlignment.LEFT
            )

        # Hachura por tipo de solo
        if incluir_hachura and espaco_disponivel > 0.1:
            tipo = _tipo_solo(h["desc"])
            pat, ang, scale = HACHURA_TIPO.get(tipo, HACHURA_TIPO["default"])
            try:
                hatch = msp.add_hatch(
                    color=colors.GRAY,
                    dxfattribs={"layer": LY_HACHURA}
                )
                hatch.set_pattern_fill(pat, scale=scale, angle=ang)
                hatch.paths.add_polyline_path([
                    (PAL_X0, y_ini),
                    (PAL_X0 + PAL_LARGURA, y_ini),
                    (PAL_X0 + PAL_LARGURA, y_fim),
                    (PAL_X0, y_fim),
                ], is_closed=True)
            except Exception:
                pass  # hachura opcional — não interrompe se falhar

    # ----------------------------------------------------------------
    # NÍVEL D'ÁGUA
    # ----------------------------------------------------------------
    if sondagem.nivel_dagua is not None:
        y_na = _y_prof(sondagem.nivel_dagua)
        na_x = PAL_X0 + PAL_LARGURA + 1.0

        # Seta indicando NA
        msp.add_line(
            (PAL_X0 + PAL_LARGURA, y_na),
            (na_x + 1.5, y_na),
            dxfattribs={"layer": LY_NA, "lineweight": 25}
        )
        # Texto NA
        msp.add_text(
            f"NA:{sondagem.nivel_dagua:.2f}".replace(".", ","),
            dxfattribs={"layer": LY_NA, "height": TXT_H_NORMAL}
        ).set_placement(
            (na_x + 1.6, y_na + 0.1),
            align=TextEntityAlignment.LEFT
        )

    # ----------------------------------------------------------------
    # LIMITE DE SONDAGEM — linha dupla e texto no rodapé
    # ----------------------------------------------------------------
    # Linha de limite (tracejada)
    msp.add_line(
        (PAL_X0, y_fundo),
        (PAL_X0 + PAL_LARGURA, y_fundo),
        dxfattribs={"layer": LY_LIMITE, "lineweight": 50}
    )
    # Texto profundidade total
    msp.add_text(
        f"Prof.={prof_max:.2f}m".replace(".", ","),
        dxfattribs={"layer": LY_TEXTO, "height": TXT_H_NORMAL}
    ).set_placement(
        (ALT_TEXT_X, y_fundo - 0.5),
        align=TextEntityAlignment.MIDDLE_CENTER
    )

    # ----------------------------------------------------------------
    # Exportar para bytes
    # ----------------------------------------------------------------
    import io
    buf = io.BytesIO()
    doc.write(buf)
    return buf.getvalue()


def gerar_dxf_multiplas(
    sondagens: list,
    distancias: list = None,
    espacamento_x: float = 15.0,
    incluir_hachura: bool = True,
) -> bytes:
    """
    Gera um único DXF com múltiplas sondagens lado a lado.

    Parâmetros:
        sondagens    : lista de SondagemSPT
        distancias   : lista de distâncias correspondentes (opcional)
        espacamento_x: distância horizontal entre palitos (unidades CAD)
        incluir_hachura: incluir hachuras de solo
    """
    try:
        import ezdxf
        from ezdxf.enums import TextEntityAlignment
    except ImportError:
        raise ImportError("Instale ezdxf: pip install ezdxf")

    if not sondagens:
        return b""

    if distancias is None:
        distancias = [0.0] * len(sondagens)

    doc  = ezdxf.new("R2010")
    doc.units = 6
    msp  = doc.modelspace()

    for nome, cor in [
        (LY_CABECALHO, 7), (LY_PALITO, 7),
        (LY_NSPT, 5), (LY_TEXTO, 7),
        (LY_HACHURA, 8), (LY_NA, 5), (LY_LIMITE, 3),
    ]:
        if nome not in doc.layers:
            doc.layers.add(nome, color=cor)

    for i, (sond, dist) in enumerate(zip(sondagens, distancias)):
        offset_x = i * espacamento_x

        # Gerar geometria individual e inserir com offset
        metros = sond.metros
        if not metros:
            continue

        prof_max = sond.profundidade_total
        y_fundo  = _y_prof(prof_max)

        def add_text(texto, x, y, altura, layer, align=None):
            from ezdxf.enums import TextEntityAlignment as TEA
            ent = msp.add_text(
                texto,
                dxfattribs={"layer": layer, "height": altura}
            )
            al = align or TEA.LEFT
            ent.set_placement((x + offset_x, y), align=al)

        def add_line(x1, y1, x2, y2, layer, lw=13):
            msp.add_line(
                (x1 + offset_x, y1), (x2 + offset_x, y2),
                dxfattribs={"layer": layer, "lineweight": lw}
            )

        from ezdxf.enums import TextEntityAlignment as TEA

        # Cabeçalho
        cx = ALT_TEXT_X + offset_x
        msp.add_lwpolyline(
            [(PAL_X0 + offset_x - 3.5, 0),
             (PAL_X0 + offset_x + PAL_LARGURA + 8.5, 0),
             (PAL_X0 + offset_x + PAL_LARGURA + 8.5, -PAL_Y0),
             (PAL_X0 + offset_x - 3.5, -PAL_Y0),
             (PAL_X0 + offset_x - 3.5, 0)],
            dxfattribs={"layer": LY_CABECALHO, "lineweight": 50}
        )
        for txt, dy in [(sond.nome, -0.5), (f"ALT: {sond.cota_boca:.3f}", -1.1),
                        (f"DIST: {dist:.3f}", -1.7)]:
            t = msp.add_text(txt, dxfattribs={"layer": LY_CABECALHO,
                                               "height": TXT_H_NORMAL})
            t.set_placement((cx, dy), align=TEA.MIDDLE_CENTER)

        add_line(PAL_X0 - 3.5, -PAL_Y0, PAL_X0 + PAL_LARGURA + 8.5, -PAL_Y0,
                 LY_CABECALHO, 50)

        # Palito
        msp.add_lwpolyline(
            [(PAL_X0 + offset_x, _y_prof(0)),
             (PAL_X0 + offset_x + PAL_LARGURA, _y_prof(0)),
             (PAL_X0 + offset_x + PAL_LARGURA, y_fundo),
             (PAL_X0 + offset_x, y_fundo),
             (PAL_X0 + offset_x, _y_prof(0))],
            dxfattribs={"layer": LY_PALITO, "lineweight": 50}
        )

        for m in range(1, int(prof_max) + 2):
            y = _y_prof(float(m))
            if y >= y_fundo - 0.01:
                add_line(PAL_X0, y, PAL_X0 + PAL_LARGURA, y, LY_PALITO, 13)
                if m % 2 == 0 or m == int(prof_max):
                    add_text(str(m), PAL_X0 + PAL_LARGURA + 0.15,
                             y + 0.1, TXT_H_NORMAL, LY_TEXTO)

        # NSPT
        for metro in metros:
            y_c = _y_prof(metro.prof_m - 0.5)
            nspt = metro.nspt
            add_text(str(nspt), PAL_X0 - 0.15, y_c + 0.1,
                     TXT_H_NORMAL, LY_NSPT)
            larg = min(nspt * NSPT_ESCALA, PAL_LARGURA)
            if larg > 0:
                msp.add_lwpolyline(
                    [(PAL_X0 + offset_x, y_c - 0.15),
                     (PAL_X0 + offset_x + larg, y_c - 0.15),
                     (PAL_X0 + offset_x + larg, y_c + 0.15),
                     (PAL_X0 + offset_x, y_c + 0.15),
                     (PAL_X0 + offset_x, y_c - 0.15)],
                    dxfattribs={"layer": LY_NSPT, "closed": True}
                )

        # Horizontes
        hs = []
        dc = metros[0].descricao if metros else ""
        oc = metros[0].origem if metros else ""
        pi = 0.0
        for metro in metros:
            d = metro.descricao or dc
            o = metro.origem or oc
            if d != dc and metro.descricao:
                hs.append({"prof_ini": pi, "prof_fim": metro.prof_m - 1.0,
                           "desc": dc, "orig": oc})
                pi = metro.prof_m - 1.0; dc = d; oc = o
        hs.append({"prof_ini": pi, "prof_fim": prof_max, "desc": dc, "orig": oc})

        for h in hs:
            yi = _y_prof(h["prof_ini"]); yf = _y_prof(h["prof_fim"])
            ym = (yi + yf) / 2
            if h["prof_ini"] > 0:
                add_line(PAL_X0 + PAL_LARGURA, yi, DESC_X + 5.5, yi, LY_TEXTO, 13)

            palavras = h["desc"].split()
            linhas = []; la = ""
            for p in palavras:
                if len(la) + len(p) + 1 <= 25:
                    la += (" " + p if la else p)
                else:
                    if la: linhas.append(la)
                    la = p
            if la: linhas.append(la)

            n = len(linhas)
            et = n * TXT_H_NORMAL * 1.5
            ys = ym + et / 2 - TXT_H_NORMAL * 0.75
            for j, ln in enumerate(linhas[:4]):
                add_text(ln, DESC_X, ys - j * TXT_H_NORMAL * 1.5,
                         TXT_H_PEQUENO, LY_TEXTO)
            if h["orig"]:
                add_text(h["orig"], ORIG_X, ym + 0.1, TXT_H_PEQUENO, LY_TEXTO)

            if incluir_hachura and abs(yf - yi) > 0.1:
                tipo = _tipo_solo(h["desc"])
                pat, ang, scale = HACHURA_TIPO.get(tipo, HACHURA_TIPO["default"])
                try:
                    from ezdxf import colors
                    hatch = msp.add_hatch(color=colors.GRAY,
                                          dxfattribs={"layer": LY_HACHURA})
                    hatch.set_pattern_fill(pat, scale=scale, angle=ang)
                    hatch.paths.add_polyline_path([
                        (PAL_X0 + offset_x, yi),
                        (PAL_X0 + offset_x + PAL_LARGURA, yi),
                        (PAL_X0 + offset_x + PAL_LARGURA, yf),
                        (PAL_X0 + offset_x, yf),
                    ], is_closed=True)
                except Exception:
                    pass

        # NA
        if sond.nivel_dagua is not None:
            y_na = _y_prof(sond.nivel_dagua)
            add_line(PAL_X0 + PAL_LARGURA, y_na,
                     PAL_X0 + PAL_LARGURA + 2.5, y_na, LY_NA, 25)
            add_text(f"NA:{sond.nivel_dagua:.2f}".replace(".", ","),
                     PAL_X0 + PAL_LARGURA + 2.6, y_na + 0.1, TXT_H_NORMAL, LY_NA)

        # Rodapé
        add_line(PAL_X0, y_fundo, PAL_X0 + PAL_LARGURA, y_fundo, LY_LIMITE, 50)
        t = msp.add_text(
            f"Prof.={prof_max:.2f}m".replace(".", ","),
            dxfattribs={"layer": LY_TEXTO, "height": TXT_H_NORMAL}
        )
        t.set_placement((cx, y_fundo - 0.5), align=TEA.MIDDLE_CENTER)

    import io
    buf = io.BytesIO()
    doc.write(buf)
    return buf.getvalue()
