# Server setup

Start with the [middleware quick start](../README.md#start-the-middleware-on-a-linux-vps). This guide adds an optional, separately installed Arma Reforger dedicated server. For the documented layout, run the game process directly on the same Linux host as the loopback-published middleware. Use a non-root game service account. A game container or a game server on another host does not share the VPS loopback interface.

The repo-root `compose.yml` is the middleware deployment. The alternate Docker and launcher files are not required for this guide. Do not start a second stack to reach the dashboard.

## 1. Install the dedicated server

Install SteamCMD and its host dependencies using the upstream instructions. As the account that will own the game files, install the stable release (Steam app ID `1874900`):

```bash
arma_install="$HOME/arma_reforger"
steamcmd +force_install_dir "$arma_install" +login anonymous +app_update 1874900 validate +quit
```

Check that SteamCMD finished successfully and that `ArmaReforgerServer` exists in the target directory before continuing. Use a Workbench and game client that match the server's release branch. See the Linux section of [Bohemia's Server Hosting guide](https://community.bohemia.net/wiki/Arma_Reforger:Server_Hosting).

If a freshly installed SteamCMD logs in but returns `Missing configuration`, refresh the app metadata and retry with the Linux depot selected explicitly:

```bash
steamcmd +@sSteamCmdForcePlatformType linux +@ShutdownOnFailedCommand 1 \
  +force_install_dir "$arma_install" +login anonymous +app_info_update 1 \
  +app_info_print 1874900 +app_update 1874900 validate +quit
```

## 2. Generate a private configuration

From the repo root, as the account that will own the game config and profile (Python 3 required):

```bash
umask 077
arma_private="$HOME/.config/arma-ai-war"
arma_data="$HOME/.local/share/arma-ai-war/server-data"
install -d -m 0700 "$arma_private" "$arma_data" "$arma_data/profile"
if [ ! -e "$arma_private/arma.env" ]; then
  cp Configs/ArmaServer/arma.env.example "$arma_private/arma.env"
fi
chmod 0600 "$arma_private/arma.env"
```

Edit `"$arma_private/arma.env"` directly in a private editor. Set `ARMA_ADMIN_PASSWORD` to a unique, password-manager-generated value of 20–32 printable ASCII characters with no spaces. Optionally set `ARMA_GAME_PASSWORD` to require a different 20–32 character password to join. Reforger's server schema rejects either password above 32 characters. Use one `ARMA_*` assignment per line. Values can be unquoted or wrapped in single or double quotes and are read literally. Don't source the file as a shell script. Exported environment variables override file entries, so clear stale exports before generating.

```bash
python3 scripts/generate_config.py --env-file "$arma_private/arma.env" --output "$arma_private/config.json"
```

Without the flags, the generator reads exported `ARMA_*` settings and writes `Arma Reforger Server/configs/config.json`. It does not read the middleware's credentials. If your game account cannot read the deployment checkout, use a separate reviewed source checkout to run the generator. The generator refuses to overwrite an existing output without `--force`. For updates, generate to a new private path and inspect it before replacing the live config. If using Workshop, reuse the reviewed [`--mods-file`](WORKBENCH_SETUP.md#2-load-only-a-trusted-build) when regenerating the config.

The template starts on loopback for gameplay and A2S, with server-browser visibility off, a 24-player PC configuration, BattlEye on, no addons, and no RCON section. Its scenario is the vanilla Conflict on Everon mission (`{ECC61978EDCC2B5A}Missions/23_Campaign.conf`). This is an upstream scenario identifier, not evidence of a tested AI War scenario. For a different mission, set `ARMA_SCENARIO_ID` to the full scenario `.conf` resource ID, not the addon ID or the world `.ent` path. [Bohemia Server Config reference](https://community.bohemia.net/wiki/Arma_Reforger:Server_Config)

## 3. Launch vanilla first

Using the same paths and game account:

```bash
(
  cd "$arma_install" &&
  ./ArmaReforgerServer -config "$arma_private/config.json" -profile "$arma_data" -maxFPS 60
)
```

Check the dedicated-server startup log for the loaded scenario and for config errors. The loopback-only default is for a local/private baseline. A client on another machine cannot join through the default loopback bind. Stop the game process gracefully before changing its config or profile. Next, follow the [Workbench and game bridge guide](WORKBENCH_SETUP.md) and test privately before allowing players in.

## 4. Decide what to expose

| Service | Default | When to allow ingress |
| --- | --- | --- |
| Middleware TCP `8080` | VPS loopback | Never public for this setup. Use local access or the dashboard SSH tunnel. |
| SSH | Managed by the VPS operator | Limit to trusted administrators where possible. |
| Gameplay UDP `2001` | Game loopback if installed | Allow the configured game UDP port only for intended players. Set `ARMA_GAME_BIND_ADDRESS` to the intended host IPv4 address. Set `ARMA_PUBLIC_ADDRESS` and `ARMA_PUBLIC_PORT` for your network if the advertised endpoint differs. Use `ARMA_GAME_VISIBLE=true` only if you intend server-browser visibility. |
| A2S UDP `17777` | Game loopback if installed | Optional. Set `ARMA_A2S_BIND_ADDRESS` and allow its UDP port only if your server needs external queries. |
| RCON UDP | Omitted | Keep off the public network, even when explicitly enabled for local use. |

Apply both VPS/provider ingress rules and host firewall rules, including IPv6 if used. Check from an approved remote client that only the intended gameplay and optional A2S ports are reachable. Server-browser visibility is not access control. Keep the test restricted by network, and add a separate join password when needed.

RCON is unnecessary for either middleware or addon setup. If you deliberately need local BattlEye RCON, set `ARMA_RCON_ENABLED=true` and a strong, distinct `ARMA_RCON_PASSWORD` in the private game config input. Keep the default loopback bind and `monitor` permission unless you need more than monitoring. Use a local RCON client, and do not publish or forward its UDP port. This game-config opt-in does not configure the middleware to use RCON.

## Enable a provider

Leave LLM disabled while validating the addon and network path. When you are ready to send in-game state to an external provider, edit `middleware/.env` privately and set `LLM_PROVIDER` and the matching credential. For example, select `openai` with `OPENAI_API_KEY` or `anthropic` with `ANTHROPIC_API_KEY`. See the [environment template](../middleware/.env.example) for the options. Do not set a fallback unless you intend to allow calls to it too. Never place a provider key in the game config, profile, or addon.

For Z.ai, select `LLM_PROVIDER=glm` and supply `GLM_API_KEY`. The default `GLM_BASE_URL` is `https://api.z.ai/api/coding/paas/v4/` and the default model is `glm-4.7`. Use a key authorized for your endpoint and workload. Set `GLM_BASE_URL` and `LLM_MODEL` explicitly if using a different supported endpoint or model. [Z.ai key and endpoint guidance](https://docs.z.ai/devpack/notice/api-key-management)

Apply the change from the repo root in a shell without conflicting exported provider settings:

```bash
docker compose --env-file middleware/.env up -d --force-recreate
curl -fsS http://127.0.0.1:8080/health
```

Check detailed health through the authenticated dashboard. Use a private test match to verify the first successful request, valid game response, and actual in-game result. A successful liveness check does not prove provider authentication or an in-game result. Review provider-side usage and cost controls before opening the server to players.
