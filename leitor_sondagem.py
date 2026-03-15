"""
leitor_sondagem.py — Lê boletins SPT em PDF e retorna dados estruturados.

Suporta:
  - Geoloc Engenharia e Geologia (parser bbox dedicado)
  - New Solos Engenharia (mesmo layout Geoloc)
  - Souli Geotecnia (parser bbox esquerda)
  - Suporte / Sondagem Mista SM (parser texto)
  - Layouts desconhecidos (tenta todos e usa o melhor)
"""

import re
import io
from dataclasses import dataclass, field
from typing import Optional, List


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
# Utilitários
# ---------------------------------------------------------------------------

_ORIGENS = {"SS","SRM","SRJ","SR","AT","AL","DFL","SAR","RC","SP","SDL","SM","SO","SRS"}

_KW_SOLO = [
    "argila","areia","silte","solo","pedregulho","rocha","aterro",
    "gnaisse","granito","limite","vegetal","orgânico","organico","turfoso",
]

def _tem_solo(s: str) -> bool:
    return any(kw in s.lower() for kw in _KW_SOLO)

def _limpar_desc(s: str) -> str:
    """Remove prefixos numéricos, extrai origem e retorna descrição em MAIÚSCULAS."""
    s = s.strip()
    s = re.sub(r'^[\d/\s]+(?=[A-ZÁÉÍÓÚ])', '', s)
    s = re.sub(r'^\d{1,2}[,.]\d{2}\s+', '', s)
    s = re.sub(r'^PM\d.*?(?=[A-ZÁÉÍÓÚ])', '', s)
    s = re.sub(r'\s+\d{1,2}\s*$', '', s)
    s = re.sub(r'\s+\d{2}\s+(?=[A-ZÁÉÍÓÚ])', ' ', s)
    # Remove letras isoladas de grau de consistência SM
    s = re.sub(r'\b[A-Z]{1,2}\b\s+[A-Z]{1,2}\b(\s+[A-Z]{1,2}\b)*', '', s)
    # Remove sufixos de penetração parcial
    s = re.sub(r'\s+\d+/\d+\s*$', '', s)
    tokens = s.split()
    tokens_limpos = [t for t in tokens if t.upper() not in _ORIGENS]
    s = " ".join(tokens_limpos).strip().rstrip(',.')
    return s.upper() if s else ""

def _extrair_origem_inline(s: str) -> tuple:
    """Extrai sigla de origem embutida no texto. Retorna (texto_sem_origem, origem)."""
    tokens = s.split()
    orig = ""
    resto = []
    for t in tokens:
        if t.upper() in _ORIGENS:
            orig = t.upper()
        else:
            resto.append(t)
    return " ".join(resto), orig

def _num(s: str) -> Optional[float]:
    try:
        return float(s.replace(',', '.'))
    except Exception:
        return None

def _extrair_cota(texto: str) -> Optional[float]:
    m = re.search(r"[Cc]ota\s+da\s+boca\s+do\s+furo[:\s]+([0-9]+[,.]?[0-9]*)", texto)
    return _num(m.group(1)) if m else None

def _extrair_nivel(texto: str) -> Optional[float]:
    m = re.search(r"[Nn][íi]vel\s+d['\u2019]?\s*[áa]gua[:\s]+([0-9]+[,.]?[0-9]*)", texto)
    return _num(m.group(1)) if m else None

def _extrair_nome(texto: str, idx: int) -> str:
    for pat in [
        r'\b(S[MP]-[0-9A-Z]+-[0-9]+-[0-9]+[A-Z0-9]*)\b',
        r'\b(S[MP]-[0-9A-Z]+-[0-9]+)\b',
    ]:
        m = re.search(pat, texto)
        if m:
            return m.group(1)
    return f"Sondagem-p{idx+1}"

# ---------------------------------------------------------------------------
# Parser linha a linha (genérico)
# ---------------------------------------------------------------------------

