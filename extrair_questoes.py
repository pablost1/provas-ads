#!/usr/bin/env python
"""Passo 2 - extrai questões dos PDFs, cruza com o gabarito e grava em SQLite + JSON.

Uso:
    python extrair_questoes.py                 # processa tudo em data/pdfs
    python extrair_questoes.py --prova <id>    # só uma prova
    python extrair_questoes.py --debug <id>    # imprime o texto "achatado" da prova e sai

Saídas (pasta out/): provas.db, questoes.json, questoes.jsonl, imagens/<prova>/, relatorio.txt
"""
import argparse
import csv
import json
import re
import sqlite3
import sys
from collections import Counter
from pathlib import Path

import pymupdf as fitz

import gabaritos as G

from assuntos import bloco as bloco_de
from assuntos import classificar, sem_acento

BASE = Path(__file__).parent
PDFS = BASE / "data" / "pdfs"
OUT = BASE / "out"

RE_MARCA = re.compile(r"pcimarkpci|www\.pciconcursos\.com\.br", re.I)
RE_ALT_PAREN = re.compile(r"^\(?([A-Ea-e])\)\s*(.*)$")
RE_ALT_PONTO = re.compile(r"^([A-Ea-e])[\.\)]\s+(?:(SQUARE|Check-square)\s*)?(.*)$")
RE_Q_PONTO = re.compile(r"^(\d{1,3})\.(?!\d)\s*(.*)$")
RE_Q_NUM = re.compile(r"^(\d{1,3})\s*$")
RE_Q_PALAVRA = re.compile(r"^QUEST[ÃA]O\s+(\d{1,3})\b\s*(.*)$", re.I)
RE_REFERE_TEXTO = re.compile(r"(texto|trecho|par[áa]grafo|linha|autor|autora|fragmento|figura|tirinha|charge|\(A\.\s*\d)", re.I)
RE_QUESTOES_CITADAS = re.compile(r"quest[õo]es\s+(?:de\s+)?(?:n[ºo°s.]*\s*)?(\d{1,3})\s*(?:a|e|à|ao|at[ée])\s*(\d{1,3})", re.I)
RE_N_QUESTOES = re.compile(r"^\d+\s+quest[õo]es$", re.I)
RE_DISCIPLINA = re.compile(
    r"^(conhecimentos|portugu|l[ií]ngua|racioc[ií]nio|legisla|inform[aá]tica|matem[aá]tica|ingl[eê]s|atualidades|direito|"
    r"no[çc][õo]es|hist[óo]ria|geografia)", re.I)


# ----------------------------------------------------------------------------- texto/imagens

def ordem_leitura(blocos, largura):
    """Ordena blocos de uma página: respeita colunas (esquerda antes da direita) em faixas verticais.
    Devolve (blocos_ordenados, duas_colunas)."""
    meio = largura / 2
    def larga(b): return b[0] < meio - 30 and b[2] > meio + 30 and (b[2] - b[0]) > 0.6 * largura
    esq = sum(1 for b in blocos if not larga(b) and (b[0] + b[2]) / 2 < meio)
    dir_ = sum(1 for b in blocos if not larga(b) and (b[0] + b[2]) / 2 >= meio)
    if esq < 2 or dir_ < 2:
        return sorted(blocos, key=lambda b: (round(b[1]), b[0])), False
    saida, faixa = [], []
    def fecha():
        L = sorted([b for b in faixa if (b[0] + b[2]) / 2 < meio], key=lambda b: b[1])
        R = sorted([b for b in faixa if (b[0] + b[2]) / 2 >= meio], key=lambda b: b[1])
        saida.extend(L + R)
        faixa.clear()
    for b in sorted(blocos, key=lambda b: (round(b[1]), b[0])):
        if larga(b):
            fecha()
            saida.append(b)
        else:
            faixa.append(b)
    fecha()
    return saida, True


POS = "\x00POS|"


def margem(b, w, h):
    """Ruído de margem: texto minúsculo/rotacionado na calha (ex.: '1943 RONDÔNIA 1981') e nº de página solto."""
    if b[3] - b[1] < 6:
        return True
    linhas = [x for x in b[4].split(chr(10)) if x.strip()]
    # rodapé: nº da página seguido do nome do cargo/prova (<= 4 linhas, colado ao fim da folha)
    return b[1] > 0.9 * h and 1 < len(linhas) <= 4 and re.fullmatch(r"\d{1,3}", linhas[0].strip()) is not None


def paginas_para_linhas(doc):
    """Devolve lista de páginas; cada página é lista de linhas (str). Antes de cada bloco de texto vai uma
    linha-sentinela 'POS|pagina|coluna|x0|y0|x1|y1' (coluna: -1 = largura total/1 coluna, 0 esquerda, 1 direita)."""
    paginas = []
    for pno, page in enumerate(doc, 1):
        w, h = page.rect.width, page.rect.height
        usaveis = [b for b in page.get_text("blocks")
                   if b[6] == 0 and not (RE_MARCA.search(b[4]) and len(b[4]) < 200) and not margem(b, w, h)]
        ordem, duas = ordem_leitura(usaveis, w)
        linhas = []
        for b in ordem:
            col = -1
            if duas and not (b[0] < w / 2 - 30 and b[2] > w / 2 + 30 and (b[2] - b[0]) > 0.6 * w):
                col = 0 if (b[0] + b[2]) / 2 < w / 2 else 1
            linhas.append(f"{POS}{pno}|{col}|{b[0]:.1f}|{b[1]:.1f}|{b[2]:.1f}|{b[3]:.1f}")
            for ln in b[4].split(chr(10)):
                linhas.append(ln.replace("\t", " ").replace(" ", " ").replace(" ", " ").rstrip())
        paginas.append(linhas)
    return paginas


