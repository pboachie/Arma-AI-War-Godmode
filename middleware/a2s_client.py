"""
a2s_client.py — Async A2S Query Client for Arma Reforger Server

Wraps the python-a2s library with:
  - Async interface using a2s.ainfo(), a2s.aplayers(), a2s.arules()
  - Result caching with configurable TTL to avoid hammering the game server
  - Graceful error handling (server offline is expected, not exceptional)

Environment variables:
    ARMA_A2S_CONNECT_ADDRESS — Server address override for the middleware client
    ARMA_A2S_ADDRESS         — Server address fallback (default: 127.0.0.1)
  ARMA_A2S_PORT      — A2S query port  (default: 17777)
  ARMA_A2S_CACHE_TTL — Cache TTL in seconds (default: 5.0)

Usage:
  client = create_a2s_client()
  status = await client.poll()
"""

import asyncio
import os
import socket
import time
import logging
from typing import Optional

import a2s

log = logging.getLogger(__name__)


class A2SQueryClient:
    """Async A2S query client with caching for Arma Reforger server status."""

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 17777,
        cache_ttl: float = 5.0,
    ):
        self._address = (host, port)
        self._cache_ttl = cache_ttl

        # Info cache
        self._info_cache: Optional[dict] = None
        self._info_cache_time: float = 0.0

        # Players cache
        self._players_cache: Optional[list[dict]] = None
        self._players_cache_time: float = 0.0

    # ── helpers ──────────────────────────────────────────────

    def _cache_fresh(self, cache_time: float) -> bool:
        """Return True if the cache timestamp is within TTL."""
        return (time.time() - cache_time) < self._cache_ttl

    # ── public API ──────────────────────────────────────────

    async def get_info(self) -> dict:
        """Get server info (name, map, players, max_players, etc).

        Returns cached result if still fresh.  On failure returns a dict
        with ``online: False``.
        """
        if self._info_cache is not None and self._cache_fresh(self._info_cache_time):
            return self._info_cache

        try:
            info = await a2s.ainfo(self._address, timeout=2.0)

            now = time.time()
            result = {
                "online": True,
                "server_name": info.server_name,
                "map_name": info.map_name,
                "game": info.game,
                "player_count": info.player_count,
                "max_players": info.max_players,
                "bot_count": info.bot_count,
                "ping": info.ping,
                "timestamp": now,
            }

            self._info_cache = result
            self._info_cache_time = now
            return result

        except (socket.timeout, OSError, asyncio.TimeoutError) as exc:
            log.debug("A2S info query failed (%s:%d): %s", *self._address, exc)
            return {"online": False, "timestamp": time.time()}

    async def get_players(self) -> list[dict]:
        """Get player list (name, score, duration).

        Returns cached result if still fresh.  On failure returns an
        empty list.
        """
        if self._players_cache is not None and self._cache_fresh(self._players_cache_time):
            return self._players_cache

        try:
            players = await a2s.aplayers(self._address, timeout=2.0)

            now = time.time()
            result = [
                {
                    "name": p.name,
                    "score": p.score,
                    "duration": p.duration,
                }
                for p in players
            ]

            self._players_cache = result
            self._players_cache_time = now
            return result

        except (socket.timeout, OSError, asyncio.TimeoutError) as exc:
            log.debug("A2S players query failed (%s:%d): %s", *self._address, exc)
            return []

    async def get_rules(self) -> dict:
        """Get server rules / cvars.

        Returns an empty dict on failure.
        """
        try:
            rules = await a2s.arules(self._address, timeout=2.0)
            return dict(rules) if rules else {}
        except (socket.timeout, OSError, asyncio.TimeoutError) as exc:
            log.debug("A2S rules query failed (%s:%d): %s", *self._address, exc)
            return {}

    async def is_online(self) -> bool:
        """Quick check whether the server responds to an A2S query.

        Uses a short 2-second timeout so callers are not blocked for long.
        """
        info = await self.get_info()
        return info.get("online", False)

    async def poll(self) -> dict:
        """Combined status snapshot — server info + player list.

        Fires both queries concurrently for speed.
        """
        info, players = await asyncio.gather(
            self.get_info(),
            self.get_players(),
        )

        return {
            "online": info.get("online", False),
            "info": info,
            "players": players,
            "timestamp": time.time(),
        }


# ── factory ─────────────────────────────────────────────────


def create_a2s_client() -> A2SQueryClient:
    """Create an A2S client from environment variables.

    Recognised env vars:
            ARMA_A2S_CONNECT_ADDRESS — preferred host override
            ARMA_A2S_ADDRESS         — fallback host, default ``127.0.0.1``
            ARMA_A2S_PORT            — default ``17777``
            ARMA_A2S_CACHE_TTL       — default ``5.0`` (seconds)
    """
    return A2SQueryClient(
                host=os.getenv(
                        "ARMA_A2S_CONNECT_ADDRESS",
                        os.getenv("ARMA_A2S_ADDRESS", "127.0.0.1"),
                ),
        port=int(os.getenv("ARMA_A2S_PORT", "17777")),
        cache_ttl=float(os.getenv("ARMA_A2S_CACHE_TTL", "5.0")),
    )
