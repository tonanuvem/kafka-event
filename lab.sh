#!/usr/bin/env bash
# ============================================================
# LAB KAFKA-EVENT : menu unico do laboratorio
#
# Mesma ideia do ~/fiaplab.sh que voce ja usa: um comando por
# momento da aula, em vez de uma sequencia de docker compose.
# ============================================================
set -uo pipefail

cd "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

COMPOSE="docker compose"
docker compose version >/dev/null 2>&1 || COMPOSE="docker-compose"

AZUL=$'\033[0;34m'; VERDE=$'\033[0;32m'; AMARELO=$'\033[1;33m'
VERMELHO=$'\033[0;31m'; NEGRITO=$'\033[1m'; FIM=$'\033[0m'

titulo() { echo; echo "${NEGRITO}${AZUL}=== $* ===${FIM}"; echo; }
ok()     { echo "${VERDE}✔${FIM} $*"; }
aviso()  { echo "${AMARELO}!${FIM} $*"; }
erro()   { echo "${VERMELHO}x${FIM} $*" >&2; }

descobrir_ip() {
    local ip
    ip=$(curl -s --max-time 4 http://169.254.169.254/latest/meta-data/public-ipv4 2>/dev/null)
    [ -z "$ip" ] && ip=$(curl -s --max-time 4 checkip.amazonaws.com 2>/dev/null)
    [ -z "$ip" ] && ip="localhost"
    echo "$ip"
}

garantir_env() {
    if [ ! -f .env ]; then
        aviso "arquivo .env ausente — rodando 'configurar' primeiro"
        cmd_configurar
    fi
    # Mantem o IP_PUBLICO sempre atual: o IP da VM muda a cada
    # restart no AWS Academy.
    local ip; ip=$(descobrir_ip)
    if grep -q '^IP_PUBLICO=' .env; then
        sed -i.bak "s|^IP_PUBLICO=.*|IP_PUBLICO=$ip|" .env && rm -f .env.bak
    else
        echo "IP_PUBLICO=$ip" >> .env
    fi
}

# ------------------------------------------------------------
cmd_configurar() {
    titulo "CONFIGURAR O LAB"
    [ -f .env ] || cp .env.exemplo .env

    local atual_aluno atual_hook
    atual_aluno=$(grep '^ALUNO=' .env | cut -d= -f2-)
    atual_hook=$(grep '^TEAMS_WEBHOOK=' .env | cut -d= -f2-)

    echo "Seu nome (aparece no titulo do card, pois o canal e da turma inteira)."
    read -r -p "  ALUNO [${atual_aluno}]: " nome
    [ -n "${nome:-}" ] && sed -i.bak "s|^ALUNO=.*|ALUNO=$nome|" .env

    echo
    echo "URL do fluxo do Power Automate (app Workflows do Teams)."
    echo "Deixe VAZIO para fazer o lab sem o Teams: o notificador"
    echo "imprime o card no log em vez de enviar."
    read -r -p "  TEAMS_WEBHOOK [${atual_hook:-vazio}]: " hook
    if [ -n "${hook:-}" ]; then
        # '|' como separador: a URL tem barras e assinatura.
        sed -i.bak "s|^TEAMS_WEBHOOK=.*|TEAMS_WEBHOOK=$hook|" .env
    fi
    rm -f .env.bak

    garantir_env
    echo
    ok "configuracao gravada em .env"
    grep -E '^(ALUNO|IP_PUBLICO)=' .env | sed 's/^/   /'
    if grep -q '^TEAMS_WEBHOOK=$' .env; then
        aviso "sem webhook: os cards vao para o log (./lab.sh logs consumidor_notifica_teams)"
    else
        ok "webhook do Teams configurado"
    fi
}

# ------------------------------------------------------------
cmd_subir() {
    garantir_env
    titulo "SUBINDO O AMBIENTE"

    echo "1/3 construindo a imagem dos microsservicos (so na primeira vez)..."
    $COMPOSE build || { erro "falha no build"; return 1; }

    echo
    echo "2/3 subindo broker, armazenamento e UIs..."
    $COMPOSE up -d kafka console objetos qdrant || return 1

    echo
    echo "    preparando topicos, bucket, colecoes e catalogo..."
    $COMPOSE up preparar || return 1

    echo
    echo "3/3 subindo os microsservicos..."
    $COMPOSE up -d produtor_envia_fatura consumidor_analisa_faturas \
                   consumidor_notifica_teams painel_de_gastos || return 1

    aviso "o categorizador precisa do LLM: suba com './lab.sh ia'"
    cmd_urls
}

# Detecta um Ollama JA instalado no host (alguns labs da FIAP instalam
# como servico systemd). Nesse caso nao faz sentido subir um segundo
# dentro de um conteiner: seriam ~3 GB de RAM e ~5 GB de imagem
# duplicados, numa VM de 8 GB.
detectar_ollama_do_host() {
    curl -sf --max-time 4 http://localhost:11434/api/tags >/dev/null 2>&1
}

# O Ollama do host costuma escutar so em 127.0.0.1, onde os conteineres
# nao alcancam. Publica tambem na ponte do Docker -- e NAO em 0.0.0.0,
# que exporia o LLM na internet, ja que o security group do lab e aberto.
abrir_ollama_para_conteineres() {
    local destino=/etc/systemd/system/ollama.service.d/lab-kafka.conf
    sudo mkdir -p "$(dirname "$destino")"
    sudo tee "$destino" >/dev/null <<'CONF'
# Adicionado pelo lab kafka-event: permite que os conteineres alcancem
# o Ollama do host pela ponte do Docker. Nao usa 0.0.0.0 de proposito.
[Service]
Environment="OLLAMA_HOST=172.17.0.1:11434"
Environment="OLLAMA_KEEP_ALIVE=30m"
Environment="OLLAMA_MAX_LOADED_MODELS=1"
CONF
    sudo systemctl daemon-reload && sudo systemctl restart ollama
    for _ in $(seq 1 20); do
        curl -sf --max-time 3 http://172.17.0.1:11434/api/tags >/dev/null 2>&1 && return 0
        sleep 2
    done
    return 1
}

cmd_ia() {
    garantir_env

    # ---------- caminho 1: Ollama ja existe no host ----------
    if detectar_ollama_do_host; then
        titulo "USANDO O OLLAMA JA INSTALADO NO HOST"
        echo "Encontrei um Ollama rodando nesta maquina. Vou usa-lo em vez"
        echo "de subir outro em conteiner (economiza ~5 GB de imagem e"
        echo "~3 GB de RAM numa VM de 8 GB)."
        echo

        local modelos; modelos=$(curl -s http://localhost:11434/api/tags \
            | grep -o '"name":"[^"]*"' | sed 's/"name":"//;s/"$//')
        echo "Modelos disponiveis:"; echo "$modelos" | sed 's/^/   /'

        # Prefere o gemma2:2b: mesmo tamanho do gemma:2b e bem melhor em
        # seguir instrucao e devolver JSON valido.
        local escolhido
        escolhido=$(echo "$modelos" | grep -x "gemma2:2b" \
            || echo "$modelos" | grep -x "gemma:2b" \
            || echo "$modelos" | head -1)
        [ -z "$escolhido" ] && { erro "nenhum modelo no Ollama do host"; return 1; }

        echo; ok "modelo escolhido: $escolhido"

        echo "Publicando o Ollama na ponte do Docker..."
        abrir_ollama_para_conteineres || { erro "nao consegui expor o Ollama aos conteineres"; return 1; }
        ok "Ollama acessivel em 172.17.0.1:11434"

        sed -i.bak "s|^OLLAMA_MODELO=.*|OLLAMA_MODELO=$escolhido|" .env
        grep -q '^OLLAMA_URL=' .env \
            && sed -i.bak "s|^OLLAMA_URL=.*|OLLAMA_URL=http://172.17.0.1:11434|" .env \
            || echo "OLLAMA_URL=http://172.17.0.1:11434" >> .env
        rm -f .env.bak

        $COMPOSE up -d consumidor_categoriza_compras || return 1
        ok "consumidor_categoriza_compras no ar"
        echo; echo "Agora envie uma fatura:  ./lab.sh enviar azul"
        return 0
    fi

    # ---------- caminho 2: subir o Ollama em conteiner ----------
    titulo "SUBINDO O LLM LOCAL (gemma:2b)"
    echo "A imagem traz o modelo embutido (~5 GB), para nao baixar o"
    echo "gemma:2b no meio da aula. A primeira construcao leva ~5 min."
    echo

    # Se a imagem ja estiver publicada no Docker Hub, usa; senao
    # constroi localmente. O "up" sozinho tentaria o pull e falharia.
    if ! docker image inspect "${IMAGEM_OLLAMA:-tonanuvem/ollama-gemma2b:latest}" >/dev/null 2>&1; then
        if ! docker pull -q "${IMAGEM_OLLAMA:-tonanuvem/ollama-gemma2b:latest}" >/dev/null 2>&1; then
            echo "imagem nao publicada -- construindo localmente..."
            $COMPOSE --profile ia build ollama || return 1
        fi
    fi

    $COMPOSE --profile ia up -d ollama || return 1

    echo "aguardando o Ollama responder..."
    for _ in $(seq 1 60); do
        if curl -sf http://localhost:11434/api/tags >/dev/null 2>&1; then
            ok "Ollama no ar"
            curl -s http://localhost:11434/api/tags \
                | grep -o '"name":"[^"]*"' | sed 's/"name":"/   modelo: /;s/"$//'
            break
        fi
        sleep 3
    done

    $COMPOSE up -d consumidor_categoriza_compras || return 1
    ok "consumidor_categoriza_compras no ar"
    echo
    echo "Agora envie uma fatura:  ./lab.sh enviar azul"
}

# ------------------------------------------------------------
cmd_urls() {
    garantir_env
    local ip; ip=$(grep '^IP_PUBLICO=' .env | cut -d= -f2-)
    titulo "ACESSOS DO LAB   (IP $ip)"
    printf "  %-26s %s\n" "Produtor (Swagger)"  "http://$ip:5001/docs"
    printf "  %-26s %s\n" "Kafka Console"       "http://$ip:8080"
    printf "  %-26s %s\n" "Objetos (os PDFs)"   "http://$ip:8888/buckets/faturas/"
    printf "  %-26s %s\n" "Qdrant (vetores)"    "http://$ip:6333/dashboard"
    printf "  %-26s %s\n" "Painel de Gastos"    "http://$ip:3000"
    echo
    echo
}

cmd_status() {
    titulo "STATUS DOS CONTEINERES"
    $COMPOSE ps
    echo
    titulo "MEMORIA EM USO"
    docker stats --no-stream --format \
        "table {{.Name}}\t{{.MemUsage}}\t{{.CPUPerc}}" 2>/dev/null | head -20
}

# ------------------------------------------------------------
cmd_enviar() {
    local banco="${1:-azul}"
    titulo "ENVIANDO FATURA DO BANCO ${banco^^}"
    curl -s -X POST "http://localhost:5001/faturas/exemplo" \
         -F "banco=$banco" | python3 -m json.tool 2>/dev/null \
        || erro "o produtor respondeu erro (./lab.sh logs produtor_envia_fatura)"
    echo
    echo "Acompanhe em: Console (8080) -> topicos, e Painel (3000)."
}

cmd_mensagem() {
    local texto="${*:-Ola Kafka, primeira mensagem do lab}"
    titulo "MODULO 1: PUBLICANDO UMA MENSAGEM"
    curl -s -X POST "http://localhost:5001/mensagem" \
         -F "texto=$texto" | python3 -m json.tool 2>/dev/null \
        || erro "falha ao publicar"
    echo
    echo "Procure no Console (8080) o topico 'lab-mensagens'."
}

cmd_tempestade() {
    local n="${1:-6}"
    garantir_env
    titulo "TEMPESTADE: $n FATURAS DE UMA VEZ"
    echo "Objetivo: criar fila de trabalho e ver o LAG subir no Console."
    echo
    for i in $(seq 1 "$n"); do
        local banco=azul; [ $((i % 2)) -eq 0 ] && banco=verde
        curl -s -X POST "http://localhost:5001/faturas/exemplo" -F "banco=$banco" \
            >/dev/null && echo "  fatura $i enviada (banco $banco)"
    done
    echo
    echo "Abra o Console -> Consumer Groups -> grupo-categoriza e veja o lag."
    echo "Depois rode: ./lab.sh escalar 2"
}

cmd_escalar() {
    local n="${1:-2}"
    titulo "ESCALANDO O CATEGORIZADOR PARA $n INSTANCIAS"
    echo "Os topicos de compra tem 3 particoes. Com $n consumidores no"
    echo "mesmo grupo, o Kafka REDISTRIBUI as particoes entre eles."
    echo
    $COMPOSE up -d --scale consumidor_categoriza_compras="$n" \
        consumidor_categoriza_compras || return 1
    echo
    ok "escalado para $n"
    echo "No Console -> Consumer Groups -> grupo-categoriza voce ve qual"
    echo "consumidor ficou com qual particao, e o lag drenando mais rapido."
}

cmd_caos() {
    titulo "CAOS: DERRUBANDO O LLM"
    echo "Com o Ollama fora, a etapa de IA falha e as mensagens vao"
    echo "para o topico compras-com-erro (a DLQ)."
    echo
    $COMPOSE stop ollama && ok "ollama parado"
    echo
    echo "Envie uma fatura agora:  ./lab.sh enviar verde"
    echo "Depois observe o topico compras-com-erro no Console."
    echo "Para voltar:  ./lab.sh ia"
}

cmd_replay() {
    titulo "REPLAY: REPROCESSAR O HISTORICO"
    cat <<'TXT'
Este e o conceito que separa o Kafka de uma fila comum: as mensagens
NAO sao apagadas quando consumidas. Voltando o offset do grupo para o
inicio, o pipeline reprocessa tudo -- util depois de melhorar o prompt
ou de corrigir o catalogo, SEM reenviar as faturas.

TXT
    read -r -p "Reprocessar todas as compras ja extraidas? [s/N] " r
    [[ "${r:-n}" =~ ^[sS]$ ]] || { aviso "cancelado"; return 0; }

    # O Kafka so deixa mover o offset de um grupo INATIVO. Parar os
    # conteineres nao basta: a sessao do consumidor ainda vive no broker
    # por alguns segundos. Entao esperamos o grupo sair de "Stable".
    echo "parando os consumidores do grupo..."
    $COMPOSE stop consumidor_categoriza_compras >/dev/null 2>&1

    local estado=""
    for _ in $(seq 1 30); do
        estado=$(docker exec kafka /opt/kafka/bin/kafka-consumer-groups.sh \
            --bootstrap-server localhost:19092 --describe --group grupo-categoriza --state \
            2>/dev/null | awk '$1 == "grupo-categoriza" {print $(NF-1)}' | head -1)
        case "$estado" in
            Empty|Dead) break ;;
        esac
        printf "."
        sleep 2
    done
    echo

    case "$estado" in
        Empty|Dead) ok "grupo inativo (estado: $estado)" ;;
        *) erro "o grupo nao ficou inativo (estado: ${estado:-desconhecido}); abortando"
           $COMPOSE start consumidor_categoriza_compras >/dev/null 2>&1
           return 1 ;;
    esac

    # Guarda a saida: o kafka-consumer-groups.sh imprime o erro e mesmo
    # assim sai com codigo 0, entao conferir o RC nao basta -- a saida
    # tem de conter a tabela de NEW-OFFSET.
    local saida
    saida=$(docker exec kafka /opt/kafka/bin/kafka-consumer-groups.sh \
        --bootstrap-server localhost:19092 \
        --group grupo-categoriza --topic compras-extraidas \
        --reset-offsets --to-earliest --execute 2>&1)

    if echo "$saida" | grep -qiE "^Error|Assignments can only"; then
        erro "falha ao resetar os offsets:"
        echo "$saida" | sed 's/^/   /'
        $COMPOSE start consumidor_categoriza_compras >/dev/null 2>&1
        return 1
    fi

    echo "$saida" | sed 's/^/   /'
    $COMPOSE start consumidor_categoriza_compras >/dev/null 2>&1
    ok "offsets zerados: o historico esta sendo reprocessado"
    echo "Acompanhe o lag no Console e o Painel sendo refeito."
}

