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

import comum

registro = comum.log("categoriza")

GRUPO = os.environ.get("GRUPO", "grupo-categoriza")
LIMIAR = float(os.environ.get("LIMIAR_CATALOGO", "0.80"))
TAMANHO_LOTE = int(os.environ.get("LOTE_IA", "5"))
OLLAMA = os.environ.get("OLLAMA_URL", "http://ollama:11434")
MODELO = os.environ.get("OLLAMA_MODELO", "gemma:2b")

with open("comerciantes.json", encoding="utf-8") as f:
    CATEGORIAS = json.load(f)["categorias"]

ESPERA_LOTE = 4.0  # segundos sem mensagem nova antes de fechar o lote


def vizinhos(qdrant, descricao, quantos=3):
    """Os `quantos` comerciantes mais parecidos do catalogo."""
    vetor = comum.vetorizar([descricao], tipo="query")[0]
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
        exemplos = ", ".join(
            f"{v['descricao']}={v['categoria']}" for v in item["vizinhos"][:3]
        )
        linhas.append(f"{n}. \"{item['compra']['descricao']}\" (parecidos: {exemplos})")

    prompt = (
        "Classifique cada compra de cartao de credito em UMA categoria.\n"
        f"Categorias permitidas: {', '.join(CATEGORIAS)}.\n\n"
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

    por_indice = {}
    for r in json.loads(bruto).get("resultados", []):
        categoria = str(r.get("categoria", "")).strip()
        # O modelo as vezes inventa categoria. Nesse caso cai em Outros,
        # em vez de poluir o relatorio com um rotulo inexistente.
        if categoria not in CATEGORIAS:
            categoria = "Outros"
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
