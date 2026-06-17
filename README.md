# Arma Reforger — AI War: 100% LLM-Managed Gamemode

A fully autonomous AI Game Master — no human admins, no scripted events, no pre-programmed scenarios. An LLM commands the entire battlefield in real-time: tactics, weather, reinforcements, narrative, and difficulty — all decisions made by AI, every second of every match.

> **Zero human intervention.** The LLM reads the battlefield, thinks strategically, and issues orders. Players fight a war that adapts, learns, and evolves — driven entirely by artificial intelligence.

## Architecture

```
┌──────────────────────────────────────────────────────┐
│               LLM (Claude / GPT-4o)                  │
│      Strategic + Narrative decisions (every 30s)     │
│      Input: SITREP JSON    Output: Command JSON      │
└─────────────────────┬────────────────────────────────┘
                      │ HTTP POST
                      ▼
┌──────────────────────────────────────────────────────┐
│           FastAPI Middleware (localhost:8000)          │
│   Prompt construction · LLM call · Validation        │
│   Game state history · Cost tracking · Retry logic   │
└─────────────────────┬────────────────────────────────┘
                      │ HTTP Response (JSON)
                      ▼
┌──────────────────────────────────────────────────────┐
│   GM_GodModeComponent (SCR_BaseGameModeComponent)    │
│                                                      │
│   Weather · Vehicles · AI Squads · Notifications     │
│   Sound · Particles · Cleanup · Trigger Zones        │
└──────────────────────────────────────────────────────┘
```

## Quick Start

### 1. Set up the middleware server

```bash
cd middleware/
cp .env.example .env
# Edit .env → pick your provider and set the API key:
#   Anthropic:  LLM_PROVIDER=anthropic  ANTHROPIC_API_KEY=sk-ant-...
#   OpenAI:     LLM_PROVIDER=openai     OPENAI_API_KEY=sk-...
#   GLM Coding: LLM_PROVIDER=glm        GLM_API_KEY=...

pip install -r requirements.txt
bash start.sh
```

Or with Docker:

```bash
cd middleware/
cp .env.example .env
# Edit .env → set provider and API key

docker compose up -d
```

Verify: `curl http://localhost:8000/health`

> **Need a dedicated server?** This repo does not ship the Arma Reforger server
> binaries or game data. See [docs/SERVER_SETUP.md](docs/SERVER_SETUP.md) for
> installing the server and generating a `config.json` from the template.

### 2. Install the mod in Arma Reforger Workbench

1. Create a new mod project in Workbench
2. Copy `scripts/Game/GameMode/` into your mod's `scripts/Game/GameMode/`
3. Copy `Configs/GodMode/` into your mod's `Configs/`
4. Register scripts in `.gproj` → Script Project Manager Settings → Modules
5. Open your GameMode entity (e.g. `GameModeSF`)
6. Add component → `GM_GodModeComponentClass`
7. Configure:
   - **Server URL**: `http://localhost:8000` (or your server IP)
   - **Update Interval**: `30` seconds
   - **LLM Enabled**: `true`
   - **Max Squads**: `30`
   - **Max Vehicles**: `20`

### 3. Run

1. Start the middleware server
2. Launch your Arma Reforger dedicated server with the mod loaded
3. The system initializes automatically — check server logs for `[GodMode]` messages

## Project Structure

