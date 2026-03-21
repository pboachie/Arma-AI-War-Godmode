#!/usr/bin/env python3
"""
test_server.py — Test the God Mode v3 middleware without Arma Reforger.

Sends mock SITREPs to the server and validates all advanced features:
  - LLM command generation
  - Tactical intelligence (player profiling, threat scoring, adaptive difficulty)
  - Battle plan persistence (phase transitions across rounds)
  - Conversation memory & auto-compaction
  - Session management

Usage:
  python test_server.py [server_url] [--rounds N] [--delay SECONDS]
  python test_server.py [server_url] [--parallel N]
  python test_server.py [server_url] [--rounds 5 --delay 3]   # full campaign test
"""

import argparse
import copy
import json
import random
import sys
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock

import requests

# ─── Default server ───
DEFAULT_URL = "http://localhost:8000"

# ─── Base Mock SITREP ───
BASE_SITREP = {
    "friendly_units": [],
    "enemy_units": [
        {
            "unit_id": "squad_1",
            "unit_type": "infantry_squad",
            "position": [3200, 0, 4800],
            "strength": 8,
            "current_order": "patrol",
            "health_pct": 1.0,
            "faction": "ussr",
        },
        {
            "unit_id": "squad_2",
            "unit_type": "infantry_squad",
            "position": [3500, 0, 5100],
            "strength": 4,
            "current_order": "defend",
            "health_pct": 0.75,
            "faction": "ussr",
        },
    ],
    "contacts": [
        {
            "contact_id": "contact_1",
            "last_known_position": [3800, 0, 4600],
            "unit_type": "infantry_squad",
            "confidence": 0.7,
            "seconds_since_seen": 45,
        }
    ],
    "players": [
        {
            "player_id": 1,
            "position": [3800, 0, 4600],
            "health_pct": 0.9,
            "is_in_vehicle": False,
            "vehicle_type": "",
        },
        {
            "player_id": 2,
            "position": [3820, 0, 4580],
            "health_pct": 1.0,
            "is_in_vehicle": False,
            "vehicle_type": "",
        },
    ],
    "current_weather": "Cloudy",
    "time_of_day": 14.5,
    "player_count": 2,
    "recent_events": [
        "player_connected:1",
        "player_connected:2",
        "trigger_entered:north_road:pos=<3800, 0, 4600>",
    ],
    "current_objective": "obj_1: Secure the northern checkpoint",
    "mission_time_seconds": 320.0,
    "wind_speed": 3.5,
    "wind_direction": 180,
    "total_spawned_groups": 2,
    "total_spawned_vehicles": 0,
}

# ─── Round-specific event pools ───
# Designed to trigger specific v3 features:
#   R2: Player aggression → triggers player profiling + threat scoring
#   R3: Flanking → tests behavior classification change
#   R4: Heavy casualties → tests adaptive difficulty + phase transition
#   R5: Night + reinforcements → tests environment analysis + escalation
ROUND_EVENTS = [
    # Round 2: Players push aggressively — kills trigger adaptive difficulty
    {
        "recent_events": [
            "shots_fired:player_1:pos=<3600, 0, 4700>",
            "shots_fired:player_1:pos=<3580, 0, 4680>",
            "enemy_killed:squad_1:member_3",
            "enemy_killed:squad_1:member_5",
            "enemy_killed:squad_1:member_7",
        ],
        "player_moves": [(3600, 0, 4700), (3610, 0, 4690)],
        "squad_damage": {"squad_1": {"strength": -3, "health_pct": 0.6}},
    },
    # Round 3: Player 2 flanks in vehicle — behavior reclassification
    {
        "recent_events": [
            "contact_lost:contact_1",
            "trigger_entered:east_flank:pos=<3900, 0, 4900>",
            "player_entered_vehicle:player_2:uaz469",
            "shots_fired:player_2:pos=<3950, 0, 4950>",
        ],
        "player_moves": [(3700, 0, 4750), (3900, 0, 4900)],
        "player_vehicle": {2: ("uaz469", True)},
        "contacts_update": [
            {
                "contact_id": "contact_2",
                "last_known_position": [3700, 0, 5000],
                "unit_type": "unknown",
                "confidence": 0.4,
                "seconds_since_seen": 10,
            }
        ],
    },
    # Round 4: Heavy escalation — AI takes losses, player gets hit
    {
        "recent_events": [
            "explosion:pos=<3650, 0, 4750>",
            "player_hit:player_1:health=0.5",
            "player_hit:player_1:health=0.3",
            "enemy_killed:squad_2:member_1",
            "enemy_killed:squad_2:member_2",
            "enemy_killed:squad_2:member_3",
            "shots_fired:player_1:pos=<3650, 0, 4760>",
            "shots_fired:player_2:pos=<3880, 0, 4870>",
        ],
        "player_moves": [(3650, 0, 4750), (3850, 0, 4850)],
        "player_damage": {1: 0.3},
        "squad_damage": {"squad_2": {"strength": -3, "health_pct": 0.25}},
        "weather_change": "Overcast",
    },
    # Round 5: Night falls, new player arrives, fog — max immersion test
    {
        "recent_events": [
            "player_connected:3",
            "reinforcement_request:north_sector",
            "objective_updated:obj_1:in_progress",
            "enemy_killed:squad_1:member_1",
        ],
        "new_player": {
            "player_id": 3,
            "position": [3400, 0, 4400],
            "health_pct": 1.0,
            "is_in_vehicle": False,
            "vehicle_type": "",
        },
        "player_moves": [(3650, 0, 4800), (3800, 0, 4850), (3400, 0, 4400)],
        "time_change": 21.5,
        "weather_change": "Foggy",
    },
]

