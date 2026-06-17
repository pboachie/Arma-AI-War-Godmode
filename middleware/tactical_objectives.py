"""
Tactical Objectives & Intel Messages for the AI War middleware.

Provides:
  - _generate_tactical_objectives(): Creates meaningful, narrative-driven objectives
    based on game state (enemy positions, clusters, vehicles, friendlies).
  - _inject_tactical_intel(): Injects radio_message commands with situational intel.

Objective types:
  1. Secure the Village — enemy cluster near a named location
  2. Destroy Enemy Convoy — enemy vehicle spotted
  3. Hold the Line — enemies closing from 3+ directions
  4. Rescue Operation — friendly squad under fire
  5. Recon Sector — unexplored area ahead of player
  6. Eliminate HVT — high-value target designation

Each objective has:
  - Clear descriptive name tied to a map location
  - Position based on actual unit locations
  - Auto-completion when tactical conditions are met
  - Auto-failure on 10 min timeout or player death
  - Max 2 active objectives at once
"""

import math
import random
import time
import logging
from typing import Optional

log = logging.getLogger("gm.tactical_objectives")

# ── Configuration ──
_OBJECTIVE_TIMEOUT_S: float = 600.0      # 10 min auto-fail
_OBJECTIVE_COOLDOWN_S: float = 120.0     # 2 min between new objectives
_MAX_ACTIVE_OBJECTIVES: int = 2
_INTEL_COOLDOWN_S: float = 45.0          # min gap between intel messages
_OBJECTIVE_CLEAR_RADIUS: float = 80.0    # meters — area considered "cleared"

# ── State ──
_active_objectives: list[dict] = []
_last_objective_time: float = 0.0
_last_intel_time: float = 0.0

# Everon named locations for narrative flavor (x, z, name)
_NAMED_LOCATIONS = [
    (3200, 3800, "Morton"),     (5500, 6600, "Montignac"),
    (1900, 8200, "Levie"),      (7200, 4500, "Chotain"),
    (4800, 2100, "Gravette"),   (6400, 8100, "Durras"),
    (2500, 5500, "Regina"),     (8100, 6200, "Laruns"),
    (3800, 7400, "Lamentin"),   (6800, 2800, "Tyrone"),
    (1200, 3200, "Le Port"),    (9200, 5000, "Arland"),
]


# ═══════════════════════════════════════════════════════════
# Utility helpers
# ═══════════════════════════════════════════════════════════

def _is_valid_pos(pos) -> bool:
    """Position is real if not (0,0,0) default."""
    if not isinstance(pos, (list, tuple)) or len(pos) < 3:
        return False
    return not (abs(float(pos[0])) < 1 and abs(float(pos[2])) < 1)


def _nearest_location_name(x: float, z: float) -> str:
    """Return the name of the closest named location, or a grid reference."""
    best_dist, best_name = float("inf"), ""
    for lx, lz, name in _NAMED_LOCATIONS:
        d = math.hypot(x - lx, z - lz)
        if d < best_dist:
            best_dist, best_name = d, name
    return best_name if best_dist < 600 else f"grid {x:.0f},{z:.0f}"


def _enemy_cluster_center(enemies: list, radius: float = 200.0) -> list:
    """Find clusters of enemies. Returns list of (cx, cz, count) tuples."""
    positions = []
    for u in enemies:
        ep = u.get("position", [0, 0, 0])
        if _is_valid_pos(ep):
            positions.append((float(ep[0]), float(ep[2])))
    if not positions:
        return []
    clusters, used = [], set()
    for i, (x1, z1) in enumerate(positions):
        if i in used:
            continue
        cx, cz, cnt, members = x1, z1, 1, [i]
        for j, (x2, z2) in enumerate(positions):
            if j != i and j not in used and math.hypot(x1 - x2, z1 - z2) < radius:
                cx += x2
                cz += z2
                cnt += 1
                members.append(j)
        cx /= cnt
        cz /= cnt
        if cnt >= 2:
            clusters.append((cx, cz, cnt))
            used.update(members)
    return sorted(clusters, key=lambda c: -c[2])


