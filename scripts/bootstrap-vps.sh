#!/usr/bin/env bash
set -euo pipefail
umask 077

die() {
    printf 'Error: %s\n' "$*" >&2
    exit 1
}

if [[ $# -eq 1 && $1 == --help ]]; then
    printf '%s\n' \
        'Usage: bash bootstrap-vps.sh [--ref FULL_COMMIT_SHA]' \
        'Clone the project into ./Arma-AI-War-Godmode and start only its middleware.' \
        'By default, clone main. Pass a reviewed commit SHA to pin the checkout.' \
        'Requires Linux, Git, Docker Engine 28+, Docker Compose v2+ with up --wait-timeout, and enough disk.'
    exit 0
fi

ref=''
if [[ $# -ne 0 ]]; then
    [[ $# -eq 2 && $1 == --ref && $2 =~ ^[0-9a-fA-F]{40}$ ]] \
        || die 'Use --ref with a full 40-character Git commit SHA, or run without arguments.'
    ref=${2,,}
fi

[[ $(uname -s) == Linux ]] || die 'Run this bootstrap on a Linux VPS.'
command -v git >/dev/null || die 'Install Git first.'
command -v docker >/dev/null || die 'Install Docker Engine and the Compose plugin first.'

repo=https://github.com/pboachie/Arma-AI-War-Godmode.git
parent=$(pwd -P)
target="$parent/Arma-AI-War-Godmode"
if [[ -e $target || -L $target ]]; then
    die 'Cannot create ./Arma-AI-War-Godmode. For an existing reviewed checkout, run its scripts/install-vps.sh directly.'
fi
# Clone beside the final directory. A failed download leaves the target free
# for a retry. After a successful clone, keep the checkout if Compose fails.
checkout=$(mktemp -d -- "$parent/.godmode-bootstrap.XXXXXXXX") || die 'Cannot create a temporary checkout here.'
cleanup() { rm -rf -- "$checkout"; }
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

# Use the public HTTPS source without inherited Git redirects or worktree overrides.
git_env=(env -i "PATH=$PATH" GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null \
    GIT_ALLOW_PROTOCOL=https GIT_TERMINAL_PROMPT=0)
for var in HTTP_PROXY HTTPS_PROXY ALL_PROXY NO_PROXY http_proxy https_proxy all_proxy no_proxy \
    SSL_CERT_FILE SSL_CERT_DIR GIT_SSL_CAINFO GIT_SSL_CAPATH; do
    if [[ -v $var ]]; then
        git_env+=("$var=${!var}")
    fi
done
git_https() { "${git_env[@]}" git "$@"; }

if [[ -n $ref ]]; then
    git_https -C "$checkout" init --quiet --template=''
    git_https -C "$checkout" remote add origin "$repo"
    git_https -C "$checkout" fetch --depth 1 --no-tags origin "$ref"
    fetched=$(git_https -C "$checkout" rev-parse --verify 'FETCH_HEAD^{commit}')
    [[ $fetched == "$ref" ]] || die 'The fetched commit did not match the requested SHA.'
    git_https -C "$checkout" checkout --quiet --detach "$ref"
else
    git_https clone --depth 1 --no-tags --single-branch --branch main --template='' -- "$repo" "$checkout"
fi

commit=$(git_https -C "$checkout" rev-parse --verify HEAD)
[[ -d "$checkout/scripts" && ! -L "$checkout/scripts" && -f "$checkout/scripts/install-vps.sh" \
    && ! -L "$checkout/scripts/install-vps.sh" ]] \
    || die 'This commit has no regular scripts/install-vps.sh. Select a revision with the VPS installer.'
if ! mv -T --no-clobber -- "$checkout" "$target" || [[ -e $checkout ]]; then
    die 'Cannot move the checkout into ./Arma-AI-War-Godmode without replacing an existing path.'
fi
trap - EXIT INT TERM
printf 'Cloned %s into %s.\n' "$commit" "$target"
exec bash "$target/scripts/install-vps.sh"
