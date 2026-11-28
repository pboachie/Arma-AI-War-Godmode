"""Offline runtime checks. All secrets and game data below are test fixtures."""

import asyncio
import gzip
import importlib.util
import json
import os
import re
import stat
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient

import auth
import server_integration
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


@pytest.mark.parametrize("serializer", ["conflict-hook", "explicit-component"])
def test_tracked_sitrep_serializers_preserve_consumed_fields(runtime, serializer):
    module = runtime()
    player = {"position": [110, 12, 130], "velocity": [1, 0, 2],
              "position_trail": [[108, 12, 128]], "heading": 45}
    if serializer == "conflict-hook":
        report = {
            "players": [{**player, "id": 7, "name": "Fixture"}], "player_count": 1,
            "mission_time_seconds": 30, "time_of_day": 13.5, "weather": "overcast",
            "events": ["player_connected:7"], "squads_active": 2, "vehicles_active": 1,
            "commands_executed": 4,
        }
    else:
        report = {
            "players": [{**player, "player_id": 7, "health_pct": 0.6, "is_in_vehicle": False}],
            "player_count": 1, "current_weather": "Overcast", "mission_time_seconds": 30,
            "recent_events": ["player_connected:7"], "current_objective": "Fixture objective",
            "total_spawned_groups": 2, "total_spawned_vehicles": 1,
            "enemy_units": [{"unit_id": "fixture-unit", "position": [150, 2, 180], "morale": "steady",
                             "health_pct": 0.7, "formation": "line", "casualties": 1}],
            "contacts": [{"contact_id": "fixture-contact", "last_known_position": [130, 5, 170]}],
            "terrain_samples": [{"position": [110, 12, 130], "terrain_type": "open", "cover_density": 0.25}],
            "spawn_anchors": [{"anchor_id": "fixture-anchor", "position": [190, 1, 210], "priority": 0.6}],
            "weather_tactical_context": {"visibility_range_meters": 250},
        }
    with TestClient(module.app) as client:
        response = client.post("/api/game-master", headers=GAME_HEADER, json=report)
        assert response.status_code == 200 and response.json() == EMPTY_BATCH
        state = client.get("/api/game-state", headers=ADMIN_HEADER).json()["state"]

    assert state["players"][0]["player_id"] == 7
    for key, value in player.items():
        assert state["players"][0][key] == value
    assert state["recent_events"] == ["player_connected:7"]
    assert state["total_spawned_groups"] == 2 and state["total_spawned_vehicles"] == 1
    assert state["current_weather"] == "Overcast"
    if serializer == "conflict-hook":
        assert state["players"][0]["name"] == "Fixture" and state["commands_executed"] == 4
    else:
        assert state["objective"] == "Fixture objective"
        assert state["enemy_units"][0]["morale"] == "steady"
        assert state["enemy_units"][0]["casualties"] == 1
        assert state["contacts"][0]["last_known_position"] == [130, 5, 170]
        assert state["terrain_samples"][0]["cover_density"] == 0.25
        assert state["spawn_anchors"][0]["priority"] == 0.6
        assert "hp:0.6" in module._build_compact_sitrep(state)
        assert "weather_tactical_context" not in state


def test_sitrep_collections_and_player_trails_are_bounded(runtime):
    module = runtime()
    caps = {
        "enemy_units": 200, "friendly_units": 200, "ai_groups": 200, "players": 200,
        "contacts": 500, "spawn_anchors": 256, "terrain_samples": 256, "vehicle_states": 200,
    }
    data = {field: [{} for _ in range(limit + 1)] for field, limit in caps.items()}
    data["players"][0] = {"position_trail": [[110, 12, 130]] * 9}
    data["recent_events"] = ["old event"] + ["fixture event"] * 100
    state = module.GameSitrep.model_validate(data).model_dump()
    assert {field: len(state[field]) for field in caps} == caps
    assert len(state["players"][0]["position_trail"]) == 8
    assert state["recent_events"] == ["fixture event"] * 100
    with pytest.raises(module.ValidationError):
        module.GameSitrep.model_validate({"recent_events": ["s" * 513]})
    with pytest.raises(module.ValidationError):
        module.GameSitrep.model_validate({"terrain_samples": [{"cover_density": "unknown"}]})


