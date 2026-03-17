"""
leitor_sondagem.py — Extração de dados SPT de PDFs de sondagem.

Modo 1 (automático): parsers por empresa (Geoloc, New Solos, Souli, Suporte SM)
Modo 2 (bbox manual): recebe coordenadas de seleção do usuário → extração precisa
"""

import re
import io
from dataclasses import dataclass
from typing import Optional, List, Tuple


# ---------------------------------------------------------------------------
# Estruturas de dados
# ---------------------------------------------------------------------------

@dataclass
class MetroSPT:
    prof_m:    float
    nspt:      int
    golpes_1:  int = 0
    golpes_2:  int = 0
    golpes_3:  int = 0
    descricao: str = ""
    origem:    str = ""


@dataclass
class SondagemSPT:
    nome:             str
    cota_boca:        float
    nivel_dagua:      Optional[float]
    metros:           List[MetroSPT]

    @property
    def profundidade_total(self) -> float:
        return self.metros[-1].prof_m if self.metros else 0.0


# ---------------------------------------------------------------------------
# Utilitários compartilhados
# ---------------------------------------------------------------------------

_ORIGENS = {"SS","SRM","SRJ","SR","AT","AL","DFL","SAR","RC","SP","SDL","SM","SO","SRS"}

_KW_SOLO = [
    "argila","areia","silte","solo","pedregulho","rocha","aterro",
    "gnaisse","granito","limite","vegetal","orgânico","organico","turfoso",
]

def _tem_solo(s: str) -> bool:
    return any(kw in s.lower() for kw in _KW_SOLO)

def _num(s: str) -> Optional[float]:
    try:
        return float(str(s).replace(',', '.').strip())
    except Exception:
        return None

def _extrair_cota(texto: str) -> Optional[float]:
    m = re.search(r"[Cc]ota\s+da\s+boca\s+do\s+furo[:\s]+([0-9.,]+)", texto)
    if m:
        return _num(m.group(1))
    m = re.search(r"[Cc]ota\s*[:\s]+([0-9.,]+)\s*m", texto)
    return _num(m.group(1)) if m else None

def _extrair_nivel(texto: str) -> Optional[float]:
    if re.search(r"[Aa]usente|nao\s+observado|n\.?\s*o\.", texto, re.IGNORECASE):
        return None
    m = re.search(r"[Nn][íi]vel\s+d['\u2019]?\s*[áa]gua[:\s]+([0-9.,]+)", texto)
    return _num(m.group(1)) if m else None

def _extrair_nome(texto: str, idx: int) -> str:
    for pat in [
        r'\b(S[MP]-[0-9A-Z]+-[0-9]+-[0-9]+[A-Z0-9]*)\b',
        r'\b(S[MP]-[0-9A-Z]+-[0-9]+)\b',
        r'\b(PM-?\d+[A-Z]?)\b',
    ]:
        m = re.search(pat, texto)
        if m:
            return m.group(1)
    return f"Sondagem-{idx+1}"

def _limpar_desc(s: str) -> str:
    s = s.strip()
    s = re.sub(r'^[\d/\s]+(?=[A-ZÁÉÍÓÚ])', '', s)
    s = re.sub(r'^\d{1,2}[,.]\d{2}\s+', '', s)
    s = re.sub(r'\s+\d{1,2}\s*$', '', s)
    s = re.sub(r'\s+\d+/\d+\s*$', '', s)
    tokens = s.split()
    tokens_limpos = [t for t in tokens if t.upper() not in _ORIGENS]
    s = " ".join(tokens_limpos).strip().rstrip(',.')
    return s.upper() if s else ""


# ---------------------------------------------------------------------------
# MODO 2: Extração por bbox selecionado pelo usuário
# ---------------------------------------------------------------------------

def extrair_cabecalho_bbox(pagina, bbox: Tuple[float, float, float, float]) -> dict:
    """
    Extrai nome, cota_boca e nivel_dagua de uma região selecionada pelo usuário.
    bbox = (x0, top, x1, bottom) em coordenadas do PDF (pontos).
    Retorna dict com chaves: nome, cota_boca, nivel_dagua
    """
    x0, top, x1, bottom = bbox
    crop = pagina.crop((x0, top, x1, bottom))
    texto = crop.extract_text() or ""

    nome = _extrair_nome(texto, 0)
    cota = _extrair_cota(texto) or 0.0
    nivel = _extrair_nivel(texto)

    return {
        "nome":        nome,
        "cota_boca":   cota,
        "nivel_dagua": nivel,
        "texto_raw":   texto,  # para debug/revisão
    }


