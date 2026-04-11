[CmdletBinding()]
param(
    [string]$PythonVersion = "3.11"
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
. (Join-Path $PSScriptRoot "common.ps1")

Set-ClawcrossUtf8
Push-Location $projectRoot

try {
    Write-Host "=========================================="
    Write-Host "  Clawcross Windows environment setup"
    Write-Host "=========================================="
    Write-Host ""

    $uv = Ensure-UvInstalled
    Write-Host "Detected uv at: $uv"

    $venvPython = Get-VenvPython -ProjectRoot $projectRoot
    if (-not $venvPython) {
        Write-Host "Creating .venv with Python $PythonVersion ..."
        & $uv venv .venv --python $PythonVersion
        if ($LASTEXITCODE -ne 0) {
            Write-Host "uv venv failed on the first attempt. Trying to install Python $PythonVersion via uv ..."
            & $uv python install $PythonVersion
            if ($LASTEXITCODE -ne 0) {
                throw "uv python install failed. Verify your network connection or install Python manually."
            }

            & $uv venv .venv --python $PythonVersion
            if ($LASTEXITCODE -ne 0) {
                throw "Failed to create .venv."
            }
        }

        $venvPython = Ensure-VenvPython -ProjectRoot $projectRoot
        Write-Host "Created virtual environment: $venvPython"
    } else {
        Write-Host "Virtual environment already exists: $venvPython"
    }

    Write-Host "Installing or updating Python dependencies ..."
    & $uv pip install -r config\requirements.txt --python $venvPython
    if ($LASTEXITCODE -ne 0) {
        throw "Dependency installation failed."
    }

    # --- acpx (ACP exchange), parity with setup_env.sh ---
    $acpxCmd = Get-Command acpx -ErrorAction SilentlyContinue
    if ($acpxCmd) {
        Write-Host ("✅ acpx already available: " + $acpxCmd.Source)
        try {
            & acpx --version 2>&1 | Out-Host
        } catch {
            Write-Host "(version check skipped)"
        }
    } else {
        Write-Host "📦 acpx not found, attempting install..."
        $npmCmd = Get-Command npm -ErrorAction SilentlyContinue
        if ($npmCmd) {
            $prevEap = $ErrorActionPreference
            $ErrorActionPreference = "Continue"
            try {
                & npm install -g acpx@latest 2>&1 | Out-Host
            } finally {
                $ErrorActionPreference = $prevEap
            }
            if ($LASTEXITCODE -ne 0) {
                Write-Host "⚠️  npm install -g acpx@latest exited with code $LASTEXITCODE (continuing)"
            }
            # Common npm global bin on Windows (often already on PATH after Node install)
            $npmBin = Join-Path $env:APPDATA "npm"
            if ((Test-Path $npmBin) -and ($env:PATH -notlike "*${npmBin}*")) {
                $env:PATH = "${npmBin};${env:PATH}"
                Write-Host "Prepended npm global bin to PATH for this session: $npmBin"
            }
            $acpxAfter = Get-Command acpx -ErrorAction SilentlyContinue
            if ($acpxAfter) {
                Write-Host ("✅ acpx installed: " + $acpxAfter.Source)
            } else {
                Write-Host "⚠️  acpx not found on PATH after install (group ACP features may be unavailable)"
                Write-Host "   Manual: npm install -g acpx@latest"
                Write-Host "   Ensure npm global directory is in your PATH (often $npmBin)"
            }
        } else {
            Write-Host "⚠️  npm not found; skipping acpx (group ACP features may be unavailable)"
            Write-Host "   After installing Node.js: npm install -g acpx@latest"
        }
    }

    Write-Host ""
    if (Test-Path "config\.env") {
        Write-Host "config/.env already exists"
    } else {
        Write-Host "config/.env is missing. Initialize it with:"
        Write-Host "  powershell -ExecutionPolicy Bypass -File .\selfskill\scripts\run.ps1 configure --init"
    }

    if (Test-Path "config\users.json") {
        Write-Host "config/users.json already exists"
    } else {
        Write-Host "config/users.json is missing. If you need password login, run:"
        Write-Host "  powershell -ExecutionPolicy Bypass -File .\scripts\adduser.ps1"
    }

    Write-Host ""
    Write-Host "Environment setup completed."
} finally {
    Pop-Location
}
