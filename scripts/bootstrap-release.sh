#!/usr/bin/env bash
# Rendered as install-linux-vps.sh by scripts/package_release.py.
set -euo pipefail
umask 077

tag='@RELEASE_TAG@'
digest='@VPS_SHA256@'
fail() { printf 'Error: %s\n' "$*" >&2; exit 1; }
[[ $(uname -s) == Linux ]] || fail 'Run this installer on Linux.'
[[ $# -eq 0 && $tag =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ && $digest =~ ^[0-9a-f]{64}$ ]] \
    || fail 'Run the published install-linux-vps.sh asset from a GitHub Release.'
command -v curl >/dev/null && command -v docker >/dev/null \
    || fail 'Install curl, Docker Engine, and the Docker Compose plugin first.'

parent=$(pwd -P)
target="$parent/Arma-AI-War-Godmode"
[[ ! -e $target && ! -L $target ]] || fail 'The destination exists. Use its scripts/install-vps.sh or the update runbook.'
stage=$(mktemp -d -- "$parent/.godmode-release.XXXXXXXX")
trap 'rm -rf -- "$stage"' EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
bundle="Arma-AI-War-Godmode-$tag-linux-vps"
asset="$bundle.tar.gz"
curl -q --proto '=https' --proto-redir '=https' -fsSL --output "$stage/$asset" \
    "https://github.com/pboachie/Arma-AI-War-Godmode/releases/download/$tag/$asset"
( cd -- "$stage" && printf '%s  %s\n' "$digest" "$asset" | sha256sum --check --strict - ) \
    || fail 'VPS bundle checksum mismatch. No installer was run.'
tar --extract --gzip --no-same-owner --file "$stage/$asset" --directory "$stage"
[[ -d "$stage/$bundle" && ! -L "$stage/$bundle" && -f "$stage/$bundle/scripts/install-vps.sh" \
    && ! -L "$stage/$bundle/scripts/install-vps.sh" ]] || fail 'The bundle is missing its installer.'
if ! mv -T --no-clobber -- "$stage/$bundle" "$target" || [[ -e "$stage/$bundle" ]]; then
    fail 'Cannot install the release without replacing an existing path.'
fi
printf 'Verified and extracted %s to %s.\n' "$tag" "$target"
bash "$target/scripts/install-vps.sh"
