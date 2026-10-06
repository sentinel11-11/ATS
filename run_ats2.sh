#!/usr/bin/env bash
# ATS v2 — запуск/перезапуск сервиса (Linux). Используется deploy/update.sh.
set -euo pipefail
cd "$(dirname "$0")"
HOST="${ATS_HOST:-0.0.0.0}"
PORT="${ATS_PORT:-9124}"
exec python3 -m app.run --host "$HOST" --port "$PORT" "$@"
