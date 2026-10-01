#!/usr/bin/env bash
# ============================================================================
#  Данные из письма «Мультиком» — в конфиг SIP-транка Asterisk.
#
#  Что делает:
#    1) рендерит deploy/asterisk/pjsip-multicom.conf (шаблон с плейсхолдерами
#       __MCM_LOGIN__/__MCM_PASS__/__MCM_HOST__/__MCM_PORT__/__MCM_NET__ и
#       блоком allow= между маркерами) в /etc/asterisk/pjsip-multicom.conf;
#    2) валидирует значения: в конфиге Asterisk ';' и '#' — комментарии, а
#       перенос строки ломает секцию, поэтому такое не должно пролезть ни через
#       окружение, ни через UI/API;
#    3) права 0640 root:asterisk, резервная копия предыдущей версии;
#    4) идемпотентно добавляет #include pjsip-multicom.conf в pjsip.conf;
#    5) module reload res_pjsip.so и ожидание регистрации у оператора;
#    6) --firewall  только allow-правила на сеть оператора (по умолчанию НЕ
#       трогает файрвол, а печатает команды);
#    7) --ami       заодно генерирует пароль и заводит пользователя AMI «ats».
#
#  В репозиторий данные письма не попадают: пароль транка берётся из окружения
#  (MCM_PASS) или спрашивается с терминала; если он уже есть в установленном
#  файле — переиспользуется (тогда можно просто проверить конфигурацию).
#
#  Запуск (root, из клона АТС на сервере):
#      MCM_PASS='***' sudo -E ./deploy/asterisk/install-multicom-trunk.sh
#  Режимы:
#      --print     показать результат, ничего не писать
#      --dry-run   показать, что будет сделано
#      --check     только проверка (файл/плейсхолдеры/#include/регистрация)
#      --firewall  применить правила файрвола
#      --ami       настроить пользователя AMI для ATS
#      --ats       записать настройки провайдера ami в саму АТС (API: settings/raw)
#      --restart   после этого ещё и перезапустить сервис (systemctl restart ats)
#      --numbers   завести 15 номеров из письма в пул АТС (через API, см. MCM_NUMBERS)
#      --numbers-only  только номера: файл транка не трогать, root не нужен
#  Переменные (значения по умолчанию = данные из письма):
#      MCM_LOGIN=00083819 MCM_HOST=95.128.224.47 MCM_PORT=5060 MCM_TRUNK=mcm
#      MCM_NET=95.128.224.0/21 MCM_RTP=10000-20000 MCM_CODECS=alaw,ulaw,g729
#      MCM_MAX_CHANNELS=15 REG_TIMEOUT=40 AST_DIR=/etc/asterisk
# ============================================================================
set -uo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
AST_DIR="${AST_DIR:-/etc/asterisk}"
TPL="${TPL:-$APP_DIR/deploy/asterisk/pjsip-multicom.conf}"
ASTERISK_CMD="${ASTERISK_CMD:-asterisk}"
OUT_PATH="$AST_DIR/pjsip-multicom.conf"

MCM_LOGIN="${MCM_LOGIN:-00083819}"
MCM_HOST="${MCM_HOST:-95.128.224.47}"
MCM_PORT="${MCM_PORT:-5060}"
MCM_TRUNK="${MCM_TRUNK:-mcm}"
MCM_NET="${MCM_NET:-95.128.224.0/21}"
MCM_RTP="${MCM_RTP:-10000-20000}"
MCM_CODECS="${MCM_CODECS:-alaw,ulaw,g729}"
MCM_MAX_CHANNELS="${MCM_MAX_CHANNELS:-15}"
REG_TIMEOUT="${REG_TIMEOUT:-40}"
# Номера из письма (10 цифр, без «7»): идут в пул АТС, а не в Asterisk.
MCM_NUMBERS="${MCM_NUMBERS:-9690229926 9690229930 9690229937 9690229938 9690229942 \
9863149340 9863149348 9863149351 9863149365 9863149385 \
9362574897 9362577603 9362962622 9362968878 9362968932}"
MCM_NUMBER_PROVIDER="${MCM_NUMBER_PROVIDER:-ami}"     # движок фильтрует пул строго по этому полю
MCM_DAILY_LIMIT="${MCM_DAILY_LIMIT:-120}"
ATS_URL="${ATS_URL:-http://127.0.0.1:9124}"
PASS_SENTINEL='__MCM_PASS__'

