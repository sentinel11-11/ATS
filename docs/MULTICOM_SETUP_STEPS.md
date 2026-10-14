# Мультиком: подключение «с нуля» — пошагово, с проверкой на каждом шаге

Для сервера, на котором **ещё ничего не применялось**. Каждый шаг: зачем → команды →
что должно получиться → что делать, если не получилось. Порядок важен: сначала диагностика,
потом изменения. Все пути — из клона `/opt/ats/app` (юнит `ats.service`, данные в `/var/lib/ats`).

Данные, которые понадобятся (из письма оператора): SIP-логин (`00083819` в нашем письме),
пароль регистрации, адрес регистратора и порт (`95.128.224.47:5060`), сеть оператора
(`95.128.224.0/21`), кодеки, число каналов, список DID-номеров, формат номера.
Формат объяснений цифр и зачем что нужно — `docs/MULTICOM_SIP_CONNECT.md` §13 и §1.

---

## Шаг 0. Проверить, что версия кода годится

```bash
cd /opt/ats/app
git rev-parse --abbrev-ref HEAD && git log -1 --oneline
grep -c -- '--dialplan' deploy/asterisk/install-multicom-trunk.sh    # должно быть >= 1
grep -c -- '--numbers'  deploy/asterisk/install-multicom-trunk.sh    # >= 1
```

Если файла скрипта нет или `--dialplan` не находится — код старый, идите на шаг 2 и возвращайтесь.
**Ничего не правьте руками до шага 6:** `pjsip-multicom.conf`, который вы создали сами, будет перезаписан.

## Шаг 1. Диагностика «как есть» (ничего не меняем)

```bash
cd /opt/ats/app
git status --short | head                 # локальные правки? (data_v2/* и app/ui — норма)
sudo systemctl cat ats | sed -n '1,20p'  # юнит: пользователь, порт, EnvironmentFile
curl -s http://127.0.0.1:9124/api/v2/health; echo
which asterisk || echo "asterisk нет"     # на «сыром» сервере его обычно нет
ls /etc/asterisk 2>/dev/null | head       # есть ли уже конфиги, что в них #include
ss -lunp | grep -E ':5060|:5038' || echo "5060/5038 свободны"
python3 -c "import sqlite3;db=sqlite3.connect('/var/lib/ats/ats.db');print('numbers:',db.execute('select count(*) from numbers').fetchone()[0]);print('provider:',sqlite3.connect('/var/lib/ats/ats.db').execute(\"select json_extract(data,'\$.provider') from settings where id=1\").fetchone()[0])"
```

Признак того, что всё понятно: вы видите текущий `provider` (например `megafon_vats` или пустой),
число номеров и что Asterisk не установлен. Запишите это — на шаге 13 сравниваете с «стало».

## Шаг 2. Подтянуть код и перезапустить сервис

```bash
cd /opt/ats/app
sudo cp -a /opt/ats/app /opt/ats/app.bak-$(date +%F)     # страховка на случайconflict
git fetch origin
git stash push -m before-multicom -- app/ui data_v2 2>/dev/null || true   # только если git ругается
git checkout arena/01a0cdee-ats && git pull --ff-only
./deploy/update.sh arena/01a0cdee-ats
curl -s http://127.0.0.1:9124/api/v2/health; echo        # "ok": true
```

`update.sh` сам делает `pip`-шаг, сборку UI (если менялся `frontend/src`), прогон тестов,
перезапуск и проверку порта; `exit 3` = обновилось, но сервис не перезапущен. Откат —
`./deploy/update.sh --rollback`.

Если `git checkout` говорит «local changes would be overwritten» — не злите git:
`git checkout -- app/ui && git clean -fd app/ui/assets` (пересоберём на шаге 2) и повторите.
**`git clean -fdx` не выполнять ни при каких обстоятельствах** — удалит `venv/` и локальные данные.

## Шаг 3. Установить телефонию

```bash
sudo apt-get update
sudo apt-get install -y asterisk asterisk-config asterisk-modules \
                        asterisk-core-sounds-en-wav espeak-ng
asterisk -V                       # в Ubuntu 24.04 (noble) это Asterisk 20.6
sudo systemctl enable --now asterisk
sudo asterisk -rx "core show version"
```

