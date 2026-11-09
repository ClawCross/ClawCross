[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot ".")).Path
. (Join-Path $projectRoot "scripts\common.ps1")

function Invoke-ChildPowerShellScript {
    param(
        [Parameter(Mandatory = $true)]
        [string]$ScriptPath
    )

    $powershellExe = (Get-Process -Id $PID).Path
    if (-not $powershellExe) {
        $powershellExe = "powershell"
    }

    & $powershellExe -ExecutionPolicy Bypass -File $ScriptPath
    return $LASTEXITCODE
}

Set-ClawcrossUtf8
Initialize-ClawcrossRuntimePaths -ProjectRoot $projectRoot
Invoke-ClawcrossHomeMigration -ProjectRoot $projectRoot
$env:WEBOT_HEADLESS = "0"

Push-Location $env:CLAWCROSS_WORKSPACE_DIR
try {
    Write-Host "========== 1/4 Environment setup =========="
    $setupExitCode = Invoke-ChildPowerShellScript -ScriptPath (Join-Path $projectRoot "scripts\setup_env.ps1")
    if ($setupExitCode -ne 0) {
        Write-Host "❌ 环境配置失败，请检查错误信息"
        exit $setupExitCode
    }

    $venvPython = Get-VenvPython -ProjectRoot $projectRoot
    if (-not $venvPython) {
        Write-Host "❌ 虚拟环境不存在: $env:CLAWCROSS_VENV_DIR，请先运行: powershell -ExecutionPolicy Bypass -File .\selfskill\scripts\run.ps1 setup"
        Write-Host "   直接使用系统 python 可能是 Python 2.x，无法运行本项目"
        exit 1
    }

    $pyMajor = & $venvPython -c "import sys; print(sys.version_info.major)" 2>$null
    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($pyMajor) -or [int]$pyMajor -lt 3) {
        Write-Host "❌ 当前 venv python 指向 Python $pyMajor（$venvPython）"
        Write-Host "   本项目需要 Python 3.11+，请确保虚拟环境已正确激活"
        Write-Host "   推荐使用: powershell -ExecutionPolicy Bypass -File .\selfskill\scripts\run.ps1 start"
        exit 1
    }

    Write-Host ""
    Write-Host "========== 2/4 API Key 配置 =========="
    $apiKeyExitCode = Invoke-ChildPowerShellScript -ScriptPath (Join-Path $projectRoot "scripts\setup_apikey.ps1")
    if ($apiKeyExitCode -ne 0) {
        Write-Host "⚠️  API Key 配置未完成（可能已跳过或出错）"
    }

    $envPath = Join-Path $env:CLAWCROSS_CONFIG_DIR ".env"
    $envValues = Read-ClawcrossEnvFile -Path $envPath
    $modelName = ""
    if ($envValues.ContainsKey("LLM_MODEL")) {
        $modelName = [string]$envValues["LLM_MODEL"]
    }
    if ([string]::IsNullOrWhiteSpace($modelName)) {
        Write-Host ""
        Write-Host "⚠️  LLM_MODEL 尚未配置，先列出可用模型："
        & $venvPython (Join-Path $projectRoot "selfskill\scripts\configure.py") --auto-model
        Write-Host ""
        Write-Host "请先设置模型，例如："
        Write-Host "  powershell -ExecutionPolicy Bypass -File .\selfskill\scripts\run.ps1 configure LLM_MODEL gpt-5.4-mini"
        exit 1
    }

    Write-Host ""
    Write-Host "========== 3/4 用户管理 =========="
    $answer = Read-Host "是否需要添加新用户？(y/N)"
    if ($answer -match "^[Yy]$") {
        Invoke-ChildPowerShellScript -ScriptPath (Join-Path $projectRoot "scripts\adduser.ps1") | Out-Null
    }

    Write-Host ""
    Write-Host "========== 4/4 启动服务 =========="
    $tunnelAnswer = Read-Host "是否部署到公网？(y/N)"

    $tunnelProcess = $null
    if ($tunnelAnswer -match "^[Yy]$") {
        Write-Host "🌐 正在后台启动 Cloudflare Tunnel..."
        $tunnelStdOut = Join-Path $env:CLAWCROSS_LOG_DIR "manual_tunnel.out.log"
        $tunnelStdErr = Join-Path $env:CLAWCROSS_LOG_DIR "manual_tunnel.err.log"
        $tunnelProcess = Start-BackgroundPythonProcess `
            -ProjectRoot $projectRoot `
            -PythonPath $venvPython `
            -Arguments @("scripts\tunnel.py") `
            -StdOutLog $tunnelStdOut `
            -StdErrLog $tunnelStdErr
        Start-Sleep -Seconds 2
    }

    try {
        & $venvPython (Join-Path $projectRoot "scripts\launcher.py")
        exit $LASTEXITCODE
    } finally {
        if ($tunnelProcess) {
            Stop-Process -Id $tunnelProcess.Id -ErrorAction SilentlyContinue
        }
    }
} finally {
    Pop-Location
}
