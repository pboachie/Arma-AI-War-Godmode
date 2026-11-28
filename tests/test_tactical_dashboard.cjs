"use strict";
const assert = require("node:assert/strict");
const {test} = require("node:test");
const {WORLD, projectPosition, buildTracks, objectivesFrom, activityFrom, freshness, formatTime} = require("../middleware/dashboard.js");

test("projects east and north, not altitude, onto the north-up Everon image", () => {
  assert.equal(WORLD, 12800);
  assert.deepEqual(projectPosition([4893, 31, 11801]), {east: 4893, north: 11801, altitude: 31, x: 4893, y: 999});
  assert.deepEqual(projectPosition([5137, 44, 4010]), {east: 5137, north: 4010, altitude: 44, x: 5137, y: 8790});
  assert.deepEqual(projectPosition([0, -2, 12800]), {east: 0, north: 12800, altitude: -2, x: 0, y: 0});
  assert.deepEqual(projectPosition([12800, 0, 0]), {east: 12800, north: 0, altitude: 0, x: 12800, y: 12800});
});

test("does not draw a fabricated point for malformed or missing positions", () => {
  for (const point of [null, undefined, [], [12, 52], [0, 0, 0], [Infinity, 0, 2], [40, NaN, 5],
    ["10", 0, 6], [10, 0, -1], [12800.001, 0, 3], [10, 0, 12801]]) {
    assert.equal(projectPosition(point), null);
  }
});

test("plots the reported entity layers independently and skips unspawned players", () => {
  const pos = [4500, 32, 7500];
  const tracks = buildTracks({
    players: [{player_id: 3, name: "<test player>", position: pos, health_pct: 0.8},
      {player_id: 4, position: [0, 0, 0]}],
    friendly_units: [{unit_id: "squad-3", position: pos}],
    enemy_units: [{unit_id: "squad-3", position: [4700, 38, 7520], faction: "USSR", strength: 5}],
    vehicle_states: [{vehicle_id: "veh-3", position: pos, vehicle_type: "truck"}],
    spawn_anchors: [{anchor_id: "site-3", position: pos, name: "Depot"}],
    ai_groups: [{unit_id: "not-an-authoritative-position"}],
  });
  assert.deepEqual(tracks.map(t => t.kind), ["site", "enemy", "friendly", "vehicle", "player"]);
  assert.equal(new Set(tracks.map(t => t.key)).size, 5);
  assert.equal(tracks[1].y, 5280);
  assert.match(tracks[1].details, /USSR.*5 in group/);
  assert.equal(tracks.at(-1).name, "<test player>");
  assert.match(tracks.at(-1).details, /80% health/);
  assert.deepEqual(buildTracks(), []);
});

test("formats readable objectives and distinguishes queued requests from decisions", () => {
  assert.deepEqual(objectivesFrom({objective: "obj_1: Operation Titan Spear | obj_2: Hold Morton"}), [
    "Operation Titan Spear", "Hold Morton",
  ]);
  assert.deepEqual(objectivesFrom({objective: "none"}), []);
  const feed = activityFrom({commands: [
    {source: "llm", timestamp: 1, commands: {commands: [{command_type: "spawn_infantry", reasoning: "Defend the depot"}]}},
    {source: "manual", timestamp: 2, commands: {commands: [], narrative_broadcast: "Test", time_override: 9.25}},
  ]});
  assert.equal(feed[0].headline, "Admin queued");
  assert.deepEqual(feed[0].items.map(item => item.detail), ["09:15", "Test"]);
  assert.equal(feed[1].headline, "Game Master");
  assert.deepEqual(feed[1].items, [{action: "spawn infantry", detail: "Defend the depot"}]);
});

test("tracks can be current, stale, absent or unavailable", () => {
  assert.equal(freshness(null), "waiting");
  assert.equal(freshness(0), "live");
  assert.equal(freshness(74.99), "live");
  assert.equal(freshness(75), "stale");
  assert.equal(freshness(2, true), "offline");
  assert.equal(formatTime(24), "00:00");
  assert.equal(formatTime(-1), "");
});
