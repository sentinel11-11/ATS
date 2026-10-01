# Деплой и обновление Axioma ATS на сервере

Практическая инструкция: как поставить, как запускать, **как обновлять программу на
сервере**, как откатиться, что делать при проблемах.

Развёртывание в этом проекте намеренно «скучное»: один процесс Python (stdlib),
одна база SQLite, один порт. Никаких Docker/K8s/Redis/Postgres — меньше moving parts
на проде и проще обновление до боевой версии.

---

## 1. Что где лежит (принятая раскладка)

| Что | Путь по умолчанию | Как переопределить |
|---|---|---|
| Код (клон репозитория) | `/opt/ats` | — |
| Боевые данные: `ats.db`, `logs/`, `recordings/`, `crm_out/`, `backups/` | `/var/lib/ats` | `ATS_DATA_DIR` (по умолчанию — `<репозиторий>/data_v2`) |
| Переменные/секреты сервиса | `/etc/ats/ats.env` | `EnvironmentFile=` в юните |
| Порт | `9124` | `ATS_PORT`/`--port`, либо `settings.port` в БД |
| systemd-сервис | `ats.service` | `ATS_SERVICE` для скрипта обновления |

Проверить, где у вас данные: `systemctl cat ats | grep -i data` или
`ls -l /var/lib/ats/ats.db`.

---

## 2. Первая установка

```bash
# 0) код
git clone <URL_репозитория> /opt/ats && cd /opt/ats
sudo mkdir -p /var/lib/ats && sudo chown -R $USER:$USER /var/lib/ats   # или пользователь ats

# 1) ПРОВЕРКА, что запускается и что база создалась
ATS_DATA_DIR=/var/lib/ats ATS_ADMIN_PASSWORD='СложныйПароль!' ./run_ats2.sh
# → http://<ip>:9124  (логин admin), health: /api/v2/health

# 2) сервис
sudo cp deploy/ats.service /etc/systemd/system/ats.service
sudo install -d -m 750 /etc/ats && sudo cp deploy/ats.env.example /etc/ats/ats.env && sudo $EDITOR /etc/ats/ats.env
sudo systemctl daemon-reload && sudo systemctl enable --now ats
curl -s http://127.0.0.1:9124/api/v2/health
```

Зависимости: ядро v2 живёт **только на стандартной библиотеке** Python ≥ 3.9.
`requirements.txt` в репозитории — от legacy-прототипа (`pyserial`, `pyinstaller`), для v2
он не нужен. Интерфейс собран и лежит в `app/ui` — Node.js на сервере не обязателен
(нужен только если вы правите `frontend/`).

Если меняете UI:
```bash
cd /opt/ats/frontend && npm ci && npm run build     # результат → ../app/ui
```

---

## 3. Обновление программы: основная команда

### 3.0 Если `deploy/update.sh` на сервере ещё нет (первый раз — только так)

Каталог `deploy/` (сам скрипт, `ats.env.example`, `ats.service`, файлы Asterisk)
появился в репозитории вместе с интеграцией Мультикома. Поэтому на сервере,
который стоит на старой ревизии, команды «в одну строку» ещё не существует:

```text
-bash: ./deploy/update.sh: No such file or directory
```

Это не поломка, а «курица и яйцо»: скрипт приходит тем самым обновлением, ради
которого его вызывают. Сначала один раз подтягиваем код руками (вариант А) или
достаём только скрипт из приходящего коммита (вариант Б) — дальше `update.sh`
уже лежит в репозитории и все будущие обновления идут одной командой.

> Второй частый вариант той же ошибки: команду копируют из чата и в терминал
> попадает `./deploy/[update.sh](http://update.sh)` (так markdown превращает
> слово в ссылку). Путь должен быть ровно `./deploy/update.sh`.

Перед тем как что-то менять, полезно снять срез состояния сервера — это ровно то, по чему
видно, «что там лежит» и можно ли обновляться fast-forward'ом:

```bash
bash deploy/check.sh | tee /tmp/ats-check.txt      # быстрый отчёт (6 разделов)
./deploy/audit.sh  | tee /tmp/ats-audit.txt        # полный: + содержимое БД, логи, Asterisk
```

Оба ничего не меняют (единственная запись — `git fetch`, он обновляет только
`refs/remotes/*`). Когда `deploy/` на сервере ещё нет, быстрый отчёт можно вытащить
из приходящего коммита: `git show "origin/$BR:deploy/check.sh" > /tmp/ats-check.sh && bash /tmp/ats-check.sh`.

