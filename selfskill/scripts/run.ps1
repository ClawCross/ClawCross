[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [string]$Command = "help",
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$Rest
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
. (Join-Path $projectRoot "scripts\common.ps1")

Set-ClawcrossUtf8

$pidFile = Join-Path $projectRoot ".clawcross.pid"
$tunnelPidFile = Join-Path $projectRoot ".tunnel.pid"
$envPath = Join-Path $projectRoot "config\.env"

function Stop-ClawcrossTunnelForFreshStart {
    $touched = $false
    if (Test-TrackedProcessRunning -PidFile $tunnelPidFile) {
        Write-Host "Stopping existing Tunnel before starting a new one..."
        Stop-TrackedProcess -PidFile $tunnelPidFile | Out-Null
        $touched = $true
    } elseif (Test-Path $tunnelPidFile) {
        Write-Host "Removing stale .tunnel.pid"
        Remove-Item $tunnelPidFile -Force -ErrorAction SilentlyContinue
        $touched = $true
    }
    if ($touched -and (Test-Path $envPath)) {
        $raw = Get-Content $envPath -Raw -ErrorAction SilentlyContinue
        if ($raw -and ($raw -match '(?m)^PUBLIC_DOMAIN=')) {
            $cleared = $raw -replace '(?m)^PUBLIC_DOMAIN=.*', 'PUBLIC_DOMAIN='
            [System.IO.File]::WriteAllText($envPath, $cleared, [System.Text.UTF8Encoding]::new($false))
            Write-Host "Cleared PUBLIC_DOMAIN in config\.env"
        }
    }
}

function Invoke-ClawcrossPython {
    param(
        [Parameter(Mandatory = $true)]
        [string[]]$Arguments
    )

    $python = Ensure-VenvPython -ProjectRoot $projectRoot
    Push-Location $projectRoot
    try {
        & $python @Arguments
        return $LASTEXITCODE
    } finally {
        Pop-Location
    }
}

function Get-ClawcrossStartOptions {
    param([string[]]$FlagArgs)
    $noTunnel = $false
    $noOpenclaw = $false
    if ($null -ne $FlagArgs) {
        foreach ($a in $FlagArgs) {
            switch -Exact ($a) {
                '--no-tunnel' { $noTunnel = $true }
                '--no-openclaw' { $noOpenclaw = $true }
            }
        }
    }
    [pscustomobject]@{ NoTunnel = $noTunnel; NoOpenclaw = $noOpenclaw }
}

function Write-MagicLinks {
    $mlUser = $env:CLAWCROSS_MAGIC_LINK_USER
    if ([string]::IsNullOrWhiteSpace($mlUser)) { $mlUser = "default" }
    $python = Ensure-VenvPython -ProjectRoot $projectRoot
    Push-Location $projectRoot
    try {
        $raw = & $python "scripts\cli.py" "token" "generate" "-u" $mlUser "--valid-hours" "24" 2>&1
    } finally {
        Pop-Location
    }
    $token = $null
    foreach ($line in @($raw)) {
        if ($line -match "Token:\s*(\S+)") {
            $token = $Matches[1]
            break
        }
    }
    $ports = Get-ClawcrossPortMap -EnvPath $envPath
    $fe = $ports["PORT_FRONTEND"]
    Write-Host ""
    Write-Host "Magic link (passwordless login; do not share publicly):"
    Write-Host "  Login user_id=$mlUser (must match ?user= in URL). Override: `$env:CLAWCROSS_MAGIC_LINK_USER='your_id'"
    if (-not $token) {
        Write-Host "  Could not generate token (set INTERNAL_TOKEN in config\.env)."
        Write-Host "  Manual: uv run scripts\cli.py token generate -u $mlUser --valid-hours 24"
        return
    }
    Write-Host "  Local:  http://127.0.0.1:$fe/login-link/${token}?user=$mlUser"
    if ($env:CLAWCROSS_NO_TUNNEL -eq "1") {
        Write-Host "  Remote: (This start used --no-tunnel; Cloudflare Tunnel was not started.)"
        Write-Host ""
        Write-Host "FOR AI ASSISTANTS: Only the Local URL applies; do not claim a public/mobile URL was provided."
        return
    }
    $pd = Get-EnvValue -Key "PUBLIC_DOMAIN"
    if ($pd -and $pd -ne "wait to set") {
        Write-Host "  Remote: ${pd}/login-link/${token}?user=$mlUser"
        Write-Host "Mobile group chat: $pd/mobile_group_chat"
    } else {
        Write-Host "  Remote: (Tunnel not ready — PUBLIC_DOMAIN not set yet)"
        Write-Host "  When ready: powershell -ExecutionPolicy Bypass -File .\selfskill\scripts\run.ps1 tunnel-status"
    }
    Write-Host ""
    Write-Host "FOR AI ASSISTANTS: Copy the Local/Remote URLs above verbatim into your reply to the user. Do not omit them. Prefer Remote for phone/HTTPS when present."
}

function Get-EnvValue {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Key,
        [string]$DefaultValue = ""
    )

    $envValues = Read-ClawcrossEnvFile -Path $envPath
    if ($envValues.ContainsKey($Key) -and -not [string]::IsNullOrWhiteSpace($envValues[$Key])) {
        return $envValues[$Key]
    }

    return $DefaultValue
}

function Get-OpenClawCommand {
    foreach ($name in @("openclaw.cmd", "openclaw")) {
        $cmd = Get-Command $name -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($cmd) {
            return $cmd.Source
        }
    }

    $whereResult = & where.exe openclaw 2>$null | Select-Object -First 1
    if ($whereResult) {
        return $whereResult
    }

    return $null
}

