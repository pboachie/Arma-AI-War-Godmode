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

# ─── LLM Retry / Rate-Limit Config ───
LLM_MAX_RETRIES = int(os.getenv("LLM_MAX_RETRIES", "5"))
LLM_RETRY_BASE_DELAY = float(os.getenv("LLM_RETRY_BASE_DELAY", "2.0"))   # seconds
LLM_RETRY_MAX_DELAY = float(os.getenv("LLM_RETRY_MAX_DELAY", "60.0"))    # cap per retry
LLM_RETRY_JITTER = float(os.getenv("LLM_RETRY_JITTER", "0.5"))           # 0-1 range
LLM_RATE_LIMIT_RPM = int(os.getenv("LLM_RATE_LIMIT_RPM", "20"))          # requests/minute

import random as _random


class _LLMRateLimiter:
    """Async token-bucket rate limiter for outbound LLM API calls."""

    def __init__(self, rpm: int):
        self._rate = rpm / 60.0          # tokens per second
        self._max_tokens = max(rpm / 6, 3)  # burst capacity (10s worth, min 3)
        self._tokens = self._max_tokens
        self._last_refill = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self):
        async with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_refill
            self._tokens = min(self._max_tokens,
                               self._tokens + elapsed * self._rate)
            self._last_refill = now

            if self._tokens >= 1.0:
                self._tokens -= 1.0
                return

            # Must wait for a token
            wait = (1.0 - self._tokens) / self._rate
            self._tokens = 0.0

        log.debug("LLM rate limiter: waiting %.2fs before next request", wait)
        await asyncio.sleep(wait)

        async with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_refill
            self._tokens = min(self._max_tokens,
                               self._tokens + elapsed * self._rate)
            self._last_refill = now
            self._tokens = max(0.0, self._tokens - 1.0)


_llm_rate_limiter = _LLMRateLimiter(LLM_RATE_LIMIT_RPM)


def _extract_retry_after(err_str: str) -> Optional[float]:
    """Try to extract a retry-after value (seconds) from an error message."""
    match = re.search(r"retry[- _]?after[:\s]*(\d+\.?\d*)", err_str, re.IGNORECASE)
    if match:
        try:
            return float(match.group(1))
        except ValueError:
            pass
    return None


