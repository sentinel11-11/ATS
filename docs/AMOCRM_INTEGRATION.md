# Интеграция ATS ↔ amoCRM (REST API v4)

Документ описывает актуальную (после аудита и исправлений) интеграцию исходящего
обзвона ATS с amoCRM: что и куда пишется, как настраивается, какие коды
статусов используются и что осознанно оставлено за рамками.

## 1. Архитектура

```
ATS Engine ──► CrmDriver (AmoCrm) ──► AmoCrmClient ──► amoCRM /api/v4
     │                │                     │
     │                │                     ├─ rate limiter (≤7 rps, квота amoCRM)
     │                │                     ├─ 429 → Retry-After/backoff
     │                │                     └─ 401 → OAuth refresh (если настроен) → retry
     │                │
     │                └─ push_result(call): поиск/создание контакта →
     │                   POST /calls (uniq, call_status, link) →
     │                   задача (только неуспех) → примечание (аналитика)
     │
     └─ при сбое ──► crm_outbox (SQLite) ──► повторы тиками движка
                     с экспоненциальным backoff до 8 попыток
```

Разделение ответственности: `Engine` не знает HTTP-контракта amoCRM;
`AmoCrm` — бизнес-правила (маппинг, задачи); `AmoCrmClient` — транспорт
(авторизация, квоты, OAuth).

## 2. Что пишется в amoCRM при завершении звонка

1. **Контакт** (`GET/POST /contacts`) — поиск по номеру с ТОЧНОЙ сверкой
   последних 10 цифр (amoCRM ищет нечётко); при отсутствии — создание
   (`auto_create_contacts`). Ошибки поиска (401/429/5xx) НЕ трактуются как
   «контакта нет» — push прерывается и уходит в outbox, дублей не создаётся.
2. **Звонок** (`POST /calls`) — официальный контракт телефонии:
   `uniq=ats-call-<id>` (идемпотентность), `direction`, `duration`,
   `call_status` (см. таблицу), `call_result` (текст результата),
   `responsible_user_id`, `call_responsible` (имя из `provider.user` ВАТС,
   если есть), `link` (запись из `calls.recording_url`), `created_at`
   (время начала звонка).
3. **Задача** (`POST /tasks`) — ТОЛЬКО для неуспешных результатов
   (настраивается `auto_task_results`); `task_type_id` из настроек.
4. **Примечание** (`POST /contacts/{id}/notes`, тип `common`) — детали
   звонка и итог ИИ-диалога. Системный телефонный факт живёт в `/calls`,
   заметка — только для текста/аналитики.

## 3. Маппинг результатов ATS → `call_status` amoCRM

Официальная семантика amoCRM: 1 — оставил сообщение, 2 — перезвонить позже,
3 — нет на месте, 4 — разговор состоялся, 5 — неверный номер,
6 — не дозвонился, 7 — номер занят.

| result ATS (calls.result)                              | call_status |
|--------------------------------------------------------|-------------|
| `done_ok`, `operator_ok`, `done_agent`                 | 4 — разговор состоялся |
| `busy`                                                  | 7 — номер занят |
| `no_answer`, `timeout`, `missed`, `machine`, `no_media`, `failed`, `canceled`, `exhausted` | 6 — не дозвонился |
| `wrong_number`, `invalid_number`                        | 5 — неверный номер |
| `no_operator`, `rejected`, `declined`                   | 2 — перезвонить позже |
| `blocked*`, `blacklisted`, `dialing`, пусто             | в amoCRM не отправляется |

Важно: маппинг строится по **calls.result**, а не по calls.status
(у завершённого звонка status всегда `'done'`). Переопределение —
`amocrm.call_status_map = {"machine": 1}` и т.п.

## 4. Настройки (`Настройки → CRM` или /settings/raw, секция `amocrm`)

