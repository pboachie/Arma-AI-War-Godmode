"""
scaling_engine.py — Multiplayer Scaling Engine for God Mode v2

Implements all eight scaling subsystems:
  1. Player-aware zone system
  2. LOD-style AI management
  3. Performance budgeting
  4. Player spread handling
  5. Coop team scaling
  6. Reinforcement wave design
  7. Server tick rate considerations
  8. Entity pooling and recycling

This module is imported by server.py and provides the ScalingEngine class
that wraps around the existing TacticalAnalyzer and BattlePlan systems.
"""

import json
import math
import time
import logging
import random
from collections import defaultdict, deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

log = logging.getLogger("godmode.scaling")


# ═══════════════════════════════════════════════════════════
# 1. ZONE SYSTEM
# ═══════════════════════════════════════════════════════════

class ZoneState(Enum):
    HOT = "hot"       # Full simulation — player present or adjacent
    WARM = "warm"     # Reduced simulation — near player zones
    COLD = "cold"     # No simulation — entities pooled after delay


@dataclass
class MapZone:
    grid_x: int
    grid_z: int
    state: ZoneState = ZoneState.COLD
    last_state_change: float = 0.0
    entity_count: int = 0
    vehicle_count: int = 0
    player_ids: list = field(default_factory=list)

    @property
    def center_world(self) -> tuple:
        """Return world-space center of this grid cell."""
        cell_size = 500  # meters, matches config
        return (
            self.grid_x * cell_size + cell_size / 2,
            self.grid_z * cell_size + cell_size / 2,
        )

    @property
    def cell_key(self) -> str:
        return f"{self.grid_x},{self.grid_z}"


class ZoneManager:
    """Divides the map into grid cells and tracks activation state.

    Algorithm:
      1. Each tick, compute which grid cells contain players.
      2. Those cells + cells within activation_radius are HOT.
      3. Next ring out is WARM.
      4. Everything else is COLD after hysteresis delay.
    """

    def __init__(self, config: dict):
        zc = config.get("zone_system", {})
        self.cell_size = zc.get("grid_cell_size_meters", 500)
        bounds = zc.get("map_bounds", {})
        self.min_x = bounds.get("min_x", 0)
        self.min_z = bounds.get("min_z", 0)
        self.max_x = bounds.get("max_x", 12800)
        self.max_z = bounds.get("max_z", 12800)
        self.activation_radius = zc.get("activation_radius_cells", 2)
        self.hysteresis = zc.get("zone_transition_hysteresis_seconds", 30)

        self.grid_width = math.ceil((self.max_x - self.min_x) / self.cell_size)
        self.grid_height = math.ceil((self.max_z - self.min_z) / self.cell_size)
        self.zones: dict[str, MapZone] = {}

    def _pos_to_cell(self, x: float, z: float) -> tuple:
        gx = max(0, min(self.grid_width - 1,
                        int((x - self.min_x) / self.cell_size)))
        gz = max(0, min(self.grid_height - 1,
                        int((z - self.min_z) / self.cell_size)))
        return gx, gz

    def _get_zone(self, gx: int, gz: int) -> MapZone:
        key = f"{gx},{gz}"
        if key not in self.zones:
            self.zones[key] = MapZone(grid_x=gx, grid_z=gz)
        return self.zones[key]

    def _cells_in_radius(self, gx: int, gz: int, radius: int):
        """Yield all (gx, gz) within Chebyshev distance radius."""
        for dx in range(-radius, radius + 1):
            for dz in range(-radius, radius + 1):
                nx, nz = gx + dx, gz + dz
                if 0 <= nx < self.grid_width and 0 <= nz < self.grid_height:
                    yield nx, nz

    def update(self, players: list) -> dict:
        """Recalculate zone states based on current player positions.

        Returns a summary dict with zone counts and entity recommendations.
        """
        now = time.time()

        # Step 1: Find player-occupied cells
        player_cells = set()
        for p in players:
            pos = p.get("position", [0, 0, 0])
            x = pos[0] if len(pos) > 0 else 0
            z = pos[2] if len(pos) > 2 else 0
            gx, gz = self._pos_to_cell(x, z)
            player_cells.add((gx, gz))
            zone = self._get_zone(gx, gz)
            pid = p.get("player_id", 0)
            if pid not in zone.player_ids:
                zone.player_ids.append(pid)

        # Step 2: Compute HOT cells (player cells + activation radius)
        hot_cells = set()
        for gx, gz in player_cells:
            for cx, cz in self._cells_in_radius(gx, gz, self.activation_radius):
                hot_cells.add((cx, cz))

        # Step 3: Compute WARM cells (one ring beyond HOT)
        warm_cells = set()
        for gx, gz in hot_cells:
            for cx, cz in self._cells_in_radius(gx, gz, 1):
                if (cx, cz) not in hot_cells:
                    warm_cells.add((cx, cz))

        # Step 4: Update zone states with hysteresis
        for key, zone in self.zones.items():
            cell = (zone.grid_x, zone.grid_z)
            old_state = zone.state

            if cell in player_cells:
                new_state = ZoneState.HOT
            elif cell in hot_cells:
                new_state = ZoneState.HOT
            elif cell in warm_cells:
                new_state = ZoneState.WARM
            else:
                new_state = ZoneState.COLD

            # Apply hysteresis: only downgrade if enough time has passed
            if new_state.value < old_state.value:  # downgrade
                if now - zone.last_state_change < self.hysteresis:
                    continue  # Keep current state
            if new_state != old_state:
                zone.state = new_state
                zone.last_state_change = now
                zone.player_ids = []

        # Step 5: Ensure HOT/WARM cells exist in the grid
        for gx, gz in hot_cells:
            zone = self._get_zone(gx, gz)
            if zone.state != ZoneState.HOT:
                zone.state = ZoneState.HOT
                zone.last_state_change = now

        for gx, gz in warm_cells:
            zone = self._get_zone(gx, gz)
            if zone.state == ZoneState.COLD:
                zone.state = ZoneState.WARM
                zone.last_state_change = now

        # Build summary
        hot_count = sum(1 for z in self.zones.values() if z.state == ZoneState.HOT)
        warm_count = sum(1 for z in self.zones.values() if z.state == ZoneState.WARM)
        cold_count = sum(1 for z in self.zones.values() if z.state == ZoneState.COLD)

        return {
            "hot_zones": hot_count,
            "warm_zones": warm_count,
            "cold_zones": cold_count,
            "player_cells": len(player_cells),
            "total_tracked": len(self.zones),
        }

    def get_zone_for_position(self, x: float, z: float) -> MapZone:
        gx, gz = self._pos_to_cell(x, z)
        return self._get_zone(gx, gz)

    def get_zones_by_state(self, state: ZoneState) -> list:
        return [z for z in self.zones.values() if z.state == state]

    def get_spawn_zones(self) -> list:
        """Return WARM zones suitable for spawning (close enough to be relevant,
        far enough to not pop in front of players)."""
        return [z for z in self.zones.values()
                if z.state == ZoneState.WARM]


# ═══════════════════════════════════════════════════════════
# 2. AI LOD MANAGEMENT
# ═══════════════════════════════════════════════════════════

class AILodTier(Enum):
    FULL = "full"
    REDUCED = "reduced"
    DORMANT = "dormant"
    POOLED = "pooled"


@dataclass
class AIEntityState:
    """Tracked state for an AI entity across LOD transitions."""
    unit_id: str
    unit_type: str
    faction: str
    position: list
    strength: int = 8
    health_pct: float = 1.0
    current_order: str = "idle"
    kills: int = 0
    waypoints: list = field(default_factory=list)
    lod_tier: AILodTier = AILodTier.FULL
    last_tier_change: float = 0.0
    pooled_at: float = 0.0


