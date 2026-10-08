# -*- coding: utf-8 -*-
"""
consumidor_analisa_faturas : resgata a "ficha" e abre a "mala".

Este e o servico que APLICA o claim-check do outro lado: recebe o evento
com a URL, baixa o PDF do S3, le as linhas e publica UM EVENTO POR
COMPRA.

Repare no efeito: 1 evento de entrada vira ~40 de saida. Esse fan-out e
o que cria a fila de trabalho para a etapa de IA -- e o que torna o lag
visivel no Console.
"""
import json
import os
import tempfile
import time
from datetime import datetime, timezone

import comum
import parsers

registro = comum.log("analisa")
GRUPO = os.environ.get("GRUPO", "grupo-analisa")


def processar(evento, produtor, s3):
    fatura_id = evento["fatura_id"]
    inicio = time.time()

    # --- resgate do claim-check: a ficha vira a mala ---
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=True) as tmp:
        s3.download_file(evento["bucket"], evento["objeto"], tmp.name)
        banco, compras = parsers.ler_fatura(tmp.name, evento.get("banco"))

    gastos = [c for c in compras if c["tipo"] == "compra"]
    registro.info(
        "fatura %s (banco %s): %d linhas lidas (%d compras) em %.1fs -> %d eventos",
        fatura_id, banco, len(compras), len(gastos), time.time() - inicio, len(compras),
    )

    # total_de_linhas viaja em TODO evento para o notificador saber
    # quando a fatura esta completa, sem precisar de banco de dados.
    for n, compra in enumerate(compras, start=1):
        saida = {
            "fatura_id": fatura_id,
            "aluno": evento["aluno"],
            "banco": banco,
            "linha": n,
            "total_de_linhas": len(compras),
            "extraido_em": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            **compra,
        }
        # CHAVE = fatura_id: todas as compras de uma fatura caem na mesma
        # particao e sao processadas em ordem.
        comum.publicar(produtor, comum.TOPICO_EXTRAIDAS, saida, chave=fatura_id)

    produtor.flush(30)


def main():
    registro.info("consumidor_analisa_faturas | grupo=%s", GRUPO)
    produtor = comum.produtor_kafka()
    s3 = comum.cliente_s3()
    consumidor = comum.esperar(
        "kafka",
        lambda: comum.consumidor_kafka(GRUPO, comum.TOPICO_FATURAS, do_inicio=True),
    )

    while True:
        msg = consumidor.poll(1.0)
        if msg is None:
            continue
        if msg.error():
            registro.warning("erro no consumo: %s", msg.error())
            continue

        try:
            processar(json.loads(msg.value()), produtor, s3)
        except Exception as e:
            registro.error("falha ao analisar: %s", e)
            comum.publicar(
                produtor,
                comum.TOPICO_ERROS,
                {
                    "etapa": "analisa",
                    "erro": f"{type(e).__name__}: {e}",
                    "evento_original": msg.value().decode("utf-8", "replace")[:1500],
                    "ocorrido_em": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                },
            )
            produtor.flush(10)


if __name__ == "__main__":
    main()
