#!/usr/bin/env bash
# ============================================================
#  ATS — диагностика сервера ПЕРЕД обновлением (deploy/audit.sh)
#
#  Только читает: ничего не перезаписывает, не рестартует, не трогает БД
#  (базу открывает в режиме "mode=ro"). Единственная запись в репозитории —
#  `git fetch`, который создаёт/обновляет refs/remotes/*.
#
#  Запуск:            ./deploy/audit.sh                  # или bash /tmp/ats-audit.sh
#  Другой каталог:    ATS_DIR=/srv/ats ./deploy/audit.sh
#  Другая ветка:      ATS_BRANCH=main  ./deploy/audit.sh
#  Сохранить отчёт:   ./deploy/audit.sh | tee /tmp/ats-audit.txt
# ============================================================
set -u
APP="${ATS_DIR:-/opt/ats}"
B="${ATS_BRANCH:-arena/01a0cdee-ats}"
R="${ATS_REMOTE:-origin}"

say()  { printf '\n===== %s =====\n' "$*"; }
sec()  { printf -- '--- %s ---\n' "$*"; }
show() { local t="$1"; shift; printf '    $ %s\n' "$t"; eval "$t" 2>&1 | sed 's/^/      /'; }

say "0. общий статус"
printf '    дата: %s | хост: %s | пользователь: %s | cwd: %s\n' \
  "$(date -Is 2>/dev/null)" "$(hostname 2>/dev/null)" "$(id -un 2>/dev/null)" "$(pwd)"
if [ -d "$APP" ]; then echo "    каталог $APP: существует"; cd "$APP" || exit 1
else echo "    каталог $APP: НЕ НАЙДЕН (укажи верный: ATS_DIR=/путь bash /tmp/ats-audit.sh)"; fi
show 'ls -ld "$APP"; df -h "$APP" 2>/dev/null | tail -1'
show 'python3 -V; git --version; command -v sqlite3 >/dev/null && sqlite3 -version | cut -c1-14; node -v 2>/dev/null; npm -v 2>/dev/null'

[ -d "$APP" ] || { echo; echo "    каталога нет — дальше показывать нечего."; exit 0; }

say "1. git: что это за каталог"
show 'git rev-parse --is-inside-work-tree'
show 'git rev-parse --is-shallow-repository'
sec 'remote'
show 'git remote -v | head -6'
sec 'текущая ветка и последние коммиты'
show 'git rev-parse --abbrev-ref HEAD'
show 'git log --oneline -5'
show 'git log -1 --format="%H / %ci / %an"'
sec 'локальные и remote ветки'
show 'git branch -a -vv | head -30'

say "2. незакоммиченное и «локально-только» (главный риск при обновлении)"
sec 'git status --porcelain'
show 'git status --porcelain | head -30'
printf '    строк в git status: %s\n' "$(git status --porcelain 2>/dev/null | wc -l)"
sec 'stash'
show 'git stash list'
sec 'коммиты, которых нет ни на одном remote — их нельзя потерять'
show 'git log --oneline --branches --not --remotes | head -20'
sec 'изменённые отслеживаемые файлы'
show 'git diff --name-only | head -30'
show 'git diff --shortstat'
sec 'ключевые файлы: есть / нет'
for f in deploy/update.sh deploy/ats.service deploy/ats.env.example deploy/asterisk/pjsip-multicom.conf \
         app/records.py app/telephony.py app/run.py docs/MULTICOM_SIP_CONNECT.md run_ats2.sh frontend/package.json; do
  if [ -e "$f" ]; then printf '    есть   %s\n' "$f"; else printf '    НЕТ    %s\n' "$f"; fi
done

