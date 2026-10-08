# -*- coding: utf-8 -*-
"""
Gera as faturas SINTETICAS do laboratorio, em dois layouts diferentes.

Por que dois layouts: num pipeline de eventos real as fontes nunca sao
homogeneas. Cada banco publica o PDF do seu jeito, e quem normaliza isso
e o consumidor_analisa_faturas -- que publica tudo no mesmo contrato de
evento. Esse e o conceito de "contrato como normalizador".

  BANCO AZUL   -> uma coluna, com coluna de Pais e com os grupos grossos
                  que o proprio banco atribui. Parsing direto por linha.
  BANCO VERDE  -> DUAS colunas lado a lado, varios portadores de cartao.
                  extract_text() embaralha as duas colunas, entao o parser
                  e obrigado a usar a posicao X de cada palavra.

Nenhum dado real: ver faturas/comerciantes.py.

Uso:  python gerar_faturas.py [pasta_saida] [--variacoes N]
"""
import os
import random
import sys
from datetime import date, timedelta

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from comerciantes import COMERCIANTES

LARGURA, ALTURA = A4

# Grupos grossos que o BANCO AZUL imprime na propria fatura. Sao de
# proposito pouco uteis para orcamento pessoal -- e o contraste com a
# categoria que o pipeline gera depois.
GRUPOS_DO_BANCO = {
    "Viagem": "Companhias aereas e turismo",
    "Transporte": "Compras diversas",
    "Alimentacao": "Compras diversas",
    "Mercado": "Compras diversas",
    "Saude": "Compras diversas",
    "Assinaturas": "Debitos diversos",
    "Compras": "Compras diversas",
    "Educacao": "Compras diversas",
    "Casa": "Debitos diversos",
    "Pets": "Compras diversas",
    "Outros": "Compras diversas",
}


def arte_do_banco(semente, lado):
    """Gera o "logo" e o QR Code de pagamento da fatura, como imagem.

    Isto nao e enfeite: uma fatura de banco real pesa centenas de KB
    justamente porque carrega logo, QR Code do Pix e fontes embutidas.
    Um PDF sintetico de 5 KB tornaria a licao do claim-check falsa -- o
    aluno veria que o arquivo caberia folgado dentro de uma mensagem.

    Com arte de verdade, a fatura_banco_verde passa de 1 MB, que e o
    limite DEFAULT de mensagem do Kafka. Dai o padrao claim-check deixa
    de ser teoria: tentar publicar o PDF como mensagem falha de fato.
    """
    from PIL import Image

    rnd = random.Random(semente)
    img = Image.new("RGB", (lado, lado))
    pixels = img.load()

    # Degrade com ruido: incompressivel o suficiente para o PNG ficar
    # com o peso de uma imagem real, e deterministico pela semente.
    for y in range(lado):
        for x in range(lado):
            base = int(255 * (x + y) / (2 * lado))
            pixels[x, y] = (
                max(0, min(255, base + rnd.randint(-28, 28))),
                max(0, min(255, int(base * 0.6) + rnd.randint(-28, 28))),
                max(0, min(255, 255 - base + rnd.randint(-28, 28))),
            )
    return img


def qr_falso(semente, modulos=33):
    """Desenha um padrao quadriculado no estilo de um QR Code.

    NAO e um QR Code valido e nao codifica nada -- e so a mancha visual
    que uma fatura de Pix tem. Nenhum leitor consegue interpretar.
    """
    from PIL import Image

    rnd = random.Random(semente)
    img = Image.new("RGB", (modulos, modulos), "white")
    pixels = img.load()
    for y in range(modulos):
        for x in range(modulos):
            if rnd.random() < 0.45:
                pixels[x, y] = (0, 0, 0)
    # Os tres quadrados de orientacao, que dao o aspecto caracteristico.
    for cx, cy in [(0, 0), (modulos - 7, 0), (0, modulos - 7)]:
        for y in range(7):
            for x in range(7):
                borda = x in (0, 6) or y in (0, 6)
                centro = 2 <= x <= 4 and 2 <= y <= 4
                pixels[cx + x, cy + y] = (0, 0, 0) if (borda or centro) else (255, 255, 255)
    return img.resize((modulos * 8, modulos * 8), Image.NEAREST)