_RE_GOLPES = re.compile(
    r'^(\d{1,2})\s+(\d{1,4})\s+(\d{1,4})'
    r'(?:\s+\d{1,4})?'
    r'(?:\s+(\d{1,4}))?'
    r'(?:\s+(.*))?$'
)
_RE_PROF = re.compile(r'^(\d{1,2}[,.]\d{1,2})$')
_RE_INT_ISOLADO = re.compile(r'^\d{1,2}$')

def _descompactar(s: str) -> tuple:
    v = _num(s)
    if v is None: return None, None
    if v <= 60: return int(v), None
    for split in [1, 2]:
        a, b = int(s[:split]), int(s[split:])
        if 0 <= a <= 60 and 0 <= b <= 60:
            return a, b
    return int(v), None


def _parse_pagina(texto: str) -> tuple:
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

    linhas = texto.split("\n")
    i = 0
    while i < len(linhas):
        linha = linhas[i].strip()
        i += 1
        if not linha:
            continue

        if any(ign in linha for ign in [
            "NEW SOLOS", "GEOLOC", "SUPORTE", "SOULI",
            "END:", "Cliente:", "Obra:", "Local:", "Resp.", "CREA",
            "ENGENHEIRO", "Escala", "Revestimento:", "Sistema:",
            "SPT Golpes", "1ª 2ª 3ª", "1ª + 2ª", "Nº de Golpes",
            "Resistência à Penetração", "Classificação do Material",
            "Origem: SRJ", "Origem: AT", "Origem: SS", "Origem: AL",
            "CONFORME", "NBR 6484",
            "Sondagem de Reconhecimento", "Sondagem a Percussão",
            "PERFIL INDIVIDUAL", "Norte:", "Este:", "Fuso:",
            "Coordenadas", "Datum:", "Perfuração:", "Altura de queda",
            "Peso:", "Amostrador", "Int.:", "Ext.:", "Nível d",
            "Rev. /", "Penetração", "atoC", "lifreP", "megirO",
            "etnesuA", "EMROFNOC", "RBN", ".A.N",
            "lifreP megirO", "PROFUNDIDADE PROF", "Classificação",
            "LIFREP", "MEGIRO",
        ]):
            continue

        if _RE_INT_ISOLADO.match(linha) or re.match(r'^0[0-9]$', linha):
            continue
        if re.match(r'^00,\d{3}$', linha):
            continue
        if len(linha) > 10:
            espaco_ratio = linha.count(' ') / len(linha)
            if espaco_ratio > 0.45 and not _tem_solo(linha):
                continue
            if espaco_ratio > 0.35 and _tem_solo(linha):
                match_txt = re.search(r'([A-ZÁÉÍÓÚ][a-záéíóúãõçàâêôü][\w\s,()àáéíóúãõçâêô]*)', linha)
                if match_txt:
                    linha = match_txt.group(1).strip()
                else:
                    continue
        if re.match(r'^0 10 20', linha):
            continue

        m_prof = _RE_PROF.match(linha)
        if m_prof:
            prof_val = _num(m_prof.group(1))
            if prof_val is not None and 0 < prof_val <= 30:
                _fechar_bloco(prof_val)
            continue

        m_g = _RE_GOLPES.match(linha)
        if m_g:
            g1_raw = m_g.group(1)
            g2_raw = m_g.group(2)
            g3_raw = m_g.group(3)
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

            nspt = g2 + g3
            resto = (m_g.group(5) or "").strip()

            soma2_str = m_g.group(4)
            if soma2_str and ',' in linha:
                m_p = re.search(r'\b(\d{1,2}[,.]\d{1,2})\b', linha)
                if m_p:
                    prof_val = _num(m_p.group(1))
                    if prof_val is not None and 0 < prof_val <= 30:
                        pos = linha.find(m_p.group(1)) + len(m_p.group(1))
                        desc_pos = linha[pos:].strip()
                        metros.append(MetroSPT(
                            prof_m=round(prof_metro, 2),
                            nspt=nspt, golpes_1=g1, golpes_2=g2, golpes_3=g3,
                        ))
                        prof_metro += 1.0
                        _fechar_bloco(prof_val)
                        if _tem_solo(desc_pos):
                            desc_atual = _limpar_desc(desc_pos)
                        continue

            if resto and _tem_solo(resto):
                desc_nova = _limpar_desc(resto)
                if desc_nova:
                    if desc_atual and desc_nova != desc_atual:
                        _fechar_bloco(prof_metro - 1.0)
                    desc_atual = desc_nova

            metros.append(MetroSPT(
                prof_m=round(prof_metro, 2),
                nspt=nspt, golpes_1=g1, golpes_2=g2, golpes_3=g3,
            ))
            prof_metro += 1.0
            continue

        tokens = linha.split()
        orig_inline = ""
        if tokens and tokens[-1].upper() in _ORIGENS:
            orig_inline = tokens[-1].upper()
            linha_sem_orig = " ".join(tokens[:-1]).strip().rstrip(".")
        else:
            linha_sem_orig = linha

        if _tem_solo(linha_sem_orig):
            m_inline = re.search(r'\s+(\d{1,2})\s+(\d{1,2})\s+(\d{1,2})(?:\s+\d{1,2})?\s*$', linha_sem_orig)
            if m_inline:
                g1i = int(m_inline.group(1))
                g2i = int(m_inline.group(2))
                g3i = int(m_inline.group(3))
                if all(0 <= g <= 60 for g in [g1i, g2i, g3i]) and not (g1i==15 and g2i==15 and g3i==15):
                    desc_inline = _limpar_desc(linha_sem_orig[:m_inline.start()])
                    if desc_inline and _tem_solo(desc_inline) and desc_inline != desc_atual:
                        _fechar_bloco(prof_metro - 1.0)
                        desc_atual = desc_inline
                        if orig_inline:
                            orig_atual = orig_inline
                    nspt_i = g2i + g3i
                    metros.append(MetroSPT(
                        prof_m=round(prof_metro, 2),
                        nspt=nspt_i, golpes_1=g1i, golpes_2=g2i, golpes_3=g3i,
                    ))
                    prof_metro += 1.0
                    continue

            desc_nova = _limpar_desc(linha_sem_orig)
            if desc_nova:
                if desc_atual and desc_nova != desc_atual:
                    _fechar_bloco(prof_metro - 1.0)
                desc_atual = desc_nova
                if orig_inline:
                    orig_atual = orig_inline
            continue

        if desc_atual and not re.match(r'^[\d\s,./]+$', linha):
            if not _tem_solo(linha) and len(linha) > 3:
                cont, orig_cont = _extrair_origem_inline(linha.strip())
                if orig_cont:
                    orig_atual = orig_cont
                if cont.strip():
                    desc_atual = (desc_atual.rstrip(",. ") + " " + cont.strip()).upper()
            continue

        if len(tokens) == 1 and tokens[0].upper() in _ORIGENS:
            orig_atual = tokens[0].upper()
            continue

        if tokens and tokens[0].upper() in _ORIGENS and len(tokens) > 1:
            orig_atual = tokens[0].upper()
            resto2 = " ".join(tokens[1:])
            if _tem_solo(resto2):
                desc_nova = _limpar_desc(resto2)
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


