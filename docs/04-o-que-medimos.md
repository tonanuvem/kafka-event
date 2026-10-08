# O que medimos (e o que isso ensina)

Este documento registra medições reais feitas na VM do lab
(`t2.large`, 2 vCPU, 7,7 GB), não estimativas. Os números servem para
dimensionar a aula — e os erros que encontramos no caminho valem mais
que os acertos.

## O ambiente

| Item | Valor |
|---|---|
| Instância | `t2.large` — 2 vCPU, 7,7 GB RAM, 96 GB disco |
| LLM | `gemma2:2b` no Ollama, CPU (sem GPU) |
| Embedding | `paraphrase-multilingual-MiniLM-L12-v2`, 384 dim |
| Geração | **4,3 tokens/s** |
| Embedding por compra | **7 ms** |

## O pipeline, ponta a ponta

Duas faturas, 82 eventos de compra:

| Etapa | Medido |
|---|---|
| Upload + claim-check (fatura de 3,7 MB) | evento de **320 bytes** — 12.069× menor |
| Leitura do PDF e extração (47 linhas) | **0,1 s** |
| Categorização pelo catálogo | instantânea, **89%** das compras |
| Categorização pela IA | **3,3 s por compra**, 11% das compras |
| Total categorizado | R$ 26.790,30 em 80 compras |

Os totais por fatura bateram **exatamente** com o gabarito do gerador:
R$ 11.224,41 (34 compras) e R$ 15.565,89 (46 compras). O pipeline não
perde nem inventa lançamento.

## Memória

| Serviço | RAM |
|---|---|
| Kafka (heap 768 MB) | 417 MB |
| `consumidor_categoriza_compras` | 659 MB |
| armazenamento de objetos | 90 MB |
| `consumidor_analisa_faturas` | 63 MB |
| `produtor_envia_fatura` | 61 MB |
| `painel_de_gastos` | 38 MB |
| Redpanda Console | 25 MB |
| Qdrant | 22 MB |
| `consumidor_notifica_teams` | 20 MB |
| **Contêineres** | **~1,4 GB** |
| Ollama + modelo (no host) | ~2,5 GB |
| **Total da máquina** | **4,9 GB de 7,7 GB** |

Cabe com folga. O Ollama é de longe o maior consumidor — por isso o lab
prefere usar um já instalado no host a subir outro em contêiner.

## A investigação que vale a aula

Medindo o acerto da IA nos 10 comerciantes **fora do catálogo** (os
únicos que chegam ao LLM), encontramos 40%. Investigar esse número
rendeu quatro lições que nenhum slide ensina.

### Lição 1 — RAG com recuperação ruim é pior que RAG nenhum

Os vizinhos recuperados estavam casando por **semelhança de string**,
não de significado:

```
consulta: OXXO ESTACAO LESTE        (loja de conveniência)
vizinho:  ESTAC PATIO NORTE  score=0,884  → Transporte
```

"ESTACAO" e "ESTAC" se parecem como texto. Nada a ver como negócio. O
exemplo foi para o prompt e o modelo respondeu **Transporte**.

### Lição 2 — existe vetor "hub", próximo de tudo e útil para nada

`CLINICA VIDA PLENA` aparecia como vizinho mais próximo de quatro
consultas sem relação nenhuma, com scores de 0,75 a 0,87:

| Consulta | Score com CLINICA VIDA PLENA |
|---|---|
| `DAISO UTILIDADES BR` | 0,868 |
| `PROPIG *A F DE SOUZA` | 0,831 |
| `LATICINIOS SERRA AZUL` | 0,799 |
| `MP *J R DA SILVA` | 0,775 |

Isso é **hubness**, uma patologia conhecida de espaços de alta dimensão:
alguns pontos ficam perto de quase todos os outros. Score alto não é
garantia de relevância — e é por isso que se mede, em vez de confiar.

### Lição 3 — os exemplos ancoram o formato, não só o conteúdo

A correção óbvia seria descartar vizinhos ruins. Testamos três
estratégias sobre os mesmos 10 itens:

| Estratégia | Acerto |
|---|---|
| A — sem vizinho (zero-shot) | **1/10** |
| B — 3 vizinhos sempre (RAG ingênuo) | **3/10** |
| C — só vizinho forte, filtrado | **4/10** |
| C′ — filtro + exigir vizinhos concordantes | **1/10** |

O C′ foi uma surpresa: filtrar *demais* derrubou o acerto para 1/10. O
motivo não é semântico. Sem exemplos, o `gemma2:2b` **responde o item 1
e para de enumerar** — exatamente como no zero-shot.

Um modelo de 2 bilhões de parâmetros usa os exemplos para sustentar o
**formato** da resposta, não apenas para decidir a categoria. Por isso o
lab nunca envia um prompt sem exemplo: quando a recuperação não traz
nada confiável, entram exemplos canônicos.

### Lição 4 — o bug mais caro foi de uma linha

O modelo responde em português **com acento** (`Educação`), e a
validação comparava com a lista sem acento (`Educacao`). Toda resposta
acentuada era recusada e virava `Outros` — descartando respostas
corretas. Uma normalização de acentos recuperou esses casos.

## O que esses números significam para a aula

Combinando as duas camadas:

```
89% das compras  →  catálogo vetorial  →  praticamente 100% de acerto
11% das compras  →  LLM local          →  ~40% de acerto nos casos difíceis
                                          (itens propositalmente ambíguos)
```

O acerto fim-a-fim fica em torno de **93%**, e os 11% que vão à IA são
justamente nomes de pessoa física e lojas genuinamente ambíguas — casos
em que mesmo uma pessoa hesitaria entre duas categorias.

A conclusão que o aluno leva não é "a IA resolve". É:

> Use a camada barata primeiro, meça antes de confiar, e saiba que um
> modelo pequeno rodando em CPU tem limites reais. O valor de engenharia
> está no roteamento, não no modelo.
