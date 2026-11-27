import hashlib
import json
import os
import subprocess
import sys
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path

from scripts.package_release import INSTALLER_TEMPLATE, VPS_FILES, WORKBENCH_FILES, build_packages


class PackageReleaseTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.git("init", "--quiet", "--template=")
        self.extra_script = "scripts/Game/GameMode/GM_NewIntegration.c"
        for name in set(VPS_FILES + WORKBENCH_FILES + (self.extra_script,)):
            path = self.repo / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(f"committed fixture for {name}\n".encode())
        (self.repo / INSTALLER_TEMPLATE).write_bytes((Path(__file__).resolve().parents[1] / INSTALLER_TEMPLATE).read_bytes())
        # These inputs must never be packaged, even if accidentally tracked.
        for name in ("middleware/.env", "scripts/Game/GameMode/godmode_api_key.txt",
                     "scripts/Game/GameMode/not-source.ebo", "middleware/campaign_state.json"):
            (self.repo / name).write_bytes(b"private fixture\n")
        self.git("add", "--force", "--", ".")
        self.git("commit", "--quiet", "-m", "fixture")
        self.commit = self.git("rev-parse", "HEAD").decode().strip()

    def git(self, *args, **kwargs):
        return subprocess.check_output([
            "git", "-C", str(self.repo), "-c", "user.name=Fixture", "-c", "user.email=fixture@example.test",
            "-c", "commit.gpgsign=false", "-c", "core.autocrlf=false", *args,
        ], **kwargs)

    def test_only_allowlisted_committed_inputs_are_archived(self):
        (self.repo / "middleware/server.py").write_bytes(b"uncommitted fixture\n")
        (self.repo / "scripts/Game/GameMode/GM_Untracked.c").write_bytes(b"untracked fixture\n")
        (self.repo / ".env").write_bytes(b"untracked credential fixture\n")
        tag = "v3.0.42"
        vps, workbench, installer, sums = build_packages(self.repo, self.commit, tag, self.root / "out")
        with tarfile.open(vps) as archive:
            members = archive.getmembers()
            paths = {item.name.split("/", 1)[1] for item in members}
            self.assertEqual(paths, set(VPS_FILES) | {"README.md", "RELEASE.json"})
            self.assertTrue(all(item.isfile() for item in members))
            self.assertEqual(archive.extractfile(f"{vps.name.removesuffix('.tar.gz')}/middleware/server.py").read(),
                             b"committed fixture for middleware/server.py\n")
            release = json.load(archive.extractfile(f"{vps.name.removesuffix('.tar.gz')}/RELEASE.json"))
        self.assertEqual(release, {"commit": self.commit, "package": "linux-vps", "tag": tag})
        with zipfile.ZipFile(workbench) as archive:
            paths = {name.split("/", 1)[1] for name in archive.namelist()}
            self.assertEqual(paths, set(WORKBENCH_FILES) | {self.extra_script, "README.md", "RELEASE.json"})
            self.assertIsNone(archive.testzip())
        self.assertIn(hashlib.sha256(vps.read_bytes()).hexdigest(), installer.read_text())
        self.assertEqual(sums.read_text().splitlines(), [
            f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}" for path in (vps, workbench, installer)
        ])

    @unittest.skipUnless(sys.platform.startswith("linux"), "release installer uses Linux utilities")
    def test_release_installer_verifies_pinned_archive_before_executing(self):
        (self.repo / "scripts/install-vps.sh").write_text("printf '%s\\n' 'fixture installer ran'\n")
        self.git("add", "scripts/install-vps.sh")
        self.git("commit", "--quiet", "-m", "fixture installer")
        sha = self.git("rev-parse", "HEAD").decode().strip()
        vps, _, installer, _ = build_packages(self.repo, sha, "v3.0.42", self.root / "out")
        bin_dir, cwd = self.root / "bin", self.root / "deployment"
        bin_dir.mkdir()
        cwd.mkdir()
        curl = bin_dir / "curl"
        curl.write_text('''#!/bin/bash
set -eu
while (( $# > 1 )); do
    if [[ $1 == --output ]]; then output=$2; shift; fi
    shift
done
[[ $1 == https://github.com/pboachie/Arma-AI-War-Godmode/releases/download/v3.0.42/Arma-AI-War-Godmode-v3.0.42-linux-vps.tar.gz ]]
cp -- "$FIXTURE_ARCHIVE" "$output"
''')
        docker = bin_dir / "docker"
        docker.write_text("#!/bin/bash\nexit 99\n")
        for tool in (curl, docker):
            tool.chmod(0o700)
        corrupt = self.root / "different.tar.gz"
        corrupt.write_bytes(vps.read_bytes() + b"unverified bytes")

        def bootstrap(archive):
            return subprocess.run(["bash", str(installer)], cwd=cwd, text=True, capture_output=True, timeout=10,
                                  env=dict(os.environ, PATH=f"{bin_dir}:{os.environ['PATH']}", FIXTURE_ARCHIVE=str(archive)))

        rejected = bootstrap(corrupt)
        target = cwd / "Arma-AI-War-Godmode"
        self.assertIn("checksum mismatch", rejected.stderr)
        self.assertNotIn("fixture installer ran", rejected.stdout)
        self.assertFalse(target.exists())
        accepted = bootstrap(vps)
        self.assertEqual(accepted.returncode, 0, accepted.stderr)
        self.assertIn("fixture installer ran", accepted.stdout)
        self.assertEqual(json.loads((target / "RELEASE.json").read_text())["commit"], sha)
        self.assertNotEqual(bootstrap(vps).returncode, 0)

    def test_build_is_repeatable_and_does_not_overwrite_outputs(self):
        first = build_packages(self.repo, self.commit, "v3.0.42", self.root / "first")
        second = build_packages(self.repo, self.commit, "v3.0.42", self.root / "second")
        self.assertEqual([path.read_bytes() for path in first], [path.read_bytes() for path in second])
        with self.assertRaises(FileExistsError):
            build_packages(self.repo, self.commit, "v3.0.42", self.root / "first")

    def test_rejects_wrong_revision_invalid_tag_and_tracked_symlink(self):
        out = self.root / "invalid"
        with self.assertRaisesRegex(ValueError, "version tag"):
            build_packages(self.repo, self.commit, "../v3.0.42", out)
        with self.assertRaisesRegex(ValueError, "full SHA"):
            build_packages(self.repo, "HEAD", "v3.0.42", out)
        target = self.git("hash-object", "-w", "--stdin", input=b".env").decode().strip()
        self.git("update-index", "--cacheinfo", f"120000,{target},middleware/server.py")
        self.git("commit", "--quiet", "-m", "fixture symlink")
        symlink_commit = self.git("rev-parse", "HEAD").decode().strip()
        with self.assertRaisesRegex(ValueError, "non-regular release input: middleware/server.py"):
            build_packages(self.repo, symlink_commit, "v3.0.42", out)
        self.assertFalse(out.exists())
