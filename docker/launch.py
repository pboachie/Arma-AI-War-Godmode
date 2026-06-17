#!/usr/bin/env python3
"""
Arma Reforger Dedicated Server Launcher
Modeled on soda3x/docker-reforger-server.

Handles SteamCMD updates, server launch, auto-restart, and clean shutdown.
"""
from __future__ import annotations

from typing import Optional
import os
import signal
import shutil
import subprocess
import sys
import time
from pathlib import Path

# ---------------------------------------------------------------------------
# Configuration from environment variables
# ---------------------------------------------------------------------------
CONFIG = os.environ.get("CONFIG", "config")
MAX_FPS = int(os.environ.get("MAX_FPS", "60"))
MAX_RESTARTS = os.environ.get("MAX_RESTARTS", "0")
FREEZE_CHECK = int(os.environ.get("FREEZE_CHECK", "300"))
USE_EXPERIMENTAL = os.environ.get("USE_EXPERIMENTAL", "false").lower() in (
    "true",
    "1",
    "yes",
)
STEAM_USER = os.environ.get("STEAM_USER", "").strip()
STEAM_PASS = os.environ.get("STEAM_PASS", "").strip()
STARTUP_PARAMETERS = os.environ.get("STARTUP_PARAMETERS", "")


def _env_flag(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")

# Paths
STEAMCMD_PATH = "/home/reforger/steamcmd/steamcmd.sh"
INSTALL_DIR = "/home/reforger/reforger_bins"
SERVER_BINARY = os.path.join(INSTALL_DIR, "ArmaReforgerServer")
CONFIG_DIR = "/home/reforger/configs"
PROFILE_DIR = "/home/reforger/profile"
WORKSHOP_DIR = "/home/reforger/workshop"
PROFILE_ADDONS_DIR = os.path.join(PROFILE_DIR, "addons")
WORKSHOP_ADDONS_DIR = os.path.join(WORKSHOP_DIR, "addons")
LOCAL_ADDON_SOURCE_ROOT = Path(
    os.environ.get("ARMA_LOCAL_ADDON_SOURCE_ROOT", "/mnt/godmode-source")
)
LOCAL_ADDON_SCRIPTS_SOURCE = LOCAL_ADDON_SOURCE_ROOT / "scripts" / "Game"
LOCAL_ADDON_CONFIGS_SOURCE = LOCAL_ADDON_SOURCE_ROOT / "Configs"
LOCAL_ADDON_ENABLED = _env_flag("ARMA_LOCAL_ADDON_ENABLED", True)
LOCAL_ADDON_ID = os.environ.get("ARMA_LOCAL_ADDON_ID", "").strip()
LOCAL_ADDON_NAME = (
    os.environ.get("ARMA_LOCAL_ADDON_NAME", "").strip()
    or LOCAL_ADDON_ID
    or "GodMode"
)

# App IDs
APP_ID_STABLE = 1874900
APP_ID_EXPERIMENTAL = 1890870

# ---------------------------------------------------------------------------
# Globals
# ---------------------------------------------------------------------------
server_process = None
shutdown_requested = False


def _signal_handler(signum, frame):
    """Handle SIGTERM and SIGINT for clean shutdown."""
    global shutdown_requested
    sig_name = signal.Signals(signum).name
    print(f"\n[launcher] Received {sig_name} — shutting down server...", flush=True)
    shutdown_requested = True
    _kill_server()


def _kill_server():
    """Terminate the server process group."""
    global server_process
    if server_process is not None and server_process.poll() is None:
        try:
            pgid = os.getpgid(server_process.pid)
            os.killpg(pgid, signal.SIGTERM)
            # Give the server a few seconds to shut down gracefully
            try:
                server_process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                print(
                    "[launcher] Server did not exit in time, sending SIGKILL...",
                    flush=True,
                )
                os.killpg(pgid, signal.SIGKILL)
                server_process.wait(timeout=5)
        except ProcessLookupError:
            pass
        except Exception as exc:
            print(f"[launcher] Error during shutdown: {exc}", flush=True)


def update_server():
    """Run SteamCMD to install / update the Arma Reforger dedicated server."""
    app_id = APP_ID_EXPERIMENTAL if USE_EXPERIMENTAL else APP_ID_STABLE
    branch_label = "experimental" if USE_EXPERIMENTAL else "stable"
    print(
        f"[launcher] Updating Arma Reforger server (app {app_id}, {branch_label})...",
        flush=True,
    )

    # Arma Reforger Dedicated Server requires authenticated login
    if STEAM_USER:
        login_args = ["+login", STEAM_USER, STEAM_PASS]
    else:
        print(
            "[launcher] WARNING: No STEAM_USER set — trying anonymous login. "
            "Arma Reforger DS typically requires authentication.",
            flush=True,
        )
        login_args = ["+login", "anonymous"]

    cmd = [
        STEAMCMD_PATH,
        "+force_install_dir",
        INSTALL_DIR,
        *login_args,
        "+app_update",
        str(app_id),
        "validate",
        "+quit",
    ]

    result = subprocess.run(cmd, stdout=sys.stdout, stderr=sys.stderr)
    if result.returncode != 0:
        print(
            f"[launcher] WARNING: SteamCMD exited with code {result.returncode}",
            flush=True,
        )
    else:
        print("[launcher] Server update completed successfully.", flush=True)

    # Verify the binary was actually installed
    if not os.path.isfile(SERVER_BINARY):
        print(
            f"[launcher] ERROR: Server binary not found at {SERVER_BINARY}",
            flush=True,
        )
        print(
            "[launcher] SteamCMD likely failed to download. "
            "Set STEAM_USER and STEAM_PASS environment variables "
            "with a Steam account that owns Arma Reforger.",
            flush=True,
        )
        sys.exit(1)


def _copy_tree_contents(source: Path, target: Path) -> bool:
    """Copy the direct children of *source* into *target*.

    Returns True when any content was copied.
    """
    if not source.is_dir():
        return False

    target.mkdir(parents=True, exist_ok=True)
    copied = False

    for entry in source.iterdir():
        destination = target / entry.name
        if destination.exists():
            if destination.is_dir():
                shutil.rmtree(destination)
            else:
                destination.unlink()

        if entry.is_dir():
            shutil.copytree(entry, destination)
        else:
            shutil.copy2(entry, destination)
        copied = True

    return copied


def stage_local_addon() -> Optional[str]:
    """Copy local addon sources into the dedicated server's addon search path."""
    addon_id = LOCAL_ADDON_ID or LOCAL_ADDON_NAME
    if not LOCAL_ADDON_ENABLED or not addon_id:
        return None

    addon_root = Path(PROFILE_ADDONS_DIR) / LOCAL_ADDON_NAME
    staged_any = False

    if LOCAL_ADDON_SCRIPTS_SOURCE.is_dir():
        staged_any |= _copy_tree_contents(
            LOCAL_ADDON_SCRIPTS_SOURCE,
            addon_root / "scripts" / "Game",
        )

    if LOCAL_ADDON_CONFIGS_SOURCE.is_dir():
        staged_any |= _copy_tree_contents(
            LOCAL_ADDON_CONFIGS_SOURCE,
            addon_root / "Configs",
        )

    if not staged_any:
        print(
            "[launcher] Local addon staging skipped — no addon content found at "
            f"{LOCAL_ADDON_SOURCE_ROOT}",
            flush=True,
        )
        return None

    # Create a minimal .gproj so the engine discovers this addon via -addonsDir.
    gproj_path = addon_root / f"{LOCAL_ADDON_NAME}.gproj"
    if not gproj_path.exists():
        gproj_content = (
            f'GameProject {{\n'
            f' ID "{addon_id}"\n'
            f' TITLE "{LOCAL_ADDON_NAME}"\n'
            f' Configurations {{\n'
            f'  GameProjectConfig PC {{\n'
            f'   PlatformHardware PC\n'
            f'   ScriptProjectManagerSettings ScriptProjectManagerSettings "{{}}" {{\n'
            f'    Modules {{\n'
            f'     ScriptModulePathClass game {{\n'
            f'      Paths {{\n'
            f'       "scripts/Game"\n'
            f'      }}\n'
            f'     }}\n'
            f'    }}\n'
            f'   }}\n'
            f'  }}\n'
            f'  GameProjectConfig HEADLESS : PC {{\n'
            f'   PlatformHardware PC\n'
            f'  }}\n'
            f' }}\n'
            f'}}\n'
        )
        gproj_path.write_text(gproj_content)
        print(f"[launcher] Created {gproj_path}", flush=True)

    print(
        "[launcher] Staged local addon "
        f"'{LOCAL_ADDON_NAME}' in {addon_root} (id={addon_id})",
        flush=True,
    )
    return addon_id


def launch_server():
    """Start the Arma Reforger dedicated server binary and return the process."""
    global server_process

    config_path = os.path.join(CONFIG_DIR, f"{CONFIG}.json")
    if not os.path.isfile(config_path):
        print(
            f"[launcher] ERROR: Config file not found: {config_path}",
            flush=True,
        )
        sys.exit(1)

    os.makedirs(PROFILE_DIR, exist_ok=True)
    os.makedirs(PROFILE_ADDONS_DIR, exist_ok=True)
    os.makedirs(WORKSHOP_DIR, exist_ok=True)
    os.makedirs(WORKSHOP_ADDONS_DIR, exist_ok=True)

    local_addon_id = stage_local_addon()
    addon_dirs = [WORKSHOP_ADDONS_DIR]
    if local_addon_id:
        addon_dirs.insert(0, PROFILE_ADDONS_DIR)

    cmd = [
        SERVER_BINARY,
        "-config",
        config_path,
        "-createDB",
        "-nothrow",
        "-maxFPS",
        str(MAX_FPS),
        "-profile",
        PROFILE_DIR,
        "-addonDownloadDir",
        WORKSHOP_DIR,
        "-addonsDir",
        ",".join(addon_dirs),
        "-freezeCheck",
        str(FREEZE_CHECK),
        "-freezeCheckMode",
        "crash",
    ]

    # NOTE: Enfusion forbids combining -config with -addons on the CLI.
    # Local addons are discovered automatically via -addonsDir (they need a .gproj).

    if STARTUP_PARAMETERS:
        cmd.extend(STARTUP_PARAMETERS.split())

    print(f"[launcher] Starting server: {' '.join(cmd)}", flush=True)

    # Run from install dir so ./addons resolves to the game's built-in addons
    server_process = subprocess.Popen(
        cmd,
        stdout=sys.stdout,
        stderr=sys.stderr,
        cwd=INSTALL_DIR,
        preexec_fn=os.setsid,
    )

    return server_process


def main():
    signal.signal(signal.SIGTERM, _signal_handler)
    signal.signal(signal.SIGINT, _signal_handler)

    print("=" * 60, flush=True)
    print("  Arma Reforger Dedicated Server Launcher", flush=True)
    print("=" * 60, flush=True)
    print(f"  CONFIG          : {CONFIG}", flush=True)
    print(f"  MAX_FPS         : {MAX_FPS}", flush=True)
    print(f"  MAX_RESTARTS    : {MAX_RESTARTS}", flush=True)
    print(f"  FREEZE_CHECK    : {FREEZE_CHECK}", flush=True)
    print(f"  USE_EXPERIMENTAL: {USE_EXPERIMENTAL}", flush=True)
    print(f"  STEAM_USER      : {STEAM_USER or '(anonymous)'}", flush=True)
    print(f"  STARTUP_PARAMS  : {STARTUP_PARAMETERS or '(none)'}", flush=True)
    print(f"  LOCAL_ADDON_ID  : {LOCAL_ADDON_ID or '(disabled)'}", flush=True)
    print(f"  LOCAL_ADDON_NAME: {LOCAL_ADDON_NAME}", flush=True)
    print("=" * 60, flush=True)

    update_server()

    infinite_restarts = MAX_RESTARTS.lower() == "none"
    max_restart_count = 0 if infinite_restarts else int(MAX_RESTARTS)
    restart_count = 0

    while not shutdown_requested:
        proc = launch_server()
        proc.wait()
        exit_code = proc.returncode

        if shutdown_requested:
            print("[launcher] Shutdown was requested — not restarting.", flush=True)
            break

        print(
            f"[launcher] Server exited with code {exit_code}.",
            flush=True,
        )

        if not infinite_restarts and restart_count >= max_restart_count:
            print(
                f"[launcher] Reached max restarts ({max_restart_count}). Exiting.",
                flush=True,
            )
            break

        restart_count += 1
        label = "inf" if infinite_restarts else f"{restart_count}/{max_restart_count}"
        print(
            f"[launcher] Restarting server (attempt {label})...",
            flush=True,
        )
        # Brief pause to avoid tight restart loops on immediate crashes
        time.sleep(3)

    print("[launcher] Launcher exiting.", flush=True)
    sys.exit(0)


if __name__ == "__main__":
    main()