Важно про имена пакетов: **в Ubuntu 24.04 пакета `asterisk-cli` нет** — `asterisk -r` и
`asterisk -rx` входят в сам пакет `asterisk` (в Debian и Ubuntu 22.04 `asterisk-cli` был
отдельным пакетом, отсюда и `E: Unable to locate package`). Если apt всё же ругнётся на
любое имя из списка, ставьте по одному: `apt-get install -y asterisk`, потом остальное.
Звуки (`asterisk-core-sounds-en-wav`) нужны тестовому `Playback(tt-monkeys)` и
`vm-noavail` из диалплана; `asterisk-config` даёт базовые `/etc/asterisk/*.conf`, без него
не будет `extensions.conf`/`pjsip.conf`, куда скрипт добавляет `#include`.
`asterisk-espeak` ставить не нужно: озвучка у нас идёт через AGI + `espeak-ng`.

Признак успеха: `core show version` отвечает без «Unable to connect». Если Asterisk стартует и
тут же падает — `sudo journalctl -u asterisk -n 30`. `espeak-ng` нужен озвучке в потоке
`flow=message`; без него такой поток честно упадёт в `no_media`.

**G.729.** В пакет Ubuntu 24.04 проприетарный `codec_g729` не входит. Если в письме указан
G.729, а лицензии/модуля у вас нет — оставьте `MCM_CODECS=alaw,ulaw`: строка `allow=g729`
в конфиге не ошибка, но согласовать этот кодек Asterisk не сможет, и при жёстком требовании
оператора звонок сорвётся. Проверяется так: `sudo asterisk -rx "module show like codec_g729"`.

## Шаг 4. Осмотреть `/etc/asterisk`, чтобы не унаследовать чужой транспорт

```bash
grep -n "include\|^;" /etc/asterisk/pjsip.conf | head
grep -rn "transport-udp\|^bind" /etc/asterisk/pjsip*.conf | head
sudo asterisk -rx "module show like res_pjsip" | head -5
```

Признак успеха: в `pjsip.conf` нет собственной секции `[transport-udp]` (наш файл добавит её).
Если есть — уберите/переименуйте чужую, иначе `module reload` не применит ничего:
`asterisk -rx "module reload res_pjsip.so"` молча оставит старый транспорт.

## Шаг 5. Пробный рендер — посмотреть, что скрипт соберёт

```bash
cd /opt/ats/app
set +o history
read -s -p "SIP-пароль из письма: " MCM_PASS; echo
export MCM_PASS
sudo -E ./deploy/asterisk/install-multicom-trunk.sh --print | head -40
sudo -E ./deploy/asterisk/install-multicom-trunk.sh --dry-run
unset MCM_PASS
```

`read -s` — пароль не попадёт ни в историю, ни в `ps` (в отличие от
`MCM_PASS='…' command`, где он виден в аргументах). Экспорт обязателен: `sudo -E env MCM_PASS …`
не находит переменную, заданную только для одной команды, — отсюда ошибка
`env: «MCM_PASS»: No such file or directory`.

Признак успеха: в `--print` видны `auth_type=userpass`, `username=…`, `server_uri=sip:…:5060`,
`dtmf_mode=rfc4733`, `allow=alaw`, `allow=ulaw`,
`match=<IP>/32`; `--dry-run` печатает только план (куда что ляжет), сам конфиг с замаскированным
`password=` смотрите в `--print`, полное значение — `--print --reveal`. Ни одной метки `__MCM_`
не осталось.
`--dry-run` не должен писать «добавлю #include», если include уже есть.

## Шаг 6. Применить всё разом

```bash
cd /opt/ats/app
export MCM_PASS          # уже введено на шаге 5 read -s; иначе повторите read -s …
export AMIPASS=$(openssl rand -hex 24)
export ATS_TOKEN=$(sudo ./deploy/ats-token.sh)   # спросит логин/пароль, напечатает токен
[ -n "$ATS_TOKEN" ] && echo "токен есть" || echo "ТОКЕН ПУСТ — см. stderr ats-token.sh"
sudo -E ./deploy/asterisk/install-multicom-trunk.sh \
     --dialplan --firewall --ami --ats --numbers
set -o history
```

Проверка токена обязательна: с пустым `ATS_TOKEN` АТС отвечает `auth_required`, и настройки
просто не запишутся. `deploy/ats-token.sh` печатает токен на stdout, а причины неудачи —
в stderr: 403 (неверный логин/пароль) или `429 too_many_attempts` (защита от перебора:
подождать `retry_after_sec`). Ручная замена хелперу — `POST /api/v2/auth/login` и поле
`token` из ответа.