async def _invoke_llm_with_retry(llm, messages, *, caller_label: str = "llm"):
    """Invoke an LLM with exponential backoff retry on transient errors (429, 5xx, timeouts)."""
    last_exc = None
    for attempt in range(1, LLM_MAX_RETRIES + 1):
        await _llm_rate_limiter.acquire()
        sem = _get_llm_semaphore()
        try:
            async with sem:
                response = await llm.ainvoke(messages)
            if attempt > 1:
                log.info("[%s] Succeeded on attempt %d/%d", caller_label, attempt, LLM_MAX_RETRIES)
            return response
        except Exception as e:
            last_exc = e
            err_str = str(e).lower()
            err_type = type(e).__name__

            # Determine if this is a retryable error
            is_rate_limit = "429" in err_str or "rate" in err_str or "too many" in err_str
            is_server_error = any(code in err_str for code in ("500", "502", "503", "504"))
            is_timeout = "timeout" in err_str or "timed out" in err_str
            is_overloaded = "overloaded" in err_str or "capacity" in err_str
            is_retryable = is_rate_limit or is_server_error or is_timeout or is_overloaded

            # Track retry/429 stats (stats dict defined later, safe at runtime)
            try:
                if is_rate_limit:
                    stats["llm_429s"] = stats.get("llm_429s", 0) + 1
                if is_retryable:
                    stats["llm_retries"] = stats.get("llm_retries", 0) + 1
            except NameError:
                pass

            if not is_retryable:
                log.error("[%s] Non-retryable error on attempt %d: %s: %s",
                          caller_label, attempt, err_type, e)
                raise

            if attempt >= LLM_MAX_RETRIES:
                log.error("[%s] All %d attempts exhausted. Last error: %s: %s",
                          caller_label, LLM_MAX_RETRIES, err_type, e)
                raise

            # Exponential backoff: base * 2^(attempt-1), capped, with jitter
            delay = min(LLM_RETRY_BASE_DELAY * (2 ** (attempt - 1)), LLM_RETRY_MAX_DELAY)
            jitter = delay * LLM_RETRY_JITTER * _random.random()
            delay += jitter

            # If the error contains a Retry-After header hint, respect it
            retry_after = _extract_retry_after(err_str)
            if retry_after and retry_after > delay:
                delay = min(retry_after + jitter, LLM_RETRY_MAX_DELAY)

            reason = "rate-limited" if is_rate_limit else (
                "server-error" if is_server_error else (
                "timeout" if is_timeout else "overloaded"))
            log.warning("[%s] Attempt %d/%d failed (%s: %s). Retrying in %.1fs...",
                        caller_label, attempt, LLM_MAX_RETRIES, reason, err_type, delay)
            await asyncio.sleep(delay)

    raise last_exc


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
        # Enhanced profiling state
        self.player_style_history: dict[int, list[str]] = defaultdict(list)
        self.player_vehicle_status: dict[int, bool] = {}
        self.player_terrain: dict[int, str] = {}
        self.player_elevation: dict[int, float] = {}

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
            threat_score = confidence * math.exp(-staleness / 60.0)
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

        # Readiness & Morale
        ready = [u for u in enemy_units if u.get("health_pct", 1.0) > 0.5]
        damaged = [u for u in enemy_units if 0.1 < u.get("health_pct", 1.0) <= 0.5]
        broken = [u for u in enemy_units if u.get("health_pct", 1.0) <= 0.3]
        analysis["readiness"] = {
            "combat_ready": len(ready),
            "damaged": len(damaged),
            "combat_effectiveness": round(len(ready) / max(len(enemy_units), 1), 2),
            "needs_reinforcement": len(ready) < len(players) * 2,
        }

        # Morale assessment — units below 30% health should retreat
        morale_warnings = []
        for u in enemy_units:
            hp = u.get("health_pct", 1.0)
            uid = u.get("unit_id", "unknown")
            strength = u.get("strength", 0)
            if hp <= 0.3:
                morale_warnings.append({
                    "unit_id": uid,
                    "health_pct": round(hp, 2),
                    "strength": strength,
                    "recommendation": "RETREAT — combat ineffective, withdraw to preserve force",
                })
            elif hp <= 0.5 and strength <= 2:
                morale_warnings.append({
                    "unit_id": uid,
                    "health_pct": round(hp, 2),
                    "strength": strength,
                    "recommendation": "MERGE or RETREAT — understrength, consider consolidation",
                })
        analysis["morale"] = {
            "broken_units": len(broken),
            "warnings": morale_warnings,
            "overall": (
                "critical" if len(broken) > len(enemy_units) * 0.4 else
                "shaky" if len(broken) > len(enemy_units) * 0.2 else
                "steady" if damaged else "strong"
            ),
        }

        # Spatial relationships (Priority 2)
        analysis["spatial"] = self._compute_spatial_relationships(enemy_units, players)

        # Player movement vectors (Priority 4)
        player_vectors = {}
        for player in players:
            pid = player.get("player_id", 0)
            vel = player.get("velocity", [0, 0, 0])
            heading = player.get("heading", 0)
            speed = math.sqrt(vel[0]**2 + (vel[2]**2 if len(vel) > 2 else 0)) if vel else 0

            # Predict direction from velocity
            if speed > 0.5:
                move_bearing = math.degrees(math.atan2(vel[0], vel[2] if len(vel) > 2 else 0)) % 360
                if move_bearing < 45 or move_bearing > 315:
                    direction = "north"
                elif move_bearing < 135:
                    direction = "east"
                elif move_bearing < 225:
                    direction = "south"
                else:
                    direction = "west"
            else:
                direction = "stationary"

            player_vectors[str(pid)] = {
                "heading": heading,
                "speed_mps": round(speed, 1),
                "predicted_direction": direction,
                "is_moving": speed > 0.5,
                "is_sprinting": speed > 4.0,
            }
        analysis["player_vectors"] = player_vectors

        # Terrain summary (Priority 5)
        terrain_samples = game_state.get("terrain_samples", [])
        if terrain_samples:
            terrain_summary = {}
            for sample in terrain_samples:
                t_type = sample.get("terrain_type", "unknown")
                if t_type not in terrain_summary:
                    terrain_summary[t_type] = {"count": 0, "total_cover": 0, "positions": []}
                terrain_summary[t_type]["count"] += 1
                terrain_summary[t_type]["total_cover"] += sample.get("cover_density", 0)
                if len(terrain_summary[t_type]["positions"]) < 3:
                    terrain_summary[t_type]["positions"].append(sample.get("position", [0, 0, 0]))

            for t_type in terrain_summary:
                count = terrain_summary[t_type]["count"]
                terrain_summary[t_type]["avg_cover"] = round(
                    terrain_summary[t_type]["total_cover"] / max(count, 1), 2)
                del terrain_summary[t_type]["total_cover"]

            analysis["terrain_summary"] = terrain_summary

        return analysis

    def _update_player_tracking(self, game_state: dict):
        for player in game_state.get("players", []):
            pid = player.get("player_id", 0)
            pos = player.get("position", [0, 0, 0])
            self.player_positions[pid].append(pos)
            if len(self.player_positions[pid]) > 20:
                self.player_positions[pid].pop(0)

            # Track vehicle status, terrain type, and elevation for enhanced profiling
            self.player_vehicle_status[pid] = player.get("is_in_vehicle", False)
            if len(pos) > 1:
                self.player_elevation[pid] = pos[1]

        # Extract per-player terrain from terrain_samples if available
        terrain_samples = game_state.get("terrain_samples", [])
        if terrain_samples:
            for player in game_state.get("players", []):
                pid = player.get("player_id", 0)
                pos = player.get("position", [0, 0, 0])
                closest_terrain = "unknown"
                closest_dist = float("inf")
                for sample in terrain_samples:
                    s_pos = sample.get("position", [0, 0, 0])
                    dx = pos[0] - s_pos[0]
                    dz = (pos[2] - s_pos[2]) if len(pos) > 2 and len(s_pos) > 2 else 0
                    dist = math.sqrt(dx * dx + dz * dz)
                    if dist < closest_dist:
                        closest_dist = dist
                        closest_terrain = sample.get("terrain_type", "unknown")
                self.player_terrain[pid] = closest_terrain

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
                profiles[pid] = {
                    "style": "unknown",
                    "confidence": 0.0,
                    "movement_rate": 0,
                    "trend": "insufficient_data",
                    "predicted_next_action": "insufficient data to predict",
                }
                continue

            total_dist = 0
            for i in range(1, len(positions)):
                dx = positions[i][0] - positions[i - 1][0]
                dz = (positions[i][2] - positions[i - 1][2]
                      if len(positions[i]) > 2 else 0)
                total_dist += math.sqrt(dx * dx + dz * dz)
            avg_speed = total_dist / len(positions)

            engagement_count = len(self.player_engagements.get(pid, []))
            in_vehicle = self.player_vehicle_status.get(pid, False)
            terrain = self.player_terrain.get(pid, "unknown")
            elevation = self.player_elevation.get(pid, 0.0)

            # --- Enhanced playstyle classification ---
            # New granular styles checked first when conditions are met
            if in_vehicle and avg_speed > 80:
                style = "vehicle_rusher"
            elif avg_speed < 15 and terrain in ("urban", "town", "city", "buildings"):
                style = "building_clearer"
            elif avg_speed < 10 and elevation > 50 and engagement_count > 1:
                style = "overwatch"
            elif avg_speed > 80 and engagement_count > 3:
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

            # --- Track style history for trend detection ---
            history = self.player_style_history[pid]
            history.append(style)
            if len(history) > 3:
                history.pop(0)

            trend = self._detect_trend(history)

            # --- Bayesian-inspired confidence ---
            # Reaches ~87% at 3 observations, ~97% at 5
            observations = len(positions)
            confidence = round(1.0 - 0.5 ** (observations / 3.0), 3)

            # --- Predicted next action ---
            predicted = self._predict_next_action(style, trend)

            profiles[pid] = {
                "style": style,
                "confidence": confidence,
                "movement_rate": round(avg_speed, 1),
                "engagements": engagement_count,
                "trend": trend,
                "predicted_next_action": predicted,
            }
        return profiles

    def _detect_trend(self, history: list[str]) -> str:
        """Detect behavioral trend from the last 3 classified styles."""
        if len(history) < 2:
            return "insufficient_data"

        _AGGRESSION = {
            "cautious_observer": 0,
            "building_clearer": 1,
            "balanced_advance": 2,
            "defensive_sniper": 2,
            "overwatch": 2,
            "flanking_maneuver": 3,
            "aggressive_push": 4,
            "aggressive_assault": 5,
            "vehicle_rusher": 5,
        }

        scores = [_AGGRESSION.get(s, 2) for s in history]

        # All same style
        if len(set(history)) == 1:
            return "consistent"

        # Check for flanking shift
        if history[-1] == "flanking_maneuver" and history[-2] != "flanking_maneuver":
            return "shifting_to_flanking"

        # Net direction over full history window
        delta = scores[-1] - scores[0]
        if delta >= 2:
            return "escalating"
        elif delta <= -2:
            return "de-escalating"

        # Smaller single-step change
        if scores[-1] > scores[-2]:
            return "escalating"
        elif scores[-1] < scores[-2]:
            return "de-escalating"

        return "consistent"

    def _predict_next_action(self, style: str, trend: str) -> str:
        """Predict the player's likely next action based on style and trend."""
        _PREDICTIONS = {
            ("aggressive_assault", "escalating"):
                "will push deeper, expect frontal advance",
            ("aggressive_assault", "consistent"):
                "continuing frontal assault, prepare defensive positions",
            ("aggressive_assault", "de-escalating"):
                "may be pulling back to regroup after push",
            ("defensive_sniper", "consistent"):
                "will hold position, expect long-range engagement",
            ("defensive_sniper", "escalating"):
                "sniper repositioning aggressively, may switch to assault",
            ("defensive_sniper", "de-escalating"):
                "retreating to deeper cover, expect disengagement",
            ("flanking_maneuver", "consistent"):
                "expect flanking approach from side/rear",
            ("flanking_maneuver", "escalating"):
                "flanker accelerating, imminent attack from flank",
            ("flanking_maneuver", "shifting_to_flanking"):
                "just started flanking, watch lateral movement",
            ("cautious_observer", "consistent"):
                "holding position, gathering intel before committing",
            ("cautious_observer", "escalating"):
                "observer preparing to move, expect advance soon",
            ("cautious_observer", "de-escalating"):
                "fully defensive posture, may be waiting for reinforcement",
            ("balanced_advance", "consistent"):
                "steady methodical advance, covering ground cautiously",
            ("balanced_advance", "escalating"):
                "picking up pace, transitioning to aggressive push",
            ("balanced_advance", "de-escalating"):
                "slowing advance, may shift to defensive posture",
            ("aggressive_push", "consistent"):
                "sustained aggressive push, expect continued pressure",
            ("aggressive_push", "escalating"):
                "all-out assault imminent, maximum aggression",
            ("aggressive_push", "de-escalating"):
                "push losing momentum, may consolidate gains",
            ("vehicle_rusher", "consistent"):
                "vehicle assault continues, expect rapid positional changes",
            ("vehicle_rusher", "escalating"):
                "vehicle charge incoming, prepare anti-vehicle response",
            ("vehicle_rusher", "de-escalating"):
                "vehicle slowing, may dismount or reposition",
            ("building_clearer", "consistent"):
                "clearing structures methodically, expect close-quarters combat",
            ("building_clearer", "escalating"):
                "accelerating urban clearance, pushing through buildings",
            ("building_clearer", "de-escalating"):
                "slowing clearance, may be fortifying cleared building",
            ("overwatch", "consistent"):
                "maintaining overwatch, covering team from elevation",
            ("overwatch", "escalating"):
                "overwatch becoming aggressive, expect accurate fire from height",
            ("overwatch", "de-escalating"):
                "overwatch pulling back, may relocate to new vantage point",
        }

        prediction = _PREDICTIONS.get((style, trend))
        if prediction:
            return prediction

        # Fallback predictions by style alone
        _STYLE_FALLBACK = {
            "aggressive_assault": "expect continued aggression and frontal pressure",
            "flanking_maneuver": "expect flanking approach from side/rear",
            "defensive_sniper": "will hold position, expect long-range engagement",
            "cautious_observer": "holding position, low threat currently",
            "balanced_advance": "steady advance, moderate threat level",
            "aggressive_push": "sustained aggressive pressure expected",
            "vehicle_rusher": "fast vehicle-based assault expected",
            "building_clearer": "close-quarters urban engagement expected",
            "overwatch": "elevated overwatch providing suppressive fire",
        }
        return _STYLE_FALLBACK.get(style, "behavior unpredictable, monitor closely")

    def _track_engagements(self, game_state: dict):
        for event in game_state.get("recent_events", []):
            if "enemy_killed" in event:
                self.player_kills += 1
            if "player_hit" in event:
                self.ai_kills += 1

        total = self.player_kills + self.ai_kills
        if total >= 5:
            ratio = self.player_kills / max(total, 1)
            # Target difficulty based on kill ratio, smoothed with EMA (alpha=0.1)
            if ratio > 0.7:
                target = min(self.difficulty_modifier + 0.2, 2.0)
            elif ratio < 0.3:
                target = max(self.difficulty_modifier - 0.2, 0.4)
            else:
                target = 1.0
            alpha = 0.1  # smoothing factor — lower = less oscillation
            self.difficulty_modifier += alpha * (target - self.difficulty_modifier)
            self.difficulty_modifier = max(0.4, min(2.0, self.difficulty_modifier))

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

    def _compute_spatial_relationships(self, enemy_units: list, players: list) -> dict:
        """Pre-compute spatial relationships so the LLM doesn't have to do coordinate math."""
        result = {}

        # Nearest enemy to each player
        nearest_enemy = {}
        for player in players:
            pid = player.get("player_id", 0)
            p_pos = player.get("position", [0, 0, 0])
            best_dist = float("inf")
            best_unit = None
            for unit in enemy_units:
                u_pos = unit.get("position", [0, 0, 0])
                dx = p_pos[0] - u_pos[0]
                dz = (p_pos[2] - u_pos[2]) if len(p_pos) > 2 and len(u_pos) > 2 else 0
                dist = math.sqrt(dx * dx + dz * dz)
                if dist < best_dist:
                    best_dist = dist
                    best_unit = unit.get("unit_id")
            if best_unit:
                nearest_enemy[str(pid)] = {
                    "unit": best_unit,
                    "distance_m": round(best_dist, 1),
                    "engagement_range": (
                        "CQB" if best_dist < 50 else
                        "close" if best_dist < 150 else
                        "medium" if best_dist < 400 else
                        "long" if best_dist < 800 else "beyond_effective"
                    ),
                }
        result["nearest_enemy_to_each_player"] = nearest_enemy

        # Flanking opportunities: units that could approach players from the side/rear
        flanking_opportunities = []
        for unit in enemy_units:
            u_pos = unit.get("position", [0, 0, 0])
            u_id = unit.get("unit_id", "")
            for player in players:
                pid = player.get("player_id", 0)
                p_pos = player.get("position", [0, 0, 0])

                dx = u_pos[0] - p_pos[0]
                dz = (u_pos[2] - p_pos[2]) if len(u_pos) > 2 and len(p_pos) > 2 else 0
                dist = math.sqrt(dx * dx + dz * dz)

                if dist > 800 or dist < 30:
                    continue

                # Calculate bearing from player to unit
                bearing = math.degrees(math.atan2(dx, dz)) % 360

                # Check player heading if available
                heading = player.get("heading", 0)
                # Angle difference between player facing and unit direction
                angle_diff = abs((bearing - heading + 180) % 360 - 180)

                # If unit is more than 90 degrees from player's facing direction, it's a flank
                if angle_diff > 90:
                    flanking_opportunities.append({
                        "unit": u_id,
                        "target_player": pid,
                        "distance_m": round(dist, 1),
                        "approach_angle": round(bearing, 1),
                        "flank_quality": "excellent" if angle_diff > 135 else "good",
                    })

        flanking_opportunities.sort(key=lambda f: f["distance_m"])
        result["flanking_opportunities"] = flanking_opportunities[:8]

        # Units not near any player (hidden/reserve)
        hidden_units = []
        for unit in enemy_units:
            u_pos = unit.get("position", [0, 0, 0])
            min_dist = float("inf")
            for player in players:
                p_pos = player.get("position", [0, 0, 0])
                dx = u_pos[0] - p_pos[0]
                dz = (u_pos[2] - p_pos[2]) if len(u_pos) > 2 and len(p_pos) > 2 else 0
                dist = math.sqrt(dx * dx + dz * dz)
                min_dist = min(min_dist, dist)
            if min_dist > 400:
                hidden_units.append(unit.get("unit_id", ""))
        result["units_beyond_player_awareness"] = hidden_units

        # Player clustering analysis
        if len(players) >= 2:
            positions = [p.get("position", [0, 0, 0]) for p in players]
            total_spread = 0
            pairs = 0
            for i in range(len(positions)):
                for j in range(i + 1, len(positions)):
                    dx = positions[i][0] - positions[j][0]
                    dz = (positions[i][2] - positions[j][2]
                          if len(positions[i]) > 2 and len(positions[j]) > 2 else 0)
                    total_spread += math.sqrt(dx * dx + dz * dz)
                    pairs += 1
            avg_spread = total_spread / max(pairs, 1)
            result["player_clustering"] = {
                "avg_spread_m": round(avg_spread, 1),
                "formation": (
                    "tight_group" if avg_spread < 50 else
                    "loose_group" if avg_spread < 150 else
                    "spread" if avg_spread < 400 else "dispersed"
                ),
                "tactical_note": (
                    "Players grouped tight — area weapons/flanking maneuver effective"
                    if avg_spread < 100 else
                    "Players spread — divide and isolate recommended"
                    if avg_spread > 300 else
                    "Standard spacing — coordinated multi-axis approach recommended"
                ),
            }
        else:
            result["player_clustering"] = {
                "avg_spread_m": 0,
                "formation": "single" if len(players) == 1 else "none",
                "tactical_note": "Single player — focus force concentration" if players else "No players detected",
            }

        # Counter-ambush detection: players stationary with high engagement count
        ambush_warnings = []
        player_profiles = self._classify_players()
        for player in players:
            pid = player.get("player_id", 0)
            vel = player.get("velocity", [0, 0, 0])
            speed = math.sqrt(vel[0]**2 + (vel[2]**2 if len(vel) > 2 else 0)) if vel else 0
            positions = self.player_positions.get(pid, [])
            engagements = len(self.player_engagements.get(pid, []))

            # Stationary + high engagements = likely ambush position
            if len(positions) >= 3 and speed < 1.0 and engagements >= 2:
                # Check if position has been stable (low movement over recent history)
                recent = positions[-3:]
                total_move = 0
                for i in range(1, len(recent)):
                    dx = recent[i][0] - recent[i-1][0]
                    dz = (recent[i][2] - recent[i-1][2]) if len(recent[i]) > 2 else 0
                    total_move += math.sqrt(dx * dx + dz * dz)

                if total_move < 30:  # barely moved over 3 SITREPs
                    p_pos = player.get("position", [0, 0, 0])
                    heading = player.get("heading", 0)
                    profile = player_profiles.get(pid, {})
                    style = profile.get("style", "unknown")
                    is_sniper = style in ("defensive_sniper",)
                    kill_zone_arc = 60.0 if is_sniper else 90.0
                    kill_zone_depth = 500.0 if is_sniper else 200.0
                    stationary_cycles = 0
                    for k in range(len(positions) - 1, 0, -1):
                        sdx = positions[k][0] - positions[k - 1][0]
                        sdz = ((positions[k][2] - positions[k - 1][2]) if len(positions[k]) > 2 else 0)
                        if math.sqrt(sdx * sdx + sdz * sdz) < 10:
                            stationary_cycles += 1
                        else:
                            break
                    duration_seconds = stationary_cycles * 30
                    reverse_bearing = (heading + 180.0) % 360.0
                    safe_bearing_left = (reverse_bearing - 30.0) % 360.0
                    safe_bearing_right = (reverse_bearing + 30.0) % 360.0
                    safe_approach_bearing = reverse_bearing
                    safe_rad = math.radians(safe_approach_bearing)
                    safe_x = round(p_pos[0] + 200.0 * math.sin(safe_rad), 1)
                    safe_z_val = p_pos[2] if len(p_pos) > 2 else 0
                    safe_z = round(safe_z_val + 200.0 * math.cos(safe_rad), 1)
                    ambush_warnings.append({
                        "player_id": pid,
                        "position": p_pos,
                        "stationary_cycles": stationary_cycles,
                        "duration_seconds": duration_seconds,
                        "engagements": engagements,
                        "player_style": style,
                        "kill_zone_bearing": round(heading, 1),
                        "kill_zone_arc": kill_zone_arc,
                        "kill_zone_depth_m": kill_zone_depth,
                        "safe_approach_bearing": round(safe_approach_bearing, 1),
                        "safe_approach_options": [round(safe_bearing_left, 1), round(safe_bearing_right, 1)],
                        "safe_approach_position": f"{safe_x},0,{safe_z}",
                        "threat": (
                            f"Player is stationary ({duration_seconds}s) and engaging "
                            f"from {'sniper' if is_sniper else 'rifleman'} position. "
                            f"Kill zone covers {kill_zone_arc} deg arc at bearing "
                            f"{round(heading, 1)} out to {kill_zone_depth}m. "
                            f"Do NOT advance into kill zone. "
                            f"Approach from bearing ~{round(safe_approach_bearing, 1)} "
                            f"using smoke and flanking."
                        ),
                    })
        result["ambush_warnings"] = ambush_warnings
        coordinated_ambush = {"detected": False}
        if len(ambush_warnings) >= 2:
            involved = []
            crossfire_pairs = []
            for i in range(len(ambush_warnings)):
                for j in range(i + 1, len(ambush_warnings)):
                    a, b = ambush_warnings[i], ambush_warnings[j]
                    a_pos, b_pos = a["position"], b["position"]
                    cdx = a_pos[0] - b_pos[0]
                    cdz = ((a_pos[2] - b_pos[2]) if len(a_pos) > 2 and len(b_pos) > 2 else 0)
                    separation = math.sqrt(cdx * cdx + cdz * cdz)
                    if separation <= 200:
                        bearing_a = a["kill_zone_bearing"]
                        bearing_b = b["kill_zone_bearing"]
                        angular_diff = abs((bearing_a - bearing_b + 180) % 360 - 180)
                        if angular_diff > 30:
                            crossfire_pairs.append((i, j, angular_diff))
                            if a["player_id"] not in involved:
                                involved.append(a["player_id"])
                            if b["player_id"] not in involved:
                                involved.append(b["player_id"])
            if involved:
                cx_sum, cz_sum, count = 0.0, 0.0, 0
                for w in ambush_warnings:
                    if w["player_id"] in involved:
                        cx_sum += w["position"][0]
                        cz_sum += (w["position"][2] if len(w["position"]) > 2 else 0)
                        count += 1
                cx = round(cx_sum / count, 1)
                cz = round(cz_sum / count, 1)
                max_angular = max(p[2] for p in crossfire_pairs)
                danger = "extreme" if len(involved) >= 3 or max_angular > 90 else "high"
                coordinated_ambush = {
                    "detected": True,
                    "players_involved": involved,
                    "crossfire_zone": f"{cx},0,{cz}",
                    "angular_spread": round(max_angular, 1),
                    "danger_level": danger,
                    "counter": (
                        "Do NOT advance through crossfire. "
                        "Use smoke + vehicle + flanking from outside kill zones. "
                        "Suppress one position while maneuvering around the other."
                    ),
                }
        result["coordinated_ambush"] = coordinated_ambush

        # Engagement zones: areas where units and players are close
        engagement_zones = []
        zone_set = set()
        for unit in enemy_units:
            u_pos = unit.get("position", [0, 0, 0])
            for player in players:
                p_pos = player.get("position", [0, 0, 0])
                dx = u_pos[0] - p_pos[0]
                dz = (u_pos[2] - p_pos[2]) if len(u_pos) > 2 and len(p_pos) > 2 else 0
                dist = math.sqrt(dx * dx + dz * dz)
                if dist < 300:
                    # Midpoint as zone center
                    mid_x = round((u_pos[0] + p_pos[0]) / 2, 1)
                    mid_z = round(((u_pos[2] if len(u_pos) > 2 else 0) +
                                   (p_pos[2] if len(p_pos) > 2 else 0)) / 2, 1)
                    zone_key = f"{round(mid_x, -1)},{round(mid_z, -1)}"
                    if zone_key not in zone_set:
                        zone_set.add(zone_key)
                        engagement_zones.append({
                            "center": f"{mid_x},0,{mid_z}",
                            "radius_m": round(dist / 2, 1),
                            "intensity": "hot" if dist < 100 else "warm" if dist < 200 else "developing",
                        })
        result["engagement_zones"] = engagement_zones[:6]

        return result


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
        # Tactic performance tracking (Priority 3)
        self.tactic_outcomes: dict[str, dict] = {}  # command_type -> {issued, successful, failed}
        self.unit_survival: dict[str, float] = {}    # unit_id -> time alive in seconds
        self.unit_spawn_times: dict[str, float] = {} # unit_id -> spawn timestamp
        self._pending_orders: dict[str, dict] = {}   # unit_id -> {command_type, target_pos, issued_at}

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

        # Register orders for outcome tracking
        for cmd in cmd_list:
            unit_id = cmd.get("unit_id", "")
            cmd_type = cmd.get("command_type", "")
            if unit_id and unit_id != "new" and cmd_type in (
                "move", "attack", "defend", "patrol", "set_ambush",
                "seek_and_destroy", "retreat"
            ):
                self._pending_orders[unit_id] = {
                    "command_type": cmd_type,
                    "target_position": cmd.get("target_position", ""),
                    "issued_at": time.time(),
                }
                if cmd_type not in self.tactic_outcomes:
                    self.tactic_outcomes[cmd_type] = {"issued": 0, "successful": 0, "failed": 0}
                self.tactic_outcomes[cmd_type]["issued"] += 1

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

    def evaluate_outcomes(self, game_state: dict):
        """Compare current game state against pending orders to determine success/failure."""
        enemy_units = {u.get("unit_id"): u for u in game_state.get("enemy_units", [])}

        resolved = []
        for unit_id, order in self._pending_orders.items():
            cmd_type = order["command_type"]

            if cmd_type not in self.tactic_outcomes:
                self.tactic_outcomes[cmd_type] = {"issued": 0, "successful": 0, "failed": 0}

            unit = enemy_units.get(unit_id)

            if not unit:
                # Unit no longer exists — it was destroyed
                self.tactic_outcomes[cmd_type]["failed"] += 1
                resolved.append(unit_id)
                continue

            order_status = unit.get("order_status", "moving")
            health = unit.get("health_pct", 1.0)
            distance = unit.get("distance_to_objective", 999)

            # Evaluate based on command type
            if cmd_type in ("attack", "seek_and_destroy"):
                # Success: unit reached target or got kills
                if unit.get("kills", 0) > 0:
                    self.tactic_outcomes[cmd_type]["successful"] += 1
                    resolved.append(unit_id)
                elif health < 0.3:
                    self.tactic_outcomes[cmd_type]["failed"] += 1
                    resolved.append(unit_id)

            elif cmd_type in ("move", "patrol"):
                # Success: unit arrived at destination
                if order_status == "arrived" or distance < 30:
                    self.tactic_outcomes[cmd_type]["successful"] += 1
                    resolved.append(unit_id)
                elif health < 0.3:
                    self.tactic_outcomes[cmd_type]["failed"] += 1
                    resolved.append(unit_id)

            elif cmd_type in ("defend", "set_ambush"):
                # Success: unit is in position and engaging or has kills
                if order_status == "arrived" and unit.get("kills", 0) > 0:
                    self.tactic_outcomes[cmd_type]["successful"] += 1
                    resolved.append(unit_id)
                elif health < 0.3:
                    self.tactic_outcomes[cmd_type]["failed"] += 1
                    resolved.append(unit_id)

            elif cmd_type == "retreat":
                # Success: unit survived and reached fallback
                if order_status == "arrived":
                    self.tactic_outcomes[cmd_type]["successful"] += 1
                    resolved.append(unit_id)
                elif health <= 0:
                    self.tactic_outcomes[cmd_type]["failed"] += 1
                    resolved.append(unit_id)

        for uid in resolved:
            self._pending_orders.pop(uid, None)

        # Track unit survival times
        import time as _time
        now = _time.time()
        for uid in enemy_units:
            if uid not in self.unit_spawn_times:
                self.unit_spawn_times[uid] = now
            self.unit_survival[uid] = now - self.unit_spawn_times[uid]

        # Clean up dead units
        dead_units = set(self.unit_spawn_times.keys()) - set(enemy_units.keys())
        for uid in dead_units:
            self.unit_survival.pop(uid, None)
            self.unit_spawn_times.pop(uid, None)

    def get_performance_summary(self) -> dict:
        """Generate tactic effectiveness summary for LLM consumption."""
        summary = {}
        most_effective = None
        least_effective = None
        best_rate = -1
        worst_rate = 2

        for tactic, outcomes in self.tactic_outcomes.items():
            issued = outcomes["issued"]
            if issued == 0:
                continue
            success_rate = outcomes["successful"] / issued
            summary[tactic] = {
                "issued": issued,
                "successful": outcomes["successful"],
                "failed": outcomes["failed"],
                "pending": issued - outcomes["successful"] - outcomes["failed"],
                "success_rate": round(success_rate, 2),
            }
            if success_rate > best_rate:
                best_rate = success_rate
                most_effective = tactic
            if success_rate < worst_rate and issued >= 2:
                worst_rate = success_rate
                least_effective = tactic

        # Average unit survival
        survival_times = list(self.unit_survival.values())
        avg_survival = sum(survival_times) / max(len(survival_times), 1)

        return {
            "tactic_outcomes": summary,
            "most_effective_tactic": most_effective,
            "least_effective_tactic": least_effective,
            "avg_unit_survival_seconds": round(avg_survival, 1),
            "total_orders_tracked": sum(o["issued"] for o in self.tactic_outcomes.values()),
            "pending_orders": len(self._pending_orders),
        }

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
        perf = self.get_performance_summary()
        if perf["tactic_outcomes"]:
            parts.append("TACTIC PERFORMANCE:")
            for tactic, data in perf["tactic_outcomes"].items():
                parts.append(f"  - {tactic}: {data['success_rate']:.0%} success "
                             f"({data['successful']}/{data['issued']})")
            if perf["most_effective_tactic"]:
                parts.append(f"  BEST: {perf['most_effective_tactic']}")
            if perf["least_effective_tactic"]:
                parts.append(f"  WORST: {perf['least_effective_tactic']} — CONSIDER ALTERNATIVES")
            parts.append(f"  Avg unit survival: {perf['avg_unit_survival_seconds']:.0f}s")
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
# Cross-Session Learning
# ═══════════════════════════════════════════════════════════