# ─── Team perspectives for parallel requests ───
TEAM_PERSPECTIVES = [
    {
        "name": "Alpha (North Assault)",
        "sector": "north",
        "player_ids": [1],
        "recent_events": [
            "shots_fired:player_1:pos=<3800, 0, 4600>",
            "contact_confirmed:contact_1:infantry",
        ],
        "extra_contacts": [
            {
                "contact_id": "contact_alpha_1",
                "last_known_position": [3750, 0, 4550],
                "unit_type": "infantry_squad",
                "confidence": 0.9,
                "seconds_since_seen": 5,
            }
        ],
    },
    {
        "name": "Bravo (East Flank)",
        "sector": "east",
        "player_ids": [2],
        "recent_events": [
            "trigger_entered:east_flank:pos=<4100, 0, 4900>",
            "vehicle_spotted:btr70:pos=<4200, 0, 5000>",
        ],
        "extra_contacts": [
            {
                "contact_id": "contact_bravo_1",
                "last_known_position": [4200, 0, 5000],
                "unit_type": "apc",
                "confidence": 0.85,
                "seconds_since_seen": 12,
            }
        ],
    },
    {
        "name": "Charlie (South Overwatch)",
        "sector": "south",
        "player_ids": [],
        "recent_events": [
            "sniper_fire:pos=<3400, 0, 5200>",
            "smoke_observed:pos=<3300, 0, 5100>",
        ],
        "extra_contacts": [
            {
                "contact_id": "contact_charlie_1",
                "last_known_position": [3400, 0, 5200],
                "unit_type": "sniper_team",
                "confidence": 0.6,
                "seconds_since_seen": 30,
            }
        ],
    },
    {
        "name": "Delta (Recon)",
        "sector": "west",
        "player_ids": [],
        "recent_events": [
            "convoy_spotted:3_vehicles:heading_east:pos=<2800, 0, 4700>",
            "radio_intercept:enemy_reinforcements_inbound",
        ],
        "extra_contacts": [
            {
                "contact_id": "contact_delta_1",
                "last_known_position": [2800, 0, 4700],
                "unit_type": "vehicle_convoy",
                "confidence": 0.95,
                "seconds_since_seen": 2,
            },
            {
                "contact_id": "contact_delta_2",
                "last_known_position": [2600, 0, 4500],
                "unit_type": "unknown",
                "confidence": 0.3,
                "seconds_since_seen": 90,
            },
        ],
    },
    {
        "name": "Echo (QRF)",
        "sector": "north_east",
        "player_ids": [3],
        "recent_events": [
            "explosion:pos=<4000, 0, 4400>",
            "casualty_report:squad_3:2_wia",
        ],
        "extra_contacts": [
            {
                "contact_id": "contact_echo_1",
                "last_known_position": [4050, 0, 4350],
                "unit_type": "mortar_team",
                "confidence": 0.7,
                "seconds_since_seen": 15,
            }
        ],
    },
    {
        "name": "Foxtrot (Armor)",
        "sector": "center",
        "player_ids": [4],
        "recent_events": [
            "tank_engine:pos=<3200, 0, 4300>",
            "heat_signature:heavy_vehicle:pos=<3100, 0, 4200>",
        ],
        "extra_contacts": [
            {
                "contact_id": "contact_foxtrot_1",
                "last_known_position": [3100, 0, 4200],
                "unit_type": "mbt",
                "confidence": 0.8,
                "seconds_since_seen": 8,
            }
        ],
    },
    {
        "name": "Golf (Air Defense)",
        "sector": "south_west",
        "player_ids": [],
        "recent_events": [
            "helicopter_inbound:pos=<2500, 200, 5300>",
            "radar_lock:hostile_aircraft",
        ],
        "extra_contacts": [
            {
                "contact_id": "contact_golf_1",
                "last_known_position": [2500, 200, 5300],
                "unit_type": "helicopter",
                "confidence": 0.92,
                "seconds_since_seen": 3,
            }
        ],
    },
    {
        "name": "Hotel (Supply)",
        "sector": "rear",
        "player_ids": [],
        "recent_events": [
            "supply_convoy_arrived:pos=<2000, 0, 4000>",
            "ammo_resupply_complete:squad_1",
        ],
        "extra_contacts": [
            {
                "contact_id": "contact_hotel_1",
                "last_known_position": [2200, 0, 4100],
                "unit_type": "saboteur",
                "confidence": 0.4,
                "seconds_since_seen": 60,
            }
        ],
    },
    {
        "name": "India (Sniper)",
        "sector": "north_west",
        "player_ids": [5],
        "recent_events": [
            "target_eliminated:officer:pos=<3600, 0, 4100>",
            "reposition_needed:counter_sniper_detected",
        ],
        "extra_contacts": [
            {
                "contact_id": "contact_india_1",
                "last_known_position": [3550, 0, 4050],
                "unit_type": "counter_sniper",
                "confidence": 0.65,
                "seconds_since_seen": 20,
            }
        ],
    },
    {
        "name": "Juliet (Engineer)",
        "sector": "south_east",
        "player_ids": [6],
        "recent_events": [
            "minefield_detected:pos=<3900, 0, 5400>",
            "bridge_rigged:pos=<3700, 0, 5500>",
        ],
        "extra_contacts": [
            {
                "contact_id": "contact_juliet_1",
                "last_known_position": [3850, 0, 5350],
                "unit_type": "engineer_squad",
                "confidence": 0.75,
                "seconds_since_seen": 10,
            }
        ],
    },
]


def build_team_sitrep(base: dict, team: dict, team_idx: int) -> dict:
    """Build a team-specific SITREP by overlaying team perspective onto base state."""
    state = copy.deepcopy(base)
    state["reporting_team"] = team["name"]
    state["sector"] = team["sector"]
    state["mission_time_seconds"] += team_idx * 0.5
    state["recent_events"] = team["recent_events"] + state["recent_events"][:2]
    state["contacts"] = state["contacts"] + team.get("extra_contacts", [])
    if team["player_ids"]:
        state["reporting_players"] = team["player_ids"]
    for unit in state["enemy_units"]:
        unit["position"][0] += random.randint(-30, 30)
        unit["position"][2] += random.randint(-30, 30)
    return state


def evolve_sitrep(sitrep: dict, round_num: int, round_data: dict) -> dict:
    """Apply round-specific mutations to simulate an evolving battlefield."""
    state = copy.deepcopy(sitrep)
    state["mission_time_seconds"] += round_num * 60

    if "player_moves" in round_data:
        for i, pos in enumerate(round_data["player_moves"]):
            if i < len(state["players"]):
                state["players"][i]["position"] = list(pos)

    for pid, hp in round_data.get("player_damage", {}).items():
        for p in state["players"]:
            if p["player_id"] == pid:
                p["health_pct"] = hp

    for pid, (vtype, in_vehicle) in round_data.get("player_vehicle", {}).items():
        for p in state["players"]:
            if p["player_id"] == pid:
                p["is_in_vehicle"] = in_vehicle
                p["vehicle_type"] = vtype

    if "new_player" in round_data:
        state["players"].append(round_data["new_player"])
        state["player_count"] = len(state["players"])

    for uid, dmg in round_data.get("squad_damage", {}).items():
        for unit in state["enemy_units"]:
            if unit["unit_id"] == uid:
                unit["strength"] = max(1, unit["strength"] + dmg.get("strength", 0))
                unit["health_pct"] = dmg.get("health_pct", unit["health_pct"])

    if "contacts_update" in round_data:
        state["contacts"] = round_data["contacts_update"]
    if "recent_events" in round_data:
        state["recent_events"] = round_data["recent_events"]
    if "weather_change" in round_data:
        state["current_weather"] = round_data["weather_change"]
    if "time_change" in round_data:
        state["time_of_day"] = round_data["time_change"]

    for unit in state["enemy_units"]:
        unit["position"][0] += random.randint(-50, 50)
        unit["position"][2] += random.randint(-50, 50)

    return state


# ─── Thread-safe output ───
_print_lock = Lock()


def _safe_print(*args, **kwargs):
    with _print_lock:
        print(*args, **kwargs)


def _validate_target_position(pos: str) -> bool:
    """Check that target_position is in 'x,y,z' format (3 numbers separated by commas)."""
    if not pos or pos == "?":
        return False
    parts = pos.split(",")
    if len(parts) != 3:
        return False
    for part in parts:
        try:
            float(part.strip())
        except (ValueError, TypeError):
            return False
    return True


def format_response(data: dict) -> list[str]:
    """Format the LLM command response into lines, with per-command validation."""
    lines = []
    commands = data.get("commands", [])
    lines.append(f"    LLM Response: {len(commands)} commands")

    if data.get("narrative_broadcast"):
        lines.append(f"    BROADCAST: {data['narrative_broadcast']}")
    if data.get("weather_command"):
        lines.append(f"    WEATHER: {data['weather_command']}")
    if data.get("time_override", -1) >= 0:
        lines.append(f"    TIME: {data['time_override']}")

    valid_count = 0
    warning_count = 0

    for i, cmd in enumerate(commands):
        lines.append(f"    -- Command {i + 1} --")
        lines.append(f"    Type:      {cmd.get('command_type', '?')}")
        lines.append(f"    Unit:      {cmd.get('unit_id', '?')}")
        lines.append(f"    Target:    {cmd.get('target_position', '?')}")
        lines.append(f"    Params:    {cmd.get('parameters', '?')}")
        lines.append(f"    Narrative: {cmd.get('narrative', '')}")
        lines.append(f"    Reasoning: {cmd.get('reasoning', '')}")

        # --- Validation ---
        cmd_valid = True
        cmd_type = cmd.get("command_type", "")

        # Validate unit_id is not empty
        unit_id = cmd.get("unit_id", "")
        if not unit_id or unit_id == "?":
            lines.append(f"    [WARN] Missing or empty unit_id")
            cmd_valid = False

        # Validate target_position format
        target_pos = cmd.get("target_position", "")
        if target_pos and target_pos != "?" and not _validate_target_position(str(target_pos)):
            lines.append(f"    [WARN] Invalid target_position format: '{target_pos}' (expected 'x,y,z')")
            cmd_valid = False

        # Validate spawn commands have non-empty parameters (unit type)
        if cmd_type in ("spawn_infantry", "spawn_vehicle"):
            params = cmd.get("parameters", "")
            if not params or params == "?":
                lines.append(f"    [WARN] {cmd_type} missing parameters (unit type)")
                cmd_valid = False

        if cmd_valid:
            valid_count += 1
        else:
            warning_count += 1

    # Validation summary
    total = len(commands)
    if total > 0:
        if warning_count == 0:
            lines.append(f"    Validation: {valid_count}/{total} commands valid")
        else:
            lines.append(f"    Validation: {valid_count}/{total} commands valid ({warning_count} warning{'s' if warning_count != 1 else ''})")

    return lines