function Invoke-OpenClawCommand {
    param(
        [Parameter(Mandatory = $true)]
        [string[]]$Arguments
    )

    $openclaw = Get-OpenClawCommand
    if (-not $openclaw) {
        throw "OpenClaw CLI was not found. Run check-openclaw first."
    }

    $result = Invoke-OpenClawAndCapture -OpenClawPath $openclaw -Arguments $Arguments
    if ($result.StdOut) {
        $result.StdOut -split "`r?`n" | Where-Object { $_ -ne "" } | ForEach-Object { Write-Host $_ }
    }
    if ($result.ExitCode -ne 0 -and $result.StdErr) {
        $result.StdErr -split "`r?`n" | Where-Object { $_ -ne "" } | ForEach-Object { Write-Host $_ }
    }

    return $result.ExitCode
}

function Invoke-OpenClawAndCapture {
    param(
        [Parameter(Mandatory = $true)]
        [string]$OpenClawPath,
        [Parameter(Mandatory = $true)]
        [string[]]$Arguments
    )

    $stdoutPath = [System.IO.Path]::GetTempFileName()
    $stderrPath = [System.IO.Path]::GetTempFileName()

    try {
        $process = Start-Process `
            -FilePath $OpenClawPath `
            -ArgumentList $Arguments `
            -Wait `
            -PassThru `
            -NoNewWindow `
            -RedirectStandardOutput $stdoutPath `
            -RedirectStandardError $stderrPath

        return [pscustomobject]@{
            ExitCode = $process.ExitCode
            StdOut = if (Test-Path $stdoutPath) { Get-Content $stdoutPath -Raw -ErrorAction SilentlyContinue } else { "" }
            StdErr = if (Test-Path $stderrPath) { Get-Content $stderrPath -Raw -ErrorAction SilentlyContinue } else { "" }
        }
    } finally {
        Remove-Item $stdoutPath -Force -ErrorAction SilentlyContinue
        Remove-Item $stderrPath -Force -ErrorAction SilentlyContinue
    }
}

function Get-OpenClawChannels {
    $openclaw = Get-OpenClawCommand
    if (-not $openclaw) {
        return @()
    }

    $result = Invoke-OpenClawAndCapture -OpenClawPath $openclaw -Arguments @("channels", "list", "--json")
    $raw = $result.StdOut.Trim()
    if ($result.ExitCode -ne 0 -or [string]::IsNullOrWhiteSpace($raw)) {
        return @()
    }

    $jsonStart = $raw.IndexOf("{")
    if ($jsonStart -lt 0) {
        return @()
    }

    try {
        $data = $raw.Substring($jsonStart) | ConvertFrom-Json -ErrorAction Stop
    } catch {
        return @()
    }

    $channels = @()
    if (-not $data.chat) {
        return $channels
    }

    foreach ($prop in $data.chat.PSObject.Properties) {
        $channelName = $prop.Name
        $accounts = $prop.Value
        if ($accounts -is [System.Array]) {
            foreach ($account in $accounts) {
                $bindKey = if ($account -eq "default") { $channelName } else { "$channelName`:$account" }
                $channels += [pscustomobject]@{
                    Channel = $channelName
                    Account = [string]$account
                    BindKey = $bindKey
                }
            }
        } elseif ($accounts) {
            $bindKey = if ($accounts -eq "default") { $channelName } else { "$channelName`:$accounts" }
            $channels += [pscustomobject]@{
                Channel = $channelName
                Account = [string]$accounts
                BindKey = $bindKey
            }
        }
    }

    return $channels
}

function Get-OpenClawAgentBindings {
    param(
        [Parameter(Mandatory = $true)]
        [string]$AgentName
    )

    $openclaw = Get-OpenClawCommand
    if (-not $openclaw) {
        return @()
    }

    $result = Invoke-OpenClawAndCapture -OpenClawPath $openclaw -Arguments @("agents", "list", "--bindings", "--json")
    $raw = $result.StdOut.Trim()
    if ($result.ExitCode -ne 0 -or [string]::IsNullOrWhiteSpace($raw)) {
        return @()
    }

    $jsonStart = $raw.IndexOf("[")
    if ($jsonStart -lt 0) {
        $jsonStart = $raw.IndexOf("{")
    }
    if ($jsonStart -lt 0) {
        return @()
    }

    try {
        $data = $raw.Substring($jsonStart) | ConvertFrom-Json -ErrorAction Stop
    } catch {
        return @()
    }

    $agents = @($data)
    foreach ($agent in $agents) {
        if ($agent.id -eq $AgentName -or $agent.name -eq $AgentName) {
            return @($agent.bindingDetails)
        }
    }

    return @()
}

function Assert-LlmModelConfigured {
    if ($env:CLAWCROSS_ALLOW_EMPTY_LLM_MODEL -eq "1") {
        return
    }

    $envValues = Read-ClawcrossEnvFile -Path $envPath
    $llmModel = ""
    if ($envValues.ContainsKey("LLM_MODEL")) {
        $llmModel = [string]$envValues["LLM_MODEL"]
    }

    if (-not [string]::IsNullOrWhiteSpace($llmModel) -and $llmModel -ne "wait to set") {
        return
    }

    Write-Host ""
    Write-Host "❌ LLM_MODEL 未配置，已停止启动。"
    Write-Host "   请先设置模型，例如：powershell -ExecutionPolicy Bypass -File .\selfskill\scripts\run.ps1 configure LLM_MODEL deepseek-chat"
    Write-Host "   可先查看可用模型：powershell -ExecutionPolicy Bypass -File .\selfskill\scripts\run.ps1 auto-model"
    Write-Host "   如需临时允许空模型启动，仅本次可设置：`$env:CLAWCROSS_ALLOW_EMPTY_LLM_MODEL='1'"
    exit 1
}

