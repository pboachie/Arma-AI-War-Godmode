# Arma Reforger AI War Godmode

AI War is an experimental game-master addon for fictional matches in Arma Reforger. A server-side script sends an in-game situation report to Python middleware. If you enable an LLM provider, the middleware can turn that report into game commands for things like weather, objectives, NPC groups, and vehicles. Game scripts still handle local behavior, and server operators remain in control of deployment and configuration.

This repository supplies the self-hostable FastAPI middleware, dashboard, and dedicated-server configuration tooling. The game addons are distributed separately through Bohemia's Workshop. Starting the middleware does not install or run the game. [AI War – Core](https://reforger.armaplatform.com/workshop/68EB7BF2F5940DD9) · [AI War – Everon](https://reforger.armaplatform.com/workshop/B161D59D617A4AF5)

## Start the middleware on a Linux VPS

You need Bash, curl, and a local [Docker Engine](https://docs.docker.com/engine/install/) (28 or newer for loopback publishing) with a [Compose v2 plugin](https://docs.docker.com/compose/install/linux/) that supports `up --wait-timeout`. Connect to Docker over a local Unix socket with an account already authorized to use it. Docker daemon access is privileged.

Run this from a directory you control. The bootstrap comes from the latest published GitHub Release and verifies that release's exact VPS bundle before starting the middleware. It leaves any existing `./Arma-AI-War-Godmode` directory untouched.

```bash
( installer=$(mktemp) && trap 'rm -f -- "$installer"' EXIT && curl -q --proto '=https' --proto-redir '=https' -fsSL -o "$installer" https://github.com/pboachie/Arma-AI-War-Godmode/releases/latest/download/install-linux-vps.sh && bash "$installer" )
```

You can also download a specific release bootstrap instead of using `latest`. For older releases without a bootstrap, including v3.0.1, verify and extract the VPS archive directly. [Release packages and verification](docs/RELEASES.md)

For a pinned **source** install, use Git and set `reviewed_commit` to the full SHA of a commit you trust. This path does not use the tested release bundle.

```bash
( installer=$(mktemp) && trap 'rm -f -- "$installer"' EXIT && curl -q --proto '=https' --proto-redir '=https' -fsSL -o "$installer" "https://raw.githubusercontent.com/pboachie/Arma-AI-War-Godmode/$reviewed_commit/scripts/bootstrap-vps.sh" && bash "$installer" --ref "$reviewed_commit" )
```

From an existing reviewed checkout, run `bash scripts/install-vps.sh` at the repo root. If the bootstrap clones successfully but Docker fails, fix the Docker error and use that same command inside the checkout. The repo pins the middleware base image and hashes its Python dependencies.

The canonical deployment uses the root `compose.yml`. Keep running Compose commands from the repo root with the same environment file:

```bash
docker compose --env-file middleware/.env ps
curl -fsS http://127.0.0.1:8080/health
```

The installer creates `middleware/.env` with two distinct keys and mode `0600` on a fresh install. It does not install host packages or change the firewall. Keep one deployment checkout per Docker host and rerun the installer from that checkout for updates. The default install binds the middleware and dashboard to `127.0.0.1:8080`, publishes no game ports or RCON, and starts without LLM calls or raw replay recording. `GET /health` is unauthenticated liveness only. Detailed health is available to the admin API at `GET /api/health`.

## Open the dashboard

From your workstation, replace `your-user@your-vps` with your SSH login and leave the tunnel running:

```bash
ssh -N -o ExitOnForwardFailure=yes -L 127.0.0.1:18080:127.0.0.1:8080 your-user@your-vps
```

Open `http://127.0.0.1:18080/dashboard` on that workstation. The dashboard's protected API calls use `X-API-Key` with the admin key (`GODMODE_API_KEY`). Retrieve that value from `middleware/.env` on the VPS in a private editor and enter it in the dashboard on a trusted workstation. Do not put it in a URL, shell command, addon, or support message.

## Play a match with the addon

1. Install the Arma Reforger dedicated server separately using SteamCMD. Generate a private server config and verify the vanilla server before adding the mod. [Dedicated-server setup](docs/SERVER_SETUP.md)
2. Add Core 1.0.7 to stock Conflict, or use the separate Everon 1.0.0 scenario. Set the game key and optional remote middleware URL in the dedicated server profile. [Connect the addon](docs/ADDON_SETUP.md)
3. Once you are ready for model calls, set `LLM_PROVIDER` and the matching provider credential in `middleware/.env`, then recreate the middleware container. This may send in-game state to your configured provider and incur charges. [Provider setup and verification](docs/SERVER_SETUP.md#enable-a-provider)

For updates, backups, rollback, or troubleshooting, see the [operator runbook](docs/OPERATIONS.md). For credential boundaries and private vulnerability reporting, see [Security](SECURITY.md). For tests and dependency maintenance, see the [development guide](docs/DEVELOPMENT.md).

Original repository code is released under the [MIT License](LICENSE). Bohemia Interactive's game, binaries, and assets are not included in that license.
