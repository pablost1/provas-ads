"""Classificação de assuntos por palavras-chave. Edite à vontade: (assunto, [palavras]).

Casamento sem acento e sem diferenciar maiúsculas. Palavras terminadas em espaço ou
cercadas por \\b exigem a palavra inteira; as demais casam como trecho.
"""
import re
import unicodedata

ASSUNTOS = [
    ("Banco de Dados", ["banco de dados", "sgbd", "sql", "oracle", "mysql", "postgres", "normalizacao", "relacional",
                        "nosql", "mongodb", "data warehouse", "olap", "etl", "entidade-relacionamento", "chave primaria",
                        "chave estrangeira", "stored procedure", "transacao", "acid", "mineracao de dados", "datawarehouse",
                        "\\bjoin\\b", "\\bselect\\b", "\\bindice", "business intelligence", "\\bbi\\b"]),
    ("Engenharia de Software", ["engenharia de software", "requisito", "\\buml\\b", "diagrama de", "caso de uso", "scrum", "agil",
                                "extreme programming", "kanban", "modelo cascata", "\\brup\\b", "\\btdd\\b", "teste de software",
                                "testes de software", "teste unitario", "testes unitarios", "cmmi", "mps.br", "qualidade de software",
                                "refatora", "padroes de projeto", "design pattern", "ciclo de vida", "pontos de funcao",
                                "analise de pontos", "metodologia de desenvolvimento", "integracao continua", "versionamento", "\\bgit\\b",
                                "de software", "metrica", "halstead", "mccall", "fan-out", "dsdm", "desenvolvimento de sistemas",
                                "teste de regressao", "testes de regressao", "analista de testes"]),
    ("Programação e Linguagens", ["\\bjava\\b", "python", "c#", "javascript", "typescript", "\\bphp\\b", "orientacao a objetos",
                                  "orientado a objetos", "heranca", "polimorfismo", "algoritmo", "estrutura de dados", "recursao",
                                  "spring", "hibernate", "framework", "\\bhtml", "\\bcss\\b", "bootstrap", "rest", "json", "\\bxml\\b",
                                  "angular", "react", "\\.net", "node", "linguagem de programacao", "compilador", "interpretador",
                                  "ponteiro", "\\bclasse\\b", "\\bmetodo\\b", "\\bjvm\\b", "\\bapi\\b", "\\bjsp\\b", "subrotina",
                                  "passagem de parametro", "\\barray", "e-mag", "acessibilidade", "bloco de codigo", "objeto implicito"]),
    ("Redes de Computadores", ["\\btcp", "\\budp\\b", "\\bdns\\b", "\\bdhcp\\b", "\\bosi\\b", "roteamento", "\\bvlan", "switch", "roteador",
                               "ethernet", "wi-fi", "wifi", "\\bhttp", "\\bftp\\b", "\\bsmtp\\b", "voip", "ipv4", "ipv6", "\\bip\\b",
                               "redes de computadores", "\\blan\\b", "\\bwan\\b", "\\bnat\\b", "camada de", "topologia", "gateway",
                               "banda larga", "protocolo", "sub-rede", "mascara de rede", "uma rede", "comutacao", "circuito dedicado"]),
    ("Sistemas Operacionais", ["sistema operacional", "sistemas operacionais", "linux", "unix", "\\bwindows", "escalonamento",
                               "memoria virtual", "deadlock", "kernel", "sistema de arquivos", "\\bbash\\b", "\\bshell\\b",
                               "threads", "\\bthread\\b", "processos", "paginacao"]),
    ("Segurança da Informação", ["seguranca da informacao", "criptografia", "assinatura digital", "certificado digital", "malware",
                                 "virus", "ataque", "firewall", "\\bids\\b", "\\bips\\b", "\\bvpn\\b", "iso 27", "iso/iec 27", "lgpd",
                                 "autenticacao", "confidencialidade", "phishing", "\\bpki\\b", "\\bhash\\b", "ransomware", "backdoor",
                                 "vulnerabilidade", "politica de seguranca", "criptografico", "\\bssl\\b", "\\btls\\b"]),
    ("Governança e Gestão de TI", ["\\bitil", "\\bcobit", "pmbok", "governanca", "gerenciamento de projeto", "gestao de projeto",
                                   "\\bbpm", "gerenciamento de servico", "gestao de servico", "\\bsla\\b", "gestao de riscos",
                                   "gerenciamento de risco", "planejamento estrategico", "balanced scorecard", "\\bswot\\b",
                                   "iso 20000", "iso 38500", "\\bpmi\\b", "escritorio de projetos", "\\bpmo\\b", "eap", "cronograma",
                                   "gestao de processos", "contratacao de ti", "\\bsisp\\b", "in 04", "ti verde", "auditoria", "processo de negocio"]),
    ("Arquitetura e Infraestrutura", ["virtualizacao", "nuvem", "\\bcloud\\b", "docker", "kubernetes", "microsservico", "microservico",
                                      "\\bsoa\\b", "web service", "arquitetura de computadores", "processador", "\\braid\\b", "storage",
                                      "backup", "\\bcache\\b", "data center", "datacenter", "devops", "ci/cd", "balanceamento de carga",
                                      "alta disponibilidade", "hipervisor", "\\bsan\\b", "\\bnas\\b", "memoria ram", "\\bcpu\\b", "hexadecimal",
                                      "binario", "disco rigido", "entrada/saida", "paralelismo", "instrucoes", "microsserv", "monolit",
                                      "orientada a servicos"]),
    ("Dados e Inteligência Artificial", ["machine learning", "aprendizado de maquina", "big data", "inteligencia artificial", "rede neural",
                                         "redes neurais", "hadoop", "\\bspark\\b", "ciencia de dados", "ia generativa", "deep learning",
                                         "\\bllm\\b", "classificacao supervisionada"]),
    ("Língua Portuguesa", ["gramatica", "concordancia", "regencia", "\\bcrase\\b", "pontuacao", "sinonimo", "vocabulo", "pronome",
                           "substantivo", "coesao", "coerencia", "interpretacao de texto", "segmento", "paragrafo", "oracao",
                           "adjetivo", "ortografia", "figura de linguagem", "o texto", "do texto", "no texto", "segundo o texto", "o autor", "trecho", "conjuncao",
                           "locucao", "sujeito", "predicado"]),
    ("Raciocínio Lógico e Matemática", ["probabilidade", "proposicao", "equacao", "porcentagem", "sequencia", "tabela verdade",
                                        "conjunto", "razao", "proporcional", "logica", "raiz", "juros", "triangulo", "area de",
                                        "media aritmetica", "combinatoria", "anagrama"]),
    ("Informática Básica", ["microsoft word", "microsoft excel", "\\bexcel", "libreoffice", "\\bcalc\\b", "\\bwriter\\b", "navegador",
                            "internet explorer", "google chrome", "mozilla", "pacote office", "planilha", "correio eletronico",
                            "windows explorer"]),
    ("Legislação e Ética", ["\\blei\\b", "constituicao", "estatuto", "codigo de etica", "direito administrativo", "licitacao",
                            "8.666", "14.133", "improbidade", "servidor publico", "decreto", "regimento", "\\bart\\.", "lei n",
                            "marco civil", "administracao publica", "sindicancia", "processo administrativo", "servidora", "servidor",
                            "defensoria", "ministerio publico", "fiscalizacao", "legalidade", "deliberacao"]),
    ("Inglês", ["\\bthe\\b", "\\bwhich\\b", "\\bwhat\\b", "\\bthat\\b", "according to the text", "\\bis\\b the"]),
]