Хелпер умеет читать и со stdin — удобно, когда TTY нет (команда внутри `$(…)`, tmux-буфер,
paste из чата): `printf 'admin\nПАРОЛЬ\n' | ./deploy/ats-token.sh`. `403 bad_login` означает,
что пароль ввели не тот (в том числе буквально `ПАРОЛЬ_…` из примера), — сам хелпер в этом
не виноват. Пароль администратора АТС не помните (или логин заблокирован после перебора)? Сбросьте его с консоли —
команда и подробности в `docs/SECURITY_CLEANUP.md` §2:

```bash
sudo -u ats ATS_DATA_DIR=/var/lib/ats /opt/ats/app/venv/bin/python -m app.run --set-password admin
sudo systemctl restart ats
```

**Прерванный прогон целиком не повторяют.** Файлы транка и диалплана уже лежат, пароль AMI
сгенерирован и записан — значит не хватает только `--ats`/`--numbers`:

```bash
export ATS_TOKEN=$(printf 'admin\n' | ./deploy/ats-token.sh)   # или обычный запуск без pipe
sudo -E ./deploy/asterisk/install-multicom-trunk.sh --ats --numbers --restart
```

`--ami` повторно не передавайте — он генерирует новый пароль и перезаписывает
`/etc/ats/ats.env` (старый звонить перестанет, пока АТС не перезапустится).

**Если по этому же серверу уже идёт боевой обзвон через другого оператора** (в проде так и
было: `provider=megafon_vats`), переключать провайдера сразу нельзя — дозовите сначала
`ami/check`. Замените последнюю строку на:

```bash
export ATS_KEEP_PROVIDER=1
sudo -E ./deploy/asterisk/install-multicom-trunk.sh \
     --dialplan --firewall --ami --ats --numbers
```

Тогда `settings.ami` запишется, а `provider` останется прежним; переключаете потом —
Настройки → «Провайдер/CRM» → «Asterisk AMI» (или `POST /api/v2/settings/raw` с ключом
`provider`), тем же способом и откатываетесь.

Что происходит построчно в логе: рендер `pjsip-multicom.conf` (0640 `root:asterisk`, бэкап
предыдущего) → `#include` в `pjsip.conf` → `extensions-ats.conf` + `ats-tts.conf` +
AGI `ats_say.py` + каталоги `/var/spool/asterisk/{monitor/ats,ats}` + `#include` в
`extensions.conf` + `cron /etc/cron.d/ats-records` → правила на сеть оператора → пользователь
AMI `ats` в `manager-ats.conf` + `ATS_AMI_SECRET` в `/etc/ats/ats.env` → настройки АТС через API
(`provider=ami`, `trunk=mcm`, `ru8`/`d10`, контексты) → 15 номеров в пул с `provider=ami`.

Признак успеха: скрипт завершился кодом 0, в конце «регистрация установлена», и он же выдал
строку `пароль AMI записан в /etc/ats/ats.env`.

Код 1 и «регистрации нет» — **не** повод останавливаться: настройки АТС, диалплан, AMI-пользователь
и номера при этом записаны (раньше скрипт обрывался на ожидании регистрации, и `--ats`/`--numbers`
не выполнялись — это исправлено). Продолжайте с шага 7а, а к регистрации вернётесь отдельно;
`REG_TIMEOUT=0` — пропустить ожидание совсем.

Пароль AMI из вывода (если он печатался) скопируйте в менеджер паролей и закройте вкладку.
Поскольку `MCM_PASS` вводился через `read -s`, а история на время отключена, в
`~/.bash_history` секретов быть не должно; проверить: `grep -c MCM_PASS ~/.bash_history` → 0.

## Шаг 7. Транк и регистрация

```bash
sudo asterisk -rx "pjsip show transports"     # transport-udp  Active  0.0.0.0:5060
sudo asterisk -rx "pjsip show endpoints"      # mcm → mcm/mcm-aor
sudo asterisk -rx "pjsip show aors"           # Avail
sudo asterisk -rx "pjsip show contacts"       # 95.128.224.47 … Avail
sudo asterisk -rx "pjsip show registrations"  # <Registration: OK> … 200 OK
sudo tail -30 /var/log/asterisk/messages.log | grep -iE "regist|40[13]|pjsip"
```

