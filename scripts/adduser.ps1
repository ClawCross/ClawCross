[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
. (Join-Path $PSScriptRoot "common.ps1")

Set-ClawcrossUtf8
Initialize-ClawcrossRuntimePaths -ProjectRoot $projectRoot
$python = Ensure-VenvPython -ProjectRoot $projectRoot

Push-Location $env:CLAWCROSS_WORKSPACE_DIR
try {
    & $python (Join-Path $projectRoot "tools\gen_password.py")
    exit $LASTEXITCODE
} finally {
    Pop-Location
}
