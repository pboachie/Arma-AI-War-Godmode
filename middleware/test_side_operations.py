"""Offline contract tests for the authenticated Core v1 bridge."""

import asyncio
import json
import re
from itertools import count
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient

import auth
from side_operations import SideOperationLoop, parse_side_operation_choice
from test_runtime_security import ADMIN_HEADER, EMPTY_BATCH, GAME_HEADER, runtime


pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def bridge(runtime, monkeypatch):
    module = runtime()
    monkeypatch.setattr(module, "LLM_PROVIDER", "ollama")
    legacy = AsyncMock()
    model = AsyncMock(return_value='{"choice":1}')
    monkeypatch.setattr(module, "_process_sitrep_background", legacy)
    monkeypatch.setattr(module, "_get_llm", lambda: SimpleNamespace(ainvoke=model))
    now = [1000.0]
    monkeypatch.setattr(module, "_side_operations", SideOperationLoop(clock=lambda: now[0]))
    # Simulate reports at regular intervals without wall-clock sleeps. The
    # side-operation clock is advanced separately to test acknowledgement TTLs.
    rate_time = count(step=11)
    monkeypatch.setattr(auth, "time", SimpleNamespace(monotonic=lambda: next(rate_time)))

    async with AsyncClient(transport=ASGITransport(app=module.app), base_url="http://test") as client:
        async def post(side=None, *, players=1, **fields):
            payload = {"mission_id": "everon", "player_count": players, **fields}
            if side is not None:
                payload["side_operations"] = side
            response = await client.post("/api/game-master", headers=GAME_HEADER, json=payload)
            assert response.status_code == 200, response.text
            return response.json()

        async def finish():
            await module._side_operation_task
            return json.loads(model.await_args.args[0][-1].content)

        try:
            yield SimpleNamespace(module=module, client=client, post=post, finish=finish,
                                  model=model, legacy=legacy, now=now)
        finally:
            await module._cancel_session_work()


def route(route_id, *, defense=True, players=1):
    return {
        "route_id": route_id, "pickup_site_id": f"{route_id}-pickup", "delivery_site_id": f"{route_id}-delivery",
        "pickup_name": "Depot", "delivery_name": "Forward base", "faction": "US",
        "allows_defense": defense, "eligible_players": players,
    }


def report(revision=0, *, session="core-map-a", routes=None, active=None, outcomes=()):
    result = {"version": 1, "session_id": session, "revision": revision, "outcomes": list(outcomes),
              "routes": [route("east"), route("west")] if routes is None else routes}
    if active is not None:
        result["active"] = active
    return result


def operation(start, status):
    return {**{field: start[field] for field in ("operation_id", "route_id", "kind")}, "status": status}


def outcome(start, revision, status, reason):
    return {**operation(start, status), "revision": revision, "reason": reason}


def options(context):
    return [(item["kind"], item["route_id"]) for item in context["opportunities"]]


