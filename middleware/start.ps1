# start.ps1 — Start God Mode: Arma Reforger Server + AI Middleware (Windows)
$ErrorActionPreference = "Stop"

# ── Resolve paths ──────────────────────────────────────────────
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptDir
$ComposeFile = Join-Path $ProjectRoot "docker\docker-compose.arma.yml"
$ComposeOverride = Join-Path $ProjectRoot "docker\docker-compose.windows.yml"

Set-Location $ScriptDir

# ── Detect docker compose command ────────────────────────────
$DockerCompose = $null
try {
    docker compose version 2>$null | Out-Null
    if ($LASTEXITCODE -eq 0) { $DockerCompose = "docker compose" }
} catch {}
if (-not $DockerCompose) {
    try {
        docker-compose version 2>$null | Out-Null
        if ($LASTEXITCODE -eq 0) { $DockerCompose = "docker-compose" }
    } catch {}
}

# ── Load .env ──────────────────────────────────────────────────
$EnvFile = Join-Path $ScriptDir ".env"
$EnvExample = Join-Path $ScriptDir ".env.example"

if (-not (Test-Path $EnvFile)) {
    if (Test-Path $EnvExample) {
        Write-Host "==============================================="
        Write-Host "  No .env file found. Creating from template..."
        Write-Host "==============================================="
        Copy-Item $EnvExample $EnvFile
        Write-Host ""
        Write-Host "  EDIT middleware\.env and set your API key"
        Write-Host "  Then re-run this script."
        Write-Host ""
    } else {
        Write-Host "ERROR: No .env or .env.example found in $ScriptDir"
    }
    exit 1
}

# Parse .env file and set environment variables
Get-Content $EnvFile | ForEach-Object {
    $line = $_.Trim()
    if ($line -and -not $line.StartsWith("#")) {
        $eqIndex = $line.IndexOf("=")
        if ($eqIndex -gt 0) {
            $key = $line.Substring(0, $eqIndex).Trim()
            $value = $line.Substring($eqIndex + 1).Trim()
            # Remove surrounding quotes if present
            if (($value.StartsWith('"') -and $value.EndsWith('"')) -or
                ($value.StartsWith("'") -and $value.EndsWith("'"))) {
                $value = $value.Substring(1, $value.Length - 2)
            }
            [Environment]::SetEnvironmentVariable($key, $value, "Process")
        }
    }
}

# ── Validate LLM provider config ──────────────────────────────
$Provider = if ($env:LLM_PROVIDER) { $env:LLM_PROVIDER } else { "anthropic" }

switch ($Provider) {
    "anthropic" {
        if (-not $env:ANTHROPIC_API_KEY -or $env:ANTHROPIC_API_KEY -eq "sk-ant-your-key-here") {
            Write-Host "ERROR: Set ANTHROPIC_API_KEY in .env"
            exit 1
        }
    }
    "openai" {
        if (-not $env:OPENAI_API_KEY) {
            Write-Host "ERROR: Set OPENAI_API_KEY in .env"
            exit 1
        }
    }
    "glm" {
        if (-not $env:GLM_API_KEY) {
            Write-Host "ERROR: Set GLM_API_KEY in .env (get one at https://open.z.ai/)"
            exit 1
        }
    }
    "ollama" { }
    default {
        Write-Host "ERROR: Unknown LLM_PROVIDER '$Provider'. Use: anthropic, openai, glm, or ollama"
        exit 1
    }
}

# ── Generate server config (if generator exists) ──────────────
$ConfigGenerator = Join-Path $ProjectRoot "scripts\generate_config.py"
if (Test-Path $ConfigGenerator) {
    Write-Host "Generating Arma Reforger server config..."
    try {
        python $ConfigGenerator
    } catch {
        Write-Host "WARNING: Config generation failed, continuing with existing config"
    }
}

# ── Start Arma Reforger server via Docker ─────────────────────
$ArmaStatus = "not started"
if (-not $DockerCompose) {
    Write-Host "NOTE: Docker Compose not found"
    Write-Host "Middleware will start without game server connection"
    $ArmaStatus = "unavailable (no compose)"
} elseif (-not (Test-Path $ComposeFile)) {
    Write-Host "NOTE: Docker compose file not found at $ComposeFile"
    Write-Host "Middleware will start without game server connection"
    $ArmaStatus = "no compose file"
} else {
    Write-Host "Starting Arma Reforger Server..."
    try {
        $composeArgs = "-f `"$ComposeFile`" -f `"$ComposeOverride`" up -d"
        Invoke-Expression "$DockerCompose $composeArgs" 2>$null
        $psOutput = Invoke-Expression "$DockerCompose -f `"$ComposeFile`" -f `"$ComposeOverride`" ps" 2>$null
        if ($psOutput -match "Up|running") {
            Write-Host "Arma server container started (game may take a few minutes to fully load)"
            $ArmaStatus = "running"
        } else {
            $ArmaStatus = "starting"
        }
    } catch {
        Write-Host "WARNING: Could not start Arma server (compose error)"
        Write-Host "Middleware will start without game server connection"
        $ArmaStatus = "unavailable"
    }
}

# ── Check Python dependencies ─────────────────────────────────
try {
    python -c "import fastapi" 2>$null
} catch {
    Write-Host "Installing Python dependencies..."
    pip install -r requirements.txt
}
if ($LASTEXITCODE -ne 0) {
    Write-Host "Installing Python dependencies..."
    pip install -r requirements.txt
}

# ── Dev mode reload flag ──────────────────────────────────────
$ReloadFlag = ""
if ($env:DEV_MODE -eq "1" -or $env:DEV_MODE -eq "true") {
    $ReloadFlag = "--reload"
}

# ── Startup banner ────────────────────────────────────────────
$Model = if ($env:LLM_MODEL) { $env:LLM_MODEL } else { "auto" }
$MwPort = if ($env:PORT) { $env:PORT } else { "8080" }
$RconPort = if ($env:RCON_PORT) { $env:RCON_PORT } else { "19999" }

Write-Host ""
Write-Host "==============================================="
Write-Host "  God Mode - Arma Reforger AI Commander"
Write-Host "==============================================="
Write-Host "  Arma Server: $ArmaStatus"
Write-Host "  Middleware:   http://0.0.0.0:$MwPort"
Write-Host "  Dashboard:    http://localhost:$MwPort/dashboard"
Write-Host "  RCON:         port $RconPort"
Write-Host "  LLM Provider: $Provider ($Model)"
if ($ReloadFlag) {
    Write-Host "  Dev Mode:     auto-reload enabled"
}
Write-Host "==============================================="
Write-Host ""

# ── Kill any existing middleware on the same port ─────────────
$existingProcess = Get-NetTCPConnection -LocalPort $MwPort -ErrorAction SilentlyContinue |
    Select-Object -ExpandProperty OwningProcess -Unique
if ($existingProcess) {
    Write-Host "Killing existing process on port $MwPort..."
    $existingProcess | ForEach-Object {
        Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue
    }
    Start-Sleep -Seconds 1
}

# ── Start middleware ──────────────────────────────────────────
$uvicornArgs = "server:app --host 0.0.0.0 --port $MwPort"
if ($ReloadFlag) { $uvicornArgs += " $ReloadFlag" }
Invoke-Expression "uvicorn $uvicornArgs"
