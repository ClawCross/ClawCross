Set-StrictMode -Version Latest

function Set-ClawcrossUtf8 {
    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    [Console]::InputEncoding = $utf8NoBom
    [Console]::OutputEncoding = $utf8NoBom
    Set-Variable -Name OutputEncoding -Value $utf8NoBom -Scope Global
    $env:PYTHONUTF8 = "1"
    $env:PYTHONIOENCODING = "utf-8"
}

function Get-UvCommand {
    $uvCommand = Get-Command uv -ErrorAction SilentlyContinue
    if ($uvCommand) {
        return $uvCommand.Source
    }

    $wingetPackageRoot = Join-Path $env:LOCALAPPDATA "Microsoft\WinGet\Packages"
    if (Test-Path $wingetPackageRoot) {
        $uvExe = Get-ChildItem $wingetPackageRoot -Recurse -Filter "uv.exe" -ErrorAction SilentlyContinue |
            Select-Object -First 1 -ExpandProperty FullName
        if ($uvExe) {
            return $uvExe
        }
    }

    return $null
}

function Ensure-UvInstalled {
    $uv = Get-UvCommand
    if ($uv) {
        return $uv
    }

    $winget = Get-Command winget -ErrorAction SilentlyContinue
    if (-not $winget) {
        throw "uv was not found and winget is unavailable. Install uv first: https://docs.astral.sh/uv/getting-started/installation/"
    }

    Write-Host "uv was not found. Installing it with winget..."
    & $winget.Source install --id astral-sh.uv -e --source winget --accept-package-agreements --accept-source-agreements --silent
    if ($LASTEXITCODE -ne 0) {
        throw "uv installation failed. Run: winget install --id astral-sh.uv -e --source winget"
    }

    $uv = Get-UvCommand
    if (-not $uv) {
        throw "uv was installed but the current PowerShell session cannot see it yet. Reopen PowerShell and try again."
    }

    return $uv
}

function Get-VenvPython {
    param(
        [Parameter(Mandatory = $true)]
        [string]$ProjectRoot
    )

    $pythonPath = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
    if (Test-Path $pythonPath) {
        return $pythonPath
    }

    return $null
}

function Ensure-VenvPython {
    param(
        [Parameter(Mandatory = $true)]
        [string]$ProjectRoot
    )

    $pythonPath = Get-VenvPython -ProjectRoot $ProjectRoot
    if (-not $pythonPath) {
        throw ".venv\Scripts\python.exe was not found. Run scripts\setup_env.ps1 first."
    }

    return $pythonPath
}

function Read-ClawcrossEnvFile {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    $values = @{}
    if (-not (Test-Path $Path)) {
        return $values
    }

    foreach ($line in Get-Content -Path $Path -Encoding UTF8) {
        $trimmed = $line.Trim()
        if (-not $trimmed -or $trimmed.StartsWith("#") -or -not $trimmed.Contains("=")) {
            continue
        }

        $key, $value = $trimmed -split "=", 2
        $values[$key.Trim()] = $value.Trim()
    }

    return $values
}

function Set-ClawcrossEnvValues {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,
        [Parameter(Mandatory = $true)]
        [hashtable]$Updates
    )

    if ($Updates.Count -eq 0) {
        return
    }

    $directory = Split-Path -Parent $Path
    if ($directory) {
        New-Item -ItemType Directory -Path $directory -Force | Out-Null
    }

    $lines = @()
    if (Test-Path $Path) {
        $lines = Get-Content -Path $Path -Encoding UTF8
    }

    $pending = @{}
    foreach ($entry in $Updates.GetEnumerator()) {
        $pending[$entry.Key] = [string]$entry.Value
    }

    $outputLines = New-Object System.Collections.Generic.List[string]
    foreach ($line in $lines) {
        $replaced = $false
        foreach ($key in @($pending.Keys)) {
            $pattern = "^\s*#?\s*{0}=" -f [regex]::Escape($key)
            if ($line -match $pattern) {
                $outputLines.Add("$key=$($pending[$key])")
                $pending.Remove($key)
                $replaced = $true
                break
            }
        }

        if (-not $replaced) {
            $outputLines.Add($line)
        }
    }

    foreach ($key in $pending.Keys) {
        $outputLines.Add("$key=$($pending[$key])")
    }

    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllLines($Path, $outputLines, $utf8NoBom)
}