say "3. сверка с $R по ветке $B"
sec 'доступ к репозиторию (403 / "Authentication" / запрос пароля = прав нет)'
show "timeout 25 git ls-remote --heads $R 2>&1 | head -10"
sec 'fetch (запись только в .git)'
show "timeout 180 git fetch $R --prune 2>&1 | tail -2"
show "timeout 180 git fetch $R \"+refs/heads/$B:refs/remotes/$R/$B\" 2>&1 | tail -3"
if git rev-parse --verify --quiet "$R/$B" >/dev/null; then
  sec "вершина $R/$B"
  show "git log -1 --format='%h %ci %an: %s' $R/$B"
  sec 'насколько разошлись'
  printf '    отстаём: %s | опережаем: %s | merge-base: %s\n' \
    "$(git rev-list --count "HEAD..$R/$B" 2>/dev/null)" \
    "$(git rev-list --count "$R/$B..HEAD" 2>/dev/null)" \
    "$(git merge-base HEAD "$R/$B" 2>/dev/null | cut -c1-8)"
  MB="$(git merge-base HEAD "$R/$B" 2>/dev/null || true)"
  [ -n "$MB" ] || MB="$R/$B"
  sec 'что ЕСТЬ на сервере своего (именно это мешает ff-only merge)'
  show "git log --oneline $R/$B..HEAD | head -20"
  show "echo '  файлы, изменённые сервером после точки расхождения:'; git diff --name-only $MB..HEAD | head -30"
  show "git diff --shortstat $MB..HEAD"
  sec 'что приходит из ветки'
  show "git diff --shortstat $MB..$R/$B"
  show "git diff --name-status $MB..$R/$B | awk '(\$1==\"A\"||\$1==\"D\")' | head -30"
  sec 'файлы, правленные с обеих сторон (вот здесь возможны конфликты)'
  show "n=0; for f in \$(comm -12 <(git diff --name-only $MB..HEAD | sort) <(git diff --name-only $MB..$R/$B | sort)); do echo \"    \$f\"; n=1; done; [ \"\$n\" = 1 ] || echo '    (чистых пересечений нет — сервер ничего своего в эти файлы не писал)'"
else
  echo "    !!! $R/$B не видна: клон с --single-branch/--depth либо нет доступа"
fi

say "4. как запущено и где данные"
sec 'systemd'
show 'systemctl list-unit-files 2>/dev/null | grep -E "^(ats|asterisk)"'
show 'systemctl is-active ats 2>&1'
show 'systemctl cat ats 2>/dev/null | grep -E "ExecStart|Environment|WorkingDirectory|^User=|Restart" | head -8'
sec 'env-файлы (ключи маскирую)'
for f in /etc/ats/ats.env "$APP/deploy/ats.env" "$HOME/.ats.env"; do
  if [ -f "$f" ]; then
    printf '    %s:\n' "$f"
    sed -E -e 's/^([A-Z_]*(KEY|SECRET|TOKEN|PASSWORD)[A-Z_]*)=.*/\1=********/' "$f" | sed 's/^/      /' | head -22
  fi
done
DATA="$(sed -nE 's/^ATS_DATA_DIR=["'"'"']?([^"'"'"']+)["'"'"']?$/\1/p' /etc/ats/ats.env 2>/dev/null | tail -1)"
[ -n "$DATA" ] || DATA="$APP/data_v2"
printf '    ATS_DATA_DIR = %s\n' "$DATA"
sec 'каталог данных и база'
show "ls -l $DATA 2>/dev/null | head -12"
show "ls -l $DATA/ats.db $DATA/ats.db-wal 2>/dev/null; echo '  backups:'; ls -lt $DATA/backups 2>/dev/null | head -4"
sec 'процесс, порт, health'
PORT="$(sed -nE 's/^ATS_PORT=["'"'"']?([0-9]+)["'"'"']?$/\1/p' /etc/ats/ats.env 2>/dev/null | tail -1)"; PORT="${PORT:-9124}"
printf '    ATS_PORT = %s\n' "$PORT"
show "ps -eo pid,etime,cmd | grep -E '[a]pp.run|[p]ython3 -m app' | head -5"
show "command -v ss >/dev/null && ss -ltnp 2>/dev/null | grep -E ':($PORT|5060|5038)' | head -6"
show "curl -s -m 5 -o /dev/null -w 'health HTTP %{http_code}\n' http://127.0.0.1:$PORT/api/v2/health"
show "curl -s -m 5 http://127.0.0.1:$PORT/api/v2/health | head -c 260; echo"
sec 'логи'
show "for f in $DATA/logs/ats.log /var/log/ats.log; do [ -f \"\$f\" ] && echo \"  tail -10 \$f:\" && tail -10 \"\$f\"; done"
show "if [ -f /tmp/ats-update.log ]; then echo '  /tmp/ats-update.log (tail -12):'; tail -12 /tmp/ats-update.log; else echo '  /tmp/ats-update.log нет — update.sh здесь ещё не запускался'; fi"