def extrair_tabela_bbox(pagina, bbox: Tuple[float, float, float, float]) -> List[MetroSPT]:
    """
    Extrai metros SPT de uma região selecionada pelo usuário.
    bbox = (x0, top, x1, bottom) em coordenadas do PDF.

    Estratégia layout-agnóstica:
    1. Extrai todas as palavras com posição x/y dentro do bbox
    2. Detecta a coluna de escala vertical (números sequenciais 1..N)
    3. Detecta colunas de golpes (números 0-60 fora da escala e descrição)
    4. Detecta coluna de descrição (texto com palavras geotécnicas)
    5. Agrupa tudo por metro e monta MetroSPT
    """
    import pdfplumber
    from collections import Counter

    x0_bbox, top_bbox, x1_bbox, bot_bbox = bbox
    crop = pagina.crop((x0_bbox, top_bbox, x1_bbox, bot_bbox))
    words = crop.extract_words(use_text_flow=False, keep_blank_chars=False)

    if not words:
        return []

    W = x1_bbox - x0_bbox
    H = bot_bbox - top_bbox

    # --- Detectar escala vertical (números 1..N sequenciais) ---
    num_words = [w for w in words
                 if re.match(r"^\d{1,2}$", w["text"])
                 and 1 <= int(w["text"]) <= 30]

    if not num_words:
        return []

    # Agrupar candidatos por faixa de x → coluna mais frequente
    x_freq = Counter(round(w["x0"] / 5) * 5 for w in num_words)
    x_escala_approx = x_freq.most_common(1)[0][0]

    escala_words = [w for w in num_words
                    if abs(w["x0"] - x_escala_approx) < W * 0.06]

    # Validar sequência (pelo menos 3 números consecutivos ou próximos)
    ns = sorted(set(int(w["text"]) for w in escala_words))
    if len(ns) < 2:
        return []

    # Montar metro_y: número → y_centro
    metro_y = {}
    for w in escala_words:
        n = int(w["text"])
        y = (w["top"] + w["bottom"]) / 2
        if n not in metro_y:
            metro_y[n] = y

    metros_lista = sorted(metro_y.keys())

    # Altura de um metro em pixels do crop
    alts = [abs(metro_y[metros_lista[i]] - metro_y[metros_lista[i-1]])
            for i in range(1, len(metros_lista))]
    altura_metro = sum(alts) / len(alts) if alts else H / max(len(metros_lista), 1)

    # --- Detectar limite da sondagem ---
    limite_words = [w for w in words
                    if any(k in w["text"].upper() for k in ["LIMITE", "IMPENET"])]
    y_limite = min((w["top"] for w in limite_words), default=H * 0.95)
    metro_y = {n: y for n, y in metro_y.items() if y <= y_limite + altura_metro}
    metros_lista = sorted(metro_y.keys())
    if not metros_lista:
        return []

    # --- Separar região de golpes da região de descrição ---
    # Palavras-solo indicam onde está a coluna de descrição
    kw_up = [k.upper() for k in _KW_SOLO]
    solo_words = [w for w in words
                  if any(k in w["text"].upper() for k in kw_up)]

    if solo_words:
        x_desc_inicio = min(w["x0"] for w in solo_words) - 5
    else:
        # Sem palavras-solo: assumir metade direita como descrição
        x_desc_inicio = W * 0.45

    # Golpes: inteiros 0–60, fora da escala e fora da descrição
    golpe_words = [w for w in words
                   if re.match(r"^\d{1,2}$", w["text"])
                   and 0 <= int(w["text"]) <= 60
                   and w["top"] <= y_limite + 10
                   and abs(w["x0"] - x_escala_approx) > W * 0.04
                   and w["x0"] < x_desc_inicio - 5]

    # Palavras de descrição: à direita de x_desc_inicio, abaixo do cabeçalho
    desc_words = [w for w in words
                  if w["x0"] >= x_desc_inicio - 10
                  and w["top"] <= y_limite + altura_metro
                  and len(w["text"]) > 1
                  and not re.match(r"^[\d,./:><=+\-]+$", w["text"])]

    # Palavras de origem (siglas): qualquer x
    orig_words = [w for w in words
                  if w["text"].upper() in _ORIGENS
                  and w["top"] <= y_limite + altura_metro]

    # --- Função: a qual metro pertence uma coordenada y? ---
    def _metro_de_y(y_pt):
        for i, n in enumerate(metros_lista):
            if n == 0:
                continue
            n_prev = metros_lista[i - 1]
            y_base = metro_y[n]
            y_topo = metro_y.get(n_prev, y_base - altura_metro)
            margem = altura_metro * 0.55
            if y_topo - margem <= y_pt <= y_base + margem:
                return n
        return None

    # --- Agrupar golpes por metro ---
    golpes_por_metro = {}
    for w in golpe_words:
        yc = (w["top"] + w["bottom"]) / 2
        n = _metro_de_y(yc)
        if n is not None:
            golpes_por_metro.setdefault(n, []).append(int(w["text"]))

    # --- Agrupar linhas de descrição ---
    desc_lines = []
    if desc_words:
        dw_sorted = sorted(desc_words, key=lambda w: w["top"])
        grupo = [dw_sorted[0]]
        for w in dw_sorted[1:]:
            if abs(w["top"] - grupo[-1]["top"]) < altura_metro * 0.4:
                grupo.append(w)
            else:
                desc_lines.append(grupo)
                grupo = [w]
        desc_lines.append(grupo)

    # Construir blocos de descrição com range de y
    blocos_desc = []
    if desc_lines:
        bloco_atual = [desc_lines[0]]
        for ln in desc_lines[1:]:
            y_gap = ln[0]["top"] - bloco_atual[-1][0]["top"]
            if y_gap < altura_metro * 1.6:
                bloco_atual.append(ln)
            else:
                y0 = bloco_atual[0][0]["top"]
                y1 = bloco_atual[-1][0]["bottom"]
                texto_bloco = " ".join(
                    " ".join(w["text"] for w in sorted(ln2, key=lambda w: w["x0"]))
                    for ln2 in bloco_atual
                )
                desc_limpa = _limpar_desc(texto_bloco)
                orig_bloco = ""
                for ow in orig_words:
                    if y0 - altura_metro * 0.5 <= ow["top"] <= y1 + altura_metro * 0.5:
                        orig_bloco = ow["text"].upper()
                        break
                if desc_limpa and _tem_solo(desc_limpa):
                    blocos_desc.append((y0, y1, desc_limpa, orig_bloco))
                bloco_atual = [ln]

        # Último bloco
        if bloco_atual:
            y0 = bloco_atual[0][0]["top"]
            y1 = bloco_atual[-1][0]["bottom"]
            texto_bloco = " ".join(
                " ".join(w["text"] for w in sorted(ln2, key=lambda w: w["x0"]))
                for ln2 in bloco_atual
            )
            desc_limpa = _limpar_desc(texto_bloco)
            orig_bloco = ""
            for ow in orig_words:
                if y0 - altura_metro * 0.5 <= ow["top"] <= y1 + altura_metro * 0.5:
                    orig_bloco = ow["text"].upper()
                    break
            if desc_limpa and _tem_solo(desc_limpa):
                blocos_desc.append((y0, y1, desc_limpa, orig_bloco))

    # --- Associar blocos de descrição → metros ---
    desc_por_metro = {}
    orig_por_metro = {}
    y_max = max(metro_y.values())

    for j, (y_ini, y_fim, texto, orig) in enumerate(blocos_desc):
        y_prox = blocos_desc[j + 1][0] if j + 1 < len(blocos_desc) else y_max + altura_metro
        for n in metros_lista:
            if n == 0:
                continue
            idx_n = metros_lista.index(n)
            n_prev = metros_lista[idx_n - 1]
            y_prev = metro_y.get(n_prev, metro_y[n] - altura_metro)
            yc = (y_prev + metro_y[n]) / 2
            if y_ini - altura_metro * 0.35 <= yc < y_prox and n not in desc_por_metro:
                desc_por_metro[n] = texto
                orig_por_metro[n] = orig

    # Preencher vazios com último valor
    ult_desc = ""
    ult_orig = ""
    for n in metros_lista:
        if n == 0:
            continue
        if n in desc_por_metro:
            ult_desc = desc_por_metro[n]
            ult_orig = orig_por_metro.get(n, "")
        else:
            desc_por_metro[n] = ult_desc
            orig_por_metro[n] = ult_orig

    # --- Montar MetroSPT ---
    metros_spt = []
    for n in metros_lista:
        if n == 0:
            continue
        gs = sorted(golpes_por_metro.get(n, []))
        gs_validos = [g for g in gs if g != 15]

        if len(gs_validos) >= 3:
            g1, g2, g3 = gs_validos[0], gs_validos[1], gs_validos[2]
        elif len(gs_validos) == 2:
            g1, g2, g3 = 0, gs_validos[0], gs_validos[1]
        elif len(gs) >= 3:
            g1, g2, g3 = gs[0], gs[1], gs[2]
        elif len(gs) == 2:
            g1, g2, g3 = 0, gs[0], gs[1]
        else:
            # Metro sem golpes detectados — adiciona com NSPT=0 para não perder a profundidade
            g1, g2, g3 = 0, 0, 0

        metros_spt.append(MetroSPT(
            prof_m=float(n),
            nspt=g2 + g3,
            golpes_1=g1,
            golpes_2=g2,
            golpes_3=g3,
            descricao=desc_por_metro.get(n, ""),
            origem=orig_por_metro.get(n, ""),
        ))

    return metros_spt




