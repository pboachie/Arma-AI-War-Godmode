"""Windows Workbench/retail qualification for the private split Arma addons.

Run with the project's middleware test Python. The smoke case does not start
tasks or make model calls. --client keeps the private game open for a join test
and reports observations, but does not turn them into a gameplay pass.
"""

import argparse
from contextlib import ExitStack, contextmanager
from hashlib import file_digest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
from tempfile import mkdtemp
from threading import Thread
import time
from urllib.error import URLError
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
CORE_ID = "68EB7BF2F5940DD9"
EVERON_ID = "B161D59D617A4AF5"
STOCK_MISSION = "{ECC61978EDCC2B5A}Missions/23_Campaign.conf"
EVERON_MISSION = "{A485A1003778460A}Missions/AI_War_Everon.conf"
PACK_FILES = ("addon.gproj", "data.pak", "resourceDatabase.rdb")


def free_port(kind):
    with socket.socket(socket.AF_INET, kind) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def read_text(path):
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except (FileNotFoundError, PermissionError):
        return ""


def traces(profile, filename):
    return "\n".join(read_text(p) for p in profile.glob("logs/*/" + filename))


def api(origin, endpoint, *, key=None, body=None):
    headers = {"X-API-Key": key} if key else {}
    if body is not None:
        headers["Content-Type"] = "application/json"
    request = Request(origin + endpoint, None if body is None else json.dumps(body).encode(), headers)
    with urlopen(request, timeout=2) as reply:
        return json.load(reply)


@contextmanager
def process(argv, log, *, cwd, env=None, visible=False):
    flags = 0 if visible else subprocess.CREATE_NO_WINDOW
    with log.open("w", encoding="utf-8") as stream:
        child = subprocess.Popen(argv, cwd=cwd, env=env, stdout=stream, stderr=subprocess.STDOUT,
                                 creationflags=flags)
    try:
        yield child
    finally:
        if child.poll() is None:
            child.terminate()
            try:
                child.wait(15)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(15)


def pack(steam, source, lane):
    workbench = steam / "Arma Reforger Tools/Workbench/ArmaReforgerWorkbenchSteamDiag.exe"
    retail_data = steam / "Arma Reforger/addons"
    packs = lane / "addons"
    for name in ("AIWar", "AIWarEveron"):
        destination = packs / name
        destination.mkdir(parents=True)
        profile = lane / ("workbench-" + name)
        with process([str(workbench), "-gproj", str(source / name / "addon.gproj"),
                      "-addonsDir", f"{retail_data},{source}", "-profile", str(profile),
                      "-wbModule", "ResourceManager", "-packAddon", "-packAddonDir", str(destination)],
                     lane / (name + "-pack.log"), cwd=workbench.parent) as child:
            if child.wait(120) != 0:
                raise RuntimeError(f"{name} Workbench pack failed, exit {child.returncode}")
        print(f"Packed {name}", flush=True)
    return packs


def manifest(addons):
    records = {}
    for name in ("AIWar", "AIWarEveron"):
        records[name] = {}
        for item in PACK_FILES:
            with (addons / name / item).open("rb") as stream:
                records[name][item] = file_digest(stream, "sha256").hexdigest()
    return records


def state_view(state):
    """Keep account data, player names/locations, and secrets out of evidence."""
    side = state.get("side_operations")
    if not isinstance(side, dict) or side.get("version") != 1:
        side = None
    else:
        side = {name: side.get(name) for name in
                ("version", "session_id", "revision", "routes", "active", "outcomes")}
    return {"players": state.get("player_count"), "side_operations": side}


