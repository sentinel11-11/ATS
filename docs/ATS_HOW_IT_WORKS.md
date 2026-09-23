# Как устроена Axioma ATS v2: подробное объяснение работы программы

Документ написан по фактическому коду репозитория (ветка `arena/01a0cdee-ats`).
Ссылки вида `app/engine.py:_start_one` — это «где смотреть в коде».

Родственные документы:
- `docs/AMOCRM_INTEGRATION.md` — интеграция с amoCRM (контракт + настройка + диагностика);
- `docs/MULTICOM_INTEGRATION.md` — интеграция с агрегатором Мультиком (телефония);
- `docs/DEPLOY.md` — установка, запуск, обновление на сервере, откат;
- `README_V2.md` — быстрый старт и список REST-маршрутов.

---

## 1. Что это такое, простыми словами

Axioma ATS — это **диспетчер исходящего обзвона + обвязка телефонии**, который стоит
**между** вашей базой контактов/CRM и оператором связи (Мультиком, МегаФон ВАТС,
Asterisk/SIP-транк, UIS). Сама АТС **не производит звук**: голосовой тракт и запись —
на стороне оператора или медиасервера (Asterisk/FreeSWITCH). ATS делает всё остальное:

1. хранит базы контактов, согласия, чёрные списки и жалобы;
2. по расписанию ведёт кампании («карусель»: кому звонить, с каким сценарием);
3. выбирает, с какого номера пула звонить (ротация CallerID, лимиты, «остывание», карантин);
4. даёт команду оператору «позвони», ловит от него события (дозвон/ответ/завершение);
5. решает, что делать с ответом: озвучить сообщение, прогнать ИИ-сценарий, передать оператору (ACD);
6. автодозванивается по причинам «не дозвонились» — с паузами и лимитом попыток;
7. пишет итоги в журнал и **в CRM (amoCRM)** — с гарантией доставки, задачи «перезвонить»,
   примечания, ссылку на запись разговора;
8. показывает всё это в web-интерфейсе в реальном времени (SSE) и считает отчёты;
9. ведёт аудит действий админов и операторов.

Один процесс Python, одна база SQLite, один HTTP-порт. Ни Redis, ни Postgres, ни Docker
не требуются — это осознанное требование эксплуатации (см. §12).

---

## 2. Архитектура по слоям

```
                Браузер (React UI, app/ui — собранный фронт)
                        │  REST /api/v2/*  +  SSE /api/v2/events
                        ▼
        ┌──────────────────────────────────────────────────────┐
        │ app/server.py   HTTP-сервер (stdlib ThreadingHTTPServer)
        │   • статика app/ui (SPA), защита от path traversal
        │   • /api/v2/* → api.route()      • /api/v2/events → SSE
        ├──────────────────────────────────────────────────────┤
        │ app/api.py      REST-роутер + бизнес-обработчики + вебхуки
        │   • авторизация/роли, rate-limit входа, аудит (audit.py)
        │   • вебхуки операторов: /webhooks/uis, /webhooks/megafon,
        │     /webhooks/multicom, /amocrm/webhook   (внешние, без сессии)
        ├──────────────────────────────────────────────────────┤
        │ app/engine.py   ДВИЖОК (отдельный поток, тик 1 с)
        │   очередь событий провайдера → FSM звонка → allocate →
        │   автодозвон → watchdog → ACD → CRM-пуш → outbox-повторы
        ├───────────────┬──────────────────┬────────────────────┤
        │ telephony.py  │  crm.py          │  agent.py          │
        │ адаптеры      │  CRM-драйверы    │  ИИ-агент L1/L2    │
        │ sim/uis/ami/  │  csv/bitrix24/   │  сценарий-дерево + │
        │ megafon_vats/ │  amocrm          │  OpenAI-совмест.   │
        │ multicom      │                  │  LLM (опц.)        │
        ├───────────────┴──────────────────┴────────────────────┤
        │ app/numbers.py  пул номеров (лимиты/кулдаун/карантин)  │
        │ app/db.py       SQLite (WAL) + схема + миграции +      │
        │                 настройки + снимки справочников         │
        └────────────────────────────────────────────────────────┘
                        ▲                         ▲
             REST + вебхуки операторов      REST API v4 amoCRM
             (Мультиком / МегаФон / Asterisk AMI)
```

