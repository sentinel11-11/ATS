# Интеграция Axioma ATS ↔ Мультиком (агрегатор связи / SIP-телефония)

Документ описывает, **как именно** АТС работает с Мультикомом: что уже реализовано в коде,
как это настроить, какие есть границы и что нужно запросить у оператора, чтобы подключить
боевой обзвон. Партнёрская/коммерческая часть — в `docs/MULTICOM_PARTNERSHIP.md`.

Статус на текущей ревизии: **адаптер рабочий (транспорт, пул, статусы, автодозвон, CRM),
но ждёт живых эндпоинтов оператора** — публичного описания REST API Мультикома у нас нет,
поэтому пути и поля настраиваются, а не зашиты.

---

## 1. Роль Мультикома в архитектуре

Мультиком для АТС — **`TelephonyProvider`** (слой «кому и с какого номера звонить»),
а не CRM и не «АТС целиком». Схема одна и та же, что и для МегаФон ВАТС
(`app/telephony.py`, `app/engine.py`):

```
      Engine (тик 1 с)                      Мультиком
  ┌───────────────────────┐          ┌──────────────────────────┐
  │ _start_one()          │  REST    │ makecall: «позвони      │
  │  numbers.acquire() ───┼──────────▶  этому клиенту с этого  │
  │  INSERT calls         │ dial()   │  номера, запись, user»  │
  │                       │          │                          │
  │ handle_event() ◀──────┼──────────┤ вебхук статуса звонка   │
  │  _on_multicom()       │ POST     │ ringing / answered /     │
  │   ├─ автодозвон       │ /api/v2/ │ completed / busy /       │
  │   ├─ ACD              │ webhooks/│ no_answer / failed /     │
  │   └─ CRM-пуш (amoCRM) │ multicom │ canceled                 │
  └───────────────────────┘          └──────────────────────────┘
```

Две возможные схемы подключения, обе поддерживаются кодом:

| Схема | Что делает Мультиком | Что делает АТС | Статус |
|---|---|---|---|
| **A. REST-управление + вебхук статусов** | принимает `makecall`, шлёт статусы, хранит запись | планирование, ротация номеров, автодозвон, ACD-очередь, CRM | реализовано (этот документ) |
| **B. SIP-транк на наш медиасервер** (Asterisk/FreeSWITCH) | даёт транки, номера, маршруты | всё то же + **аудио**: TTS-озвучка, IVR, собственный диалог-бот, локальная запись | транспорт `ami` готов (`app/asterisk.py`), needs-стенд |

Схема A — то, что можно показать клиенту завтра. Схема B — то, что включает
`flow=message` (озвучка сообщения) и «живой» ИИ-диалог; обычно A и B работают вместе:
номера и маршруты от агрегатора, медиа — на Asterisk.

---

## 2. Код: где что лежит

| Файл | Ответственность |
|---|---|
| `app/providers/multicom.py` | `MulticomClient` (HTTP-транспорт: account / numbers / makecall / hangup), `MulticomProvider` (контракт движка), `map_multicom_webhook` (нормализация статусов), `verify_webhook_secret`, `webhook_fingerprint`, `normalize_number`, `phone_variants` |
| `app/telephony.py` | `PROVIDER_NAMES`, фабрика `make_provider()` → `multicom` |
| `app/engine.py` | `_multicom_client`, `multicom_check`, `multicom_pool_sync`, `handle_event → _on_multicom` (корреляция по `external_call_id`, дедуп по `fingerprint`), `_multicom_completed`, `_multicom_incoming` |
| `app/api.py` | `POST /api/v2/multicom/check`, `POST /api/v2/multicom/pool-sync`, `POST /api/v2/multicom/simulate-event`, `POST /api/v2/webhooks/multicom`, health-флаги |
| `app/config.py` | секция `multicom` в настройках (всё настраивается без правки кода) |
| `frontend/src/pages/Settings.jsx` | карточка «Интеграция с Агрегатором Мультиком» + выбор провайдера |
| `tests/test_multicom_provider.py`, `tests/test_multicom.py` | 26 проверок: контракт `dial(req)`, корреляция вебхуков, секреты, автодозвон, входящие, синк пула, честный `no_media` |

---

## 3. Настройки (секция `multicom`)

