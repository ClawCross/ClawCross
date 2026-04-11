[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
. (Join-Path $PSScriptRoot "common.ps1")

Set-ClawcrossUtf8
$python = Ensure-VenvPython -ProjectRoot $projectRoot
$envPath = Join-Path $projectRoot "config\.env"
$env:WEBOT_HEADLESS = "1"

if (-not (Test-Path $envPath)) {
    throw "config/.env is missing. Run selfskill\scripts\run.ps1 configure --init first."
}

$resolution = Resolve-ClawcrossPortConfiguration -EnvPath $envPath
if ($resolution.AutoUpdated) {
    Write-Host "Updated config/.env to avoid blocked default Windows ports."
    foreach ($entry in $resolution.NewPorts.GetEnumerator()) {
        Write-Host "  $($entry.Key): $($resolution.CurrentPorts[$entry.Key]) -> $($entry.Value)"
    }
} elseif ($resolution.RequiresManualUpdate) {
    Write-Host "The configured Clawcross ports are blocked and were not auto-changed because they are custom values."
    foreach ($entry in $resolution.CurrentPorts.GetEnumerator()) {
        $check = $resolution.Checks[$entry.Key]
        if (-not $check.Available) {
            Write-Host "  $($entry.Key)=$($entry.Value) is blocked: $([string]::Join('; ', $check.Reasons))"
        }
    }
    throw "Update the custom PORT_* values in config/.env and try again."
}

Push-Location $projectRoot
try {
    & $python "scripts\launcher.py"
    exit $LASTEXITCODE
} finally {
    Pop-Location
}
