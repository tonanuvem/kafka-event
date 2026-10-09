# -*- coding: utf-8 -*-
"""
Leitura das faturas em PDF. Um parser por banco.

A licao desta etapa: fontes heterogeneas, contrato unico. Os dois
parsers devolvem exatamente a mesma estrutura de compra, e e isso que
permite ao resto do pipeline ignorar de qual banco veio o arquivo.

  BANCO AZUL  : uma coluna. extract_text() basta.
  BANCO VERDE : DUAS colunas lado a lado. extract_text() INTERCALA as
                colunas e produz lixo (duas compras na mesma linha de
                texto). O parser e obrigado a usar a posicao X de cada
                palavra para separar as colunas antes de ler.
"""
import re

import pdfplumber

# "1.234,56" -> 1234.56
VALOR = re.compile(r"^-?\d{1,3}(?:\.\d{3})*,\d{2}-?$")
DATA = re.compile(r"^\d{2}/\d{2}$")
PARCELA = re.compile(r"\b(?:PARC\s+)?(\d{2})/(\d{2})\b")


def para_numero(texto):
    negativo = texto.startswith("-") or texto.endswith("-")
    limpo = texto.strip("-").replace(".", "").replace(",", ".")
    valor = float(limpo)
    return -valor if negativo else valor


def _compra(data, descricao, valor, parcela=None, origem_secao=None):
    """O CONTRATO. Os dois parsers devolvem este mesmo dicionario."""
    descricao_limpa = PARCELA.sub("", descricao).strip()
    descricao_limpa = re.sub(r"\s{2,}", " ", descricao_limpa)
    return {
        "data": data,
        "descricao": descricao_limpa,
        "descricao_bruta": descricao,
        "valor": round(abs(valor), 2),
        "tipo": "credito" if valor < 0 else "compra",
        "parcela": parcela,
        "secao_no_pdf": origem_secao,
    }


# ----------------------------------------------------------------------
# BANCO AZUL : uma coluna
# ----------------------------------------------------------------------
# 10/09 AF INTERNET PASSAGEM Sao Paulo BR R$ 1.846,68
LINHA_AZUL = re.compile(
    r"^(\d{2}/\d{2})\s+(.+?)\s+(BR|US|CA|AR|PT)\s+R\$\s+(-?[\d.,]+-?)$"
)
# 01/09 PGTO DEBITO CONTA 4321 R$ 10.346,13-   (credito, sem coluna Pais)
LINHA_AZUL_SEM_PAIS = re.compile(r"^(\d{2}/\d{2})\s+(.+?)\s+R\$\s+(-?[\d.,]+-?)$")


def ler_banco_azul(caminho):
    compras = []
    secao = None

    with pdfplumber.open(caminho) as pdf:
        for pagina in pdf.pages:
            for linha in (pagina.extract_text() or "").split("\n"):
                linha = linha.strip()
                if not linha:
                    continue

                # ARMADILHA: daqui para baixo o PDF repete compras que
                # serao cobradas em faturas FUTURAS. Somar isso infla o
                # total da fatura. O parser tem de parar aqui.
                if linha.startswith("Parcelamentos Proxima Fatura"):
                    return compras

                if linha.startswith(("Total", "Subtotal", "Data Descricao", "Pagina")):
                    continue

                achou = LINHA_AZUL.match(linha) or LINHA_AZUL_SEM_PAIS.match(linha)
                if not achou:
                    # Linha que nao casa e cabecalho do grupo que o
                    # proprio banco atribuiu ("Compras diversas").
                    if len(linha) < 48 and not linha[0].isdigit():
                        secao = linha
                    continue

                grupos = achou.groups()
                data, descricao, valor = grupos[0], grupos[1], grupos[-1]
                parcela = None
                if m := PARCELA.search(descricao):
                    parcela = f"{m.group(1)}/{m.group(2)}"

                compras.append(_compra(data, descricao, para_numero(valor), parcela, secao))

    return compras


# ----------------------------------------------------------------------
# BANCO VERDE : duas colunas -> precisa de posicao
# ----------------------------------------------------------------------
LIMITE_COLUNAS = 295  # pontos; a coluna da direita comeca em ~306