function Show-Help {
    Write-Host "Clawcross Windows PowerShell entry point"
    Write-Host ""
    Write-Host "Usage:"
    Write-Host "  powershell -ExecutionPolicy Bypass -File selfskill\scripts\run.ps1 <command> [args]"
    Write-Host ""
    Write-Host "Commands:"
    Write-Host "  setup                          Optional: full setup_env.ps1 (start runs it when venv/deps are missing)"
    Write-Host "  start [--no-tunnel] [--no-openclaw]   Background start; --no-tunnel skips Cloudflare; --no-openclaw skips OpenClaw import + gateway warm"
    Write-Host "  start-foreground [--no-openclaw] [--no-tunnel]   Foreground start; --no-openclaw same (--no-tunnel ignored; no tunnel in this mode)"
    Write-Host "  stop                           Stop services"
    Write-Host "  status                         Show current service status"
    Write-Host "  add-user <name> <password>     Create or update a password user"
    Write-Host "  configure ...                  Run selfskill/scripts/configure.py"
    Write-Host "  auto-model                     Query available models from the configured API"
    Write-Host "  sync-openclaw-llm              Sync Clawcross's current LLM config back to OpenClaw"
    Write-Host "  evolve-skill ...               Update a Markdown skill from execution failures"
    Write-Host "  cli ...                        Run scripts/cli.py"
    Write-Host "  check-openclaw                 Detect or install OpenClaw"
    Write-Host "  check-openclaw-weixin          Install or inspect the OpenClaw Weixin plugin"
    Write-Host "  bind-openclaw-channel <agent> <bind_key>  Bind an OpenClaw channel account to an agent"
    Write-Host "  start-tunnel                   Start Cloudflare Tunnel in the background"
    Write-Host "  stop-tunnel                    Stop Cloudflare Tunnel"
    Write-Host "  tunnel-status                  Show Cloudflare Tunnel status"
    Write-Host "  help                           Show this help"
    Write-Host ""
    Write-Host "Docs (read before creating/managing Teams):"
    Write-Host "  docs/build_team.md       - Create/configure Team (members, personas, JSON)"
    Write-Host "  docs/create_workflow.md  - Create OASIS workflow YAML (graph, persona types, examples)"
    Write-Host "  docs/cli.md              - Complete CLI command reference and examples"
    Write-Host "  docs/example_team.md     - Example Team file structure and content"
    Write-Host "  docs/openclaw-commands.md - OpenClaw agent integration commands"
    Write-Host ""
    Write-Host "Tip: Use 'uv run scripts/cli.py <command> --help' for detailed usage and docs"
}

function Show-PortChecks {
    param(
        [Parameter(Mandatory = $true)]
        [hashtable]$Checks,
        [Parameter(Mandatory = $true)]
        [hashtable]$Ports
    )

    foreach ($entry in $Ports.GetEnumerator()) {
        $check = $Checks[$entry.Key]
        if ($check.Available) {
            Write-Host "  $($entry.Key)=$($entry.Value) is available"
        } else {
            Write-Host "  $($entry.Key)=$($entry.Value) is blocked: $([string]::Join('; ', $check.Reasons))"
        }
    }
}

function Prepare-ClawcrossPorts {
    $resolution = Resolve-ClawcrossPortConfiguration -EnvPath $envPath

    if ($resolution.AutoUpdated) {
        Write-Host "The default Clawcross ports are blocked on this Windows machine."
        Write-Host "Updated config/.env to use a safe local port set:"
        foreach ($entry in $resolution.NewPorts.GetEnumerator()) {
            Write-Host "  $($entry.Key): $($resolution.CurrentPorts[$entry.Key]) -> $($entry.Value)"
        }
    } elseif ($resolution.RequiresManualUpdate) {
        Write-Host "The configured Clawcross ports are blocked and were not auto-changed because they are custom values."
        Show-PortChecks -Checks $resolution.Checks -Ports $resolution.CurrentPorts
        Write-Host "Update PORT_AGENT / PORT_SCHEDULER / PORT_OASIS / PORT_FRONTEND in config/.env, then try again."
        return $null
    }

    return Get-ClawcrossPortMap -EnvPath $envPath
}

function Show-StartupFailureDiagnostics {
    param(
        [Parameter(Mandatory = $true)]
        [string]$StdOutLog,
        [Parameter(Mandatory = $true)]
        [string]$StdErrLog
    )

    $stderrTail = Get-ClawcrossLogTail -Path $StdErrLog -LineCount 25
    if ($stderrTail.Count -gt 0) {
        Write-Host ""
        Write-Host "Last stderr lines:"
        foreach ($line in $stderrTail) {
            Write-Host "  $line"
        }
    }

    $stdoutTail = Get-ClawcrossLogTail -Path $StdOutLog -LineCount 15
    if ($stdoutTail.Count -gt 0) {
        Write-Host ""
        Write-Host "Last stdout lines:"
        foreach ($line in $stdoutTail) {
            Write-Host "  $line"
        }
    }
}

function Get-ClawcrossServiceProcesses {
    $scriptPatterns = @(
        "scripts[\\/]+launcher\.py",
        "src[\\/]+time\.py",
        "oasis[\\/]+server\.py",
        "src[\\/]+mainagent\.py",
        "src[\\/]+front\.py"
    )

    $candidatePids = New-Object System.Collections.Generic.List[int]
    $trackedPid = Get-TrackedProcessId -PidFile $pidFile
    if ($trackedPid) {
        $candidatePids.Add([int]$trackedPid)
    }

    if (Test-Path $envPath) {
        $ports = Get-ClawcrossPortMap -EnvPath $envPath
        foreach ($port in $ports.Values) {
            $listener = Get-ListeningPortInfo -Port $port
            if ($listener) {
                $candidatePids.Add([int]$listener.OwningProcess)
            }
        }
    }

    $matched = New-Object System.Collections.Generic.List[object]
    foreach ($pidValue in ($candidatePids | Sort-Object -Unique)) {
        $proc = Get-CimInstance Win32_Process -Filter "ProcessId = $pidValue" -ErrorAction SilentlyContinue
        if (-not $proc) {
            continue
        }

        if ($proc.Name -notin @("python.exe", "pythonw.exe")) {
            continue
        }

        $commandLine = $proc.CommandLine
        if (-not $commandLine) {
            continue
        }

        if ($scriptPatterns | Where-Object { $commandLine -match $_ }) {
            $matched.Add($proc)
        }
    }

    return @($matched | Sort-Object ProcessId -Unique)
}