_BATTLE_HISTORY_DIR = os.path.join(os.path.dirname(__file__), "battle_history")
_MAX_PAST_SESSIONS = 5


def _save_battle_summary(session_id: str, summary: str, battle_plan: "BattlePlan"):
    """Persist a battle summary to disk for cross-session learning."""
    os.makedirs(_BATTLE_HISTORY_DIR, exist_ok=True)
    record = {
        "session_id": session_id,
        "timestamp": time.time(),
        "summary": summary,
        "phase": battle_plan.current_phase,
        "turns": battle_plan.turn_count,
        "lessons": battle_plan.lessons_learned,
        "tactic_performance": battle_plan.get_performance_summary(),
    }
    filepath = os.path.join(_BATTLE_HISTORY_DIR, f"{session_id}_{int(time.time())}.json")
    try:
        with open(filepath, "w") as f:
            json.dump(record, f, indent=2, default=str)
        log.info("Saved battle summary: %s", filepath)
    except Exception as e:
        log.warning("Failed to save battle summary: %s", e)


def _load_past_battle_lessons() -> str:
    """Load lessons from previous sessions to inform current battle."""
    if not os.path.isdir(_BATTLE_HISTORY_DIR):
        return ""

    files = []
    for fname in os.listdir(_BATTLE_HISTORY_DIR):
        if fname.endswith(".json"):
            fpath = os.path.join(_BATTLE_HISTORY_DIR, fname)
            files.append((os.path.getmtime(fpath), fpath))

    if not files:
        return ""

    files.sort(reverse=True)
    parts = ["=== LESSONS FROM PAST BATTLES ==="]

    for _, fpath in files[:_MAX_PAST_SESSIONS]:
        try:
            with open(fpath) as f:
                record = json.load(f)
            perf = record.get("tactic_performance", {})
            lessons = record.get("lessons", [])
            best = perf.get("most_effective_tactic", "unknown")
            worst = perf.get("least_effective_tactic", "unknown")
            parts.append(
                f"Session {record.get('session_id','?')} "
                f"({record.get('turns',0)} turns, phase: {record.get('phase','?')}): "
                f"Best tactic: {best}, Worst: {worst}"
            )
            for lesson in lessons[-2:]:
                parts.append(f"  - {lesson}")
        except Exception:
            continue

    return "\n".join(parts) if len(parts) > 1 else ""


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

