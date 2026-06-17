"""
server_manager.py — Arma Reforger Docker Container Lifecycle Manager

Manages the Arma Reforger dedicated server running as a Docker container
(via docker-compose). Provides async methods to start, stop, restart,
monitor, and inspect the game server from the God Mode middleware.

Usage:
    from server_manager import create_server_manager

    mgr = create_server_manager()
    await mgr.start()
    status = await mgr.get_status()
    await mgr.start_monitor()
"""

import asyncio
import json
import os
import time
import logging
from datetime import datetime, timezone
from typing import Optional

log = logging.getLogger("godmode.server_manager")


# ═══════════════════════════════════════════════════════════
# DOCKER AVAILABILITY CHECK
# ═══════════════════════════════════════════════════════════

_COMPOSE_CMD: list[str] = ["docker", "compose"]  # v2 plugin (default)


async def _check_docker_available() -> bool:
    """Return True if docker compose (v2 plugin) or docker-compose (legacy) is callable."""
    global _COMPOSE_CMD

    # Try v2 plugin first: docker compose
    for cmd in (["docker", "compose"], ["docker-compose"]):
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd, "version",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await proc.communicate()
            if proc.returncode == 0:
                _COMPOSE_CMD = cmd
                return True
        except (FileNotFoundError, OSError):
            continue
    return False


# ═══════════════════════════════════════════════════════════
# ARMA SERVER MANAGER
# ═══════════════════════════════════════════════════════════