def remove_ruido(paginas):
    """Remove cabeçalhos/rodapés repetidos em muitas páginas."""
    n = len(paginas)
    cont = Counter()
    for linhas in paginas:
        vistos = {re.sub(r"\d+", "#", l.strip()) for l in linhas if not l.startswith(POS)}
        cont.update(vistos)
    ruido = set()
    if n >= 4:
        for k, c in cont.items():
            if c >= max(3, 0.4 * n) and 6 <= len(k) <= 120 and not k.startswith("(") and not re.match(r"^[a-e]\.", k):
                ruido.add(k)
    return [[l for l in linhas if l.startswith(POS) or re.sub(r"\d+", "#", l.strip()) not in ruido] for linhas in paginas]


# ----------------------------------------------------------------------------- questões

def junta(partes):
    txt = ""
    for p in partes:
        if not txt:
            txt = p
        elif p.startswith("\n"):
            txt += p
        elif txt.endswith("-") and len(txt) > 1 and txt[-2].isalpha() and p[:1].islower():
            txt = txt[:-1] + p
        else:
            txt += " " + p
    return re.sub(r"[ ]{2,}", " ", txt).strip()


class Q:
    def __init__(self, numero, disciplina):
        self.numero = numero
        self.disciplina = disciplina
        self.enun = []
        self.alts = {}      # letra -> lista de partes
        self.marcada = None  # alternativa marcada no próprio PDF (FEPESE)
        self.atual = None   # letra da alternativa em construção
        self.regs = []      # (pagina, coluna, x0, y0, x1, y1) dos blocos de texto da questão
        self.texto = None   # texto de apoio compartilhado (dict) ou None
        self.primeira = False

    def completa(self):
        return len(self.alts) >= 4

    def marca(self, pos):
        if not pos or (self.regs and self.regs[-1] == pos):
            return
        if self.regs and len(self.alts) >= 5 and self.alts.get("E"):
            ant = self.regs[-1]
            # texto solto bem depois da última alternativa (folha de redação, rodapé etc.) não faz parte da questão
            if pos[0] != ant[0] or (pos[1] == ant[1] and pos[3] - ant[5] > 40):
                return
        self.regs.append(pos)

    def add(self, linha):
        alvo = self.alts[self.atual] if self.atual else self.enun
        alvo.append(linha)