function Stop-ClawcrossServiceProcesses {
    $serviceProcesses = @(Get-ClawcrossServiceProcesses)
    if ($serviceProcesses.Count -eq 0) {
        return $false
    }

    Write-Host "Found existing Clawcross service processes. Stopping them first..."
    foreach ($proc in $serviceProcesses) {
        Write-Host "  PID $($proc.ProcessId): $($proc.CommandLine)"
        Stop-Process -Id $proc.ProcessId -Force -ErrorAction SilentlyContinue
    }

    Start-Sleep -Seconds 1
    return $true
}

# 等价于 run.sh run_clawcross_setup_if_needed：缺 venv / 缺依赖 / 有 npm 无 acpx 时跑 setup_env.ps1
function Invoke-ClawcrossSetupIfNeeded {
    $venvPy = Get-VenvPython -ProjectRoot $projectRoot
    if (-not $venvPy) {
        Write-Host "📋 Virtualenv missing — running scripts\setup_env.ps1 ..."
        & (Join-Path $projectRoot "scripts\setup_env.ps1")
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
        return
    }
    $null = & $venvPy -c "import fastapi" 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-Host "📋 Python dependencies incomplete — running scripts\setup_env.ps1 ..."
        & (Join-Path $projectRoot "scripts\setup_env.ps1")
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
        return
    }
    $npmCmd = Get-Command npm -ErrorAction SilentlyContinue
    $acpxCmd = Get-Command acpx -ErrorAction SilentlyContinue
    if ($npmCmd -and -not $acpxCmd) {
        Write-Host "📋 npm is available but acpx is not on PATH — running scripts\setup_env.ps1 ..."
        & (Join-Path $projectRoot "scripts\setup_env.ps1")
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    }
}

# ---- uv 环境自检 & 自动配置 ----
Push-Location $projectRoot
try {
    # 确保 uv 可用（未安装则自动安装）
    $uv = Ensure-UvInstalled

    # 确保虚拟环境存在
    $venvPython = Get-VenvPython -ProjectRoot $projectRoot
    if (-not $venvPython) {
        Write-Host "Creating .venv with Python 3.11 ..."
        & $uv venv .venv --python 3.11
        if ($LASTEXITCODE -ne 0) {
            Write-Host "uv venv failed. Trying to install Python 3.11 via uv ..."
            & $uv python install 3.11
            if ($LASTEXITCODE -ne 0) {
                throw "uv python install failed. Verify your network connection or install Python manually."
            }
            & $uv venv .venv --python 3.11
            if ($LASTEXITCODE -ne 0) {
                throw "Failed to create .venv."
            }
        }
        $venvPython = Get-VenvPython -ProjectRoot $projectRoot
        Write-Host "Created virtual environment: $venvPython"
    }

    # 确保依赖已安装（通过尝试 import fastapi 判断）
    $importCheck = & $venvPython -c "import fastapi" 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Installing Python dependencies (config/requirements.txt) ..."
        & $uv pip install -r config\requirements.txt --python $venvPython
        if ($LASTEXITCODE -ne 0) {
            throw "Dependency installation failed."
        }
        Write-Host "Dependencies installed."
    }
} finally {
    Pop-Location
}

