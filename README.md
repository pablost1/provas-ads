# Provas de Analista de Sistemas (PCI Concursos)

Pipeline em 3 passos. Tudo roda local, só o passo 1 acessa a internet.

## 1. Baixar provas + gabaritos
    abrir_brave.bat                                   # Brave normal com porta de depuração (deixe aberto)
    python baixar_provas.py https://www.pciconcursos.com.br/provas/analista-de-sistemas --cdp http://127.0.0.1:9222 --limite 20
- Lista as 30 páginas (1.456 provas) em `data/provas.json` e baixa para `data/pdfs/<id>/` (prova + gabarito + `arquivos.json`).
- Cada prova tem captcha Cloudflare Turnstile: você resolve na janela do Brave; o script espera e segue.
- Retoma de onde parou (pula provas já baixadas). Use `--limite N` para baixar em lotes.

## 2. Extrair, correlacionar e organizar
    python extrair_questoes.py            # tudo (reprocessa o que já está em data/pdfs)
    python extrair_questoes.py --prova <id>
    python extrair_questoes.py --debug <id>   # mostra o texto "achatado" de uma prova
Saídas em `out/`:
- `provas.db` (SQLite): `provas`, `questoes`, `imagens`, `assuntos`, `questao_assunto`, `tentativas`
- `questoes.json` / `questoes.jsonl`: uma linha por questão (prova, banca, ano, órgão, enunciado, alternativas, gabarito, assuntos, imagens)
- `imagens/<prova>/*.png`: figuras recortadas
- `relatorio.txt`: por prova, nº de questões, gabarito casado, números faltando (conferência)
Assuntos vêm de palavras-chave em `assuntos.py` (edite à vontade e rode de novo).

Exemplo de consulta por assunto:
    SELECT q.id, q.enunciado, q.gabarito FROM questoes q
    JOIN questao_assunto qa ON qa.questao_id=q.id JOIN assuntos a ON a.id=qa.assunto_id
    WHERE a.nome='Banco de Dados';

## 3. Visualizar, filtrar e fazer simulado
    python servidor.py        # http://localhost:8000
- **Questões**: filtros (assunto, banca, ano, órgão, bloco, prova, busca, só com figura), responde e vê certo/errado pelo gabarito.
- **Simulado**: escolhe uma prova, responde tudo (atalhos A–E / setas), finaliza e vê nota, acertos por assunto e revisão.
- **Desempenho**: histórico por assunto e simulados recentes (tabela `tentativas`).
A API (`/api/questoes?assunto=...&banca=...`) também serve para outros sistemas.

## Gabaritos

- `extrair_questoes.py` lê cada PDF de gabarito com vários leitores (`parse_gabarito`, `parse_por_pagina`, e em `gabaritos.py`: `parse_grade_colunas` para grades de cargos lado a lado e `parse_extra` para formatos próprios) e fica com a seção que casa com o cargo e cobre mais questões.
- Quando há mais de um gabarito (`definitivo`, `retificado`, `preliminar`), o definitivo tem prioridade.
- Se nada casar, a prova fica "sem gabarito". Para informar um à mão, crie `data/gabaritos_manuais/<id-da-prova>.json` com `{"1":"A","2":"C","3":"X"}` (`X` = anulada) e rode `python extrair_questoes.py`.

## Textos de apoio e auditoria

- Textos que várias questões compartilham (ex.: o texto "Bolsa-Floresta" das questões 1 a 14 do TJ-RO) ficam na tabela `textos` e em `questoes.texto_id`; o app mostra o texto acima do enunciado e `questoes.json` traz o campo `texto`. A numeração de linhas da margem (5, 10, 15...) é removida.
- `python auditoria.py` compara o texto de cada PDF com o que foi guardado e lista provas com cobertura baixa e questões que citam um texto que não foi encontrado.
- Antes de reprocessar, o backup anterior está em `out_backup_2026-10-29/`.

## Dados por prova

`out/provas.json` e `out/provas.csv` trazem uma linha por prova: `id`, `orgao`, `banca`, `ano`, `cargo`, `url` (página do PCI), arquivos de prova e gabarito usados, e contagens (`n_questoes`, `n_com_gabarito`, `n_anuladas`, `n_textos_apoio`, `n_com_imagem`). Em `questoes.json`, `prova_id` liga cada questão à sua prova, e `prova_titulo` repete "órgão · banca · ano · cargo". O `relatorio.txt` também começa cada linha com esses dados.

## Site online (GitHub Pages)

O GitHub Pages só serve arquivos estáticos, então não roda o `servidor.py`. Por isso existe uma versão estática do mesmo app:

1. `python gerar_site.py` cria a pasta `docs/` (front-end, `data/*.json`, imagens e PDFs das provas).
2. No GitHub: *Settings → Pages → Deploy from a branch* → escolha a branch e a pasta `/docs`.

Nessa versão a correção acontece no navegador e o progresso (respostas e simulados) fica salvo no `localStorage` do próprio navegador. Rode `gerar_site.py` de novo sempre que reprocessar as provas.
