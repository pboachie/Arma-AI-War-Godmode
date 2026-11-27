"""Offline runtime checks. All secrets and game data below are test fixtures."""

import asyncio
import gzip
import importlib.util
import os
import re
import stat
import sys
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import server_manager


ADMIN = "fixture_admin_key_" + "a" * 32
GAME = "fixture_game_key_" + "g" * 32
ADMIN_HEADER = {"X-API-Key": ADMIN}
GAME_HEADER = {"X-API-Key": GAME}
SERVER_PATH = Path(__file__).with_name("server.py")
EMPTY_BATCH = {"commands": [], "narrative_broadcast": "", "weather_command": "", "time_override": -1}


@pytest.fixture
def runtime(monkeypatch, tmp_path):
    def load(*, admin=ADMIN, game=GAME, origins="", rcon_enabled="", record_replays=False):
        settings = {
            "GODMODE_API_KEY": admin,
            "GODMODE_GAME_API_KEY": game,
            "GODMODE_RECORD_REPLAYS": "true" if record_replays else "false",
            "GODMODE_STATE_DIR": str(tmp_path / "state"),
            "LLM_PROVIDER": "disabled",
            "FALLBACK_PROVIDER": "openai",
            "AGENTS_ENABLED": "true",
            "RCON_ENABLED": rcon_enabled,
            "SERVER_CONTROL_ENABLED": "",
            "CORS_ORIGINS": origins,
        }
        for key, value in settings.items():
            monkeypatch.setenv(key, value)
        # Isolate app construction and its process-wide state from legacy tests.
        name = "_runtime_security_test_server"
        spec = importlib.util.spec_from_file_location(name, SERVER_PATH)
        module = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, name, module)
        spec.loader.exec_module(module)
        return module
    return load


def test_public_surface_and_missing_configuration(runtime):
    app = runtime(admin="", game="").app
    with TestClient(app) as client:
        assert client.get("/health").json() == {"status": "ok"}
        head = client.head("/health")
        assert head.status_code == 200 and head.content == b""
        for method, path in (
            ("POST", "/health"), ("GET", "/health/"), ("GET", "/dashboard/"),
            ("GET", "/stats"), ("GET", "/api/health"), ("GET", "/api/server/status"),
            ("GET", "/api/server/log/stream"), ("GET", "/api/game-state"),
            ("GET", "/docs"), ("GET", "/redoc"), ("GET", "/openapi.json"),
            ("POST", "/api/game-master"), ("GET", "/api/pending-commands"),
            ("POST", "/api/server/rcon"), ("POST", "/api/rcon"), ("POST", "/api/server/start"),
        ):
            result = client.request(method, path, headers=ADMIN_HEADER, follow_redirects=False)
            assert result.status_code == 401, (method, path, result.text)
            assert result.json() == {"detail": "Invalid or missing API key"}
            assert result.headers["cache-control"] == "no-store"
        assert client.get("/dashboard").status_code == 200


def test_keys_are_scoped_and_only_a_single_header_counts(runtime):
    client = TestClient(runtime().app)
    assert client.get("/api/health", headers=ADMIN_HEADER).status_code == 200
    assert client.get("/stats", headers=ADMIN_HEADER).status_code == 200
    assert client.get("/api/metrics", headers=ADMIN_HEADER).status_code == 200
    assert client.get("/docs", headers=ADMIN_HEADER).status_code == 404
    assert client.get("/api/health", params={"api_key": ADMIN}).status_code == 401
    assert client.get("/api/health", headers=[("X-API-Key", ADMIN), ("X-API-Key", ADMIN)]).status_code == 401
    assert client.get("/api/health", headers={"X-API-Key": "bogus"}).status_code == 401
    for method, path in (
        ("GET", "/api/health"), ("POST", "/api/manual-command"),
        ("POST", "/api/campaign/reset"), ("GET", "/stats"), ("GET", "/api/server/status"),
        ("GET", "/api/server/log/stream"), ("POST", "/api/server/restart"),
        ("POST", "/api/game-master/"), ("HEAD", "/api/pending-commands"),
    ):
        assert client.request(method, path, headers=GAME_HEADER).status_code == 401
    assert client.post("/api/game-master", headers=ADMIN_HEADER, json={}).status_code == 401
    assert client.get("/api/pending-commands", headers=ADMIN_HEADER).status_code == 401
    assert client.get("/api/pending-commands", headers=GAME_HEADER).json() == EMPTY_BATCH


