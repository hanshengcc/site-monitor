#!/bin/bash
# Quick start script for development (without Docker)
set -e

echo "=== Site Monitor - Dev Start ==="

# 1. Check dependencies
command -v python3 >/dev/null || { echo "Python3 required"; exit 1; }
command -v node >/dev/null || { echo "Node.js required"; exit 1; }

# 2. Install Python deps
echo "[1/4] Installing Python dependencies..."
pip install -r requirements.txt -q

# 3. Install frontend deps & build
echo "[2/4] Building frontend..."
cd frontend
npm install --silent
npm run build
cd ..

# 4. Check PostgreSQL
echo "[3/4] Checking PostgreSQL..."
if ! docker ps | grep -q site-monitor-db; then
    echo "Starting PostgreSQL via Docker..."
    docker run -d --name site-monitor-db \
        -e POSTGRES_DB=site_monitor \
        -e POSTGRES_USER=monitor \
        -e POSTGRES_PASSWORD=monitor123 \
        -p 5432:5432 \
        -v "$(pwd)/init.sql:/docker-entrypoint-initdb.d/init.sql" \
        postgres:16-alpine
    echo "Waiting for PostgreSQL..."
    sleep 5
fi

# 5. Start app
echo "[4/4] Starting application..."
export DATABASE_URL="postgresql+asyncpg://monitor:monitor123@localhost:5432/site_monitor"
export SCREENSHOTS_DIR="./screenshots"

uvicorn backend.app.main:app --host 0.0.0.0 --port 8080 --reload
