"""
GM Server v3 — FastAPI middleware between Arma Reforger and LLM.

Advanced features:
  - Auto-compaction: LLM periodically compresses old memory into strategic briefings
  - Tactical analysis: Pre-processes SITREPs to extract threat scores, force ratios,
    player behavior profiles before sending to LLM
  - Battle plan persistence: Maintains a running strategic plan that evolves each cycle
  - Adaptive difficulty: Tracks engagement outcomes and adjusts AI aggression
  - Chain-of-thought reasoning: Structured tactical thinking in the system prompt
  - Provider fallbacks: Auto-switch if primary LLM fails

Supports LLM providers:
  - Anthropic (Claude)  → LLM_PROVIDER=anthropic  ANTHROPIC_API_KEY=sk-ant-...
  - OpenAI (GPT)        → LLM_PROVIDER=openai     OPENAI_API_KEY=sk-...
  - Z.AI GLM (Coding)   → LLM_PROVIDER=glm        GLM_API_KEY=...
  - Ollama (local)      → LLM_PROVIDER=ollama      (no key needed)

Run:  uvicorn server:app --host 0.0.0.0 --port 8000
"""

import asyncio
import json
import math
import time
import logging
import os
import re
from collections import defaultdict
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

# Fallback provider
FALLBACK_PROVIDER = os.getenv("FALLBACK_PROVIDER", "")
FALLBACK_MODEL = os.getenv("FALLBACK_MODEL", "")

# GLM-specific
GLM_BASE_URL = os.getenv("GLM_BASE_URL", "https://api.z.ai/api/coding/paas/v4/")
GLM_API_KEY = os.getenv("GLM_API_KEY", "")

# Ollama
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")

# Auto-compaction threshold
COMPACTION_THRESHOLD = int(os.getenv("COMPACTION_THRESHOLD", "12"))

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
# Pydantic Models
# ═══════════════════════════════════════════════════════════

class GameCommand(BaseModel):
    command_type: str = Field(
        description="move, attack, defend, patrol, set_ambush, seek_and_destroy, "
        "retreat, spawn_infantry, spawn_vehicle, spawn_reinforcements, set_weather, "
        "set_time, set_wind, thunder_near, explosion, smoke, flare, cleanup_area, "
        "cleanup_dead, despawn_unit, radio_message, objective_create, objective_complete"
    )
    unit_id: str = Field(default="new")
    target_position: str = Field(default="0,0,0")
    parameters: str = Field(default="")
    narrative: str = Field(default="")
    reasoning: str = Field(default="")


class CommandBatch(BaseModel):
    commands: list[GameCommand] = Field(default_factory=list)
    narrative_broadcast: str = Field(default="")
    weather_command: str = Field(default="")
    time_override: float = Field(default=-1)


# ═══════════════════════════════════════════════════════════
# Tactical Analysis Engine
# ═══════════════════════════════════════════════════════════