def test_player_profiles_follow_roster_session_and_inactivity(runtime, monkeypatch):
    module = runtime()
    clock = Mock(return_value=10.0)
    monkeypatch.setattr(module, "time", Mock(wraps=time, monotonic=clock))
    monkeypatch.setattr(module, "_MAX_SITREP_PLAYERS", 2)
    analyzer, pacing = module._tactical_analyzer, module._adaptive_pacing
    maps = (
        analyzer.player_positions, analyzer.player_engagements, analyzer.player_style_history,
        analyzer.player_vehicle_status, analyzer.player_terrain, analyzer.player_elevation,
        pacing._player_speeds, pacing._player_headings, pacing._player_idle_since,
    )

    def observe(session, ids):
        state = {
            "mission_id": session, "players": [{"player_id": i, "position": [110, 12, 130]} for i in ids],
            "terrain_samples": [{"position": [115, 12, 130], "terrain_type": "open"}],
            "recent_events": ["shots_fired:player_4"],
        }
        analyzer._update_player_tracking(state)
        analyzer._classify_players()
        pacing.update(state)

    for _ in range(3):
        observe("first", [1, 2, 4])
    assert set(analyzer.player_positions) == {1, 2}
    assert set(analyzer.player_style_history) == {1, 2}
    assert not analyzer.player_engagements
    observe("first", [2, 3])
    assert all(set(history) <= {2, 3} for history in maps)
    assert len(analyzer.player_positions[2]) == 4
    observe("second", [2])
    assert all(set(history) <= {2} for history in maps)
    assert len(analyzer.player_positions[2]) == 1
    observe("second", [2])
    clock.return_value += module._ROSTER_IDLE_SECONDS + 1
    observe("second", [2])
    assert len(analyzer.player_positions[2]) == len(pacing._player_speeds[2]) == 1
    observe("second", [])
    assert not any(maps)


def test_game_result_validation_only_records_known_outcomes(runtime):
    module = runtime()
    valid = {"command_id": "fixture-result", "unit_id": "fixture-unit",
             "command_type": "set_time", "status": "succeeded"}
    samples = [
        valid, {**valid, "command_type": "unknown_fixture_type"},
        {**valid, "status": "unknown"}, {**valid, "unit_id": "not an id"},
        {**valid, "detail": "s" * 513},
    ]
    with TestClient(module.app) as client:
        response = client.post("/api/game-master", headers=GAME_HEADER, json={
            "mission_id": "fixture", "command_results": samples,
        })
        assert response.status_code == 200 and response.json() == EMPTY_BATCH
    assert module._battle_plans["fixture"].tactic_outcomes == {
        "set_time": {"issued": 0, "successful": 1, "failed": 0},
    }
    assert len(module.SitrepCommandResults(command_results=[valid] * 101).command_results) == 100


def test_authenticated_game_rate_buckets_do_not_interfere(runtime, monkeypatch):
    module = runtime()
    clock = Mock(return_value=100.0)
    monkeypatch.setattr(auth, "time", SimpleNamespace(monotonic=clock))
    with TestClient(module.app) as client:
        for _ in range(5):
            assert client.post("/api/game-master", headers=ADMIN_HEADER, json={}).status_code == 401
        for _ in range(4):
            assert client.post("/api/game-master", headers=GAME_HEADER, json={}).status_code == 200
        limited = client.post("/api/game-master", headers=GAME_HEADER, json={})
        assert limited.status_code == 429 and limited.headers["retry-after"] == "10"
        for _ in range(10):
            assert client.get("/api/pending-commands", headers=GAME_HEADER).json() == EMPTY_BATCH
        assert client.get("/api/pending-commands", headers=GAME_HEADER).status_code == 429
        assert client.get("/api/health", headers=ADMIN_HEADER).status_code == 200
        clock.return_value += 10
        assert client.post("/api/game-master", headers=GAME_HEADER, json={}).status_code == 200
        assert client.get("/api/pending-commands", headers=GAME_HEADER).status_code == 200


