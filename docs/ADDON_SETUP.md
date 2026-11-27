# Connect AI War to middleware

Core 1.0.7 adds the server-profile URL override and game-key authentication. Version 1.0.6 predates both. Do not disable middleware authentication to use the older build. [AI War – Core](https://reforger.armaplatform.com/workshop/68EB7BF2F5940DD9)

## Pin the Workshop build

For stock Conflict, leave the default `ARMA_SCENARIO_ID` unchanged and save this as `"$arma_private/workshop-mods.json"` using the paths from the dedicated-server setup guide.

```json
[
  {"modId": "68EB7BF2F5940DD9", "name": "AI War - Core", "version": "1.0.7"}
]
```

For the custom Everon scenario, set `ARMA_SCENARIO_ID={A485A1003778460A}Missions/AI_War_Everon.conf` in your private `arma.env` and use these pins. Workshop can resolve Core automatically, but pin both versions if you want to reproduce this exact setup. [AI War – Everon](https://reforger.armaplatform.com/workshop/B161D59D617A4AF5)

```json
[
  {"modId": "68EB7BF2F5940DD9", "name": "AI War - Core", "version": "1.0.7"},
  {"modId": "B161D59D617A4AF5", "name": "AI War - Everon", "version": "1.0.0"}
]
```

Generate a new configuration at a private path. Inspect it before replacing the live configuration.

```bash
python3 scripts/generate_config.py --env-file "$arma_private/arma.env" \
  --mods-file "$arma_private/workshop-mods.json" --output "$arma_private/config.review.json"
```

## Configure the game bridge

Stop the game server and put these plain-text files in the **dedicated server's profile directory**, not in the addon or BI server config.

| File | Contents |
| --- | --- |
| `godmode_api_key.txt` | The middleware's game key, `GODMODE_GAME_API_KEY`. Do not use the dashboard/admin key or a model-provider key. |
| `godmode_server_url.txt` | For a remote middleware, one HTTPS origin, for example `https://middleware.example.com`. Optional for same-host middleware. |

Use ASCII text with no BOM, quotes, spaces, or extra lines. The URL may include a port and one trailing slash, but no path, credentials, query, or fragment. If the URL file is absent, the addon uses its scenario default, normally `http://127.0.0.1:8080`. An invalid override disables the bridge. Plain HTTP is supported only for explicit loopback.

For a rented Arma server, use its panel file manager or SFTP if it exposes the directory Enfusion mounts as `$profile:`. With `-profile /path/to/server-data`, this is normally `/path/to/server-data/profile`. The server startup log identifies it as the filesystem named `profile`. If the panel does not expose it, ask your provider where to upload private files. Keep the game key readable only by the server account or authorized panel operators. The addon reads both files at startup, so restart the game server after editing them. [Bohemia server startup parameters](https://community.bistudio.com/wiki/Arma_Reforger:Startup_Parameters)

For remote self-hosting, keep the default VPS port private and have a TLS reverse proxy forward the game route to the middleware. Keep dashboard and admin routes private. The game connection is outbound HTTPS from Arma and does not require opening an additional game-server UDP port.

## Verify the connection

Start the middleware with LLM calls disabled, then start the game server. Check its log for the expected Workshop versions and scenario, and check the middleware for an authenticated `POST /api/game-master` returning HTTP `200`. A successful `GET /health` does not verify the addon connection. Before enabling model calls for players, join privately and verify that a returned command appears in-game.

Both published scenarios started and authenticated to loopback middleware on a clean Linux dedicated server. Live player and model behavior, and external HTTPS on a hosting panel, remain unverified.