**Ключевая идея разделения:** движок не знает HTTP-контрактов ни одного оператора и ни
одной CRM. Есть два интерфейса-адаптера:

| Интерфейс | Где определён | Реализации |
|---|---|---|
| `TelephonyProvider` | `app/telephony.py` | `sim` (стенд), `uis` (каркас), `ami` (Asterisk Manager), `megafon_vats` (REST ВАТС), `multicom` (REST агрегатора) |
| `CrmDriver` | `app/crm.py` | `csv` (выгрузка), `bitrix24` (каркас), `amocrm` (REST API v4) |

Новый оператор = новый класс с методами `configure / dial / hangup` (+ опционально
`external_id / connect_operator / make_channel`). Новый CRM = новый `push_result`.
Ядро, ACD, автодозвон, пул номеров при этом не трогаются.

Реестр провайдеров — один на весь код: `app/telephony.py:PROVIDER_NAMES`
(`sim, uis, ami, megafon_vats, multicom`). Из него же берутся допустимые значения
`settings.provider`, `--provider` в CLI и `numbers.provider` в пуле.

---

## 3. Модули и их ответственность

| Файл | Что делает |
|---|---|
| `app/config.py` | Пути (`data_v2/`, `logs/`, `recordings/`, `crm_out/`), `DEFAULT_SETTINGS` — все умолчания продукта. Переопределяется env: `ATS_DATA_DIR`, `ATS_DB`, `ATS_FAST`, `ATS_ADMIN_PASSWORD`, `ATS_DEV_SEED`, `ATS_ALLOW_SIM_FALLBACK` |
| `app/db.py` | Схема SQLite, лёгкие миграции `ALTER` в `_migrate()`, импорт legacy-JSON, `get_settings/save_settings` (таблица `settings`, одна строка id=1), `public_settings()` (не отдаёт секции с секретами), outbox-функции, снимки `amo_users/vats_users/vats_groups` |
| `app/security.py` | PBKDF2-SHA256, 120 000 итераций, соль 16 байт; токены сессий в памяти (TTL 8 ч, продление при обращении); `drop_sessions_for(login)` — сброс сессий при смене пароля/деактивации |
| `app/events.py` | Шина событий + SSE-подписчики (последнее событие затирается при переполнении очереди клиента — UI не «вешается») |
| `app/audit.py` | Аудит действий: кто/откуда/что сделал, маскирование секретов в `details_json` |
| `app/api.py` | ~1900 строк: аутентификация, все роуты, вебхуки операторов, синки, загрузка записей, отчёты, health |
| `app/engine.py` | Диспетчер: тик, события, дозвон, автодозвон, watchdog, ACD, CRM-пуш, outbox |
| `app/numbers.py` | Пул CallerID: `acquire()` (лимит/кулдаун/карантин/вес), `mark_used/mark_answered`, `pool_state`, сброс суточных счётчиков |
| `app/agent.py` | ИИ-агент: `run_scripted()` (дерево сценария), `AgentLLM` (OpenAI-совместимый L2) |
| `app/crm.py` | `CrmDriver` + `CsvCrm` + `Bitrix24Crm` + `AmoCrm` (маппинг результатов, задачи, заметки, ответственные) |
| `app/providers/amocrm.py` | HTTP-клиент amoCRM v4: транспорт, квоты, OAuth-refresh |
| `app/providers/megafon_vats.py` | Клиент + провайдер МегаФон ВАТС, нормализация вебхуков, план синка пула |
| `app/providers/multicom.py` | Клиент + провайдер агрегатора Мультиком, нормализация вебхуков, секрет вебхука |
| `app/server.py` | HTTP-сервер: раздача SPA, прокси в `api.route()`, SSE-эндпоинт, лимит тела 64 МБ |
| `app/run.py` | CLI: запуск, `--provider`, `--admin-password`, `--list-users`, `--set-password`, `--clean-demo`, бэкап БД при старте |
| `frontend/` | React + Vite + Tailwind; собирается в `app/ui` (`npm run build`) |
| `xp_bridge/`, `server.py`, `data/` | LEGACY-прототип (GSM-модемы, Windows). v2 его не использует; `data/*.json` импортируются при первой инициализации БД |