class AILodManager:
    """Manages LOD tier assignments for all AI entities.

    Algorithm:
      For each AI entity, compute minimum distance to any player.
      Assign LOD tier based on distance thresholds.
      Apply hysteresis buffer to prevent oscillation.
    """

    def __init__(self, config: dict):
        lod_cfg = config.get("ai_lod", {})
        tiers = lod_cfg.get("tiers", {})
        self.buffer = lod_cfg.get("transition_buffer_meters", 50)

        self.tier_ranges = {
            AILodTier.FULL: tiers.get("full", {}).get("range_meters", 300),
            AILodTier.REDUCED: tiers.get("reduced", {}).get("range_meters", 800),
            AILodTier.DORMANT: tiers.get("dormant", {}).get("range_meters", 1500),
            AILodTier.POOLED: 999999,
        }

        self.tier_update_rates = {
            AILodTier.FULL: tiers.get("full", {}).get("update_rate_hz", 10),
            AILodTier.REDUCED: tiers.get("reduced", {}).get("update_rate_hz", 2),
            AILodTier.DORMANT: tiers.get("dormant", {}).get("update_rate_hz", 0.2),
            AILodTier.POOLED: 0,
        }

        self.entities: dict[str, AIEntityState] = {}

    def register_entity(self, entity: AIEntityState):
        self.entities[entity.unit_id] = entity

    def remove_entity(self, unit_id: str) -> Optional[AIEntityState]:
        return self.entities.pop(unit_id, None)

    def _distance_to_nearest_player(self, pos: list, players: list) -> float:
        min_dist = float("inf")
        for p in players:
            p_pos = p.get("position", [0, 0, 0])
            dx = pos[0] - p_pos[0]
            dz = (pos[2] - p_pos[2]) if len(pos) > 2 and len(p_pos) > 2 else 0
            dist = math.sqrt(dx * dx + dz * dz)
            min_dist = min(min_dist, dist)
        return min_dist

    def _tier_for_distance(self, dist: float, current_tier: AILodTier) -> AILodTier:
        """Determine appropriate tier with hysteresis.

        When upgrading (moving closer), use exact boundary.
        When downgrading (moving farther), require distance + buffer.
        """
        ordered_tiers = [AILodTier.FULL, AILodTier.REDUCED,
                         AILodTier.DORMANT, AILodTier.POOLED]
        current_idx = ordered_tiers.index(current_tier)

        for i, tier in enumerate(ordered_tiers):
            threshold = self.tier_ranges[tier]
            if i < current_idx:
                # Upgrading — use exact threshold
                if dist <= threshold:
                    return tier
            elif i == current_idx:
                # Same tier — check if we should downgrade
                if dist <= threshold + self.buffer:
                    return tier
            else:
                # Downgrading — need to exceed threshold + buffer
                if dist <= threshold + self.buffer:
                    return tier

        return AILodTier.POOLED

    def update(self, players: list) -> dict:
        """Recalculate LOD tiers for all entities.

        Returns tier distribution summary.
        """
        now = time.time()
        tier_counts = defaultdict(int)
        transitions = []

        for uid, entity in self.entities.items():
            dist = self._distance_to_nearest_player(entity.position, players)
            new_tier = self._tier_for_distance(dist, entity.lod_tier)

            if new_tier != entity.lod_tier:
                old_tier = entity.lod_tier
                entity.lod_tier = new_tier
                entity.last_tier_change = now
                if new_tier == AILodTier.POOLED:
                    entity.pooled_at = now
                transitions.append({
                    "unit_id": uid,
                    "from": old_tier.value,
                    "to": new_tier.value,
                    "distance": round(dist, 1),
                })

            tier_counts[entity.lod_tier.value] += 1

        return {
            "tier_distribution": dict(tier_counts),
            "transitions": transitions,
            "total_entities": len(self.entities),
        }

    def get_entities_in_tier(self, tier: AILodTier) -> list:
        return [e for e in self.entities.values() if e.lod_tier == tier]

    def get_simulation_cost_estimate(self) -> float:
        """Estimate relative CPU cost of current entity distribution.

        Returns a value where 1.0 = cost of one fully simulated entity.
        """
        cost_weights = {
            AILodTier.FULL: 1.0,
            AILodTier.REDUCED: 0.25,
            AILodTier.DORMANT: 0.02,
            AILodTier.POOLED: 0.0,
        }
        total = 0.0
        for entity in self.entities.values():
            total += cost_weights[entity.lod_tier]
        return total


# ═══════════════════════════════════════════════════════════
# 3. PERFORMANCE BUDGET
# ═══════════════════════════════════════════════════════════

class PerformanceTier(Enum):
    HEALTHY = "healthy"
    STRESSED = "stressed"
    CRITICAL = "critical"
    EMERGENCY = "emergency"


@dataclass
class PerformanceBudget:
    """Tracks server FPS and computes dynamic limits.

    Uses EWMA (exponentially weighted moving average) for smooth FPS tracking
    to prevent rapid oscillation between tiers.
    """

    target_fps: float = 30.0
    ewma_alpha: float = 0.3
    sample_window: float = 10.0

    def __post_init__(self):
        self.fps_history: deque = deque(maxlen=100)
        self.ewma_fps: float = self.target_fps
        self.current_tier: PerformanceTier = PerformanceTier.HEALTHY
        self.last_sample_time: float = time.time()
        self.tier_entry_time: float = time.time()
        # Prevent flapping: require 5s in new tier before allowing change
        self.tier_change_cooldown: float = 5.0

    def record_fps(self, fps: float):
        """Record a new FPS sample and update EWMA."""
        now = time.time()
        self.fps_history.append((now, fps))
        self.ewma_fps = self.ewma_alpha * fps + (1.0 - self.ewma_alpha) * self.ewma_fps
        self.last_sample_time = now

    def _compute_tier(self) -> PerformanceTier:
        fps = self.ewma_fps
        if fps >= 30:
            return PerformanceTier.HEALTHY
        elif fps >= 20:
            return PerformanceTier.STRESSED
        elif fps >= 15:
            return PerformanceTier.CRITICAL
        else:
            return PerformanceTier.EMERGENCY

    def update(self) -> dict:
        """Evaluate current performance tier.

        Returns budget multipliers for AI count, vehicle count, spawn rate,
        and LLM update interval.
        """
        now = time.time()
        new_tier = self._compute_tier()

        # Apply cooldown to prevent flapping
        if new_tier != self.current_tier:
            if now - self.tier_entry_time >= self.tier_change_cooldown:
                old_tier = self.current_tier
                self.current_tier = new_tier
                self.tier_entry_time = now
                log.info("Performance tier changed: %s -> %s (EWMA FPS: %.1f)",
                         old_tier.value, new_tier.value, self.ewma_fps)

        # Compute budget multipliers
        budgets = {
            PerformanceTier.HEALTHY:   {"ai_pct": 1.0,  "veh_pct": 1.0,  "spawn_mult": 1.0,  "interval_mult": 1.0},
            PerformanceTier.STRESSED:  {"ai_pct": 0.7,  "veh_pct": 0.6,  "spawn_mult": 0.5,  "interval_mult": 1.5},
            PerformanceTier.CRITICAL:  {"ai_pct": 0.4,  "veh_pct": 0.3,  "spawn_mult": 0.0,  "interval_mult": 2.5},
            PerformanceTier.EMERGENCY: {"ai_pct": 0.25, "veh_pct": 0.2,  "spawn_mult": 0.0,  "interval_mult": 4.0},
        }

        budget = budgets[self.current_tier]
        return {
            "tier": self.current_tier.value,
            "ewma_fps": round(self.ewma_fps, 1),
            "ai_budget_pct": budget["ai_pct"],
            "vehicle_budget_pct": budget["veh_pct"],
            "spawn_rate_multiplier": budget["spawn_mult"],
            "llm_interval_modifier": budget["interval_mult"],
            "force_despawn_dormant": self.current_tier == PerformanceTier.EMERGENCY,
        }


# ═══════════════════════════════════════════════════════════
# 4. PLAYER SPREAD ANALYSIS
# ═══════════════════════════════════════════════════════════