| Поле | Назначение |
|---|---|
| `subdomain` | субдомен аккаунта (`company` из company.amocrm.ru) |
| `access_token` / `access_token_env` | долгосрочный токен (вкладка «Ключи» приватной интеграции) |
| `client_id`, `client_secret`, `refresh_token`, `redirect_uri` | OAuth-режим: по 401 клиент обновляет пару токенов и АТОМАРНО сохраняет новую (refresh одноразовый) |
| `responsible_user_id` | ответственный по умолчанию для контактов/звонков/задач |
| `operator_user_map` | карта `{"vats_login/ext": amo_user_id}` — звонок/задача вешаются на реального сотрудника |
| `use_contact_responsible` | брать ответственного из найденного контакта (true по умолчанию) |
| `auto_create_contacts` / `auto_create_tasks` | автосоздание контактов / задач «перезвонить» |
| `auto_task_results` | список результатов, для которых создаётся задача |
| `task_type_id` | id типа задачи (справочник: `GET /account?with=task_types`) |
| `call_status_map` | переопределение маппинга результатов |
| `webhook_token` / `webhook_token_env` | секрет вебхука amoCRM (fail-closed: без него вебхук выключен) |
| `rate_limit_rps` | локальный лимит запросов (≤7/с — квота amoCRM) |
| `timeout_sec` | HTTP-таймаут |

Секреты можно хранить в переменных окружения (поля `*_env`), в БД значения
маскируются в UI.

## 5. Вебхук `POST/GET /api/v2/amocrm/webhook`

- Внешний (без X-ATS-Token), защищён секретами:
  - **POST** (события/клик из виджета): `?token=<webhook_token>` или заголовок
    `X-Amo-Token`. Click-to-call в движке пока не реализован — ответ `501
    click2call_not_supported` (явно, без падений).
  - **GET** (хук отключения интеграции): `client_uuid`, `account_id`,
    `signature`; подпись проверяется как `HMAC-SHA256(client_uuid+account_id,
    client_secret)` по официальной схеме amoCRM. При валидной подписи
    интеграция выключается (`amocrm.disabled=true`, драйвер fail-closed).
- Без `webhook_token` / `client_secret` маршрут выключен (`403 webhook_disabled`).

## 6. Синхронизация менеджеров `POST /api/v2/amocrm/users-sync`

Забирает `GET /users` и СОХРАНЯЕТ снимок в таблицу `amo_users` (раньше
возвращался JSON без сохранения). Ошибки amoCRM (401/403/429/5xx) теперь
пробрасываются как сбой синхронизации, а не как «пользователей 0».

## 7. Надёжность доставки: `crm_outbox`

При любой ошибке `push_result` (amoCRM недоступна, 401/429/5xx, сеть)
снапшот звонка кладётся в `crm_outbox`; движок повторяет доставку каждый тик
(пачками до 5), backoff `2^attempts` минут (потолок 2ч), после 8 попыток —
`failed`. `uniq` делает повтор безопасным: дубль звонка в amoCRM не создаётся.

## 8. Unsorted («Неразобранное»)

`create_unsorted()` — только для ВХОДЯЩИХ звонков (так задуман SIP-режим
amoCRM): снабжён обязательным `metadata` (номер, время, длительность,
ссылка на запись) и `source_uid=uuid4`. Исходящий обзвон в неразобранное
не кладётся — регистрируется через `/api/v4/calls`.

## 9. Сознательно не реализовано (дорожная карта amoCRM Integration v2)

- Полный OAuth-рконнект из UI (кнопка «Подключить amoCRM», redirect flow,
  хранение `oauth_states`): сейчас OAuth поддержан на уровне refresh-ротации.
  Рекомендуемый прод-режим — долгосрочный токен приватной интеграции
  (1 день — 5 лет, без refresh).
- Воронки/этапы (`/leads/pipelines`), создание сделок `/leads`, таблица
  связей `crm_links` (ATS entity ↔ amo entity) — нужны для сценария
  «кампания → сделка в воронке».
- Подписанные URL записей разговоров (локальные записи отдаются только по
  авторизованному API; в `link` уходит провайдерская `recording_url` ВАТС).
- Click-to-call из виджета amoCRM (возвращается явный 501).
- Мультиаккаунт amoCRM (сейчас один аккаунт на инстанс ATS), платформа
  amocrm.com/kommo.com (хост зашит `*.amocrm.ru`).
- Пакетное создание контактов (batch) при массовом импорте.