```
arma-godmode/
├── scripts/Game/GameMode/
│   ├── GM_GodModeComponent.c      ← Central orchestrator
│   ├── GM_RestClient.c            ← REST API wrapper with retry/circuit breaker
│   ├── GM_CommandSchema.c         ← JSON serialization structs
│   ├── GM_WeatherController.c     ← Weather, time, wind, moon phase
│   ├── GM_VehicleManager.c        ← Vehicle spawn/crew/track/cleanup
│   ├── GM_SquadManager.c          ← AI group spawn/command/lifecycle
│   ├── GM_CleanupSystem.c         ← Entity garbage collection
│   ├── GM_SoundController.c       ← Environmental audio
│   ├── GM_ParticleController.c    ← Visual effects (explosions, smoke, flares)
│   ├── GM_NarrativeManager.c      ← Notifications, radio, objectives
│   └── GM_TriggerManager.c        ← Spatial trigger zones
├── Configs/
│   ├── GodMode/
│   │   └── gm_config.json         ← Runtime configuration reference
│   └── ArmaServer/
│       └── config.template.json   ← Sanitized dedicated-server config template
├── docs/
│   └── SERVER_SETUP.md            ← Dedicated-server install & config guide
├── middleware/
│   ├── server.py                  ← FastAPI middleware server
│   ├── requirements.txt           ← Python dependencies
│   ├── start.sh                   ← Quick-start script
│   ├── .env.example               ← Environment template
│   ├── Dockerfile                 ← Container build
│   └── docker-compose.yml         ← One-command deploy
├── LICENSE
└── README.md
```

> The `Arma Reforger Server/` install (binaries, SteamCMD, BattlEye, game data)
> is **not** part of this repository — it is `.gitignore`d. Bring your own; see
> [docs/SERVER_SETUP.md](docs/SERVER_SETUP.md).

## How It Works

### Game Loop

Every N seconds (default 30):

1. **Collect SITREP** — unit positions, health, player states, weather, events
2. **Serialize to JSON** via `JsonApiStruct`
3. **POST to middleware** (async, non-blocking)
4. **Middleware calls LLM** with SITREP + history + system prompt
5. **LLM returns commands** — tactical orders, spawns, weather, narrative
6. **Commands dispatched** to sub-managers (squad, vehicle, weather, etc.)
7. **Narrative broadcast** to players via HUD hints

### Available Commands (20 types)

| Category | Commands |
|----------|----------|
| Movement | `move`, `attack`, `defend`, `patrol`, `set_ambush`, `seek_and_destroy`, `retreat` |
| Spawning | `spawn_infantry`, `spawn_vehicle`, `spawn_reinforcements` |
| Environment | `set_weather`, `set_time`, `set_wind`, `thunder_near`, `play_sound` |
| Effects | `explosion`, `smoke`, `flare` |
| Cleanup | `cleanup_area`, `cleanup_dead`, `despawn_unit` |
| Narrative | `radio_message`, `objective_create`, `objective_complete` |

### Dynamic Scaling

The system auto-adjusts based on player count:

| Players | Max Squads | Max Vehicles | Update Interval |
|---------|-----------|-------------|-----------------|
| 0       | 10        | 5           | 30s             |
| 1–4     | 15        | 8           | 30s             |
| 5–12    | 30        | 15          | 45s             |
| 13+     | 50        | 20          | 60s             |

### Fault Tolerance

- **Circuit breaker**: After 5 consecutive REST errors, backs off automatically
- **Graceful degradation**: If LLM is unreachable, AI continues on existing waypoints
- **Command validation**: Invalid commands are logged and skipped
- **Cleanup safety**: Entities near players are deferred, not popped out of existence

## Configuration