def compute_player_spread(players: list) -> dict:
    """Analyze player spatial distribution.

    Returns spread metrics and the corresponding scaling modifiers.

    Algorithm:
      1. Compute average pairwise distance between all players.
      2. Compute centroid and max distance from centroid.
      3. Classify into spread tier.
      4. Return squad/vehicle modifiers.
    """
    if len(players) < 2:
        return {
            "avg_spread_m": 0,
            "max_spread_m": 0,
            "centroid": [0, 0, 0],
            "formation": "single" if players else "none",
            "squad_modifier": 1.0,
            "vehicle_modifier": 1.0,
            "cluster_count": len(players),
        }

    positions = []
    for p in players:
        pos = p.get("position", [0, 0, 0])
        positions.append((pos[0], pos[2] if len(pos) > 2 else 0))

    # Average pairwise distance
    total_dist = 0
    pairs = 0
    for i in range(len(positions)):
        for j in range(i + 1, len(positions)):
            dx = positions[i][0] - positions[j][0]
            dz = positions[i][1] - positions[j][1]
            total_dist += math.sqrt(dx * dx + dz * dz)
            pairs += 1
    avg_spread = total_dist / max(pairs, 1)

    # Centroid
    cx = sum(p[0] for p in positions) / len(positions)
    cz = sum(p[1] for p in positions) / len(positions)

    # Max distance from centroid
    max_spread = 0
    for p in positions:
        dx = p[0] - cx
        dz = p[1] - cz
        max_spread = max(max_spread, math.sqrt(dx * dx + dz * dz))

    # Cluster detection: simple single-linkage with 200m threshold
    clusters = _count_clusters(positions, threshold=200)

    # Classification
    if avg_spread < 100:
        formation = "clustered"
        sq_mod, veh_mod = 0.8, 0.7
    elif avg_spread < 300:
        formation = "grouped"
        sq_mod, veh_mod = 1.0, 1.0
    elif avg_spread < 600:
        formation = "spread"
        sq_mod, veh_mod = 1.3, 1.2
    else:
        formation = "dispersed"
        sq_mod, veh_mod = 1.5, 1.4

    return {
        "avg_spread_m": round(avg_spread, 1),
        "max_spread_m": round(max_spread, 1),
        "centroid": [round(cx, 1), 0, round(cz, 1)],
        "formation": formation,
        "squad_modifier": sq_mod,
        "vehicle_modifier": veh_mod,
        "cluster_count": clusters,
    }


def _count_clusters(positions: list, threshold: float) -> int:
    """Simple single-linkage clustering. Returns number of distinct groups."""
    if not positions:
        return 0
    n = len(positions)
    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for i in range(n):
        for j in range(i + 1, n):
            dx = positions[i][0] - positions[j][0]
            dz = positions[i][1] - positions[j][1]
            if math.sqrt(dx * dx + dz * dz) < threshold:
                union(i, j)

    return len(set(find(i) for i in range(n)))


# ═══════════════════════════════════════════════════════════
# 5. COOP TEAM SCALING
# ═══════════════════════════════════════════════════════════

class TeamCoordinationAnalyzer:
    """Detects how organized a player group is and adjusts difficulty.

    Metrics:
      - Movement correlation: are players moving in the same direction?
      - Spacing consistency: do players maintain formation?
      - Simultaneous engagement: do players shoot at the same time?
    """

    def __init__(self):
        self.heading_history: dict[int, deque] = defaultdict(
            lambda: deque(maxlen=20))
        self.position_history: dict[int, deque] = defaultdict(
            lambda: deque(maxlen=20))
        self.engagement_times: dict[int, deque] = defaultdict(
            lambda: deque(maxlen=30))
        self._last_update: float = 0

    def update(self, players: list, events: list) -> dict:
        """Analyze team coordination from player data and events.

        Returns a coordination score (0-1) and difficulty tier.
        """
        now = time.time()
        self._last_update = now

        # Record positions and headings
        for p in players:
            pid = p.get("player_id", 0)
            pos = p.get("position", [0, 0, 0])
            heading = p.get("heading", 0)
            self.position_history[pid].append((now, pos))
            self.heading_history[pid].append((now, heading))

        # Record engagement times
        for event in events:
            if isinstance(event, str) and "shots_fired:player_" in event:
                try:
                    pid_str = event.split(":")[1].replace("player_", "")
                    pid = int(pid_str)
                    self.engagement_times[pid].append(now)
                except (ValueError, IndexError):
                    pass

        if len(players) < 2:
            return {
                "coordination_score": 0.0,
                "team_tier": "random_group",
                "difficulty_multiplier": 0.8,
                "ai_aggression": "cautious",
                "metrics": {},
            }

        # Metric 1: Movement correlation
        movement_corr = self._compute_movement_correlation(players)

        # Metric 2: Spacing consistency
        spacing_score = self._compute_spacing_consistency()

        # Metric 3: Simultaneous engagement
        engagement_score = self._compute_simultaneous_engagement()

        # Weighted combined score
        coordination = (
            0.35 * movement_corr +
            0.35 * spacing_score +
            0.30 * engagement_score
        )
        coordination = max(0.0, min(1.0, coordination))

        # Determine tier
        if coordination < 0.3:
            tier = "random_group"
            diff_mult = 0.8
            aggression = "cautious"
        elif coordination < 0.6:
            tier = "casual_team"
            diff_mult = 1.0
            aggression = "balanced"
        elif coordination < 0.85:
            tier = "organized_team"
            diff_mult = 1.3
            aggression = "aggressive"
        else:
            tier = "elite_team"
            diff_mult = 1.6
            aggression = "ruthless"

        return {
            "coordination_score": round(coordination, 3),
            "team_tier": tier,
            "difficulty_multiplier": diff_mult,
            "ai_aggression": aggression,
            "metrics": {
                "movement_correlation": round(movement_corr, 3),
                "spacing_consistency": round(spacing_score, 3),
                "simultaneous_engagement": round(engagement_score, 3),
            },
        }

    def _compute_movement_correlation(self, players: list) -> float:
        """Cosine similarity of movement vectors between all player pairs."""
        vectors = []
        for p in players:
            pid = p.get("player_id", 0)
            history = list(self.position_history.get(pid, []))
            if len(history) < 2:
                continue
            # Movement vector from second-to-last to last position
            p1 = history[-2][1]
            p2 = history[-1][1]
            dx = p2[0] - p1[0]
            dz = (p2[2] - p1[2]) if len(p2) > 2 and len(p1) > 2 else 0
            mag = math.sqrt(dx * dx + dz * dz)
            if mag > 1.0:  # Must have actually moved
                vectors.append((dx / mag, dz / mag))

        if len(vectors) < 2:
            return 0.0

        # Average pairwise cosine similarity
        total = 0.0
        pairs = 0
        for i in range(len(vectors)):
            for j in range(i + 1, len(vectors)):
                dot = vectors[i][0] * vectors[j][0] + vectors[i][1] * vectors[j][1]
                total += max(0.0, dot)  # Clamp negative (opposite directions)
                pairs += 1
        return total / max(pairs, 1)

    def _compute_spacing_consistency(self) -> float:
        """Low std-dev of inter-player distance over time = good formation."""
        distance_samples = []
        pids = list(self.position_history.keys())
        if len(pids) < 2:
            return 0.0

        # Use last 10 samples
        for sample_idx in range(-1, -11, -1):
            sample_distances = []
            for i in range(len(pids)):
                hist_i = list(self.position_history[pids[i]])
                if len(hist_i) < abs(sample_idx):
                    continue
                for j in range(i + 1, len(pids)):
                    hist_j = list(self.position_history[pids[j]])
                    if len(hist_j) < abs(sample_idx):
                        continue
                    p1 = hist_i[sample_idx][1]
                    p2 = hist_j[sample_idx][1]
                    dx = p1[0] - p2[0]
                    dz = (p1[2] - p2[2]) if len(p1) > 2 and len(p2) > 2 else 0
                    sample_distances.append(math.sqrt(dx * dx + dz * dz))
            if sample_distances:
                distance_samples.append(
                    sum(sample_distances) / len(sample_distances))

        if len(distance_samples) < 3:
            return 0.0

        mean = sum(distance_samples) / len(distance_samples)
        variance = sum((d - mean) ** 2 for d in distance_samples) / len(distance_samples)
        std = math.sqrt(variance)

        # Low std = consistent spacing = high coordination
        # Score: 1.0 if std < 30m, 0.0 if std > 200m
        if std < 30:
            return 1.0
        elif std > 200:
            return 0.0
        else:
            return 1.0 - (std - 30) / 170.0

    def _compute_simultaneous_engagement(self) -> float:
        """Fraction of engagements where 2+ players fire within 5 seconds."""
        all_times = []
        for pid, times in self.engagement_times.items():
            for t in times:
                all_times.append((t, pid))
        all_times.sort()

        if len(all_times) < 4:
            return 0.0

        simultaneous = 0
        total = 0
        window = 5.0  # seconds

        for i, (t, pid) in enumerate(all_times):
            total += 1
            for j in range(i + 1, len(all_times)):
                t2, pid2 = all_times[j]
                if t2 - t > window:
                    break
                if pid2 != pid:
                    simultaneous += 1
                    break

        return simultaneous / max(total, 1)


