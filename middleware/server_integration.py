"""
Server integration endpoints for Arma Reforger game server management.
Provides /api/server/* routes for monitoring, RCON, and lifecycle control.
"""
import asyncio
import json
import logging
import os
import re
import time
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Query, Request
from starlette.responses import StreamingResponse

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/server", tags=["server"])

# ─── Globals (initialized in init_services()) ───

_server_manager = None  # ArmaServerManager | None
_rcon_client = None     # BERconClient | None
_a2s_client = None      # A2SQueryClient | None


# ─── Helpers ───

def _require_service(service, name: str):
    """Raise 503 if a service is not configured."""
    if service is None:
        raise HTTPException(
            status_code=503,
            detail={"error": "not configured", "service": name},
        )


_LIFECYCLE_PAST_TENSE = {"start": "started", "stop": "stopped", "restart": "restarted"}


async def _lifecycle_action(action_name: str):
    """Run a server_manager lifecycle action (start/stop/restart)."""
    _require_service(_server_manager, "server_manager")
    try:
        succeeded = await getattr(_server_manager, action_name)()
    except Exception as e:
        log.error("Failed to %s server: %s", action_name, e)
        succeeded = False
    if not succeeded:
        raise HTTPException(status_code=503, detail=f"Could not {action_name} server")
    return {"status": _LIFECYCLE_PAST_TENSE[action_name]}


# ═══════════════════════════════════════════════════════════
# Endpoints
# ═══════════════════════════════════════════════════════════

@router.get("/status")
async def server_status():
    """Combined status from process manager, A2S query, and RCON."""
    result = {"timestamp": time.time()}

    if _server_manager is not None:
        try:
            result["process"] = await _server_manager.get_status()
        except Exception as e:
            log.warning("Failed to get server process status: %s", e)
            result["process"] = {"error": str(e)}
    else:
        result["process"] = None

    if _a2s_client is not None:
        try:
            info, players = await asyncio.gather(
                _a2s_client.get_info(),
                _a2s_client.get_players(),
            )
            result["a2s"] = {
                "online": True,
                "player_count": info.get("player_count", 0),
                "server_name": info.get("server_name", ""),
                "max_players": info.get("max_players", 0),
                "players": players,
                **{k: v for k, v in info.items()
                   if k not in ("player_count", "server_name", "max_players")},
            }
        except Exception as e:
            log.debug("A2S query failed: %s", e)
            result["a2s"] = {"online": False, "error": str(e)}
    else:
        result["a2s"] = None

    if _rcon_client is not None:
        result["rcon"] = {"connected": _rcon_client.is_connected}
    else:
        result["rcon"] = None

    return result


@router.post("/start")
async def server_start():
    """Start the Arma Reforger game server."""
    return await _lifecycle_action("start")


@router.post("/stop")
async def server_stop():
    """Stop the Arma Reforger game server."""
    return await _lifecycle_action("stop")


@router.post("/restart")
async def server_restart():
    """Restart the Arma Reforger game server."""
    return await _lifecycle_action("restart")


@router.get("/players")
async def server_players():
    """Player list from A2S query."""
    _require_service(_a2s_client, "a2s_client")
    try:
        info, players = await asyncio.gather(
            _a2s_client.get_info(),
            _a2s_client.get_players(),
        )
        return {
            "players": players,
            "count": info.get("player_count", len(players)),
            "max": info.get("max_players", 0),
        }
    except Exception as e:
        log.error("Player query failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/log")
