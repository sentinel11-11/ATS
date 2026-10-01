#!/usr/bin/env bash
# ============================================================
#  Быстрая диагностика сервера перед обновлением ATS (одна вставка).
#  Только читает: пишет исключительно в .git (git fetch). Ничего не рестартует.
#
#  Запуск:              bash /tmp/ats-check.sh
#  Другой каталог:      ATS_DIR=/srv/ats bash /tmp/ats-check.sh
#  Другая ветка:        ATS_BRANCH=main  bash /tmp/ats-check.sh
#  Полный вариант отчёта: ./deploy/audit.sh (больше секций: БД, логи, Asterisk)
# ============================================================
set -u
A="${ATS_DIR:-/opt/ats}"; B="${ATS_BRANCH:-arena/01a0cdee-ats}"; R="${ATS_REMOTE:-origin}"
[ -d "$A" ] || { echo "НЕТ каталога $A — скажи, где лежит код, и повтори: ATS_DIR=/путь bash /tmp/ats-check.sh"; exit 1; }
cd "$A" || exit 1
h(){ printf '\n===== %s =====\n' "$*"; }
p(){ printf '  %s\n' "$*"; }
r(){ printf '  $ %s\n' "$*"; eval "$*" 2>&1 | sed 's/^/    /'; }

h "1. что за копия лежит на сервере"
r 'pwd; ls -ld .; id -un'
r 'git rev-parse --is-inside-work-tree || echo "ЭТО НЕ GIT-КЛОН"'
r 'git remote -v | head -2'
r 'git rev-parse --abbrev-ref HEAD; git log --oneline -3; git log -1 --format="HEAD: %h %ci %an"'
r 'git branch -a -vv | head -12'

h "2. незакоммиченные правки и stash"
r 'git status --porcelain | head -20; printf "  всего строк: %s\n" "$(git status --porcelain | wc -l)"'
r 'git stash list | head -5'
r 'git diff --shortstat; git diff --name-only | head -20'

h "3. расхождение с $R/$B (это решает, как обновляться)"
r "timeout 60 git fetch $R --prune 2>&1 | tail -2"
r "timeout 60 git fetch $R \"+refs/heads/$B:refs/remotes/$R/$B\" 2>&1 | tail -2"
r "timeout 30 git ls-remote --heads $R 2>&1 | head -8"
printf '  $ (сверка)\n'
printf '    отстаём=%s  опережаем=%s  merge-base=%s\n' \
  "$(timeout 30 git rev-list --count "HEAD..$R/$B" 2>/dev/null)" \
  "$(timeout 30 git rev-list --count "$R/$B..HEAD" 2>/dev/null)" \
  "$(timeout 30 git merge-base HEAD "$R/$B" 2>/dev/null | cut -c1-8)"
r "echo '  свои коммиты, которых вообще нет на GitHub (их нельзя терять):'; git log --oneline --branches --not --remotes | head -10"
r "echo '  свои коммиты поверх $R/$B:'; git log --oneline $R/$B..HEAD | head -10"
r "MB=$(timeout 30 git merge-base HEAD $R/$B 2>/dev/null); echo '  файлы, которые сервер правил сам после точки расхождения:'; git diff --name-only \${MB:-$R/$B}..HEAD | head -25"

h "4. ключевые файлы: пришли / не пришли"
for f in deploy/update.sh deploy/ats.service deploy/asterisk/pjsip-multicom.conf app/records.py \
         docs/MULTICOM_SIP_CONNECT.md run_ats2.sh frontend/package-lock.json; do
  if [ -e "$f" ]; then p "есть  $f"; else p "НЕТ   $f"; fi
done

h "5. как запущено, где данные, жив ли сервис"
r 'systemctl list-unit-files 2>/dev/null | grep -E "^(ats|asterisk)" | head -4; systemctl is-active ats 2>&1 | head -1'
r 'systemctl cat ats 2>/dev/null | grep -E "ExecStart|Environment|^WorkingDirectory|^User=" | head -6'
r 'for f in /etc/ats/ats.env deploy/ats.env ~/.ats.env; do [ -f "$f" ] && { echo "-- $f:"; sed -E "s/^([A-Z_]*(KEY|SECRET|TOKEN|PASSWORD)[A-Z_]*)=.*/\1=***/" "$f" | head -14; }; done'
r 'DATA=$(sed -nE "s/^ATS_DATA_DIR=//p" /etc/ats/ats.env 2>/dev/null | tail -1); DATA=${DATA:-data_v2}; echo "  data: $DATA"; ls -l "$DATA"/ats.db 2>/dev/null | tail -1; echo "$DATA" > /tmp/.ats-check-data'
r 'ps -eo pid,etime,args | grep -E "[a]pp.run|[p]ython3 -m app" | head -3'
r 'curl -s -m 5 -o /dev/null -w "  health HTTP %{http_code}\n" http://127.0.0.1:9124/api/v2/health'
python3 - "$(cat /tmp/.ats-check-data 2>/dev/null || echo data_v2)/ats.db" <<'PY' 2>&1 | sed 's/^/    /'
import os, sqlite3, sys
db = sys.argv[1]
if not os.path.exists(db):
    print("базы %s нет" % db); raise SystemExit
try:
    c = sqlite3.connect("file:%s?mode=ro" % db, uri=True)
except Exception as e:
    print("не открыл базу: %s" % e); raise SystemExit
for t in ("contacts", "campaigns", "numbers", "calls"):
    try:
        print("%-10s %s" % (t, c.execute("select count(*) from %s" % t).fetchone()[0]))
    except Exception as e:
        print("%-10s n/a (%s)" % (t, e))
c.close()
PY

h "6. телефония: есть ли Asterisk"
r 'command -v asterisk >/dev/null && asterisk -V 2>&1 | head -1 || echo "asterisk НЕ установлен"'
r 'for f in /etc/asterisk/pjsip.conf /etc/asterisk/manager.conf /etc/asterisk/extensions.conf; do [ -f "$f" ] && echo "есть $f" || echo "нет $f"; done'
r 'command -v espeak-ng >/dev/null && espeak-ng --version 2>&1 | head -1 || echo "espeak-ng НЕ установлен"'
rm -f /tmp/.ats-check-data
h "готово: пришли этот вывод целиком"