def test_bad_key_config_fails_closed(runtime):
    client = TestClient(runtime(admin=ADMIN, game=ADMIN).app)
    assert client.get("/api/health", headers=ADMIN_HEADER).status_code == 401
    assert client.get("/api/pending-commands", headers=ADMIN_HEADER).status_code == 401
    bad = "nonascii_" + "é" * 32
    client = TestClient(runtime(admin=bad).app)
    assert client.get("/api/health", headers=ADMIN_HEADER).status_code == 401
    assert client.get("/api/pending-commands", headers=GAME_HEADER).status_code == 200


def test_mount_preserves_role_boundaries(runtime):
    parent = FastAPI()
    parent.mount("/middleware", runtime().app)
    with TestClient(parent) as client:
        assert client.get("/middleware/health").status_code == 200
        assert client.get("/middleware/api/health", headers=ADMIN_HEADER).status_code == 200
        assert client.get("/middleware/api/health", headers=GAME_HEADER).status_code == 401
        assert client.get("/middleware/api/pending-commands", headers=GAME_HEADER).status_code == 200
        assert client.get("/middleware/api/pending-commands", headers=ADMIN_HEADER).status_code == 401


def test_cors_defaults_to_no_permission_and_explicit_origins_still_need_key(runtime):
    origin = "https://dashboard.example.test"
    preflight = {
        "Origin": origin, "Access-Control-Request-Method": "GET",
        "Access-Control-Request-Headers": "x-api-key",
    }
    client = TestClient(runtime(origins="*, ").app)
    result = client.options("/api/health", headers=preflight)
    assert result.status_code == 401
    assert "Access-Control-Allow-Origin" not in result.headers
    client = TestClient(runtime(origins=f"*, {origin}").app)
    result = client.options("/api/health", headers=preflight)
    assert result.status_code == 200 and result.headers["Access-Control-Allow-Origin"] == origin
    result = client.get("/api/health", headers={"Origin": origin})
    assert result.status_code == 401
    assert result.headers["Access-Control-Allow-Origin"] == origin


