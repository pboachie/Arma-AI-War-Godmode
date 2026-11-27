#!/usr/bin/env bash
set +x # Do not trace credentials, including when invoked with bash -x.
set -euo pipefail
umask 077

usage() {
    printf '%s\n' \
        'Usage: bash scripts/install-vps.sh [--stop | --adopt-existing-state]' \
        'Run from a checkout you trust. This also works from a pinned checkout.' \
        '--stop stops only this checkout’s middleware. It leaves state intact.' \
        '--adopt-existing-state claims an unowned older volume using your existing private settings.' \
        'Requires Linux, a local Docker Engine 28+ (Unix socket), Docker Compose v2+' \
        'with up --wait support, and enough disk for the image and middleware state.' \
        'This starts only the middleware. It does not install Docker or the game,' \
        'change the firewall, or provision host software.'
}

die() {
    printf 'Error: %s\n' "$*" >&2
    exit 1
}

if [[ $# -eq 1 && $1 == --help ]]; then
    usage
    exit 0
fi
operation=start
case ${1:-} in
    '') [[ $# -eq 0 ]] || die 'Use --help for options.' ;;
    --stop) [[ $# -eq 1 ]] || die 'Use --stop by itself.'; operation=stop ;;
    --adopt-existing-state) [[ $# -eq 1 ]] || die 'Use --adopt-existing-state by itself.'; operation=adopt ;;
    *) die 'Use --help for options.' ;;
esac
[[ $(uname -s) == Linux ]] || die 'Run this installer on a Linux VPS.'

root=$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)
cd -- "$root"
[[ -f compose.yml && -f middleware/Dockerfile ]] || die 'The checkout is missing its middleware Compose files.'
command -v docker >/dev/null || die 'Install Docker Engine 28+ and the Docker Compose plugin first.'

# Keep Docker client paths (including rootless Docker), but never let ambient
# Compose or application variables override middleware/.env. Also never remove
# orphans that happen to share the Compose project name.
docker_env=(env -i "PATH=$PATH" "COMPOSE_REMOVE_ORPHANS=false")
for var in HOME DOCKER_CONFIG XDG_CONFIG_HOME XDG_RUNTIME_DIR DBUS_SESSION_BUS_ADDRESS \
    GNUPGHOME PASSWORD_STORE_DIR SSL_CERT_FILE SSL_CERT_DIR; do
    if [[ -v $var ]]; then
        docker_env+=("$var=${!var}")
    fi
done

docker_target=()
docker_cli() { "${docker_env[@]}" docker "${docker_target[@]}" "$@"; }

# Resolve and pin the Docker target before making changes. A selected Docker
# context has precedence over DOCKER_HOST, just as it does in the Docker CLI.
if [[ -z ${DOCKER_CONTEXT:-} && -n ${DOCKER_HOST:-} ]]; then
    [[ $DOCKER_HOST == unix:///* ]] || die 'DOCKER_HOST must point to a local Unix socket, or be unset.'
    docker_target=(--host "$DOCKER_HOST")
else
    context=${DOCKER_CONTEXT:-}
    if [[ -z $context ]]; then
        context=$(docker_cli context show 2>/dev/null) || die 'Cannot resolve the local Docker context.'
    fi
    endpoint=$(docker_cli context inspect --format '{{(index .Endpoints "docker").Host}}' -- "$context" 2>/dev/null) \
        || die 'Cannot inspect the selected Docker context.'
    [[ $endpoint == unix:///* ]] || die 'Select a local Docker context using a Unix socket.'
    docker_target=(--context "$context")
fi

engine=$(docker_cli version --format '{{.Server.Os}} {{.Server.Version}}' 2>/dev/null) \
    || die 'Cannot reach the local Docker Engine. Check that it is running and your account can use it.'
if [[ ! $engine =~ ^linux[[:space:]]+([0-9]+)\. ]] || (( 10#${BASH_REMATCH[1]} < 28 )); then
    die 'Docker Engine 28+ on Linux is required for loopback-only port publishing.'
fi
compose_version=$(docker_cli compose version --short 2>/dev/null) || die 'Install the Docker Compose plugin.'
if [[ ! $compose_version =~ ^v?([0-9]+)\. ]] || (( 10#${BASH_REMATCH[1]} < 2 )); then
    die 'Docker Compose v2 or later is required.'
fi
compose_help=$(docker_cli compose up --help 2>/dev/null) || die 'Cannot check the Docker Compose plugin.'
[[ $compose_help == *--wait-timeout* ]] || die 'Update Docker Compose to a version that supports up --wait-timeout.'

# The public stack uses one stable project per Docker host. Keep a separate
# ownership marker so removing containers never drops the ownership check.
project=godmode-middleware
state_volume=${project}_middleware-state
owner_volume=${project}-ownership
owner_id=$(printf '%s' "$root" | sha256sum)
owner_id=${owner_id%% *}
existing_dirs=$(docker_cli ps --all --filter "label=com.docker.compose.project=$project" \
    --format '{{.Label "com.docker.compose.project.working_dir"}}' 2>/dev/null) \
    || die 'Cannot check for an existing middleware installation.'
if [[ -n $existing_dirs ]]; then
    while IFS= read -r existing_dir; do
        [[ $existing_dir == "$root" ]] || die 'Middleware already exists from another checkout. Run this installer from its original checkout.'
    done <<< "$existing_dirs"
fi

existing_volumes=$(docker_cli volume ls --format '{{.Name}}' 2>/dev/null) || die 'Cannot check deployment volumes.'
has_volume() { [[ $'\n'"$existing_volumes"$'\n' == *$'\n'"$1"$'\n'* ]]; }
verify_owner() {
    local found
    found=$(docker_cli volume inspect --format '{{index .Labels "io.godmode.checkout-sha"}}' "$owner_volume" 2>/dev/null) \
        || die 'Cannot inspect middleware deployment ownership.'
    [[ $found == "$owner_id" ]] || die 'The middleware state belongs to another checkout. Use its original checkout.'
}
if has_volume "$owner_volume"; then
    verify_owner
elif has_volume "$state_volume" && [[ -z $existing_dirs && $operation == start ]]; then
    die 'Existing middleware state has no checkout owner. From its intended checkout, use --adopt-existing-state with the original private settings.'
fi

env_file="$root/middleware/.env"
compose() {
    local settings_file=$1
    shift
    docker_cli compose --project-directory "$root" -f "$root/compose.yml" \
        --project-name "$project" --env-file "$settings_file" "$@"
}

validate_config() {
    local settings entry operator_key='' game_key='' provider=disabled
    compose "$1" config --quiet >/dev/null 2>&1 \
        || die 'Compose configuration is invalid. Check middleware/.env (or the checkout if this is a fresh install).'
    # Ask Compose to parse dotenv quoting and precedence. Keep the result in
    # shell memory rather than printing it or evaluating it as a shell script.
    settings=$(compose "$1" config --environment 2>/dev/null) || die 'Cannot check the Compose settings.'
    port=8080
    while IFS= read -r entry; do
        case "$entry" in
            GODMODE_API_KEY=*) operator_key=${entry#*=} ;;
            GODMODE_GAME_API_KEY=*) game_key=${entry#*=} ;;
            GODMODE_PORT=?*) port=${entry#*=} ;;
            LLM_PROVIDER=?*) provider=${entry#*=} ;;
        esac
    done <<< "$settings"
    local key_pattern='^[A-Za-z0-9_-]{32,128}$'
    if [[ ! $operator_key =~ $key_pattern || ! $game_key =~ $key_pattern || $operator_key == "$game_key" ]]; then
        die 'Set two distinct random API keys in middleware/.env, each 32–128 characters (letters, digits, underscores, or hyphens).'
    fi
    if [[ ! $port =~ ^[1-9][0-9]{0,4}$ ]] || (( 10#$port > 65535 )); then
        die 'GODMODE_PORT must be an integer from 1 to 65535.'
    fi
    case "$provider" in
        disabled|anthropic|openai|glm|openrouter|ollama) ;;
        *) die 'LLM_PROVIDER must be disabled, anthropic, openai, glm, openrouter, or ollama.' ;;
    esac
}

if [[ $operation == stop && -z $existing_dirs ]]; then
    printf '%s\n' 'No middleware container to stop.'
    exit 0
fi

if [[ -e $env_file || -L $env_file ]]; then
    [[ -f $env_file && ! -L $env_file && -O $env_file && -r $env_file ]] \
        || die 'Use an operator-owned regular middleware/.env file, not a symlink.'
    [[ $(stat -c '%a' -- "$env_file") == 600 ]] || die 'Set existing middleware/.env permissions with chmod 600 middleware/.env.'
    validate_config "$env_file"
else
    if [[ -n $existing_dirs || $operation != start ]] || has_volume "$state_volume"; then
        die 'Restore the deployment’s private middleware/.env before using its state or services.'
    fi
    candidate=$(mktemp "$root/middleware/.env.tmp.XXXXXXXX") || die 'Cannot create a private settings file in middleware/.'
    trap 'rm -f -- "$candidate"' EXIT
    trap 'exit 130' INT
    trap 'exit 143' TERM
    random_key() { od -An -N32 -tx1 /dev/urandom | tr -d '[:space:]'; }
    operator_key=$(random_key)
    game_key=$(random_key)
    while [[ $game_key == "$operator_key" ]]; do game_key=$(random_key); done
    printf '# See middleware/.env.example for optional settings. Keep this file private.\nGODMODE_API_KEY=%s\nGODMODE_GAME_API_KEY=%s\nGODMODE_PORT=8080\nLLM_PROVIDER=disabled\n' \
        "$operator_key" "$game_key" > "$candidate"
    unset operator_key game_key
    validate_config "$candidate"
    # Publish atomically without replacing any file another process put there.
    ln -T -- "$candidate" "$env_file" 2>/dev/null \
        || die 'Cannot create middleware/.env without replacing an existing path. Check the path, then re-run.'
    rm -f -- "$candidate"
    trap - EXIT INT TERM
    printf '%s\n' 'Created private settings in middleware/.env (mode 0600).'
fi

if [[ $operation == stop ]]; then
    compose "$env_file" stop middleware >/dev/null 2>&1 || die 'Could not stop this checkout’s middleware service.'
    printf '%s\n' 'Middleware stopped. State and keys were preserved.'
    exit 0
fi

if ! has_volume "$owner_volume"; then
    docker_cli volume create --label "io.godmode.checkout-sha=$owner_id" "$owner_volume" >/dev/null 2>&1 \
        || die 'Cannot record middleware deployment ownership.'
    # Creation is idempotent and can return another checkout’s marker after a race.
    verify_owner
fi

printf '%s\n' 'Building and starting the loopback-only middleware. The first build can take a few minutes.'
# Do not relay diagnostics that might include raw dotenv values. Build and
# startup status can be inspected separately by the operator.
compose "$env_file" up --build --wait --wait-timeout 120 --no-deps middleware >/dev/null 2>&1 \
    || die 'Middleware did not build or become healthy. Check Docker availability, disk, and whether the port is in use. Inspect the service with docker compose --env-file middleware/.env -f compose.yml ps and logs middleware.'

printf 'Middleware is healthy at http://127.0.0.1:%s/health on this VPS.\n' "$port"
printf 'Dashboard: http://127.0.0.1:%s/dashboard (use an SSH tunnel for remote access).\n' "$port"
printf '%s\n' 'Credentials are in middleware/.env and were not printed.'
