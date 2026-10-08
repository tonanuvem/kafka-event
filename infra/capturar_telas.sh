#!/usr/bin/env bash
# ============================================================
# Captura as telas das ferramentas do lab, com Chromium headless
# em conteiner. Serve para gerar as imagens do roteiro em Word e
# para o aluno registrar a propria execucao.
#
# Roda na VM do lab (Linux), onde --network host alcanca as portas
# publicadas. No Docker Desktop do macOS isso NAO funciona.
# ============================================================
set -uo pipefail

cd "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEST="$PWD/docs/telas"
mkdir -p "$DEST"

IMAGEM="zenika/alpine-chrome:latest"
docker image inspect "$IMAGEM" >/dev/null 2>&1 || docker pull -q "$IMAGEM"

capturar() {
    local nome="$1" url="$2" espera="${3:-6000}"
    printf "  %-34s " "$nome"
    docker run --rm --network host -v "$DEST":/saida \
        --entrypoint chromium-browser "$IMAGEM" \
        --headless=new --no-sandbox --disable-gpu --disable-dev-shm-usage \
        --hide-scrollbars --force-device-scale-factor=1.4 \
        --window-size=1680,1150 \
        --run-all-compositor-stages-before-draw \
        --virtual-time-budget="$espera" \
        --screenshot="/saida/$nome.png" "$url" >/dev/null 2>&1

    if [ -s "$DEST/$nome.png" ]; then
        echo "ok ($(du -h "$DEST/$nome.png" | cut -f1))"
    else
        echo "FALHOU"
    fi
}

echo ""
echo "=== capturando as telas do lab em $DEST ==="
echo ""

# --- Kafka: a espinha dorsal ---
capturar 01-console-topicos        "http://localhost:8080/topics"
capturar 02-console-grupos         "http://localhost:8080/groups"

# --- O produtor ---
capturar 03-swagger-produtor       "http://localhost:5001/docs"

# --- Claim-check: o arquivo de verdade ---
capturar 04-objetos-bucket         "http://localhost:8888/buckets/faturas/"
capturar 05-objetos-cluster        "http://localhost:9333/"

# --- Busca vetorial ---
capturar 06-qdrant-colecoes        "http://localhost:6333/dashboard#/collections"
capturar 07-qdrant-catalogo        "http://localhost:6333/dashboard#/collections/comerciantes"
capturar 08-qdrant-compras         "http://localhost:6333/dashboard#/collections/compras"
capturar 09-qdrant-visualize       "http://localhost:6333/dashboard#/collections/compras/visualize" 11000
capturar 10-qdrant-graph           "http://localhost:6333/dashboard#/collections/compras/graph" 11000

# --- O resultado de negocio ---
capturar 11-painel-gastos          "http://localhost:3000/" 9000

echo ""
echo "Telas em: $DEST"
ls -1 "$DEST" 2>/dev/null | sed 's/^/  /'
echo ""
