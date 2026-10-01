# Мультиком через SIP-транк: как подключить всё и как проверить

Документ про то, как завестить номера Мультиком (МСТ «Мультифон») в этой АТС,
когда у оператора **нет REST API и вебхуков** — а есть только данные SIP-регистрации.
Это ровно тот случай, который описан в письме оператора (см. §1).

Соседние документы: `docs/MULTICOM_INTEGRATION.md` (REST-адаптер агрегатора, если
API всё-таки дадут), `docs/AMOCRM_INTEGRATION.md` (CRM), `docs/DEPLOY.md` (сервер и
обновление), `docs/ADMIN_LOGS.md` (журналы).

---

## 1. Почему через Asterisk, а не через API

Из письма Мультиком следует только это:

| Дано | Значение |
|---|---|
| Логин SIP-регистрации | `00083819` |
| Пароль | `9xF4txEyRK` (в репозиторий не коммитить!) |
| Сервер регистрации | `95.128.224.47`, порт `5060`, UDP/TCP |
| Кодеки | G.711 (a-law/u-law), G.729 |
| Номера (DID) | 15 шт., например `9690229926`, `9863149340`, `9362574897` |
| Формат исходящего набора | `8_КодГорода_Номер` (пример `84952588233`), международный `810_…` |
| B-номер входящего вызова | 10 цифр (`4952588233`) |
| «Тишина» в одну сторону | открыть в файрволе сеть `95.128.224.0/21` |
| Wiki оператора | `http://95.128.229.229/wiki/index.php` (страница «Asterisk») |

Ключевое: **REST API, ключа авторизации и webhook-эндпоинта оператор не выдавал**.
Значит провайдер `multicom` (`app/providers/multicom.py`, см. `docs/MULTICOM_INTEGRATION.md`)
применить не к чему — ему некуда слать `makecall` и неоткуда принимать статусы.

Рабочая схема для такого набора данных одна:

```
     движок ATS  ──AMI (5038)──►  Asterisk  ──SIP/RTP──► 95.128.224.47 (Мультиком) ──► абонент
        ▲                          │
        └───── события/запись ─────┘
```

ATS управляет Asterisk по Manager Interface (провайдер `ami`, `app/telephony.py` →
`AsteriskAmiProvider`, клиент AMI — `app/asterisk.py`), а голос, кодеки, DTMF, запись и
входящие — ответственность Asterisk. Из этого следует честный список возможностей:

**Работает полностью:** исходящие по кампании, автодозвон и ретраи по причинам
(busy/no_answer/…), лимиты/кулдаун/карантин на каждый номер, тихие часы и согласие
(152-ФЗ), потоки `operator` (очередь АЦД + перевод на сотрудника), `agent` (ИИ-скрининг),
`message` (озвучка текста через AGI), входящие с DID (журнал + пропущенный → задача
перезвонить + карточка в amoCRM), запись разговоров, статусы в amoCRM, веб-интерфейс.

**Не работает (и не может без API оператора):** «номера и баланс в интерфейсе» из
личного кабинета (только вручную), внешний CDR/запись на стороне Мультиком,
досброс вызова REST-командой, подтверждение «доставлено» от оператора.

---

## 1а. Что настраивается через интерфейс, а что — только на сервере

Частый вопрос: «почему нельзя мышкой?» Он про то, где кончается ответственность
приложения. АТС осознанно **не умеет** трогать систему: во всём `app/` нет ни
`subprocess`, ни `os.system`, ни записи куда-либо вне `ATS_DATA_DIR` — только SQLite
и свои настройки. Плюс сервис работает под `User=ats` и прав на `/etc/asterisk` не
имеет физически. Это не недоделка, а граница: веб-морда смотрит в интернет через
nginx, и «кнопка, которая пишет в chroot чужого сервиса и делает reload» — это
готовый путь к RCE для любого, кто завладеет токеном администратора.

Мышкой настраивается ровно то, что относится к АТС:

| Что | Где в UI | Ключ/эндпоинт |
|---|---|---|
| Провайдер (`ami`) | Настройки → Телефония | `settings.provider` |
| AMI: host/port/user/secret, имя транка, технология, `number_format`, `caller_id_format`, `context`, `dial_prefix`, `inbound_contexts`, `play_message`, `inbound_enabled` | Настройки → блок «Asterisk AMI Manager» | `settings.ami.*` |
| Пул из 15 DID (+провайдер номера, лимит, вес, кулдаун) | Вкладка «Номера» | `POST /api/v2/numbers/save` |
| Кампании, шаблоны, расписание, повтор | Вкладки «Кампании»/«Шаблоны» | `settings` + `campaigns` |
| Проверка связи с Asterisk и транком | Кнопка «Проверить Asterisk и транк» | `POST /api/v2/ami/check` |
| Реквизиты Мультикома как справка (sip_host/user/secret, API, секрет вебхука) | Блок «Интеграция с Агрегатором Мультиком» (§7а) | `settings.multicom.*` |
| amoCRM: токен, карта операторов, результаты | Настройки → CRM | `settings.amocrm.*` |

Только на сервере (и так правильно): `apt-get install asterisk espeak-ng`, файлы
`/etc/asterisk/*` (в том числе пароль транка и `#include`), `manager.conf` (пользователь
AMI), права `0640 root:asterisk`, файрвол и RTP-порты, `systemctl restart ats`.
Причина не в лени интерфейса: `pjsip.conf` — конфиг чужого сервиса со своей семантикой
(endpoint/auth/aor/registration/identify), одна лишняя точка с запятой в значении — и
телефония стоит, причём чинить её уже не через АТС.

Что имеет смысл добавить в UI, если хочется «максимум мышкой» (по возрастанию риска):

