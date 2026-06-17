FROM python:3.12-slim AS builder

WORKDIR /build
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# ── Production stage ──
FROM python:3.12-slim

# Install curl for healthcheck and create non-root user
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd -r godmode && useradd -r -g godmode -s /sbin/nologin godmode

COPY --from=builder /install /usr/local

WORKDIR /app

COPY server.py .
COPY agents.py .
COPY dashboard.html .

# Copy optional files only if they exist
COPY scaling_engine.py* ./
COPY server_integration.py* ./
COPY server_manager.py* ./
COPY rcon_client.py* ./
COPY a2s_client.py* ./
COPY auth.py* ./
COPY test_server.py* ./
COPY test_server_integration.py* ./

# Ensure replay/log directories exist and are writable
RUN mkdir -p /app/replays /app/logs /app/battle_summaries \
    && chown -R godmode:godmode /app

USER godmode

EXPOSE 8000

# Hypercorn for production:
# - Supports h2c (HTTP/2 cleartext) which Enfusion's RestContext requires
# - Single worker (LLM calls are async, multiple workers waste memory)
# - Graceful shutdown timeout for in-flight LLM calls
CMD ["hypercorn", "server:app", \
     "--bind", "0.0.0.0:8000", \
     "--graceful-timeout", "30", \
     "--access-log", "-"]