# ═══════════════════════════════════════════════════════════
# 6. REINFORCEMENT WAVE SYSTEM
# ═══════════════════════════════════════════════════════════

class WaveMode(Enum):
    TRICKLE = "trickle"
    WAVE = "wave"
    TRIGGERED = "triggered"


@dataclass
class ReinforcementWaveManager:
    """Manages AI reinforcement spawning across three modes.

    Mode selection algorithm:
      - TRICKLE: early game or few players — steady drip of single squads
      - WAVE: mid game with enough players — periodic bursts with rest gaps
      - TRIGGERED: reactive spawns from game events (objective capture, etc.)

    Triggered mode runs in parallel with either trickle or wave mode.
    """

    def __post_init__(self):
        self.last_trickle_spawn: float = 0.0
        self.last_wave_start: float = 0.0
        self.last_wave_end: float = 0.0
        self.current_wave_number: int = 0
        self.pending_triggers: list = []
        self.spawn_history: deque = deque(maxlen=100)

    def __init__(self, config: dict = None):
        cfg = (config or {}).get("reinforcement_waves", {})
        self.trickle_cfg = cfg.get("wave_modes", {}).get("trickle", {})
        self.wave_cfg = cfg.get("wave_modes", {}).get("wave", {})
        self.trigger_cfg = cfg.get("wave_modes", {}).get("triggered", {}).get("triggers", {})

        self.last_trickle_spawn = 0.0
        self.last_wave_start = 0.0
        self.last_wave_end = 0.0
        self.current_wave_number = 0
        self.pending_triggers: list = []
        self.spawn_history: deque = deque(maxlen=100)

    def _select_mode(self, mission_time: float, player_count: int) -> WaveMode:
        """Select primary wave mode based on game state."""
        if mission_time < 300 or player_count <= 2:
            return WaveMode.TRICKLE
        return WaveMode.WAVE

    def process_events(self, events: list, game_state: dict):
        """Scan events for reinforcement triggers."""
        now = time.time()
        for event in events:
            if not isinstance(event, str):
                continue

            # Objective captured
            if "objective_captured" in event or "objective_complete" in event:
                trigger = self.trigger_cfg.get("objective_captured", {})
                if trigger:
                    self.pending_triggers.append({
                        "type": "objective_captured",
                        "fire_at": now + trigger.get("delay_seconds", 15),
                        "units": trigger.get("units", 4),
                        "composition": trigger.get("composition", "counterattack"),
                    })

            # Squad wiped
            if "squad_wiped" in event or "unit_destroyed" in event:
                trigger = self.trigger_cfg.get("squad_wiped", {})
                if trigger:
                    self.pending_triggers.append({
                        "type": "squad_wiped",
                        "fire_at": now + trigger.get("delay_seconds", 45),
                        "units": trigger.get("units", 2),
                        "composition": trigger.get("composition", "replacement"),
                    })

    def get_spawn_commands(self, mission_time: float, player_count: int,
                           current_squad_count: int, max_squads: int,
                           spawn_rate_mult: float) -> list:
        """Compute spawn commands for this tick.

        Returns list of spawn command dicts ready to inject into LLM output.
        """
        if spawn_rate_mult <= 0:
            return []

        now = time.time()
        commands = []
        budget_remaining = max_squads - current_squad_count

        if budget_remaining <= 0:
            return []

        # Process triggered spawns (always active)
        ready_triggers = [t for t in self.pending_triggers if now >= t["fire_at"]]
        for trigger in ready_triggers:
            units = min(trigger["units"], budget_remaining)
            if units > 0:
                commands.append({
                    "source": "triggered",
                    "trigger_type": trigger["type"],
                    "unit_count": units,
                    "composition": trigger["composition"],
                })
                budget_remaining -= units
            self.pending_triggers.remove(trigger)

        # Primary mode
        mode = self._select_mode(mission_time, player_count)

        if mode == WaveMode.TRICKLE:
            interval = self.trickle_cfg.get("spawn_interval_seconds", 45)
            variance = self.trickle_cfg.get("spawn_variance_seconds", 15)
            effective_interval = interval + random.uniform(-variance, variance)
            effective_interval /= max(spawn_rate_mult, 0.1)

            if now - self.last_trickle_spawn >= effective_interval and budget_remaining > 0:
                units = min(
                    self.trickle_cfg.get("units_per_spawn", 1),
                    self.trickle_cfg.get("max_concurrent_spawns", 2),
                    budget_remaining,
                )
                commands.append({
                    "source": "trickle",
                    "unit_count": units,
                    "composition": "standard",
                })
                self.last_trickle_spawn = now

        elif mode == WaveMode.WAVE:
            wave_interval = self.wave_cfg.get("wave_interval_seconds", 120)
            rest_period = self.wave_cfg.get("rest_period_seconds", 90)
            wave_duration = self.wave_cfg.get("wave_duration_seconds", 30)

            time_since_wave_end = now - self.last_wave_end
            time_since_wave_start = now - self.last_wave_start

            in_rest = time_since_wave_end < rest_period
            in_wave = (self.last_wave_start > self.last_wave_end and
                       time_since_wave_start < wave_duration)

            if in_wave and budget_remaining > 0:
                # Currently in a wave — keep spawning
                base_units = self.wave_cfg.get("units_per_wave_base", 3)
                per_player = self.wave_cfg.get("units_per_wave_per_player", 1)
                escalation = self.wave_cfg.get("escalation_per_wave", 0.15)

                total = base_units + per_player * player_count
                total *= (1.0 + escalation * self.current_wave_number)
                total *= spawn_rate_mult
                total = min(int(total), budget_remaining)

                if total > 0:
                    commands.append({
                        "source": "wave",
                        "wave_number": self.current_wave_number,
                        "unit_count": total,
                        "composition": "mixed" if self.current_wave_number > 2 else "standard",
                    })
                    self.last_wave_end = now

            elif not in_rest and time_since_wave_end >= wave_interval:
                # Start new wave
                self.current_wave_number += 1
                self.last_wave_start = now
                log.info("Starting reinforcement wave #%d", self.current_wave_number)

        return commands


# ═══════════════════════════════════════════════════════════
# 7. ENTITY POOL
# ═══════════════════════════════════════════════════════════

@dataclass
class PooledEntity:
    """An entity in the object pool, ready for reuse."""
    pool_slot: int
    in_use: bool = False
    last_returned: float = 0.0
    reuse_count: int = 0