def dinheiro(v):
    """1234.5 -> '1.234,50' (formato brasileiro)."""
    s = f"{v:,.2f}"
    return s.replace(",", "@").replace(".", ",").replace("@", ".")


def sortear_lancamentos(rnd, quantidade, inicio):
    """Sorteia lancamentos a partir do catalogo sintetico."""
    itens = []
    for _ in range(quantidade):
        desc, categoria, cidade = rnd.choice(COMERCIANTES)

        # Faixa de valor coerente com a categoria: evita supermercado de
        # R$ 3,00 e assinatura de R$ 900,00, o que atrapalharia a leitura
        # do resultado pelo aluno.
        faixa = {
            "Assinaturas": (9.90, 69.90),
            "Transporte": (6.00, 220.00),
            "Alimentacao": (18.00, 260.00),
            "Mercado": (45.00, 680.00),
            "Saude": (25.00, 420.00),
            "Compras": (30.00, 900.00),
            "Educacao": (120.00, 1800.00),
            "Viagem": (280.00, 3200.00),
            "Casa": (80.00, 640.00),
            "Pets": (35.00, 310.00),
            "Outros": (20.00, 380.00),
        }[categoria]
        valor = round(rnd.uniform(*faixa), 2)

        dia = inicio + timedelta(days=rnd.randint(0, 29))

        # ~18% das compras sao parceladas.
        parcela = None
        if rnd.random() < 0.18:
            total = rnd.choice([3, 4, 6, 10, 12])
            parcela = (rnd.randint(1, total), total)

        itens.append(
            {
                "data": dia,
                "descricao": desc,
                "cidade": cidade,
                "categoria_real": categoria,
                "valor": valor,
                "parcela": parcela,
            }
        )
    itens.sort(key=lambda i: i["data"])
    return itens