| Ключ | Умолч. | Назначение |
|---|---|---|
| `api_url` | `https://api.multicom.ru/v1` | база REST API (можно вставить любой URL из письма оператора — лишнее после пути отрезается) |
| `api_key` / `api_key_env` | — / `ATS_MULTICOM_API_KEY` | токен; в UI маскируется, маска `********` не затирает сохранённое значение |
| `account_id` | `""` | идёт заголовком `X-Account-Id` (лицевой счёт/tenant) |
| `sip_host`, `sip_user`, `sip_secret` | `sip.multicom.ru`, `""`, `""` | реквизиты SIP-регистрации (для схемы B и для софтфонов операторов) |
| `webhook_secret` / `webhook_secret_env` | — / `ATS_MULTICOM_WEBHOOK_TOKEN` | **входной секрет** нашего `/api/v2/webhooks/multicom`; без него приём событий закрыт (403) |
| `default_user` | `ats` | значение `user` в makecall (кому «принадлежит» исходящий у оператора) |
| `callback_number` | `""` | если оператор работает по callback-схеме: номер, на который оператор звонит первым |
| `record` | `true` | просить ли запись разговора на стороне оператора |
| `account_path`, `numbers_path`, `makecall_path`, `hangup_path` | `/account`, `/numbers`, `/calls/make`, `/calls/{call_id}/hangup` | **пути эндпоинтов** — правятся под фактический контракт API (поддержка `{call_id}`) |
| `timeout_sec` | 15 | HTTP-таймаут |

Всё это доступно в UI (Настройки → Мультиком) и через `POST /api/v2/settings/raw`
с телом `{"provider_config": {"multicom": {…}}}`.

---

## 4. Что происходит при звонке (по шагам)

1. `_allocate()` выбрал кампанию/позицию, проверил согласие и чёрный список
   (`docs/ATS_HOW_IT_WORKS.md` §5).
2. `numbers.acquire(provider="multicom")` — номер пула, у которого есть суточный лимит,
   он не в карантине и «остыл». Номера в пул проще всего завести кнопкой
   **«Синк номеров»** (`POST /api/v2/multicom/pool-sync`): берёт `GET /numbers`,
   нормализует к E.164 без `+`, дедуплицирует, **ничего не удаляет** и не трогает
   карантин/выключатели админа. Есть `dry_run`.
3. Создаётся `calls(status='dialing')`, затем `MulticomProvider.dial(req)`:
   ```json
   POST /calls/make
   {"phone":"79002223344","caller_id":"79001112233","user":"ats",
    "direction":"outbound","record":true,"client_reference":"ats-call-1234"}
   ```
   `client_reference` — наш `call_id`: если оператор вернёт его в вебхуке, корреляция
   работает даже без внешнего id.
4. Из ответа вытаскивается внешний id (`call_id | callid | id | uuid | call_uuid`,
   в т.ч. внутри `data/result/call`) и пишется в `calls.external_call_id`.
   **Это то, по чему вебхук потом находит звонок**, — и это переживает рестарт АТС.
5. Ошибка API/сети → исключение → `_finish_attempt('failed', retryable=True)`: позиция
   уйдёт на автодозвон, а не будет «висеть успех».
6. Вебхуки оператора (`POST /api/v2/webhooks/multicom`, заголовок
   `X-Multicom-Secret` или `?token=`… см. §5) кладутся в очередь движка; HTTP-ответ —
   сразу `200`, никакой тяжёлой работы в обработчике.
7. `_on_multicom()`:
   - дедуп по `fingerprint` в таблице `provider_events` (повторы оператора не плодят
     дозвоны и CRM-пуши); не нашли звонок → пишем `orphan` и спокойно отвечаем;
   - топ-ап фактов: `recording_url` (→ потом уйдёт в amoCRM как `link`),
     фактический `caller_id` (если оператор подставил свой номер — фиксируем),
     `provider_user`;
   - `ring` → `ringing`; `answered` → общий `_on_answered` (ветка flow);
     `completed`/`done` → `_multicom_completed`; `busy/no_answer/failed` →
     `_finish_attempt(retryable)`; `canceled` → если был ответ — считаем завершённым
     разговором, иначе `failed`+ретрай; `dropped` → общий `_on_dropped`.