class EntityPool:
    """Object pool for AI entities to avoid spawn/despawn churn.

    Instead of creating and destroying game entities (expensive Arma operations),
    entities are teleported, reset, and reassigned.

    Algorithm:
      - Checkout: grab an unused entity from pool, teleport to position, assign identity
      - Return: hide/teleport to limbo position, mark as available
      - Grow: if pool is empty, allocate a batch of new entities
    """

    def __init__(self, config: dict = None):
        cfg = (config or {}).get("entity_pool", {})
        self.initial_size = cfg.get("initial_pool_size", 20)
        self.max_size = cfg.get("max_pool_size", 80)
        self.grow_increment = cfg.get("grow_increment", 5)
        self.death_return_delay = cfg.get(
            "recycling_policy", {}).get("on_death", {}).get(
            "return_to_pool_after_seconds", 10)

        self.pool: list[PooledEntity] = []
        self.peak_concurrent = 0
        self.total_checkouts = 0
        self.total_returns = 0
        self.pool_exhaustion_count = 0

        # Initialize pool
        for i in range(self.initial_size):
            self.pool.append(PooledEntity(pool_slot=i))

    def checkout(self) -> Optional[PooledEntity]:
        """Get an entity from the pool. Returns None if pool exhausted and at max."""
        for entity in self.pool:
            if not entity.in_use:
                entity.in_use = True
                entity.reuse_count += 1
                self.total_checkouts += 1

                in_use = sum(1 for e in self.pool if e.in_use)
                self.peak_concurrent = max(self.peak_concurrent, in_use)
                return entity

        # Pool exhausted — try to grow
        if len(self.pool) < self.max_size:
            grow_count = min(self.grow_increment,
                             self.max_size - len(self.pool))
            log.warning("Entity pool exhausted — growing by %d (total: %d)",
                        grow_count, len(self.pool) + grow_count)
            for _ in range(grow_count):
                new_entity = PooledEntity(pool_slot=len(self.pool))
                self.pool.append(new_entity)

            # Try again
            return self.checkout()

        self.pool_exhaustion_count += 1
        log.error("Entity pool fully exhausted at max size %d", self.max_size)
        return None

    def return_entity(self, slot: int):
        """Return an entity to the pool for reuse."""
        for entity in self.pool:
            if entity.pool_slot == slot:
                entity.in_use = False
                entity.last_returned = time.time()
                self.total_returns += 1
                return
        log.warning("Attempted to return unknown pool slot %d", slot)

    def get_stats(self) -> dict:
        in_use = sum(1 for e in self.pool if e.in_use)
        available = len(self.pool) - in_use
        return {
            "pool_size": len(self.pool),
            "in_use": in_use,
            "available": available,
            "peak_concurrent": self.peak_concurrent,
            "total_checkouts": self.total_checkouts,
            "total_returns": self.total_returns,
            "pool_exhaustions": self.pool_exhaustion_count,
            "avg_reuse_count": round(
                sum(e.reuse_count for e in self.pool) / max(len(self.pool), 1), 1),
        }


# ═══════════════════════════════════════════════════════════
# 8. MAIN SCALING ENGINE
# ═══════════════════════════════════════════════════════════

class ScalingEngine:
    """Unified scaling coordinator that ties all subsystems together.

    This is the primary interface used by server.py. Each game_master tick:
      1. Update zone states from player positions.
      2. Update AI LOD tiers.
      3. Check performance budget.
      4. Compute player spread.
      5. Evaluate team coordination.
      6. Process reinforcement waves.
      7. Return comprehensive scaling decision.
    """

    def __init__(self, config_path: str = None):
        if config_path:
            with open(config_path) as f:
                self.config = json.load(f)
        else:
            self.config = {}

        self.zone_manager = ZoneManager(self.config)
        self.lod_manager = AILodManager(self.config)
        self.perf_budget = PerformanceBudget()
        self.team_analyzer = TeamCoordinationAnalyzer()
        self.wave_manager = ReinforcementWaveManager(self.config)
        self.entity_pool = EntityPool(self.config)
        self._last_tick = time.time()

    def compute_scaling(self, game_state: dict) -> dict:
        """Main entry point — compute all scaling decisions for this tick.

        Parameters
        ----------
        game_state : dict
            The raw SITREP from the Arma Reforger game server, containing
            players, enemy_units, recent_events, mission_time_seconds, etc.

        Returns
        -------
        dict with:
            - max_squads: int (adjusted maximum)
            - max_vehicles: int (adjusted maximum)
            - update_interval: float (seconds for next LLM call)
            - zone_summary: dict
            - lod_summary: dict
            - performance: dict
            - spread: dict
            - team: dict
            - reinforcements: list of spawn command dicts
            - pool_stats: dict
        """
        now = time.time()
        dt = now - self._last_tick
        self._last_tick = now

        players = game_state.get("players", [])
        enemy_units = game_state.get("enemy_units", [])
        events = game_state.get("recent_events", [])
        mission_time = game_state.get("mission_time_seconds", 0)
        player_count = game_state.get("player_count", len(players))
        server_fps = game_state.get("server_fps", 30)

        # 1. Zone update
        zone_summary = self.zone_manager.update(players)

        # 2. LOD update
        # Sync entities from game state
        known_ids = set()
        for u in enemy_units:
            uid = u.get("unit_id", "")
            if uid:
                known_ids.add(uid)
                if uid not in self.lod_manager.entities:
                    self.lod_manager.register_entity(AIEntityState(
                        unit_id=uid,
                        unit_type=u.get("unit_type", "infantry"),
                        faction=u.get("faction", "ussr"),
                        position=u.get("position", [0, 0, 0]),
                        strength=u.get("strength", 8),
                        health_pct=u.get("health_pct", 1.0),
                        current_order=u.get("current_order", "idle"),
                    ))
                else:
                    entity = self.lod_manager.entities[uid]
                    entity.position = u.get("position", entity.position)
                    entity.health_pct = u.get("health_pct", entity.health_pct)
                    entity.strength = u.get("strength", entity.strength)

        # Remove entities no longer in game state
        stale = [uid for uid in self.lod_manager.entities if uid not in known_ids]
        for uid in stale:
            self.lod_manager.remove_entity(uid)

        lod_summary = self.lod_manager.update(players)

        # 3. Performance budget
        self.perf_budget.record_fps(server_fps)
        perf = self.perf_budget.update()

        # 4. Player spread
        spread = compute_player_spread(players)

        # 5. Team coordination
        team = self.team_analyzer.update(players, events)

        # 6. Compute effective limits
        # Base formula: max_squads = base + per_player * count * spread_mod * diff_mod
        base_cfg = self.config.get("player_scaling_v2", {}).get("base_formula", {})
        base_squads = base_cfg.get("base_squads", 15)
        per_player_sq = base_cfg.get("per_player_squads", 10)
        base_vehicles = base_cfg.get("base_vehicles", 6)
        per_player_veh = base_cfg.get("per_player_vehicles", 4)
        base_interval = base_cfg.get("base_update_interval", 25)
        per_player_interval = base_cfg.get("per_player_interval_add", 2.5)
        max_interval = base_cfg.get("max_update_interval", 90)
        min_interval = base_cfg.get("min_update_interval", 20)

        raw_squads = base_squads + per_player_sq * player_count
        raw_vehicles = base_vehicles + per_player_veh * player_count

        # Apply modifiers
        spread_sq_mod = spread["squad_modifier"]
        spread_veh_mod = spread["vehicle_modifier"]
        team_diff_mod = team["difficulty_multiplier"]
        perf_ai_mod = perf["ai_budget_pct"]
        perf_veh_mod = perf["vehicle_budget_pct"]
        perf_interval_mod = perf["llm_interval_modifier"]

        effective_squads = int(
            raw_squads * spread_sq_mod * team_diff_mod * perf_ai_mod
        )
        effective_vehicles = int(
            raw_vehicles * spread_veh_mod * team_diff_mod * perf_veh_mod
        )

        # Hard caps from performance budget
        abs_max_ai = self.config.get("performance_budget", {}).get(
            "absolute_max_ai_entities", 200)
        abs_max_veh = self.config.get("performance_budget", {}).get(
            "absolute_max_vehicles", 40)
        effective_squads = max(2, min(effective_squads, abs_max_ai))
        effective_vehicles = max(1, min(effective_vehicles, abs_max_veh))

        # Update interval
        raw_interval = base_interval + per_player_interval * player_count
        effective_interval = raw_interval * perf_interval_mod
        effective_interval = max(min_interval, min(max_interval, effective_interval))

        # 7. Reinforcement waves
        self.wave_manager.process_events(events, game_state)
        reinforcements = self.wave_manager.get_spawn_commands(
            mission_time=mission_time,
            player_count=player_count,
            current_squad_count=len(enemy_units),
            max_squads=effective_squads,
            spawn_rate_mult=perf["spawn_rate_multiplier"],
        )

        # 8. Emergency despawn (performance emergency tier)
        despawn_commands = []
        if perf.get("force_despawn_dormant"):
            dormant = self.lod_manager.get_entities_in_tier(AILodTier.DORMANT)
            for entity in dormant:
                despawn_commands.append({
                    "command_type": "despawn_unit",
                    "unit_id": entity.unit_id,
                    "reasoning": "performance_emergency_despawn",
                })

        return {
            "max_squads": effective_squads,
            "max_vehicles": effective_vehicles,
            "update_interval": round(effective_interval, 1),
            "zone_summary": zone_summary,
            "lod_summary": lod_summary,
            "performance": perf,
            "spread": spread,
            "team": team,
            "reinforcements": reinforcements,
            "emergency_despawns": despawn_commands,
            "pool_stats": self.entity_pool.get_stats(),
            "simulation_cost": round(self.lod_manager.get_simulation_cost_estimate(), 1),
        }


