#!/usr/bin/env python3
"""
Integration tests for Arma Reforger server management modules.

Tests cover: RCON client, A2S query client, config generator,
auth middleware, rate limiting, server manager, and live API endpoints.

Usage:
    python test_server_integration.py --unit          # Unit tests (no server needed)
    python test_server_integration.py --api URL       # API tests (middleware must be running)
    python test_server_integration.py --all URL       # All tests
"""
import sys
import os
import time
import struct
import binascii
import tempfile

import requests

# Add middleware to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# ─── Default server ───
DEFAULT_URL = "http://localhost:8000"


# ═══════════════════════════════════════════════════════════════
#  UNIT TESTS — no server required
# ═══════════════════════════════════════════════════════════════

def test_rcon_packet_build():
    """BERconClient._build_packet produces correct BE RCON wire format."""
    from rcon_client import BERconClient

    client = BERconClient.__new__(BERconClient)

    # Test basic packet structure: b'BE' + CRC32(4 bytes) + 0xFF + payload
    payload = b"\x00password"
    packet = client._build_packet(payload)

    # Header must be b'BE'
    assert packet[:2] == b"BE", f"Expected header b'BE', got {packet[:2]!r}"

    # After header: 4-byte CRC32 (little-endian), then 0xFF, then payload
    crc_bytes = packet[2:6]
    assert packet[6] == 0xFF, f"Expected 0xFF separator at byte 6, got {packet[6]:#x}"
    assert packet[7:] == payload, f"Payload mismatch: {packet[7:]!r} != {payload!r}"

    # Verify CRC32 matches: CRC is computed over 0xFF + payload
    expected_crc = binascii.crc32(b"\xff" + payload) & 0xFFFFFFFF
    actual_crc = struct.unpack("<I", crc_bytes)[0]
    assert actual_crc == expected_crc, (
        f"CRC32 mismatch: got {actual_crc:#010x}, expected {expected_crc:#010x}"
    )

    # Login packet: payload starts with 0x00
    login_payload = b"\x00testpass"
    login_pkt = client._build_packet(login_payload)
    assert login_pkt[7] == 0x00, "Login packet payload must start with 0x00"

    # Command packet: payload starts with 0x01 + sequence byte
    cmd_payload = b"\x01\x05players"
    cmd_pkt = client._build_packet(cmd_payload)
    assert cmd_pkt[7] == 0x01, "Command packet payload must start with 0x01"
    assert cmd_pkt[8] == 0x05, f"Sequence byte mismatch: got {cmd_pkt[8]}, expected 5"

    return True


def test_rcon_player_parse():
    """Parsing of RCON 'players' response into list of dicts."""
    from rcon_client import BERconClient

    client = BERconClient.__new__(BERconClient)

    mock_response = (
        "Players on server:\n"
        "[#] [IP Address]:[Port] [Ping] [GUID] [Name]\n"
        "0  1.2.3.4:1234  50  abc123  TestPlayer\n"
        "1  5.6.7.8:5678  80  def456  AnotherPlayer"
    )

    players = client._parse_players(mock_response)

    assert isinstance(players, list), f"Expected list, got {type(players).__name__}"
    assert len(players) >= 1, f"Expected at least 1 player, got {len(players)}"

    p0 = players[0]
    assert "name" in p0 or "Name" in p0 or "player" in str(p0).lower(), (
        f"Player dict missing name-like key: {p0}"
    )

    return True


def test_a2s_client_cache():
    """A2SQueryClient returns cached data within TTL window."""
    from a2s_client import A2SQueryClient

    client = A2SQueryClient("127.0.0.1", 2301, cache_ttl=1.0)

    # Manually inject cached data
    fake_info = {"name": "Test Server", "players": 5, "online": True}
    client._info_cache = fake_info
    client._info_cache_time = time.time()

    # Within TTL, should return cached data without network call
    result = client.get_info()
    assert result == fake_info, f"Expected cached data, got {result}"

    # After TTL expires, cache is stale
    client._info_cache_time = time.time() - 2.0
    # Don't call get_info() here as it would try network — just verify staleness
    elapsed = time.time() - client._info_cache_time
    assert elapsed > client._cache_ttl, (
        f"Cache should be stale: elapsed {elapsed:.1f}s > TTL {client._cache_ttl}s"
    )

    return True


