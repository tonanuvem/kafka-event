# MCP: dando ferramentas a um modelo

## O problema que ele resolve

Um LLM sozinho só sabe gerar texto. Para ele *fazer* algo — consultar
seu Kafka, ler um arquivo, chamar uma API — alguém precisa descrever as
ferramentas disponíveis e executar as chamadas que o modelo pedir.

Até o fim de 2024, cada aplicação de IA fazia essa cola do seu jeito. A
integração escrita para o Cursor não servia no Claude, e a do Claude não
servia no Amazon Q. Era **N aplicações × M ferramentas** de trabalho
duplicado.

## O que é

**MCP (Model Context Protocol)** é um protocolo aberto, publicado pela
Anthropic no fim de 2024 e hoje adotado amplamente — Amazon Q, OpenAI,
Google, VS Code, JetBrains. Ele padroniza essa conversa: você escreve
**um** servidor e **qualquer** cliente MCP passa a usá-lo.

A analogia honesta é **USB-C**, ou o **JDBC/ODBC da era da IA**.

## Os dois papéis

**Servidor MCP** — um processo que você escreve (aqui, ~220 linhas de
Python). Ele **anuncia** o que sabe fazer e executa quando pedido:

- `tools` — ações: *"publicar um alerta"*, *"consultar o lag"*
- `resources` — dados legíveis: *"o runbook X"*
- `prompts` — modelos de instrução prontos

Comunica por **stdio** (processo local) ou **HTTP**.

**Cliente / Host MCP** — o aplicativo de IA (`q chat`, Claude Code, VS
Code). Ele descobre as tools, repassa a lista ao modelo e, quando o
modelo escolhe uma, executa e devolve o resultado.

> **Repare: não existe IA dentro do servidor MCP.** Ele é só uma casca
> padronizada em volta do Kafka e do Qdrant. A inteligência está no
> cliente. Esse é o ponto que mais confunde quem chega agora.

## Vendo o protocolo cru

A forma mais honesta de entender MCP é olhar os bytes. O servidor fala
JSON-RPC pela entrada e saída padrão. O aperto de mão:

```json
→ {"jsonrpc":"2.0","id":1,"method":"initialize",
   "params":{"protocolVersion":"2024-11-05","capabilities":{},
             "clientInfo":{"name":"teste","version":"1"}}}

← {"jsonrpc":"2.0","id":1,"result":{
     "protocolVersion":"2024-11-05",
     "capabilities":{"tools":{"listChanged":false}, ...},
     "serverInfo":{"name":"faturas-fiap"}}}
```

Depois o cliente pede a lista de ferramentas, e o servidor responde com
o **schema** de cada uma — é esse schema que o LLM lê para saber o que
pode chamar e com quais argumentos:

```json
→ {"jsonrpc":"2.0","id":2,"method":"tools/list"}

← {"tools":[{"name":"listar_topicos",
             "description":"Lista os topicos do Kafka com particoes...",
             "inputSchema":{"type":"object","properties":{}}}, ...]}
```

E a execução:

```json
→ {"jsonrpc":"2.0","id":3,"method":"tools/call",
   "params":{"name":"resumo_por_categoria","arguments":{}}}
```

## As ferramentas deste lab

| Tool | O que faz |
|---|---|
| `listar_topicos()` | Tópicos, partições e quantidade de mensagens |
| `consultar_lag(grupo)` | Lag por partição de um consumer group |
| `enviar_fatura(banco)` | Publica uma fatura e dispara o pipeline |
| `resumo_por_categoria()` | Total gasto por categoria |
| `buscar_gastos(pergunta)` | **Busca semântica** no histórico de compras |
| `consultar_catalogo(descricao)` | Os vizinhos e seus scores — mostra *por que* o pipeline decidiu |

## Duas formas de experimentar, nenhuma exige chave de API

### 1. MCP Inspector — ver o protocolo

A UI oficial do projeto. Lista e chama as ferramentas na mão, mostrando
o JSON que vai e volta. **Não precisa de LLM nenhum**, e é
pedagogicamente o melhor jeito de entender o protocolo.

```bash
npx @modelcontextprotocol/inspector \
    docker exec -i servidor_mcp_faturas python -m mcp_faturas
```

### 2. Amazon Q CLI — ver o modelo orquestrando

O Amazon Q já vem instalado na VM do lab e lê `~/.aws/amazonq/mcp.json`.

```bash
./lab.sh mcp-q     # grava a configuração
q chat
```

Dentro da conversa, `/mcp` confirma que o servidor carregou. Depois é só
perguntar em português:

> *"Quanto eu gastei com transporte? Tem alguma compra fora do padrão?
> Reprocessa a fatura do banco verde e me diz se mudou algo."*

O Q decide sozinho quais ferramentas chamar, em que ordem, e junta as
respostas. É o fecho da aula: **o aluno conversando com o pipeline que
acabou de construir**.

## Pré-requisito do Amazon Q

O `q login` exige uma conta **Builder ID** (gratuita, pessoal). Cada
aluno faz a sua, uma vez, pelo navegador. O módulo do Inspector não
precisa de conta nenhuma — por isso ele vem primeiro no roteiro.
