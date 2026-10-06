#!/usr/bin/env bash
# ============================================================
#  Быстрая диагностика сервера перед обновлением ATS (одна вставка).
#  Только читает: единственная запись в репозиторий — `git fetch` (правки только
#  в .git/refs/remotes/*). Ничего не рестартует и не перематывает.
#
#  Запуск:              bash /tmp/ats-check.sh
#  Другой каталог:      ATS_DIR=/srv/ats bash /tmp/ats-check.sh
#  Другая ветка:        ATS_BRANCH=arena/01a0cdee-ats bash /tmp/ats-check.sh
#  Каталог с данными:   ATS_DATA=/var/lib/ats bash /tmp/ats-check.sh
#  Полный вариант:      ./deploy/audit.sh   (+ содержимое настроек, логи, lock)
# ============================================================
set -u
A="${ATS_DIR:-/opt/ats}"; B="${ATS_BRANCH:-arena/01a0cdee-ats}"; R="${ATS_REMOTE:-origin}"
[ -d "$A" ] || { echo "НЕТ каталога $A — скажи, где лежит код, и повтори: ATS_DIR=/путь bash /tmp/ats-check.sh"; exit 1; }
cd "$A" || exit 1

# git ищет репозиторий ТОЛЬКО вверх по дереву. Если проект лежит не в $A, а, скажем,
# в $A/app (частый случай, когда клон положили в подкаталог), из $A он выглядит как
# «не git». Ищем клон и вниз — на один-два уровня.
if ! git -C "$A" rev-parse --git-dir >/dev/null 2>&1; then
  for d in "$A"/* "$A"/*/*; do
    [ -d "$d/.git" ] || continue
    echo "подсказка: git-клон найден глубже: $d (запускаю от него; у тебя, видимо, WorkingDirectory именно там)"
    A="$d"; cd "$A" || exit 1
    break
  done
fi
h(){ printf '\n===== %s =====\n' "$*"; }
p(){ printf '  %s\n' "$*"; }
r(){ printf '  $ %s\n' "$*"; eval "$*" 2>&1 | sed 's/^/    /'; }
# команда только если перед нами git-репозиторий (иначе git печатает простыню usage)
G=""; git rev-parse --git-dir >/dev/null 2>&1 || G=1
g(){ if [ -n "$G" ]; then printf '  $ %s\n' "$*"; echo "    (пропущено: это не git-репозиторий)"; else r "$*"; fi; }
PYV="$(command -v python3 || echo python3)"
VPY="$A/app/venv/bin/python"; [ -x "$VPY" ] || VPY="$A/venv/bin/python"; [ -x "$VPY" ] || VPY="$PYV"

h "1. что за копия лежит на сервере"
r 'pwd; ls -ld .; id -un'
if [ -n "$G" ]; then
  p "это НЕ git-клон: код сюда принесли файлами (scp/rsync/архив). Обновлять так, как"
  p "описано в docs/DEPLOY.md §3.0 «не git-клон»: клон рядом + перенос кода, данные не трогаем."
  r 'ls -la "$A" | head -25'
  r 'find . -maxdepth 2 -not -path "./app/venv*" -not -path "./.git*" | sort | head -40'
  r 'echo "  всего файлов (без venv): $(find . -type f -not -path "./app/venv/*" | wc -l)"; du -sh "$A" 2>/dev/null | tail -1'
  r 'echo "  файлы, изменённые позже остальных (кто-то правил руками):"; find . -type f -not -path "./app/venv/*" -printf "%TY-%Tm-%Td %TH:%TM %10s  %p\n" 2>/dev/null | sort -r | head -8'
  r 'echo "  CRLF (если Windows-переносы — хэши не совпадут с GitHub):"; file "$A/app/run.py" 2>/dev/null | cut -c1-120'
  r 'echo "  хэши python-кода для сверки с ревизиями:"; find . -name "*.py" -not -path "./app/venv/*" | sort | xargs md5sum 2>/dev/null | head -40'
else
  r 'git remote -v | head -2'
  r 'git rev-parse --abbrev-ref HEAD; git log --oneline -3; git log -1 --format="HEAD: %h %ci %an"'
  r 'git branch -a -vv | head -12'
fi

h "2. незакоммиченное и локально-только"
g 'git status --porcelain | head -20'
g 'printf "  всего строк в git status: %s\n" "$(git status --porcelain | wc -l)"'
g 'git stash list | head -5'
g 'git diff --shortstat'
g 'git diff --name-only | head -20'
g 'echo "  свои коммиты, которых нет на GitHub:"; git log --oneline --branches --not --remotes | head -10'

h "3. расхождение с $R/$B"
if [ -n "$G" ]; then
  p "нечего сверять: git-меток на сервере нет. Ориентир — хэши файлов из §1"
  p "и дата latest-файла; дальше я сравню их с ревизиями из GitHub сам."
else
  r "GIT_TERMINAL_PROMPT=0 timeout 60 git fetch $R --prune < /dev/null 2>&1 | tail -2"
  r "GIT_TERMINAL_PROMPT=0 timeout 60 git fetch $R \"+refs/heads/$B:refs/remotes/$R/$B\" < /dev/null 2>&1 | tail -2"
  r "GIT_TERMINAL_PROMPT=0 timeout 30 git ls-remote --heads $R < /dev/null 2>&1 | head -8"
  printf '    отстаём=%s  опережаем=%s  merge-base=%s\n' \
    "$(timeout 30 git rev-list --count "HEAD..$R/$B" 2>/dev/null)" \
    "$(timeout 30 git rev-list --count "$R/$B..HEAD" 2>/dev/null)" \
    "$(timeout 30 git merge-base HEAD "$R/$B" 2>/dev/null | cut -c1-8)"
  g "echo '  свои коммиты поверх $R/$B:'; git log --oneline $R/$B..HEAD | head -10"
  g "MB=$(timeout 30 git merge-base HEAD $R/$B 2>/dev/null); echo '  файлы, которые сервер правил сам:'; git diff --name-only \${MB:-$R/$B}..HEAD | head -25"