def test_lifecycle_config_returns_only_operational_fields(runtime, monkeypatch, caplog):
    module = runtime()
    fixture_secret = "SYNTHETIC_PRIVATE_GAME_PASSWORD"
    config = {"bindPort": 2001, "game": {"name": "Fixture", "maxPlayers": 8, "password": fixture_secret,
                                        "passwordAdmin": fixture_secret, "other": {"value": fixture_secret}},
              "rcon": {"password": fixture_secret}}
    manager = Mock(get_config=AsyncMock(return_value=config))
    monkeypatch.setattr(server_integration, "_server_manager", manager)
    client = TestClient(module.app)
    assert client.get("/api/server/config", headers=GAME_HEADER).status_code == 401
    response = client.get("/api/server/config", headers=ADMIN_HEADER)
    assert response.status_code == 200
    assert response.json() == {"bindPort": 2001, "game": {"name": "Fixture", "maxPlayers": 8}}
    manager.get_config.side_effect = ValueError(fixture_secret)
    response = client.get("/api/server/config", headers=ADMIN_HEADER)
    assert response.status_code == 500
    assert response.json() == {"detail": "Unable to read server configuration"}
    assert fixture_secret not in caplog.text


def test_provider_parser_budgets_preserve_known_command_fields(runtime):
    module = runtime()
    expected = {"command_type": "set_time", "parameters": "12", "reasoning": "fixture: // noon",
                "sync_group": "fixture", "unit_id": "", "target_position": "0,0,0", "narrative": ""}
    clean = {"commands": [expected, {"command_type": "unknown"}, {"command_type": "set_time", "parameters": {}}]}
    parsed = module.parse_llm_response(json.dumps(clean))
    assert parsed["commands"] == [expected]
    count = module._MAX_COMMANDS_PER_BATCH
    assert len(module._validate_command_batch({"commands": [expected] * (count + 1)})["commands"]) == count
    assert len(module._extract_json_candidates("{} " * 12)) == module._LLM_JSON_CANDIDATE_LIMIT
    assert module._extract_json_candidates("{" + "[" * module._LLM_JSON_DEPTH_LIMIT) == []
    assert module.parse_llm_response(" " * module._LLM_RESPONSE_LIMIT + "{}") == module.empty_response()
    with pytest.raises(ValueError, match="endpoints must be HTTPS"):
        module._https_provider_url("http://provider.example.test/v1")
    assert module._https_provider_url("https://provider.example.test/v1").startswith("https://")


def test_single_provider_slot_and_compaction_share_admission(runtime, monkeypatch):
    module = runtime()
    monkeypatch.setattr(module, "_llm_enabled", lambda: True)
    monkeypatch.setattr(module, "_LLM_MAX_CONCURRENT", 1)
    monkeypatch.setattr(module, "_LLM_ADMISSION_TIMEOUT", 0.03)
    monkeypatch.setattr(module, "LLM_MAX_RETRIES", 1)
    monkeypatch.setattr(module, "COMPACTION_THRESHOLD", 7)
    limiter = Mock(acquire=AsyncMock())
    monkeypatch.setattr(module, "_llm_rate_limiter", limiter)
    response = SimpleNamespace(content='{"commands":[{"command_type":"set_time","parameters":"12"}]}')
    summary = SimpleNamespace(content="Fixture summary", usage_metadata={"input_tokens": 4, "output_tokens": 2})
    provider = Mock(ainvoke=AsyncMock(side_effect=[response, summary]))
    monkeypatch.setattr(module, "_get_llm", lambda: provider)

    async def exercise():
        async with asyncio.timeout(5):
            batch = await module.call_llm("Fixture report", session_id="fixture")
            assert batch["commands"][0]["parameters"] == "12"
            history = module.get_session_history("fixture")
            for _ in range(6):
                history.add_message(module.HumanMessage(content="Fixture report"))
            # A held slot must prevent compaction from entering the provider.
            async with module._get_llm_semaphore():
                await module._maybe_compact_memory("fixture")
                assert provider.ainvoke.await_count == 1
                assert "fixture" not in module._compacted_summaries
            # Refill after the failed compaction trimmed the history.
            for _ in range(7):
                history.add_message(module.HumanMessage(content="Fixture report"))
            await module._maybe_compact_memory("fixture")
            assert module._compacted_summaries["fixture"] == "Fixture summary"
            assert len(history.messages) == 6
            assert module.stats["compaction_runs"] == 1
            assert limiter.acquire.await_count == provider.ainvoke.await_count == 2
            assert not module._get_llm_semaphore().locked()

    asyncio.run(exercise())


