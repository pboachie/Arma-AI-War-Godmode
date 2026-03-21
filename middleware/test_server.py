#!/usr/bin/env python3
"""
test_server.py — Test the God Mode middleware without Arma Reforger.

Sends mock SITREPs to the server and prints the LLM's command responses.
Supports single, multi-round, and parallel request testing.

Usage:
  python test_server.py [server_url] [--rounds N] [--delay SECONDS]
  python test_server.py [server_url] [--parallel N]   # N concurrent SITREPs
  python test_server.py [server_url] [--rounds 3 --parallel 2]  # combined
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

# ─── Round-specific event pools for evolving state ───
ROUND_EVENTS = [
    # Round 2: players push forward
    {
        "recent_events": [
            "shots_fired:player_1:pos=<3600, 0, 4700>",
            "enemy_killed:squad_1:member_3",
        ],
        "player_moves": [(3600, 0, 4700), (3610, 0, 4690)],
        "squad_damage": {"squad_1": {"strength": -1, "health_pct": 0.85}},
    },
    # Round 3: contact lost, flanking attempt
    {
        "recent_events": [
            "contact_lost:contact_1",
            "trigger_entered:east_flank:pos=<3900, 0, 4900>",
            "player_entered_vehicle:player_2:uaz469",
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
    # Round 4: escalation, casualties on both sides
    {
        "recent_events": [
            "explosion:pos=<3650, 0, 4750>",
            "player_hit:player_1:health=0.5",
            "enemy_killed:squad_2:member_1",
            "enemy_killed:squad_2:member_2",
        ],
        "player_moves": [(3650, 0, 4750), (3850, 0, 4850)],
        "player_damage": {1: 0.5},
        "squad_damage": {"squad_2": {"strength": -2, "health_pct": 0.4}},
        "weather_change": "Overcast",
    },
    # Round 5: reinforcements arrive, night falls
    {
        "recent_events": [
            "player_connected:3",
            "reinforcement_request:north_sector",
            "objective_updated:obj_1:in_progress",
        ],
        "new_player": {
            "player_id": 3,
            "position": [3400, 0, 4400],
            "health_pct": 1.0,
            "is_in_vehicle": False,
            "vehicle_type": "",
        },
        "player_moves": [(3650, 0, 4800), (3800, 0, 4850), (3400, 0, 4400)],
        "time_change": 19.5,
        "weather_change": "Foggy",
    },
]

# ─── Team perspectives for parallel requests ───
# Each represents a different team/sector reporting simultaneously,
# simulating how multiple squads would send SITREPs at the same time.
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

    # Tag with team info so the LLM sees which sector is reporting
    state["reporting_team"] = team["name"]
    state["sector"] = team["sector"]

    # Shift mission time slightly per team to simulate network jitter
    state["mission_time_seconds"] += team_idx * 0.5

    # Merge team-specific events
    state["recent_events"] = team["recent_events"] + state["recent_events"][:2]

    # Add team-specific contacts
    state["contacts"] = state["contacts"] + team.get("extra_contacts", [])

    # If team has assigned players, highlight them
    if team["player_ids"]:
        state["reporting_players"] = team["player_ids"]

    # Vary positions slightly per team perspective (fog of war)
    for unit in state["enemy_units"]:
        unit["position"][0] += random.randint(-30, 30)
        unit["position"][2] += random.randint(-30, 30)

    return state


def evolve_sitrep(sitrep: dict, round_num: int, round_data: dict) -> dict:
    """Apply round-specific mutations to simulate an evolving battlefield."""
    state = copy.deepcopy(sitrep)

    # Advance mission time
    state["mission_time_seconds"] += round_num * 60

    # Apply player position changes
    if "player_moves" in round_data:
        for i, pos in enumerate(round_data["player_moves"]):
            if i < len(state["players"]):
                state["players"][i]["position"] = list(pos)

    # Apply player damage
    for pid, hp in round_data.get("player_damage", {}).items():
        for p in state["players"]:
            if p["player_id"] == pid:
                p["health_pct"] = hp

    # Apply player vehicle changes
    for pid, (vtype, in_vehicle) in round_data.get("player_vehicle", {}).items():
        for p in state["players"]:
            if p["player_id"] == pid:
                p["is_in_vehicle"] = in_vehicle
                p["vehicle_type"] = vtype

    # Add new player
    if "new_player" in round_data:
        state["players"].append(round_data["new_player"])
        state["player_count"] = len(state["players"])

    # Apply squad damage
    for uid, dmg in round_data.get("squad_damage", {}).items():
        for unit in state["enemy_units"]:
            if unit["unit_id"] == uid:
                unit["strength"] = max(1, unit["strength"] + dmg.get("strength", 0))
                unit["health_pct"] = dmg.get("health_pct", unit["health_pct"])

    # Update contacts
    if "contacts_update" in round_data:
        state["contacts"] = round_data["contacts_update"]

    # Update events
    if "recent_events" in round_data:
        state["recent_events"] = round_data["recent_events"]

    # Weather / time changes
    if "weather_change" in round_data:
        state["current_weather"] = round_data["weather_change"]
    if "time_change" in round_data:
        state["time_of_day"] = round_data["time_change"]

    # Drift unit positions slightly to simulate movement
    for unit in state["enemy_units"]:
        unit["position"][0] += random.randint(-50, 50)
        unit["position"][2] += random.randint(-50, 50)

    return state


# Thread-safe print lock for parallel output
_print_lock = Lock()


def _safe_print(*args, **kwargs):
    """Thread-safe print wrapper."""
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
    """Send a SITREP after an initial stagger delay."""
    if stagger_delay > 0:
        time.sleep(stagger_delay)
    return send_sitrep(server_url, sitrep, label)


def send_parallel_sitreps(
    server_url: str,
    sitreps: list[tuple[dict, str]],
    stagger: float = 0.5,
) -> list[dict]:
    """Send multiple SITREPs in parallel threads with staggered starts.

    Args:
        stagger: seconds between each thread's launch to avoid slamming the API.
                 The server also queues via semaphore, but staggering reduces
                 client-side pile-up and gives the API breathing room.
    """
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


def main():
    parser = argparse.ArgumentParser(description="God Mode middleware test client")
    parser.add_argument("server_url", nargs="?", default=DEFAULT_URL, help="Server URL")
    parser.add_argument("--rounds", type=int, default=1, help="Number of SITREP rounds to send (max 5)")
    parser.add_argument("--delay", type=float, default=2.0, help="Delay in seconds between rounds")
    parser.add_argument(
        "--parallel", type=int, default=0,
        help="Number of concurrent team SITREPs per round (2-4). "
             "Simulates multiple teams reporting at the same time.",
    )
    parser.add_argument(
        "--stagger", type=float, default=0.5,
        help="Seconds between launching each parallel request (default 0.5). "
             "Prevents hitting API rate limits by spacing out requests.",
    )
    args = parser.parse_args()

    rounds = min(args.rounds, len(ROUND_EVENTS) + 1)
    parallel = min(args.parallel, len(TEAM_PERSPECTIVES))

    print("=" * 60)
    print("  God Mode — Middleware Test")
    print(f"  Server:   {args.server_url}")
    print(f"  Rounds:   {rounds}")
    if parallel:
        print(f"  Parallel: {parallel} concurrent team SITREPs per round")
        print(f"  Stagger:  {args.stagger}s between each parallel request")
    if rounds > 1:
        print(f"  Delay:    {args.delay}s between rounds")
    print("=" * 60)

    # Health check
    print("\n[*] Health check...")
    try:
        r = requests.get(f"{args.server_url}/health", timeout=5)
        print(f"    Status: {r.status_code}")
        print(f"    Response: {json.dumps(r.json(), indent=2)}")
    except requests.ConnectionError:
        print(f"    ERROR: Cannot connect to {args.server_url}")
        print("    Make sure the server is running: bash start.sh")
        sys.exit(1)

    # Send SITREPs
    sitrep = copy.deepcopy(BASE_SITREP)
    total_sent = 0
    total_ok = 0

    for rnd in range(1, rounds + 1):
        if rnd > 1:
            round_idx = rnd - 2
            if round_idx < len(ROUND_EVENTS):
                sitrep = evolve_sitrep(sitrep, rnd, ROUND_EVENTS[round_idx])
            print(f"\n    (waiting {args.delay}s before next round...)")
            time.sleep(args.delay)

        if parallel >= 2:
            # ── Parallel mode: multiple teams report simultaneously ──
            print(f"\n{'━' * 60}")
            print(f"  ROUND {rnd} — {parallel} teams reporting in parallel")
            print(f"{'━' * 60}")

            teams = TEAM_PERSPECTIVES[:parallel]
            batch = [
                (build_team_sitrep(sitrep, team, i), f"R{rnd}-T{i+1}")
                for i, team in enumerate(teams)
            ]
            results = send_parallel_sitreps(args.server_url, batch, stagger=args.stagger)

            total_sent += len(results)
            total_ok += sum(1 for r in results if r["success"])

            # Summary for this round
            print(f"\n    Round {rnd} summary:")
            for res in sorted(results, key=lambda x: x["label"]):
                status = "OK" if res["success"] else f"FAIL ({res['error']})"
                elapsed = f" ({res.get('elapsed', 0):.1f}s)" if res["success"] else ""
                cmd_count = len(res["data"].get("commands", [])) if res["data"] else 0
                print(f"      {res['label']} {res['team']}: {status}{elapsed} — {cmd_count} commands")
        else:
            # ── Sequential mode (original behavior) ──
            result = send_sitrep(args.server_url, sitrep, f"#{rnd}")
            total_sent += 1
            if result["success"]:
                total_ok += 1

    # Memory inspection (v2 — LangChain)
    print(f"\n\n[*] Conversation memory...")
    try:
        r = requests.get(f"{args.server_url}/api/memory", timeout=5)
        if r.status_code == 200:
            mem = r.json()
            for sid, info in mem.items():
                print(f"    Session '{sid}': {info['message_count']} messages")
                for msg in info.get("messages", [])[-3:]:
                    role = msg["role"].upper()
                    content = msg["content"][:120]
                    print(f"      [{role}] {content}")
        else:
            print(f"    Memory endpoint returned {r.status_code}")
    except Exception as e:
        print(f"    Memory endpoint not available: {e}")

    # Stats
    print(f"\n[*] Server stats...")
    try:
        r = requests.get(f"{args.server_url}/stats", timeout=5)
        print(f"    {json.dumps(r.json(), indent=2)}")
    except Exception:
        pass

    print("\n" + "=" * 60)
    print(f"  Test complete: {total_ok}/{total_sent} requests succeeded.")
    print("=" * 60)


if __name__ == "__main__":
    main()