function Get-TrackedProcessId {
    param(
        [Parameter(Mandatory = $true)]
        [string]$PidFile
    )

    if (-not (Test-Path $PidFile)) {
        return $null
    }

    $raw = Get-Content -Path $PidFile -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $raw) {
        return $null
    }

    $pidValue = 0
    if ([int]::TryParse($raw.Trim(), [ref]$pidValue)) {
        return $pidValue
    }

    return $null
}

function Test-TrackedProcessRunning {
    param(
        [Parameter(Mandatory = $true)]
        [string]$PidFile
    )

    $pidValue = Get-TrackedProcessId -PidFile $PidFile
    if (-not $pidValue) {
        return $false
    }

    return [bool](Get-Process -Id $pidValue -ErrorAction SilentlyContinue)
}

function Stop-TrackedProcess {
    param(
        [Parameter(Mandatory = $true)]
        [string]$PidFile,
        [int]$WaitSeconds = 15
    )

    $pidValue = Get-TrackedProcessId -PidFile $PidFile
    if (-not $pidValue) {
        Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
        return $false
    }

    $process = Get-Process -Id $pidValue -ErrorAction SilentlyContinue
    if ($process) {
        Stop-Process -Id $pidValue -ErrorAction SilentlyContinue
        $deadline = (Get-Date).AddSeconds($WaitSeconds)
        while ((Get-Date) -lt $deadline) {
            if (-not (Get-Process -Id $pidValue -ErrorAction SilentlyContinue)) {
                break
            }
            Start-Sleep -Milliseconds 500
        }

        if (Get-Process -Id $pidValue -ErrorAction SilentlyContinue) {
            Stop-Process -Id $pidValue -Force -ErrorAction SilentlyContinue
            Start-Sleep -Seconds 1
        }
    }

    Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
    return $true
}

function Start-BackgroundPythonProcess {
    param(
        [Parameter(Mandatory = $true)]
        [string]$ProjectRoot,
        [Parameter(Mandatory = $true)]
        [string]$PythonPath,
        [Parameter(Mandatory = $true)]
        [string[]]$Arguments,
        [Parameter(Mandatory = $true)]
        [string]$StdOutLog,
        [Parameter(Mandatory = $true)]
        [string]$StdErrLog
    )

    $stdoutDir = Split-Path -Parent $StdOutLog
    $stderrDir = Split-Path -Parent $StdErrLog
    if ($stdoutDir) {
        New-Item -ItemType Directory -Path $stdoutDir -Force | Out-Null
    }
    if ($stderrDir) {
        New-Item -ItemType Directory -Path $stderrDir -Force | Out-Null
    }

    return Start-Process -FilePath $PythonPath `
        -ArgumentList $Arguments `
        -WorkingDirectory $ProjectRoot `
        -WindowStyle Hidden `
        -RedirectStandardOutput $StdOutLog `
        -RedirectStandardError $StdErrLog `
        -PassThru
}

function Wait-HttpEndpoint {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Url,
        [int]$Attempts = 30,
        [int]$DelaySeconds = 2
    )

    for ($i = 0; $i -lt $Attempts; $i++) {
        try {
            Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 5 | Out-Null
            return $true
        } catch {
            Start-Sleep -Seconds $DelaySeconds
        }
    }

    return $false
}

