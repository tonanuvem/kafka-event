# -*- coding: utf-8 -*-
"""
consumidor_categoriza_compras : busca vetorial primeiro, IA so no resto.

O PROBLEMA. Na fatura o estabelecimento chega deformado pela maquininha:
"IFD*CANTINA DO ZECA", "DL*GOOGLE YouTubePre", "MP *J R DA SILVA". Um
LIKE '%uber%' resolve um caso e falha nos outros mil, e uma tabela de
regras fica eternamente incompleta.

A SOLUCAO EM DUAS CAMADAS, que e como se faz na industria:

  1) BUSCA VETORIAL (barata, ~5 ms)
     A descricao vira um vetor de 384 numeros. Procuramos no catalogo os
     vizinhos mais proximos por distancia de cosseno. Se o mais proximo
     estiver perto o bastante (score >= LIMIAR_CATALOGO), herdamos a
     categoria dele e o LLM NEM E CHAMADO.

  2) LLM LOCAL (caro, ~10-20 s por lote em CPU)
     So o que sobrou. E mesmo aqui a busca vetorial ajuda: os 3 vizinhos
     mais proximos vao no prompt como exemplos. Isso e RAG -- recuperar
     contexto relevante para melhorar a resposta do modelo.

Em numeros do lab: ~70% das compras saem na camada 1, instantaneas.
Sem esse roteamento, uma fatura de 40 linhas levaria ~10 minutos.
"""
import json
import os
import time
import uuid
from datetime import datetime, timezone

import httpx
from qdrant_client import models

import re
import unicodedata

import comum

registro = comum.log("categoriza")

# Prefixo de intermediador de pagamento: "IFD*", "MP *", "PAYGO*", "DL*".
# Nao diz nada sobre o que foi comprado e so atrapalha o embedding.
PREFIXO_ADQUIRENTE = re.compile(r"^[A-Z0-9]{2,8}\s?\*\s?")
PARCELA = re.compile(r"\bPARC\s+\d{2}/\d{2}\b", re.I)


def sem_acento(texto):
    """'Educação' -> 'Educacao'.

    O modelo responde em portugues COM acento, e as categorias do lab
    sao escritas sem. Sem esta normalizacao, uma resposta correta como
    'Educação' seria recusada e viraria 'Outros'.
    """
    return "".join(
        c for c in unicodedata.normalize("NFD", texto)
        if unicodedata.category(c) != "Mn"
    )


def normalizar(descricao):
    """Limpa o ruido da maquininha antes de virar vetor.

    O embedding trabalha com significado, e "IFD*" nao tem nenhum. Tirar
    esses pedacos melhora a similaridade sem custo nenhum.
    """
    texto = PREFIXO_ADQUIRENTE.sub("", descricao)
    texto = PARCELA.sub("", texto)
    texto = re.sub(r"\s{2,}", " ", texto).strip()
    return texto or descricao

GRUPO = os.environ.get("GRUPO", "grupo-categoriza")
LIMIAR = float(os.environ.get("LIMIAR_CATALOGO", "0.80"))
TAMANHO_LOTE = int(os.environ.get("LOTE_IA", "5"))
OLLAMA = os.environ.get("OLLAMA_URL", "http://ollama:11434")
MODELO = os.environ.get("OLLAMA_MODELO", "gemma:2b")

with open("comerciantes.json", encoding="utf-8") as f:
    CATEGORIAS = json.load(f)["categorias"]

ESPERA_LOTE = 4.0  # segundos sem mensagem nova antes de fechar o lote

# Abaixo deste score, o "vizinho mais proximo" e ruido: nomes curtos e
# abreviados produzem vizinhos que casam por semelhanca de string, nao
# de significado (ex.: "OXXO ESTACAO LESTE" casa com "ESTAC PATIO
# NORTE"). Mandar isso como exemplo para o LLM PIORA a resposta -- RAG
# com recuperacao ruim e pior do que RAG nenhum. Entao filtramos.
LIMIAR_EXEMPLO = float(os.environ.get("LIMIAR_EXEMPLO", "0.72"))

# Exemplos canonicos: um representante de cada categoria, usados quando
# a recuperacao nao trouxe nada confiavel.
EXEMPLOS_PADRAO = [
    {"descricao": "POSTO IPIRANGA CENTRO", "categoria": "Transporte", "score": 1.0},
    {"descricao": "PANELA DE BARRO REST", "categoria": "Alimentacao", "score": 1.0},
    {"descricao": "SUPERMERCADO BOA VEZ", "categoria": "Mercado", "score": 1.0},
]


