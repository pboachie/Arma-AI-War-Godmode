# Security policy

## Deployment boundary

The default Linux VPS deployment publishes middleware TCP `8080` on `127.0.0.1` only. Use SSH local forwarding for remote dashboard access. `/health` is unauthenticated liveness and `/dashboard` serves the page without a key, but neither should be published to the internet. Admin data and operations, including `/api/health`, require the admin key. The public install does not publish game ports, enable RCON, or start a game server.

If you add a dedicated server, expose only the intended gameplay UDP port and, if needed, the A2S query UDP port. Keep RCON local or off. A hidden server is not necessarily unreachable. Check actual ingress, including your VPS firewall, host firewall and other network interfaces. [Server setup and port boundaries](docs/SERVER_SETUP.md#4-decide-what-to-expose)

## Keep credentials separate

- `GODMODE_API_KEY` is for operators and the dashboard. `GODMODE_GAME_API_KEY` is for `POST /api/game-master` and `GET /api/pending-commands`. Send either as `X-API-Key` only for its intended routes. Do not put keys in URLs.
- Keep `middleware/.env` readable only by its deployment account. Give the game server only the game key in its local `$profile:godmode_api_key.txt`, readable only by the game service account. Never put a key in game source, addon assets, a Workshop upload, or the client.
- Keep provider credentials in the middleware environment and dedicated-server passwords in private game configuration. Use distinct passwords and keys. Treat backups, generated configs, Docker environment inspection, and unredacted logs as private.
- Use code and addons from reviewed sources. When you enable a hosted LLM provider, the service can send in-game data to that provider. Review provider data handling and usage before enabling it.
- Raw replay recording is off by default. Treat any opt-in recordings and the state volume as private game data. [Replay retention](docs/OPERATIONS.md#record-a-replay-for-troubleshooting)

If a credential leaks, revoke or rotate it. Removing it from a file or commit does not invalidate it. [Incident response and credential rotation](docs/OPERATIONS.md#respond-to-a-suspected-exposure)

## Report a vulnerability privately

Use **Report a vulnerability** in the repository's [Security tab](https://github.com/pboachie/Arma-AI-War-Godmode/security) if private reporting is enabled. Otherwise contact a maintainer privately. Do not post security details or secrets in a public issue.

Include the affected commit or version, the environment and deployment mode, the relevant paths or behaviors, potential impact, and sanitized evidence. Remove API keys, passwords, private IPs, player data, and raw configuration files. Only investigate on systems you own or are authorized to test.
