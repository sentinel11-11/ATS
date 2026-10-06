#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AGI «ats_say.py» — озвучить сообщение, переданное движком ATS.

Текст звонка ATS кладёт в канальную переменную ``ATS_TEXT_B64`` (base64 — так
переводы строк и кавычки не ломают пакет AMI). Скрипт:

  1. отвечает на вызов, если канал ещё не поднят;
  2. раскодировывает текст и синтезирует WAV внешней TTS-командой
     (``/etc/asterisk/ats-tts.conf``, по умолчанию ``espeak-ng``);
  3. проигрывает файл с возможностью прервать набором DTMF;
  4. пишет итог в канальную переменную ``ATS_PLAY`` — ``ok`` или
     ``failed:<причина>``. Движок ATS читает её по AMI и ставит в журнале
     честный результат: без реально проигранного файла «доставлено» не появится.

Установка:
    cp ats_say.py /var/lib/asterisk/agi-bin/
    chmod 755 /var/lib/asterisk/agi-bin/ats_say.py
    chown asterisk:asterisk /var/lib/asterisk/agi-bin/ats_say.py
Проверка из консоли Asterisk:
    channel originate PJSIP/101@ats-test application AGI args:"ats_say.py"
"""
import base64
import os
import re
import shlex
import shutil
import subprocess
import sys
import time

TTS_CONF = "/etc/asterisk/ats-tts.conf"
DEFAULT_CMD = "/usr/bin/espeak-ng -v ru -s 150 -w {out} {text}"
MAX_CHARS = 1200
INTERRUPT_DIGITS = "0123456789*#"


class Agi:
    """Минимальный протокол AGI: параметры со stdin, команды в stdout."""

    def __init__(self, stdin=None, stdout=None):
        self._in = stdin or sys.stdin
        self._out = stdout or sys.stdout
        self.params = {}

    def read_params(self):
        while True:
            line = self._in.readline()
            if not line:
                break
            line = line.rstrip("\r\n")
            if line == "":
                break
            key, sep, val = line.partition(":")
            if sep:
                self.params[key.strip().lower()] = val.strip()
        return self.params

    def command(self, cmd):
        self._out.write(cmd + "\n\n")
        self._out.flush()
        return self._read_reply()

    def _read_reply(self):
        line = self._in.readline()
        if not line:
            return {"code": -1, "result": "", "item": ""}
        line = line.rstrip("\r\n")
        code, _, rest = line.partition(" ")
        result, _, item = rest.partition(" (")
        result = result.replace("result=", "").strip()
        if item.endswith(")"):
            item = item[:-1]
        try:
            code = int(code)
        except ValueError:
            code = -1
        return {"code": code, "result": result, "item": item}

    def set_var(self, name, value):
        self.command('SET VARIABLE {} "{}"'.format(name, str(value).replace('"', "'")))

    def verbose(self, text, level=1):
        self.command('VERBOSE "{}" {}'.format(str(text).replace('"', "'")[:200], level))


def load_tts_command():
    """cmd=… и tmp_dir=… из /etc/asterisk/ats-tts.conf (файл необязателен)."""
    cmd, tmp_dir = DEFAULT_CMD, "/var/spool/asterisk/ats"
    try:
        with open(TTS_CONF, "r", encoding="utf-8") as fh:
            for raw in fh:
                line = raw.strip()
                if not line or line.startswith((";", "#")) or "=" not in line:
                    continue
                key, _, val = line.partition("=")
                key, val = key.strip().lower(), val.strip()
                if key == "cmd" and val:
                    cmd = val
                elif key == "tmp_dir" and val:
                    tmp_dir = val
    except OSError:
        pass
    return cmd, tmp_dir


def clean_text(value):
    """Текст в одну строку: AMI/AGI не переносят переводы строк, кавычки режем."""
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    text = text.replace('"', "'")
    return text[:MAX_CHARS]


def encode_for_cmd(cmd_template, out_path, text):
    """Подстановка {out}/{text} с кавычками для shell-аргументов."""
    rendered = cmd_template.replace("{out}", shlex.quote(out_path)).replace(
        "{text}", shlex.quote(text))
    return ["sh", "-c", rendered]


def render_speech(text, cmd_template, tmp_dir):
    """Синтез в WAV. Возвращает (путь, ошибка)."""
    out_path = os.path.join(tmp_dir, "ats-{}-{}.wav".format(int(time.time()),
                                                             os.getpid()))
    try:
        os.makedirs(tmp_dir, mode=0o770, exist_ok=True)
    except OSError as e:
        return "", "каталог {} недоступен: {}".format(tmp_dir, e)
    binary = cmd_template.split()[0]
    if not os.path.exists(binary) and not shutil.which(binary):
        return "", "TTS-бинарник не найден: {}".format(binary)
    try:
        proc = subprocess.run(encode_for_cmd(cmd_template, out_path, text),
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=60)
    except subprocess.TimeoutExpired:
        return "", "TTS: таймаут 60 с"
    except Exception as e:  # noqa: BLE001 — любой сбой синтеза = честный failed
        return "", "TTS: {}".format(e)
    if proc.returncode != 0 or not os.path.exists(out_path) or os.path.getsize(out_path) < 44:
        return "", "TTS: код {}, вывод {}".format(proc.returncode,
                                                   (proc.stdout or b"")[:200].decode("utf-8", "ignore"))
    return out_path, ""


def main():
    agi = Agi()
    params = agi.read_params()

    def finish(status):
        agi.set_var("ATS_PLAY", status)
        agi.command("HANGUP")
        return 0

    raw = params.get("agi_ats_text_b64") or params.get("agi_variable_ats_text_b64") or ""
    if not raw:
        reply = agi.command("GET VARIABLE ATS_TEXT_B64")
        raw = reply.get("item") or ""
    try:
        text = clean_text(base64.b64decode(raw).decode("utf-8", "ignore")) if raw else ""
    except Exception:  # noqa: BLE001
        return finish("failed:текст повреждён (base64)")
    if not text:
        return finish("failed:пустой текст")

    if params.get("agi_answered", "").lower() != "true":
        agi.command("ANSWER")
        time.sleep(0.2)

    cmd_template, tmp_dir = load_tts_command()
    wav, err = render_speech(text, cmd_template, tmp_dir)
    if not wav:
        agi.verbose("ats_say: " + err)
        return finish("failed:" + err)
    reply = {"code": -1, "result": ""}
    try:
        reply = agi.command("CONTROL PLAYBACK {} {}".format(wav, INTERRUPT_DIGITS))
    finally:
        try:
            os.remove(wav)
        except OSError:
            pass
    if reply.get("code", -1) != 200:
        return finish("failed:канал закрылся во время озвучки")
    played = reply.get("result", "")
    if played and played not in ("0", ""):
        agi.verbose("ats_say: прервано DTMF={}".format(played))
    return finish("ok:interrupted" if played not in ("", "0") else "ok")


if __name__ == "__main__":
    sys.exit(main())