def exemplos_confiaveis(vizinhos):
    """Escolhe os exemplos que vao no prompt.

    ATENCAO -- isto foi medido, nao deduzido. Duas licoes:

    1) O gemma2:2b PRECISA de exemplos para manter o formato de lista.
       Sem nenhum exemplo ele responde o item 1 e para: no teste, o
       zero-shot classificou 1 de 10. Entao nunca devolvemos lista
       vazia, mesmo quando a recuperacao nao serve.

    2) Vizinho fraco e enganoso. "OXXO ESTACAO LESTE" casa com
       "ESTAC PATIO NORTE" por semelhanca de string (score 0,884) e
       empurra a resposta para Transporte. Por isso o filtro de limiar.

    Quando sobra pouco, completamos com exemplos canonicos: ancoram o
    formato sem apontar para a categoria errada.
    """
    fortes = [v for v in vizinhos if v["score"] >= LIMIAR_EXEMPLO][:3]
    if fortes:
        return fortes
    return EXEMPLOS_PADRAO

# O que cada categoria significa. Com vizinho ruim, e isto que sustenta
# a decisao do modelo.
GUIA_DE_CATEGORIAS = (
    "Transporte=combustivel, estacionamento, app de corrida, pedagio; "
    "Alimentacao=restaurante, bar, padaria, delivery, loja de conveniencia; "
    "Mercado=supermercado, acougue, hortifruti, laticinios; "
    "Saude=farmacia, clinica, laboratorio, suplemento, plano; "
    "Assinaturas=servico digital recorrente (streaming, nuvem, software); "
    "Compras=loja de varejo, e-commerce, utilidades, papelaria, vestuario; "
    "Educacao=escola, faculdade, curso, academia; "
    "Viagem=passagem, hotel, locadora, seguro viagem; "
    "Casa=conta de consumo, condominio, manutencao; "
    "Pets=pet shop, veterinario; "
    "Outros=pessoa fisica ou nao identificavel"
)


def vizinhos(qdrant, descricao, quantos=3):
    """Os `quantos` comerciantes mais parecidos do catalogo."""
    vetor = comum.vetorizar([normalizar(descricao)])[0]
    achados = qdrant.query_points(
        collection_name=comum.COLECAO_CATALOGO,
        query=vetor,
        limit=quantos,
        with_payload=True,
    ).points
    return vetor, [
        {
            "descricao": p.payload["descricao"],
            "categoria": p.payload["categoria"],
            "score": round(p.score, 4),
        }
        for p in achados
    ]


def perguntar_ao_llm(pendentes):
    """Uma chamada do LLM para um LOTE de compras duvidosas.

    O prompt e curto de proposito: em CPU, cada token custa. E usamos
    format=json do Ollama, que obriga o modelo a devolver JSON valido --
    sem isso o gemma:2b costuma enfeitar a resposta com texto solto.
    """
    linhas = []
    for n, item in enumerate(pendentes, start=1):
        # So entra como exemplo o vizinho confiavel. Vizinho fraco e
        # ruido e empurra o modelo para a categoria errada.
        bons = exemplos_confiaveis(item["vizinhos"])
        descricao = normalizar(item["compra"]["descricao"])
        if bons:
            exemplos = ", ".join(f"{v['descricao']}={v['categoria']}" for v in bons)
            linhas.append(f'{n}. "{descricao}" (parecidos: {exemplos})')
        else:
            linhas.append(f'{n}. "{descricao}"')

    prompt = (
        "Classifique cada compra de cartao de credito brasileiro em UMA categoria.\n"
        f"Categorias: {GUIA_DE_CATEGORIAS}.\n\n"
        + "\n".join(linhas)
        + "\n\nResponda apenas JSON: "
        '{"resultados":[{"n":1,"categoria":"..."}]}'
    )

    resposta = httpx.post(
        f"{OLLAMA}/api/generate",
        json={
            "model": MODELO,
            "prompt": prompt,
            "stream": False,
            "format": "json",
            "options": {"temperature": 0, "num_predict": 220},
        },
        timeout=300,
    )
    resposta.raise_for_status()
    bruto = resposta.json()["response"]

    # Indice sem acento e sem caixa -> o nome canonico da categoria.
    canonico = {sem_acento(c).lower(): c for c in CATEGORIAS}

    por_indice = {}
    for r in json.loads(bruto).get("resultados", []):
        bruta = str(r.get("categoria", "")).strip()
        # O modelo as vezes inventa categoria. Nesse caso cai em Outros,
        # em vez de poluir o relatorio com um rotulo inexistente.
        categoria = canonico.get(sem_acento(bruta).lower(), "Outros")
        por_indice[int(r["n"])] = categoria
    return por_indice