def test_disabled_features_do_not_create_clients_or_run_agents(runtime, monkeypatch):
    module = runtime()
    llm = Mock(side_effect=AssertionError("LLM client initialization is disabled"))
    rcon = Mock(side_effect=AssertionError("RCON client initialization is disabled"))
    lifecycle = Mock(side_effect=AssertionError("Process control must not initialize"))
    agents = AsyncMock(side_effect=AssertionError("Agents are disabled"))
    monkeypatch.setattr(module, "_build_chat_model", llm)
    monkeypatch.setattr(module, "create_rcon_client", rcon)
    monkeypatch.setattr(module, "create_orchestrator", agents)
    monkeypatch.setattr(server_manager, "create_server_manager", lifecycle)
    with TestClient(module.app) as client:
        health = client.get("/api/health", headers=ADMIN_HEADER).json()
        assert health["llm_provider"] == "disabled"
        assert health["model"] is None and health["fallback_provider"] is None
        assert health["agents_enabled"] is False
        assert health["replay_recording"] is False
        assert client.get("/api/rcon/status", headers=ADMIN_HEADER).json()["enabled"] is False
        for path in ("/api/rcon", "/api/server/rcon", "/api/server/start", "/api/server/restart", "/api/server/stop"):
            denied = client.post(path, headers=ADMIN_HEADER)
            assert denied.status_code == 503, (path, denied.text)
        for path in ("/api/server/log", "/api/server/config", "/api/server/log/stream"):
            assert client.get(path, headers=ADMIN_HEADER).status_code == 503

        marker = "Synthetic admin broadcast"
        queued = client.post("/api/manual-command", headers=ADMIN_HEADER, json={"narrative_broadcast": marker})
        assert queued.status_code == 200, queued.text
        telemetry = {
            "mission_id": "smoke", "player_count": 1,
            "players": [{"player_id": 1, "position": [120, 0, 130]}],
            "mission_time_seconds": 25,
            "recent_events": ["player_connected:1", "player_killed:pos=120 0 130",
                              {"type": "location_captured", "location": "Synthetic base", "faction": "US"}],
        }
        response = client.post("/api/game-master", headers=GAME_HEADER, json=telemetry)
        assert response.status_code == 200, response.text
        assert response.json() == {**EMPTY_BATCH, "narrative_broadcast": marker}
        assert client.get("/api/pending-commands", headers=GAME_HEADER).json() == EMPTY_BATCH
        campaign = client.get("/api/campaign", headers=ADMIN_HEADER).json()
        assert campaign["total_player_deaths"] == 1
        assert campaign["faction_control"] == {"Synthetic base": "US"}
        report = client.get("/api/battle-summary/smoke", headers=ADMIN_HEADER)
        assert report.status_code == 200, report.text
        assert "Local statistics only" in report.json()["report"]
        assert module._background_llm_task is None
        for factory in (llm, rcon, lifecycle, agents):
            factory.assert_not_called()

    assert module._CAMPAIGN_STATE_PATH.is_file()
    assert not list(module._STATE_DIR.glob(".godmode-state-*"))
    assert not module._REPLAY_DIR.exists()
    assert len(list(module._BATTLE_SUMMARIES_DIR.glob("smoke_*.json"))) == 1
    assert module._BATTLE_HISTORY_DIR.parent == module._STATE_DIR


def test_bad_sitrep_is_not_persisted_and_limits_are_applied(runtime):
    module = runtime()
    with TestClient(module.app) as client:
        malformed = client.post("/api/game-master", headers=GAME_HEADER, content="not-json")
        assert malformed.json() == EMPTY_BATCH
        assert client.get("/api/game-state", headers=ADMIN_HEADER).json()["state"] is None
        assert client.post("/api/game-master", headers=GAME_HEADER, content=b" " * (1024 * 1024 + 1)).status_code == 413
        assert client.post("/api/session", headers=ADMIN_HEADER, json={"session_id": ""}).status_code == 400
        assert client.post("/api/manual-command", headers=ADMIN_HEADER, json={"commands": None}).status_code == 400
        assert client.post("/api/manual-command", headers=ADMIN_HEADER, json={"narrative_broadcast": "Queue smoke"}).status_code == 200
        assert client.get("/api/pending-commands", headers=GAME_HEADER).json()["narrative_broadcast"] == "Queue smoke"
        assert client.get("/api/pending-commands", headers=GAME_HEADER).json() == EMPTY_BATCH
    assert not (module._STATE_DIR / "last_invalid_game_master_body.bin").exists()
    assert not module._REPLAY_DIR.exists()


def test_opt_in_replay_rotates_and_bounds_reports(runtime, monkeypatch):
    module = runtime(record_replays=True)
    monkeypatch.setattr(module, "_REPLAY_BYTES_PER_FILE", 256)
    monkeypatch.setattr(module, "_REPLAY_FILE_LIMIT", 3)
    monkeypatch.setattr(module, "_REPORT_FILE_LIMIT", 2)
    for i in range(7):
        module._replay.record("sitrep", {"marker": str(i) * 120}, session_id="smoke")
        module._save_after_action_report("smoke", {"report": str(i)})
    module._replay.close()
    replays = list(module._REPLAY_DIR.glob("smoke_*.jsonl.gz"))
    reports = list(module._BATTLE_SUMMARIES_DIR.glob("smoke_*.json"))
    assert len(replays) == 3 and len(reports) == 2
    for replay in replays:
        with gzip.open(replay, "rb") as fh:
            content = fh.read()
        assert 0 < len(content) <= 256 and b'"type": "sitrep"' in content
    if os.name == "posix":
        assert all(stat.S_IMODE(path.stat().st_mode) == 0o600 for path in replays + reports)


