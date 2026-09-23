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
