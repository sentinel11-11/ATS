# BASELINE — верифицированное исходное состояние (этап A)

Дата: 2026-10-21. Ветка: `arena/eda2b68e-ats` (от `arena/01a0cdee-ats`).
Проверенный HEAD: `7af8eee3a31dd125cd059e8176fb801a96215b30`.
Окружение аудита: Linux, Python 3.11.2, Node 22.22.3, npm 10.9.8.
Тип проверки: статический анализ + полный локальный прогон тестов и сборки.
Live-тесты телефонии и нагрузочные тесты **не проводились**.

## 1. Карта системы

| Слой | Файлы | Роль |
|---|---|---|
| HTTP | `app/server.py` (222 стр.) | ThreadingHTTPServer, статика `app/ui`, SSE, раздача записей |
| REST API | `app/api.py` (1963 стр.) | ~73 шаблона маршрутов `/api/v2/*`, вебхуки провайдеров, аудит |
| Движок | `app/engine.py` (1721 стр.) | планирование, allocate/watchdog, ACD, события провайдеров, CRM outbox |
| Хранилище | `app/db.py` (812 стр.) | SQLite (WAL, 1 соединение, общий RLock), схема/миграции, настройки |
| Безопасность | `app/security.py` (147 стр.) | PBKDF2-пароли, сессии (таблица `sessions` + in-memory кэш/фолбэк) |
| Телефония | `app/telephony.py`, `app/asterisk.py`, `app/providers/*` | sim/uis/ami/megafon_vats/multicom + amoCRM |
| События | `app/events.py` | in-process pub/sub → SSE (очередь 200/подписчика, число подписчиков не ограничено) |
| UI | `frontend/src` (React/Vite), собран в `app/ui` | токен в localStorage, SSE через `?token=` |
| Эксплуатация | `deploy/`, `docs/` | systemd, backup, update, nginx-инструкции |

Таблицы БД (21): users, templates, contacts, databases, numbers, vats_users,
vats_groups, campaigns, campaign_items, calls, provider_events, attempts,
operators, acd, agent_sessions, blacklist, events, audit_logs, settings,
amo_users, crm_outbox, sessions.

Фоновые потоки: 1 — цикл движка (`Engine._loop`, тик 1 с / 0.3 с в FAST).
CRM-outbox обрабатывается синхронно внутри `tick_once()`.

## 2. Прогон baseline

| Проверка | Команда | Результат |
|---|---|---|
| Полный набор тестов | `python3 -m unittest discover -s tests` | **396 тестов, OK, 1 skipped, ~57 с** |
| Frontend install | `cd frontend && npm ci` | OK |
| Frontend build | `cd frontend && npm run build` | OK (3.2 с); `sourceHash` совпал с исходниками, изменился только `builtAt` |
| Guard сборки UI | `python3 -m unittest tests.test_ui_build` | 5 тестов OK |
| Синтаксис shell | `bash -n` для всех `*.sh` в git | 7 файлов OK |
| Python import | все модули `app/*` импортируются | OK |

Счётчики тестов по файлам: см. `grep -c "def test_" tests/*.py` (итого 396).

**Расхождение документации подтверждено:** README.md заявляет 57 проверок,
README_V2.md — 309, фактический набор — 396. Заявления коммитов о «396 зелёных»
совпадают с фактическим прогоном.

## 3. Матрица интеграций (статус на baseline)

| Интеграция | Unit/integration-тесты | Staging/live | Статус |
|---|---|---|---|
| sim (стенд) | да (множество E2E через HTTP API) | — | подтверждён тестами |
| UIS | map/вебхук-тесты | нет | unit-level |
| AMI/Asterisk | test_ami_bridge (mock AMI) | нет | unit-level; конфигурация Asterisk требует живой проверки |
| МегаФон ВАТС | test_megafon_* (mock ВАТС-сервер) | инструкция LIVE_TEST_MEGAFON.md | integration-tested; live не подтверждён; media-сценарий message ограничен |
| Мультиком | test_multicom_* (mock REST+вебхук) | нет | integration-tested |
| amoCRM | test_amocrm (mock API) | нет | integration-tested |
| Bitrix24 | каркас (README_V2) | нет | незавершён |

## 4. Подтверждённые проблемы (со ссылкой на файлы)

- **P0.** `data_v2/ats.db` (143 КБ, 1 пользователь, настройки) и
  `data_v2/initial_credentials.txt` (логин+пароль админа) — **в git-индексе**.
  `docs/SECURITY_CLEANUP.md` дополнительно содержит дословные значения
  скомпрометированного пароля, SIP-логина, IP регистратора и номера DID.
- **P1.** `app/security.py`: при ошибках БД сессии молча переходят на
  in-memory кэш (fail-open); кэш принимается и когда БД доступна, но строки нет.
- **P1.** `frontend/src/useSSE.js`, `Contacts/Journal/Reports.jsx`: долгоживущий
  токен сессии в query string (`?token=`); сервер его принимает (`app/server.py::auth_ok`).
- **P1.** `app/config.py`: `HOST_DEFAULT="0.0.0.0"`; TLS терминируется не приложением.
- **P1.** `app/api.py::_LOGIN_ATTEMPTS` — лимит входа только по логину, in-process.
- **P1.** `app/server.py::MAX_BODY=64MiB` читается целиком в память; превышение
  молча даёт `{}` вместо 413; лимитов соединений/SSE нет.
- **P1.** `app/db.py` — одно соединение+RLock, `q()` коммитит на каждый вызов;
  составные операции не атомарны.
- **P1.** `app/engine.py::tick_once` синхронно ходит в CRM (`_crm_outbox_tick`).
- **P1.** Вебхуки телефонии подтверждаются 200 **до** персистентной записи
  (`ENGINE.push_event` → in-memory `queue.Queue`).
- **P1.** CI (`.github/workflows/`) отсутствует.
- **P1.** `frontend/src/pages/Settings.jsx:85` — успех сохранения при
  `rawRes.ok OR basicRes.ok` (ложный успех при частичном сбое).
- **P2.** Моно-модули api/engine; ручные миграции; токен в localStorage;
  локальные timestamp без UTC; нет нагрузочных отчётов.

## 5. Секреты в истории (сводка, без значений)

`git log` по `data_v2/` подтверждает коммиты runtime-БД и файла первичных
учётных данных. Значения из `docs/SECURITY_CLEANUP.md` (пароль админа,
SIP-логин, IP регистратора, 15 DID-номеров) считать скомпрометированными:
репозиторий публичный, удаление файла из HEAD не удаляет историю.
Требуется ротация по внеполосной процедуре (см. `docs/SECURITY_CLEANUP.md`,
раздел «Сделать сегодня») и согласованное с владельцем переписывание истории.

## 6. Что НЕ проверялось

- Реальные звонки через провайдеров (нет доступа к боевым учеткам/телефонии).
- Нагрузочные испытания (готовится отдельный стенд, см. `tools/loadtest/`).
- Поведение под PostgreSQL (сейчас только SQLite).
- Восстановление из бэкапа на боевом сервере (процедура описана в deploy/backup.sh,
  репетиция не проводилась).

Дальнейшие этапы: B (P0-гигиена), C (дефекты надёжности), D (CI/документация),
E (ADR по tenancy), F (нагрузочные сценарии). Отчёт — в `docs/engineering/`.
