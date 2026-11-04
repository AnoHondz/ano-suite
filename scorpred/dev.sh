#!/usr/bin/env bash
# dev.sh — start Flask API (port 5001) + Vite dev server (port 5173) together.
# Open http://localhost:5173 in Chrome for the React app with live reload.
set -euo pipefail
cd "$(dirname "$0")"

# Install frontend deps if node_modules is missing
if [ ! -d "frontend/node_modules" ]; then
  echo "[dev] Installing frontend dependencies..."
  npm --prefix frontend install
fi

cleanup() {
  echo ""
  echo "[dev] Shutting down..."
  kill "$FLASK_PID" "$VITE_PID" 2>/dev/null || true
  wait "$FLASK_PID" "$VITE_PID" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

# Flask on 5001 (Vite proxies /api/* here)
echo "[dev] Starting Flask on http://localhost:5001 ..."
PORT=5001 python app.py &
FLASK_PID=$!

# Vite on 5173
echo "[dev] Starting Vite on http://localhost:5173 ..."
npm --prefix frontend run dev &
VITE_PID=$!

# Wait briefly then open Chrome
sleep 2
open -a "Google Chrome" http://localhost:5173 2>/dev/null || \
  open http://localhost:5173 2>/dev/null || \
  echo "[dev] Open http://localhost:5173 in your browser."

echo "[dev] Running. Press Ctrl+C to stop."
wait