def bbox_canvas_para_pdf(
    rect: dict,
    canvas_w: float, canvas_h: float,
    pdf_w: float, pdf_h: float,
) -> Tuple[float, float, float, float]:
    """
    Converte um rect do streamlit-drawable-canvas → bbox PDF (x0, top, x1, bottom).
    rect tem chaves: left, top, width, height (em pixels do canvas).
    """
    sx = pdf_w / canvas_w
    sy = pdf_h / canvas_h
    x0 = rect["left"] * sx
    y0 = rect["top"] * sy
    x1 = (rect["left"] + rect["width"]) * sx
    y1 = (rect["top"] + rect["height"]) * sy
    return x0, y0, x1, y1


# ---------------------------------------------------------------------------
# MODO 1: Parsers automáticos (mantidos para fallback/compatibilidade)
# ---------------------------------------------------------------------------

def _extrair_cota_auto(texto): return _extrair_cota(texto)
def _extrair_nivel_auto(texto): return _extrair_nivel(texto)

_RE_GOLPES = re.compile(
    r'^(\d{1,2})\s+(\d{1,4})\s+(\d{1,4})'
    r'(?:\s+\d{1,4})?'
    r'(?:\s+(\d{1,4}))?'
    r'(?:\s+(.*))?$'
)
_RE_PROF  = re.compile(r'^(\d{1,2}[,.]\d{1,2})$')
_RE_INT_I = re.compile(r'^\d{1,2}$')


def _descompactar(s: str):
    v = _num(s)
    if v is None:
        return None, None
    if v <= 60:
        return int(v), None
    for split in [1, 2]:
        a, b = int(s[:split]), int(s[split:])
        if 0 <= a <= 60 and 0 <= b <= 60:
            return a, b
    return int(v), None