def parse_questoes(paginas):
    # ignora capa/instruções: começa na primeira página que tem alternativas
    ini = next((k for k, p in enumerate(paginas)
                if sum(1 for l in p if RE_ALT_PAREN.match(l.strip()) or RE_ALT_PONTO.match(l.strip())) >= 3), 0)
    linhas = [l for p in paginas[ini:] for l in p]
    qs, atual, disciplina = [], None, ""
    vistos, ultimo = set(), 0
    pos = None
    textos, texto_atual = [], None   # textos de apoio (compartilhados por várias questões)
    suporte, regs_sup, suporte_ativo = [], [], False
    novo_bloco = False
    ult_heading = -9

    def prox_texto(k):
        while k < len(linhas):
            t = linhas[k].strip()
            if t and not t.startswith(POS):
                return t
            k += 1
        return ""

    def bloco_em(k):
        """Texto do bloco que começa na linha k (até a próxima sentinela) e índice da sentinela seguinte."""
        partes = []
        while k < len(linhas) and not linhas[k].startswith(POS):
            if linhas[k].strip():
                partes.append(linhas[k].strip())
            k += 1
        return " ".join(partes), k

    def inicio_de_questao(t, esperado):
        m = RE_Q_PALAVRA.match(t) or RE_Q_PONTO.match(t)
        if m:
            return True
        m = RE_Q_NUM.match(t.split()[0]) if t.split() else None
        return bool(m) and int(m.group(1)) == esperado

    def tem_alternativas(t):
        # bloco com alternativas ou numeração de questão dentro: é questão, não texto de apoio
        return len(re.findall(r"(?:^|\s)\(?[A-Ea-e]\)\s", t)) >= 2 or len(re.findall(r"(?:^|\s)\d{1,3}\)\s", t)) >= 1

    def comeca_suporte(k):
        """O bloco da linha k abre um texto de apoio? (bloco longo, ou título curto seguido de bloco longo)"""
        t, fim = bloco_em(k)
        if not t or t[0].islower() or inicio_de_questao(t, ultimo + 1) or tem_alternativas(t):
            return False
        if len(t) >= 100:
            return True
        if len(t) <= 80 and not t.endswith((".", ":", ";", "?")) and fim < len(linhas):
            prox, _ = bloco_em(fim + 1)
            return len(prox) >= 100 and not inicio_de_questao(prox, ultimo + 1) and not tem_alternativas(prox)
        return False

    def proxima_letra():
        if atual is None:
            return "A"
        return "ABCDE"[len(atual.alts)] if len(atual.alts) < 5 else None

    i = 0
    while i < len(linhas):
        bruta = linhas[i]
        i += 1
        if bruta.startswith(POS):
            f = bruta[len(POS):].split("|")
            pos = (int(f[0]), int(f[1])) + tuple(float(x) for x in f[2:6])
            novo_bloco = True
            continue
        indent = len(bruta) - len(bruta.lstrip())
        l = re.sub(r"\s+", " ", bruta).strip()
        if not l:
            continue
        pronto = atual is None or atual.completa()
        inicio_bloco, novo_bloco = novo_bloco, False

        # cabeçalho de disciplina
        if pronto and len(l) <= 70 and not l.endswith((".", ":", "?", ";")):
            if RE_N_QUESTOES.match(l):
                prev = ""
                for k in range(i - 2, max(i - 4, -1), -1):
                    t = re.sub(r"\s+", " ", linhas[k]).strip()
                    if t:
                        prev = t
                        break
                disciplina = prev
                suporte, regs_sup, suporte_ativo = [], [], False
                continue
            if RE_DISCIPLINA.match(l) and not RE_Q_PONTO.match(l):
                disciplina = l
                suporte, regs_sup, suporte_ativo = [], [], False
                ult_heading = i
                continue
            if ult_heading == i - 1 and l.isupper() and not RE_Q_PONTO.match(l):
                disciplina += " " + l          # cabeçalho quebrado em dois blocos ("HISTÓRIA E GEOGRAFIA DO" / "ESTADO DE RONDÔNIA II")
                ult_heading = i
                continue

        # alternativa
        letra = proxima_letra() if atual is not None else None
        if atual is not None and letra:
            m = RE_ALT_PAREN.match(l)
            marcada = False
            if m and m.group(1).upper() == letra:
                txt = m.group(2)
            else:
                m = RE_ALT_PONTO.match(l)
                if m and m.group(1).upper() == letra:
                    txt = m.group(3)
                    marcada = m.group(2) == "Check-square"
                else:
                    m = None
            if m:
                atual.alts[letra] = [txt] if txt else []
                atual.atual = letra
                atual.marca(pos)
                if marcada:
                    atual.marcada = letra
                continue

        # início de questão
        novo = None
        m = RE_Q_PALAVRA.match(l)
        if m:
            novo = (int(m.group(1)), m.group(2))
        else:
            m = RE_Q_PONTO.match(l)
            if m:
                novo = (int(m.group(1)), m.group(2))
            else:
                m = RE_Q_NUM.match(l)
                if m and int(m.group(1)) == ultimo + 1 and (ultimo or int(m.group(1)) <= 5):
                    novo = (int(m.group(1)), "")
        if novo and not novo[1] and RE_ALT_PAREN.match(prox_texto(i)):
            novo = None  # o "12." é conteúdo de uma alternativa, não início de questão
        if novo and novo[0] not in vistos and novo[0] <= 200:
            n, resto = novo
            esperando = atual is not None and atual.atual and not atual.alts[atual.atual]
            ok = (pronto and (not esperando or n == ultimo + 1)) or (
                atual is not None and len(atual.alts) >= 2 and n == ultimo + 1)
            if ok:
                if suporte:
                    conteudo = junta(suporte)
                    if len(conteudo) >= 80:
                        texto_atual = {"conteudo": conteudo, "disciplina": disciplina, "de": n, "ate": n, "regs": regs_sup,
                                       "fim_citado": n}
                        mq = RE_QUESTOES_CITADAS.search(conteudo)   # "Para responder às questões de nos 30 e 31..."
                        if mq:
                            texto_atual["fim_citado"] = max(n, int(mq.group(2)))
                        textos.append(texto_atual)
                    suporte, regs_sup, suporte_ativo = [], [], False
                elif texto_atual and texto_atual["disciplina"] != disciplina:
                    texto_atual = None
                atual = Q(n, disciplina)
                atual.texto = texto_atual
                atual.primeira = bool(texto_atual) and texto_atual["de"] == n
                atual.marca(pos)
                qs.append(atual)
                vistos.add(n)
                ultimo = n
                if resto:
                    atual.enun.append(resto)
                continue

        if not suporte_ativo and inicio_bloco:
            if atual is None:
                suporte_ativo = bool(disciplina) and comeca_suporte(i - 1)
            elif len(atual.alts) >= 4 and all(atual.alts.values()) and comeca_suporte(i - 1):
                suporte_ativo = True
        if suporte_ativo:
            if pos and (not regs_sup or regs_sup[-1] != pos):
                regs_sup.append(pos)
            if re.fullmatch(r"\d{1,3}", l) and int(l) % 5 == 0:
                continue   # numeração de linhas na margem do texto (5, 10, 15...)
            suporte.append((chr(10) if inicio_bloco and suporte else "") + l)
            continue
        if atual is None:
            continue
        atual.marca(pos)
        if indent >= 2 and not atual.atual:
            atual.add("\n" + l)
        else:
            atual.add(l)

    saida = []
    encerrados, perdidas = set(), {}
    for q in qs:
        enun = junta(q.enun)
        # itens "I.", "II.", "III." passam a ocupar linhas próprias
        enun = re.sub(r"(?<=[.:;?!)])\s+(?=(?:I|II|III|IV|V|VI)\s?[.\-–]\s)", "\n", enun)
        alts = {k: junta(v) for k, v in q.alts.items()}
        texto = None
        if q.texto and id(q.texto) not in encerrados:
            if q.primeira or q.texto["de"] <= q.numero <= q.texto["fim_citado"]:
                texto = q.texto
            elif RE_REFERE_TEXTO.search(enun + " " + " ".join(alts.values())):
                perdidas.pop(id(q.texto), None)
                texto = q.texto
            else:
                perdidas[id(q.texto)] = perdidas.get(id(q.texto), 0) + 1
                if perdidas[id(q.texto)] >= 2:   # duas questões seguidas sem citar o texto: ele acabou
                    encerrados.add(id(q.texto))
        if texto:
            texto["ate"] = max(texto["ate"], q.numero)
        regs = (q.texto["regs"] + q.regs) if (texto and q.primeira) else q.regs   # figuras do texto ficam com a 1ª questão
        saida.append({"numero": q.numero, "disciplina": q.disciplina, "enunciado": enun, "alternativas": alts,
                      "marcada_no_pdf": q.marcada, "regs": regs, "texto": texto})
    return saida


