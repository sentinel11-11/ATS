#!/usr/bin/env bash
# ATS v2 — безопасное обновление на сервере из этого репозитория.
#
#   ./deploy/update.sh                       # текущая ветка, origin
#   ./deploy/update.sh arena/01a0cdee-ats    # явная ветка
#   ./deploy/update.sh main origin --fast    # без прогона тестов
#   ./deploy/update.sh --rollback            # откат на предыдущую ревизию
#
# Флаги:  --fast        пропустить unittest (быстрое обновление «в поле»)
#         --no-build    не пересобирать React-интерфейс (frontend/)
#         --no-restart  не трогать systemd/процесс (обновить только код)
#
# Порядок: lock → предпроверки → защита runtime-данных → fetch → бэкап БД →
# ff-only merge → (сборка UI) → тесты → рестарт → health-check → автооткат,
# если health не поднялся.
#
# Переменные окружения (по умолчанию всё подбирается сам):
#   ATS_DATA_DIR (data_v2)  ATS_PORT (из таблицы settings)  ATS_SERVICE (ats)
set -uo pipefail

LOCK=/tmp/ats-update.lock
LOG=/tmp/ats-update.log

log()  { printf '\033[1;36m[ats-update]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[ats-update]\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31m[ats-update]\033[0m %s\n' "$*" >&2; exit 1; }

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$APP_DIR" || die "нет каталога $APP_DIR"

# Переменные окружения сервера (ATS_DATA_DIR / ATS_PORT / ATS_SERVICE / ключи):
# /etc/ats/ats.env → <репозиторий>/deploy/ats.env → ~/.ats.env → переменные shell.
_ENV_SRC=""
for _envf in /etc/ats/ats.env "$APP_DIR/deploy/ats.env" "$HOME/.ats.env"; do
  if [ -f "$_envf" ]; then set -a; . "$_envf" 2>/dev/null || true; set +a; _ENV_SRC="$_envf"; break; fi
done

BRANCH=""; REMOTE="origin"; DO_BUILD=1; DO_TESTS=1; DO_RESTART=1; MODE="update"
for a in "$@"; do
  case "$a" in
    --rollback)     MODE="rollback" ;;
    --fast|--no-tests) DO_TESTS=0 ;;
    --no-build)     DO_BUILD=0 ;;
    --no-restart)   DO_RESTART=0 ;;
    -*)             die "неизвестный флаг: $a" ;;
    *) if [ -z "$BRANCH" ]; then BRANCH="$a"; else REMOTE="$a"; fi ;;
  esac
done

command -v git >/dev/null || die "не найден git"
command -v python3 >/dev/null || die "не найден python3"
git rev-parse --git-dir >/dev/null 2>&1 || die "$APP_DIR — не git-репозиторий: обновляйте там, куда клонирован ATS"

DATA_DIR="${ATS_DATA_DIR:-$APP_DIR/data_v2}"
DB_FILE="$DATA_DIR/ats.db"
SERVICE="${ATS_SERVICE:-ats}"
STAMP="$(date -u +%Y%m%d-%H%M%S)"

detect_port() {
  python3 - "$DATA_DIR" <<'PY' 2>/dev/null || true
import json, os, sqlite3, sys
db = os.path.join(sys.argv[1], "ats.db")
try:
    c = sqlite3.connect("file:%s?mode=ro" % db, uri=True)
    row = c.execute("SELECT data FROM settings WHERE id=1").fetchone()
    c.close()
    print(json.loads(row[0]).get("port", 9124) if row else 9124)
except Exception:
    print(9124)
PY
}
PORT="${ATS_PORT:-$(detect_port)}"; PORT="${PORT:-9124}"

health_probe() {
  python3 - "$PORT" <<'PY' 2>/dev/null
import json, sys, urllib.request
try:
    with urllib.request.urlopen("http://127.0.0.1:%s/api/v2/health" % sys.argv[1], timeout=3) as r:
        d = json.loads(r.read().decode("utf-8") or "{}")
    print("ok" if d.get("ok") else "bad:" + json.dumps(d, ensure_ascii=False)[:200])
except Exception as e:
    print("down:%s" % e)
PY
}