cmd_catalogo() {
    case "${1:-listar}" in
        add)
            local desc="${2:-}" cat="${3:-}"
            [ -z "$desc" ] || [ -z "$cat" ] && {
                echo "uso: ./lab.sh catalogo add \"IFD*NOVA LOJA\" Alimentacao"; return 1; }
            titulo "ADICIONANDO AO CATALOGO VETORIAL"
            $COMPOSE run --rm -T preparar python -c "
import json, comum
from qdrant_client import models
q = comum.cliente_qdrant()
v = comum.vetorizar(['$desc'], tipo='passage')[0]
n = q.count(comum.COLECAO_CATALOGO).count
q.upsert(collection_name=comum.COLECAO_CATALOGO, points=[
    models.PointStruct(id=n+1000, vector=v,
        payload={'descricao': '$desc', 'categoria': '$cat'})])
print('adicionado: $desc -> $cat  (catalogo agora com', n+1, 'pontos)')
" || return 1
            echo
            echo "Agora rode ./lab.sh replay para reprocessar com o catalogo novo."
            ;;
        *)
            titulo "CATALOGO VETORIAL"
            curl -s "http://localhost:6333/collections/comerciantes" \
                | python3 -m json.tool 2>/dev/null | head -25
            ;;
    esac
}

cmd_ia_teste() {
    titulo "TESTE DIRETO NO LLM"
    local desc="${*:-IFD*CANTINA DO ZECA}"
    echo "Pergunta crua ao gemma:2b sobre: $desc"
    echo
    curl -s http://localhost:11434/api/generate -d "{
        \"model\": \"${OLLAMA_MODELO:-gemma:2b}\",
        \"prompt\": \"Categorize a compra de cartao \\\"$desc\\\" em uma palavra.\",
        \"stream\": false, \"options\": {\"temperature\": 0, \"num_predict\": 30}
    }" | python3 -c "