DATA-DRIVEN DECISIONS:
15. ORDER FEEDBACK: Check order_status for each unit — "stuck" or "engaged" units need new orders
16. TACTIC PERFORMANCE: Review TACTIC PERFORMANCE section — double down on what works, abandon what fails
17. SPATIAL INTEL: Use pre-computed distances and flanking angles — don't estimate, use the numbers
18. PLAYER VECTORS: Velocity and heading predict where players are GOING, not where they ARE
19. TERRAIN: Use terrain_type and cover_density when positioning units — forests for ambush, hills for observation
20. COMBAT STATS: Units with high casualties need reinforcement or retreat. Units with kills are effective — support them

MORALE & FORCE PRESERVATION:
21. Units below 30% health are COMBAT INEFFECTIVE — order retreat immediately
22. Units below 50% with ≤2 men should merge with nearby squads or withdraw
23. Check MORALE section — broken units drag down overall effectiveness
24. Preserve experienced units (high kill count) — they are more valuable than fresh spawns

COUNTER-AMBUSH:
25. Check COUNTER-AMBUSH WARNINGS — never advance directly into a player's kill zone
26. Stationary players with high engagements are likely snipers or ambushers
27. Counter with: smoke screen + flanking, indirect approach, or vehicle rush

PLAYER RESPONSE MATRIX:
28. vs aggressive_assault: Set ambushes 200-300m ahead of their advance. Let them overextend, then cut off retreat.
29. vs flanking_maneuver: Refuse the flank (pull back threatened side), counterattack their weakened center.
30. vs defensive_sniper: Smoke screen + close assault from cover. Or vehicle rush to force displacement.
31. vs cautious_observer: Probe with fire teams to find their position. Pin with MG, flank with squad.
32. vs vehicle_rusher: AT team ambush on road. Mine approaches. Force into kill zone.
33. vs building_clearer: Defend from depth. Booby-trap approaches with ambush squads. Use grenades/explosions.
34. vs overwatch: Approach from dead ground (reverse slope). Smoke their position. Night assault.

