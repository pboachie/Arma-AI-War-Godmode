FROM python:3.12-slim@sha256:191083cdf6c7bda8c76158983163ead5d5cf7b77cf3033e805bb0f5296f51b4b AS builder

WORKDIR /build
COPY requirements.txt .
RUN pip install --no-cache-dir --require-hashes --only-binary=:all: --prefix=/install -r requirements.txt

FROM python:3.12-slim@sha256:191083cdf6c7bda8c76158983163ead5d5cf7b77cf3033e805bb0f5296f51b4b

RUN groupadd --gid 10001 godmode \
    && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin godmode \
    && install -d -m 0700 -o godmode -g godmode /var/lib/godmode

COPY --from=builder /install /usr/local
WORKDIR /app
# Keep the non-root reader independent of the operator's checkout umask.
COPY --chmod=0444 server.py agents.py scaling_engine.py tactical_objectives.py auth.py dashboard.html \
     server_integration.py server_manager.py rcon_client.py a2s_client.py ./

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    GODMODE_STATE_DIR=/var/lib/godmode

USER 10001:10001
EXPOSE 8000/tcp

# Hypercorn also supports the cleartext HTTP/2 used by the game client.
CMD ["hypercorn", "server:app", "--bind", "0.0.0.0:8000", "--graceful-timeout", "30"]