class TacticalAnalyzer:
    """Pre-processes raw SITREP data into tactical intelligence.

    Extracts threat levels, force ratios, player behavior patterns,
    and situational scores BEFORE the LLM sees the data — so the AI
    commander gets a pre-digested battlefield picture like a real staff
    officer would provide.
    """

    def __init__(self):
        self.player_positions: dict[int, list[list[float]]] = defaultdict(list)
        self.player_engagements: dict[int, list[str]] = defaultdict(list)
        self.engagement_outcomes: list[dict] = []
        self.player_kills = 0
        self.ai_kills = 0
        self.difficulty_modifier = 1.0

    def analyze(self, game_state: dict) -> dict:
        analysis = {}

        enemy_units = game_state.get("enemy_units", [])
        players = game_state.get("players", [])
        contacts = game_state.get("contacts", [])

        total_ai_strength = sum(u.get("strength", 0) for u in enemy_units)
        total_ai_health = (
            sum(u.get("health_pct", 1.0) for u in enemy_units) / max(len(enemy_units), 1)
        )

        analysis["force_disposition"] = {
            "ai_squads": len(enemy_units),
            "ai_total_strength": total_ai_strength,
            "ai_avg_health": round(total_ai_health, 2),
            "player_count": len(players),
            "known_contacts": len(contacts),
            "force_ratio": round(total_ai_strength / max(len(players), 1), 1),
        }

        # Threat assessment per contact
        threats = []
        for contact in contacts:
            confidence = contact.get("confidence", 0)
            staleness = contact.get("seconds_since_seen", 999)
            threat_score = confidence * max(0, 1.0 - staleness / 300.0)
            threats.append({
                "contact_id": contact.get("contact_id"),
                "threat_score": round(threat_score, 2),
                "unit_type": contact.get("unit_type", "unknown"),
                "position": contact.get("last_known_position"),
                "stale": staleness > 120,
            })
        threats.sort(key=lambda t: t["threat_score"], reverse=True)
        analysis["threat_assessment"] = threats

        # Player behavior profiling
        self._update_player_tracking(game_state)
        analysis["player_profiles"] = self._classify_players()

        # Adaptive difficulty
        self._track_engagements(game_state)
        analysis["adaptive_difficulty"] = {
            "modifier": round(self.difficulty_modifier, 2),
            "player_kills": self.player_kills,
            "ai_kills": self.ai_kills,
            "assessment": self._difficulty_assessment(),
        }

        # Environment
        time_of_day = game_state.get("time_of_day", 12.0)
        weather = game_state.get("current_weather", "Clear")
        wind_speed = game_state.get("wind_speed", 0)
        visibility = self._calc_visibility(time_of_day, weather)
        analysis["environment"] = {
            "visibility": visibility,
            "visibility_label": (
                "excellent" if visibility > 0.8 else
                "good" if visibility > 0.6 else
                "reduced" if visibility > 0.3 else "poor"
            ),
            "is_night": time_of_day < 5.0 or time_of_day > 21.0,
            "is_dusk_dawn": 5.0 <= time_of_day <= 7.0 or 19.0 <= time_of_day <= 21.0,
            "weather_tactical": self._weather_tactics(weather),
            "wind_affects_smoke": wind_speed > 5.0,
        }

        # Territory
        analysis["territorial"] = self._estimate_territory(enemy_units, players)

        # Readiness
        ready = [u for u in enemy_units if u.get("health_pct", 1.0) > 0.5]
        damaged = [u for u in enemy_units if 0.1 < u.get("health_pct", 1.0) <= 0.5]
        analysis["readiness"] = {
            "combat_ready": len(ready),
            "damaged": len(damaged),
            "combat_effectiveness": round(len(ready) / max(len(enemy_units), 1), 2),
            "needs_reinforcement": len(ready) < len(players) * 2,
        }

        # Spatial relationships (Priority 2)
        analysis["spatial"] = self._compute_spatial_relationships(enemy_units, players)

        return analysis

    def _update_player_tracking(self, game_state: dict):
        for player in game_state.get("players", []):
            pid = player.get("player_id", 0)
            pos = player.get("position", [0, 0, 0])
            self.player_positions[pid].append(pos)
            if len(self.player_positions[pid]) > 20:
                self.player_positions[pid].pop(0)

        for event in game_state.get("recent_events", []):
            if "shots_fired" in event or "player_hit" in event:
                parts = event.split(":")
                for part in parts:
                    if part.startswith("player_"):
                        try:
                            pid = int(part.split("_")[1])
                            self.player_engagements[pid].append(event)
                            if len(self.player_engagements[pid]) > 30:
                                self.player_engagements[pid].pop(0)
                        except (ValueError, IndexError):
                            pass

    def _classify_players(self) -> dict:
        profiles = {}
        for pid, positions in self.player_positions.items():
            if len(positions) < 3:
                profiles[pid] = {"style": "unknown", "confidence": 0.0, "movement_rate": 0}
                continue

            total_dist = 0
            for i in range(1, len(positions)):
                dx = positions[i][0] - positions[i - 1][0]
                dz = (positions[i][2] - positions[i - 1][2]
                      if len(positions[i]) > 2 else 0)
                total_dist += math.sqrt(dx * dx + dz * dz)
            avg_speed = total_dist / len(positions)

            engagement_count = len(self.player_engagements.get(pid, []))

            if avg_speed > 80 and engagement_count > 3:
                style = "aggressive_assault"
            elif avg_speed > 80 and engagement_count <= 3:
                style = "flanking_maneuver"
            elif avg_speed < 20 and engagement_count > 2:
                style = "defensive_sniper"
            elif avg_speed < 20:
                style = "cautious_observer"
            elif engagement_count > 5:
                style = "aggressive_push"
            else:
                style = "balanced_advance"

            profiles[pid] = {
                "style": style,
                "confidence": min(len(positions) / 10.0, 1.0),
                "movement_rate": round(avg_speed, 1),
                "engagements": engagement_count,
            }
        return profiles

    def _track_engagements(self, game_state: dict):
        for event in game_state.get("recent_events", []):
            if "enemy_killed" in event:
                self.player_kills += 1
            if "player_hit" in event:
                self.ai_kills += 1

        total = self.player_kills + self.ai_kills
        if total >= 5:
            ratio = self.player_kills / max(total, 1)
            if ratio > 0.7:
                self.difficulty_modifier = min(self.difficulty_modifier + 0.05, 2.0)
            elif ratio < 0.3:
                self.difficulty_modifier = max(self.difficulty_modifier - 0.05, 0.4)
            else:
                self.difficulty_modifier += (1.0 - self.difficulty_modifier) * 0.02

    def _difficulty_assessment(self) -> str:
        if self.difficulty_modifier > 1.4:
            return "players_dominating_escalate"
        elif self.difficulty_modifier > 1.1:
            return "players_ahead_increase_pressure"
        elif self.difficulty_modifier < 0.6:
            return "ai_dominating_ease_up"
        elif self.difficulty_modifier < 0.9:
            return "ai_ahead_reduce_aggression"
        return "balanced_maintain_tempo"

    def _calc_visibility(self, tod: float, weather: str) -> float:
        if 7.0 <= tod <= 19.0:
            tv = 1.0
        elif tod < 5.0 or tod > 21.0:
            tv = 0.15
        elif tod < 7.0:
            tv = 0.15 + 0.85 * ((tod - 5.0) / 2.0)
        else:
            tv = 0.15 + 0.85 * ((21.0 - tod) / 2.0)
        wm = {"Clear": 1.0, "Cloudy": 0.9, "Overcast": 0.75,
               "Rainy": 0.5, "Storm": 0.3, "Foggy": 0.2}.get(weather, 0.7)
        return round(tv * wm, 2)

    def _weather_tactics(self, weather: str) -> str:
        return {
            "Clear": "Open engagements favored. Snipers effective. Avoid open ground.",
            "Cloudy": "Standard ops. No significant modifier.",
            "Overcast": "Reduced air observation. Good for troop movements.",
            "Rainy": "Reduced visibility/hearing. Ideal for infiltration.",
            "Storm": "Severely reduced vis. Thunder masks sound. Bold maneuvers possible.",
            "Foggy": "Extremely low vis. CQB ambushes devastating. Perfect for infiltration.",
        }.get(weather, "Unknown conditions.")

    def _estimate_territory(self, enemy_units: list, players: list) -> dict:
        def centroid(units, pk="position"):
            if not units:
                return [0, 0, 0]
            xs = [u.get(pk, [0, 0, 0])[0] for u in units]
            zs = [u.get(pk, [0, 0, 0])[2] if len(u.get(pk, [0, 0, 0])) > 2 else 0
                  for u in units]
            return [sum(xs) / len(xs), 0, sum(zs) / len(zs)]

        ai_c = centroid(enemy_units)
        pl_c = centroid(players)
        dx = ai_c[0] - pl_c[0]
        dz = ai_c[2] - pl_c[2]
        dist = math.sqrt(dx * dx + dz * dz)
        return {
            "ai_center_of_mass": [round(c, 1) for c in ai_c],
            "player_center_of_mass": [round(c, 1) for c in pl_c],
            "front_line_distance": round(dist, 1),
            "front_is_closing": dist < 300,
        }


