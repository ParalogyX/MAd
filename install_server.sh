#!/usr/bin/env bash
set -euo pipefail

REPO_URL="${REPO_URL:-git@github.com:ParalogyX/MAd.git}"
INSTALL_DIR="${INSTALL_DIR:-$(pwd -P)}"
if [ -z "${APP_DIR:-}" ]; then
    if [ -d "$INSTALL_DIR/.git" ]; then
        APP_DIR="$INSTALL_DIR"
    else
        APP_DIR="$INSTALL_DIR/repo"
    fi
fi
DATA_DIR="${DATA_DIR:-$INSTALL_DIR}"
SERVICE_NAME="${SERVICE_NAME:-mad-signals}"
MT5_HOST="${MT5_HOST:-127.0.0.1}"
MT5_PORT="${MT5_PORT:-8001}"
TZ="${TZ:-Europe/Amsterdam}"
SUDO_CMD=()

log() {
    printf '\n==> %s\n' "$*"
}

fail() {
    printf 'ERROR: %s\n' "$*" >&2
    exit 1
}

sudo_cmd() {
    if [ "${EUID:-$(id -u)}" -eq 0 ]; then
        SUDO_CMD=()
        return 0
    fi
    command -v sudo >/dev/null 2>&1 || fail "sudo is required when not running as root."
    SUDO_CMD=(sudo)
}

install_host_dependencies_if_possible() {
    if command -v git >/dev/null 2>&1 \
        && command -v docker >/dev/null 2>&1 \
        && { docker compose version >/dev/null 2>&1 \
            || command -v docker-compose >/dev/null 2>&1; }; then
        return 0
    fi

    if ! command -v apt-get >/dev/null 2>&1; then
        fail "Please install git and Docker first, then rerun this script."
    fi

    sudo_cmd
    log "Installing missing host packages with apt-get"
    "${SUDO_CMD[@]}" apt-get update
    "${SUDO_CMD[@]}" apt-get install -y git openssh-client docker.io

    if ! docker compose version >/dev/null 2>&1 \
        && ! command -v docker-compose >/dev/null 2>&1; then
        "${SUDO_CMD[@]}" apt-get install -y docker-compose-plugin \
            || "${SUDO_CMD[@]}" apt-get install -y docker-compose
    fi

    if command -v systemctl >/dev/null 2>&1; then
        "${SUDO_CMD[@]}" systemctl enable --now docker || true
    fi
}

detect_docker_command() {
    if docker info >/dev/null 2>&1; then
        DOCKER_CMD=(docker)
        return 0
    fi

    if [ "${EUID:-$(id -u)}" -ne 0 ] \
        && command -v sudo >/dev/null 2>&1 \
        && sudo docker info >/dev/null 2>&1; then
        DOCKER_CMD=(sudo docker)
        return 0
    fi

    fail "Docker is not running or this user cannot access it."
}

detect_compose_command() {
    if "${DOCKER_CMD[@]}" compose version >/dev/null 2>&1; then
        COMPOSE_CMD=("${DOCKER_CMD[@]}" compose)
        return 0
    fi

    if command -v docker-compose >/dev/null 2>&1; then
        if [ "${DOCKER_CMD[0]}" = "sudo" ]; then
            COMPOSE_CMD=(sudo docker-compose)
        else
            COMPOSE_CMD=(docker-compose)
        fi
        return 0
    fi

    fail "Docker Compose is not available."
}

clone_or_update_repository() {
    if [ -d "$APP_DIR/.git" ]; then
        log "Updating existing repository in $APP_DIR from main"
        git -C "$APP_DIR" fetch origin main
        git -C "$APP_DIR" checkout main
        git -C "$APP_DIR" pull --ff-only origin main
        return 0
    fi

    if [ -e "$APP_DIR" ]; then
        fail "$APP_DIR already exists but is not a Git repository."
    fi

    log "Cloning repository into $APP_DIR"
    git clone --branch main --single-branch "$REPO_URL" "$APP_DIR"
}

prepare_runtime_directories() {
    log "Preparing external runtime files in $DATA_DIR"
    mkdir -p "$DATA_DIR/logs"
    mkdir -p "$DATA_DIR/Best signals"
    mkdir -p "$DATA_DIR/Trade plans"
    mkdir -p "$DATA_DIR/Results"
}

write_compose_env_file() {
    log "Writing Docker Compose environment file"
    cat > "$APP_DIR/.env" <<EOF
M_AD_DATA_DIR=$DATA_DIR
MT5_HOST=$MT5_HOST
MT5_PORT=$MT5_PORT
TZ=$TZ
EOF
}

main() {
    mkdir -p "$INSTALL_DIR"
    install_host_dependencies_if_possible
    detect_docker_command
    detect_compose_command
    clone_or_update_repository
    prepare_runtime_directories

    cd "$APP_DIR"
    export M_AD_DATA_DIR="$DATA_DIR"
    export MT5_HOST
    export MT5_PORT
    export TZ
    write_compose_env_file

    log "Building Docker image"
    "${COMPOSE_CMD[@]}" build

    log "Starting $SERVICE_NAME container"
    "${COMPOSE_CMD[@]}" up -d

    log "Deployment complete"
    printf 'Repository directory: %s\n' "$APP_DIR"
    printf 'External runtime directory: %s\n' "$DATA_DIR"
    printf 'Logs: %s/logs\n' "$DATA_DIR"
    printf 'Best signals: %s/Best signals\n' "$DATA_DIR"
    printf 'Trade plans: %s/Trade plans\n' "$DATA_DIR"
    printf 'Results: %s/Results\n' "$DATA_DIR"
    printf 'Editable session rules: %s/session_rules.json\n' "$DATA_DIR"
    printf 'MT5 bridge default: %s:%s\n' "$MT5_HOST" "$MT5_PORT"
    printf 'View logs with: cd %s && %s logs -f %s\n' \
        "$APP_DIR" "${COMPOSE_CMD[*]}" "$SERVICE_NAME"
    printf 'Attach console with: cd %s && %s attach %s\n' \
        "$APP_DIR" "${DOCKER_CMD[*]}" "$SERVICE_NAME"
    printf "Detach from attach mode with Ctrl-p then Ctrl-q.\n"
}

main "$@"