Признак успеха: `200 OK` и контакт `Avail`. `403` — логин/пароль или нас не пустили по IP
(спросить оператора, какой IP они видят: `curl -s ifconfig.me`); `408`/пусто — UDP 5060 наружу
или обратное правило на их сеть; контакт есть, но `Non-Corr` — нормально, корреляция появится
после первого звонка.

### 7а. «No objects found»: сначала разделим две причины

Это **не** «оператор нас не пустил»: Asterisk либо не увидел файл, либо увидел и отверг
объект. Разбирается одной командой — посмотрите, есть ли транспорт:

```bash
asterisk -rx "pjsip show transports"      # есть transport-udp?
asterisk -rx "pjsip show endpoints"
asterisk -rx "pjsip show aors"
```

**Случай A — транспорт ЕСТЬ, а endpoint'а нет: объект не прошёл валидацию.** Ровно то, что
поймали на проде. Причина — `auth_name=` в `[mcm-auth]`: поле `auth_name` было у объекта
`auth` в Asterisk 12/13, с 16-й версии его нет, а `username` обязателен и непустой
(`res/res_pjsip/config_auth.c` — «`mcm-auth`: No authentication username»). Auth не создан —
и следом не создаются `[mcm]` (`outbound_auth=mcm-auth`) и `[mcm-reg]` (тот же auth): в CLI
один транспорт и два пустых списка. Шаблон исправлен (`auth_type=userpass` + `username=`),
`--check` теперь ругается на `auth_name` в установленном файле. Лечение — обновить репозиторий
и перегенерить файл:

```bash
cd /opt/ats/app && git pull --ff-only origin arena/01a0cdee-ats
sudo -E ./deploy/asterisk/install-multicom-trunk.sh
asterisk -rx "pjsip show endpoints" ; asterisk -rx "pjsip show registrations"
```

**Случай B — нет ни транспорта, ни endpoint'а: файл вообще не прочитан.** Проживший пример с
коробки: `/etc/asterisk/pjsip.conf` в Ubuntu — километровый закомментированный образец, и
дописанная в его хвост строка `#include` попадает внутрь чужой секции. Asterisk на это не
жалуется — он просто не применяет включение. С этой версии скрипт ставит `#include` **первой
строкой** и сам проверяет после reload, что endpoint появился.

```bash
head -1 /etc/asterisk/pjsip.conf      # должно быть: #include pjsip-multicom.conf
ls -l /etc/asterisk/pjsip-multicom.conf             # 0640 root:asterisk
sudo -u asterisk head -3 /etc/asterisk/pjsip-multicom.conf   # демон действительно может читать
asterisk -rx "module reload res_pjsip.so"
asterisk -rx "pjsip show endpoints" ; asterisk -rx "pjsip show transports"
```

Если после перезапуска демона всё равно пусто — оставляем в `pjsip.conf` только наше включение
(оригинал скрипт сохранит рядом как `pjsip.conf.dist-<stamp>`):

```bash
sudo -E ./deploy/asterisk/install-multicom-trunk.sh --pjsip-standalone
asterisk -rx "pjsip show endpoints" ; asterisk -rx "pjsip show registrations"
```

**Случай C — транспорт и AOR есть, а endpoint'а и регистрации нет: поле не прошло ACO.**
Asterisk разбирает секцию построчно и на первой непонятной опции бросает **категорию целиком**
(`aco_process_category_options` → `return -1`), поэтому в логе одна строка, а в CLI — пусто.
Ровно так было на проде, уже после починки `username`:

```
ERROR config_options.c: Error parsing dtmf_mode=rfc2833 at line 65 of /etc/asterisk/pjsip-multicom.conf
ERROR res_sorcery_config.c: Could not create an object of type 'endpoint' with id 'mcm' from configuration file 'pjsip.conf'
ERROR config_options.c: Could not find option suitable for category 'mcm-reg' named 'line_request' at line 42
ERROR res_sorcery_config.c: Could not create an object of type 'registration' with id 'mcm-reg' ...
```

Обе строки — наследие вики оператора, она описывает chan_sip и Asterisk 10-16:

| Как в вики | Как в chan_pjsip на Asterisk 20 |
|---|---|
| `dtmf_mode=rfc2833` | `rfc4733` (RFC 4733 отменил RFC 2833); всего допустимо `auto, auto_info, info, inband, none, rfc4733` |
| `line_request=registration` | такой опции нет вовсе; `line=yes` + `endpoint=mcm` ставят осознанно, если device state endpoint'а должен следовать за регистрацией |
| `auth_name=` | `username=` плюс `auth_type=userpass` |
| `secret=`, `insecure=`, `type=peer`, `directmedia=`, `dtmfmode=` | объект `auth` с `password=` и ссылка `outbound_auth=`; `force_rport`+`rewrite_contact`; `direct_media`; `dtmf_mode` |

Скрипт больше не дожидается встречи с логом: `validate_against_asterisk20` сверяет рендер со
списком запрещённых строк **до записи** и отказывается ставить нерабочий конфиг, а `--check`
повторяет ту же проверку по установленному файлу.

Объектов по-прежнему нет — ищем точную строку ошибки в логе (в `--check` и сразу после reload
скрипт печатает её сам). В Ubuntu 24.04 пишет
`/var/log/asterisk/messages.log.log` (файлов `messages` и `full` там обычно нет; список файлов и
что в них лить задаёт `/etc/asterisk/logger.conf`):

```bash
ls -l /var/log/asterisk/
grep -iE "pjsip|syntax|error" /var/log/asterisk/messages.log | tail -30
```

И последний аргумент, когда лог молчит: запустить Asterisk на переднем плане — он сам скажет,
какие файлы читает и на чём споткнулся:

```bash
asterisk -rx "core show settings" | grep -iE -m4 "astetcdir|config"   # каталог = /etc/asterisk?
sudo systemctl stop asterisk
sudo -u asterisk /usr/sbin/asterisk -vvvvc      # читаем экран; выход — Ctrl-]
sudo systemctl start asterisk
```

`-c` без пайпа намеренно: с `| grep` консоль не интерактивная и `Ctrl-]` не работает. Если
запуск на переднем плане отвечает «Another Asterisk instance is running» — значит второй
экземпляр всё-таки есть, и `asterisk -rx` говорит именно с ним, а не с тем, что читает ваши
конфиги. Проверьте `ps aux | grep -c '[a]sterisk'` и `systemctl status asterisk`.

В норме на экране есть `Parsing '/etc/asterisk/pjsip.conf'` и ошибка на строке, если включение
не применилось. Строки `pjsip-multicom.conf` нет вообще — включение не дошло (проверьте
`astetcdir` в `/etc/asterisk/asterisk.conf` и запускайте `--pjsip-standalone`).

**Случай D — все объекты есть, но `pjsip show registrations` = `Unregistered` или `Rejected`.**
Это наш финальный случай на проде: endpoint, aor, identify и auth на месте, ACO-ошибок в логе
нет — значит конфигурация наша закончена и дальше только регистратор оператора. Статусы:
`Registered` и `Expiring` — работает, `Unregistered` — ответа нет вообще, `Rejected` — ответ
пришёл и нам отказали (значит не «сети нет»: `401` = пароль/логин, `403` = аккаунт не
активирован или IP не в allowlist). Проверяется так:

```bash
grep -aE "No response received|Fatal response|Forbidden|Maximum retries|stopping outbound registration" \
  /var/log/asterisk/messages.log | tail -10
asterisk -rx "pjsip show registrations"          # дважды, с интервалом ~70 секунд
timeout 90 tcpdump -nn -i any -s 0 -w /tmp/sip5060.pcap 'udp port 5060 and host 95.128.224.47'
grep -ac "REGISTER sip" /tmp/sip5060.pcap
grep -aoE "SIP/2.0 [0-9]{3}" /tmp/sip5060.pcap | sort | uniq -c
```

Первая команда важнее всех остальных: что именно произошло — пишет в лог сам Asterisk, и `--check`
эти строки печатает. Три ловушки, на которых мы и стояли:

1. `Rejected` **не** означает «отказали по паролю». Так же CLI подписывает случай, когда ответа
   нет вовсе и `max_retries=10` исчерпан — на проде в логе было `No response received … retrying
   in '60'` ×3, потом `Maximum retries reached … stopping registration attempt`.
