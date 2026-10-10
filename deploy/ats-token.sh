#!/usr/bin/env bash
# ============================================================================
#  deploy/ats-token.sh — получить токен API АТС для скриптов.
#
#  Нужен, когда вызываем API из shell: install-multicom-trunk.sh --ats/--numbers,
#  attach_recordings.py вручную, /api/v2/ami/check и т.п. Токен печатается одной
#  строкой на stdout, всё остальное (подсказки, ошибки) — на stderr, поэтому
#  конструкция TOK=$(./deploy/ats-token.sh) не подхватывает лишний текст.
#
#  Запуск:
#      TOK=$(/opt/ats/app/deploy/ats-token.sh)              # спросит логин и пароль
#      ATS_LOGIN=admin TOK=$(… ./deploy/ats-token.sh)       # логин из окружения
#      ATS_PASSWORD_FILE=/run/ats/pw ./deploy/ats-token.sh  # неинтерактивно
#      printf 'admin\nсекрет\n' | ./deploy/ats-token.sh      # две строки со stdin
#      ATS_URL=http://127.0.0.1:9124 ./deploy/ats-token.sh   # другой адрес/порт
#
#  Пароль не принимается аргументом командной строки: аргументы видны в ps всем
#  пользователям и остаются в истории shell. Только интерактивный ввод, файл
#  (ATS_PASSWORD_FILE) или pipe.
#  Коды выхода: 0 — токен напечатан; 1 — войти не удалось (сеть/403/429/пустой ответ).
# ============================================================================
set -uo pipefail

ATS_URL="${ATS_URL:-http://127.0.0.1:9124}"

# Читаем аккуратно: в интерактивной сессии — из stdin; внутри $(…) часть shells
# отдаёт не-tty, тогда пробуем /dev/tty; если и он недоступен (EIO) — читаем stdin
# как обычный поток (это делает возможной конструкцию printf '…\n…\n' | ats-token.sh).
ask() {
  local var="$1" prompt="$2" silent="${3:-}" opt="" ans=""
  [ "$silent" = "s" ] && opt="-s"
  if [ -t 0 ]; then
    read -r $opt -p "$prompt" ans || true
    [ "$silent" = "s" ] && echo >&2
  elif [ -e /dev/tty ] && read -r $opt -p "$prompt" ans </dev/tty 2>/dev/null; then
    [ "$silent" = "s" ] && echo >&2
  else
    read -r ans || true
  fi
  printf -v "$var" '%s' "$ans"
}

login="${ATS_LOGIN:-}"
[ -n "$login" ] || ask login "Логин администратора ATS: "

pw=""
if [ -n "${ATS_PASSWORD_FILE:-}" ] && [ -r "${ATS_PASSWORD_FILE:-}" ]; then
  pw="$(head -1 "${ATS_PASSWORD_FILE}")"
fi
[ -n "$pw" ] || ask pw "Пароль ${login:-}: " s
if [ -z "$pw" ]; then
  echo "[ats-token] пароль не получен — введите вручную, или ATS_PASSWORD_FILE=…, или" >&2
  echo "            printf 'логин\\nпароль\\n' | $0" >&2
  exit 1
fi

ATS_PW="$pw" ATS_URL="$ATS_URL" ATS_LOGIN="$login" python3 - <<'PYTOK'
import json
import os
import sys
import urllib.error
import urllib.request

url = os.environ["ATS_URL"].rstrip("/")
login = os.environ.get("ATS_LOGIN") or ""
body = json.dumps({"login": login, "password": os.environ["ATS_PW"]}).encode()
req = urllib.request.Request(url + "/api/v2/auth/login", data=body, method="POST",
                             headers={"Content-Type": "application/json"})
try:
    with urllib.request.urlopen(req, timeout=15) as r:
        raw = r.read().decode("utf-8", "ignore")
except urllib.error.HTTPError as e:                       # noqa: BLE001
    detail = ""
    try:
        detail = e.read().decode("utf-8", "ignore")[:200]
    except Exception:                                     # noqa: BLE001
        pass
    sys.stderr.write("[ats-token] %s вернул HTTP %s: %s\n" % (url, e.code, detail or e.reason))
    if e.code == 429:
        sys.stderr.write("[ats-token] сработала защита от перебора — подождите и повторите\n")
    raise SystemExit(1)
except Exception as e:                                    # noqa: BLE001
    sys.stderr.write("[ats-token] запрос к %s не прошёл: %s\n" % (url, str(e)[:180]))
    raise SystemExit(1)
try:
    res = json.loads(raw or "{}")
except ValueError:
    sys.stderr.write("[ats-token] ответ не JSON: %s\n" % raw[:180])
    raise SystemExit(1)
tok = res.get("token") or ""
if not tok:
    sys.stderr.write("[ats-token] сервер не выдал токен (проверьте логин/пароль): %s\n"
                     % str(res)[:180])
    raise SystemExit(1)
sys.stdout.write(tok + "\n")
PYTOK
rc=$?
unset pw ATS_PW
exit "$rc"
