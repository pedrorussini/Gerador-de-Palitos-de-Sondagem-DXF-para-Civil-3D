"""
leitor_sondagem.py — Parser de laudos SPT digitais (pdfplumber).

Estratégia: análise linha a linha do texto extraído.
Cada linha pode conter combinações de:
  - Golpes: "g1 g2 g3" ou "g1 g2 g3 soma1 soma2"
  - Profundidade de mudança de horizonte: número após os golpes
  - Início de descrição: texto após os golpes
  - Descrição isolada: linha de texto sem números
  - Origem: SS, SRM, SRJ, AT, SR, AL, DFL, SAR, RC na mesma linha ou sozinha
  - Descrição + Origem: "Solo orgânico. SS"

NSPT = g2 + g3 (2ª + 3ª sequência, NBR 6484).
"""

import re
import pdfplumber
from dataclasses import dataclass, field
from typing import Optional


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
    nome:        str
    cota_boca:   float
    nivel_dagua: Optional[float]
    metros:      list = field(default_factory=list)

    @property
    def profundidade_total(self) -> float:
        return max((m.prof_m for m in self.metros), default=0.0)


# ---------------------------------------------------------------------------
# Utilitários
# ---------------------------------------------------------------------------

def _num(s) -> Optional[float]:
    try:
        return float(str(s).replace(",", ".").strip())
    except (ValueError, AttributeError):
        return None

_ORIGENS = {"SS","SRM","SRJ","SR","AT","AL","DFL","SAR","RC","SP","SDL","SM","SO","SRS"}

_KW_SOLO = [
    "argila","areia","silte","solo","pedregulho","rocha","aterro",
    "gnaisse","granito","limite","vegetal","orgânico","organico","turfoso",
]

def _tem_solo(s: str) -> bool:
    return any(kw in s.lower() for kw in _KW_SOLO)

def _limpar_desc(s: str) -> str:
    """Remove prefixos numéricos e tokens que contaminam a descrição."""
    s = s.strip()
    # Remove prefixos: "151515 ", "151516 ", "05 ", "09 ", "1/30 ", "4/15 "
    s = re.sub(r'^[\d/\s]+(?=[A-ZÁÉÍÓÚ])', '', s)
    # Remove "X,XX " de cotas no início: "5,73 Argila..." → "Argila..."
    s = re.sub(r'^\d{1,2}[,.]\d{2}\s+', '', s)
    # Remove "PM1 – 1/47 1/32 NNNN" no início
    s = re.sub(r'^PM\d.*?(?=[A-ZÁÉÍÓÚ])', '', s)
    # Remove sufixos numéricos: " 2 4 4 8", " 03 E ROXO" → mas manter "E ROXO"
    s = re.sub(r'\s+\d{1,2}\s*$', '', s)
    # Remove " 03 " e similares que antecedem texto (número de amostra embutido)
    s = re.sub(r'\s+\d{2}\s+(?=[A-ZÁÉÍÓÚ])', ' ', s)
    return s.strip().rstrip(',.')

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
# Parser linha a linha
# ---------------------------------------------------------------------------

# Padrão de golpes: 3 inteiros obrigatórios (0-60), seguidos de opcionais
_RE_GOLPES = re.compile(
    r'^(\d{1,2})\s+(\d{1,4})\s+(\d{1,4})'   # g1 g2g3_colados_ou_separados
    r'(?:\s+\d{1,4})?'                          # soma1 opcional
    r'(?:\s+(\d{1,4}))?'                        # soma2 opcional (pode ser prof)
    r'(?:\s+(.*))?$'                             # resto da linha
)

# Descompactar golpes colados: "1213" → (12, 13) assumindo que cada parte <= 60
def _descompactar(s: str) -> tuple:
    """Se s tem 3-4 dígitos, tenta separar em dois golpes válidos."""
    v = _num(s)
    if v is None: return None, None
    if v <= 60: return int(v), None   # número simples
    # Tentar separar: "1213" → 12 e 13
    for split in [1, 2]:
        a, b = int(s[:split]), int(s[split:])
        if 0 <= a <= 60 and 0 <= b <= 60:
            return a, b
    return int(v), None

# Profundidade isolada (ex: "6,05" ou "8,12")
_RE_PROF = re.compile(r'^(\d{1,2}[,.]\d{1,2})$')

# Número inteiro isolado (escala do gráfico — ignorar)
_RE_INT_ISOLADO = re.compile(r'^\d{1,2}$')


