#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Загрузить записи разговоров, написанные диалпланом Asterisk, в ATS.

ATS отдаёт медиа через Asterisk, поэтому файлы записей лежат на стороне АТС
(``/var/spool/asterisk/monitor/ats/``). Скрипт раскладывает их по звонкам
(маршрут ``POST /api/v2/calls/recording``), после чего запись доступна в
карточке звонка и в amoCRM (подписанная ссылка, ``settings.records``).

Имена файлов задаёт диалплан (``deploy/asterisk/extensions-ats.conf``):
    call-<call_id>.wav        — исходящий из кампании (ATS_CALL_ID из Originate)
    play-<call_id>.wav        — то же, если озвучка шла в контексте ats-play
    in-<канал_без_слэша>.wav — входящий с транка: канал = external_call_id
                                (PJSIP_mcm-0000000a → PJSIP/mcm-0000000a)

Запуск по cron (от пользователя asterisk или root):
    */5 * * * * /usr/bin/python3 /opt/ats/deploy/asterisk/attach_recordings.py \
        >> /var/log/ats-records.log 2>&1

Учётные данные — из окружения или файла /etc/ats/ats.env:
    ATS_URL=http://127.0.0.1:9124
    ATS_LOGIN=ats_records            # пользователь с ролью admin
    ATS_PASSWORD=*****
    ATS_TOKEN=                       # либо готовый токен (тогда вход не нужен)
