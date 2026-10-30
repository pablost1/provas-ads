"""Auditoria: quanto do texto de cada PDF de prova foi aproveitado no banco?

Compara as palavras do PDF (sem ruído repetido) com as palavras guardadas (enunciados + alternativas + textos
de apoio) e lista as provas com cobertura baixa e as questões que citam um texto que não foi encontrado.

    python auditoria.py            # todas as provas do banco
    python auditoria.py --min 0.9  # limite de cobertura (padrão 0.93)
"""
import argparse
import json
import re
import sqlite3
import sys
from pathlib import Path

import pymupdf as fitz

import extrair_questoes as E
from assuntos import sem_acento

BASE = Path(__file__).parent


def palavras(t: str) -> list[str]:
    return [w for w in re.findall(r"[a-z]{4,}", sem_acento(t))]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--min", type=float, default=0.93)
    a = ap.parse_args()
    con = sqlite3.connect(E.OUT / "provas.db")
    linhas = []
    for (pid,) in con.execute("SELECT id FROM provas ORDER BY id").fetchall():
        pasta = E.PDFS / pid
        try:
            meta = json.loads((pasta / "arquivos.json").read_text(encoding="utf-8"))
            f = pasta / meta["arquivo_prova"] if "arquivo_prova" in meta else pasta / next(
                x["arquivo"] for x in meta["arquivos"] if x["tipo"] == "prova")
            pg = E.remove_ruido(E.paginas_para_linhas(fitz.open(f)))
        except Exception as e:
            linhas.append(f"{pid}: não foi possível ler o PDF ({e!r})")
            continue
        # só o miolo da prova: da primeira página com alternativas em diante
        ini = next((k for k, p in enumerate(pg) if sum(1 for l in p if E.RE_ALT_PAREN.match(l.strip())) >= 3), 0)
        pdf_txt = " ".join(l for p in pg[ini:] for l in p if not l.startswith(E.POS))
        guardado = " ".join(
            " ".join(filter(None, r)) for r in con.execute(
                "SELECT enunciado, a, b, c, d, e FROM questoes WHERE prova_id=?", (pid,)))
        guardado += " " + " ".join(r[0] for r in con.execute("SELECT conteudo FROM textos WHERE prova_id=?", (pid,)))
        pw, gw = palavras(pdf_txt), set(palavras(guardado))
        if not pw:
            continue
        cob = sum(1 for w in pw if w in gw) / len(pw)
        cita = re.compile(r"(no texto|do texto|segundo o texto|texto acima|trecho|\(A\. ?\d)", re.I)
        sem_texto = [n for n, e in con.execute(
            "SELECT numero, enunciado FROM questoes WHERE prova_id=? AND texto_id IS NULL AND length(enunciado) < 260 "
            "ORDER BY numero", (pid,)) if cita.search(e or "")]
        if cob < a.min or sem_texto:
            linhas.append(f"{pid[-60:]:60} cobertura={cob:5.1%}  citam texto sem texto={sem_texto[:12]}")
    print("\n".join(linhas) or "nenhuma prova abaixo do limite")
    return 0


if __name__ == "__main__":
    sys.exit(main())