# ═══════════════════════════════════════════════════════════
# 9. ADAPTIVE DIFFICULTY CONTROLLER
# ═══════════════════════════════════════════════════════════

class DifficultyTier(Enum):
    """Discrete difficulty tiers that map to concrete AI behavior parameters."""
    MERCY = "mercy"           # Players getting destroyed, AI becomes very weak
    EASY = "easy"             # AI is clearly winning, significant nerfs
    REDUCED = "reduced"       # AI is ahead, moderate nerfs
    BALANCED = "balanced"     # Even match
    ELEVATED = "elevated"     # Players are ahead, AI gets a boost
    HARD = "hard"             # Players dominating, full AI capability
    RUTHLESS = "ruthless"     # Players stomping, AI uses everything


@dataclass
class DifficultyParameters:
    """Concrete parameters that mechanically affect AI combat effectiveness.

    These are NOT suggestions to the LLM -- they are hard limits enforced
    by the middleware before commands reach the game engine.
    """
    accuracy_multiplier: float = 1.0       # Scales AI aim skill (0.1 = terrible, 1.0 = normal)
    reaction_time_multiplier: float = 1.0  # Scales delay before AI engages (2.0 = 2x slower)
    max_simultaneous_attackers: int = 99   # How many AI squads can attack at once
    spawn_rate_multiplier: float = 1.0     # Multiplier on reinforcement spawn rate
    max_active_squads: int = 99            # Hard cap on total AI squads
    aggression_cap: str = "ruthless"       # Maximum allowed aggression level
    suppress_spawn_commands: bool = False  # Block all spawn commands from LLM
    force_retreat_pct: float = 0.0         # Force this % of squads to retreat
    engagement_range_cap: float = 9999.0   # Max engagement distance in meters
    heavy_vehicle_allowed: bool = True     # Allow T-72, BMP-1 spawns

    def to_dict(self) -> dict:
        return {
            "accuracy_multiplier": round(self.accuracy_multiplier, 2),
            "reaction_time_multiplier": round(self.reaction_time_multiplier, 2),
            "max_simultaneous_attackers": self.max_simultaneous_attackers,
            "spawn_rate_multiplier": round(self.spawn_rate_multiplier, 2),
            "max_active_squads": self.max_active_squads,
            "aggression_cap": self.aggression_cap,
            "suppress_spawn_commands": self.suppress_spawn_commands,
            "force_retreat_pct": round(self.force_retreat_pct, 2),
            "engagement_range_cap": round(self.engagement_range_cap, 1),
            "heavy_vehicle_allowed": self.heavy_vehicle_allowed,
        }


# Tier -> concrete parameters mapping
_DIFFICULTY_TIER_PARAMS = {
    DifficultyTier.MERCY: DifficultyParameters(
        accuracy_multiplier=0.15,
        reaction_time_multiplier=3.0,
        max_simultaneous_attackers=1,
        spawn_rate_multiplier=0.0,
        max_active_squads=3,
        aggression_cap="cautious",
        suppress_spawn_commands=True,
        force_retreat_pct=0.15,
        engagement_range_cap=100.0,
        heavy_vehicle_allowed=False,
    ),
    DifficultyTier.EASY: DifficultyParameters(
        accuracy_multiplier=0.3,
        reaction_time_multiplier=2.5,
        max_simultaneous_attackers=2,
        spawn_rate_multiplier=0.2,
        max_active_squads=5,
        aggression_cap="cautious",
        suppress_spawn_commands=False,
        force_retreat_pct=0.1,
        engagement_range_cap=200.0,
        heavy_vehicle_allowed=False,
    ),
    DifficultyTier.REDUCED: DifficultyParameters(
        accuracy_multiplier=0.5,
        reaction_time_multiplier=1.8,
        max_simultaneous_attackers=3,
        spawn_rate_multiplier=0.5,
        max_active_squads=8,
        aggression_cap="balanced",
        suppress_spawn_commands=False,
        force_retreat_pct=0.15,
        engagement_range_cap=400.0,
        heavy_vehicle_allowed=True,
    ),
    DifficultyTier.BALANCED: DifficultyParameters(
        accuracy_multiplier=0.7,
        reaction_time_multiplier=1.2,
        max_simultaneous_attackers=5,
        spawn_rate_multiplier=1.0,
        max_active_squads=20,
        aggression_cap="aggressive",
        suppress_spawn_commands=False,
        force_retreat_pct=0.0,
        engagement_range_cap=800.0,
        heavy_vehicle_allowed=True,
    ),
    DifficultyTier.ELEVATED: DifficultyParameters(
        accuracy_multiplier=0.85,
        reaction_time_multiplier=1.0,
        max_simultaneous_attackers=8,
        spawn_rate_multiplier=1.3,
        max_active_squads=16,
        aggression_cap="aggressive",
    ),
    DifficultyTier.HARD: DifficultyParameters(
        accuracy_multiplier=1.0,
        reaction_time_multiplier=0.8,
        max_simultaneous_attackers=12,
        spawn_rate_multiplier=1.5,
        max_active_squads=20,
        aggression_cap="ruthless",
    ),
    DifficultyTier.RUTHLESS: DifficultyParameters(
        accuracy_multiplier=1.0,
        reaction_time_multiplier=0.6,
        max_simultaneous_attackers=99,
        spawn_rate_multiplier=2.0,
        max_active_squads=25,
        aggression_cap="ruthless",
    ),
}


