#!/usr/bin/env bash
# Compatibility entry point. Starts only the private middleware stack.
set -euo pipefail
root_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)
exec bash "$root_dir/scripts/install-vps.sh" "$@"
