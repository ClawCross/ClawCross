[CmdletBinding()]
param(
    [string]$PythonVersion = "3.11"
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
. (Join-Path $PSScriptRoot "common.ps1")

$internalRuntimeBase = Join-Path $HOME ".local\lib\openclaw-internal\runtime"

function Add-InternalRuntimePaths {
    $uvBin = Join-Path $internalRuntimeBase "uv\bin"
    $nodeBin = Join-Path $internalRuntimeBase "node\bin"

    if (Test-Path (Join-Path $uvBin "uv.exe")) {
        $env:PATH = "$uvBin;$env:PATH"
    }

    if (Test-Path (Join-Path $nodeBin "node.exe")) {
        $env:PATH = "$nodeBin;$env:PATH"
        $npmrc = Join-Path $internalRuntimeBase ".npmrc"
        if (Test-Path $npmrc) {
            $env:npm_config_userconfig = $npmrc
        }
    }
}

function Get-NodeMajorVersion {
    $nodeCmd = Get-Command node -ErrorAction SilentlyContinue
    if (-not $nodeCmd) {
        return $null
    }

    $nodeVersion = (& $nodeCmd.Source --version 2>$null).Trim()
    if ($nodeVersion -match '^v?(\d+)\.') {
        return [int]$Matches[1]
    }

    return $null
}

Set-ClawcrossUtf8
Initialize-ClawcrossRuntimePaths -ProjectRoot $projectRoot
Invoke-ClawcrossHomeMigration -ProjectRoot $projectRoot
Push-Location $env:CLAWCROSS_WORKSPACE_DIR

try {
    Write-Host "=========================================="
    Write-Host "  Clawcross Windows environment setup"
    Write-Host "=========================================="
    Write-Host ""

    Add-InternalRuntimePaths

    $nodeMajor = Get-NodeMajorVersion
    if ($null -eq $nodeMajor) {
        Write-Host "⚠️  Node.js 未安装，acpx 及 OpenClaw 功能不可用"
        Write-Host "   推荐安装 Node.js 22+，或使用腾讯内网版 OpenClaw 自带的 Node.js"
    } elseif ($nodeMajor -lt 22) {
        Write-Host "⚠️  Node.js 版本较低（检测到 v$nodeMajor），建议升级到 22+"
    }

    $uv = Ensure-UvInstalled
    Write-Host "Detected uv at: $uv"

    $venvPython = Get-VenvPython -ProjectRoot $projectRoot
    if (-not $venvPython) {
        Write-Host "Creating virtual environment at $env:CLAWCROSS_VENV_DIR with Python $PythonVersion ..."
        & $uv venv $env:CLAWCROSS_VENV_DIR --python $PythonVersion
        if ($LASTEXITCODE -ne 0) {
            Write-Host "uv venv failed on the first attempt. Trying to install Python $PythonVersion via uv ..."
            & $uv python install $PythonVersion
            if ($LASTEXITCODE -ne 0) {
                throw "uv python install failed. Verify your network connection or install Python manually."
            }

            & $uv venv $env:CLAWCROSS_VENV_DIR --python $PythonVersion
            if ($LASTEXITCODE -ne 0) {
                throw "Failed to create virtual environment."
            }
        }

        $venvPython = Ensure-VenvPython -ProjectRoot $projectRoot
        Write-Host "Created virtual environment: $venvPython"
    } else {
        Write-Host "Virtual environment already exists: $venvPython"
    }

    Write-Host "Installing or updating Python dependencies ..."
    & $uv pip install -r (Join-Path $projectRoot "config\requirements.txt") --python $venvPython
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
            if ((Test-Path $npmBin) -and ($env:PATH -notlike ('*' + $npmBin + '*'))) {
                $env:PATH = $npmBin + ';' + $env:PATH
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
    $openclawCmd = $null
    foreach ($candidate in @("openclaw.cmd", "openclaw")) {
        $cmd = Get-Command $candidate -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($cmd) {
            $openclawCmd = $cmd.Source
            break
        }
    }

    if ($openclawCmd) {
        $openclawVersion = ""
        try {
            $openclawVersion = (& $openclawCmd --version 2>$null | Select-Object -First 1)
        } catch {
            $openclawVersion = ""
        }
        if ($openclawVersion) {
            Write-Host "OpenClaw detected at: $openclawCmd"
            Write-Host "  Version: $openclawVersion"
        } else {
            Write-Host "OpenClaw detected at: $openclawCmd"
        }
    } else {
        Write-Host "OpenClaw not installed (optional component; core features still work)"
    }

    Write-Host ""
    if (Test-Path (Join-Path $env:CLAWCROSS_CONFIG_DIR ".env")) {
        Write-Host "config/.env already exists"
    } else {
        Write-Host "config/.env is missing. Initialize it with:"
        Write-Host "  powershell -ExecutionPolicy Bypass -File .\selfskill\scripts\run.ps1 configure --init"
    }

    if (Test-Path (Join-Path $env:CLAWCROSS_CONFIG_DIR "users.json")) {
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
