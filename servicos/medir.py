# -*- coding: utf-8 -*-
"""
Mede o lab no ambiente real. Roda dentro da rede do compose.

Produz os numeros que decidem o desenho da aula:
  - latencia e tokens/s do LLM local
  - se o gemma:2b devolve JSON valido de forma confiavel
  - acerto da categorizacao contra o gabarito das faturas sinteticas
  - quanto do trabalho o catalogo resolve sem chamar a IA
  - tempo de ponta a ponta de uma fatura
"""
import json
import os
import statistics
import sys
import time

import httpx

import comum

OLLAMA = os.environ.get("OLLAMA_URL", "http://ollama:11434")
MODELO = os.environ.get("OLLAMA_MODELO", "gemma:2b")

with open("comerciantes.json", encoding="utf-8") as f:
    CATEGORIAS = json.load(f)["categorias"]

# As 10 descricoes que ficam de fora do catalogo (as unicas que chegam
# ao LLM), com a categoria correta -- o gabarito.
GABARITO = {
    "PROPIG *A F DE SOUZA": "Outros",
    "OXXO ESTACAO LESTE": "Alimentacao",
    "VMT*NUTRIFORMULA": "Saude",
    "DAISO UTILIDADES BR": "Compras",
    "LATICINIOS SERRA AZUL": "Mercado",
    "PBADMINISTRADORA COND": "Casa",
    "ASA*ACADEMIA PULSE": "Educacao",
    "HOTEL MIRANTE SUL": "Viagem",
    "AUTO POSTO ARACAJU": "Transporte",
    "MP *J R DA SILVA": "Outros",
}


def titulo(t):
    print(f"\n{'=' * 64}\n  {t}\n{'=' * 64}")


def montar_prompt(qdrant, descricoes):
    linhas = []
    for n, desc in enumerate(descricoes, start=1):
        vetor = comum.vetorizar([desc])[0]
        achados = qdrant.query_points(
            collection_name=comum.COLECAO_CATALOGO, query=vetor, limit=3, with_payload=True
        ).points
        exemplos = ", ".join(f"{p.payload['descricao']}={p.payload['categoria']}" for p in achados)
        linhas.append(f'{n}. "{desc}" (parecidos: {exemplos})')

    return (
        "Classifique cada compra de cartao de credito em UMA categoria.\n"
        f"Categorias permitidas: {', '.join(CATEGORIAS)}.\n\n"
        + "\n".join(linhas)
        + '\n\nResponda apenas JSON: {"resultados":[{"n":1,"categoria":"..."}]}'
    )


def medir_llm(qdrant, rodadas=3, por_lote=5):
    titulo(f"LLM LOCAL : {MODELO}")

    descricoes = list(GABARITO)[:por_lote]
    prompt = montar_prompt(qdrant, descricoes)
    print(f"lote de {por_lote} compras | prompt de {len(prompt)} caracteres\n")

    tempos, taxas, validos, acertos, total_itens = [], [], 0, 0, 0

    for r in range(1, rodadas + 1):
        t0 = time.time()
        try:
            resp = httpx.post(
                f"{OLLAMA}/api/generate",
                json={
                    "model": MODELO, "prompt": prompt, "stream": False,
                    "format": "json", "options": {"temperature": 0, "num_predict": 220},
                },
                timeout=900,
            )
            resp.raise_for_status()
            d = resp.json()
        except Exception as e:
            print(f"  rodada {r}: FALHOU -- {type(e).__name__}: {e}")
            continue

        dt = time.time() - t0
        tempos.append(dt)
        if d.get("eval_duration"):
            taxas.append(d.get("eval_count", 0) / (d["eval_duration"] / 1e9))

        try:
            resultados = json.loads(d["response"]).get("resultados", [])
            validos += 1
            for item in resultados:
                n = int(item.get("n", 0))
                if 1 <= n <= len(descricoes):
                    total_itens += 1
                    esperado = GABARITO[descricoes[n - 1]]
                    if str(item.get("categoria", "")).strip() == esperado:
                        acertos += 1
            situacao = f"JSON ok, {len(resultados)} itens"
        except Exception as e:
            situacao = f"JSON INVALIDO ({type(e).__name__})"

        print(f"  rodada {r}: {dt:6.1f}s  ({dt / por_lote:5.1f}s/compra)  {situacao}")

    if not tempos:
        print("\n  nenhuma rodada concluiu -- o LLM nao respondeu")
        return None

    print()
    print(f"  tempo por lote de {por_lote} : mediana {statistics.median(tempos):.1f}s"
          f"  (min {min(tempos):.1f}  max {max(tempos):.1f})")
    print(f"  tempo por compra     : {statistics.median(tempos) / por_lote:.1f}s")
    if taxas:
        print(f"  geracao              : {statistics.median(taxas):.1f} tokens/s")
    print(f"  JSON valido          : {validos}/{len(tempos)} rodadas")
    if total_itens:
        print(f"  acerto de categoria  : {acertos}/{total_itens} ({acertos / total_itens * 100:.0f}%)")
    return statistics.median(tempos) / por_lote


