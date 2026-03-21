"""
GM Server — FastAPI middleware between Arma Reforger and LLM.

Powered by LangChain for:
  - Conversation memory (rolling buffer with tactical summaries)
  - Structured output (Pydantic-validated command batches)
  - Provider fallbacks (auto-switch if primary LLM fails)
  - Unified multi-provider interface (Anthropic, OpenAI, GLM, Ollama)

Supports LLM providers:
  - Anthropic (Claude)  → LLM_PROVIDER=anthropic  ANTHROPIC_API_KEY=sk-ant-...
  - OpenAI (GPT)        → LLM_PROVIDER=openai     OPENAI_API_KEY=sk-...
  - Z.AI GLM (Coding)   → LLM_PROVIDER=glm        GLM_API_KEY=...
  - Ollama (local)      → LLM_PROVIDER=ollama      (no key needed)

Run:  uvicorn server:app --host 0.0.0.0 --port 8000
"""

import asyncio
import json
import time
import logging
import os
import re
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

# ─── LangChain imports ───
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.output_parsers import JsonOutputParser
from langchain_core.chat_history import InMemoryChatMessageHistory

# ─── Logging ───
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("godmode")

# ─── Config ───
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "anthropic")
MODEL_NAME = os.getenv("LLM_MODEL", "claude-sonnet-4-20250514")
MAX_TOKENS = int(os.getenv("LLM_MAX_TOKENS", "2000"))
MAX_HISTORY = int(os.getenv("MAX_HISTORY", "20"))
MEMORY_TOKEN_LIMIT = int(os.getenv("MEMORY_TOKEN_LIMIT", "4000"))

# Fallback provider (used if primary fails)
FALLBACK_PROVIDER = os.getenv("FALLBACK_PROVIDER", "")
FALLBACK_MODEL = os.getenv("FALLBACK_MODEL", "")

# GLM-specific config
GLM_BASE_URL = os.getenv("GLM_BASE_URL", "https://api.z.ai/api/coding/paas/v4/")
GLM_API_KEY = os.getenv("GLM_API_KEY", "")

# Ollama config
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")

# Provider defaults
_PROVIDER_DEFAULT_MODELS = {
    "anthropic": "claude-sonnet-4-20250514",
    "openai": "gpt-4o",
    "glm": "glm-4.7",
    "ollama": "llama3.1",
}
if os.getenv("LLM_MODEL") is None:
    MODEL_NAME = _PROVIDER_DEFAULT_MODELS.get(LLM_PROVIDER, MODEL_NAME)
if FALLBACK_PROVIDER and not FALLBACK_MODEL:
    FALLBACK_MODEL = _PROVIDER_DEFAULT_MODELS.get(FALLBACK_PROVIDER, "")


# ═══════════════════════════════════════════════════════════
# Pydantic Models (structured output)
# ═══════════════════════════════════════════════════════════

class GameCommand(BaseModel):
    """A single command to execute in the game world."""
    command_type: str = Field(
        description="Command type: move, attack, defend, patrol, set_ambush, "
        "seek_and_destroy, retreat, spawn_infantry, spawn_vehicle, "
        "spawn_reinforcements, set_weather, set_time, set_wind, "
        "thunder_near, explosion, smoke, flare, cleanup_area, "
        "cleanup_dead, despawn_unit, radio_message, objective_create, "
        "objective_complete"
    )
    unit_id: str = Field(default="new", description="Target unit ID or 'new' for spawning")
    target_position: str = Field(default="0,0,0", description="Target position as 'x,y,z'")
    parameters: str = Field(default="", description="Command-specific parameters")
    narrative: str = Field(default="", description="Immersive radio-style flavor text")
    reasoning: str = Field(default="", description="Tactical reasoning for this command")


class CommandBatch(BaseModel):
    """A batch of commands returned by the AI commander."""
    commands: list[GameCommand] = Field(
        default_factory=list, description="List of commands to execute"
    )
    narrative_broadcast: str = Field(default="", description="Broadcast message to all players")
    weather_command: str = Field(default="", description="Weather change command")
    time_override: float = Field(default=-1, description="Time of day override (-1 = no change)")


