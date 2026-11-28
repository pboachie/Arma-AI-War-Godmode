#!/usr/bin/env python3
"""Build public downloads from an exact Git commit, never the working tree."""

import argparse
import gzip
import hashlib
import io
import json
import re
import subprocess
import tarfile
from pathlib import Path


PROJECT = "Arma-AI-War-Godmode"
UPSTREAM = f"https://github.com/pboachie/{PROJECT}"
TAG_PATTERN = re.compile(r"v(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)")
ADDON_PREFIXES = ("scripts/game/", "configs/godmode/", "docker/")
ADDON_SUFFIXES = (".gproj", ".ent", ".ent.meta", ".layer", ".ebo", ".pak", ".rdb")
ADDON_BUILD_FILES = {"middleware/docker-compose.yml"}
INSTALLER_TEMPLATE = "scripts/bootstrap-release.sh"
VPS_FILES = (
    "LICENSE",
    "compose.yml",
    "middleware/.dockerignore",
    "middleware/.env.example",
    "middleware/Dockerfile",
    "middleware/a2s_client.py",
    "middleware/agents.py",
    "middleware/auth.py",
    "middleware/dashboard.html",
    "middleware/dashboard.js",
    "middleware/everon-map.jpg",
    "middleware/everon-map.NOTICE.md",
    "middleware/rcon_client.py",
    "middleware/requirements.txt",
    "middleware/scaling_engine.py",
    "middleware/server.py",
    "middleware/server_integration.py",
    "middleware/server_manager.py",
    "middleware/side_operations.py",
    "middleware/tactical_objectives.py",
    "scripts/install-vps.sh",
    "scripts/stop.sh",
)


def _git(repo: Path, *args: str) -> bytes:
    return subprocess.check_output(["git", "--no-replace-objects", "-C", str(repo), *args])


def _tree(repo: Path, commit: str) -> dict[str, tuple[str, str]]:
    if not re.fullmatch(r"[a-f0-9]{40}", commit) or _git(
        repo, "rev-parse", "--verify", f"{commit}^{{commit}}"
    ).decode().strip() != commit:
        raise ValueError("Use the full SHA of a Git commit")
    entries = {}
    for entry in _git(repo, "ls-tree", "--full-tree", "-rz", commit).split(b"\0"):
        if entry:
            meta, name = entry.split(b"\t", 1)
            mode, _, oid = meta.decode("ascii").split()
            entries[name.decode("utf-8", errors="surrogateescape")] = (mode, oid)
    return entries


def _read_files(repo: Path, tree: dict[str, tuple[str, str]], paths) -> dict[str, bytes]:
    selected = {}
    for path in sorted(set(paths)):
        mode, oid = tree.get(path, ("", ""))
        if mode not in ("100644", "100755"):
            raise ValueError(f"Missing or non-regular release input: {path}")
        selected[path] = _git(repo, "cat-file", "blob", oid)
    return selected


def _metadata(commit: str, tag: str) -> dict[str, bytes]:
    base = f"{UPSTREAM}/blob/{commit}"
    header = f"# {PROJECT} {tag}\n\nBuilt from commit [`{commit}`]({UPSTREAM}/tree/{commit}).\n\n"
    instructions = f"""This bundle contains the middleware and its Docker Compose installer. It does not install the game or addon.

Run the following command from the extracted directory. It needs Linux with Bash, Docker Engine 28+, and a Docker Compose plugin that supports `up --wait-timeout`.

```bash
bash scripts/install-vps.sh
```

The installer builds the middleware image, creates two private keys, and publishes only to VPS loopback.
See the [dashboard instructions]({base}/README.md#open-the-dashboard) and [operator runbook]({base}/docs/OPERATIONS.md).
Get the addon from [Bohemia's Workshop](https://reforger.armaplatform.com/workshop/68EB7BF2F5940DD9).
"""
    manifest = {"commit": commit, "package": "linux-vps", "tag": tag}
    return {"README.md": (header + instructions).encode(),
            "RELEASE.json": (json.dumps(manifest, sort_keys=True, indent=2) + "\n").encode()}


def _write_tar(path: Path, root: str, files: dict[str, bytes]) -> None:
    with path.open("xb") as raw, gzip.GzipFile(fileobj=raw, filename="", mode="wb", mtime=0) as gz:
        with tarfile.open(fileobj=gz, mode="w", format=tarfile.USTAR_FORMAT) as archive:
            for name, data in sorted(files.items()):
                member = tarfile.TarInfo(f"{root}/{name}")
                member.mode = 0o755 if name.endswith(".sh") else 0o644
                member.size = len(data)
                archive.addfile(member, io.BytesIO(data))


def _digest(path: Path) -> str:
    with path.open("rb") as fh:
        return hashlib.file_digest(fh, "sha256").hexdigest()


def build_packages(repo: Path, commit: str, tag: str, output: Path) -> tuple[Path, Path, Path]:
    if not TAG_PATTERN.fullmatch(tag) or len(tag) > 40:
        raise ValueError("Use a version tag of the form vMAJOR.MINOR.PATCH")
    tree = _tree(repo, commit)
    addon_paths = sorted(
        name for name in tree
        if name.lower() in ADDON_BUILD_FILES
        or name.lower().startswith(ADDON_PREFIXES)
        or name.lower().endswith(ADDON_SUFFIXES)
    )
    if addon_paths:
        raise ValueError("Addon source or build assets in public commit: " + ", ".join(addon_paths))
    vps = _read_files(repo, tree, VPS_FILES) | _metadata(commit, tag)
    installer_code = _read_files(repo, tree, (INSTALLER_TEMPLATE,))[INSTALLER_TEMPLATE]
    if any(installer_code.count(token) != 1 for token in (b"@RELEASE_TAG@", b"@VPS_SHA256@")):
        raise ValueError("Release installer tag and digest placeholders are required exactly once")

    output.mkdir(parents=True, exist_ok=False)
    vps_root = f"{PROJECT}-{tag}-linux-vps"
    tar_path = output / f"{vps_root}.tar.gz"
    _write_tar(tar_path, vps_root, vps)
    installer = output / "install-linux-vps.sh"
    installer.write_bytes(installer_code.replace(b"@RELEASE_TAG@", tag.encode())
                          .replace(b"@VPS_SHA256@", _digest(tar_path).encode()))
    checksums = output / "SHA256SUMS"
    with checksums.open("x", encoding="ascii", newline="\n") as manifest:
        for path in (tar_path, installer):
            manifest.write(f"{_digest(path)}  {path.name}\n")
    return tar_path, installer, checksums


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit", required=True, help="Full SHA to package")
    parser.add_argument("--tag", required=True, help="Version label for the downloads")
    parser.add_argument("--output", required=True, type=Path, help="New directory for release assets")
    args = parser.parse_args()
    try:
        for path in build_packages(Path.cwd(), args.commit, args.tag, args.output):
            print(path.name)
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        parser.exit(1, f"Could not package release: {exc}\n")


if __name__ == "__main__":
    main()