def send_sitrep(server_url: str, sitrep: dict, label: str) -> dict:
    """Send a SITREP and return result dict. Thread-safe output."""
    team = sitrep.get("reporting_team", "")
    header = f"  SITREP {label}" + (f" [{team}]" if team else "")

    info_lines = [
        f"\n{'─' * 50}",
        header,
        f"{'─' * 50}",
        f"    Mission time: {sitrep['mission_time_seconds']:.0f}s",
        f"    Players: {sitrep['player_count']}",
        f"    Enemy units: {len(sitrep['enemy_units'])}",
        f"    Contacts: {len(sitrep['contacts'])}",
        f"    Weather: {sitrep['current_weather']}",
        f"    Time of day: {sitrep['time_of_day']}",
        f"    Events: {sitrep['recent_events']}",
    ]
    if team:
        info_lines.append(f"    Sector: {sitrep.get('sector', '?')}")

    _safe_print("\n".join(info_lines))

    result = {"label": label, "team": team, "success": False, "data": None, "error": None}

    try:
        t0 = time.time()
        r = requests.post(
            f"{server_url}/api/game-master",
            json=sitrep,
            timeout=120,
        )
        elapsed = time.time() - t0

        if r.status_code == 200:
            data = r.json()
            result["success"] = True
            result["data"] = data
            result["elapsed"] = elapsed
            response_lines = [f"    Status: {r.status_code} ({elapsed:.1f}s)"] + format_response(data)
            _safe_print("\n".join(response_lines))
        else:
            result["error"] = r.text
            _safe_print(f"    Status: {r.status_code}\n    ERROR: {r.text}")

    except requests.Timeout:
        result["error"] = "timeout"
        _safe_print(f"    [{label}] ERROR: Request timed out")
    except Exception as e:
        result["error"] = str(e)
        _safe_print(f"    [{label}] ERROR: {e}")

    return result


def _staggered_send(server_url: str, sitrep: dict, label: str, stagger_delay: float):
    if stagger_delay > 0:
        time.sleep(stagger_delay)
    return send_sitrep(server_url, sitrep, label)


def send_parallel_sitreps(
    server_url: str,
    sitreps: list[tuple[dict, str]],
    stagger: float = 0.5,
) -> list[dict]:
    results = []
    with ThreadPoolExecutor(max_workers=len(sitreps)) as pool:
        futures = {}
        for i, (sit, label) in enumerate(sitreps):
            delay = i * stagger
            future = pool.submit(_staggered_send, server_url, sit, label, delay)
            futures[future] = label
        for future in as_completed(futures):
            results.append(future.result())
    return results


# ═══════════════════════════════════════════════════════════
# v3 Feature Inspection
# ═══════════════════════════════════════════════════════════

def inspect_tactical_intel(server_url: str):
    """Query and display the tactical intelligence analysis."""
    print(f"\n{'━' * 60}")
    print("  TACTICAL INTELLIGENCE")
    print(f"{'━' * 60}")
    try:
        r = requests.get(f"{server_url}/api/tactical-intel", timeout=5)
        if r.status_code != 200:
            print(f"    Endpoint returned {r.status_code}")
            return
        intel = r.json()
        if intel.get("status") == "no_data":
            print("    No data yet")
            return

        # Force disposition
        fd = intel.get("force_disposition", {})
        print(f"    FORCES: {fd.get('ai_squads', 0)} AI squads "
              f"({fd.get('ai_total_strength', 0)} troops, "
              f"{fd.get('ai_avg_health', 0):.0%} health) "
              f"vs {fd.get('player_count', 0)} players")
        print(f"    Force ratio: {fd.get('force_ratio', 0)}:1")

        # Threats
        threats = intel.get("threat_assessment", [])
        active = [t for t in threats if t.get("threat_score", 0) > 0.1]
        if active:
            print(f"    THREATS ({len(active)}):")
            for t in active[:5]:
                stale = " [STALE]" if t.get("stale") else ""
                print(f"      {t.get('contact_id', '?')}: {t.get('unit_type', '?')} "
                      f"(score: {t.get('threat_score', 0):.2f}){stale}")

        # Player profiles
        profiles = intel.get("player_profiles", {})
        if profiles:
            print("    PLAYER PROFILES:")
            for pid, p in profiles.items():
                conf = p.get("confidence", 0)
                if conf > 0.1:
                    print(f"      Player {pid}: {p.get('style', '?').replace('_', ' ')} "
                          f"(confidence: {conf:.0%}, speed: {p.get('movement_rate', 0):.0f}, "
                          f"engagements: {p.get('engagements', 0)})")

        # Adaptive difficulty
        ad = intel.get("adaptive_difficulty", {})
        print(f"    DIFFICULTY: {ad.get('assessment', '?')} "
              f"(modifier: x{ad.get('modifier', 1.0):.2f}, "
              f"player_kills: {ad.get('player_kills', 0)}, "
              f"ai_kills: {ad.get('ai_kills', 0)})")

        # Environment
        env = intel.get("environment", {})
        night = " NIGHT" if env.get("is_night") else ""
        dawn = " DUSK/DAWN" if env.get("is_dusk_dawn") else ""
        print(f"    ENVIRONMENT: {env.get('visibility_label', '?')} visibility "
              f"({env.get('visibility', 0):.0%}){night}{dawn}")
        print(f"      Tactical: {env.get('weather_tactical', '')}")

        # Territory
        terr = intel.get("territorial", {})
        if terr.get("front_line_distance", 0) > 0:
            closing = " (CLOSING)" if terr.get("front_is_closing") else ""
            print(f"    FRONT LINE: {terr['front_line_distance']:.0f}m{closing}")

        # Readiness
        rd = intel.get("readiness", {})
        reinf = " ** NEEDS REINFORCEMENT **" if rd.get("needs_reinforcement") else ""
        print(f"    READINESS: {rd.get('combat_ready', 0)} ready, "
              f"{rd.get('damaged', 0)} damaged "
              f"({rd.get('combat_effectiveness', 0):.0%} effective){reinf}")

    except Exception as e:
        print(f"    Error: {e}")


def inspect_battle_plan(server_url: str):
    """Query and display the current battle plan."""
    print(f"\n{'━' * 60}")
    print("  BATTLE PLAN")
    print(f"{'━' * 60}")
    try:
        r = requests.get(f"{server_url}/api/battle-plan", timeout=5)
        if r.status_code != 200:
            print(f"    Endpoint returned {r.status_code}")
            return
        bp = r.json()
        if bp.get("status") == "no_plan":
            print("    No battle plan yet")
            return

        print(f"    Phase:  {bp.get('phase', '?').upper().replace('_', ' ')}")
        print(f"    Turn:   {bp.get('turn', 0)}")

        ops = bp.get("active_operations", [])
        if ops:
            print(f"    Active operations ({len(ops)}):")
            for op in ops[:8]:
                print(f"      - {op.get('type', '?')} {op.get('unit', '?')} "
                      f"-> {op.get('target', '?')}")
                if op.get("reasoning"):
                    print(f"        Reason: {op['reasoning'][:80]}")

        lessons = bp.get("lessons_learned", [])
        if lessons:
            print("    Lessons learned:")
            for l in lessons[-3:]:
                print(f"      * {l}")

    except Exception as e:
        print(f"    Error: {e}")


def inspect_memory(server_url: str):
    """Query and display conversation memory state."""
    print(f"\n{'━' * 60}")
    print("  CONVERSATION MEMORY")
    print(f"{'━' * 60}")
    try:
        r = requests.get(f"{server_url}/api/memory", timeout=5)
        if r.status_code != 200:
            print(f"    Endpoint returned {r.status_code}")
            return
        mem = r.json()
        if not mem:
            print("    No sessions yet")
            return

        for sid, info in mem.items():
            count = info.get("message_count", 0)
            compacted = info.get("compacted_summary", "")
            print(f"    Session '{sid}': {count} messages")

            if compacted:
                print(f"    COMPACTED SUMMARY:")
                for line in compacted.split("\n")[:6]:
                    print(f"      {line.strip()}")
                if compacted.count("\n") > 6:
                    print(f"      ... ({compacted.count(chr(10)) - 6} more lines)")

            msgs = info.get("messages", [])
            if msgs:
                print(f"    Recent messages ({len(msgs)}):")
                for msg in msgs[-4:]:
                    role = msg["role"].upper()
                    content = msg["content"][:100]
                    print(f"      [{role}] {content}")

    except Exception as e:
        print(f"    Error: {e}")