8. Финал → `calls.result` (`operator_ok` для `flow=operator`, `done_ok` иначе, либо
   причина недозвона) → `campaign_items` → **пуш в amoCRM**
   (`docs/AMOCRM_INTEGRATION.md`) → событие в UI по SSE.

### 4.1 Входящие

`ring` с `direction=in` и неизвестным внешним id → создаётся журнал-запись входящего
(`log_non_campaign_calls`), в UI всплывает карточка звонка; `completed` без ответа →
`missed` **+ задача «Перезвонить клиенту» в amoCRM**. Контакт автоматически не создаётся
(не спамим базу), но подтягивается по номеру, если он уже есть.

### 4.2 Статусы вебхука → события движка

`MULTICOM_EVENT_MAP` (сырой `status|state|event|event_type`, регистр не важен):

| Событие АТС | Статусы оператора |
|---|---|
| `ring` | `ring`, `ringing`, `dialing`, `originate` |
| `answered` | `answered`, `connected`, `bridged`, `answer`, `talking`, `in_progress` |
| `completed` | `completed`, `ended`, `finished`, `success`, `done`, `hangup` |
| `busy` | `busy`, `occupied`, `user_busy` |
| `no_answer` | `no_answer`, `noanswer`, `timeout`, `ring_no_answer`, `missed` |
| `failed` | `failed`, `rejected`, `error`, `call_rejected`, `unavailable` |
| `canceled` | `cancel`, `canceled`, `cancelled` |
| `dropped` | `dropped`, `disconnect`, `disconnected`, `abandoned` |

Неизвестный статус не роняет обработку: он уходит как `raw_status` и, если звонок найден,
только логируется — то есть **новый статус в API оператора не ломает АТС**. Если у
Мультикома другие названия — их можно либо добавить в таблицу (1 строка кода), либо
попросить оператора отдавать наши коды.

---

## 5. Вебхук: контракт и безопасность

```
POST https://<ваш-домен>/api/v2/webhooks/multicom
Header: X-Multicom-Secret: <multicom.webhook_secret>   (или ?secret=…)
Content-Type: application/json  (form-encoded тоже принимается)

{"call_id":"mc-12345","status":"completed","phone":"79002223344",
 "direction":"out","duration":41,"record_url":"https://…/12345.mp3",
 "clid":"79001112233","user":"101","cause":"normal clearing"}
```

- Ответ всегда быстрый: `200 {"ok":true}`; «непонятное» тело (нет id звонка) — `422`.
- **Fail-closed:** без настроенного `webhook_secret` маршрут отвечает `403
  webhook_disabled`; неверный секрет → `401 bad_secret`. Сравнение — `hmac.compare_digest`.
- Повторы (оператор ретраит свой POST) схлопываются по `fingerprint` —
  **один и тот же статус не применится дважды**.
- Идемпотентность по «итогам» обеспечивает и CRM: `uniq = ats-call-<call_id>`.
- Диагностика: `GET /api/v2/health` → `webhook: true`,
  `GET /api/v2/health/details` → `webhooks.multicom`, `stats.pool_multicom`.

Полезно для приёмки без живого оператора:
```bash
curl -s -XPOST -H "X-Ats-Token: $T" -H 'Content-Type: application/json' \
  -d '{"call_id":"mc-12345","status":"answered"}' \
  localhost:9124/api/v2/multicom/simulate-event
```
(только admin, секрет вебхука не требуется, `fingerprint` помечается `-sim`, чтобы
повторная симуляция не отбрасывалась дедупом).

---

## 6. Границы: чего в этой связке нет (важно для честного демо)

1. **Нет аудио в АТС.** `flow=message` → `no_media`, `flow=agent` без LLM → `no_channel`
   (мы принципиально не пишем «сообщение доставлено», когда озвучивать нечем).
   Решение — схема B (SIP-транк Мультикома → Asterisk, провайдер `ami`) или TTS/IVR на
   стороне оператора.
2. **Нет команды «соединить два плеча»** в REST-контракте агрегатора → `connect_operator`
   возвращает «неприменимо» (бридж фиксируется формально), реальный разговор всё равно
   доживает до `completed`. Для «тёплого» перевода нужен либо софтфон оператора на
   trunk-регистрации, либо Asterisk.
3. **Запись разговора** — только ссылкой от оператора (`recording_url`); загрузка файла в
   АТС (`POST /api/v2/calls/recording`) есть, но это ручной/внешний контур.
