# kafka-event — Analisador Inteligente de Faturas

Laboratório de **comunicação baseada em eventos** com Apache Kafka, para o
MBA da FIAP. O aluno sobe um pipeline que recebe faturas de cartão de
crédito em PDF, extrai as compras, categoriza os gastos com busca vetorial
e um LLM local, e publica um resumo no Microsoft Teams.

```
                                   ┌──── busca vetorial ────┐
                                   │  Qdrant (comerciantes) │
                                   └───────────┬────────────┘
 upload do PDF        ┌─ S3 ─┐                 │
       │              │ o PDF│◀── guarda ──┐   │
       ▼              └──────┘             │   │
 produtor_envia_fatura ──── só a URL ───────┘   │
       │                                        │
       ▼                                        │
 faturas-recebidas ──▶ consumidor_analisa_faturas
                              │  resgata o PDF, lê as linhas
                              │  1 evento ──▶ N eventos
                              ▼
                       compras-extraidas (3 partições)
                              │
                              ▼
                   consumidor_categoriza_compras ◀──▶ Ollama (gemma:2b)
                              │
                              ▼
                     compras-categorizadas ──┬──▶ painel_de_gastos
                              │              └──▶ Qdrant (compras)
                              ▼
                   consumidor_notifica_teams ──▶ card no Teams
                              │
                       falha ──▶ compras-com-erro
```

## Começar

```bash
git clone https://github.com/tonanuvem/kafka-event
cd kafka-event
./lab.sh configurar     # seu nome + webhook do Teams
./lab.sh subir          # broker, armazenamento, UIs e microsserviços
./lab.sh ia             # LLM local + categorizador
./lab.sh enviar azul    # dispara o pipeline
```

`./lab.sh` sem argumento lista todos os comandos.

## Acessos

| Ferramenta | Porta | O que mostra |
|---|---|---|
| Swagger do produtor | 5001 | Onde o evento nasce |
| Redpanda Console | 8080 | Tópicos, partições, consumer groups, **lag** |
| Navegador de objetos | 8888 | O PDF guardado de verdade |
| Painel do cluster de objetos | 9333 | Topologia do armazenamento |
| Qdrant | 6333 | Vetores, clusters 2D, grafo HNSW |
| Painel de Gastos | 3000 | O resultado de negócio |

## Conceitos que o lab ensina

| Conceito | Onde aparece |
|---|---|
| Produtor, tópico, consumidor, offset | Módulo 1, com `lab-mensagens` |
| **Claim-Check** | O PDF vai para o S3; o evento leva só a URL |
| **Fan-out** | 1 fatura → ~40 eventos de compra |
| Chave de partição | `fatura_id` como chave mantém a ordem por fatura |
| **Busca vetorial** (embedding, cosseno, top-k, HNSW) | Catálogo de comerciantes no Qdrant |
| **RAG** | Os vizinhos mais próximos entram no prompt do LLM |
| Roteamento por confiança | Vetor resolve o barato; IA só o difícil |
| **Consumer lag e rebalanceamento** | `./lab.sh tempestade` e `./lab.sh escalar` |
| **Replay** | `./lab.sh replay` reprocessa o histórico |
| DLQ | `./lab.sh caos` desvia as falhas para `compras-com-erro` |
| **MCP** | `./lab.sh mcp` expõe o pipeline como ferramentas de IA |

## As faturas

As duas faturas em `faturas/` são **sintéticas**: geradas por
`faturas/gerar_faturas.py`, com estabelecimentos, valores, titular e
cartão inventados. Nenhum dado real de nenhuma pessoa.

Elas imitam **dois layouts diferentes** de propósito:

- **Banco Azul** — coluna única, com coluna de País e com os grupos
  grossos que o próprio banco atribui. Parsing linha a linha. **662 KB.**
- **Banco Verde** — **duas colunas** lado a lado e vários portadores de
  cartão. O `extract_text()` do PDF intercala as colunas e produz lixo:
  o parser é obrigado a usar a posição X de cada palavra. **3,7 MB**, ou
  seja, **acima do limite default de 1 MB de mensagem do Kafka** — que é
  justamente o que torna o claim-check uma necessidade e não teoria.

Para gerar variações (útil no módulo de carga):

```bash
docker run --rm -v "$PWD/faturas":/w -w /w python:3.11-slim \
  sh -c "pip install -q reportlab pillow && python gerar_faturas.py . --variacoes 4"
```

## Requisitos

- Docker e Docker Compose
- ~8 GB de RAM e ~15 GB de disco livre (a imagem do Ollama com o
  `gemma:2b` embutido tem ~5 GB)
- Opcional: canal do Teams com um fluxo do Power Automate. Sem webhook o
  lab funciona igual — o notificador imprime os cards no log.

## Licença e uso

Material didático da FIAP. As faturas são sintéticas e os
estabelecimentos fictícios.