**Шаг 0 — понять, что за каталог `/opt/ats`:**

```bash
cd /opt/ats
git rev-parse --is-inside-work-tree >/dev/null 2>&1 && echo "OK: это git-клон" || echo "НЕТ: это не git-клон"
git remote -v
git log --oneline -3
git status --short | head
ls deploy 2>/dev/null || echo "каталога deploy нет — ожидаемо для старой ревизии"
```

- `НЕТ: это не git-клон` → код просто скопировали (scp/rsync). Обновлять надо так:
  клонировать в новый каталог и перенести оттуда код, оставив свои `data_v2/`
  и `/etc/ats/ats.env` на месте — см. §2 и §3.2.
- remote ведёт не на `sentinel11-11/ATS` → `git remote set-url origin <адрес>`
  (или завести второй remote: `git remote add ats https://github.com/sentinel11-11/ATS.git`,
  дальше во всех командах вместо `origin` писать `ats`).
- `git status --short` что-то показывает → нормально: свои правки уйдут в `stash`,
  а защиту боевой базы от перезаписи делает либо шаг 2 варианта А, либо шаг 4
  самого `update.sh`.

**Вариант А — один раз руками (ничего дополнительно не надо):**

```bash
cd /opt/ats
BR=arena/01a0cdee-ats
# 1) данные от git: явно тянем нужную ветку (актуально для клона -b main --single-branch / --depth)
git fetch origin "+refs/heads/$BR:refs/remotes/origin/$BR"
# 2) запрещаем git трогать runtime-файлы, которые исторически лежат в индексе
git ls-files data_v2 | xargs -r -n1 git update-index --skip-worktree
# 3) свои правки — в stash; база — копией «на всякий»
git stash push -u -m "before-$BR" >/dev/null 2>&1 || true
mkdir -p data_v2/backups
[ -f data_v2/ats.db ] && cp -a data_v2/ats.db "data_v2/backups/ats-before-update-$(date +%Y%m%d-%H%M%S).db"
# 4) переезд на нужную ревизию (только fast-forward, никаких merge-коммитов на проде)
git checkout "$BR" 2>/dev/null || git checkout -b "$BR" "origin/$BR"
git merge --ff-only "origin/$BR"
```

Дальше — тесты, интерфейс и рестарт:

```bash
python3 -m unittest discover -s tests | tail -3     # ожидаем «Ran 309 tests … OK»
if command -v npm >/dev/null; then (cd frontend && npm ci --no-audit --no-fund && npm run build); fi
sudo systemctl restart ats && curl -s localhost:9124/api/v2/health
```

Если сервис ещё не поставлен как systemd-unit (`/etc/systemd/system/ats.service`) —
рестарт свой: `kill "$(cat data_v2/ats.pid)" 2>/dev/null; ./run_ats2.sh` (или см. §2).

Контроль, что всё дошло:

```bash
git log --oneline -1                 # должен показать ревизию из origin/$BR
ls docs/MULTICOM_SIP_CONNECT.md deploy/asterisk/ deploy/update.sh
```

После этого можно сразу прогнать штатный путь — он честно скажет, что актуальны:

```bash
./deploy/update.sh "$BR"             # «уже на актуальной ревизии — ничего делать не нужно»
```

**Вариант Б — достать из приходящего коммита только скрипт и обновляться им:**

```bash
cd /opt/ats
BR=arena/01a0cdee-ats
git fetch origin "+refs/heads/$BR:refs/remotes/origin/$BR"
git show "origin/$BR:deploy/update.sh" > /tmp/update.sh
ATS_APP_DIR=/opt/ats bash /tmp/update.sh "$BR"
```

`ATS_APP_DIR` обязателен именно в этом сценарии: запущенный из `/tmp` скрипт иначе
решил бы, что репозиторий лежит в `/`, и всё сломалось бы на первом же `cd`.
Дальше он сам сделает fetch → защиту `data_v2/` → stash → бэкап БД → ff-only
merge → сборку UI → тесты → рестарт → health-check с автооткатом (§3.1).

Если после `git fetch` оказалось, что `merge --ff-only` не проходит (на сервере
накопились свои коммиты), разбор руками:

```bash
git log --oneline origin/$BR..HEAD    # что у нас лишнего
git rebase origin/$BR                 # переставить свои коммиты поверх
# или, если локальные коммиты не нужны: git reset --hard origin/$BR
```

### 3.0.1 Если `/opt/ats` — не git-клон (переезд на нормальное обновление)

Проверка: `cd /opt/ats && git rev-parse --is-inside-work-tree` → «not a git repository».
Обычно за этим стоит раскладка «код принесли файлами»: корень проекта лежит на уровень
глубже (например `/opt/ats/app/…`), venv живёт внутри кода, юнит запускает
`/opt/ats/app/venv/bin/python -m app.run --host 127.0.0.1 --port 9124`, данные в `/var/lib/ats`.
Минусы такого состояния: нет истории, нет отката, нет `deploy/update.sh`, «обновление» =
перетаскивание файлов, и никто не знает, правился ли код руками.

Полезно знать: ядро ATS v2 — чистый stdlib (не-stdlib импортов в `app/` нет), поэтому
venv приложению не нужен; данные при переезде не трогаются вообще, они в `ATS_DATA_DIR`.

**Шаг 1. понять, какая ревизия сейчас на сервере и правили ли её руками:**

```bash
find /opt/ats -name '*.py' -not -path '*/venv/*' | sort | xargs md5sum > /tmp/manifest.txt
python3 /path/to/clone/deploy/match-manifest.py /tmp/manifest.txt   # покажет ревизию и ручные правки
```

**Шаг 2. собрать новый код рядом и проверить на копии базы (живой сервис не трогаем):**

```bash
git clone -b arena/01a0cdee-ats https://github.com/sentinel11-11/ATS.git /opt/ats-new
cp -a /var/lib/ats /tmp/ats-smoke
cd /opt/ats-new && python3 -m unittest discover -s tests 2>&1 | tail -3
ATS_DATA_DIR=/tmp/ats-smoke ATS_ALLOW_SIM_FALLBACK=1 nohup python3 -m app.run --host 127.0.0.1 --port 9199 >/tmp/smoke.log 2>&1 &
sleep 3; curl -s localhost:9199/api/v2/health; echo; kill %1 2>/dev/null; rm -rf /tmp/ats-smoke
```

**Шаг 3. конфиг окружения** — его читают и systemd (`EnvironmentFile`), и `update.sh`;
без него скрипт обновления ищет базу в `<репозиторий>/data_v2` и может не найти:

```bash
install -d -m 750 /etc/ats
cat > /etc/ats/ats.env <<'ENV'
ATS_DATA_DIR=/var/lib/ats
ATS_HOST=127.0.0.1
ATS_PORT=9124
ATS_SERVICE=ats
ENV
chmod 640 /etc/ats/ats.env
```

Ключи API (amoCRM/ВАТС/AMI), если они были вписаны прямо в юнит, перенести сюда же.

**Шаг 4. переезд и откат:**

```bash
ts=$(date +%Y%m%d-%H%M%S)
systemctl stop ats
mv /opt/ats /opt/ats.legacy-$ts && mv /opt/ats-new /opt/ats
chown -R ats:ats /opt/ats
cp /opt/ats/deploy/ats.service /etc/systemd/system/ats.service   # ExecStart без жёстких --host/--port, venv не нужен
systemctl daemon-reload && systemctl start ats && curl -s localhost:9124/api/v2/health
# откат, если health не OK:
#   systemctl stop ats && rm -rf /opt/ats && mv /opt/ats.legacy-$ts /opt/ats && systemctl start ats
```

**Шаг 5. разбор легаси-копии** (свои файлы: сертификаты, выгрузки, ручные правки):

```bash
diff -rq /opt/ats.legacy-$ts/app/app /opt/ats/app 2>/dev/null | head -20
ls -la /opt/ats.legacy-$ts | grep -vE ' (app|venv|__pycache__)'
```

Дальше все обновления — `cd /opt/ats && ./deploy/update.sh <ветка>`. `ats.legacy-$ts`
удаляй, когда убедились (обычно на следующий день).

**Две вещи, которые стоит поправить при переезде:**

- `ATS_ALLOW_SIM_FALLBACK=1` в юните — удобен на стенде, но на боевой линии опасен:
  при недоступном AMI/Asterisk АТС не упадёт, а уйдёт в симуляцию, и в журнале начнут
  появляться несуществующие дозвоны. Держать включённым только на время наладки.