MODE="install"; FIREWALL=0; WANT_AMI=0; WANT_NUMBERS=0; WANT_ATS=0; WANT_RESTART=0
for a in "$@"; do
  case "$a" in
    --print)    MODE="print" ;;
    --dry-run)  MODE="dry" ;;
    --check)    MODE="check" ;;
    --firewall) FIREWALL=1 ;;
    --ami)      WANT_AMI=1 ;;
    --ats)       WANT_ATS=1 ;;
    --restart)   WANT_RESTART=1; WANT_ATS=1 ;;
    --numbers)   WANT_NUMBERS=1 ;;
    --numbers-only) WANT_NUMBERS=1; MODE="numbers" ;;
    -h|--help)  sed -n '2,33p' "$0"; exit 0 ;;
    *) echo "[mcm-trunk] неизвестный флаг: $a (см. --help)" >&2; exit 2 ;;
  esac
done

log()  { echo "[mcm-trunk] $*"; }
warn() { echo "[mcm-trunk] ВНИМАНИЕ: $*" >&2; }
die()  { echo "[mcm-trunk] ОШИБКА: $*" >&2; exit 1; }

# ---------------------------------------------------------------- валидация --
# Значение уходит в sed-замену и в конфиг Asterisk: запрещаем переводы строк и
# синтаксис конфига. Для пароля символы # и ; допустимы (это данные, не синтаксис),
# поэтому для него проверяем только перенос строки.
check_value() {
  local name="$1" val="$2"
  case "$val" in
    *$'\n'*|*$'\r'*) die "$name: значение не должно содержать переносы строк" ;;
  esac
  if [ "$name" = "MCM_PASS" ]; then
    [ -n "$val" ] || die "MCM_PASS пустой"
    return 0
  fi
  case "$val" in
    *";"*|*"#"*|*"*"*) die "$name: запрещено содержать ; # * (синтаксис конфига Asterisk)" ;;
    *[!A-Za-z0-9._:/@+,-]*) die "$name: допустимы буквы, цифры и . _ : / @ + - , (получено: $val)" ;;
  esac
  return 0
}

check_value MCM_LOGIN "$MCM_LOGIN"        || exit 1
check_value MCM_HOST "$MCM_HOST"          || exit 1
check_value MCM_PORT "$MCM_PORT"          || exit 1
check_value MCM_TRUNK "$MCM_TRUNK"        || exit 1
check_value MCM_NET "$MCM_NET"            || exit 1
check_value MCM_CODECS "$MCM_CODECS"      || exit 1
check_value MCM_MAX_CHANNELS "$MCM_MAX_CHANNELS" || exit 1
case "$MCM_PORT" in ''|*[!0-9]*) die "MCM_PORT должен быть числом: $MCM_PORT" ;; esac
case "$MCM_MAX_CHANNELS" in ''|*[!0-9]*) die "MCM_MAX_CHANNELS должен быть числом" ;; esac

command -v sed >/dev/null 2>&1 || die "не найден sed"
[ -f "$TPL" ] || die "нет шаблона $TPL — запускайте из клона АТС (cd /opt/ats/app)"

# ------------------------------------------------------------------- пароль --
if [ -z "${MCM_PASS:-}" ] && [ -f "$OUT_PATH" ]; then
  prev="$(sed -n 's/^password=//p' "$OUT_PATH" | head -1)"
  case "$prev" in
    ''|"$PASS_SENTINEL"|ПАРОЛЬ_ИЗ_ПИСЬМА) : ;;
    *) MCM_PASS="$prev"; log "пароль переиспользован из $OUT_PATH" ;;
  esac