### Middleware Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `LLM_PROVIDER` | `anthropic` | `anthropic`, `openai`, or `glm` |
| `ANTHROPIC_API_KEY` | (required for anthropic) | Your Anthropic API key |
| `OPENAI_API_KEY` | (required for openai) | Your OpenAI API key |
| `GLM_API_KEY` | (required for glm) | Your Z.AI API key ([get one here](https://open.z.ai/)) |
| `GLM_BASE_URL` | `https://api.z.ai/api/coding/paas/v4/` | GLM endpoint — coding or general |
| `GLM_TEMPERATURE` | `0.8` | GLM temperature (0.0–1.0) |
| `LLM_MODEL` | auto per provider | Model name override (see below) |
| `LLM_MAX_TOKENS` | `2000` | Max response tokens |
| `MAX_HISTORY` | `20` | SITREP history depth |

**Default models per provider** (used when `LLM_MODEL` is not set):

| Provider | Default Model | Other Options |
|----------|--------------|---------------|
| `anthropic` | `claude-sonnet-4-20250514` | Any Claude model |
| `openai` | `gpt-4o` | `gpt-4-turbo`, etc. |
| `glm` | `glm-4.7` | `glm-5`, `glm-4.6` |

**GLM endpoint options:**

| Endpoint | URL | Best for |
|----------|-----|----------|
| Coding (default) | `https://api.z.ai/api/coding/paas/v4/` | Agent/coding tasks — optimized for structured JSON output |
| General | `https://api.z.ai/api/paas/v4/` | General-purpose use |

### Workbench Component Properties

| Property | Default | Description |
|----------|---------|-------------|
| Update Interval | 30 | Seconds between LLM calls |
| Server URL | http://localhost:8000 | Middleware address |
| LLM Enabled | true | Set false for offline testing |
| Max Squads | 30 | AI group spawn cap |
| Max Vehicles | 20 | Vehicle spawn cap |

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/game-master` | Receive SITREP, return commands |
| GET | `/health` | Health check with stats |
| GET | `/stats` | Detailed usage statistics |
| POST | `/api/reset` | Clear game history |

## Estimated LLM Cost

| Interval | Calls/hr | Claude Sonnet | GLM-4.7 (Coding) |
|----------|----------|---------------|-------------------|
| 30s      | 120      | ~$0.50/hr     | ~$0.15/hr         |
| 60s      | 60       | ~$0.30/hr     | ~$0.08/hr         |
| 120s     | 30       | ~$0.20/hr     | ~$0.04/hr         |

GLM's coding endpoint is significantly cheaper for this use case. Check [Z.AI pricing](https://open.z.ai/) for current rates.

## Important Notes

### Prefab GUIDs

The GUID-style resource paths (e.g., `{5D63BFCD0CF5171D}Prefabs/...`) in the vehicle and squad managers are representative. You must verify them in the Workbench Resource Browser for your game version:

1. Open Workbench → Resource Browser
2. Navigate to `Prefabs/Vehicles/` or `Prefabs/Groups/`
3. Right-click the prefab → Copy Resource Path
4. Update the `GetVehiclePrefab()` or `GetGroupPrefab()` switch cases

### Sound Event Names

Sound events like `SOUND_ENVIRONMENT_THUNDER` are defined in `.acp` files. The exact event names may vary by game version. Check the Audio Editor in Workbench.

### Particle Effect Prefabs

Explosion and smoke prefab paths are placeholders. Find actual paths in `Prefabs/Weapons/Effects/` and `Prefabs/Particles/` in the Resource Browser.

## Extending the System

### Add a new command type

1. Add the constant to `GM_CommandTypes` in `GM_CommandSchema.c`
2. Add a `case` in `ExecuteCommand()` in `GM_GodModeComponent.c`
3. Add the command to the LLM system prompt in `server.py`
4. Implement the handler in the appropriate manager

### Add a new vehicle/squad type

1. Find the prefab GUID in Workbench Resource Browser
2. Add a `case` in `GetVehiclePrefab()` or `GetGroupPrefab()`
3. Add the type name to the LLM system prompt

### Custom LLM behavior

The system prompt in `server.py` is where 80% of gameplay quality comes from. Tune:
- Tactical doctrine (aggression, caution, escalation curve)
- Map-specific knowledge (add known positions, terrain features)
- Narrative style (radio chatter tone, immersion level)
- Force composition preferences

## Security

- **Never commit secrets.** API keys live in `middleware/.env` (gitignored); use
  `middleware/.env.example` as the template.
- **Server passwords** (admin/RCON) are injected at config-generation time from
  environment variables — they are never stored in the tracked template.
- The generated `config.json` and all runtime data (`battle_history/`,
  `replays/`, logs) are gitignored.

## License

This project's original code is released under the [MIT License](LICENSE).
Arma Reforger and its assets are © Bohemia Interactive a.s. and are **not**
covered by this license — you must own the game and follow Bohemia's EULA and
server-hosting terms.
