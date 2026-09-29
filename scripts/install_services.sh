#!/usr/bin/env bash
# Installs capture, clipper and API as systemd user services (Ubuntu): they start with
# the desktop session and restart automatically if they fail.
#
# Usage: scripts/install_services.sh              install (or update) and start
#        scripts/install_services.sh --uninstall  stop and remove
#
# Manage afterwards with:
#   systemctl --user status 'arena-replay-*'
#   systemctl --user restart arena-replay-capture
#   journalctl --user -u arena-replay-clipper -f
set -euo pipefail

cd "$(dirname "$0")/.."
REPO_DIR="$(pwd)"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
UNITS=(arena-replay-capture arena-replay-clipper arena-replay-api)

say() { printf '\033[1m[install]\033[0m %s\n' "$*"; }
die() { printf '\033[31m[install] %s\033[0m\n' "$*" >&2; exit 1; }

[ "$(uname -s)" = Linux ] || die "systemd services are for the Ubuntu machine; on macOS use scripts/run_all.sh"
command -v systemctl >/dev/null || die "systemctl not found"

if [ "${1:-}" = "--uninstall" ]; then
    systemctl --user disable --now "${UNITS[@]}" 2>/dev/null || true
    for unit in "${UNITS[@]}"; do rm -f "$UNIT_DIR/$unit.service"; done
    systemctl --user daemon-reload
    say "services removed (the database container is left running)"
    exit 0
fi
[ -z "${1:-}" ] || die "unknown option: $1"

[ -x .venv/bin/python ] || die "missing .venv: python3.12 -m venv .venv && .venv/bin/pip install -e '.[dev]'"
[ -f .env ] || die "missing .env: cp .env.example .env (and set the password)"
[ -f config.yaml ] || die "missing config.yaml: cp config.example.yaml config.yaml"
case "$REPO_DIR" in *[[:space:]]*) die "the project path must not contain spaces: $REPO_DIR" ;; esac
.venv/bin/python -c "from replay.config import load_settings; load_settings()" \
    || die "invalid configuration (see the error above)"

say "starting the database and applying migrations..."
docker compose up -d --wait db >/dev/null
.venv/bin/alembic upgrade head

say "installing units into $UNIT_DIR"
mkdir -p "$UNIT_DIR"
for unit in "${UNITS[@]}"; do
    sed "s|@REPO_DIR@|$REPO_DIR|g" "deploy/systemd/$unit.service" > "$UNIT_DIR/$unit.service"
done
systemctl --user daemon-reload
systemctl --user enable "${UNITS[@]}"
systemctl --user restart "${UNITS[@]}"

sleep 3
systemctl --user --no-pager --lines=0 status "${UNITS[@]}" || true

if [ "${XDG_SESSION_TYPE:-}" != "x11" ]; then
    say "WARNING: this session is '${XDG_SESSION_TYPE:-unknown}', not x11: the keyboard trigger"
    say "         needs 'Ubuntu on Xorg' (gear icon on the login screen)."
fi
say "done. Test page: http://127.0.0.1:8000/dev (port from config.yaml)"
say "logs: journalctl --user -u 'arena-replay-*' -f   or   tail -F data/logs/*.log"
say "to start without anyone logging in, enable automatic login in Settings > Users."
