#!/bin/bash
set -e

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_DIR"

# Ensure full PATH including project venv
export PATH="$PROJECT_DIR/venv/bin:/usr/local/bin:/usr/bin:/bin:$PATH"

# 1. Fetch remote changes
git fetch origin main >/dev/null 2>&1

LOCAL=$(git rev-parse HEAD)
REMOTE=$(git rev-parse origin/main)

if [ "$LOCAL" = "$REMOTE" ]; then
    exit 0
fi

echo "[$(date '+%Y-%m-%d %H:%M:%S')] New changes detected: $LOCAL -> $REMOTE. Starting deployment..."

# Check which files changed before pulling
DIFF_FILES=$(git diff --name-only HEAD origin/main)

# Pull latest code
git pull origin main

# Update python deps if requirements.txt changed
if echo "$DIFF_FILES" | grep -q "requirements.txt"; then
    echo "Updating Python dependencies..."
    source venv/bin/activate
    pip install -r requirements.txt -q
fi

# Apply DB schema changes if init.sql changed
if echo "$DIFF_FILES" | grep -q "init.sql"; then
    echo "Applying database updates from init.sql..."
    if docker ps 2>/dev/null | grep -q "site-monitor-db"; then
        docker exec -i site-monitor-db psql -U monitor -d site_monitor < init.sql 2>&1 || true
    elif command -v psql >/dev/null 2>&1; then
        PGPASSWORD=monitor123 psql -h localhost -p 5433 -U monitor -d site_monitor < init.sql 2>&1 || \
        PGPASSWORD=monitor123 psql -h localhost -p 5432 -U monitor -d site_monitor < init.sql 2>&1 || true
    fi
fi

# Rebuild frontend if frontend files changed
if echo "$DIFF_FILES" | grep -q "^frontend/"; then
    echo "Frontend changes detected, rebuilding frontend..."
    cd frontend
    npm install --silent
    npm run build
    cd ..
fi

# Ensure systemd service listens only on localhost (127.0.0.1)
SERVICE_FILE="/etc/systemd/system/site-monitor.service"
if [ -f "$SERVICE_FILE" ]; then
    if grep -q -- "--host 0.0.0.0" "$SERVICE_FILE"; then
        echo "Updating $SERVICE_FILE to listen on 127.0.0.1 (localhost only)..."
        sed -i 's/--host 0.0.0.0/--host 127.0.0.1/g' "$SERVICE_FILE"
        systemctl daemon-reload
    fi
fi

# Restart site-monitor service
echo "Restarting site-monitor service..."
systemctl restart site-monitor

echo "[$(date '+%Y-%m-%d %H:%M:%S')] Deployment completed successfully!"