def medir_catalogo(qdrant):
    """Quanto do trabalho o vetor resolve sozinho, sem chamar a IA."""
    titulo("ROTEAMENTO : CATALOGO versus IA")

    limiar = float(os.environ.get("LIMIAR_CATALOGO", "0.80"))
    sys.path.insert(0, "/app/faturas_exemplo")
    try:
        from comerciantes import COMERCIANTES
    except ImportError:
        print("  faturas de exemplo nao montadas -- pulando")
        return

    # Mede como a FATURA apresenta a compra (com cidade colada), nao a
    # string exata do catalogo. Medir o catalogo contra ele mesmo daria
    # score 1,000 e um numero bonito que nao significa nada.
    descricoes = [f"{d} {cidade}" for d, _, cidade in COMERCIANTES]
    t0 = time.time()
    vetores = comum.vetorizar(descricoes)
    dt_vetor = (time.time() - t0) / len(descricoes)

    resolvidos, scores = 0, []
    for desc, vetor in zip(descricoes, vetores):
        achados = qdrant.query_points(
            collection_name=comum.COLECAO_CATALOGO, query=vetor, limit=1, with_payload=True
        ).points
        if achados:
            scores.append(achados[0].score)
            if achados[0].score >= limiar:
                resolvidos += 1

    pct = resolvidos / len(descricoes) * 100
    print(f"  limiar configurado   : {limiar}")
    print(f"  embedding por compra : {dt_vetor * 1000:.0f} ms")
    print(f"  resolvidos pelo vetor: {resolvidos}/{len(descricoes)} ({pct:.0f}%)")
    print(f"  score mediano        : {statistics.median(scores):.3f}")
    print(f"\n  -> {100 - pct:.0f}% das compras chegariam ao LLM")


def medir_ponta_a_ponta():
    titulo("PONTA A PONTA : UMA FATURA")
    try:
        antes = httpx.get("http://painel_de_gastos:3000/dados", timeout=20).json()["quantidade"]
    except Exception as e:
        print(f"  painel indisponivel: {e}")
        return

    t0 = time.time()
    r = httpx.post(
        "http://produtor_envia_fatura:5001/faturas/exemplo", data={"banco": "azul"}, timeout=60
    )
    fatura = r.json()
    print(f"  fatura {fatura['fatura_id']} enviada ({fatura['tamanho_bytes'] / 1024:.0f} KB)")
    print(f"  evento no Kafka: {len(json.dumps(fatura))} bytes "
          f"({fatura['tamanho_bytes'] / len(json.dumps(fatura)):.0f}x menor)\n")

    ultimo, estavel = antes, 0
    while time.time() - t0 < 900:
        time.sleep(5)
        try:
            agora = httpx.get("http://painel_de_gastos:3000/dados", timeout=20).json()["quantidade"]
        except Exception:
            continue
        novas = agora - antes
        if agora == ultimo:
            estavel += 1
            if estavel >= 4 and novas > 0:
                break
        else:
            estavel = 0
            print(f"    {time.time() - t0:5.0f}s  {novas:3d} compras categorizadas")
        ultimo = agora

    print(f"\n  TOTAL: {ultimo - antes} compras em {time.time() - t0:.0f}s")


def main():
    print(f"\nmedindo o lab | modelo {MODELO} | {time.strftime('%Y-%m-%d %H:%M')}")
    qdrant = comum.cliente_qdrant()
    comum.modelo_embedding()

    medir_catalogo(qdrant)
    medir_llm(qdrant)
    if "--completo" in sys.argv:
        medir_ponta_a_ponta()
    print()


if __name__ == "__main__":
    main()
