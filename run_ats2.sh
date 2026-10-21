#!/usr/bin/env bash
# ATS v2 — запуск/перезапуск сервиса (Linux). Используется deploy/update.sh.
set -euo pipefail
cd "$(dirname "$0")"
# loopback по умолчанию: наружу — только через reverse-proxy с TLS (docs/DEPLOY.md §6)
HOST="${ATS_HOST:-127.0.0.1}"
PORT="${ATS_PORT:-9124}"
exec python3 -m app.run --host "$HOST" --port "$PORT" "$@"
