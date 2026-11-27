"""Deny-by-default authorization for the middleware's two trust domains."""

import hashlib
import hmac
import logging
import math
import re
import time
from collections import deque

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

log = logging.getLogger(__name__)
API_KEY_PATTERN = re.compile(rb"[A-Za-z0-9_-]{32,128}")


def _route_path(request: Request) -> str:
    """Match the path the router sees, including under an ASGI mount."""
    path = request.scope["path"]
    root = request.scope.get("root_path", "")
    if root and (path == root or path.startswith(root + "/")):
        return path[len(root):]
    return path


def _key_digest(key: str, setting: str) -> bytes | None:
    raw = key.encode("ascii", errors="replace")
    if not API_KEY_PATTERN.fullmatch(raw):
        log.warning("%s is missing or invalid; its routes are locked", setting)
        return None
    return hashlib.sha256(raw).digest()


class APIKeyMiddleware(BaseHTTPMiddleware):
    """Authenticate the exact method/path pair using a single header."""

    PUBLIC_ROUTES = {
        ("GET", "/health"), ("HEAD", "/health"), ("GET", "/dashboard"),
    }
    GAME_ROUTES = {
        ("POST", "/api/game-master"), ("GET", "/api/pending-commands"),
    }

    def __init__(self, app, api_key: str = "", game_api_key: str = ""):
        super().__init__(app)
        self._admin_key = _key_digest(api_key, "GODMODE_API_KEY")
        self._game_key = _key_digest(game_api_key, "GODMODE_GAME_API_KEY")
        if (self._admin_key is not None and self._game_key is not None
                and hmac.compare_digest(self._admin_key, self._game_key)):
            log.error("The admin and game API keys must be distinct; protected routes are locked")
            self._admin_key = self._game_key = None

    async def dispatch(self, request: Request, call_next):
        route = (request.method, _route_path(request))
        if route in self.PUBLIC_ROUTES:
            return await call_next(request)

        expected = self._game_key if route in self.GAME_ROUTES else self._admin_key
        values = request.headers.getlist("x-api-key")
        if expected is not None and len(values) == 1:
            raw = values[0].encode("latin-1")
            if API_KEY_PATTERN.fullmatch(raw) and hmac.compare_digest(
                hashlib.sha256(raw).digest(), expected
            ):
                return await call_next(request)
        return JSONResponse(
            {"detail": "Invalid or missing API key"},
            status_code=401,
            headers={"WWW-Authenticate": 'APIKey realm="GodMode"'},
        )


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Bound authenticated game traffic and the two RCON aliases separately."""

    RATE_LIMITED_PATHS = {"/api/rcon", "/api/server/rcon"}

    def __init__(self, app, max_per_second: int = 5):
        super().__init__(app)
        self._buckets = {
            "rcon": (deque(), max_per_second, 1.0),
            "report": (deque(), 4, 10.0),
            "poll": (deque(), 10, 1.0),
        }

    async def dispatch(self, request: Request, call_next):
        path = _route_path(request)
        method = request.method
        if method == "POST" and path in self.RATE_LIMITED_PATHS:
            bucket = "rcon"
        elif (method, path) == ("POST", "/api/game-master"):
            bucket = "report"
        elif (method, path) == ("GET", "/api/pending-commands"):
            bucket = "poll"
        else:
            return await call_next(request)

        requests, limit, window = self._buckets[bucket]
        now = time.monotonic()
        while requests and now - requests[0] >= window:
            requests.popleft()
        if len(requests) >= limit:
            return JSONResponse(
                {"detail": "Request rate limit exceeded"},
                status_code=429,
                headers={"Retry-After": str(max(1, math.ceil(window - (now - requests[0]))))},
            )
        requests.append(now)
        return await call_next(request)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Protect shell, API responses and auth failures from caching and framing."""

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response