import json,sys
d=json.load(sys.stdin)
print('resposta :', d.get('response','').strip())
print('tempo    : %.1f s' % (d.get('total_duration',0)/1e9))
print('tokens/s : %.1f' % (d.get('eval_count',0)/(d.get('eval_duration',1)/1e9)))
" 2>/dev/null || erro "Ollama nao respondeu (./lab.sh ia)"
}

cmd_mcp() {
    garantir_env
    titulo "SERVIDOR MCP"
    echo "Sobe o servidor MCP e o Inspector oficial para explorar as tools."
    echo
    $COMPOSE --profile mcp up -d servidor_mcp_faturas 2>/dev/null \
        || aviso "servico MCP ainda nao configurado no compose"
    local ip; ip=$(grep '^IP_PUBLICO=' .env | cut -d= -f2-)
    echo "Inspector:  npx @modelcontextprotocol/inspector"
    echo "Servidor :  http://$ip:8008/mcp"
    echo
    echo "Para usar no Amazon Q:  ./lab.sh mcp-q"
}

cmd_mcp_q() {
    titulo "REGISTRANDO O MCP NO AMAZON Q CLI"
    mkdir -p ~/.aws/amazonq
    local destino=~/.aws/amazonq/mcp.json
    cat > "$destino" <<'JSON'
{
  "mcpServers": {
    "faturas": {
      "command": "docker",
      "args": ["exec", "-i", "servidor_mcp_faturas", "python", "-m", "mcp_faturas"],
      "timeout": 120000
    }
  }
}
JSON
    ok "gravado em $destino"
    echo
    echo "Agora:  q chat"
    echo "  e dentro da conversa:  /mcp     (confirma que carregou)"
    echo
    echo "Experimente perguntar:"
    echo "  \"Quanto eu gastei com transporte? Tem compra fora do padrao?\""
}

