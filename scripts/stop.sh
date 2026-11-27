#!/usr/bin/env bash
# Stops only the middleware stack from this checkout, without deleting state.
set -euo pipefail
root_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)
exec bash "$root_dir/scripts/install-vps.sh" --stop