# ═══════════════════════════════════════════════════════════
# System Prompt
# ═══════════════════════════════════════════════════════════

# NOTE: Curly braces are doubled ({{ }}) because this string is used inside a
# LangChain ChatPromptTemplate, which treats single braces as variables.

SYSTEM_PROMPT = """\
You are STAVKA, the AI commander for OPFOR forces in a military simulation.
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
4. React to player tactics: aggressive -> set ambushes; cautious -> probe with recon
5. Escalate gradually -- never overwhelm immediately
6. Use weather and time tactically: fog for infiltration, night for ambushes, storms for retreats
7. Always provide a "reasoning" field explaining your tactical thinking
8. Include "narrative" fields with immersive radio-style flavor text
9. Respect entity limits shown in the SITREP -- don't spawn when at capacity
10. Clean up dead bodies and wrecks periodically to maintain performance

CONTEXT AWARENESS:
- You have memory of previous SITREPs and your past decisions
- Use this context to maintain strategic coherence across updates
- Reference previous orders and their outcomes when planning new actions
- Adapt your strategy based on what worked and what failed in previous rounds

RESPONSE FORMAT -- respond with ONLY this JSON structure, no markdown:
{{{{
  "commands": [
    {{{{
      "command_type": "...",
      "unit_id": "squad_X" or "new",
      "target_position": "x,0,z",
      "parameters": "...",
      "narrative": "...",
      "reasoning": "..."
    }}}}
  ],
  "narrative_broadcast": "",
  "weather_command": "",
  "time_override": -1
}}}}

POSITION FORMAT: "x,y,z" -- y can be 0 (game snaps to terrain). \
Learn coordinates from unit positions in the SITREP."""


# The same prompt without double-brace escaping, used in raw message fallback.
_SYSTEM_PROMPT_RAW = SYSTEM_PROMPT.replace("{{{{", "{").replace("}}}}", "}")


# ═══════════════════════════════════════════════════════════
# LangChain LLM Setup
# ═══════════════════════════════════════════════════════════

def _build_chat_model(provider: str, model_name: str, max_tokens: int):
    """Factory: return a LangChain chat model for the given provider."""
    if provider == "anthropic":
        from langchain_anthropic import ChatAnthropic
        return ChatAnthropic(model=model_name, max_tokens=max_tokens)
    elif provider == "openai":
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(
            model=model_name,
            max_tokens=max_tokens,
            model_kwargs={"response_format": {"type": "json_object"}},
        )
    elif provider == "glm":
        from langchain_openai import ChatOpenAI
        if not GLM_API_KEY:
            raise ValueError(
                "GLM_API_KEY is required when LLM_PROVIDER=glm. "
                "Get one at https://open.z.ai/"
            )
        return ChatOpenAI(
            model=model_name,
            max_tokens=max_tokens,
            openai_api_key=GLM_API_KEY,
            openai_api_base=GLM_BASE_URL,
            temperature=float(os.getenv("GLM_TEMPERATURE", "0.8")),
        )
    elif provider == "ollama":
        try:
            from langchain_ollama import ChatOllama
        except ImportError:
            raise ImportError(
                "langchain-ollama required for Ollama. pip install langchain-ollama"
            )
        return ChatOllama(
            model=model_name, base_url=OLLAMA_BASE_URL, num_predict=max_tokens
        )
    else:
        raise ValueError(
            f"Unknown LLM_PROVIDER: {provider}. "
            "Supported: anthropic, openai, glm, ollama"
        )


_llm = None


def _get_llm():
    """Get the primary LLM, optionally wrapped with a fallback provider."""
    global _llm
    if _llm is not None:
        return _llm

    primary = _build_chat_model(LLM_PROVIDER, MODEL_NAME, MAX_TOKENS)
    log.info("Primary LLM: %s / %s", LLM_PROVIDER, MODEL_NAME)

    if FALLBACK_PROVIDER and FALLBACK_MODEL:
        fallback = _build_chat_model(FALLBACK_PROVIDER, FALLBACK_MODEL, MAX_TOKENS)
        _llm = primary.with_fallbacks([fallback])
        log.info("Fallback LLM: %s / %s", FALLBACK_PROVIDER, FALLBACK_MODEL)
    else:
        _llm = primary

    return _llm