switch ($Command) {
    "setup" {
        & (Join-Path $projectRoot "scripts\setup_env.ps1")
        exit $LASTEXITCODE
    }

    "start" {
        $startOpts = Get-ClawcrossStartOptions -FlagArgs $Rest
        Invoke-ClawcrossSetupIfNeeded
        # Auto-create .env if missing
        if (-not (Test-Path $envPath)) {
            Write-Host "config/.env is missing, auto-initializing from template..."
            Invoke-ClawcrossPython -Arguments @("selfskill\scripts\configure.py", "--init") | Out-Null
        }

        if ($startOpts.NoOpenclaw) {
            Write-Host ""
            Write-Host "⏭️  --no-openclaw: skipping LLM import from OpenClaw; launcher will not warm OpenClaw gateway."
            $env:CLAWCROSS_NO_OPENCLAW = "1"
        } else {
            Remove-Item Env:\CLAWCROSS_NO_OPENCLAW -ErrorAction SilentlyContinue
            $envValues = Read-ClawcrossEnvFile -Path $envPath
            $llmKey = ""
            if ($envValues.ContainsKey("LLM_API_KEY")) { $llmKey = $envValues["LLM_API_KEY"] }
            if ([string]::IsNullOrWhiteSpace($llmKey) -or $llmKey -eq "your_api_key_here") {
                Write-Host ""
                Write-Host "🔄 LLM_API_KEY still empty/placeholder: trying OpenClaw -> Clawcross .env (optional)..."
                try {
                    Invoke-ClawcrossPython -Arguments @("selfskill\scripts\configure_openclaw.py", "--import-clawcross-llm-from-openclaw") | Out-Null
                } catch {
                    Write-Host "⚠️ OpenClaw import skipped or failed; continuing startup."
                }
            }
        }

        Assert-LlmModelConfigured

        Stop-ClawcrossServiceProcesses | Out-Null
        Remove-Item $pidFile -Force -ErrorAction SilentlyContinue

        $ports = Prepare-ClawcrossPorts
        if (-not $ports) {
            exit 1
        }

        if (Test-TrackedProcessRunning -PidFile $pidFile) {
            $oldPid = Get-TrackedProcessId -PidFile $pidFile
            Write-Host "Found an existing instance (PID: $oldPid). Stopping it first..."
            Stop-TrackedProcess -PidFile $pidFile | Out-Null
        } else {
            Remove-Item $pidFile -Force -ErrorAction SilentlyContinue
        }

        $python = Ensure-VenvPython -ProjectRoot $projectRoot
        $env:WEBOT_HEADLESS = "1"
        if ($startOpts.NoOpenclaw) {
            $env:CLAWCROSS_NO_OPENCLAW = "1"
        } else {
            Remove-Item Env:\CLAWCROSS_NO_OPENCLAW -ErrorAction SilentlyContinue
        }
        $stdoutLog = Join-Path $projectRoot "logs\launcher.out.log"
        $stderrLog = Join-Path $projectRoot "logs\launcher.err.log"
        $process = Start-BackgroundPythonProcess `
            -ProjectRoot $projectRoot `
            -PythonPath $python `
            -Arguments @("scripts\launcher.py") `
            -StdOutLog $stdoutLog `
            -StdErrLog $stderrLog

        Set-Content -Path $pidFile -Value $process.Id -Encoding UTF8
        $agentPort = [int]$ports["PORT_AGENT"]
        $frontendPort = [int]$ports["PORT_FRONTEND"]

        Write-Host "Service started. PID: $($process.Id)"
        Write-Host "Logs:"
        Write-Host "  stdout: $stdoutLog"
        Write-Host "  stderr: $stderrLog"
        Write-Host "If your terminal / CI / agent runner reaps child processes after the command exits, use:"
        Write-Host "  powershell -ExecutionPolicy Bypass -File .\selfskill\scripts\run.ps1 start-foreground"
        Write-Host "Waiting for http://127.0.0.1:$agentPort/v1/models ..."

        if (Wait-HttpEndpoint -Url "http://127.0.0.1:$agentPort/v1/models") {
            Write-Host "Service is ready."
            Write-Host "Web UI: http://127.0.0.1:$frontendPort"
            Write-Host ""
            Write-Host "==================================================="
            Invoke-ClawcrossPython -Arguments @("scripts\cli.py", "status") | Out-Null
            Write-Host ""
        } else {
            Start-Sleep -Seconds 1
            if (-not (Test-TrackedProcessRunning -PidFile $pidFile)) {
                Write-Host "Service exited during startup."
                Show-StartupFailureDiagnostics -StdOutLog $stdoutLog -StdErrLog $stderrLog
                exit 1
            }
            Write-Host "Service is still starting. Check status or logs if it does not become ready soon."
            Write-Host "Web UI (when ready): http://127.0.0.1:$frontendPort"
            Write-Host ""
            Write-Host "==================================================="
            Invoke-ClawcrossPython -Arguments @("scripts\cli.py", "status") | Out-Null
            Write-Host ""
        }

        if (-not $startOpts.NoTunnel) {
            Stop-ClawcrossTunnelForFreshStart
            Write-Host "Starting Cloudflare Tunnel for mobile remote access..."
            $python = Ensure-VenvPython -ProjectRoot $projectRoot
            $tunnelStdoutLog = Join-Path $projectRoot "logs\tunnel.out.log"
            $tunnelStderrLog = Join-Path $projectRoot "logs\tunnel.err.log"
            $tunnelProcess = Start-BackgroundPythonProcess `
                -ProjectRoot $projectRoot `
                -PythonPath $python `
                -Arguments @("scripts\tunnel.py") `
                -StdOutLog $tunnelStdoutLog `
                -StdErrLog $tunnelStderrLog
            Set-Content -Path $tunnelPidFile -Value $tunnelProcess.Id -Encoding UTF8
            Write-Host "Tunnel started. PID: $($tunnelProcess.Id)"

            $tunnelReady = $false
            for ($i = 0; $i -lt 20; $i++) {
                Start-Sleep -Seconds 2
                $envContent = Get-Content $envPath -ErrorAction SilentlyContinue | Out-String
                if ($envContent -match 'PUBLIC_DOMAIN=(https://\S+trycloudflare\.com\S*)') {
                    $publicDomain = $matches[1]
                    Write-Host "Mobile access: $publicDomain/mobile_group_chat"
                    $tunnelReady = $true
                    break
                }
            }
            if (-not $tunnelReady) {
                Write-Host "Tunnel is still starting. Check later: powershell -File selfskill\scripts\run.ps1 tunnel-status"
            }
        } else {
            Write-Host ""
            Write-Host "⏭️  --no-tunnel: skipping Cloudflare Tunnel (local access only)."
        }

        if ($startOpts.NoTunnel) {
            $env:CLAWCROSS_NO_TUNNEL = "1"
        } else {
            Remove-Item Env:\CLAWCROSS_NO_TUNNEL -ErrorAction SilentlyContinue
        }
        Write-MagicLinks
        Remove-Item Env:\CLAWCROSS_NO_TUNNEL -ErrorAction SilentlyContinue

        Write-Host ""
        Write-Host "Docs (read before creating/managing Teams):"
        Write-Host "  docs/build_team.md       - Create/configure Team (members, personas, JSON)"
        Write-Host "  docs/create_workflow.md  - Create OASIS workflow YAML (graph, persona types, examples)"
        Write-Host "  docs/cli.md              - Complete CLI command reference and examples"
        Write-Host "  docs/example_team.md     - Example Team file structure and content"
        Write-Host "  docs/openclaw-commands.md - OpenClaw agent integration commands"
        Write-Host ""
        Write-Host "Tip: Use 'uv run scripts/cli.py <command> --help' for detailed usage"
        exit 0
    }

    "start-foreground" {
        $startOpts = Get-ClawcrossStartOptions -FlagArgs $Rest
        Invoke-ClawcrossSetupIfNeeded
        # Auto-create .env if missing
        if (-not (Test-Path $envPath)) {
            Write-Host "config/.env is missing, auto-initializing from template..."
            Invoke-ClawcrossPython -Arguments @("selfskill\scripts\configure.py", "--init") | Out-Null
        }

        if ($startOpts.NoOpenclaw) {
            Write-Host ""
            Write-Host "⏭️  --no-openclaw: skipping LLM import from OpenClaw; launcher will not warm OpenClaw gateway."
            $env:CLAWCROSS_NO_OPENCLAW = "1"
        } else {
            Remove-Item Env:\CLAWCROSS_NO_OPENCLAW -ErrorAction SilentlyContinue
            $envValues = Read-ClawcrossEnvFile -Path $envPath
            $llmKey = ""
            if ($envValues.ContainsKey("LLM_API_KEY")) { $llmKey = $envValues["LLM_API_KEY"] }
            if ([string]::IsNullOrWhiteSpace($llmKey) -or $llmKey -eq "your_api_key_here") {
                Write-Host ""
                Write-Host "🔄 LLM_API_KEY still empty/placeholder: trying OpenClaw -> Clawcross .env (optional)..."
                try {
                    Invoke-ClawcrossPython -Arguments @("selfskill\scripts\configure_openclaw.py", "--import-clawcross-llm-from-openclaw") | Out-Null
                } catch {
                    Write-Host "⚠️ OpenClaw import skipped or failed; continuing startup."
                }
            }
        }

        Assert-LlmModelConfigured

        if ($startOpts.NoTunnel) {
            Write-Host ""
            Write-Host "ℹ️  start-foreground does not start Tunnel; --no-tunnel is ignored for this mode."
        }

        Stop-ClawcrossServiceProcesses | Out-Null
        Remove-Item $pidFile -Force -ErrorAction SilentlyContinue

        $ports = Prepare-ClawcrossPorts
        if (-not $ports) {
            exit 1
        }

        $python = Ensure-VenvPython -ProjectRoot $projectRoot
        $env:WEBOT_HEADLESS = "1"
        if ($startOpts.NoOpenclaw) {
            $env:CLAWCROSS_NO_OPENCLAW = "1"
        } else {
            Remove-Item Env:\CLAWCROSS_NO_OPENCLAW -ErrorAction SilentlyContinue
        }
        Write-Host "Starting Clawcross in the foreground (headless) ..."
        Write-Host "This session stays attached. Press Ctrl+C to stop all services."

        Push-Location $projectRoot
        try {
            & $python "scripts\launcher.py"
            exit $LASTEXITCODE
        } finally {
            Pop-Location
        }
    }

    "stop" {
        $stoppedTracked = Stop-TrackedProcess -PidFile $pidFile
        $stoppedChildren = Stop-ClawcrossServiceProcesses
        if ($stoppedTracked -or $stoppedChildren) {
            Remove-Item $pidFile -Force -ErrorAction SilentlyContinue
            Write-Host "Service stopped."
        } else {
            Write-Host "Service is not running."
        }
        exit 0
    }

    "status" {
        $ports = Get-ClawcrossPortMap -EnvPath $envPath
        $trackedRunning = Test-TrackedProcessRunning -PidFile $pidFile
        $serviceProcesses = @(Get-ClawcrossServiceProcesses)

        if (-not $trackedRunning -and $serviceProcesses.Count -eq 0) {
            Remove-Item $pidFile -Force -ErrorAction SilentlyContinue
            Write-Host "Service is not running."
            if (Test-Path $envPath) {
                $checks = @{}
                foreach ($entry in $ports.GetEnumerator()) {
                    $checks[$entry.Key] = Test-ClawcrossPortAvailability -Port $entry.Value
                }
                Show-PortChecks -Checks $checks -Ports $ports
            }
            exit 1
        }

        if ($trackedRunning) {
            $pidValue = Get-TrackedProcessId -PidFile $pidFile
            Write-Host "Service is running. PID: $pidValue"
        } else {
            Remove-Item $pidFile -Force -ErrorAction SilentlyContinue
            Write-Host "Service processes are running, but the tracked launcher PID is unavailable."
            Write-Host "This usually happens after a foreground launch or when an external process manager owns the launcher."
            foreach ($proc in $serviceProcesses) {
                Write-Host "  PID $($proc.ProcessId): $($proc.CommandLine)"
            }
        }

        foreach ($entry in $ports.GetEnumerator()) {
            $listener = Get-ListeningPortInfo -Port $entry.Value
            if ($listener) {
                Write-Host "  $($entry.Key)=$($entry.Value) is listening (PID $($listener.OwningProcess))"
            } else {
                $check = Test-ClawcrossPortAvailability -Port $entry.Value
                if ($check.Available) {
                    Write-Host "  $($entry.Key)=$($entry.Value) is not listening yet"
                } else {
                    Write-Host "  $($entry.Key)=$($entry.Value) is blocked: $([string]::Join('; ', $check.Reasons))"
                }
            }
        }

        Write-Host ""
        Write-Host "Docs (read before creating/managing Teams):"
        Write-Host "  docs/build_team.md       - Create/configure Team (members, personas, JSON)"
        Write-Host "  docs/create_workflow.md  - Create OASIS workflow YAML (graph, persona types, examples)"
        Write-Host "  docs/cli.md              - Complete CLI command reference and examples"
        Write-Host "  docs/example_team.md     - Example Team file structure and content"
        Write-Host "  docs/openclaw-commands.md - OpenClaw agent integration commands"
        Write-Host ""
        Write-Host "Tip: Use 'uv run scripts/cli.py <command> --help' for detailed usage"

        Write-MagicLinks

        exit 0
    }

    "add-user" {
        if ($Rest.Count -lt 2) {
            Write-Host "Usage: run.ps1 add-user <username> <password>"
            exit 1
        }

        $code = Invoke-ClawcrossPython -Arguments @("selfskill\scripts\adduser.py", $Rest[0], $Rest[1])
        exit $code
    }

    "configure" {
        if ($Rest.Count -eq 0) {
            Write-Host "Usage: run.ps1 configure <KEY> <VALUE> | --init | --show | --batch ..."
            exit 1
        }

        $code = Invoke-ClawcrossPython -Arguments (@("selfskill\scripts\configure.py") + $Rest)
        if ($code -ne 0) {
            exit $code
        }
        if ($Rest[0] -eq "--init") {
            Write-Host ""
            Write-Host "=== init 完成，自动触发 OpenClaw 检测 ==="
            $powershellExe = Join-Path $PSHOME "powershell.exe"
            if (-not (Test-Path $powershellExe)) {
                $powershellExe = "powershell"
            }
            & $powershellExe -ExecutionPolicy Bypass -File $PSCommandPath check-openclaw
            exit $LASTEXITCODE
        }
        exit 0
    }

    "auto-model" {
        $code = Invoke-ClawcrossPython -Arguments @("selfskill\scripts\configure.py", "--auto-model")
        exit $code
    }

    "sync-openclaw-llm" {
        $code = Invoke-ClawcrossPython -Arguments @("selfskill\scripts\configure_openclaw.py", "--sync-clawcross-llm")
        exit $code
    }

    "evolve-skill" {
        $code = Invoke-ClawcrossPython -Arguments (@("selfskill\scripts\evolve_skill.py") + $Rest)
        exit $code
    }

    "cli" {
        if ($Rest.Count -eq 0) {
            Write-Host "Usage: run.ps1 cli <command> [args]"
            exit 1
        }

        $code = Invoke-ClawcrossPython -Arguments (@("scripts\cli.py") + $Rest)
        exit $code
    }

    "check-openclaw" {
        $python = Ensure-VenvPython -ProjectRoot $projectRoot
        $openclaw = Get-OpenClawCommand
        if ($openclaw) {
            Write-Host "OpenClaw detected at: $openclaw"
            Push-Location $projectRoot
            try {
                & $python "selfskill\scripts\configure_openclaw.py" "--auto-detect"
                $code = $LASTEXITCODE
            } finally {
                Pop-Location
            }
            if ($code -eq 0 -and ((Test-TrackedProcessRunning -PidFile $pidFile) -or @(Get-ClawcrossServiceProcesses).Count -gt 0)) {
                Write-Host ""
                Write-Host "If Clawcross was already running before OpenClaw was installed or reconfigured,"
                Write-Host "restart Clawcross so OASIS reloads the openclaw CLI and gateway settings:"
                Write-Host "  powershell -ExecutionPolicy Bypass -File .\selfskill\scripts\run.ps1 stop"
                Write-Host "  powershell -ExecutionPolicy Bypass -File .\selfskill\scripts\run.ps1 start"
            }
            exit $code
        }

        $node = Get-Command node -ErrorAction SilentlyContinue
        $npm = Get-Command npm -ErrorAction SilentlyContinue
        if (-not $node -or -not $npm) {
            Write-Host "node/npm were not found."
            Write-Host "On native Windows, install Node.js 22+ first, or use the WSL flow documented in SKILL.md."
            exit 1
        }

        $nodeVersion = (& $node.Source --version).Trim()
        $nodeMajor = [int]($nodeVersion.TrimStart("v").Split(".")[0])
        if ($nodeMajor -lt 22) {
            Write-Host "Node.js is too old: $nodeVersion"
            Write-Host "Please upgrade to Node.js 22+ or use the WSL flow."
            exit 1
        }

        $shouldInstall = $env:OPENCLAW_AUTO_INSTALL -eq "1"
        if (-not $shouldInstall) {
            $reply = Read-Host "OpenClaw is missing. Install it now? [y/N]"
            $shouldInstall = $reply -match "^[Yy]"
        }

        if (-not $shouldInstall) {
            Write-Host "Skipped OpenClaw installation."
            exit 0
        }

        & $npm.Source install -g openclaw@latest --ignore-scripts
        if ($LASTEXITCODE -ne 0) {
            throw "OpenClaw installation failed. Check npm and your network connection."
        }

        & $python "selfskill\scripts\configure_openclaw.py" "--init-workspace"
        if ($LASTEXITCODE -ne 0) {
            throw "OpenClaw workspace initialization failed."
        }

        Write-Host "OpenClaw has been installed."
        Write-Host "Next, run:"
        Write-Host "  openclaw onboard --non-interactive --accept-risk --install-daemon"
        Write-Host "Optional if you want OpenClaw to reuse your existing OpenAI key:"
        Write-Host "  openclaw onboard --non-interactive --accept-risk --install-daemon --openai-api-key <LLM_API_KEY>"
        Write-Host "Then run:"
        Write-Host "  openclaw config set gateway.http.endpoints.chatCompletions.enabled true"
        Write-Host "  powershell -ExecutionPolicy Bypass -File .\selfskill\scripts\run.ps1 check-openclaw"
        Write-Host ""
        Write-Host "If the dashboard later says 'gateway token missing', either paste OPENCLAW_GATEWAY_TOKEN"
        Write-Host "into Control UI settings, or for loopback-only local use run:"
        Write-Host "  openclaw config set gateway.auth.mode none"
        Write-Host "  openclaw config unset gateway.auth.token"
        Write-Host "  openclaw gateway restart"
        exit 0
    }

    "check-openclaw-weixin" {
        $openclaw = Get-OpenClawCommand
        if (-not $openclaw) {
            Write-Host "OpenClaw is missing. Install or configure it first:"
            Write-Host "  powershell -ExecutionPolicy Bypass -File .\selfskill\scripts\run.ps1 check-openclaw"
            exit 1
        }

        Write-Host "Using OpenClaw CLI: $openclaw"
        if ($openclaw -like "*.cmd") {
            Write-Host "Windows note: using openclaw.cmd avoids PowerShell execution-policy issues from openclaw.ps1."
        }
        Write-Host "If the official npx installer says it cannot find openclaw on Windows,"
        Write-Host "this helper uses the manual plugin flow instead."

        $pluginEntry = Join-Path $env:USERPROFILE ".openclaw\extensions\openclaw-weixin\index.ts"
        $pluginInstalledNow = $false
        if (-not (Test-Path $pluginEntry)) {
            Write-Host ""
            Write-Host "Installing @tencent-weixin/openclaw-weixin ..."
            $code = Invoke-OpenClawCommand -Arguments @("plugins", "install", "@tencent-weixin/openclaw-weixin")
            if ($code -ne 0) {
                throw "OpenClaw Weixin plugin installation failed."
            }
            $pluginInstalledNow = $true
        } else {
            Write-Host ""
            Write-Host "Weixin plugin is already installed."
        }

        $code = Invoke-OpenClawCommand -Arguments @("config", "set", "plugins.entries.openclaw-weixin.enabled", "true")
        if ($code -ne 0) {
            throw "Failed to enable the OpenClaw Weixin plugin."
        }

        if ($pluginInstalledNow) {
            $code = Invoke-OpenClawCommand -Arguments @("gateway", "restart")
            if ($code -ne 0) {
                Write-Host "OpenClaw gateway restart reported an issue. Continue by checking 'openclaw status'."
            }
        }

        $channels = @(Get-OpenClawChannels | Where-Object { $_.Channel -eq "openclaw-weixin" })
        Write-Host ""
        if ($channels.Count -eq 0) {
            Write-Host "The plugin is installed but no Weixin account is logged in yet."
            Write-Host "Next step:"
            Write-Host "  $openclaw channels login --channel openclaw-weixin"
            Write-Host ""
            Write-Host "After you scan the QR code, verify with:"
            Write-Host "  $openclaw channels list --json"
            Write-Host "Then bind the account to an agent:"
            Write-Host "  powershell -ExecutionPolicy Bypass -File .\selfskill\scripts\run.ps1 bind-openclaw-channel main openclaw-weixin:<account_id>"
            exit 0
        }

        Write-Host "Detected Weixin channel accounts:"
        foreach ($channel in $channels) {
            Write-Host "  $($channel.BindKey)"
        }

        Write-Host ""
        Write-Host "Bind an account to an OpenClaw agent with:"
        foreach ($channel in $channels) {
            Write-Host "  powershell -ExecutionPolicy Bypass -File .\selfskill\scripts\run.ps1 bind-openclaw-channel main $($channel.BindKey)"
        }
        exit 0
    }

    "bind-openclaw-channel" {
        if ($Rest.Count -lt 2) {
            Write-Host "Usage: run.ps1 bind-openclaw-channel <agent> <bind_key>"
            Write-Host "Example:"
            Write-Host "  powershell -ExecutionPolicy Bypass -File .\selfskill\scripts\run.ps1 bind-openclaw-channel main openclaw-weixin:account-id"
            exit 1
        }

        $agentName = $Rest[0]
        $bindKey = $Rest[1]

        $code = Invoke-OpenClawCommand -Arguments @("agents", "bind", "--agent", $agentName, "--bind", $bindKey)
        if ($code -ne 0) {
            throw "OpenClaw channel binding failed."
        }

        Write-Host ""
        Write-Host "Current bindings for '$agentName':"
        $bindings = @(Get-OpenClawAgentBindings -AgentName $agentName)
        if ($bindings.Count -eq 0) {
            Write-Host "  (No bindings detected yet. Refresh OpenClaw / Clawcross and try again.)"
        } else {
            foreach ($binding in $bindings) {
                Write-Host "  $binding"
            }
        }

        Write-Host ""
        Write-Host "Refresh Clawcross's OpenClaw Channels tab or run:"
        Write-Host "  uv run scripts/cli.py openclaw bindings --agent $agentName"
        exit 0
    }

    "start-tunnel" {
        Stop-ClawcrossTunnelForFreshStart

        $python = Ensure-VenvPython -ProjectRoot $projectRoot
        $stdoutLog = Join-Path $projectRoot "logs\tunnel.out.log"
        $stderrLog = Join-Path $projectRoot "logs\tunnel.err.log"
        $process = Start-BackgroundPythonProcess `
            -ProjectRoot $projectRoot `
            -PythonPath $python `
            -Arguments @("scripts\tunnel.py") `
            -StdOutLog $stdoutLog `
            -StdErrLog $stderrLog

        Set-Content -Path $tunnelPidFile -Value $process.Id -Encoding UTF8
        Write-Host "Tunnel started. PID: $($process.Id)"
        Write-Host "Logs:"
        Write-Host "  stdout: $stdoutLog"
        Write-Host "  stderr: $stderrLog"
        Write-Host "Waiting for PUBLIC_DOMAIN (up to ~40s)..."
        $ready = $false
        for ($i = 0; $i -lt 20; $i++) {
            Start-Sleep -Seconds 2
            $envValues = Read-ClawcrossEnvFile -Path $envPath
            $pd = if ($envValues.ContainsKey("PUBLIC_DOMAIN")) { $envValues["PUBLIC_DOMAIN"] } else { "" }
            if ($pd -and $pd -ne "wait to set" -and $pd -match "trycloudflare\.com") {
                Write-Host "Public URL: $pd"
                $ready = $true
                break
            }
        }
        if (-not $ready) {
            Write-Host "Tunnel may still be starting; check logs or run tunnel-status later."
        }
        Write-MagicLinks
        exit 0
    }

    "stop-tunnel" {
        if (Stop-TrackedProcess -PidFile $tunnelPidFile) {
            Write-Host "Tunnel stopped."
        } else {
            Write-Host "Tunnel is not running."
        }
        exit 0
    }

    "tunnel-status" {
        if (-not (Test-TrackedProcessRunning -PidFile $tunnelPidFile)) {
            Write-Host "Tunnel is not running."
            exit 1
        }

        $pidValue = Get-TrackedProcessId -PidFile $tunnelPidFile
        Write-Host "Tunnel is running. PID: $pidValue"

        $envValues = Read-ClawcrossEnvFile -Path $envPath
        if ($envValues.ContainsKey("PUBLIC_DOMAIN") -and -not [string]::IsNullOrWhiteSpace($envValues["PUBLIC_DOMAIN"])) {
            Write-Host "Public URL: $($envValues["PUBLIC_DOMAIN"])"
        }

        Write-MagicLinks

        exit 0
    }

    "help" { Show-Help; exit 0 }
    "--help" { Show-Help; exit 0 }
    "-h" { Show-Help; exit 0 }

    default {
        Write-Host "Unknown command: $Command"
        Show-Help
        exit 1
    }
}