# ----------------------------------------------------------------------
# BANCO AZUL : uma coluna, com Pais, agrupado pelos grupos do banco
# ----------------------------------------------------------------------
def fatura_banco_azul(caminho, rnd, competencia, lancamentos):
    c = canvas.Canvas(caminho, pagesize=A4)
    venc = competencia.replace(day=15)

    total = sum(i["valor"] for i in lancamentos)
    pago_anterior = round(total * rnd.uniform(0.8, 1.1), 2)

    # ---------- pagina 1: resumo ----------
    y = ALTURA - 25 * mm
    c.setFont("Helvetica-Bold", 15)
    c.drawString(20 * mm, y, "BANCO AZUL")
    c.drawImage(ImageReader(arte_do_banco(101, 430)), 20 * mm, y - 3 * mm,
                width=13 * mm, height=13 * mm, mask=None)
    c.setFont("Helvetica-Bold", 15)
    c.drawString(36 * mm, y, "")
    c.setFont("Helvetica", 9)
    c.drawRightString(LARGURA - 20 * mm, y, "AZULCARD VISA PLATINUM  Final 4321")

    y -= 12 * mm
    c.setFont("Helvetica-Bold", 12)
    c.drawString(20 * mm, y, "Ola, ALUNO FIAP, esta e a sua fatura de")
    c.setFont("Helvetica-Bold", 12)
    c.drawString(20 * mm, y - 6 * mm, competencia.strftime("%B/%Y").upper())

    y -= 20 * mm
    c.setFont("Helvetica-Bold", 10)
    c.drawString(20 * mm, y, "Resumo da fatura")
    c.setFont("Helvetica", 9)
    linhas_resumo = [
        ("Saldo fatura anterior", pago_anterior),
        ("Pagamentos/Creditos", -pago_anterior),
        ("Compras nacionais", total),
        ("Tarifas, encargos e multas", 0.00),
    ]
    for rotulo, valor in linhas_resumo:
        y -= 5.5 * mm
        c.drawString(22 * mm, y, rotulo)
        sufixo = "-" if valor < 0 else ""
        c.drawRightString(LARGURA - 20 * mm, y, f"R$ {dinheiro(abs(valor))}{sufixo}")

    y -= 8 * mm
    c.setFont("Helvetica-Bold", 11)
    c.drawString(22 * mm, y, "Total")
    c.drawRightString(LARGURA - 20 * mm, y, f"R$ {dinheiro(total)}")

    y -= 12 * mm
    c.setFont("Helvetica", 9)
    c.drawString(20 * mm, y, f"Vencimento  {venc.strftime('%d/%m/%Y')}")
    c.drawString(20 * mm, y - 5 * mm, "Limite unico  R$ 30.000,00")
    c.drawString(20 * mm, y - 10 * mm, "Esta fatura esta em Debito em conta")

    y -= 14 * mm
    c.setFont("Helvetica-Bold", 9)
    c.drawString(20 * mm, y, "Pague com Pix")
    c.drawImage(ImageReader(qr_falso(7001)), 20 * mm, y - 32 * mm,
                width=28 * mm, height=28 * mm)
    c.setFont("Helvetica", 7)
    c.drawString(52 * mm, y - 10 * mm, "QR Code meramente ilustrativo: nao codifica dados.")

    y -= 42 * mm
    c.setFont("Helvetica-Oblique", 7.5)
    for texto in [
        "Este documento e SINTETICO e foi gerado para uso didatico no laboratorio FIAP.",
        "Titular, cartao, valores e estabelecimentos sao ficticios.",
    ]:
        c.drawString(20 * mm, y, texto)
        y -= 4 * mm

    c.setFont("Helvetica", 7)
    c.drawRightString(LARGURA - 20 * mm, 12 * mm, "Pagina 1/2")
    c.showPage()

    # ---------- pagina 2: lancamentos ----------
    y = ALTURA - 20 * mm
    c.setFont("Helvetica-Bold", 11)
    c.drawString(20 * mm, y, "Lancamentos nesta fatura")
    y -= 8 * mm
    c.setFont("Helvetica", 8.5)
    c.drawString(20 * mm, y, "ALUNO FIAP (Cartao 4321)")

    def cabecalho_colunas(yy):
        c.setFont("Helvetica-Bold", 8.5)
        c.drawString(20 * mm, yy, "Data")
        c.drawString(34 * mm, yy, "Descricao")
        c.drawString(140 * mm, yy, "Pais")
        c.drawRightString(LARGURA - 20 * mm, yy, "Valor")
        return yy - 5 * mm

    y -= 7 * mm
    y = cabecalho_colunas(y)

    # Pagamento do mes anterior aparece como credito (valor com "-").
    c.setFont("Helvetica-Bold", 8.5)
    c.drawString(20 * mm, y, "Pagamentos")
    y -= 5 * mm
    c.setFont("Helvetica", 8.5)
    c.drawString(20 * mm, y, competencia.strftime("%d/%m")[:5])
    c.drawString(34 * mm, y, "PGTO DEBITO CONTA 4321")
    c.drawRightString(LARGURA - 20 * mm, y, f"R$ {dinheiro(pago_anterior)}-")
    y -= 7 * mm

    # Agrupa pelos grupos grossos do banco.
    por_grupo = {}
    for i in lancamentos:
        por_grupo.setdefault(GRUPOS_DO_BANCO[i["categoria_real"]], []).append(i)

    for grupo, itens in sorted(por_grupo.items()):
        if y < 35 * mm:
            c.setFont("Helvetica", 7)
            c.drawRightString(LARGURA - 20 * mm, 12 * mm, "Pagina 2/2")
            c.showPage()
            y = ALTURA - 20 * mm
            y = cabecalho_colunas(y)

        c.setFont("Helvetica-Bold", 8.5)
        c.drawString(20 * mm, y, grupo)
        y -= 5 * mm

        c.setFont("Helvetica", 8.5)
        for i in itens:
            if y < 30 * mm:
                c.setFont("Helvetica", 7)
                c.drawRightString(LARGURA - 20 * mm, 12 * mm, "Pagina 2/2")
                c.showPage()
                y = ALTURA - 20 * mm
                y = cabecalho_colunas(y)
                c.setFont("Helvetica", 8.5)

            desc = i["descricao"]
            if i["parcela"]:
                desc = f"{desc} PARC {i['parcela'][0]:02d}/{i['parcela'][1]:02d}"
            c.drawString(20 * mm, y, i["data"].strftime("%d/%m"))
            c.drawString(34 * mm, y, f"{desc} {i['cidade']}"[:72])
            c.drawString(140 * mm, y, "BR")
            c.drawRightString(LARGURA - 20 * mm, y, f"R$ {dinheiro(i['valor'])}")
            y -= 4.6 * mm

    y -= 4 * mm
    c.setFont("Helvetica-Bold", 9)
    c.drawString(20 * mm, y, "Total")
    c.drawRightString(LARGURA - 20 * mm, y, f"R$ {dinheiro(total)}")

    # ARMADILHA DIDATICA: esta secao repete compras que serao cobradas em
    # faturas FUTURAS. Somar estas linhas infla o total. O parser tem de
    # parar aqui -- e o aluno descobre isso comparando com o "Total".
    y -= 10 * mm
    c.setFont("Helvetica-Bold", 9)
    c.drawString(20 * mm, y, "Parcelamentos Proxima Fatura")
    y -= 5.5 * mm
    c.setFont("Helvetica", 8.5)
    futuros = [i for i in lancamentos if i["parcela"] and i["parcela"][0] < i["parcela"][1]][:5]
    for i in futuros:
        prox = i["parcela"][0] + 1
        c.drawString(20 * mm, y, i["data"].strftime("%d/%m"))
        c.drawString(
            34 * mm, y,
            f"{i['descricao']} PARC {prox:02d}/{i['parcela'][1]:02d} {i['cidade']}"[:72],
        )
        c.drawRightString(LARGURA - 20 * mm, y, f"R$ {dinheiro(i['valor'])}")
        y -= 4.6 * mm

    c.setFont("Helvetica", 7)
    c.drawRightString(LARGURA - 20 * mm, 12 * mm, "Pagina 2/2")
    c.save()
    return total


