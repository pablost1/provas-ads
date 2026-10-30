#!/usr/bin/env python
"""Passo 1 - lista e baixa provas + gabaritos de uma seção do pciconcursos.

Uso:
    python baixar_provas.py https://www.pciconcursos.com.br/provas/analista-de-sistemas
    python baixar_provas.py <url> --limite 5          # só as 5 primeiras (teste)
    python baixar_provas.py <url> --so-listar         # só gera data/provas.json

O download de cada prova é protegido por captcha (Cloudflare Turnstile). O script
abre um navegador REAL (visível); se o captcha pedir um clique, resolva-o na janela
e o script segue sozinho. Nada é burlado: é o mesmo fluxo de um usuário.
Retoma de onde parou (provas já baixadas são puladas).
"""
import argparse
import json
import re
import sys
import time
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130 Safari/537.36"
DATA = Path(__file__).parent / "data"
PDFS = DATA / "pdfs"


def listar(url_secao: str, pausa: float = 0.8) -> list[dict]:
    """Percorre todas as páginas da seção e devolve as provas listadas."""
    base = url_secao.rstrip("/")
    provas, pagina, ultima = [], 1, 1
    while pagina <= ultima:
        r = requests.get(base if pagina == 1 else f"{base}/{pagina}", headers={"User-Agent": UA}, timeout=30)
        r.raise_for_status()
        r.encoding = "utf-8"
        s = BeautifulSoup(r.text, "lxml")
        for a in s.select(".pagination a.page-link[href]"):
            m = re.search(r"/(\d+)$", a["href"])
            if m:
                ultima = max(ultima, int(m.group(1)))
        for tr in s.select("#lista_provas tbody tr"):
            a = tr.select_one("a.prova_download")
            tds = tr.find_all("td")
            if not a or len(tds) < 4:
                continue
            link = urljoin(base, a["href"])
            provas.append({
                "id": link.rstrip("/").rsplit("/", 1)[-1],
                "url": link,
                "cargo": a.get_text(" ", strip=True),
                "ano": tds[1].get_text(strip=True),
                "orgao": tds[2].get_text(strip=True),
                "banca": tds[3].get_text(strip=True),
            })
        print(f"  listagem: página {pagina}/{ultima} ({len(provas)} provas)", flush=True)
        pagina += 1
        time.sleep(pausa)
    return provas


def eh_gabarito(nome: str) -> bool:
    return bool(re.search(r"gab|resposta|respostas", nome, re.I))


def ja_baixada(prova: dict) -> bool:
    """True se arquivos.json existe e todos os PDFs listados nele continuam em disco."""
    pasta = PDFS / prova["id"]
    try:
        info = json.loads((pasta / "arquivos.json").read_text(encoding="utf-8"))
        return bool(info["arquivos"]) and all((pasta / a["arquivo"]).exists() for a in info["arquivos"])
    except Exception:
        return False


def baixar_prova(ctx, prova: dict, espera_captcha: int) -> bool:
    pasta = PDFS / prova["id"]
    meta = pasta / "arquivos.json"
    if ja_baixada(prova):
        return True
    page = ctx.new_page()
    capturado = {}

    def on_resp(resp):
        if resp.url.endswith("/provas/link") and resp.request.method == "POST":
            try:
                capturado["json"] = resp.json()
            except Exception:
                pass

    page.on("response", on_resp)
    try:
        page.goto(prova["url"], wait_until="domcontentloaded", timeout=60000)
        fim = time.time() + espera_captcha
        avisou = False
        while time.time() < fim:
            j = capturado.get("json")
            if j and j.get("ok") and j.get("arquivos"):
                break
            # o captcha pode estar pronto mas sem token ainda: clicar num link tenta liberar
            if time.time() > fim - espera_captcha + 6 and not avisou:
                print("    >> se aparecer o captcha na janela, resolva-o (clique na caixa).", flush=True)
                avisou = True
            page.wait_for_timeout(500)
        j = capturado.get("json")
        if not (j and j.get("ok") and j.get("arquivos")):
            print("    ! captcha não resolvido a tempo / sem arquivos; pulando.", flush=True)
            return False
        pasta.mkdir(parents=True, exist_ok=True)
        salvos = []
        for arq in j["arquivos"]:
            nome = re.sub(r"[^\w.\-]+", "_", arq["arquivo"])
            r = ctx.request.get(arq["baixar"], headers={"Referer": prova["url"]}, timeout=120000)
            if not r.ok or not r.body()[:5].startswith(b"%PDF"):
                print(f"    ! falha em {nome} (HTTP {r.status})", flush=True)
                continue
            (pasta / nome).write_bytes(r.body())
            salvos.append({"arquivo": nome, "tipo": "gabarito" if eh_gabarito(nome) else "prova"})
        if not salvos:
            return False
        meta.write_text(json.dumps({**prova, "arquivos": salvos}, ensure_ascii=False, indent=2), encoding="utf-8")
        return True
    finally:
        page.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("url")
    ap.add_argument("--limite", type=int)
    ap.add_argument("--so-listar", action="store_true")
    ap.add_argument("--cdp", help="ex.: http://127.0.0.1:9222 (Brave aberto por abrir_brave.bat)")
    ap.add_argument("--brave-path", default=r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe")
    ap.add_argument("--espera-captcha", type=int, default=90, help="segundos esperando o captcha por prova")
    a = ap.parse_args()

    DATA.mkdir(exist_ok=True)
    lista_f = DATA / "provas.json"
    if lista_f.exists() and not a.so_listar:
        provas = json.loads(lista_f.read_text(encoding="utf-8"))
        print(f"Usando listagem existente: {len(provas)} provas ({lista_f})")
    else:
        print("Listando provas...")
        provas = listar(a.url)
        lista_f.write_text(json.dumps(provas, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"{len(provas)} provas -> {lista_f}")
    if a.so_listar:
        return
    pendentes = [p for p in provas if not ja_baixada(p)]
    print(f"{len(provas) - len(pendentes)} já baixadas (puladas, sem abrir página nem captcha); {len(pendentes)} pendentes.")
    provas = pendentes[: a.limite] if a.limite else pendentes
    if not provas:
        print("Nada a baixar.")
        return

    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        if a.cdp:
            # Brave aberto normalmente (abrir_brave.bat) e só conectado: sem flags de automação
            browser = p.chromium.connect_over_cdp(a.cdp)
            ctx = browser.contexts[0]
        else:
            opts = {}
            if Path(a.brave_path).exists():
                opts["executable_path"] = a.brave_path
            ctx = p.chromium.launch_persistent_context(
                str(DATA / "browser-profile"), headless=False, locale="pt-BR",
                viewport={"width": 1100, "height": 800}, **opts,
            )
        ok = 0
        for i, prova in enumerate(provas, 1):
            print(f"[{i}/{len(provas)}] {prova['cargo']} - {prova['orgao']} {prova['ano']} ({prova['banca']})", flush=True)
            try:
                ok += bool(baixar_prova(ctx, prova, a.espera_captcha))
            except KeyboardInterrupt:
                break
            except Exception as e:
                print(f"    ! erro: {e}", flush=True)
            time.sleep(1.5)
        if not a.cdp:
            ctx.close()
    print(f"Concluído: {ok} provas baixadas/presentes.")


if __name__ == "__main__":
    sys.exit(main())
