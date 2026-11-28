"use strict";
(() => {
  const WORLD = 12800;
  const STALE_AFTER = 75;
  const KINDS = {player: "Player", friendly: "Friendly AI", enemy: "Enemy AI", vehicle: "Vehicle", site: "Site"};
  // The PLACES data is from Bewilderbeest's EnfusionMapMaker, under APL-SA.
  // See everon-map.NOTICE.md for source, license and calibration.
  const PLACES = [
    ["Everon Airport", 4893.46, 11800.709], ["Saint-Philippe", 4500.872, 10776.053],
    ["Meaux", 4517.52, 9467.668], ["Tyrone", 4948.837, 9075.68],
    ["Gravette", 4128.282, 7792.364], ["Montignac", 4775.641, 7086.945],
    ["Entre-Deux", 5760.571, 7061.821], ["Lamentin", 1281.055, 5946.839],
    ["Villeneuve", 2847.008, 6339.848], ["Provins", 5488.17, 6083.411],
    ["Chotain", 7087.74, 6011.904], ["Figari", 5256.366, 5341.263],
    ["Levie", 7456.592, 4737.094], ["Morton", 5137.254, 4009.941],
    ["Durras", 8826.12, 2746.123], ["Saint-Pierre", 9689.432, 1558.166],
  ];
  const array = value => Array.isArray(value) ? value : [];
  const clean = (value, length = 180) => typeof value === "string" ? value.trim().slice(0, length) : "";
  const clamp = (n, lo, hi) => Math.max(lo, Math.min(n, hi));
  const label = value => clean(value).replace(/_/g, " ");
  const formatTime = hours => {
    if (!Number.isFinite(hours) || hours < 0 || hours > 24) return "";
    const minutes = Math.round(hours * 60) % 1440;
    return String(Math.floor(minutes / 60)).padStart(2, "0") + ":" + String(minutes % 60).padStart(2, "0");
  };

  function projectPosition(position) {
    // Enfusion is [X east, Y altitude, Z north]. The basemap is north-up.
    if (!Array.isArray(position) || position.length !== 3) return null;
    const [east, altitude, north] = position;
    if (![east, altitude, north].every(Number.isFinite) || (east === 0 && altitude === 0 && north === 0)) return null;
    if (east < 0 || east > WORLD || north < 0 || north > WORLD) return null;
    return {east, north, altitude, x: east, y: WORLD - north};
  }

  function buildTracks(state) {
    const sources = [
      ["site", "spawn_anchors", "anchor_id"], ["enemy", "enemy_units", "unit_id"],
      ["friendly", "friendly_units", "unit_id"], ["vehicle", "vehicle_states", "vehicle_id"],
      ["player", "players", "player_id"],
    ];
    const tracks = new Map();
    for (const [kind, field, keyField] of sources) {
      for (const [index, row] of array(state?.[field]).entries()) {
        if (!row || typeof row !== "object") continue;
        const point = projectPosition(row.position);
        if (!point) continue;
        const reportedId = row[keyField] ?? row.id ?? row.unit_id;
        const identity = Number.isInteger(reportedId) ? String(reportedId) : clean(reportedId, 64);
        const key = kind + ":" + (identity ? "id:" + identity : "row:" + index);
        const name = clean(row.name) || (kind === "player" ? "" : identity) || KINDS[kind] + " " + (index + 1);
        const health = Number.isFinite(row.health_pct) && row.health_pct >= 0 && row.health_pct <= 1
          ? Math.round(row.health_pct * 100) + "% health" : "";
        const details = [
          KINDS[kind], clean(row.faction), label(row.unit_type || row.vehicle_type || row.anchor_type),
          health, label(row.current_order || row.control_state),
          Number.isInteger(row.strength) && row.strength > 0 ? row.strength + " in group" : "",
        ].filter(Boolean).join(" · ");
        tracks.set(key, {key, kind, name, details, ...point});
      }
    }
    return [...tracks.values()];
  }

  function objectivesFrom(state) {
    const value = clean(state?.objective, 3000);
    return value.split(/\s+\|\s+/).map(item => item.replace(/^obj_\d+:\s*/i, "").trim())
      .filter(item => item && item.toLowerCase() !== "none").slice(0, 12);
  }

  function activityFrom(result) {
    return array(result?.commands).slice(-12).reverse().flatMap(entry => {
      if (!entry || typeof entry !== "object") return [];
      const batch = entry.commands;
      const commands = array(Array.isArray(batch) ? batch : batch?.commands);
      const headline = entry.source === "manual" ? "Admin queued" : "Game Master";
      const items = commands.slice(0, 8).flatMap(row => {
        const action = label(row?.command_type);
        if (!action) return [];
        return [{action, detail: clean(row.narrative) || clean(row.reasoning) || clean(row.unit_id)}];
      });
      const broadcast = clean(batch?.narrative_broadcast, 250);
      if (broadcast) items.unshift({action: "Broadcast", detail: broadcast});
      const time = formatTime(batch?.time_override);
      if (time) items.unshift({action: "Time change", detail: time});
      const weather = label(batch?.weather_command);
      if (weather) items.unshift({action: "Weather change", detail: weather});
      if (batch?.llm_unavailable === true) {
        items.unshift({action: "AI model unavailable", detail: label(batch.llm_error_reason)});
      }
      if (commands.length > 8) items.push({action: "+" + (commands.length - 8) + " more in this batch", detail: ""});
      return items.length ? [{headline, timestamp: entry.timestamp, items}] : [];
    }).slice(0, 6);
  }

  function freshness(age, unavailable = false) {
    if (unavailable) return "offline";
    if (!Number.isFinite(age)) return "waiting";
    return age >= STALE_AFTER ? "stale" : "live";
  }

  if (typeof module !== "undefined" && module.exports) {
    module.exports = {WORLD, PLACES, projectPosition, buildTracks, objectivesFrom, activityFrom, freshness, formatTime};
  }
  if (typeof document === "undefined") return;

  const byId = id => document.getElementById(id);
  const routes = Object.freeze({
    game: "/api/game-state", commands: "/api/command-log", tactical: "/api/tactical-intel",
    plan: "/api/battle-plan", campaign: "/api/campaign", health: "/api/health",
    stats: "/stats", metrics: "/api/metrics", server: "/api/server/status", serverMetrics: "/api/server/metrics",
    rcon: "/api/rcon/status", behavior: "/api/behavior-config", logs: "/api/server/log?lines=100",
    agents: "/api/agents", replays: "/api/replays", reports: "/api/battle-summaries",
  });
  const activityPath = "/api/command-log?limit=12";
  const terrainPath = "/api/dashboard/maps/everon";
  const chatPath = "/api/dashboard/chat";
  const readPaths = new Set([...Object.values(routes), activityPath, terrainPath]);
  const manualPath = "/api/manual-command";
  const writePaths = new Set([manualPath, "/api/reset", chatPath]);
  const base = new URL(".", location.href);
  const svg = byId("battle-map");
  const terrain = byId("terrain");
  const defaultTerrain = document.querySelector("main").dataset.terrain === "everon" ? "everon" : "";
  const layerInputs = [...document.querySelectorAll("[data-layer]")];
  const sideButtons = [...document.querySelectorAll("[data-side-view]")];
  const svgNode = (tag, attributes = {}) => {
    const node = document.createElementNS("http://www.w3.org/2000/svg", tag);
    for (const [k, v] of Object.entries(attributes)) node.setAttribute(k, String(v));
    return node;
  };
  const node = (tag, value = "", className = "") => {
    const result = document.createElement(tag);
    result.textContent = value;
    if (className) result.className = className;
    return result;
  };

  let session, timer, debugSerial = 0, terrainSerial = 0;
  let snapshot, receipt, gameUnavailable, tracks = [], selection = "", imageUrl, drag;
  let transcript = [];
  let camera = {x: WORLD / 2, y: WORLD / 2, span: WORLD};
  let view = {x: 0, y: 0, width: WORLD, height: WORLD, metersPerPixel: 20};
  let drawFrame;

  function showStatus(message, error = false, id = "status") {
    const el = byId(id);
    el.textContent = message;
    el.dataset.error = String(error);
  }

  async function request(active, path, method = "GET", body) {
    const allowed = method === "GET" ? readPaths : method === "POST" ? writePaths : null;
    if (active !== session || !allowed?.has(path)) throw new Error("Request not permitted");
    const url = new URL(path.slice(1), base);
    if (url.origin !== base.origin || url.username || url.password) throw new Error("Invalid API destination");
    const headers = {"X-API-Key": active.key, Accept: path === terrainPath ? "image/jpeg" : "application/json"};
    if (body !== undefined) headers["Content-Type"] = "application/json";
    const response = await fetch(url, {
      method, headers, body, mode: "same-origin", redirect: "error", cache: "no-store",
      credentials: "omit", referrerPolicy: "no-referrer",
      signal: AbortSignal.any([active.abort.signal, AbortSignal.timeout(path === chatPath ? 60000 : path === terrainPath ? 18000 : 8000)]),
    });
    if (response.status === 401 && active === session) {
      lock("The admin key was rejected. Enter a valid admin key to continue.", true);
      throw new Error("Admin key rejected");
    }
    if (!response.ok) {
      if (path === chatPath) {
        const problem = await response.json().catch(() => null);
        if (clean(problem?.detail)) throw new Error(clean(problem.detail));
      }
      throw new Error("Request returned HTTP " + response.status + ".");
    }
    return path === terrainPath ? response.blob() : response.json();
  }

  function reportAge() {
    if (!Number.isFinite(snapshot?.age_seconds)) return null;
    return Math.max(0, snapshot.age_seconds + (performance.now() - receipt) / 1000);
  }

  function renderFreshness() {
    const age = reportAge();
    const state = freshness(age, gameUnavailable);
    const badge = byId("freshness");
    badge.dataset.state = state;
    badge.textContent = {live: "Receiving game reports", waiting: "Waiting for game report", stale: "Telemetry stale", offline: "Telemetry unavailable"}[state];
    byId("freshness-age").textContent = Number.isFinite(age)
      ? "Last SITREP " + Math.floor(age) + "s ago · refreshes every 10s"
      : "Markers update on each SITREP";
    byId("marker-layer").dataset.stale = String(state === "stale" || state === "offline");
  }

  function clearImage() {
    byId("basemap").removeAttribute("href");
    if (imageUrl) URL.revokeObjectURL(imageUrl);
    imageUrl = undefined;
    byId("grid-layer").replaceChildren();
    byId("place-layer").replaceChildren();
    byId("marker-layer").replaceChildren();
    setMapPosition(null);
  }

  function lock(message = "", error = false) {
    if (session) {
      session.abort.abort();
      session.key = "";
    }
    session = undefined;
    clearInterval(timer);
    debugSerial++;
    terrainSerial++;
    snapshot = undefined;
    gameUnavailable = false;
    tracks = [];
    transcript = [];
    selection = "";
    drag = undefined;
    svg.dataset.drag = "false";
    clearImage();
    byId("console").hidden = true;
    byId("unlock-panel").hidden = false;
    for (const id of ["quick-panel", "diagnostics"]) byId(id).open = false;
    for (const id of ["admin-key", "batch", "broadcast", "game-clock", "chat-input"]) byId(id).value = "";
    for (const id of ["unlock", "queue", "reset", "send-broadcast", "send-clock", "refresh"]) byId(id).disabled = false;
    byId("chat-input").disabled = false;
    for (const id of ["quick-status", "operator-status"]) showStatus("", false, id);
    byId("detail").textContent = "No data yet.";
    byId("model").textContent = "—";
    byId("activity").replaceChildren();
    byId("activity-empty").hidden = false;
    showStatus("", false, "chat-status");
    renderChat();
    renderPlanner();
    showSideView("situation");
    terrain.value = defaultTerrain;
    for (const input of layerInputs) input.checked = input.defaultChecked;
    fit();
    renderState();
    showStatus(message, error);
  }

  function setMapOverlay(message) {
    byId("map-overlay").hidden = !message;
    byId("map-message").textContent = message;
  }

  async function loadTerrain() {
    const active = session;
    const attempt = ++terrainSerial;
    selection = "";
    clearImage();
    fit();
    renderTrackPicker();
    if (!active?.unlocked || terrain.value !== "everon") {
      setMapOverlay("Select Everon if this server is running that terrain.");
      return;
    }
    setMapOverlay("Loading the Everon satellite map…");
    let candidate;
    try {
      const blob = await request(active, terrainPath);
      if (active !== session || attempt !== terrainSerial) return;
      if (blob.type !== "image/jpeg") throw new Error("Invalid map image format");
      candidate = URL.createObjectURL(blob);
      const image = new Image();
      image.src = candidate;
      await image.decode();
      if (active !== session || attempt !== terrainSerial) return;
      imageUrl = candidate;
      byId("basemap").setAttribute("href", imageUrl);
      candidate = undefined;
      setMapOverlay("");
      drawGrid();
      renderTrackPicker();
      scheduleDraw();
    } catch {
      if (active === session && attempt === terrainSerial) setMapOverlay("Everon imagery could not load. Use Refresh to retry.");
    } finally {
      if (candidate) URL.revokeObjectURL(candidate);
    }
  }

  function drawGrid() {
    const grid = document.createDocumentFragment();
    for (let i = 1000; i <= WORLD; i += 1000) {
      grid.append(svgNode("path", {class: "terrain-grid", d: "M" + i + " 0v" + WORLD + " M0 " + (WORLD - i) + "h" + WORLD}));
    }
    byId("grid-layer").replaceChildren(grid);
  }

  function setMapPosition(point) {
    byId("map-position").textContent = point
      ? "E " + Math.round(point.x) + " m · N " + Math.round(WORLD - point.y) + " m"
      : "East and north in meters";
  }

  function pointAt(clientX, clientY) {
    const rect = svg.getBoundingClientRect();
    return {x: view.x + (clientX - rect.left) * view.metersPerPixel, y: view.y + (clientY - rect.top) * view.metersPerPixel};
  }

  function renderCamera() {
    const ratio = (svg.clientWidth || 1) / (svg.clientHeight || 1);
    const width = camera.span * Math.max(1, ratio);
    const height = camera.span * Math.max(1, 1 / ratio);
    camera.x = width < WORLD ? clamp(camera.x, width / 2, WORLD - width / 2) : WORLD / 2;
    camera.y = height < WORLD ? clamp(camera.y, height / 2, WORLD - height / 2) : WORLD / 2;
    view = {x: camera.x - width / 2, y: camera.y - height / 2, width, height, metersPerPixel: width / (svg.clientWidth || 1)};
    svg.setAttribute("viewBox", [view.x, view.y, width, height].join(" "));
  }

  function zoom(factor, clientX, clientY) {
    if (!imageUrl) return;
    const next = clamp(camera.span * factor, 520, WORLD);
    if (next === camera.span) return;
    if (Number.isFinite(clientX)) {
      const point = pointAt(clientX, clientY);
      const ratio = next / camera.span;
      camera.x = point.x + (camera.x - point.x) * ratio;
      camera.y = point.y + (camera.y - point.y) * ratio;
    }
    camera.span = next;
    scheduleDraw();
  }

  function fit() {
    camera = {x: WORLD / 2, y: WORLD / 2, span: WORLD};
    scheduleDraw();
  }

  const visible = () => {
    if (!imageUrl) return [];
    const shown = new Set(layerInputs.filter(input => input.checked).map(input => input.dataset.layer));
    return tracks.filter(track => shown.has(track.kind));
  };

  function drawMarkers() {
    const pins = document.createDocumentFragment();
    const labels = document.createDocumentFragment();
    if (imageUrl) {
      const r = 6.5 * view.metersPerPixel;
      for (const track of visible()) {
        const pin = svgNode("g", {transform: "translate(" + track.x + " " + track.y + ")", class: "map-pin " + track.kind,
          role: "button", tabindex: "0", "aria-label": KINDS[track.kind] + ": " + track.name});
        pin.dataset.track = track.key;
        pin.dataset.selected = String(track.key === selection);
        const title = svgNode("title");
        title.textContent = KINDS[track.kind] + " · " + track.name;
        const halo = svgNode("circle", {r: r * 1.55, class: "halo"});
        let symbol;
        if (track.kind === "enemy" || track.kind === "friendly") {
          const points = track.kind === "enemy" ? [[0, -r], [r, r], [-r, r]] : [[0, -r], [r, 0], [0, r], [-r, 0]];
          symbol = svgNode("polygon", {points: points.map(p => p.join(",")).join(" "), class: "symbol"});
        } else if (track.kind === "vehicle") {
          symbol = svgNode("rect", {x: -r, y: -r, width: 2 * r, height: 2 * r, rx: r * .22, class: "symbol"});
        } else {
          symbol = svgNode("circle", {r, class: "symbol"});
        }
        pin.append(title, halo, symbol);
        pins.append(pin);
      }
      if (camera.span >= 850) {
        const fontSize = (camera.span > 10000 ? 10 : 12) * view.metersPerPixel;
        const boxes = [];
        for (const [name, east, north] of PLACES) {
          const y = WORLD - north - 13 * view.metersPerPixel;
          const halfWidth = (name.length * .34 + .5) * fontSize;
          const box = {left: east - halfWidth, right: east + halfWidth, top: y - fontSize, bottom: y + .35 * fontSize};
          if (boxes.some(other => other.left < box.right && box.left < other.right
              && other.top < box.bottom && box.top < other.bottom)) continue;
          boxes.push(box);
          const text = svgNode("text", {x: east, y, class: "place-name", "font-size": fontSize});
          text.textContent = name;
          labels.append(text);
        }
      }
    }
    const oldFocus = document.activeElement?.dataset.track;
    byId("place-layer").replaceChildren(labels);
    byId("marker-layer").replaceChildren(pins);
    if (oldFocus) {
      const next = [...byId("marker-layer").children].find(child => child.dataset.track === oldFocus);
      (next || svg).focus({preventScroll: true});
    }
  }

  function scheduleDraw() {
    if (drawFrame !== undefined) return;
    drawFrame = requestAnimationFrame(() => {
      drawFrame = undefined;
      renderCamera();
      drawMarkers();
    });
  }

  function showSelection() {
    const track = visible().find(item => item.key === selection);
    const details = byId("track-detail");
    if (!track) {
      details.textContent = "Select a marker to see its details.";
      return;
    }
    details.replaceChildren(
      node("strong", track.name),
      node("p", track.details),
      node("p", "E " + Math.round(track.east) + " m · N " + Math.round(track.north) + " m · Elevation " + Math.round(track.altitude) + " m", "tabular")
    );
  }

  function renderTrackPicker() {
    const list = visible();
    const picker = byId("track-picker");
    byId("track-count").textContent = imageUrl ? list.length + " shown" : "";
    picker.replaceChildren(new Option("Choose a track", ""), ...list.map(track => new Option(KINDS[track.kind] + " · " + track.name, track.key)));
    if (!list.some(track => track.key === selection)) selection = "";
    picker.value = selection;
    picker.disabled = !list.length;
    byId("focus").disabled = !list.some(track => track.kind === "player");
    showSelection();
  }

  function chooseTrack(key, center = false) {
    const track = visible().find(item => item.key === key);
    selection = track?.key || "";
    byId("track-picker").value = selection;
    if (center && track) camera = {x: track.x, y: track.y, span: Math.min(camera.span, 2600)};
    if (track) showSideView("situation");
    showSelection();
    scheduleDraw();
  }

  function focusPlayers() {
    const players = visible().filter(track => track.kind === "player");
    if (!players.length) return;
    const xs = players.map(track => track.x), ys = players.map(track => track.y);
    const [left, right, top, bottom] = [Math.min(...xs), Math.max(...xs), Math.min(...ys), Math.max(...ys)];
    camera = {x: (left + right) / 2, y: (top + bottom) / 2,
      span: clamp(Math.max(right - left, bottom - top) * 1.25 + 700, 2200, WORLD)};
    scheduleDraw();
  }

  function renderState() {
    const state = snapshot?.state;
    byId("players").textContent = state?.player_count ?? "—";
    byId("friendly-ai").textContent = state ? array(state.friendly_units).length : "—";
    byId("enemy-ai").textContent = state ? array(state.enemy_units).length : "—";
    byId("conditions").textContent = [clean(state?.current_weather), formatTime(state?.time_of_day)].filter(Boolean).join(" · ") || "—";
    tracks = buildTracks(state);
    const objectives = objectivesFrom(state);
    byId("objectives").replaceChildren(...objectives.map(item => node("li", item)));
    byId("objective-empty").hidden = Boolean(objectives.length);
    const active = state?.side_operations?.active;
    const route = array(state?.side_operations?.routes).find(value => value?.route_id === active?.route_id);
    const operation = byId("side-operation");
    const journey = active?.kind === "defense"
      ? clean(route?.delivery_name)
      : [clean(route?.pickup_name), clean(route?.delivery_name)].filter(Boolean).join(" → ");
    operation.textContent = [label(active?.kind), label(active?.status), journey].filter(Boolean).join(" · ");
    operation.hidden = !operation.textContent;
    const events = array(state?.recent_events).slice(-10).reverse().map(event => {
      if (typeof event === "string") return clean(event);
      return [label(event?.type), clean(event?.name), clean(event?.location)].filter(Boolean).join(" · ");
    }).filter(Boolean);
    byId("events").replaceChildren(...events.map(event => node("li", event)));
    byId("events-empty").hidden = Boolean(events.length);
    renderFreshness();
    renderTrackPicker();
    scheduleDraw();
  }

  function renderActivity(data, unavailable = false) {
    const entries = activityFrom(data);
    const items = entries.map(entry => {
      const row = node("li");
      const time = new Date(entry.timestamp * 1000);
      const when = Number.isFinite(time.getTime()) ? time.toLocaleTimeString() : "";
      row.append(node("div", [entry.headline, when].filter(Boolean).join(" · "), "meta tag"));
      for (const item of entry.items) {
        row.append(node("p", [item.action, item.detail].filter(Boolean).join(" · ")));
      }
      return row;
    });
    byId("activity").replaceChildren(...items);
    byId("activity-empty").hidden = Boolean(items.length);
    byId("activity-empty").textContent = unavailable ? "Activity feed unavailable." : "No activity reported yet.";
  }

  function showSideView(name) {
    for (const button of sideButtons) {
      const selected = button.dataset.sideView === name;
      button.setAttribute("aria-pressed", String(selected));
      byId("view-" + button.dataset.sideView).hidden = !selected;
    }
  }

  function renderPlanner(health, plan, planUnavailable = false) {
    const planner = byId("planner-state");
    if (!health) planner.textContent = "Middleware status unavailable";
    else if (health.llm_provider === "disabled") planner.textContent = "Model disabled";
    else if (health.circuit_breaker_open) planner.textContent = "Provider temporarily unavailable";
    else if (health.game_master_processing || health.inflight_calls > 0) planner.textContent = "Processing a game decision";
    else if (health.operator_chat_busy) planner.textContent = "Answering an admin question";
    else planner.textContent = snapshot?.state?.player_count === 0 ? "Waiting for players" : "Waiting for the next relevant report";
    byId("planner-inflight").textContent = health?.inflight_calls ?? "—";
    byId("planner-errors").textContent = health?.total_errors ?? "—";
    byId("planner-commands").textContent = health?.total_commands_issued ?? "—";
    const operation = plan?.operation;
    const name = operation?.started ? clean(operation.operation_name) : "";
    byId("plan-operation").textContent = name;
    byId("plan-operation").hidden = !name;
    byId("plan-phase").textContent = label(name ? operation.current_phase : plan?.phase) || "—";
    byId("plan-turn").textContent = plan?.turn ?? "—";
    byId("plan-directive").textContent = planUnavailable ? "Battle plan unavailable."
      : clean(name ? operation.phase_directive : plan?.briefing, 600) || "No battle plan yet.";
    const orders = array(plan?.active_operations).slice(-6).reverse().map(item =>
      [label(item?.type), clean(item?.unit), clean(item?.target)].filter(Boolean).join(" · ")
    ).filter(Boolean);
    byId("plan-orders").replaceChildren(...orders.map(order => node("li", order)));
  }

  function renderChat() {
    const history = byId("chat-history");
    history.replaceChildren(...transcript.map(message => {
      const row = node("li");
      row.dataset.role = message.role;
      row.append(node("p", message.role === "user" ? "You" : "Game Master", "tag meta"),
        node("p", message.text, "wrap-text"));
      if (message.role === "assistant") {
        row.append(node("p", Number.isFinite(message.age)
          ? "Match report was " + Math.round(message.age) + "s old when asked."
          : "No match report was available when asked.", "meta"));
      }
      return row;
    }));
    history.scrollTop = history.scrollHeight;
    byId("chat-send").disabled = Boolean(session?.chatting);
    byId("chat-clear").disabled = Boolean(session?.chatting) || transcript.length === 0;
  }

  async function refreshDebug() {
    const active = session;
    if (!active?.unlocked || !byId("diagnostics").open) return;
    const id = ++debugSerial;
    const viewName = byId("view").value;
    const path = Object.hasOwn(routes, viewName) ? routes[viewName] : routes.game;
    try {
      const data = await request(active, path);
      if (active === session && id === debugSerial && byId("diagnostics").open) byId("detail").textContent = JSON.stringify(data, null, 2);
    } catch (error) {
      if (active === session && id === debugSerial) byId("detail").textContent = error.message;
    }
  }

  async function refresh() {
    const active = session;
    if (!active?.unlocked || active.refreshing) return;
    active.refreshing = true;
    byId("refresh").disabled = true;
    try {
      const [health, game, activity, plan] = await Promise.allSettled([
        request(active, routes.health), request(active, routes.game), request(active, activityPath), request(active, routes.plan),
      ]);
      if (active !== session) return;
      gameUnavailable = game.status !== "fulfilled";
      if (!gameUnavailable) {
        snapshot = game.value;
        receipt = performance.now();
      }
      byId("model").textContent = clean(health.value?.model) || clean(health.value?.llm_provider) || "—";
      renderState();
      renderActivity(activity.value, activity.status !== "fulfilled");
      renderPlanner(health.value, plan.value, plan.status !== "fulfilled");
      void refreshDebug();
      if (health.status !== "fulfilled") showStatus("Unable to refresh middleware health.", true);
      else showStatus("Last poll " + new Date().toLocaleTimeString());
    } finally {
      if (active === session) byId("refresh").disabled = false;
      active.refreshing = false;
    }
  }

  async function queue(batch, button, target, message, onSuccess) {
    const active = session;
    if (!active?.unlocked || button.disabled) return;
    button.disabled = true;
    try {
      const result = await request(active, manualPath, "POST", JSON.stringify(batch));
      if (active !== session) return;
      showStatus(message || "Queued commands: " + result.command_count, false, target);
      onSuccess?.();
      await refresh();
    } catch (error) {
      if (active === session) showStatus(error.message, true, target);
    } finally {
      if (active === session) button.disabled = false;
    }
  }

  byId("unlock-form").addEventListener("submit", async event => {
    event.preventDefault();
    if (session) return;
    const input = byId("admin-key");
    const key = input.value;
    input.value = "";
    if (!/^[A-Za-z0-9_-]{32,128}$/.test(key)) return showStatus("Enter the 32–128 character admin API key.", true);
    const active = {key, abort: new AbortController()};
    session = active;
    byId("unlock").disabled = true;
    showStatus("Checking admin key…");
    try {
      await request(active, routes.health);
      if (active !== session) return;
      active.unlocked = true;
      byId("unlock-panel").hidden = true;
      byId("console").hidden = false;
      terrain.value = defaultTerrain;
      byId("refresh").focus();
      timer = setInterval(refresh, 10000);
      void loadTerrain();
      await refresh();
    } catch {
      if (active === session) lock("Could not unlock the dashboard. Check the middleware connection.", true);
    }
  });

  byId("broadcast-form").addEventListener("submit", event => {
    event.preventDefault();
    const field = byId("broadcast");
    const value = clean(field.value, 250);
    if (!value) return showStatus("Enter a message to send.", true, "quick-status");
    return queue({commands: [], narrative_broadcast: value}, byId("send-broadcast"), "quick-status",
      "Broadcast queued for the game.", () => { field.value = ""; });
  });
  byId("clock-form").addEventListener("submit", event => {
    event.preventDefault();
    const match = /^([01]\d|2[0-3]):([0-5]\d)$/.exec(byId("game-clock").value);
    if (!match) return showStatus("Enter a valid in-game time.", true, "quick-status");
    return queue({commands: [], time_override: Number(match[1]) + Number(match[2]) / 60},
      byId("send-clock"), "quick-status", "Time change queued for the game.");
  });
  for (const button of sideButtons) {
    button.addEventListener("click", () => showSideView(button.dataset.sideView));
  }
  byId("chat-form").addEventListener("submit", async event => {
    event.preventDefault();
    const active = session;
    if (!active?.unlocked || active.chatting) return;
    const field = byId("chat-input");
    const question = clean(field.value, 1000);
    if (!question) return showStatus("Enter a question.", true, "chat-status");
    active.chatting = true;
    field.disabled = true;
    renderChat();
    showStatus("Waiting for the Game Master…", false, "chat-status");
    try {
      const messages = transcript.slice(-4).map(item => ({role: item.role, text: item.text.slice(0, 1600)}));
      messages.push({role: "user", text: question});
      const answer = await request(active, chatPath, "POST", JSON.stringify({messages}));
      if (active !== session) return;
      transcript = [...transcript.slice(-22), messages.at(-1),
        {role: "assistant", text: clean(answer.reply, 3000), age: answer.report_age_seconds}];
      field.value = "";
      showStatus("", false, "chat-status");
    } catch (error) {
      if (active === session) showStatus(error.message, true, "chat-status");
    } finally {
      active.chatting = false;
      if (active === session) {
        field.disabled = false;
        renderChat();
      }
    }
  });
  byId("chat-clear").addEventListener("click", () => {
    if (session?.chatting) return;
    transcript = [];
    showStatus("", false, "chat-status");
    renderChat();
  });
  byId("command-form").addEventListener("submit", event => {
    event.preventDefault();
    try {
      const batch = JSON.parse(byId("batch").value);
      if (!batch || !Array.isArray(batch.commands)) throw new Error();
      return queue(batch, byId("queue"), "operator-status");
    } catch {
      showStatus('Enter a JSON object with a "commands" array.', true, "operator-status");
    }
  });
  byId("reset").addEventListener("click", async () => {
    const active = session;
    if (!active?.unlocked || !window.confirm("Reset current session data and the command log?")) return;
    byId("reset").disabled = true;
    try {
      await request(active, "/api/reset", "POST");
      if (active !== session) return;
      showStatus("Session data reset.", false, "operator-status");
      await refresh();
    } catch (error) {
      if (active === session) showStatus(error.message, true, "operator-status");
    } finally {
      if (active === session) byId("reset").disabled = false;
    }
  });

  svg.addEventListener("pointerdown", event => {
    if (!imageUrl || event.button !== 0 || drag || event.target.closest("[data-track]")) return;
    drag = {id: event.pointerId, x: event.clientX, y: event.clientY};
    svg.dataset.drag = "true";
    svg.setPointerCapture(event.pointerId);
  });
  svg.addEventListener("pointermove", event => {
    if (!imageUrl) return;
    if (drag?.id === event.pointerId) {
      camera.x -= (event.clientX - drag.x) * view.metersPerPixel;
      camera.y -= (event.clientY - drag.y) * view.metersPerPixel;
      drag.x = event.clientX;
      drag.y = event.clientY;
      scheduleDraw();
    }
    const point = pointAt(event.clientX, event.clientY);
    setMapPosition(point.x >= 0 && point.x <= WORLD && point.y >= 0 && point.y <= WORLD ? point : null);
  });
  svg.addEventListener("lostpointercapture", event => {
    if (drag?.id === event.pointerId) drag = undefined;
    svg.dataset.drag = "false";
  });
  svg.addEventListener("pointerleave", () => setMapPosition(null));
  svg.addEventListener("wheel", event => {
    if (!imageUrl) return;
    event.preventDefault();
    zoom(event.deltaY < 0 ? .82 : 1.22, event.clientX, event.clientY);
  }, {passive: false});
  svg.addEventListener("click", event => {
    const marker = event.target.closest("[data-track]");
    if (marker) chooseTrack(marker.dataset.track);
  });
  svg.addEventListener("keydown", event => {
    if (!imageUrl) return;
    const marker = event.target.closest("[data-track]");
    if (marker && (event.key === "Enter" || event.key === " ")) {
      event.preventDefault();
      return chooseTrack(marker.dataset.track);
    }
    const pan = camera.span / 10;
    const shift = {ArrowLeft: [-pan, 0], ArrowRight: [pan, 0], ArrowUp: [0, -pan], ArrowDown: [0, pan]};
    if (Object.hasOwn(shift, event.key)) {
      const [dx, dy] = shift[event.key];
      camera.x += dx;
      camera.y += dy;
      scheduleDraw();
    } else if (event.key === "+" || event.key === "=") zoom(.78);
    else if (event.key === "-") zoom(1.28);
    else if (event.key === "Home") fit();
    else return;
    event.preventDefault();
  });
  byId("terrain").addEventListener("change", loadTerrain);
  byId("track-picker").addEventListener("change", event => chooseTrack(event.target.value, true));
  byId("focus").addEventListener("click", focusPlayers);
  byId("zoom-in").addEventListener("click", () => zoom(.78));
  byId("zoom-out").addEventListener("click", () => zoom(1.28));
  byId("fit").addEventListener("click", fit);
  for (const input of layerInputs) input.addEventListener("change", () => { renderTrackPicker(); scheduleDraw(); });
  new ResizeObserver(scheduleDraw).observe(svg);
  byId("view").addEventListener("change", refreshDebug);
  byId("diagnostics").addEventListener("toggle", refreshDebug);
  byId("refresh").addEventListener("click", () => {
    if (terrain.value && !imageUrl) void loadTerrain();
    return refresh();
  });
  byId("lock").addEventListener("click", () => {
    lock("Dashboard locked.");
    byId("admin-key").focus();
  });
  window.addEventListener("pagehide", () => lock());
})();