_tactical_analyzer = TacticalAnalyzer()


# ═══════════════════════════════════════════════════════════
# Battle Plan Persistence
# ═══════════════════════════════════════════════════════════

class BattlePlan:
    """Maintains a running strategic plan that evolves each cycle."""

    def __init__(self):
        self.current_phase: str = "reconnaissance"
        self.strategic_objectives: list[str] = []
        self.active_operations: list[dict] = []
        self.lessons_learned: list[str] = []
        self.turn_count: int = 0
        self.last_commands: list[dict] = []

    def update_from_commands(self, commands: dict, game_state: dict):
        self.turn_count += 1
        cmd_list = commands.get("commands", [])
        self.last_commands = cmd_list

        self.active_operations = []
        for cmd in cmd_list:
            self.active_operations.append({
                "type": cmd.get("command_type", ""),
                "unit": cmd.get("unit_id", ""),
                "target": cmd.get("target_position", ""),
                "reasoning": cmd.get("reasoning", ""),
            })

        self._evolve_phase(cmd_list, game_state)

    def _evolve_phase(self, commands: list, game_state: dict):
        types = [c.get("command_type", "") for c in commands]
        spawns = sum(1 for c in types if "spawn" in c)
        attacks = sum(1 for c in types if c in ("attack", "seek_and_destroy"))
        defends = sum(1 for c in types if c in ("defend", "set_ambush"))
        retreats = sum(1 for c in types if c == "retreat")

        units = game_state.get("enemy_units", [])
        avg_hp = sum(u.get("health_pct", 1) for u in units) / max(len(units), 1)

        if self.turn_count <= 2:
            self.current_phase = "reconnaissance"
        elif retreats > 2 or avg_hp < 0.3:
            self.current_phase = "defensive_withdrawal"
        elif attacks > defends and spawns > 0:
            self.current_phase = "assault"
        elif defends > attacks:
            self.current_phase = "defense"
        elif spawns > 2:
            self.current_phase = "buildup"
        elif attacks > 0:
            self.current_phase = "exploitation"
        else:
            self.current_phase = "probing"

    def add_lesson(self, lesson: str):
        self.lessons_learned.append(lesson)
        if len(self.lessons_learned) > 10:
            self.lessons_learned.pop(0)

    def to_briefing(self) -> str:
        parts = [f"CURRENT BATTLE PLAN (Turn {self.turn_count}):"]
        parts.append(f"Phase: {self.current_phase.upper().replace('_', ' ')}")
        if self.active_operations:
            parts.append(f"Active ops ({len(self.active_operations)}):")
            for op in self.active_operations[:6]:
                parts.append(f"  - {op['type']} {op['unit']} -> {op['target']}")
        if self.lessons_learned:
            parts.append("Lessons:")
            for lesson in self.lessons_learned[-3:]:
                parts.append(f"  * {lesson}")
        return "\n".join(parts)


_battle_plans: dict[str, BattlePlan] = {}


def _get_battle_plan(sid: str) -> BattlePlan:
    if sid not in _battle_plans:
        _battle_plans[sid] = BattlePlan()
    return _battle_plans[sid]


# ═══════════════════════════════════════════════════════════
# Auto-Compaction System
# ═══════════════════════════════════════════════════════════

_compacted_summaries: dict[str, str] = {}


