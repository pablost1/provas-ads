"""Leitores de gabarito além do parser genérico de extrair_questoes.parse_gabarito.

Todos devolvem {(rótulo, tipo): {n: letra}}, o mesmo formato do parser genérico, para
serem unidos e passados a escolhe_secao().
"""
import re
from pathlib import Path

import pymupdf as fitz

from assuntos import sem_acento

RE_MARCA = re.compile(r"pcimarkpci|www\.pciconcursos\.com\.br", re.I)
SEP = "-–—�"
LETRA = "ABCDEXx*"


def prioridade(nome: str) -> int:
    """Menor = melhor. Definitivo/retificado antes de preliminar/provisório."""
    n = sem_acento(nome)
    if re.search(r"definitiv|final|oficial", n):
        return 0
    if re.search(r"retificad", n):
        return 1
    if re.search(r"preliminar|provisor|antes", n):
        return 3
    return 2


def tokens(texto: str) -> list[str]:
    t = re.sub(r"[^a-z0-9 ]", " ", sem_acento(texto))
    return [w for w in t.split() if len(w) >= 3 and w not in ("para", "dos", "das", "com", "tipo", "cargo", "area", "prova")]


def pontua(rotulo: str, cargo: str, pistas: str = "") -> float:
    """Quanto o rótulo de uma seção casa com o cargo da prova (aceita abreviações: MICROINF. ~ microinformática).

    Conta as palavras do cargo presentes no rótulo; palavras de sobra só penalizam rótulos curtos
    (cabeçalhos de página, com nome do órgão etc., são longos).
    """
    ct = tokens(cargo.split(" - ")[-1] + " " + pistas)
    rt = tokens(rotulo)
    if not ct or not rt:
        return 0.0
    cobre = sum(1 for c in ct if any(c == r or (len(r) >= 4 and c.startswith(r)) for r in rt))
    casou = sum(1 for r in rt if any(c == r or (len(r) >= 4 and c.startswith(r)) for c in ct))
    extra = len(rt) - casou if len(rt) <= 8 else 0
    return cobre - 0.5 * extra if cobre else 0.0


def escolhe(secoes: dict, cargo: str, tipo_prova: str, n_questoes: int, pistas: str = ""):
    """Escolhe a seção do cargo. Casa por pontuação de palavras; desempata por tipo e nº de questões."""
    if not secoes:
        return None, {}
    pts = {k: pontua(k[0], cargo, pistas) for k in secoes}
    melhor = max(pts.values())
    if melhor <= 0:
        if len(secoes) == 1:
            k = next(iter(secoes))
            return k, secoes[k]
        return None, {}
    cand = [k for k, p in pts.items() if p == melhor]
    cand.sort(key=lambda k: (k[1].rstrip("'") != tipo_prova, abs(max(secoes[k], default=0) - n_questoes)))
    return cand[0], secoes[cand[0]]


# ----------------------------------------------------------------------------- grade de colunas (Cesgranrio)

def _triplas(page):
    """Triplas 'N – L' na página: [(y, centro_x, n, letra)], lidas pelas palavras com coordenadas.

    Aceita 'N', '–', 'L' separados, 'N–' 'L' e 'N–L'; 'ANUL'/'ANULADA' vira X.
    """
    ws = [w for w in page.get_text("words") if not RE_MARCA.search(w[4])]
    ws.sort(key=lambda w: (round(w[1] / 3), w[0]))
    re_l = re.compile(r"^(?:[A-Ea-eXx*]|ANUL\w*)$")
    re_ns = re.compile(r"^(\d{1,3})[" + SEP + r"]$")
    re_nsl = re.compile(r"^(\d{1,3})[" + SEP + r"]([A-Ea-eXx*])$")
    out, i = [], 0

    def letra(t):
        return "X" if t.upper().startswith("ANUL") else t.upper()

    while i < len(ws):
        a = ws[i]
        n = l = None
        usados = 0
        m3 = re_nsl.match(a[4])
        m2 = re_ns.match(a[4])
        if m3:
            n, l, usados = int(m3.group(1)), m3.group(2), 1
            ult = a
        elif m2 and i + 1 < len(ws) and re_l.match(ws[i + 1][4]) and abs(ws[i + 1][1] - a[1]) < 3:
            n, l, usados, ult = int(m2.group(1)), ws[i + 1][4], 2, ws[i + 1]
        elif (a[4].isdigit() and len(a[4]) <= 3 and i + 2 < len(ws) and len(ws[i + 1][4]) == 1 and ws[i + 1][4] in SEP
              and re_l.match(ws[i + 2][4]) and abs(ws[i + 2][1] - a[1]) < 3):
            n, l, usados, ult = int(a[4]), ws[i + 2][4], 3, ws[i + 2]
        if n is not None:
            out.append((a[1], (a[0] + ult[2]) / 2, n, letra(l), a[0]))
            i += usados
        else:
            i += 1
    return out