# ----------------------------------------------------------------------------- figuras

def funde(rects, folga=10):
    """Une retângulos que se tocam (com folga) até estabilizar."""
    rects = list(rects)
    mudou = True
    while mudou:
        mudou = False
        for a in range(len(rects)):
            for b in range(a + 1, len(rects)):
                ra, rb = rects[a], rects[b]
                if fitz.Rect(ra.x0 - folga, ra.y0 - folga, ra.x1 + folga, ra.y1 + folga).intersects(rb):
                    rects[a] = ra | rb
                    del rects[b]
                    mudou = True
                    break
            if mudou:
                break
    return rects


def retangulos_visuais(page):
    """(desenhos, imagens) da página: clusters vetoriais fundidos e imagens rasterizadas, filtrados por tamanho."""
    w, h = page.rect.width, page.rect.height

    def bons(rects):
        saida = []
        for r in rects:
            r = r & page.rect
            if r.is_empty or r.width < 40 or r.height < 25:
                continue
            if r.width * r.height > 0.6 * w * h or r.y1 < 0.05 * h or r.y0 > 0.95 * h:
                continue
            saida.append(r)
        return saida

    try:
        desenhos = funde([fitz.Rect(r) for r in page.cluster_drawings()])
    except Exception:
        desenhos = []
    try:
        imagens = [fitz.Rect(i["bbox"]) for i in page.get_image_info()]
    except Exception:
        imagens = []
    return bons(desenhos), bons(imagens)


def extrai_figuras(doc, questoes, pid):
    """Liga cada figura da página à questão cujo trecho (coluna + faixa vertical) a contém; salva PNGs.
    Devolve {numero: [caminho_relativo, ...]}."""
    por_numero = {q["numero"]: q for q in questoes}
    por_numero = {q["numero"]: q for q in questoes}
    cont = {}
    for q in questoes:
        for (pno, col, *_r) in q["regs"]:
            cont.setdefault(pno, Counter())[col] += 1
    # página realmente de 2 colunas: poucos blocos de largura total e as duas colunas bem povoadas
    duas = {pno: c[0] >= 3 and c[1] >= 3 and c[-1] <= 0.3 * sum(c.values()) for pno, c in cont.items()}
    por_pagina = {}
    for q in questoes:
        for (pno, col, x0, y0, x1, y1) in q["regs"]:
            por_pagina.setdefault((pno, col if duas[pno] else -1), []).append((q["numero"], y0, y1))
    saida = {}
    # figuras com a mesma geometria em muitas páginas são marca d'água/decoração
    bruto = {n: retangulos_visuais(doc[n - 1]) for n in range(1, len(doc) + 1)}
    chave = lambda r: (round(r.x0 / 6), round(r.y0 / 6), round(r.width / 6), round(r.height / 6))
    sig = Counter()
    for des, ims in bruto.values():
        sig.update({chave(r) for r in des + ims})
    limite = max(3, 0.25 * len(doc))
    visuais = {n: [r for r in des + ims if sig[chave(r)] < limite] for n, (des, ims) in bruto.items()}
    for (pno, col), itens in por_pagina.items():
        page = doc[pno - 1]
        w, h = page.rect.width, page.rect.height
        faixa = {-1: (0, w), 0: (0, w / 2 + 8), 1: (w / 2 - 8, w)}[col]
        # início/fim de cada questão nesta coluna
        ini = {}
        for n, y0, y1 in itens:
            lo, hi = ini.get(n, (y0, y1))
            ini[n] = (min(lo, y0), max(hi, y1))
        starts = sorted(lo for lo, _ in ini.values())
        figs = [r for r in visuais[pno] if r.x0 >= faixa[0] - 20 and r.x1 <= faixa[1] + 20]
        usadas = set()
        for n, (lo, hi) in sorted(ini.items(), key=lambda kv: kv[1][0]):
            prox = min([s_ for s_ in starts if s_ > lo + 1], default=h * 0.95)
            fim = min(hi + 160, prox)
            q = por_numero[n]
            alt_em_imagem = any(not v.strip() for v in q["alternativas"].values())
            q = por_numero[n]
            alt_em_imagem = any(not v.strip() for v in q["alternativas"].values())
            for k, r in enumerate(figs):
                if k in usadas or r.y0 < lo - 4 or r.y0 > fim + 2:
                    continue
                if r.y0 > hi + 2 and not alt_em_imagem:
                    continue  # abaixo de todo o texto da questão: folha de respostas, logo etc.
                if r.y0 > hi + 2 and not alt_em_imagem:
                    continue  # abaixo de todo o texto da questão: folha de respostas, logo etc.
                usadas.add(k)
                nome = f"p{pno}_c{col}_{int(r.y0)}.png"
                destino = OUT / "imagens" / pid
                destino.mkdir(parents=True, exist_ok=True)
                clip = fitz.Rect(r.x0 - 4, r.y0 - 4, r.x1 + 4, r.y1 + 4) & page.rect
                page.get_pixmap(clip=clip, dpi=150).save(destino / nome)
                arq = f"{pid}/{nome}"
                if arq not in saida.setdefault(n, []):
                    saida[n].append(arq)
    return saida