class DifficultyController:
    """Adaptive difficulty system that produces MECHANICAL effects, not just labels.

    Key design principles:
      1. The difficulty modifier directly controls spawn caps, accuracy, and aggression
         -- it is NOT just advisory text for the LLM.
      2. Progressive scaling: the worse the imbalance, the faster the system responds.
      3. Mercy rule: if players have 0 kills after a threshold of AI kills, emergency nerf.
      4. Player behavior feedback: all players hiding/stationary indicates they are
         overwhelmed, which should reduce difficulty even before kill counts reflect it.
      5. Kill ratio uses a sliding window (not all-time totals) to respond to
         current battle state rather than historical averages.
    """

    def __init__(self):
        self.difficulty_modifier: float = 1.0
        self.current_tier: DifficultyTier = DifficultyTier.BALANCED

        # Sliding window for recent engagement tracking
        self._kill_window: deque = deque(maxlen=50)  # (timestamp, "player"|"ai")
        self._player_kills_total: int = 0
        self._ai_kills_total: int = 0

        # Mercy rule state
        self._mercy_active: bool = False
        self._mercy_activated_at: float = 0.0
        self._mercy_cooldown: float = 120.0  # seconds before mercy can deactivate

        # Consecutive zero-kill tracking
        self._consecutive_ticks_zero_player_kills: int = 0

        # Behavior signal tracking
        self._all_players_passive_ticks: int = 0

        self._last_update: float = time.time()

    def track_engagement(self, event: str, timestamp: float = None):
        """Record a kill event into the sliding window.

        Args:
            event: Raw event string from game state (e.g., "enemy_killed", "player_hit")
            timestamp: Event timestamp, defaults to now
        """
        ts = timestamp or time.time()
        if "enemy_killed" in event:
            self._kill_window.append((ts, "player"))
            self._player_kills_total += 1
        elif "player_hit" in event or "player_killed" in event:
            self._kill_window.append((ts, "ai"))
            self._ai_kills_total += 1

    def _get_recent_kill_ratio(self, window_seconds: float = 300.0) -> tuple:
        """Get kill counts within a recent time window.

        Returns (player_kills, ai_kills) in the window.
        Using a time window instead of all-time totals means the system
        responds to the CURRENT battle state, not historical averages.
        """
        cutoff = time.time() - window_seconds
        player_kills = 0
        ai_kills = 0
        for ts, who in self._kill_window:
            if ts >= cutoff:
                if who == "player":
                    player_kills += 1
                else:
                    ai_kills += 1
        return player_kills, ai_kills

    def _compute_imbalance_score(self, player_kills: int, ai_kills: int) -> float:
        """Compute a signed imbalance score.

        Returns a value from -1.0 (AI completely dominating) to +1.0 (players dominating).
        0.0 = balanced.

        Uses a non-linear scaling so extreme imbalances (14:0) produce much
        stronger signals than mild ones (3:2).
        """
        total = player_kills + ai_kills
        if total == 0:
            return 0.0

        # Raw ratio: 0.0 = all AI kills, 1.0 = all player kills
        raw_ratio = player_kills / total

        # Center around 0.5, scale to [-1, 1]
        linear = (raw_ratio - 0.5) * 2.0

        # Apply non-linear scaling: extreme ratios get amplified
        # sign(x) * x^0.6 preserves sign but compresses toward extremes
        if linear >= 0:
            score = linear ** 0.7
        else:
            score = -(abs(linear) ** 0.7)

        # Weight by total kills -- more data = more confidence
        confidence = min(1.0, total / 8.0)
        return score * confidence

    def _check_mercy_rule(self, player_kills: int, ai_kills: int,
                          player_profiles: dict,
                          mission_time: float = 0.0) -> bool:
        """Determine if the mercy rule should be active.

        Mercy rule triggers when:
          - Players have 0 kills AND AI has 5+ kills (recent window), OR
          - Players have 0 kills AND AI has 10+ kills (all-time), OR
          - All players are classified as passive/hiding for 3+ consecutive ticks

        Mercy rule does NOT trigger during the first 120s of the mission
        (warm-up period where players are still spawning/deploying).

        Mercy rule deactivates when:
          - Players get 2+ kills in the recent window AND mercy has been active
            for at least the cooldown period
        """
        now = time.time()

        # Warm-up grace period: don't activate mercy during the first 120s
        # when players are still spawning, choosing loadouts, and deploying.
        if mission_time < 120.0 and not self._mercy_active:
            return self._mercy_active

        # Check activation conditions
        should_activate = False

        if player_kills == 0 and ai_kills >= 5:
            should_activate = True
            log.warning("Mercy rule trigger: 0 player kills vs %d AI kills "
                        "(recent window)", ai_kills)

        if self._player_kills_total == 0 and self._ai_kills_total >= 10:
            should_activate = True
            log.warning("Mercy rule trigger: 0 player kills vs %d AI kills "
                        "(all-time)", self._ai_kills_total)

        if self._all_players_passive_ticks >= 10:
            should_activate = True
            log.warning("Mercy rule trigger: all players passive for %d "
                        "consecutive ticks", self._all_players_passive_ticks)

        if should_activate and not self._mercy_active:
            self._mercy_active = True
            self._mercy_activated_at = now
            log.warning("MERCY RULE ACTIVATED — AI will be severely nerfed "
                        "until players can recover")

        # Check deactivation conditions
        if self._mercy_active:
            time_in_mercy = now - self._mercy_activated_at
            if player_kills >= 2 and time_in_mercy >= self._mercy_cooldown:
                self._mercy_active = False
                log.info("Mercy rule deactivated — players have %d kills, "
                         "returning to normal difficulty", player_kills)

        return self._mercy_active

    def _analyze_player_behavior_signals(self, player_profiles: dict) -> float:
        """Analyze player behavior to detect signs of being overwhelmed.

        Returns a modifier from -0.3 (players clearly struggling) to +0.1
        (players confidently aggressive).

        Key signals:
          - All players stationary/hiding = overwhelmed
          - All players defensive = under pressure
          - Mix of aggressive styles = confident and pushing
        """
        if not player_profiles:
            return 0.0

        styles = []
        for pid, profile in player_profiles.items():
            if profile.get("confidence", 0) < 0.3:
                continue
            styles.append(profile.get("style", "unknown"))

        if not styles:
            return 0.0

        # Only truly stationary/hiding players count as passive.
        # "cautious_observer" is removed — a player scouting/driving is not struggling.
        passive_styles = {"defensive_sniper", "overwatch"}
        aggressive_styles = {"aggressive_assault", "aggressive_push",
                             "vehicle_rusher", "flanking_maneuver"}

        passive_count = sum(1 for s in styles if s in passive_styles)
        aggressive_count = sum(1 for s in styles if s in aggressive_styles)
        total = len(styles)

        # Track consecutive ticks where ALL players are passive
        if passive_count == total and total > 0:
            self._all_players_passive_ticks += 1
        else:
            self._all_players_passive_ticks = 0

        passive_ratio = passive_count / total
        aggressive_ratio = aggressive_count / total

        # All passive = strong signal players are struggling
        if passive_ratio >= 1.0:
            return -0.3
        elif passive_ratio >= 0.7:
            return -0.2
        elif passive_ratio >= 0.5:
            return -0.1
        elif aggressive_ratio >= 0.7:
            return 0.1
        return 0.0

    def _select_tier(self, modifier: float, mercy_active: bool) -> DifficultyTier:
        """Map the continuous difficulty modifier to a discrete tier."""
        if mercy_active:
            return DifficultyTier.MERCY

        if modifier <= 0.3:
            return DifficultyTier.EASY
        elif modifier <= 0.6:
            return DifficultyTier.REDUCED
        elif modifier <= 1.0:
            return DifficultyTier.BALANCED
        elif modifier <= 1.3:
            return DifficultyTier.ELEVATED
        elif modifier <= 1.7:
            return DifficultyTier.HARD
        else:
            return DifficultyTier.RUTHLESS

    def update(self, game_state: dict, player_profiles: dict = None) -> dict:
        """Main update — compute difficulty from current battle state.

        This method:
          1. Ingests new kill events from game_state
          2. Computes recent kill ratio (sliding window)
          3. Analyzes player behavior for distress signals
          4. Checks mercy rule
          5. Computes new difficulty modifier with adaptive alpha
          6. Selects difficulty tier
          7. Returns both the tier and the concrete parameters

        The returned parameters dict contains mechanical limits that the
        middleware MUST enforce on LLM-generated commands.
        """
        now = time.time()
        dt = now - self._last_update
        self._last_update = now

        # 1. Ingest events
        for event in game_state.get("recent_events", []):
            if isinstance(event, str):
                self.track_engagement(event)

        # 2. Recent kill ratio
        recent_pk, recent_ak = self._get_recent_kill_ratio(window_seconds=300.0)
        total_recent = recent_pk + recent_ak

        # 3. Player behavior analysis
        profiles = player_profiles or {}
        behavior_modifier = self._analyze_player_behavior_signals(profiles)

        # 4. Mercy rule check
        mission_time = game_state.get("mission_time_seconds", 0.0)
        mercy_active = self._check_mercy_rule(recent_pk, recent_ak, profiles,
                                              mission_time=mission_time)

        # 5. Compute imbalance and update modifier
        imbalance = self._compute_imbalance_score(recent_pk, recent_ak)

        # Adaptive alpha: the more extreme the imbalance, the faster we adjust.
        # Normal games use alpha=0.15 for smooth transitions.
        # Lopsided games (14:0) use alpha=0.5 for rapid response.
        base_alpha = 0.15
        imbalance_magnitude = abs(imbalance)
        if imbalance_magnitude > 0.7:
            alpha = 0.5   # Rapid response to extreme imbalance
        elif imbalance_magnitude > 0.4:
            alpha = 0.3   # Faster response to moderate imbalance
        else:
            alpha = base_alpha

        # Target difficulty based on imbalance score
        # imbalance = -1.0 (AI dominating) -> target = 0.2 (very low)
        # imbalance =  0.0 (balanced)      -> target = 1.0
        # imbalance = +1.0 (players dominating) -> target = 2.0
        if imbalance < 0:
            # AI winning: reduce difficulty. Non-linear so extreme cases drop faster.
            target = 1.0 + imbalance * 0.8  # range [0.2, 1.0]
        else:
            # Players winning: increase difficulty
            target = 1.0 + imbalance * 1.0  # range [1.0, 2.0]

        # Apply behavior modifier
        target += behavior_modifier

        # Time-based escalation: enemies get harder as time goes on.
        # Ramps from +0.0 at mission start to +0.6 at 30 minutes,
        # with a gentle curve that accelerates after the first 5 minutes.
        if mission_time > 300:  # After 5 minutes of warm-up
            elapsed_past_warmup = mission_time - 300
            # Logarithmic ramp: fast initial climb, then tapering
            # At 10min: +0.15, 15min: +0.25, 20min: +0.35, 30min: +0.50
            time_escalation = min(0.6, 0.15 * math.log2(1 + elapsed_past_warmup / 300))
            target += time_escalation

        # Clamp target
        target = max(0.15, min(2.0, target))

        # EMA update
        self.difficulty_modifier += alpha * (target - self.difficulty_modifier)
        self.difficulty_modifier = max(0.15, min(2.0, self.difficulty_modifier))

        # Track zero-kill ticks for faster mercy activation
        if recent_pk == 0 and recent_ak > 0:
            self._consecutive_ticks_zero_player_kills += 1
        elif recent_pk > 0:
            self._consecutive_ticks_zero_player_kills = 0

        # 6. Select tier
        new_tier = self._select_tier(self.difficulty_modifier, mercy_active)
        if new_tier != self.current_tier:
            log.info("Difficulty tier changed: %s -> %s (modifier=%.2f, "
                     "mercy=%s, pk=%d, ak=%d)",
                     self.current_tier.value, new_tier.value,
                     self.difficulty_modifier, mercy_active,
                     recent_pk, recent_ak)
            self.current_tier = new_tier

        # 7. Get parameters for this tier
        params = _DIFFICULTY_TIER_PARAMS[self.current_tier]

        return {
            "modifier": round(self.difficulty_modifier, 2),
            "tier": self.current_tier.value,
            "parameters": params.to_dict(),
            "player_kills_recent": recent_pk,
            "ai_kills_recent": recent_ak,
            "player_kills_total": self._player_kills_total,
            "ai_kills_total": self._ai_kills_total,
            "imbalance_score": round(imbalance, 3),
            "behavior_modifier": round(behavior_modifier, 2),
            "mercy_active": mercy_active,
            "alpha_used": round(alpha, 2),
            "assessment": self._assessment_label(),
            "consecutive_zero_kill_ticks": self._consecutive_ticks_zero_player_kills,
        }

    def _assessment_label(self) -> str:
        """Human-readable assessment for logs and LLM prompt."""
        labels = {
            DifficultyTier.MERCY: "MERCY_MODE_players_need_help",
            DifficultyTier.EASY: "ai_dominating_heavy_nerf",
            DifficultyTier.REDUCED: "ai_ahead_moderate_nerf",
            DifficultyTier.BALANCED: "balanced_maintain_tempo",
            DifficultyTier.ELEVATED: "players_ahead_increase_pressure",
            DifficultyTier.HARD: "players_dominating_full_force",
            DifficultyTier.RUTHLESS: "players_stomping_maximum_force",
        }
        return labels.get(self.current_tier, "unknown")

    def get_parameters(self) -> DifficultyParameters:
        """Get current difficulty parameters for command enforcement."""
        return _DIFFICULTY_TIER_PARAMS[self.current_tier]