def _parse_pagina(texto: str) -> tuple[list[MetroSPT], list]:
    """
    Analisa o texto de uma página e retorna metros SPT e horizontes.
    Retorna:
        metros     : list[MetroSPT]
        horizontes : list[dict]  com prof_ini, prof_fim, desc, orig
    """
    metros: list[MetroSPT] = []
    # Blocos: (prof_ini_m, prof_fim_m, desc, orig)
    # prof_ini = 0 para o primeiro, depois = prof da mudança anterior
    blocos: list[dict] = []

    prof_metro = 1.0        # profundidade do próximo metro a ser registrado
    desc_atual = ""
    orig_atual = ""
    prof_bloco_ini = 0.0    # profundidade de início do bloco atual

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

        # Ignorar cabeçalhos e rodapés conhecidos
        if any(ign in linha for ign in [
            "NEW SOLOS", "GEOLOC", "SUPORTE", "SOULI",
            "END:", "Cliente:", "Obra:", "Local:", "Resp.", "CREA",
            "ENGENHEIRO", "Escala", "Revestimento:", "Sistema:",
            "SPT Golpes", "1ª 2ª 3ª", "1ª + 2ª", "Nº de Golpes",
            "Resistência à Penetração", "Classificação do Material",
            "Origem: SRJ", "Origem: AT", "CONFORME", "NBR 6484",
            "Sondagem de Reconhecimento", "Sondagem a Percussão",
            "PERFIL INDIVIDUAL", "Norte:", "Este:", "Fuso:",
            "Coordenadas", "Datum:", "Perfuração:", "Altura de queda",
            "Peso:", "Amostrador", "Int.:", "Ext.:", "Nível d",
            "Rev. /", "Penetração", "atoC", "lifreP", "megirO",
            "etnesuA", "EMROFNOC", "RBN", ".A.N",
        ]):
            continue

        # Ignorar linhas de escala gráfica (números 0-19 isolados, cotas invertidas)
        # e números de amostra de 2 dígitos ("02", "03"...)
        if _RE_INT_ISOLADO.match(linha) or re.match(r'^0[0-9]$', linha):
            continue
        if re.match(r'^00,\d{3}$', linha):  # cotas invertidas: "00,026"
            continue
        # Linhas com texto espaçado letra a letra (cotas + dados misturados na mesma célula)
        # Ex: "6 6 9 9 1 1 , , 5 3 1 1 0 0" — razão (espaços/chars) muito alta
        if len(linha) > 10:
            espaco_ratio = linha.count(' ') / len(linha)
            if espaco_ratio > 0.45 and not _tem_solo(linha):
                continue
            # Linha com texto espaçado mas com descrição embutida: extrair só a parte textual
            if espaco_ratio > 0.35 and _tem_solo(linha):
                # Remover a parte de números espaçados e ficar só com o texto
                match_txt = re.search(r'([A-ZÁÉÍÓÚ][a-záéíóúãõçàâêôü][\w\s,()àáéíóúãõçâêô]*)', linha)
                if match_txt:
                    linha = match_txt.group(1).strip()
                else:
                    continue
        if re.match(r'^0 10 20', linha):     # escala horizontal
            continue

        # Profundidade de mudança de horizonte isolada (ex: "6,05")
        m_prof = _RE_PROF.match(linha)
        if m_prof:
            prof_val = _num(m_prof.group(1))
            if prof_val is not None and 0 < prof_val <= 30:
                _fechar_bloco(prof_val)
            continue

        # Linha de golpes SPT
        m_g = _RE_GOLPES.match(linha)
        if m_g:
            g1_raw = m_g.group(1)
            g2_raw = m_g.group(2)
            g3_raw = m_g.group(3)
            # Descompactar golpes colados: "1213" pode ser g2=12, g3=13
            g1 = int(g1_raw) if _num(g1_raw) and _num(g1_raw) <= 60 else 0
            g2_v, g3_extra = _descompactar(g2_raw)
            if g3_extra is not None:
                # g2 e g3 estavam colados no segundo grupo
                g2, g3 = g2_v, g3_extra
            else:
                g2 = g2_v or 0
                g3_v, _ = _descompactar(g3_raw)
                g3 = g3_v or 0
            # Validar que são golpes plausíveis
            if not all(0 <= g <= 60 for g in [g1, g2, g3]):
                continue
            # Evitar capturar linhas de amostras do formato Suporte ("1 1/24 1/22")
            if '/' in linha and not re.search(r'\d+/\d{2}\b', linha):
                # Tem barra mas não é no formato "30/8" (penetração parcial)
                pass
            elif '/' in linha and re.match(r'^\d+\s+\d+\s+\d+.*/', linha):
                # Formato Geoloc "4 5 3 9 8/31" — a barra é na soma, golpes são g1 g2 g3
                pass  # continua processando normalmente

            # Ignorar linhas "151515" (avanços de 15cm do amostrador Geoloc)
            if g1 == 15 and g2 == 15 and g3 == 15:
                continue

            # Ignorar números de amostra isolados (02, 03... em SP-12A)

            nspt = g2 + g3
            resto = (m_g.group(5) or "").strip()

            # O "soma2" opcional pode ser na verdade uma profundidade de mudança
            soma2_str = m_g.group(4)
            if soma2_str and ',' in linha:
                # Verificar se há profundidade decimal no resto
                m_p = re.search(r'\b(\d{1,2}[,.]\d{1,2})\b', linha)
                if m_p:
                    prof_val = _num(m_p.group(1))
                    if prof_val is not None and 0 < prof_val <= 30:
                        # Extrair descrição após a profundidade
                        pos = linha.find(m_p.group(1)) + len(m_p.group(1))
                        desc_pos = linha[pos:].strip()
                        # Registrar metro antes de fechar o bloco
                        metros.append(MetroSPT(
                            prof_m=round(prof_metro, 2),
                            nspt=nspt, golpes_1=g1, golpes_2=g2, golpes_3=g3,
                        ))
                        prof_metro += 1.0
                        _fechar_bloco(prof_val)
                        if _tem_solo(desc_pos):
                            desc_atual = _limpar_desc(desc_pos)
                        continue

            # Resto da linha pode conter início de descrição
            if resto and _tem_solo(resto):
                # Novo bloco de descrição começa aqui
                if desc_atual and not _tem_solo(desc_atual):
                    desc_atual = ""
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

        # Linha de texto puro
        # Verificar se é "Descrição. ORIGEM" numa mesma linha
        tokens = linha.split()
        orig_inline = ""
        if tokens and tokens[-1].upper() in _ORIGENS:
            orig_inline = tokens[-1].upper()
            linha_sem_orig = " ".join(tokens[:-1]).strip().rstrip(".")
        else:
            linha_sem_orig = linha

        if _tem_solo(linha_sem_orig):
            # Verificar se há golpes SPT no final da linha (formato SP-12A / Souli)
            # Ex: "SILTE ARENOSO (AREIA FINA À MÉDIA), MARROM 2 4 4 8"
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
                    # Novo horizonte começa aqui
                    _fechar_bloco(prof_metro - 1.0)
                desc_atual = desc_nova
                if orig_inline:
                    orig_atual = orig_inline
            continue

        # Linha de continuação de descrição (ex: "muito mole a rija.")
        if desc_atual and not re.match(r'^[\d\s,./]+$', linha):
            if not _tem_solo(linha) and len(linha) > 3:
                desc_atual = desc_atual.rstrip(",. ") + " " + linha.strip()
            continue

        # Origem isolada
        if len(tokens) == 1 and tokens[0].upper() in _ORIGENS:
            orig_atual = tokens[0].upper()
            continue

    # Fechar último bloco
    if desc_atual:
        blocos.append({
            "prof_ini": prof_bloco_ini,
            "prof_fim": 999.0,
            "desc": desc_atual,
            "orig": orig_atual,
        })

    return metros, blocos