# ----------------------------------------------------------------------------- gabarito

RE_G_LISTA = re.compile(r"^(\d{1,3})\s*[-–—�]\s*([A-Ea-eXx*])\s*$")
RE_INT = re.compile(r"^\d{1,3}$")
RE_LETRA = re.compile(r"^[A-Ea-eXx*]$")


def parse_gabarito(pdf: Path):
    """Devolve {(rótulo, tipo): {n: letra}}.

    Cabeçalhos "Cargo ou opção ..." entram numa fila e são consumidos, na ordem, a cada nova lista de
    respostas (numeração recomeça). Cobre tanto "cabeçalho + lista" quanto "todos os cabeçalhos, depois
    todas as listas" (FCC). Sem cabeçalho na fila, usa a última linha de texto como rótulo (FGV/FEPESE).
    """
    doc = fitz.open(pdf)
    linhas = []
    for page in doc:
        linhas += linhas_da_pagina(page)
    return parse_gabarito_linhas(linhas)


def linhas_da_pagina(page):
    return [l for l in (x.replace(chr(160), " ").strip() for x in page.get_text().split(chr(10)))
            if l and not RE_MARCA.search(l)]


def parse_gabarito_linhas(linhas):
    secoes = {}
    fila = []                 # [rótulo, tipo]
    rotulo, tipo_rot = "", "1"
    rotulo_i = -9
    cur = None
    ultimo_n = 0

    def nova_secao():
        nonlocal cur
        if fila:
            lab, tp = fila.pop(0)
        else:
            lab, tp = rotulo, tipo_rot
        k, c = (lab, tp), 2
        while k in secoes:
            k, c = (lab, tp + "'" * c), c + 1
        secoes[k] = {}
        cur = secoes[k]

    i = 0
    while i < len(linhas):
        l = linhas[i]
        m = RE_G_LISTA.match(l)
        if m:
            n = int(m.group(1))
            if cur is None or n <= ultimo_n:
                nova_secao()
            cur[n] = m.group(2).upper()
            ultimo_n = n
            i += 1
            continue
        if RE_INT.match(l):
            ints, j = [], i
            while j < len(linhas) and RE_INT.match(linhas[j]):
                ints.append(int(linhas[j]))
                j += 1
            letras = []
            while j < len(linhas) and RE_LETRA.match(linhas[j]):
                letras.append(linhas[j].upper())
                j += 1
            if letras and len(letras) <= len(ints):
                if cur is None or ints[0] <= ultimo_n:
                    nova_secao()
                for n, a_ in zip(ints, letras):
                    cur[n] = a_
                ultimo_n = ints[len(letras) - 1]
                i = j
                continue
            i += 1
            continue
        mt = re.search(r"tipo\s*(?:de\s*)?gabarito:?\s*(\d+)", l, re.I)
        if mt:
            if fila:
                fila[-1][1] = mt.group(1)
            else:
                tipo_rot = mt.group(1)
        elif re.match(r"cargo ou op", l, re.I):
            partes, j = [l], i + 1
            while j < len(linhas) and not re.match(r"tipo", linhas[j], re.I) and len(partes) < 3                     and not RE_G_LISTA.match(linhas[j]) and not re.match(r"C\. Gerais", linhas[j]):
                partes.append(linhas[j])
                j += 1
            fila.append([" ".join(partes), "1"])
            i = j
            continue
        elif len(l) >= 8 and re.search(r"[A-Za-zÀ-ú]{4}", l) and not re.match(r"p[áa]gina \d+", l, re.I):
            rotulo = (rotulo + " " + l) if rotulo_i == i - 1 else l   # título quebrado em várias linhas
            rotulo_i = i
            mp = re.search(r"PROVA\s+(\d+)", l)
            tipo_rot = mp.group(1) if mp else "1"
            ultimo_n = 0 if cur is not None and False else ultimo_n
        i += 1
    return {k: v for k, v in secoes.items() if v}