start_process() {
  pid="$(cat "$DATA_DIR/ats.pid" 2>/dev/null || true)"
  if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
    log "останавливаю старый процесс ATS (pid $pid)"; kill "$pid" 2>/dev/null || true; sleep 2
    kill -9 "$pid" 2>/dev/null || true
  fi
  mkdir -p "$DATA_DIR/logs"
  log "запуск: python3 -m app.run --host 0.0.0.0 --port $PORT (лог: $DATA_DIR/logs/ats.log)"
  # 9>&- — ОБЯЗАТЕЛЬНО: иначе наследник держит flock из fd 9 и следующее
  # обновление/откат навсегда получит «другое обновление уже выполняется».
  nohup python3 -m app.run --host 0.0.0.0 --port "$PORT" >>"$DATA_DIR/logs/ats.log" 2>&1 9>&- &
  echo $! > "$DATA_DIR/ats.pid"
}

restart_and_check() {
  if [ "$DO_RESTART" != "1" ]; then log "--no-restart: сервис не трогаем (перезапустите сами)"; return 0; fi
  if command -v systemctl >/dev/null 2>&1 && systemctl list-unit-files 2>/dev/null | grep -q "^${SERVICE}\.service"; then
    log "systemctl restart $SERVICE"
    (systemctl restart "$SERVICE" 2>>"$LOG" || sudo -n systemctl restart "$SERVICE" 2>>"$LOG") || \
      warn "рестарт systemd не удался правами — запускаю свой процесс"
    systemctl is-active --quiet "$SERVICE" 2>/dev/null || start_process
  else
    start_process
  fi
  log "жду health /api/v2/health на порту $PORT…"
  for _ in $(seq 1 30); do
    res="$(health_probe)"
    if [ "$res" = "ok" ]; then log "health OK"; return 0; fi
    sleep 2
  done
  warn "health не ответил: ${res:-нет ответа}"
  # Чаще всего приложение честно отказалось стартовать (fail-closed: не выбран
  # провайдер, нет ключа оператора/CRM) — печатаем причину, а не «серый» таймаут.
  if [ -f "$DATA_DIR/logs/ats.log" ]; then
    warn "последние строки $DATA_DIR/logs/ats.log:"
    tail -n 12 "$DATA_DIR/logs/ats.log" | sed 's/^/      /' || true
  elif command -v systemctl >/dev/null 2>&1 && systemctl list-unit-files 2>/dev/null | grep -q "^${SERVICE}\.service"; then
    warn "последние строки journalctl -u $SERVICE:"
    (journalctl -u "$SERVICE" -n 12 --no-pager 2>/dev/null || sudo -n journalctl -u "$SERVICE" -n 12 --no-pager 2>/dev/null) | sed 's/^/      /' || true
  fi
  return 1
}

rollback() {
  local target="${TARGET:-$(cat .deploy-last 2>/dev/null || true)}"
  [ -n "$target" ] || die "нечего откатывать: нет .deploy-last. Укажите вручную: TARGET=<sha> $0 --rollback"
  log "откат на $target"
  git reset --hard "$target" >>"$LOG" 2>&1 || die "git reset не удался (см. $LOG)"
  restart_and_check && log "готово: сервис на $target, health OK" \
    || die "откат применён, но health не OK — смотрите $LOG и $DATA_DIR/logs/ats.log"
}

# ================== РЕЖИМ ОТКАТА ==================
if [ "$MODE" = "rollback" ]; then
  exec 9>"$LOCK"; flock -n 9 || die "другое обновление/откат уже выполняется (lock $LOCK). fuser -v $LOCK — кто держит"
  : >"$LOG" 2>/dev/null || LOG=/dev/null
  rollback
  exit 0