say "5. Asterisk: есть ли почва под Мультиком"
show 'if command -v asterisk >/dev/null; then asterisk -V 2>&1 | head -1; timeout 10 asterisk -rx "core show version" 2>&1 | head -2; timeout 10 asterisk -rx "pjsip show endpoints" 2>&1 | head -5; else echo "asterisk НЕ установлен"; fi'
show 'for f in /etc/asterisk/pjsip.conf /etc/asterisk/sip.conf /etc/asterisk/manager.conf /etc/asterisk/extensions.conf; do if [ -f "$f" ]; then echo "  есть $f:"; grep -n include "$f" | head -3; else echo "  нет $f"; fi; done'
show 'ls -l /var/lib/asterisk/agi-bin/ats_say.py 2>&1 | tail -1; ls -ld /var/spool/asterisk/monitor/ats 2>&1 | tail -1'
show 'command -v espeak-ng >/dev/null && espeak-ng --version 2>&1 | head -1 || echo "espeak-ng НЕ установлен"'
show 'ls /usr/lib/asterisk/modules/codec_g729* 2>/dev/null | head -1 || echo "codec_g729: нет (нормально — хватит alaw/ulaw)"'

say "6. состояние внутри АТС (читаю базу только для чтения)"
python3 - "$DATA/ats.db" <<'PY' 2>&1 | sed 's/^/    /'
import json, os, sqlite3, sys
db = sys.argv[1]
if not os.path.exists(db):
    print("базы %s нет (возможно, ATS_DATA_DIR другой — см. §4)" % db); raise SystemExit
try:
    c = sqlite3.connect("file:%s?mode=ro" % db, uri=True)
except Exception as e:
    print("не открыл базу: %s" % e); raise SystemExit
def one(sql):
    try:
        return c.execute(sql).fetchone()[0]
    except Exception as e:
        return "n/a (%s)" % e
for t in ("contacts", "campaigns", "numbers", "calls", "crm_outbox"):
    print("%-12s %s" % (t, one("select count(*) from %s" % t)))
row = one("select data from settings where id=1")
if isinstance(row, str) and row:
    try:
        s = json.loads(row)
    except Exception:
        s = {}
    print("provider      = %s" % s.get("provider"))
    print("host:port     = %s:%s" % (s.get("host"), s.get("port")))
    pc = s.get("provider_config") or {}
    ami = pc.get("ami") or {}
    print("секция ami    = %s" % ("есть" if ami else "НЕТ"))
    for k in sorted(ami):
        v = str(ami[k])
        if any(x in k.lower() for x in ("secret", "password", "token")):
            v = v[:3] + "…"
        print("   ami.%-16s = %s" % (k, v))
    print("crm включён    = %s" % (pc.get("crm") or {}).get("integration_enabled"))
    rec = pc.get("records") or {}
    print("records        = %s %s" % ("есть" if rec else "НЕТ", dict(list(rec.items())[:3])))
else:
    print("settings id=1 пуста — АТС ещё не настраивалась")
c.close()
PY

say "7. готовность к штатному update.sh"
if [ -f "$APP/deploy/update.sh" ]; then
  if [ -x "$APP/deploy/update.sh" ]; then
    echo "    deploy/update.sh есть и исполняемый → можно: ./deploy/update.sh $B"
  else
    echo "    deploy/update.sh есть, но без +x → chmod +x deploy/update.sh (или bash deploy/update.sh $B)"
  fi
else
  echo "    deploy/update.sh ОТСУТСТВУЕТ → первый проход через бутстрап (docs/DEPLOY.md §3.0)"
fi
show 'if command -v fuser >/dev/null; then fuser -v /tmp/ats-update.lock 2>&1 | head -2; else echo "(fuser нет в системе — проверяю lock-файл: $(ls -l /tmp/ats-update.lock 2>/dev/null || echo файла нет))"; fi'
show 'ls -la "$APP" | head -22'
printf '    владелец каталога: %s | я: %s | ' "$(stat -c %U "$APP" 2>/dev/null)" "$(id -un)"
[ -w "$APP" ] && echo "запись возможна" || echo "НЕТ записи — нужен владелец/через sudo"
printf '\n===== конец отчёта =====\n'
