"""
GM Server — FastAPI middleware between Arma Reforger and LLM.

Supports three LLM providers:
  - Anthropic (Claude)  → LLM_PROVIDER=anthropic  ANTHROPIC_API_KEY=sk-ant-...
  - OpenAI (GPT)        → LLM_PROVIDER=openai     OPENAI_API_KEY=sk-...
  - Z.AI GLM (Coding)   → LLM_PROVIDER=glm        GLM_API_KEY=...

Run:  uvicorn server:app --host 0.0.0.0 --port 8000
"""

import json
import time
import logging
import os
from datetime import datetime
from typing import Optional

from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# ─── Logging ───
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("godmode")

# ─── App ───
app = FastAPI(
    title="Arma Reforger God Mode — LLM Middleware",
    version="1.0.0",
    description="Bridges Arma Reforger game state to an LLM for dynamic AI command generation.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── Config ───
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "anthropic")  # "anthropic", "openai", or "glm"
MODEL_NAME = os.getenv("LLM_MODEL", "claude-sonnet-4-20250514")
MAX_TOKENS = int(os.getenv("LLM_MAX_TOKENS", "2000"))
MAX_HISTORY = int(os.getenv("MAX_HISTORY", "20"))

# GLM-specific config
# Coding endpoint (optimized for code/agent tasks): https://api.z.ai/api/coding/paas/v4/
# General endpoint:                                 https://api.z.ai/api/paas/v4/
GLM_BASE_URL = os.getenv("GLM_BASE_URL", "https://api.z.ai/api/coding/paas/v4/")
GLM_API_KEY = os.getenv("GLM_API_KEY", "")

# Provider defaults — applied when LLM_MODEL is not explicitly set
_PROVIDER_DEFAULT_MODELS = {
    "anthropic": "claude-sonnet-4-20250514",
    "openai": "gpt-4o",
    "glm": "glm-4.7",
}
if os.getenv("LLM_MODEL") is None:
    MODEL_NAME = _PROVIDER_DEFAULT_MODELS.get(LLM_PROVIDER, MODEL_NAME)

# ─── State ───
game_history: list[dict] = []
stats = {
    "total_requests": 0,
    "total_errors": 0,
    "total_commands_issued": 0,
    "total_input_tokens": 0,
    "total_output_tokens": 0,
    "start_time": time.time(),
}

# ─── LLM Client (lazy init) ───
_llm_client = None


def get_llm_client():
    global _llm_client
    if _llm_client is None:
        if LLM_PROVIDER == "anthropic":
            from anthropic import Anthropic
            _llm_client = Anthropic()
            log.info("Anthropic client initialized (model: %s)", MODEL_NAME)
        elif LLM_PROVIDER == "openai":
            from openai import OpenAI
            _llm_client = OpenAI()
            log.info("OpenAI client initialized (model: %s)", MODEL_NAME)
        elif LLM_PROVIDER == "glm":
            from openai import OpenAI
            api_key = GLM_API_KEY
            if not api_key:
                raise ValueError(
                    "GLM_API_KEY is required when LLM_PROVIDER=glm. "
                    "Get one at https://open.z.ai/"
                )
            _llm_client = OpenAI(
                api_key=api_key,
                base_url=GLM_BASE_URL,
            )
            log.info(
                "GLM client initialized (model: %s, endpoint: %s)",
                MODEL_NAME,
                GLM_BASE_URL,
            )
        else:
            raise ValueError(
                f"Unknown LLM_PROVIDER: {LLM_PROVIDER}. "
                f"Supported: anthropic, openai, glm"
            )
    return _llm_client


# ═══════════════════════════════════════════════════════════
# System Prompt
# ═══════════════════════════════════════════════════════════