cmd_medir() {
    garantir_env
    titulo "MEDINDO O LAB NESTE AMBIENTE"
    $COMPOSE run --rm -T \
        -v "$PWD/faturas:/app/faturas_exemplo:ro" \
        consumidor_categoriza_compras python -m medir "$@"
    echo
    titulo "MEMORIA POR CONTEINER"
    docker stats --no-stream --format "table {{.Name}}\t{{.MemUsage}}\t{{.CPUPerc}}"
    echo
    echo "CPU/RAM da maquina:"
    nproc 2>/dev/null | sed 's/^/  vCPUs: /'
    free -h 2>/dev/null | sed -n '2p' | awk '{print "  RAM total: "$2"  usada: "$3"  livre: "$4}'
}

cmd_telas() {
    titulo "CAPTURANDO AS TELAS DAS FERRAMENTAS"
    bash infra/capturar_telas.sh
}

cmd_logs()     { $COMPOSE logs -f --tail=80 "${@:-}"; }
cmd_derrubar() {
    titulo "DERRUBANDO O AMBIENTE"
    read -r -p "Apagar tambem os dados (topicos, PDFs, vetores)? [s/N] " r
    if [[ "${r:-n}" =~ ^[sS]$ ]]; then
        $COMPOSE --profile ia --profile mcp down -v && ok "ambiente e dados removidos"
    else
        $COMPOSE --profile ia --profile mcp down && ok "conteineres removidos (dados preservados)"
    fi
}

