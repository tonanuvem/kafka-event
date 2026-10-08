# -*- coding: utf-8 -*-
"""Infraestrutura compartilhada pelos microsservicos do lab."""
import json
import logging
import os
import sys
import time

AMBIENTE = os.environ.get

TOPICO_FATURAS = AMBIENTE("TOPICO_FATURAS", "faturas-recebidas")
TOPICO_EXTRAIDAS = AMBIENTE("TOPICO_EXTRAIDAS", "compras-extraidas")
TOPICO_CATEGORIZADAS = AMBIENTE("TOPICO_CATEGORIZADAS", "compras-categorizadas")
TOPICO_ERROS = AMBIENTE("TOPICO_ERROS", "compras-com-erro")
TOPICO_MENSAGENS = AMBIENTE("TOPICO_MENSAGENS", "lab-mensagens")

BUCKET = AMBIENTE("S3_BUCKET", "faturas")
COLECAO_CATALOGO = "comerciantes"
COLECAO_COMPRAS = "compras"

MODELO_EMBEDDING = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
DIMENSOES = 384


def log(nome):
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stdout,
    )
    return logging.getLogger(nome)


# ---------------------------------------------------------------- Kafka
def produtor_kafka():
    from confluent_kafka import Producer

    return Producer(
        {
            "bootstrap.servers": AMBIENTE("KAFKA_BROKER", "kafka:19092"),
            "client.id": AMBIENTE("HOSTNAME", "lab"),
            # Garante que a mensagem nao se duplica num retry do cliente.
            "enable.idempotence": True,
            "linger.ms": 20,
        }
    )


def consumidor_kafka(grupo, topicos, do_inicio=False):
    from confluent_kafka import Consumer

    c = Consumer(
        {
            "bootstrap.servers": AMBIENTE("KAFKA_BROKER", "kafka:19092"),
            "group.id": grupo,
            # "latest" seria mais natural em producao, mas num lab o aluno
            # sobe o consumidor DEPOIS de publicar e esperaria ver as
            # mensagens que ja estao la.
            "auto.offset.reset": "earliest" if do_inicio else "latest",
            # Commit automatico: o lab trata de streaming de eventos, nao
            # de semantica exactly-once. O modulo da DLQ discute o tema.
            "enable.auto.commit": True,
            "session.timeout.ms": 45000,
            "max.poll.interval.ms": 600000,  # o LLM em CPU e lento
        }
    )
    c.subscribe(topicos if isinstance(topicos, list) else [topicos])
    return c


def publicar(produtor, topico, valor, chave=None):
    """Publica um dicionario como JSON. A CHAVE decide a particao: usando
    o fatura_id, todas as compras de uma fatura caem na mesma particao e
    chegam em ordem."""
    produtor.produce(
        topico,
        key=(chave or "").encode("utf-8") if chave else None,
        value=json.dumps(valor, ensure_ascii=False).encode("utf-8"),
    )
    produtor.poll(0)


def esperar(descricao, funcao, tentativas=40, intervalo=3):
    """Espera uma dependencia subir, em vez de morrer no primeiro erro."""
    registro = log("espera")
    for n in range(1, tentativas + 1):
        try:
            return funcao()
        except Exception as e:
            registro.info("aguardando %s (%d/%d): %s", descricao, n, tentativas, e)
            time.sleep(intervalo)
    raise RuntimeError(f"{descricao} nao respondeu")


# ------------------------------------------------- Armazenamento S3
def cliente_s3():
    """Cliente S3 via boto3 -- o SDK oficial da AWS.

    O endpoint aponta para o SeaweedFS do lab, mas o MESMO codigo roda
    contra o S3 de verdade: basta remover o endpoint_url. E por isso que
    o lab usa boto3 em vez do SDK de um fornecedor especifico.
    """
    import boto3
    from botocore.config import Config

    return boto3.client(
        "s3",
        endpoint_url=AMBIENTE("S3_ENDPOINT", "http://objetos:8333"),
        aws_access_key_id=AMBIENTE("S3_CHAVE", "labfiap"),
        aws_secret_access_key=AMBIENTE("S3_SEGREDO", "labfiap123"),
        region_name="us-east-1",
        config=Config(signature_version="s3v4", retries={"max_attempts": 5}),
    )


# --------------------------------------------------------------- Qdrant
def cliente_qdrant():
    from qdrant_client import QdrantClient

    return QdrantClient(url=AMBIENTE("QDRANT_URL", "http://qdrant:6333"), timeout=30)


_modelo = None


def modelo_embedding():
    """Carrega o modelo uma vez por processo (leva ~3 s)."""
    global _modelo
    if _modelo is None:
        from fastembed import TextEmbedding

        _modelo = TextEmbedding(model_name=MODELO_EMBEDDING)
    return _modelo


def vetorizar(textos, tipo=None):
    """Transforma texto em vetor de 384 numeros.

    O parametro `tipo` existe so por compatibilidade com modelos da
    familia e5, que exigem prefixar o texto com 'query:' ou 'passage:'.
    O modelo usado aqui e da familia `paraphrase`, que NAO usa prefixo:
    adicionar um degradaria a similaridade. Detalhe que custa caro
    esquecer quando se troca de modelo de embedding.
    """
    modelo = modelo_embedding()
    return [v.tolist() for v in modelo.embed(list(textos))]