- `--host 127.0.0.1` — если интерфейсом пользуются не только через SSH-туннель,
  слушать `0.0.0.0` и закрывать фронт reverse-proxy с TLS (§6); для ссылок на записи
  из amoCRM нужен адрес, доступный из браузера (`records.base_url`).

### 3.1 Один шаг (то, что нужно помнить)

```bash
cd /opt/ats && ./deploy/update.sh <BRANCH>
```

например для текущей рабочей ветки этого проекта:

```bash
cd /opt/ats && ./deploy/update.sh arena/01a0cdee-ats
```

Скрипт делает всё правильно и по порядку:

1. **lock** (`/tmp/ats-update.lock`) — два обновления одновременно не запустятся;
2. читает `/etc/ats/ats.env` (данные/порт/сервис/ключи);
3. `git fetch` и сверка: если новых коммитов нет — выходит, ничего не трогая;
4. **защищает runtime-данные**: на файлы `data_v2/` (в т.ч. боевую `ats.db`) вешается
   `skip-worktree`, поэтому `git` их не перезапишет (см. §3.4);
5. неубранные локальные правки уходят в `git stash` (сообщение с инструкцией, как вернуть);
6. **бэкап БД** через `sqlite3 backup` в `…/backups/ats-before-update-<ts>.db`
   (корректный снимок живой WAL-базы; храним 20 последних);
7. `git checkout <ветка>` + `git merge --ff-only origin/<ветка>` (никаких «мерджей» на проде);
8. если менялся `frontend/` — пересборка UI; если менялся `requirements.txt` — `pip install`;
9. **прогон тестов** `python3 -m unittest discover -s tests`;
10. рестарт (`systemctl restart ats`, либо свой процесс с PID-файлом) и
    **health-check** `/api/v2/health` до 60 секунд;
11. если health не поднялся — **автооткат** на предыдущую ревизию + рестарт.

Флаги:

```bash
./deploy/update.sh                      # текущая ветка, origin
./deploy/update.sh arena/01a0cdee-ats   # явная ветка
./deploy/update.sh main origin --fast    # без прогона тестов (быстро, «в поле»)
./deploy/update.sh arena/01a0cdee-ats --no-build    # не трогать UI
./deploy/update.sh arena/01a0cdee-ats --no-restart  # обновить код, сервис не трогать
./deploy/update.sh --rollback            # вернуться на предыдущую ревизию + рестарт
```

Лог операции: `/tmp/ats-update.log`.

Если файла `deploy/update.sh` на сервере ещё нет — это первый проход, см. §3.0.

### 3.2 Если хочется руками (то же самое, минимальный вариант)

```bash
cd /opt/ats
sudo systemctl stop ats                                        # 1. остановить (иначе пишем по живой базе)
mkdir -p /var/lib/ats/backups
sqlite3 /var/lib/ats/ats.db "PRAGMA wal_checkpoint(TRUNCATE)" # 2. схлопнуть WAL в основной файл…
cp -a /var/lib/ats/ats.db /var/lib/ats/backups/ats-before-update.db   #    …и только потом копия
git fetch origin && git merge --ff-only origin/arena/01a0cdee-ats     # 3. код
python3 -m unittest discover -s tests                                  # 4. тесты до пуска
sudo systemctl start ats && curl -s localhost:9124/api/v2/health      # 5. вверх + проверка
```

Короткая «грязная» версия для стенда (без остановок и тестов) — но на проде так не надо:

```bash
cd /opt/ats && git pull --ff-only origin arena/01a0cdee-ats && sudo systemctl restart ats
```

### 3.3 Обновление без простоя (graceful)

Движок перечитывает настройки каждый тик, а БД — SQLite WAL, поэтому безопасная
последовательность такая:

1. остановить **приём** новых кампаний: UI → Кампании → «Пауза» (или `POST /api/v2/campaigns/<id>/pause`)
   — активные звонки доживут до финала (см. watchdog-таймауты);
2. `./deploy/update.sh <ветка> --fast` (или без `--fast`, если время есть);
3. снять кампании с паузы. Потери событий не будет: не доставленные в CRM итоги лежат в
   `crm_outbox` и уйдут сами, а `calls.external_call_id` позволяет вебхукам оператора
   коррелироваться после рестарта.