def inspect_stats(server_url: str):
    """Query and display full server stats."""
    print(f"\n{'━' * 60}")
    print("  SERVER STATISTICS")
    print(f"{'━' * 60}")
    try:
        r = requests.get(f"{server_url}/stats", timeout=5)
        if r.status_code != 200:
            print(f"    Endpoint returned {r.status_code}")
            return
        s = r.json()

        print(f"    Uptime:      {s.get('uptime_seconds', 0):.0f}s")
        print(f"    Requests:    {s.get('total_requests', 0)}")
        print(f"    Errors:      {s.get('total_errors', 0)} "
              f"({s.get('error_rate', 0):.1f}%)")
        print(f"    Commands:    {s.get('total_commands_issued', 0)} "
              f"(avg {s.get('avg_cmds', 0):.1f}/req)")
        print(f"    Tokens:      {s.get('total_input_tokens', 0)} in / "
              f"{s.get('total_output_tokens', 0)} out")
        print(f"    Compactions: {s.get('compaction_runs', 0)}")
        print(f"    Difficulty:  x{s.get('difficulty', 1.0):.2f} "
              f"(kills: player {s.get('player_kills', 0)} / ai {s.get('ai_kills', 0)})")

        sessions = s.get("sessions", {})
        if sessions:
            print(f"    Sessions ({len(sessions)}):")
            for sid, info in sessions.items():
                phase = info.get("phase", "?")
                turn = info.get("turn", 0)
                msgs = info.get("messages", 0)
                comp = " [compacted]" if info.get("compacted") else ""
                print(f"      {sid}: {msgs} msgs, turn {turn}, "
                      f"phase={phase}{comp}")

    except Exception as e:
        print(f"    Error: {e}")


def inspect_post_battle(server_url: str, all_round_stats: list[dict]):
    """Generate a comprehensive post-battle analysis from stats and intel."""
    # ── Battle Outcome ──
    print(f"\n{'━' * 60}")
    print("  BATTLE OUTCOME")
    print(f"{'━' * 60}")
    try:
        stats_r = requests.get(f"{server_url}/stats", timeout=5)
        intel_r = requests.get(f"{server_url}/api/tactical-intel", timeout=5)
        plan_r = requests.get(f"{server_url}/api/battle-plan", timeout=5)
    except Exception as e:
        print(f"    Error fetching data: {e}")
        return

    s = stats_r.json() if stats_r.status_code == 200 else {}
    intel = intel_r.json() if intel_r.status_code == 200 else {}
    plan = plan_r.json() if plan_r.status_code == 200 else {}

    # Kills & difficulty
    p_kills = s.get("player_kills", 0)
    ai_kills = s.get("ai_kills", 0)
    difficulty = s.get("difficulty", 1.0)
    total_cmds = s.get("total_commands_issued", 0)
    total_reqs = s.get("total_requests", 0)
    errors = s.get("total_errors", 0)

    print(f"    Kills — Player: {p_kills}  AI: {ai_kills}")
    if p_kills + ai_kills > 0:
        ratio = p_kills / max(ai_kills, 1)
        print(f"    Kill ratio (player/AI): {ratio:.2f}")
    print(f"    Final difficulty: x{difficulty:.2f}")
    print(f"    Total commands issued: {total_cmds} across {total_reqs} requests")
    if errors:
        print(f"    Errors: {errors} ({errors / max(total_reqs, 1) * 100:.1f}%)")
    else:
        print(f"    Errors: 0 (clean run)")

    # LLM reliability
    empty_rate = s.get("empty_response_rate", 0)
    parse_rate = s.get("parse_failure_rate", 0)
    if empty_rate > 0 or parse_rate > 0:
        print(f"    LLM reliability — empty: {empty_rate:.1f}%, parse fail: {parse_rate:.1f}%")

    # ── Tactic Performance ──
    tactic_perf = s.get("tactic_performance", {})
    has_tactic_data = False
    for sid, perf in tactic_perf.items():
        outcomes = perf.get("tactic_outcomes", {})
        if not outcomes:
            continue
        has_tactic_data = True
        print(f"\n{'━' * 60}")
        print(f"  TACTIC EFFECTIVENESS (session: {sid})")
        print(f"{'━' * 60}")
        for tactic, data in sorted(outcomes.items(), key=lambda x: -x[1].get("issued", 0)):
            bar_len = min(data.get("issued", 0), 20)
            success_bar = "█" * min(data.get("successful", 0), bar_len)
            fail_bar = "░" * min(data.get("failed", 0), bar_len - len(success_bar))
            pend_bar = "·" * max(0, bar_len - len(success_bar) - len(fail_bar))
            print(f"    {tactic:20s} {success_bar}{fail_bar}{pend_bar} "
                  f"{data.get('success_rate', 0):.0%} "
                  f"({data.get('successful', 0)}/{data.get('issued', 0)})")
        best = perf.get("most_effective_tactic")
        worst = perf.get("least_effective_tactic")
        if best:
            print(f"    Best tactic:  {best}")
        if worst:
            print(f"    Worst tactic: {worst}")
        avg_surv = perf.get("avg_unit_survival_seconds", 0)
        if avg_surv > 0:
            print(f"    Avg unit survival: {avg_surv:.0f}s")
        pending = perf.get("pending_orders", 0)
        if pending:
            print(f"    Pending orders: {pending}")

    if not has_tactic_data:
        print(f"\n    No tactic outcome data recorded (orders not yet resolved)")

    # ── Force Status ──
    if intel.get("status") != "no_data":
        fd = intel.get("force_disposition", {})
        rd = intel.get("readiness", {})
        ad = intel.get("adaptive_difficulty", {})
        print(f"\n{'━' * 60}")
        print("  FINAL FORCE STATUS")
        print(f"{'━' * 60}")
        print(f"    AI forces: {fd.get('ai_squads', 0)} squads, "
              f"{fd.get('ai_total_strength', 0)} troops, "
              f"{fd.get('ai_avg_health', 0):.0%} health")
        print(f"    Combat effectiveness: {rd.get('combat_effectiveness', 0):.0%}")
        if rd.get("needs_reinforcement"):
            print(f"    [WARN] Forces need reinforcement")
        assessment = ad.get("assessment", "unknown")
        print(f"    Difficulty assessment: {assessment}")

    # ── Battle Plan Final State ──
    if plan.get("status") != "no_plan":
        phase = plan.get("phase", "?")
        turn = plan.get("turn", 0)
        lessons = plan.get("lessons_learned", [])
        print(f"\n{'━' * 60}")
        print("  STRATEGIC SUMMARY")
        print(f"{'━' * 60}")
        print(f"    Final phase: {phase.upper().replace('_', ' ')}")
        print(f"    Turns completed: {turn}")
        if lessons:
            print(f"    Lessons learned ({len(lessons)}):")
            for lesson in lessons:
                print(f"      * {lesson}")
        else:
            print(f"    No lessons recorded")

    # ── Per-round response time sparkline ──
    round_times = []
    for rs in all_round_stats:
        elapsed_vals = [r.get("elapsed", 0) for r in rs["results"] if r.get("elapsed")]
        if elapsed_vals:
            round_times.append(sum(elapsed_vals) / len(elapsed_vals))
    if round_times:
        spark_chars = "▁▂▃▄▅▆▇█"
        mn, mx = min(round_times), max(round_times)
        rng = mx - mn if mx > mn else 1
        spark = "".join(
            spark_chars[min(int((t - mn) / rng * (len(spark_chars) - 1)), len(spark_chars) - 1)]
            for t in round_times
        )
        print(f"\n    Response time trend: {spark}  "
              f"(min {min(round_times):.1f}s / max {max(round_times):.1f}s)")