fi

# ================== ОБЫЧНОЕ ОБНОВЛЕНИЕ ==================
exec 9>"$LOCK"; flock -n 9 || die "другое обновление уже выполняется (lock $LOCK) — дождитесь.
Если уверенности нет: fuser -v $LOCK  (покажет pid; держатель — обычно зависший update)"
: >"$LOG" 2>/dev/null || LOG=/dev/null
[ -z "$BRANCH" ] && BRANCH="$(git rev-parse --abbrev-ref HEAD)"
PREV_SHA="$(git rev-parse HEAD)"
log "каталог: $APP_DIR | ветка: $BRANCH | remote: $REMOTE | данные: $DATA_DIR | порт: $PORT"
[ -n "${_ENV_SRC:-}" ] && log "env: $_ENV_SRC"

git fetch "$REMOTE" --prune >>"$LOG" 2>&1 || die "git fetch $REMOTE не удалось (см. $LOG)"
REMOTE_SHA="$(git rev-parse "$REMOTE/$BRANCH" 2>/dev/null || true)"
[ -n "$REMOTE_SHA" ] || die "ветка $REMOTE/$BRANCH не найдена (git branch -r)"
BEHIND="$(git rev-list --count "HEAD..$REMOTE/$BRANCH")"
if [ "$BEHIND" = "0" ] && [ "$PREV_SHA" = "$REMOTE_SHA" ]; then
  log "уже на актуальной ревизии (${PREV_SHA:0:8}) — ничего делать не нужно"
  exit 0
fi
log "обновляем: ${PREV_SHA:0:8} → ${REMOTE_SHA:0:8} ($BEHIND коммитов)"

# --- защита runtime-данных ---
# Боевая база data_v2/ats.db исторически лежит в индексе git: pull мог бы
# перезаписать её «свежей» версией из репозитория. Вешаем skip-worktree —
# git не трогает файл, даже если он меняется в приходящих коммитах.
if [ -d "$DATA_DIR" ]; then
  tracked="$(git ls-files "$DATA_DIR" | tr '\n' ' ')"
  if [ -n "${tracked// /}" ]; then
    n=0
    for f in $tracked; do git update-index --skip-worktree "$f" 2>>"$LOG" && n=$((n+1)); done
    log "защищено от перезаписи runtime-файлов: $n"
  fi
fi
dirty="$(git status --porcelain | grep -v '^?? ' | grep -v ' data_v2/' || true)"
if [ -n "$dirty" ]; then
  warn "незакоммиченные правки на сервере — убираю в stash:"
  echo "$dirty" | sed 's/^/      /'
  git stash push -u -m "ats-update-$STAMP" >>"$LOG" 2>&1 || die "git stash не удался (см. $LOG)"
  warn "вернуть их после: git stash list / git stash pop"
fi

# --- бэкап БД (SQLite backup API — корректный снимок живой базы с WAL) ---
if [ -f "$DB_FILE" ]; then
  BK_DIR="$DATA_DIR/backups"; mkdir -p "$BK_DIR"
  BK="$BK_DIR/ats-before-update-$STAMP.db"
  if python3 - "$DB_FILE" "$BK" <<'PY' >>"$LOG" 2>&1
import sqlite3, sys
a = sqlite3.connect(sys.argv[1]); b = sqlite3.connect(sys.argv[2])
try:
    a.backup(b)
finally:
    b.close(); a.close()
PY
  then log "бэкап БД: $BK"; else warn "бэкап БД не сделан (см. $LOG)"; fi
  # держим 20 последних предобновленческих снимков
  ls -1t "$BK_DIR"/ats-before-update-*.db 2>/dev/null | tail -n +21 | while read -r f; do rm -f "$f"; done
fi

echo "$PREV_SHA" > .deploy-last
git checkout "$BRANCH" >>"$LOG" 2>&1 || die "не переключились на $BRANCH (см. $LOG)"

