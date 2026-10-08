<#
.SINOPSE
  Prepara o canal do Teams que vai receber os cards do laboratorio.

.CONTEXTO
  Os Incoming Webhooks do Teams (Office 365 Connectors) foram
  DESCONTINUADOS pela Microsoft. O substituto e um fluxo do Power
  Automate criado pelo app "Workflows", a partir do modelo
  "Publicar em um canal quando uma solicitacao de webhook for recebida".

.O QUE ESTE SCRIPT FAZ, E O QUE NAO FAZ
  FAZ de forma confiavel:
    - extrai o TeamId e o ChannelId a partir do "link do canal" que voce
      copia no Teams (um colar, sem precisar de permissao nenhuma);
    - gera o JSON da definicao do fluxo, pronto para importar;
    - TESTA um webhook existente, enviando um Adaptive Card de validacao.

  TENTA, mas pode nao conseguir:
    - criar o fluxo automaticamente. A API do Power Automate usada para
      isso (api.flow.microsoft.com) NAO e documentada nem suportada pela
      Microsoft, e politicas de DLP ou de licenciamento do tenant podem
      bloquear. Se falhar, o script mostra o caminho manual -- que sao
      seis cliques e e o procedimento oficial do roteiro.

.EXEMPLOS
  # 1) Extrair os IDs do canal da turma
  .\configurar_teams.ps1 -LinkDoCanal "https://teams.microsoft.com/l/channel/19%3Aabc...%40thread.tacv2/Lab%20Kafka?groupId=11111111-..."

  # 2) Validar um webhook que voce ja criou pelo app Workflows
  .\configurar_teams.ps1 -Webhook "https://prod-00.brazilsouth.logic.azure.com:443/workflows/..." -Testar

  # 3) Tentar criar o fluxo automaticamente (requer Azure CLI logado)
  .\configurar_teams.ps1 -LinkDoCanal "..." -CriarFluxo
#>

[CmdletBinding()]
param(
    [string]$LinkDoCanal,
    [string]$TeamId,
    [string]$ChannelId,
    [string]$Webhook,
    [string]$NomeDoFluxo = "Lab Kafka FIAP - Faturas",
    [switch]$Testar,
    [switch]$CriarFluxo
)

$ErrorActionPreference = "Stop"

function Escrever-Titulo($t) {
    Write-Host ""
    Write-Host ("=" * 62) -ForegroundColor Cyan
    Write-Host "  $t" -ForegroundColor Cyan
    Write-Host ("=" * 62) -ForegroundColor Cyan
    Write-Host ""
}

# ----------------------------------------------------------------------
# 1. IDs do canal a partir do link
#
# No Teams: canal -> ... -> "Obter link do canal". A URL carrega o
# threadId (o canal) e o groupId (a equipe). Este e o caminho robusto:
# nao depende de consentimento de escopos do Microsoft Graph.
# ----------------------------------------------------------------------
function Obter-IdsDoCanal {
    param([string]$Link)

    Add-Type -AssemblyName System.Web

    if ($Link -notmatch '/l/channel/([^/]+)/') {
        throw "Nao encontrei o trecho '/l/channel/<id>/' no link. Use 'Obter link do canal' no Teams."
    }
    $canal = [System.Web.HttpUtility]::UrlDecode($Matches[1])

    if ($Link -notmatch '[?&]groupId=([0-9a-fA-F-]{36})') {
        throw "Nao encontrei 'groupId=<guid>' no link."
    }
    $equipe = $Matches[1]

    return @{ ChannelId = $canal; TeamId = $equipe }
}

