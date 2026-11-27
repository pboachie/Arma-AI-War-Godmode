# Development checks

From the repository root with Python 3.12 and Bash on Linux:

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --require-hashes --only-binary=:all: -r middleware/requirements.txt -r tests/requirements.txt
python -m pip check
python -m unittest discover -s tests
python -m pytest -q middleware/test_runtime_security.py middleware/test_side_operations.py
for script in scripts/bootstrap-release.sh scripts/bootstrap-vps.sh scripts/install-vps.sh middleware/start.sh scripts/stop.sh; do bash -n "$script"; done
```

These checks use synthetic game data and no model credentials. The middleware health check only tests liveness. Before making claims about in-game integration, verify an authenticated report and a visible game response from the intended Workshop addon and game build. [Addon setup](ADDON_SETUP.md)

The side-operation tests exercise the existing authenticated report endpoint. The optional v1 start is returned once, on a report matching the original Core session and revision. Polling for legacy commands cannot consume it. Only Core reports outcomes. An acknowledgement timeout affects retry policy without creating an outcome.

For recovery testing, stop the private middleware long enough to trigger the retry cooldown, then start it again. Verify that reporting resumes without restarting the game server.

## Qualify Everon side operations

Keep the Workbench projects out of the public repository. On Windows with matching Reforger retail, dedicated server, Workbench, and the middleware test dependencies, run `tests/verify_local_side_operations.py` with `--pilot <private-evidence-directory>` and either `--source <split-project-parent>` to pack both projects or `--addons <packed-addon-parent>` to verify existing packs. The harness loads each case into its own loopback-only profile, verifies the actual package mounts and a legacy command round-trip, and records non-player telemetry and package hashes. Its default mode disables the model. Use `--case everon --manual-client --fixture-model` to hold a match for a player with deterministic model responses. The script prints a matching client launch command and the local join address after the server is ready.

If the retail server browser cannot look up the loopback address, add `--lan-address <this-computer-private-IP>` to that manual Everon test. It registers the game on that specific interface, keeps it unlisted with a temporary match password, and leaves the middleware on loopback.

Before using a new build on a player server, check a private match with the exact candidate packs:

1. Stock Conflict must still play normally and report no side-operation routes. Everon should advertise its authored routes but should not receive a start while no eligible player is in game.
2. Join Everon and spawn on the supported faction. Confirm a native task and pickup marker are visible to that faction. Confirm the other faction does not receive the task.
3. Pick up and deliver the operation cargo in the game. Verify that only this cargo advances the task, the pickup marker moves to the delivery site, and playthrough reports the completed operation under the original ID. A defense follow-up must use the same route and reference the reported success. An ignored or failed run must not start that defense.
4. Interrupt the private middleware after task acceptance. Players should still be able to finish, and the authoritative terminal result should reach middleware after reconnect without opening a duplicate task. Retain game and middleware evidence without credentials or player identifiers.

Mark compile, boot, client join, cargo delivery, and model decisions separately. An authenticated report or deterministic provider stub does not prove a live model-driven round. Always keep the game and clients on matching packs, including when testing private packs instead of Workshop builds.

## Preview release packages locally

The packager uses only allowlisted files from a committed revision. It does not read local settings, upload artifacts, or create a tag. Use a new output directory for each run.

```bash
python scripts/package_release.py --commit "$(git rev-parse HEAD)" --tag v0.0.0 --output dist/release-smoke
(cd dist/release-smoke && sha256sum --check --strict SHA256SUMS)
```

The GitHub Actions check installs the tested VPS archive on a disposable runner. See the [release guide](RELEASES.md) for publishing, reruns, and fork permissions.

## Update the middleware image inputs

Review upstream release notes before updating `middleware/requirements.in` or the Python image digest in `middleware/Dockerfile`. Use a trusted installation of `uv` to regenerate the Python lock:

```bash
uv pip compile --universal --python-version 3.12 --generate-hashes --only-binary :all: \
  --no-annotate --no-header --output-file middleware/requirements.txt middleware/requirements.in
```

Use `--upgrade-package PACKAGE` to intentionally update an already locked dependency. Review the lock diff, test from a clean environment, and rebuild the middleware image. The VPS installer does not start the game.

Regenerate the test lock separately so it cannot override the runtime dependency lock.

```bash
uv pip compile --universal --python-version 3.12 --generate-hashes --only-binary :all: \
  --constraint middleware/requirements.txt --no-annotate --no-header \
  --output-file tests/requirements.txt tests/requirements.in
```