# Untracked-файлы на сервере, совпадающие с приходящими из репозитория, — частая
# причина «merge aborted» (например, кто-то положил рядом свою копию deploy/update.sh).
# Уводим ровно их (боевую БД не трогаем: она отслеживается и уже под skip-worktree).
incoming="$(git diff --name-only HEAD.."$REMOTE/$BRANCH" 2>/dev/null || true)"
moved=0
for f in $incoming; do
  if [ -f "$f" ] && git status --porcelain -- "$f" 2>/dev/null | grep -q '^?? '; then
    mv "$f" "$f.pre-update-$STAMP" && moved=$((moved+1))
    warn "локальная копия $f отложена в $f.pre-update-$STAMP"
  fi
done
[ "$moved" -gt 0 ] && log "отложено конфликтующих untracked-файлов: $moved (их содержимое — в .pre-update-*)"

git merge --ff-only "$REMOTE/$BRANCH" >>"$LOG" 2>&1 || die "
ff-only merge не прошёл. Последние строки git:
$(tail -5 "$LOG" | sed 's/^/      /')
Типовые причины: (а) на сервере свои коммиты поверх $REMOTE/$BRANCH; (б) незакоммиченные
изменения отслеживаемых файлов. Разбор вручную:
    cd $APP_DIR
    git status --short
    git log --oneline $REMOTE/$BRANCH..HEAD      # что у вас лишнего
    git rebase $REMOTE/$BRANCH                   # или git reset --hard $REMOTE/$BRANCH, если лишнее не нужно
"
log "код: $(git rev-parse --short HEAD) ($BRANCH)"
CHANGED="$(git diff --name-only "$PREV_SHA" HEAD 2>/dev/null || echo '*')"

# --- зависимости Python (ядро v2 живёт на stdlib; requirements.txt — legacy) ---
if echo "$CHANGED" | grep -q '^requirements\.txt$'; then
  log "requirements.txt изменился → python3 -m pip install -r requirements.txt"
  python3 -m pip install -r requirements.txt >>"$LOG" 2>&1 || warn "pip install не прошёл (для ATS v2 это не блокирует запуск)"
fi

# --- сборка интерфейса, если менялся frontend/ ---
if [ "$DO_BUILD" = "1" ] && echo "$CHANGED" | grep -q '^frontend/'; then
  if command -v npm >/dev/null 2>&1; then
    log "пересборка UI: cd frontend && npm ci && npm run build (→ app/ui)"
    ( cd frontend && npm ci --no-audit --no-fund >>"$LOG" 2>&1 && npm run build >>"$LOG" 2>&1 ) \
      || die "сборка UI упала (см. $LOG). Без пересборки: $0 $BRANCH --no-build"
  else
    warn "npm не найден — используем собранный app/ui из репозитория"
  fi
fi

# --- тесты: падаем ДО рестарта, чтобы не ронять живой сервис ---
if [ "$DO_TESTS" = "1" ]; then
  log "прогон тестов: python3 -m unittest discover -s tests"
  python3 -m unittest discover -s tests >>"$LOG" 2>&1 || die "
тесты упали — код не выкладываем на сервис (процесс не тронут).
    tail -60 $LOG
откат кода: $0 --rollback"
  log "тесты OK"
fi

if ! restart_and_check; then
  warn "сервис после обновления не отвечает: tail -80 $LOG ; tail -80 $DATA_DIR/logs/ats.log"
  rollback || warn "откат тоже не поднял сервис — нужен ручной разбор"
  exit 1
fi

log "готово."
echo "    было:   ${PREV_SHA:0:8}"
echo "    стало:  $(git rev-parse --short HEAD) ($BRANCH)"
echo "    пришло коммитов: $BEHIND"
git log --oneline "$PREV_SHA"..HEAD 2>/dev/null | sed 's/^/      /' | head -20
echo "    откат:  $0 --rollback   (вернёт ${PREV_SHA:0:8} + рестарт)"
echo "    лог:    $LOG"