async def test_supply_completion_offers_same_route_defense_once(bridge):
    initial = report(routes=[route("east"), route("west"), route("unavailable", players=0)])
    assert await bridge.post(initial) == EMPTY_BATCH
    assert options(await bridge.finish()) == [("supply_run", "east"), ("supply_run", "west")]

    # Neither another auth domain nor the legacy polling API can consume the start.
    denied = await bridge.client.post("/api/game-master", headers=ADMIN_HEADER, json={"side_operations": initial})
    assert denied.status_code == 401
    polled = await bridge.client.get("/api/pending-commands", headers=GAME_HEADER)
    assert polled.json() == EMPTY_BATCH

    bridge.module._last_good_responses["everon"] = bridge.module.parse_llm_response(json.dumps({
        "commands": [{"command_type": "move", "unit_id": "g1"}, {"command_type": "side_operation_start"}],
        "side_operation_start": {"operation_id": "model-invented"},
    }))
    replies = await asyncio.gather(bridge.post(initial), bridge.post(initial))
    delivered, = [reply for reply in replies if "side_operation_start" in reply]
    supply = delivered["side_operation_start"]
    assert [command["command_type"] for command in delivered["commands"]] == ["move"]
    assert supply == {"version": 1, "session_id": "core-map-a", "revision": 0, "route_id": "east",
                      "operation_id": supply["operation_id"], "kind": "supply_run"}
    assert re.fullmatch(r"[0-9a-f]{32}", supply["operation_id"])
    assert bridge.model.await_count == 1

    for revision, status in [(1, "active"), (2, "picked_up")]:
        assert "side_operation_start" not in await bridge.post(report(revision, active=operation(supply, status)))
    assert bridge.model.await_count == 1

    completed = outcome(supply, 3, "completed", "delivered")
    delivered_report = report(3, outcomes=[completed])
    assert "side_operation_start" not in await bridge.post(delivered_report)
    context = await bridge.finish()
    assert options(context) == [("defense", "east"), ("supply_run", "east"), ("supply_run", "west")]
    assert context["previous_outcomes"] == [{k: v for k, v in completed.items() if k != "operation_id"}]

    defense = (await bridge.post(delivered_report))["side_operation_start"]
    assert (defense["kind"], defense["route_id"], defense["revision"]) == ("defense", "east", 3)
    assert defense["operation_id"] != supply["operation_id"]
    assert await bridge.post(delivered_report) == EMPTY_BATCH

    defended = outcome(defense, 5, "completed", "held")
    # Outcomes are revision-ordered even if the producer serializes them in reverse.
    assert await bridge.post(report(5, outcomes=[defended, completed])) == EMPTY_BATCH
    assert options(await bridge.finish()) == [("supply_run", "east"), ("supply_run", "west")]
    assert await bridge.post(delivered_report) == EMPTY_BATCH  # replay cannot consume rev 5's choice


@pytest.mark.parametrize("status", ["failed", "expired", "rejected"])
async def test_core_failure_reroutes_to_an_alternative(bridge, status):
    initial = report()
    await bridge.post(initial)
    await bridge.finish()
    start = (await bridge.post(initial))["side_operation_start"]

    failed = outcome(start, 1, status, "delivery_unavailable")
    latest = report(1, outcomes=[failed])
    assert await bridge.post(latest) == EMPTY_BATCH
    context = await bridge.finish()
    assert options(context) == [("supply_run", "west")]
    assert context["previous_outcomes"][0]["status"] == status
    assert context["previous_outcomes"][0]["reason"] == "delivery_unavailable"
    assert await bridge.post(initial) == EMPTY_BATCH
    retry = (await bridge.post(latest))["side_operation_start"]
    assert (retry["route_id"], retry["revision"]) == ("west", 1)
    assert retry["operation_id"] != start["operation_id"]


async def test_republished_failure_does_not_extend_its_backoff(bridge):
    routes = [route("east")]
    state = report(routes=routes)
    await bridge.post(state)
    await bridge.finish()
    start = (await bridge.post(state))["side_operation_start"]
    failed = outcome(start, 1, "failed", "missed_delivery")
    state = report(1, routes=routes, outcomes=[failed])
    for advance in (0, 29):
        bridge.now[0] += advance
        assert await bridge.post(state) == EMPTY_BATCH
        assert bridge.model.await_count == 1
    bridge.now[0] += 1
    assert await bridge.post(state) == EMPTY_BATCH
    assert options(await bridge.finish()) == [("supply_run", "east")]

    start = (await bridge.post(state))["side_operation_start"]
    state = report(2, routes=routes, outcomes=[failed, outcome(start, 2, "failed", "missed_delivery")])
    for advance in (0, 59):
        bridge.now[0] += advance
        assert await bridge.post(state) == EMPTY_BATCH
        assert bridge.model.await_count == 2
    bridge.now[0] += 1
    await bridge.post(state)
    await bridge.finish()
    assert bridge.model.await_count == 3


async def test_ignored_start_waits_for_ack_then_reroutes_without_inventing_an_outcome(bridge):
    state = report()
    await bridge.post(state)
    await bridge.finish()
    first = (await bridge.post(state))["side_operation_start"]
    bridge.now[0] += 119
    assert await bridge.post(state) == EMPTY_BATCH
    assert bridge.model.await_count == 1
    bridge.now[0] += 1
    assert await bridge.post(state) == EMPTY_BATCH
    context = await bridge.finish()
    assert options(context) == [("supply_run", "west")]
    assert context["previous_outcomes"] == []
    second = (await bridge.post(state))["side_operation_start"]
    assert first["operation_id"] != second["operation_id"]
    assert first["revision"] == second["revision"] == 0