async def server_log(lines: int = Query(default=50, ge=1, le=1000)):
    """Tail the server log file."""
    _require_service(_server_manager, "server_manager")
    try:
        log_lines = await _server_manager.get_log(lines=lines)
        return {"lines": log_lines, "count": len(log_lines)}
    except Exception as e:
        log.error("Failed to read server log: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


def _parse_log_level(line: str) -> str:
    """Extract log level from a log line by scanning for keywords."""
    upper = line.upper()
    if "ERROR" in upper or "FATAL" in upper or "EXCEPTION" in upper:
        return "error"
    if "WARN" in upper or "WARNING" in upper:
        return "warn"
    return "info"


@router.get("/log/stream")
async def server_log_stream(request: Request):
    """Stream server logs in real-time via Server-Sent Events."""
    _require_service(_server_manager, "server_manager")

    async def _event_generator():
        sent_count = 0
        last_ping = time.monotonic()
        poll_interval = 2  # seconds
        ping_interval = 10  # seconds

        while True:
            # Check for client disconnect
            if await request.is_disconnected():
                log.debug("SSE log stream client disconnected")
                break

            try:
                all_lines = await _server_manager.tail_log(lines=500)
            except Exception as e:
                log.error("Failed to tail log for SSE stream: %s", e)
                error_payload = json.dumps({
                    "line": f"[stream error] {e}",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "level": "error",
                })
                yield f"data: {error_payload}\n\n"
                await asyncio.sleep(poll_interval)
                continue

            total = len(all_lines)

            if sent_count == 0:
                # On first poll, only send the last 20 lines to avoid a flood
                start = max(0, total - 20)
            else:
                start = sent_count

            if total > start:
                for line in all_lines[start:total]:
                    payload = json.dumps({
                        "line": line,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "level": _parse_log_level(line),
                    })
                    yield f"data: {payload}\n\n"
                sent_count = total
            elif total < sent_count:
                # Log was truncated/rotated; reset counter
                sent_count = 0

            # Send keepalive ping every ping_interval seconds
            now = time.monotonic()
            if now - last_ping >= ping_interval:
                yield "event: ping\ndata: {}\n\n"
                last_ping = now

            await asyncio.sleep(poll_interval)

    return StreamingResponse(
        _event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/config")
async def server_config():
    """Expose selected operational settings, never the credential-bearing file."""
    _require_service(_server_manager, "server_manager")
    try:
        config = await _server_manager.get_config()
        if not isinstance(config, dict) or "error" in config:
            raise ValueError("Unavailable server configuration")
        result = {key: config[key] for key in ("bindAddress", "bindPort", "publicAddress", "publicPort")
                  if type(config.get(key)) in (str, int)}
        game = config.get("game")
        if isinstance(game, dict):
            result["game"] = {
                key: game[key] for key in ("name", "scenarioId", "maxPlayers", "visible", "crossPlatform")
                if type(game.get(key)) in (str, int, bool)
            }
        return result
    except Exception as e:
        log.error("Failed to read server config (%s)", type(e).__name__)
        raise HTTPException(status_code=500, detail="Unable to read server configuration") from None


def _parse_docker_size(value: str) -> float | None:
    """Parse a Docker human-readable size string (e.g. '1.5GiB', '320MiB', '4.2kB') to MB."""
    if not value:
        return None
    match = re.match(r"([\d.]+)\s*(\w+)", value.strip())
    if not match:
        return None
    num = float(match.group(1))
    unit = match.group(2).lower()
    multipliers = {
        "b": 1 / (1024 * 1024),
        "kb": 1 / 1024, "kib": 1 / 1024,
        "mb": 1, "mib": 1,
        "gb": 1024, "gib": 1024,
        "tb": 1024 * 1024, "tib": 1024 * 1024,
    }
    return round(num * multipliers.get(unit, 1), 2)


def _parse_docker_stats(raw: str) -> dict:
    """Parse JSON output from docker stats --no-stream --format json."""
    data = json.loads(raw)
    result: dict = {
        "cpu_percent": None,
        "memory_used_mb": None,
        "memory_limit_mb": None,
        "memory_percent": None,
        "network_rx_mb": None,
        "network_tx_mb": None,
        "disk_read_mb": None,
        "disk_write_mb": None,
        "pids": None,
        "source": "docker",
    }

    # CPU — e.g. "23.50%"
    cpu_str = data.get("CPUPerc", "").replace("%", "").strip()
    if cpu_str:
        try:
            result["cpu_percent"] = round(float(cpu_str), 2)
        except ValueError:
            pass

    # Memory — e.g. "512MiB / 2GiB"
    mem_usage = data.get("MemUsage", "")
    if "/" in mem_usage:
        used_str, limit_str = mem_usage.split("/", 1)
        result["memory_used_mb"] = _parse_docker_size(used_str)
        result["memory_limit_mb"] = _parse_docker_size(limit_str)

    mem_pct = data.get("MemPerc", "").replace("%", "").strip()
    if mem_pct:
        try:
            result["memory_percent"] = round(float(mem_pct), 2)
        except ValueError:
            pass

    # Network — e.g. "10.5MB / 3.2MB"
    net_io = data.get("NetIO", "")
    if "/" in net_io:
        rx_str, tx_str = net_io.split("/", 1)
        result["network_rx_mb"] = _parse_docker_size(rx_str)
        result["network_tx_mb"] = _parse_docker_size(tx_str)

    # Block I/O — e.g. "100MB / 50MB"
    block_io = data.get("BlockIO", "")
    if "/" in block_io:
        read_str, write_str = block_io.split("/", 1)
        result["disk_read_mb"] = _parse_docker_size(read_str)
        result["disk_write_mb"] = _parse_docker_size(write_str)

    # PIDs
    pids_str = data.get("PIDs", "").strip()
    if pids_str:
        try:
            result["pids"] = int(pids_str)
        except ValueError:
            pass

    return result


def _host_metrics() -> dict:
    """Fallback: gather basic metrics from the host OS."""
    result: dict = {
        "cpu_percent": None,
        "memory_used_mb": None,
        "memory_limit_mb": None,
        "memory_percent": None,
        "network_rx_mb": None,
        "network_tx_mb": None,
        "disk_read_mb": None,
        "disk_write_mb": None,
        "pids": None,
        "source": "host",
    }
    # CPU usage — os.getloadavg() is Linux/macOS only
    import platform
    if platform.system() != "Windows":
        try:
            load1, load5, load15 = os.getloadavg()
            cpu_count = os.cpu_count() or 1
            result["cpu_percent"] = round((load1 / cpu_count) * 100, 2)
        except OSError:
            pass

    # Memory stats — /proc/meminfo on Linux, ctypes on Windows
    if platform.system() == "Windows":
        try:
            import ctypes
            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]
            stat = MEMORYSTATUSEX()
            stat.dwLength = ctypes.sizeof(stat)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat))
            total_mb = stat.ullTotalPhys / (1024 * 1024)
            avail_mb = stat.ullAvailPhys / (1024 * 1024)
            used_mb = total_mb - avail_mb
            result["memory_limit_mb"] = round(total_mb, 2)
            result["memory_used_mb"] = round(used_mb, 2)
            result["memory_percent"] = round((used_mb / total_mb) * 100, 2) if total_mb > 0 else None
        except (OSError, ValueError):
            pass
    else:
        try:
            with open("/proc/meminfo") as f:
                meminfo = {}
                for line in f:
                    parts = line.split(":")
                    if len(parts) == 2:
                        key = parts[0].strip()
                        val = parts[1].strip().split()[0]  # value in kB
                        meminfo[key] = int(val)
                total_kb = meminfo.get("MemTotal", 0)
                avail_kb = meminfo.get("MemAvailable", meminfo.get("MemFree", 0))
                used_kb = total_kb - avail_kb
                result["memory_limit_mb"] = round(total_kb / 1024, 2)
                result["memory_used_mb"] = round(used_kb / 1024, 2)
                if total_kb > 0:
                    result["memory_percent"] = round((used_kb / total_kb) * 100, 2)
        except (OSError, ValueError, KeyError):
            pass

    return result