# ---------------------------------------------------------------------------
# Parser bbox Geoloc / New Solos (classificação à DIREITA)
# ---------------------------------------------------------------------------

def _parse_geoloc_bbox(pagina) -> list:
    import re as _re
    _ORIGENS_LOCAL = {"SRM","SRJ","SS","AT","SR","AL","DFL","SAR","RC","SRR"}
    _EXCLUIR_TEXTO = {
        "LIMITE","IMPENET","TRÉPANO","CIRCULAÇÃO","LAVAGEM","PERFURAÇÃO",
        "CREA","CIVIL","ENGENHEIRO","RESPONSÁVEL","OBS","NBR",
    }

    W = pagina.width; H = pagina.height
    words = pagina.extract_words(use_text_flow=False, keep_blank_chars=False)

    # Escala vertical
    escala_words = [w for w in words
                    if W*0.27 < w["x0"] < W*0.34
                    and w["top"] > H*0.22 and w["top"] < H*0.85
                    and _re.match(r"^\d{1,2}$", w["text"])
                    and int(w["text"]) <= 20]
    metro_y = {}
    for w in escala_words:
        n = int(w["text"])
        y = (w["top"] + w["bottom"]) / 2
        if n not in metro_y:
            metro_y[n] = y

    if not metro_y:
        return []

    # Limite da sondagem
    limite_words = [w for w in words
                    if w["top"] > H*0.30
                    and any(k in w["text"].upper() for k in ["LIMITE","IMPENET"])
                    and W*0.40 < w["x0"] < W*0.75]
    y_limite = min((w["top"] for w in limite_words), default=H*0.85)
    metro_y = {n: y for n, y in metro_y.items() if y <= y_limite + 30}

    if not metro_y:
        return []

    metros_lista = sorted(metro_y.keys())
    alturas = [abs(metro_y[metros_lista[i]] - metro_y[metros_lista[i-1]])
               for i in range(1, len(metros_lista))]
    altura_metro = sum(alturas) / len(alturas) if alturas else 28.0

    # Golpes
    y_ultimo_metro = max(metro_y.values())
    golpes_words = [w for w in words
                    if W*0.04 < w["x0"] < W*0.24
                    and w["top"] > H*0.22
                    and w["top"] < y_ultimo_metro + 15
                    and _re.match(r"^\d{1,2}$", w["text"])
                    and 0 <= int(w["text"]) <= 60]

    metros_sorted = sorted(metro_y.keys())

    def _y_metro_v2(y_pt):
        for idx, n in enumerate(metros_sorted):
            if n == 0: continue
            n_prev = metros_sorted[idx-1]
            y_base = metro_y[n]
            y_topo = metro_y[n_prev]
            if y_topo - 12 <= y_pt <= y_base + 12:
                return n
        return None

    golpes_por_metro = {}
    for w in golpes_words:
        n = _y_metro_v2((w["top"] + w["bottom"]) / 2)
        if n is not None:
            golpes_por_metro.setdefault(n, []).append(int(w["text"]))

    # Classificação (x > 65%)
    classif_words = [w for w in words
                     if w["x0"] > W*0.65
                     and w["top"] > H*0.22
                     and w["bottom"] < H*0.90
                     and not _re.match(r"^\d{3,},\d{2}$", w["text"])
                     and not _re.match(r"^\d+/\d+$", w["text"])
                     ]
    _EXCLUIR_SET = {"CREA", "Ghisi", "Civil", "Engenheiro", "Responsável",
                    "LIMITE", "SONDAGEM", "Obs.:"}
    classif_words = [w for w in classif_words if w["text"] not in _EXCLUIR_SET]

    # Origens à direita (x > 85%)
    origens_direita = {}
    for w in classif_words:
        if w["x0"] > W*0.85 and w["text"].upper() in _ORIGENS_LOCAL:
            origens_direita[round(w["top"])] = w["text"].upper()

    # Agrupar em linhas
    linhas_classif = []
    if classif_words:
        cs = sorted(classif_words, key=lambda w: w["top"])
        la = [cs[0]]
        for w in cs[1:]:
            if abs(w["top"] - la[-1]["top"]) < 4:
                la.append(w)
            else:
                linhas_classif.append(la)
                la = [w]
        linhas_classif.append(la)

    def _linha_texto(wds):
        return " ".join(
            w["text"] for w in sorted(wds, key=lambda w: w["x0"])
            if w["text"].upper() not in _ORIGENS_LOCAL
        )

    def _origem_da_linha(wds):
        for w in wds:
            if w["text"].upper() in _ORIGENS_LOCAL:
                return w["text"].upper()
        return ""

    def _limpar_desc_final(txt):
        txt = _re.sub(r"\d{1,2}[,\.]\d{1,2}", "", txt)
        txt = _re.sub(r"~\d+m,?", "", txt)
        txt = _re.sub(r"\s{2,}", " ", txt)
        return txt.strip("., ").upper()

    # Agrupar linhas em blocos
    blocos_classif = []
    if linhas_classif:
        bl = [linhas_classif[0]]
        for ln in linhas_classif[1:]:
            y_anterior = bl[-1][0]["top"]
            y_atual    = ln[0]["top"]
            if y_atual - y_anterior < altura_metro * 1.5:
                bl.append(ln)
            else:
                y0 = bl[0][0]["top"]; y1 = bl[-1][0]["bottom"]
                txts = []; orig = ""
                for b in bl:
                    o = _origem_da_linha(b)
                    if o: orig = o
                    t = _linha_texto(b)
                    if t.strip(): txts.append(t)
                for y_orig, o_dir in origens_direita.items():
                    if y0 - 5 <= y_orig <= y1 + 20:
                        orig = o_dir; break
                desc = _limpar_desc_final(" ".join(txts))
                if desc:
                    blocos_classif.append((y0, y1, desc, orig))
                bl = [ln]
        if bl:
            y0 = bl[0][0]["top"]; y1 = bl[-1][0]["bottom"]
            txts = []; orig = ""
            for b in bl:
                o = _origem_da_linha(b)
                if o: orig = o
                t = _linha_texto(b)
                if t.strip(): txts.append(t)
            for y_orig, o_dir in origens_direita.items():
                if y0 - 5 <= y_orig <= y1 + 20:
                    orig = o_dir; break
            desc = _limpar_desc_final(" ".join(txts))
            if desc:
                blocos_classif.append((y0, y1, desc, orig))

    # Filtrar blocos válidos
    y_max_metros = max(metro_y.values()) + altura_metro if metro_y else 9999
    blocos_validos = [(yi, yf, txt, orig) for yi, yf, txt, orig in blocos_classif
                      if yi <= y_max_metros + altura_metro * 0.5
                      and not any(k in txt.upper() for k in
                                  ["LIMITE DE", "OBS", "NBR-6484", "IMPENETRÁVEL",
                                   "TRÉPANO", "CIRCULAÇÃO", "LAVAGEM", "PERFURAÇÃO"])]

    # Associar blocos → metros
    desc_por_metro = {}; orig_por_metro = {}
    y_max = max(metro_y.values())

    for j, (y_ini, y_fim, texto, orig) in enumerate(blocos_validos):
        y_prox = blocos_validos[j+1][0] if j+1 < len(blocos_validos) else y_max + altura_metro
        for n in metros_lista:
            if n == 0: continue
            n_prev = metros_lista[metros_lista.index(n)-1]
            yp = metro_y.get(n_prev, metro_y[n] - altura_metro)
            yc = (yp + metro_y[n]) / 2
            if y_ini - altura_metro*0.3 <= yc < y_prox and n not in desc_por_metro:
                desc_por_metro[n] = texto
                orig_por_metro[n] = orig

    # Preencher vazios
    ultima_desc = ""; ultima_orig = ""
    for n in metros_lista:
        if n == 0: continue
        if n in desc_por_metro:
            ultima_desc = desc_por_metro[n]
            ultima_orig = orig_por_metro.get(n, "")
        else:
            desc_por_metro[n] = ultima_desc.upper() if ultima_desc else ""
            orig_por_metro[n] = ultima_orig

    # Montar MetroSPT
    metros_spt = []
    for n in metros_lista:
        if n == 0: continue
        gs = sorted(golpes_por_metro.get(n, []))
        gs_validos = [g for g in gs if g != 15]
        if len(gs_validos) >= 3:   g1,g2,g3 = gs_validos[0],gs_validos[1],gs_validos[2]
        elif len(gs_validos) == 2: g1,g2,g3 = 0,gs_validos[0],gs_validos[1]
        elif len(gs) >= 3:         g1,g2,g3 = gs[0],gs[1],gs[2]
        else: continue
        metros_spt.append(MetroSPT(
            prof_m=float(n), nspt=g2+g3,
            golpes_1=g1, golpes_2=g2, golpes_3=g3,
            descricao=desc_por_metro.get(n, ""),
            origem=orig_por_metro.get(n, ""),
        ))
    return metros_spt


