[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$projectRoot = $PSScriptRoot
. (Join-Path $projectRoot "scripts\common.ps1")

Set-ClawcrossUtf8
$runScript = Join-Path $projectRoot "selfskill\scripts\run.ps1"
$envPath = Join-Path $projectRoot "config\.env"

Write-Host "========== 1/4 Environment setup =========="
& (Join-Path $projectRoot "scripts\setup_env.ps1")

if (-not (Test-Path $envPath)) {
    Write-Host ""
    Write-Host "Initializing config/.env ..."
    & $runScript configure --init
}

$envValues = Read-ClawcrossEnvFile -Path $envPath
$apiKeyConfigured = $envValues.ContainsKey("LLM_API_KEY") -and `
    -not [string]::IsNullOrWhiteSpace($envValues["LLM_API_KEY"]) -and `
    $envValues["LLM_API_KEY"] -ne "your_api_key_here"

if (-not $apiKeyConfigured) {
    Write-Host ""
    Write-Host "========== 2/4 API Key setup =========="
    & (Join-Path $projectRoot "scripts\setup_apikey.ps1")
} else {
    Write-Host ""
    Write-Host "========== 2/4 API Key setup =========="
    Write-Host "LLM API configuration already exists."
}

$envValues = Read-ClawcrossEnvFile -Path $envPath
$modelConfigured = $envValues.ContainsKey("LLM_MODEL") -and `
    -not [string]::IsNullOrWhiteSpace($envValues["LLM_MODEL"])

if (-not $modelConfigured) {
    Write-Host ""
    Write-Host "LLM_MODEL is not configured yet. Listing available models..."
    & $runScript auto-model
    Write-Host ""
    Write-Host "Set one model before starting services, for example:"
    Write-Host "  powershell -ExecutionPolicy Bypass -File .\selfskill\scripts\run.ps1 configure LLM_MODEL gpt-5.4-mini"
    exit 1
}

Write-Host ""
Write-Host "========== 3/4 User management =========="
$addUser = Read-Host "Add a password user now? (y/N)"
if ($addUser -match "^[Yy]$") {
    & (Join-Path $projectRoot "scripts\adduser.ps1")
}

Write-Host ""
Write-Host "========== 4/4 Start services =========="
& $runScript start

$useTunnel = Read-Host "Start Cloudflare Tunnel for public access? (y/N)"
if ($useTunnel -match "^[Yy]$") {
    & $runScript start-tunnel
}