def _enemies_near(enemies: list, x: float, z: float, radius: float) -> int:
    """Count enemies within radius of a point."""
    count = 0
    for u in enemies:
        ep = u.get("position", [0, 0, 0])
        if _is_valid_pos(ep):
            if math.hypot(float(ep[0]) - x, float(ep[2]) - z) < radius:
                count += 1
    return count


def _bearing_word(dx: float, dz: float) -> str:
    """Convert a delta vector into a compass word (north, south-east, etc.)."""
    angle = math.degrees(math.atan2(dx, dz)) % 360
    dirs = ["north", "north-east", "east", "south-east",
            "south", "south-west", "west", "north-west"]
    return dirs[int((angle + 22.5) / 45) % 8]


def _player_centroid(game_state: dict) -> Optional[tuple]:
    """Return (px, pz) centroid of all valid players, or None."""
    valid = [p for p in game_state.get("players", [])
             if _is_valid_pos(p.get("position", [0, 0, 0]))]
    if not valid:
        return None
    n = len(valid)
    return (sum(float(p["position"][0]) for p in valid) / n,
            sum(float(p["position"][2]) for p in valid if len(p["position"]) > 2) / n)


# ═══════════════════════════════════════════════════════════
# Objective completion logic
# ═══════════════════════════════════════════════════════════

def _check_objective_completion(obj: dict, game_state: dict, now: float) -> str:
    """Check if an objective should complete or fail.
    Returns 'complete', 'fail', or 'active'."""
    # Timeout — 10 min
    if now - obj["created_at"] >= _OBJECTIVE_TIMEOUT_S:
        return "fail"

    enemies = game_state.get("enemy_units", [])
    otype = obj["type"]
    ox, oz = obj["position"]

    if otype == "secure_village":
        # Complete when no enemies remain within the objective radius
        if _enemies_near(enemies, ox, oz, _OBJECTIVE_CLEAR_RADIUS * 2) == 0:
            if now - obj["created_at"] > 30:
                return "complete"

    elif otype == "destroy_convoy":
        # Complete when the tracked vehicle unit is gone from enemy_units
        target_id = obj.get("target_unit_id", "")
        if target_id:
            alive = any(u.get("unit_id") == target_id for u in enemies)
            if not alive and now - obj["created_at"] > 15:
                return "complete"
        else:
            if _enemies_near(enemies, ox, oz, _OBJECTIVE_CLEAR_RADIUS) == 0:
                if now - obj["created_at"] > 30:
                    return "complete"

    elif otype == "hold_the_line":
        # Complete after surviving 180s
        if now - obj["created_at"] >= 180.0:
            return "complete"

    elif otype == "rescue_operation":
        # Complete when enemies cleared from friendly position
        if _enemies_near(enemies, ox, oz, _OBJECTIVE_CLEAR_RADIUS * 1.5) == 0:
            if now - obj["created_at"] > 20:
                return "complete"

    elif otype == "recon_sector":
        # Complete when player gets within 100m of the objective
        pc = _player_centroid(game_state)
        if pc and math.hypot(pc[0] - ox, pc[1] - oz) < 100:
            return "complete"

    elif otype == "eliminate_hvt":
        # Complete when the HVT unit is dead/gone
        target_id = obj.get("target_unit_id", "")
        if target_id:
            alive = any(u.get("unit_id") == target_id for u in enemies)
            if not alive and now - obj["created_at"] > 15:
                return "complete"
        elif now - obj["created_at"] >= 300:
            return "fail"

    # Player death check — if all players are dead, fail objectives
    players = game_state.get("players", [])
    alive_players = [p for p in players
                     if _is_valid_pos(p.get("position", [0, 0, 0]))
                     and p.get("health_pct", p.get("health", 1.0)) > 0]
    if players and not alive_players:
        return "fail"

    return "active"


# ═══════════════════════════════════════════════════════════
# Objective selection logic
# ═══════════════════════════════════════════════════════════