MORALE-DRIVEN ORDERS:
35. Check MORALE section EVERY turn. Units flagged for retreat MUST receive retreat orders.
36. Retreat to a position 300-500m behind the front line, in cover.
37. After retreat, either reinforce with fresh spawn or merge remnants.
38. NEVER send broken units (≤30% HP) into attack — they will be destroyed for nothing.
39. Preserve units with kills > 0 — they are combat-proven and more valuable.

CROSS-SESSION ADAPTATION:
40. Review LESSONS FROM PAST BATTLES if present — these are real outcomes from prior sessions.
41. If a tactic failed in past sessions, do NOT repeat it unless circumstances have changed.
42. If a tactic succeeded, consider it your default approach for similar situations.
43. Players may have learned from past sessions too — vary your approach.

ESCALATION & PACING:
44. Escalate gradually — war, not shooting gallery
45. Early: observation, probing patrols
46. Mid: coordinated attacks on objectives
47. Late: combined arms assaults or prepared defenses
48. Players dominating: bring heavy assets gradually (BTR, BMP, T-72)
49. AI dominating: pull back, let players advance into prepared positions

IMMERSION:
50. Radio messages: authentic military jargon, callsigns, urgency
51. Narrative: chaos of battle, confusion, incomplete information
52. Weather changes serve tactical purposes
53. Clean up dead/wrecks periodically for performance

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
    # Queue & retry stats
    "llm_retries": 0,
    "llm_429s": 0,
    "llm_circuit_breaks": 0,
    "llm_coalesced_requests": 0,
    "llm_queue_depth": 0,
    "empty_llm_responses": 0,
    "parse_failures": 0,
}
pending_manual_commands: list[dict] = []
command_log: list[dict] = []
MAX_COMMAND_LOG = 200

# Rate limiter for manual commands (token bucket)
_manual_cmd_tokens = 10.0  # max burst
_manual_cmd_rate = 2.0     # refill per second
_manual_cmd_last = time.time()

_state_lock = asyncio.Lock()

# ─── LLM concurrency gate & request deduplication ───
# Limits concurrent LLM calls to avoid flooding the provider with 429s.
_LLM_MAX_CONCURRENT = int(os.getenv("LLM_MAX_CONCURRENT", "2"))
_llm_semaphore: Optional[asyncio.Semaphore] = None  # lazily created (needs running loop)


def _get_llm_semaphore() -> asyncio.Semaphore:
    global _llm_semaphore
    if _llm_semaphore is None:
        _llm_semaphore = asyncio.Semaphore(_LLM_MAX_CONCURRENT)
    return _llm_semaphore


# Inflight deduplication: if an LLM call is already running for a session,
# subsequent requests wait for the same result instead of making a new call.
_inflight_calls: dict[str, asyncio.Task] = {}

# Last successful LLM response per session — used as fallback when LLM is unavailable.
_last_good_responses: dict[str, dict] = {}

# Consecutive LLM failure count — used for circuit-breaker backoff.
_consecutive_failures: int = 0
_CIRCUIT_BREAKER_THRESHOLD = int(os.getenv("LLM_CIRCUIT_BREAKER_THRESHOLD", "5"))
_circuit_open_until: float = 0.0  # timestamp when circuit should close

def empty_response(reason: str = "") -> dict:
    return {
        "commands": [],
        "narrative_broadcast": "",
        "weather_command": "",
        "time_override": -1,
        "llm_unavailable": bool(reason),
        "llm_error_reason": reason,
    }


def _emergency_fallback_response(game_state: Optional[dict] = None) -> dict:
    """Generate a context-aware fallback when the LLM is completely unavailable.

    Instead of returning an empty command list that leaves AI troops idle,
    this produces a sensible defensive posture so the game continues naturally.
    """
    commands = []
    narrative = ("Command link temporarily disrupted. "
                 "All units hold current positions and maintain defensive posture.")

    if game_state:
        # If we have AI groups, tell them to defend their current positions
        ai_groups = game_state.get("ai_groups", [])
        for group in ai_groups[:8]:  # cap to avoid huge batches
            gid = group.get("group_id", group.get("id", ""))
            if not gid:
                continue
            pos = group.get("position", "0,0,0")
            commands.append({
                "command_type": "defend",
                "unit_id": str(gid),
                "target_position": str(pos),
                "parameters": "alert_level=high",
                "narrative": "Hold position — awaiting restored comms.",
                "reasoning": "LLM unavailable; autonomous defensive posture.",
            })

    return {
        "commands": commands,
        "narrative_broadcast": narrative,
        "weather_command": "",
        "time_override": -1,
        "llm_unavailable": True,
        "llm_error_reason": "LLM service unavailable — using autonomous fallback.",
    }


# ═══════════════════════════════════════════════════════════
# LLM Call
# ═══════════════════════════════════════════════════════════