2. После `stopping registration attempt` попытки прекращаются: статус замирает, `exp.` только
   растёт, и новый захват пакетов пуст — это мы молчим, а не они. Возвращает попытки
   `asterisk -rx "module reload res_pjsip.so"`, а шаблоновые `auth_rejection_permanent=no`,
   `forbidden_retry_interval=60`, `fatal_retry_interval=60`, `max_retries=100000` делают это
   насовсем. Эти поля появляются в файле только после перегенерации
   (`sudo -E ./deploy/asterisk/install-multicom-trunk.sh` без флагов); `--check` не пишет, но
   скажет «нет полей, которые есть в шаблоне репозитория», если файл старый.
3. `tcpdump -A | grep REGISTER` непригоден: ASCII-дамп переносит строки каждые 16 байт, слово
   `REGISTER` рвётся, и grep молчит при живом трафике (11 пакетов в захвате — 0 совпадений).
   Поэтому `-w` в файл и `grep` по файлу.

`401` в логе — сверять `username`/`password` с письмом; `403` — письмо оператору; `No response
received` — тоже письмо оператору, но сначала `ip route get 95.128.224.47` (исходящий адрес) и
`timeout 6 bash -c '</dev/tcp/95.128.224.47/5060'; echo "tcp=$?"` (124 = фильтруют, 0 = открыть
TCP-транспорт). Формулировки — §12а и §13. Обратите внимание:
`pjsip show endpoints` при этом показывает `State: Unavailable` — для транка это норма (у
исходящего транка нет входящего контакта), на набор `PJSIP/<номер>@mcm` не влияет.

## Шаг 8. AMI: доступ АТС к Asterisk

```bash
sudo asterisk -rx "manager show user ats"       # Inbound/Read/Write, permit 127.0.0.1
sudo ss -lntp | grep :5038                      # слушается
curl -s -X POST -H "X-Ats-Token: $TOK" http://127.0.0.1:9124/api/v2/ami/check | python3 -m json.tool
```

Признак успеха: в `ami/check` `ok: true`, зелёные пункты `asterisk`, `manager_login`,
`registration`, `dialplan`, `numbers_active` (=15). Пункт `registration` сверяет точные слова
статуса (`Registered`/`Expiring`/`Avail`), поэтому при `Unregistered` он честно красный — и
красный он будет, пока Мультиком не ответил на REGISTER, даже если AMI полностью рабочий. Если
лог Asterisk читается, в детали пункта так же, как в `--check`, печатается строка с причиной
(`Fatal response '401'` = пароль, `403 Forbidden` = активация/IP, `stopping outbound
registration` = попытки кончились и нужен reload) — смотреть её удобнее в JSON, чем переключаться
между окнами: `curl -s -X POST -H "X-Ats-Token: $TOK" http://127.0.0.1:9124/api/v2/ami/check |
python3 -m json.tool | grep -A3 registration`. Пункт `secret` укажет источник пароля —
`ATS_AMI_SECRET` или настройки. Если `manager_login` красный — пароль в `ats.env` и в
`manager-ats.conf` разошлись (перечитать: `sudo systemctl restart ats`).

## Шаг 9. Что видит сама АТС

```bash
curl -s -H "X-Ats-Token: $TOK" http://127.0.0.1:9124/api/v2/settings/raw | python3 -m json.tool | sed -n '/"ami"/,/}/p'
curl -s -H "X-Ats-Token: $TOK" http://127.0.0.1:9124/api/v2/health/details \
  | python3 -m json.tool | sed -n '/"telephony"/,/}/p'
```

Ожидание: `provider: "ami"`, `trunk: "mcm"`, `number_format: "ru8"`, `caller_id_format: "d10"`,
`context: "ats-out"`, `inbound_contexts: "from-mcm,ats-in"`, `record_calls: true`,
`secret: "********"` (или `""` — тогда секрет берётся из `ats.env`, так и задумано). В
`/api/v2/health/details` (только admin) видно `telephony: {provider: "ami", connected: true}` —
это и есть «движок реально работает через Asterisk». UI: Настройки → «Провайдер/CRM» → «Asterisk AMI
Manager» — те же поля, кнопка «Проверить Asterisk и транк» = шаг 8.

## Шаг 10. Номера

```bash
curl -s -H "X-Ats-Token: $TOK" "http://127.0.0.1:9124/api/v2/numbers" | python3 -m json.tool | head -30
sqlite3 /var/lib/ats/ats.db "select number,provider,active,daily_limit from numbers order by id" 2>/dev/null \
  || echo "sqlite3 нет — смотрите вкладку «Номера»"
```