1. **Генератор конфига (read-only)**: форма «логин/пароль/сервер/кодеки» → готовый текст
   `pjsip-multicom.conf` кнопкой «скопировать» + чек-лист команд. Ошибок плейсхолдеров
   как не бывает, так и не появляется ничего опасного.
2. **Панель состояния Asterisk** на той же вкладке: регистрация, контакты, кодеки,
   активные каналы — всё это уже читается по AMI действием `Command`
   (`app/asterisk.py::run_command`, им же пользуется `ami/check`), то есть только чтение.
3. **Привилегированный хелпер** `ats-asterisk-apply` + одна строка в sudoers: принимает
   только фиксированный набор ключей, рендерит файл из шаблона репозитория (не из
   пользовательского текста), делает бэкап, `module reload res_pjsip.so`, проверяет
   регистрацию и сам откатывается при FAIL. Вот это и есть «настройка полностью из
   интерфейса», но root-действие при установке всё равно остаётся (положить файл и
   sudoers-строку), и запускаться оно должно по явной кнопке с подтверждением.
4. `apt-get install` из UI — сознательно не делаем: это пакетный менеджер root'а.

## 2. Что в коде под это заточено

Фактические точки, которые надо настраивать (не «когда-нибудь», а сейчас):

- `settings.provider = "ami"` — выбор транспорта телефонии (`app/telephony.py::make_provider`).
- `settings.ami.*` — хост/пользователь/секрет AMI, транк, форматы номеров, озвучка, входящие
  (`app/config.py`, блок `"ami"`; все поля редактируются в UI: *Настройки → Asterisk AMI Manager*).
- `AsteriskAmiProvider.dial()` — AMI `Originate`: канал `{tech}/{номер}@{транк}` (PJSIP) или
  `SIP/{транк}/{номер}` (chan_sip), `CallerID: "ats-call-<id>" <cli>`, переменные канала
  `ATS_CALL_ID`, `ATS_TEXT_B64`, `ATS_RECORD`.
- `format_outbound_number()` — форматы `ru8` (`8XXXXXXXXXX`, то, что требует Мультиком),
  `e164`, `d10`, `digits`, `raw`. Номер в базе остаётся E.164, оператору уходит в его формате.
- События AMI → события движка: `Newchannel`→ring, `Dial(ANSWER)`→answered,
  `Hangup`→done, `OriginateResponse(Failure)`→failed, `VariableSet(ATS_PLAY)`→ошибка озвучки.
- Входящие: `Newchannel` в контексте `settings.ami.inbound_contexts` и с каналом транка →
  `Engine._on_inbound()` создаёт запись журнала (`direction=in`, `external_call_id` = имя канала),
  `Newstate 6` → answered, `Hangup` → `done_ok`/`missed` (+ задача «Перезвонить клиенту»).
- Поток `message`: текст не «имитируется», а реально озвучивается AGI
  (`deploy/asterisk/agi/ats_say.py`), а `done_ok` появляется только после финала канала;
  потерянное событие Hangup добирает watchdog по `settings.message_max_sec`.
- Записи: диалплан пишет файлы, `deploy/asterisk/attach_recordings.py` (cron) раскладывает их
  по звонкам через `POST /api/v2/calls/recording`; в amoCRM уходит подписанная ссылка
  (`app/records.py`, `settings.records`).
- `POST /api/v2/ami/check` — чек-лист связи с Asterisk и транком без единого звонка
  (`app/telephony.py::ami_check`).

Шаблоны конфигов Asterisk лежат в `deploy/asterisk/`:

| Файл | Куда ставится |
|---|---|
| `pjsip-multicom.conf` | `/etc/asterisk/` + `#include` в `pjsip.conf` |
| `sip-multicom.conf` | `/etc/asterisk/` + `#include` в `sip.conf` (chan_sip, вариант «как у них в wiki») |
| `extensions-ats.conf` | `/etc/asterisk/` + `#include` в `extensions.conf` |
| `manager-ats.conf` | `/etc/asterisk/` + `#include` в конец `manager.conf` |
| `ats-tts.conf` | `/etc/asterisk/ats-tts.conf` |
| `agi/ats_say.py` | `/var/lib/asterisk/agi-bin/ats_say.py` (0755) |
| `attach_recordings.py` | `/opt/ats/deploy/asterisk/` (запуск по cron) |

---

## 3. Шаг 0 — сервер и сеть (половина всех «не работает» решается здесь)

Asterisk и ATS могут быть на одной машине (тогда AMI смотрит на `127.0.0.1`), могут на разных.

1. Публичный IPv4 (или белый IP + NAT с пробросом 5060/udp+tcp и RTP).
2. Разрешить вход от сети оператора (из их письма — `/21`, в их wiki на 5060 пущена `/25`):

```bash
# iptables (CentOS/старая Ubuntu)
iptables -I INPUT -p udp -s 95.128.224.0/21 --dport 5060 -j ACCEPT
iptables -I INPUT -p tcp -s 95.128.224.0/21 --dport 5060 -j ACCEPT
iptables -I INPUT -p udp -s 95.128.224.0/21 --dport 10000:20000 -j ACCEPT   # RTP от оператора
iptables -A INPUT -p udp --dport 5060 -j DROP        # остальной SIP-скан — в мусор
service iptables save

# nftables (Debian 12 / Ubuntu 22+)
nft add table ip mcm
nft add chain ip mcm in '{ type filter hook input priority 0 ; }'
nft add rule ip mcm in ip saddr 95.128.224.0/21 udp dport 5060 accept
nft add rule ip mcm in ip saddr 95.128.224.0/21 tcp dport 5060 accept
nft add rule ip mcm in ip saddr 95.128.224.0/21 udp dport 10000-20000 accept
nft add rule ip mcm in udp dport 5060 counter drop
```

3. AMI-порт (5038) наружу **не открывать никогда**. Если ATS на другой машине —
   `permit` в `manager.conf` + правило фаервола с их внутреннего IP.
