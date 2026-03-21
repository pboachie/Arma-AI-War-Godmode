#!/usr/bin/env python3
"""
test_server.py — Test the God Mode middleware without Arma Reforger.

Sends a mock SITREP to the server and prints the LLM's command response.
Usage: python test_server.py [server_url]
"""

import json
import sys
import requests

SERVER_URL = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000"

# ─── Mock SITREP ───
MOCK_SITREP = {
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


def main():
    print("=" * 60)
    print("  God Mode — Middleware Test")
    print(f"  Server: {SERVER_URL}")
    print("=" * 60)

    # Health check
    print("\n[1] Health check...")
    try:
        r = requests.get(f"{SERVER_URL}/health", timeout=5)
        print(f"    Status: {r.status_code}")
        print(f"    Response: {json.dumps(r.json(), indent=2)}")
    except requests.ConnectionError:
        print(f"    ERROR: Cannot connect to {SERVER_URL}")
        print("    Make sure the server is running: bash start.sh")
        sys.exit(1)

    # Send mock SITREP
    print("\n[2] Sending mock SITREP...")
    print(f"    Players: {MOCK_SITREP['player_count']}")
    print(f"    Enemy units: {len(MOCK_SITREP['enemy_units'])}")
    print(f"    Contacts: {len(MOCK_SITREP['contacts'])}")

    try:
        r = requests.post(
            f"{SERVER_URL}/api/game-master",
            json=MOCK_SITREP,
            timeout=30,
        )
        print(f"    Status: {r.status_code}")

        if r.status_code == 200:
            data = r.json()
            commands = data.get("commands", [])
            print(f"\n[3] LLM Response: {len(commands)} commands")

            if data.get("narrative_broadcast"):
                print(f"\n    BROADCAST: {data['narrative_broadcast']}")

            if data.get("weather_command"):
                print(f"    WEATHER: {data['weather_command']}")

            if data.get("time_override", -1) >= 0:
                print(f"    TIME: {data['time_override']}")

            for i, cmd in enumerate(commands):
                print(f"\n    ── Command {i + 1} ──")
                print(f"    Type:      {cmd.get('command_type', '?')}")
                print(f"    Unit:      {cmd.get('unit_id', '?')}")
                print(f"    Target:    {cmd.get('target_position', '?')}")
                print(f"    Params:    {cmd.get('parameters', '?')}")
                print(f"    Narrative: {cmd.get('narrative', '')}")
                print(f"    Reasoning: {cmd.get('reasoning', '')}")
        else:
            print(f"    ERROR: {r.text}")

    except requests.Timeout:
        print("    ERROR: Request timed out (LLM may be slow)")
    except Exception as e:
        print(f"    ERROR: {e}")

    # Stats
    print("\n[4] Server stats...")
    try:
        r = requests.get(f"{SERVER_URL}/stats", timeout=5)
        print(f"    {json.dumps(r.json(), indent=2)}")
    except Exception:
        pass

    print("\n" + "=" * 60)
    print("  Test complete.")
    print("=" * 60)


if __name__ == "__main__":
    main()