def analyze_command_patterns(round_stats: list[dict]):
    """Analyze command patterns across all rounds and print a concise report.

    Each entry in round_stats is a dict with keys:
      - round: int
      - results: list of result dicts (each with 'success', 'data', 'elapsed', 'team')
    """
    print(f"\n{'━' * 60}")
    print("  COMMAND PATTERN ANALYSIS")
    print(f"{'━' * 60}")

    # Aggregate command type counts across all rounds
    total_cmd_types = defaultdict(int)
    team_cmd_counts = defaultdict(int)  # team -> total commands
    team_zero_rounds = defaultdict(int)  # team -> rounds with 0 commands
    round_times = []  # avg response time per round

    for rs in round_stats:
        rnd = rs["round"]
        round_elapsed_values = []
        for res in rs["results"]:
            team_label = res.get("team", "") or res.get("label", f"R{rnd}")
            if res.get("success") and res.get("data"):
                cmds = res["data"].get("commands", [])
                team_cmd_counts[team_label] += len(cmds)
                if len(cmds) == 0:
                    team_zero_rounds[team_label] += 1
                for cmd in cmds:
                    total_cmd_types[cmd.get("command_type", "unknown")] += 1
            else:
                team_zero_rounds[team_label] += 1

            elapsed = res.get("elapsed", 0)
            if elapsed > 0:
                round_elapsed_values.append(elapsed)

        if round_elapsed_values:
            round_times.append(sum(round_elapsed_values) / len(round_elapsed_values))

    # Print command type distribution
    if total_cmd_types:
        print("    Command type distribution:")
        for ctype, count in sorted(total_cmd_types.items(), key=lambda x: -x[1]):
            print(f"      {ctype}: {count}")
    else:
        print("    No commands generated across all rounds.")

    # Teams with consistent 0-command rounds
    zero_issues = {t: c for t, c in team_zero_rounds.items() if c > 1}
    if zero_issues:
        print("    Teams with repeated 0-command rounds:")
        for t, c in zero_issues.items():
            print(f"      {t}: {c} rounds with 0 commands")

    # Response time trend — ignore small increases (<10%) that are likely noise
    if len(round_times) >= 3:
        significant_increases = sum(
            1 for i in range(1, len(round_times))
            if round_times[i] > round_times[i - 1] * 1.10
        )
        # Also check if last round is >50% slower than first (overall degradation)
        overall_growth = (round_times[-1] - round_times[0]) / max(round_times[0], 0.1)
        if significant_increases >= len(round_times) - 1 or overall_growth > 1.0:
            print(f"    [WARN] Response times trending UP across rounds "
                  f"(possible context window bloat)")
            for i, t in enumerate(round_times):
                pct = ""
                if i > 0:
                    change = (t - round_times[i - 1]) / max(round_times[i - 1], 0.1) * 100
                    pct = f" ({change:+.0f}%)"
                print(f"      Round {i + 1}: {t:.1f}s{pct}")
        else:
            print(f"    Response time trend: stable "
                  f"(avg {sum(round_times) / len(round_times):.1f}s)")
    elif round_times:
        print(f"    Response time avg: {sum(round_times) / len(round_times):.1f}s")

    return total_cmd_types, team_zero_rounds, round_times


# ═══════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="God Mode v3 middleware test client — tests all advanced features"
    )
    parser.add_argument("server_url", nargs="?", default=DEFAULT_URL, help="Server URL")
    parser.add_argument("--rounds", type=int, default=1,
                        help="Number of SITREP rounds (max 5)")
    parser.add_argument("--delay", type=float, default=2.0,
                        help="Delay in seconds between rounds")
    parser.add_argument("--parallel", type=int, default=0,
                        help="Concurrent team SITREPs per round (2-10)")
    parser.add_argument("--stagger", type=float, default=0.5,
                        help="Seconds between parallel requests")
    parser.add_argument("--no-intel", action="store_true",
                        help="Skip tactical intelligence inspection between rounds")
    parser.add_argument("--validate", action="store_true",
                        help="Strict validation mode: exit code 1 if error rate >10%%, "
                             "any team returns 0 commands more than once, "
                             "or avg response time >60s")
    args = parser.parse_args()

    rounds = min(args.rounds, len(ROUND_EVENTS) + 1)
    parallel = min(args.parallel, len(TEAM_PERSPECTIVES))

    print("=" * 60)
    print("  God Mode v3 — Advanced Tactical AI Test")
    print(f"  Server:   {args.server_url}")
    print(f"  Rounds:   {rounds}")
    if parallel:
        print(f"  Parallel: {parallel} concurrent team SITREPs")
        print(f"  Stagger:  {args.stagger}s")
    if rounds > 1:
        print(f"  Delay:    {args.delay}s between rounds")
    print(f"  Features: tactical-intel, battle-plan, memory, adaptive-difficulty")
    print("=" * 60)

    # ── Health check ──
    print("\n[*] Health check...")
    try:
        r = requests.get(f"{args.server_url}/health", timeout=5)
        h = r.json()
        print(f"    Status:  {h.get('status')}")
        print(f"    Version: {h.get('version', '?')}")
        print(f"    LLM:     {h.get('llm_provider', '?')} / {h.get('model', '?')}")
        fb = h.get("fallback_provider")
        if fb:
            print(f"    Fallback: {fb} / {h.get('fallback_model', '?')}")
        print(f"    Memory:  {h.get('memory_messages', 0)} msgs across "
              f"{h.get('active_sessions', 0)} sessions")
    except requests.ConnectionError:
        print(f"    ERROR: Cannot connect to {args.server_url}")
        print("    Start the server: bash start.sh")
        sys.exit(1)

    # ── Reset for clean test ──
    print("\n[*] Resetting server state...")
    try:
        r = requests.post(f"{args.server_url}/api/reset", timeout=5)
        print(f"    {r.json()}")
    except Exception:
        print("    Reset failed (non-critical)")

    # ── Send SITREPs ──
    sitrep = copy.deepcopy(BASE_SITREP)
    total_sent = 0
    total_ok = 0
    all_round_stats = []  # for analyze_command_patterns
    cumulative_tokens_in = 0
    cumulative_tokens_out = 0

    for rnd in range(1, rounds + 1):
        if rnd > 1:
            round_idx = rnd - 2
            if round_idx < len(ROUND_EVENTS):
                sitrep = evolve_sitrep(sitrep, rnd, ROUND_EVENTS[round_idx])
            print(f"\n    (waiting {args.delay}s...)")
            time.sleep(args.delay)

        print(f"\n{'━' * 60}")
        if parallel >= 2:
            print(f"  ROUND {rnd}/{rounds} — {parallel} teams in parallel")
        else:
            print(f"  ROUND {rnd}/{rounds}")
        print(f"{'━' * 60}")

        round_results = []

        if parallel >= 2:
            teams = TEAM_PERSPECTIVES[:parallel]
            batch = [
                (build_team_sitrep(sitrep, team, i), f"R{rnd}-T{i+1}")
                for i, team in enumerate(teams)
            ]
            results = send_parallel_sitreps(args.server_url, batch, stagger=args.stagger)
            round_results = results

            total_sent += len(results)
            total_ok += sum(1 for r in results if r["success"])

            print(f"\n    Round {rnd} summary:")
            for res in sorted(results, key=lambda x: x["label"]):
                status = "OK" if res["success"] else f"FAIL ({res['error']})"
                elapsed = f" ({res.get('elapsed', 0):.1f}s)" if res["success"] else ""
                cmd_count = len(res["data"].get("commands", [])) if res["data"] else 0
                print(f"      {res['label']} {res['team']}: "
                      f"{status}{elapsed} — {cmd_count} commands")
        else:
            result = send_sitrep(args.server_url, sitrep, f"#{rnd}")
            round_results = [result]
            total_sent += 1
            if result["success"]:
                total_ok += 1

        # ── Per-round performance summary ──
        round_cmd_types = defaultdict(int)
        round_elapsed_vals = []
        round_tokens_in = 0
        round_tokens_out = 0
        for res in round_results:
            if res.get("elapsed"):
                round_elapsed_vals.append(res["elapsed"])
            if res.get("success") and res.get("data"):
                data = res["data"]
                for cmd in data.get("commands", []):
                    round_cmd_types[cmd.get("command_type", "unknown")] += 1
                # Track tokens if present in response
                round_tokens_in += data.get("input_tokens", 0) or data.get("tokens_in", 0)
                round_tokens_out += data.get("output_tokens", 0) or data.get("tokens_out", 0)

        cumulative_tokens_in += round_tokens_in
        cumulative_tokens_out += round_tokens_out

        avg_time = (sum(round_elapsed_vals) / len(round_elapsed_vals)) if round_elapsed_vals else 0
        print(f"\n    -- Round {rnd} Performance --")
        print(f"    Avg response time: {avg_time:.1f}s")
        if round_cmd_types:
            dist_str = ", ".join(f"{k}: {v}" for k, v in sorted(round_cmd_types.items(), key=lambda x: -x[1]))
            print(f"    Command types: {dist_str}")
        else:
            print(f"    Command types: (none)")
        if round_tokens_in or round_tokens_out:
            print(f"    Tokens this round: {round_tokens_in} in / {round_tokens_out} out "
                  f"(cumulative: {cumulative_tokens_in} in / {cumulative_tokens_out} out)")

        all_round_stats.append({"round": rnd, "results": round_results})

        # ── Inspect v3 features after each round ──
        if not args.no_intel:
            inspect_tactical_intel(args.server_url)
            inspect_battle_plan(args.server_url)

    # ── Final inspection ──
    print(f"\n\n{'═' * 60}")
    print("  POST-BATTLE ANALYSIS")
    print(f"{'═' * 60}")

    inspect_post_battle(args.server_url, all_round_stats)
    inspect_memory(args.server_url)
    inspect_stats(args.server_url)

    # ── Command pattern analysis ──
    total_cmd_types, team_zero_rounds, round_times = analyze_command_patterns(all_round_stats)

    # ── Summary ──
    print(f"\n{'═' * 60}")
    print(f"  Test complete: {total_ok}/{total_sent} requests succeeded")
    print(f"{'═' * 60}")

    # ── Validate mode ──
    if args.validate:
        failures = []
        error_rate = ((total_sent - total_ok) / total_sent * 100) if total_sent > 0 else 0
        if error_rate > 10:
            failures.append(f"Error rate {error_rate:.1f}% exceeds 10% threshold")

        repeat_zero_teams = {t: c for t, c in team_zero_rounds.items() if c > 1}
        if repeat_zero_teams:
            for t, c in repeat_zero_teams.items():
                failures.append(f"Team '{t}' returned 0 commands in {c} rounds (>1)")

        avg_response = (sum(round_times) / len(round_times)) if round_times else 0
        if avg_response > 60:
            failures.append(f"Avg response time {avg_response:.1f}s exceeds 60s threshold")

        if failures:
            print(f"\n{'═' * 60}")
            print("  VALIDATION FAILED")
            print(f"{'═' * 60}")
            for f in failures:
                print(f"    [FAIL] {f}")
            sys.exit(1)
        else:
            print(f"\n  Validation passed (error rate: {error_rate:.1f}%, "
                  f"avg response: {avg_response:.1f}s)")