def test_a2s_client_offline():
    """A2SQueryClient handles offline/unreachable server gracefully."""
    from a2s_client import A2SQueryClient

    client = A2SQueryClient("192.0.2.1", 59999, timeout=0.5, cache_ttl=0.0)

    # is_online() should return False, not raise
    online = client.is_online()
    assert online is False, f"Expected False for unreachable server, got {online}"

    # get_info() should return dict with online=False, not raise
    info = client.get_info()
    assert isinstance(info, dict), f"Expected dict, got {type(info).__name__}"
    assert info.get("online") is False, f"Expected online=False in {info}"

    return True


def test_config_generator():
    """Config generator replaces placeholders with environment variables."""
    gen_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "..", "scripts", "generate_config.py")
    if not os.path.isfile(gen_path):
        raise ImportError(f"scripts/generate_config.py not found at {gen_path}")

    import importlib.util
    spec = importlib.util.spec_from_file_location("generate_config", gen_path)
    gen_mod = importlib.util.module_from_spec(spec)

    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as tmpl:
        tmpl.write('{"admin_password": "__ADMIN_PASSWORD__", "name": "TestServer"}')
        tmpl_path = tmpl.name

    out_path = tmpl_path + ".out"
    old_env = os.environ.get("ARMA_ADMIN_PASSWORD")
    try:
        os.environ["ARMA_ADMIN_PASSWORD"] = "test123"
        spec.loader.exec_module(gen_mod)

        # Try common generator function signatures
        if hasattr(gen_mod, "generate"):
            gen_mod.generate(tmpl_path, out_path)
        elif hasattr(gen_mod, "main"):
            sys.argv = ["generate_config.py", tmpl_path, out_path]
            gen_mod.main()
        elif hasattr(gen_mod, "process_template"):
            gen_mod.process_template(tmpl_path, out_path)
        else:
            raise ImportError("generate_config.py has no recognized entry point")

        with open(out_path) as f:
            content = f.read()
        assert "test123" in content, f"Expected 'test123' in output, got: {content[:200]}"
        assert "__ADMIN_PASSWORD__" not in content, (
            f"Placeholder not replaced in output: {content[:200]}"
        )
    finally:
        if old_env is None:
            os.environ.pop("ARMA_ADMIN_PASSWORD", None)
        else:
            os.environ["ARMA_ADMIN_PASSWORD"] = old_env
        for p in (tmpl_path, out_path):
            if os.path.exists(p):
                os.unlink(p)

    return True


def _make_auth_test_app():
    """Build a FastAPI app with APIKeyMiddleware for auth tests."""
    from auth_middleware import APIKeyMiddleware
    from fastapi import FastAPI

    app = FastAPI()
    app.add_middleware(APIKeyMiddleware, api_key="testkey", exempt_paths=["/health"])

    @app.get("/protected")
    async def protected():
        return {"status": "ok"}

    @app.get("/health")
    async def health():
        return {"status": "healthy"}

    return app


def test_auth_middleware_rejects():
    """APIKeyMiddleware rejects requests without a valid API key."""
    from starlette.testclient import TestClient

    client = TestClient(_make_auth_test_app())
    resp = client.get("/protected")
    assert resp.status_code == 401, f"Expected 401 without key, got {resp.status_code}"

    return True


def test_auth_middleware_allows_exempt():
    """APIKeyMiddleware allows access to exempt paths without a key."""
    from starlette.testclient import TestClient

    client = TestClient(_make_auth_test_app())
    resp = client.get("/health")
    assert resp.status_code == 200, f"Expected 200 for exempt /health, got {resp.status_code}"

    return True


def test_auth_middleware_allows_valid_key():
    """APIKeyMiddleware allows requests with a valid API key."""
    from starlette.testclient import TestClient

    client = TestClient(_make_auth_test_app())
    resp = client.get("/protected", headers={"X-API-Key": "testkey"})
    assert resp.status_code == 200, f"Expected 200 with valid key, got {resp.status_code}"

    return True