function Get-ListeningPortInfo {
    param(
        [Parameter(Mandatory = $true)]
        [int]$Port
    )

    try {
        $connection = Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction Stop | Select-Object -First 1
        if ($connection) {
            return [pscustomobject]@{
                LocalPort = $Port
                OwningProcess = $connection.OwningProcess
            }
        }
    } catch {
    }

    $netstatLine = netstat -ano -p tcp | Select-String -Pattern ("^\s*TCP\s+\S+:{0}\s+\S+\s+LISTENING\s+(\d+)\s*$" -f $Port) | Select-Object -First 1
    if ($netstatLine) {
        $parts = $netstatLine.Line.Trim() -split "\s+"
        $pidValue = $parts[-1]
        if ($pidValue -match "^\d+$") {
            return [pscustomobject]@{
                LocalPort = $Port
                OwningProcess = [int]$pidValue
            }
        }
    }

    return $null
}

function Test-LocalPort {
    param(
        [Parameter(Mandatory = $true)]
        [int]$Port
    )

    return [bool](Get-ListeningPortInfo -Port $Port)
}

function Get-ExcludedTcpPortRanges {
    $ranges = New-Object System.Collections.Generic.List[object]

    try {
        $output = netsh int ipv4 show excludedportrange protocol=tcp 2>$null
        foreach ($line in $output) {
            if ($line -match "^\s*(\d+)\s+(\d+)(\s+\*)?\s*$") {
                $ranges.Add([pscustomobject]@{
                    StartPort = [int]$matches[1]
                    EndPort = [int]$matches[2]
                })
            }
        }
    } catch {
    }

    return $ranges
}

function Test-TcpPortExcluded {
    param(
        [Parameter(Mandatory = $true)]
        [int]$Port
    )

    foreach ($range in Get-ExcludedTcpPortRanges) {
        if ($Port -ge $range.StartPort -and $Port -le $range.EndPort) {
            return $true
        }
    }

    return $false
}

function Test-ClawcrossPortAvailability {
    param(
        [Parameter(Mandatory = $true)]
        [int]$Port
    )

    $reasons = New-Object System.Collections.Generic.List[string]
    $listener = Get-ListeningPortInfo -Port $Port

    if (Test-TcpPortExcluded -Port $Port) {
        $reasons.Add("reserved by Windows excluded port ranges")
    }

    if ($listener) {
        $reasons.Add("already listening (PID $($listener.OwningProcess))")
    }

    return [pscustomobject]@{
        Port = $Port
        Available = ($reasons.Count -eq 0)
        Reasons = @($reasons)
    }
}

function Get-ClawcrossPortMap {
    param(
        [Parameter(Mandatory = $true)]
        [string]$EnvPath
    )

    $envValues = Read-ClawcrossEnvFile -Path $EnvPath
    $agentPort = "51200"
    $schedulerPort = "51201"
    $oasisPort = "51202"
    $frontendPort = "51209"
    if ($envValues.ContainsKey("PORT_AGENT") -and -not [string]::IsNullOrWhiteSpace($envValues["PORT_AGENT"])) {
        $agentPort = $envValues["PORT_AGENT"]
    }
    if ($envValues.ContainsKey("PORT_SCHEDULER") -and -not [string]::IsNullOrWhiteSpace($envValues["PORT_SCHEDULER"])) {
        $schedulerPort = $envValues["PORT_SCHEDULER"]
    }
    if ($envValues.ContainsKey("PORT_OASIS") -and -not [string]::IsNullOrWhiteSpace($envValues["PORT_OASIS"])) {
        $oasisPort = $envValues["PORT_OASIS"]
    }
    if ($envValues.ContainsKey("PORT_FRONTEND") -and -not [string]::IsNullOrWhiteSpace($envValues["PORT_FRONTEND"])) {
        $frontendPort = $envValues["PORT_FRONTEND"]
    }

    return [ordered]@{
        PORT_AGENT = [int]$agentPort
        PORT_SCHEDULER = [int]$schedulerPort
        PORT_OASIS = [int]$oasisPort
        PORT_FRONTEND = [int]$frontendPort
    }
}