def enforce_difficulty_on_commands(commands: list, params: DifficultyParameters,
                                   current_squad_count: int) -> list:
    """Mechanically enforce difficulty parameters on LLM-generated commands.

    This function is the critical enforcement layer. The LLM generates commands
    as an unrestricted AI commander, but this function applies hard limits
    based on the current difficulty tier BEFORE commands reach the game engine.

    Enforcement rules:
      1. If suppress_spawn_commands is True, remove all spawn commands
      2. If squad count exceeds max_active_squads, remove spawn commands
      3. If heavy_vehicle_allowed is False, downgrade heavy vehicle spawns
      4. Cap the number of simultaneous attack commands
      5. Force retreat commands for a percentage of squads
      6. Inject accuracy/reaction parameters into attack/engage commands
    """
    if not commands:
        return commands

    enforced = []
    attack_count = 0
    spawn_count = 0

    _HEAVY_VEHICLES = {"t72", "bmp1", "btr70"}
    _LIGHT_VEHICLES = {"uaz469", "uaz469_mg", "ural_truck"}
    _ATTACK_COMMANDS = {
        "attack", "seek_and_destroy", "suppress", "breach",
        "flank_left", "flank_right", "dismount_assault",
    }

    for cmd in commands:
        ct = cmd.get("command_type", "")

        # Rule 1 & 2: Spawn suppression
        if ct in ("spawn_infantry", "spawn_vehicle", "spawn_reinforcements"):
            if params.suppress_spawn_commands:
                log.info("Difficulty enforcement: BLOCKED spawn command "
                         "(mercy/suppression active)")
                continue
            if current_squad_count >= params.max_active_squads:
                log.info("Difficulty enforcement: BLOCKED spawn command "
                         "(squad cap %d reached)", params.max_active_squads)
                continue
            spawn_count += 1

        # Rule 3: Heavy vehicle downgrade
        if ct == "spawn_vehicle" and not params.heavy_vehicle_allowed:
            vehicle_type = cmd.get("parameters", "").strip().lower()
            if vehicle_type in _HEAVY_VEHICLES:
                replacement = random.choice(list(_LIGHT_VEHICLES))
                log.info("Difficulty enforcement: downgraded %s -> %s "
                         "(heavy vehicles blocked)", vehicle_type, replacement)
                cmd["parameters"] = replacement

        # Rule 4: Attack command cap
        if ct in _ATTACK_COMMANDS:
            attack_count += 1
            if attack_count > params.max_simultaneous_attackers:
                # Convert excess attacks to defensive posture
                log.info("Difficulty enforcement: converted attack -> defend "
                         "(max %d attackers)", params.max_simultaneous_attackers)
                cmd["command_type"] = "defend"

        # Rule 6: Inject accuracy/reaction parameters into combat commands
        if ct in _ATTACK_COMMANDS or ct in ("overwatch", "defend", "patrol"):
            existing_params = cmd.get("parameters", "")
            skill_params = (f"skill={params.accuracy_multiplier:.2f},"
                            f"reaction={params.reaction_time_multiplier:.1f}")
            if existing_params:
                cmd["parameters"] = f"{existing_params},{skill_params}"
            else:
                cmd["parameters"] = skill_params

        # Enforce engagement range
        if ct in _ATTACK_COMMANDS:
            existing_params = cmd.get("parameters", "")
            cmd["parameters"] = (f"{existing_params},"
                                 f"engage_range={params.engagement_range_cap:.0f}")

        enforced.append(cmd)

    return enforced


def generate_forced_retreats(enemy_units: list,
                              params: DifficultyParameters) -> list:
    """Generate retreat commands for a percentage of AI squads.

    When difficulty is low (AI dominating), force some squads to retreat
    to reduce combat pressure on players. This creates a mechanical effect
    independent of LLM decision-making.
    """
    if params.force_retreat_pct <= 0 or not enemy_units:
        return []

    retreat_count = max(1, int(len(enemy_units) * params.force_retreat_pct))
    # Prioritize retreating the squads closest to players or with highest strength
    # (removing the most threatening units)
    sorted_units = sorted(enemy_units,
                          key=lambda u: u.get("strength", 0), reverse=True)

    retreat_commands = []
    for unit in sorted_units[:retreat_count]:
        uid = unit.get("unit_id", "")
        if not uid:
            continue
        pos = unit.get("position", [0, 0, 0])
        # Retreat 500m away from current position (roughly toward map center)
        retreat_pos = [pos[0] + random.uniform(-500, 500),
                       0,
                       pos[2] + random.uniform(-500, 500)]
        retreat_commands.append({
            "command_type": "retreat",
            "unit_id": uid,
            "target_position": f"{retreat_pos[0]:.0f},0,{retreat_pos[2]:.0f}",
            "parameters": "forced_by_difficulty",
            "narrative": "",
            "reasoning": f"difficulty_enforcement_retreat "
                         f"(tier={params.aggression_cap})",
        })
        log.info("Difficulty enforcement: forced retreat for %s "
                 "(strength=%d)", uid, unit.get("strength", 0))

    return retreat_commands
