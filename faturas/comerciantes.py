# -*- coding: utf-8 -*-
"""
Comerciantes SINTETICOS para as faturas do laboratorio.

Nenhum nome aqui veio de uma fatura real: todos sao inventados. O que foi
preservado de proposito e o FORMATO com que o nome chega na fatura, porque
e justamente ele que torna a categorizacao difficil:

  - prefixos de intermediador de pagamento: "MP *", "IFD*", "DL*", "PAYGO*"
  - truncamento no meio da palavra: "DL*GOOGLE YouTubePre"
  - cidade colada na descricao: "AUTO POSTO ARACAJU"
  - nome de pessoa fisica (venda via maquininha): "MP *J R DA SILVA"

Esse ruido e o motivo de a busca por palavra-chave falhar e a busca
vetorial funcionar. Ver docs/03-busca-vetorial.md.
"""

# (descricao_na_fatura, categoria_verdadeira, cidade)
# A coluna de categoria serve para dois fins: montar o catalogo do Qdrant
# (apenas parte dela) e permitir medir o acerto do pipeline.
COMERCIANTES = [
    # --- Transporte ---
    ("DL*UBERRIDES",            "Transporte",   "Sao Paulo"),
    ("99POP VIAGEM",            "Transporte",   "Sao Paulo"),
    ("ZUL + CARTAO 7KQ2P",      "Transporte",   "Sao Paulo"),
    ("ESTAC PATIO NORTE",       "Transporte",   "Sao Paulo"),
    ("AUTO POSTO ARACAJU",      "Transporte",   "Aracaju"),
    ("REDE PARX SHOPPING",      "Transporte",   "Sao Paulo"),
    ("POSTO IPIRANGA CENTRO",   "Transporte",   "Campinas"),

    # --- Alimentacao fora de casa / delivery ---
    ("IFD*CANTINA DO ZECA",     "Alimentacao",  "Sao Paulo"),
    ("IFD*IFOOD CLUB",          "Alimentacao",  "Sao Paulo"),
    ("PAYGO*BAR DO TONHO",      "Alimentacao",  "Santos"),
    ("MP *PASTELARIA LUA",      "Alimentacao",  "Sao Paulo"),
    ("NORTH BAR E GRILL",       "Alimentacao",  "Sao Paulo"),
    ("PANELA DE BARRO REST",    "Alimentacao",  "Sao Paulo"),
    ("PADARIA AURORA SUL",      "Alimentacao",  "Sao Paulo"),
    ("OXXO ESTACAO LESTE",      "Alimentacao",  "Sao Paulo"),

    # --- Mercado ---
    ("SUPERMERCADO BOA VEZ",    "Mercado",      "Sao Paulo"),
    ("EMPORIO SAO JUDAS",       "Mercado",      "Sao Paulo"),
    ("HORTIFRUTI VILA NOVA",    "Mercado",      "Sao Paulo"),
    ("ACOUGUE DOIS IRMAOS",     "Mercado",      "Guarulhos"),
    ("LATICINIOS SERRA AZUL",   "Mercado",      "Pocos de Caldas"),

    # --- Saude ---
    ("DROGARIA SAO JUDAS 0412", "Saude",        "Sao Paulo"),
    ("FARMA MAIS CENTRO",       "Saude",        "Sao Paulo"),
    ("VMT*NUTRIFORMULA",        "Saude",        "Sao Paulo"),
    ("CLINICA VIDA PLENA",      "Saude",        "Sao Paulo"),
    ("LAB ANALISES CENTRO",      "Saude",       "Sao Paulo"),

    # --- Assinaturas e digital ---
    ("NETFLIX ENTRETENIMENTO",  "Assinaturas",  "Barueri"),
    ("DL*GOOGLE YouTubePre",    "Assinaturas",  "Sao Paulo"),
    ("Microsoft*Store",         "Assinaturas",  "Sao Paulo"),
    ("APPLE COM/BILL",          "Assinaturas",  "Sao Paulo"),
    ("AMAZON MUSIC BR",         "Assinaturas",  "Sao Paulo"),
    ("EBN*HOSTINGER WEB",       "Assinaturas",  "Curitiba"),
    ("GOOGLE ONE ARMAZENAM",    "Assinaturas",  "Sao Paulo"),
    ("SPOTIFY BR PREMIUM",      "Assinaturas",  "Sao Paulo"),

    # --- Compras / e-commerce ---
    ("AMAZON BR *AMAZON BR",    "Compras",      "Sao Paulo"),
    ("MERCADOLIVRE*MERCADOL",   "Compras",      "Osasco"),
    ("SHOPEE *LOJA BRILHO",     "Compras",      "Sao Paulo"),
    ("MAGALU *MAGAZINELUIZ",    "Compras",      "Franca"),
    ("DAISO UTILIDADES BR",     "Compras",      "Sao Paulo"),
    ("KALUNGA PAPELARIA 88",    "Compras",      "Sao Paulo"),

    # --- Educacao ---
    ("COLEGIO MONTE VERDE",     "Educacao",     "Sao Paulo"),
    ("ASA*ACADEMIA PULSE",      "Educacao",     "Joinville"),
    ("UDEMY BRASIL CURSOS",     "Educacao",     "Sao Paulo"),

    # --- Viagem ---
    ("GOL LINHAS AEREAS",       "Viagem",       "Sao Paulo"),
    ("AF INTERNET PASSAGEM",    "Viagem",       "Sao Paulo"),
    ("SG CAR RENTAL",           "Viagem",       "Sao Paulo"),
    ("HOTEL MIRANTE SUL",       "Viagem",       "Florianopolis"),
    ("ALLIANZ TRAVEL SEG",      "Viagem",       "Sao Bernardo"),

    # --- Casa e servicos ---
    ("CONTA VIVO FIXO",         "Casa",         "Sao Paulo"),
    ("ENEL SP ENERGIA",         "Casa",         "Sao Paulo"),
    ("PBADMINISTRADORA COND",   "Casa",         "Sao Paulo"),

    # --- Pets ---
    ("ANIMALIA PET SHOP",       "Pets",         "Sao Paulo"),
    ("VET AMIGO FIEL",          "Pets",         "Sao Paulo"),

    # --- Pessoa fisica via maquininha (o caso mais dificil) ---
    ("MP *J R DA SILVA",        "Outros",       "Sao Paulo"),
    ("PAG*M OLIVEIRA ME",       "Outros",       "Sao Paulo"),
    ("PROPIG *A F DE SOUZA",    "Outros",       "Sao Paulo"),
]

CATEGORIAS = sorted({c for _, c, _ in COMERCIANTES})

# Comerciantes DELIBERADAMENTE fora do catalogo do Qdrant. Eles caem abaixo
# do limiar de similaridade e por isso sao os unicos que chegam ao LLM.
# Sem esta lista o modulo de IA nunca seria exercitado no lab.
FORA_DO_CATALOGO = {
    "PROPIG *A F DE SOUZA",
    "OXXO ESTACAO LESTE",
    "VMT*NUTRIFORMULA",
    "DAISO UTILIDADES BR",
    "LATICINIOS SERRA AZUL",
    "PBADMINISTRADORA COND",
    "ASA*ACADEMIA PULSE",
    "HOTEL MIRANTE SUL",
    "AUTO POSTO ARACAJU",
    "MP *J R DA SILVA",
}