fi
if [ -z "${MCM_PASS:-}" ]; then
  case "$MODE" in
    print|dry|check|numbers) MCM_PASS="$PASS_SENTINEL" ;;        # без записи — не спрашиваем
    *) if [ -t 0 ]; then
         read -rs -p "Пароль SIP-регистрации ($MCM_LOGIN@$MCM_HOST): " MCM_PASS; echo
       else
         die "не задан MCM_PASS. Пример: MCM_PASS='…' sudo -E $0   (в git пароль не класть)"
       fi ;;
  esac
fi
check_value MCM_PASS "$MCM_PASS" || exit 1

# ------------------------------------------------------------------- кодеки --
ALLOW_BLOCK=""
IFS=',' read -ra _codecs <<< "$MCM_CODECS"
for c in "${_codecs[@]}"; do
  c="$(printf '%s' "$c" | tr -d '[:space:]')"
  [ -n "$c" ] || continue
  case "$c" in *[!a-zA-Z0-9]*) die "MCM_CODECS: недопустимое имя кодека '$c'" ;; esac
  ALLOW_BLOCK="${ALLOW_BLOCK}allow=${c}"$'\n'
done
[ -n "$ALLOW_BLOCK" ] || die "MCM_CODECS пустой"
ALLOW_BLOCK="${ALLOW_BLOCK%$'\n'}"

esc() { printf '%s' "$1" | sed -e 's/[\\&|]/\\&/g'; }

render() {
  sed \
    -e "s|__MCM_LOGIN__|$(esc "$MCM_LOGIN")|g" \
    -e "s|__MCM_HOST__|$(esc "$MCM_HOST")|g" \
    -e "s|__MCM_PORT__|$(esc "$MCM_PORT")|g" \
    -e "s|__MCM_NET__|$(esc "$MCM_NET")|g" \
    -e "s|__MCM_MAXCH__|$(esc "$MCM_MAX_CHANNELS")|g" \
    -e "s|__MCM_PASS__|$(esc "$MCM_PASS")|g" \
    "$TPL" | awk -v allow="$ALLOW_BLOCK" -v trunk="$MCM_TRUNK" '
      /^;__MCM_ALLOW_BEGIN__$/ { print allow; skip=1; next }
      /^;__MCM_ALLOW_END__$/   { skip=0; next }
      skip { next }
      { gsub(/^\[mcm/, "[" trunk); gsub(/endpoint=mcm$/, "endpoint=" trunk);
        gsub(/aors=mcm-aor/, "aors=" trunk "-aor");
        gsub(/outbound_auth=mcm-auth/, "outbound_auth=" trunk "-auth");
        print }' 
}

rendered="$(render)" || die "не удалось отрендерить шаблон"
left="$(printf '%s\n' "$rendered" | grep -n '__MCM_[A-Z_]*__' | head -5)"
if [ -n "$left" ] && [ "$MODE" != "print" ] && [ "$MODE" != "dry" ] && [ "$MODE" != "check" ] && [ "$MODE" != "numbers" ]; then
  die "в результате остались незаполненные плейсхолдеры: $left"
fi

import_numbers() {
  # Номера — в БД АТС через API, а не в конфиг Asterisk: их назначение — CLI
  # исходящих и DID входящих. Суточный лимит 120 на номер — безопасный старт для
  # мобильных маршрутов; поднимать лучше по неделе, следя за quarantine.
  command -v curl >/dev/null 2>&1 || die "нужен curl"
  [ -n "${ATS_TOKEN:-}" ] || die "нужен ATS_TOKEN: токен администратора АТС (заголовок X-Ats-Token)"
  case "$MCM_NUMBER_PROVIDER" in
    sim|uis|ami|megafon_vats|multicom) : ;;
    *) die "MCM_NUMBER_PROVIDER должен быть одним из: sim uis ami megafon_vats multicom" ;;
  esac
  local n body res added=0 exists=0 bad=0
  for n in $MCM_NUMBERS; do
    case "$n" in ''|*[!0-9]*) warn "пропускаю «$n»: ожидал 10 цифр без «7»"; bad=$((bad+1)); continue ;; esac
    body="{\"number\":\"+7$n\",\"label\":\"MCM $n\",\"kind\":\"mobile\",\"provider\":\"$MCM_NUMBER_PROVIDER\",\"daily_limit\":$MCM_DAILY_LIMIT}"
    res="$(curl -s -m 20 -X POST "$ATS_URL/api/v2/numbers/save" \
             -H "X-Ats-Token: $ATS_TOKEN" -H 'Content-Type: application/json' -d "$body" 2>/dev/null || true)"
    if printf '%s' "$res" | grep -q 'number_exists'; then
      exists=$((exists+1))
    elif printf '%s' "$res" | grep -q '"ok"'; then
      added=$((added+1))
    else
      warn "номер $n: непонятный ответ: $(printf '%s' "$res" | head -c 160)"
      bad=$((bad+1))
    fi
  done
  log "пул номеров (provider=$MCM_NUMBER_PROVIDER, daily_limit=$MCM_DAILY_LIMIT): добавлено $added, уже было $exists, проблем $bad"
  [ "$bad" = "0" ] || return 1
  return 0
}