### 3.4 Почему «защищаем» данные и что с этим делать дальше

Исторически боевые файлы попали в индекс git: `data_v2/ats.db`,
`data_v2/initial_credentials.txt`. Пока они отслеживаются, `git merge/pull` может
перезаписать базу и **стереть историю звонков**. Поэтому:

- `deploy/update.sh` автоматически вешает `git update-index --skip-worktree` на всё
  отслеживаемое внутри `data_v2` — git перестаёт трогать эти файлы;
- **правильное долгосрочное решение** (сделать в техническое окно, 2 минуты):

```bash
cd /opt/ats
git rm --cached data_v2/ats.db data_v2/initial_credentials.txt   # убрать из индекса, на диске останутся
printf '\n# runtime-данные боевого сервера — не в git\ndata_v2/\n' >> .gitignore
git commit -m "Хранение: data_v2 больше не в git (боевая БД не перезаписывается обновлением)"
```

  после этого `ATS_DATA_DIR` вообще не обязан совпадать с каталогом репозитория, а
  обновление перестаёт зависеть от состояния базы.

- Если боевые данные живут в `/var/lib/ats` (рекомендуется), пункт выше — просто
  страховка на случай, если кто-то запустил АТС «в репозитории».

### 3.5 Откат

```bash
cd /opt/ats && ./deploy/update.sh --rollback      # код + рестарт + health-check
```

База **не** откатывается автоматически (откатывать данные — значит потерять звонки
клиента). Если нужна и база:

```bash
sudo systemctl stop ats
cp -a /var/lib/ats/backups/ats-before-update-<ts>.db /var/lib/ats/ats.db
rm -f /var/lib/ats/ats.db-wal /var/lib/ats/ats.db-shm     # старые WAL-хвосты от другой базы
sudo systemctl start ats
```

---

## 4. Что происходит с БД при обновлении

Схема создаётся/догоняется кодом: `db.init_db()` → `SCHEMA` + `_migrate()`
(`ALTER TABLE ADD COLUMN`, индексы после колонок). Отдельных «миграций» запускать не надо.
Практические следствия:

- обновление не требует ручных SQL, если вы не правили схему сами;
- даунгрейд (откат кода) на базе с новыми колонками — безопасен: старые версии просто
  не используют новые поля; но **не** рассчитывайте на это как на механизм: держите бэкап.

Полезные проверки после обновления:

```bash
sqlite3 /var/lib/ats/ats.db "PRAGMA integrity_check"
sqlite3 /var/lib/ats/ats.db "SELECT COUNT(*) FROM calls; SELECT COUNT(*) FROM campaign_items WHERE status IN ('queued','dialing','wait_operator');"
sqlite3 /var/lib/ats/ats.db "SELECT id,status,attempts,substr(last_error,1,120) FROM crm_outbox ORDER BY id DESC LIMIT 10"
```

---

## 5. Регулярное обслуживание

| Что | Как часто |
|---|---|
| Бэкап БД | `deploy/backup.sh` (или systemd timer) — см. ниже; `backup` API SQLite, не `cp` «вживую» |
| Ротация логов | `logrotate`: `/var/lib/ats/logs/*.log` (если сервис через systemd — логи в journald, настройте `SystemMaxUse`) |
| Проверка диска | БД + `recordings/` растут; оставьте ≥ 5 ГБ запаса |
| Ревизия «зависших» | `SELECT COUNT(*) FROM calls WHERE ended_at='';` — если >0 долго: смотрите watchdog-таймауты и вебхуки оператора |
| Ревизия CRM-очереди | `crm_outbox` в статусе `pending` долго = amoCRM недоступна/токен протух |
| Проверка секретов/ssl | срок TLS-сертификата на обратном прокси; срок действия OAuth-токена amoCRM (для приватной интеграции с бессрочным ключом — не актуально) |

`deploy/backup.sh` (рекомендуемый cron `10 3 * * *`):

```bash
#!/usr/bin/env bash
set -euo pipefail
DATA="${ATS_DATA_DIR:-/var/lib/ats}"
OUT="$DATA/backups"; mkdir -p "$OUT"
python3 - "$DATA/ats.db" "$OUT/ats-$(date +%Y%m%d-%H%M%S).db" <<'PY'
import sqlite3, sys
a=sqlite3.connect(sys.argv[1]); b=sqlite3.connect(sys.argv[2]); a.backup(b); b.close(); a.close()
PY
find "$OUT" -name 'ats-*.db' -mtime +21 -delete
```