def _pick_tactical_objective(game_state: dict, existing_types: set) -> Optional[dict]:
    """Analyze the game state and select the most appropriate objective.
    Returns an objective dict or None if no good objective is available."""
    enemies = game_state.get("enemy_units", [])
    ai_groups = game_state.get("ai_groups", [])
    valid_players = [p for p in game_state.get("players", [])
                     if _is_valid_pos(p.get("position", [0, 0, 0]))]
    if not valid_players:
        return None
    pc = _player_centroid(game_state)
    if not pc:
        return None
    px, pz = pc
    candidates = []  # (priority, objective_dict)

    # --- 1) Secure the Village ---
    if "secure_village" not in existing_types:
        for cx, cz, cnt in _enemy_cluster_center(enemies, radius=250):
            if cnt >= 3:
                dist_to_player = math.hypot(cx - px, cz - pz)
                if 200 < dist_to_player < 1500:
                    loc = _nearest_location_name(cx, cz)
                    candidates.append((10 + cnt, {
                        "type": "secure_village",
                        "name": f"Secure {loc}",
                        "position": (cx, cz),
                        "narrative": f"Enemy forces have fortified near {loc}. Move in and clear them out.",
                        "completion_condition": "Eliminate all hostiles in the area",
                    }))

    # --- 2) Destroy Enemy Convoy ---
    if "destroy_convoy" not in existing_types:
        for vu in enemies:
            uid_lower = vu.get("unit_id", "").lower()
            if any(v in uid_lower for v in ("vehicle", "btr", "bmp", "t72", "uaz", "ural")):
                vp = vu.get("position", [0, 0, 0])
                if _is_valid_pos(vp):
                    vx, vz = float(vp[0]), float(vp[2])
                    dist = math.hypot(vx - px, vz - pz)
                    if 150 < dist < 2000:
                        loc = _nearest_location_name(vx, vz)
                        candidates.append((15, {
                            "type": "destroy_convoy",
                            "name": f"Destroy Enemy Convoy near {loc}",
                            "position": (vx, vz),
                            "target_unit_id": vu.get("unit_id", ""),
                            "narrative": f"Enemy armored vehicle spotted near {loc}. Engage and destroy.",
                            "completion_condition": "Destroy the enemy vehicle",
                        }))
                        break  # only one convoy objective

    # --- 3) Hold the Line ---
    if "hold_the_line" not in existing_types:
        directions = set()
        for u in enemies:
            ep = u.get("position", [0, 0, 0])
            if _is_valid_pos(ep):
                ex, ez = float(ep[0]), float(ep[2])
                dist = math.hypot(ex - px, ez - pz)
                if dist < 600:
                    directions.add(_bearing_word(ex - px, ez - pz))
        if len(directions) >= 3:
            loc = _nearest_location_name(px, pz)
            candidates.append((20, {
                "type": "hold_the_line",
                "name": f"Hold the Line at {loc}",
                "position": (px, pz),
                "narrative": f"Enemy forces closing from multiple directions near {loc}. Dig in and hold your ground.",
                "completion_condition": "Survive for 3 minutes",
            }))

    # --- 4) Rescue Operation ---
    if "rescue_operation" not in existing_types:
        for g in ai_groups:
            gp = g.get("position", [0, 0, 0]) if isinstance(g, dict) else getattr(g, "position", [0, 0, 0])
            if _is_valid_pos(gp):
                gx, gz = float(gp[0]), float(gp[2])
                threats = _enemies_near(enemies, gx, gz, 200)
                dist_to_player = math.hypot(gx - px, gz - pz)
                if threats >= 2 and 100 < dist_to_player < 1200:
                    loc = _nearest_location_name(gx, gz)
                    candidates.append((18, {
                        "type": "rescue_operation",
                        "name": f"Rescue Friendlies near {loc}",
                        "position": (gx, gz),
                        "narrative": f"Friendly squad pinned down near {loc} under heavy contact. Move to support.",
                        "completion_condition": "Neutralize threats around friendly squad",
                    }))
                    break

    # --- 5) Recon Sector ---
    if "recon_sector" not in existing_types:
        heading = float(valid_players[0].get("heading", 0))
        rdist = random.uniform(400, 700)
        angle_offset = random.uniform(-30, 30)
        tx = px + math.sin(math.radians(heading + angle_offset)) * rdist
        tz = pz + math.cos(math.radians(heading + angle_offset)) * rdist
        loc = _nearest_location_name(tx, tz)
        candidates.append((5, {
            "type": "recon_sector",
            "name": f"Recon {loc}",
            "position": (tx, tz),
            "narrative": f"Command requests reconnaissance of the area near {loc}. Proceed with caution.",
            "completion_condition": "Reach the designated area",
        }))

    # --- 6) Eliminate HVT ---
    if "eliminate_hvt" not in existing_types and len(enemies) >= 4:
        eligible = [u for u in enemies
                    if _is_valid_pos(u.get("position", [0, 0, 0]))
                    and u.get("health_pct", 1.0) > 0.3]
        if eligible:
            hvt = random.choice(eligible)
            hp = hvt.get("position", [0, 0, 0])
            hx, hz = float(hp[0]), float(hp[2])
            dist = math.hypot(hx - px, hz - pz)
            if 200 < dist < 2000:
                loc = _nearest_location_name(hx, hz)
                uid = hvt.get("unit_id", "")
                unit_desc = "enemy officer"
                for tag, label in [("mg", "enemy MG team leader"),
                                   ("at", "enemy anti-tank specialist"),
                                   ("recon", "enemy recon team leader"),
                                   ("fire", "enemy fire team leader")]:
                    if tag in uid.lower():
                        unit_desc = label
                        break
                candidates.append((12, {
                    "type": "eliminate_hvt",
                    "name": f"Eliminate HVT near {loc}",
                    "position": (hx, hz),
                    "target_unit_id": uid,
                    "narrative": f"Intel reports high-value target ({unit_desc}) operating near {loc}. Eliminate with prejudice.",
                    "completion_condition": "Eliminate the designated target",
                }))

    if not candidates:
        return None
    # Sort by priority (highest number = most urgent situation)
    candidates.sort(key=lambda c: -c[0])
    return candidates[0][1]