# ═══════════════════════════════════════════════════════════
# Unit Tests — TacticalAnalyzer feature tests
# ═══════════════════════════════════════════════════════════
#
# These tests import TacticalAnalyzer directly and validate
# the new tactical features without requiring a running server.
# Run them via: python test_server.py --unit-tests
#   or individually: python -c "from test_server import *; test_morale_warnings()"

import math

# Lazy import of TacticalAnalyzer — only resolved when unit tests run
_TacticalAnalyzer = None


def _get_analyzer():
    """Import TacticalAnalyzer from server module on first use."""
    global _TacticalAnalyzer
    if _TacticalAnalyzer is None:
        import importlib
        import os
        import sys as _sys
        # Ensure middleware directory is on the path
        middleware_dir = os.path.dirname(os.path.abspath(__file__))
        if middleware_dir not in _sys.path:
            _sys.path.insert(0, middleware_dir)
        mod = importlib.import_module("server")
        _TacticalAnalyzer = mod.TacticalAnalyzer
    return _TacticalAnalyzer()


def _make_game_state(
    enemy_units=None,
    players=None,
    contacts=None,
    recent_events=None,
    **overrides,
) -> dict:
    """Build a minimal game_state dict for unit testing."""
    state = {
        "enemy_units": enemy_units or [],
        "players": players or [],
        "contacts": contacts or [],
        "recent_events": recent_events or [],
        "current_weather": "Clear",
        "time_of_day": 12.0,
        "wind_speed": 2.0,
        "wind_direction": 0,
        "player_count": len(players) if players else 0,
        "current_objective": "test",
        "mission_time_seconds": 100.0,
        "total_spawned_groups": 0,
        "total_spawned_vehicles": 0,
    }
    state.update(overrides)
    return state


# ─── 1. Morale system tests ────────────────────────────────

def test_morale_retreat_warning():
    """Units below 30% HP should produce a RETREAT morale warning."""
    analyzer = _get_analyzer()
    game_state = _make_game_state(
        enemy_units=[
            {"unit_id": "broken_squad", "unit_type": "infantry_squad",
             "position": [100, 0, 100], "strength": 5, "health_pct": 0.2,
             "current_order": "defend", "faction": "ussr"},
            {"unit_id": "healthy_squad", "unit_type": "infantry_squad",
             "position": [200, 0, 200], "strength": 8, "health_pct": 0.9,
             "current_order": "patrol", "faction": "ussr"},
        ],
        players=[{"player_id": 1, "position": [500, 0, 500],
                  "health_pct": 1.0, "is_in_vehicle": False, "vehicle_type": ""}],
    )
    result = analyzer.analyze(game_state)
    morale = result.get("morale", {})
    warnings = morale.get("warnings", [])

    retreat_ids = [w["unit_id"] for w in warnings if "RETREAT" in w["recommendation"]]
    assert "broken_squad" in retreat_ids, (
        f"Expected broken_squad (20% HP) to trigger RETREAT warning, got: {retreat_ids}"
    )
    assert "healthy_squad" not in retreat_ids, (
        "healthy_squad (90% HP) should NOT trigger RETREAT warning"
    )
    print("  [PASS] test_morale_retreat_warning")


def test_morale_merge_recommendation():
    """Units below 50% HP with strength <= 2 should produce a MERGE recommendation."""
    analyzer = _get_analyzer()
    game_state = _make_game_state(
        enemy_units=[
            {"unit_id": "weak_squad", "unit_type": "infantry_squad",
             "position": [100, 0, 100], "strength": 2, "health_pct": 0.4,
             "current_order": "defend", "faction": "ussr"},
            {"unit_id": "strong_low_hp", "unit_type": "infantry_squad",
             "position": [200, 0, 200], "strength": 6, "health_pct": 0.45,
             "current_order": "defend", "faction": "ussr"},
        ],
        players=[{"player_id": 1, "position": [500, 0, 500],
                  "health_pct": 1.0, "is_in_vehicle": False, "vehicle_type": ""}],
    )
    result = analyzer.analyze(game_state)
    warnings = result.get("morale", {}).get("warnings", [])

    merge_ids = [w["unit_id"] for w in warnings if "MERGE" in w["recommendation"]]
    assert "weak_squad" in merge_ids, (
        f"Expected weak_squad (40% HP, strength 2) to trigger MERGE, got: {merge_ids}"
    )
    # strong_low_hp has strength 6, so it should NOT trigger MERGE despite low HP
    assert "strong_low_hp" not in merge_ids, (
        "strong_low_hp (strength 6) should NOT trigger MERGE — only understrength units"
    )
    print("  [PASS] test_morale_merge_recommendation")


def test_morale_overall_critical():
    """Overall morale should be 'critical' when >40% of units are broken (<=30% HP)."""
    analyzer = _get_analyzer()
    game_state = _make_game_state(
        enemy_units=[
            {"unit_id": f"squad_{i}", "unit_type": "infantry_squad",
             "position": [100 * i, 0, 100], "strength": 3,
             "health_pct": 0.2 if i <= 3 else 0.9,  # 3/5 broken = 60%
             "current_order": "defend", "faction": "ussr"}
            for i in range(1, 6)
        ],
        players=[{"player_id": 1, "position": [900, 0, 900],
                  "health_pct": 1.0, "is_in_vehicle": False, "vehicle_type": ""}],
    )
    result = analyzer.analyze(game_state)
    overall = result.get("morale", {}).get("overall", "")
    assert overall == "critical", (
        f"Expected morale 'critical' with 60% broken units, got: '{overall}'"
    )
    print("  [PASS] test_morale_overall_critical")


# ─── 2. Counter-ambush detection tests ─────────────────────