def publicar_resultado(produtor, qdrant, compra, categoria, origem, confianca, vetor, vizinhos_achados):
    saida = {
        **compra,
        "categoria": categoria,
        "origem_da_categoria": origem,  # catalogo | ia | regra
        "confianca": confianca,
        "vizinhos_considerados": vizinhos_achados[:3],
        "categorizado_em": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    comum.publicar(produtor, comum.TOPICO_CATEGORIZADAS, saida, chave=compra["fatura_id"])

    # Indexa na colecao de historico: e o que viabiliza a busca
    # semantica do servidor MCP ("quanto gastei com comida fora?").
    if vetor is not None:
        qdrant.upsert(
            collection_name=comum.COLECAO_COMPRAS,
            points=[
                models.PointStruct(
                    id=uuid.uuid4().hex,
                    vector=vetor,
                    payload={
                        "descricao": compra["descricao"],
                        "categoria": categoria,
                        "valor": compra["valor"],
                        "data": compra["data"],
                        "banco": compra["banco"],
                        "aluno": compra["aluno"],
                        "fatura_id": compra["fatura_id"],
                        "origem_da_categoria": origem,
                    },
                )
            ],
        )
    return saida


def main():
    registro.info(
        "consumidor_categoriza_compras | grupo=%s limiar=%.2f lote=%d modelo=%s",
        GRUPO, LIMIAR, TAMANHO_LOTE, MODELO,
    )
    produtor = comum.produtor_kafka()
    qdrant = comum.esperar("qdrant", comum.cliente_qdrant)
    comum.modelo_embedding()  # carrega agora, nao no primeiro evento
    registro.info("modelo de embedding carregado")

    consumidor = comum.esperar(
        "kafka",
        lambda: comum.consumidor_kafka(GRUPO, comum.TOPICO_EXTRAIDAS, do_inicio=True),
    )

    pendentes = []
    ultimo = time.time()
    contador = {"catalogo": 0, "ia": 0, "regra": 0}

    def fechar_lote():
        """Manda o que sobrou para o LLM e publica."""
        if not pendentes:
            return
        inicio = time.time()
        try:
            decisoes = perguntar_ao_llm(pendentes)
        except Exception as e:
            registro.error("LLM falhou (%s) -- caindo no vizinho mais proximo", e)
            decisoes = {}

        for n, item in enumerate(pendentes, start=1):
            # Sem resposta do modelo, o melhor palpite disponivel ainda e
            # o vizinho mais proximo. O lab continua andando.
            if n in decisoes:
                categoria, origem = decisoes[n], "ia"
            else:
                categoria = item["vizinhos"][0]["categoria"] if item["vizinhos"] else "Outros"
                origem = "catalogo"
            contador[origem] += 1
            publicar_resultado(
                produtor, qdrant, item["compra"], categoria, origem,
                item["vizinhos"][0]["score"] if item["vizinhos"] else 0.0,
                item["vetor"], item["vizinhos"],
            )

        registro.info(
            "lote de %d classificado pela IA em %.1fs | acumulado catalogo=%d ia=%d",
            len(pendentes), time.time() - inicio, contador["catalogo"], contador["ia"],
        )
        pendentes.clear()
        produtor.flush(30)

    while True:
        msg = consumidor.poll(1.0)

        if msg is None:
            if pendentes and time.time() - ultimo > ESPERA_LOTE:
                fechar_lote()
            continue
        if msg.error():
            registro.warning("erro no consumo: %s", msg.error())
            continue

        try:
            compra = json.loads(msg.value())

            # Credito/pagamento nao e gasto: resolve por regra, sem IA.
            if compra.get("tipo") == "credito":
                contador["regra"] += 1
                publicar_resultado(
                    produtor, qdrant, compra, "Pagamentos", "regra", 1.0, None, []
                )
                produtor.flush(5)
                continue

            vetor, achados = vizinhos(qdrant, compra["descricao"])

            if achados and achados[0]["score"] >= LIMIAR:
                # CAMADA 1: o catalogo resolveu. Nem toca no LLM.
                contador["catalogo"] += 1
                publicar_resultado(
                    produtor, qdrant, compra, achados[0]["categoria"],
                    "catalogo", achados[0]["score"], vetor, achados,
                )
                produtor.flush(5)
            else:
                # CAMADA 2: duvidoso -> espera formar lote para a IA.
                pendentes.append({"compra": compra, "vetor": vetor, "vizinhos": achados})
                ultimo = time.time()
                if len(pendentes) >= TAMANHO_LOTE:
                    fechar_lote()

        except Exception as e:
            registro.error("falha ao categorizar: %s", e)
            comum.publicar(
                produtor,
                comum.TOPICO_ERROS,
                {
                    "etapa": "categoriza",
                    "erro": f"{type(e).__name__}: {e}",
                    "evento_original": msg.value().decode("utf-8", "replace")[:1500],
                    "ocorrido_em": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                },
            )
            produtor.flush(10)


if __name__ == "__main__":
    main()