---

## 4. Данные: что и где хранится

Один файл `data_v2/ats.db` (SQLite, `PRAGMA journal_mode=WAL`, `busy_timeout=5000`).

| Таблица | Назначение |
|---|---|
| `users`, `operators` | Учётки (роль `admin`/`operator`) и операторы ACD (внутренний номер, `vats_login`, статус) |
| `databases`, `contacts` | Базы обзвона и контакты: `phone` (уникальный индекс!), `consent`, `consent_source`, `blacklisted`, `complaints`, `tags`, `database_id` |
| `templates` | Шаблоны текста + `scenario` (JSON-дерево ИИ-диалога) |
| `numbers` | Пул номеров: `daily_limit`, `weight`, `cooldown_until`, `daily_date/daily_count`, `quarantined`, `enabled_outgoing`, `dialed_total/answered_total`, `provider_ref` |
| `campaigns` | Кампания: `flow` (`message`/`agent`/`operator`), `status`, `schedule` {start,end,days}, `max_channels`, `retry_max`, `retry_delay_min`, `retry_map`, `connect_on_qualify`, `scenario`, `llm_criteria` |
| `campaign_items` | Позиция очереди («кому и когда») — именно её статус видит оператор в UI |
| `calls` | Звонок-попытка: `status`, `result`, `detail`, `duration_sec`, `caller_id`, `number_id`, `provider`, `external_call_id`, `direction`, `agent_result`, `recording`, `recording_url`, `provider_user`, `wait_sec`, `missed_status`, `rating` |
| `attempts` | История попыток (1 звонок = N записей) |
| `acd` | Очередь/история переводов: `queued → offered → ringing → answered → bridged → completed/missed` |
| `agent_sessions` | Транскрипты диалогов ИИ-агента |
| `blacklist` | «Не звонить» (телефон + причина + источник) |
| `provider_events` | Каждый вебхук оператора с `fingerprint` — дедупликация и форензика |
| `crm_outbox` | Очередь доставки в CRM с backoff (см. §9) |
| `amo_users`, `vats_users`, `vats_groups` | Снимки справочников amoCRM/ВАТС для связей «оператор ATS ↔ человек в CRM/АТС» |
| `events`, `audit_logs` | События (для тяжёлой истории) и аудит админских действий |

**Резервные копии:** при каждом старте `app/run.py:_backup_db` делает корректный снимок
через `sqlite3 backup` в `data_v2/backups/ats-<ts>.db` (хранится 12 последних);
`deploy/update.sh` дополнительно делает `ats-before-update-<ts>.db` (20 последних).

---

## 5. Жизненный цикл кампании: полный проход

Движок — один поток `ats-engine`, цикл `Engine._loop → tick_once()` (`app/engine.py`):

```
tick_once():
    1) drain_events → handle_event(...)     # все события операторов подряд
    2) _allocate()                          # запуск новых дозвонов
    3) _watchdog()                          # зависшие звонки, ACD, страховки
    4) _crm_outbox_tick()                   # повторная доставка в CRM
    sleep(1.0 c)                            # 0.3 c при ATS_FAST=1 (тесты/демо)
```

### 5.1 `_allocate()` — сколько и кому звонить
1. Перечитывает настройки из БД (правки из UI применяются «на лету», без рестарта) и
   сбрасывает суточные счётчики номеров при смене даты.