@pytest.mark.parametrize("later_operation", [False, True])
async def test_defense_requires_current_permission_and_no_later_operation(bridge, later_operation):
    delivery = outcome({"operation_id": "delivered", "route_id": "east", "kind": "supply_run"},
                       3, "completed", "delivered")
    outcomes = [delivery]
    if later_operation:
        outcomes.append(outcome({"operation_id": "later", "route_id": "west", "kind": "supply_run"},
                                4, "expired", "route_closed"))
    routes = [route("east", defense=later_operation), route("west")]
    assert await bridge.post(report(outcomes[-1]["revision"], routes=routes, outcomes=outcomes)) == EMPTY_BATCH
    assert all(kind == "supply_run" for kind, _ in options(await bridge.finish()))


async def test_model_context_limits_choices_and_keeps_the_latest_outcomes(bridge):
    routes = [route(f"route-{i}") for i in range(12)]
    outcomes = [outcome({"operation_id": f"core-{i}", "route_id": "retired", "kind": "defense"},
                        i, "completed", "held") for i in range(1, 13)]
    await bridge.post(report(12, routes=routes, outcomes=list(reversed(outcomes))))
    context = await bridge.finish()
    assert options(context) == [("supply_run", f"route-{i}") for i in range(8)]
    assert [item["revision"] for item in context["previous_outcomes"]] == list(range(5, 13))


@pytest.mark.parametrize("change", ["revision", "core_session", "mission", "campaign", "zero_players"])
async def test_inflight_model_decision_cannot_cross_a_context_change(bridge, change):
    started, release = asyncio.Event(), asyncio.Event()

    async def blocked(_messages):
        started.set()
        await release.wait()
        return '{"choice":1}'

    bridge.model.side_effect = blocked
    original, latest, fields = report(), report(), {}
    assert await bridge.post(original) == EMPTY_BATCH
    await asyncio.wait_for(started.wait(), timeout=3)
    if change == "revision":
        latest = report(1, routes=[route("west")])
    elif change == "core_session":
        latest = report(session="core-map-b")
    elif change == "mission":
        fields = {"mission_id": "another-mission"}
    elif change == "zero_players":
        fields = {"players": 0}
    if change == "campaign":
        assert (await bridge.client.post("/api/campaign/reset", headers=ADMIN_HEADER)).status_code == 200
    else:
        reply = await bridge.post(latest, **fields)
        assert "side_operation_start" not in reply
    release.set()
    await bridge.finish()

    bridge.model.side_effect = None
    if change in ("core_session", "revision"):
        assert await bridge.post(original) == EMPTY_BATCH
        assert bridge.model.await_count == 1
    if change == "zero_players":
        fields = {}
    assert await bridge.post(latest, **fields) == EMPTY_BATCH
    await bridge.finish()
    next_start = (await bridge.post(latest, **fields))["side_operation_start"]
    assert next_start["session_id"] == latest["session_id"]
    assert next_start["revision"] == latest["revision"]
    assert next_start["route_id"] == ("west" if change == "revision" else "east")


@pytest.mark.parametrize("change", ["revision", "core_session", "campaign", "api_reset", "stale", "route", "legacy"])
async def test_queued_start_requires_a_current_eligible_report(bridge, change):
    state = report()
    await bridge.post(state)
    await bridge.finish()
    if change == "revision":
        state = report(1)
    elif change == "core_session":
        state = report(session="core-map-b")
    elif change == "campaign":
        assert (await bridge.client.post("/api/campaign/reset", headers=ADMIN_HEADER)).status_code == 200
    elif change == "api_reset":
        assert (await bridge.client.post("/api/reset", headers=ADMIN_HEADER)).status_code == 200
    elif change == "stale":
        bridge.now[0] += 120
    elif change == "route":
        state = report(routes=[route("east", players=0), route("west")])
    elif change == "legacy":
        assert await bridge.post() == EMPTY_BATCH
    assert await bridge.post(state) == EMPTY_BATCH
    assert options(await bridge.finish())[0][1] == ("west" if change == "route" else "east")


