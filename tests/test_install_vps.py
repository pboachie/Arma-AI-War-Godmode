"""Offline installer regression tests. Run on Linux with Python 3."""

import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
DOCKER_STUB = r'''#!/usr/bin/env bash
set -eu
printf '%s\n' "$*" >> docker.calls
if [[ -v GODMODE_API_KEY || -v GODMODE_PORT || -v LLM_PROVIDER || -v COMPOSE_FILE ]]; then
    printf '%s\n' 'ambient application or Compose setting reached Docker' >&2
    exit 1
fi
case "$*" in
    'context show') echo default; exit ;;
    'context inspect '*) cat docker.endpoint; exit ;;
    '--context default version '*) cat docker.version; exit ;;
    '--context default compose version --short') echo v2.30.0; exit ;;
    '--context default compose up --help') echo 'up --wait-timeout'; exit ;;
    '--context default ps --all '*) if [[ -f docker.existing ]]; then cat docker.existing; fi; exit ;;
esac
[[ $1 == --context && $2 == default && $3 == compose ]]
settings=''
while (( $# )); do
    if [[ $1 == --env-file ]]; then
        settings=$2
        shift 2
        break
    fi
    shift
done
case "$*" in
    'config --quiet')
        if [[ -f docker.bad-config ]]; then
            cat "$settings" >&2
            exit 1
        fi
        ;;
    'config --environment') cat "$settings" ;;
    'up --build --wait --wait-timeout 120 --no-deps middleware')
        if [[ -f docker.bad-up ]]; then
            cat "$settings" >&2
            exit 1
        fi
        ;;
    *) exit 1 ;;
esac
'''


@unittest.skipUnless(sys.platform.startswith("linux"), "VPS installer requires Linux")
class InstallerTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="godmode installer ")
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        for relative in ("compose.yml", "middleware/Dockerfile", "scripts/install-vps.sh"):
            target = self.root / relative
            target.parent.mkdir(exist_ok=True)
            shutil.copyfile(ROOT / relative, target)
        self.settings = self.root / "middleware/.env"
        self.calls = self.root / "docker.calls"
        (self.root / "docker.endpoint").write_text("unix:///var/run/docker.sock\n")
        (self.root / "docker.version").write_text("linux 28.0.0\n")
        stub = self.root / "docker"
        stub.write_text(DOCKER_STUB)
        stub.chmod(0o700)
        self.env = dict(os.environ, PATH=f"{self.root}:{os.environ['PATH']}")
        for name in ("DOCKER_HOST", "DOCKER_CONTEXT"):
            self.env.pop(name, None)

    def run_installer(self, **environment):
        return subprocess.run(
            ["bash", "-x", str(self.root / "scripts/install-vps.sh")],
            cwd=self.root,
            env=self.env | environment,
            capture_output=True,
            text=True,
            timeout=20,
        )

    def test_creates_private_settings_and_keeps_operator_edits(self):
        first = self.run_installer(
            GODMODE_API_KEY="ambient", GODMODE_PORT="9999", LLM_PROVIDER="openai", COMPOSE_FILE="legacy.yml"
        )
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        self.assertIn("http://127.0.0.1:8080/health", first.stdout)
        self.assertEqual(stat.S_IMODE(self.settings.stat().st_mode), 0o600)
        config = dict(line.split("=", 1) for line in self.settings.read_text().splitlines() if "=" in line)
        operator_key, game_key = config["GODMODE_API_KEY"], config["GODMODE_GAME_API_KEY"]
        self.assertRegex(operator_key, r"^[0-9a-f]{64}$")
        self.assertRegex(game_key, r"^[0-9a-f]{64}$")
        self.assertNotEqual(operator_key, game_key)
        self.assertEqual(config["LLM_PROVIDER"], "disabled")
        edited = self.settings.read_text().replace("GODMODE_PORT=8080", "GODMODE_PORT=9080")
        self.settings.write_text(edited + "LLM_MODEL=$(touch .env-was-sourced)\n")
        before = self.settings.read_bytes()

        second = self.run_installer()
        self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
        self.assertIn("http://127.0.0.1:9080/health", second.stdout)
        self.assertFalse((self.root / ".env-was-sourced").exists())
        self.assertEqual(self.settings.read_bytes(), before)
        self.assertFalse(list(self.settings.parent.glob(".env.tmp.*")))
        for completed in (first, second):
            self.assertNotIn(operator_key, completed.stdout + completed.stderr)
            self.assertNotIn(game_key, completed.stdout + completed.stderr)

    def test_remote_engine_and_old_engine_do_not_create_settings(self):
        result = self.run_installer(DOCKER_HOST="ssh://example.test")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.calls.exists())
        self.assertFalse(self.settings.exists())

        (self.root / "docker.endpoint").write_text("tcp://example.test:2376\n")
        self.assertNotEqual(self.run_installer().returncode, 0)
        self.assertNotIn(" version ", self.calls.read_text())
        self.assertFalse(self.settings.exists())

        (self.root / "docker.endpoint").write_text("unix:///var/run/docker.sock\n")
        (self.root / "docker.version").write_text("linux 27.5.1\n")
        result = self.run_installer()
        self.assertIn("Docker Engine 28+", result.stderr)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.settings.exists())

    def test_invalid_config_and_docker_errors_do_not_leak_credentials(self):
        operator_key = "z" * 64
        self.settings.write_text(f"GODMODE_API_KEY={operator_key}\nGODMODE_GAME_API_KEY={operator_key}\n")
        self.settings.chmod(0o600)
        repeat = self.run_installer()
        self.assertNotEqual(repeat.returncode, 0)
        self.assertNotIn(operator_key, repeat.stdout + repeat.stderr)
        self.assertNotIn(" --no-deps ", self.calls.read_text())

        self.settings.write_text(self.settings.read_text().replace(f"GAME_API_KEY={operator_key}", "GAME_API_KEY=" + "y" * 64))
        before = self.settings.read_bytes()
        for marker in ("docker.bad-config", "docker.bad-up"):
            fail_on = self.root / marker
            fail_on.touch()
            result = self.run_installer()
            fail_on.unlink()
            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn(operator_key, result.stdout + result.stderr)
            self.assertEqual(self.settings.read_bytes(), before)

    def test_existing_stack_from_other_checkout_is_left_untouched(self):
        deployment = self.root / "docker.existing"
        deployment.write_text("/another/checkout\n")
        result = self.run_installer()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("another checkout", result.stderr)
        self.assertFalse(self.settings.exists())
        deployment.write_text(f"{self.root}\n")
        self.assertEqual(self.run_installer().returncode, 0)


if __name__ == "__main__":
    unittest.main()
