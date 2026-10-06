# Чистка конфиденциального из репозитория (выполнять ПОСЛЕ настройки Мультикома)

Документ — план и готовые команды. Самому ничего удалять до слов заказчика «настройка
закончена, чисти» (данные письма нужны, пока конфигурируем сервер).

Репозиторий `sentinel11-11/ATS` на момент составления **публичный**
(`gh api repos/sentinel11-11/ATS --jq .private` → `false`). Это значит: всё, что
закоммичено в любую ветку, читается кем угодно. Дефолтная ветка `main` чиста: в ней
нет ни `data_v2/`, ни `docs/MULTICOM_SIP_CONNECT.md` — всё перечисленное ниже лежит
только в ветках `arena/01a0cdee-ats` и `arena/01a072c1-ats`.

## 1. Инвентарь: что именно мешает

| Что | Где | Чем плохо |
|---|---|---|
| Логин SIP-регистрации `00083819` | `docs/MULTICOM_SIP_CONNECT.md` (6), `deploy/asterisk/sip-multicom.conf` (3), `deploy/asterisk/install-multicom-trunk.sh` (2), `tests/*` (7) | логин + IP регистратора = готовые данные для попытки регистрации/подмены |
| IP регистратора `95.128.224.47`, сеть `95.128.224.0/21` | те же файлы + `docs/MULTICOM_INTEGRATION.md` | невысокая чувствительность, но в связке с логином — да |
| 15 реальных номеров DID | `docs/MULTICOM_SIP_CONNECT.md` (7), `tests/test_multicom_sip_trunk.py` (36), `tests/test_multicom_trunk_install.py` (2), **`deploy/asterisk/install-multicom-trunk.sh` — значения по умолчанию `MCM_NUMBERS`**, `app/engine.py` (2) | публичный список боевых городских номеров компании + структура пула |
| dev-база с настройками | `data_v2/ats.db` (140 КБ, в треке) | схема «база в git» сама по себе неверна: туда же уезжают `settings` (ключи API), контакты, журнал звонков |
| пароль админа открытым текстом | `data_v2/initial_credentials.txt` (в треке, коммит `3716eb7`) | `Логин: admin Пароль: cs4kjXnPopzx` — читается из репозитория |
| Пароли SIP/AMI | **в репозитории их нет** (проверено `git grep`): в шаблонах плейсхолдеры `__MCM_PASS__`, `ЗАМЕНИТЕ_НА_СЛУЧАЙНЫЙ_ПАРОЛЬ` | — |

Паролей оператора и AMI в git не было ни разу: `install-multicom-trunk.sh` подставляет
их из окружения, `/etc/ats/ats.env` и `/etc/asterisk/*` в репозиторий не входят.

## 2. Сделать сегодня, не дожидаясь конца настройки

Пункты не мешают конфигурации, а снижают уже существующий риск.

```bash
# 1) сменить пароль админа АТС (UI → Настройки → Пользователи; или впервые входим и меняем)
curl -s -X POST http://127.0.0.1:9124/api/v2/users/save -H "X-Ats-Token: $ATS_TOKEN" \
  -H 'Content-Type: application/json' -d '{"login":"admin","password":"НОВЫЙ_ПАРОЛЬ"}'

# 2) убедиться, что файл с паролем не лежит в папке репозитория на сервере
sudo ls -l /opt/ats/app/data_v2/ 2>/dev/null || echo "data_v2 на сервере нет — ок"

# 3) если решите, что репозиторий не должен быть публичным (рекомендую):
gh repo edit sentinel11-11/ATS --visibility private
```

Смена пароля в UI тоже подходит — главное, что `cs4kjXnPopzx` из `data_v2/initial_credentials.txt`
уже виден в сети, и его нельзя «оставить до конца настройки».

## 3. Удалить из репозитория (после того как всё заведено и заработало)