2. Берёт кампании со `status='running'`; для каждой проверяет окно`in_window(schedule, settings)`:
   дни недели + `start`/`end` (умолч. 08:00–20:00, пн–вс).
3. Общий лимит `max_channels` (умолч. 3) и личный `campaign.max_channels`
   (0 = из настроек; берётся `min` из двух). Свободные каналы =
   `active_channels()` — число `calls` с пустым `ended_at`.
4. На каждый свободный слот — `_start_one()`.

### 5.2 `_start_one()` — одна попытка дозвона
1. `SELECT ... campaign_items WHERE status='queued' AND (next_attempt_at='' OR <= now) LIMIT 1` —
   позиция из очереди, у которой наступило время попытки.
2. Проверки (звонка не будет, позиция получит терминальный статус):
   - контакта нет → `error`;
   - `consent_required=True` и нет согласия → `blocked_no_consent` (152-ФЗ-режим);
   - контакт в чёрном списке → `blacklisted`.
3. `numbers.acquire(provider=<имя провайдера>, cooldown_sec)` — если пул пуст/остыл/
   в лимите/в карантине, возвращает `None` → **в этом тике не звоним**, ждём следующий.
   Это же и защита от «звонков в никуда» при неверном provider у номеров.
4. Подстановка шаблона: `{name} {phone} {group} {note}`.
5. Создаётся запись `calls (status='dialing')`, позиция → `dialing`, `attempts += 1`.
6. `provider.dial({call_id, phone, caller_id, flow, text, template_id})`.
   Ошибка оператора → `calls.result='failed'` + `_finish_attempt(retryable=True)`.
7. Если провайдер умеет `external_id(call_id)` — внешний id (и фактический CID)
   **сохраняются в `calls`**, поэтому вебхуки коррелируются даже после рестарта ATS.
8. `numbers.mark_used()` (кулдаун + суточный счётчик), событие `call` в SSE.

### 5.3 `handle_event()` — события оператора
Два вида: «внутренние» (sim, AMI) — содержат наш `call_id`; «внешние» от REST-операторов
(`megafon_vats`, `multicom`) — содержат только внешний id, их маршрутизирует
`_on_megafon` / `_on_multicom`, которые резолвят звонок по `external_call_id` и
дедуплицируют по `fingerprint`.

| Событие | Реакция движка |
|---|---|
| `ring` | `calls.status='ringing'`, позиция → `dialing` |
| `answered` | `_on_answered()`: человек/автоответчик, далее — ветка flow |
| `status` | `_finish_attempt(...)` с retryable по причине |
| `dropped` | канал закрылся: если ждали оператора → `no_operator`, иначе `timeout`+ретрай |
| `done` | звонок закрыт |

### 5.4 `_on_answered()` — три сценария
- **автоответчик** (`human=False`) → `machine` (ретрай);
- **`flow=message`**: если у провайдера нет аудио (`supports_media=False` — это
  `megafon_vats` и `multicom`, REST-клиенты без медиаслоя) → честный
  `no_media`, **а не** фиктивное «сообщение доставлено». С медиаслоем
  (Asterisk/AMI) — озвучка и `done_ok`;
- **`flow=operator`** → позиция/звонок в `wait_operator`, запись в `acd(status='queued')`,
  событие в SSE (операторы видят «ждёт звонка»);
- **`flow=agent`** → ИИ-диалог (см. §7). Если клиент квалифицирован и в кампании включён
  `connect_on_qualify` → перевод в ACD, иначе `done_agent`.

### 5.5 `_finish_attempt()` — финал попытки и автодозвон
Пишет `calls(status='done', ended_at, duration_sec, result, detail)`, затем:
- `ok=True` → позиция = результат (`done_ok/operator_ok/done_agent`), `completed_at`;
- ретрайбл (`busy/no_answer/machine/failed/timeout`) и `attempts < retry_max` →
  позиция возвращается в `queued` с `next_attempt_at = now + delay`;