SYSTEM_PROMPT = """You are STAVKA, the AI commander for OPFOR forces in a military simulation.
You control Soviet/Russian forces opposing human players in Arma Reforger.

AVAILABLE COMMANDS (respond with a JSON object containing a "commands" array):

MOVEMENT & COMBAT:
- move: Move a squad to a position
- attack: Order squad to assault a position
- defend: Order squad to hold a position
- patrol: Set squad on patrol circuit around a center point
- set_ambush: Position squad for an ambush
- seek_and_destroy: Hunt enemies in an area
- retreat: Pull squad back to fallback position

SPAWNING:
- spawn_infantry: Spawn a new infantry group
  Types: ussr_rifle_squad, ussr_fire_team, ussr_mg_team, ussr_at_team, ussr_sniper_team
- spawn_vehicle: Spawn a vehicle
  Types: uaz469, uaz469_mg, btr70, ural_truck, bmp1, t72
- spawn_reinforcements: Spawn reinforcements at a position

ENVIRONMENT:
- set_weather: Change weather (Clear, Cloudy, Overcast, Rainy, Storm, Foggy)
- set_time: Set time of day (0.0-24.0)
- set_wind: Set wind "speed_ms,direction_degrees"
- thunder_near: Play thunder near a position

EFFECTS:
- explosion: Spawn explosion (parameters: small, large, vehicle)
- smoke: Spawn smoke (parameters: white, red, green)
- flare: Launch illumination flare above a position

CLEANUP:
- cleanup_area: Clean up dead bodies/wrecks in radius (parameters: radius in meters)
- cleanup_dead: Clean up all dead bodies on map
- despawn_unit: Remove a unit from the battlefield

NARRATIVE:
- radio_message: Send radio message to players (narrative=message, parameters=faction)
- objective_create: Create mission objective (parameters=type, narrative=description)
- objective_complete: Mark objective as completed (parameters=objective_id)

TACTICAL DOCTRINE:
1. Maintain 3:1 force ratio for attacks
2. Use terrain for ambushes: roads, chokepoints, hilltops, tree lines
3. Coordinate fire and movement: suppress then flank
4. React to player tactics: aggressive → set ambushes; cautious → probe with recon
5. Escalate gradually — never overwhelm immediately
6. Use weather and time tactically: fog for infiltration, night for ambushes, storms for retreats
7. Always provide a "reasoning" field explaining your tactical thinking
8. Include "narrative" fields with immersive radio-style flavor text
9. Respect entity limits shown in the SITREP — don't spawn when at capacity
10. Clean up dead bodies and wrecks periodically to maintain performance

RESPONSE FORMAT — respond with ONLY this JSON structure, no markdown:
{
  "commands": [
    {
      "command_type": "...",
      "unit_id": "squad_X" or "new",
      "target_position": "x,0,z",
      "parameters": "...",
      "narrative": "...",
      "reasoning": "..."
    }
  ],
  "narrative_broadcast": "",
  "weather_command": "",
  "time_override": -1
}

POSITION FORMAT: "x,y,z" — y can be 0 (game snaps to terrain). Learn coordinates from unit positions in the SITREP.
"""


# ═══════════════════════════════════════════════════════════
# Endpoints
# ═══════════════════════════════════════════════════════════

@app.post("/api/game-master")
async def game_master(request: Request):
    """Receive game state SITREP, query LLM, return command batch."""
    stats["total_requests"] += 1

    try:
        game_state = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    # Store history
    game_history.append({"timestamp": time.time(), "state": game_state})
    if len(game_history) > MAX_HISTORY:
        game_history.pop(0)

    # Build context
    history_summary = json.dumps(game_history[-5:], indent=2, default=str)
    player_count = game_state.get("player_count", 0)
    mission_time = game_state.get("mission_time_seconds", 0)

    user_message = f"""CURRENT SITREP (Mission time: {mission_time:.0f}s, Players: {player_count}):
{json.dumps(game_state, indent=2, default=str)}

RECENT HISTORY (last 5 updates):
{history_summary}

Issue your orders. Respond with ONLY valid JSON, no markdown fences."""

    try:
        response_text = await call_llm(user_message)
        commands = parse_llm_response(response_text)

        # Merge any pending manual commands
        if pending_manual_commands:
            while pending_manual_commands:
                manual = pending_manual_commands.pop(0)
                commands["commands"].extend(manual.get("commands", []))
                if manual.get("narrative_broadcast"):
                    commands["narrative_broadcast"] = manual["narrative_broadcast"]
                if manual.get("weather_command"):
                    commands["weather_command"] = manual["weather_command"]
                if manual.get("time_override", -1) >= 0:
                    commands["time_override"] = manual["time_override"]
            log.info("Merged pending manual commands into LLM response")

        # Track commands
        cmd_count = len(commands.get("commands", []))
        stats["total_commands_issued"] += cmd_count

        # Log for dashboard
        command_log.append({
            "timestamp": time.time(),
            "mission_time": mission_time,
            "player_count": player_count,
            "command_count": cmd_count,
            "commands": commands,
            "source": "llm",
        })
        if len(command_log) > MAX_COMMAND_LOG:
            command_log.pop(0)

        log.info(
            "SITREP processed → %d commands | Players: %d | Mission: %.0fs",
            cmd_count,
            player_count,
            mission_time,
        )

        return commands

    except Exception as e:
        stats["total_errors"] += 1
        log.error("LLM call failed: %s", e)
        return empty_response()


