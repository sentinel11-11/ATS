# ATS v2 — платформа исходящего обзвона (реализация в репозитории)

> Статус: **v2 MVP реализован и работает в репозитории** (2026-09-05).
> Верхнеуровневые планы — `ПЛАН_ДОРАБОТКИ_ATS.md` (редакция 3) и `ДОРОЖНАЯ_КАРТА_ATS.md` (очередь T01–T42). Этот файл — практическое руководство по коду v2.

## 1. Что это

ATS v2 — переработанное ядро старого прототипа `server.py`/`xp_bridge` (тот код остался в репозитории как legacy и v2 не используется). Реализовано под требования заказчика:

- **Кампании обзвона** (карусель): список контактов → автопрогон по расписанию, старт/пауза/стоп.
- **Автодозвон**: повторные попытки для `busy / no_answer / machine / failed` с интервалами и максимумом попыток.
- **Пул номеров (ротация Caller ID)**: суточные лимиты, «остывание» после звонка, веса, карантин по жалобам — защита номеров от маркировки антиспамом («Защитник»).
- **Согласия и чёрный список**: звонки только контактам с consent, «не звонить» снимает контакты с очередей, жалобы копятся и уводят номер в карантин.
- **Три сценария звонка (flow)**:
  - `message` — озвучка сообщения (текст шаблона);
  - `agent` — ИИ-агент: сценарный диалог L1 (вопросы/ответы 1–2) либо LLM L2 (OpenAI-совместимый, если настроен `llm`); квалифицированных — на оператора;
  - `operator` — сразу перевод на оператора (ACD-очередь).
- **ACD (перевод на сотрудника)**: операторы, статусы, очередь, принять/завершить, контекст (кто звонил, ответы бота).
- **Телефония за интерфейсом `TelephonyProvider`**: `sim` (симуляция, работает без оборудования), `uis` (UIS/МегаФон «Ювис», каркас), `ami` (Asterisk, каркас).
- **CRM за интерфейсом `CrmDriver`**: `csv` (файловый экспорт результатов, работает сейчас), `bitrix24` (каркас).
- **Безопасность**: обязательная авторизация (X-Ats-Token), роли admin/operator, PBKDF2, пароль админа генерируется при первом запуске.
- **Хранение**: SQLite (WAL), журнал попыток (1 звонок = N попыток), события + SSE в UI.

## 2. Быстрый старт

```bash
# Linux/macOS
./run_ats2.sh                      # данные по умолчанию: ./data_v2
# или явно:
ATS_ADMIN_PASSWORD='MyPass123!' python3 -m app.run --host 0.0.0.0 --port 9124
```

Windows: `run_ats2.bat` (или `py -3 -m app.run`).

При первом запуске создаются `data_v2/ats.db` и учётные данные:
`data_v2/initial_credentials.txt` (admin с сгенерированным паролем + operator/operator1234).
Если задан `ATS_ADMIN_PASSWORD`, пароль админа — этот. **Смените пароль в интерфейсе (Настройки → Смена пароля).**

Проверка: откройте `http://127.0.0.1:9124`, войдите. Далее:
1. **Контакты** — добавьте или импортируйте CSV (`Имя;Телефон;Группа;Примечание;1`), убедитесь, что стоит «согласие».
2. **Кампании** — «Новая кампания»: выберите сценарий (`agent`/`message`/`operator`), шаблон, расписание; «Добавить контакты»; ▶ старт.
3. Смотрите **Обзор**, **Журнал**, **Операторы/ACD**.
4. **Лаборатория симуляции** (на вкладке «Обзор», только при провайдере sim) задаёт исход следующего звонка на конкретный номер: `answered_human / answered_machine / busy / no_answer / blocked` и ответ абонента боту (`1`/`2`).

### Тесты

```bash
python3 -m unittest discover -s tests -v     # Linux
run_tests.bat                                 # Windows
```

## 3. Структура кода

```
app/
  config.py     пути, умолчания, настройки (синглтон в settings)
  db.py         SQLite: схема, миграции, сиды, импорт legacy data/*.json, settings
  security.py   пароли (PBKDF2), токены, роли
  events.py     шина событий + SSE
  telephony.py  TelephonyProvider: SimProvider (работает) + каркасы UIS/AMI
  numbers.py    пул номеров: лимиты, остывание, карантин
  agent.py      ИИ-агент L1 (сценарии JSON) + L2-каркас (LLM OpenAI-совместимый)
  crm.py        CrmDriver: csv (работает) + bitrix24 (каркас)
  engine.py     движок: события провайдера, автодозвон, watchdog, ACD, агент
  api.py        REST API v2 (роуты + бизнес-обработчики)
  server.py     HTTP-сервер: статика app/ui, API, SSE
  run.py        CLI: python -m app.run
  ui/index.html одностраничный UI (vanilla JS)
tests/          unittest-набор (12 тестов: безопасность, БД, пул, E2E движка, HTTP API)
data_v2/        runtime (БД, логи, записи, CRM-выгрузка) — создаётся автоматически
data/, server.py, xp_bridge/, static/  — LEGACY (старый прототип, не используется v2)
```

