[CmdletBinding()]
param(
    [Parameter(Position = 0)][string]$Command = "help",
    [Parameter(ValueFromRemainingArguments = $true)][string[]]$Rest
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
if ($Command -eq "dev") {
    $env:CLAWCROSS_HOME = Join-Path $projectRoot ".clawcross-dev"
    $Command = "start"
}
if ($env:CLAWCROSS_USE_LEGACY_PATHS -in @("1", "true", "yes", "on")) {
    $env:CLAWCROSS_HOME = $projectRoot
    $env:CLAWCROSS_VENV_DIR = Join-Path $projectRoot ".venv"
} else {
    if (-not $env:CLAWCROSS_HOME) { $env:CLAWCROSS_HOME = Join-Path $HOME ".clawcross" }
    if (-not $env:CLAWCROSS_VENV_DIR) { $env:CLAWCROSS_VENV_DIR = Join-Path $env:CLAWCROSS_HOME "venv" }
}
$python = Join-Path $env:CLAWCROSS_VENV_DIR "Scripts\python.exe"
$readOnly = @("status", "stop", "components", "stop-tunnel", "tunnel-status", "logs", "doctor", "check-openclaw", "check-openclaw-weixin")
$needsUv = @("start", "start-foreground", "start-fg", "restart", "setup", "install-component", "start-tunnel", "cli", "clawcross", "evolve-skill")
if ($Command -in @("help", "-h", "--help") -and -not (Test-Path $python)) {
    Write-Host "ClawCross commands: start, setup, stop, status, configure, components, install-component, logs, cli, help"
    exit 0
}
if (-not (Test-Path $python) -and $Command -in $readOnly) {
    $systemPython = Get-Command python -ErrorAction SilentlyContinue
    if ($systemPython) { $python = $systemPython.Source }
}
if (-not (Test-Path $python) -or $Command -in $needsUv) {
    $uv = $null
    $uvCommand = Get-Command uv -ErrorAction SilentlyContinue
    if (-not $uvCommand) {
        foreach ($candidate in @((Join-Path $HOME ".local\bin\uv.exe"), (Join-Path $HOME ".cargo\bin\uv.exe"))) {
            if (Test-Path $candidate) { $uv = $candidate; break }
        }
    }
    if (-not $uvCommand -and -not $uv) {
        $winget = Get-Command winget -ErrorAction SilentlyContinue
        if ($winget) {
            & $winget.Source install --id astral-sh.uv -e --source winget --accept-package-agreements --accept-source-agreements --silent
            if ($LASTEXITCODE -ne 0) { throw "uv installation failed" }
        } else {
            $installer = Join-Path ([System.IO.Path]::GetTempPath()) ("clawcross-uv-" + [guid]::NewGuid().ToString("N") + ".ps1")
            try {
                Invoke-WebRequest -Uri "https://astral.sh/uv/install.ps1" -OutFile $installer -UseBasicParsing
                & $installer
            } finally {
                Remove-Item $installer -Force -ErrorAction SilentlyContinue
            }
        }
        $uvCommand = Get-Command uv -ErrorAction SilentlyContinue
        if (-not $uvCommand) {
            foreach ($candidate in @((Join-Path $HOME ".local\bin\uv.exe"), (Join-Path $HOME ".cargo\bin\uv.exe"))) {
                if (Test-Path $candidate) { $uv = $candidate; break }
            }
            if (-not $uv -and $env:LOCALAPPDATA) {
                $packages = Join-Path $env:LOCALAPPDATA "Microsoft\WinGet\Packages"
                if (Test-Path $packages) {
                    $uv = Get-ChildItem $packages -Recurse -Filter uv.exe -ErrorAction SilentlyContinue |
                        Select-Object -First 1 -ExpandProperty FullName
                }
            }
            if (-not $uv) { throw "uv installation completed but uv.exe was not found" }
        }
    }
    if (-not $uv) { $uv = $uvCommand.Source }
    $env:CLAWCROSS_UV_BIN = $uv
    if (-not (Test-Path $python)) {
        & $uv venv $env:CLAWCROSS_VENV_DIR --python 3.11
        if ($LASTEXITCODE -ne 0) {
            & $uv python install 3.11
            if ($LASTEXITCODE -ne 0) { throw "Python 3.11 installation failed" }
            & $uv venv $env:CLAWCROSS_VENV_DIR --python 3.11
            if ($LASTEXITCODE -ne 0) { throw "Virtual environment creation failed" }
        }
    }
}
if (-not $env:CLAWCROSS_UV_BIN) {
    $uvCommand = Get-Command uv -ErrorAction SilentlyContinue
    if ($uvCommand) { $env:CLAWCROSS_UV_BIN = $uvCommand.Source }
}
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
& $python (Join-Path $projectRoot "launch\runtime_control.py") $Command @Rest
exit $LASTEXITCODE
