[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
. (Join-Path $PSScriptRoot "common.ps1")

Set-ClawcrossUtf8
Initialize-ClawcrossRuntimePaths -ProjectRoot $projectRoot
$env:WEBOT_HEADLESS = "1"

Push-Location $env:CLAWCROSS_WORKSPACE_DIR
try {
    $python = Get-VenvPython -ProjectRoot $projectRoot
    if (-not $python) {
        $python = "python"
    }
    & $python (Join-Path $projectRoot "scripts\launcher.py")
    exit $LASTEXITCODE
} finally {
    Pop-Location
}