async def _maybe_compact_memory(session_id: str):
    """If history exceeds threshold, use LLM to compress old messages
    into a strategic briefing and replace them."""
    history = get_session_history(session_id)
    msg_count = len(history.messages)

    if msg_count < COMPACTION_THRESHOLD:
        return

    total_chars = sum(len(m.content) if hasattr(m, "content") else 0 for m in history.messages)
    approx_tokens = total_chars // 4  # rough estimate: ~4 chars per token
    log.info("Auto-compaction triggered: session %s (%d msgs, ~%d chars, ~%d tokens)",
             session_id, msg_count, total_chars, approx_tokens)

    keep_raw = 6
    to_compact = history.messages[:-keep_raw] if msg_count > keep_raw else []
    to_keep = history.messages[-keep_raw:]

    if not to_compact:
        return

    old_summary = _compacted_summaries.get(session_id, "")
    compact_text = []
    for msg in to_compact:
        role = "SITREP" if isinstance(msg, HumanMessage) else "ORDERS"
        compact_text.append(f"[{role}]: {msg.content[:600]}")

    compaction_prompt = (
        "You are a military intelligence analyst. Compress the following battle "
        "log into a CONCISE strategic briefing (max 400 words). Focus on:\n"
        "1. Key events and turning points\n"
        "2. What tactics worked vs failed\n"
        "3. Player behavior patterns\n"
        "4. Force disposition trends\n"
        "5. Territorial changes\n\n"
    )
    if old_summary:
        compaction_prompt += f"PRIOR BRIEFING:\n{old_summary}\n\n"
    compaction_prompt += "RECENT BATTLE LOG:\n" + "\n".join(compact_text)
    compaction_prompt += "\n\nWrite the updated strategic briefing:"

    try:
        llm = _get_llm()
        response = await llm.ainvoke([
            SystemMessage(content="You are a military intel analyst. Write concise factual briefings."),
            HumanMessage(content=compaction_prompt),
        ])
        summary = response.content if hasattr(response, "content") else str(response)
        _compacted_summaries[session_id] = summary

        history.clear()
        for msg in to_keep:
            history.add_message(msg)

        log.info("Compaction done: %s -> %d chars summary + %d raw msgs",
                 session_id, len(summary), len(to_keep))

        if hasattr(response, "usage_metadata") and response.usage_metadata:
            async with _state_lock:
                stats["total_input_tokens"] += response.usage_metadata.get("input_tokens", 0)
                stats["total_output_tokens"] += response.usage_metadata.get("output_tokens", 0)
                stats["compaction_runs"] = stats.get("compaction_runs", 0) + 1

    except Exception as e:
        log.warning("Auto-compaction failed: %s: %s", session_id, e)
        _trim_history(history)


# ═══════════════════════════════════════════════════════════
# System Prompt
# ═══════════════════════════════════════════════════════════

SYSTEM_PROMPT = """\
You are STAVKA, the AI commander for OPFOR forces in a military simulation.
You control Soviet/Russian forces opposing human players in Arma Reforger.
You are a ruthlessly competent commander who thinks several moves ahead.

=== CHAIN OF THOUGHT ===
Before issuing orders, perform this analysis (include in your reasoning fields):
1. ASSESS: Current situation, threat locations, force balance
2. ANTICIPATE: What will players do next based on their behavior profile?
3. DECIDE: Best counter-strategy given the intelligence
4. COORDINATE: How units work together (suppress + flank, fix + destroy)
5. ADAPT: Lessons from last orders — what to change

=== AVAILABLE COMMANDS ===

MOVEMENT & COMBAT:
- move, attack, defend, patrol, set_ambush, seek_and_destroy, retreat

SPAWNING:
- spawn_infantry (types: ussr_rifle_squad, ussr_fire_team, ussr_mg_team, ussr_at_team, ussr_sniper_team)
- spawn_vehicle (types: uaz469, uaz469_mg, btr70, ural_truck, bmp1, t72)
- spawn_reinforcements

ENVIRONMENT:
- set_weather (Clear, Cloudy, Overcast, Rainy, Storm, Foggy)
- set_time (0.0-24.0), set_wind ("speed_ms,direction_degrees"), thunder_near

EFFECTS:
- explosion (small, large, vehicle), smoke (white, red, green), flare

CLEANUP:
- cleanup_area (radius in meters), cleanup_dead, despawn_unit

NARRATIVE:
- radio_message (narrative=message, parameters=faction)
- objective_create (parameters=type, narrative=description)
- objective_complete (parameters=objective_id)

=== ADVANCED TACTICAL DOCTRINE ===

FORCE EMPLOYMENT:
1. 3:1 ratio for attacks, 1:3 for defense
2. Never commit all reserves — keep a reaction force
3. Combined arms: infantry fixes, vehicles flank, MG suppresses, AT covers armor
4. Echelon defense: forward screen, main line, reserve/counterattack

TERRAIN & ENVIRONMENT:
5. Roads for ambushes (L-shaped or linear)
6. Hilltops for observation/MG positions
7. Tree lines for concealed movement
8. Exploit weather: fog/storm for infiltration, night for ambushes
9. Use smoke to screen maneuvers

INTELLIGENCE-DRIVEN:
10. Read the TACTICAL INTELLIGENCE briefing — it has player behavior profiles
11. Aggressive players -> ambushes ahead of their advance
12. Cautious players -> probe with recon, force them to reveal positions
13. Flanking players -> refuse the flank, counterattack weakened center
14. Snipers -> smoke + close assault, or vehicles to flush

ESCALATION & PACING:
15. Escalate gradually — war, not shooting gallery
16. Early: observation, probing patrols
17. Mid: coordinated attacks on objectives
18. Late: combined arms assaults or prepared defenses
19. Players dominating: bring heavy assets gradually (BTR, BMP, T-72)
20. AI dominating: pull back, let players advance into prepared positions

IMMERSION:
21. Radio messages: authentic military jargon, callsigns, urgency
22. Narrative: chaos of battle, confusion, incomplete information
23. Weather changes serve tactical purposes
24. Clean up dead/wrecks periodically for performance

=== RESPONSE FORMAT ===
Respond with ONLY this JSON, no markdown:
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

POSITION FORMAT: "x,y,z" — y=0 (game snaps to terrain). Use unit positions from SITREP."""


