#!/usr/bin/env python
"""Gera a versão estática do app em docs/ (para o GitHub Pages).

Lê out/provas.db e escreve:
    docs/index.html          cópia de web/index.html (o mesmo front-end, em modo estático)
    docs/data/provas.json    metadados das provas + listas de filtros
    docs/data/questoes.json  todas as questões (com gabarito: a correção acontece no navegador)
    docs/imagens/<prova>/    figuras das questões
    docs/pdf/<prova>.pdf     PDF de cada prova (não inclui o gabarito)

Uso:  python gerar_site.py
Depois, publique a pasta docs/ (Settings > Pages > Deploy from a branch > /docs).
"""
import json
import shutil
import sqlite3
from pathlib import Path

BASE = Path(__file__).parent
DB = BASE / "out" / "provas.db"
DOCS = BASE / "docs"


def main():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    if DOCS.exists():
        shutil.rmtree(DOCS)
    (DOCS / "data").mkdir(parents=True)

    provas = [dict(r) for r in con.execute(
        """SELECT p.id, p.cargo, p.orgao, p.banca, p.ano, p.arquivo_prova, COUNT(q.id) AS n_questoes,
                  COALESCE(SUM(q.gabarito IS NOT NULL OR q.anulada=1), 0) AS n_gabarito
           FROM provas p LEFT JOIN questoes q ON q.prova_id=p.id GROUP BY p.id ORDER BY p.ano DESC, p.orgao""")]
    col = lambda c: [r[0] for r in con.execute(f"SELECT DISTINCT {c} FROM provas ORDER BY {c}")]
    meta = {
        "provas": [{k: v for k, v in p.items() if k != "arquivo_prova"} for p in provas],
        "assuntos": [dict(r) for r in con.execute(
            "SELECT a.nome, COUNT(*) AS n FROM assuntos a JOIN questao_assunto qa ON qa.assunto_id=a.id GROUP BY a.id ORDER BY n DESC")],
        "bancas": col("banca"), "anos": col("ano")[::-1], "orgaos": col("orgao"),
        "disciplinas": [r[0] for r in con.execute("SELECT DISTINCT disciplina FROM questoes WHERE disciplina<>'' ORDER BY 1")],
        "blocos": ["específico", "geral"],
    }

    textos = {r["id"]: dict(r) for r in con.execute("SELECT id, conteudo, de_numero, ate_numero FROM textos")}
    assuntos, imagens = {}, {}
    for r in con.execute("SELECT qa.questao_id, a.nome FROM questao_assunto qa JOIN assuntos a ON a.id=qa.assunto_id"):
        assuntos.setdefault(r[0], []).append(r[1])
    for r in con.execute("SELECT questao_id, arquivo FROM imagens"):
        imagens.setdefault(r[0], []).append(r[1])

    questoes = []
    for r in con.execute("""SELECT q.*, p.banca, p.ano, p.orgao, p.cargo FROM questoes q JOIN provas p ON p.id=q.prova_id
                            ORDER BY p.ano DESC, q.prova_id, q.numero"""):
        d = {
            "id": r["id"], "prova_id": r["prova_id"], "numero": r["numero"], "disciplina": r["disciplina"],
            "bloco": r["bloco"], "enunciado": r["enunciado"],
            "alternativas": {k: r[k.lower()] for k in "ABCDE" if r[k.lower()] is not None},
            "banca": r["banca"], "ano": r["ano"], "orgao": r["orgao"], "cargo": r["cargo"],
            "assuntos": assuntos.get(r["id"], []), "imagens": imagens.get(r["id"], []),
            "anulada": bool(r["anulada"]), "gabarito": r["gabarito"],
        }
        t = textos.get(r["texto_id"]) if r["texto_id"] else None
        if t:
            d["texto"] = {"id": t["id"], "conteudo": t["conteudo"], "de": t["de_numero"], "ate": t["ate_numero"]}
        questoes.append(d)

    (DOCS / "data" / "provas.json").write_text(json.dumps(meta, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    (DOCS / "data" / "questoes.json").write_text(json.dumps(questoes, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    n_img = 0
    origem_img = BASE / "out" / "imagens"
    for pid in {q["prova_id"] for q in questoes}:
        pasta = origem_img / pid
        if pasta.is_dir():
            shutil.copytree(pasta, DOCS / "imagens" / pid)
            n_img += len(list(pasta.iterdir()))

    n_pdf = 0
    (DOCS / "pdf").mkdir()
    for p in provas:
        f = BASE / "data" / "pdfs" / p["id"] / (p["arquivo_prova"] or "")
        if p["arquivo_prova"] and f.is_file():
            shutil.copyfile(f, DOCS / "pdf" / f"{p['id']}.pdf")
            n_pdf += 1

    shutil.copyfile(BASE / "web" / "index.html", DOCS / "index.html")
    (DOCS / ".nojekyll").write_text("", encoding="utf-8")
    tam = sum(f.stat().st_size for f in DOCS.rglob("*") if f.is_file()) / 1e6
    print(f"docs/: {len(provas)} provas, {len(questoes)} questões, {n_img} imagens, {n_pdf} PDFs ({tam:.0f} MB)")


if __name__ == "__main__":
    main()
