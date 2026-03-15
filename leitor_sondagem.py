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

            metros, blocos = _parse_pagina(texto)
            metros = _associar(metros, blocos)

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