# ---------------------------------------------------------------------------
# Parser bbox universal (Souli, SM, layouts com descrição variada)
# ---------------------------------------------------------------------------

def _parse_bbox_esquerda(pagina) -> list:
    import re as _re
    from collections import Counter as _Counter
    _ORIGENS_SET = {"SS","SRM","SRJ","SR","AT","AL","DFL","SAR","RC","SP","SDL","SRS"}
    _KW = ["argila","areia","silte","solo","pedregulho","rocha","aterro",
           "vegetal","orgânico","turfoso"]
    _KW_UP = [k.upper() for k in _KW]
    _IGNORAR = {
        "LIMITE","SONDAGEM","NBR","ENGENHEIRO","CREA","OBS","RESPONSÁVEL",
        "PÁGINA","DATA","CLIENTE","OBRA","LOCAL","ENSAIO","AVANÇO",
        "PENETRAÇÃO","RESISTÊNCIA","GOLPES","REVESTIMENTO","SISTEMA",
        "PERFURAÇÃO","SONDADOR","COORDENADAS","NORTE","ESTE","DATUM",
        "CLASSIFICAÇÃO","MATERIAL","LIFREP","MEGIRO","PROFUNDIDADE",
        "COTA","PERF","REV","PESO","AMOSTRADOR","EXT","INT","ESCALA",
        "ALTURA","QUEDA","MANUAL","SIRGAS","FUSO","TRADO","CIRCULAÇÃO",
    }

    W = pagina.width; H = pagina.height
    words = pagina.extract_words(use_text_flow=False, keep_blank_chars=False)

    # Escala
    esc_cands = [w for w in words
                 if _re.match(r"^\d{1,2}$", w["text"])
                 and int(w["text"]) <= 30 and w["top"] > H*0.20]
    if len(esc_cands) < 3: return []

    x_freq = _Counter(round(w["x0"]/10)*10 for w in esc_cands)
    x_esc = x_freq.most_common(1)[0][0]

    metro_y = {}
    for w in esc_cands:
        if abs(w["x0"] - x_esc) < 25:
            n = int(w["text"]); y = (w["top"]+w["bottom"])/2
            if n not in metro_y: metro_y[n] = y

    lim_w = [w for w in words if any(k in w["text"].upper() for k in ["LIMITE","IMPENET"])
             and w["top"] > H*0.30]
    y_lim = min((w["top"] for w in lim_w), default=H*0.85)
    metro_y = {n:y for n,y in metro_y.items() if y<=y_lim+40}
    if len(metro_y) < 2: return []

    ml = sorted(metro_y.keys())
    alts = [abs(metro_y[ml[i]]-metro_y[ml[i-1]]) for i in range(1,len(ml))]
    h_metro = sum(alts)/len(alts) if alts else 28.0

    # Dados
    dados_words = [w for w in words
                   if w["top"] > H*0.22 and w["top"] < y_lim+h_metro
                   and len(w["text"]) > 1
                   and not _re.match(r"^[\d,./:><]+$", w["text"])
                   and not _re.match(r"^\d+/\d+$", w["text"])
                   and w["text"].upper() not in _IGNORAR
                   and abs(w["x0"]-x_esc) > 15]

    solo_words = [w for w in dados_words if any(k in w["text"].upper() for k in _KW_UP)]
    if not solo_words: return []

    x_desc_min = min(w["x0"] for w in solo_words)-5
    x_desc_max = max(w["x0"]+w.get("width",50) for w in solo_words)+10

    desc_words = [w for w in dados_words
                  if x_desc_min-10 <= w["x0"] <= x_desc_max
                  and (any(k in w["text"].upper() for k in _KW_UP)
                       or (w["text"][0].isupper() and len(w["text"])>2)
                       or (w["text"].isupper() and len(w["text"])>2))]

    # Golpes: inteiros 0-60 fora da desc e fora da escala
    golpe_words = [w for w in words
                   if _re.match(r"^\d{1,2}$", w["text"])
                   and 0 <= int(w["text"]) <= 60
                   and w["top"] > H*0.22 and w["top"] < y_lim+15
                   and not (x_desc_min-15 <= w["x0"] <= x_desc_max+15)
                   and not (x_esc-20 <= w["x0"] <= x_esc+20)]

    def _y_metro(y_pt):
        for i,n in enumerate(ml):
            if n==0: continue
            np=ml[i-1]
            if metro_y[np]-12 <= y_pt <= metro_y[n]+12: return n
        return None

    gpm = {}
    for w in golpe_words:
        n = _y_metro((w["top"]+w["bottom"])/2)
        if n is not None: gpm.setdefault(n,[]).append(int(w["text"]))

    # Origens (qualquer X)
    orig_words = [w for w in words if w["text"].upper() in _ORIGENS_SET
                  and w["top"]>H*0.20 and w["top"]<y_lim+h_metro]
    orig_by_y = {}
    for w in orig_words:
        yr = round(w["top"])
        if yr not in orig_by_y: orig_by_y[yr] = w["text"].upper()

    # Linhas e blocos de descrição
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
                desc = _re.sub(r"\b0\d\b","", " ".join(txts))
                desc = _re.sub(r"^[A-ZÀ-Ú]{1,2}\s+","",desc).strip().upper()
                desc = _re.sub(r"\s{2,}"," ",desc).strip("., ")
                if desc and len(desc)>3 and any(k in desc for k in _KW_UP):
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
            desc = _re.sub(r"\b0\d\b","", " ".join(txts))
            desc = _re.sub(r"^[A-ZÀ-Ú]{1,2}\s+","",desc).strip().upper()
            desc = _re.sub(r"\s{2,}"," ",desc).strip("., ")
            if desc and len(desc)>3 and any(k in desc for k in _KW_UP):
                blocos.append((y0,y1,desc,orig))

    # Associar
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
    return metros_spt


