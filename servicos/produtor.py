# -*- coding: utf-8 -*-
"""
produtor_envia_fatura : onde o evento nasce.

Aplica o padrao CLAIM-CHECK (ficha de bagagem):

  o PDF             -> vai para o S3 (SeaweedFS)  (a mala, grande)
  o evento no Kafka -> leva apenas a referencia  (a ficha, pequena)

Por que isso importa: o limite default de mensagem do Kafka e 1 MB, e a
fatura passa disso. Mais ainda, um broker de eventos nao e um sistema de
arquivos -- empurrar binario grande por ele degrada todo o cluster.
"""
import enum
import os
import tempfile
import uuid
from datetime import datetime, timezone

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import RedirectResponse

import comum
import parsers


class Banco(str, enum.Enum):
    """Enum, e nao texto livre, de proposito.

    Em campo de texto opcional o Swagger UI preenche "string" sozinho, e
    quem clica em Execute sem editar envia esse valor. Com enum, o
    Swagger desenha um menu suspenso e a armadilha desaparece.
    """

    detectar = "detectar"
    azul = "azul"
    verde = "verde"

registro = comum.log("produtor")

PASTA_EXEMPLOS = "/app/faturas_exemplo"
ALUNO = os.environ.get("ALUNO", "aluno-sem-nome")

app = FastAPI(
    title="Produtor de Faturas",
    description=(
        "Publica eventos no Kafka. O PDF vai para o MinIO; o evento leva "
        "somente a URL (padrao claim-check)."
    ),
    version="1.0",
)

produtor = comum.produtor_kafka()
s3 = comum.cliente_s3()


@app.get("/", include_in_schema=False)
def raiz():
    return RedirectResponse("/docs")


@app.post("/mensagem", tags=["1. O basico"])
def enviar_mensagem(texto: str = Form(..., description="Qualquer texto")):
    """MODULO 1: o caminho mais curto possivel.

    Publica um texto no topico `lab-mensagens`. Depois procure a
    mensagem no Console (porta 8080) e veja o card chegar no Teams.
    Nenhum arquivo, nenhuma IA: so produtor -> topico -> consumidor.
    """
    evento = {
        "aluno": ALUNO,
        "texto": texto,
        "enviado_em": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    comum.publicar(produtor, comum.TOPICO_MENSAGENS, evento, chave=ALUNO)
    produtor.flush(10)
    registro.info("mensagem publicada: %s", texto[:60])
    return {"publicado_em": comum.TOPICO_MENSAGENS, "evento": evento}


def _publicar_fatura(conteudo: bytes, nome_arquivo: str, banco: str | None):
    if not conteudo[:4] == b"%PDF":
        raise HTTPException(400, "o arquivo nao parece ser um PDF")

    # VALIDA ANTES DE GRAVAR.
    #
    # A ordem aqui importa mais do que parece. Antes, o arquivo era
    # gravado primeiro e so depois o consumidor descobria que o layout
    # era desconhecido -- entao uma fatura REAL enviada por engano ja
    # tinha ido para o armazenamento, que neste lab e publico. Validando
    # primeiro, o arquivo errado nunca chega a ser guardado.
    if banco == Banco.detectar.value:
        banco = None

    with tempfile.NamedTemporaryFile(suffix=".pdf") as tmp:
        tmp.write(conteudo)
        tmp.flush()
        try:
            banco, _ = parsers.ler_fatura(tmp.name, banco)
        except parsers.LayoutDesconhecido as e:
            registro.warning(
                "recusado antes de gravar: %s (%d KB)", nome_arquivo, len(conteudo) // 1024
            )
            raise HTTPException(422, str(e))
        except Exception as e:
            raise HTTPException(422, f"nao consegui ler este PDF: {e}")

    fatura_id = uuid.uuid4().hex[:12]
    objeto = f"{ALUNO}/{fatura_id}.pdf"

    # 1) a "mala" vai para o armazenamento de objetos
    s3.put_object(
        Bucket=comum.BUCKET,
        Key=objeto,
        Body=conteudo,
        ContentType="application/pdf",
    )

    # 2) a "ficha" vai para o Kafka -- poucas centenas de bytes
    evento = {
        "fatura_id": fatura_id,
        "aluno": ALUNO,
        "banco": banco,
        "arquivo_original": nome_arquivo,
        "bucket": comum.BUCKET,
        "objeto": objeto,
        "url_s3": f"s3://{comum.BUCKET}/{objeto}",
        "tamanho_bytes": len(conteudo),
        "recebido_em": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    comum.publicar(produtor, comum.TOPICO_FATURAS, evento, chave=fatura_id)
    produtor.flush(10)

    registro.info(
        "fatura %s: PDF de %d KB no S3, evento de %d bytes no Kafka",
        fatura_id,
        len(conteudo) // 1024,
        len(str(evento)),
    )
    return evento


@app.post("/faturas", tags=["2. Faturas"])
async def enviar_fatura(
    arquivo: UploadFile = File(..., description="PDF da fatura sintetica do lab"),
    banco: Banco = Form(Banco.detectar, description="deixe em 'detectar' se nao souber"),
):
    """Sobe um PDF de fatura: arquivo para o S3, evento para o Kafka.

    **Envie apenas as faturas sinteticas do laboratorio**, que estao em
    `faturas/` no repositorio. NAO envie faturas reais: elas contem
    dados pessoais e financeiros, e o armazenamento deste laboratorio
    fica acessivel a quem alcancar a VM.

    PDFs que nao sejam uma das faturas do lab sao recusados aqui mesmo,
    antes de serem gravados.

    Compare no Console o tamanho do evento com o tamanho do PDF no S3:
    e a essencia do claim-check.
    """
    return _publicar_fatura(
        await arquivo.read(), arquivo.filename or "fatura.pdf", banco.value
    )


@app.post("/faturas/exemplo", tags=["2. Faturas"])
def enviar_fatura_exemplo(banco: Banco = Form(Banco.azul, description="qual fatura do lab enviar")):
    """Atalho: usa uma das faturas sinteticas que ja vem no repositorio.

    Serve para disparar o pipeline com um clique, sem precisar procurar
    arquivo no disco.
    """
    if banco == Banco.detectar:
        banco = Banco.azul

    caminho = os.path.join(PASTA_EXEMPLOS, f"fatura_banco_{banco.value}.pdf")
    if not os.path.exists(caminho):
        raise HTTPException(404, f"fatura de exemplo nao encontrada: {caminho}")

    with open(caminho, "rb") as f:
        return _publicar_fatura(f.read(), os.path.basename(caminho), banco)


@app.get("/saude", tags=["3. Diagnostico"])
def saude():
    return {
        "servico": "produtor_envia_fatura",
        "aluno": ALUNO,
        "broker": os.environ.get("KAFKA_BROKER"),
        "exemplos": sorted(os.listdir(PASTA_EXEMPLOS))
        if os.path.isdir(PASTA_EXEMPLOS)
        else [],
    }