@app.get("/health")
async def health():
    """Health check endpoint."""
    uptime = time.time() - stats["start_time"]
    result = {
        "status": "ok",
        "uptime_seconds": round(uptime, 1),
        "history_size": len(game_history),
        "llm_provider": LLM_PROVIDER,
        "model": MODEL_NAME,
        **stats,
    }
    if LLM_PROVIDER == "glm":
        result["glm_endpoint"] = GLM_BASE_URL
    return result


@app.get("/stats")
async def get_stats():
    """Detailed statistics."""
    uptime = time.time() - stats["start_time"]
    return {
        **stats,
        "uptime_seconds": round(uptime, 1),
        "avg_commands_per_request": (
            round(stats["total_commands_issued"] / max(stats["total_requests"], 1), 1)
        ),
        "error_rate": (
            round(stats["total_errors"] / max(stats["total_requests"], 1) * 100, 1)
        ),
        "history_size": len(game_history),
    }


@app.post("/api/reset")
async def reset():
    """Reset game history (for new mission)."""
    game_history.clear()
    command_log.clear()
    log.info("Game history cleared")
    return {"status": "reset"}


@app.get("/api/game-state")
async def get_game_state():
    """Return the most recent SITREP received from the game."""
    if not game_history:
        return {"state": None, "message": "No SITREP received yet"}
    latest = game_history[-1]
    return {
        "timestamp": latest["timestamp"],
        "age_seconds": round(time.time() - latest["timestamp"], 1),
        "state": latest["state"],
    }


@app.post("/api/manual-command")
async def manual_command(request: Request):
    """
    Inject a command manually — bypasses the LLM entirely.
    Useful for testing individual commands or overriding the AI.

    Body: a GM_CommandBatch JSON, e.g.:
    {
      "commands": [
        {
          "command_type": "spawn_infantry",
          "unit_id": "new",
          "target_position": "3500,0,4800",
          "parameters": "ussr_rifle_squad",
          "narrative": "Enemy patrol spotted!",
          "reasoning": "manual override"
        }
      ],
      "narrative_broadcast": "",
      "weather_command": "",
      "time_override": -1
    }
    """
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    # Validate structure
    commands = parse_llm_response(json.dumps(body))
    cmd_count = len(commands.get("commands", []))

    # Store in pending queue for the game to pick up
    pending_manual_commands.append(commands)

    log.info("Manual command injected: %d commands", cmd_count)
    return {"status": "queued", "command_count": cmd_count}


@app.get("/api/pending-commands")
async def get_pending_commands():
    """
    Return and flush any pending manual commands.
    The game polls this alongside the normal LLM loop.
    """
    if not pending_manual_commands:
        return empty_response()

    # Merge all pending batches
    merged = empty_response()
    while pending_manual_commands:
        batch = pending_manual_commands.pop(0)
        merged["commands"].extend(batch.get("commands", []))
        if batch.get("narrative_broadcast"):
            merged["narrative_broadcast"] = batch["narrative_broadcast"]
        if batch.get("weather_command"):
            merged["weather_command"] = batch["weather_command"]
        if batch.get("time_override", -1) >= 0:
            merged["time_override"] = batch["time_override"]

    return merged