# ---------------------------------------------------------------------------
# Interface pública
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
                "Classificação do Material", "Resistência à Penetração",
                "PERFIL INDIVIDUAL", "Sondagem a Percussão",
                "Sondagem de Reconhecimento", "N-SPT", "NSPT",
                "Sondagem executada conforme",
            ])
            eh_memorial = any(kw in texto for kw in [
                "Memorial Fotográfico", "Registro Fotográfico",
                "Localização de Sondagem", "Quadro de Fotos",
            ])
            if not eh_perfil or eh_memorial:
                continue

            nome        = _extrair_nome(texto, idx)
            cota_boca   = _extrair_cota(texto) or 0.0
            nivel_dagua = _extrair_nivel(texto)

            # Grupos de empresa → parser adequado
            _GRUPO_A = [
                "GEOLOC ENGENHARIA E GEOLOGIA",
                "NEW SOLOS ENGENHARIA",
                "Sondagem de Reconhecimento com SPT",
            ]
            _GRUPO_B = [
                "SUPORTE SONDAGENS", "Suporte Sondagens",
                "souligeotecnia", "SOULI", "Souli",
                "PERFIL INDIVIDUAL DE SONDAGEM MISTA À PERCUSSÃO",
                "PERFIL INDIVIDUAL DE SONDAGEM MISTA (SM)",
            ]

            if any(kw in texto for kw in _GRUPO_A):
                metros = _parse_geoloc_bbox(pagina)
                blocos = []
                if len(metros) < 3:
                    metros_txt, blocos = _parse_pagina(texto)
                    metros_txt = _associar(metros_txt, blocos)
                    if len(metros_txt) > len(metros):
                        metros = metros_txt; blocos = []
            elif any(kw in texto for kw in _GRUPO_B):
                metros = _parse_bbox_esquerda(pagina)
                blocos = []
                if len(metros) < 3:
                    metros_txt, blocos = _parse_pagina(texto)
                    metros_txt = _associar(metros_txt, blocos)
                    if len(metros_txt) > len(metros):
                        metros = metros_txt; blocos = []
            else:
                metros_dir = _parse_geoloc_bbox(pagina)
                metros_esq = _parse_bbox_esquerda(pagina)
                metros_txt, blocos = _parse_pagina(texto)
                metros_txt = _associar(metros_txt, blocos)
                metros = max([metros_dir, metros_esq, metros_txt], key=len)
                blocos = []

            # Deduplicar e ordenar
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
