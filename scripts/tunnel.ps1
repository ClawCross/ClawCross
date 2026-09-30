[CmdletBinding()]
param()
$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
& (Join-Path $projectRoot "selfskill\scripts\run.ps1") start-tunnel @args
exit $LASTEXITCODE
