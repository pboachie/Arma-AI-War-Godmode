#!/usr/bin/env bash
# start.sh — Start God Mode: Arma Reforger Server + AI Middleware
set -euo pipefail

# ── Resolve paths ──────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
COMPOSE_FILE="$PROJECT_ROOT/docker/docker-compose.arma.yml"

cd "$SCRIPT_DIR"

# ── Detect docker compose command ────────────────────────────
if docker compose version &>/dev/null; then
    DOCKER_COMPOSE="docker compose"
elif command -v docker-compose &>/dev/null; then
    DOCKER_COMPOSE="docker-compose"
else
    DOCKER_COMPOSE=""
fi

# ── Signal trap: clean shutdown ────────────────────────────────
cleanup() {
    echo ""
    echo "Shutting down God Mode..."
    if [ -n "$DOCKER_COMPOSE" ] && [ -f "$COMPOSE_FILE" ]; then
        $DOCKER_COMPOSE -f "$COMPOSE_FILE" down 2>/dev/null || true
    fi
    echo "Shutdown complete."
    exit 0
}
trap cleanup SIGINT SIGTERM

# ── Load .env ──────────────────────────────────────────────────
if [ ! -f .env ]; then
    if [ -f .env.example ]; then
        echo "═══════════════════════════════════════════════"
        echo "  No .env file found. Creating from template..."
        echo "═══════════════════════════════════════════════"
        cp .env.example .env
        echo ""
        echo "  EDIT middleware/.env and set your API key"
        echo "  Then re-run this script."
        echo ""
    else
        echo "ERROR: No .env or .env.example found in $SCRIPT_DIR"
    fi
    exit 1
fi

set -a
source .env
set +a

# ── Validate LLM provider config ──────────────────────────────
PROVIDER="${LLM_PROVIDER:-anthropic}"

case "$PROVIDER" in
    anthropic)
        if [ "${ANTHROPIC_API_KEY:-}" = "sk-ant-your-key-here" ] || [ -z "${ANTHROPIC_API_KEY:-}" ]; then
            echo "ERROR: Set ANTHROPIC_API_KEY in .env"
            exit 1
        fi
        ;;
    openai)
        if [ -z "${OPENAI_API_KEY:-}" ]; then
            echo "ERROR: Set OPENAI_API_KEY in .env"
            exit 1
        fi
        ;;
    glm)
        if [ -z "${GLM_API_KEY:-}" ]; then
            echo "ERROR: Set GLM_API_KEY in .env (get one at https://open.z.ai/)"
            exit 1
        fi
        ;;
    ollama)
        ;;
    *)
        echo "ERROR: Unknown LLM_PROVIDER '$PROVIDER'. Use: anthropic, openai, glm, or ollama"
        exit 1
        ;;
esac

# ── Generate server config (if generator exists) ──────────────
CONFIG_GENERATOR="$PROJECT_ROOT/scripts/generate_config.py"
if [ -f "$CONFIG_GENERATOR" ]; then
    echo "Generating Arma Reforger server config..."
    python3 "$CONFIG_GENERATOR" || {
        echo "WARNING: Config generation failed, continuing with existing config"
    }
fi

# ── Start Arma Reforger server via Docker ─────────────────────
ARMA_STATUS="not started"
if [ -z "$DOCKER_COMPOSE" ]; then
    echo "NOTE: Docker Compose not found (tried 'docker compose' and 'docker-compose')"
    echo "Middleware will start without game server connection"
    ARMA_STATUS="unavailable (no compose)"
elif [ ! -f "$COMPOSE_FILE" ]; then
    echo "NOTE: Docker compose file not found at $COMPOSE_FILE"
    echo "Middleware will start without game server connection"
    ARMA_STATUS="no compose file"
else
    echo "Starting Arma Reforger Server..."
    $DOCKER_COMPOSE -f "$COMPOSE_FILE" up -d 2>/dev/null && {
        # Verify container is running
        if $DOCKER_COMPOSE -f "$COMPOSE_FILE" ps 2>/dev/null | grep -q "Up\|running"; then
            echo "Arma server container started (game may take a few minutes to fully load)"
            ARMA_STATUS="running"
        else
            ARMA_STATUS="starting"
        fi
    } || {
        echo "WARNING: Could not start Arma server (compose error)"
        echo "Middleware will start without game server connection"
        ARMA_STATUS="unavailable"
    }
fi

# ── Check Python dependencies ─────────────────────────────────
if ! python3 -c "import fastapi" 2>/dev/null; then
    echo "Installing Python dependencies..."
    pip install -r requirements.txt
fi

# ── Dev mode reload flag ──────────────────────────────────────
RELOAD_FLAG=""
if [ "${DEV_MODE:-}" = "1" ] || [ "${DEV_MODE:-}" = "true" ]; then
    RELOAD_FLAG="--reload"
fi

# ── Startup banner ────────────────────────────────────────────
MODEL="${LLM_MODEL:-auto}"
echo ""
echo "═══════════════════════════════════════════════"
echo "  God Mode — Arma Reforger AI Commander"
echo "═══════════════════════════════════════════════"
echo "  Arma Server: $ARMA_STATUS"
echo "  Middleware:   http://0.0.0.0:${PORT:-8080}"
echo "  Dashboard:    http://localhost:${PORT:-8080}/dashboard"
echo "  RCON:         port ${RCON_PORT:-19999}"
echo "  LLM Provider: $PROVIDER ($MODEL)"
if [ -n "$RELOAD_FLAG" ]; then
    echo "  Dev Mode:     auto-reload enabled"
fi
echo "═══════════════════════════════════════════════"
echo ""

# ── Kill any existing middleware on the same port ─────────────
MW_PORT="${PORT:-8080}"
if fuser "${MW_PORT}/tcp" >/dev/null 2>&1; then
    echo "Killing existing process on port ${MW_PORT}..."
    fuser -k "${MW_PORT}/tcp" >/dev/null 2>&1 || true
    sleep 1
fi

# ── Start middleware ──────────────────────────────────────────
exec uvicorn server:app --host 0.0.0.0 --port "$MW_PORT" $RELOAD_FLAG