4. NAT: если Asterisk за NAT, в `pjsip-multicom.conf` раскомментировать
   `external_signaling_address`/`local_net`, в `rtp.conf` — диапазон, и на NAT-шлюзе
   пробросить `10000-20000/udp` (иначе «слышно в одну сторону» или тишина).

Проверка сети до всякой телефонии:

```bash
nc -zvu 95.128.224.47 5060 && echo "udp 5060 ок"     # UDP «проверится» только по отсутствию ICMP
ip -4 addr show                         # понять, какой адрес увидит оператор
```

---

## 4. Шаг 1 — Asterisk

```bash
apt-get update && apt-get install -y asterisk asterisk-cli espeak-ng    # espeak = TTS для озвучки
asterisk -rx "core show version"
asterisk -rx "module show like chan_pjsip"      # должен быть res_pjsip + chan_pjsip
asterisk -rx "module show like codec_g729"      # пусто — значит G.729 нет (см. §12)
```

Если нужен G.729 (15 номеров × экономия канала) — по инструкции оператора ставится модуль
`codec_g729` (их wiki ссылается на `asterisk.hosting.lv`); без него хватает `alaw`/`ulaw`,
просто трафик в 2 раза больше.

## 5. Шаг 2 — транк на Мультиком

Рекомендуемый вариант — `chan_pjsip` (Asterisk 12+). Данные из письма вносит ОДИН скрипт:
он рендерит `deploy/asterisk/pjsip-multicom.conf` (шаблон с метками `__MCM_LOGIN__`,
`__MCM_PASS__`, `__MCM_HOST__`, `__MCM_PORT__`, `__MCM_NET__`, `__MCM_MAXCH__` и блоком
`allow=`) в `/etc/asterisk/pjsip-multicom.conf`, проверяет значения (перенос строки и `;`
ломали бы синтаксис конфига), ставит 0640 root:asterisk, делает бэкап предыдущего файла,
добавляет `#include` в `pjsip.conf`, перечитывает `res_pjsip` и ждёт `200 OK` от
регистрации. Пароль в репозиторий и в историю shell не попадает.

```bash
cd /opt/ats/app
MCM_PASS='***' sudo -E ./deploy/asterisk/install-multicom-trunk.sh
# варианты: --print (показать результат) | --dry-run | --check | --firewall (правила на
# сеть оператора) | --ami (пользователь AMI + сгенерированный пароль) |
# --numbers (15 номеров письма в пул ATS с provider=ami) | --ats (settings.ami и
# provider=ami через API) | --restart (systemctl restart ats после --ats)
```

Значения по умолчанию — из письма (`00083819`, `95.128.224.47:5060`,
`95.128.224.0/21`, `alaw,ulaw,g729`, 15 каналов); переопределяются окружением:
`MCM_LOGIN MCM_HOST MCM_PORT MCM_TRUNK MCM_NET MCM_RTP MCM_CODECS MCM_MAX_CHANNELS`.

Вручную — то же самое, если хочется без скрипта:

```bash
cp deploy/asterisk/pjsip-multicom.conf /etc/asterisk/
$EDITOR /etc/asterisk/pjsip-multicom.conf      # вместо __MCM_*__ — данные письма
chown root:asterisk /etc/asterisk/pjsip-multicom.conf && chmod 0640 /etc/asterisk/pjsip-multicom.conf
grep -q 'pjsip-multicom.conf' /etc/asterisk/pjsip.conf || echo '#include pjsip-multicom.conf' >> /etc/asterisk/pjsip.conf
asterisk -rx "module reload res_pjsip.so"
```

Покрыто тестами: `tests/test_multicom_trunk_install.py` (рендер данных письма,
переименование секций под `MCM_TRUNK`, отказ на опасных значениях, идемпотентный
`#include`, права 0640, права root) и `tests/test_deploy_scripts.py` (синтаксис всех
`deploy/*.sh` и запрет `grep -q` в условиях).

Проверка (именно в этом порядке):

```bash
asterisk -rx "pjsip show transports"    # transport-udp  Active  0.0.0.0:5060
asterisk -rx "pjsip show endpoints"      # Endpoint:  <Aor>      mcm            → mcm/mcm-aor
asterisk -rx "pjsip show aors"           # Contact:   <Aor/ContactUri> …
asterisk -rx "pjsip show contacts"       # 95.128.224.47 … Avail
```

- `pjsip show contacts` показывает `Avail`/`Non-Corr` — да, контакт появляется **после**
  успешной исходящей регистрации; при `Unspecified`/пустоте — 403 (логин/пароль/IP) или сеть.
- Сырой SIP можно посмотреть так: `asterisk -rx "pjsip set logger on host 95.128.224.47"`,
  затем `tail -f /var/log/asterisk/messages | grep -i "40[13]\|REGISTER"`.

Если у вас Asterisk 10–18 и вы хотите ровно как в документации оператора — `chan_sip`:

```bash
cp deploy/asterisk/sip-multicom.conf /etc/asterisk/   # этот вариант скрипт не трогает:
# правьте руками — строки 9 и 27, вместо ПАРОЛЬ_ИЗ_ПИСЬМА (две позиции!)
echo '#include sip-multicom.conf' >> /etc/asterisk/sip.conf
asterisk -rx "module reload func_sip.so" ; asterisk -rx "sip reload"
asterisk -rx "sip show registry"    # Expected this host … State: Registered
asterisk -rx "sip show peers"       # mcm/…  OK
```

В `asterisk.conf`/сборке для chan_sip убедитесь, что модуль не отключён (`nocs`-сборки без
chan_sip бывают в Alpine/некоторых образах). В Asterisk 22 chan_sip удалён — только PJSIP.

## 6. Шаг 3 — диалплан, озвучка, запись