def test_provider_deadline_releases_the_slot(runtime, monkeypatch):
    module = runtime()
    monkeypatch.setattr(module, "_LLM_MAX_CONCURRENT", 1)
    monkeypatch.setattr(module, "LLM_CALL_TIMEOUT", 0.03)
    monkeypatch.setattr(module, "LLM_MAX_RETRIES", 1)

    async def wait_for_briefing(_messages):
        await asyncio.Event().wait()

    async def exercise():
        async with asyncio.timeout(5):
            provider = Mock(ainvoke=AsyncMock(side_effect=wait_for_briefing))
            with pytest.raises(TimeoutError):
                await module._invoke_llm_with_retry(provider, [])
            assert not module._get_llm_semaphore().locked()
            provider.ainvoke.side_effect = None
            provider.ainvoke.return_value = "Fresh fixture summary"
            assert await module._invoke_llm_with_retry(provider, []) == "Fresh fixture summary"

    asyncio.run(exercise())


def test_provider_rate_waiters_each_consume_a_refilled_token(runtime, monkeypatch):
    module = runtime()
    clock = Mock(return_value=0.0)
    monkeypatch.setattr(module, "time", Mock(wraps=time, monotonic=clock))

    async def exercise():
        sleeping = asyncio.Queue()

        async def wait_for_tick(delay):
            wake = asyncio.Event()
            sleeping.put_nowait((delay, wake))
            await wake.wait()

        monkeypatch.setattr(module, "asyncio", SimpleNamespace(Lock=asyncio.Lock, sleep=wait_for_tick))
        limiter = module._LLMRateLimiter(60)
        for _ in range(10):
            await limiter.acquire()
        async with asyncio.timeout(5):
            tasks = [asyncio.create_task(limiter.acquire()) for _ in range(2)]
            first, second = await sleeping.get(), await sleeping.get()
            assert first[0] == second[0] == 1.0
            clock.return_value = 1.0
            first[1].set()
            second[1].set()
            third = await sleeping.get()
            assert third[0] == 1.0 and sum(task.done() for task in tasks) == 1
            clock.return_value = 2.0
            third[1].set()
            await asyncio.gather(*tasks)

    asyncio.run(exercise())


