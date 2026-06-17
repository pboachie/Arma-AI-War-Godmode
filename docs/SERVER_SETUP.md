# Server Setup Guide

This repository **does not ship the Arma Reforger dedicated server** (binaries,
SteamCMD, BattlEye, and game data are ~21 GB and owned by Bohemia Interactive).
You bring your own server install; this repo provides the mod, the AI
middleware, and the config tooling that drive it.

> The folders `Arma Reforger Server/`, `arma_reforger/`, `steamcmd/`,
> `battleye/`, and `saves/` are intentionally `.gitignore`d. Never commit them —
> they contain large binaries and your RCON/admin passwords.

---

## 1. Install the dedicated server

Install the Arma Reforger Dedicated Server via SteamCMD (app id `1874900`):

```bash
steamcmd +force_install_dir ./ArmaReforgerServer \
         +login anonymous \
         +app_update 1874900 validate \
         +quit
```

See Bohemia's official guide:
<https://community.bistudio.com/wiki/Arma_Reforger:Server_Hosting>

## 2. Generate your server config

The server reads a `config.json`. Generate one from the sanitized template
so your passwords never live in source control:

```bash
# Set your secrets (do NOT commit these)
export ARMA_ADMIN_PASSWORD="choose-a-strong-admin-password"
export ARMA_RCON_PASSWORD="choose-a-strong-rcon-password"
export ARMA_SERVER_NAME="AI War [100% LLM-Managed]"
export ARMA_SCENARIO_ID="ECC61978EDCC2B5A"   # your mod's scenario GUID

python scripts/generate_config.py
```

- Template: [Configs/ArmaServer/config.template.json](../Configs/ArmaServer/config.template.json)
- Output (gitignored): `Arma Reforger Server/configs/config.json`

Values are pulled from the environment (or `middleware/.env`). Available knobs:

| Env var | Default | Purpose |
|---------|---------|---------|
| `ARMA_SERVER_NAME` | `AI War [100% LLM-Managed]` | Public server name |
| `ARMA_ADMIN_PASSWORD` | `changeme` | In-game admin password |
| `ARMA_RCON_PASSWORD` | `changeme_rcon` | RCON password (used by the middleware) |
| `ARMA_RCON_BIND_ADDRESS` | `0.0.0.0` | RCON bind address |
| `ARMA_RCON_PORT` | `19999` | RCON port |
| `ARMA_GAME_PORT` | `2001` | Game port |
| `ARMA_A2S_PORT` | `17777` | Steam A2S query port |
| `ARMA_MAX_PLAYERS` | `124` | Player cap |
| `ARMA_SCENARIO_ID` | `ECC61978EDCC2B5A` | Scenario GUID |

> **Security:** pick unique, strong passwords. The RCON password also goes in
> `middleware/.env` so the AI can issue server commands — keep both private.

## 3. Install the mod

Follow the **Install the mod in Arma Reforger Workbench** section of the
[main README](../README.md#2-install-the-mod-in-arma-reforger-workbench), then
add your mod's id to the `"mods"` array in the generated `config.json`.

## 4. Start everything

```bash
# 1) Middleware (AI brain)
cd middleware && cp .env.example .env   # fill in your LLM key + RCON password
pip install -r requirements.txt
bash start.sh

# 2) Dedicated server
./ArmaReforgerServer/ArmaReforgerServer \
    -config "Arma Reforger Server/configs/config.json" \
    -maxFPS 60 -logStats 5000
```

Verify the middleware: `curl http://localhost:8080/health`

## Docker

Container-based deployment lives in [`docker/`](../docker/) and
[`middleware/docker-compose.yml`](../middleware/docker-compose.yml). The same
`ARMA_*` env vars apply — set them in your `.env` before `docker compose up -d`.