fi
r "echo '  доступ к репозиторию с сервера (приватный — нужен ключ/логин):'; GIT_TERMINAL_PROMPT=0 timeout 25 git ls-remote --heads https://github.com/sentinel11-11/ATS.git < /dev/null 2>&1 | head -6"

h "4. ключевые файлы: пришли / не пришли"
for f in deploy/update.sh deploy/ats.service deploy/asterisk/pjsip-multicom.conf app/records.py \
         docs/MULTICOM_SIP_CONNECT.md run_ats2.sh frontend/package-lock.json app/ui/index.html app/ui/assets; do
  if [ -e "$f" ]; then p "есть  $f"; else p "НЕТ   $f"; fi
done

h "5. как запущено, где данные, жив ли сервис"
r 'systemctl list-unit-files 2>/dev/null | grep -E "^(ats|asterisk)" | head -4; systemctl is-active ats 2>&1 | head -1'
r 'systemctl cat ats 2>/dev/null | grep -E "ExecStart|Environment|^WorkingDirectory|^User=|^PIDFile" | head -8'
# ATS_DATA_DIR ищем по-честному: /etc/ats/ats.env → deploy/ats.env → ~/.ats.env → сам юнит
DATA="${ATS_DATA:-}"
for f in /etc/ats/ats.env "$A/deploy/ats.env" "$HOME/.ats.env"; do
  [ -z "$DATA" ] && [ -f "$f" ] && DATA="$(sed -nE 's/^[[:space:]]*(Environment=)?ATS_DATA_DIR=//p' "$f" 2>/dev/null | tail -1 | tr -d '"')"
done
[ -n "$DATA" ] || DATA="$(systemctl show ats -p Environment --value 2>/dev/null | tr ' ' '\n' | sed -nE 's/^ATS_DATA_DIR=//p' | tail -1)"
[ -n "$DATA" ] || DATA="$A/data_v2"
PORT="$(systemctl show ats -p Environment --value 2>/dev/null | tr ' ' '\n' | sed -nE 's/^ATS_PORT=//p' | tail -1)"
PORT="${ATS_PORT:-${PORT:-9124}}"
printf '  ATS_DATA_DIR = %s\n  ATS_PORT     = %s\n' "$DATA" "$PORT"
r "ls -l $DATA 2>/dev/null | head -12; echo '  sizes:'; ls -l $DATA/ats.db $DATA/ats.db-wal 2>/dev/null"
r "ps -eo pid,etime,args | grep -E '[a]pp.run|[p]ython3 -m app' | head -3"
r "curl -s -m 5 -o /dev/null -w '  health HTTP %{http_code}\n' http://127.0.0.1:$PORT/api/v2/health"

h "6. состояние внутри АТС (читаю базу mode=ro, ничего не пишу)"
"$VPY" - "$DATA/ats.db" <<'PY' 2>&1 | sed 's/^/    /'
import json, os, sqlite3, sys
db = sys.argv[1]
print("python: %s" % sys.version.split()[0])
if not os.path.exists(db):
    print("базы %s нет — уточни ATS_DATA_DIR (в юните/ats.env)" % db); raise SystemExit
try:
    c = sqlite3.connect("file:%s?mode=ro" % db, uri=True)
except Exception as e:
    print("не открыл базу: %s" % e); raise SystemExit
for t in ("contacts", "campaigns", "numbers", "calls"):
    try:
        print("%-10s %s" % (t, c.execute("select count(*) from %s" % t).fetchone()[0]))
    except Exception as e:
        print("%-10s n/a (%s)" % (t, e))
try:
    row = c.execute("select data from settings where id=1").fetchone()
except Exception as e:
    print("settings недоступны: %s" % e); row = None
c.close()
if row and row[0]:
    s = json.loads(row[0])
    print("provider   = %s | host:port = %s:%s" % (s.get("provider"), s.get("host"), s.get("port")))
    pc = s.get("provider_config") or {}
    print("секции     = %s" % sorted(pc))
    ami = pc.get("ami") or {}
    print("ami        = %s" % (ami or "НЕТ (Мультиком через Asterisk ещё не настроен)"))
    for k in sorted(ami):
        v = str(ami[k])
        if any(x in k.lower() for x in ("secret", "password", "token")):
            v = v[:3] + "…"
        print("   ami.%-16s = %s" % (k, v))
PY

h "7. телефония: есть ли Asterisk"
r 'command -v asterisk >/dev/null && asterisk -V 2>&1 | head -1 || echo "asterisk НЕ установлен"'
r 'for f in /etc/asterisk/pjsip.conf /etc/asterisk/manager.conf /etc/asterisk/extensions.conf; do [ -f "$f" ] && echo "есть $f" || echo "нет $f"; done'
r 'command -v espeak-ng >/dev/null && espeak-ng --version 2>&1 | head -1 || echo "espeak-ng НЕ установлен"'
r 'echo "  reverse-proxy (нужен для HTTPS-ссылок на записи):"; command -v nginx caddy 2>/dev/null || echo "nginx/caddy не найдены"'
h "готово: пришли этот вывод целиком"