def _parse_pagina(texto: str):
    metros = []
    blocos = []
    prof_metro = 1.0
    desc_atual = ""
    orig_atual = ""
    prof_bloco_ini = 0.0

    def _fechar_bloco(prof_fim):
        nonlocal desc_atual, orig_atual, prof_bloco_ini
        if desc_atual:
            blocos.append({
                "prof_ini": prof_bloco_ini,
                "prof_fim": prof_fim,
                "desc": desc_atual,
                "orig": orig_atual,
            })
        desc_atual = ""
        orig_atual = ""
        prof_bloco_ini = prof_fim

    _IGNORAR_LINHAS = [
        "NEW SOLOS","GEOLOC","SUPORTE","SOULI","END:","Cliente:","Obra:",
        "Local:","Resp.","CREA","ENGENHEIRO","Escala","Revestimento:","Sistema:",
        "SPT Golpes","1ª 2ª 3ª","1ª + 2ª","Nº de Golpes","Resistência à Penetração",
        "Classificação do Material","Origem: SRJ","Origem: AT","Origem: SS",
        "CONFORME","NBR 6484","Sondagem de Reconhecimento","PERFIL INDIVIDUAL",
        "Norte:","Este:","Fuso:","Coordenadas","Datum:","Perfuração:",
        "Altura de queda","Peso:","Amostrador","Nível d",
    ]

    for linha in texto.split("\n"):
        linha = linha.strip()
        if not linha:
            continue
        if any(ign in linha for ign in _IGNORAR_LINHAS):
            continue
        if _RE_INT_I.match(linha):
            continue

        m_prof = _RE_PROF.match(linha)
        if m_prof:
            prof_val = _num(m_prof.group(1))
            if prof_val and 0 < prof_val <= 30:
                _fechar_bloco(prof_val)
            continue

        m_g = _RE_GOLPES.match(linha)
        if m_g:
            g1_raw, g2_raw, g3_raw = m_g.group(1), m_g.group(2), m_g.group(3)
            g1 = int(g1_raw) if _num(g1_raw) and _num(g1_raw) <= 60 else 0
            g2_v, g3_extra = _descompactar(g2_raw)
            if g3_extra is not None:
                g2, g3 = g2_v, g3_extra
            else:
                g2 = g2_v or 0
                g3_v, _ = _descompactar(g3_raw)
                g3 = g3_v or 0
            if not all(0 <= g <= 60 for g in [g1, g2, g3]):
                continue
            if g1 == 15 and g2 == 15 and g3 == 15:
                continue
            metros.append(MetroSPT(
                prof_m=round(prof_metro, 2),
                nspt=g2 + g3, golpes_1=g1, golpes_2=g2, golpes_3=g3,
            ))
            prof_metro += 1.0
            continue

        if _tem_solo(linha):
            desc_nova = _limpar_desc(linha)
            if desc_nova:
                if desc_atual and desc_nova != desc_atual:
                    _fechar_bloco(prof_metro - 1.0)
                desc_atual = desc_nova
            continue

    if desc_atual:
        blocos.append({
            "prof_ini": prof_bloco_ini,
            "prof_fim": 999.0,
            "desc": desc_atual,
            "orig": orig_atual,
        })

    return metros, blocos


def _associar(metros, blocos):
    if not blocos:
        return metros
    for metro in metros:
        for b in blocos:
            if b["prof_ini"] <= metro.prof_m <= b["prof_fim"]:
                metro.descricao = b["desc"]
                metro.origem    = b["orig"]
                break
    return metros


