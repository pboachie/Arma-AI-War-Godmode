#!/usr/bin/env bash
# stop.sh — Stop God Mode: Arma Server + Middleware
set -euo pipefail

# ── Resolve paths ──────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
COMPOSE_FILE="$PROJECT_ROOT/docker/docker-compose.arma.yml"

echo "═══════════════════════════════════════════════"
echo "  Stopping God Mode..."
echo "═══════════════════════════════════════════════"
echo ""

# ── Stop middleware (uvicorn) ─────────────────────────────────
UVICORN_PIDS=$(pgrep -f "uvicorn server:app" 2>/dev/null || true)
if [ -n "$UVICORN_PIDS" ]; then
    echo "Stopping middleware (uvicorn)..."
    echo "$UVICORN_PIDS" | xargs kill 2>/dev/null || true
    # Wait briefly for graceful shutdown
    sleep 2
    # Force kill if still running
    REMAINING=$(pgrep -f "uvicorn server:app" 2>/dev/null || true)
    if [ -n "$REMAINING" ]; then
        echo "  Force-killing remaining uvicorn processes..."
        echo "$REMAINING" | xargs kill -9 2>/dev/null || true
    fi
    echo "  Middleware stopped."
else
    echo "  Middleware not running."
fi

# ── Stop Arma Reforger server (Docker) ────────────────────────
if [ -f "$COMPOSE_FILE" ]; then
    if docker-compose -f "$COMPOSE_FILE" ps 2>/dev/null | grep -q "Up"; then
        echo "Stopping Arma Reforger server..."
        docker-compose -f "$COMPOSE_FILE" down 2>/dev/null || {
            echo "  WARNING: Failed to stop Docker containers"
        }
        echo "  Arma server stopped."
    else
        echo "  Arma server not running."
    fi
else
    echo "  No Docker compose file found (skipping)."
fi

# ── Status ────────────────────────────────────────────────────
echo ""
echo "═══════════════════════════════════════════════"
echo "  God Mode shutdown complete."
echo "═══════════════════════════════════════════════"