# ----------------------------------------------------------------------
# BANCO VERDE : DUAS colunas lado a lado, varios portadores
# ----------------------------------------------------------------------
def fatura_banco_verde(caminho, rnd, competencia, lancamentos):
    c = canvas.Canvas(caminho, pagesize=A4)
    venc = competencia.replace(day=10)
    total = sum(i["valor"] for i in lancamentos)

    # Dois portadores: o titular e um cartao adicional. Obriga o parser a
    # entender que a fatura tem blocos por portador.
    corte = int(len(lancamentos) * 0.72)
    portadores = [
        ("ALUNO FIAP", "4455 XXXX XXXX 1098", lancamentos[:corte]),
        ("DEPENDENTE FIAP", "4455 XXXX XXXX 2077", lancamentos[corte:]),
    ]

    # ---------- pagina 1: resumo ----------
    y = ALTURA - 22 * mm
    c.drawImage(ImageReader(arte_do_banco(202, 760)), LARGURA - 36 * mm, y - 4 * mm,
                width=16 * mm, height=16 * mm, mask=None)
    c.setFont("Helvetica-Bold", 15)
    c.drawString(20 * mm, y, "BANCO VERDE")
    y -= 10 * mm
    c.setFont("Helvetica", 10)
    c.drawString(
        20 * mm, y,
        "Ola, ALUNO FIAP! Esta e a fatura do seu cartao VERDE UNIQUE MASTERCARD",
    )
    y -= 5 * mm
    c.drawString(
        20 * mm, y,
        f"contendo compras e pagamentos realizados ate {competencia.strftime('%d/%m')}.",
    )

    y -= 14 * mm
    c.setFont("Helvetica-Bold", 9)
    for rotulo, valor, dx in [
        ("Total a Pagar", f"R$ {dinheiro(total)}", 0),
        ("Vencimento", venc.strftime("%d/%m/%Y"), 60),
        ("Seu limite e", "R$ 45.000,00", 120),
    ]:
        c.setFont("Helvetica", 8)
        c.drawString(20 * mm + dx * mm, y, rotulo)
        c.setFont("Helvetica-Bold", 11)
        c.drawString(20 * mm + dx * mm, y - 6 * mm, valor)

    y -= 22 * mm
    c.setFont("Helvetica-Bold", 9)
    c.drawString(20 * mm, y, "Opcoes de Pagamento ate a Data de Vencimento")
    y -= 6 * mm
    c.setFont("Helvetica", 8.5)
    c.drawString(22 * mm, y, "1 Pagamento Total")
    c.drawRightString(LARGURA - 20 * mm, y, f"R$ {dinheiro(total)}")
    y -= 5 * mm
    c.drawString(22 * mm, y, "2 Pagamento Minimo")
    c.drawRightString(LARGURA - 20 * mm, y, f"R$ {dinheiro(round(total * 0.15, 2))}")

    y -= 12 * mm
    c.setFont("Helvetica-Bold", 9)
    c.drawString(20 * mm, y, "Pague com Pix")
    c.drawImage(ImageReader(qr_falso(7002, 37)), 20 * mm, y - 34 * mm,
                width=30 * mm, height=30 * mm)
    c.drawImage(ImageReader(arte_do_banco(203, 700)), 60 * mm, y - 34 * mm,
                width=30 * mm, height=30 * mm, mask=None)

    y -= 44 * mm
    c.setFont("Helvetica-Oblique", 7.5)
    for texto in [
        "Este documento e SINTETICO e foi gerado para uso didatico no laboratorio FIAP.",
        "Titular, cartao, valores e estabelecimentos sao ficticios.",
    ]:
        c.drawString(20 * mm, y, texto)
        y -= 4 * mm

    c.setFont("Helvetica", 7)
    c.drawRightString(LARGURA - 20 * mm, 12 * mm, "1/2")
    c.showPage()

    # ---------- pagina 2+: detalhamento em DUAS colunas ----------
    COL_X = [20 * mm, 108 * mm]   # origem de cada coluna
    TOPO = ALTURA - 28 * mm
    BASE = 22 * mm

    c.setFont("Helvetica-Bold", 11)
    c.drawString(20 * mm, ALTURA - 20 * mm, "Detalhamento da Fatura")

    estado = {"col": 0, "y": TOPO, "pagina": 2}

    def nova_pagina():
        c.setFont("Helvetica", 7)
        c.drawRightString(LARGURA - 20 * mm, 12 * mm, f"{estado['pagina']}/2")
        c.showPage()
        estado["pagina"] += 1
        c.setFont("Helvetica-Bold", 11)
        c.drawString(20 * mm, ALTURA - 20 * mm, "Detalhamento da Fatura")
        estado["col"] = 0
        estado["y"] = TOPO

    def avancar(altura):
        """Desce na coluna; ao chegar no pe, pula para a coluna da direita."""
        estado["y"] -= altura
        if estado["y"] < BASE:
            if estado["col"] == 0:
                estado["col"] = 1
                estado["y"] = TOPO
            else:
                nova_pagina()

    def x():
        return COL_X[estado["col"]]

    def escrever_cabecalho_secao(titulo):
        avancar(6 * mm)
        c.setFont("Helvetica-Bold", 8.5)
        c.drawString(x(), estado["y"], titulo)
        avancar(4.5 * mm)
        c.setFont("Helvetica-Bold", 7)
        c.drawString(x(), estado["y"], "Compra Data Descricao")
        c.drawString(x() + 62 * mm, estado["y"], "Parcela")
        c.drawRightString(x() + 80 * mm, estado["y"], "R$")
        avancar(4.2 * mm)

    def escrever_lancamento(item, credito=False):
        c.setFont("Helvetica", 7.5)
        c.drawString(x(), estado["y"], "1")
        c.drawString(x() + 5 * mm, estado["y"], item["data"].strftime("%d/%m"))
        c.drawString(x() + 16 * mm, estado["y"], item["descricao"][:26])
        if item["parcela"]:
            c.drawString(
                x() + 62 * mm, estado["y"],
                f"{item['parcela'][0]:02d}/{item['parcela'][1]:02d}",
            )
        sinal = "-" if credito else ""
        c.drawRightString(x() + 80 * mm, estado["y"], f"{sinal}{dinheiro(item['valor'])}")
        avancar(4.0 * mm)

    for nome, cartao, itens in portadores:
        avancar(5 * mm)
        c.setFont("Helvetica-Bold", 8.5)
        c.drawString(x(), estado["y"], f"{nome} - {cartao}")
        avancar(4.5 * mm)

        if nome == "ALUNO FIAP":
            escrever_cabecalho_secao("Pagamento e Demais Creditos")
            escrever_lancamento(
                {
                    "data": competencia,
                    "descricao": "DEB AUTOM DE FATURA EM C/C",
                    "valor": round(total * 0.9, 2),
                    "parcela": None,
                },
                credito=True,
            )

        parceladas = [i for i in itens if i["parcela"]]
        avista = [i for i in itens if not i["parcela"]]

        if parceladas:
            escrever_cabecalho_secao("Parcelamentos")
            for i in parceladas:
                escrever_lancamento(i)

        if avista:
            escrever_cabecalho_secao("Despesas")
            for i in avista:
                escrever_lancamento(i)

    avancar(5 * mm)
    c.setFont("Helvetica-Bold", 8)
    c.drawString(x(), estado["y"], "VALOR TOTAL")
    c.drawRightString(x() + 80 * mm, estado["y"], dinheiro(total))

    c.setFont("Helvetica", 7)
    c.drawRightString(LARGURA - 20 * mm, 12 * mm, f"{estado['pagina']}/2")
    c.save()
    return total