def test_replay_write_failure_does_not_break_game_bridge(runtime):
    module = runtime(record_replays=True)
    module._STATE_DIR.mkdir()
    module._REPLAY_DIR.write_text("not a directory")
    with TestClient(module.app) as client:
        response = client.post("/api/game-master", headers=GAME_HEADER, json={"mission_id": "smoke"})
        assert response.status_code == 200 and response.json() == EMPTY_BATCH


def test_session_history_is_bounded_with_default_provider_disabled(runtime, monkeypatch):
    module = runtime()
    monkeypatch.setattr(module, "MAX_SESSIONS", 2)
    monkeypatch.setattr(module, "MAX_BATTLE_PLANS", 2)
    with TestClient(module.app) as client:
        for session in ("first", "second", "third"):
            response = client.post("/api/game-master", headers=GAME_HEADER, json={"mission_id": session})
            assert response.status_code == 200
            assert client.get(f"/api/battle-summary/{session}", headers=ADMIN_HEADER).status_code == 200
        assert set(module._session_stats) == set(module._battle_plans) == set(module._generated_summaries) == {"second", "third"}
        assert client.get("/api/battle-summary/first", headers=ADMIN_HEADER).status_code == 200


def test_provider_diagnostics_omit_response_and_exception_bodies(runtime, caplog):
    module = runtime()
    marker = "SYNTHETIC_PRIVATE_PROVIDER_CONTENT"

    class ProviderError(Exception):
        status_code = 401

    stub = Mock(ainvoke=AsyncMock(side_effect=ProviderError(marker)))
    with pytest.raises(ProviderError):
        asyncio.run(module._invoke_llm_with_retry(stub, []))
    assert module.parse_llm_response(marker)["commands"] == []
    assert "ProviderError (HTTP 401)" in caplog.text
    assert marker not in caplog.text


def test_explicit_rcon_is_one_admin_service_for_both_aliases(runtime, monkeypatch):
    module = runtime(rcon_enabled="true")
    connection = Mock(
        is_connected=True,
        connect=AsyncMock(return_value=True),
        disconnect=AsyncMock(),
        send_command=AsyncMock(return_value="No synthetic players"),
    )
    factory = Mock(return_value=connection)
    monkeypatch.setattr(module, "create_rcon_client", factory)
    with TestClient(module.app) as client:
        for path in ("/api/rcon", "/api/server/rcon"):
            assert client.post(path, headers=GAME_HEADER, json={"command": "players"}).status_code == 401
            result = client.post(path, headers=ADMIN_HEADER, json={"command": "players"})
            assert result.status_code == 200 and result.json()["success"] is True
        assert client.get("/api/rcon/status", headers=ADMIN_HEADER).json()["commands_sent"] == 2
    factory.assert_called_once_with()
    connection.connect.assert_awaited_once_with()
    connection.disconnect.assert_awaited_once_with()
    assert connection.send_command.await_count == 2


def test_dashboard_is_a_nonced_shell_without_external_code(runtime):
    client = TestClient(runtime().app)
    first = client.get("/dashboard")
    second = client.get("/dashboard")
    assert first.status_code == 200 and first.headers["cache-control"] == "no-store"
    policy = first.headers["content-security-policy"]
    nonce = re.search(r"script-src 'nonce-([A-Za-z0-9_-]+)'", policy).group(1)
    assert first.text.count(f'nonce="{nonce}"') == 2
    assert nonce not in second.headers["content-security-policy"]
    assert "connect-src 'self'" in policy and "'unsafe-inline'" not in policy
    assert ADMIN not in first.text and GAME not in first.text
    assert '<div id="console" hidden>' in first.text
    for missing in ("<script src=", "<link", "innerHTML", "EventSource", "localStorage", "sessionStorage"):
        assert missing not in first.text
    assert 'mode: "same-origin", redirect: "error"' in first.text
    assert ".textContent" in first.text