## 4. REST API v2 (основное)

Авторизация: заголовок `X-Ats-Token` (или `?token=` для SSE). Получение: `POST /api/v2/auth/login {login,password}`.

| Метод и путь | Назначение |
|---|---|
| GET `/api/v2/dashboard` | Сводка: кампании, каналы, звонки за сегодня, пул, операторы, ACD |
| GET/POST `/api/v2/contacts`, `/contacts/delete`, `/contacts/import`, `GET /api/v2/export/contacts.csv` | База контактов |
| GET `/api/v2/numbers`; POST `/api/v2/numbers/save`, `/numbers/quarantine`, `/numbers/reset` | Пул номеров |
| GET `/api/v2/campaigns`; POST `/campaigns/save`; POST `/campaigns/{id}/start|pause|stop|add-contacts|clear`; GET `/campaigns/{id}` | Кампании (детали: контакты + звонки) |
| GET `/api/v2/calls` (фильтры status/campaign_id/limit) | Журнал звонков |
| GET `/api/v2/templates`; POST `/templates/save`, `/templates/delete` | Шаблоны + сценарии бота (JSON) |
| GET `/api/v2/acd`, `/operators`; POST `/acd/accept`, `/calls/complete`, `/operators/status` | ACD/операторы |
| GET/POST `/api/v2/blacklist`, `/blacklist/add`, `/blacklist/delete`, `/complaint` | «Не звонить», жалобы |
| GET/POST `/api/v2/settings`; GET/POST `/api/v2/settings/raw` (админ) | Настройки; конфигурация uis/ami/llm/crm/bitrix24 |
| POST `/api/v2/sim/script` | Задание исхода симуляции для номера |
| GET `/api/v2/events?token=...` | SSE: события call/item/acd/campaign/agent |

## 5. Статусы звонка/элемента

Элемент кампании: `queued → dialing → agent/wait_operator/talk →`
`done_ok (message) | done_agent (агент, не квалифицирован) | operator_ok (оператор)`
`| busy | no_answer | machine | failed | blocked | exhausted | no_operator | blocked_no_consent | blacklisted | canceled`

Звонок (calls): `dialing → ringing → answered → (talk|agent|wait_operator) → done`, плюс результат и детали; для агента — `agent_result` (JSON: qualified, answers, transcript, engine).

## 6. Подключение реальной телефонии (UIS / Asterisk) — по шагам T01/T13/T14

1. Запросите у UIS: SIP-линии/транк на существующие номера, либо Call API (ключ, URL, лимиты) — письмо-черновик в `ДОРОЖНАЯ_КАРТА_ATS.md` (Приложение A).
2. Заполните конфигурацию: UI → Настройки → «Провайдеры и интеграции (JSON)», например:
   ```json
   { "provider": "uis",
     "uis":  { "api_url": "https://...", "api_key": "..." },
     "ami":  { "host": "127.0.0.1", "port": 5038, "user": "ats", "secret": "..." },
     "llm":  { "enabled": true, "base_url": "https://...", "model": "...", "api_key_env": "ATS_LLM_KEY" },
     "crm":  { "driver": "bitrix24" },
     "bitrix24": { "webhook_url": "https://..." } }
   ```
   или напрямую: `POST /api/v2/settings/raw` с `{"uis": {...}}`.
3. Перезапустите: `python3 -m app.run` (провайдер выбирается на старте).
4. Реализация транспорта — в `app/telephony.py` (классы `UISCallApiProvider`, `AsteriskAmiProvider`, помечены TODO(T13)/(T14)). Интерфейс и цикл движка уже готовы: адаптер должен лишь вызывать `self.emit({...})` с событиями `ring / answered(human) / status / done` — ядро, агент и ACD не меняются.
5. Номера в пуле: для реального провайдера создайте номера с `provider = uis/ami` (номер = ваш Caller ID, E.164).

## 7. Что осталось «вне кода» (внешние зависимости из дорожной карты)

- T01 — ответ UIS (SIP/Call API) и заполнение адаптера `uis`;
- T14 — Asterisk + SIP-транк и реализация AMI-адаптера;
- T02 — юрист (регламент согласий 152-ФЗ);
- T03 — ответы клиента (CRM, сценарий бота, механика перевода);
- T36/T37 — договоры с операторами (МегаФон, МТС и др.);
- Пилотные кампании на реальных номерах — операционная задача после T13–T15.

Всё остальное (ядро, БД, API, UI, движок кампаний/автодозвона, пул номеров, агент L1, ACD, CRM-csv, тесты) реализовано и работает в репозитории.