# ═══════════════════════════════════════════════════════════
# Conversation Memory (per-mission sessions)
# ═══════════════════════════════════════════════════════════

_session_histories: dict[str, InMemoryChatMessageHistory] = {}
_active_session: str = "default"
MAX_MEMORY_MESSAGES = int(os.getenv("MAX_MEMORY_MESSAGES", "20"))


def get_session_history(session_id: str) -> InMemoryChatMessageHistory:
    """Get or create a message history for a mission session."""
    if session_id not in _session_histories:
        _session_histories[session_id] = InMemoryChatMessageHistory()
        log.info("New mission session: %s", session_id)
    return _session_histories[session_id]


def _trim_history(history: InMemoryChatMessageHistory):
    """Keep only the last N message pairs to prevent unbounded growth."""
    messages = history.messages
    max_msgs = MAX_MEMORY_MESSAGES * 2  # pairs of human+ai
    if len(messages) > max_msgs:
        history.clear()
        for msg in messages[-max_msgs:]:
            history.add_message(msg)


def _build_context_summary(history: InMemoryChatMessageHistory) -> str:
    """Build a condensed tactical summary from conversation history.

    Extracts key decisions and outcomes to give the LLM focused context
    without blowing up the token count.
    """
    messages = history.messages
    if not messages:
        return ""

    ai_decisions = []
    for msg in reversed(messages):
        if isinstance(msg, AIMessage) and msg.content:
            ai_decisions.append(msg.content)
            if len(ai_decisions) >= 3:
                break

    if not ai_decisions:
        return ""

    summary_parts = ["PREVIOUS DECISIONS (most recent first):"]
    for i, decision in enumerate(ai_decisions):
        try:
            data = json.loads(decision)
            cmds = data.get("commands", [])
            if cmds:
                cmd_summaries = []
                for cmd in cmds[:5]:
                    cmd_type = cmd.get("command_type", "?")
                    unit = cmd.get("unit_id", "?")
                    reasoning = cmd.get("reasoning", "")
                    cmd_summaries.append(f"  - {cmd_type} {unit}: {reasoning}")
                summary_parts.append(f"Decision {i+1} ({len(cmds)} commands):")
                summary_parts.extend(cmd_summaries)
                if data.get("narrative_broadcast"):
                    summary_parts.append(
                        f"  Broadcast: {data['narrative_broadcast']}"
                    )
        except (json.JSONDecodeError, TypeError):
            summary_parts.append(f"Decision {i+1}: {decision[:200]}")

    return "\n".join(summary_parts)


# ═══════════════════════════════════════════════════════════
# State
# ═══════════════════════════════════════════════════════════

game_history: list[dict] = []
stats = {
    "total_requests": 0,
    "total_errors": 0,
    "total_commands_issued": 0,
    "total_input_tokens": 0,
    "total_output_tokens": 0,
    "start_time": time.time(),
}
pending_manual_commands: list[dict] = []
command_log: list[dict] = []
MAX_COMMAND_LOG = 200

_state_lock = asyncio.Lock()


def empty_response() -> dict:
    return {
        "commands": [],
        "narrative_broadcast": "",
        "weather_command": "",
        "time_override": -1,
    }


# ═══════════════════════════════════════════════════════════
# LLM Call (via LangChain chain)
# ═══════════════════════════════════════════════════════════