def _parse_geoloc_bbox(pagina) -> list:
    """Parser automático para layout Geoloc/New Solos (classificação à direita)."""
    _ORIGENS_LOCAL = {"SRM","SRJ","SS","AT","SR","AL","DFL","SAR","RC","SRR"}
    W = pagina.width; H = pagina.height
    words = pagina.extract_words(use_text_flow=False, keep_blank_chars=False)

    # Escala vertical (coluna de profundidade)
    escala_words = [w for w in words
                    if W*0.27 < w["x0"] < W*0.34
                    and w["top"] > H*0.22 and w["top"] < H*0.85
                    and re.match(r"^\d{1,2}$", w["text"])
                    and int(w["text"]) <= 20]
    metro_y = {}
    for w in escala_words:
        n = int(w["text"])
        y = (w["top"] + w["bottom"]) / 2
        if n not in metro_y:
            metro_y[n] = y

    if not metro_y:
        return []

    limite_words = [w for w in words
                    if w["top"] > H*0.30
                    and any(k in w["text"].upper() for k in ["LIMITE","IMPENET"])
                    and W*0.40 < w["x0"] < W*0.75]
    y_limite = min((w["top"] for w in limite_words), default=H*0.85)
    metro_y = {n: y for n, y in metro_y.items() if y <= y_limite + 30}
    if not metro_y:
        return []

    metros_lista = sorted(metro_y.keys())
    alts = [abs(metro_y[metros_lista[i]] - metro_y[metros_lista[i-1]])
            for i in range(1, len(metros_lista))]
    altura_metro = sum(alts) / len(alts) if alts else 28.0

    y_ultimo = max(metro_y.values())
    golpes_ind = [w for w in words
                  if W*0.04 < w["x0"] < W*0.18
                  and w["top"] > H*0.22 and w["top"] < y_ultimo + 15
                  and re.match(r"^\d{1,2}$", w["text"])
                  and 0 <= int(w["text"]) <= 60]

    def _y_metro(y_pt):
        for i, n in enumerate(metros_lista):
            if n == 0: continue
            n_prev = metros_lista[i-1]
            y_base = metro_y[n]
            y_topo = metro_y.get(n_prev, y_base - altura_metro)
            if y_topo - altura_metro*0.45 <= y_pt <= y_base + altura_metro*0.45:
                return n
        return None

    golpes_por_metro = {}
    for w in golpes_ind:
        yc = (w["top"] + w["bottom"]) / 2
        n = _y_metro(yc)
        if n is not None:
            golpes_por_metro.setdefault(n, []).append(int(w["text"]))

    # Descrições (coluna direita, x > 55% da largura)
    desc_words = [w for w in words
                  if w["x0"] > W*0.55
                  and w["top"] > H*0.22 and w["top"] < y_limite + altura_metro
                  and len(w["text"]) > 1
                  and not re.match(r"^[\d,./:><=]+$", w["text"])]

    origens_dir = {}
    orig_words = [w for w in words
                  if w["text"].upper() in _ORIGENS_LOCAL
                  and w["x0"] > W*0.88 and w["top"] > H*0.22]
    for w in orig_words:
        yr = round((w["top"] + w["bottom"]) / 2)
        if yr not in origens_dir:
            origens_dir[yr] = w["text"].upper()

    # Agrupar linhas de descrição em blocos
    desc_lines = []
    if desc_words:
        dw_s = sorted(desc_words, key=lambda w: w["top"])
        grp = [dw_s[0]]
        for w in dw_s[1:]:
            if abs(w["top"] - grp[-1]["top"]) < 5:
                grp.append(w)
            else:
                desc_lines.append(grp)
                grp = [w]
        desc_lines.append(grp)

    blocos_desc = []
    if desc_lines:
        bl = [desc_lines[0]]
        for ln in desc_lines[1:]:
            if ln[0]["top"] - bl[-1][0]["top"] < altura_metro * 1.5:
                bl.append(ln)
            else:
                y0 = bl[0][0]["top"]; y1 = bl[-1][0]["bottom"]
                texto_b = " ".join(
                    " ".join(w["text"] for w in sorted(g, key=lambda w: w["x0"])
                             if w["text"].upper() not in _ORIGENS_LOCAL)
                    for g in bl
                )
                desc_limpa = _limpar_desc(texto_b)
                orig_b = ""
                for yr, oo in origens_dir.items():
                    if y0 - 5 <= yr <= y1 + 20:
                        orig_b = oo; break
                if desc_limpa and _tem_solo(desc_limpa):
                    blocos_desc.append((y0, y1, desc_limpa, orig_b))
                bl = [ln]
        if bl:
            y0 = bl[0][0]["top"]; y1 = bl[-1][0]["bottom"]
            texto_b = " ".join(
                " ".join(w["text"] for w in sorted(g, key=lambda w: w["x0"])
                         if w["text"].upper() not in _ORIGENS_LOCAL)
                for g in bl
            )
            desc_limpa = _limpar_desc(texto_b)
            orig_b = ""
            for yr, oo in origens_dir.items():
                if y0 - 5 <= yr <= y1 + 20:
                    orig_b = oo; break
            if desc_limpa and _tem_solo(desc_limpa):
                blocos_desc.append((y0, y1, desc_limpa, orig_b))

    y_max = max(metro_y.values())
    desc_por_metro = {}; orig_por_metro = {}
    for j, (y_ini, y_fim, texto, orig) in enumerate(blocos_desc):
        y_prox = blocos_desc[j+1][0] if j+1 < len(blocos_desc) else y_max + altura_metro
        for n in metros_lista:
            if n == 0: continue
            n_prev = metros_lista[metros_lista.index(n)-1]
            yp = metro_y.get(n_prev, metro_y[n] - altura_metro)
            yc = (yp + metro_y[n]) / 2
            if y_ini - altura_metro*0.3 <= yc < y_prox and n not in desc_por_metro:
                desc_por_metro[n] = texto
                orig_por_metro[n] = orig

    ult_d = ""; ult_o = ""
    for n in metros_lista:
        if n == 0: continue
        if n in desc_por_metro:
            ult_d = desc_por_metro[n]; ult_o = orig_por_metro.get(n, "")
        else:
            desc_por_metro[n] = ult_d; orig_por_metro[n] = ult_o

    metros_spt = []
    for n in metros_lista:
        if n == 0: continue
        gs = sorted(golpes_por_metro.get(n, []))
        gv = [g for g in gs if g != 15]
        if len(gv) >= 3:   g1,g2,g3 = gv[0],gv[1],gv[2]
        elif len(gv) == 2: g1,g2,g3 = 0,gv[0],gv[1]
        elif len(gs) >= 3: g1,g2,g3 = gs[0],gs[1],gs[2]
        else: continue
        metros_spt.append(MetroSPT(
            prof_m=float(n), nspt=g2+g3,
            golpes_1=g1, golpes_2=g2, golpes_3=g3,
            descricao=desc_por_metro.get(n,""),
            origem=orig_por_metro.get(n,""),
        ))

    # ── Capturar profundidades decimais do texto da coluna direita ──────────
    # Ex: "10,13 Pedregulhos de gnaisse." ou "10,23 LIMITE DE SONDAGEM"
    # Essas profundidades aparecem no texto mas não na escala vertical.
    _RE_PROF_DEC = re.compile(r'\b(\d{1,2}[,.][\d]{2})\b')
    prof_inteiros = {m.prof_m for m in metros_spt}
    prof_max_inteiro = max(prof_inteiros) if prof_inteiros else 0.0

    # Palavras da coluna direita com profundidades decimais > último metro inteiro
    dec_candidates = [w for w in words
                      if w["x0"] > W*0.40
                      and w["top"] >= y_ultimo - altura_metro*0.5
                      and w["top"] <= y_limite + altura_metro*2
                      and _RE_PROF_DEC.match(w["text"])]

    # Também varrer o texto extraído próximo ao limite
    texto_limite = pagina.crop((W*0.40, y_ultimo - altura_metro, W, y_limite + altura_metro*2)).extract_text() or ""
    for m in _RE_PROF_DEC.finditer(texto_limite):
        val = _num(m.group(1))
        if val and val > prof_max_inteiro and val not in prof_inteiros:
            # Herdar golpes e descrição do último metro inteiro
            ultimo = metros_spt[-1] if metros_spt else None
            desc_dec = ""
            # Tentar capturar texto após a profundidade decimal
            pos = m.end()
            trecho = texto_limite[pos:pos+120].strip()
            if _tem_solo(trecho):
                desc_dec = _limpar_desc(trecho.split('\n')[0])
            elif ultimo:
                desc_dec = ultimo.descricao
            orig_dec = ultimo.origem if ultimo else ""
            metros_spt.append(MetroSPT(
                prof_m=round(val, 2),
                nspt=ultimo.nspt if ultimo else 0,
                golpes_1=ultimo.golpes_1 if ultimo else 0,
                golpes_2=ultimo.golpes_2 if ultimo else 0,
                golpes_3=ultimo.golpes_3 if ultimo else 0,
                descricao=desc_dec,
                origem=orig_dec,
            ))
            prof_inteiros.add(val)

    # Também capturar de palavras individuais (ex: "10,13" como token isolado)
    for w in dec_candidates:
        val = _num(_RE_PROF_DEC.match(w["text"]).group(1))
        if val and val > prof_max_inteiro and val not in prof_inteiros:
            ultimo = metros_spt[-1] if metros_spt else None
            metros_spt.append(MetroSPT(
                prof_m=round(val, 2),
                nspt=ultimo.nspt if ultimo else 0,
                golpes_1=ultimo.golpes_1 if ultimo else 0,
                golpes_2=ultimo.golpes_2 if ultimo else 0,
                golpes_3=ultimo.golpes_3 if ultimo else 0,
                descricao=ultimo.descricao if ultimo else "",
                origem=ultimo.origem if ultimo else "",
            ))
            prof_inteiros.add(val)

    metros_spt.sort(key=lambda m: m.prof_m)
    return metros_spt


