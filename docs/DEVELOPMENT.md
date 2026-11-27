# Development checks

From the repository root with Python 3.12 and Bash on Linux:

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --require-hashes --only-binary=:all: -r middleware/requirements.txt -r tests/requirements.txt
python -m pip check
python -m unittest discover -s tests
python -m pytest -q middleware/test_runtime_security.py
for script in scripts/bootstrap-release.sh scripts/bootstrap-vps.sh scripts/install-vps.sh middleware/start.sh scripts/stop.sh; do bash -n "$script"; done
```

These checks use synthetic game data and no model credentials. The middleware health check only tests liveness. Before making claims about in-game integration, also compile the addon in Workbench and test an authenticated request and a visible game response with the intended game build. [Workbench guide](WORKBENCH_SETUP.md)

On each compiled bridge path, stop the private middleware long enough to trigger the retry cooldown, then start it again. Verify that a single report goes through after cooldown and that normal reporting resumes without restarting the game server. The source archive is not a tested Workshop build.

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

Use `--upgrade-package PACKAGE` to intentionally update an already locked dependency. Review the lock diff, test from a clean environment, and rebuild the middleware image. The historical combined game and middleware image is retired. The regular VPS installer does not start the game.

Regenerate the test lock separately so it cannot override the runtime dependency lock.

```bash
uv pip compile --universal --python-version 3.12 --generate-hashes --only-binary :all: \
  --constraint middleware/requirements.txt --no-annotate --no-header \
  --output-file tests/requirements.txt tests/requirements.in
```
