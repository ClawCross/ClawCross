Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

$EnvFile = Join-Path $ProjectRoot "config/.env"
$ExampleFile = Join-Path $ProjectRoot "config/.env.example"

function Get-DotEnvValue {
    param(
        [string]$Path,
        [string]$Key
    )

    if (-not (Test-Path $Path)) {
        return $null
    }

    foreach ($line in Get-Content -Path $Path -Encoding UTF8) {
        if ($line -match "^(#\s*)?$([regex]::Escape($Key))=(.*)$") {
            return $matches[2]
        }
    }

    return $null
}

function Set-DotEnvValue {
    param(
        [string]$Path,
        [string]$Key,
        [string]$Value
    )

    $pattern = "^(#\s*)?$([regex]::Escape($Key))=.*$"
    $replacement = "$Key=$Value"
    $lines = @()

    if (Test-Path $Path) {
        $lines = @(Get-Content -Path $Path -Encoding UTF8)
    }

    $updated = $false
    $newLines = @(
        foreach ($line in $lines) {
        if ($line -match $pattern) {
            $updated = $true
            $replacement
        } else {
            $line
        }
    }
    )

    if (-not $updated) {
        if ($newLines.Count -gt 0 -and $newLines[-1] -ne "") {
            $newLines += ""
        }
        $newLines += $replacement
    }

    Set-Content -Path $Path -Value $newLines -Encoding UTF8
}

$existingKey = Get-DotEnvValue -Path $EnvFile -Key "LLM_API_KEY"
if ($existingKey -and $existingKey -ne "your_api_key_here") {
    if ($existingKey.Length -ge 12) {
        $maskedKey = "{0}...{1}" -f $existingKey.Substring(0, 8), $existingKey.Substring($existingKey.Length - 4)
    } else {
        $maskedKey = "[configured]"
    }

    Write-Host "API Key already configured ($maskedKey)"
    $reset = Read-Host "Reconfigure it? (y/N)"
    if ($reset -notmatch "^[Yy]$") {
        Write-Host "Keeping current configuration"
        exit 0
    }
}

Write-Host "================================================"
Write-Host "  Configure LLM API access"
Write-Host "  Supports DeepSeek / OpenAI / Gemini / Claude / MiniMax"
Write-Host "  / Antigravity-Manager (free via Google One Pro)"
Write-Host "================================================"
Write-Host ""

$apiKey = Read-Host "Enter API Key"
if ([string]::IsNullOrWhiteSpace($apiKey)) {
    Write-Host "No API Key entered. Skipping configuration."
    exit 1
}

$baseUrl = Read-Host "Enter API Base URL (default https://api.deepseek.com, no /v1)"
if ([string]::IsNullOrWhiteSpace($baseUrl)) {
    $baseUrl = "https://api.deepseek.com"
}

$existingModel = Get-DotEnvValue -Path $EnvFile -Key "LLM_MODEL"
$modelInput = Read-Host "Enter model name (leave blank to keep current, type 'auto' to discover later with auto-model)"
if ([string]::IsNullOrWhiteSpace($modelInput)) {
    $modelName = $existingModel
} elseif ($modelInput -match "^(auto|AUTO)$") {
    $modelName = ""
} else {
    $modelName = $modelInput
}

Write-Host ""
Write-Host "Optional audio settings. Leave them blank to follow the detected LLM provider automatically."
Write-Host "Current built-in audio defaults:"
Write-Host "  OpenAI -> TTS_MODEL=gpt-4o-mini-tts, TTS_VOICE=alloy, STT_MODEL=whisper-1"
Write-Host "  Gemini -> TTS_MODEL=gemini-2.5-flash-preview-tts, TTS_VOICE=charon"

$ttsModel = Read-Host "Enter TTS model name (leave blank to use the automatic provider default)"
$ttsVoice = ""
if (-not [string]::IsNullOrWhiteSpace($ttsModel)) {
    $ttsVoice = Read-Host "Enter TTS voice (leave blank to use the provider default)"
}
$sttModel = Read-Host "Enter speech-to-text model name (leave blank to use the automatic provider default if available)"

$visionInput = Read-Host "Does this model support vision/image input? (y/N)"
$visionSupport = if ($visionInput -match "^[Yy]$") { "true" } else { "false" }

$standardInput = Read-Host "Use OpenAI standard API mode? (Y/n)"
$standardMode = if ($standardInput -match "^[Nn]$") { "false" } else { "true" }

if (-not (Test-Path $EnvFile) -and (Test-Path $ExampleFile)) {
    Copy-Item $ExampleFile $EnvFile
}

Set-DotEnvValue -Path $EnvFile -Key "LLM_API_KEY" -Value $apiKey
Set-DotEnvValue -Path $EnvFile -Key "LLM_BASE_URL" -Value $baseUrl
Set-DotEnvValue -Path $EnvFile -Key "LLM_MODEL" -Value $modelName
Set-DotEnvValue -Path $EnvFile -Key "TTS_MODEL" -Value $ttsModel
Set-DotEnvValue -Path $EnvFile -Key "TTS_VOICE" -Value $ttsVoice
Set-DotEnvValue -Path $EnvFile -Key "STT_MODEL" -Value $sttModel
Set-DotEnvValue -Path $EnvFile -Key "LLM_VISION_SUPPORT" -Value $visionSupport
Set-DotEnvValue -Path $EnvFile -Key "OPENAI_STANDARD_MODE" -Value $standardMode

Write-Host "API configuration saved to config/.env"
if ([string]::IsNullOrWhiteSpace($modelName)) {
    Write-Host ""
    Write-Host "LLM_MODEL is still empty. Discover available models first:"
    Write-Host "  powershell -ExecutionPolicy Bypass -File .\selfskill\scripts\run.ps1 auto-model"
    Write-Host "Then set one explicitly:"
    Write-Host "  powershell -ExecutionPolicy Bypass -File .\selfskill\scripts\run.ps1 configure LLM_MODEL <model>"
}