def parse_grade_colunas(pdf: Path) -> dict:
    """Grade com um bloco 'N – L' por cargo lado a lado (cada número repetido nas colunas).

    Os cabeçalhos das colunas são lidos pela posição-x das palavras acima da primeira linha da grade
    (alinhamento centralizado ou à esquerda, o que casar melhor). Questões fora da grade
    (Conhecimentos Gerais) valem para todas as colunas.
    """
    secoes, comuns = {}, {}
    doc = fitz.open(pdf)
    for page in doc:
        tr = _triplas(page)
        if not tr:
            continue
        linhas = {}
        for y, x, n, l, esq in tr:
            linhas.setdefault(round(y / 3), []).append((x, n, l, esq))
        grade = {k: sorted(v) for k, v in linhas.items() if len(v) >= 4 and len({n for _, n, _, _ in v}) == 1}
        for k, v in linhas.items():
            if k not in grade:
                for _, n, l, _ in v:
                    comuns[n] = l
        if not grade:
            continue
        ys = sorted(grade)
        blocos, atual = [], [ys[0]]
        for k in ys[1:]:
            if k - atual[-1] > 8:        # >24pt: novo bloco (novos cabeçalhos)
                blocos.append(atual)
                atual = []
            atual.append(k)
        blocos.append(atual)
        palavras = [w for w in page.get_text("words") if not RE_MARCA.search(w[4])]
        topo_ant = 0
        for bl in blocos:
            primeira = grade[bl[0]]
            cx = [x for x, _, _, _ in primeira]
            ex = [e for _, _, _, e in primeira]
            y0 = bl[0] * 3
            faixa = [w for w in palavras if max(topo_ant, y0 - 220) < w[1] < y0 - 4]
            # a faixa estreita (cabeçalho colado) vale se houver texto; senão, usa a larga
            estreita = [w for w in faixa if w[1] > y0 - 40]
            uso = estreita if len(estreita) >= len(cx) // 2 else faixa
            d_cent = sum(min(abs((w[0] + w[2]) / 2 - c) for c in cx) for w in uso)
            d_esq = sum(min(abs(w[0] - e) for e in ex) for w in uso)
            cab = [[] for _ in cx]
            for w in uso:
                j = (min(range(len(cx)), key=lambda i: abs(ex[i] - w[0])) if d_esq < d_cent
                     else min(range(len(cx)), key=lambda i: abs(cx[i] - (w[0] + w[2]) / 2)))
                cab[j].append(w)
            rot = [" ".join(w[4] for w in sorted(c, key=lambda w: (round(w[1] / 4), w[0]))) for c in cab]
            for k in bl:
                for j, (_, n, l, _) in enumerate(grade[k]):
                    if j < len(rot):
                        secoes.setdefault((rot[j] or f"coluna {j + 1}", "1"), {})[n] = l
            topo_ant = bl[-1] * 3 + 12
    for s in secoes.values():
        for n, l in comuns.items():
            s.setdefault(n, l)
    return secoes


def titulo_pagina(linhas: list[str]) -> str:
    """Cabeçalho do cargo de uma página de gabarito: linhas antes da tabela mais, se houver, o rodapé de texto."""
    corte = next((i for i, l in enumerate(linhas) if re.fullmatch(r"\d{1,3}", l) or re.match(r"^\d{1,3}\s*[" + SEP + r"]\s*[A-E]", l)
                  or re.match(r"QUEST\S{1,2}O\s+\d", l, re.I)), len(linhas))
    topo = linhas[:min(corte, 14)]
    fim = [l for l in linhas[-3:] if len(l) > 15 and re.search(r"[A-Za-zÀ-ú]{4}", l) and not re.match(r"QUEST\S{1,2}O", l, re.I)]
    return " ".join(topo + fim)[:300]


def _texto_paginas(pdf: Path):
    for page in fitz.open(pdf):
        yield [l for l in (x.replace(chr(160), " ").strip() for x in page.get_text().split(chr(10)))
               if l and not RE_MARCA.search(l)]


def parse_extra(pdf: Path) -> dict:
    """Formatos próprios: 'QUESTÃO N L' (Unilavras) e tabelas 'QST GAB' com título 'COD - cargo - TIPO n' (FESAG)."""
    secoes = {}
    re_q = re.compile(r"QUEST\S{1,2}O\s+(\d{1,3})\s+(ANULADA|[A-E])(?![A-Za-z])", re.I)
    re_tit = re.compile(r"(\d{3,5}\s*-\s*[^|]*?)\s*-\s*TIPO\s*(\d)", re.I)
    for linhas in _texto_paginas(pdf):
        txt = chr(10).join(linhas)
        pares = {int(n): ("X" if l.upper().startswith("ANUL") else l.upper()) for n, l in re_q.findall(re.sub(r"\s+", " ", txt))}
        if len(pares) >= 5:
            k = (titulo_pagina(linhas), "1")
            secoes.setdefault(k, {}).update(pares)
            continue
        marcas = list(re_tit.finditer(txt))
        for i, m in enumerate(marcas):
            seg = txt[m.end(): marcas[i + 1].start() if i + 1 < len(marcas) else len(txt)]
            seg = re.sub(r"QST|GAB", " ", seg)
            p = {int(n): l for n, l in re.findall(r"\b(\d{1,3})\s+([A-EX])\b", seg)}
            if len(p) >= 5:
                secoes.setdefault((re.sub(r"\s+", " ", m.group(1)).strip(), m.group(2)), {}).update(p)
    return secoes