# ═══════════════════════════════════════════════════════════
# Main objective generator (replaces _generate_dynamic_objectives)
# ═══════════════════════════════════════════════════════════

def generate_tactical_objectives(batch: dict, game_state: Optional[dict]) -> dict:
    """Create meaningful, narrative-driven objectives based on tactical situation.

    Tracks active objectives and auto-completes/fails them when conditions are met.
    Max 2 active objectives, 2 min cooldown between new ones, 10 min timeout.
    """
    global _last_objective_time

    now = time.time()
    commands = batch.get("commands", [])

    if not game_state:
        batch["commands"] = commands
        return batch

    # ── Phase 1: Check active objectives for completion / failure ──
    remaining = []
    for obj in _active_objectives:
        status = _check_objective_completion(obj, game_state, now)
        if status == "complete":
            commands.append({
                "command_type": "objective_complete",
                "unit_id": "",
                "target_position": f"{obj['position'][0]:.0f},0,{obj['position'][1]:.0f}",
                "parameters": obj["id"],
                "narrative": obj["name"],
                "reasoning": f"Objective completed: {obj['name']} — {obj.get('completion_condition', 'conditions met')}",
                "_system_injected": True,
            })
            log.info("Objective COMPLETED: %s [%s] after %.0fs",
                     obj["id"], obj["name"], now - obj["created_at"])
        elif status == "fail":
            fail_reason = "timed out" if now - obj["created_at"] >= _OBJECTIVE_TIMEOUT_S else "conditions lost"
            commands.append({
                "command_type": "objective_fail",
                "unit_id": "",
                "target_position": f"{obj['position'][0]:.0f},0,{obj['position'][1]:.0f}",
                "parameters": obj["id"],
                "narrative": obj["name"],
                "reasoning": f"Objective failed: {obj['name']} — {fail_reason}",
                "_system_injected": True,
            })
            log.info("Objective FAILED: %s [%s] after %.0fs — %s",
                     obj["id"], obj["name"], now - obj["created_at"], fail_reason)
        else:
            remaining.append(obj)

    _active_objectives.clear()
    _active_objectives.extend(remaining)

    # ── Phase 2: Create new objective if slots available ──
    if (len(_active_objectives) >= _MAX_ACTIVE_OBJECTIVES
            or now - _last_objective_time < _OBJECTIVE_COOLDOWN_S):
        batch["commands"] = commands
        return batch

    existing_types = {o["type"] for o in _active_objectives}
    new_obj = _pick_tactical_objective(game_state, existing_types)
    if not new_obj:
        batch["commands"] = commands
        return batch

    obj_id = f"obj_{new_obj['type']}_{int(now)}_{random.randint(100, 999)}"
    tx, tz = new_obj["position"]

    commands.append({
        "command_type": "objective_create",
        "unit_id": "",
        "target_position": f"{tx:.0f},0,{tz:.0f}",
        "parameters": obj_id,
        "narrative": new_obj["name"],
        "reasoning": new_obj["narrative"],
        "_system_injected": True,
    })

    _active_objectives.append({
        "id": obj_id,
        "name": new_obj["name"],
        "type": new_obj["type"],
        "position": (tx, tz),
        "created_at": now,
        "status": "active",
        "completion_condition": new_obj.get("completion_condition", ""),
        "target_unit_id": new_obj.get("target_unit_id", ""),
    })
    _last_objective_time = now
    log.info("Tactical objective: %s [%s] at %.0f,%.0f — %s",
             obj_id, new_obj["name"], tx, tz, new_obj.get("completion_condition", ""))

    batch["commands"] = commands
    return batch