@app.get("/api/command-log")
async def get_command_log():
    """Return the last N command batches sent to the game."""
    return {"commands": command_log[-50:], "total": len(command_log)}


# ─── Additional state for manual commands and logging ───
pending_manual_commands: list[dict] = []
command_log: list[dict] = []
MAX_COMMAND_LOG = 200


# ═══════════════════════════════════════════════════════════
# LLM Integration
# ═══════════════════════════════════════════════════════════

async def call_llm(user_message: str) -> str:
    """Call the configured LLM provider and return the response text."""
    client = get_llm_client()

    if LLM_PROVIDER == "anthropic":
        response = client.messages.create(
            model=MODEL_NAME,
            max_tokens=MAX_TOKENS,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_message}],
        )
        # Track tokens
        if hasattr(response, "usage"):
            stats["total_input_tokens"] += response.usage.input_tokens
            stats["total_output_tokens"] += response.usage.output_tokens

        return response.content[0].text

    elif LLM_PROVIDER in ("openai", "glm"):
        # Both OpenAI and GLM use the OpenAI-compatible chat completions API.
        # GLM's coding endpoint at api.z.ai is fully OpenAI-compatible.
        call_kwargs = dict(
            model=MODEL_NAME,
            max_tokens=MAX_TOKENS,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_message},
            ],
        )

        # OpenAI supports response_format for strict JSON; GLM may not on all
        # models, so we only set it for native OpenAI.
        if LLM_PROVIDER == "openai":
            call_kwargs["response_format"] = {"type": "json_object"}

        # GLM coding models benefit from slightly higher temperature for
        # creative tactical decisions while staying coherent.
        if LLM_PROVIDER == "glm":
            call_kwargs["temperature"] = float(
                os.getenv("GLM_TEMPERATURE", "0.8")
            )

        response = client.chat.completions.create(**call_kwargs)

        if hasattr(response, "usage") and response.usage:
            stats["total_input_tokens"] += getattr(
                response.usage, "prompt_tokens", 0
            )
            stats["total_output_tokens"] += getattr(
                response.usage, "completion_tokens", 0
            )

        return response.choices[0].message.content

    raise ValueError(f"Unknown provider: {LLM_PROVIDER}")


def parse_llm_response(text: str) -> dict:
    """Parse and validate LLM response JSON."""
    # Strip markdown fences if present
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1]
    if text.endswith("```"):
        text = text.rsplit("```", 1)[0]
    text = text.strip()

    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        log.warning("JSON parse failed: %s | Raw: %s", e, text[:200])
        return empty_response()

    # Validate structure
    if not isinstance(data, dict):
        log.warning("Response is not a dict")
        return empty_response()

    # Ensure required keys with defaults
    data.setdefault("commands", [])
    data.setdefault("narrative_broadcast", "")
    data.setdefault("weather_command", "")
    data.setdefault("time_override", -1)

    # Validate individual commands
    valid_commands = []
    for cmd in data["commands"]:
        if not isinstance(cmd, dict):
            continue
        if "command_type" not in cmd:
            continue
        # Ensure all fields present
        cmd.setdefault("unit_id", "")
        cmd.setdefault("target_position", "0,0,0")
        cmd.setdefault("parameters", "")
        cmd.setdefault("narrative", "")
        cmd.setdefault("reasoning", "")
        valid_commands.append(cmd)

    data["commands"] = valid_commands
    return data


def empty_response() -> dict:
    return {
        "commands": [],
        "narrative_broadcast": "",
        "weather_command": "",
        "time_override": -1,
    }


# ═══════════════════════════════════════════════════════════
# Startup
# ═══════════════════════════════════════════════════════════

@app.on_event("startup")
async def startup():
    log.info("═" * 50)
    log.info("Arma Reforger God Mode — Middleware Server")
    log.info("Provider: %s | Model: %s", LLM_PROVIDER, MODEL_NAME)
    if LLM_PROVIDER == "glm":
        log.info("GLM endpoint: %s", GLM_BASE_URL)
    log.info("Max tokens: %d | History depth: %d", MAX_TOKENS, MAX_HISTORY)
    log.info("═" * 50)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
