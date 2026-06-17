"""
Multi-Agent Tactical System for GM Server.

10 specialized agents that work as a pipeline:
  Phase 1 (parallel, code-based): Intelligence, Threat, Difficulty, ForcePreservation, Environment, Logistics
  Phase 2 (parallel, LLM-based): StrategicCommander, TacticalPlanner, NarrativeDirector
  Phase 3 (async, post-cycle): LearningEngine

Each agent has a focused role, produces typed outputs, and feeds into the next phase.
"""

import asyncio
import json
import logging
import math
import random
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

log = logging.getLogger("godmode.agents")

SUPPORTED_OPFOR_INFANTRY_TYPES = {
    "ussr_rifle_squad",
    "ussr_fire_team",
    "ussr_mg_team",
    "ussr_at_team",
    "ussr_sniper_team",
}


# ═══════════════════════════════════════════════════════════
# Agent Data Types
# ═══════════════════════════════════════════════════════════

@dataclass
class AgentResult:
    """Standard output from any agent."""
    agent_name: str
    commands: list = field(default_factory=list)
    lessons: list = field(default_factory=list)
    intel_updates: dict = field(default_factory=dict)
    narrative: list = field(default_factory=list)
    metadata: dict = field(default_factory=dict)


@dataclass
class AgentContext:
    """Shared context passed between agents. Accumulates results."""
    game_state: dict
    tactical_intel: dict
    battle_plan: object  # BattlePlan instance
    difficulty_params: dict
    session_id: str
    turn_number: int
    agent_results: dict = field(default_factory=dict)  # agent_name -> AgentResult
    enriched_intel: dict = field(default_factory=dict)  # accumulated intel from all agents


class AgentPhase(Enum):
    PRE_PROCESS = "pre_process"
    DECISION = "decision"
    POST_PROCESS = "post_process"


# ═══════════════════════════════════════════════════════════
# Base Agent
# ═══════════════════════════════════════════════════════════

class BaseAgent:
    """Base class for all tactical agents."""

    name: str = "base"
    role: str = "Base agent"
    phase: AgentPhase = AgentPhase.PRE_PROCESS
    requires_llm: bool = False

    async def process(self, ctx: AgentContext) -> AgentResult:
        raise NotImplementedError

    def _empty_result(self) -> AgentResult:
        return AgentResult(agent_name=self.name)


# ═══════════════════════════════════════════════════════════
# Agent 1: Intelligence Agent (code-based)
# ═══════════════════════════════════════════════════════════