# ═══════════════════════════════════════════════════════════
# Tactical Intel Radio Messages
# ═══════════════════════════════════════════════════════════

_INTEL_TEMPLATES = {
    "enemy_patrol": [
        "Enemy patrol spotted {dist:.0f}m to the {direction}.",
        "Movement detected {dist:.0f}m {direction} — possible enemy patrol.",
        "Hostile contacts bearing {direction}, approximately {dist:.0f} meters.",
    ],
    "enemy_vehicle": [
        "Enemy vehicle sighted {dist:.0f}m {direction}. Stay in cover.",
        "Armor contact — enemy vehicle {dist:.0f}m to the {direction}.",
        "Warning: mechanized enemy unit {dist:.0f}m {direction}.",
    ],
    "enemy_cluster": [
        "Multiple enemy contacts concentrated {dist:.0f}m {direction}. Exercise caution.",
        "Large hostile presence detected {dist:.0f}m to the {direction}.",
        "Enemy force buildup reported {dist:.0f}m {direction}.",
    ],
    "friendly_support": [
        "Friendly squad moving to support from the {direction}.",
        "Reinforcements inbound from the {direction}. Hold position.",
        "Allied unit approaching from the {direction}.",
    ],
    "area_clear": [
        "Sector to the {direction} appears clear. Proceed when ready.",
        "No contacts detected {direction}. Area looks clean.",
    ],
    "spawn_alert": [
        "New enemy contacts reported near {location}. Stay sharp.",
        "Fresh hostiles deploying near {location}.",
    ],
}


