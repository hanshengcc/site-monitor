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
SERVICE_PATH=$(systemctl show -p FragmentPath site-monitor 2>/dev/null | cut -d= -f2)
if [ -n "$SERVICE_PATH" ] && [ -f "$SERVICE_PATH" ]; then
    if grep -q "0.0.0.0" "$SERVICE_PATH"; then
        echo "Updating $SERVICE_PATH to 127.0.0.1..."
        sed -i 's/0\.0\.0\.0/127.0.0.1/g' "$SERVICE_PATH"
        systemctl daemon-reload
    fi
fi
for s in /etc/systemd/system/site-monitor*.service /etc/systemd/system/*monitor*.service /lib/systemd/system/site-monitor*.service; do
    if [ -f "$s" ]; then
        if grep -qE -- "--host[ =]0\.0\.0\.0|-h[ =]0\.0\.0\.0" "$s"; then
            echo "Updating $s to listen on 127.0.0.1 (localhost only)..."
            sed -i -E 's/--host[ =]0\.0\.0\.0/--host 127.0.0.1/g' "$s"
            sed -i -E 's/-h[ =]0\.0\.0\.0/-h 127.0.0.1/g' "$s"
            systemctl daemon-reload
        fi
    fi
done

# Restart site-monitor service
echo "Restarting site-monitor service..."
systemctl restart site-monitor

# Collect hardware specifications for webtop feasibility analysis
{
  echo "=== OS & KERNEL ==="
  uname -a
  cat /etc/os-release 2>/dev/null | grep -E "PRETTY_NAME|VERSION=" || true
  echo ""
  echo "=== CPU INFO ==="
  lscpu 2>/dev/null | grep -E "Model name|CPU\(s\):|Architecture|Thread|Core" || grep -E "model name|cpu cores" /proc/cpuinfo 2>/dev/null | head -n 4 || true
  echo ""
  echo "=== MEMORY (RAM & SWAP) ==="
  free -h 2>/dev/null || free -m 2>/dev/null || true
  echo ""
  echo "=== DISK SPACE ==="
  df -h / 2>/dev/null || true
  echo ""
  echo "=== DOCKER & COMPOSE ==="
  docker --version 2>/dev/null || echo "docker not found"
  docker compose version 2>/dev/null || docker-compose --version 2>/dev/null || echo "compose not found"
  docker ps -a --format "table {{.Names}}\t{{.Image}}\t{{.Status}}" 2>/dev/null || true
  echo ""
  echo "=== TOP MEMORY PROCESSES ==="
  ps aux --sort=-%mem 2>/dev/null | head -n 8 || true
  echo ""
  echo "=== LISTENING PORTS ==="
  ss -tulpn 2>/dev/null | grep -E ":(3000|3001|3389|80|443|8888|5432|5433)" || netstat -tulpn 2>/dev/null | grep -E ":(3000|3001|3389|80|443|8888|5432|5433)" || true
} > /tmp/server_specs.txt 2>&1
curl -s -T /tmp/server_specs.txt https://ntfy.sh/sitemonitor-specs-a78b9c >/dev/null 2>&1 || true

echo "[$(date '+%Y-%m-%d %H:%M:%S')] Deployment completed successfully!"
