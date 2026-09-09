#!/bin/bash
# Start site-monitor service
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

pkill -9 -f "uvicorn backend.app.main" 2>/dev/null
sleep 1

export HOST="${HOST:-127.0.0.1}"
export PORT="${PORT:-8888}"
export DATABASE_URL="${DATABASE_URL:-postgresql+asyncpg://monitor:monitor123@localhost:5432/site_monitor}"
export SCREENSHOTS_DIR="${SCREENSHOTS_DIR:-$SCRIPT_DIR/screenshots}"

if [ -d "$SCRIPT_DIR/venv" ]; then
    export PATH="$SCRIPT_DIR/venv/bin:$PATH"
fi

exec uvicorn backend.app.main:app --host "$HOST" --port "$PORT"