TITULAR = re.compile(r"^([A-Z][A-Z\s]+?)\s+-\s+\d{4}\s+XXXX\s+XXXX\s+(\d{4})$")
SECOES_VERDE = ("Pagamento e Demais Creditos", "Parcelamentos", "Despesas")


def _linhas_por_coluna(pagina, tolerancia=2.0):
    """Reconstroi o texto de cada coluna separadamente.

    E aqui que mora a diferenca em relacao ao Banco Azul: agrupamos as
    palavras por altura (top) E por coluna (x0), em vez de confiar na
    linha de texto que o extrator devolve.
    """
    esquerda, direita = {}, {}

    for p in pagina.extract_words(use_text_flow=False):
        # Arredonda a altura para juntar palavras da mesma linha visual.
        chave = round(p["top"] / tolerancia)
        destino = esquerda if p["x0"] < LIMITE_COLUNAS else direita
        destino.setdefault(chave, []).append(p)

    def montar(bloco):
        saida = []
        for chave in sorted(bloco):
            palavras = sorted(bloco[chave], key=lambda w: w["x0"])
            saida.append(" ".join(w["text"] for w in palavras).strip())
        return saida

    # Coluna da esquerda inteira, depois a da direita: e a ordem de
    # leitura humana da fatura.
    return montar(esquerda) + montar(direita)


def ler_banco_verde(caminho):
    compras = []
    titular = None
    secao = None

    with pdfplumber.open(caminho) as pdf:
        for pagina in pdf.pages:
            for linha in _linhas_por_coluna(pagina):
                linha = linha.strip()
                if not linha or linha.startswith(("Detalhamento", "Compra Data")):
                    continue
                if linha.startswith("VALOR TOTAL"):
                    continue

                if m := TITULAR.match(linha):
                    titular = m.group(1).strip()
                    continue
                if linha in SECOES_VERDE:
                    secao = linha
                    continue

                # 1 05/09 EBN*HOSTINGER WEB 02/04 66,54
                partes = linha.split()
                if len(partes) < 4 or partes[0] != "1" or not DATA.match(partes[1]):
                    continue
                if not VALOR.match(partes[-1]):
                    continue

                data = partes[1]
                valor = para_numero(partes[-1])
                meio = partes[2:-1]

                parcela = None
                if meio and DATA.match(meio[-1]):
                    parcela = meio.pop()

                descricao = " ".join(meio)
                if not descricao:
                    continue

                compra = _compra(data, descricao, valor, parcela, secao)
                compra["titular"] = titular
                compras.append(compra)

    return compras


LEITORES = {"azul": ler_banco_azul, "verde": ler_banco_verde}


def detectar_banco(caminho):
    """Descobre o banco pelo conteudo, para o aluno nao precisar informar."""
    with pdfplumber.open(caminho) as pdf:
        cabecalho = (pdf.pages[0].extract_text() or "").upper()
    if "BANCO VERDE" in cabecalho:
        return "verde"
    if "BANCO AZUL" in cabecalho:
        return "azul"
    return None


class LayoutDesconhecido(ValueError):
    """O PDF nao e uma das faturas sinteticas do laboratorio."""


def ler_fatura(caminho, banco=None):
    """Le a fatura, detectando o layout quando preciso.

    Repare no `not in LEITORES` em vez de `banco or ...`: um valor
    informado mas INVALIDO tem de cair na deteccao tambem. A versao
    anterior usava `banco or detectar_banco(...)`, entao um banco
    invalido porem "verdadeiro" pulava a deteccao. Foi exatamente o que
    aconteceu com o valor "string", que o Swagger UI preenche sozinho
    em campo de texto opcional.
    """
    if banco not in LEITORES:
        banco = detectar_banco(caminho)

    if banco not in LEITORES:
        raise LayoutDesconhecido(
            "Este PDF nao e uma das faturas sinteticas do laboratorio. "
            "O lab reconhece apenas fatura_banco_azul.pdf e "
            "fatura_banco_verde.pdf, que estao em faturas/ no repositorio. "
            "NAO envie faturas reais: elas contem dados pessoais e "
            "financeiros, e o armazenamento deste laboratorio e aberto."
        )
    return banco, LEITORES[banco](caminho)