def parse_por_pagina(pdf: Path):
    """Gabaritos 'um cargo por página': o rótulo é o cabeçalho da página (ex.: 'ANALISTA - DESENVOLVIMENTO DE SISTEMAS')."""
    doc = fitz.open(pdf)
    secoes = {}
    for page in doc:
        linhas = linhas_da_pagina(page)
        titulo = G.titulo_pagina(linhas)
        atual = {}
        for sec in parse_gabarito_linhas(linhas).values():
            if atual and set(atual) & set(sec):           # numeração repetida = outro tipo de prova
                secoes[(titulo, str(len([k for k in secoes if k[0] == titulo]) + 1))] = atual
                atual = {}
            atual.update(sec)
        if atual:
            k = (titulo, "1")
            if k in secoes and not set(secoes[k]) & set(atual):   # continuação do mesmo cargo na página seguinte
                secoes[k].update(atual)
            else:
                secoes[(titulo, str(len([x for x in secoes if x[0] == titulo]) + 1))] = atual
    return secoes


def escolhe_secao(secoes, cargo, tipo_prova):
    alvo = sem_acento(cargo.split(" - ")[-1]).strip()
    cand = {k: v for k, v in secoes.items() if alvo and alvo in sem_acento(k[0])}
    if not cand:
        if len(secoes) == 1:
            k = next(iter(secoes))
            return k, secoes[k]
        return None, {}
    pref = [k for k in cand if k[1] == tipo_prova]
    k = pref[0] if pref else next(iter(cand))
    return k, cand[k]


def tipo_da_prova(nome_arquivo, paginas):
    m = re.search(r"tipo[_ ]0*(\d)", nome_arquivo, re.I)
    if m:
        return m.group(1)
    topo = " ".join(paginas[0][:80]) if paginas else ""
    m = re.search(r"TIPO\s+(\d)", topo)
    return m.group(1) if m else "1"


# ----------------------------------------------------------------------------- banco

SCHEMA = """
CREATE TABLE IF NOT EXISTS provas(id TEXT PRIMARY KEY, cargo TEXT, orgao TEXT, banca TEXT, ano INTEGER, url TEXT,
    arquivo_prova TEXT, arquivo_gabarito TEXT, secao_gabarito TEXT);
CREATE TABLE IF NOT EXISTS questoes(id TEXT PRIMARY KEY, prova_id TEXT REFERENCES provas(id), numero INTEGER,
    disciplina TEXT, bloco TEXT, enunciado TEXT, a TEXT, b TEXT, c TEXT, d TEXT, e TEXT,
    gabarito TEXT, anulada INTEGER DEFAULT 0, marcada_no_pdf TEXT);
CREATE TABLE IF NOT EXISTS textos(id TEXT PRIMARY KEY, prova_id TEXT REFERENCES provas(id), disciplina TEXT,
    conteudo TEXT, de_numero INTEGER, ate_numero INTEGER);
CREATE TABLE IF NOT EXISTS imagens(questao_id TEXT REFERENCES questoes(id), arquivo TEXT);
CREATE TABLE IF NOT EXISTS assuntos(id INTEGER PRIMARY KEY, nome TEXT UNIQUE);
CREATE TABLE IF NOT EXISTS questao_assunto(questao_id TEXT REFERENCES questoes(id), assunto_id INTEGER REFERENCES assuntos(id));
CREATE TABLE IF NOT EXISTS tentativas(id INTEGER PRIMARY KEY AUTOINCREMENT, simulado_id TEXT, prova_id TEXT, questao_id TEXT,
    resposta TEXT, correta INTEGER, ts TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE INDEX IF NOT EXISTS ix_q_prova ON questoes(prova_id);
CREATE INDEX IF NOT EXISTS ix_t_prova ON textos(prova_id);
CREATE INDEX IF NOT EXISTS ix_qa ON questao_assunto(assunto_id, questao_id);
"""


def grava(con, prova, questoes):
    pid = prova["id"]
    ids = [r[0] for r in con.execute("SELECT id FROM questoes WHERE prova_id=?", (pid,))]
    for qid in ids:
        con.execute("DELETE FROM imagens WHERE questao_id=?", (qid,))
        con.execute("DELETE FROM questao_assunto WHERE questao_id=?", (qid,))
    con.execute("DELETE FROM questoes WHERE prova_id=?", (pid,))
    con.execute("DELETE FROM textos WHERE prova_id=?", (pid,))
    vistos = set()
    for q in questoes:
        t = q.get("texto")
        if t and t["id"] not in vistos:
            vistos.add(t["id"])
            con.execute("INSERT INTO textos VALUES (?,?,?,?,?,?)",
                        (t["id"], pid, t["disciplina"], t["conteudo"], t["de"], t["ate"]))
    con.execute("INSERT OR REPLACE INTO provas VALUES (?,?,?,?,?,?,?,?,?)",
                (pid, prova["cargo"], prova["orgao"], prova["banca"], int(prova["ano"] or 0), prova["url"],
                 prova.get("arquivo_prova"), prova.get("arquivo_gabarito"), prova.get("secao_gabarito")))
    for q in questoes:
        alts = q["alternativas"]
        con.execute("INSERT INTO questoes(id, prova_id, numero, disciplina, bloco, enunciado, a, b, c, d, e, gabarito, anulada,"
                    " marcada_no_pdf, texto_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (q["id"], pid, q["numero"], q["disciplina"], q["bloco"], q["enunciado"],
                     alts.get("A"), alts.get("B"), alts.get("C"), alts.get("D"), alts.get("E"),
                     q["gabarito"], int(q["anulada"]), q["marcada_no_pdf"], q["texto"]["id"] if q.get("texto") else None))
        for arq in q["imagens"]:
            con.execute("INSERT INTO imagens VALUES (?,?)", (q["id"], arq))
        for nome in q["assuntos"]:
            con.execute("INSERT OR IGNORE INTO assuntos(nome) VALUES (?)", (nome,))
            aid = con.execute("SELECT id FROM assuntos WHERE nome=?", (nome,)).fetchone()[0]
            con.execute("INSERT INTO questao_assunto VALUES (?,?)", (q["id"], aid))