@contextmanager
def fixture_provider(origin, admin, evidence):
    """Loopback-only Ollama fixture, not a real model or an in-game bypass."""
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            if self.path != "/api/chat":
                self.send_error(404)
                return
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= 2 * 1024 * 1024:
                self.send_error(413)
                return
            data = json.loads(self.rfile.read(size))
            prompt = "\n".join(str(msg.get("content", "")) for msg in data.get("messages", []))
            state = api(origin, "/api/game-state", key=admin)["state"] or {}
            side = state.get("side_operations") or {}
            answer = {"commands": [], "narrative_broadcast": "", "weather_command": "", "time_override": -1}
            if "Core owns execution and outcomes" in prompt:
                answer = {"choice": 0}
                if not side.get("active"):
                    answer.update(fixture_choice(side, prompt, evidence))
            reply = json.dumps({"model": "aiwar-local-fixture", "created_at": "2026-01-01T00:00:00Z",
                                "message": {"role": "assistant", "content": json.dumps(answer)},
                                "done": True, "done_reason": "stop"}) + "\n"
            result = reply.encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/x-ndjson" if data.get("stream") else "application/json")
            self.send_header("Content-Length", str(len(result)))
            self.end_headers()
            self.wfile.write(result)

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as httpd:
        server_thread = Thread(target=httpd.serve_forever, daemon=True)
        server_thread.start()
        try:
            yield f"http://127.0.0.1:{httpd.server_port}"
        finally:
            httpd.shutdown()
            server_thread.join()


def fixture_choice(side, prompt, evidence):
    context = None
    for line in reversed(prompt.splitlines()):
        try:
            data = json.loads(line)
        except ValueError:
            continue
        if isinstance(data, dict) and isinstance(data.get("opportunities"), list):
            context = data
            break
    if not context:
        return {}
    eligible = {r["route_id"] for r in side.get("routes", []) if r["eligible_players"]}
    offered = [option for option in context["opportunities"] if option.get("route_id") in eligible]
    outcomes = side.get("outcomes", [])
    latest = max(outcomes, key=lambda item: item["revision"], default=None)
    kind = "supply_run"
    if latest and latest["kind"] == "supply_run" and latest["status"] == "completed":
        fields = ("route_id", "kind", "status", "revision")
        seen = any(all(outcome.get(field) == latest[field] for field in fields)
                   for outcome in context.get("previous_outcomes", []))
        if not seen:
            return {}
        offered = [option for option in offered if option["route_id"] == latest["route_id"]]
        kind = "defense"
    elif latest:
        return {}
    choice = next((option for option in offered if option.get("kind") == kind), None)
    if choice is None or type(choice.get("choice")) is not int:
        return {}
    record = {"revision": side["revision"], "fixture_choice": choice,
              "observed_outcome_id": latest["operation_id"] if latest else None}
    with evidence.open("a", encoding="utf-8") as log:
        log.write(json.dumps(record) + "\n")
    return {"choice": choice["choice"]}


