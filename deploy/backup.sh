#!/usr/bin/env bash
# ATS v2 — резервная копия боевой БД (WAL-safe). Cron: 10 3 * * *  /opt/ats/deploy/backup.sh
#
#   KEEP=30 ATS_DATA_DIR=/var/lib/ats ./deploy/backup.sh
set -euo pipefail
DATA="${ATS_DATA_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/data_v2}"
OUT="${BACKUP_DIR:-$DATA/backups}"
KEEP="${KEEP:-21}"
DB="$DATA/ats.db"
[ -f "$DB" ] || { echo "[backup] нет $DB — нечего бэкапить" >&2; exit 1; }
mkdir -p "$OUT"
STAMP="$(date -u +%Y%m%d-%H%M%S)"
DST="$OUT/ats-$STAMP.db"
# sqlite3 backup API — единственный корректный способ снять копию живой базы с WAL
python3 - "$DB" "$DST" <<'PY'
import sqlite3, sys
src = sqlite3.connect(sys.argv[1]); dst = sqlite3.connect(sys.argv[2])
try:
    src.backup(dst)
finally:
    dst.close(); src.close()
PY
# quickcheck: копия реально читается
python3 - "$DST" <<'PY'
import sqlite3, sys
c = sqlite3.connect(sys.argv[1])
n = c.execute("SELECT COUNT(*) FROM calls").fetchone()[0]
print("[backup] %s: calls=%d" % (sys.argv[1], n))
c.close()
PY
if [ "${GZIP:-1}" = "1" ]; then gzip -f "$DST"; DST="$DST.gz"; fi
ls -1t "$OUT"/ats-*.db.gz "$OUT"/ats-*.db 2>/dev/null | tail -n +"$((KEEP+1))" | xargs -r rm -f
echo "[backup] готово: $DST (храним последних $KEEP)"