def test_counter_ambush_stationary_engaged():
    """Stationary players with engagements should trigger ambush warnings."""
    analyzer = _get_analyzer()

    # Simulate a player who has been stationary across multiple SITREPs
    # by feeding multiple game states to build up position history and engagements.
    player = {"player_id": 10, "position": [500, 0, 500],
              "health_pct": 0.8, "is_in_vehicle": False, "vehicle_type": "",
              "velocity": [0, 0, 0]}
    enemy = {"unit_id": "target_squad", "unit_type": "infantry_squad",
             "position": [600, 0, 600], "strength": 4, "health_pct": 0.7,
             "current_order": "patrol", "faction": "ussr"}

    # Feed 3 SITREPs with same position and engagement events
    for i in range(3):
        gs = _make_game_state(
            enemy_units=[enemy],
            players=[player],
            recent_events=[
                "shots_fired:player_10:pos=<500, 0, 500>",
                "shots_fired:player_10:pos=<500, 0, 500>",
            ],
        )
        result = analyzer.analyze(gs)

    # After 3 rounds of stationary + engaging, ambush warning should appear
    spatial = result.get("spatial", {})
    ambush_warnings = spatial.get("ambush_warnings", [])
    warned_pids = [w["player_id"] for w in ambush_warnings]
    assert 10 in warned_pids, (
        f"Expected ambush warning for stationary player 10 with engagements, "
        f"got warnings for: {warned_pids}"
    )
    print("  [PASS] test_counter_ambush_stationary_engaged")


def test_counter_ambush_no_warning_for_moving_player():
    """Moving players should NOT trigger ambush warnings, even with engagements."""
    analyzer = _get_analyzer()

    # Feed 3 SITREPs with significantly different positions each time
    for i in range(3):
        player = {"player_id": 20, "position": [100 + i * 200, 0, 100 + i * 200],
                  "health_pct": 1.0, "is_in_vehicle": False, "vehicle_type": "",
                  "velocity": [5, 0, 5]}
        gs = _make_game_state(
            enemy_units=[{"unit_id": "s1", "unit_type": "infantry_squad",
                          "position": [800, 0, 800], "strength": 6,
                          "health_pct": 0.9, "current_order": "patrol",
                          "faction": "ussr"}],
            players=[player],
            recent_events=[
                "shots_fired:player_20:pos=<500, 0, 500>",
                "shots_fired:player_20:pos=<500, 0, 500>",
            ],
        )
        result = analyzer.analyze(gs)

    spatial = result.get("spatial", {})
    ambush_warnings = spatial.get("ambush_warnings", [])
    warned_pids = [w["player_id"] for w in ambush_warnings]
    assert 20 not in warned_pids, (
        f"Moving player 20 should NOT trigger ambush warning, "
        f"but got warnings for: {warned_pids}"
    )
    print("  [PASS] test_counter_ambush_no_warning_for_moving_player")


# ─── 3. Enhanced player profiling tests ─────────────────────

def test_player_profile_high_confidence_at_5_positions():
    """Confidence should reach 1.0 (max) at 5 position samples, not 10."""
    analyzer = _get_analyzer()

    player = {"player_id": 30, "position": [0, 0, 0],
              "health_pct": 1.0, "is_in_vehicle": False, "vehicle_type": ""}

    # Feed exactly 5 SITREPs with varying positions
    for i in range(5):
        player_copy = copy.deepcopy(player)
        player_copy["position"] = [100 * i, 0, 100 * i]
        gs = _make_game_state(
            players=[player_copy],
            enemy_units=[{"unit_id": "s1", "unit_type": "infantry_squad",
                          "position": [2000, 0, 2000], "strength": 6,
                          "health_pct": 0.9, "current_order": "patrol",
                          "faction": "ussr"}],
        )
        result = analyzer.analyze(gs)

    profiles = result.get("player_profiles", {})
    confidence = profiles.get(30, {}).get("confidence", 0)
    assert confidence >= 1.0, (
        f"Expected confidence >= 1.0 after 5 positions (formula: min(len/5, 1.0)), "
        f"got: {confidence}"
    )
    print("  [PASS] test_player_profile_high_confidence_at_5_positions")


def test_player_profile_low_confidence_at_3_positions():
    """At 3 positions, confidence should be 3/5 = 0.6, not 1.0."""
    analyzer = _get_analyzer()

    for i in range(3):
        gs = _make_game_state(
            players=[{"player_id": 31, "position": [50 * i, 0, 50 * i],
                      "health_pct": 1.0, "is_in_vehicle": False, "vehicle_type": ""}],
            enemy_units=[{"unit_id": "s1", "unit_type": "infantry_squad",
                          "position": [2000, 0, 2000], "strength": 6,
                          "health_pct": 0.9, "current_order": "patrol",
                          "faction": "ussr"}],
        )
        result = analyzer.analyze(gs)

    profiles = result.get("player_profiles", {})
    confidence = profiles.get(31, {}).get("confidence", 0)
    assert abs(confidence - 0.6) < 0.01, (
        f"Expected confidence ~0.6 after 3 positions, got: {confidence}"
    )
    print("  [PASS] test_player_profile_low_confidence_at_3_positions")


# ─── 4. Exponential threat decay tests ──────────────────────

def test_threat_score_exponential_decay_at_60s():
    """At staleness=60s, threat_score should be ~37% of confidence (e^-1)."""
    analyzer = _get_analyzer()
    confidence = 0.9
    staleness = 60
    gs = _make_game_state(
        contacts=[{
            "contact_id": "decay_test_60",
            "last_known_position": [500, 0, 500],
            "unit_type": "infantry_squad",
            "confidence": confidence,
            "seconds_since_seen": staleness,
        }],
        players=[{"player_id": 1, "position": [100, 0, 100],
                  "health_pct": 1.0, "is_in_vehicle": False, "vehicle_type": ""}],
    )
    result = analyzer.analyze(gs)
    threats = result.get("threat_assessment", [])
    threat = next((t for t in threats if t["contact_id"] == "decay_test_60"), None)
    assert threat is not None, "Contact decay_test_60 not found in threat assessment"

    expected = confidence * math.exp(-staleness / 60.0)  # 0.9 * e^-1 ~ 0.331
    actual = threat["threat_score"]
    assert abs(actual - round(expected, 2)) < 0.02, (
        f"At staleness=60, expected threat_score ~{expected:.3f}, got: {actual}"
    )
    # Verify it is roughly 37% of original confidence
    ratio = actual / confidence
    assert 0.35 <= ratio <= 0.40, (
        f"At staleness=60, score should be ~37% of confidence, ratio was: {ratio:.3f}"
    )
    print("  [PASS] test_threat_score_exponential_decay_at_60s")


def test_threat_score_exponential_decay_at_120s():
    """At staleness=120s, threat_score should be ~13% of confidence (e^-2)."""
    analyzer = _get_analyzer()
    confidence = 0.9
    staleness = 120
    gs = _make_game_state(
        contacts=[{
            "contact_id": "decay_test_120",
            "last_known_position": [500, 0, 500],
            "unit_type": "infantry_squad",
            "confidence": confidence,
            "seconds_since_seen": staleness,
        }],
        players=[{"player_id": 1, "position": [100, 0, 100],
                  "health_pct": 1.0, "is_in_vehicle": False, "vehicle_type": ""}],
    )
    result = analyzer.analyze(gs)
    threats = result.get("threat_assessment", [])
    threat = next((t for t in threats if t["contact_id"] == "decay_test_120"), None)
    assert threat is not None, "Contact decay_test_120 not found in threat assessment"

    expected = confidence * math.exp(-staleness / 60.0)  # 0.9 * e^-2 ~ 0.122
    actual = threat["threat_score"]
    assert abs(actual - round(expected, 2)) < 0.02, (
        f"At staleness=120, expected threat_score ~{expected:.3f}, got: {actual}"
    )
    ratio = actual / confidence
    assert 0.11 <= ratio <= 0.16, (
        f"At staleness=120, score should be ~13% of confidence, ratio was: {ratio:.3f}"
    )
    print("  [PASS] test_threat_score_exponential_decay_at_120s")


