# Arma Reforger AI War Godmode

AI War is an experimental game-master addon for fictional matches in Arma Reforger. A server-side script sends an in-game situation report to Python middleware. If you enable an LLM provider, the middleware can turn that report into game commands for things like weather, objectives, NPC groups, and vehicles. Game scripts still handle local behavior, and server operators remain in control of deployment and configuration.

The repository supplies Enfusion source, the FastAPI middleware and dashboard, and dedicated-server configuration tooling. It does not supply Arma Reforger binaries, a ready-to-load Workshop addon, or a verified playable scenario. Starting the middleware does not install or run the game.

## Start the middleware on a Linux VPS

You need Git, Bash, curl, and a local [Docker Engine](https://docs.docker.com/engine/install/) (28 or newer for loopback publishing) with a [Compose v2 plugin](https://docs.docker.com/compose/install/linux/) that supports `up --wait-timeout`. Connect to Docker over a local Unix socket with an account already authorized to use it. Docker daemon access is privileged.

Once the installer is available upstream, run this from a directory you control on the VPS. It downloads the bootstrap, clones `main` into `./Arma-AI-War-Godmode`, and starts the middleware without replacing an existing checkout.

```bash
( installer=$(mktemp) && trap 'rm -f -- "$installer"' EXIT && curl -q --proto '=https' --proto-redir '=https' -fsSL -o "$installer" https://raw.githubusercontent.com/pboachie/Arma-AI-War-Godmode/main/scripts/bootstrap-vps.sh && bash "$installer" )
```

The one-liner follows the moving `main` branch. To pin both steps, set `reviewed_commit` to the full SHA of a commit you trust, then run:

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
2. Import the scripts into a Workbench mod project, verify resources and compile, and test a scenario with a trusted build of the addon. [Workbench and game bridge](docs/WORKBENCH_SETUP.md)
3. Once you are ready for model calls, set `LLM_PROVIDER` and the matching provider credential in `middleware/.env`, then recreate the middleware container. This may send in-game state to your configured provider and incur charges. [Provider setup and verification](docs/SERVER_SETUP.md#enable-a-provider)

For updates, backups, rollback, or troubleshooting, see the [operator runbook](docs/OPERATIONS.md). For credential boundaries and private vulnerability reporting, see [Security](SECURITY.md). For tests and dependency maintenance, see the [development guide](docs/DEVELOPMENT.md).

Original repository code is released under the [MIT License](LICENSE). Bohemia Interactive's game, binaries, and assets are not included in that license.
