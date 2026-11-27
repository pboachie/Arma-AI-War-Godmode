"""Offline bootstrap tests using an isolated substitute Git remote."""

import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest


BOOTSTRAP = Path(__file__).resolve().parents[1] / "scripts/bootstrap-vps.sh"
UPSTREAM = "https://github.com/pboachie/Arma-AI-War-Godmode.git"
GIT = shutil.which("git")


@unittest.skipUnless(sys.platform.startswith("linux") and GIT, "requires Linux and Git")
class BootstrapTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="godmode bootstrap ")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.remote = self.root / "remote"
        self.remote.mkdir()
        self.git("init", "--quiet", "--initial-branch=main")
        installer = self.remote / "scripts/install-vps.sh"
        installer.parent.mkdir()
        for marker in ("older fixture", "latest fixture"):
            installer.write_text(f"#!/bin/bash\nprintf '%s\\n' {shlex.quote(marker)}\n")
            self.git("add", str(installer))
            self.git("commit", "--quiet", "-m", marker)
            if marker == "older fixture":
                self.pin = self.git("rev-parse", "HEAD").strip()

        bin_dir = self.root / "bin"
        bin_dir.mkdir()
        git_stub = bin_dir / "git"
        git_stub.write_text(
            "#!/bin/sh\n"
            '[ "$GIT_ALLOW_PROTOCOL" = https ] || exit 7\n'
            f"GIT_ALLOW_PROTOCOL=file exec {shlex.quote(GIT)} -c "
            f"{shlex.quote(f'url.{self.remote.as_uri()}.insteadOf={UPSTREAM}')} \"$@\"\n"
        )
        git_stub.chmod(0o700)
        docker_stub = bin_dir / "docker"
        docker_stub.write_text("#!/bin/sh\nexit 1\n")
        docker_stub.chmod(0o700)
        self.env = dict(os.environ, PATH=f"{bin_dir}:{os.environ['PATH']}")
        self.destination = self.root / "Arma-AI-War-Godmode"

    def git(self, *args):
        return subprocess.check_output(
            [GIT, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.test", *args],
            cwd=self.remote,
            text=True,
        )

    def run_bootstrap(self, *args):
        return subprocess.run(
            ["bash", str(BOOTSTRAP), *args], cwd=self.root, env=self.env,
            capture_output=True, text=True, timeout=20,
        )

    def test_clones_main_and_refuses_to_reuse_checkout(self):
        first = self.run_bootstrap()
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertIn("latest fixture", first.stdout)
        sentinel = self.destination / "operator-settings"
        sentinel.write_text("keep this\n")
        second = self.run_bootstrap()
        self.assertNotEqual(second.returncode, 0)
        self.assertIn("existing reviewed checkout", second.stderr)
        self.assertEqual(sentinel.read_text(), "keep this\n")

    def test_runs_exact_reviewed_commit(self):
        invalid = self.run_bootstrap("--ref", "main")
        self.assertNotEqual(invalid.returncode, 0)
        self.assertFalse(self.destination.exists())
        pinned = self.run_bootstrap("--ref", self.pin.upper())
        self.assertEqual(pinned.returncode, 0, pinned.stderr)
        self.assertIn(self.pin, pinned.stdout)
        self.assertIn("older fixture", pinned.stdout)
        self.assertNotIn("latest fixture", pinned.stdout)

    def test_failed_fetch_and_missing_installer_leave_destination_free(self):
        failed = self.run_bootstrap("--ref", "f" * 40)
        self.assertNotEqual(failed.returncode, 0)
        self.assertFalse(self.destination.exists())
        self.assertFalse(list(self.root.glob(".godmode-bootstrap.*")))

        self.git("rm", "scripts/install-vps.sh")
        self.git("commit", "--quiet", "-m", "fixture without installer")
        failed = self.run_bootstrap()
        self.assertIn("no regular scripts/install-vps.sh", failed.stderr)
        self.assertNotEqual(failed.returncode, 0)
        self.assertFalse(self.destination.exists())
        self.assertFalse(list(self.root.glob(".godmode-bootstrap.*")))

        retried = self.run_bootstrap("--ref", self.pin)
        self.assertEqual(retried.returncode, 0, retried.stderr)
        self.assertIn("older fixture", retried.stdout)

    def test_installer_failure_keeps_checkout_for_operator_retry(self):
        (self.remote / "scripts/install-vps.sh").write_text("#!/bin/bash\nexit 23\n")
        self.git("commit", "--quiet", "-am", "fixture whose installer fails")
        failed = self.run_bootstrap()
        self.assertEqual(failed.returncode, 23, failed.stderr)
        self.assertTrue((self.destination / "scripts/install-vps.sh").is_file())
        self.assertFalse(list(self.root.glob(".godmode-bootstrap.*")))


if __name__ == "__main__":
    unittest.main()