```bash
cp deploy/asterisk/extensions-ats.conf /etc/asterisk/
echo '#include extensions-ats.conf' >> /etc/asterisk/extensions.conf
mkdir -p /var/spool/asterisk/monitor/ats
chown -R asterisk:asterisk /var/spool/asterisk/monitor /var/spool/asterisk/ats 2>/dev/null
chmod 770 /var/spool/asterisk/monitor/ats
mkdir -p /var/spool/asterisk/ats && chown asterisk:asterisk /var/spool/asterisk/ats
cp deploy/asterisk/ats-tts.conf /etc/asterisk/
cp deploy/asterisk/agi/ats_say.py /var/lib/asterisk/agi-bin/
chmod 755 /var/lib/asterisk/agi-bin/ats_say.py
chown asterisk:asterisk /var/lib/asterisk/agi-bin/ats_say.py
asterisk -rx "dialplan reload"
asterisk -rx "dialplan show ats-out"      # должен показать контекст с AGI/MixMonitor
asterisk -rx "dialplan show from-mcm"     # входящие (DID → сотрудники/очередь)
```

Что делает `ats-out`: поднимает канал, включает `MixMonitor(.../call-<ATS_CALL_ID>.wav)` при
`ATS_RECORD=1`, зовёт `AGI(ats_say.py)` если пришёл `ATS_TEXT_B64`, и если AGI не оставил
`ATS_PLAY` — выставляет `failed:AGI не вернул результат`. Это защита от вранья в журнале:
«доставлено» появляется только когда озвучка реально состоялась.

Проверка TTS-цепочки без звонка:

```bash
/usr/bin/espeak-ng -v ru -s 150 -w /tmp/t.wav "проверка голоса" && ls -l /tmp/t.wav
```

## 7. Шаг 4 — доступ ATS к Manager Interface

```bash
AMIPASS=$(openssl rand -hex 24)
# проще всего: sudo -E ./deploy/asterisk/install-multicom-trunk.sh --ami (сгенерирует пароль)
cp deploy/asterisk/manager-ats.conf /etc/asterisk/
sed -i "s/ЗАМЕНИТЕ_НА_СЛУЧАЙНЫЙ_ПАРОЛЬ/$AMIPASS/" /etc/asterisk/manager-ats.conf
echo '#include manager-ats.conf' >> /etc/asterisk/manager.conf
sed -i 's/^enabled = no/enabled = yes/' /etc/asterisk/manager.conf
chmod 0640 /etc/asterisk/manager-ats.conf && chown root:asterisk /etc/asterisk/manager-ats.conf
asterisk -rx "module reload manager.cfm"   # или: asterisk -rx "core restart now"
asterisk -rx "manager show user ats"       # Inbound / Read / Write
```

Проверка «с улицы» (должна быть отказана) и сlocalhost (должна пройти):

```bash
timeout 3 bash -c '</dev/tcp/127.0.0.1/5038' && echo "AMI слушает 5038"
printf 'Action: Login\r\nUsername: ats\r\nSecret: %s\r\nActionID: 1\r\n\r\n' "$AMIPASS" \
  | timeout 5 nc 127.0.0.1 5038 | head -4     # Response: Success
```

---

## 7а. Форма «Интеграция с Агрегатором Мультиком (MultiCom API / SIP)» — что вводить

Путь в UI: **Настройки → вкладка «Провайдер/CRM» → блок «Интеграция с Агрегатором
Мультиком»**. Это форма провайдера `multicom` — то есть режима «ATS сама дёргает REST
оператора (makecall) и принимает статусы вебхуком». Ваш Мультиком выдал только
SIP-регистрацию (§1), поэтому форма нужна здесь ровно для двух вещей: хранить
реквизиты в одном месте и не мешать работать маршруту `ami`. Разберём по полям —
в ключах `settings.multicom.*` (править можно и через `POST /api/v2/settings/raw`).

| Поле в UI | Ключ | Что вписать и почему |
|---|---|---|
| API URL | `api_url` | Оставить `https://api.multicom.ru/v1` (дефолт) или пустым. Реального API у нас нет: клиент умеет `GET /account`, `GET /numbers`, `POST /calls/make`, `POST /calls/{id}/hangup`; если оператор пришлёт свой контракт, пути правятся ключами `account_path`, `numbers_path`, `makecall_path`, `hangup_path` (только через `/settings/raw`, в UI их нет) |
| API Key / Token | `api_key` | Пусто. Пока ключа нет, обе кнопки и провайдер `multicom` возвращают `Мультиком не настроен: укажите API Key…` — это намеренный fail-fast: АТС не должна делать вид, что звонит. Хранить можно не в БД, а в env `ATS_MULTICOM_API_KEY` |
| ID Аккаунта / Лицевой счет | `account_id` | Номер договора/лицевого счёта из письма Мультикома. Используется как заголовок `X-Account-Id` во всех REST-запросах; для `ami`-маршрута — только справка |
| SIP Сервер (sip_host) | `sip_host` | `95.128.224.47:5060` (регрессор из письма). **ATS к нему не подключается и не регистрируется** — это делает Asterisk (`/etc/asterisk/pjsip.conf` + строка `register=`, §5). Поле справочное, чтобы реквизиты были в одном месте |
| SIP Логин | `sip_user` | `00083819` |
| SIP Пароль | `sip_secret` | Лучше оставить пустым в UI. Значение нужно только Asterisk'у, и держать его надо в `pjsip.conf` (права 640, владелец `root:asterisk`), а не в `ats.db`: в настройках АТС секрет лежит в БД и маскируется в API, но файл с 640 защищён лучше. В UI поле есть, чтобы можно было пробросить его в генератор конфига, если решите хранить там |
| Сотрудник ATS по умолчанию (user) | `default_user` | `ats`. Подставляется в `user` тела `POST /calls/make` — «кто инициировал звонок» на стороне оператора; в `ami`-режиме не используется |
| Секрет вебхука (X-Multicom-Secret) | `webhook_secret` | Пусто, пока оператор не подтвердил, что шлёт события. При пустом секрете приём `POST /api/v2/webhooks/multicom` закрыт отдачей 403 (fail-closed) — это норма, а не поломка. Если дадут: сгенерировать длинный случайный ключ (`openssl rand -hex 32`), вписать сюда и передать агрегатору в заголовке `X-Multicom-Secret` |
| Записывать разговоры на стороне оператора | `record` | Для нашей схемы — **Нет**. Запись делает Asterisk (`MixMonitor`, §6) и складывает в `ATS_DATA_DIR/recordings`, а `attach_recordings.py` приклеивает файл к звонку. «Да» имеет смысл только если оператор сам отдаёт ссылку на свою запись в ответе makecall |
| URL вебхука для Мультикома | — (подсказка) | Адрес формируется из того, откуда открыт UI: `https://axiomats.ru/api/v2/webhooks/multicom`. Отдавать его оператору нужно только вместе с секретом и только если они подтверждают отправку статусов. Проверить, что путь доходит до АТС через nginx: `curl -i -X POST https://axiomats.ru/api/v2/webhooks/multicom` → без секрета обязан быть **403**, а не 404/502 |