```bash
cd /opt/ats/app            # или в рабочем клоне, откуда пуш в arena/01a0cdee-ats
git checkout arena/01a0cdee-ats && git pull --ff-only

# 3.1. перестать хранить базу и креды в git (файлы останутся на диске)
git rm --cached -q data_v2/ats.db data_v2/initial_credentials.txt
printf '\n# локальные данные и секреты — никогда в git\ndata_v2/\n*.env\n!deploy/ats.env.example\n' >> .gitignore
git add .gitignore

# 3.2. вынести номера из значений по умолчанию скрипта в файл вне репозитория
sudo install -m 600 /dev/null /etc/ats/mcm.env
sudo tee -a /etc/ats/mcm.env >/dev/null <<'EOF'
MCM_PASS='ПАРОЛЬ_ИЗ_ПИСЬМА'
MCM_NUMBERS='9690229926 9690229930 …'
EOF
sed -i 's/^MCM_NUMBERS="\${MCM_NUMBERS:-[^}]*}"/MCM_NUMBERS="${MCM_NUMBERS:-}"   # берём из /etc\/ats\/mcm.env или $PWD/' deploy/asterisk/install-multicom-trunk.sh

# 3.3. обезличить данные письма в документации и тестах
python3 - <<'PY'
import pathlib, re
files = [pathlib.Path(p) for p in (
    "docs/MULTICOM_SIP_CONNECT.md", "docs/MULTICOM_INTEGRATION.md",
    "deploy/asterisk/sip-multicom.conf", "deploy/asterisk/install-multicom-trunk.sh",
    "tests/test_multicom_sip_trunk.py", "tests/test_multicom_trunk_install.py")]
sub = {"00083819": "00000000", "95.128.224.47": "sip.example-operator.ru",
       "95.128.224.0/21": "192.0.2.0/21",
       "9690229926": "5550000001", "9863149340": "5550000011", "9362574897": "5550000021"}
for f in files:
    t = f.read_text(encoding="utf-8"); o = t
    for a, b in sub.items():
        t = t.replace(a, b)
    t = re.sub(r"96902299\d\d|98631493\d\d|936257\d{4}", "5550000000", t)
    if t != o:
        f.write_text(t, encoding="utf-8"); print("вычищено:", f)
PY
sed -n 's/.*\(8_?\|7\)\([0-9]\{10\}\).*/остаток: &/p' docs/MULTICOM_SIP_CONNECT.md | head
grep -rn "00083819\|96902299\|95\.128\.224" --include="*" . | grep -v '^\./\.git/' | head   # должно быть пусто

python3 -m unittest discover -s tests 2>&1 | tail -3    # 356 OK — после замены номеров в тестах
git add -A && git commit -F - <<'MSG'
убрать данные оператора из репозитория: база/креды из трека, номера и логин — плейсхолдеры

- data_v2/ats.db и data_v2/initial_credentials.txt больше не в git (+ .gitignore);
- 15 DID, логин 00083819 и IP регистратора заменены на нейтральные значения в docs и тестах,
  MCM_NUMBERS больше не задан по умолчанию в install-multicom-trunk.sh — берётся из /etc/ats/mcm.env.
MSG
git push origin arena/01a0cdee-ats
```

После пуша — прогнать `./deploy/update.sh arena/01a0cdee-ats --no-restart` на сервере и
проверить, что `--numbers` по-прежнему находит номера: с удалённым значением по умолчанию
он должен читать `MCM_NUMBERS` из `/etc/ats/mcm.env` (или `--numbers-only` со списком в
переменной окружения).

## 4. Про историю коммитов (важно)

`git rm` и новые коммиты **не стирают** старые: данные письма и `cs4kjXnPopzx` останутся
доступны по SHA в ветках `arena/*`, пока существуют сами ветки (а после удаления ветки —
до сборки мусора на стороне GitHub; кэши и форки не гарантированно).

Варианты, по возрастанию радикальности:

1. **Ничего не переписываем.** Accept: пароль адмена уже сменён, SIP-пароль и AMI-пароль в
   git никогда не попадали, в `main` конфиденциального нет. Остаточный риск — логин `00083819`
   и список номеров в истории `arena/*`.
2. **Приватный репозиторий** (`gh repo edit --visibility private`) + удалить ветки
   `arena/01a0cdee-ats`, `arena/01a072c1-ats` после слияния: старые коммиты перестают быть
   публично доступны. Заодно запросить GC в поддержку GitHub, если принципиально.
3. **Переписать историю** `git filter-repo --replace-text …` и `git push --force` по
   arena-веткам. Делать только если репозиторий останется публичным: это ломает все
   клоны (переклон обязательный), поэтому только вместе с финальным слиянием в `main`.

Решение по п.3/п.4 — за заказчиком; я ничего не переписываю без явной команды.

## 5. Чего при чистке делать нельзя

* не трогать `docs/MULTICOM_SIP_CONNECT.md` целиком: там инструкция, по которой живём; вычищаются
  только конкретные значения;
* не удалять `deploy/ats.env.example` — это шаблон без секретов (все значения закомментированы);
* не «чистить» историю `git filter-branch` на лету, пока сервер работает с этими ветками;
* не выполнять `git clean -fdx` в `/opt/ats/app` — вместе с локальным мусором удалит
  `venv/`, `data_v2/` и всё, что сервер нагенерировал сам.