apply_ats_settings() {
  # Провайдер и форматы — настройки АТС, их пишем через API (секции settings/raw),
  # а не правкой БД: только так проходит та же валидация, что и через UI.
  [ -n "${ATS_TOKEN:-}" ] || die "нужен ATS_TOKEN (токен администратора АТС): ATS_TOKEN=… $0 --ats"
  [ -n "${AMIPASS:-}" ] || die "нужен AMIPASS — тот же пароль, что в manager-ats.conf (--ami его генерирует; можно задать явно)"
  ATS_URL="$ATS_URL" ATS_TOKEN="$ATS_TOKEN" AMIPASS="$AMIPASS" MCM_TRUNK="$MCM_TRUNK" \
  AMI_HOST="${AMI_HOST:-127.0.0.1}" AMI_PORT="${AMI_PORT:-5038}" AMI_USER="${AMI_USER:-ats}" \
  python3 - <<'PYATS'
import json, os, urllib.request

ami = {
    "host": os.environ.get("AMI_HOST", "127.0.0.1"),
    "port": int(os.environ.get("AMI_PORT", "5038")),
    "user": os.environ.get("AMI_USER", "ats"),
    "secret": os.environ["AMIPASS"],
    "trunk": os.environ["MCM_TRUNK"],
    "tech": "PJSIP",
    "number_format": "ru8",          # из письма: 8_КодГорода_НомерТелефона
    "caller_id_format": "d10",       # из письма: номера в 10 знаков
    "context": "ats-out",
    "inbound_contexts": "from-mcm,ats-in",
    "record_calls": True,
    "play_message": True,
    "inbound_enabled": True,
}
url = os.environ["ATS_URL"].rstrip("/") + "/api/v2/settings/raw"
body = json.dumps({"provider_config": {"provider": "ami", "ami": ami}}).encode()
req = urllib.request.Request(url, data=body, method="POST",
                             headers={"Content-Type": "application/json",
                                      "X-Ats-Token": os.environ["ATS_TOKEN"]})
try:
    with urllib.request.urlopen(req, timeout=20) as r:
        out = r.read().decode("utf-8", "ignore")
except Exception as e:                                   # noqa: BLE001
    print("ATS_ERROR %s" % str(e)[:200])
    raise SystemExit(1)
print("ATS_OK " + out[:160])
PYATS
  local rc=$?
  if [ "$rc" != "0" ]; then
    die "не удалось записать настройки АТС ($ATS_URL) — проверьте ATS_TOKEN и доступность сервиса"
  fi
  log "настройки АТС записаны: provider=ami, транк='$MCM_TRUNK', number_format=ru8, caller_id_format=d10"
}

have_asterisk() { command -v "$ASTERISK_CMD" >/dev/null 2>&1; }