---

## 6. Обратный прокси (Nginx) — HTTPS, логин и SSE

UI использует SSE (`/api/v2/events`): без отключения буферизации «живые» статусы
звонков не придут.

```nginx
server {
  listen 443 ssl http2;
  server_name ats.example.com;
  ssl_certificate     /etc/letsencrypt/live/ats.example.com/fullchain.pem;
  ssl_certificate_key /etc/letsencrypt/live/ats.example.com/privkey.pem;

  client_max_body_size 64m;              # импорт баз .xlsx и загрузка записей

  location / {
    proxy_pass http://127.0.0.1:9124;
    proxy_set_header Host              $host;
    proxy_set_header X-Real-IP         $remote_addr;
    proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
  }
  location = /api/v2/events {            # SSE
    proxy_pass http://127.0.0.1:9124;
    proxy_http_version 1.1;
    proxy_set_header Connection "";
    proxy_buffering off;
    proxy_read_timeout 4h;
  }
}
```

Вебхуки оператора и amoCRM ходят **снаружи** на `https://ats.example.com/api/v2/webhooks/<…>`
и `/api/v2/amocrm/webhook` — не закрывайте их авторизацией по IP без нужды, но держите
секреты (`multicom.webhook_secret`, `megafon_vats.crm_token`, `amocrm.webhook_token`).

---

## 7. Жёсткая изоляция (по желанию, для «взрослого» прода)

В `deploy/ats.service` уже включены `NoNewPrivileges/PrivateTmp/ProtectSystem/ProtectHome`.
Дополнительно, если АТС должна говорить только с оператором и amoCRM:

```ini
DynamicUsers=yes
IPAddressAllow=127.0.0.0/8
IPAddressDeny=any
# + адреса API МегаФона/Мультикома/amoCRM (или отдельный HTTPS-прокси с ACL)
RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX
```

Проверяйте после таких правок, что вебхуки и исходящие HTTP-запросы живы
(`GET /api/v2/health`, `POST /api/v2/multicom/check`, `POST /api/v2/amocrm/check`).

---

## 8. Диагностика «сервер не поднялся после обновления»

Первые три вопроса на упавшем обновлении: `tail -60 /tmp/ats-update.log` (что делал
`update.sh`), `git -C /opt/ats log --oneline -1` (на какой ревизии мы реально),
`journalctl -u ats -n 50 --no-pager` или `tail -50 <ATS_DATA_DIR>/logs/ats.log`.

```bash
systemctl status ats -l
journalctl -u ats -n 100 --no-pager          # или tail -100 /var/lib/ats/logs/ats.log
tail -80 /tmp/ats-update.log                 # лог обновления (тесты/сборка/merge)
ss -lntp | grep 9124                          # порт занят? старый процесс не умер?
curl -s localhost:9124/api/v2/health          # должен быть {"ok": true, …}
```

Частые причины:
- **порт занят старым процессом** — убейте его (`kill $(cat /var/lib/ats/ats.pid)`);
- **`provider` не может инициализироваться** (ключ/URL оператора недоступны) — движок
  специально не стартует «в симуляции»; временно: `ATS_ALLOW_SIM_FALLBACK=1` **только
  для отладки**;
- **упал UI после пересборки** — обновите код без сборки: `./deploy/update.sh <ветка> --no-build`;
- **тесты не прошли на обновлении** — скрипт не трогал сервис; смотрите
  `tail -60 /tmp/ats-update.log`, правьте ветку или обновляйтесь с `--fast` осознанно.

---

## 9. Ветка для продолжения работ и пуши

Работа этого блока ведётся в ветке **`arena/01a0cdee-ats`** (она же — «рабочая ветка
сессии»; в `main` не мержится, пока не скажете). Отправка изменений:

```bash
cd /opt/ats                      # или в вашей рабочей копии
git add -A && git commit -m "…"
git push origin arena/01a0cdee-ats
```

На сервере после этого — `./deploy/update.sh arena/01a0cdee-ats`.
Держать сервер на «боевой» ветке (`main`) и приходить в `arena/…` только для проверки —
тоже нормальный вариант: тогда на проде `./deploy/update.sh main`, а `arena/…` — на
стендовом инстансе (порт/данные другие).
