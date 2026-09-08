#!/bin/bash
set -e

# Ensure full PATH for cron
export PATH="/root/site-monitor/venv/bin:/usr/local/bin:/usr/bin:/bin:$PATH"

PROJECT_DIR="/root/site-monitor"
cd "$PROJECT_DIR"

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

# Rebuild frontend if frontend files changed
if echo "$DIFF_FILES" | grep -q "^frontend/"; then
    echo "Frontend changes detected, rebuilding frontend..."
    cd frontend
    npm install --silent
    npm run build
    cd ..
fi

# Restart site-monitor service
echo "Restarting site-monitor service..."
systemctl restart site-monitor

echo "[$(date '+%Y-%m-%d %H:%M:%S')] Deployment completed successfully!"