def main():
    saida = sys.argv[1] if len(sys.argv) > 1 else "."
    variacoes = 0
    if "--variacoes" in sys.argv:
        variacoes = int(sys.argv[sys.argv.index("--variacoes") + 1])
    os.makedirs(saida, exist_ok=True)

    competencia = date(2026, 9, 1)

    # As duas faturas do repositorio usam semente fixa: todo aluno abre
    # exatamente o mesmo PDF, entao o resultado da aula e comparavel.
    rnd = random.Random(20260901)
    azul = sortear_lancamentos(rnd, 34, competencia)
    t1 = fatura_banco_azul(os.path.join(saida, "fatura_banco_azul.pdf"), rnd, competencia, azul)
    print(f"fatura_banco_azul.pdf      {len(azul):3d} lancamentos  total R$ {dinheiro(t1)}")

    rnd = random.Random(20260902)
    verde = sortear_lancamentos(rnd, 46, competencia)
    t2 = fatura_banco_verde(os.path.join(saida, "fatura_banco_verde.pdf"), rnd, competencia, verde)
    print(f"fatura_banco_verde.pdf     {len(verde):3d} lancamentos  total R$ {dinheiro(t2)}")

    print()
    LIMITE_KAFKA = 1048576
    for nome in ("fatura_banco_azul.pdf", "fatura_banco_verde.pdf"):
        tam = os.path.getsize(os.path.join(saida, nome))
        marca = "ACIMA do limite de 1 MB do Kafka" if tam > LIMITE_KAFKA else "cabe numa mensagem"
        print(f"  {nome:26s} {tam/1024:8.0f} KB   ({marca})")

    # Variacoes servem ao modulo de carga (./lab.sh tempestade), onde o
    # objetivo e gerar lag -- nao precisam ser deterministicas.
    for n in range(1, variacoes + 1):
        rnd = random.Random(90000 + n)
        comp = competencia - timedelta(days=30 * n)
        banco = "azul" if n % 2 else "verde"
        nome = f"fatura_banco_{banco}_{comp.strftime('%Y%m')}.pdf"
        itens = sortear_lancamentos(rnd, rnd.randint(30, 50), comp)
        fn = fatura_banco_azul if banco == "azul" else fatura_banco_verde
        t = fn(os.path.join(saida, nome), rnd, comp, itens)
        print(f"{nome:40s} {len(itens):3d} lancamentos  total R$ {dinheiro(t)}")


if __name__ == "__main__":
    main()
