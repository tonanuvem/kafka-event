# -*- coding: utf-8 -*-
"""
servidor_mcp_faturas : expõe o lab como ferramentas para um LLM.

O QUE E MCP. Model Context Protocol e um protocolo aberto que padroniza
como uma aplicacao de IA conversa com ferramentas externas. Antes dele,
cada cliente (Amazon Q, Claude, Cursor, VS Code) tinha o seu jeito, e a
integracao escrita para um nao servia para o outro: N clientes x M
ferramentas de trabalho duplicado. Com MCP, voce escreve UM servidor e
qualquer cliente MCP passa a usa-lo. E o USB-C das ferramentas de IA.

DOIS PAPEIS:
  servidor (este arquivo) -> ANUNCIA as ferramentas e as executa
  cliente  (q chat, o Inspector) -> descobre as tools, repassa ao LLM
                                    e executa a que o LLM escolher

Repare: NAO existe IA neste arquivo. O servidor MCP e so uma casca
padronizada em volta do Kafka e do Qdrant. A inteligencia esta no
cliente.

Como rodar: stdio (entrada/saida padrao), o transporte usado tanto pelo
Amazon Q CLI quanto pelo MCP Inspector.
"""
import json
import os

from mcp.server.mcpserver import MCPServer

import comum

servidor = MCPServer("faturas-fiap")

ALUNO = os.environ.get("ALUNO", "aluno-sem-nome")


# ---------------------------------------------------------------- Kafka
@servidor.tool()
def listar_topicos() -> str:
    """Lista os topicos do Kafka com particoes e quantidade de mensagens."""
    from confluent_kafka import Consumer
    from confluent_kafka.admin import AdminClient

    broker = os.environ.get("KAFKA_BROKER", "kafka:19092")
    admin = AdminClient({"bootstrap.servers": broker})
    consumidor = Consumer({"bootstrap.servers": broker, "group.id": "mcp-leitura"})

    saida = []
    for nome, meta in admin.list_topics(timeout=20).topics.items():
        if nome.startswith("__"):  # topicos internos do Kafka
            continue
        total = 0
        for pid in meta.partitions:
            inicio, fim = consumidor.get_watermark_offsets(
                __import__("confluent_kafka").TopicPartition(nome, pid), timeout=10
            )
            total += max(0, fim - inicio)
        saida.append({"topico": nome, "particoes": len(meta.partitions), "mensagens": total})

    consumidor.close()
    return json.dumps(sorted(saida, key=lambda t: t["topico"]), ensure_ascii=False, indent=2)


@servidor.tool()
def consultar_lag(grupo: str = "grupo-categoriza") -> str:
    """Lag de um consumer group: quantas mensagens ainda faltam processar,
    por particao. Lag alto significa que o consumidor nao esta dando
    conta do ritmo de producao."""
    from confluent_kafka import Consumer, TopicPartition
    from confluent_kafka.admin import AdminClient, ConsumerGroupTopicPartitions

    broker = os.environ.get("KAFKA_BROKER", "kafka:19092")
    admin = AdminClient({"bootstrap.servers": broker})
    consumidor = Consumer({"bootstrap.servers": broker, "group.id": f"{grupo}-leitura"})

    try:
        futuro = admin.list_consumer_group_offsets(
            [ConsumerGroupTopicPartitions(grupo)]
        )[grupo]
        atribuicoes = futuro.result().topic_partitions
    except Exception as e:
        return json.dumps({"erro": f"grupo '{grupo}' nao encontrado: {e}"}, ensure_ascii=False)

    detalhe, lag_total = [], 0
    for tp in atribuicoes:
        _, fim = consumidor.get_watermark_offsets(
            TopicPartition(tp.topic, tp.partition), timeout=10
        )
        posicao = tp.offset if tp.offset >= 0 else 0
        lag = max(0, fim - posicao)
        lag_total += lag
        detalhe.append(
            {"topico": tp.topic, "particao": tp.partition,
             "processado_ate": posicao, "existe_ate": fim, "lag": lag}
        )

    consumidor.close()
    return json.dumps(
        {"grupo": grupo, "lag_total": lag_total, "particoes": detalhe},
        ensure_ascii=False, indent=2,
    )


@servidor.tool()
def enviar_fatura(banco: str = "azul") -> str:
    """Dispara o pipeline publicando uma fatura de exemplo.
    banco: 'azul' (uma coluna) ou 'verde' (duas colunas)."""
    import httpx

    if banco not in ("azul", "verde"):
        return json.dumps({"erro": "banco deve ser 'azul' ou 'verde'"})
    r = httpx.post(
        "http://produtor_envia_fatura:5001/faturas/exemplo",
        data={"banco": banco},
        timeout=60,
    )
    return json.dumps(r.json(), ensure_ascii=False, indent=2)


# --------------------------------------------------------------- Qdrant
# Acima deste score, a pergunta e considerada "sobre uma categoria" e a
# busca passa a ser filtrada por ela. Abaixo, busca-se em tudo: uma
# pergunta como "compras acima de 500 reais" nao e sobre categoria
# nenhuma, e forcar um filtro ali so daria resposta errada com cara de
# certa.
LIMIAR_ROTEAMENTO = float(os.environ.get("LIMIAR_ROTEAMENTO", "0.45"))


def _cosseno(a, b):
    import math

    produto = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return produto / (na * nb) if na and nb else 0.0


