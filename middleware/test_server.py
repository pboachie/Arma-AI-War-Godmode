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


def format_response(data: dict) -> list[str]:
    """Format the LLM command response into lines."""
    lines = []
    commands = data.get("commands", [])
    lines.append(f"    LLM Response: {len(commands)} commands")

    if data.get("narrative_broadcast"):
        lines.append(f"    BROADCAST: {data['narrative_broadcast']}")
    if data.get("weather_command"):
        lines.append(f"    WEATHER: {data['weather_command']}")
    if data.get("time_override", -1) >= 0:
        lines.append(f"    TIME: {data['time_override']}")

    for i, cmd in enumerate(commands):
        lines.append(f"    -- Command {i + 1} --")
        lines.append(f"    Type:      {cmd.get('command_type', '?')}")
        lines.append(f"    Unit:      {cmd.get('unit_id', '?')}")
        lines.append(f"    Target:    {cmd.get('target_position', '?')}")
        lines.append(f"    Params:    {cmd.get('parameters', '?')}")
        lines.append(f"    Narrative: {cmd.get('narrative', '')}")
        lines.append(f"    Reasoning: {cmd.get('reasoning', '')}")

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
                        help="Concurrent team SITREPs per round (2-4)")
    parser.add_argument("--stagger", type=float, default=0.5,
                        help="Seconds between parallel requests")
    parser.add_argument("--no-intel", action="store_true",
                        help="Skip tactical intelligence inspection between rounds")
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

        if parallel >= 2:
            teams = TEAM_PERSPECTIVES[:parallel]
            batch = [
                (build_team_sitrep(sitrep, team, i), f"R{rnd}-T{i+1}")
                for i, team in enumerate(teams)
            ]
            results = send_parallel_sitreps(args.server_url, batch, stagger=args.stagger)

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
            total_sent += 1
            if result["success"]:
                total_ok += 1

        # ── Inspect v3 features after each round ──
        if not args.no_intel:
            inspect_tactical_intel(args.server_url)
            inspect_battle_plan(args.server_url)

    # ── Final inspection ──
    print(f"\n\n{'═' * 60}")
    print("  POST-BATTLE ANALYSIS")
    print(f"{'═' * 60}")

    inspect_memory(args.server_url)
    inspect_stats(args.server_url)

    # ── Summary ──
    print(f"\n{'═' * 60}")
    print(f"  Test complete: {total_ok}/{total_sent} requests succeeded")
    print(f"{'═' * 60}")


if __name__ == "__main__":
    main()