# ----------------------------------------------------------------------
# 2. Definicao do fluxo
#
# Gatilho: requisicao HTTP (manual/paLegacy). Acao: postar no canal.
# O corpo aceito e o mesmo que o lab envia: um Adaptive Card dentro de
# attachments. Ver servicos/notifica.py.
# ----------------------------------------------------------------------
function Novo-JsonDoFluxo {
    param([string]$Equipe, [string]$Canal, [string]$Nome)

    $definicao = [ordered]@{
        properties = [ordered]@{
            displayName = $Nome
            definition  = [ordered]@{
                '$schema'      = 'https://schema.management.azure.com/providers/Microsoft.Logic/schemas/2016-06-01/workflowdefinition.json#'
                contentVersion = '1.0.0.0'
                triggers       = [ordered]@{
                    'Quando_uma_solicitacao_de_webhook_for_recebida' = [ordered]@{
                        type = 'Request'
                        kind = 'Http'
                        inputs = [ordered]@{
                            schema = [ordered]@{
                                type = 'object'
                                properties = [ordered]@{
                                    type        = @{ type = 'string' }
                                    attachments = @{ type = 'array' }
                                    text        = @{ type = 'string' }
                                }
                            }
                        }
                    }
                }
                actions = [ordered]@{
                    'Postar_card_no_canal' = [ordered]@{
                        type = 'OpenApiConnection'
                        inputs = [ordered]@{
                            host = [ordered]@{
                                connectionName = 'shared_teams'
                                operationId    = 'PostCardToConversation'
                                apiId          = '/providers/Microsoft.PowerApps/apis/shared_teams'
                            }
                            parameters = [ordered]@{
                                poster    = 'Flow bot'
                                location  = 'Channel'
                                'body/recipient/groupId'   = $Equipe
                                'body/recipient/channelId' = $Canal
                                'body/messageBody'         = "@{string(triggerBody()?['attachments'][0]?['content'])}"
                            }
                        }
                    }
                }
            }
        }
    }
    return ($definicao | ConvertTo-Json -Depth 20)
}