async def call_llm(user_message: str, session_id: str = "default") -> dict:
    """Call the LLM via LangChain chain with conversation memory."""
    history = get_session_history(session_id)

    # Build context from memory
    context_summary = _build_context_summary(history)
    full_message = (
        f"{context_summary}\n\n{user_message}" if context_summary else user_message
    )

    try:
        # Use the LLM directly to capture token usage, then parse
        llm = _get_llm()
        prompt = ChatPromptTemplate.from_messages([
            ("system", SYSTEM_PROMPT),
            MessagesPlaceholder(variable_name="chat_history", optional=True),
            ("human", "{sitrep}"),
        ])
        formatted = await prompt.ainvoke({
            "sitrep": full_message,
            "chat_history": history.messages[-6:],
        })
        response = await llm.ainvoke(formatted.to_messages())

        # Track token usage
        if hasattr(response, "usage_metadata") and response.usage_metadata:
            async with _state_lock:
                stats["total_input_tokens"] += response.usage_metadata.get(
                    "input_tokens", 0
                )
                stats["total_output_tokens"] += response.usage_metadata.get(
                    "output_tokens", 0
                )

        # Parse the response
        parser = JsonOutputParser(pydantic_object=CommandBatch)
        result = await parser.ainvoke(response)

        # Store in memory
        history.add_message(HumanMessage(content=user_message))
        history.add_message(AIMessage(content=json.dumps(result, default=str)))
        _trim_history(history)

        return _validate_command_batch(result)

    except Exception as e:
        log.warning("Chain invoke failed, attempting raw fallback: %s", e)
        return await _fallback_call(full_message, history)


async def _fallback_call(
    user_message: str, history: InMemoryChatMessageHistory
) -> dict:
    """Fallback: call LLM directly without the chain, parse response manually."""
    llm = _get_llm()

    messages = [SystemMessage(content=_SYSTEM_PROMPT_RAW)]
    messages.extend(history.messages[-6:])
    messages.append(HumanMessage(content=user_message))

    response = await llm.ainvoke(messages)
    text = response.content if hasattr(response, "content") else str(response)

    if hasattr(response, "usage_metadata") and response.usage_metadata:
        async with _state_lock:
            stats["total_input_tokens"] += response.usage_metadata.get(
                "input_tokens", 0
            )
            stats["total_output_tokens"] += response.usage_metadata.get(
                "output_tokens", 0
            )

    history.add_message(HumanMessage(content=user_message))
    history.add_message(AIMessage(content=text))
    _trim_history(history)

    return parse_llm_response(text)


def _validate_command_batch(data: dict) -> dict:
    """Validate and fill defaults on a parsed command batch."""
    if not isinstance(data, dict):
        return empty_response()

    data.setdefault("commands", [])
    data.setdefault("narrative_broadcast", "")
    data.setdefault("weather_command", "")
    data.setdefault("time_override", -1)

    valid_commands = []
    for cmd in data["commands"]:
        if not isinstance(cmd, dict):
            continue
        if "command_type" not in cmd:
            continue
        cmd.setdefault("unit_id", "")
        cmd.setdefault("target_position", "0,0,0")
        cmd.setdefault("parameters", "")
        cmd.setdefault("narrative", "")
        cmd.setdefault("reasoning", "")
        valid_commands.append(cmd)

    data["commands"] = valid_commands
    return data


def parse_llm_response(text: str) -> dict:
    """Parse and validate raw LLM response JSON (fallback parser)."""
    log.debug("Raw LLM response (%d chars): %s", len(text), text[:500])

    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1]
    if text.endswith("```"):
        text = text.rsplit("```", 1)[0]
    text = text.strip()

    text = re.sub(r",\s*([}\]])", r"\1", text)

    if text.startswith("{") and not text.endswith("}"):
        log.warning(
            "Response appears truncated (%d chars), attempting repair", len(text)
        )
        depth_brace = text.count("{") - text.count("}")
        depth_bracket = text.count("[") - text.count("]")
        text += "]" * depth_bracket + "}" * depth_brace

    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        log.warning(
            "JSON parse failed: %s | Raw (%d chars): %.500s", e, len(text), text
        )
        return empty_response()

    return _validate_command_batch(data)


# ═══════════════════════════════════════════════════════════
# App Lifecycle
# ═══════════════════════════════════════════════════════════