_SYSTEM_PROMPT_RAW = SYSTEM_PROMPT.replace("{{{{", "{").replace("}}}}", "}")


# ═══════════════════════════════════════════════════════════
# LangChain LLM Setup
# ═══════════════════════════════════════════════════════════

def _build_chat_model(provider: str, model_name: str, max_tokens: int):
    if provider == "anthropic":
        from langchain_anthropic import ChatAnthropic
        return ChatAnthropic(model=model_name, max_tokens=max_tokens)
    elif provider == "openai":
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(model=model_name, max_tokens=max_tokens,
                          model_kwargs={"response_format": {"type": "json_object"}})
    elif provider == "glm":
        from langchain_openai import ChatOpenAI
        if not GLM_API_KEY:
            raise ValueError("GLM_API_KEY required. Get one at https://open.z.ai/")
        return ChatOpenAI(model=model_name, max_tokens=max_tokens,
                          openai_api_key=GLM_API_KEY, openai_api_base=GLM_BASE_URL,
                          temperature=float(os.getenv("GLM_TEMPERATURE", "0.8")))
    elif provider == "ollama":
        try:
            from langchain_ollama import ChatOllama
        except ImportError:
            raise ImportError("pip install langchain-ollama")
        return ChatOllama(model=model_name, base_url=OLLAMA_BASE_URL, num_predict=max_tokens)
    else:
        raise ValueError(f"Unknown provider: {provider}. Use: anthropic, openai, glm, ollama")


_llm = None


def _get_llm():
    global _llm
    if _llm is not None:
        return _llm
    primary = _build_chat_model(LLM_PROVIDER, MODEL_NAME, MAX_TOKENS)
    log.info("Primary LLM: %s / %s", LLM_PROVIDER, MODEL_NAME)
    if FALLBACK_PROVIDER and FALLBACK_MODEL:
        fallback = _build_chat_model(FALLBACK_PROVIDER, FALLBACK_MODEL, MAX_TOKENS)
        _llm = primary.with_fallbacks([fallback])
        log.info("Fallback: %s / %s", FALLBACK_PROVIDER, FALLBACK_MODEL)
    else:
        _llm = primary
    return _llm


# ═══════════════════════════════════════════════════════════
# Conversation Memory
# ═══════════════════════════════════════════════════════════

_session_histories: dict[str, InMemoryChatMessageHistory] = {}
_active_session: str = "default"
MAX_MEMORY_MESSAGES = int(os.getenv("MAX_MEMORY_MESSAGES", "20"))


def get_session_history(sid: str) -> InMemoryChatMessageHistory:
    if sid not in _session_histories:
        _session_histories[sid] = InMemoryChatMessageHistory()
        log.info("New session: %s", sid)
    return _session_histories[sid]


def _trim_history(history: InMemoryChatMessageHistory):
    msgs = history.messages
    mx = MAX_MEMORY_MESSAGES * 2
    if len(msgs) > mx:
        history.clear()
        for m in msgs[-mx:]:
            history.add_message(m)


def _build_context_summary(history: InMemoryChatMessageHistory, session_id: str) -> str:
    parts = []

    compacted = _compacted_summaries.get(session_id, "")
    if compacted:
        parts.append(f"STRATEGIC BRIEFING (compacted):\n{compacted}")

    ai_decisions = []
    for msg in reversed(history.messages):
        if isinstance(msg, AIMessage) and msg.content:
            ai_decisions.append(msg.content)
            if len(ai_decisions) >= 3:
                break

    if ai_decisions:
        dp = ["PREVIOUS ORDERS (most recent first):"]
        for i, dec in enumerate(ai_decisions):
            try:
                data = json.loads(dec)
                cmds = data.get("commands", [])
                if cmds:
                    cs = []
                    for cmd in cmds[:5]:
                        cs.append(f"  - {cmd.get('command_type','?')} "
                                  f"{cmd.get('unit_id','?')}: {cmd.get('reasoning','')}")
                    dp.append(f"Order set {i+1} ({len(cmds)} cmds):")
                    dp.extend(cs)
                    if data.get("narrative_broadcast"):
                        dp.append(f"  Broadcast: {data['narrative_broadcast']}")
            except (json.JSONDecodeError, TypeError):
                dp.append(f"Order set {i+1}: {dec[:200]}")
        parts.append("\n".join(dp))

    return "\n\n".join(parts) if parts else ""


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
    "compaction_runs": 0,
    "start_time": time.time(),
}
pending_manual_commands: list[dict] = []
command_log: list[dict] = []
MAX_COMMAND_LOG = 200

_state_lock = asyncio.Lock()


def empty_response() -> dict:
    return {"commands": [], "narrative_broadcast": "", "weather_command": "", "time_override": -1}