def test_threat_score_fresh_contact_near_full():
    """A fresh contact (staleness=0) should have threat_score equal to confidence."""
    analyzer = _get_analyzer()
    confidence = 0.85
    gs = _make_game_state(
        contacts=[{
            "contact_id": "fresh_contact",
            "last_known_position": [300, 0, 300],
            "unit_type": "infantry_squad",
            "confidence": confidence,
            "seconds_since_seen": 0,
        }],
        players=[{"player_id": 1, "position": [100, 0, 100],
                  "health_pct": 1.0, "is_in_vehicle": False, "vehicle_type": ""}],
    )
    result = analyzer.analyze(gs)
    threats = result.get("threat_assessment", [])
    threat = next((t for t in threats if t["contact_id"] == "fresh_contact"), None)
    assert threat is not None, "Contact fresh_contact not found in threat assessment"

    actual = threat["threat_score"]
    assert abs(actual - confidence) < 0.02, (
        f"Fresh contact (staleness=0) should have threat_score={confidence}, got: {actual}"
    )
    print("  [PASS] test_threat_score_fresh_contact_near_full")


# ─── 5. Difficulty smoothing tests (EMA alpha=0.1) ─────────

def test_difficulty_ema_smoothing_less_oscillation():
    """EMA smoothing (alpha=0.1) should produce less oscillation than flat adjustments.

    We simulate alternating rounds where players dominate and AI dominates,
    and verify the EMA-smoothed modifier stays closer to 1.0 than flat +/-0.05 would.
    """
    analyzer = _get_analyzer()

    flat_modifier = 1.0
    ema_values = []
    flat_values = []

    for cycle in range(20):
        if cycle % 2 == 0:
            # Players dominate: 5 enemy kills, 0 player hits
            events = ["enemy_killed:sq:m1"] * 5
        else:
            # AI dominates: 0 enemy kills, 5 player hits
            events = ["player_hit:player_1:health=0.5"] * 5

        gs = _make_game_state(
            enemy_units=[{"unit_id": "s1", "unit_type": "infantry_squad",
                          "position": [100, 0, 100], "strength": 6,
                          "health_pct": 0.8, "current_order": "patrol",
                          "faction": "ussr"}],
            players=[{"player_id": 1, "position": [500, 0, 500],
                      "health_pct": 0.5, "is_in_vehicle": False, "vehicle_type": ""}],
            recent_events=events,
        )
        analyzer.analyze(gs)
        ema_values.append(analyzer.difficulty_modifier)

        # Simulate flat +/-0.05 for comparison
        if cycle % 2 == 0:
            flat_modifier = min(flat_modifier + 0.05, 2.0)
        else:
            flat_modifier = max(flat_modifier - 0.05, 0.4)
        flat_values.append(flat_modifier)

    # Measure oscillation: sum of absolute differences between consecutive values
    def oscillation(values):
        return sum(abs(values[i] - values[i - 1]) for i in range(1, len(values)))

    ema_osc = oscillation(ema_values)
    flat_osc = oscillation(flat_values)

    assert ema_osc < flat_osc, (
        f"EMA smoothing should produce less oscillation than flat adjustments. "
        f"EMA oscillation: {ema_osc:.4f}, Flat oscillation: {flat_osc:.4f}"
    )
    print(f"  [PASS] test_difficulty_ema_smoothing_less_oscillation "
          f"(EMA osc={ema_osc:.4f} < flat osc={flat_osc:.4f})")


def test_difficulty_modifier_stays_bounded():
    """Difficulty modifier should stay within [0.4, 2.0] bounds regardless of input."""
    analyzer = _get_analyzer()

    # Flood with player kills to push modifier up
    for _ in range(50):
        gs = _make_game_state(
            enemy_units=[{"unit_id": "s1", "unit_type": "infantry_squad",
                          "position": [100, 0, 100], "strength": 6,
                          "health_pct": 0.8, "current_order": "patrol",
                          "faction": "ussr"}],
            players=[{"player_id": 1, "position": [500, 0, 500],
                      "health_pct": 1.0, "is_in_vehicle": False, "vehicle_type": ""}],
            recent_events=["enemy_killed:sq:m1"] * 10,
        )
        analyzer.analyze(gs)

    assert analyzer.difficulty_modifier <= 2.0, (
        f"Difficulty modifier exceeded upper bound: {analyzer.difficulty_modifier}"
    )
    assert analyzer.difficulty_modifier >= 0.4, (
        f"Difficulty modifier below lower bound: {analyzer.difficulty_modifier}"
    )
    print("  [PASS] test_difficulty_modifier_stays_bounded")


# ─── 6. Rate limiting tests ────────────────────────────────

def test_rate_limit_manual_command(server_url: str = DEFAULT_URL):
    """Sending more than 10 rapid requests to /api/manual-command should return 429.

    This test requires a running server at the specified URL.
    """
    endpoint = f"{server_url}/api/manual-command"
    valid_command = {
        "commands": [{
            "command_type": "move",
            "unit_id": "squad_1",
            "target_position": "100,0,100",
            "parameters": "",
            "narrative": "test move",
        }]
    }

    # First, reset the server to clear any lingering state
    try:
        requests.post(f"{server_url}/api/reset", timeout=5)
    except Exception:
        pass

    # Send 12 rapid-fire requests (burst limit is 10)
    status_codes = []
    for i in range(12):
        try:
            r = requests.post(endpoint, json=valid_command, timeout=5)
            status_codes.append(r.status_code)
        except Exception as e:
            status_codes.append(f"error:{e}")

    got_429 = status_codes.count(429)
    assert got_429 >= 1, (
        f"Expected at least one 429 after exceeding burst limit of 10, "
        f"status codes were: {status_codes}"
    )
    print(f"  [PASS] test_rate_limit_manual_command "
          f"(got {got_429} x 429 out of 12 requests)")


# ─── Test runner ────────────────────────────────────────────

_UNIT_TESTS = [
    test_morale_retreat_warning,
    test_morale_merge_recommendation,
    test_morale_overall_critical,
    test_counter_ambush_stationary_engaged,
    test_counter_ambush_no_warning_for_moving_player,
    test_player_profile_high_confidence_at_5_positions,
    test_player_profile_low_confidence_at_3_positions,
    test_threat_score_exponential_decay_at_60s,
    test_threat_score_exponential_decay_at_120s,
    test_threat_score_fresh_contact_near_full,
    test_difficulty_ema_smoothing_less_oscillation,
    test_difficulty_modifier_stays_bounded,
]

_SERVER_TESTS = [
    test_rate_limit_manual_command,
]


def run_unit_tests():
    """Run all unit tests that do not require a running server."""
    print(f"\n{'═' * 60}")
    print("  UNIT TESTS — TacticalAnalyzer features")
    print(f"{'═' * 60}\n")
    passed = 0
    failed = 0
    for test_fn in _UNIT_TESTS:
        try:
            test_fn()
            passed += 1
        except AssertionError as e:
            failed += 1
            print(f"  [FAIL] {test_fn.__name__}: {e}")
        except Exception as e:
            failed += 1
            print(f"  [ERROR] {test_fn.__name__}: {e}")
    print(f"\n  Results: {passed} passed, {failed} failed out of {len(_UNIT_TESTS)}")
    return failed


def run_server_tests(server_url: str = DEFAULT_URL):
    """Run tests that require a running server."""
    print(f"\n{'═' * 60}")
    print(f"  SERVER TESTS — requires {server_url}")
    print(f"{'═' * 60}\n")
    passed = 0
    failed = 0
    for test_fn in _SERVER_TESTS:
        try:
            test_fn(server_url)
            passed += 1
        except AssertionError as e:
            failed += 1
            print(f"  [FAIL] {test_fn.__name__}: {e}")
        except Exception as e:
            failed += 1
            print(f"  [ERROR] {test_fn.__name__}: {e}")
    print(f"\n  Results: {passed} passed, {failed} failed out of {len(_SERVER_TESTS)}")
    return failed


if __name__ == "__main__":
    # Support --unit-tests and --server-tests flags alongside original behavior
    if "--unit-tests" in sys.argv:
        sys.argv.remove("--unit-tests")
        url = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_URL
        failures = run_unit_tests()
        sys.exit(failures)
    elif "--server-tests" in sys.argv:
        sys.argv.remove("--server-tests")
        url = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_URL
        failures = run_server_tests(url)
        sys.exit(failures)
    elif "--all-tests" in sys.argv:
        sys.argv.remove("--all-tests")
        url = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_URL
        f1 = run_unit_tests()
        f2 = run_server_tests(url)
        sys.exit(f1 + f2)
    else:
        main()
