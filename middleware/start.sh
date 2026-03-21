#!/usr/bin/env bash
# start.sh — Quick-start the God Mode middleware server
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# Check for .env
if [ ! -f .env ]; then
    echo "═══════════════════════════════════════════════"
    echo "  No .env file found. Creating from template..."
    echo "═══════════════════════════════════════════════"
    cp .env.example .env
    echo ""
    echo "  EDIT middleware/.env and set your ANTHROPIC_API_KEY"
    echo "  Then re-run this script."
    echo ""
    exit 1
fi

# Load .env
set -a
source .env
set +a

# Validate API key based on provider
PROVIDER="${LLM_PROVIDER:-anthropic}"

case "$PROVIDER" in
    anthropic)
        if [ "${ANTHROPIC_API_KEY:-}" = "sk-ant-your-key-here" ] || [ -z "${ANTHROPIC_API_KEY:-}" ]; then
            echo "ERROR: Set ANTHROPIC_API_KEY in .env"
            exit 1
        fi
        echo "  Provider: Anthropic (Claude)"
        ;;
    openai)
        if [ -z "${OPENAI_API_KEY:-}" ]; then
            echo "ERROR: Set OPENAI_API_KEY in .env"
            exit 1
        fi
        echo "  Provider: OpenAI"
        ;;
    glm)
        if [ -z "${GLM_API_KEY:-}" ]; then
            echo "ERROR: Set GLM_API_KEY in .env (get one at https://open.z.ai/)"
            exit 1
        fi
        echo "  Provider: Z.AI GLM (Coding)"
        echo "  Endpoint: ${GLM_BASE_URL:-https://api.z.ai/api/coding/paas/v4/}"
        ;;
    *)
        echo "ERROR: Unknown LLM_PROVIDER '$PROVIDER'. Use: anthropic, openai, or glm"
        exit 1
        ;;
esac

# Check Python deps
if ! python3 -c "import fastapi" 2>/dev/null; then
    echo "Installing Python dependencies..."
    pip install -r requirements.txt
fi

echo "═══════════════════════════════════════════════"
echo "  Starting God Mode Middleware Server"
echo "  Model:    ${LLM_MODEL:-auto}"
echo "  URL:      http://0.0.0.0:8000"
echo "═══════════════════════════════════════════════"
echo ""

uvicorn server:app --host 0.0.0.0 --port 8000 --reload