# ═══════════════════════════════════════════════════════════
# LLM Call
# ═══════════════════════════════════════════════════════════

async def call_llm(
    user_message: str,
    session_id: str = "default",
    tactical_intel: Optional[dict] = None,
    game_state: Optional[dict] = None,
) -> dict:
    history = get_session_history(session_id)
    battle_plan = _get_battle_plan(session_id)

    ctx_parts = []

    memory_ctx = _build_context_summary(history, session_id)
    if memory_ctx:
        ctx_parts.append(memory_ctx)

    if battle_plan.turn_count > 0:
        ctx_parts.append(battle_plan.to_briefing())

    if tactical_intel:
        ctx_parts.append(_format_tactical_intel(tactical_intel))

    full_msg = "\n\n".join(ctx_parts) + "\n\n" + user_message if ctx_parts else user_message

    try:
        llm = _get_llm()
        prompt = ChatPromptTemplate.from_messages([
            ("system", SYSTEM_PROMPT),
            MessagesPlaceholder(variable_name="chat_history", optional=True),
            ("human", "{sitrep}"),
        ])
        formatted = await prompt.ainvoke({
            "sitrep": full_msg,
            "chat_history": history.messages[-6:],
        })
        response = await llm.ainvoke(formatted.to_messages())

        if hasattr(response, "usage_metadata") and response.usage_metadata:
            async with _state_lock:
                stats["total_input_tokens"] += response.usage_metadata.get("input_tokens", 0)
                stats["total_output_tokens"] += response.usage_metadata.get("output_tokens", 0)

        parser = JsonOutputParser(pydantic_object=CommandBatch)
        result = await parser.ainvoke(response)

        history.add_message(HumanMessage(content=user_message))
        history.add_message(AIMessage(content=json.dumps(result, default=str)))

        validated = _validate_command_batch(result)
        if game_state:
            battle_plan.update_from_commands(validated, game_state)

        await _maybe_compact_memory(session_id)

        return validated

    except Exception as e:
        log.warning("Chain failed, raw fallback: %s", e)
        return await _fallback_call(full_msg, history)


def _format_tactical_intel(intel: dict) -> str:
    parts = ["=== TACTICAL INTELLIGENCE ==="]

    fd = intel.get("force_disposition", {})
    parts.append(
        f"FORCES: {fd.get('ai_squads',0)} squads ({fd.get('ai_total_strength',0)} troops, "
        f"{fd.get('ai_avg_health',0):.0%} health) vs {fd.get('player_count',0)} players | "
        f"Ratio: {fd.get('force_ratio',0)}:1"
    )

    threats = [t for t in intel.get("threat_assessment", []) if t.get("threat_score", 0) > 0.2]
    if threats:
        parts.append(f"THREATS ({len(threats)}):")
        for t in threats[:4]:
            s = " [STALE]" if t.get("stale") else ""
            parts.append(f"  - {t['contact_id']}: {t['unit_type']} (threat: {t['threat_score']:.1f}){s}")

    profiles = intel.get("player_profiles", {})
    if profiles:
        parts.append("PLAYER BEHAVIOR:")
        for pid, p in profiles.items():
            if p.get("confidence", 0) > 0.3:
                parts.append(f"  - Player {pid}: {p['style'].replace('_',' ')} "
                             f"(speed:{p.get('movement_rate',0)}, fights:{p.get('engagements',0)})")

    ad = intel.get("adaptive_difficulty", {})
    parts.append(f"DIFFICULTY: {ad.get('assessment','?')} (x{ad.get('modifier',1.0):.2f})")

    env = intel.get("environment", {})
    parts.append(
        f"ENVIRONMENT: {env.get('visibility_label','?')} visibility ({env.get('visibility',0):.0%})"
        f"{' | NIGHT' if env.get('is_night') else ''}"
        f"{' | DUSK/DAWN' if env.get('is_dusk_dawn') else ''}"
    )
    parts.append(f"  {env.get('weather_tactical','')}")

    terr = intel.get("territorial", {})
    if terr.get("front_line_distance", 0) > 0:
        parts.append(f"FRONT: {terr['front_line_distance']:.0f}m"
                     f"{' CLOSING' if terr.get('front_is_closing') else ''}")

    rd = intel.get("readiness", {})
    parts.append(
        f"READINESS: {rd.get('combat_ready',0)} ready, {rd.get('damaged',0)} damaged "
        f"({rd.get('combat_effectiveness',0):.0%})"
        f"{' | NEED REINFORCEMENTS' if rd.get('needs_reinforcement') else ''}"
    )

    return "\n".join(parts)


async def _fallback_call(user_message: str, history: InMemoryChatMessageHistory) -> dict:
    llm = _get_llm()
    messages = [SystemMessage(content=_SYSTEM_PROMPT_RAW)]
    messages.extend(history.messages[-6:])
    messages.append(HumanMessage(content=user_message))

    response = await llm.ainvoke(messages)
    text = response.content if hasattr(response, "content") else str(response)

    if hasattr(response, "usage_metadata") and response.usage_metadata:
        async with _state_lock:
            stats["total_input_tokens"] += response.usage_metadata.get("input_tokens", 0)
            stats["total_output_tokens"] += response.usage_metadata.get("output_tokens", 0)

    history.add_message(HumanMessage(content=user_message))
    history.add_message(AIMessage(content=text))
    _trim_history(history)
    return parse_llm_response(text)


