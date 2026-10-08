# -*- coding: utf-8 -*-
"""
consumidor_notifica_teams : o resultado de negocio.

Consome tres topicos:

  lab-mensagens          -> card simples, imediato (modulo 1 do lab)
  compras-categorizadas  -> AGREGA por fatura e manda UM card com o
                            resumo por categoria
  compras-com-erro       -> card de alerta

Por que agregar em vez de postar cada compra: 40 cards por fatura
inundariam o canal da turma e estourariam o limite de requisicoes do
Power Automate (30 alunos x 40 linhas = 1200 chamadas). Um card por
fatura resolve os dois problemas.

A agregacao e EM MEMORIA, usando o campo total_de_linhas que viaja em
todo evento: quando chegam as N linhas da fatura, o resumo esta pronto.
"""
import json
import os
import time
from collections import defaultdict
from datetime import datetime, timezone

import httpx

import comum

registro = comum.log("notifica")

GRUPO = os.environ.get("GRUPO", "grupo-notifica")
WEBHOOK = os.environ.get("TEAMS_WEBHOOK", "").strip()
ALUNO = os.environ.get("ALUNO", "aluno-sem-nome")
FORMATO = os.environ.get("FORMATO_CARD", "adaptive")

# Se uma fatura ficar incompleta (linha perdida, erro no meio), nao
# espera para sempre: manda o que tem e avisa que esta parcial.
ESPERA_MAXIMA = 150


def enviar(titulo, linhas, cor="good"):
    """Envia ao Teams. Sem webhook configurado, imprime no log --
    o lab inteiro funciona sem o Teams pronto."""
    if FORMATO == "texto":
        corpo = {"text": f"**{titulo}**\n\n" + "\n".join(f"- {l}" for l in linhas)}
    else:
        corpo = {
            "type": "message",
            "attachments": [
                {
                    "contentType": "application/vnd.microsoft.card.adaptive",
                    "content": {
                        "type": "AdaptiveCard",
                        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                        "version": "1.4",
                        "body": [
                            {
                                "type": "TextBlock",
                                "text": titulo,
                                "weight": "Bolder",
                                "size": "Medium",
                                "color": cor,
                                "wrap": True,
                            },
                            *[
                                {"type": "TextBlock", "text": l, "wrap": True, "spacing": "None"}
                                for l in linhas
                            ],
                        ],
                    },
                }
            ],
        }

    if not WEBHOOK:
        registro.info("[SEM WEBHOOK] %s | %s", titulo, " / ".join(linhas[:4]))
        return

    try:
        r = httpx.post(WEBHOOK, json=corpo, timeout=30)
        if r.status_code in (200, 201, 202):
            registro.info("card enviado ao Teams: %s", titulo)
        else:
            registro.warning("Teams respondeu %s: %s", r.status_code, r.text[:200])
    except Exception as e:
        registro.error("falha ao enviar ao Teams: %s", e)


def card_da_fatura(fatura_id, compras, parcial=False):
    gastos = [c for c in compras if c.get("tipo") != "credito"]
    total = sum(c["valor"] for c in gastos)

    por_categoria = defaultdict(float)
    for c in gastos:
        por_categoria[c["categoria"]] += c["valor"]

    do_catalogo = sum(1 for c in gastos if c["origem_da_categoria"] == "catalogo")
    da_ia = sum(1 for c in gastos if c["origem_da_categoria"] == "ia")

    banco = compras[0].get("banco", "?")
    linhas = [
        f"Banco **{banco}** · {len(gastos)} compras · **R$ {total:,.2f}**".replace(",", "@")
        .replace(".", ",")
        .replace("@", "."),
        "",
        "**Gastos por categoria**",
    ]
    for categoria, valor in sorted(por_categoria.items(), key=lambda x: -x[1]):
        fatia = valor / total * 100 if total else 0
        barra = "█" * max(1, round(fatia / 5))
        v = f"{valor:,.2f}".replace(",", "@").replace(".", ",").replace("@", ".")
        linhas.append(f"`{barra:<20}` {categoria} — R$ {v} ({fatia:.0f}%)")

    maiores = sorted(gastos, key=lambda c: -c["valor"])[:5]
    linhas += ["", "**Maiores gastos**"]
    for c in maiores:
        v = f"{c['valor']:,.2f}".replace(",", "@").replace(".", ",").replace("@", ".")
        marca = "IA" if c["origem_da_categoria"] == "ia" else "catalogo"
        linhas.append(f"{c['data']} · {c['descricao'][:34]} · R$ {v} _({marca})_")

    linhas += [
        "",
        f"Resolvido pelo catalogo: **{do_catalogo}** · pela IA: **{da_ia}**",
        f"fatura `{fatura_id}`",
    ]
    if parcial:
        linhas.append("**ATENCAO: resumo parcial** — faltaram linhas (ver compras-com-erro).")

    titulo = f"[{ALUNO}] Fatura analisada — banco {banco}"
    return titulo, linhas, ("warning" if parcial else "good")


def main():
    registro.info(
        "consumidor_notifica_teams | grupo=%s webhook=%s",
        GRUPO, "configurado" if WEBHOOK else "AUSENTE (vai so logar)",
    )
    consumidor = comum.esperar(
        "kafka",
        lambda: comum.consumidor_kafka(
            GRUPO,
            [comum.TOPICO_MENSAGENS, comum.TOPICO_CATEGORIZADAS, comum.TOPICO_ERROS],
            do_inicio=True,
        ),
    )

    acumulado = defaultdict(list)
    primeira_linha_em = {}
    erros_enviados = 0

    while True:
        msg = consumidor.poll(1.0)

        # Fecha faturas que ficaram penduradas.
        agora = time.time()
        for fid in [f for f, t in primeira_linha_em.items() if agora - t > ESPERA_MAXIMA]:
            registro.warning("fatura %s incompleta -- enviando resumo parcial", fid)
            enviar(*card_da_fatura(fid, acumulado[fid], parcial=True))
            acumulado.pop(fid, None)
            primeira_linha_em.pop(fid, None)

        if msg is None:
            continue
        if msg.error():
            registro.warning("erro no consumo: %s", msg.error())
            continue

        topico = msg.topic()
        try:
            dados = json.loads(msg.value())
        except Exception:
            continue

        # ---- modulo 1: o caminho mais simples ----
        if topico == comum.TOPICO_MENSAGENS:
            enviar(
                f"[{dados.get('aluno', '?')}] Mensagem pelo Kafka",
                [dados.get("texto", ""), "", f"topico `{topico}` · offset `{msg.offset()}`"],
            )
            continue

        # ---- erros ----
        if topico == comum.TOPICO_ERROS:
            erros_enviados += 1
            if erros_enviados <= 5:  # nao inunda o canal numa falha em massa
                enviar(
                    f"[{ALUNO}] Falha no pipeline — etapa {dados.get('etapa')}",
                    [f"`{dados.get('erro', '')[:300]}`", "", "Mensagem desviada para compras-com-erro."],
                    cor="attention",
                )
            continue

        # ---- agregacao da fatura ----
        fid = dados.get("fatura_id")
        if not fid:
            continue
        acumulado[fid].append(dados)
        primeira_linha_em.setdefault(fid, time.time())

        if len(acumulado[fid]) >= dados.get("total_de_linhas", 10**6):
            enviar(*card_da_fatura(fid, acumulado[fid]))
            acumulado.pop(fid, None)
            primeira_linha_em.pop(fid, None)


if __name__ == "__main__":
    main()