Кнопки блока и что они реально делают:

- **«Проверить связь»** → `POST /api/v2/multicom/check` → `GET {api_url}/account`
  (при 404/405 клиент пробует `GET /status`). Без `api_key` ответ —
  `{"ok": false, "error": "multicom_not_configured", …}`. На маршруте `ami` эта
  проверка не нужна вовсе: там «проверка связи» — `POST /api/v2/ami/check` (§11.1).
- **«Синк номеров»** → `POST /api/v2/multicom/pool-sync` → `GET {api_url}/numbers` и
  добавление новых в таблицу `numbers`. Важно: движок берёт номер строго по полю
  `numbers.provider` (`numbers.acquire(provider=…)`), поэтому импорт умеет указывать,
  в чей пул класть: `{"provider": "ami"}`. По умолчанию — `multicom`, и для `ami`-маршрута
  такой импорт бесполезен:

```bash
curl -s -X POST $ATS/api/v2/multicom/pool-sync -H "X-Ats-Token: $TOK" -H 'Content-Type: application/json' \
  -d '{"provider": "ami", "dry_run": true}'      # сначала так: покажет added без записи
# уже импортированные «multicom»-номера перекрасить в ami:
#   UPDATE numbers SET provider='ami', label='ami' WHERE provider='multicom';
```

И главное по этому блоку: **провайдер в «Настройки → Телефония» для Мультикома надо
выбрать `Asterisk AMI Manager` (`ami`), а не `Мультиком (MultiCom API / SIP)`**. При
`provider=multicom` без REST-ключа движок не сделает ни одного звонка (и правильно
делает — симуляцию он себе не включает). Содержимое формы при этом можно оставить
заполненным: оно ни во что не вмешивается, а реквизиты всегда под рукой.

## 8. Шаг 5 — настройки ATS (провайдер `ami`)

Путь в UI: **Настройки → Телефония → провайдер `Asterisk AMI Manager`** и блок
**Asterisk AMI Manager** (там же кнопка «Проверить Asterisk и транк»).
Через API — `POST /api/v2/settings/raw` с секциями `provider`, `ami`, `records`.

Значения под Мультиком (полный набор, `settings.ami`):

| Ключ | Значение | Зачем |
|---|---|---|
| `host`, `port` | `127.0.0.1`, `5038` | AMI на той же машине |
| `user`, `secret` | `ats`, `<AMIPASS>` | пользователь `manager.conf` |
| `timeout` | `5` | таймаут действий AMI |
| `trunk` | `mcm` | имя endpoint'а/peer'а из §5; подставляется в диал-строку и в фильтр входящих |
| `tech` | `PJSIP` (или `SIP`) | канал: `PJSIP/номер@mcm` против `SIP/mcm/номер` |
| `channel_pattern` | пусто | полный шаблон диал-строки, если нужно иначе (`{number}`, `{trunk}`) |
| `number_format` | `ru8` | Мультиком требует `8XXXXXXXXXX`; в базе остаётся `+7…` |
| `dial_prefix` | пусто | надбавка после формата (например `0` для выхода на межгород) |
| `caller_id_format` | `d10` | CLI 10 цифр (`9690229926`) — как оператор сам отдаёт номер; `digits`/`e164` — если потребуют полный |
| `context` | `ats-out` | контекст, куда Originate кладёт исходящий канал |
| `ring_timeout_ms` | `35000` | сколько звонить абонента |
| `play_message` | `true` | озвучка текста в `message` через AGI |
| `play_context` | `ats-play` | резервный контекст озвучки (для `Redirect`) |
| `record_calls` | `true` | ставит `ATS_RECORD=1` → MixMonitor в диалплане |
| `inbound_enabled` | `true` | входящие с транка в журнал/CRM |
| `inbound_contexts` | `from-mcm,ats-in` | контексты, которые считаем входящими с DID |
| `inbound_tech` | `PJSIP` | префикс имени канала входящего (`PJSIP/mcm-…`) |
| `operator_trunk` | пусто (= `trunk`) | отдельный транк для исходящих на сотрудников, если нужен |
| `op_context` | `from-internal` | контекст ответившего оператора (обычно `Wait` + `Bridge`) |
| `acd_answer_timeout`, `bridge_timeout` | `35`, `10` | ожидания АЦД |
| `acd_callerid` | `9690229926` | какой CLI показывать сотруднику при входящем/переводе |

Быстрее всего — `sudo -E ./deploy/asterisk/install-multicom-trunk.sh --ami --ats` (пароль AMI
генерируется, `settings.ami` и `provider=ami` пишутся через API тем же запросом; флага
`--restart` хватает, чтобы провайдер пересоздался). Ниже — то же самое вручную, если хочется
контроля.

