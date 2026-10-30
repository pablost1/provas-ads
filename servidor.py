#!/usr/bin/env python
"""Passo 3 - servidor local: API JSON sobre out/provas.db + app web em web/.

    python servidor.py            # http://localhost:8000
    python servidor.py --porta 9000 --nao-abrir

Só biblioteca padrão. O gabarito só sai da API quando você responde (/api/tentativas) ou finaliza
um simulado (/api/simulado), então dá para praticar sem "spoiler".
"""
import argparse
import json
import mimetypes
import sqlite3
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

BASE = Path(__file__).parent
DB = BASE / "out" / "provas.db"
WEB = BASE / "web"
IMAGENS = BASE / "out" / "imagens"
PDFS = BASE / "data" / "pdfs"


def conectar():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    return con


def q_para_dict(con, r, com_gabarito=False):
    d = {
        "id": r["id"], "prova_id": r["prova_id"], "numero": r["numero"], "disciplina": r["disciplina"],
        "bloco": r["bloco"], "enunciado": r["enunciado"],
        "alternativas": {k: r[k.lower()] for k in "ABCDE" if r[k.lower()] is not None},
        "banca": r["banca"], "ano": r["ano"], "orgao": r["orgao"], "cargo": r["cargo"],
        "assuntos": [x[0] for x in con.execute(
            "SELECT a.nome FROM assuntos a JOIN questao_assunto qa ON qa.assunto_id=a.id WHERE qa.questao_id=?", (r["id"],))],
        "imagens": [x[0] for x in con.execute("SELECT arquivo FROM imagens WHERE questao_id=?", (r["id"],))],
        "anulada": bool(r["anulada"]),
    }
    if "texto_id" in r.keys() and r["texto_id"]:
        t = con.execute("SELECT conteudo, de_numero, ate_numero FROM textos WHERE id=?", (r["texto_id"],)).fetchone()
        if t:
            d["texto"] = {"id": r["texto_id"], "conteudo": t["conteudo"], "de": t["de_numero"], "ate": t["ate_numero"]}
    if com_gabarito:
        d["gabarito"] = r["gabarito"]
    return d


SELECT_Q = """SELECT q.*, p.banca, p.ano, p.orgao, p.cargo FROM questoes q JOIN provas p ON p.id=q.prova_id"""