@router.get("/metrics")
async def server_metrics():
    """Return system/container metrics (Docker stats or host fallback)."""
    # Try Docker stats if server_manager is available
    if _server_manager is not None:
        try:
            container_id = await _server_manager.get_container_id()
            if container_id:
                proc = await asyncio.create_subprocess_exec(
                    "docker", "stats", "--no-stream", "--format", "json", container_id,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, stderr = await proc.communicate()
                if proc.returncode == 0 and stdout:
                    return _parse_docker_stats(stdout.decode(errors="replace"))
                log.warning(
                    "docker stats failed (rc=%s): %s",
                    proc.returncode,
                    stderr.decode(errors="replace").strip(),
                )
        except Exception as e:
            log.warning("Docker metrics unavailable, falling back to host: %s", e)

    # Fallback to host metrics
    return _host_metrics()


# ═══════════════════════════════════════════════════════════
# Lifecycle
# ═══════════════════════════════════════════════════════════

async def init_services(*, server_control_enabled: bool = False, rcon_client=None):
    """Initialize server management services. Called on middleware startup."""
    global _server_manager, _rcon_client, _a2s_client

    # Standalone middleware must not probe Docker or control a server process.
    if server_control_enabled:
        try:
            from server_manager import create_server_manager
            manager = create_server_manager()
            if not manager._process_mode:
                await manager._ensure_docker_check()
            await manager.start_monitor()
            _server_manager = manager
            log.info("Server manager initialized")
        except ImportError:
            log.warning("server_manager not available")
        except Exception as e:
            log.warning("server_manager init failed: %s", e)
    else:
        log.info("Server process control disabled")

    # server.py owns the single explicitly opted-in RCON connection.
    _rcon_client = rcon_client
    if _server_manager is not None and _rcon_client is not None:
        _server_manager._rcon_connected_fn = lambda: _rcon_client.is_connected

    # A2S query client
    try:
        from a2s_client import create_a2s_client
        _a2s_client = create_a2s_client()
        log.info("A2S query client initialized")
    except ImportError:
        log.warning("a2s_client not available")
    except Exception as e:
        log.warning("A2S client init failed: %s", e)


async def shutdown_services():
    """Cleanup on middleware shutdown."""
    global _server_manager, _rcon_client, _a2s_client
    if _server_manager is not None:
        try:
            await _server_manager.stop_monitor()
            log.info("Server manager stopped")
        except Exception as e:
            log.warning("Server manager stop error: %s", e)
    _server_manager = _rcon_client = _a2s_client = None