registration_state() {
  local out
  out="$("$ASTERISK_CMD" -rx "pjsip show registrations" 2>&1)"
  out="$out
$("$ASTERISK_CMD" -rx "pjsip show contacts" 2>&1)"
  printf '%s' "$out"
}

check_only() {
  local rc=0 line
  if [ ! -f "$OUT_PATH" ]; then
    warn "$OUT_PATH не установлен"; rc=1
  else
    log "файл: $(ls -l "$OUT_PATH" | awk '{print $1, $3":"$4}')"
    for k in auth_name password server_uri from_user match; do
      line="$(sed -n "s/^$k=//p" "$OUT_PATH" | head -1)"
      case "$line" in
        ''|"$PASS_SENTINEL"|ПАРОЛЬ_ИЗ_ПИСЬМА) warn "поле $k не заполнено"; rc=1 ;;
        *) log "  $k = $line" ;;
      esac
    done
    awk '/^\[/ {sec=$0} /^allow=/ {found[sec]=found[sec] $0 " "} END {for (s in found) if (found[s] != "") print "  " s found[s]}' "$OUT_PATH"
  fi
  grep -q 'pjsip-multicom.conf' "$AST_DIR/pjsip.conf" 2>/dev/null \
    || { warn "в $AST_DIR/pjsip.conf нет '#include pjsip-multicom.conf' — Asterisk файл не увидит"; rc=1; }
  if have_asterisk; then
    local st; st="$(registration_state)"
    if printf '%s' "$st" | grep -Eqi "200|registered|avail|bound"; then
      log "регистрация/контакт есть"
    else
      warn "регистрации нет. Вывод: $(printf '%s' "$st" | tr '\n' ' ' | cut -c1-200)"; rc=1
    fi
  else
    warn "cli '$ASTERISK_CMD' не найдена — Asterisk не установлен или не запущен"; rc=1
  fi
  return $rc
}

case "$MODE" in
  print) printf '%s\n' "$rendered"; exit 0 ;;
  check) if check_only; then log "всё в порядке"; exit 0; else exit 1; fi ;;
esac

if [ "$MODE" = "numbers" ]; then
  import_numbers && log "готово (номера). Файл транка не трогали." || exit 1
  exit 0
fi

# ALLOW_NONROOT=1 — только для тестов и для случая «нет root, но каталог доступен
# на запись» (например, контейнер/стенд с AST_DIR в tmpfs). На проде не ставить.
if [ "$(id -u)" != "0" ] && [ "${ALLOW_NONROOT:-0}" != "1" ]; then
  die "нужны права root (пишем в $AST_DIR). Запуск: sudo -E $0"
fi
[ -d "$AST_DIR" ] || die "нет каталога $AST_DIR — сначала: apt-get install -y asterisk asterisk-cli espeak-ng"

if [ "$MODE" = "dry" ]; then
  log "файл: $OUT_PATH (копия $OUT_PATH.bak-<stamp>)"
  log "кодеки: $(printf '%s' "$ALLOW_BLOCK" | tr '\n' ' ')"
  grep -q 'pjsip-multicom.conf' "$AST_DIR/pjsip.conf" 2>/dev/null \
    || log "добавлю '#include pjsip-multicom.conf' в $AST_DIR/pjsip.conf"
  [ "$FIREWALL" = "1" ] && log "файрвол: разрешу $MCM_NET → $MCM_PORT tcp/udp и RTP $MCM_RTP/udp"
  [ "$WANT_AMI" = "1" ] && log "AMI: сгенерирую пароль и заведу $AST_DIR/manager-ats.conf"
  [ "$WANT_ATS" = "1" ] && log "АТС: запишу settings.ami (provider=ami, trunk=$MCM_TRUNK, ru8/d10) через $ATS_URL"
  [ "$WANT_NUMBERS" = "1" ] && log "АТС: заведу номера в пул provider=$MCM_NUMBER_PROVIDER ($(printf '%s' "$MCM_NUMBERS" | wc -w) шт., daily_limit=$MCM_DAILY_LIMIT)"
  log "рендер (первые 22 строки, пароль скрыт):"
  printf '%s\n' "$rendered" | head -22 | sed -e 's/^password=.*/password=***/' -e 's/^/    /'
  exit 0