- иначе → терминальный статус (`exhausted`, если были ретраи; иначе причина).
В `attempts` — строка истории. Публикуется **новый** статус позиции (не протухший).
В CRM пушится только финал: `ok` или `not retryable` или `attempts >= retry_max`.

**Интервал ретрая** (`_retry_delay`): сначала `campaign.retry_map[причина]` (минуты,
например `{"busy":10,"no_answer":30}`), иначе базовый `retry_delay_min` с эскалацией
`base * (attempts+1)`, потолок — 24 ч.

---

## 6. Пул номеров: зачем и как

Номера («линии») — расходный ресурс: операторы и антиспам-системы (например
«Запрет вызова»/маркировка «Спам/Возможно мошенничество») душат номера за
массовые обзвоны. Поэтому `app/numbers.py`:

- `daily_limit` на номер (умолч. 100) + счётчик `daily_count`, сброс при смене даты
  (дата проверяется **на каждом обращении**, а не кэшируется, иначе после полуночи лимит
  считался бы вчерашним);
- `cooldown_until` — «остывание» после дозвона (`line_cooldown_sec`, умолч. 5 с);
- `weight` — приоритет («основной» номер круче «резервного»);
- `quarantined` — карантин. Вручную в UI или автоматически: при жалобе абонента
  (`POST /api/v2/complaint`) считаются жалобы на номер за всё время; если их
  `>= auto_quarantine_on_complaints` (умолч. 3) — номер в карантин;
- `enabled_outgoing` — выключатель «номер есть, но исходящие с него нельзя»
  (управляется синком с оператором, см. `plan_number_sync`);
- `provider` — привязка к транспорту: `acquire()` выбирает номер **того же** провайдера,
  что выбран в настройках;
- статистика `dialed_total/answered_total` — конверсия дозвона по номеру (видно в UI).

---

## 7. ИИ-агент (flow=agent)

`app/agent.py`, два уровня:

**L1 — сценарное дерево (работает всегда, не требует LLM).**
Шаблон хранит `scenario` JSON:
```json
{"greeting": "Здравствуйте! ...",
 "questions": [{"id": "q1", "text": "Вам удобно говорить? Скажите «да» или «нет».",
                "choices": {"да": {"next": "q2"}, "нет": {"next": "end", "qualified": false}}}],
 "qualified_text": "Спасибо, передаю специалисту.",
 "not_qualified_text": "Спасибо, до свидания.",
 "default_qualified": false}
```
Ответ нормализуется (`normalize_answer`: `1/да/yes/ага/хочу/интересует → "1"`, `2/нет/… → "2"`).
Кампания может переопределить сценарий шаблона (`campaigns.scenario`).
Результат: `{qualified, answers[], summary, transcript, engine:"scripted_l1"}`.

**L2 — LLM (OpenAI-совместимый).** Включается `settings.llm.enabled` + `api_key_env`
(умолч. `ATS_LLM_KEY`) + `base_url`/`model`. Промпт строится из контакта +
`campaign.llm_criteria`; ответ парсится в `qualified`. Ошибка LLM →
`engine:"llm_error", qualified:false` (звонок не «зависает»).

Провайдер без интерактивного канала (`make_channel() is None`, это `megafon_vats` и
`multicom`) даёт честный `engine:"no_channel"` — диалог не имитируется.

Результат пишется в `calls.agent_result` (JSON) и `agent_sessions` (транскрипт);
в amoCRM уходит в примечание к контакту (см. `docs/AMOCRM_INTEGRATION.md` §2.4).

---

## 8. ACD: перевод на сотрудника

Таблицы `operators` + `acd`. Механика (`Engine.accept_acd`):

1. Звонок в `acd` со статусом `queued` (его создаёт `_to_operator_queue`).
2. Оператор жмёт «Принять» (или API с конкретным `operator_id`).
3. Проверяется доступность: оператор `status='free'`; если провайдер требует
   `needs_operator_ext` (Asterisk/ВАТС-бридж) — у оператора обязан быть внутренний номер,
   иначе мгновенный «accept» был бы ложным успехом → `operator_no_ext`.
