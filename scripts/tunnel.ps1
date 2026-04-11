[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
. (Join-Path $PSScriptRoot "common.ps1")

Set-ClawcrossUtf8
$python = Ensure-VenvPython -ProjectRoot $projectRoot

Push-Location $projectRoot
try {
    & $python "scripts\tunnel.py"
    exit $LASTEXITCODE
} finally {
    Pop-Location
}