fi

# ------------------------------------------------------------------ запись ---
stamp="$(date +%Y%m%d-%H%M%S)-$$"   # pid: два прогона в одну секунду не должны затирать бэкап друг друга
if [ -f "$OUT_PATH" ]; then
  cp -a "$OUT_PATH" "$OUT_PATH.bak-$stamp" && log "резервная копия: $OUT_PATH.bak-$stamp"
fi
grp="$(id -g asterisk >/dev/null 2>&1 && echo asterisk || echo root)"
# -o/-g имеет смысл только от root; иначе install упадёт на законной записи в свой каталог
install_args=(-m 0640)
if [ "$(id -u)" = "0" ]; then install_args+=(-o root -g "$grp"); fi
tmp="$(mktemp)" || die "mktemp не удался"
printf '%s\n' "$rendered" > "$tmp" || { rm -f "$tmp"; die "не удалось собрать файл"; }
install "${install_args[@]}" "$tmp" "$OUT_PATH" || { rm -f "$tmp"; die "не удалось записать $OUT_PATH"; }
rm -f "$tmp"
log "установлен $OUT_PATH (0640$( [ "$(id -u)" = "0" ] && echo " root:$grp" )), транк='$MCM_TRUNK', регистрация $MCM_LOGIN@$MCM_HOST:$MCM_PORT"

if [ -f "$AST_DIR/pjsip.conf" ]; then
  grep -q 'pjsip-multicom.conf' "$AST_DIR/pjsip.conf" \
    || { printf '#include pjsip-multicom.conf\n' >> "$AST_DIR/pjsip.conf"; log "#include добавлен в pjsip.conf"; }
else
  warn "нет $AST_DIR/pjsip.conf — восстановите конфиг из пакета или создайте пустой файл со строкой #include pjsip-multicom.conf"
fi

# ------------------------------------------------- пользователь AMI (по флагу)
if [ "$WANT_AMI" = "1" ]; then
  MT="$AST_DIR/manager-ats.conf"
  if [ ! -f "$MT" ] && [ -f "$APP_DIR/deploy/asterisk/manager-ats.conf" ]; then
    install "${install_args[@]}" "$APP_DIR/deploy/asterisk/manager-ats.conf" "$MT"
  fi
  if [ -f "$MT" ]; then
    AMIPASS="${AMIPASS:-$(openssl rand -hex 24 2>/dev/null || head -c 24 /dev/urandom | od -An -tx1 | tr -d ' \n')}"
    sed -i "s|^secret *= .*|secret = $(esc "$AMIPASS")|" "$MT"
    chmod 0640 "$MT"
    if [ -f "$AST_DIR/manager.conf" ]; then
      grep -q 'manager-ats.conf' "$AST_DIR/manager.conf" \
        || printf '#include manager-ats.conf\n' >> "$AST_DIR/manager.conf"
      sed -i 's/^enabled = no/enabled = yes/' "$AST_DIR/manager.conf"
    else
      warn "нет $AST_DIR/manager.conf — создайте его с секцией [general] enabled = yes и строкой #include manager-ats.conf"
    fi
    export AMIPASS
    log "AMI: пользователь 'ats'. Пароль (вбить в АТС → Asterisk AMI Manager → «AMI Пароль»):"
    log "     $AMIPASS"
  else
    warn "нет $MT — скопируйте deploy/asterisk/manager-ats.conf и повторите --ami"
  fi
fi