4. **Резервирование до звонка провайдеру**: `acd→ringing`, `calls→operator_ringing`,
   `operators→busy`. Это защищает от двойного принятия одного и того же звонка.
5. `provider.connect_operator(call_id, ext, progress)` — реальный бридж:
   - AMI: `Originate` оператора → `OriginateResponse` → `Bridge`, иначе откат;
   - `megafon_vats`: бридж уже построила ВАТС (callback-схема), провайдер возвращает
     `True` только если звонок реально отвечен и ещё не завершён;
   - `multicom`: API агрегатора бриджа не даёт → базовый `None` = «бридж неприменим»,
     фиксируется формально, разговор доживает до финального вебхука;
   - провал (`False`) → `_acd_rollback()`: позиция обратно в очередь, оператор свободен
     (если у него нет другого живого занятия).
6. Успех → `acd=bridged`, `calls.result='operator_ok'`, позиция `operator_ok`,
   пуш в CRM, best-effort presence-синхронизация в ВАТС (`set_dnd`).
7. `POST /api/v2/calls/complete` — оператор завершил: `free`, `acd=completed`,
   `hangup`, CRM-пуш.

Watchdog ACD:
- `acd_wait_timeout_sec` (умолч. 60 с) — не приняли → `no_operator` + **задача
  «перезвонить» в CRM** (`_no_operator_finish`); для звонков, где разговор ведёт оператор
  (`megafon_vats`/`multicom` с `external_call_id`), короткий таймаут не применяется;
- `ringing/answered` зависли между фазами → возврат в очередь, оператор разблокирован.

---

## 9. Гарантии доставки в CRM (это же — защита от «потерянных» итогов)

`Engine._push_crm` → `crm.push_result(call_row)`. Любое исключение (CRM недоступна,
401/429/5xx, сеть, рестарт между попытками) → снапшот звонка уходит в `crm_outbox`
(`kind='push_result'`). Каждый тик `_crm_outbox_tick()` берёт до 5 созревших записей,
повторяет отправку, при ошибке ставит `next_attempt_at = now + 2^attempts` минут
(потолок 2 ч) и после 8 попыток помечает `failed`.

Повтор безопасен: `calls.uniq = "ats-call-<id>"` в amoCRM идемпотентен, дубль не
создаётся. Отдельно дедуплицируются вебхуки операторов (`provider_events.fingerprint`),
поэтому «одно и то же событие дважды» не порождает двойной дозвон/двойной пуш.

---

## 10. Watchdog и честность статусов

| Механизм | Что ловит | Результат |
|---|---|---|
| `watchdog_timeout_min` (20) | `dialing/ringing` без финала | `timeout` + ретрай, `hangup` |
| `acd_wait_timeout_sec` (60) | клиент ждёт оператора, никто не принял | `no_operator` + задача в CRM |
| `vats_conversation_timeout_min` (30) | оператор/ВАТС не прислал финал (`wait_operator` с внешним id) | `timeout` + ретрай |
| heal-логика (`_megafon_heal_no_operator`, `history`-top-up) | гонка watchdog ↔ факт оператора | результат звонка правится по «земным» данным, позиция кампании не переоткрывается (нет двойных дозвонов и CRM-пушей) |

Принцип, который проходит через весь код: **лучше честный `no_media`/`no_channel`/
`failed`, чем красивый `done_ok`**. Это критично и для отчётов, и для amoCRM, и для
разбора рекламаций.

---

## 11. Fail-closed: где и почему

- **Провайдер не выбран** → движок не стартует (`make_provider` бросает
  `ProviderNotConfigured`). Тихий откат на `sim` запрещён (иначе оператор думал бы, что
  звонки идут); для стенда — явный `ATS_ALLOW_SIM_FALLBACK=1`.