# Cabeçalho de disciplina da prova -> assunto (sobrepõe as palavras-chave)
DISCIPLINAS = [
    (r"portugu", "Língua Portuguesa"),
    (r"raciocinio|matematica", "Raciocínio Lógico e Matemática"),
    (r"ingl", "Inglês"),
    (r"legisla|etica|direito|administra", "Legislação e Ética"),
    (r"atualidades|conhecimentos gerais|conhecimentos basicos", "Conhecimentos Gerais"),
]

# Assuntos considerados "específicos" da área de TI (os demais viram bloco "geral")
TI = {"Outros (TI)", "Banco de Dados", "Engenharia de Software", "Programação e Linguagens", "Redes de Computadores",
      "Sistemas Operacionais", "Segurança da Informação", "Governança e Gestão de TI",
      "Arquitetura e Infraestrutura", "Dados e Inteligência Artificial"}


def sem_acento(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s.lower()) if unicodedata.category(c) != "Mn")


_COMPILADOS = [(nome, [re.compile(p if p.startswith("\\") or "\\" in p else re.escape(p)) for p in palavras])
               for nome, palavras in ASSUNTOS]


def classificar(texto: str, disciplina: str = "", max_assuntos: int = 2) -> list[str]:
    t = sem_acento(texto)
    pontos = []
    for nome, pats in _COMPILADOS:
        n = sum(1 for p in pats if p.search(t))
        if n:
            pontos.append((n, nome))
    pontos.sort(key=lambda x: -x[0])
    corte = max(1, (pontos[0][0] + 1) // 2) if pontos else 1   # só assuntos com pontuação perto da maior
    achados = [nome for n, nome in pontos if n >= corte][:max_assuntos]
    d = sem_acento(disciplina)
    for pat, nome in DISCIPLINAS:
        if d and re.search(pat, d):
            if nome == "Conhecimentos Gerais":
                return achados or [nome]
            return [nome]
    if achados:
        return achados
    return ["Outros (TI)"] if "especific" in d else ["Outros"]


def bloco(assuntos: list[str]) -> str:
    return "específico" if set(assuntos) & TI else "geral"
