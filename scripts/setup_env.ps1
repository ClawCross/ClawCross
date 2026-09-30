[CmdletBinding()]
param()
$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
& (Join-Path $projectRoot "selfskill\scripts\run.ps1") setup @args
exit $LASTEXITCODE
