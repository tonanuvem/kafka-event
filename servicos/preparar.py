# -*- coding: utf-8 -*-
"""
Prepara o ambiente do lab e sai. Roda UMA vez, antes dos servicos.

Faz o que o roteiro antigo pedia que o aluno fizesse a mao: criar
topicos, criar o bucket, criar as colecoes vetoriais e carregar o
catalogo de comerciantes.
"""
import json
import os

from confluent_kafka.admin import AdminClient, NewTopic
from qdrant_client import models

import comum

registro = comum.log("preparar")

# 3 particoes nos topicos de compra: e o minimo para o aluno VER o
# rebalanceamento quando sobe um segundo consumidor no mesmo grupo.
TOPICOS = [
    (comum.TOPICO_MENSAGENS, 1),
    (comum.TOPICO_FATURAS, 1),
    (comum.TOPICO_EXTRAIDAS, 3),
    (comum.TOPICO_CATEGORIZADAS, 3),
    (comum.TOPICO_ERROS, 1),
]


def criar_topicos():
    admin = AdminClient({"bootstrap.servers": os.environ.get("KAFKA_BROKER", "kafka:19092")})
    existentes = set(admin.list_topics(timeout=30).topics)

    novos = [
        NewTopic(nome, num_partitions=p, replication_factor=1)
        for nome, p in TOPICOS
        if nome not in existentes
    ]
    if not novos:
        registro.info("topicos ja existem")
        return

    for nome, futuro in admin.create_topics(novos).items():
        try:
            futuro.result()
            registro.info("topico criado: %s", nome)
        except Exception as e:
            registro.warning("topico %s: %s", nome, e)


def criar_bucket():
    s3 = comum.cliente_s3()
    existentes = [b["Name"] for b in s3.list_buckets().get("Buckets", [])]
    if comum.BUCKET in existentes:
        registro.info("bucket ja existe: %s", comum.BUCKET)
    else:
        s3.create_bucket(Bucket=comum.BUCKET)
        registro.info("bucket criado: %s", comum.BUCKET)


def criar_colecoes():
    """Duas colecoes, dois papeis distintos:

    comerciantes -> catalogo de referencia. E consultado ANTES da IA:
                    se o vizinho mais proximo for proximo o bastante,
                    a categoria vem dele e o LLM nem e chamado.
    compras      -> historico do que o pipeline ja categorizou. Serve a
                    busca semantica do servidor MCP ("gastos com comida").
    """
    qdrant = comum.cliente_qdrant()
    existentes = {c.name for c in qdrant.get_collections().collections}

    for nome in (comum.COLECAO_CATALOGO, comum.COLECAO_COMPRAS):
        if nome in existentes:
            registro.info("colecao ja existe: %s", nome)
            continue
        qdrant.create_collection(
            collection_name=nome,
            vectors_config=models.VectorParams(
                size=comum.DIMENSOES,
                # Cosseno: compara ANGULO entre vetores, nao tamanho.
                # E o que faz "significado parecido" virar "score alto".
                distance=models.Distance.COSINE,
            ),
        )
        registro.info("colecao criada: %s", nome)


def carregar_catalogo():
    qdrant = comum.cliente_qdrant()
    if qdrant.count(comum.COLECAO_CATALOGO).count > 0:
        registro.info("catalogo ja carregado")
        return

    with open("comerciantes.json", encoding="utf-8") as f:
        dados = json.load(f)
    itens = dados["comerciantes"]

    registro.info("vetorizando %d comerciantes...", len(itens))
    vetores = comum.vetorizar([i["descricao"] for i in itens], tipo="passage")

    qdrant.upsert(
        collection_name=comum.COLECAO_CATALOGO,
        points=[
            models.PointStruct(
                id=n,
                vector=v,
                payload={"descricao": i["descricao"], "categoria": i["categoria"]},
            )
            for n, (i, v) in enumerate(zip(itens, vetores))
        ],
    )
    registro.info("catalogo carregado: %d comerciantes", len(itens))


def main():
    registro.info("=== preparando o ambiente do lab ===")
    comum.esperar("kafka", criar_topicos)
    comum.esperar("s3", criar_bucket)
    comum.esperar("qdrant", criar_colecoes)
    carregar_catalogo()
    registro.info("=== ambiente pronto ===")


if __name__ == "__main__":
    main()