Пример записи настроек (админ-токеном):

```bash
TOK=$(curl -s -X POST $ATS/api/v2/auth/login -d '{"login":"admin","password":"…"}' | sed 's/.*"token":"\([^"]*\)".*/\1/')
curl -s -X POST $ATS/api/v2/settings/raw -H "X-Ats-Token: $TOK" -H 'Content-Type: application/json' \
  -d '{"provider_config":{"provider":"ami","ami":{"host":"127.0.0.1","port":5038,"user":"ats",
       "secret":"'"$AMIPASS"'","trunk":"mcm","tech":"PJSIP","number_format":"ru8",
       "caller_id_format":"d10","context":"ats-out","inbound_contexts":"from-mcm,ats-in"},
       "records":{"base_url":"https://ats.example.ru","link_ttl_hours":72}}}'
```

Секреты лучше держать не в БД, а в окружении (`/etc/ats/ats.env`): переменные
`ATS_RECORDS_LINK_TOKEN` для подписи ссылок на записи; для AMI пароля — просто
`sed`-правка настроек один раз.

`provider` принимается и этим запросом (раньше — нет: цикл мержил только dict-секции,
и «переключись на ami одним curl» оставляло движок на старом провайдере). Рестарт не
нужен: `POST /settings/raw` вызывает `ENGINE.reload_settings()`, который пересоздаёт
провайдер и CRM на месте. Перезапуск (`systemctl restart ats`) требуется только если
меняли `/etc/ats/ats.env`, юнит или код.

То же самое делает скрипт: `sudo -E ./deploy/asterisk/install-multicom-trunk.sh --ami --ats`.

## 9. Шаг 6 — пул из 15 номеров

Мультиком прислал 10-значные московские мобильные. В базе номер хранится в E.164
(`+7…`), а оператору уходит в формате `ru8` — за это отвечает `number_format`, ничего
переименовывать в кампаниях не надо.

Тем же скриптом (без цикла curl, идемпотентно — существующие номера вернут
`number_exists` и это не ошибка):

```bash
ATS_TOKEN=$TOK sudo -E ./deploy/asterisk/install-multicom-trunk.sh --numbers-only
```

Вручную:

```bash
for n in 9690229926 9690229930 9690229937 9690229938 9690229942 \
         9863149340 9863149348 9863149351 9863149365 9863149385 \
         9362574897 9362577603 9362962622 9362968878 9362968932; do
  curl -s -X POST $ATS/api/v2/numbers/save -H "X-Ats-Token: $TOK" -H 'Content-Type: application/json' \
    -d "{\"number\":\"+7$n\",\"label\":\"MCM $n\",\"kind\":\"mobile\",\"provider\":\"ami\",\"daily_limit\":120}"
done
curl -s $ATS/api/v2/numbers -H "X-Ats-Token: $TOK" | python3 -m json.tool | head -30
```

Почему `daily_limit: 120`: 15 номеров × 120 = 1800 вызовов/сутки — старт, с которого
мобильные операторы не начинают резать маршруты; анти-маркировка важнее скорости.
Поднимать лимит — по одному шагу в неделю, следя за `quarantined` и долей `no_answer`.
Дальше работают штатные механизмы: `weight` (частота использования), `cooldown_sec`,
карантин по `failed`/`forbidden`, `POST /api/v2/numbers/reset` (сброс суточного счётчика).

## 10. Шаг 7 — кампания

Три потока на выбор (все работают через Asterisk):

- `operator` — дозвонились, положили в очередь АЦД, оператор принимает в UI, ATS
  делает AMI `Bridge` (при разрыве — задача «перезвонить»). Настройки бриджа — `op_*` выше.
- `message` — автоинформ: текст озвучивает AGI (TTS). Текст ≤1500 символов; DTMF-клавиша
  во время озвучки прерывает её (в журнале — `ok:interrupted`).
- `agent` — ИИ-скрининг (`llm.enabled`),Qualified → в очередь оператору.

```bash
# кампания «Мультим_старт», flow=message, шаблон 1, 2 повтора, расписание
curl -s -X POST $ATS/api/v2/campaigns/save -H "X-Ats-Token: $TOK" -H 'Content-Type: application/json' \
  -d '{"name":"Мультим_старт","flow":"message","template_id":1,"max_channels":6,
       "retry_max":2,"retry_delay_min":30,"schedule":{"days":[1,2,3,4,5],"from":"10:00","to":"18:00"}}'
curl -s -X POST $ATS/api/v2/campaigns/start -H "X-Ats-Token: $TOK" -d '{"id":1}'
watch -n2 "curl -s $ATS/api/v2/calls?limit=5 -H 'X-Ats-Token: $TOK' | python3 -m json.tool"
```

`max_channels` (и глобальный `settings.max_channels`) — сколько одновременных каналов
держит ATS; на транке согласуйте с Мультиком (их `device_state_busy_at=15` в §5).

## 11. Шаг 8 — проверки (то, ради чего весь документ)

### 11.1 Чек-лист одним запросом

```bash
curl -s -X POST $ATS/api/v2/ami/check -H "X-Ats-Token: $TOK" -H 'Content-Type: application/json' -d '{}' \
  | python3 -c 'import json,sys; r=json.load(sys.stdin); [print(("OK " if c["ok"] else ("FAIL " if c["critical"] else "WARN "))+c["name"]+": "+(c["detail"] or "-")[:160]) for c in r["report"]["checks"]]'
```

Что проверяется и что делать при провале:

| Строка | Смысл | Если FAIL |
|---|---|---|
| `config` | есть ли host/user/secret в `settings.ami` | заполнить §8 |
| `ami_login` | TCP 5038 + `Login` прошёл | юзер/пароль/`permit`, модуль `manager.cfm`, `enabled=yes` |
| `asterisk`, `ping` | версия, живость | Asterisk не запущен / рестарт |
| `trunk_configured` | транк виден в `pjsip show endpoints`/`sip show peers` | имя `trunk` ≠ имя в конфиге, `#include` не подключён |
| `registration` | контакт/регистрация у оператора | пароль, IP в их allowlist, 5060, `qualify` |
| `dialplan` | контекст `ats-out` существует | `#include extensions-ats.conf`, `dialplan reload` |
| `agi_play` | контекст `ats-play` (если `play_message`) | см. §6 |
| `codec_g729` | есть ли G.729 | не критично: хватит alaw/ulaw |
| `numbers_pool` | сколько активных номеров `provider=ami` | §9 |

### 11.2 Живой тест-дозвон (один, руками)

Сначала — без ATS, чтобы отделить «наш код» от «линии оператора»:

```bash
asterisk -rx "channel originate PJSIP/89XXXXXXXX@mcm application Playback tt-monkeys" -x ""
# или так, если хочется через контекст ATS:
asterisk -rx "channel originate PJSIP/89XXXXXXXX@mcm extension s@ats-out"
asterisk -rvvv      # смотреть SIP/SDP и причины отказа вживую
```

Потом — через ATS (уже с CID, переменной и журналом): заведите контакт-«пробник» на свой
номер, кампания `flow=operator`, `max_channels=1`, «Старт», кнопка «Позвонить» в карточке.
В журнале (`Звонки`) должно пройти: `dialing → ringing → answered → wait_operator → done`,
в `asterisk -rvvv` — `Newchannel … CallerIDName=ats-call-<id>`.

### 11.3 Проверка озвучки (message)

```bash
ls -l /var/spool/asterisk/monitor/ats/        # появился call-<id>.wav
asterisk -rvvv | grep -i ats_say              # «ats_say: …» при проблемах с TTS
```

Результат в журнале: `done_ok` с detail `Сообщение озвучено (Asterisk)`; если AGI не смог —
`failed` с текстом `Озвучка не выполнена: …` (это правильно: без TTS не бывает «доставлено»).

### 11.4 Проверка входящих

```bash
asterisk -rx "dialplan show from-mcm"
tail -f /var/log/asterisk/full | grep "ATS in"
```

Позвоните на один из 15 DID: в `Звонках` появляется запись `direction=in` (статус
`ringing` → `done_ok` после разговора, либо `missed` + задача в amoCRM «Перезвонить
клиенту»). Контакт подтянется, если 10-значный CLI совпал с номером в базе — ATS ищет
по всем написаниям (`9690229926`/`7969…`/`+7969…`/`8969…`).

### 11.5 Проверка записей и amoCRM

```bash
cat >> /etc/cron.d/ats-records <<'EOF'
*/5 * * * * root /usr/bin/python3 /opt/ats/deploy/asterisk/attach_recordings.py >> /var/log/ats-records.log 2>&1
EOF
ATS_URL=$ATS ATS_LOGIN=admin ATS_PASSWORD='…' /usr/bin/python3 \
  /opt/ats/deploy/asterisk/attach_recordings.py --dry-run
```

В карточке звонка появляется запись; в сделке amoCRM — поле «Запись» со
подписанной ссылкой `/api/v2/records/<call_id>?exp=…&sig=…` (живёт
`records.link_ttl_hours`, токена сессии не требует). Проверьте, что `records.base_url`
— это адрес, доступный сотрудникам из браузера.

### 11.6 Что должно быть в `/api/v2/health`

```bash
curl -s $ATS/api/v2/health | python3 -m json.tool
```

В `GET /api/v2/health` — `telephony.provider: ami` и `connected: true` (то есть AMI live
прямо сейчас). В `GET /api/v2/health/details` (admin) — маскированный `provider_config.ami`
(проверьте `trunk`/`tech`/`number_format`), `provider_config.records` и счётчики пулов
`pool_ami` / `pool_multicom` / `pool_megafon` (`usable` — сколько номеров реально можно
брать в работу). Если `provider` пустой — ATS специально не запускает обзвон
(fail-closed, см. `docs/DEPLOY.md` §8).

---

## 12. Типовые проблемы

| Симптом | Где смотреть | Лечится |
|---|---|---|
| «Тишина», вызов идёт, голоса нет | `rtp.conf`, `pjsip show transports`, `tcpdump -i any -n udp portrange 10000-20000` | открыть RTP наружу/пробросить NAT, `direct_media=no`, `rtp_symmetric/force_rport/rewrite_contact` |
| Слышно только абонента | `external_signaling_address` | указать публичный IP + `local_net`, либо SIP-ALG выключить |
| Нет фразы оператора на выключенном мобильном, ранний медиастрим | их wiki | `progressinband=yes`, `prematuremedia=no` (chan_sip) |
| 403 на REGISTER | `/var/log/asterisk/messages`, `pjsip set logger on host …` | пароль/`from_user`/IP не в их allowlist; при смене IP — письмо оператору |
| `404 Not Found` при исходящем | формат номера | `number_format=ru8`; проверить `dial_prefix`; у них `8_Код_Номер`, межгород — `810…` |
| 407/488 на INVITE при исходящем | кодеки | `allow=alaw,ulaw` (+g729 при модуле); `disallow=all` первым |
| Оператор rejects CLI | `Originate` → `CallerID` | `caller_id_format`: `d10` → `digits` → `e164`; подставлять разрешено только свои 15 номеров |
| DTMF не долетает | `dtmf_mode=rfc2833` | у chan_sip — `dtmfmode=rfc2833` |
| Входящие не попадают в журнал | `dialplan show <context>`, имя канала | `inbound_contexts` должен содержать контекст входящих, `trunk`/`inbound_tech` — начало имени канала (`PJSIP/mcm-…`) |
| Звонок «завис» в `talk`/`dialing` | `settings.message_max_sec`, `watchdog_timeout_min` | AMI-обрыв: события не дошли; watchdog закроет `timeout`, движок повторит по `retry_max` |
| Один и тот же номер «не звонит» сутки | `numbers` (`daily_count`, `cooldown_until`, `quarantined`) | `POST /api/v2/numbers/reset`, снять карантин из UI |
| Запись не привязалась | `/var/log/ats-records.log` | имя файла должно быть `call-<id>.wav` / `in-<канал>.wav`; файл должен быть старше 20 с |
| 401 в ATS при работе с AMI | — | 401 = только истёкшая сессия ATS; к Asterisk отношения не имеет |