# ------------------------------------------------------------
ajuda() {
    cat <<'TXT'

  LAB KAFKA-EVENT : Analisador Inteligente de Faturas

  PREPARAR
    ./lab.sh configurar          nome do aluno + webhook do Teams
    ./lab.sh subir               sobe tudo, menos o LLM
    ./lab.sh ia                  sobe o LLM local e o categorizador
    ./lab.sh urls                mostra os acessos
    ./lab.sh status              conteineres + memoria

  USAR
    ./lab.sh mensagem [texto]    modulo 1: so produtor -> topico -> Teams
    ./lab.sh enviar azul|verde   envia uma fatura (dispara o pipeline)
    ./lab.sh catalogo            ve o catalogo vetorial
    ./lab.sh catalogo add "DESC" Categoria
    ./lab.sh ia-teste [desc]     pergunta crua ao LLM, com tokens/s
    ./lab.sh medir [--completo]  mede latencia, acerto, RAM do ambiente

  EXPERIMENTOS
    ./lab.sh tempestade [n]      n faturas de uma vez -> gera lag
    ./lab.sh escalar [n]         n consumidores -> rebalanceamento
    ./lab.sh caos                derruba o LLM -> DLQ
    ./lab.sh replay              reprocessa o historico

  AVANCADO
    ./lab.sh mcp                 servidor MCP + Inspector
    ./lab.sh mcp-q               registra o MCP no Amazon Q CLI

  MANUTENCAO
    ./lab.sh telas               captura as telas das ferramentas (PNG)
    ./lab.sh logs [servico]      acompanha os logs
    ./lab.sh derrubar            encerra o ambiente

TXT
}

case "${1:-ajuda}" in
    configurar) cmd_configurar ;;
    subir)      cmd_subir ;;
    ia)         cmd_ia ;;
    urls)       cmd_urls ;;
    status)     cmd_status ;;
    mensagem)   shift; cmd_mensagem "$@" ;;
    enviar)     shift; cmd_enviar "$@" ;;
    tempestade) shift; cmd_tempestade "$@" ;;
    escalar)    shift; cmd_escalar "$@" ;;
    caos)       cmd_caos ;;
    replay)     cmd_replay ;;
    catalogo)   shift; cmd_catalogo "$@" ;;
    ia-teste)   shift; cmd_ia_teste "$@" ;;
    mcp)        cmd_mcp ;;
    mcp-q)      cmd_mcp_q ;;
    medir)      shift; cmd_medir "$@" ;;
    telas)      cmd_telas ;;
    logs)       shift; cmd_logs "$@" ;;
    derrubar)   cmd_derrubar ;;
    *)          ajuda ;;
esac