function Find-ClawcrossAvailablePortSet {
    param(
        [int]$StartPort = 53000,
        [int]$EndPort = 64000
    )

    for ($basePort = $StartPort; $basePort -le $EndPort; $basePort += 20) {
        $candidate = [ordered]@{
            PORT_AGENT = $basePort
            PORT_SCHEDULER = $basePort + 1
            PORT_OASIS = $basePort + 2
            PORT_FRONTEND = $basePort + 9
        }

        $allAvailable = $true
        foreach ($port in $candidate.Values) {
            if (-not (Test-ClawcrossPortAvailability -Port $port).Available) {
                $allAvailable = $false
                break
            }
        }

        if ($allAvailable) {
            return $candidate
        }
    }

    throw "Could not find an available local port set for Clawcross."
}

function Resolve-ClawcrossPortConfiguration {
    param(
        [Parameter(Mandatory = $true)]
        [string]$EnvPath
    )

    $currentPorts = Get-ClawcrossPortMap -EnvPath $EnvPath
    $checks = @{}
    foreach ($entry in $currentPorts.GetEnumerator()) {
        $checks[$entry.Key] = Test-ClawcrossPortAvailability -Port $entry.Value
    }

    $blocked = @($checks.GetEnumerator() | Where-Object { -not $_.Value.Available })
    if ($blocked.Count -eq 0) {
        return [pscustomobject]@{
            AutoUpdated = $false
            RequiresManualUpdate = $false
            CurrentPorts = $currentPorts
            Checks = $checks
        }
    }

    $defaultPorts = [ordered]@{
        PORT_AGENT = 51200
        PORT_SCHEDULER = 51201
        PORT_OASIS = 51202
        PORT_FRONTEND = 51209
    }

    $usingDefaultLayout = $true
    foreach ($entry in $defaultPorts.GetEnumerator()) {
        if ($currentPorts[$entry.Key] -ne $entry.Value) {
            $usingDefaultLayout = $false
            break
        }
    }

    if (-not $usingDefaultLayout) {
        return [pscustomobject]@{
            AutoUpdated = $false
            RequiresManualUpdate = $true
            CurrentPorts = $currentPorts
            Checks = $checks
        }
    }

    $newPorts = Find-ClawcrossAvailablePortSet
    $updates = @{
        PORT_AGENT = $newPorts["PORT_AGENT"]
        PORT_SCHEDULER = $newPorts["PORT_SCHEDULER"]
        PORT_OASIS = $newPorts["PORT_OASIS"]
        PORT_FRONTEND = $newPorts["PORT_FRONTEND"]
    }

    $envValues = Read-ClawcrossEnvFile -Path $EnvPath
    $currentAgentUrl = "http://127.0.0.1:$($currentPorts["PORT_AGENT"])/v1/chat/completions"
    $currentOasisUrl = "http://127.0.0.1:$($currentPorts["PORT_OASIS"])"

    if (-not $envValues.ContainsKey("AI_API_URL") -or [string]::IsNullOrWhiteSpace($envValues["AI_API_URL"]) -or $envValues["AI_API_URL"] -eq $currentAgentUrl) {
        $updates["AI_API_URL"] = "http://127.0.0.1:$($newPorts["PORT_AGENT"])/v1/chat/completions"
    }

    if (-not $envValues.ContainsKey("OASIS_BASE_URL") -or [string]::IsNullOrWhiteSpace($envValues["OASIS_BASE_URL"]) -or $envValues["OASIS_BASE_URL"] -eq $currentOasisUrl) {
        $updates["OASIS_BASE_URL"] = "http://127.0.0.1:$($newPorts["PORT_OASIS"])"
    }

    Set-ClawcrossEnvValues -Path $EnvPath -Updates $updates

    return [pscustomobject]@{
        AutoUpdated = $true
        RequiresManualUpdate = $false
        CurrentPorts = $currentPorts
        NewPorts = $newPorts
        Checks = $checks
        UpdatedKeys = @($updates.Keys)
    }
}

function Get-ClawcrossLogTail {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,
        [int]$LineCount = 40
    )

    if (-not (Test-Path $Path)) {
        return @()
    }

    return @(Get-Content -Path $Path -Tail $LineCount -ErrorAction SilentlyContinue)
}