def _parse_bbox_esquerda(pagina) -> list:
    """Parser automático para layouts Souli/Suporte SM (golpes à esquerda)."""
    from collections import Counter as _Counter
    _ORIGENS_SET = {"SS","SRM","SRJ","SR","AT","AL","DFL","SAR","RC","SP","SDL","SRS"}
    _KW_UP = [k.upper() for k in _KW_SOLO]

    W = pagina.width; H = pagina.height
    words = pagina.extract_words(use_text_flow=False, keep_blank_chars=False)

    esc_cands = [w for w in words
                 if re.match(r"^\d{1,2}$", w["text"])
                 and int(w["text"]) <= 30 and w["top"] > H*0.20]
    if len(esc_cands) < 3:
        return []

    x_freq = _Counter(round(w["x0"]/10)*10 for w in esc_cands)
    x_esc = x_freq.most_common(1)[0][0]

    metro_y = {}
    for w in esc_cands:
        if abs(w["x0"] - x_esc) < 25:
            n = int(w["text"]); y = (w["top"]+w["bottom"])/2
            if n not in metro_y: metro_y[n] = y

    lim_w = [w for w in words
             if any(k in w["text"].upper() for k in ["LIMITE","IMPENET"])
             and w["top"] > H*0.30]
    y_lim = min((w["top"] for w in lim_w), default=H*0.85)
    metro_y = {n:y for n,y in metro_y.items() if y<=y_lim+40}
    if len(metro_y) < 2: return []

    ml = sorted(metro_y.keys())
    alts = [abs(metro_y[ml[i]]-metro_y[ml[i-1]]) for i in range(1,len(ml))]
    h_metro = sum(alts)/len(alts) if alts else 28.0

    solo_words = [w for w in words if any(k in w["text"].upper() for k in _KW_UP)]
    if not solo_words: return []

    x_desc_min = min(w["x0"] for w in solo_words) - 5
    x_desc_max = max(w["x0"] + w.get("width", 50) for w in solo_words) + 10

    desc_words = [w for w in words
                  if x_desc_min-10 <= w["x0"] <= x_desc_max
                  and w["top"] <= y_lim + h_metro
                  and (any(k in w["text"].upper() for k in _KW_UP)
                       or (w["text"][0].isupper() and len(w["text"])>2))]

    golpe_words = [w for w in words
                   if re.match(r"^\d{1,2}$", w["text"])
                   and 0 <= int(w["text"]) <= 60
                   and w["top"] > H*0.22 and w["top"] < y_lim+15
                   and not (x_desc_min-15 <= w["x0"] <= x_desc_max+15)
                   and not (x_esc-20 <= w["x0"] <= x_esc+20)]

    orig_words = [w for w in words
                  if w["text"].upper() in _ORIGENS_SET
                  and w["top"]>H*0.20 and w["top"]<y_lim+h_metro]

    def _y_metro(y_pt):
        for i, n in enumerate(ml):
            if n==0: continue
            np = ml[i-1]
            if metro_y[np]-12 <= y_pt <= metro_y[n]+12: return n
        return None

    gpm = {}
    for w in golpe_words:
        n = _y_metro((w["top"]+w["bottom"])/2)
        if n is not None: gpm.setdefault(n,[]).append(int(w["text"]))

    orig_by_y = {}
    for w in orig_words:
        yr = round(w["top"])
        if yr not in orig_by_y: orig_by_y[yr] = w["text"].upper()

    linhas = []
    if desc_words:
        ds = sorted(desc_words, key=lambda w: w["top"])
        la = [ds[0]]
        for w in ds[1:]:
            if abs(w["top"]-la[-1]["top"]) < 6: la.append(w)
            else: linhas.append(la); la=[w]
        linhas.append(la)

    blocos = []
    if linhas:
        def _lt(wds):
            return " ".join(w["text"] for w in sorted(wds,key=lambda w:w["x0"])
                            if w["text"].upper() not in _ORIGENS_SET)
        bl = [linhas[0]]
        for ln in linhas[1:]:
            if ln[0]["top"]-bl[-1][0]["top"] < h_metro*1.5:
                bl.append(ln)
            else:
                y0=bl[0][0]["top"]; y1=bl[-1][0]["bottom"]
                txts=[]; orig=""
                for b in bl:
                    t=_lt(b)
                    tokens=t.split()
                    if tokens and tokens[0].upper() in _ORIGENS_SET:
                        orig=tokens[0].upper(); t=" ".join(tokens[1:])
                    elif tokens and tokens[-1].upper() in _ORIGENS_SET:
                        orig=tokens[-1].upper(); t=" ".join(tokens[:-1])
                    txts.append(t)
                for yo,oo in orig_by_y.items():
                    if y0-h_metro*0.5 <= yo <= y1+h_metro*0.5: orig=oo; break
                desc = re.sub(r"\b0\d\b","", " ".join(txts))
                desc = re.sub(r"\s{2,}"," ",desc).strip("., ").upper()
                if desc and len(desc)>3 and _tem_solo(desc):
                    blocos.append((y0,y1,desc,orig))
                bl=[ln]
        if bl:
            y0=bl[0][0]["top"]; y1=bl[-1][0]["bottom"]
            txts=[]; orig=""
            for b in bl:
                t=_lt(b)
                tokens=t.split()
                if tokens and tokens[0].upper() in _ORIGENS_SET:
                    orig=tokens[0].upper(); t=" ".join(tokens[1:])
                elif tokens and tokens[-1].upper() in _ORIGENS_SET:
                    orig=tokens[-1].upper(); t=" ".join(tokens[:-1])
                txts.append(t)
            for yo,oo in orig_by_y.items():
                if y0-h_metro*0.5 <= yo <= y1+h_metro*0.5: orig=oo; break
            desc = re.sub(r"\b0\d\b","", " ".join(txts))
            desc = re.sub(r"\s{2,}"," ",desc).strip("., ").upper()
            if desc and len(desc)>3 and _tem_solo(desc):
                blocos.append((y0,y1,desc,orig))

    dpm={}; opm={}
    y_max=max(metro_y.values())
    for j,(y_ini,y_fim,texto,orig) in enumerate(blocos):
        y_prox=blocos[j+1][0] if j+1<len(blocos) else y_max+h_metro
        for n in ml:
            if n==0: continue
            np=ml[ml.index(n)-1]
            yp=metro_y.get(np,metro_y[n]-h_metro)
            yc=(yp+metro_y[n])/2
            if y_ini-h_metro*0.3 <= yc < y_prox and n not in dpm:
                dpm[n]=texto; opm[n]=orig

    ud=""; uo=""
    for n in ml:
        if n==0: continue
        if n in dpm: ud=dpm[n]; uo=opm.get(n,"")
        else: dpm[n]=ud; opm[n]=uo

    metros_spt=[]
    for n in ml:
        if n==0: continue
        gs=sorted(gpm.get(n,[]))
        gv=[g for g in gs if g!=15]
        if len(gv)>=3:   g1,g2,g3=gv[0],gv[1],gv[2]
        elif len(gv)==2: g1,g2,g3=0,gv[0],gv[1]
        elif len(gs)>=3: g1,g2,g3=gs[0],gs[1],gs[2]
        else: continue
        metros_spt.append(MetroSPT(
            prof_m=float(n),nspt=g2+g3,
            golpes_1=g1,golpes_2=g2,golpes_3=g3,
            descricao=dpm.get(n,""),origem=opm.get(n,""),
        ))

    # Capturar profundidades decimais proximas ao limite da sondagem
    import re as _re2
    _RE_PROF_DEC2 = _re2.compile(r'\b(\d{1,2}[,.]\d{2})\b')
    prof_set  = {m.prof_m for m in metros_spt}
    prof_max2 = max(prof_set) if prof_set else 0.0
    y_ult2    = max(metro_y.values()) if metro_y else H*0.85
    txt_lim2  = pagina.crop((0, y_ult2 - h_metro, W, y_lim + h_metro*2)).extract_text() or ""
    for mat in _RE_PROF_DEC2.finditer(txt_lim2):
        val = _num(mat.group(1))
        if val and val > prof_max2 and val not in prof_set:
            ult = metros_spt[-1] if metros_spt else None
            trecho = txt_lim2[mat.end():mat.end()+120].strip()
            desc_d = _limpar_desc(trecho.split(chr(10))[0]) if _tem_solo(trecho) else (ult.descricao if ult else "")
            metros_spt.append(MetroSPT(
                prof_m=round(val,2),
                nspt=ult.nspt if ult else 0,
                golpes_1=ult.golpes_1 if ult else 0,
                golpes_2=ult.golpes_2 if ult else 0,
                golpes_3=ult.golpes_3 if ult else 0,
                descricao=desc_d,
                origem=ult.origem if ult else "",
            ))
            prof_set.add(val)

    metros_spt.sort(key=lambda m: m.prof_m)
    return metros_spt