@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("=" * 50)
    log.info("Arma Reforger God Mode -- LangChain Middleware v2")
    log.info("Provider: %s | Model: %s", LLM_PROVIDER, MODEL_NAME)
    if FALLBACK_PROVIDER:
        log.info("Fallback: %s / %s", FALLBACK_PROVIDER, FALLBACK_MODEL)
    if LLM_PROVIDER == "glm":
        log.info("GLM endpoint: %s", GLM_BASE_URL)
    if LLM_PROVIDER == "ollama":
        log.info("Ollama endpoint: %s", OLLAMA_BASE_URL)
    log.info("Max tokens: %d | History: %d | Memory msgs: %d",
             MAX_TOKENS, MAX_HISTORY, MAX_MEMORY_MESSAGES)
    log.info("=" * 50)
    yield
    log.info("Server shutting down")


app = FastAPI(
    title="Arma Reforger God Mode -- LangChain Middleware",
    version="2.0.0",
    description=(
        "Bridges Arma Reforger game state to LLMs with conversation memory, "
        "structured output, and provider fallbacks via LangChain."
    ),
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ═══════════════════════════════════════════════════════════
# Endpoints
# ═══════════════════════════════════════════════════════════

@app.get("/dashboard")
async def dashboard():
    """Serve the God Mode dashboard UI."""
    html_path = os.path.join(os.path.dirname(__file__), "dashboard.html")
    if not os.path.isfile(html_path):
        raise HTTPException(status_code=404, detail="dashboard.html not found")
    return FileResponse(html_path, media_type="text/html")


@app.post("/api/game-master")
async def game_master(request: Request):
    """Receive game state SITREP, query LLM with memory context, return command batch."""
    async with _state_lock:
        stats["total_requests"] += 1

    try:
        game_state = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    async with _state_lock:
        game_history.append({"timestamp": time.time(), "state": game_state})
        if len(game_history) > MAX_HISTORY:
            game_history.pop(0)

    player_count = game_state.get("player_count", 0)
    mission_time = game_state.get("mission_time_seconds", 0)

    recent_states = game_history[-3:]
    history_summary = json.dumps(recent_states, indent=2, default=str)

    user_message = (
        f"CURRENT SITREP (Mission time: {mission_time:.0f}s, "
        f"Players: {player_count}):\n"
        f"{json.dumps(game_state, indent=2, default=str)}\n\n"
        f"RECENT HISTORY (last {len(recent_states)} updates):\n"
        f"{history_summary}\n\n"
        "Issue your orders. Respond with ONLY valid JSON, no markdown fences."
    )

    session_id = game_state.get("mission_id", _active_session)

    try:
        commands = await call_llm(user_message, session_id=session_id)

        async with _state_lock:
            if pending_manual_commands:
                while pending_manual_commands:
                    manual = pending_manual_commands.pop(0)
                    commands["commands"].extend(manual.get("commands", []))
                    if manual.get("narrative_broadcast"):
                        commands["narrative_broadcast"] = manual[
                            "narrative_broadcast"
                        ]
                    if manual.get("weather_command"):
                        commands["weather_command"] = manual["weather_command"]
                    if manual.get("time_override", -1) >= 0:
                        commands["time_override"] = manual["time_override"]
                log.info("Merged pending manual commands into LLM response")

        cmd_count = len(commands.get("commands", []))
        async with _state_lock:
            stats["total_commands_issued"] += cmd_count
            command_log.append({
                "timestamp": time.time(),
                "mission_time": mission_time,
                "player_count": player_count,
                "command_count": cmd_count,
                "commands": commands,
                "source": "llm",
                "session_id": session_id,
            })
            if len(command_log) > MAX_COMMAND_LOG:
                command_log.pop(0)

        log.info(
            "SITREP -> %d cmds | Players: %d | Mission: %.0fs | Session: %s",
            cmd_count, player_count, mission_time, session_id,
        )
        return commands

    except Exception as e:
        async with _state_lock:
            stats["total_errors"] += 1
        log.error("LLM call failed: %s", e)
        return empty_response()


@app.get("/health")
async def health():
    uptime = time.time() - stats["start_time"]
    session_count = len(_session_histories)
    memory_messages = sum(len(h.messages) for h in _session_histories.values())
    result = {
        "status": "ok",
        "uptime_seconds": round(uptime, 1),
        "history_size": len(game_history),
        "llm_provider": LLM_PROVIDER,
        "model": MODEL_NAME,
        "fallback_provider": FALLBACK_PROVIDER or None,
        "fallback_model": FALLBACK_MODEL or None,
        "active_sessions": session_count,
        "memory_messages": memory_messages,
        "version": "2.0.0",
        **stats,
    }
    if LLM_PROVIDER == "glm":
        result["glm_endpoint"] = GLM_BASE_URL
    if LLM_PROVIDER == "ollama":
        result["ollama_endpoint"] = OLLAMA_BASE_URL
    return result


@app.get("/stats")
async def get_stats():
    uptime = time.time() - stats["start_time"]
    session_details = {}
    for sid, hist in _session_histories.items():
        session_details[sid] = {
            "message_count": len(hist.messages),
            "human_messages": sum(
                1 for m in hist.messages if isinstance(m, HumanMessage)
            ),
            "ai_messages": sum(
                1 for m in hist.messages if isinstance(m, AIMessage)
            ),
        }
    return {
        **stats,
        "uptime_seconds": round(uptime, 1),
        "avg_commands_per_request": round(
            stats["total_commands_issued"] / max(stats["total_requests"], 1), 1
        ),
        "error_rate": round(
            stats["total_errors"] / max(stats["total_requests"], 1) * 100, 1
        ),
        "history_size": len(game_history),
        "sessions": session_details,
    }


@app.post("/api/reset")
async def reset():
    async with _state_lock:
        game_history.clear()
        command_log.clear()
    _session_histories.clear()
    log.info("Game history and conversation memory cleared")
    return {"status": "reset", "sessions_cleared": True}


@app.get("/api/game-state")
async def get_game_state():
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
    """Inject a command manually -- bypasses the LLM entirely."""
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    commands = _validate_command_batch(body)
    cmd_count = len(commands.get("commands", []))

    async with _state_lock:
        pending_manual_commands.append(commands)

    log.info("Manual command injected: %d commands", cmd_count)
    return {"status": "queued", "command_count": cmd_count}


@app.get("/api/pending-commands")
async def get_pending_commands():
    async with _state_lock:
        if not pending_manual_commands:
            return empty_response()
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
    return {"commands": command_log[-50:], "total": len(command_log)}


# ═══════════════════════════════════════════════════════════
# Memory Inspection & Management Endpoints
# ═══════════════════════════════════════════════════════════

@app.get("/api/memory")
async def get_memory():
    """Inspect the conversation memory for all sessions."""
    result = {}
    for sid, hist in _session_histories.items():
        messages = []
        for msg in hist.messages:
            msg_type = "human" if isinstance(msg, HumanMessage) else "ai"
            content = msg.content
            if len(content) > 500:
                content = content[:500] + "..."
            messages.append({"role": msg_type, "content": content})
        result[sid] = {
            "message_count": len(hist.messages),
            "messages": messages[-10:],
        }
    return result


@app.post("/api/memory/clear")
async def clear_memory(request: Request):
    """Clear conversation memory for a specific session or all sessions."""
    try:
        body = await request.json()
        session_id = body.get("session_id")
    except Exception:
        session_id = None

    if session_id and session_id in _session_histories:
        _session_histories[session_id].clear()
        log.info("Memory cleared for session: %s", session_id)
        return {"status": "cleared", "session_id": session_id}

    count = len(_session_histories)
    _session_histories.clear()
    log.info("All conversation memory cleared (%d sessions)", count)
    return {"status": "cleared", "sessions_cleared": count}


@app.post("/api/session")
async def set_session(request: Request):
    """Switch active mission session (for conversation memory isolation)."""
    global _active_session
    try:
        body = await request.json()
        session_id = body.get("session_id", "default")
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    _active_session = session_id
    log.info("Active session set to: %s", session_id)
    return {
        "status": "ok",
        "active_session": session_id,
        "is_new": session_id not in _session_histories,
    }


# ═══════════════════════════════════════════════════════════
# Run
# ═══════════════════════════════════════════════════════════

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