def inject_tactical_intel(batch: dict, game_state: Optional[dict]) -> dict:
    """Inject radio_message commands with tactical intel based on the game state.

    Provides situational awareness to the player about nearby threats and friendlies.
    Respects a 45s cooldown between messages to avoid spam.
    """
    global _last_intel_time

    now = time.time()
    commands = batch.get("commands", [])

    if not game_state or now - _last_intel_time < _INTEL_COOLDOWN_S:
        batch["commands"] = commands
        return batch

    pc = _player_centroid(game_state)
    if not pc:
        batch["commands"] = commands
        return batch
    px, pz = pc

    enemies = game_state.get("enemy_units", [])
    ai_groups = game_state.get("ai_groups", [])
    intel_messages = []  # (priority_distance, message_text)

    # --- Enemy patrol intel (units 200-800m away) ---
    for u in enemies:
        ep = u.get("position", [0, 0, 0])
        if not _is_valid_pos(ep):
            continue
        ex, ez = float(ep[0]), float(ep[2])
        dist = math.hypot(ex - px, ez - pz)
        uid = u.get("unit_id", "").lower()
        if 200 < dist < 800:
            direction = _bearing_word(ex - px, ez - pz)
            is_vehicle = any(v in uid for v in ("btr", "bmp", "t72", "uaz", "ural", "vehicle"))
            if is_vehicle:
                tpl = random.choice(_INTEL_TEMPLATES["enemy_vehicle"])
                intel_messages.append((dist, tpl.format(dist=dist, direction=direction)))
            elif random.random() < 0.3:  # don't spam every infantry unit
                tpl = random.choice(_INTEL_TEMPLATES["enemy_patrol"])
                intel_messages.append((dist, tpl.format(dist=dist, direction=direction)))

    # --- Enemy cluster intel ---
    for cx, cz, cnt in _enemy_cluster_center(enemies, radius=200):
        dist = math.hypot(cx - px, cz - pz)
        if 300 < dist < 1200 and cnt >= 3:
            direction = _bearing_word(cx - px, cz - pz)
            tpl = random.choice(_INTEL_TEMPLATES["enemy_cluster"])
            intel_messages.append((dist * 0.5, tpl.format(dist=dist, direction=direction)))

    # --- Friendly support intel ---
    for g in ai_groups:
        gp = g.get("position", [0, 0, 0]) if isinstance(g, dict) else getattr(g, "position", [0, 0, 0])
        if _is_valid_pos(gp):
            gx, gz = float(gp[0]), float(gp[2])
            dist = math.hypot(gx - px, gz - pz)
            if 100 < dist < 600:
                direction = _bearing_word(gx - px, gz - pz)
                tpl = random.choice(_INTEL_TEMPLATES["friendly_support"])
                intel_messages.append((dist, tpl.format(direction=direction)))
                break  # one friendly intel per cycle

    # --- Spawn alerts (from spawn commands in this batch) ---
    for cmd in commands:
        ct = cmd.get("command_type", "")
        if ct in ("spawn_infantry", "spawn_vehicle", "spawn_reinforcements"):
            tp = cmd.get("target_position", "")
            if isinstance(tp, str) and "," in tp:
                parts = tp.split(",")
                try:
                    sx, sz = float(parts[0]), float(parts[-1])
                    dist = math.hypot(sx - px, sz - pz)
                    if 150 < dist < 1000:
                        loc = _nearest_location_name(sx, sz)
                        tpl = random.choice(_INTEL_TEMPLATES["spawn_alert"])
                        intel_messages.append((dist * 0.8, tpl.format(location=loc)))
                except (ValueError, IndexError):
                    pass

    # --- Occasional "area clear" when few enemies ---
    if not intel_messages:
        if random.random() < 0.15 and len(enemies) < 3:
            heading = float(game_state.get("players", [{}])[0].get("heading", 0))
            direction = _bearing_word(
                math.sin(math.radians(heading)),
                math.cos(math.radians(heading)),
            )
            tpl = random.choice(_INTEL_TEMPLATES["area_clear"])
            intel_messages.append((999, tpl.format(direction=direction)))

    if not intel_messages:
        batch["commands"] = commands
        return batch

    # Pick the most important message (lowest distance = highest priority)
    intel_messages.sort(key=lambda m: m[0])
    chosen_msg = intel_messages[0][1]

    commands.append({
        "command_type": "radio_message",
        "unit_id": "",
        "target_position": "0,0,0",
        "parameters": "tactical_intel",
        "narrative": chosen_msg,
        "reasoning": "System-generated tactical intel",
        "_system_injected": True,
    })
    _last_intel_time = now
    log.info("Tactical intel: %s", chosen_msg)

    batch["commands"] = commands
    return batch


def get_active_objectives() -> list[dict]:
    """Return a copy of the active objectives list for external inspection."""
    return list(_active_objectives)
