[CmdletBinding()]
param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$Args
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    $python = Join-Path $root ".venv\bin\python"
}
if (-not (Test-Path $python)) {
    $pythonCmd = Get-Command python -ErrorAction SilentlyContinue
    if ($pythonCmd) {
        $python = $pythonCmd.Source
    } else {
        $pythonCmd = Get-Command python3 -ErrorAction SilentlyContinue
        if ($pythonCmd) {
            $python = $pythonCmd.Source
        } else {
            throw "Python was not found. Run setup/start first or install Python."
        }
    }
}

& $python (Join-Path $root "scripts\clawcross.py") @Args
exit $LASTEXITCODE