class ArmaServerManager:
    """Manages the Arma Reforger Docker container lifecycle.

    All public methods are async and safe to call even when Docker is not
    installed — they return graceful "not available" responses instead of
    raising exceptions.
    """

    def __init__(
        self,
        compose_file: str = None,
        service_name: str = "arma-server",
        project_root: str = None,
    ):
        self._project_root = project_root or os.getenv("PROJECT_ROOT", "..")
        self._compose_candidates = self._build_compose_candidates(compose_file, self._project_root)
        self._compose_file = self._compose_candidates[0] if self._compose_candidates else (
            compose_file or os.path.join(self._project_root, "docker", "docker-compose.arma.yml")
        )
        self._service = service_name
        self._start_time: Optional[float] = None
        self._restart_count: int = 0
        self._last_crash: Optional[str] = None
        self._monitor_task: Optional[asyncio.Task] = None
        self._docker_available: Optional[bool] = None
        self._was_running: bool = False
        self._rcon_connected_fn = None  # set externally to check RCON status
        self._process_mode = os.getenv("GODMODE_PROCESS_MODE", "").lower() in ("true", "1", "yes")

    def _build_compose_candidates(self, compose_file: Optional[str], project_root: str) -> list[str]:
        """Build an ordered list of compose files that may own the arma-server service."""
        candidates: list[str] = []

        def _add(path: Optional[str]):
            if not path:
                return
            normalized = os.path.abspath(path)
            if normalized not in candidates and os.path.isfile(normalized):
                candidates.append(normalized)

        _add(os.getenv("ARMA_COMPOSE_FILE"))
        _add(os.getenv("COMPOSE_FILE"))
        _add(compose_file)
        _add(os.path.join(project_root, "docker", "docker-compose.arma.yml"))
        _add(os.path.join(project_root, "middleware", "docker-compose.yml"))
        _add(os.path.join(os.getcwd(), "docker-compose.yml"))

        return candidates

    async def _probe_compose_service(self, compose_file: str) -> tuple[bool, Optional[str]]:
        """Return (running, container_id) for a compose file candidate."""
        cmd = [*_COMPOSE_CMD, "-f", compose_file, "ps", "-q", self._service]
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()
            if proc.returncode != 0:
                return False, None

            container_id = stdout.decode(errors="replace").strip().splitlines()
            if container_id and container_id[0].strip():
                return True, container_id[0].strip()
        except (FileNotFoundError, OSError):
            return False, None

        return False, None

    async def _resolve_compose_file(self) -> str:
        """Pick the compose file that currently owns the running arma-server container.

        Falls back to the first existing candidate when no running service is found.
        """
        if not await self._ensure_docker_check():
            return self._compose_file

        for candidate in self._compose_candidates:
            running, _ = await self._probe_compose_service(candidate)
            if running:
                if candidate != self._compose_file:
                    log.info("Detected running arma-server via compose file: %s", candidate)
                self._compose_file = candidate
                return candidate

        return self._compose_file

    # ───────────────────────────────────────────────────────
    # Internal helpers
    # ───────────────────────────────────────────────────────

    async def _ensure_docker_check(self) -> bool:
        """Cache the docker-availability check so we only probe once."""
        if self._docker_available is None:
            self._docker_available = await _check_docker_available()
            if not self._docker_available:
                log.warning(
                    "Docker Compose CLI is not available inside the middleware runtime — "
                    "API lifecycle control is disabled, but a server started externally "
                    "can still be reached over RCON/A2S"
                )
        return self._docker_available

    async def _run_docker(self, *args: str) -> tuple[int, str, str]:
        """Run a docker-compose command and return (returncode, stdout, stderr).

        If docker-compose is not available, returns (-1, "", "docker-compose not available").
        """
        if not await self._ensure_docker_check():
            return -1, "", "docker-compose not available"

        await self._resolve_compose_file()

        cmd = [*_COMPOSE_CMD, "-f", self._compose_file, *args]
        log.debug("Running: %s", " ".join(cmd))

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()
            rc = proc.returncode
            out = stdout.decode(errors="replace")
            err = stderr.decode(errors="replace")

            if rc != 0:
                log.debug(
                    "docker-compose %s exited %d: %s",
                    " ".join(args),
                    rc,
                    err.strip() or out.strip(),
                )

            return rc, out, err
        except FileNotFoundError:
            self._docker_available = False
            return -1, "", "docker-compose not found"
        except OSError as exc:
            log.error("OS error running docker-compose: %s", exc)
            return -1, "", str(exc)

    # ───────────────────────────────────────────────────────
    # Lifecycle methods
    # ───────────────────────────────────────────────────────

    async def start(self) -> bool:
        """Start the game server. Returns True on success."""
        log.info("Starting Arma server (service=%s) ...", self._service)
        if self._process_mode:
            success = await self._supervisor_ctl("start", "arma-server")
        else:
            rc, out, err = await self._run_docker("up", "-d", self._service)
            success = rc == 0
            if not success:
                log.error("Failed to start Arma server: %s", err.strip() or out.strip())
        if success:
            self._start_time = time.time()
            self._was_running = True
            log.info("Arma server started successfully")
        return success

    async def stop(self, timeout: float = 30.0) -> bool:
        """Stop the game server gracefully."""
        log.info("Stopping Arma server (service=%s, timeout=%.0fs) ...", self._service, timeout)
        if self._process_mode:
            success = await self._supervisor_ctl("stop", "arma-server")
        else:
            rc_stop, _, err_stop = await self._run_docker(
                "stop", "-t", str(int(timeout)), self._service
            )
            await self._run_docker("rm", "-f", self._service)
            success = rc_stop == 0
            if not success:
                log.error("Failed to stop Arma server: %s", err_stop.strip())
        if success:
            self._start_time = None
            self._was_running = False
            log.info("Arma server stopped")
        return success

    async def restart(self) -> bool:
        """Restart the game server and increment the restart counter."""
        log.info("Restarting Arma server ...")
        if self._process_mode:
            await self._supervisor_ctl("restart", "arma-server")
            self._start_time = time.time()
            self._was_running = True
            self._restart_count += 1
            log.info("Arma server restarted (total restarts: %d)", self._restart_count)
            return True
        await self.stop()
        success = await self.start()
        if success:
            self._restart_count += 1
            log.info("Arma server restarted (total restarts: %d)", self._restart_count)
        return success

    # ───────────────────────────────────────────────────────
    # Status / inspection
    # ───────────────────────────────────────────────────────

    async def _is_process_running(self) -> bool:
        """Check if ArmaReforgerServer process is running (same-container mode)."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "pgrep", "-f", "ArmaReforgerServer",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await proc.communicate()
            return proc.returncode == 0 and bool(stdout.strip())
        except (FileNotFoundError, OSError):
            return False

    async def _supervisor_ctl(self, action: str, program: str) -> bool:
        """Run a supervisorctl command (process-mode lifecycle control)."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "supervisorctl", "-c", "/etc/supervisor/conf.d/godmode.conf",
                action, program,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()
            success = proc.returncode == 0
            if success:
                log.info("supervisorctl %s %s: OK", action, program)
            else:
                log.error("supervisorctl %s %s failed: %s", action, program,
                          stderr.decode(errors="replace").strip())
            return success
        except (FileNotFoundError, OSError) as exc:
            log.error("supervisorctl not available: %s", exc)
            return False

    async def is_running(self) -> bool:
        """Check if the game server is currently running.

        In process mode: checks for the ArmaReforgerServer process directly.
        In Docker mode: tries docker-compose ps, falls back to RCON.
        """
        # Process mode: check for game server process directly
        if self._process_mode:
            if await self._is_process_running():
                return True
            if self._rcon_connected_fn and self._rcon_connected_fn():
                return True
            return False

        if not await self._ensure_docker_check():
            # Docker unavailable — use RCON as proof the server is up
            if self._rcon_connected_fn and self._rcon_connected_fn():
                return True
            return False

        # Attempt JSON format first (docker-compose v2+)
        rc, out, err = await self._run_docker("ps", "--format", "json")
        if rc == 0 and out.strip():
            result = self._parse_running_json(out)
            if result is not None:
                return result

        # Fallback: plain-text `docker-compose ps`
        rc, out, err = await self._run_docker("ps")
        if rc == 0 and self._parse_running_text(out):
            return True

        # Final fallback: RCON connectivity proves the server is up
        if self._rcon_connected_fn and self._rcon_connected_fn():
            return True

        return False

    def _parse_running_json(self, raw: str) -> Optional[bool]:
        """Parse JSON output from `docker-compose ps --format json`.

        Returns True/False if parsing succeeds, or None if the output
        is not valid JSON (signals the caller to fall back to text parsing).
        """
        try:
            stripped = raw.strip()
            if stripped.startswith("["):
                data = json.loads(stripped)
            else:
                data = [json.loads(line) for line in stripped.splitlines() if line.strip()]

            for entry in data:
                name = entry.get("Service") or entry.get("Name") or ""
                state = (entry.get("State") or entry.get("Status") or "").lower()
                if self._service in name and "running" in state:
                    return True
            return False
        except (json.JSONDecodeError, TypeError, KeyError):
            log.debug("Could not parse JSON from docker-compose ps, falling back")
            return None

    def _parse_running_text(self, raw: str) -> bool:
        """Parse plain-text output from `docker-compose ps`."""
        for line in raw.splitlines():
            if self._service in line and "up" in line.lower():
                return True
        return False

    async def get_container_id(self) -> Optional[str]:
        """Return the container ID for the service, or None."""
        rc, out, err = await self._run_docker("ps", "-q", self._service)
        if rc == 0 and out.strip():
            return out.strip().splitlines()[0]
        return None

    async def get_status(self) -> dict:
        """Get detailed server status as a dict."""
        if self._process_mode:
            running = await self.is_running()
            container_id = None
            docker_ok = None  # not applicable in process mode
        else:
            docker_ok = await self._ensure_docker_check()
            if docker_ok:
                running, container_id = await asyncio.gather(
                    self.is_running(), self.get_container_id()
                )
            else:
                running = await self.is_running()
                container_id = None

        uptime = None
        if running:
            if self._start_time is not None:
                uptime = round(time.time() - self._start_time, 1)
            else:
                self._start_time = time.time()
                uptime = 0.0

        return {
            "running": running,
            "container_id": container_id,
            "uptime_seconds": uptime,
            "restart_count": self._restart_count,
            "last_crash": self._last_crash,
            "compose_file": self._compose_file,
            "service": self._service,
            "docker_available": docker_ok,
        }

    async def tail_log(self, lines: int = 50) -> list[str]:
        """Get the last N lines of server logs."""
        if self._process_mode:
            # In process mode, read from the game server's console.log
            try:
                # Find the most recent log directory
                proc = await asyncio.create_subprocess_exec(
                    "find", "/home/reforger/profile/logs", "-name", "console.log",
                    "-printf", "%T@ %p\n",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, _ = await proc.communicate()
                if proc.returncode == 0 and stdout:
                    # Get the most recent console.log by timestamp
                    log_files = stdout.decode(errors="replace").strip().splitlines()
                    if log_files:
                        latest = sorted(log_files, reverse=True)[0].split(" ", 1)[1]
                        tail_proc = await asyncio.create_subprocess_exec(
                            "tail", "-n", str(lines), latest,
                            stdout=asyncio.subprocess.PIPE,
                            stderr=asyncio.subprocess.PIPE,
                        )
                        tail_out, _ = await tail_proc.communicate()
                        if tail_proc.returncode == 0 and tail_out:
                            return tail_out.decode(errors="replace").splitlines()
            except (FileNotFoundError, OSError):
                pass
            return ["[no game server logs available yet]"]

        rc, out, err = await self._run_docker(
            "logs", f"--tail={lines}", self._service
        )
        if rc != 0:
            if not await self._ensure_docker_check():
                return ["[docker-compose not available]"]
            return [f"[error fetching logs: {err.strip() or 'unknown error'}]"]

        # docker-compose logs may mix stdout and stderr; combine and return
        combined = out + err
        return [line for line in combined.splitlines() if line]

    # ───────────────────────────────────────────────────────
    # Background monitoring
    # ───────────────────────────────────────────────────────

    async def start_monitor(self):
        """Start background monitoring task.

        The monitor checks the container every 15 seconds and logs warnings
        if the server unexpectedly stops.
        """
        if self._monitor_task is not None and not self._monitor_task.done():
            log.warning("Monitor is already running")
            return

        self._monitor_task = asyncio.create_task(
            self._monitor_loop(), name="arma-server-monitor"
        )
        log.info("Server monitor started (interval=15s)")

    async def stop_monitor(self):
        """Stop background monitoring."""
        if self._monitor_task is None or self._monitor_task.done():
            log.debug("Monitor is not running; nothing to stop")
            return

        self._monitor_task.cancel()
        try:
            await self._monitor_task
        except asyncio.CancelledError:
            pass
        self._monitor_task = None
        log.info("Server monitor stopped")

    async def _monitor_loop(self):
        """Background loop: check container health every 15 seconds.

        If the container was previously running and is now stopped,
        records the crash timestamp and logs a warning.
        """
        log.debug("Monitor loop started")
        try:
            while True:
                await asyncio.sleep(15)
                try:
                    running = await self.is_running()

                    if self._was_running and not running:
                        crash_ts = datetime.now(timezone.utc).isoformat()
                        self._last_crash = crash_ts
                        log.warning(
                            "Arma server is DOWN (was previously running). "
                            "Crash recorded at %s",
                            crash_ts,
                        )

                    self._was_running = running
                except Exception as exc:
                    log.error("Monitor check failed: %s", exc)
        except asyncio.CancelledError:
            log.debug("Monitor loop cancelled")
            raise

    # ───────────────────────────────────────────────────────
    # Dunder helpers
    # ───────────────────────────────────────────────────────

    async def get_log(self, lines: int = 50) -> list[str]:
        """Alias for tail_log() — used by server_integration endpoints."""
        return await self.tail_log(lines=lines)

    async def get_config(self) -> dict:
        """Read the server configuration JSON file."""
        # In process mode, the config is at the standard Arma server path
        candidates = [
            "/home/reforger/configs/config.json",
        ]
        if not self._process_mode:
            candidates.insert(0, os.path.join(
                os.path.dirname(self._compose_file), "..", "Configs", "GodMode", "gm_config.json"
            ))
            project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            candidates.append(os.path.join(project_root, "Configs", "GodMode", "gm_config.json"))

        config_path = None
        for candidate in candidates:
            if os.path.isfile(candidate):
                config_path = candidate
                break

        if config_path is None:
            return {"error": "config file not found", "searched": candidates[-1]}

        try:
            with open(config_path, "r") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            return {"error": f"failed to read config: {e}"}

    def __repr__(self) -> str:
        return (
            f"ArmaServerManager(service={self._service!r}, "
            f"compose_file={self._compose_file!r})"
        )


# ═══════════════════════════════════════════════════════════
# FACTORY
# ═══════════════════════════════════════════════════════════

def create_server_manager() -> ArmaServerManager:
    """Create a server manager from environment variables.

    Reads PROJECT_ROOT from the environment (defaults to the parent of the
    middleware directory) and returns a configured ArmaServerManager.
    """
    project_root = os.getenv(
        "PROJECT_ROOT",
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    )
    return ArmaServerManager(project_root=project_root)
