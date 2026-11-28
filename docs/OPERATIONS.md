# Operator runbook

Use the repo-root `compose.yml` and `middleware/.env` on the VPS. These commands manage only the middleware. Stop or restart the separately installed Arma server using its own service manager. For public ingress limits, see [Server setup](SERVER_SETUP.md#4-decide-what-to-expose).

The historical combined image is retired. Run the game separately so it never shares middleware or provider credentials. On Windows, use the guarded Linux installer from a WSL filesystem with Docker integration rather than the retired native launcher.

## Check the current deployment

Run locally on the VPS from the repo root:

```bash
git rev-parse HEAD
docker compose --env-file middleware/.env ps
docker compose --env-file middleware/.env images
curl -fsS http://127.0.0.1:8080/health
docker compose --env-file middleware/.env logs --tail=80 middleware
```

Use `/api/health` with the admin key or check the dashboard via its [SSH tunnel](../README.md#open-the-dashboard) for details. The dashboard page can load without the admin key, but its protected data calls require the key. Check that the published middleware port is on `127.0.0.1` only and no game or RCON ports are published by Compose. If you changed `GODMODE_PORT`, use the matching host port for health checks, the tunnel destination, and the game bridge. Review logs privately and redact them before sharing. Docker inspection and resolved Compose configuration can reveal credentials.

## Tactical dashboard

Unlock with the admin API key over the existing SSH tunnel. Choose **Everon ·
satellite** only for a scenario using the Eden/Everon terrain, including AI War
– Everon. Core SITREPs do not identify the terrain. Set
`GODMODE_DASHBOARD_MAP=everon` in the deployed `middleware/.env` to preselect it
for that server on the next middleware recreation. Leave it unset for other
maps or if you change terrains frequently.

Use **Focus players**, the layer toggles, or select a track for its faction,
order, health, and location when the game reports those details. The map is a
north-up 12.8 km satellite capture with a 1 km grid. It uses the live report's
X and Z coordinates and deliberately skips unlocated tracks. Counts show the
players or AI tracks the addon reports, not every NPC or vehicle in the world.
Objectives and Side Operations appear beside the map as reported by the game.
The current bridge does not supply positions for every objective.

Switch to **AI operations** to see planner phase, turn, planned actions and
recent Game Master decisions. **Chat** asks the configured model for an
advisory explanation based on a bounded current briefing. It uses the same
provider limits as game decisions and does not insert orders into the match.
Questions and replies stay in the tab rather than becoming a game transcript.

The page checks for new reports every 10 seconds. Positions change on SITREPs,
not every game frame. After 75 seconds without a recent report, the dashboard
labels positions stale. A failed read marks them unavailable, and the last
known markers are dimmed. A queued command or a Game Master decision is not
proof that the game executed it.

Use **Admin commands** for a broadcast or time change. Raw API views, existing
JSON batch commands, and session reset remain in **Diagnostics & advanced
commands**. The browser never calls an external tile provider and uses the
admin key for the map image as well as telemetry. See the bundled [Everon map
notice](../middleware/everon-map.NOTICE.md) for the imagery and location data
license and calibration.

For an install from a release archive, read the source SHA and tag from `RELEASE.json` instead of using Git.

## Back up

1. Record the running source SHA, middleware image ID or digest, configured ports, dedicated-server build, and exact trusted addon versions. Keep this inventory separate from credentials.
2. Schedule downtime. Stop the game gracefully so it stops writing match state. From the deployment directory, run `bash scripts/stop.sh` to flush and stop that checkout’s middleware before taking a consistent snapshot.
3. Back up the Compose `middleware-state` volume mounted at `/var/lib/godmode`. Use your Docker volume backup tooling or your VPS storage snapshot system. Preserve file ownership and permissions. Separately back up `middleware/.env`, the private Arma config and input file, and the dedicated-server profile and saves if present. Assume backups contain credentials and private game data. Keep them encrypted and access-restricted. [Docker volume backup and restore](https://docs.docker.com/engine/storage/volumes/#back-up-restore-or-migrate-data-volumes)
4. Start the middleware with `bash scripts/install-vps.sh`, then start the game if used. Verify liveness, dashboard authentication, and the game separately. Periodically restore into an isolated test environment where provider calls and player ingress are disabled. Do not remove the data volume as part of a restart or routine update.

## Update

1. Back up first and retain the known-good image and source revision. Fetch and review the change you plan to deploy. Pay particular attention to the installer, Compose ports, volumes, environment settings, and authorization changes. Do not deploy from an unreviewed moving branch or overwrite local edits.
2. Switch the existing deployment checkout to the reviewed commit, keeping the same Compose project and volume. Keep `middleware/.env` private and compare variable names against the new template without copying the template over it. Rerun the installer from the same checkout. It ignores exported application settings and waits for the middleware health check:

   ```bash
   bash scripts/install-vps.sh
   curl -fsS http://127.0.0.1:8080/health
   ```

3. Verify that the port is still loopback-only, admin access still works, and the game bridge can submit an authenticated report if in use. If provider calls are enabled, verify them in a private match and check usage. Update the game installation and addons separately during a planned game maintenance window, with matching client and server versions.

For an archive install, verify the new download and extract it into a separate temporary directory first. After backing up and stopping the stack, update the application files in the original deployment directory. Preserve `middleware/.env`, keep the same directory path and Compose volume, and rerun the installer there. [Release downloads and checksums](RELEASES.md)

The guarded installer records checkout ownership in a Docker volume named `godmode-middleware-ownership`. It is not mounted into the middleware and survives container removal. If an older installation has only its state volume left, restore its original `middleware/.env` to the intended checkout and run `bash scripts/install-vps.sh --adopt-existing-state` once. This does not claim a deployment already owned by another checkout.

## Record a replay for troubleshooting

Raw replay recording is off by default. The middleware still keeps the current campaign and derived battle summaries in its private volume. For a short debugging session, set `GODMODE_RECORD_REPLAYS=true` in `middleware/.env` and recreate the middleware from the same checkout. Replays can contain player IDs, positions, and game events, so keep them private and turn recording off when you finish.

New recordings rotate after 8 MiB of uncompressed events and keep the newest 16 managed replay files. The middleware keeps up to 50 managed files per report directory as it writes new reports. Back up anything you need before enabling recording or updating from an older deployment. Turning recording off stops new raw writes but doesn't delete older files. For deletion, stop the middleware and remove only the intended recordings from the private volume.

## Roll back

1. Stop the failing deployment and save its logs and current state separately. Return the same checkout to the recorded known-good source commit. Prefer the known-good image from that deployment if retained. Rebuilds do not guarantee a byte-for-byte match.
2. Keep active credentials. Restore the prior configuration settings and corresponding state snapshot if the failed version changed state in an incompatible way. Restore into the intended volume while the middleware is stopped. Do not restore retired keys or discard newer state without preserving a copy.
3. Restart on the known-good image/config. If you retained the exact image, tag it with the service image name recorded from `docker compose images`, then use `docker compose --env-file middleware/.env up -d --no-build --pull never`. Check the port, liveness, admin dashboard, and private game bridge before allowing players back. Never roll back to a version with unsafe network exposure or missing API authentication. If a game or addon update caused the failure, recover the matching game/addon/profile combination separately, or keep it offline until you have a compatible build.

## Respond to a suspected exposure

1. Contain first. Close unintended ingress at the VPS firewall and host firewall, stop affected services if necessary, and disable provider calls if you suspect unauthorized usage. Check other network paths, including IPv6 and any proxies. Do not reopen public middleware or RCON access to troubleshoot.
2. Keep restricted copies of relevant logs and timestamps. Record deployed revisions, reachable ports, and provider usage. Revoke exposed provider credentials in the provider console, review recent usage and charges, and rotate affected keys as below.
3. Restore only after checking that ingress, route authentication, and key separation work as intended. Notify affected people privately if needed. See [Security](../SECURITY.md) to report a repository vulnerability without publishing operational secrets.

### Rotate credentials

Use a private editor or your secret manager. For middleware keys, use two separately generated, URL-safe random values, each 32–128 characters using only letters, digits, underscores, and hyphens. Do not reuse either key as a game password or a provider credential.

| Credential | Change it here | Apply and verify |
| --- | --- | --- |
| Admin API key | `GODMODE_API_KEY` in `middleware/.env` | Recreate the container. Clear the old key from the dashboard and use the replacement over the SSH tunnel. Verify that protected data loads with the replacement and the retired key no longer works. |
| Game API key | `GODMODE_GAME_API_KEY` in `middleware/.env` and the server-local `$profile:godmode_api_key.txt` | Stop the game before changing both copies. Keep the profile file at mode `0600`. Recreate middleware and relaunch the game. Verify an authenticated game request in a private match. Do not republish the addon. |
| Provider key | The provider console and the matching setting in `middleware/.env` | Revoke the exposed key, recreate middleware with the replacement, and verify provider usage with a private test. Set `LLM_PROVIDER=disabled` while you are not using a provider. |
| Arma admin or join password | Private `arma.env` and its generated `config.json` | Generate a fresh config and preserve the intended scenario and addon settings. Restart the game and verify the new password. |
| RCON password, if enabled | Private Arma config input and any explicitly configured local RCON client | Prefer disabling RCON if it is no longer needed. Otherwise generate a fresh game config with a distinct password, restart the game, and verify the local-only bind. |

After middleware credential or provider changes, from the repo root run `docker compose --env-file middleware/.env up -d --force-recreate`. A plain container restart does not replace environment values. Update the restricted backup with the new configuration and do not restore revoked values later.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| Installer cannot use Docker | On the VPS, check that the supported Engine and Compose v2 plugin are installed and that your deployment account already has daemon access. Do not loosen permissions on the Docker socket. |
| `/health` does not answer | Use Compose `ps` and logs. Check for build/startup errors or a host port conflict. Confirm the host bind, configured port, and that the request is made on the VPS or through its tunnel. |
| Dashboard tunnel fails | Confirm SSH access is allowed, the VPS-side port answers locally, and workstation port `18080` is free. If you use another local port, update both the forward and the dashboard URL. Keep the tunnel bound to workstation loopback. |
| Protected route responds `401` | Use the admin key for dashboard/API calls and the game key for bridge calls. Check that the values are distinct, valid and current, and that Compose was recreated after changes. Use the `X-API-Key` header only. Never copy keys into URLs or diagnostic output. |
| Game never reports or reports `401` | Check the exact scenario/addon loaded, component or hook initialization, the game host's middleware URL, and the path, permissions and single-line contents of the dedicated-server profile key file. `127.0.0.1` must refer to the middleware host from the game process. Do not work around this by opening a public API port. |
| Reports arrive but there are no model decisions | Check `LLM_PROVIDER`, its credential and model, provider-side usage, and middleware logs for authentication, quota, or connectivity errors. `LLM_PROVIDER=disabled` is intentional until activation. A report can be accepted without a model call. |
| Game loads vanilla or cannot join | Check the full `.conf` resource ID, exact addon ID and version, the client's matching build, and any dependencies. Verify the intended gameplay UDP port and ingress from the client. A2S is not required for the game bridge. |
| Commands have no visible effect | Verify the installed Workshop version and scenario. Check both the game server and middleware logs. Authentication and a successful HTTP response do not prove the game applied the command. |
| Server lifecycle or RCON controls do not work | Expected under the root Compose deployment. Manage the game separately. No RCON is required for the game bridge. |
