# Busca vetorial: significado virando geometria

## O problema concreto

Abra a fatura e olhe a coluna de descrição. O nome do estabelecimento
não chega como ele é — chega deformado pela maquininha e pelo
intermediador de pagamento:

```
IFD*CANTINA DO ZECA          DL*GOOGLE YouTubePre
PAYGO*BAR DO TONHO           MP *J R DA SILVA
VMT*NUTRIFORMULA             PROPIG *A F DE SOUZA
AMAZON BR *AMAZON BR         ZUL + CARTAO 7KQ2P
```

Esses prefixos (`IFD*` do iFood, `MP *` do Mercado Pago, `DL*`, `PAYGO*`)
são códigos do adquirente. O nome vem truncado no meio da palavra, com a
cidade colada, às vezes sendo apenas o nome de uma pessoa física que
vendeu pela maquininha.

Duas abordagens ingênuas falham aqui:

**Palavra-chave.** `WHERE descricao LIKE '%uber%'` acerta um caso e erra
mil. Não há palavra em comum entre `PAYGO*BAR DO TONHO` e
`NORTH BAR E GRILL` que os ligue a "alimentação" — e `BAR` sozinho
também aparece em `BARRACA`, `BARBEARIA`, `BARATHEON LTDA`.

**Tabela de regras.** Fica eternamente incompleta. Todo mês surge um
estabelecimento novo, e alguém tem que mantê-la.

## A ideia do embedding

Um **modelo de embedding** é uma função que transforma texto num **ponto
num espaço de muitas dimensões** — no nosso caso, 384 números:

```
"IFD*CANTINA DO ZECA"  →  [0.021, -0.118, 0.307, ..., 0.044]   (384 números)
```

O treino desse modelo garante uma propriedade específica, e é dela que
tudo depende: *textos com significado parecido caem em posições
próximas*. `IFD*CANTINA DO ZECA` e `PAYGO*BAR DO TONHO` ficam vizinhos
mesmo sem compartilhar uma única letra útil, porque o modelo aprendeu
que ambos são lugares de comer.

**Significado virou geometria.** É essa a mudança de chave.

## A busca

Com tudo virando ponto, "achar o parecido" vira "achar o mais próximo".
A medida é a **distância de cosseno**: ela compara o *ângulo* entre dois
vetores, não o tamanho deles. Vetores apontando para a mesma direção têm
score perto de 1; perpendiculares, perto de 0.

Dado um lançamento desconhecido, pegamos os **k vizinhos mais próximos**
no catálogo (top-k) e olhamos a categoria deles.

## Por que precisa de um banco vetorial

Comparar um vetor com 100 mil comerciantes, um por um, é lento demais
para rodar a cada compra. O Qdrant indexa os vetores num grafo chamado
**HNSW** (*Hierarchical Navigable Small World*): uma estrutura em
camadas onde a busca "salta" de vizinho em vizinho e chega perto do alvo
em pouquíssimos passos, em vez de varrer tudo.

É busca **aproximada**: troca-se um fio de precisão por ordens de
magnitude de velocidade. Para categorizar compras, essa troca é ótima.

O Qdrant também guarda um **payload** (categoria, banco, data, valor)
junto de cada vetor, e sabe filtrar por ele.

## Veja acontecendo: as quatro abas do dashboard

Abra `http://SEU_IP:6333/dashboard`. Cada aba revela uma camada:

| Aba | O que fazer | O conceito que cai a ficha |
|---|---|---|
| **Collections → Points** | Abra um ponto da coleção `compras` | "Ah, o vetor é *isso*: 384 números. E o texto fica no payload, não no vetor." |
| **Console** | Rode uma busca por `"corrida de aplicativo"` | Os vizinhos vêm **com o score**. Dá para ver o top-k e entender o limiar |
| **Visualize** | Projeção 2D, colorida por categoria | **Os clusters aparecem**: transporte numa nuvem, alimentação em outra, e os outliers no meio |
| **Graph** | Parta de um ponto e caminhe pelos vizinhos | **O HNSW por dentro** — é a estrutura que torna a busca rápida |

Na aba **Visualize**, use esta consulta para colorir por categoria:

```json
{
  "limit": 500,
  "color_by": { "payload": "categoria" }
}
```

E clique em **RUN**. O algoritmo padrão é UMAP; `"algorithm": "TSNE"` e
`"PCA"` também existem. Clicar num ponto mostra o payload dele e os
vizinhos mais próximos.

## As duas coleções do lab, e por que são duas

O lab usa busca vetorial para **dois** fins diferentes, e isso fica
explícito em duas coleções separadas:

### `comerciantes` — o catálogo de referência

Pré-carregada com 45 comerciantes conhecidos e sua categoria. É
consultada **antes** da IA:

```
compra nova  →  vetor  →  vizinho mais próximo no catálogo
                              │
                   score ≥ 0,80 ?
                     │              │
                    sim            não
                     │              │
         herda a categoria     vai para o LLM
         (instantâneo)         (caro, ~8 s)
```

Isto é **roteamento por confiança**, e é como se faz na indústria:
resolva o barato primeiro, deixe a IA para o que sobrou.

Faça o teste: adicione um comerciante e reprocesse.

```bash
./lab.sh catalogo add "IFD*NOVA CANTINA" Alimentacao
./lab.sh replay
```

A categorização muda. Isso é **RAG** na sua forma mais nua: recuperar
contexto relevante para melhorar a decisão.

### `compras` — o histórico, para perguntar depois

Cada compra categorizada é indexada aqui. Serve à **busca semântica**
do servidor MCP:

> *"quanto eu gastei com comida fora de casa?"*

encontra `IFD*CANTINA DO ZECA`, `PAYGO*BAR DO TONHO` e
`NORTH BAR E GRILL` — sem que nenhuma dessas palavras apareça na
pergunta.

## Um detalhe que custa caro esquecer

Modelos de embedding diferentes têm convenções diferentes. A família
**e5** exige prefixar o texto com `query:` ou `passage:`; a família
**paraphrase** (que este lab usa) **não usa prefixo** — colocar um
*degrada* a similaridade.

O lab usa `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`:
384 dimensões, 220 MB, multilíngue (importante: as descrições estão em
português). O modelo vem **embutido na imagem**, para o primeiro evento
da aula não disparar um download e parecer travamento.
