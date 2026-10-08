#!/usr/bin/env bash
# 本地一键启动：API(8000) + Web(3000)，不依赖 Docker
set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

if [ ! -f "$ROOT/.env" ]; then
  cp "$ROOT/.env.example" "$ROOT/.env"
  echo "[dev] 已复制 .env.example -> .env（无 AK 将走 DEMO_MODE 快照）"
fi

if [ ! -d "$ROOT/apps/api/.venv" ]; then
  python -m venv "$ROOT/apps/api/.venv"
fi
VENV_PY="$ROOT/apps/api/.venv/bin/python"
"$VENV_PY" -m pip install -q -r "$ROOT/apps/api/requirements.txt"
"$VENV_PY" -m pip install -q -e "$ROOT/packages/geo"

if [ ! -d "$ROOT/apps/web/node_modules" ]; then
  (cd "$ROOT/apps/web" && npm install)
fi

(cd "$ROOT/apps/api" && "$VENV_PY" -m uvicorn app.main:app --reload --port 8000) &
API_PID=$!
trap 'kill $API_PID 2>/dev/null' EXIT
(cd "$ROOT/apps/web" && npm run dev)