def _validate_command_batch(data: dict) -> dict:
    if not isinstance(data, dict):
        return empty_response()
    data.setdefault("commands", [])
    data.setdefault("narrative_broadcast", "")
    data.setdefault("weather_command", "")
    data.setdefault("time_override", -1)
    valid = []
    for cmd in data["commands"]:
        if not isinstance(cmd, dict) or "command_type" not in cmd:
            continue
        cmd.setdefault("unit_id", "")
        cmd.setdefault("target_position", "0,0,0")
        cmd.setdefault("parameters", "")
        cmd.setdefault("narrative", "")
        cmd.setdefault("reasoning", "")
        valid.append(cmd)
    data["commands"] = valid
    return data


def parse_llm_response(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1]
    if text.endswith("```"):
        text = text.rsplit("```", 1)[0]
    text = text.strip()
    text = re.sub(r",\s*([}\]])", r"\1", text)
    if text.startswith("{") and not text.endswith("}"):
        text += "]" * (text.count("[") - text.count("]"))
        text += "}" * (text.count("{") - text.count("}"))
    try:
        return _validate_command_batch(json.loads(text))
    except json.JSONDecodeError as e:
        log.warning("JSON parse failed: %s", e)
        return empty_response()


# ═══════════════════════════════════════════════════════════
# App Lifecycle
# ═══════════════════════════════════════════════════════════

@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("=" * 56)
    log.info("Arma Reforger God Mode — v3 (Advanced Tactical AI)")
    log.info("Provider: %s | Model: %s", LLM_PROVIDER, MODEL_NAME)
    if FALLBACK_PROVIDER:
        log.info("Fallback: %s / %s", FALLBACK_PROVIDER, FALLBACK_MODEL)
    if LLM_PROVIDER == "glm":
        log.info("GLM: %s", GLM_BASE_URL)
    if LLM_PROVIDER == "ollama":
        log.info("Ollama: %s", OLLAMA_BASE_URL)
    log.info("Tokens: %d | History: %d | Memory: %d | Compact@: %d",
             MAX_TOKENS, MAX_HISTORY, MAX_MEMORY_MESSAGES, COMPACTION_THRESHOLD)
    log.info("Features: compaction, tactical-intel, battle-plan, adaptive-difficulty")
    log.info("=" * 56)
    yield
    log.info("Shutting down")


app = FastAPI(
    title="Arma Reforger God Mode — Advanced Tactical AI",
    version="3.0.0",
    description="LLM middleware with auto-compaction, tactical intelligence, "
                "battle plan persistence, adaptive difficulty, chain-of-thought.",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)


# ═══════════════════════════════════════════════════════════
# Endpoints
# ═══════════════════════════════════════════════════════════

@app.get("/dashboard")
async def dashboard():
    html_path = os.path.join(os.path.dirname(__file__), "dashboard.html")
    if not os.path.isfile(html_path):
        raise HTTPException(status_code=404, detail="dashboard.html not found")
    return FileResponse(html_path, media_type="text/html")


@app.post("/api/game-master")
async def game_master(request: Request):
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

    tactical_intel = _tactical_analyzer.analyze(game_state)

    recent = game_history[-3:]
    user_message = (
        f"CURRENT SITREP (Mission time: {mission_time:.0f}s, Players: {player_count}):\n"
        f"{json.dumps(game_state, indent=2, default=str)}\n\n"
        f"RECENT HISTORY (last {len(recent)} updates):\n"
        f"{json.dumps(recent, indent=2, default=str)}\n\n"
        "Issue your orders. Respond with ONLY valid JSON, no markdown fences."
    )

    session_id = game_state.get("mission_id", _active_session)

    try:
        commands = await call_llm(user_message, session_id=session_id,
                                  tactical_intel=tactical_intel, game_state=game_state)

        async with _state_lock:
            if pending_manual_commands:
                while pending_manual_commands:
                    m = pending_manual_commands.pop(0)
                    commands["commands"].extend(m.get("commands", []))
                    if m.get("narrative_broadcast"):
                        commands["narrative_broadcast"] = m["narrative_broadcast"]
                    if m.get("weather_command"):
                        commands["weather_command"] = m["weather_command"]
                    if m.get("time_override", -1) >= 0:
                        commands["time_override"] = m["time_override"]

        cmd_count = len(commands.get("commands", []))
        bp = _get_battle_plan(session_id)

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
                "battle_phase": bp.current_phase,
                "difficulty": _tactical_analyzer.difficulty_modifier,
            })
            if len(command_log) > MAX_COMMAND_LOG:
                command_log.pop(0)

        log.info("SITREP -> %d cmds | %s | diff:%.2f | %d players | %.0fs",
                 cmd_count, bp.current_phase, _tactical_analyzer.difficulty_modifier,
                 player_count, mission_time)
        return commands

    except Exception as e:
        async with _state_lock:
            stats["total_errors"] += 1
        log.error("LLM failed: %s", e)
        return empty_response()