def _associar(metros: list[MetroSPT], blocos: list[dict]) -> list[MetroSPT]:
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
# Parser especializado para layout Geoloc — baseado em bounding boxes
# ---------------------------------------------------------------------------

def _parse_geoloc_bbox(pagina) -> list:
    """
    Parser Geoloc usando coordenadas (x,y) das palavras no PDF.
    
    Estratégia:
    1. Identificar Y de cada metro pela coluna de inteiros (escala do gráfico: 0,1,2...)
    2. Extrair golpes por Y (coluna x=25-55% da largura)
    3. Extrair classificação por Y (coluna x>70% da largura)  
    4. Associar classificação ao intervalo de metros por Y
    """
    import re
    
    W = pagina.width
    H = pagina.height
    words = pagina.extract_words(use_text_flow=False, keep_blank_chars=False)
    
    _ORIGENS = {"SRM", "SRJ", "SS", "AT", "SR", "AL", "DFL", "SAR", "RC"}
    
    # --- 1. Encontrar Y de cada metro pelo gráfico de inteiros 0-20 ---
    # A escala vertical está em x ~ 25-32% da página
    # Escala vertical: inteiros 0-20 em x=27-34% da largura
    # Excluir duplicatas tomando apenas o primeiro Y de cada número
    escala_words = [w for w in words
                    if W*0.27 < w["x0"] < W*0.34
                    and w["top"] > H*0.22
                    and w["top"] < H*0.85
                    and re.match(r"^\d{1,2}$", w["text"])
                    and int(w["text"]) <= 20]
    
    # Mapear metro_numero → Y_centro no PDF
    metro_y = {}
    for w in escala_words:
        n = int(w["text"])
        y_centro = (w["top"] + w["bottom"]) / 2
        metro_y[n] = y_centro
    
    if not metro_y:
        return []
    
    # Detectar o limite real da sondagem pelo Y do texto "LIMITE DE SONDAGEM"
    # ou pela última profundidade decimal (ex: "9,72") na coluna de cotas
    limite_words = [w for w in words
                    if w["top"] > H*0.30
                    and any(k in w["text"].upper() for k in ["LIMITE", "IMPENET"])
                    and W*0.40 < w["x0"] < W*0.75]
    y_limite = min((w["top"] for w in limite_words), default=H*0.85)
    
    # Remover metros da escala que estão além do limite da sondagem (+2 metros de folga)
    metro_y = {n: y for n, y in metro_y.items() if y <= y_limite + 30}
    
    if not metro_y:
        return []
    
    metros_lista = sorted(metro_y.keys())
    
    # Calcular altura de cada linha de metro em pixels
    alturas = []
    for i in range(1, len(metros_lista)):
        alturas.append(metro_y[metros_lista[i]] - metro_y[metros_lista[i-1]])
    altura_metro = sum(alturas)/len(alturas) if alturas else 28.0
    
    # --- 2. Extrair golpes por Y ---
    # Golpes estão em x=5-24% (g1, g2, g3 individuais) e x=24-40% (somas)
    golpes_words = [w for w in words
                    if W*0.05 < w["x0"] < W*0.25
                    and w["top"] > H*0.22
                    and re.match(r"^\d{1,2}$", w["text"])
                    and 0 <= int(w["text"]) <= 60]
    
    # Agrupar golpes por linha (Y similar ± 5pts)
    def _y_metro(y_pt):
        """Retorna o número do metro mais próximo dado Y em pts."""
        best_n, best_d = None, 9999
        for n, ym in metro_y.items():
            # Cada metro ocupa altura_metro pixels
            # Metro n cobre de metro_y[n-1] a metro_y[n]
            if n == 0:
                continue
            y_topo = metro_y.get(n-1, ym - altura_metro)
            y_base = ym
            if y_topo - 5 <= y_pt <= y_base + 5:
                d = abs(y_pt - (y_topo + y_base)/2)
                if d < best_d:
                    best_d = d
                    best_n = n
        return best_n
    
    # Golpes individuais (g1,g2,g3) estão em x=5-20% da página
    # A escala vertical do gráfico fica em x~29-31% — excluir
    # Limitar Y ao intervalo real dos metros (não além do último metro + folga)
    y_ultimo_metro = max(metro_y.values()) if metro_y else H
    golpes_words = [w for w in words
                    if W*0.04 < w["x0"] < W*0.24
                    and w["top"] > H*0.22
                    and w["top"] < y_ultimo_metro + 15
                    and re.match(r"^\d{1,2}$", w["text"])
                    and 0 <= int(w["text"]) <= 60]

    # Função melhorada: usar borda superior do metro seguinte como limite
    metros_sorted = sorted(metro_y.keys())
    
    def _y_metro_v2(y_pt):
        """Retorna o metro cujo intervalo [y_topo, y_base] contém y_pt."""
        for idx, n in enumerate(metros_sorted):
            if n == 0:
                continue
            n_prev = metros_sorted[idx-1]
            y_base = metro_y[n]        # Y da marca do metro n (base do intervalo)
            y_topo = metro_y[n_prev]   # Y da marca do metro anterior (topo do intervalo)
            # Tolerar ±12pts para golpes deslocados verticalmente
            if y_topo - 12 <= y_pt <= y_base + 12:
                return n
        return None

    # Agrupar golpes por metro
    golpes_por_metro = {}
    for w in golpes_words:
        n = _y_metro_v2((w["top"] + w["bottom"])/2)
        if n is not None:
            if n not in golpes_por_metro:
                golpes_por_metro[n] = []
            golpes_por_metro[n].append(int(w["text"]))
    
    # --- 3. Extrair classificação por coluna X ---
    # Classificação: x > 70% da largura, y > 20% da altura
    classif_words = [w for w in words
                     if w["x0"] > W*0.70
                     and w["top"] > H*0.22
                     and w["bottom"] < H*0.90
                     and not re.match(r"^\d{3,},\d{2}$", w["text"])  # excluir cotas
                     and not re.match(r"^\d+/\d+$", w["text"])  # excluir "1/1"
                     ]
    
    # Excluir palavras de cabeçalho/rodapé
    _EXCLUIR = {"CREA", "Ghisi", "Civil", "Engenheiro", "Responsável",
                "LIMITE", "SONDAGEM", "Obs.:"}
    classif_words = [w for w in classif_words if w["text"] not in _EXCLUIR]
    
    # Agrupar palavras em linhas (Y similar ± 3pts)
    linhas_classif = []
    if classif_words:
        classif_sorted = sorted(classif_words, key=lambda w: w["top"])
        linha_atual = [classif_sorted[0]]
        for w in classif_sorted[1:]:
            if abs(w["top"] - linha_atual[-1]["top"]) < 4:
                linha_atual.append(w)
            else:
                linhas_classif.append(linha_atual)
                linha_atual = [w]
        linhas_classif.append(linha_atual)
    
    # Reconstruir texto de cada linha (ordenado por X)
    def _linha_texto(wds):
        return " ".join(w["text"] for w in sorted(wds, key=lambda w: w["x0"]))
    
    # Associar linhas de classificação a intervalos de Y
    # Cada grupo de linhas contíguas = um horizonte
    blocos_classif = []  # [(y_ini, y_fim, texto, origem)]
    if linhas_classif:
        bloco_linhas = [linhas_classif[0]]
        for ln in linhas_classif[1:]:
            y_anterior = bloco_linhas[-1][0]["top"]
            y_atual    = ln[0]["top"]
            if y_atual - y_anterior < altura_metro * 1.5:
                bloco_linhas.append(ln)
            else:
                # Fechar bloco
                y_ini = bloco_linhas[0][0]["top"]
                y_fim = bloco_linhas[-1][0]["bottom"]
                textos = []
                orig = ""
                for b in bloco_linhas:
                    t = _linha_texto(b)
                    if t.upper() in _ORIGENS:
                        orig = t.upper()
                    else:
                        textos.append(t)
                blocos_classif.append((y_ini, y_fim, " ".join(textos), orig))
                bloco_linhas = [ln]
        # Fechar último
        if bloco_linhas:
            y_ini = bloco_linhas[0][0]["top"]
            y_fim = bloco_linhas[-1][0]["bottom"]
            textos = []
            orig = ""
            for b in bloco_linhas:
                t = _linha_texto(b)
                if t.upper() in _ORIGENS:
                    orig = t.upper()
                else:
                    textos.append(t)
            blocos_classif.append((y_ini, y_fim, " ".join(textos), orig))
    
    # Associar cada bloco de classificação ao intervalo de metros por Y
    # Regra: a descrição de um horizonte vale do seu Y_ini até o Y_ini do próximo
    # Isso cobre casos onde o texto ocupa menos linhas que o horizonte geológico

    # Filtrar blocos que são rodapé/observações (y muito abaixo dos metros)
    y_max_metros = max(metro_y.values()) + altura_metro if metro_y else 9999
    blocos_classif_validos = [(yi, yf, txt, orig) for yi, yf, txt, orig in blocos_classif
                              if yi <= y_max_metros + altura_metro * 0.5
                              and not any(k in txt.upper() for k in
                                          ["LIMITE DE", "OBS.:", "NBR-6484", "IMPENETRÁVEL",
                                           "TRÉPANO", "CIRCULAÇÃO", "LAVAGEM", "PERFURAÇÃO"])]

    desc_por_metro = {}
    orig_por_metro = {}
    
    # Para cada bloco, determinar até qual Y ele é válido (até o Y_ini do próximo)
    for j, (y_ini, y_fim, texto, orig) in enumerate(blocos_classif_validos):
        # Próximo horizonte começa em...
        if j + 1 < len(blocos_classif_validos):
            y_proximo = blocos_classif_validos[j+1][0]
        else:
            y_proximo = y_max_metros + altura_metro

        # Aplicar a todos os metros cujo Y-centro está no intervalo [y_ini, y_proximo)
        for n in metros_lista:
            if n == 0:
                continue
            y_prev = metro_y.get(n-1, metro_y[n] - altura_metro)
            y_centro_metro = (y_prev + metro_y[n]) / 2
            if y_ini - altura_metro * 0.3 <= y_centro_metro < y_proximo:
                if n not in desc_por_metro:  # não sobrescrever descrição já atribuída
                    desc_por_metro[n] = texto
                    orig_por_metro[n] = orig

    # Preencher metros sem descrição com a do metro anterior
    ultima_desc = ""
    ultima_orig = ""
    for n in metros_lista:
        if n == 0:
            continue
        if n in desc_por_metro:
            ultima_desc = desc_por_metro[n]
            ultima_orig = orig_por_metro.get(n, "")
        else:
            desc_por_metro[n] = ultima_desc
            orig_por_metro[n] = ultima_orig
    
    # --- 4. Montar MetroSPT ---
    metros_spt = []
    for n in metros_lista:
        if n == 0:
            continue
        gs = sorted(golpes_por_metro.get(n, []))
        # Filtrar 15s (avanços de 15cm)
        gs_validos = [g for g in gs if g != 15]
        if len(gs_validos) >= 3:
            g1, g2, g3 = gs_validos[0], gs_validos[1], gs_validos[2]
        elif len(gs_validos) == 2:
            g1, g2, g3 = 0, gs_validos[0], gs_validos[1]
        elif len(gs_validos) == 1:
            g1, g2, g3 = 0, 0, gs_validos[0]
        else:
            # Usar todos incluindo 15s se não há outros
            if len(gs) >= 3:
                g1, g2, g3 = gs[0], gs[1], gs[2]
            else:
                continue  # metro sem golpes válidos
        
        nspt = g2 + g3
        metros_spt.append(MetroSPT(
            prof_m    = float(n),
            nspt      = nspt,
            golpes_1  = g1,
            golpes_2  = g2,
            golpes_3  = g3,
            descricao = desc_por_metro.get(n, ""),
            origem    = orig_por_metro.get(n, ""),
        ))
    
    return metros_spt