async def call_llm(
    user_message: str,
    session_id: str = "default",
    tactical_intel: Optional[dict] = None,
    game_state: Optional[dict] = None,
) -> dict:
    global _consecutive_failures, _circuit_open_until

    # ── Circuit breaker: skip LLM entirely if too many recent failures ──
    now = time.time()
    if _consecutive_failures >= _CIRCUIT_BREAKER_THRESHOLD and now < _circuit_open_until:
        remaining = _circuit_open_until - now
        log.warning("Circuit breaker OPEN (%d consecutive failures). "
                     "Skipping LLM for %.0fs. Using cached/emergency fallback.",
                     _consecutive_failures, remaining)
        cached = _last_good_responses.get(session_id)
        if cached:
            log.info("Returning cached last-good response for session %s", session_id)
            return cached
        return _emergency_fallback_response(game_state)

    # ── Request deduplication: coalesce rapid-fire SITREPs for same session ──
    if session_id in _inflight_calls:
        existing_task = _inflight_calls[session_id]
        if not existing_task.done():
            stats["llm_coalesced_requests"] = stats.get("llm_coalesced_requests", 0) + 1
            log.info("Deduplicating LLM call for session %s — waiting on inflight request",
                     session_id)
            try:
                return await existing_task
            except Exception:
                cached = _last_good_responses.get(session_id)
                if cached:
                    return cached
                return _emergency_fallback_response(game_state)

    # Wrap the real work in a shared task so concurrent callers can dedup
    inner_task = asyncio.ensure_future(
        _call_llm_inner(user_message, session_id, tactical_intel, game_state)
    )
    _inflight_calls[session_id] = inner_task
    try:
        return await inner_task
    finally:
        _inflight_calls.pop(session_id, None)


async def _call_llm_inner(
    user_message: str,
    session_id: str,
    tactical_intel: Optional[dict],
    game_state: Optional[dict],
) -> dict:
    """Inner LLM call with concurrency limiting and proper fallback chain."""
    global _consecutive_failures, _circuit_open_until

    history = get_session_history(session_id)
    battle_plan = _get_battle_plan(session_id)

    ctx_parts = []

    # Cross-session learning: inject lessons from past battles
    past_lessons = _load_past_battle_lessons()
    if past_lessons:
        ctx_parts.append(past_lessons)

    memory_ctx = _build_context_summary(history, session_id)
    if memory_ctx:
        ctx_parts.append(memory_ctx)

    if battle_plan.turn_count > 0:
        ctx_parts.append(battle_plan.to_briefing())

    if tactical_intel:
        ctx_parts.append(_format_tactical_intel(tactical_intel))

    full_msg = "\n\n".join(ctx_parts) + "\n\n" + user_message if ctx_parts else user_message

    # ── Acquire concurrency semaphore to limit parallel LLM calls ──
    sem = _get_llm_semaphore()
    acquired = False
    try:
        try:
            await asyncio.wait_for(sem.acquire(), timeout=10.0)
            acquired = True
        except asyncio.TimeoutError:
            log.warning("LLM semaphore timeout (all %d slots busy). "
                         "Returning cached/emergency fallback.", _LLM_MAX_CONCURRENT)
            cached = _last_good_responses.get(session_id)
            if cached:
                return cached
            return _emergency_fallback_response(game_state)

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
        response = await _invoke_llm_with_retry(llm, formatted.to_messages(),
                                                       caller_label="call_llm")

        if hasattr(response, "usage_metadata") and response.usage_metadata:
            async with _state_lock:
                stats["total_input_tokens"] += response.usage_metadata.get("input_tokens", 0)
                stats["total_output_tokens"] += response.usage_metadata.get("output_tokens", 0)

        # Try structured parser first, then fall back to manual extraction
        text = response.content if hasattr(response, "content") else str(response)
        result = None

        try:
            parser = JsonOutputParser(pydantic_object=CommandBatch)
            result = await parser.ainvoke(response)
        except Exception as parse_err:
            log.warning("JsonOutputParser failed, attempting manual extraction: %s", parse_err)
            result = parse_llm_response(text)

        if not result or not result.get("commands"):
            log.info("Parser produced no commands, trying deeper extraction from %d chars", len(text))
            result = parse_llm_response(text)

        # Minimum-response guarantee: if all parsing still yields 0 commands,
        # generate defensive hold commands so units are never left idle
        if not result or not result.get("commands"):
            stats["empty_llm_responses"] = stats.get("empty_llm_responses", 0) + 1
            log.warning("LLM returned 0 commands after all parse attempts — "
                        "generating emergency defensive hold")
            result = _emergency_fallback_response(game_state)

        history.add_message(HumanMessage(content=user_message))
        history.add_message(AIMessage(content=json.dumps(result, default=str)))

        validated = _validate_command_batch(result)
        if game_state:
            battle_plan.update_from_commands(validated, game_state)

        await _maybe_compact_memory(session_id)

        # Success: reset circuit breaker, cache good response
        _consecutive_failures = 0
        _last_good_responses[session_id] = validated
        return validated

    except Exception as e:
        log.warning("Chain failed (type=%s), raw fallback: %s", type(e).__name__, e)
        fallback_result = await _fallback_call(full_msg, history, game_state=game_state)

        if fallback_result.get("commands"):
            # Fallback succeeded — reset circuit breaker
            _consecutive_failures = 0
            _last_good_responses[session_id] = fallback_result
        else:
            # Both primary and fallback failed — advance circuit breaker
            _consecutive_failures += 1
            if _consecutive_failures >= _CIRCUIT_BREAKER_THRESHOLD:
                # Open circuit for exponential backoff: 10s, 20s, 40s, ...
                backoff = min(10 * (2 ** (_consecutive_failures - _CIRCUIT_BREAKER_THRESHOLD)),
                              300)
                _circuit_open_until = time.time() + backoff
                stats["llm_circuit_breaks"] = stats.get("llm_circuit_breaks", 0) + 1
                log.warning("Circuit breaker OPENED: %d consecutive failures. "
                            "Cooling off for %ds.", _consecutive_failures, backoff)

        return fallback_result
    finally:
        if acquired:
            sem.release()


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

    # Morale assessment
    morale = intel.get("morale", {})
    if morale.get("warnings"):
        parts.append(f"MORALE: {morale.get('overall', '?').upper()}")
        for w in morale["warnings"]:
            parts.append(f"  !! {w['unit_id']}: {w['health_pct']:.0%} HP, {w['strength']} men — {w['recommendation']}")

    # Spatial relationships (Priority 2)
    spatial = intel.get("spatial", {})
    if spatial:
        nearest = spatial.get("nearest_enemy_to_each_player", {})
        if nearest:
            parts.append("SPATIAL INTELLIGENCE:")
            for pid, info in nearest.items():
                parts.append(f"  - Player {pid}: nearest enemy {info['unit']} at {info['distance_m']}m ({info['engagement_range']})")

        flanks = spatial.get("flanking_opportunities", [])
        if flanks:
            parts.append(f"FLANKING OPPORTUNITIES ({len(flanks)}):")
            for f in flanks[:4]:
                parts.append(f"  - {f['unit']} can flank Player {f['target_player']} "
                           f"({f['flank_quality']}, {f['distance_m']}m, bearing {f['approach_angle']}\u00b0)")

        hidden = spatial.get("units_beyond_player_awareness", [])
        if hidden:
            parts.append(f"CONCEALED RESERVES: {', '.join(hidden)} (undetected by players)")

        clustering = spatial.get("player_clustering", {})
        if clustering:
            parts.append(f"PLAYER FORMATION: {clustering.get('formation', '?')} "
                       f"(spread: {clustering.get('avg_spread_m', 0):.0f}m)")
            parts.append(f"  \u2192 {clustering.get('tactical_note', '')}")

        zones = spatial.get("engagement_zones", [])
        if zones:
            parts.append(f"ACTIVE ENGAGEMENT ZONES ({len(zones)}):")
            for z in zones[:4]:
                parts.append(f"  - {z['center']} ({z['intensity']}, radius {z['radius_m']}m)")

        ambushes = spatial.get("ambush_warnings", [])
        if ambushes:
            parts.append(f"!! COUNTER-AMBUSH WARNINGS ({len(ambushes)}):")
            for a in ambushes:
                parts.append(f"  - Player {a['player_id']} at {a['position']}: {a['threat']}")

    # Tactic performance (Priority 3)
    perf = intel.get("tactic_performance", {})
    if perf and perf.get("tactic_outcomes"):
        parts.append("TACTIC EFFECTIVENESS:")
        for tactic, data in perf["tactic_outcomes"].items():
            parts.append(f"  - {tactic}: {data['success_rate']:.0%} success ({data['successful']}/{data['issued']})")
        if perf.get("most_effective_tactic"):
            parts.append(f"  \u2605 BEST: {perf['most_effective_tactic']}")
        if perf.get("least_effective_tactic"):
            parts.append(f"  \u2717 WORST: {perf['least_effective_tactic']} \u2014 CHANGE APPROACH")

    # Player movement vectors (Priority 4)
    player_vectors = intel.get("player_vectors", {})
    if player_vectors:
        parts.append("PLAYER MOVEMENT VECTORS:")
        for pid, vec in player_vectors.items():
            parts.append(f"  - Player {pid}: heading {vec.get('heading', 0):.0f}\u00b0 "
                       f"speed {vec.get('speed_mps', 0):.1f}m/s "
                       f"\u2192 {vec.get('predicted_direction', 'unknown')}")

    # Terrain context (Priority 5)
    terrain = intel.get("terrain_summary", {})
    if terrain:
        parts.append("TERRAIN INTELLIGENCE:")
        for zone_type, data in terrain.items():
            parts.append(f"  - {zone_type}: {data.get('count', 0)} areas, "
                       f"avg cover {data.get('avg_cover', 0):.0%}")

    return "\n".join(parts)