# ------------------------------------------------------------------- файрвол --
if [ "$FIREWALL" = "1" ]; then
  if command -v ufw >/dev/null 2>&1; then
    for proto in udp tcp; do
      ufw allow from "$MCM_NET" to any port "$MCM_PORT" proto "$proto" >/dev/null 2>&1 \
        && log "ufw: $MCM_NET → $MCM_PORT/$proto"
    done
    IFS='-' read -r rtp_lo rtp_hi <<< "$MCM_RTP"
    ufw allow from "$MCM_NET" to any port "${rtp_lo}:${rtp_hi:-$rtp_lo}" proto udp >/dev/null 2>&1 \
      && log "ufw: $MCM_NET → RTP udp $MCM_RTP"
  elif command -v iptables >/dev/null 2>&1; then
    IFS='-' read -r rtp_lo rtp_hi <<< "$MCM_RTP"
    iptables -I INPUT -s "$MCM_NET" -p udp --dport "$MCM_PORT" -j ACCEPT && log "iptables: udp/$MCM_PORT"
    iptables -I INPUT -s "$MCM_NET" -p tcp --dport "$MCM_PORT" -j ACCEPT && log "iptables: tcp/$MCM_PORT"
    iptables -I INPUT -s "$MCM_NET" -p udp --dport "${rtp_lo}:${rtp_hi:-$rtp_lo}" -j ACCEPT && log "iptables: RTP udp $MCM_RTP"
  else
    warn "не нашёл ни ufw, ни iptables — откройте вручную: $MCM_NET, $MCM_PORT tcp/udp, RTP $MCM_RTP/udp"
  fi
else
  log "файрвол не трогал (--firewall). Нужные правила: разрешить $MCM_NET → $MCM_PORT udp+tcp и RTP $MCM_RTP/udp"
fi

# ------------------------------------------------------- применить и проверить
if have_asterisk; then
  "$ASTERISK_CMD" -rx "module reload res_pjsip.so" >/dev/null 2>&1 \
    && log "res_pjsip перечитан" \
    || warn "module reload res_pjsip.so не прошёл (systemctl status asterisk)"
  log "жду регистрацию у $MCM_HOST (до ${REG_TIMEOUT}с)…"
  ok=0
  for _ in $(seq 1 $((REG_TIMEOUT / 4 + 1))); do
    if registration_state | grep -Eqi "200|registered|avail|bound"; then ok=1; break; fi
    sleep 4
  done
  if [ "$ok" = "1" ]; then
    log "регистрация установлена. Дальше: в UI «Проверить Asterisk и транк» должно стать OK."
  else
    warn "регистрации нет. Проверьте: логин/пароль; UDP 5060 наружу; правило на $MCM_NET (--firewall); транспорт 0.0.0.0:$MCM_PORT."
    registration_state | head -8 | sed 's/^/      /'
    exit 1
  fi
else
  warn "Asterisk не установлен — файл записан, применять его пока нечем: apt-get install -y asterisk asterisk-cli espeak-ng"
fi

if [ "$WANT_ATS" = "1" ]; then
  apply_ats_settings
  if [ "$WANT_RESTART" = "1" ]; then
    if command -v systemctl >/dev/null 2>&1; then
      systemctl restart ats 2>/dev/null && log "сервис ats перезапущен (провайдер пересоздан)" \
        || warn "systemctl restart ats не прошёл — выполните вручную"
    else
      warn "systemctl не найден — перезапустите АТС сами, иначе settings.ami не применятся"
    fi
  else
    log "провайдер пересоздаётся сразу (settings/raw дёргает reload_settings); рестарт"
    log "понадобится только если меняли /etc/ats/ats.env или сам юнит"
  fi
fi

if [ "$WANT_NUMBERS" = "1" ]; then
  import_numbers || warn "номера завелись не все — проверьте ATS_TOKEN и доступность $ATS_URL"
else
  log "номера из письма пока не заведены: перезапустите с --numbers (или добавьте во вкладке «Номера» с провайдером $MCM_NUMBER_PROVIDER)."
fi

log "готово. Следующие шаги — docs/MULTICOM_SIP_CONNECT.md: §6 диалплан, §7 AMI, §8 провайдер ami, §9 пул из 15 номеров (provider=ami)."
