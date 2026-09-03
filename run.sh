#!/bin/bash
# Start site-monitor service
pkill -9 -f "uvicorn backend.app.main" 2>/dev/null
sleep 1

export DATABASE_URL="postgresql+asyncpg://monitor:monitor123@localhost:5433/site_monitor"
export SCREENSHOTS_DIR="/root/site-monitor/screenshots"
export PATH="/root/site-monitor/venv/bin:$PATH"

cd /root/site-monitor
exec uvicorn backend.app.main:app --host 0.0.0.0 --port 8888