def _rotear(vetor_pergunta):
    """Descobre de qual categoria a pergunta fala.

    Compara a pergunta com a DEFINICAO de cada categoria (11 vetores,
    custo irrisorio). Medido no lab, isso leva a precisao no top-5 de
    83% para 97%: filtra de uma vez o ruido das outras categorias, em
    vez de torcer para o ranking resolver.
    """
    from categoriza import DEFINICOES_CATEGORIA

    nomes = list(DEFINICOES_CATEGORIA)
    vetores = comum.vetorizar(
        ["%s: %s" % (n, DEFINICOES_CATEGORIA[n]) for n in nomes]
    )
    melhor, score = max(
        ((n, _cosseno(vetor_pergunta, v)) for n, v in zip(nomes, vetores)),
        key=lambda t: t[1],
    )
    return (melhor if score >= LIMIAR_ROTEAMENTO else None), round(score, 4)


@servidor.tool()
def buscar_gastos(pergunta: str, limite: int = 10, categoria: str = "") -> str:
    """Busca SEMANTICA nas compras ja categorizadas.

    Funciona por significado, nao por palavra: 'comida fora de casa'
    encontra 'IFD*CANTINA DO ZECA' e 'PAYGO*BAR DO TONHO' mesmo sem
    nenhuma dessas palavras aparecer na pergunta.

    A pergunta e primeiro roteada para uma categoria. Informe
    `categoria` para forcar uma, ou 'todas' para desligar o filtro.
    """
    from qdrant_client import models

    qdrant = comum.cliente_qdrant()
    vetor = comum.vetorizar([pergunta])[0]

    if categoria.lower() in ("todas", "all"):
        alvo, score = None, None
    elif categoria:
        alvo, score = categoria, None
    else:
        alvo, score = _rotear(vetor)

    filtro = None
    if alvo:
        filtro = models.Filter(must=[models.FieldCondition(
            key="categoria", match=models.MatchValue(value=alvo))])

    achados = qdrant.query_points(
        collection_name=comum.COLECAO_COMPRAS,
        query=vetor,
        limit=max(1, min(limite, 50)),
        with_payload=True,
        query_filter=filtro,
    ).points

    resultado = [
        {
            "descricao": p.payload.get("descricao"),
            "categoria": p.payload.get("categoria"),
            "valor": p.payload.get("valor"),
            "data": p.payload.get("data"),
            "banco": p.payload.get("banco"),
            "similaridade": round(p.score, 4),
        }
        for p in achados
    ]
    return json.dumps(
        {
            "pergunta": pergunta,
            # Exposto de proposito: o aluno ve a decisao de roteamento e
            # consegue depurar quando a resposta nao faz sentido.
            "roteada_para": alvo or "(sem filtro de categoria)",
            "score_do_roteamento": score,
            "encontradas": len(resultado),
            "soma": round(sum(r["valor"] or 0 for r in resultado), 2),
            "compras": resultado,
        },
        ensure_ascii=False, indent=2,
    )


@servidor.tool()
def resumo_por_categoria() -> str:
    """Total gasto por categoria, somando tudo o que o pipeline ja
    categorizou."""
    qdrant = comum.cliente_qdrant()

    por_categoria, por_origem = {}, {}
    total, quantidade = 0.0, 0
    proximo = None

    while True:
        pontos, proximo = qdrant.scroll(
            collection_name=comum.COLECAO_COMPRAS,
            limit=256, offset=proximo, with_payload=True, with_vectors=False,
        )
        for p in pontos:
            categoria = p.payload.get("categoria", "?")
            valor = p.payload.get("valor") or 0
            por_categoria[categoria] = round(por_categoria.get(categoria, 0) + valor, 2)
            origem = p.payload.get("origem_da_categoria", "?")
            por_origem[origem] = por_origem.get(origem, 0) + 1
            total += valor
            quantidade += 1
        if proximo is None:
            break

    return json.dumps(
        {
            "compras_categorizadas": quantidade,
            "total": round(total, 2),
            "por_categoria": dict(sorted(por_categoria.items(), key=lambda x: -x[1])),
            "por_origem_da_categoria": por_origem,
        },
        ensure_ascii=False, indent=2,
    )


@servidor.tool()
def consultar_catalogo(descricao: str) -> str:
    """Mostra os vizinhos mais proximos de uma descricao no catalogo de
    comerciantes, com o score. Util para entender POR QUE o pipeline
    escolheu (ou nao) uma categoria pelo catalogo."""
    qdrant = comum.cliente_qdrant()
    vetor = comum.vetorizar([descricao])[0]
    achados = qdrant.query_points(
        collection_name=comum.COLECAO_CATALOGO, query=vetor, limit=5, with_payload=True
    ).points

    limiar = float(os.environ.get("LIMIAR_CATALOGO", "0.80"))
    vizinhos = [
        {"descricao": p.payload["descricao"], "categoria": p.payload["categoria"],
         "score": round(p.score, 4)}
        for p in achados
    ]
    return json.dumps(
        {
            "consulta": descricao,
            "limiar_para_dispensar_a_ia": limiar,
            "resolveria_pelo_catalogo": bool(vizinhos and vizinhos[0]["score"] >= limiar),
            "vizinhos": vizinhos,
        },
        ensure_ascii=False, indent=2,
    )


if __name__ == "__main__":
    servidor.run()
