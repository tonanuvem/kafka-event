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

---

# Apêndice: consertando a busca por linguagem natural

A primeira versão deste laboratório tinha um defeito honesto: perguntar
*"comida fora de casa"* trazia farmácia entre os primeiros resultados.

## O diagnóstico

O problema é **assimetria de recuperação**. A pergunta é uma frase em
português; o documento indexado é um nome curto em maiúsculas. São
distribuições de texto diferentes.

E o modelo usado, `paraphrase-multilingual-MiniLM`, é treinado para
similaridade **simétrica** — pares de frases parecidas entre si. Ele
compara comerciante com comerciante muito bem (daí os 91% de acerto do
catálogo) e pergunta com nome curto muito mal.

Isso revela algo que estava escondido: **os dois usos de busca vetorial
no lab têm requisitos diferentes**, e estavam sendo tratados com a mesma
ferramenta.

## As três saídas, medidas

Precisão no top-5, sobre as 80 compras do laboratório, em 6 perguntas:

| Estratégia | Precisão |
|---|---|
| Indexar `DESCRIÇÃO (Categoria)` | **23%** |
| Indexar a **definição da categoria** junto | **83%** |
| + **rotear a pergunta** para uma categoria antes de buscar | **97%** |

**Enriquecer o documento, não a consulta.** O texto indexado passou de
`CANTINA DO ZECA (Alimentacao)` para `CANTINA DO ZECA. Alimentacao:
restaurante, bar, lanchonete, padaria, delivery de comida, comer fora
de casa`. Agora o documento contém as palavras que uma pessoa usaria ao
perguntar. Esse é o ponto: a ponte entre o vocabulário da fatura e o
vocabulário da pergunta.

**Rotear antes de buscar.** A pergunta é comparada com a definição de
cada categoria (11 vetores, custo irrisório) e a busca é filtrada pela
vencedora. Isso elimina de uma vez o ruído das outras categorias, em
vez de torcer para o ranking resolver.

## Quando NÃO rotear — e como saber

Nem toda pergunta é sobre uma categoria. *"Compras acima de 500 reais"*
é sobre valor. Forçar um filtro ali dá resposta errada com cara de
certa, que é o pior tipo de erro.

O critério para distinguir foi **medido**, não escolhido. Comparando 8
perguntas sobre categoria com 7 que não são:

| Critério | Perguntas sobre categoria | Perguntas que não são | Separa? |
|---|---|---|---|
| Score absoluto da 1ª | 0,513 a 0,926 | até **0,750** | **Não** |
| **Margem** entre a 1ª e a 2ª | 7 de 8 acima de 0,08 | todas abaixo de 0,078 | **Sim** |

Faz sentido quando se pensa: quando a pergunta tem assunto, uma
categoria se destaca das outras. Quando não tem, várias ficam
igualmente mornas. **É o empate, não o valor absoluto, que denuncia a
ausência de assunto.**

A única pergunta de categoria que fica abaixo do corte —
*"corridas de aplicativo"* — é justamente uma que o roteador erraria
(ele a associa a Assinaturas, por causa de "aplicativo"). Abster-se ali
é o comportamento correto: a busca passa a varrer tudo, sem fingir
certeza.

## E a solução de raiz?

Trocar por um modelo treinado para recuperação **assimétrica** — a
família `e5` ou o `bge-m3`, que usam prefixos `query:` e `passage:` —
resolveria o problema na origem. Mas os multilingues dessa família
passam de 1 GB e disputariam memória com o LLM numa VM de 8 GB.

Enriquecer o índice e rotear a consulta custa zero de memória e
resolveu 97% do caso. Essa é a troca que se faz na prática: **antes de
trocar o modelo, verifique se o problema está em como você indexou.**
