# Development checks

From the repository root with Python 3.12 and Bash on Linux:

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --require-hashes --only-binary=:all: -r middleware/requirements.txt
python -m pip install pytest
python -m pip check
python -m unittest discover -s tests
python -m pytest -q middleware/test_runtime_security.py
for script in scripts/bootstrap-vps.sh scripts/install-vps.sh middleware/start.sh scripts/stop.sh; do bash -n "$script"; done
```

These checks use synthetic game data and no model credentials. The middleware health check only tests liveness. Before making claims about in-game integration, also compile the addon in Workbench and test an authenticated request and a visible game response with the intended game build. [Workbench guide](WORKBENCH_SETUP.md)

## Update the middleware image inputs

Review upstream release notes before updating `middleware/requirements.in` or the Python image digest in `middleware/Dockerfile`. Use a trusted installation of `uv` to regenerate the Python lock:

```bash
uv pip compile --universal --python-version 3.12 --generate-hashes --only-binary :all: \
  --no-annotate --no-header --output-file middleware/requirements.txt middleware/requirements.in
```

Use `--upgrade-package PACKAGE` to intentionally update an already locked dependency. Review the lock diff, test from a clean environment, and rebuild the middleware image. Keep the experimental combined Dockerfile's Python digest in sync if you maintain that image. The regular VPS installer does not build or start a legacy stack.