Признак успеха: 15 строк, `provider=ami`, `active=1`. Если установщик выдал
`добавлено 0 … проблем 15` или «АТС не отвечает на …/api/v2/health» — это не отказ API, а окно
перезапуска: `systemctl restart ats` закрывает порт на 1–3 с, и запросы в этом окне терялись
(на проде так и пропали все 15 номеров). Скрипт теперь проверяет, что сервис отвечает, прежде
чем писать в пул, а `--restart` выполняется в самом конце; завести только номера —
`--numbers-only` при тех же `ATS_TOKEN` (и `ATS_URL`, если он нестандартный). Движок берёт номера строго по
`numbers.provider` (`numbers.acquire(provider=…)`), поэтому номер с меткой `multicom`/`sim`
в работу не попадёт — это самая частая причина «кампания запущена, тишина».

## Шаг 11. Озвучка и запись (то, что «не работает» чаще всего)

```bash
ls -l /var/lib/asterisk/agi-bin/ats_say.py            # 0750 root:asterisk
sudo asterisk -rx "dialplan show ats-out"              # MixMonitor + AGI ats_say.py
sudo asterisk -rx "dialplan show from-mcm"              # Dial(PJSIP/101&PJSIP/102,25)
ls -ld /var/spool/asterisk/monitor/ats /var/spool/asterisk/ats   # 0770 asterisk:asterisk
cat /etc/cron.d/ats-records                             # раскладка записей раз в 5 минут
sudo -u asterisk /usr/bin/espeak-ng -v ru -s 150 -w /tmp/t.wav "проверка" && ls -l /tmp/t.wav && sudo rm -f /tmp/t.wav
```

Признак успеха: контексты видны, каталоги принадлежат `asterisk`, espeak пишет WAV.
Если оператор даёт добавочные сотрудников — проверьте, что `PJSIP/101`/`PJSIP/102` реально
существуют (`pjsip show endpoints`), иначе `Dial()` в `from-mcm` всегда дасть `NO ANSWER`.

## Шаг 12. Живые проверки: по одной на каждую функцию

```bash
# 1) канал до оператора — без АТС
sudo asterisk -rx "channel originate PJSIP/ВАШ_МОБИЛЬНЫЙ@mcm application Playback tt-monkeys"
# 2) исходящий из АТС: тестовая кампания на 1 номер, flow=message
# 3) тёплый перевод: flow=agent (или оператор на добавочном)
# 4) входящий: позвонить на один из 15 DID → журнал direction=in + задача в CRM
```

Для 2–3: создать кампанию с одним контактом (свой номер), `flow=message` с текстом,
запустить и смотреть:

```bash
watch -n2 'curl -s -H "X-Ats-Token: '"$TOK"' "http://127.0.0.1:9124/api/v2/calls?limit=3" | head -c 600; echo; sudo asterisk -rx "core show channels concise" | head'
```

Ожидание по статусам движка: `dialing → ringing → answered → wait_operator/done_ok`
(для `message` — `done_ok` только после окончания озвучки, `ATS_PLAY` выставляется AGI).
Отдельная проверка записи: через ≤5 минут после звонка файл в `/var/spool/asterisk/monitor/ats/call-<id>.wav`
и в журнале появилась ссылка; в `journalctl -u ats` — «запись присоединена».

## Шаг 13. Убрать страховки стенда и зафиксировать состояние

```bash
# отключить тихий откат на симулятор (иначе «не работает» превратится в «симулируется»)
sudo grep -n ATS_ALLOW_SIM_FALLBACK /etc/systemd/system/ats.service /etc/ats/ats.env 2>/dev/null
sudo systemctl edit ats        # в [Service] закомментировать/удалить Environment=ATS_ALLOW_SIM_FALLBACK=1
sudo systemctl daemon-reload && sudo systemctl restart ats
./deploy/check.sh ; curl -s http://127.0.0.1:9124/api/v2/health; echo
```

Порт 5060 сейчас открыт наружу (`ufw: Status: inactive`), и лог это подтверждает — чужие хосты
уже шлют REGISTER на ваш IP: «Request 'REGISTER' from '<sip:433@89.223.127.145>' failed for
'45.61.184.132:51366' — No matching endpoint found». Пока своих endpoint'ов нет, отказ дешёвый;
с рабочим транком это обычная попытка угнать минуты через ваш аккаунт. Включать файрвол так
(первое правило — чтобы не потерять SSH):

