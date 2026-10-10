#!/usr/bin/env bash
# ============================================================================
#  deploy/ats-token.sh — получить токен API АТС для скриптов.
#
#  Нужен, когда вызываем API из shell: install-multicom-trunk.sh --ats/--numbers,
#  attach_recordings.py вручную, /api/v2/ami/check и т.п. Печатает токен одной
#  строкой на stdout; всё остальное (подсказки, ошибки) — на stderr, поэтому
#  конструкция TOK=$(./deploy/ats-token.sh) не подхватывает мусор.
#
#  Запуск:
#      TOK=$(/opt/ats/app/deploy/ats-token.sh)                 # спросит логин и пароль
#      ATS_LOGIN=admin TOK=$(… ./deploy/ats-token.sh)          # логин из окружения
#      ATS_PASSWORD_FILE=/run/ats/pw ./deploy/ats-token.sh     # неинтерактивно
#      ATS_URL=http://127.0.0.1:9124 ./deploy/ats-token.sh     # другой адрес/порт
#
#  Пароль не принимается аргументом: аргументы видно в ps всем пользователям и они
#  остаются в истории shell. Только интерактивный ввод (read -s) или файл.
#  Коды выхода: 0 — токен напечатан; 1 — не удалось войти (сеть/403/пустой ответ).
# ============================================================================
set -uo pipefail

ATS_URL="${ATS_URL:-http://127.0.0.1:9124}"

login="${ATS_LOGIN:-}"
if [ -z "$login" ]; then
  if [ -e /dev/tty ]; then read -r -p "Логин администратора ATS: " login </dev/tty
  else read -r -p "Логин администратора ATS: " login; fi
fi
[ -n "$login" ] || { echo "[ats-token] логин пуст" >&2; exit 1; }

pw=""
if [ -n "${ATS_PASSWORD_FILE:-}" ] && [ -r "${ATS_PASSWORD_FILE:-}" ]; then
  pw="$(head -1 "${ATS_PASSWORD_FILE}")"
fi
if [ -z "$pw" ]; then
  if [ -e /dev/tty ]; then read -r -s -p "Пароль ${login}: " pw </dev/tty && echo >&2
  else read -r -s -p "Пароль ${login}: " pw && echo >&2; fi
fi
[ -n "$pw" ] || { echo "[ats-token] пароль пуст — ввод не получился (нужен tty или ATS_PASSWORD_FILE)" >&2; exit 1; }

ATS_PW="$pw" ATS_URL="$ATS_URL" ATS_LOGIN="$login" python3 - <<'PYTOK'
import json
import os
import sys
import urllib.request

url = os.environ["ATS_URL"].rstrip("/")
body = json.dumps({"login": os.environ["ATS_LOGIN"],
                   "password": os.environ["ATS_PW"]}).encode()
os.environ.pop("ATS_PW", None)
req = urllib.request.Request(url + "/api/v2/auth/login", data=body, method="POST",
                             headers={"Content-Type": "application/json"})
try:
    with urllib.request.urlopen(req, timeout=15) as r:
        res = json.loads(r.read().decode("utf-8", "ignore") or "{}")
except Exception as e:                                    # noqa: BLE001
    sys.stderr.write("[ats-token] запрос к %s не прошёл: %s\n" % (url, str(e)[:180]))
    raise SystemExit(1)
tok = res.get("token") or ""
if not tok:
    sys.stderr.write("[ats-token] сервер не выдал токен (неверный логин/пароль или "
                     "429 too_many_attempts): %s\n" % str(res)[:180])
    raise SystemExit(1)
sys.stdout.write(tok + "\n")
PYTOK
rc=$?
unset pw ATS_PW
[ "$rc" = "0" ] || exit "$rc"
exit 0