async def _fallback_call(user_message: str, history: InMemoryChatMessageHistory,
                         game_state: Optional[dict] = None) -> dict:
    try:
        llm = _get_llm()
        messages = [SystemMessage(content=_SYSTEM_PROMPT_RAW)]
        messages.extend(history.messages[-6:])
        messages.append(HumanMessage(content=user_message))

        response = await _invoke_llm_with_retry(llm, messages,
                                                       caller_label="fallback_call")
        text = response.content if hasattr(response, "content") else str(response)

        if hasattr(response, "usage_metadata") and response.usage_metadata:
            async with _state_lock:
                stats["total_input_tokens"] += response.usage_metadata.get("input_tokens", 0)
                stats["total_output_tokens"] += response.usage_metadata.get("output_tokens", 0)

        history.add_message(HumanMessage(content=user_message))
        history.add_message(AIMessage(content=text))
        _trim_history(history)
        result = parse_llm_response(text)

        # If fallback parse also yields 0 commands, use emergency fallback
        if not result.get("commands"):
            stats["empty_llm_responses"] = stats.get("empty_llm_responses", 0) + 1
            log.warning("Fallback call returned 0 commands. Raw LLM text (%d chars): %.500s",
                        len(text), text)
            return _emergency_fallback_response(game_state)

        return result

    except Exception as e:
        log.error("Fallback LLM call also failed after retries: %s: %s", type(e).__name__, e)
        return _emergency_fallback_response(game_state)


def _validate_command_batch(data: dict) -> dict:
    if not isinstance(data, dict):
        return empty_response()
    data.setdefault("commands", [])
    data.setdefault("narrative_broadcast", "")
    data.setdefault("weather_command", "")
    data.setdefault("time_override", -1)
    valid = []
    dropped = 0
    for cmd in data["commands"]:
        if not isinstance(cmd, dict):
            log.warning("Dropped non-dict command: %s", type(cmd).__name__)
            dropped += 1
            continue
        if "command_type" not in cmd:
            log.warning("Dropped command missing command_type: %s",
                        json.dumps(cmd, default=str)[:200])
            dropped += 1
            continue
        cmd.setdefault("unit_id", "")
        cmd.setdefault("target_position", "0,0,0")
        cmd.setdefault("parameters", "")
        cmd.setdefault("narrative", "")
        cmd.setdefault("reasoning", "")
        # Ensure spawn commands have a non-empty parameters field (vehicle/unit type)
        ct = cmd["command_type"]
        if ct == "spawn_vehicle" and not cmd["parameters"].strip():
            log.warning("spawn_vehicle command has empty parameters, defaulting to 'uaz469'")
            cmd["parameters"] = "uaz469"
        elif ct == "spawn_infantry" and not cmd["parameters"].strip():
            log.warning("spawn_infantry command has empty parameters, defaulting to 'ussr_rifle_squad'")
            cmd["parameters"] = "ussr_rifle_squad"
        valid.append(cmd)
    if dropped:
        log.warning("Dropped %d/%d malformed commands from batch",
                    dropped, dropped + len(valid))
    data["commands"] = valid
    return data


def _build_compact_sitrep(game_state: dict) -> str:
    """Build a token-efficient situation report from game_state.

    Only includes operational data the LLM needs for issuing orders that is NOT
    already covered by the tactical intel (force ratios, threat assessment,
    player profiles, readiness, morale, spatial, etc. are all in tactical intel).
    """
    lines = []

    # --- Mission context (one line) ---
    objective = game_state.get("objective", game_state.get("mission_name", ""))
    mission_time = game_state.get("mission_time_seconds", 0)
    weather = game_state.get("current_weather", "Clear")
    tod = game_state.get("time_of_day", 12.0)
    lines.append(
        f"OBJ:{objective} T:{mission_time:.0f}s WX:{weather} ToD:{tod:.1f}h"
    )

    # --- Friendly / enemy unit roster (one line per unit) ---
    enemy_units = game_state.get("enemy_units", [])
    if enemy_units:
        lines.append("UNITS:")
        for u in enemy_units:
            uid = u.get("unit_id", "?")
            pos = u.get("position", "?")
            hp = u.get("health_pct", 1.0)
            strength = u.get("strength", 0)
            order = u.get("current_order", u.get("order", ""))
            stance = u.get("stance", "")
            parts = [f"  {uid} pos:{pos} hp:{hp:.0%} str:{strength}"]
            if order:
                parts.append(f"ord:{order}")
            if stance:
                parts.append(f"stance:{stance}")
            lines.append(" ".join(parts))

    ai_groups = game_state.get("ai_groups", [])
    if ai_groups:
        lines.append("AI_GROUPS:")
        for g in ai_groups:
            gid = g.get("group_id", g.get("id", "?"))
            pos = g.get("position", "?")
            order = g.get("current_order", g.get("order", ""))
            lines.append(f"  {gid} pos:{pos} ord:{order}")

    # --- Player positions and states (one line each) ---
    players = game_state.get("players", [])
    if players:
        lines.append("PLAYERS:")
        for p in players:
            pid = p.get("player_id", p.get("id", "?"))
            pos = p.get("position", "?")
            hp = p.get("health", p.get("health_pct", 1.0))
            weapon = p.get("weapon", "")
            vehicle = p.get("vehicle", "")
            parts = [f"  P{pid} pos:{pos} hp:{hp}"]
            if weapon:
                parts.append(f"wpn:{weapon}")
            if vehicle:
                parts.append(f"veh:{vehicle}")
            lines.append(" ".join(parts))

    # --- Recent events (just the event strings) ---
    events = game_state.get("recent_events", [])
    if events:
        lines.append("EVENTS:")
        for ev in events:
            if isinstance(ev, dict):
                desc = ev.get("description", ev.get("event",
                       json.dumps(ev, separators=(',', ':'))))
                lines.append(f"  - {desc}")
            else:
                lines.append(f"  - {ev}")

    return "\n".join(lines)


def _strip_markdown_fences(text: str) -> str:
    """Remove markdown code fences (```json ... ```) wrapping JSON content."""
    text = text.strip()
    text = re.sub(r"^```[a-zA-Z]*\s*\n?", "", text)
    text = re.sub(r"\n?```\s*$", "", text)
    return text.strip()


def _fix_common_json_issues(text: str) -> str:
    """Fix common JSON issues that LLMs produce."""
    text = re.sub(r",\s*([}\]])", r"\1", text)
    text = re.sub(r'(?<!["\w])//[^\n]*', "", text)
    if '"' not in text and "'" in text:
        text = text.replace("'", '"')
    return text


def _extract_json_candidates(text: str) -> list:
    """Extract all possible JSON object substrings from text, longest first."""
    candidates = []
    i = 0
    while i < len(text):
        if text[i] == "{":
            depth = 0
            in_string = False
            escape_next = False
            start = i
            closed = False
            for j in range(i, len(text)):
                ch = text[j]
                if escape_next:
                    escape_next = False
                    continue
                if ch == "\\":
                    escape_next = True
                    continue
                if ch == '"' and not escape_next:
                    in_string = not in_string
                    continue
                if in_string:
                    continue
                if ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        candidates.append(text[start:j + 1])
                        i = j
                        closed = True
                        break
            if not closed:
                fragment = text[start:]
                open_b = fragment.count("[") - fragment.count("]")
                open_c = fragment.count("{") - fragment.count("}")
                if open_b > 0:
                    fragment += "]" * open_b
                if open_c > 0:
                    fragment += "}" * open_c
                candidates.append(fragment)
        i += 1
    candidates.sort(key=len, reverse=True)
    return candidates