@pytest.mark.parametrize("endpoint", ["/api/reset", "/api/campaign/reset"])
async def test_resets_do_not_release_an_already_issued_start(bridge, endpoint):
    state = report()
    await bridge.post(state)
    await bridge.finish()
    assert "side_operation_start" in await bridge.post(state)
    assert (await bridge.client.post(endpoint, headers=ADMIN_HEADER)).status_code == 200
    assert await bridge.post(state) == EMPTY_BATCH
    assert bridge.model.await_count == 1


@pytest.mark.parametrize("output", [
    '{"choice":0}', '{"choice":2}', '{"choice":1,"operation_id":"invented"}',
    '{"choice":1,"coords":[0,0,0],"resource":"fake","status":"completed"}',
    '{"side_operation_start":{"route_id":"east","kind":"defense"}}',
])
async def test_abstention_and_hostile_or_unsupported_proposals_do_not_start(bridge, output):
    bridge.model.return_value = output
    state = report(routes=[route("east")])
    assert await bridge.post(state) == EMPTY_BATCH
    assert options(await bridge.finish()) == [("supply_run", "east")]
    assert await bridge.post(state) == EMPTY_BATCH
    assert bridge.model.await_count == 1


def test_choice_parser_rejects_ambiguous_or_repaired_json():
    for bad in ('[ {"choice":1} ]', '{"choice":true}', '{"choice":"1"}', '{"choice":1.0}',
                '{"choice":0,"choice":1}', '{"choice":1', 'answer: {"choice":1}', '{"choice":1} {}'):
        assert parse_side_operation_choice(bad) == 0
    assert parse_side_operation_choice('{"choice":1}') == 1


async def test_disabled_and_old_clients_preserve_legacy_processing(bridge, monkeypatch):
    assert await bridge.post() == EMPTY_BATCH
    assert bridge.legacy.await_count == 1
    for ext in (report(routes=[]), report(routes=[route("east", players=0)]), {**report(), "version": 2}):
        assert await bridge.post(ext) == EMPTY_BATCH
    assert "side_operation_start" not in await bridge.post(report(), players=0)
    assert bridge.legacy.await_count == 4
    monkeypatch.setattr(bridge.module, "LLM_PROVIDER", "disabled")
    queued = await bridge.client.post("/api/manual-command", headers=ADMIN_HEADER, json={
        "narrative_broadcast": "Fixture", "side_operation_start": {"operation_id": "forged"},
    })
    assert queued.status_code == 200
    assert await bridge.post(report()) == {**EMPTY_BATCH, "narrative_broadcast": "Fixture"}
    assert bridge.module._side_operation_task is None
    bridge.model.assert_not_awaited()


async def test_mission_fields_are_bounded_preserved_and_shared_with_the_choice_model(bridge):
    mission_state = {
        "active_scenario_id": "convoy", "active_archetype": "convoy_escort", "escalation_curve": "standard_bell",
        "current_phase": "contact", "phase_intensity": 0.75, "mission_progress_pct": 0.4, "alert_level": "yellow",
        "waves_completed": 2, "total_player_kills": 4, "total_player_deaths": 1, "total_opfor_spawned": 9,
        "total_opfor_destroyed": 5, "opfor_casualty_rate": 0.55, "player_casualty_rate": 0.2,
        "force_ratio_assessment": "balanced", "completed_objectives": ["first"], "failed_objectives": [],
        "active_minefields": ["mine_1"], "recommended_next_archetype": "fob_defense",
    }
    oversized = {**mission_state, "completed_objectives": ["older"] * 40 + ["x" * 129, {"id": "bad"}, "latest"],
                 "phase_intensity": 1.1, "waves_completed": True, "unknown": "strip me"}
    assert await bridge.post(report(), mission_state=oversized) == EMPTY_BATCH
    context = await bridge.finish()
    observed = (await bridge.client.get("/api/game-state", headers=ADMIN_HEADER)).json()["state"]
    expected = {**mission_state, "completed_objectives": ["older"] * 29 + ["latest"]}
    del expected["phase_intensity"], expected["waves_completed"]
    assert observed["mission_state"] == context["mission_state"] == expected

    bad_side = report(routes=[route(f"route-{i}") for i in range(129)])
    assert await bridge.post(bad_side, mission_state=mission_state) == EMPTY_BATCH
    stored = (await bridge.client.get("/api/game-state", headers=ADMIN_HEADER)).json()["state"]
    assert "side_operations" not in stored
    assert stored["mission_state"] == mission_state