"""
import base64
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

WATCH_DIR = os.environ.get("ATS_WATCH_DIR", "/var/spool/asterisk/monitor/ats")
DONE_DIRNAME = "done"
AUDIO_EXT = (".wav", ".mp3", ".ogg", ".opus", ".m4a", ".aac", ".flac")
MIN_AGE_SEC = 20          # файл должен «дозреть»: MixMonitor пишет на лету
MAX_SIZE = 200 * 1024 * 1024
ENV_FILES = ("/etc/ats/ats.env", os.path.expanduser("~/.ats.env"))


def load_env(paths=ENV_FILES):
    """env-файл вида KEY=value (в комментариях и пустых строках нет смысла)."""
    for path in paths:
        try:
            with open(path, "r", encoding="utf-8") as fh:
                for raw in fh:
                    line = raw.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    key, _, val = line.partition("=")
                    key = key.strip()
                    val = val.strip().strip('"').strip("'")
                    if key and key not in os.environ:
                        os.environ[key] = val
        except OSError:
            continue


def parse_name(fname):
    """(call_id, external_call_id) по имени файла; (0, "") — неизвестный файл."""
    name = os.path.basename(str(fname or ""))
    stem = os.path.splitext(name)[0]
    m = re.match(r"^(?:call|play)-(\d+)$", stem)
    if m:
        return int(m.group(1)), ""
    m = re.match(r"^in-(.+)$", stem)
    if m:
        # SUBST(${CHANNEL},/,_,-) в диалплане: возвращаем первый слэш
        return 0, m.group(1).replace("_", "/", 1)
    return 0, ""


def candidate_files(watch_dir=WATCH_DIR, min_age_sec=MIN_AGE_SEC, now=None):
    """Файлы, готовые к отправке: правильный формат имени, не свежий, непустой."""
    out = []
    now = now if now is not None else time.time()
    try:
        entries = sorted(os.listdir(watch_dir))
    except OSError:
        return out
    for name in entries:
        path = os.path.join(watch_dir, name)
        if not name.lower().endswith(AUDIO_EXT) or not os.path.isfile(path):
            continue
        call_id, ext_id = parse_name(name)
        if not call_id and not ext_id:
            continue
        try:
            st = os.stat(path)
        except OSError:
            continue
        if st.st_size <= 44 or (now - st.st_mtime) < min_age_sec:
            continue
        if st.st_size > MAX_SIZE:
            continue
        out.append(path)
    return out


class Client:
    def __init__(self, base_url=None, login=None, password=None):
        self.base = (base_url or os.environ.get("ATS_URL") or "http://127.0.0.1:9124").rstrip("/")
        self.login = login or os.environ.get("ATS_LOGIN", "")
        self.password = password or os.environ.get("ATS_PASSWORD", "")
        # ATS принимает токен в заголовке X-Ats-Token
        self.token = os.environ.get("ATS_TOKEN", "")

    def _post(self, path, payload, token=None):
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(self.base + path, data=data,
                                     headers={"Content-Type": "application/json"})
        if token:
            req.add_header("X-Ats-Token", token)
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8", "ignore") or "{}")

    def login_once(self):
        if self.token:
            return self.token
        if not (self.login and self.password):
            raise RuntimeError("не заданы ATS_LOGIN/ATS_PASSWORD (или /etc/ats/ats.env)")
        res = self._post("/api/v2/auth/login", {"login": self.login, "password": self.password})
        self.token = str(res.get("token") or "")
        if not self.token:
            raise RuntimeError("ATS не выдала токен: {}".format(res))
        return self.token

    def attach(self, path):
        call_id, ext_id = parse_name(path)
        with open(path, "rb") as fh:
            raw = fh.read()
        payload = {"filename": os.path.basename(path),
                   "data_b64": base64.b64encode(raw).decode("ascii")}
        if call_id:
            payload["call_id"] = call_id
        if ext_id:
            payload["external_call_id"] = ext_id
        return self._post("/api/v2/calls/recording", payload, token=self.token)

def archive(path):
    """Унести обработанный файл в подкаталог done/ (чтобы не гонять по кругу)."""
    dst_dir = os.path.join(os.path.dirname(path), DONE_DIRNAME)
    try:
        os.makedirs(dst_dir, mode=0o770, exist_ok=True)
        dst = os.path.join(dst_dir, os.path.basename(path))
        if os.path.exists(dst):
            stem, ext = os.path.splitext(os.path.basename(path))
            dst = os.path.join(dst_dir, "{}.{}{}".format(stem, int(time.time()), ext))
        os.replace(path, dst)
        return dst
    except OSError as e:
        print("[records] не удалось переместить {}: {}".format(path, e))
        return ""


def run(dry_run=False, watch_dir=WATCH_DIR):
    load_env()
    client = Client()
    files = candidate_files(watch_dir)
    if dry_run:
        for path in files:
            call_id, ext_id = parse_name(path)
            print("[records] сушка: {} → {}".format(
                path, "call {}".format(call_id) if call_id else "канал {}".format(ext_id)))
        print("[records] файлов к привязке: {}".format(len(files)))
        return 0
    if not files:
        return 0
    try:
        client.login_once()
    except Exception as e:  # noqa: BLE001
        print("[records] вход в ATS не выполнен:", e)
        return 2
    ok = failed = 0
    for path in files:
        try:
            res = client.attach(path)
            if isinstance(res, dict) and res.get("ok"):
                ok += 1
                archive(path)
            else:
                failed += 1
                print("[records] отказ ATS по {}: {}".format(path, res))
        except urllib.error.HTTPError as e:
            failed += 1
            body = ""
            try:
                body = e.read().decode("utf-8", "ignore")[:200]
            except Exception:  # noqa: BLE001
                pass
            print("[records] HTTP {} по {}: {}".format(e.code, path, body))
            if e.code == 401:            # токен протух — один раз пере логинимся
                try:
                    client.login_once()
                except Exception:  # noqa: BLE001
                    break
        except Exception as e:  # noqa: BLE001
            failed += 1
            print("[records] ошибка по {}: {}".format(path, e))
    print("[records] привязано {}, ошибок {}".format(ok, failed))
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(run(dry_run=("--dry-run" in sys.argv),
                 watch_dir=os.environ.get("ATS_WATCH_DIR", WATCH_DIR)))