def filtros_sql(qs):
    where, params = [], []
    def um(chave, coluna):
        v = qs.get(chave, [""])[0]
        if v:
            where.append(f"{coluna}=?")
            params.append(v)
    um("banca", "p.banca"); um("ano", "p.ano"); um("orgao", "p.orgao"); um("bloco", "q.bloco"); um("prova", "q.prova_id")
    um("disciplina", "q.disciplina")
    for a in qs.get("assunto", []):
        if a:
            where.append("EXISTS (SELECT 1 FROM questao_assunto qa JOIN assuntos a ON a.id=qa.assunto_id "
                         "WHERE qa.questao_id=q.id AND a.nome=?)")
            params.append(a)
    t = qs.get("q", [""])[0].strip()
    if t:
        where.append("(q.enunciado LIKE ? OR q.a LIKE ? OR q.b LIKE ? OR q.c LIKE ? OR q.d LIKE ? OR q.e LIKE ?)")
        params += [f"%{t}%"] * 6
    if qs.get("com_gabarito", [""])[0] == "1":
        where.append("(q.gabarito IS NOT NULL OR q.anulada=1)")
    if qs.get("com_imagem", [""])[0] == "1":
        where.append("EXISTS (SELECT 1 FROM imagens i WHERE i.questao_id=q.id)")
    return (" WHERE " + " AND ".join(where)) if where else "", params


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def json(self, obj, status=200):
        corpo = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(corpo)))
        self.end_headers()
        self.wfile.write(corpo)

    def arquivo(self, caminho: Path, inline=False):
        if not caminho.is_file():
            return self.json({"erro": "não encontrado"}, 404)
        dados = caminho.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mimetypes.guess_type(str(caminho))[0] or "application/octet-stream")
        self.send_header("Content-Length", str(len(dados)))
        if inline:
            self.send_header("Content-Disposition", "inline")
        self.end_headers()
        self.wfile.write(dados)

    def do_GET(self):
        u = urlparse(self.path)
        qs = parse_qs(u.query)
        rota = unquote(u.path)
        if rota.startswith("/api/"):
            con = conectar()
            try:
                return self.api_get(con, rota, qs)
            finally:
                con.close()
        if rota.startswith("/pdf/"):   # só o PDF da prova (o gabarito não é exposto)
            con = conectar()
            try:
                r = con.execute("SELECT arquivo_prova FROM provas WHERE id=?", (rota[len("/pdf/"):],)).fetchone()
            finally:
                con.close()
            if not r or not r["arquivo_prova"]:
                return self.json({"erro": "prova não encontrada"}, 404)
            alvo = (PDFS / rota[len("/pdf/"):] / r["arquivo_prova"]).resolve()
            if PDFS.resolve() in alvo.parents:
                return self.arquivo(alvo, inline=True)
            return self.json({"erro": "proibido"}, 403)
        if rota.startswith("/imagens/"):
            alvo = (IMAGENS / rota[len("/imagens/"):]).resolve()
            if IMAGENS.resolve() in alvo.parents:
                return self.arquivo(alvo)
            return self.json({"erro": "proibido"}, 403)
        alvo = (WEB / ("index.html" if rota == "/" else rota.lstrip("/"))).resolve()
        if WEB.resolve() in alvo.parents or alvo == WEB.resolve():
            return self.arquivo(alvo)
        return self.json({"erro": "não encontrado"}, 404)

    def api_get(self, con, rota, qs):
        if rota == "/api/meta":
            provas = [dict(r) for r in con.execute(
                """SELECT p.id, p.cargo, p.orgao, p.banca, p.ano, COUNT(q.id) AS n_questoes,
                          COALESCE(SUM(q.gabarito IS NOT NULL OR q.anulada=1), 0) AS n_gabarito
                   FROM provas p LEFT JOIN questoes q ON q.prova_id=p.id GROUP BY p.id ORDER BY p.ano DESC, p.orgao""")]
            ass = [dict(r) for r in con.execute(
                """SELECT a.nome, COUNT(*) AS n FROM assuntos a JOIN questao_assunto qa ON qa.assunto_id=a.id
                   GROUP BY a.id ORDER BY n DESC""")]
            col = lambda c: [r[0] for r in con.execute(f"SELECT DISTINCT {c} FROM provas ORDER BY {c}")]
            disc = [r[0] for r in con.execute("SELECT DISTINCT disciplina FROM questoes WHERE disciplina<>'' ORDER BY 1")]
            return self.json({"provas": provas, "assuntos": ass, "bancas": col("banca"), "anos": col("ano")[::-1],
                              "orgaos": col("orgao"), "disciplinas": disc, "blocos": ["específico", "geral"]})
        if rota == "/api/questoes":
            onde, params = filtros_sql(qs)
            limite = min(int(qs.get("limite", ["20"])[0]), 200)
            desloc = int(qs.get("deslocamento", ["0"])[0])
            seed = qs.get("embaralhar", [""])[0]
            ordem = (f"((q.rowid * 2654435761 + {int(seed)}) % 1000003)" if seed.isdigit()
                     else "p.ano DESC, q.prova_id, q.numero")
            total = con.execute(f"SELECT COUNT(*) FROM questoes q JOIN provas p ON p.id=q.prova_id{onde}", params).fetchone()[0]
            rows = con.execute(f"{SELECT_Q}{onde} ORDER BY {ordem} LIMIT ? OFFSET ?", params + [limite, desloc]).fetchall()
            return self.json({"total": total, "itens": [q_para_dict(con, r) for r in rows]})
        if rota.startswith("/api/prova/"):
            pid = rota[len("/api/prova/"):]
            p = con.execute("SELECT * FROM provas WHERE id=?", (pid,)).fetchone()
            if not p:
                return self.json({"erro": "prova não encontrada"}, 404)
            rows = con.execute(f"{SELECT_Q} WHERE q.prova_id=? ORDER BY q.numero", (pid,)).fetchall()
            return self.json({"prova": dict(p), "questoes": [q_para_dict(con, r) for r in rows]})
        if rota == "/api/stats":
            por_assunto = [dict(r) for r in con.execute(
                """SELECT a.nome AS assunto, COUNT(*) AS respondidas, SUM(t.correta) AS acertos
                   FROM tentativas t JOIN questao_assunto qa ON qa.questao_id=t.questao_id
                   JOIN assuntos a ON a.id=qa.assunto_id GROUP BY a.id ORDER BY respondidas DESC""")]
            simulados = [dict(r) for r in con.execute(
                """SELECT t.simulado_id, t.prova_id, MIN(t.ts) AS quando, COUNT(*) AS respondidas, SUM(t.correta) AS acertos,
                          p.orgao, p.ano, p.banca
                   FROM tentativas t JOIN provas p ON p.id=t.prova_id WHERE t.simulado_id LIKE 'sim-%'
                   GROUP BY t.simulado_id ORDER BY quando DESC LIMIT 30""")]
            tot = con.execute("SELECT COUNT(*), COALESCE(SUM(correta),0) FROM tentativas").fetchone()
            return self.json({"respondidas": tot[0], "acertos": tot[1], "por_assunto": por_assunto, "simulados": simulados})
        return self.json({"erro": "rota desconhecida"}, 404)

    def do_POST(self):
        tam = int(self.headers.get("Content-Length") or 0)
        try:
            corpo = json.loads(self.rfile.read(tam) or b"{}")
        except Exception:
            return self.json({"erro": "JSON inválido"}, 400)
        rota = urlparse(self.path).path
        con = conectar()
        try:
            if rota == "/api/tentativas":     # prática: uma questão por vez
                r = con.execute("SELECT gabarito, anulada FROM questoes WHERE id=?", (corpo.get("questao_id"),)).fetchone()
                if not r:
                    return self.json({"erro": "questão inexistente"}, 404)
                resp = (corpo.get("resposta") or "").upper()
                if r["gabarito"] is None and not r["anulada"]:   # prova sem gabarito: não dá para corrigir nem registrar
                    return self.json({"correta": None, "gabarito": None, "anulada": False, "sem_gabarito": True})
                correta = bool(r["anulada"]) or (resp == r["gabarito"])
                con.execute("INSERT INTO tentativas(simulado_id, prova_id, questao_id, resposta, correta) "
                            "SELECT ?, prova_id, id, ?, ? FROM questoes WHERE id=?",
                            ("pratica", resp, int(correta), corpo["questao_id"]))
                con.commit()
                return self.json({"correta": correta, "gabarito": r["gabarito"], "anulada": bool(r["anulada"])})
            if rota == "/api/simulado":       # finaliza simulado: {prova_id, respostas:{questao_id: letra}}
                pid = corpo.get("prova_id")
                respostas = corpo.get("respostas") or {}
                sid = "sim-" + uuid.uuid4().hex[:10]
                itens, acertos = {}, 0
                por_assunto = {}
                for r in con.execute("SELECT id, gabarito, anulada FROM questoes WHERE prova_id=? ORDER BY numero", (pid,)):
                    resp = (respostas.get(r["id"]) or "").upper()
                    if r["gabarito"] is None and not r["anulada"]:
                        itens[r["id"]] = {"resposta": resp, "gabarito": None, "correta": False, "anulada": False, "sem_gabarito": True}
                        continue
                    correta = bool(r["anulada"]) or (resp != "" and resp == r["gabarito"])
                    itens[r["id"]] = {"resposta": resp, "gabarito": r["gabarito"], "correta": correta, "anulada": bool(r["anulada"])}
                    acertos += correta
                    for (nome,) in con.execute("SELECT a.nome FROM assuntos a JOIN questao_assunto qa ON qa.assunto_id=a.id "
                                               "WHERE qa.questao_id=?", (r["id"],)):
                        d = por_assunto.setdefault(nome, {"total": 0, "acertos": 0})
                        d["total"] += 1
                        d["acertos"] += correta
                    if resp:
                        con.execute("INSERT INTO tentativas(simulado_id, prova_id, questao_id, resposta, correta) VALUES (?,?,?,?,?)",
                                    (sid, pid, r["id"], resp, int(correta)))
                con.commit()
                return self.json({"simulado_id": sid, "total": sum(1 for i in itens.values() if not i.get("sem_gabarito")),
                                  "sem_gabarito": sum(1 for i in itens.values() if i.get("sem_gabarito")), "acertos": acertos, "itens": itens,
                                  "por_assunto": por_assunto})
            return self.json({"erro": "rota desconhecida"}, 404)
        finally:
            con.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--porta", type=int, default=8000)
    ap.add_argument("--nao-abrir", action="store_true")
    a = ap.parse_args()
    if not DB.exists():
        raise SystemExit("out/provas.db não existe: rode primeiro python extrair_questoes.py")
    srv = ThreadingHTTPServer(("127.0.0.1", a.porta), H)
    url = f"http://localhost:{a.porta}"
    print(f"Servindo em {url}  (Ctrl+C para parar)")
    if not a.nao_abrir:
        webbrowser.open(url)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
