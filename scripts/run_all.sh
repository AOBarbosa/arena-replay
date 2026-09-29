#!/usr/bin/env bash
# Runs the whole system in one terminal: database, migrations, capture, clipper and API.
# If any service exits, everything is stopped. Ctrl+C stops everything cleanly.
#
# Usage: scripts/run_all.sh [--fake-camera] [--stdin]
#   --fake-camera  also start scripts/fake_camera.sh for every court in config.yaml
#   --stdin        trigger from this terminal (Enter or a court id) instead of the keyboard
#
# Logs: this terminal and data/logs/<service>.log. For unattended operation with
# automatic restarts, use scripts/install_services.sh (systemd) instead.
set -euo pipefail

cd "$(dirname "$0")/.."

FAKE_CAMERA=0
CLIPPER_STDIN=0
for arg in "$@"; do
    case "$arg" in
        --fake-camera) FAKE_CAMERA=1 ;;
        --stdin) CLIPPER_STDIN=1 ;;
        -h|--help) sed -n '2,11p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "unknown option: $arg (see --help)" >&2; exit 2 ;;
    esac
done

PY=.venv/bin/python
say() { printf '\033[1m[run_all]\033[0m %s\n' "$*"; }
die() { printf '\033[31m[run_all] %s\033[0m\n' "$*" >&2; exit 1; }

# --- checks --------------------------------------------------------------------------

[ -x "$PY" ] || die "missing .venv: python3.12 -m venv .venv && .venv/bin/pip install -e '.[dev]'"
[ -f .env ] || die "missing .env: cp .env.example .env (and set the password)"
[ -f "${REPLAY_CONFIG:-config.yaml}" ] || die "missing config: cp config.example.yaml config.yaml"
command -v ffmpeg >/dev/null || die "ffmpeg not found"
command -v docker >/dev/null || die "docker not found"
if command -v systemctl >/dev/null \
    && systemctl --user is-active --quiet arena-replay-capture 2>/dev/null; then
    die "the services are already running under systemd; stop them first:
    systemctl --user stop arena-replay-capture arena-replay-clipper arena-replay-api"
fi

# Values from config.yaml (also validates it)
read -r API_URL COURTS < <("$PY" - <<'EOF'
from replay.config import load_app_config
c = load_app_config()
host = "127.0.0.1" if c.api.host in ("0.0.0.0", "::") else c.api.host
print(f"http://{host}:{c.api.port}", ",".join(court.id for court in c.courts))
EOF
) || die "invalid config (see the error above)"

# --- database ------------------------------------------------------------------------

say "starting the database..."
docker compose up -d --wait db >/dev/null
say "applying migrations..."
.venv/bin/alembic upgrade head 2>&1 | grep -v "^INFO  \[alembic.runtime.migration\] Will assume" || true

# --- services ------------------------------------------------------------------------

PIDS=()
NAMES=()

started() {
    PIDS+=("$1")
    NAMES+=("$2")
    say "started $2 (pid $1)"
}

stop_all() {
    trap '' INT TERM
    say "stopping..."
    # Ctrl+C already reached every process in this terminal; give them a moment
    for _ in 1 2 3; do
        alive=0
        for pid in ${PIDS[@]+"${PIDS[@]}"}; do kill -0 "$pid" 2>/dev/null && alive=1; done
        [ "$alive" = 0 ] && break
        sleep 1
    done
    for pid in ${PIDS[@]+"${PIDS[@]}"}; do kill -TERM "$pid" 2>/dev/null || true; done
    for _ in $(seq 1 15); do
        alive=0
        for pid in ${PIDS[@]+"${PIDS[@]}"}; do kill -0 "$pid" 2>/dev/null && alive=1; done
        [ "$alive" = 0 ] && break
        sleep 1
    done
    for pid in ${PIDS[@]+"${PIDS[@]}"}; do kill -KILL "$pid" 2>/dev/null || true; done
    say "stopped"
}

trap 'stop_all; exit 0' INT TERM

if [ "$FAKE_CAMERA" = 1 ]; then
    for court in ${COURTS//,/ }; do
        scripts/fake_camera.sh "$court" &
        started $! "fake-camera-$court"
    done
    sleep 3
fi

"$PY" -m replay.capture &
started $! capture

if [ "$CLIPPER_STDIN" = 1 ]; then
    "$PY" -m replay.clipper --stdin <&0 &
else
    "$PY" -m replay.clipper &
fi
started $! clipper

"$PY" -m replay.api &
started $! api

# Wait for the API before announcing the URLs
for _ in $(seq 1 30); do
    "$PY" -c "import sys, urllib.request; urllib.request.urlopen(sys.argv[1], timeout=1)" \
        "$API_URL/api/v1/health" 2>/dev/null && break
    sleep 0.5
done
say "ready — test page: $API_URL/dev · status: $API_URL/api/v1/status · logs: data/logs/"
say "Ctrl+C to stop"

# --- supervise -----------------------------------------------------------------------

while true; do
    for i in "${!PIDS[@]}"; do
        if ! kill -0 "${PIDS[$i]}" 2>/dev/null; then
            code=0
            wait "${PIDS[$i]}" || code=$?
            printf '\033[31m[run_all] %s exited (code %s); stopping everything\033[0m\n' \
                "${NAMES[$i]}" "$code" >&2
            stop_all
            exit 1
        fi
    done
    sleep 1
done