def check_game(args, steam, addons, lane, name, origin, game_key, admin):
    case = lane / name
    profile = case / "profile"
    profile.mkdir(parents=True)
    keypath = profile / "godmode_api_key.txt"
    keypath.write_bytes((game_key + "\r\n").encode("ascii"))
    (profile / "godmode_server_url.txt").write_bytes((origin + "\r\n").encode("ascii"))
    gport = free_port(socket.SOCK_DGRAM)
    qport = free_port(socket.SOCK_DGRAM)
    while qport == gport:
        qport = free_port(socket.SOCK_DGRAM)
    stock = name == "stock"
    with_fixture = args.fixture_model and not stock
    scenario_id = STOCK_MISSION if stock else EVERON_MISSION
    addons_needed = [{"modId": CORE_ID, "name": "AI War - Core"}]
    if not stock:
        addons_needed.append({"modId": EVERON_ID, "name": "AI War - Everon"})
    cfg = json.loads((ROOT / "Configs/ArmaServer/config.template.json").read_text())
    cfg.update(bindPort=gport, publicPort=gport)
    cfg["a2s"]["port"] = qport
    cfg["game"].update(name="AI War Side Operations (local qualification)",
                       scenarioId=scenario_id, maxPlayers=4, mods=addons_needed,
                       passwordAdmin=secrets.token_urlsafe(18))
    cfg["game"]["gameProperties"]["battlEye"] = False
    cfgfile = case / "config.json"
    cfgfile.write_text(json.dumps(cfg), encoding="ascii")

    marker = f"Side operations qualification {name} {secrets.token_hex(3)}"
    message = {"commands": [], "narrative_broadcast": marker, "time_override": 9.25}
    if not with_fixture:
        api(origin, "/api/manual-command", key=admin, body=message)

    server_binary = steam / "Arma Reforger Server/ArmaReforgerServer.exe"
    argv = [str(server_binary), "-config", str(cfgfile), "-addonsDir", str(addons),
            "-profile", str(case), "-noThrow", "-maxFPS", "30", "-backendDisableStorage", "-listScenarios"]
    observations = []
    try:
        with process(argv, case / "dedicated.log", cwd=server_binary.parent) as server:
            end = time.monotonic() + args.startup_seconds
            while time.monotonic() < end:
                if server.poll() is not None:
                    raise RuntimeError(f"{name} dedicated server exited {server.returncode}")
                scripts = traces(case, "script.log")
                console = traces(case, "console.log")
                listen = [line for line in console.splitlines() if "Starting RPL server, listening" in line]
                if any(f"127.0.0.1:{gport}," not in line for line in listen):
                    raise RuntimeError("Dedicated server did not honor the private bind address")
                response_applied = with_fixture or (marker in scripts and "[GodMode:Weather] Time" in scripts)
                if response_applied and listen and scenario_id in console:
                    state = api(origin, "/api/game-state", key=admin)["state"]
                    if state is not None:
                        break
                time.sleep(1)
            else:
                raise RuntimeError(f"{name} timed out before scenario and authenticated command acknowledgment")

            actual = console.lower().replace("\\", "/")
            for mod in (("AIWar",) if stock else ("AIWar", "AIWarEveron")):
                packed = (addons / mod).as_posix().lower()
                pack_seen = any(f"'{packed}/addon.gproj'" in line and "(packed)" in line
                                for line in actual.splitlines())
                mounted = [line for line in actual.splitlines()
                           if "filesystem: adding package" in line and line.endswith(f"under name {mod.lower()}")]
                if not pack_seen or not mounted or any(f"'{packed}/'" not in line for line in mounted):
                    raise RuntimeError(f"{name} could not prove {mod} mounted from {packed}")
            snapshot = state_view(state)
            side = snapshot["side_operations"]
            if args.baseline or stock:
                if side and side.get("routes"):
                    raise RuntimeError(f"{name} unexpectedly advertises side operations")
            else:
                if not side or not side.get("routes") or side.get("active"):
                    raise RuntimeError("Everon did not advertise an idle, versioned operation capability")
                if any(route["eligible_players"] != 0 for route in side["routes"]):
                    raise RuntimeError("Player-free server reported an eligible player")
            observations.append(snapshot)
            marker_result = "" if with_fixture else "legacy command applied, "
            routes = len(side["routes"]) if side else 0
            print(f"PASS {name} smoke: exact packed game, authenticated bridge, "
                  f"{marker_result}{routes} side-operation routes", flush=True)

            if args.client and not stock:
                client_profile = lane / "client"
                game_binary = steam / "Arma Reforger/ArmaReforgerSteam.exe"
                client_args = [str(game_binary), "-addonsDir", str(addons), "-addons", EVERON_ID,
                               "-profile", str(client_profile),
                               "-window", "-screenWidth", "1200", "-screenHeight", "720"]
                if not args.client_menu:
                    client_args.extend(["-client", f"127.0.0.1:{gport}"])
                with process(client_args, lane / "client-stdout.log", cwd=game_binary.parent,
                             visible=True) as client:
                    print(f"Client started for loopback UDP {gport}, profile {client_profile}", flush=True)
                    hold_end = time.monotonic() + args.client_seconds
                    while time.monotonic() < hold_end and server.poll() is None and client.poll() is None:
                        state = api(origin, "/api/game-state", key=admin)["state"]
                        if state is not None:
                            snapshot = state_view(state)
                            if snapshot != observations[-1]:
                                observations.append(snapshot)
                                print(json.dumps(snapshot), flush=True)
                        time.sleep(1)
    finally:
        keypath.unlink(missing_ok=True)
        cfgfile.unlink(missing_ok=True)
        (case / "observations.json").write_text(json.dumps(observations, indent=2) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pilot", type=Path, required=True,
                        help="Private qualification directory where new per-run profiles and logs are created")
    builds = parser.add_mutually_exclusive_group(required=True)
    builds.add_argument("--source", type=Path, help="Private addon source parent to pack")
    builds.add_argument("--addons", type=Path, help="Parent of existing packs (AIWar and AIWarEveron)")
    parser.add_argument("--baseline", action="store_true", help="Existing release with no side operations")
    parser.add_argument("--case", choices=["stock", "everon", "both"], default="both")
    parser.add_argument("--client", action="store_true", help="Launch the retail client in the Everon test")
    parser.add_argument("--client-menu", action="store_true", help="Start at the menu for manual direct join")
    parser.add_argument("--fixture-model", action="store_true", help="Exercise the local provider bridge with fixture choices")
    parser.add_argument("--client-seconds", type=int, default=180)
    parser.add_argument("--startup-seconds", type=int, default=120)
    args = parser.parse_args()
    if args.client_menu and not args.client:
        parser.error("--client-menu requires --client")
    if args.fixture_model and args.baseline:
        parser.error("The fixture model requires the side-operation-capable candidate")
    if os.name != "nt":
        parser.error("Windows Steam retail and Workbench are required")
    steam = Path(os.environ["ProgramFiles(x86)"]) / "Steam/steamapps/common"
    lane = Path(mkdtemp(prefix="qualify-", dir=args.pilot.resolve(strict=True)))
    print(f"Local qualification evidence at {lane}", flush=True)
    packs = args.addons.resolve(strict=True) if args.addons else pack(steam, args.source.resolve(strict=True), lane)
    hashes = manifest(packs)
    (lane / "package-hashes.json").write_text(json.dumps(hashes, indent=2) + "\n", encoding="utf-8")
    for case in (("stock", "everon") if args.case == "both" else (args.case,)):
        key, admin = (secrets.token_urlsafe(36) for _ in range(2))
        env = dict(os.environ, GODMODE_GAME_API_KEY=key, GODMODE_API_KEY=admin, LLM_PROVIDER="disabled",
                   GODMODE_STATE_DIR=str(lane / (case + "-middleware-state")), GODMODE_RECORD_REPLAYS="false",
                   RCON_ENABLED="false", SERVER_CONTROL_ENABLED="false", CORS_ORIGINS="",
                   FALLBACK_PROVIDER="", PYTHON_DOTENV_DISABLED="1")
        port = free_port(socket.SOCK_STREAM)
        origin = f"http://127.0.0.1:{port}"
        with ExitStack() as stack:
            if args.fixture_model and case == "everon":
                fixture_url = stack.enter_context(fixture_provider(origin, admin, lane / "fixture-decisions.jsonl"))
                env.update(LLM_PROVIDER="ollama", LLM_MODEL="aiwar-local-fixture", OLLAMA_BASE_URL=fixture_url,
                           AGENTS_ENABLED="false")
            middle = stack.enter_context(process([sys.executable, "-m", "hypercorn", "server:app", "--bind",
                                                  f"127.0.0.1:{port}", "--access-logfile", "-"],
                                                 lane / (case + "-middleware.log"), cwd=ROOT / "middleware", env=env))
            health_end = time.monotonic() + 30
            while time.monotonic() < health_end and middle.poll() is None:
                try:
                    api(origin, "/health")
                    break
                except (OSError, URLError):
                    time.sleep(.25)
            else:
                raise RuntimeError(f"{case} middleware health check failed")
            check_game(args, steam, packs, lane, case, origin, key, admin)
    if manifest(packs) != hashes:
        raise RuntimeError("Packed addon bytes changed during qualification")


if __name__ == "__main__":
    main()