def test_reset_awaits_old_cycle_and_summary_before_accepting_fresh_work(runtime, monkeypatch):
    module = runtime()
    monkeypatch.setattr(module, "_llm_enabled", lambda: True)
    monkeypatch.setattr(module, "_LLM_MAX_CONCURRENT", 2)
    monkeypatch.setattr(module, "_llm_rate_limiter", Mock(acquire=AsyncMock()))

    async def exercise():
        release = asyncio.Event()
        kinds = ("cycle", "summary")
        entered = {kind: asyncio.Event() for kind in kinds}
        cancelled = {kind: asyncio.Event() for kind in kinds}
        calls = dict.fromkeys(kinds, 0)
        report = {
            "mission_id": "fixture", "player_count": 1,
            "players": [{"player_id": 1, "position": [125, 4, 148]}],
        }

        async def provider_response(messages):
            kind = "summary" if "after-action reports" in messages[0].content else "cycle"
            calls[kind] += 1
            old = calls[kind] == 1
            if old:
                entered[kind].set()
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    # Simulate provider cleanup that returns a late response.
                    cancelled[kind].set()
                    await release.wait()
            value = "old" if old else "fresh"
            content = value if kind == "summary" else json.dumps({
                "commands": [{"command_type": "set_time", "parameters": value}],
            })
            return SimpleNamespace(content=content)

        provider = Mock(ainvoke=AsyncMock(side_effect=provider_response))
        monkeypatch.setattr(module, "_get_llm", lambda: provider)
        async with asyncio.timeout(7), AsyncClient(transport=ASGITransport(app=module.app),
                                                    base_url="http://fixture.test") as client:
            assert (await client.post("/api/game-master", headers=GAME_HEADER, json=report)).status_code == 200
            await entered["cycle"].wait()
            summary_path = "/api/battle-summary/fixture"
            old_summary = asyncio.create_task(client.get(summary_path, headers=ADMIN_HEADER))
            await entered["summary"].wait()
            reset = asyncio.create_task(client.post("/api/reset", headers=ADMIN_HEADER))
            try:
                await asyncio.gather(*(event.wait() for event in cancelled.values()))
                assert not reset.done()
                assert (await client.post("/api/game-master", headers=GAME_HEADER, json=report)).json() == EMPTY_BATCH
            finally:
                release.set()
            assert (await reset).json() == {"status": "reset"}
            assert (await old_summary).status_code == 409
            assert module._background_llm_task is None
            assert not module._last_good_responses and not module._inflight_calls
            assert not module.command_log and not module._generated_summaries
            assert not list(module._BATTLE_SUMMARIES_DIR.glob("*.json"))

            assert (await client.post("/api/game-master", headers=GAME_HEADER, json=report)).status_code == 200
            await module._background_llm_task
            assert len(module.command_log) == 1
            batch = module._last_good_responses["fixture"]
            assert any(cmd["parameters"] == "fresh" for cmd in batch["commands"])
            assert not any(cmd["parameters"] == "old" for cmd in batch["commands"])
            fresh = await client.get(summary_path, headers=ADMIN_HEADER)
            assert fresh.json()["report"] == "fresh"
            assert len(list(module._BATTLE_SUMMARIES_DIR.glob("fixture_*.json"))) == 1

    asyncio.run(exercise())


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
    assert "img-src blob:" in policy
    assert ADMIN not in first.text and GAME not in first.text
    assert '<div id="console" hidden>' in first.text
    for missing in ("<script src=", "<link", "innerHTML", "EventSource", "localStorage", "sessionStorage"):
        assert missing not in first.text
    assert 'mode: "same-origin", redirect: "error"' in first.text
    assert ".textContent" in first.text


def test_dashboard_everon_image_requires_only_the_admin_key(runtime):
    import hashlib

    client = TestClient(runtime().app)
    path = "/api/dashboard/maps/everon"
    assert client.get(path).status_code == 401
    assert client.get(path, headers=GAME_HEADER).status_code == 401
    assert client.get("/api/dashboard/maps/anything-else", headers=ADMIN_HEADER).status_code == 404
    image = client.get(path, headers=ADMIN_HEADER)
    assert image.status_code == 200
    assert image.headers["content-type"] == "image/jpeg"
    assert image.headers["cache-control"] == "no-store"
    assert hashlib.sha256(image.content).hexdigest() == "c2c86822d9b3aaa49c5defbd8b1f09d2c982f6cbd0dea5b307cb74b029e50d63"


def test_dashboard_terrain_setting_is_an_allowlisted_hint(runtime, monkeypatch):
    client = TestClient(runtime().app)
    monkeypatch.delenv("GODMODE_DASHBOARD_MAP", raising=False)
    assert 'data-terrain=""' in client.get("/dashboard").text
    monkeypatch.setenv("GODMODE_DASHBOARD_MAP", "everon")
    assert 'data-terrain="everon"' in client.get("/dashboard").text
    monkeypatch.setenv("GODMODE_DASHBOARD_MAP", '"test-attribute="injected')
    html = client.get("/dashboard").text
    assert 'data-terrain=""' in html
    assert "test-attribute" not in html


def test_command_log_accepts_a_bounded_display_limit(runtime):
    app = runtime()
    client = TestClient(app.app)
    for i in range(24):
        app.command_log.append({"timestamp": i, "source": "manual", "commands": []})
    path = "/api/command-log"
    assert client.get(path + "?limit=12", headers=GAME_HEADER).status_code == 401
    data = client.get(path + "?limit=12", headers=ADMIN_HEADER).json()
    assert data["total"] == 24
    assert [row["timestamp"] for row in data["commands"]] == list(range(12, 24))
    assert len(client.get(path, headers=ADMIN_HEADER).json()["commands"]) == 24
    for invalid in ("0", "-1", "51", "not-an-integer"):
        assert client.get(path + "?limit=" + invalid, headers=ADMIN_HEADER).status_code == 422