- **CRM-драйвер неизвестен/с опечаткой** → `ValueError`, а не молча CSV.
- **Вебхуки** (`uis`, `megafon`, `multicom`, `amocrm POST`) без настроенного секрета →
  `403 webhook_disabled`.
- **Сохранение настроек**: если UI прислал маску `********` вместо ключа, сохранённый
  секрет не затирается; если сохранённого ключа нет — `400 secret_missing_*` с объяснением.
- **Ошибки поиска контакта в amoCRM** не трактуются как «контакта нет» (иначе плодились
  бы дубли) — пуш прерывается и уходит в outbox.
- **401 от внешнего API никогда не проксируется наружу как 401** (см. §6
  `docs/AMOCRM_INTEGRATION.md`): HTTP 401 в этом проекте означает только «истекла сессия ATS».

---

## 12. Безопасность и разграничение прав

- Все приватные роуты — заголовок `X-Ats-Token` (для SSE допускается `?token=`);
  роли `admin`/`operator`; часть маршрутов — только admin (`users`, `settings/raw`,
  `reports`, `amocrm/*`, `multicom/*`, `megafon/*`, экспорт, загрузка записей).
- Пароли: PBKDF2-SHA256 × 120 000, индивидуальная соль. Смена/сброс пароля или
  деактивация пользователя сбрасывают его активные сессии.
- Вход: 5 неудачных попыток на логин за 10 минут → блокировка на 300 с (`429 too_many_attempts`).
- Секреты в UI/API маскируются (`_mask_secrets`), в `public_settings` секции с ключами
  не отдаются вовсе; в аудит не пишутся.
- Аудит: `audit_logs` (кто, что, откуда, `path`, `details_json`, код ошибки), UI-раздел
  «Админские логи», фильтры — см. `docs/ADMIN_LOGS.md`.
- Статика: резолв пути с проверкой, что файл внутри `app/ui` (защита от `../`);
  `Cache-Control: no-store`.
- Тело запроса ограничено 64 МБ (загрузка баз .xlsx и записей разговоров).
- Health-check (`GET /api/v2/health`) публичный, но отдаёт только флаги без секретов.

---

## 13. Веб-интерфейс и API

UI — React-SPA (`frontend/`), собирается в `app/ui` и раздаётся тем же процессом:
Обзор, Кампании, Журнал звонков, Контакты и базы, Пул номеров, Операторы/ACD, Шаблоны и
сценарии, Чёрный список, Отчёты, Настройки, Админские логи (последние три — admin-only).
Живые изменения приходят по SSE (`useSSE`), никаких опросов раз в секунду.

REST v2 (полный список — `README_V2.md` §4). Основные группы:
`auth`, `contacts`/`databases`/`import*`, `campaigns*`, `calls`, `journal`,
`templates`, `acd`/`operators`, `numbers*`, `blacklist`/`complaint`,
`settings`/`settings/raw`, `reports`, `export/*`, `records`, `audit/logs`,
`megafon/*`, `multicom/*`, `amocrm/*`, `webhooks/*`, `health*`, `sim/script`.

Пример ручного управления кампанией:
```bash
T=$(curl -s -XPOST localhost:9124/api/v2/auth/login -d '{"login":"admin","password":"…"}' | jq -r .token)
curl -s -H "X-Ats-Token: $T" localhost:9124/api/v2/dashboard | jq '.campaigns_running, .items'
curl -s -XPOST -H "X-Ats-Token: $T" localhost:9124/api/v2/campaigns/3/start
```

---

## 14. Настройки, которые реально влияют на обзвон