```bash
ufw default deny incoming && ufw default allow outgoing
ufw allow OpenSSH
ufw allow from 95.128.224.0/21 to any port 5060 proto udp
ufw allow from 95.128.224.0/21 to any port 5060 proto tcp
ufw allow from 95.128.224.0/21 to any port 10000:20000 proto udp   # RTP
ufw enable && ufw status numbered
```

Последнее правило нужно потому, что `direct_media=no`: медиа всегда идёт через серверы
оператора, поэтому одного их диапазона сети достаточно. После включения `ufw` проверьте
тестовый звонок ещё раз — односторонний звук означал бы, что RTP-порт не совпал с `MCM_RTP`.

Признак приёмки (все пять пунктов — «да»):

1. `ami/check` → `ok: true`, `numbers_active: 15`;
2. `health/details` → `telephony: {provider: "ami", connected: true}`;
3. тестовый `flow=message` дошёл до `done_ok`, запись в журнале открывается;
4. тестовый `flow=agent` дождался оператора и зафиксировал `operator_id`/длительность;
5. входящий на DID создал запись `direction=in` и задачу «Перезвонить клиенту» в CRM.

## Шаг 14. Частые провалы (по коду проверок, а не по примете)

| Симптом | Где искать и что делать |
|---|---|
| `module reload` «успешен», но `pjsip show endpoints` пуст | §7а: сначала `pjsip show transports` — транспорт есть → объект не прошёл валидацию (в `[mcm-auth]` обязателен `username`, `auth_name` Asterisk 16+ не знает); транспорта нет → `#include` не первой строкой (дистрибутивный `pjsip.conf` его съедает), чинится `--pjsip-standalone`; затем `grep -rn "\[mcm\]" /etc/asterisk/` на дубль секции |
| `401/403` на регистрации | пароль (не тот, что AMI!) или оператор не добавил ваш IP в whitelist: `curl -s ifconfig.me` → в письмо оператору |
| Один звук / нет звука | RTP: `MCM_RTP` и `local_net`/`external_signaling_address` в `[transport-udp]`; правило на `95.128.224.0/21` для `10000-20000/udp`; `rtp.conf` |
| `dialplan show ats-out` пусто | не запускали `--dialplan` (или нет `#include extensions-ats.conf`) → повторить шаг 6 с `--dialplan` |
| `no_media` в `flow=message` | нет `espeak-ng` или AGI: см. шаг 11; проверьте `ats_say.py` и `ats-tts.conf` |
| Кампания «в ожидании номера» | шаг 10: `provider` у номеров ≠ `ami`, `active=0`, или `daily_limit` исчерпан (`numbers.daily_count`), или карантин после жалоб |
| `manager_login` красный | пароль: сверить `manager-ats.conf` и `ats.env`, `sudo asterisk -rx "module reload manager.cfm"`, `sudo systemctl restart ats` |
| AMI вообще недоступен с другого хоста | так и задумано: `permit=127.0.0.1`; для удалённого Asterisk — туннель (`ssh -N -L 5038:127.0.0.1:5038 host`), TLS в AMI-клиенте АТС нет |
| Записи нет в журнале | `attach_recordings.py` не отработал: `/var/log/ats-records.log`, права на каталог, `ATS_URL` в `/etc/ats/ats.env` |

## Шаг 15. Откат

```bash
cd /opt/ats/app
# конфиг транка: вернуть предыдущую версию (скрипт оставляет .bak-<stamp>)
sudo cp -a /etc/asterisk/pjsip-multicom.conf.bak-<stamp> /etc/asterisk/pjsip-multicom.conf
sudo asterisk -rx "module reload res_pjsip.so"
# настройки АТС: вернуть провайдера, который был до (шаг 1)
curl -s -X POST -H "X-Ats-Token: $TOK" -H 'Content-Type: application/json' \
  -d '{"provider":"megafon_vats"}' http://127.0.0.1:9124/api/v2/settings/raw
./deploy/update.sh --rollback      # если нужно вернуть и код
```

`ats.env` и `manager-ats.conf` скрипт не удаляет — пароль AMI можно оставить: он больше
никому не мешает, а при возврате на `ami` понадобится снова.