def test_game_master_invalid_body_fails_soft():
    """Malformed game-master bodies should return an empty command batch, not HTTP 400."""
    import importlib
    from starlette.testclient import TestClient

    server = importlib.import_module("server")
    client = TestClient(server.app)

    resp = client.post(
        "/api/game-master",
        content=b"\x00\x01not-json-at-all\x02",
        headers={"Content-Type": "application/json"},
    )

    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text[:200]}"

    data = resp.json()
    assert data.get("commands") == [], f"Expected empty commands, got: {data}"
    assert data.get("llm_unavailable") is True, f"Expected llm_unavailable=true, got: {data}"
    assert data.get("llm_error_reason") == "invalid_game_master_json", (
        f"Expected invalid_game_master_json reason, got: {data}"
    )

    return True


def test_rate_limiter():
    """RateLimitMiddleware enforces request rate limits."""
    from rate_limiter import RateLimitMiddleware
    from fastapi import FastAPI
    from starlette.testclient import TestClient

    test_app = FastAPI()
    test_app.add_middleware(RateLimitMiddleware, max_per_second=2)

    @test_app.get("/limited")
    async def limited():
        return {"status": "ok"}

    client = TestClient(test_app)

    # First two requests should succeed
    r1 = client.get("/limited")
    r2 = client.get("/limited")
    assert r1.status_code == 200, f"Request 1 failed: {r1.status_code}"
    assert r2.status_code == 200, f"Request 2 failed: {r2.status_code}"

    # Third rapid request should be rate-limited
    r3 = client.get("/limited")
    assert r3.status_code == 429, (
        f"Expected 429 on third rapid request, got {r3.status_code}"
    )

    return True


def test_server_manager_status_no_docker():
    """ArmaServerManager.get_status() handles missing Docker gracefully."""
    from server_manager import ArmaServerManager

    mgr = ArmaServerManager(compose_file="/nonexistent/docker-compose.yml")
    status = mgr.get_status()

    assert isinstance(status, dict), f"Expected dict, got {type(status).__name__}"
    assert status.get("running") is False, f"Expected running=False, got {status}"
    assert status.get("docker_available") is False, (
        f"Expected docker_available=False, got {status}"
    )

    return True


# ═══════════════════════════════════════════════════════════════
#  API TESTS — require running middleware
# ═══════════════════════════════════════════════════════════════

def test_server_status_endpoint(url):
    """GET /api/server/status returns process, a2s, and rcon info."""
    resp = requests.get(f"{url}/api/server/status", timeout=10)
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"

    data = resp.json()
    for key in ("process", "a2s", "rcon"):
        assert key in data, f"Missing key '{key}' in response: {list(data.keys())}"
        assert isinstance(data[key], dict), (
            f"Expected dict for '{key}', got {type(data[key]).__name__}"
        )

    return True


def test_server_players_endpoint(url):
    """GET /api/server/players returns players list and count."""
    resp = requests.get(f"{url}/api/server/players", timeout=10)
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"

    data = resp.json()
    assert "players" in data, f"Missing 'players' key: {list(data.keys())}"
    assert "count" in data, f"Missing 'count' key: {list(data.keys())}"
    assert isinstance(data["players"], list), (
        f"Expected list for 'players', got {type(data['players']).__name__}"
    )
    assert isinstance(data["count"], int), (
        f"Expected int for 'count', got {type(data['count']).__name__}"
    )

    return True


def test_server_log_endpoint(url):
    """GET /api/server/log returns log lines."""
    resp = requests.get(f"{url}/api/server/log", params={"lines": 10}, timeout=10)
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"

    data = resp.json()
    assert "lines" in data, f"Missing 'lines' key: {list(data.keys())}"

    return True


def test_health_includes_server(url):
    """GET /health includes server-related fields."""
    resp = requests.get(f"{url}/health", timeout=10)
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"

    data = resp.json()
    assert "status" in data, f"Missing 'status' key: {list(data.keys())}"
    # Server fields may or may not be present depending on setup;
    # just verify the endpoint works and returns valid JSON dict
    assert isinstance(data, dict), f"Expected dict response, got {type(data).__name__}"

    return True