| Ключ | Умолч. | Смысл |
|---|---|---|
| `provider` | `""` (fail-closed) | транспорт телефонии |
| `max_channels` | 3 | сколько звонков одновременно |
| `consent_required` | true | звонить только при наличии согласия |
| `retry_max` / `retry_delay_min` | 2 / 15 | автодозвон: попытки и базовый пауза |
| `line_cooldown_sec` | 5 | пауза «остывания» номера после дозвона |
| `watchdog_timeout_min` | 20 | зависший дозвон → timeout+ретрай |
| `acd_wait_timeout_sec` | 60 | клиент не дождался оператора → no_operator + задача в CRM |
| `vats_conversation_timeout_min` | 30 | нет финала от оператора → страховка (действует и для multicom) |
| `window_start/window_end/window_days` | 08:00 / 20:00 / все дни | разрешённое окно обзвона |
| `auto_quarantine_on_complaints` | 3 | жалоб на номер ≥ N → карантин |
| `log_non_campaign_calls` | true | писать входящие/нескамповые звонки в журнал |
| `sim_outcome`, `sim_answer` | — | поведение стенда `sim` |

Менять можно из UI (Настройки) или `POST /api/v2/settings/raw` — движок перечитывает
настройки каждый тик, рестарт не нужен (кроме смены `provider`, которая пересоздаёт
адаптер: это делает `Engine.reload_settings()` автоматически после сохранения).

---

## 15. Производительность и масштабирование (что реально)

- Тик 1 с; `max_channels` — основной регулятор нагрузки; узкое место на практике —
  квоты оператора и `daily_limit` номеров, не CPU.
- Один инстанс = одна SQLite-база (WAL) + один движок. Горизонтальное масштабирование =
  «несколько инстансов, у каждого свои номера и своя CRM» (общую базу на NFS не класть:
  SQLite по сети — плохая идея).
- amoCRM-квота 7 req/с зашита в клиент (локальный лимитер + backoff на 429).
- HTTP-сервер — stdlib `ThreadingHTTPServer`: для UI+API сотен операторов достаточно;
  при росте — Nginx перед ATS (буферизация SSE требует `proxy_buffering off`).

---

## 16. Что сознательно НЕ умеет v2 (чтобы не обещать лишнего)

- **Свой медиа-слой**: нет TTS/IVR/записи разговора внутри ATS. Нужен Asterisk
  (`provider=ami`, транспорт написан) или оператор, который сам даёт запись/IVR.
  Пока этого нет: `flow=message` на REST-операторах = `no_media`, `flow=agent` = `no_channel`.
- **Click-to-call из виджета amoCRM** — маршрут есть, но отвечает `501`.
- **Воронки/сделки amoCRM** (`/leads`, этапы) — не реализованы.
- **Биллинг/тайфинды**, многоуровневые очереди со стратегиями (round-robin, skills) —
  есть простая FIFO-очередь и presence.
- **Один инстанс = один аккаунт amoCRM** и один оператор связи «по умолчанию»
  (адаптеры нескольких операторов сосуществуют, но активен один на движок).
- Телефония «из браузера» (WebRTC-софтфон) — нет: оператор принимает звонок на свой
  внутренний номер/softphone.

---

## 17. Как читать журнал, если что-то пошло не так

1. `GET /api/v2/health` и `GET /api/v2/health/details` (admin) — провайдер, живость,
   вебхуки, пул (`pool_megafon`, `pool_multicom`), движок-поток.
2. UI «Журнал звонков» → карточка звонка: `status/result/detail/agent_result`, тайминги.
3. Лог процесса (`data_v2/logs/ats.log`, если сервис через `deploy/run`-скрипт, или
   лог systemd: `journalctl -u ats -f`) — там же строки `[multicom]`, `[megafon]`, `[crm]`.
4. `sqlite3 data_v2/ats.db "SELECT id,status,attempts,last_error FROM crm_outbox ORDER BY id DESC LIMIT 20"` —
   что не ушло в CRM.
5. `sqlite3 data_v2/ats.db "SELECT provider,event_type,status,COUNT(*) FROM provider_events GROUP BY 1,2,3"` —
   сколько событий оператора и сколько orphan/dup.

Подробнее по amoCRM — `docs/AMOCRM_INTEGRATION.md` (раздел «Диагностика»).