# ---------------------------------------------------------------------------
# Interface pública — modo automático
# ---------------------------------------------------------------------------

def ler_pdf_sondagem(caminho_ou_bytes) -> list:
    import pdfplumber

    if isinstance(caminho_ou_bytes, (bytes, bytearray)):
        ctx = pdfplumber.open(io.BytesIO(caminho_ou_bytes))
    else:
        ctx = pdfplumber.open(caminho_ou_bytes)

    sondagens = []
    with ctx as pdf:
        for idx, pagina in enumerate(pdf.pages):
            texto = pagina.extract_text() or ""

            eh_perfil = any(kw in texto for kw in [
                "Classificação do Material","Resistência à Penetração",
                "PERFIL INDIVIDUAL","Sondagem a Percussão",
                "Sondagem de Reconhecimento","N-SPT","NSPT",
                "Sondagem executada conforme",
            ])
            eh_memorial = any(kw in texto for kw in [
                "Memorial Fotográfico","Registro Fotográfico",
                "Localização de Sondagem","Quadro de Fotos",
            ])
            if not eh_perfil or eh_memorial:
                continue

            nome        = _extrair_nome(texto, idx)
            cota_boca   = _extrair_cota(texto) or 0.0
            nivel_dagua = _extrair_nivel(texto)

            _GRUPO_A = [
                "GEOLOC ENGENHARIA E GEOLOGIA","NEW SOLOS ENGENHARIA",
                "Sondagem de Reconhecimento com SPT",
            ]
            _GRUPO_B = [
                "SUPORTE SONDAGENS","Suporte Sondagens","souligeotecnia","SOULI","Souli",
                "PERFIL INDIVIDUAL DE SONDAGEM MISTA À PERCUSSÃO",
                "PERFIL INDIVIDUAL DE SONDAGEM MISTA (SM)",
            ]

            if any(kw in texto for kw in _GRUPO_A):
                metros = _parse_geoloc_bbox(pagina)
                if len(metros) < 3:
                    metros_txt, blocos = _parse_pagina(texto)
                    metros_txt = _associar(metros_txt, blocos)
                    if len(metros_txt) > len(metros):
                        metros = metros_txt
            elif any(kw in texto for kw in _GRUPO_B):
                metros = _parse_bbox_esquerda(pagina)
                if len(metros) < 3:
                    metros_txt, blocos = _parse_pagina(texto)
                    metros_txt = _associar(metros_txt, blocos)
                    if len(metros_txt) > len(metros):
                        metros = metros_txt
            else:
                metros_dir = _parse_geoloc_bbox(pagina)
                metros_esq = _parse_bbox_esquerda(pagina)
                metros_txt, blocos = _parse_pagina(texto)
                metros_txt = _associar(metros_txt, blocos)
                metros = max([metros_dir, metros_esq, metros_txt], key=len)

            vistos: set = set()
            unicos = []
            for m in sorted(metros, key=lambda x: x.prof_m):
                if m.prof_m not in vistos:
                    vistos.add(m.prof_m)
                    unicos.append(m)

            if unicos:
                sondagens.append(SondagemSPT(
                    nome=nome,
                    cota_boca=cota_boca,
                    nivel_dagua=nivel_dagua,
                    metros=unicos,
                ))
    return sondagens


