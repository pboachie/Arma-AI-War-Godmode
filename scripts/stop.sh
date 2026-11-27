#!/usr/bin/env bash
# Stops only the middleware stack from this checkout, without deleting state.
set -euo pipefail
root_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)
exec docker compose --project-directory "$root_dir" --file "$root_dir/compose.yml" \
    --env-file "$root_dir/middleware/.env" stop
