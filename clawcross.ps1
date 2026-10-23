$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
& (Join-Path $root "launch\run.ps1") clawcross @args
exit $LASTEXITCODE