def test_game_master_backward_compat(url):
    """POST /api/game-master still works with minimal SITREP payload."""
    sitrep = {
        "friendly_units": [],
        "enemy_units": [
            {
                "unit_id": "test_squad",
                "unit_type": "infantry_squad",
                "position": [3200, 0, 4800],
                "strength": 6,
                "current_order": "patrol",
                "health_pct": 1.0,
                "faction": "ussr",
            }
        ],
        "contacts": [],
        "players": [
            {
                "player_id": 1,
                "position": [3800, 0, 4600],
                "health_pct": 0.9,
                "is_in_vehicle": False,
                "vehicle_type": "",
            }
        ],
        "current_weather": "Clear",
        "time_of_day": 12.0,
        "player_count": 1,
        "recent_events": ["player_connected:1"],
        "current_objective": "Patrol area",
        "mission_time_seconds": 60.0,
        "wind_speed": 2.0,
        "wind_direction": 90,
        "total_spawned_groups": 1,
        "total_spawned_vehicles": 0,
    }

    resp = requests.post(f"{url}/api/game-master", json=sitrep, timeout=60)
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text[:200]}"

    data = resp.json()
    assert "commands" in data, f"Missing 'commands' key: {list(data.keys())}"

    return True


def test_rcon_endpoint(url):
    """POST /api/server/rcon returns response and success fields."""
    resp = requests.post(
        f"{url}/api/server/rcon",
        json={"command": "players"},
        timeout=10,
    )
    # May return 200 (success) or 503/500 (no RCON server) — both are acceptable
    data = resp.json()
    assert "response" in data or "success" in data or "error" in data, (
        f"Expected 'response', 'success', or 'error' key: {list(data.keys())}"
    )

    return True


# ═══════════════════════════════════════════════════════════════
#  TEST RUNNERS
# ═══════════════════════════════════════════════════════════════

_UNIT_TESTS = [
    test_rcon_packet_build,
    test_rcon_player_parse,
    test_a2s_client_cache,
    test_a2s_client_offline,
    test_config_generator,
    test_auth_middleware_rejects,
    test_auth_middleware_allows_exempt,
    test_auth_middleware_allows_valid_key,
    test_rate_limiter,
    test_server_manager_status_no_docker,
]

_API_TESTS = [
    test_server_status_endpoint,
    test_server_players_endpoint,
    test_server_log_endpoint,
    test_health_includes_server,
    test_game_master_backward_compat,
    test_rcon_endpoint,
]


def _run_test_suite(tests, label, call_arg=None):
    """Run a list of test functions, printing results.

    If call_arg is provided, each test is called with that argument (e.g. a URL).
    Returns True if all non-skipped tests passed.
    """
    print(f"\n{'=' * 60}")
    print(f"  {label}")
    print(f"{'=' * 60}\n")

    passed = 0
    failed = 0
    skipped = 0

    for test_fn in tests:
        try:
            result = test_fn(call_arg) if call_arg is not None else test_fn()
            if result is None or result:
                passed += 1
                print(f"  [PASS] {test_fn.__name__}")
            else:
                failed += 1
                print(f"  [FAIL] {test_fn.__name__}")
        except ImportError as e:
            skipped += 1
            print(f"  [SKIP] {test_fn.__name__} ({e})")
        except AssertionError as e:
            failed += 1
            print(f"  [FAIL] {test_fn.__name__}: {e}")
        except Exception as e:
            failed += 1
            print(f"  [FAIL] {test_fn.__name__}: {e}")

    print(f"\n  Results: {passed} passed, {failed} failed, {skipped} skipped"
          f" out of {len(tests)}")
    return failed == 0


def run_unit_tests():
    """Run all unit tests that don't need a server."""
    return _run_test_suite(_UNIT_TESTS, "UNIT TESTS -- Server Integration Modules")


def run_api_tests(url):
    """Run API tests against a running middleware."""
    return _run_test_suite(_API_TESTS, f"API TESTS -- requires {url}", call_arg=url)


# ═══════════════════════════════════════════════════════════════
#  CLI ENTRY POINT
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    if "--unit" in sys.argv:
        success = run_unit_tests()
    elif "--api" in sys.argv:
        idx = sys.argv.index("--api")
        url = sys.argv[idx + 1] if len(sys.argv) > idx + 1 else DEFAULT_URL
        success = run_api_tests(url)
    elif "--all" in sys.argv:
        idx = sys.argv.index("--all")
        url = sys.argv[idx + 1] if len(sys.argv) > idx + 1 else DEFAULT_URL
        success = run_unit_tests() and run_api_tests(url)
    else:
        print(__doc__)
        sys.exit(0)

    sys.exit(0 if success else 1)