---

## 13. Что ответить Мультикому (письмо-запрос)

Готовый текст — подставьте свои адреса. Он закрывает всё, без чего связка либо
не заработает, либо заработает и потом встанет.

```
Тема: 00083819 — подключение АТС (Asterisk) к SIP-транку: параметры и лимиты

Виталий, добрый день!

Номера (15 шт.) и данные регистрации получили, интеграция идёт через собственный
IP-телефонный шлюз (Asterisk, chan_pjsip) с исходящими/входящими вызовами.
Для запуска и приёмки просим подтвердить/предоставить:

1. Сетевой доступ: добавьте, пожалуйста, в allowlist наш статический IP
   <ПУБЛИЧНЫЙ_IP> (сигнализация UDP/TCP 5060 и RTP 10000-20000/udp).
   Наша сеть для маршрутизации: <СЕТЬ/маска>. Обращаем внимание: при «тишине»
   по вашей рекомендации открыли сеть 95.128.224.0/21.
2. Логин 00083819: одно место (регистрация) на 15 номеров — верно ли, что входящие
   на все 15 DID приходят на один транк с B-номером 10 цифр в Request-URI?
   Нужны ли отдельные учётные записи/контексты на номер?
3. Ограничения: максимальное число одновременных исходящих вызовов (каналов),
   допустимая интенсивность вызовов (NUMBER/мин), лимит длительности разговора,
   допустимая доля неотвеченных, политика блокировок при массовом обзвоне.
4. Caller ID: в каком формате вы принимаете A-номер (9690229926 / 89690229926 /
   +79690229926) и разрешено ли подставлять любой из 15 наших номеров в каждом
   вызове (у нас исходящий пул ротируется по лимитам).
5. Формат набора: подтверждаем 8XXXXXXXXXX по РФ и 810… по международной;
   нужен ли префикс для мобильных/городских направлений внутри сети.
6. Кодеки: G.711a/u-law ок; G.729 — требуется ли лицензия с вашей стороны и
   включён ли transcoding на ваших SBC?
7. DTMF: rfc2833 (и/или inband) — как предпочтительнее для ваших шлюзов?
8. Биллинг/отчёты: есть ли выгрузка CDR (CSV/API/e-mail) по номеру 00083819 —
   нужна сверка длительностей и статуса «отвечен/неотвечен»; доступны ли записи
   разговоров на вашей стороне (мы пишем свои)?
9. Приём вызовов: как быстро поднимается резерв при недоступности нашего IP,
   приходит ли нам уведомление; есть ли ограничение на длину номера в CLI.
10. Тестовый период: просим включить тестовый режим/лимит на 2 недели с
    возможностью обзвона по нашему списку номеров.

Дополнительно: у нас собственная АТС (исходящие кампании + ИИ-скрининг + очередь
операторов + интеграция с amoCRM), готовы обсудить партнёрство — детали в приложении.

С уважением, <ФИО>, <компания>, <телефон>, <e-mail>
```

Отдельно (не в письмо, а себе): партнёрский трек — `docs/MULTICOM_PARTNERSHIP.md`,
там что мы предлагаем оператору и как считать экономику; технические требования
к их API (если захотят интеграцию глубже, чем SIP) — `docs/MULTICOM_INTEGRATION.md` §M1–M6.

---

## 14. Безопасность и хозяйство

- Пароль SIP и AMI-секрет — **не** в git. В шаблонах заглушки; реальные значения
  живут в `/etc/ats/ats.env` (ATS) и в `/etc/asterisk/*.conf` с правами `0640 root:asterisk`.
- `manager.conf`: только `permit=127.0.0.1` (или внутренний IP ATS), `webenabled=no`.
- 5060 наружу — только сеть оператора; остальной SIP-скан — `DROP` (+`fail2ban`/`sipfloodds`).
- 152-ФЗ: обзвон только по согласившимся контактам (`consent`), чёрный список и
  тихие часы включены по умолчанию; запись разговора предупреждается в тексте сообщения
  (шаблон) — см. `docs/ATS_HOW_IT_WORKS.md` §6.
- Логи: `/var/log/asterisk/full`, `/var/log/ats-records.log`, журнал ATS (`docs/ADMIN_LOGS.md`).
- Обновление сервера: `cd /opt/ats && ./deploy/update.sh arena/01a0cdee-ats`
  (бэкап БД → ff-only → тесты → рестарт → health → автооткат), `docs/DEPLOY.md`.

## 15. Если Мультиком всё-таки даст REST API

Тогда имеет смысл переключиться/дополнить провайдером `multicom`
(`docs/MULTICOM_INTEGRATION.md`): makecall через их API, статусы по webhook
`/api/v2/webhooks/multicom` (секрет, дедуп, корреляция по `external_call_id`),
синк пула номеров `POST /api/v2/multicom/pool-sync` (можно указать, в чей пул класть номера: `{"provider": "multicom"|"ami"}`, §7а). SIP-транк при этом остаётся
нужным для голоса, записи и входящих — то есть схема §1 не отменяется, а
к `ami` добавляется приём событий оператора (топы-ап статусов, фактический CLI, ссылки
на их записи).