class IntelligenceAgent(BaseAgent):
    """Enhanced player profiling with pattern memory and prediction."""

    name = "intelligence"
    role = "Player behavior analysis, movement corridors, engagement patterns"
    phase = AgentPhase.PRE_PROCESS

    def __init__(self):
        # Long-term tracking across turns
        self.player_movement_corridors: dict[int, list] = defaultdict(list)  # pid -> [direction_vectors]
        self.player_engagement_ranges: dict[int, list] = defaultdict(list)   # pid -> [distances]
        self.player_reaction_times: dict[int, list] = defaultdict(list)      # pid -> [seconds to react]
        self.player_kill_positions: dict[int, list] = defaultdict(list)      # pid -> [positions where they got kills]
        self.player_death_positions: list = []                                # positions where AI units die
        self.player_preferred_terrain: dict[int, dict] = defaultdict(lambda: defaultdict(int))
        self.player_activity_windows: dict[int, list] = defaultdict(list)    # pid -> [(start, end)] active periods
        self.team_coordination_score: float = 0.0
        self._last_player_positions: dict[int, list] = {}
        self._turn_count = 0

    async def process(self, ctx: AgentContext) -> AgentResult:
        result = self._empty_result()
        self._turn_count += 1
        players = ctx.game_state.get("players", [])
        enemy_units = ctx.game_state.get("enemy_units", [])

        # Track movement corridors
        for player in players:
            pid = player.get("id", player.get("player_id", 0))
            pos = player.get("position", [0, 0, 0])
            if pid in self._last_player_positions:
                last = self._last_player_positions[pid]
                dx = pos[0] - last[0]
                dz = (pos[2] - last[2]) if len(pos) > 2 and len(last) > 2 else 0
                dist = math.sqrt(dx * dx + dz * dz)
                if dist > 5:  # meaningful movement
                    bearing = math.degrees(math.atan2(dx, dz)) % 360
                    self.player_movement_corridors[pid].append({
                        "bearing": bearing, "distance": dist,
                        "from": last, "to": pos, "turn": self._turn_count
                    })
                    # Keep last 20 movements
                    if len(self.player_movement_corridors[pid]) > 20:
                        self.player_movement_corridors[pid].pop(0)
            self._last_player_positions[pid] = pos

            # Track terrain preferences
            terrain = player.get("terrain_type", "unknown")
            self.player_preferred_terrain[pid][terrain] += 1

        # Track engagement ranges from unit data
        for unit in enemy_units:
            if unit.get("is_engaging"):
                u_pos = unit.get("position", [0, 0, 0])
                for player in players:
                    p_pos = player.get("position", [0, 0, 0])
                    dx = u_pos[0] - p_pos[0]
                    dz = (u_pos[2] - p_pos[2]) if len(u_pos) > 2 and len(p_pos) > 2 else 0
                    dist = math.sqrt(dx * dx + dz * dz)
                    if dist < 500:
                        pid = player.get("id", player.get("player_id", 0))
                        self.player_engagement_ranges[pid].append(dist)
                        if len(self.player_engagement_ranges[pid]) > 50:
                            self.player_engagement_ranges[pid].pop(0)

        # Track where AI units die (death zones)
        dead_units = ctx.game_state.get("destroyed_units", [])
        for uid_data in dead_units:
            pos = uid_data if isinstance(uid_data, list) else uid_data.get("position", [0, 0, 0])
            self.player_death_positions.append({"position": pos, "turn": self._turn_count})
        if len(self.player_death_positions) > 100:
            self.player_death_positions = self.player_death_positions[-100:]

        # Compute team coordination score
        if len(players) >= 2:
            self._compute_team_coordination(players)

        # Build enriched intel
        enriched = {}

        # Predict player destinations from movement corridors
        predictions = {}
        for pid, corridors in self.player_movement_corridors.items():
            if len(corridors) >= 3:
                recent = corridors[-3:]
                avg_bearing = sum(c["bearing"] for c in recent) / len(recent)
                avg_speed = sum(c["distance"] for c in recent) / len(recent)
                last_pos = recent[-1]["to"]
                # Project forward 60 seconds at current speed/bearing
                rad = math.radians(avg_bearing)
                pred_x = last_pos[0] + avg_speed * 2 * math.sin(rad)
                pred_z = (last_pos[2] if len(last_pos) > 2 else 0) + avg_speed * 2 * math.cos(rad)
                predictions[pid] = {
                    "predicted_position": f"{pred_x:.0f},0,{pred_z:.0f}",
                    "bearing": round(avg_bearing, 1),
                    "speed": round(avg_speed, 1),
                    "confidence": min(len(corridors) / 10, 1.0),
                }
        enriched["player_predictions"] = predictions

        # Identify death zones (clusters where AI dies)
        death_zones = self._cluster_death_zones()
        enriched["death_zones"] = death_zones

        # Average engagement ranges per player
        engagement_profiles = {}
        for pid, ranges in self.player_engagement_ranges.items():
            if ranges:
                engagement_profiles[pid] = {
                    "avg_range": round(sum(ranges) / len(ranges), 1),
                    "min_range": round(min(ranges), 1),
                    "max_range": round(max(ranges), 1),
                    "preferred_range": round(sorted(ranges)[len(ranges) // 2], 1),
                    "samples": len(ranges),
                }
        enriched["engagement_profiles"] = engagement_profiles

        # Team coordination
        enriched["team_coordination"] = {
            "score": round(self.team_coordination_score, 2),
            "assessment": (
                "highly_coordinated" if self.team_coordination_score > 0.7
                else "loosely_coordinated" if self.team_coordination_score > 0.4
                else "independent"
            ),
        }

        # Terrain preference summary
        terrain_prefs = {}
        for pid, terrains in self.player_preferred_terrain.items():
            if terrains:
                total = sum(terrains.values())
                fav = max(terrains, key=terrains.get)
                terrain_prefs[pid] = {
                    "favorite_terrain": fav,
                    "time_pct": round(terrains[fav] / total * 100, 1),
                }
        enriched["terrain_preferences"] = terrain_prefs

        result.intel_updates = enriched
        return result

    def _compute_team_coordination(self, players):
        """Score how coordinated the players are (0-1)."""
        positions = [p.get("position", [0, 0, 0]) for p in players]
        if len(positions) < 2:
            return

        # Factor 1: Distance between players (closer = more coordinated)
        total_dist = 0
        pairs = 0
        for i in range(len(positions)):
            for j in range(i + 1, len(positions)):
                dx = positions[i][0] - positions[j][0]
                dz = ((positions[i][2] - positions[j][2])
                      if len(positions[i]) > 2 and len(positions[j]) > 2 else 0)
                total_dist += math.sqrt(dx * dx + dz * dz)
                pairs += 1
        avg_dist = total_dist / max(pairs, 1)
        dist_score = max(0, 1 - avg_dist / 500)  # 0-500m range

        # Factor 2: Similar movement direction
        bearings = []
        for p in players:
            pid = p.get("id", p.get("player_id", 0))
            corrs = self.player_movement_corridors.get(pid, [])
            if corrs:
                bearings.append(corrs[-1]["bearing"])
        if len(bearings) >= 2:
            bearing_spread = max(bearings) - min(bearings)
            if bearing_spread > 180:
                bearing_spread = 360 - bearing_spread
            direction_score = max(0, 1 - bearing_spread / 90)
        else:
            direction_score = 0.5

        self.team_coordination_score = 0.6 * dist_score + 0.4 * direction_score

    def _cluster_death_zones(self) -> list:
        """Cluster AI death positions into danger zones."""
        if len(self.player_death_positions) < 3:
            return []

        recent = [d for d in self.player_death_positions if self._turn_count - d["turn"] <= 10]
        if len(recent) < 2:
            return []

        # Simple grid-based clustering
        grid = defaultdict(list)
        for d in recent:
            pos = d["position"]
            if isinstance(pos, list) and len(pos) >= 2:
                gx = int(pos[0] // 100) * 100
                gz = int((pos[2] if len(pos) > 2 else 0) // 100) * 100
                grid[(gx, gz)].append(pos)

        zones = []
        for (gx, gz), positions in grid.items():
            if len(positions) >= 2:
                cx = sum(p[0] for p in positions) / len(positions)
                cz = sum((p[2] if len(p) > 2 else 0) for p in positions) / len(positions)
                zones.append({
                    "center": f"{cx:.0f},0,{cz:.0f}",
                    "deaths": len(positions),
                    "danger": "extreme" if len(positions) >= 4 else "high",
                    "warning": f"AI lost {len(positions)} units in this area recently - AVOID or use smoke/suppression",
                })
        return zones[:5]


# ═══════════════════════════════════════════════════════════
# Agent 2: Threat Assessment Agent (code-based)
# ═══════════════════════════════════════════════════════════

class ThreatAssessmentAgent(BaseAgent):
    """Spatial threat modeling and engagement probability mapping."""

    name = "threat_assessment"
    role = "Threat scoring, engagement probability, safe route planning"
    phase = AgentPhase.PRE_PROCESS

    def __init__(self):
        self.threat_history: deque = deque(maxlen=50)
        self._engagement_outcomes: list = []

    async def process(self, ctx: AgentContext) -> AgentResult:
        result = self._empty_result()
        players = ctx.game_state.get("players", [])
        enemy_units = ctx.game_state.get("enemy_units", [])

        # Build threat heat map (grid cells with danger scores)
        threat_map = self._build_threat_map(players, ctx)

        # Identify safe approach routes to objectives
        safe_routes = self._plan_safe_routes(players, enemy_units, threat_map)

        # Predict engagement outcomes for potential maneuvers
        engagement_forecasts = self._forecast_engagements(enemy_units, players, ctx)

        # Compute force balance per sector
        sector_analysis = self._analyze_sectors(enemy_units, players)

        result.intel_updates = {
            "threat_map": threat_map[:10],  # top 10 threat cells
            "safe_routes": safe_routes[:5],
            "engagement_forecasts": engagement_forecasts,
            "sector_analysis": sector_analysis,
        }
        return result

    def _build_threat_map(self, players, ctx) -> list:
        """Grid-based threat heat map from player positions and capabilities."""
        threat_cells = []
        intel_agent = ctx.agent_results.get("intelligence")
        predictions = {}
        if intel_agent:
            predictions = intel_agent.intel_updates.get("player_predictions", {})

        for player in players:
            pid = player.get("id", player.get("player_id", 0))
            pos = player.get("position", [0, 0, 0])
            style = player.get("classified_style", "unknown")
            engagements = len(player.get("contacts", []))

            # Base threat radius depends on player style
            threat_radius = {
                "defensive_sniper": 400,
                "aggressive_assault": 200,
                "flanking_maneuver": 250,
                "vehicle_rusher": 350,
                "overwatch": 450,
                "building_clearer": 100,
                "cautious_observer": 150,
            }.get(style, 200)

            # Threat score factors
            threat_score = 0.3  # base
            if engagements > 5:
                threat_score += 0.3
            elif engagements > 2:
                threat_score += 0.15
            if style in ("defensive_sniper", "overwatch", "aggressive_assault"):
                threat_score += 0.2
            speed = player.get("speed", 0)
            if speed > 5:
                threat_score += 0.1  # mobile players are harder to predict

            # Current position threat
            threat_cells.append({
                "center": f"{pos[0]:.0f},0,{pos[2] if len(pos) > 2 else 0:.0f}",
                "radius": threat_radius,
                "score": round(min(threat_score, 1.0), 2),
                "source": f"player_{pid}",
                "type": "current",
            })

            # Predicted position threat (lower confidence)
            pred = predictions.get(pid)
            if pred and pred.get("confidence", 0) > 0.3:
                threat_cells.append({
                    "center": pred["predicted_position"],
                    "radius": int(threat_radius * 0.7),
                    "score": round(min(threat_score * pred["confidence"], 1.0), 2),
                    "source": f"player_{pid}_predicted",
                    "type": "predicted",
                })

        # Sort by threat score descending
        threat_cells.sort(key=lambda x: x["score"], reverse=True)
        return threat_cells

    def _plan_safe_routes(self, players, enemy_units, threat_map) -> list:
        """Identify positions that are safest for AI approach."""
        if not players or not enemy_units:
            return []

        routes = []
        # For each AI unit, find approach vectors that avoid threat zones
        for unit in enemy_units[:6]:
            u_pos = unit.get("position", [0, 0, 0])
            uid = unit.get("unit_id", "")

            # Find nearest player
            min_dist = float("inf")
            nearest_player_pos = None
            for p in players:
                p_pos = p.get("position", [0, 0, 0])
                dx = u_pos[0] - p_pos[0]
                dz = (u_pos[2] - p_pos[2]) if len(u_pos) > 2 and len(p_pos) > 2 else 0
                d = math.sqrt(dx * dx + dz * dz)
                if d < min_dist:
                    min_dist = d
                    nearest_player_pos = p_pos

            if not nearest_player_pos:
                continue

            # Direct bearing to player
            dx = nearest_player_pos[0] - u_pos[0]
            dz = ((nearest_player_pos[2] if len(nearest_player_pos) > 2 else 0) -
                  (u_pos[2] if len(u_pos) > 2 else 0))
            direct_bearing = math.degrees(math.atan2(dx, dz)) % 360

            # Generate alternative approach bearings (flank angles)
            for offset, label in [(-90, "left_flank"), (90, "right_flank"),
                                   (-45, "oblique_left"), (45, "oblique_right"),
                                   (180, "withdraw")]:
                approach_bearing = (direct_bearing + offset) % 360
                rad = math.radians(approach_bearing)
                # Waypoint 150m in approach direction
                wp_x = u_pos[0] + 150 * math.sin(rad)
                wp_z = (u_pos[2] if len(u_pos) > 2 else 0) + 150 * math.cos(rad)

                # Score this route: lower threat = better
                route_threat = 0
                for cell in threat_map:
                    parts = cell["center"].split(",")
                    cx, cz = float(parts[0]), float(parts[2]) if len(parts) > 2 else 0
                    d = math.sqrt((wp_x - cx) ** 2 + (wp_z - cz) ** 2)
                    if d < cell["radius"]:
                        route_threat += cell["score"] * (1 - d / cell["radius"])

                routes.append({
                    "unit_id": uid,
                    "approach": label,
                    "waypoint": f"{wp_x:.0f},0,{wp_z:.0f}",
                    "bearing": round(approach_bearing, 1),
                    "threat_score": round(route_threat, 2),
                    "distance_to_target": round(min_dist, 0),
                })

        routes.sort(key=lambda x: x["threat_score"])
        return routes

    def _forecast_engagements(self, enemy_units, players, ctx) -> list:
        """Predict outcome of potential engagements."""
        forecasts = []
        for unit in enemy_units[:6]:
            hp = unit.get("health_pct", 1.0)
            men = unit.get("unit_count", unit.get("member_count", 4))
            unit_type = unit.get("type", "rifle_squad")

            # Combat power estimation
            ai_power = hp * men * (1.5 if "mg" in unit_type else 1.0)

            for player in players:
                p_pos = player.get("position", [0, 0, 0])
                u_pos = unit.get("position", [0, 0, 0])
                dx = u_pos[0] - p_pos[0]
                dz = (u_pos[2] - p_pos[2]) if len(u_pos) > 2 and len(p_pos) > 2 else 0
                dist = math.sqrt(dx * dx + dz * dz)

                if dist > 500:
                    continue

                # Player effectiveness scales with engagement count
                pid = player.get("id", player.get("player_id", 0))
                p_engagements = len(player.get("contacts", []))
                player_power = 1.0 + min(p_engagements, 10) * 0.2

                # Difficulty modifier
                diff_mod = ctx.difficulty_params.get("modifier", 1.0)
                ai_effective = ai_power * diff_mod

                win_chance = ai_effective / (ai_effective + player_power)
                forecasts.append({
                    "unit_id": unit.get("unit_id", ""),
                    "vs_player": pid,
                    "distance": round(dist, 0),
                    "ai_win_probability": round(win_chance, 2),
                    "recommendation": (
                        "engage" if win_chance > 0.6
                        else "support_needed" if win_chance > 0.35
                        else "avoid_or_retreat"
                    ),
                })

        return forecasts[:10]

    def _analyze_sectors(self, enemy_units, players) -> dict:
        """Divide battlefield into sectors and analyze force balance."""
        if not enemy_units and not players:
            return {}

        # Get battlefield center
        all_positions = []
        for u in enemy_units:
            p = u.get("position", [0, 0, 0])
            all_positions.append(p)
        for p in players:
            pos = p.get("position", [0, 0, 0])
            all_positions.append(pos)

        if not all_positions:
            return {}

        cx = sum(p[0] for p in all_positions) / len(all_positions)
        cz = sum((p[2] if len(p) > 2 else 0) for p in all_positions) / len(all_positions)

        sectors = {"north": {"ai": 0, "players": 0}, "south": {"ai": 0, "players": 0},
                   "east": {"ai": 0, "players": 0}, "west": {"ai": 0, "players": 0}}

        for u in enemy_units:
            p = u.get("position", [0, 0, 0])
            ns = "north" if (p[2] if len(p) > 2 else 0) > cz else "south"
            ew = "east" if p[0] > cx else "west"
            sectors[ns]["ai"] += 1
            sectors[ew]["ai"] += 1

        for pl in players:
            p = pl.get("position", [0, 0, 0])
            ns = "north" if (p[2] if len(p) > 2 else 0) > cz else "south"
            ew = "east" if p[0] > cx else "west"
            sectors[ns]["players"] += 1
            sectors[ew]["players"] += 1

        # Find weak sectors (few AI, many players) and strong sectors
        for name, data in sectors.items():
            ratio = data["ai"] / max(data["players"], 0.5)
            data["ratio"] = round(ratio, 1)
            data["assessment"] = (
                "strong" if ratio > 2 else "balanced" if ratio > 0.8
                else "weak" if ratio > 0.3 else "exposed"
            )

        return sectors


# ═══════════════════════════════════════════════════════════
# Agent 3: Learning Engine (code + optional async LLM)
# ═══════════════════════════════════════════════════════════

class LearningEngine(BaseAgent):
    """Generates diverse, actionable lessons from battle patterns.

    15+ lesson categories with deduplication and relevance scoring.
    This is the major improvement over the old system that only tracked
    unit survival time.
    """

    name = "learning_engine"
    role = "Pattern recognition, lesson generation, cross-session adaptation"
    phase = AgentPhase.POST_PROCESS

    def __init__(self):
        # Lesson deduplication: track which lesson *types* have been generated
        self._lesson_signatures: set = set()
        # Pattern tracking
        self.tactic_correlations: dict = defaultdict(lambda: {"wins": 0, "losses": 0})
        self.position_outcomes: dict = defaultdict(lambda: {"success": 0, "failure": 0})
        self.timing_patterns: list = []
        self.player_adaptation_history: dict = defaultdict(list)
        self.environmental_outcomes: dict = defaultdict(lambda: {"success": 0, "failure": 0})
        self.spawn_timing_outcomes: list = []
        self.command_diversity_history: list = []
        self.engagement_range_outcomes: list = []
        self.force_composition_survival: dict = defaultdict(list)
        self._consecutive_same_command: dict = defaultdict(int)
        self._last_command_per_unit: dict = {}

    async def process(self, ctx: AgentContext) -> AgentResult:
        result = self._empty_result()
        battle_plan = ctx.battle_plan

        if ctx.turn_number < 2:
            return result

        lessons = []

        # Run all lesson generators
        lessons.extend(self._spatial_pattern_lessons(ctx))
        lessons.extend(self._timing_lessons(ctx))
        lessons.extend(self._tactic_correlation_lessons(ctx))
        lessons.extend(self._environmental_lessons(ctx))
        lessons.extend(self._player_adaptation_lessons(ctx))
        lessons.extend(self._force_composition_lessons(ctx))
        lessons.extend(self._engagement_range_lessons(ctx))
        lessons.extend(self._position_value_lessons(ctx))
        lessons.extend(self._reinforcement_timing_lessons(ctx))
        lessons.extend(self._command_diversity_lessons(ctx))
        lessons.extend(self._kill_zone_lessons(ctx))
        lessons.extend(self._retreat_effectiveness_lessons(ctx))
        lessons.extend(self._coordination_lessons(ctx))
        lessons.extend(self._difficulty_adaptation_lessons(ctx))
        lessons.extend(self._attrition_rate_lessons(ctx))

        # Deduplicate and prioritize
        unique_lessons = []
        for lesson in lessons:
            sig = self._lesson_signature(lesson)
            if sig not in self._lesson_signatures:
                self._lesson_signatures.add(sig)
                unique_lessons.append(lesson)

        # Limit: keep top lessons by priority
        result.lessons = unique_lessons[:5]  # max 5 new lessons per turn

        # Track command diversity
        cmd_list = getattr(battle_plan, "last_commands", [])
        if cmd_list:
            cmd_types = [c.get("command_type", "") for c in cmd_list]
            self.command_diversity_history.append({
                "turn": ctx.turn_number,
                "types": cmd_types,
                "unique": len(set(cmd_types)),
                "total": len(cmd_types),
            })

        # Track consecutive same commands per unit
        for cmd in cmd_list:
            uid = cmd.get("unit_id", "")
            ctype = cmd.get("command_type", "")
            if uid and ctype:
                if self._last_command_per_unit.get(uid) == ctype:
                    self._consecutive_same_command[uid] += 1
                else:
                    self._consecutive_same_command[uid] = 1
                self._last_command_per_unit[uid] = ctype

        return result

    def _lesson_signature(self, lesson: str) -> str:
        """Create a normalized signature for deduplication.
        Strips numbers so 'survived 53s' and 'survived 54s' are the same."""
        import re
        stripped = re.sub(r'\d+\.?\d*', 'N', lesson)
        # Also normalize whitespace
        stripped = ' '.join(stripped.split())
        return stripped[:80]

    # ─── Lesson Category 1: Spatial Patterns ───

    def _spatial_pattern_lessons(self, ctx: AgentContext) -> list:
        """Learn from where units succeed or fail on the map."""
        lessons = []
        intel = ctx.agent_results.get("intelligence")
        if not intel:
            return lessons

        death_zones = intel.intel_updates.get("death_zones", [])
        for zone in death_zones:
            if zone["deaths"] >= 3:
                lessons.append(
                    f"DEATH ZONE at {zone['center']}: {zone['deaths']} AI units lost recently. "
                    f"Do NOT send units through this area without smoke/suppression cover."
                )

        # Sector imbalance
        threat = ctx.agent_results.get("threat_assessment")
        if threat:
            sectors = threat.intel_updates.get("sector_analysis", {})
            for name, data in sectors.items():
                if data.get("assessment") == "exposed":
                    lessons.append(
                        f"{name.upper()} sector is EXPOSED (ratio {data.get('ratio', 0)}:1). "
                        f"Reinforce or withdraw from this sector immediately."
                    )

        return lessons

    # ─── Lesson Category 2: Timing Patterns ───

    def _timing_lessons(self, ctx: AgentContext) -> list:
        """Learn about timing of engagements and player behavior."""
        lessons = []
        mission_time = ctx.game_state.get("mission_time_seconds", 0)

        # Track when players are most aggressive
        players = ctx.game_state.get("players", [])
        active_engagers = sum(1 for p in players
                              if len(p.get("contacts", [])) > 0)

        if active_engagers > 0:
            self.timing_patterns.append({
                "time": mission_time,
                "active_players": active_engagers,
                "total_players": len(players),
            })

        # Analyze timing patterns after enough data
        if len(self.timing_patterns) >= 5:
            recent = self.timing_patterns[-5:]
            all_active = all(t["active_players"] > 0 for t in recent)
            all_passive = all(t["active_players"] == 0 for t in recent)

            if all_active:
                lessons.append(
                    "Players have been continuously active for 5+ cycles. "
                    "Expect sustained pressure - rotate units to maintain readiness."
                )
            elif all_passive and len(players) > 0:
                lessons.append(
                    "Players have been passive for 5+ cycles despite being present. "
                    "They may be regrouping or setting up a coordinated assault. "
                    "Increase observation and prepare defensive positions."
                )

        return lessons

    # ─── Lesson Category 3: Tactic Correlations ───

    def _tactic_correlation_lessons(self, ctx: AgentContext) -> list:
        """Learn which tactic combinations work or fail."""
        lessons = []
        perf = ctx.battle_plan.get_performance_summary() if hasattr(ctx.battle_plan, "get_performance_summary") else {}
        outcomes = perf.get("tactic_outcomes", {})

        # Check for paired tactic success
        suppress_data = outcomes.get("suppress", {})
        flank_l = outcomes.get("flank_left", {})
        flank_r = outcomes.get("flank_right", {})

        suppress_rate = suppress_data.get("success_rate", 0) if suppress_data.get("issued", 0) >= 2 else None
        flank_rate = None
        flank_issued = (flank_l.get("issued", 0) + flank_r.get("issued", 0))
        if flank_issued >= 2:
            flank_success = flank_l.get("successful", 0) + flank_r.get("successful", 0)
            flank_rate = flank_success / flank_issued

        if suppress_rate is not None and flank_rate is not None:
            if suppress_rate > 0.6 and flank_rate > 0.5:
                lessons.append(
                    f"Suppress+flank combination is effective (suppress: {suppress_rate:.0%}, "
                    f"flank: {flank_rate:.0%}). Continue using paired fire-and-maneuver."
                )
            elif suppress_rate > 0.6 and flank_rate < 0.3:
                lessons.append(
                    f"Suppression works ({suppress_rate:.0%}) but flanking fails ({flank_rate:.0%}). "
                    f"Players may be covering flanks - try frontal assault under suppression instead."
                )

        # Check individual tactic performance
        for tactic, data in outcomes.items():
            issued = data.get("issued", 0)
            rate = data.get("success_rate", 0)
            if issued >= 4:
                if rate == 0:
                    lessons.append(
                        f"'{tactic}' has ZERO success after {issued} attempts. "
                        f"STOP using this tactic and switch approach entirely."
                    )
                elif rate >= 0.8:
                    lessons.append(
                        f"'{tactic}' is highly effective ({rate:.0%} success over {issued} uses). "
                        f"Double down on this approach."
                    )
                elif rate < 0.25 and issued >= 5:
                    lessons.append(
                        f"'{tactic}' is largely failing ({rate:.0%} over {issued} uses). "
                        f"Consider alternatives or different conditions before retrying."
                    )

        return lessons

    # ─── Lesson Category 4: Environmental Exploitation ───

    def _environmental_lessons(self, ctx: AgentContext) -> list:
        """Learn from weather/time impact on outcomes."""
        lessons = []
        weather = ctx.game_state.get("weather", "Clear")
        time_of_day = ctx.game_state.get("time_of_day", 12.0)
        visibility = ctx.tactical_intel.get("environment", {}).get("visibility_pct", 100)

        is_night = time_of_day < 5.0 or time_of_day > 20.0
        is_poor_vis = visibility < 30

        perf = ctx.battle_plan.get_performance_summary() if hasattr(ctx.battle_plan, "get_performance_summary") else {}
        outcomes = perf.get("tactic_outcomes", {})

        # Track environment-outcome correlations
        env_key = f"{weather}_{('night' if is_night else 'day')}"
        total_success = sum(d.get("successful", 0) for d in outcomes.values())
        total_failed = sum(d.get("failed", 0) for d in outcomes.values())

        if total_success + total_failed > 0:
            self.environmental_outcomes[env_key]["success"] += total_success
            self.environmental_outcomes[env_key]["failure"] += total_failed

        # Generate lessons from environmental patterns
        for env, data in self.environmental_outcomes.items():
            total = data["success"] + data["failure"]
            if total >= 5:
                rate = data["success"] / total
                weather_part, time_part = env.rsplit("_", 1)
                if rate > 0.7:
                    lessons.append(
                        f"Operations during {weather_part}/{time_part} conditions show {rate:.0%} success rate. "
                        f"Favor operations during these conditions."
                    )
                elif rate < 0.3:
                    lessons.append(
                        f"Operations during {weather_part}/{time_part} conditions fail {1-rate:.0%} of the time. "
                        f"Avoid major operations in these conditions or adapt tactics."
                    )

        if is_poor_vis and is_night:
            # Check if silent_move is being used
            silent_issued = outcomes.get("silent_move", {}).get("issued", 0)
            if silent_issued == 0 and ctx.turn_number > 3:
                lessons.append(
                    f"Night + poor visibility ({visibility}%) but silent_move not being used. "
                    f"These conditions are IDEAL for infiltration - use silent_move and ambushes."
                )

        return lessons

    # ─── Lesson Category 5: Player Adaptation ───

    def _player_adaptation_lessons(self, ctx: AgentContext) -> list:
        """Detect when players change their behavior in response to AI tactics."""
        lessons = []
        players = ctx.game_state.get("players", [])

        for player in players:
            pid = player.get("id", player.get("player_id", 0))
            style = player.get("classified_style", "unknown")
            trend = player.get("behavior_trend", "unknown")

            self.player_adaptation_history[pid].append({
                "turn": ctx.turn_number,
                "style": style,
                "trend": trend,
            })

            history = self.player_adaptation_history[pid]
            if len(history) >= 4:
                recent_styles = [h["style"] for h in history[-4:]]
                unique_styles = len(set(recent_styles))
                if unique_styles >= 3:
                    lessons.append(
                        f"Player {pid} is rapidly shifting tactics ({' -> '.join(recent_styles[-3:])}). "
                        f"They are adapting to our strategy. Vary approach and avoid predictable patterns."
                    )
                elif unique_styles == 1 and len(history) >= 6:
                    lessons.append(
                        f"Player {pid} has been consistently '{recent_styles[0]}' for {len(history)} turns. "
                        f"Use the specific counter-tactic from the player response matrix."
                    )

            if trend == "escalating":
                lessons.append(
                    f"Player {pid} aggression is ESCALATING ({style}). "
                    f"Prepare ambushes ahead of their advance vector."
                )
            elif trend == "de-escalating" and len(history) >= 3:
                prev_styles = [h["style"] for h in history[-3:]]
                if any("aggressive" in s for s in prev_styles):
                    lessons.append(
                        f"Player {pid} was aggressive but is now de-escalating. "
                        f"They may be wounded, low on ammo, or repositioning. "
                        f"Maintain pressure but watch for trap."
                    )

        return lessons

    # ─── Lesson Category 6: Force Composition ───

    def _force_composition_lessons(self, ctx: AgentContext) -> list:
        """Learn which unit types survive longest and perform best."""
        lessons = []
        enemy_units = ctx.game_state.get("enemy_units", [])

        for unit in enemy_units:
            uid = unit.get("unit_id", "")
            utype = unit.get("type", "unknown")
            hp = unit.get("health_pct", 1.0)
            survival = ctx.battle_plan.unit_survival.get(uid, 0) if hasattr(ctx.battle_plan, "unit_survival") else 0

            self.force_composition_survival[utype].append({
                "survival": survival,
                "health": hp,
                "kills": unit.get("kills", 0),
            })

        # Analyze after enough data
        for utype, records in self.force_composition_survival.items():
            if len(records) >= 5:
                avg_survival = sum(r["survival"] for r in records[-5:]) / 5
                avg_kills = sum(r["kills"] for r in records[-5:]) / 5
                total_kills = sum(r["kills"] for r in records[-5:])

                if avg_survival < 30:
                    lessons.append(
                        f"'{utype}' units survive only {avg_survival:.0f}s on average. "
                        f"They are being destroyed too quickly - use them in cover or as support, not point units."
                    )
                elif avg_survival > 120 and avg_kills > 0:
                    lessons.append(
                        f"'{utype}' units are durable ({avg_survival:.0f}s avg) and effective ({total_kills} kills). "
                        f"Prioritize spawning this unit type."
                    )

        return lessons

    # ─── Lesson Category 7: Engagement Range ───

    def _engagement_range_lessons(self, ctx: AgentContext) -> list:
        """Learn optimal engagement distances."""
        lessons = []
        intel = ctx.agent_results.get("intelligence")
        if not intel:
            return lessons

        profiles = intel.intel_updates.get("engagement_profiles", {})
        for pid, profile in profiles.items():
            if profile.get("samples", 0) >= 5:
                preferred = profile["preferred_range"]
                if preferred < 100:
                    lessons.append(
                        f"Player {pid} prefers CQB range ({preferred:.0f}m median). "
                        f"Keep engagements beyond 200m or set up close-range ambushes."
                    )
                elif preferred > 300:
                    lessons.append(
                        f"Player {pid} engages at long range ({preferred:.0f}m median). "
                        f"Use smoke to close distance or counter-snipe with designated marksmen."
                    )

        return lessons

    # ─── Lesson Category 8: Position Value ───

    def _position_value_lessons(self, ctx: AgentContext) -> list:
        """Learn which positions on the map are valuable."""
        lessons = []
        enemy_units = ctx.game_state.get("enemy_units", [])

        for unit in enemy_units:
            uid = unit.get("unit_id", "")
            pos = unit.get("position", [0, 0, 0])
            status = unit.get("order_status", "")
            hp = unit.get("health_pct", 1.0)
            kills = unit.get("kills", 0)
            terrain = unit.get("terrain_type", "unknown")
            cover = unit.get("cover_density", "unknown")

            # Grid-based position tracking
            gx = int(pos[0] // 200) * 200
            gz = int((pos[2] if len(pos) > 2 else 0) // 200) * 200
            pos_key = f"{gx},{gz}"

            if kills > 0 and hp > 0.5:
                self.position_outcomes[pos_key]["success"] += 1
            elif hp < 0.3:
                self.position_outcomes[pos_key]["failure"] += 1

        # Generate lessons from position data
        for pos_key, data in self.position_outcomes.items():
            total = data["success"] + data["failure"]
            if total >= 4:
                rate = data["success"] / total
                if rate > 0.75:
                    lessons.append(
                        f"Grid {pos_key} is a STRONG position ({rate:.0%} success, {total} engagements). "
                        f"Prioritize holding this area."
                    )
                elif rate < 0.25:
                    lessons.append(
                        f"Grid {pos_key} is a KILL ZONE for AI ({1-rate:.0%} failure, {total} engagements). "
                        f"Avoid positioning units here."
                    )

        return lessons

    # ─── Lesson Category 9: Reinforcement Timing ───

    def _reinforcement_timing_lessons(self, ctx: AgentContext) -> list:
        """Learn about spawn timing effectiveness."""
        lessons = []
        cmd_list = getattr(ctx.battle_plan, "last_commands", [])

        spawn_commands = [c for c in cmd_list if "spawn" in c.get("command_type", "")]
        if spawn_commands:
            # Check if there are active engagements happening
            enemy_units = ctx.game_state.get("enemy_units", [])
            engaged_units = sum(1 for u in enemy_units if u.get("is_engaging"))
            total_units = len(enemy_units)

            engagement_ratio = engaged_units / max(total_units, 1)
            self.spawn_timing_outcomes.append({
                "turn": ctx.turn_number,
                "spawns": len(spawn_commands),
                "engagement_ratio": engagement_ratio,
            })

        if len(self.spawn_timing_outcomes) >= 4:
            during_combat = [s for s in self.spawn_timing_outcomes if s["engagement_ratio"] > 0.5]
            during_lull = [s for s in self.spawn_timing_outcomes if s["engagement_ratio"] <= 0.5]

            if len(during_combat) >= 3:
                lessons.append(
                    "Reinforcements being spawned during active combat. "
                    "Fresh units arriving mid-battle may get caught in crossfire. "
                    "Consider spawning during lulls and positioning before engagement."
                )

        return lessons

    # ─── Lesson Category 10: Command Diversity ───

    def _command_diversity_lessons(self, ctx: AgentContext) -> list:
        """Detect monotonous command patterns."""
        lessons = []

        if len(self.command_diversity_history) >= 3:
            recent = self.command_diversity_history[-3:]
            avg_unique = sum(h["unique"] for h in recent) / len(recent)
            avg_total = sum(h["total"] for h in recent) / len(recent)

            diversity_ratio = avg_unique / max(avg_total, 1)
            if diversity_ratio < 0.3 and avg_total > 3:
                # Find the dominant command
                all_types = []
                for h in recent:
                    all_types.extend(h["types"])
                from collections import Counter
                most_common = Counter(all_types).most_common(1)
                if most_common:
                    dominant = most_common[0]
                    lessons.append(
                        f"Command pattern is monotonous: '{dominant[0]}' used {dominant[1]} times "
                        f"out of {len(all_types)} total commands ({dominant[1]/len(all_types):.0%}). "
                        f"Players will predict this. Diversify tactics: combine movement, suppression, "
                        f"flanking, and environmental commands."
                    )

        # Check for units stuck in loops
        for uid, count in self._consecutive_same_command.items():
            if count >= 4:
                cmd = self._last_command_per_unit.get(uid, "?")
                lessons.append(
                    f"Unit {uid} has received '{cmd}' for {count} consecutive turns. "
                    f"This unit may be stuck or ineffective. Reassign to a different objective."
                )

        return lessons

    # ─── Lesson Category 11: Kill Zone Analysis ───

    def _kill_zone_lessons(self, ctx: AgentContext) -> list:
        """Learn from ambush detection data."""
        lessons = []
        ambush_warnings = ctx.tactical_intel.get("ambush_warnings", [])
        coordinated = ctx.tactical_intel.get("coordinated_ambush", {})

        if coordinated.get("detected"):
            involved = coordinated.get("players_involved", [])
            lessons.append(
                f"COORDINATED AMBUSH detected! {len(involved)} players in crossfire setup. "
                f"Zone: {coordinated.get('crossfire_zone')}. "
                f"Do NOT send units through. Use smoke + indirect approach."
            )

        for warning in ambush_warnings:
            if warning.get("duration_seconds", 0) > 90:
                lessons.append(
                    f"Player {warning['player_id']} has maintained ambush position for "
                    f"{warning['duration_seconds']}s. Well-established position. "
                    f"Approach from bearing {warning.get('safe_approach_bearing', '?')} with suppression."
                )

        return lessons

    # ─── Lesson Category 12: Retreat Effectiveness ───

    def _retreat_effectiveness_lessons(self, ctx: AgentContext) -> list:
        """Analyze retreat patterns."""
        lessons = []
        perf = ctx.battle_plan.get_performance_summary() if hasattr(ctx.battle_plan, "get_performance_summary") else {}
        outcomes = perf.get("tactic_outcomes", {})
        retreat_data = outcomes.get("retreat", {})

        if retreat_data.get("issued", 0) >= 3:
            rate = retreat_data.get("success_rate", 0)
            if rate < 0.4:
                lessons.append(
                    f"Retreat orders have low success ({rate:.0%}). "
                    f"Units are being destroyed during withdrawal. "
                    f"Use smoke screens, suppressing fire, or fighting_withdrawal instead of direct retreat."
                )
            elif rate > 0.8:
                # Check if retreat is overused
                total_issued = sum(d.get("issued", 0) for d in outcomes.values())
                retreat_pct = retreat_data["issued"] / max(total_issued, 1)
                if retreat_pct > 0.5:
                    lessons.append(
                        f"Retreat accounts for {retreat_pct:.0%} of all orders. "
                        f"Over-reliance on retreat cedes initiative. "
                        f"Mix in defensive positions and counter-attacks when possible."
                    )

        return lessons

    # ─── Lesson Category 13: Coordination ───

    def _coordination_lessons(self, ctx: AgentContext) -> list:
        """Lessons about multi-unit coordination."""
        lessons = []
        intel = ctx.agent_results.get("intelligence")
        if not intel:
            return lessons

        coord = intel.intel_updates.get("team_coordination", {})
        score = coord.get("score", 0)

        if score > 0.7 and ctx.turn_number > 3:
            lessons.append(
                f"Players are HIGHLY COORDINATED (score: {score:.0%}). "
                f"Do not engage piecemeal. Use combined arms and numerical superiority. "
                f"Attack from multiple axes simultaneously."
            )
        elif score < 0.3 and len(ctx.game_state.get("players", [])) >= 2:
            lessons.append(
                f"Players are acting INDEPENDENTLY (coordination: {score:.0%}). "
                f"Exploit this: isolate and defeat in detail. "
                f"Pin one player, concentrate force on the other."
            )

        return lessons

    # ─── Lesson Category 14: Difficulty Adaptation ───

    def _difficulty_adaptation_lessons(self, ctx: AgentContext) -> list:
        """Learn from difficulty system interactions."""
        lessons = []
        diff = ctx.difficulty_params
        tier = diff.get("tier", "")
        modifier = diff.get("modifier", 1.0)
        mercy = diff.get("mercy_active", False)

        if mercy:
            lessons.append(
                "MERCY MODE active — players struggling. "
                "Focus on creating interesting encounters, not winning. "
                "Use patrol, observe, and light probing only."
            )

        if modifier < 0.5 and ctx.turn_number > 5:
            lessons.append(
                f"Difficulty very low (x{modifier:.2f}) after {ctx.turn_number} turns. "
                f"Players may need time to learn the game. "
                f"Present clear, telegraphed threats they can react to."
            )

        return lessons

    # ─── Lesson Category 15: Attrition Rate ───

    def _attrition_rate_lessons(self, ctx: AgentContext) -> list:
        """Track overall attrition trends."""
        lessons = []
        enemy_units = ctx.game_state.get("enemy_units", [])

        if not enemy_units:
            return lessons

        alive = len(enemy_units)
        avg_hp = sum(u.get("health_pct", 1.0) for u in enemy_units) / alive
        effective = sum(1 for u in enemy_units if u.get("health_pct", 1.0) > 0.5)

        combat_effectiveness = effective / alive if alive else 0

        if combat_effectiveness < 0.3 and alive >= 2:
            lessons.append(
                f"Only {effective}/{alive} units ({combat_effectiveness:.0%}) are combat effective. "
                f"Force is combat-ineffective. Consolidate, merge remnants, and request reinforcements "
                f"before any offensive operations."
            )
        elif avg_hp > 0.8 and alive >= 3:
            perf = ctx.battle_plan.get_performance_summary() if hasattr(ctx.battle_plan, "get_performance_summary") else {}
            total_failed = sum(d.get("failed", 0) for d in perf.get("tactic_outcomes", {}).values())
            if total_failed < 2:
                lessons.append(
                    f"Force is in excellent condition ({alive} units, {avg_hp:.0%} avg HP). "
                    f"Good time to press advantage with coordinated operations."
                )

        return lessons


# ═══════════════════════════════════════════════════════════
# Agent 4: Difficulty Agent (code-based, wraps existing)
# ═══════════════════════════════════════════════════════════

class DifficultyAgent(BaseAgent):
    """Wraps DifficultyController with agent interface and adds recommendations."""

    name = "difficulty"
    role = "Dynamic difficulty assessment and command recommendations"
    phase = AgentPhase.PRE_PROCESS

    async def process(self, ctx: AgentContext) -> AgentResult:
        result = self._empty_result()
        diff = ctx.difficulty_params

        tier = diff.get("tier", "balanced")
        modifier = diff.get("modifier", 1.0)
        mercy = diff.get("mercy_active", False)

        # Generate tactical recommendations based on difficulty
        recommendations = []
        forbidden_commands = []
        preferred_commands = []

        if mercy or tier in ("mercy", "MERCY_MODE_players_need_help"):
            forbidden_commands = ["attack", "seek_and_destroy", "flank_left", "flank_right",
                                  "breach", "spawn_infantry", "spawn_vehicle"]
            preferred_commands = ["retreat", "patrol", "defend", "radio_comms", "set_weather"]
            recommendations.append("MERCY MODE: Only patrol, defend, and retreat. No attacks.")
        elif tier in ("easy", "reduced"):
            forbidden_commands = ["seek_and_destroy", "breach", "coordinated_operation"]
            preferred_commands = ["defend", "patrol", "silent_move", "overwatch"]
            recommendations.append(f"Reduced difficulty ({modifier:.2f}x): Light probing only.")
        elif tier in ("hard", "extreme"):
            preferred_commands = ["attack", "flank_left", "flank_right", "bounding_overwatch",
                                  "suppress", "coordinated_operation"]
            recommendations.append(f"High difficulty ({modifier:.2f}x): Aggressive combined arms.")

        result.intel_updates = {
            "difficulty_recommendations": recommendations,
            "forbidden_commands": forbidden_commands,
            "preferred_commands": preferred_commands,
            "max_spawns": diff.get("max_spawns_per_cycle", 3),
            "max_attackers": diff.get("max_attackers", 5),
        }

        return result


# ═══════════════════════════════════════════════════════════
# Agent 5: Force Preservation Agent (code-based)
# ═══════════════════════════════════════════════════════════

class ForcePreservationAgent(BaseAgent):
    """Morale, retreat decisions, unit merging recommendations."""

    name = "force_preservation"
    role = "Unit health monitoring, retreat/merge decisions, morale tracking"
    phase = AgentPhase.PRE_PROCESS

    def __init__(self):
        self.unit_morale: dict[str, float] = {}  # uid -> morale 0-1
        self.merge_candidates: list = []

    async def process(self, ctx: AgentContext) -> AgentResult:
        result = self._empty_result()
        enemy_units = ctx.game_state.get("enemy_units", [])

        critical_units = []
        retreat_commands = []
        merge_suggestions = []
        low_hp_units = []

        for unit in enemy_units:
            uid = unit.get("unit_id", "")
            hp = unit.get("health_pct", 1.0)
            men = unit.get("unit_count", unit.get("member_count", 4))
            status = unit.get("order_status", "idle")
            pos = unit.get("position", [0, 0, 0])

            # Update morale based on health and casualties
            prev_morale = self.unit_morale.get(uid, 0.8)
            morale = min(1.0, hp * 0.6 + (men / 8) * 0.4)
            # Morale decays slowly from combat stress
            if status in ("engaged", "suppressed"):
                morale *= 0.9
            self.unit_morale[uid] = morale

            if hp < 0.25:
                critical_units.append(uid)
                # Generate auto-retreat command
                # Retreat away from nearest player
                players = ctx.game_state.get("players", [])
                retreat_pos = self._calculate_retreat_position(pos, players)
                retreat_commands.append({
                    "command_type": "retreat",
                    "unit_id": uid,
                    "target_position": retreat_pos,
                    "reasoning": f"FORCE PRESERVATION: {uid} critical ({hp:.0%} HP, {men} men). Auto-retreat.",
                    "narrative": "Unit is combat ineffective. Withdrawing to regroup.",
                    "parameters": "",
                })

            elif hp < 0.5 and men <= 2:
                low_hp_units.append({
                    "unit_id": uid,
                    "health": hp,
                    "men": men,
                    "position": pos,
                })

        # Find merge candidates: low-HP units near each other
        for i in range(len(low_hp_units)):
            for j in range(i + 1, len(low_hp_units)):
                a, b = low_hp_units[i], low_hp_units[j]
                dx = a["position"][0] - b["position"][0]
                dz = ((a["position"][2] - b["position"][2])
                      if len(a["position"]) > 2 and len(b["position"]) > 2 else 0)
                dist = math.sqrt(dx * dx + dz * dz)
                if dist < 300:
                    merge_suggestions.append({
                        "unit_a": a["unit_id"],
                        "unit_b": b["unit_id"],
                        "distance": round(dist, 0),
                        "suggestion": (
                            f"Merge {a['unit_id']} ({a['men']} men, {a['health']:.0%}) "
                            f"with {b['unit_id']} ({b['men']} men, {b['health']:.0%}). "
                            f"Move both to midpoint and consolidate."
                        ),
                    })

        result.commands = retreat_commands
        result.intel_updates = {
            "critical_units": critical_units,
            "unit_morale": {uid: round(m, 2) for uid, m in self.unit_morale.items()},
            "merge_suggestions": merge_suggestions[:3],
            "force_status": {
                "total_units": len(enemy_units),
                "combat_effective": sum(1 for u in enemy_units if u.get("health_pct", 1.0) > 0.5),
                "critical": len(critical_units),
                "avg_morale": round(sum(self.unit_morale.values()) / max(len(self.unit_morale), 1), 2),
            },
        }

        return result

    def _calculate_retreat_position(self, unit_pos, players) -> str:
        """Calculate a retreat position away from the nearest player."""
        if not players:
            # Default: retreat south
            return f"{unit_pos[0]},0,{(unit_pos[2] if len(unit_pos) > 2 else 0) - 300}"

        # Find centroid of players
        px = sum(p.get("position", [0])[0] for p in players) / len(players)
        pz = sum((p.get("position", [0, 0, 0])[2] if len(p.get("position", [0, 0, 0])) > 2 else 0)
                 for p in players) / len(players)

        # Retreat away from player centroid
        dx = unit_pos[0] - px
        dz = (unit_pos[2] if len(unit_pos) > 2 else 0) - pz
        dist = math.sqrt(dx * dx + dz * dz)
        if dist < 1:
            dx, dz = 0, -1
            dist = 1

        # Normalize and project 400m away from players
        rx = unit_pos[0] + (dx / dist) * 400
        rz = (unit_pos[2] if len(unit_pos) > 2 else 0) + (dz / dist) * 400

        return f"{rx:.0f},0,{rz:.0f}"


# ═══════════════════════════════════════════════════════════
# Agent 6: Environment Agent (code-based)
# ═══════════════════════════════════════════════════════════

class EnvironmentAgent(BaseAgent):
    """Weather and time management based on tactical situation."""

    name = "environment"
    role = "Weather/time recommendations, mood setting"
    phase = AgentPhase.PRE_PROCESS

    def __init__(self):
        self._last_weather_change_turn = 0
        self._current_mood = "calm_before"

    async def process(self, ctx: AgentContext) -> AgentResult:
        result = self._empty_result()
        weather_controls_available = ctx.game_state.get("weather_controls_available", True)
        weather = ctx.game_state.get("current_weather", ctx.game_state.get("weather", "Clear"))
        time_of_day = ctx.game_state.get("time_of_day", 12.0)
        mission_time = ctx.game_state.get("mission_time_seconds", 0)
        phase = ctx.battle_plan.current_phase if hasattr(ctx.battle_plan, "current_phase") else "probing"

        recommendations = []

        # Don't change weather too frequently
        turns_since_change = ctx.turn_number - self._last_weather_change_turn

        if weather_controls_available and turns_since_change >= 5:  # At least 5 turns between changes
            # Match weather to tactical phase
            if phase == "assault" and weather not in ("Storm", "Foggy"):
                recommendations.append({
                    "type": "weather_suggestion",
                    "suggestion": "Consider storm or fog to cover assault approach",
                    "priority": "medium",
                })
            elif phase == "reconnaissance" and weather not in ("Clear", "Cloudy"):
                recommendations.append({
                    "type": "weather_suggestion",
                    "suggestion": "Clear weather aids reconnaissance operations",
                    "priority": "low",
                })
            elif phase == "defensive_withdrawal" and weather != "Foggy":
                recommendations.append({
                    "type": "weather_suggestion",
                    "suggestion": "Fog would cover withdrawal and break contact",
                    "priority": "high",
                })

        # Mood recommendation based on battle state
        force_pres = ctx.agent_results.get("force_preservation")
        if force_pres:
            force_status = force_pres.intel_updates.get("force_status", {})
            if force_status.get("critical", 0) > force_status.get("total_units", 1) * 0.5:
                recommendations.append({
                    "type": "mood_suggestion",
                    "suggestion": "environment_mood: dread — forces are in dire condition",
                    "priority": "high",
                })

        # Ambient intensity
        engagement_zones = ctx.tactical_intel.get("engagement_zones", [])
        hot_zones = sum(1 for z in engagement_zones if z.get("intensity") == "hot")
        if hot_zones >= 2:
            recommendations.append({
                "type": "ambient_suggestion",
                "suggestion": "set_ambient_intensity: 3 — intense combat across multiple zones",
                "priority": "medium",
            })

        result.intel_updates = {
            "environment_recommendations": recommendations,
            "current_conditions": {
                "weather": weather,
                "weather_controls_available": weather_controls_available,
                "time_of_day": time_of_day,
                "is_night": time_of_day < 5.0 or time_of_day > 20.0,
                "mission_time": mission_time,
            },
        }

        return result


# ═══════════════════════════════════════════════════════════
# Agent 7: Logistics Agent (code-based)
# ═══════════════════════════════════════════════════════════

class LogisticsAgent(BaseAgent):
    """Reinforcement priority, spawn decisions, force composition planning."""

    name = "logistics"
    role = "Reinforcement needs, spawn priorities, composition optimization"
    phase = AgentPhase.PRE_PROCESS

    def __init__(self):
        self._spawn_history: list = []

    async def process(self, ctx: AgentContext) -> AgentResult:
        result = self._empty_result()
        enemy_units = ctx.game_state.get("enemy_units", [])
        players = ctx.game_state.get("players", [])
        diff = ctx.difficulty_params

        # Assess reinforcement needs
        total_units = len(enemy_units)
        effective_units = sum(1 for u in enemy_units if u.get("health_pct", 1.0) > 0.5)
        player_count = len(players)
        desired_ratio = 2.5  # AI:player force ratio goal
        needed = max(0, int(player_count * desired_ratio) - effective_units)

        # Clamp to difficulty limits
        max_spawns = diff.get("max_spawns_per_cycle", 3)
        spawn_count = min(needed, max_spawns)

        # Determine what types are needed based on current composition
        current_types = defaultdict(int)
        for u in enemy_units:
            utype = u.get("type", "unknown")
            current_types[utype] += 1

        # Priority queue: what the force needs
        spawn_priorities = []
        has_mg = sum(v for k, v in current_types.items() if "mg" in k)
        has_at = sum(v for k, v in current_types.items() if "at" in k)
        has_sniper = sum(v for k, v in current_types.items() if "sniper" in k)
        has_screening = sum(v for k, v in current_types.items() if k == "ussr_fire_team")

        # Check what players are doing to recommend counters
        player_styles = [p.get("classified_style", "unknown") for p in players]

        if any("vehicle" in s for s in player_styles) and has_at == 0:
            spawn_priorities.append({
                "type": "ussr_at_team",
                "priority": "critical",
                "reason": "Vehicle-using players detected, no AT capability",
            })

        if any("sniper" in s or "overwatch" in s for s in player_styles) and has_sniper == 0:
            spawn_priorities.append({
                "type": "ussr_sniper_team",
                "priority": "high",
                "reason": "Counter-sniper capability needed",
            })

        if has_mg == 0 and total_units >= 2:
            spawn_priorities.append({
                "type": "ussr_mg_team",
                "priority": "high",
                "reason": "No suppression capability — MG team needed for fire support",
            })

        if has_screening == 0 and player_count > 0:
            spawn_priorities.append({
                "type": "ussr_fire_team",
                "priority": "medium",
                "reason": "No light screening force — use a fire team for observation and probing",
            })

        spawn_priorities = [sp for sp in spawn_priorities if sp["type"] in SUPPORTED_OPFOR_INFANTRY_TYPES]

        # Default: rifle squads fill remaining slots
        if spawn_count > len(spawn_priorities):
            remaining = spawn_count - len(spawn_priorities)
            for _ in range(remaining):
                spawn_priorities.append({
                    "type": "ussr_rifle_squad",
                    "priority": "normal",
                    "reason": "General reinforcement",
                })

        result.intel_updates = {
            "reinforcement_assessment": {
                "needed": needed,
                "allowed": spawn_count,
                "current_strength": total_units,
                "effective_strength": effective_units,
                "desired_ratio": desired_ratio,
            },
            "spawn_priorities": spawn_priorities[:max_spawns],
            "composition_gaps": {
                "mg_teams": has_mg,
                "at_teams": has_at,
                "sniper_teams": has_sniper,
                "screening_teams": has_screening,
            },
        }

        return result


# ═══════════════════════════════════════════════════════════
# Agent 8: Strategic Commander (LLM-backed)
# ═══════════════════════════════════════════════════════════

STRATEGIC_COMMANDER_PROMPT = """\
You are the STRATEGIC COMMANDER for OPFOR forces. Your role is ONLY high-level strategy.
You receive pre-processed intelligence from specialized staff officers.

Your outputs:
1. Strategic phase assessment (what phase should we be in?)
2. Force allocation priorities (which sectors need attention?)
3. Key tactical objectives (what are we trying to achieve this turn?)
4. Risk assessment (what could go wrong?)

You do NOT issue specific unit commands — your Tactical Planner handles that.
Think like a general, not a squad leader.

Respond with ONLY this JSON (keep values SHORT — max 10 words each):
{
  "phase_recommendation": "reconnaissance|probing|buildup|assault|exploitation|defense|defensive_withdrawal",
  "sector_priorities": [{"sector": "north/south/east/west", "priority": "high/medium/low", "action": "brief action"}],
  "objectives": ["objective 1", "objective 2"],
  "risk_assessment": "brief risk",
  "force_allocation": {"attack": 0.3, "defend": 0.4, "reserve": 0.3},
  "reasoning": "brief reasoning"
}
IMPORTANT: Do NOT wrap in markdown fences. Output raw JSON only. Keep ALL string values under 10 words."""


class StrategicCommanderAgent(BaseAgent):
    """Code-driven strategic decision-making (no LLM call — derives phase from game state)."""

    name = "strategic_commander"
    role = "High-level strategy, phase management, force allocation"
    phase = AgentPhase.DECISION
    requires_llm = False

    async def process(self, ctx: AgentContext) -> AgentResult:
        result = self._empty_result()

        # Derive strategic phase from game state instead of burning an LLM call
        phase, force_alloc, objectives = self._derive_strategy(ctx)

        result.metadata = {
            "llm_response": {
                "phase_recommendation": phase,
                "force_allocation": force_alloc,
                "objectives": objectives,
                "sector_priorities": self._derive_sector_priorities(ctx),
            },
            "agent_role": "strategic",
        }

        return result

    def _derive_strategy(self, ctx) -> tuple:
        """Derive strategic phase from game state metrics."""
        gs = ctx.game_state
        bp = ctx.battle_plan
        enemy_units = gs.get("enemy_units", [])
        players = gs.get("players", [])
        mission_time = gs.get("mission_time", 0)

        total_units = len(enemy_units)
        healthy = sum(1 for u in enemy_units if u.get("health_pct", 1.0) > 0.5)
        player_count = max(len(players), 1)

        # Check force preservation status
        fp = ctx.agent_results.get("force_preservation")
        critical_count = 0
        if fp:
            fs = fp.intel_updates.get("force_status", {})
            critical_count = fs.get("critical", 0)

        # Phase logic
        if total_units == 0:
            phase = "reconnaissance"
            alloc = {"attack": 0.0, "defend": 0.2, "reserve": 0.8}
            objectives = ["Deploy initial forces", "Establish observation posts"]
        elif total_units < 4 or mission_time < 120:
            phase = "buildup"
            alloc = {"attack": 0.2, "defend": 0.5, "reserve": 0.3}
            objectives = ["Spawn reinforcements", "Secure key positions"]
        elif critical_count > total_units * 0.4:
            phase = "defensive_withdrawal"
            alloc = {"attack": 0.0, "defend": 0.3, "reserve": 0.7}
            objectives = ["Retreat critical units", "Consolidate remaining forces"]
        elif healthy > player_count * 3:
            phase = "assault"
            alloc = {"attack": 0.6, "defend": 0.2, "reserve": 0.2}
            objectives = ["Press the attack", "Exploit weak sectors"]
        elif healthy > player_count * 1.5:
            phase = "probing"
            alloc = {"attack": 0.4, "defend": 0.3, "reserve": 0.3}
            objectives = ["Probe enemy defenses", "Find weaknesses"]
        else:
            phase = "defense"
            alloc = {"attack": 0.1, "defend": 0.6, "reserve": 0.3}
            objectives = ["Hold current positions", "Request reinforcements"]

        return phase, alloc, objectives

    def _derive_sector_priorities(self, ctx) -> list:
        """Derive sector priorities from threat assessment."""
        ta = ctx.agent_results.get("threat_assessment")
        priorities = []
        if ta:
            sectors = ta.intel_updates.get("sector_analysis", {})
            for name, data in sectors.items():
                assessment = data.get("assessment", "unknown")
                if assessment in ("weak", "exposed"):
                    priorities.append({"sector": name, "priority": "high", "action": "reinforce"})
                elif assessment == "contested":
                    priorities.append({"sector": name, "priority": "medium", "action": "hold"})
                else:
                    priorities.append({"sector": name, "priority": "low", "action": "monitor"})
        if not priorities:
            priorities = [{"sector": "all", "priority": "medium", "action": "patrol"}]
        return priorities

    def _build_strategic_briefing(self, ctx: AgentContext) -> str:
        parts = ["=== STRATEGIC BRIEFING ==="]

        # Force status
        fp = ctx.agent_results.get("force_preservation")
        if fp:
            fs = fp.intel_updates.get("force_status", {})
            parts.append(f"FORCE: {fs.get('total_units', 0)} units, "
                         f"{fs.get('combat_effective', 0)} effective, "
                         f"{fs.get('critical', 0)} critical. "
                         f"Morale: {fs.get('avg_morale', 0):.0%}")

        # Threat summary
        ta = ctx.agent_results.get("threat_assessment")
        if ta:
            sectors = ta.intel_updates.get("sector_analysis", {})
            for name, data in sectors.items():
                if data.get("assessment") in ("weak", "exposed"):
                    parts.append(f"SECTOR {name.upper()}: {data['assessment']} (AI:Player = {data.get('ratio', 0)}:1)")

        # Difficulty
        diff = ctx.agent_results.get("difficulty")
        if diff:
            recs = diff.intel_updates.get("difficulty_recommendations", [])
            for r in recs:
                parts.append(f"DIFFICULTY: {r}")

        # Logistics
        logi = ctx.agent_results.get("logistics")
        if logi:
            reinf = logi.intel_updates.get("reinforcement_assessment", {})
            parts.append(f"REINFORCEMENTS: Need {reinf.get('needed', 0)}, "
                         f"allowed {reinf.get('allowed', 0)}")

        # Player intel
        intel = ctx.agent_results.get("intelligence")
        if intel:
            coord = intel.intel_updates.get("team_coordination", {})
            parts.append(f"PLAYER COORDINATION: {coord.get('assessment', 'unknown')}")
            preds = intel.intel_updates.get("player_predictions", {})
            for pid, pred in list(preds.items())[:3]:
                parts.append(f"Player {pid} heading {pred.get('bearing', '?')}° "
                             f"-> {pred.get('predicted_position', '?')}")

        # Recent lessons
        bp = ctx.battle_plan
        if hasattr(bp, "lessons_learned") and bp.lessons_learned:
            parts.append("RECENT LESSONS:")
            for lesson in bp.lessons_learned[-3:]:
                parts.append(f"  * {lesson}")

        # Players and current situation
        players = ctx.game_state.get("players", [])
        parts.append(f"\nPLAYERS: {len(players)}")
        for p in players:
            pid = p.get("id", p.get("player_id", "?"))
            style = p.get("classified_style", "unknown")
            trend = p.get("behavior_trend", "unknown")
            parts.append(f"  P{pid}: {style} ({trend})")

        return "\n".join(parts)


# ═══════════════════════════════════════════════════════════
# Agent 9: Tactical Planner (LLM-backed)
# ═══════════════════════════════════════════════════════════

TACTICAL_PLANNER_PROMPT = """\
You are the TACTICAL PLANNER for OPFOR forces. You translate strategic directives into specific unit orders.

You receive:
1. Strategic commander's phase and objectives
2. Intelligence briefings from staff officers
3. Current unit positions and status

RULES:
- Issue SPECIFIC commands for SPECIFIC units
- Use combined arms: suppress + flank, fix + destroy
- Use safe approach routes from intelligence
- When SPAWN ANCHORS are provided, prefer those exact coordinates for new spawns, checkpoints, mines, and roadblocks
- OBEY difficulty restrictions (forbidden/preferred commands)
- Critical units MUST retreat (force preservation agent already generated retreat commands — do not override)
- Use diverse commands — do NOT spam the same command type
- Maximum {max_commands} commands per response

Respond with ONLY this JSON:
{{
  "commands": [
    {{
      "command_type": "...",
      "unit_id": "squad_X" or "new",
      "target_position": "x,0,z",
      "parameters": "...",
      "narrative": "...",
      "reasoning": "...",
      "formation": "",
      "combat_mode": "",
      "speed_mode": "",
      "stance": ""
    }}
  ]
}}

POSITION FORMAT: "x,y,z" — y=0. Use positions from the briefing.
IMPORTANT: Do NOT wrap in markdown fences. Output raw JSON only."""


TACTICAL_PLANNER_ADVANCED = """
=== AVAILABLE COMMANDS ===
MOVEMENT: move, attack, defend, patrol, set_ambush, seek_and_destroy, retreat
ADVANCED: suppress, overwatch, silent_move, garrison, establish_op, fighting_withdrawal
FLANKING: flank_left, flank_right, breach, bounding_overwatch (params: partner=squad_X)
VEHICLE: hull_down, dismount_assault, convoy_form
SPAWNING: spawn_infantry (params: ussr_rifle_squad | ussr_fire_team | ussr_mg_team | ussr_at_team | ussr_sniper_team), spawn_vehicle (params: uaz469 | uaz469_mg | btr70 | ural_truck | bmp1 | t72)
ENVIRONMENT: set_weather, set_time, artillery_barrage, building_fire
NARRATIVE: radio_comms (params: channel, sender_callsign, message), intel_feed, intercepted_comms (params: channel, sender_callsign, message), faction_comms
CLEANUP: cleanup_dead, despawn_unit

ANCHOR USAGE:
- neutral_base anchors = reinforcement staging, garrison, occupation, reserve assembly
- road_junction anchors = checkpoints, ambushes, convoy interdiction, screening forces
- roadblock_candidate anchors = set_roadblock, lay_mines, vehicle denial, choke-point defense
- When anchors are present, copy their coordinates exactly into target_position
"""


class TacticalPlannerAgent(BaseAgent):
    """LLM-backed squad-level tactical planning."""

    name = "tactical_planner"
    role = "Unit-level command generation, coordinated maneuvers"
    phase = AgentPhase.DECISION
    requires_llm = True

    async def process(self, ctx: AgentContext) -> AgentResult:
        result = self._empty_result()

        # Build detailed tactical briefing
        briefing = self._build_tactical_briefing(ctx)

        max_commands = 12
        prompt = TACTICAL_PLANNER_PROMPT.format(max_commands=max_commands) + TACTICAL_PLANNER_ADVANCED

        result.metadata = {
            "llm_prompt": prompt,
            "llm_message": briefing,
            "agent_role": "tactical",
        }

        return result

    def _build_tactical_briefing(self, ctx: AgentContext) -> str:
        parts = ["=== TACTICAL BRIEFING ==="]

        # Strategic commander's guidance
        strat = ctx.agent_results.get("strategic_commander")
        if strat and strat.metadata.get("llm_response"):
            parts.append(f"COMMANDER'S INTENT: {json.dumps(strat.metadata['llm_response'])}")

        # Current units and their status
        enemy_units = ctx.game_state.get("enemy_units", [])
        parts.append(f"\nUNITS ({len(enemy_units)}):")
        for unit in enemy_units:
            uid = unit.get("unit_id", "?")
            hp = unit.get("health_pct", 1.0)
            men = unit.get("unit_count", unit.get("member_count", "?"))
            pos = unit.get("position", [0, 0, 0])
            status = unit.get("order_status", "idle")
            kills = unit.get("kills", 0)
            utype = unit.get("type", "unknown")
            pos_str = f"{pos[0]:.0f},0,{pos[2] if len(pos) > 2 else 0:.0f}"
            parts.append(f"  {uid} ({utype}): {hp:.0%} HP, {men} men, at {pos_str}, "
                         f"status={status}, kills={kills}")

        # Safe routes from threat assessment
        ta = ctx.agent_results.get("threat_assessment")
        if ta:
            routes = ta.intel_updates.get("safe_routes", [])
            if routes:
                parts.append("\nSAFE APPROACH ROUTES:")
                for r in routes[:8]:
                    parts.append(f"  {r['unit_id']}: {r['approach']} via {r['waypoint']} "
                                 f"(threat: {r['threat_score']})")

            forecasts = ta.intel_updates.get("engagement_forecasts", [])
            if forecasts:
                parts.append("\nENGAGEMENT FORECASTS:")
                for f in forecasts[:6]:
                    parts.append(f"  {f['unit_id']} vs P{f['vs_player']}: "
                                 f"{f['ai_win_probability']:.0%} win chance -> {f['recommendation']}")

        # Difficulty restrictions
        diff = ctx.agent_results.get("difficulty")
        if diff:
            forbidden = diff.intel_updates.get("forbidden_commands", [])
            preferred = diff.intel_updates.get("preferred_commands", [])
            if forbidden:
                parts.append(f"\nFORBIDDEN COMMANDS: {', '.join(forbidden)}")
            if preferred:
                parts.append(f"PREFERRED COMMANDS: {', '.join(preferred)}")

        # Force preservation: critical units already retreating
        fp = ctx.agent_results.get("force_preservation")
        if fp:
            critical = fp.intel_updates.get("critical_units", [])
            if critical:
                parts.append(f"\nAUTO-RETREATING (do not override): {', '.join(critical)}")
            merges = fp.intel_updates.get("merge_suggestions", [])
            for m in merges:
                parts.append(f"MERGE SUGGESTION: {m['suggestion']}")

        # Logistics: what to spawn
        logi = ctx.agent_results.get("logistics")
        if logi:
            priorities = logi.intel_updates.get("spawn_priorities", [])
            if priorities:
                parts.append("\nSPAWN PRIORITIES:")
                for sp in priorities:
                    parts.append(f"  {sp['type']} ({sp['priority']}): {sp['reason']}")

        anchors = list(ctx.game_state.get("spawn_anchors", []))
        if anchors:
            parts.append("\nSPAWN ANCHORS:")
            anchors.sort(key=lambda anchor: float(anchor.get("priority", 0) or 0), reverse=True)
            for anchor in anchors[:12]:
                pos = anchor.get("position", [0, 0, 0])
                radius = float(anchor.get("tactical_radius", 0) or 0)
                priority = float(anchor.get("priority", 0) or 0)
                name = anchor.get("name", anchor.get("anchor_id", "anchor"))
                anchor_type = anchor.get("anchor_type", "anchor")
                control = anchor.get("control_state", "unassigned")
                notes = anchor.get("notes", "")
                pos_str = f"{pos[0]:.0f},0,{pos[2] if len(pos) > 2 else 0:.0f}"
                line = f"  {name} [{anchor_type}/{control}] at {pos_str}"
                if radius > 0:
                    line += f" radius={radius:.0f}m"
                if priority > 0:
                    line += f" priority={priority:.2f}"
                if notes:
                    line += f" — {notes}"
                parts.append(line)

        # Environment recommendations
        env = ctx.agent_results.get("environment")
        if env:
            recs = env.intel_updates.get("environment_recommendations", [])
            for r in recs:
                parts.append(f"ENVIRONMENT: {r['suggestion']} [{r['priority']}]")

        # Player positions
        players = ctx.game_state.get("players", [])
        if players:
            parts.append(f"\nPLAYERS ({len(players)}):")
            for p in players:
                pid = p.get("id", p.get("player_id", "?"))
                pos = p.get("position", [0, 0, 0])
                style = p.get("classified_style", "unknown")
                velocity = p.get("velocity", [0, 0, 0])
                speed = 0.0
                if isinstance(velocity, (list, tuple)) and len(velocity) >= 3:
                    speed = math.sqrt(float(velocity[0]) ** 2 + float(velocity[2]) ** 2)
                heading = float(p.get("heading", 0) or 0)
                trail = p.get("position_trail", [])
                pos_str = f"{pos[0]:.0f},0,{pos[2] if len(pos) > 2 else 0:.0f}"
                line = f"  P{pid}: {style} at {pos_str} (speed={speed:.1f}"
                if heading:
                    line += f", heading={heading:.0f}"
                line += ")"
                if trail:
                    line += f" trail_pts={len(trail)}"
                parts.append(line)

        # Death zones to avoid
        intel_agent = ctx.agent_results.get("intelligence")
        if intel_agent:
            dz = intel_agent.intel_updates.get("death_zones", [])
            for zone in dz:
                parts.append(f"DEATH ZONE: {zone['center']} ({zone['deaths']} losses) — AVOID")

        return "\n".join(parts)


# ═══════════════════════════════════════════════════════════
# Agent 10: Narrative Director (LLM-backed)
# ═══════════════════════════════════════════════════════════

NARRATIVE_DIRECTOR_PROMPT = """\
You are the NARRATIVE DIRECTOR for a military simulation. Create immersive radio communications and atmosphere.

Your job is to make the battlefield feel alive with:
1. Radio chatter between AI units (using proper military comms procedure)
2. Intelligence feeds revealing enemy movement
3. Intercepted enemy transmissions (adding tension)
4. Atmospheric narrative broadcasts

RULES:
- Use proper military radio procedure (callsigns, brevity codes)
- Match tone to the tactical situation (desperate when losing, confident when winning)
- Keep messages SHORT (1-2 sentences each)
- Generate 2-4 narrative commands maximum
- Use these callsigns: Krasny, Sokol, Buran, Shtorm (Soviet), Overlord, Warlord (Command)

Respond with ONLY this JSON:
{
  "commands": [
    {
      "command_type": "radio_comms",
      "unit_id": "stavka",
      "target_position": "",
      "parameters": "channel=command_net,sender_callsign=X,message=Y",
      "narrative": "...",
      "reasoning": "atmosphere"
    }
  ],
  "narrative_broadcast": "A brief atmospheric description of the current battle state"
}
IMPORTANT: Do NOT wrap in markdown fences. Output raw JSON only."""


class NarrativeDirectorAgent(BaseAgent):
    """LLM-backed narrative and atmosphere management."""

    name = "narrative_director"
    role = "Radio comms, atmosphere, immersion, pacing"
    phase = AgentPhase.DECISION
    requires_llm = True

    async def process(self, ctx: AgentContext) -> AgentResult:
        result = self._empty_result()

        briefing = self._build_narrative_briefing(ctx)

        result.metadata = {
            "llm_prompt": NARRATIVE_DIRECTOR_PROMPT,
            "llm_message": briefing,
            "agent_role": "narrative",
        }

        return result

    def _build_narrative_briefing(self, ctx: AgentContext) -> str:
        parts = ["=== SITUATION FOR NARRATIVE ==="]

        mission_time = ctx.game_state.get("mission_time_seconds", 0)
        weather = ctx.game_state.get("weather", "Clear")
        tod = ctx.game_state.get("time_of_day", 12.0)

        parts.append(f"Time: {mission_time:.0f}s, Weather: {weather}, "
                     f"{'NIGHT' if (tod < 5 or tod > 20) else 'DAY'}")

        phase = ctx.battle_plan.current_phase if hasattr(ctx.battle_plan, "current_phase") else "probing"
        parts.append(f"Battle phase: {phase}")

        # Force status for tone
        fp = ctx.agent_results.get("force_preservation")
        if fp:
            fs = fp.intel_updates.get("force_status", {})
            parts.append(f"Force condition: {fs.get('combat_effective', 0)}/{fs.get('total_units', 0)} effective, "
                         f"morale: {fs.get('avg_morale', 0):.0%}")
            if fs.get("critical", 0) > 0:
                parts.append(f"CASUALTIES: {fs['critical']} units in critical condition")

        # Recent events
        enemy_units = ctx.game_state.get("enemy_units", [])
        engaged = [u for u in enemy_units if u.get("is_engaging")]
        if engaged:
            parts.append(f"CONTACT: {len(engaged)} units actively engaged")

        players = ctx.game_state.get("players", [])
        parts.append(f"Enemy: {len(players)} players detected")

        diff = ctx.difficulty_params
        if diff.get("mercy_active"):
            parts.append("TONE: Cautious, observational — we are in mercy mode")

        return "\n".join(parts)


# ═══════════════════════════════════════════════════════════
# Agent Orchestrator
# ═══════════════════════════════════════════════════════════

class AgentOrchestrator:
    """Coordinates all 10 agents in a phased pipeline.

    Phase 1 (parallel, code-based):
        Intelligence, ThreatAssessment, Difficulty, ForcePreservation, Environment, Logistics
    Phase 1b (code-based, after threat assessment):
        StrategicCommander (derives phase from game state, no LLM call)
    Phase 2 (parallel, LLM-based — 2 API calls):
        TacticalPlanner, NarrativeDirector
    Phase 3 (async post-cycle):
        LearningEngine
    """

    def __init__(self, llm_caller=None):
        """
        Args:
            llm_caller: async function(system_prompt, user_message) -> dict
                        Used by LLM-backed agents to make API calls.
        """
        # Initialize all 10 agents
        self.agents = {
            "intelligence": IntelligenceAgent(),
            "threat_assessment": ThreatAssessmentAgent(),
            "difficulty": DifficultyAgent(),
            "force_preservation": ForcePreservationAgent(),
            "environment": EnvironmentAgent(),
            "logistics": LogisticsAgent(),
            "strategic_commander": StrategicCommanderAgent(),
            "tactical_planner": TacticalPlannerAgent(),
            "narrative_director": NarrativeDirectorAgent(),
            "learning_engine": LearningEngine(),
        }
        self.llm_caller = llm_caller
        self._turn_count = 0
        self._cycle_times: deque = deque(maxlen=50)

    async def run_cycle(
        self,
        game_state: dict,
        tactical_intel: dict,
        battle_plan: object,
        difficulty_params: dict,
        session_id: str = "default",
    ) -> dict:
        """Run the full agent pipeline and return merged command batch."""
        cycle_start = time.time()
        self._turn_count += 1

        ctx = AgentContext(
            game_state=game_state,
            tactical_intel=tactical_intel,
            battle_plan=battle_plan,
            difficulty_params=difficulty_params,
            session_id=session_id,
            turn_number=self._turn_count,
        )

        # ── Phase 1: Code-based pre-processing (parallel) ──
        phase1_agents = ["intelligence", "difficulty", "force_preservation",
                         "environment", "logistics"]
        phase1_tasks = [self._run_agent(name, ctx) for name in phase1_agents]
        phase1_results = await asyncio.gather(*phase1_tasks, return_exceptions=True)

        for name, result in zip(phase1_agents, phase1_results):
            if isinstance(result, Exception):
                log.warning("Agent %s failed in phase 1: %s", name, result)
                ctx.agent_results[name] = AgentResult(agent_name=name)
            else:
                ctx.agent_results[name] = result

        # Threat assessment depends on intelligence results
        try:
            threat_result = await self._run_agent("threat_assessment", ctx)
            ctx.agent_results["threat_assessment"] = threat_result
        except Exception as e:
            log.warning("Threat assessment agent failed: %s", e)
            ctx.agent_results["threat_assessment"] = AgentResult(agent_name="threat_assessment")

        # Strategic commander is now code-only (no LLM call) — runs after threat assessment
        try:
            strat_result = await self._run_agent("strategic_commander", ctx)
            ctx.agent_results["strategic_commander"] = strat_result
        except Exception as e:
            log.warning("Strategic commander failed: %s", e)
            ctx.agent_results["strategic_commander"] = AgentResult(agent_name="strategic_commander")

        # ── Phase 2: LLM-based decision-making (parallel, 2 agents, 90s timeout) ──
        # GLM (glm-4.7) can take 40-70s per call; 90s accommodates slow responses.
        if self.llm_caller:
            phase2_agents = ["tactical_planner", "narrative_director"]
            phase2_tasks = [self._run_llm_agent(name, ctx) for name in phase2_agents]
            phase2_timed_out = False
            try:
                phase2_results = await asyncio.wait_for(
                    asyncio.gather(*phase2_tasks, return_exceptions=True),
                    timeout=90,
                )
            except asyncio.TimeoutError:
                log.warning("Phase 2 LLM agents timed out after 90s — using tactical fallback")
                phase2_results = [asyncio.TimeoutError() for _ in phase2_agents]
                phase2_timed_out = True

            for name, result in zip(phase2_agents, phase2_results):
                if isinstance(result, Exception):
                    log.warning("LLM agent %s failed in phase 2: %s", name, result)
                    if name == "tactical_planner":
                        # Fallback: spawn 1-2 squads near players so the game
                        # keeps moving even when the LLM is too slow.
                        ctx.agent_results[name] = self._phase2_tactical_fallback(ctx)
                    else:
                        ctx.agent_results[name] = AgentResult(agent_name=name)
                else:
                    ctx.agent_results[name] = result

            # If phase 2 timed out entirely AND tactical_planner still has no
            # commands, inject the fallback now.
            if phase2_timed_out:
                tp = ctx.agent_results.get("tactical_planner")
                if tp is None or not tp.commands:
                    ctx.agent_results["tactical_planner"] = self._phase2_tactical_fallback(ctx)

        # ── Phase 3: Learning (post-process, async) ──
        learning_result = None
        try:
            learning_result = await self._run_agent("learning_engine", ctx)
            ctx.agent_results["learning_engine"] = learning_result

            # Apply lessons to battle plan
            if hasattr(battle_plan, "add_lesson"):
                for lesson in learning_result.lessons:
                    battle_plan.add_lesson(lesson)
        except Exception as e:
            log.warning("Learning engine failed: %s", e)

        # ── Merge all results into final command batch ──
        merged = self._merge_results(ctx)

        cycle_time = time.time() - cycle_start
        self._cycle_times.append(cycle_time)
        log.info("Agent pipeline complete: %.1fs | agents: %d | commands: %d | lessons: %d",
                 cycle_time, len(ctx.agent_results),
                 len(merged.get("commands", [])),
                 len(learning_result.lessons) if learning_result else 0)

        # Add agent metadata to response
        merged["_agent_metadata"] = {
            "cycle_time_ms": round(cycle_time * 1000),
            "agents_run": list(ctx.agent_results.keys()),
            "new_lessons": learning_result.lessons if learning_result else [],
            "turn": self._turn_count,
        }

        return merged

    async def _run_agent(self, name: str, ctx: AgentContext) -> AgentResult:
        """Run a code-based agent."""
        agent = self.agents[name]
        start = time.time()
        result = await agent.process(ctx)
        elapsed = time.time() - start
        log.debug("Agent %s completed in %.1fms", name, elapsed * 1000)
        return result

    async def _run_llm_agent(self, name: str, ctx: AgentContext) -> AgentResult:
        """Run an LLM-backed agent: first get its briefing, then call LLM."""
        agent = self.agents[name]
        result = await agent.process(ctx)

        if not self.llm_caller:
            return result

        prompt = result.metadata.get("llm_prompt", "")
        message = result.metadata.get("llm_message", "")

        if not prompt or not message:
            return result

        try:
            llm_response = await self.llm_caller(prompt, message)
            result.metadata["llm_response"] = llm_response

            # Extract commands from LLM response
            if isinstance(llm_response, dict):
                result.commands = llm_response.get("commands", [])
                if llm_response.get("narrative_broadcast"):
                    result.narrative.append(llm_response["narrative_broadcast"])
        except Exception as e:
            log.warning("LLM call for agent %s failed: %s", name, e)

        return result

    def _phase2_tactical_fallback(self, ctx: AgentContext) -> AgentResult:
        """Generate basic tactical commands when Phase 2 times out.

        Spawns 1-2 OPFOR squads near the closest player so the battle keeps
        going even when the LLM is unreachable or too slow.
        """
        commands = []
        players = ctx.game_state.get("players", [])
        if not players:
            log.warning("Phase 2 fallback: no players in game_state — returning empty result")
            return AgentResult(agent_name="tactical_planner")

        # Pick the first player as spawn anchor
        anchor = players[0]
        px, pz = 0.0, 0.0
        pos = anchor.get("position")
        if isinstance(pos, (list, tuple)) and len(pos) >= 2:
            px, pz = float(pos[0]), float(pos[2]) if len(pos) > 2 else float(pos[1])
        elif isinstance(pos, str):
            parts = pos.split(",")
            if len(parts) >= 2:
                px = float(parts[0])
                pz = float(parts[2]) if len(parts) > 2 else float(parts[1])

        opfor_types = ["ussr_rifle_squad", "ussr_fire_team"]
        num_squads = random.randint(1, 2)

        for i in range(num_squads):
            angle = random.uniform(0, 360)
            dist = random.uniform(200, 400)
            sx = px + math.sin(math.radians(angle)) * dist
            sz = pz + math.cos(math.radians(angle)) * dist
            commands.append({
                "command_type": "spawn_infantry",
                "unit_id": "new",
                "target_position": f"{sx:.0f},0,{sz:.0f}",
                "parameters": random.choice(opfor_types),
                "reasoning": f"Phase 2 timeout fallback: OPFOR squad {i + 1} near player.",
            })

        log.info("Phase 2 fallback generated %d spawn commands near player at %.0f,%.0f",
                 len(commands), px, pz)
        return AgentResult(agent_name="tactical_planner", commands=commands)

    def _merge_results(self, ctx: AgentContext) -> dict:
        """Merge all agent results into a single command batch."""
        all_commands = []
        narrative_broadcast = ""
        weather_command = ""
        time_override = -1

        # Priority order for command merging:
        # 1. Force preservation (retreat commands for critical units)
        fp = ctx.agent_results.get("force_preservation")
        if fp:
            all_commands.extend(fp.commands)
            retreating_units = set(c.get("unit_id") for c in fp.commands)
        else:
            retreating_units = set()

        # 2. Tactical planner commands (main orders)
        tp = ctx.agent_results.get("tactical_planner")
        if tp:
            for cmd in tp.commands:
                # Don't override retreat commands for critical units
                if cmd.get("unit_id") not in retreating_units:
                    all_commands.append(cmd)

        # 3. Narrative commands
        nd = ctx.agent_results.get("narrative_director")
        if nd:
            all_commands.extend(nd.commands)
            if nd.narrative:
                narrative_broadcast = nd.narrative[0]

        # Extract weather/time from tactical planner or environment recommendations
        for cmd in all_commands:
            if cmd.get("command_type") == "set_weather":
                weather_command = cmd.get("parameters", "")
            elif cmd.get("command_type") == "set_time":
                try:
                    time_override = float(cmd.get("parameters", -1))
                except (ValueError, TypeError):
                    pass

        return {
            "commands": all_commands,
            "narrative_broadcast": narrative_broadcast,
            "weather_command": weather_command,
            "time_override": time_override,
        }

    def get_agent_status(self) -> dict:
        """Return status of all agents for dashboard."""
        return {
            "agents": {
                name: {
                    "name": agent.name,
                    "role": agent.role,
                    "phase": agent.phase.value,
                    "requires_llm": agent.requires_llm,
                }
                for name, agent in self.agents.items()
            },
            "turn_count": self._turn_count,
            "avg_cycle_time_ms": (
                round(sum(self._cycle_times) / len(self._cycle_times) * 1000, 1)
                if self._cycle_times else 0
            ),
        }


# ═══════════════════════════════════════════════════════════
# Fallback: Single-Agent Mode
# ═══════════════════════════════════════════════════════════

async def create_orchestrator(llm_caller=None) -> AgentOrchestrator:
    """Factory function to create and configure the orchestrator."""
    orchestrator = AgentOrchestrator(llm_caller=llm_caller)
    log.info("Agent orchestrator initialized with %d agents", len(orchestrator.agents))
    return orchestrator