def _extract_commands_from_text(text: str) -> dict:
    """Last-resort: regex-extract individual command objects from free text."""
    result = empty_response()
    commands = []
    command_types = [
        "move", "attack", "defend", "patrol", "retreat", "flank",
        "spawn_group", "heal_unit", "resupply", "set_stance",
        "set_formation", "set_behavior", "garrison", "ambush",
        "suppress", "overwatch", "reinforce", "hold_fire",
        "cleanup_dead", "despawn_unit", "radio_message",
        "objective_create", "objective_complete",
    ]
    for ct in command_types:
        pattern = rf'["\']?command_type["\']?\s*[:=]\s*["\']({ct})["\']'
        for match in re.finditer(pattern, text, re.IGNORECASE):
            pos = match.start()
            brace_start = text.rfind("{", max(0, pos - 200), pos)
            if brace_start == -1:
                continue
            depth = 0
            end = brace_start
            for k in range(brace_start, min(len(text), brace_start + 1000)):
                if text[k] == "{":
                    depth += 1
                elif text[k] == "}":
                    depth -= 1
                    if depth == 0:
                        end = k + 1
                        break
            snippet = _fix_common_json_issues(text[brace_start:end])
            try:
                cmd = json.loads(snippet)
                if isinstance(cmd, dict) and "command_type" in cmd:
                    commands.append(cmd)
            except json.JSONDecodeError:
                pass

    if commands:
        log.info("Text extraction recovered %d commands from non-JSON response", len(commands))
        result["commands"] = commands

    narr_match = re.search(
        r'["\']?narrative_broadcast["\']?\s*[:=]\s*["\']([^"\']+)["\']',
        text, re.IGNORECASE,
    )
    if narr_match:
        result["narrative_broadcast"] = narr_match.group(1)

    return result


def parse_llm_response(text: str) -> dict:
    """Parse LLM response text into a command batch dict.

    Applies multiple extraction strategies in order of preference:
    1. Strip markdown fences and parse clean JSON
    2. Extract JSON object candidates from mixed text
    3. Parse bare JSON arrays of commands
    4. Last resort: regex-extract individual commands from free text
    """
    if not text or not text.strip():
        log.warning("parse_llm_response called with empty text")
        return empty_response()

    original_text = text

    # Strategy 1: Strip fences and try direct parse
    text = _strip_markdown_fences(text)
    text_fixed = _fix_common_json_issues(text)
    try:
        return _validate_command_batch(json.loads(text_fixed))
    except json.JSONDecodeError:
        pass

    # Strategy 2: Extract JSON object candidates from the text
    candidates = _extract_json_candidates(text_fixed)
    for candidate in candidates:
        candidate = _fix_common_json_issues(candidate)
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, dict):
                if "commands" in parsed or "command_type" in parsed:
                    if "command_type" in parsed and "commands" not in parsed:
                        parsed = {"commands": [parsed]}
                    log.info("Extracted valid JSON from candidate (%d chars)", len(candidate))
                    return _validate_command_batch(parsed)
        except json.JSONDecodeError:
            continue

    # Strategy 3: Maybe it is a bare JSON array of commands (no wrapper object)
    arr_match = re.search(r"\[[\s\S]*\]", text_fixed)
    if arr_match:
        try:
            arr = json.loads(_fix_common_json_issues(arr_match.group()))
            if isinstance(arr, list) and arr and isinstance(arr[0], dict):
                log.info("Parsed response as bare JSON array of %d commands", len(arr))
                return _validate_command_batch({"commands": arr})
        except json.JSONDecodeError:
            pass

    # Strategy 4: Last resort -- regex extraction of individual commands
    extracted = _extract_commands_from_text(original_text)
    if extracted.get("commands"):
        return _validate_command_batch(extracted)

    log.warning("All JSON parse strategies failed for response (%d chars): %.300s",
                len(original_text), original_text)
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
    log.info("Retry: max=%d base=%.1fs cap=%.0fs jitter=%.0f%% | Rate: %d RPM | Max-concurrent: %d",
             LLM_MAX_RETRIES, LLM_RETRY_BASE_DELAY, LLM_RETRY_MAX_DELAY,
             LLM_RETRY_JITTER * 100, LLM_RATE_LIMIT_RPM, _LLM_MAX_CONCURRENT)
    log.info("Circuit breaker: threshold=%d failures", _CIRCUIT_BREAKER_THRESHOLD)
    log.info("Features: compaction, tactical-intel, battle-plan, adaptive-difficulty")
    log.info("=" * 56)
    yield
    # Save battle summaries on shutdown for cross-session learning
    for sid, bp in _battle_plans.items():
        if bp.turn_count > 0:
            summary = _compacted_summaries.get(sid, "")
            if not summary:
                summary = bp.to_briefing()
            _save_battle_summary(sid, summary, bp)
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

    # Evaluate tactic outcomes from previous orders (Priority 3)
    session_id = game_state.get("mission_id", _active_session)
    battle_plan = _get_battle_plan(session_id)
    battle_plan.evaluate_outcomes(game_state)
    tactical_intel["tactic_performance"] = battle_plan.get_performance_summary()

    compact_sitrep = _build_compact_sitrep(game_state)
    user_message = (
        f"CURRENT SITREP (Mission time: {mission_time:.0f}s, Players: {player_count}):\n"
        f"{compact_sitrep}\n\n"
        "Issue your orders. Respond with ONLY valid JSON, no markdown fences."
    )

    try:
        _llm_call_start = time.time()
        commands = await call_llm(user_message, session_id=session_id,
                                  tactical_intel=tactical_intel, game_state=game_state)
        _llm_response_time_ms = round((time.time() - _llm_call_start) * 1000)

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
                "response_time_ms": _llm_response_time_ms,
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
        log.error("game_master endpoint error: %s: %s", type(e).__name__, e)
        # Even at this outermost layer, return a playable response.
        # Try returning a cached good response first, then emergency fallback.
        cached = _last_good_responses.get(session_id)
        if cached:
            log.info("Returning cached last-good response for session %s "
                     "after outer exception", session_id)
            return cached
        return _emergency_fallback_response(game_state)


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
        "circuit_breaker_failures": _consecutive_failures,
        "circuit_breaker_open": time.time() < _circuit_open_until,
        "circuit_breaker_open_until": _circuit_open_until if _circuit_open_until > 0 else None,
        "cached_sessions": len(_last_good_responses),
        "inflight_calls": len(_inflight_calls),
        "llm_max_concurrent": _LLM_MAX_CONCURRENT,
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
    # Live queue/circuit info
    sem = _get_llm_semaphore()
    inflight_count = len([t for t in _inflight_calls.values() if not t.done()])
    circuit_status = "closed"
    if _consecutive_failures >= _CIRCUIT_BREAKER_THRESHOLD:
        circuit_status = "open" if time.time() < _circuit_open_until else "half-open"

    # Gather last 10 response times from command_log for sparkline
    recent_response_times = [
        entry["response_time_ms"]
        for entry in command_log[-10:]
        if entry.get("response_time_ms") is not None
    ]

    return {
        **stats, "uptime_seconds": round(up, 1),
        "avg_cmds": round(stats["total_commands_issued"] / max(stats["total_requests"], 1), 1),
        "error_rate": round(stats["total_errors"] / max(stats["total_requests"], 1) * 100, 1),
        "history_size": len(game_history),
        "difficulty": round(_tactical_analyzer.difficulty_modifier, 2),
        "player_kills": _tactical_analyzer.player_kills,
        "ai_kills": _tactical_analyzer.ai_kills,
        "sessions": sessions,
        "tactic_performance": {
            sid: bp.get_performance_summary()
            for sid, bp in _battle_plans.items()
        },
        # Queue & retry info
        "llm_inflight": inflight_count,
        "llm_max_concurrent": _LLM_MAX_CONCURRENT,
        "llm_consecutive_failures": _consecutive_failures,
        "llm_circuit_status": circuit_status,
        "recent_response_times_ms": recent_response_times,
        # LLM reliability metrics
        "empty_response_rate": round(
            stats.get("empty_llm_responses", 0) / max(stats["total_requests"], 1) * 100, 1
        ),
        "parse_failure_rate": round(
            stats.get("parse_failures", 0) / max(stats["total_requests"], 1) * 100, 1
        ),
    }


@app.post("/api/reset")
async def reset():
    # Save battle summaries before clearing
    for sid, bp in _battle_plans.items():
        if bp.turn_count > 0:
            summary = _compacted_summaries.get(sid, "")
            if not summary:
                summary = bp.to_briefing()
            _save_battle_summary(sid, summary, bp)

    async with _state_lock:
        game_history.clear()
        command_log.clear()
    _session_histories.clear()
    _battle_plans.clear()
    _compacted_summaries.clear()
    _last_good_responses.clear()
    _inflight_calls.clear()
    global _consecutive_failures, _circuit_open_until
    _consecutive_failures = 0
    _circuit_open_until = 0.0
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
    global _manual_cmd_tokens, _manual_cmd_last
    # Token bucket rate limiting
    now = time.time()
    _manual_cmd_tokens = min(10.0, _manual_cmd_tokens + (now - _manual_cmd_last) * _manual_cmd_rate)
    _manual_cmd_last = now
    if _manual_cmd_tokens < 1.0:
        raise HTTPException(status_code=429, detail="Rate limit exceeded. Max 2 commands/sec, burst 10.")
    _manual_cmd_tokens -= 1.0

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