# ---------------------------------------------------------------------------
# Utilitários de horizontes
# ---------------------------------------------------------------------------

def agrupar_horizontes(metros, cota_boca=0.0, offset_cota=0.0):
    if not metros: return []
    horizontes = []
    desc_atual = metros[0].descricao or "Não identificado"
    orig_atual = metros[0].origem
    grupo = [metros[0]]

    for metro in metros[1:]:
        desc = metro.descricao or desc_atual
        orig = metro.origem or orig_atual
        if metro.descricao and metro.descricao != desc_atual:
            _fechar_h(horizontes, grupo, desc_atual, orig_atual, offset_cota)
            desc_atual = desc; orig_atual = orig; grupo = []
        grupo.append(metro)

    _fechar_h(horizontes, grupo, desc_atual, orig_atual, offset_cota)
    return horizontes


def _fechar_h(horizontes, metros, desc, orig, offset):
    if not metros: return
    prof_ini = max(metros[0].prof_m - 1.0 + offset, 0.0)
    prof_fim = metros[-1].prof_m + offset
    esp = round(prof_fim - prof_ini, 2)
    nspts = [m.nspt for m in metros]
    horizontes.append({
        "prof_ini_m":  round(prof_ini, 2),
        "prof_fim_m":  round(prof_fim, 2),
        "espessura_m": max(esp, 1.0),
        "descricao":   desc,
        "origem":      orig,
        "nspt_lista":  nspts,
        "nspt_medio":  round(sum(nspts)/len(nspts), 1) if nspts else 0.0,
        "nspt_min":    min(nspts) if nspts else 0,
        "nspt_max":    max(nspts) if nspts else 0,
    })