# ----------------------------------------------------------------------
# 3. Teste do webhook : a parte mais util e 100% confiavel
# ----------------------------------------------------------------------
function Testar-Webhook {
    param([string]$Url)

    Escrever-Titulo "TESTANDO O WEBHOOK"

    if ([string]::IsNullOrWhiteSpace($Url)) {
        throw "Informe -Webhook com a URL gerada pelo app Workflows."
    }

    $card = @{
        type = 'message'
        attachments = @(@{
            contentType = 'application/vnd.microsoft.card.adaptive'
            content = @{
                type       = 'AdaptiveCard'
                '$schema'  = 'http://adaptivecards.io/schemas/adaptive-card.json'
                version    = '1.4'
                body       = @(
                    @{ type = 'TextBlock'; text = 'Lab Kafka FIAP - teste de webhook'
                       weight = 'Bolder'; size = 'Medium'; wrap = $true },
                    @{ type = 'TextBlock'; wrap = $true
                       text = 'Se este card apareceu no canal, o fluxo do Power Automate esta funcionando e o lab pode enviar os resumos de fatura.' },
                    @{ type = 'TextBlock'; wrap = $true; isSubtle = $true
                       text = ("Enviado em {0}" -f (Get-Date -Format 'dd/MM/yyyy HH:mm:ss')) }
                )
            }
        })
    } | ConvertTo-Json -Depth 20

    try {
        $r = Invoke-WebRequest -Uri $Url -Method Post -Body $card `
                 -ContentType 'application/json' -UseBasicParsing
        Write-Host "  HTTP $($r.StatusCode) - card enviado." -ForegroundColor Green
        Write-Host "  Confira o canal do Teams." -ForegroundColor Green
        Write-Host ""
        Write-Host "  Agora grave no .env do lab (na VM):" -ForegroundColor Yellow
        Write-Host "     TEAMS_WEBHOOK=$Url"
    }
    catch {
        Write-Host "  FALHOU: $($_.Exception.Message)" -ForegroundColor Red
        Write-Host ""
        Write-Host "  Causas comuns:" -ForegroundColor Yellow
        Write-Host "   - a URL expirou ou o fluxo foi desativado"
        Write-Host "   - o fluxo espera um corpo diferente (confira o esquema no gatilho)"
        Write-Host "   - falta a assinatura (?api-version=...&sig=...) no fim da URL"
        exit 1
    }
}

# ----------------------------------------------------------------------
# 4. Caminho manual : o procedimento OFICIAL do roteiro
# ----------------------------------------------------------------------
function Mostrar-CaminhoManual {
    param([string]$Equipe, [string]$Canal)

    Escrever-Titulo "CAMINHO MANUAL (6 CLIQUES) - PROCEDIMENTO OFICIAL"

    Write-Host @"
  1. No Teams, abra o canal da turma
  2. ... (mais opcoes) no canal  ->  Fluxos de trabalho / Workflows
  3. Procure o modelo:
       "Publicar em um canal quando uma solicitacao de webhook for recebida"
  4. Avance: confirme a conta, escolha a Equipe e o Canal
  5. Clique em "Adicionar fluxo de trabalho"
  6. COPIE a URL HTTP POST apresentada no fim

  Trate essa URL como SENHA: quem a tiver publica no canal da turma.

  Depois valide com:
       .\configurar_teams.ps1 -Webhook "<URL>" -Testar

  E grave no .env de cada aluno (ou distribua para a turma):
       TEAMS_WEBHOOK=<URL>
"@ -ForegroundColor White

    if ($Equipe) {
        Write-Host ""
        Write-Host "  Para conferencia, os IDs do seu canal:" -ForegroundColor Cyan
        Write-Host "    TeamId    : $Equipe"
        Write-Host "    ChannelId : $Canal"
    }
}

# ======================================================================
# PRINCIPAL
# ======================================================================
Escrever-Titulo "LAB KAFKA FIAP - CONFIGURACAO DO CANAL DO TEAMS"

if ($Testar) { Testar-Webhook -Url $Webhook; exit 0 }

if ($LinkDoCanal) {
    $ids = Obter-IdsDoCanal -Link $LinkDoCanal
    $TeamId    = $ids.TeamId
    $ChannelId = $ids.ChannelId
    Write-Host "  IDs extraidos do link:" -ForegroundColor Green
    Write-Host "    TeamId    : $TeamId"
    Write-Host "    ChannelId : $ChannelId"
}

if (-not $TeamId -or -not $ChannelId) {
    Write-Host "  Informe -LinkDoCanal (recomendado) ou -TeamId e -ChannelId." -ForegroundColor Yellow
    Mostrar-CaminhoManual
    exit 0
}

# Sempre grava a definicao: serve para importar o fluxo manualmente
# e para inspecao, mesmo que a criacao automatica falhe.
$arquivo = Join-Path $PSScriptRoot "fluxo_gerado.json"
Novo-JsonDoFluxo -Equipe $TeamId -Canal $ChannelId -Nome $NomeDoFluxo |
    Out-File -FilePath $arquivo -Encoding utf8
Write-Host ""
Write-Host "  Definicao do fluxo gravada em: $arquivo" -ForegroundColor Green

if (-not $CriarFluxo) {
    Mostrar-CaminhoManual -Equipe $TeamId -Canal $ChannelId
    Write-Host ""
    Write-Host "  (Para TENTAR a criacao automatica, repita com -CriarFluxo)" -ForegroundColor DarkGray
    exit 0
}

# --- tentativa de criacao automatica (API nao suportada) ---
Escrever-Titulo "TENTANDO CRIAR O FLUXO AUTOMATICAMENTE"
Write-Host "  AVISO: a API usada aqui nao e documentada nem suportada" -ForegroundColor Yellow
Write-Host "  pela Microsoft, e pode ser bloqueada pelo seu tenant." -ForegroundColor Yellow
Write-Host ""

if (-not (Get-Command az -ErrorAction SilentlyContinue)) {
    Write-Host "  Azure CLI (az) nao encontrado -- necessario para obter o token." -ForegroundColor Red
    Mostrar-CaminhoManual -Equipe $TeamId -Canal $ChannelId
    exit 1
}

try {
    $token = (az account get-access-token `
                --resource "https://service.flow.microsoft.com/" `
                --query accessToken -o tsv 2>$null)
    if (-not $token) { throw "token vazio (rode 'az login')" }

    $ambiente = (Invoke-RestMethod -Method Get `
        -Uri "https://api.flow.microsoft.com/providers/Microsoft.ProcessSimple/environments?api-version=2016-11-01" `
        -Headers @{ Authorization = "Bearer $token" }).value |
        Where-Object { $_.properties.isDefault } | Select-Object -First 1

    if (-not $ambiente) { throw "nenhum ambiente padrao do Power Automate encontrado" }
    Write-Host "  Ambiente: $($ambiente.name)" -ForegroundColor Green

    $resposta = Invoke-RestMethod -Method Post `
        -Uri "https://api.flow.microsoft.com/providers/Microsoft.ProcessSimple/environments/$($ambiente.name)/flows?api-version=2016-11-01" `
        -Headers @{ Authorization = "Bearer $token"; 'Content-Type' = 'application/json' } `
        -Body (Get-Content $arquivo -Raw)

    Write-Host "  Fluxo criado: $($resposta.name)" -ForegroundColor Green
    Write-Host ""
    Write-Host "  IMPORTANTE: abra o fluxo no portal do Power Automate," -ForegroundColor Yellow
    Write-Host "  autorize a conexao do Teams e copie a URL do gatilho." -ForegroundColor Yellow
    Write-Host "  https://make.powerautomate.com"
}
catch {
    Write-Host "  A criacao automatica falhou: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host "  Isto era esperado em muitos tenants. Use o caminho manual:" -ForegroundColor Yellow
    Mostrar-CaminhoManual -Equipe $TeamId -Canal $ChannelId
    exit 1
}