# ---------------------------------------------------------------------------
# Parser bbox genérico — funciona para New Solos, Souli, Suporte e outros
# ---------------------------------------------------------------------------

def _detectar_colunas_bbox(pagina) -> dict:
    """
    Detecta automaticamente as colunas do boletim SPT usando a densidade
    de palavras por faixa X.
    
    Retorna dict com:
        x_classif_min  : X mínimo da coluna de classificação
        x_golpes_min   : X mínimo da coluna de golpes (g1,g2,g3)
        x_golpes_max   : X máximo da coluna de golpes
        x_escala_min   : X mínimo da escala vertical (inteiros 0-30)
        x_escala_max   : X máximo da escala vertical
        y_dados_ini    : Y onde começam os dados (abaixo do cabeçalho)
        y_dados_fim    : Y onde terminam os dados
    """
    import re
    W = pagina.width
    H = pagina.height
    words = pagina.extract_words()
    
    # Contar palavras por faixa de 5% em X
    bins = {i: 0 for i in range(0, 100, 5)}
    for w in words:
        b = int(w["x0"] / W * 100 // 5) * 5
        if b in bins:
            bins[b] += 1
    
    # Encontrar onde estão os inteiros da escala (0-30 como coluna)
    # São inteiros isolados em Y > 20% da página
    escala_cands = [w for w in words
                    if re.match(r"^\d{1,2}$", w["text"])
                    and int(w["text"]) <= 30
                    and w["top"] > H * 0.20]
    
    # Agrupar por X para encontrar a coluna da escala
    if escala_cands:
        xs_escala = sorted(set(round(w["x0"]) for w in escala_cands))
        # A escala é uma coluna densa de inteiros — encontrar o X mais frequente
        from collections import Counter
        x_freq = Counter(round(w["x0"] / 5) * 5 for w in escala_cands)
        x_escala_centro = x_freq.most_common(1)[0][0]
        x_escala_min = x_escala_centro - 15
        x_escala_max = x_escala_centro + 25
    else:
        x_escala_min = W * 0.25
        x_escala_max = W * 0.35
    
    # Golpes ficam tipicamente à esquerda da escala
    x_golpes_max = x_escala_min
    x_golpes_min = W * 0.04
    
    # Classificação fica à direita da escala
    x_classif_min = x_escala_max + W * 0.05
    
    # Y dos dados: abaixo do cabeçalho (tipicamente após 20% da altura)
    y_dados_ini = H * 0.20
    y_dados_fim = H * 0.90
    
    return {
        "x_classif_min": x_classif_min,
        "x_golpes_min":  x_golpes_min,
        "x_golpes_max":  x_golpes_max,
        "x_escala_min":  x_escala_min,
        "x_escala_max":  x_escala_max,
        "y_dados_ini":   y_dados_ini,
        "y_dados_fim":   y_dados_fim,
        "W": W, "H": H,
    }


def _parse_bbox_generico(pagina) -> list:
    """
    Parser bbox genérico para boletins SPT.
    Detecta automaticamente as colunas e associa descrições por Y.
    
    Funciona para: New Solos/ECO Noroeste, Souli, Suporte e outros.
    Para Geoloc usa _parse_geoloc_bbox (mais preciso).
    """
    import re
    _ORIGENS = {"SRM", "SRJ", "SS", "AT", "SR", "AL", "DFL", "SAR", "RC", "SRR"}
    _EXCLUIR_TEXTO = {
        "LIMITE", "OBS.:", "NBR-6484", "IMPENETRÁVEL", "TRÉPANO",
        "CIRCULAÇÃO", "LAVAGEM", "PERFURAÇÃO", "SONDAGEM", "REGISTRO",
        "FOTOGRÁFICO", "MEMORIAL", "CREA", "ENGENHEIRO", "RESPONSÁVEL",
    }
    
    cols = _detectar_colunas_bbox(pagina)
    W = cols["W"]; H = cols["H"]
    words = pagina.extract_words(use_text_flow=False, keep_blank_chars=False)
    
    # --- 1. Escala vertical → metro_y ---
    escala_words = [w for w in words
                    if cols["x_escala_min"] <= w["x0"] <= cols["x_escala_max"]
                    and w["top"] > cols["y_dados_ini"]
                    and w["top"] < cols["y_dados_fim"]
                    and re.match(r"^\d{1,2}$", w["text"])
                    and int(w["text"]) <= 30]
    
    metro_y = {}
    for w in escala_words:
        n = int(w["text"])
        y = (w["top"] + w["bottom"]) / 2
        if n not in metro_y:
            metro_y[n] = y
    
    if len(metro_y) < 2:
        return []
    
    # Detectar limite da sondagem
    limite_words = [w for w in words
                    if w["top"] > H * 0.30
                    and any(k in w["text"].upper() for k in ["LIMITE", "IMPENET", "TREPANO"])
                    and w["x0"] > W * 0.30]
    y_limite = min((w["top"] for w in limite_words), default=H * 0.85)
    metro_y = {n: y for n, y in metro_y.items() if y <= y_limite + 40}
    
    if len(metro_y) < 2:
        return []
    
    metros_lista = sorted(metro_y.keys())
    alturas = [metro_y[metros_lista[i]] - metro_y[metros_lista[i-1]]
               for i in range(1, len(metros_lista))]
    altura_metro = sum(alturas) / len(alturas) if alturas else 28.0
    
    # --- 2. Golpes por metro ---
    golpes_words = [w for w in words
                    if cols["x_golpes_min"] <= w["x0"] <= cols["x_golpes_max"]
                    and w["top"] > cols["y_dados_ini"]
                    and w["top"] <= y_limite + 15
                    and re.match(r"^\d{1,2}$", w["text"])
                    and 0 <= int(w["text"]) <= 60]
    
    def _y_metro(y_pt):
        for idx, n in enumerate(metros_lista):
            if n == 0:
                continue
            n_prev = metros_lista[idx - 1]
            y_base = metro_y[n]
            y_topo = metro_y[n_prev]
            if y_topo - 12 <= y_pt <= y_base + 12:
                return n
        return None
    
    golpes_por_metro = {}
    for w in golpes_words:
        n = _y_metro((w["top"] + w["bottom"]) / 2)
        if n is not None:
            golpes_por_metro.setdefault(n, []).append(int(w["text"]))
    
    # --- 3. Classificação por coluna X ---
    classif_words = [w for w in words
                     if w["x0"] >= cols["x_classif_min"]
                     and w["top"] > cols["y_dados_ini"]
                     and w["top"] <= y_limite + altura_metro
                     and not re.match(r"^\d{3,},\d{2}$", w["text"])
                     and not re.match(r"^\d+/\d+$", w["text"])
                     and w["text"] not in _EXCLUIR_TEXTO]
    
    # Agrupar palavras em linhas (Y ± 4pts)
    linhas_classif = []
    if classif_words:
        cs = sorted(classif_words, key=lambda w: w["top"])
        la = [cs[0]]
        for w in cs[1:]:
            if abs(w["top"] - la[-1]["top"]) < 5:
                la.append(w)
            else:
                linhas_classif.append(la)
                la = [w]
        linhas_classif.append(la)
    
    def _lt(wds):
        return " ".join(w["text"] for w in sorted(wds, key=lambda w: w["x0"]))
    
    # Agrupar linhas em blocos de horizonte
    blocos_classif = []
    if linhas_classif:
        bl = [linhas_classif[0]]
        for ln in linhas_classif[1:]:
            gap = ln[0]["top"] - bl[-1][0]["top"]
            if gap < altura_metro * 1.5:
                bl.append(ln)
            else:
                y0 = bl[0][0]["top"]; y1 = bl[-1][0]["bottom"]
                txts = []; orig = ""
                for b in bl:
                    t = _lt(b)
                    if t.upper() in _ORIGENS:
                        orig = t.upper()
                    elif not any(k in t.upper() for k in _EXCLUIR_TEXTO):
                        txts.append(t)
                if txts:
                    blocos_classif.append((y0, y1, " ".join(txts), orig))
                bl = [ln]
        if bl:
            y0 = bl[0][0]["top"]; y1 = bl[-1][0]["bottom"]
            txts = []; orig = ""
            for b in bl:
                t = _lt(b)
                if t.upper() in _ORIGENS:
                    orig = t.upper()
                elif not any(k in t.upper() for k in _EXCLUIR_TEXTO):
                    txts.append(t)
            if txts:
                blocos_classif.append((y0, y1, " ".join(txts), orig))
    
    # --- 4. Associar blocos → metros ---
    desc_por_metro = {}
    orig_por_metro = {}
    y_max = max(metro_y.values())
    
    for j, (y_ini, y_fim, texto, orig) in enumerate(blocos_classif):
        y_prox = blocos_classif[j + 1][0] if j + 1 < len(blocos_classif) else y_max + altura_metro
        for n in metros_lista:
            if n == 0:
                continue
            n_prev = metros_lista[metros_lista.index(n) - 1]
            yp = metro_y.get(n_prev, metro_y[n] - altura_metro)
            yc = (yp + metro_y[n]) / 2
            if y_ini - altura_metro * 0.3 <= yc < y_prox:
                if n not in desc_por_metro:
                    desc_por_metro[n] = texto
                    orig_por_metro[n] = orig
    
    # Preencher metros sem descrição com o anterior
    ultima_desc = ""; ultima_orig = ""
    for n in metros_lista:
        if n == 0:
            continue
        if n in desc_por_metro:
            ultima_desc = desc_por_metro[n]
            ultima_orig = orig_por_metro.get(n, "")
        else:
            desc_por_metro[n] = ultima_desc
            orig_por_metro[n] = ultima_orig
    
    # --- 5. Montar MetroSPT ---
    metros_spt = []
    for n in metros_lista:
        if n == 0:
            continue
        gs = sorted(golpes_por_metro.get(n, []))
        gs_v = [g for g in gs if g != 15]
        if len(gs_v) >= 3:
            g1, g2, g3 = gs_v[0], gs_v[1], gs_v[2]
        elif len(gs_v) == 2:
            g1, g2, g3 = 0, gs_v[0], gs_v[1]
        elif len(gs) >= 3:
            g1, g2, g3 = gs[0], gs[1], gs[2]
        else:
            continue
        metros_spt.append(MetroSPT(
            prof_m    = float(n),
            nspt      = g2 + g3,
            golpes_1  = g1, golpes_2  = g2, golpes_3  = g3,
            descricao = desc_por_metro.get(n, ""),
            origem    = orig_por_metro.get(n, ""),
        ))
    
    return metros_spt


# ---------------------------------------------------------------------------
# Interface pública
# ---------------------------------------------------------------------------

def ler_pdf_sondagem(caminho_pdf: str) -> list[SondagemSPT]:
    sondagens = []
    with pdfplumber.open(caminho_pdf) as pdf:
        for idx, pagina in enumerate(pdf.pages):
            texto = pagina.extract_text() or ""

            eh_perfil = any(kw in texto for kw in [
                "Classificação do Material", "Resistência à Penetração",
                "PERFIL INDIVIDUAL", "Sondagem a Percussão",
                "Sondagem de Reconhecimento", "N-SPT", "NSPT",
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

            # Detectar empresa e usar parser bbox dedicado
            if "GEOLOC ENGENHARIA E GEOLOGIA" in texto:
                # Parser bbox específico para Geoloc
                metros = _parse_geoloc_bbox(pagina)
                blocos = []
            elif any(kw in texto for kw in [
                "NEW SOLOS", "ECO NOROESTE", "ECO-NOROESTE",
                "SOULI", "SUPORTE SONDAGENS", "Suporte Sondagens",
            ]):
                # Parser bbox genérico para demais empresas com layout tabular
                metros = _parse_bbox_generico(pagina)
                blocos = []
                # Fallback: se bbox não extraiu metros suficientes, tentar texto
                if len(metros) < 3:
                    metros_txt, blocos = _parse_pagina(texto)
                    metros_txt = _associar(metros_txt, blocos)
                    if len(metros_txt) > len(metros):
                        metros = metros_txt
                        blocos = []
            else:
                # Parser genérico de texto para layouts desconhecidos
                metros, blocos = _parse_pagina(texto)
                metros = _associar(metros, blocos)
                # Tentar também o bbox genérico e usar o melhor
                metros_bbox = _parse_bbox_generico(pagina)
                if len(metros_bbox) > len(metros):
                    metros = metros_bbox
                    blocos = []

            # Deduplicar e ordenar
            vistos: set[float] = set()
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


def agrupar_horizontes(
    metros: list[MetroSPT],
    cota_boca: float,
    offset_cota: float = 0.0,
) -> list[dict]:
    if not metros:
        return []

    horizontes = []
    desc_atual = metros[0].descricao or "Não identificado"
    orig_atual = metros[0].origem
    grupo: list[MetroSPT] = [metros[0]]

    for metro in metros[1:]:
        desc = metro.descricao or desc_atual
        orig = metro.origem or orig_atual
        if metro.descricao and metro.descricao != desc_atual:
            _fechar_h(horizontes, grupo, desc_atual, orig_atual, offset_cota)
            desc_atual = desc
            orig_atual = orig
            grupo = []
        grupo.append(metro)

    _fechar_h(horizontes, grupo, desc_atual, orig_atual, offset_cota)
    return horizontes


def _fechar_h(horizontes, metros, desc, orig, offset):
    if not metros:
        return
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