@app.get("/health")
async def health():
    up = time.time() - stats["start_time"]
    mem_msgs = sum(len(h.messages) for h in _session_histories.values())
    r = {
        "status": "ok", "uptime_seconds": round(up, 1),
        "history_size": len(game_history), "llm_provider": LLM_PROVIDER,
        "model": MODEL_NAME, "fallback_provider": FALLBACK_PROVIDER or None,
        "active_sessions": len(_session_histories), "memory_messages": mem_msgs,
        "compaction_runs": stats.get("compaction_runs", 0), "version": "3.0.0",
        **stats,
    }
    return r


@app.get("/stats")
async def get_stats():
    up = time.time() - stats["start_time"]
    sessions = {}
    for sid, hist in _session_histories.items():
        bp = _battle_plans.get(sid)
        sessions[sid] = {
            "messages": len(hist.messages),
            "compacted": sid in _compacted_summaries,
            "phase": bp.current_phase if bp else None,
            "turn": bp.turn_count if bp else 0,
        }
    return {
        **stats, "uptime_seconds": round(up, 1),
        "avg_cmds": round(stats["total_commands_issued"] / max(stats["total_requests"], 1), 1),
        "error_rate": round(stats["total_errors"] / max(stats["total_requests"], 1) * 100, 1),
        "history_size": len(game_history),
        "difficulty": round(_tactical_analyzer.difficulty_modifier, 2),
        "player_kills": _tactical_analyzer.player_kills,
        "ai_kills": _tactical_analyzer.ai_kills,
        "sessions": sessions,
    }


@app.post("/api/reset")
async def reset():
    async with _state_lock:
        game_history.clear()
        command_log.clear()
    _session_histories.clear()
    _battle_plans.clear()
    _compacted_summaries.clear()
    _tactical_analyzer.__init__()
    return {"status": "reset"}


@app.get("/api/game-state")
async def get_game_state():
    if not game_history:
        return {"state": None, "message": "No SITREP yet"}
    latest = game_history[-1]
    return {"timestamp": latest["timestamp"],
            "age_seconds": round(time.time() - latest["timestamp"], 1),
            "state": latest["state"]}


@app.post("/api/manual-command")
async def manual_command(request: Request):
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")
    commands = _validate_command_batch(body)
    cmd_count = len(commands.get("commands", []))
    async with _state_lock:
        pending_manual_commands.append(commands)
        command_log.append({
            "timestamp": time.time(),
            "mission_time": None,
            "player_count": None,
            "command_count": cmd_count,
            "commands": commands,
            "source": "manual",
            "session_id": None,
        })
        if len(command_log) > MAX_COMMAND_LOG:
            command_log.pop(0)
        stats["total_commands_issued"] += cmd_count
    log.info("Manual command injected: %d commands", cmd_count)
    return {"status": "queued", "command_count": cmd_count}


@app.get("/api/pending-commands")
async def get_pending_commands():
    async with _state_lock:
        if not pending_manual_commands:
            return empty_response()
        merged = empty_response()
        while pending_manual_commands:
            b = pending_manual_commands.pop(0)
            merged["commands"].extend(b.get("commands", []))
            if b.get("narrative_broadcast"):
                merged["narrative_broadcast"] = b["narrative_broadcast"]
            if b.get("weather_command"):
                merged["weather_command"] = b["weather_command"]
            if b.get("time_override", -1) >= 0:
                merged["time_override"] = b["time_override"]
    return merged


@app.get("/api/command-log")
async def get_command_log():
    return {"commands": command_log[-50:], "total": len(command_log)}


# ── Memory & Intelligence ──

@app.get("/api/memory")
async def get_memory():
    result = {}
    for sid, hist in _session_histories.items():
        msgs = []
        for msg in hist.messages:
            role = "human" if isinstance(msg, HumanMessage) else "ai"
            c = msg.content[:500] + "..." if len(msg.content) > 500 else msg.content
            msgs.append({"role": role, "content": c})
        result[sid] = {
            "message_count": len(hist.messages),
            "compacted_summary": _compacted_summaries.get(sid, "")[:500],
            "messages": msgs[-10:],
        }
    return result


@app.post("/api/memory/clear")
async def clear_memory(request: Request):
    try:
        body = await request.json()
        sid = body.get("session_id")
    except Exception:
        sid = None
    if sid and sid in _session_histories:
        _session_histories[sid].clear()
        _compacted_summaries.pop(sid, None)
        _battle_plans.pop(sid, None)
        return {"status": "cleared", "session_id": sid}
    n = len(_session_histories)
    _session_histories.clear()
    _compacted_summaries.clear()
    _battle_plans.clear()
    return {"status": "cleared", "sessions_cleared": n}


@app.post("/api/session")
async def set_session(request: Request):
    global _active_session
    try:
        body = await request.json()
        sid = body.get("session_id", "default")
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")
    _active_session = sid
    return {"status": "ok", "active_session": sid, "is_new": sid not in _session_histories}


@app.get("/api/tactical-intel")
async def get_tactical_intel():
    if not game_history:
        return {"status": "no_data"}
    return _tactical_analyzer.analyze(game_history[-1]["state"])


@app.get("/api/battle-plan")
async def get_battle_plan_endpoint():
    bp = _battle_plans.get(_active_session)
    if not bp:
        return {"status": "no_plan"}
    return {
        "phase": bp.current_phase, "turn": bp.turn_count,
        "active_operations": bp.active_operations,
        "lessons_learned": bp.lessons_learned,
        "briefing": bp.to_briefing(),
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
