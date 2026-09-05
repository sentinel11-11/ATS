#!/usr/bin/env bash
# ATS v2 — запуск (Linux/macOS). Пример: ./run_ats2.sh --port 9124
cd "$(dirname "$0")"
export ATS_DATA_DIR="${ATS_DATA_DIR:-$PWD/data_v2}"
exec python3 -m app.run "$@"