4. **Нет DTMF/ASR-обратной связи** от оператора → «наберите 1, если интересно» требует
   медиаслой.
5. **Нумерация лимитов** — `daily_limit` номера в АТС это наша страховка от маркировки;
   реальные лимиты оператора (частота на номер, «запрет исходящих») надо учитывать при
   выборе значений.

---

## 7. Что запросить у Мультикома для полноценной интеграции

1. Актуальное описание **REST API**: базовый URL, способ авторизации (Bearer/API-key/IP),
   эндпоинты: профиль аккаунта, список номеров, `makecall` (или «позвонить из CRM»),
   завершение звонка, статусы звонка.
2. **Вебхуки**: какие события отдают (dial/answer/hangup/busy/…), формат (JSON/form),
   есть ли подпись/секрет, какие поля коррелируют вызов (наш `client_reference`?),
   политика ретраев и дедупликации.
3. **Запись разговоров**: включение по аккаунту, где хранится, срок, подписанные ссылки,
   API выгрузки (наш `recording_url` → amoCRM `link`).
4. **SIP-транк** (если идём схемой B): адреса, кодек(ы), DTMF (inband/RFC2833/SIP INFO),
   CLI/CLIR-политика, лимит одновременных каналов, поддержка `P-Associated-URI`/diversion для входящих.
5. **Пулы номеров и лимиты:** сколько номеров можно задействовать под исходящий обзвон,
   лимиты вызовов/номер/сутки, политика блокировок и «маркировки», как подаётся жалоба,
   есть ли у оператора «белые списки»/заявки на массовый обзвон.
6. **Тестовый стенд**: тестовый аккаунт + 2–3 номера на 2 недели, IP-в whitelist,
   песочница вебхуков.
7. **SLA и поддержка**: время реакции по транку/API, канал для инцидентов,
   кто отвечает клиенту.
8. **Биллинг/тарифы для партнёров**: оптовые минуты, цена номера, white-label условия.

---

## 8. План доводки (когда есть ответы из §7)

| № | Задача | Где | Оценка |
|---|---|---|---|
| M1 | Сверить пути/поля с фактическим API (конфиг → при необходимости правки `MULTICOM_EVENT_MAP`) | `providers/multicom.py` | 0,5 дня |
| M2 | Вебхук: принять подпись оператора (если она есть) вместо/вместе с секретом | `verify_webhook_secret` | 0,5 дня |
| M3 | `GET /numbers` → синк лимитов/статусов номера (по аналогии `plan_number_sync` МегаФона): `enabled_outgoing`, карантин по жалобам | `engine.multicom_pool_sync` | 1 день |
| M4 | Запись: загрузка по ссылке/выгрузка файла → `calls.recording` (+ выдача по авторизованному API) | `api` | 1 день |
| M5 | Схема B: SIP-транк → Asterisk, `provider=ami`, включение `flow=message`/IVR/записи | стенд | 2–3 дня + стенд |
| M6 | Пилот: 2–3 номера, 500–1000 вызовов, отчёт по коннект-рейту и жалобам | — | 1 неделя календарно |

---

## 9. Чек-лист подключения (боевой)

- [ ] `settings.provider = "multicom"` (UI: Настройки → «Основной провайдер»);
- [ ] `api_url` + `api_key` заполнены, `POST /multicom/check` → `ok: true` (видно аккаунт);
- [ ] номера заведены (`pool-sync` или вручную), у каждого выставлен `daily_limit`
      (осторожно с 100+ на один номер — это прямой путь к маркировке);
- [ ] `webhook_secret` задан, оператору передан URL `https://<домен>/api/v2/webhooks/multicom`
      + секрет; `GET /api/v2/health` показывает `webhook: true`;
- [ ] тестовый `flow=operator` кампания: 1 контакт, дозвон, в журнале `operator_ok`,
      `external_call_id` заполнен, запись доступна по ссылке;
- [ ] тест недозвона: `busy`/`no_answer` → позиция ушла в `queued` с `next_attempt_at`;
- [ ] `provider_events`: нет лавины `orphan` (если есть — оператор не отдаёт/путает id);
- [ ] `crm_outbox` пуст, в amoCRM звонок с правильным статусом;
- [ ] `GET /api/v2/health/details` → `pool_multicom.usable > 0`, `engine_thread: true`.
