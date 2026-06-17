"""
API key authentication middleware for God Mode.
Optional — only enforced when GODMODE_API_KEY is set in .env.
"""
import time
import logging
from collections import defaultdict
from fastapi import Request, HTTPException
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

log = logging.getLogger(__name__)


class APIKeyMiddleware(BaseHTTPMiddleware):
    """Validates X-API-Key header on protected endpoints."""

    EXEMPT_PATHS = {
        "/health",
        "/dashboard",
        "/api/game-master",      # Game mod doesn't send API keys
        "/api/pending-commands",  # Game mod polling
    }

    def __init__(self, app, api_key: str):
        super().__init__(app)
        self._api_key = api_key

    async def dispatch(self, request: Request, call_next):
        # Skip if no API key configured (auth disabled)
        if not self._api_key:
            return await call_next(request)

        # Skip exempt paths
        path = request.url.path.rstrip("/")
        if path in self.EXEMPT_PATHS:
            return await call_next(request)

        # Check X-API-Key header or api_key query param
        key = request.headers.get("X-API-Key") or request.query_params.get("api_key")
        if key != self._api_key:
            log.warning("Unauthorized request to %s from %s", path, request.client.host)
            raise HTTPException(status_code=401, detail="Invalid or missing API key")

        return await call_next(request)


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Simple rate limiter for sensitive endpoints like RCON."""

    RATE_LIMITED_PATHS = {"/api/server/rcon"}

    def __init__(self, app, max_per_second: int = 5):
        super().__init__(app)
        self._max = max_per_second
        self._requests = defaultdict(list)  # ip -> [timestamps]

    async def dispatch(self, request: Request, call_next):
        path = request.url.path.rstrip("/")
        if path not in self.RATE_LIMITED_PATHS:
            return await call_next(request)

        ip = request.client.host
        now = time.time()
        # Clean old entries
        self._requests[ip] = [t for t in self._requests[ip] if now - t < 1.0]

        if len(self._requests[ip]) >= self._max:
            return JSONResponse(
                status_code=429,
                content={"detail": "Rate limit exceeded. Max {} requests/second.".format(self._max)}
            )

        self._requests[ip].append(now)
        return await call_next(request)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Add security headers to all responses."""

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "SAMEORIGIN"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        return response