# ----------------------------------------------------------------------------- principal

def processa(pasta: Path, relatorio):
    meta = json.loads((pasta / "arquivos.json").read_text(encoding="utf-8"))
    provas = [a["arquivo"] for a in meta["arquivos"] if a["tipo"] == "prova"]
    gabs = [a["arquivo"] for a in meta["arquivos"] if a["tipo"] == "gabarito"]
    if not provas:
        relatorio.append(f"[{meta['id']}] SEM ARQUIVO DE PROVA")
        return meta, []
    pid = meta["id"]
    doc = fitz.open(pasta / provas[0])
    paginas = remove_ruido(paginas_para_linhas(doc))
    questoes = parse_questoes(paginas)
    tipo = tipo_da_prova(provas[0], paginas)

    ids_texto = {}
    for q in questoes:
        t = q.get("texto")
        if t:
            t["id"] = ids_texto.setdefault(id(t), f"{pid}~T{len(ids_texto) + 1}")
    figuras = extrai_figuras(doc, questoes, pid)

    secao, gab = None, {}
    cod = re.findall(r"(?:CARGO|Cargo)\s*\S{0,2}\s*(\d{3,5})\s*\S{0,2}\s*([\w/]+)", " ".join(paginas[0][:40]))
    pistas = Path(provas[0]).stem + " " + " ".join(" ".join(m) for m in cod)   # nome do arquivo + código do cargo da capa
    nq = max((q["numero"] for q in questoes), default=0)
    for g in sorted(gabs, key=G.prioridade):   # definitivo antes de preliminar
        for leitor in (parse_gabarito, parse_por_pagina, G.parse_grade_colunas, G.parse_extra):
            try:
                secoes = {k: v for k, v in leitor(pasta / g).items() if len(v) >= 3}
            except Exception as e:
                relatorio.append(f"[{pid}] {leitor.__name__} falhou em {g}: {e}")
                continue
            s_, g_ = escolhe_secao(secoes, meta["cargo"], tipo)
            if not g_:
                s_, g_ = G.escolhe(secoes, meta["cargo"], tipo, nq, pistas)
            if len(g_) > len(gab) and len(g_) >= 0.6 * nq:   # seção que cobre pouco a prova é de outro cargo
                secao, gab = s_, g_
                meta["arquivo_gabarito"] = g
        if gab:
            break
    manual = BASE / "data" / "gabaritos_manuais" / f"{pid}.json"
    if manual.exists():   # {"1": "A", "2": "C", "3": "X" (anulada), ...}
        gab = {int(k): v.upper() for k, v in json.loads(manual.read_text(encoding="utf-8")).items()}
        secao = ("gabarito manual", "-")
        meta["arquivo_gabarito"] = manual.name
    meta["arquivo_prova"] = provas[0]
    meta["secao_gabarito"] = f"{secao[0]} | tipo {secao[1]}" if secao else None

    sem_gab = 0
    for q in questoes:
        q["id"] = f"{pid}#{q['numero']:03d}"
        letra = gab.get(q["numero"])
        q["anulada"] = letra in ("X", "*") if letra else False
        q["gabarito"] = letra if letra in list("ABCDE") else None
        if not letra:
            sem_gab += 1
        q["imagens"] = figuras.get(q["numero"], [])
        texto = q["enunciado"] + " " + " ".join(q["alternativas"].values())
        q["assuntos"] = classificar(texto, q["disciplina"])
        q["bloco"] = bloco_de(q["assuntos"])
        divergente = q["marcada_no_pdf"] and q["gabarito"] and q["marcada_no_pdf"] != q["gabarito"]
        q["divergente"] = bool(divergente)
    nums = [q["numero"] for q in questoes]
    faltam = sorted(set(range(1, max(nums) + 1)) - set(nums)) if nums else []
    incompletas = [q["numero"] for q in questoes if len(q["alternativas"]) < 4]
    relatorio.append(
        f"[{pid}] {meta['orgao']} · {meta['banca']} · {meta['ano']} · {meta['cargo']}: {len(questoes)} questões (maior nº {max(nums) if nums else 0}); "
        f"gabarito: {len(gab)} respostas, seção={meta['secao_gabarito']!r}; sem gabarito={sem_gab}; "
        f"nº faltando={faltam[:15]}; com <4 alternativas={incompletas[:15]}; "
        f"divergem da marca do PDF={[q['numero'] for q in questoes if q['divergente']][:15]}")
    return meta, questoes


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--prova")
    ap.add_argument("--debug")
    a = ap.parse_args()
    OUT.mkdir(exist_ok=True)

    if a.debug:
        pasta = PDFS / a.debug
        meta = json.loads((pasta / "arquivos.json").read_text(encoding="utf-8"))
        f = next(x["arquivo"] for x in meta["arquivos"] if x["tipo"] == "prova")
        paginas = remove_ruido(paginas_para_linhas(fitz.open(pasta / f)))
        for n, p in enumerate(paginas, 1):
            print(f"#### PAGINA {n}")
            print("\n".join(p))
        return

    con = sqlite3.connect(OUT / "provas.db")
    con.executescript(SCHEMA)
    if "texto_id" not in [r[1] for r in con.execute("PRAGMA table_info(questoes)")]:
        con.execute("ALTER TABLE questoes ADD COLUMN texto_id TEXT")
    relatorio, todas = [], []
    pastas = sorted(p for p in PDFS.iterdir() if (p / "arquivos.json").exists())
    if a.prova:
        pastas = [p for p in pastas if p.name == a.prova]
    for p in pastas:
        try:
            meta, qs = processa(p, relatorio)
            if qs:
                grava(con, meta, qs)
                con.commit()
                todas += [{**{k: meta[k] for k in ("id", "cargo", "orgao", "banca", "ano")}, **q} for q in qs]
        except Exception as e:  # uma prova ruim não derruba o lote
            relatorio.append(f"[{p.name}] ERRO: {e!r}")
    # exporta tudo do banco (inclui provas de execuções anteriores)
    rows = con.execute("""SELECT q.id, p.id, p.banca, p.ano, p.orgao, p.cargo, q.numero, q.disciplina, q.bloco, q.enunciado,
        q.a,q.b,q.c,q.d,q.e, q.gabarito, q.anulada, q.texto_id FROM questoes q JOIN provas p ON p.id=q.prova_id ORDER BY p.id, q.numero""").fetchall()
    textos_db = dict(con.execute("SELECT id, conteudo FROM textos"))
    export = []
    for r in rows:
        ass = [x[0] for x in con.execute("SELECT a.nome FROM assuntos a JOIN questao_assunto qa ON qa.assunto_id=a.id WHERE qa.questao_id=?", (r[0],))]
        imgs = [x[0] for x in con.execute("SELECT arquivo FROM imagens WHERE questao_id=?", (r[0],))]
        export.append({"id": r[0], "prova_id": r[1], "prova_titulo": f"{r[4]} · {r[2]} · {r[3]} · {r[5]}",
                       "banca": r[2], "ano": r[3], "orgao": r[4], "cargo": r[5], "numero": r[6],
                       "disciplina": r[7], "bloco": r[8], "enunciado": r[9],
                       "alternativas": {k: v for k, v in zip("ABCDE", r[10:15]) if v is not None},
                       "gabarito": r[15], "anulada": bool(r[16]), "assuntos": ass, "imagens": imgs,
                       "texto": textos_db.get(r[17])})
    provas_out = []
    for r in con.execute("""SELECT p.id, p.orgao, p.banca, p.ano, p.cargo, p.url, p.arquivo_prova, p.arquivo_gabarito, p.secao_gabarito,
            COUNT(q.id), COALESCE(SUM(q.gabarito IS NOT NULL), 0), COALESCE(SUM(q.anulada), 0),
            (SELECT COUNT(*) FROM textos t WHERE t.prova_id=p.id),
            (SELECT COUNT(DISTINCT i.questao_id) FROM imagens i JOIN questoes q2 ON q2.id=i.questao_id WHERE q2.prova_id=p.id)
        FROM provas p LEFT JOIN questoes q ON q.prova_id=p.id GROUP BY p.id ORDER BY p.ano DESC, p.orgao, p.id"""):
        provas_out.append(dict(zip(("id", "orgao", "banca", "ano", "cargo", "url", "arquivo_prova", "arquivo_gabarito", "secao_gabarito",
                                    "n_questoes", "n_com_gabarito", "n_anuladas", "n_textos_apoio", "n_com_imagem"), r)))
    (OUT / "provas.json").write_text(json.dumps(provas_out, ensure_ascii=False, indent=1), encoding="utf-8")
    with open(OUT / "provas.csv", "w", encoding="utf-8-sig", newline="") as fh:   # utf-8-sig: o Excel abre com acentos
        w = csv.DictWriter(fh, fieldnames=list(provas_out[0]) if provas_out else ["id"])
        w.writeheader()
        w.writerows(provas_out)
    (OUT / "questoes.json").write_text(json.dumps(export, ensure_ascii=False, indent=1), encoding="utf-8")
    (OUT / "questoes.jsonl").write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in export), encoding="utf-8")
    (OUT / "relatorio.txt").write_text("\n".join(relatorio), encoding="utf-8")
    print("\n".join(relatorio))
    print(f"\n{len(export)} questões no banco -> {OUT / 'provas.db'}")
    con.close()


if __name__ == "__main__":
    sys.exit(main())
