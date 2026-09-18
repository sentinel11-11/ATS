import json
import os
import shutil
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime

FROZEN = bool(getattr(sys, "frozen", False))
BASE = os.path.dirname(os.path.abspath(sys.executable)) if FROZEN else os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(BASE, "shared")) if FROZEN else os.path.abspath(os.path.join(BASE, "..", "shared"))
ACTIVE = set()
ACTIVE_LOCK = threading.Lock()


def stamp():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def ensure():
    for n in ["commands", "status", "processed", "bad", "log"]:
        p = os.path.join(ROOT, n)
        if not os.path.exists(p):
            os.makedirs(p)


def write_json(path, value):
    with open(path, "wb") as f:
        f.write(json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8"))


def emit(command, status, text):
    value = {"id": uuid.uuid4().hex, "datetime": stamp(), "command_id": command.get("id", ""), "call_id": command.get("call_id", ""), "line_id": command.get("line_id", ""), "status": status, "status_text": text}
    write_json(os.path.join(ROOT, "status", "status_{}_{}.json".format(datetime.now().strftime("%Y%m%d_%H%M%S"), uuid.uuid4().hex[:8])), value)


def log(text):
    with open(os.path.join(ROOT, "log", "xp_bridge.log"), "ab") as f:
        f.write(("[{}] {}\r\n".format(stamp(), text)).encode("utf-8"))


def speak(text, audio_command):
    if audio_command:
        cmd = audio_command.replace("{text}", str(text).replace('"', '\\"'))
        return subprocess.Popen(cmd, shell=True)
    ps = "$v=New-Object -ComObject SAPI.SpVoice;$v.Speak('{}')".format(str(text).replace("'", "''"))
    return subprocess.Popen(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def call_test(command):
    emit(command, "ringing", "Тестовый вызов")
    time.sleep(1.5)
    emit(command, "connected", "Тестовое соединение установлено")
    time.sleep(1)
    emit(command, "done", "Тестовый вызов завершён")


def call_com(command):
    try:
        import serial
    except Exception:
        emit(command, "error", "Не установлен pyserial")
        return
    line = command.get("line", {})
    port = line.get("com_port", "COM1")
    baud = int(line.get("baudrate", 9600))
    phone = str(command.get("phone", ""))
    prefix = str(line.get("dial_prefix", "ATD"))
    suffix = str(line.get("dial_suffix", ";"))
    hangup = str(line.get("hangup_command", "ATH"))
    try:
        ser = serial.Serial(port, baud, timeout=1)
        time.sleep(.3)
        ser.reset_input_buffer()
        ser.write((prefix + phone + suffix + "\r").encode("ascii", "ignore"))
        emit(command, "ringing", "Набор через {}".format(port))
        deadline = time.time() + 35
        connected = False
        while time.time() < deadline:
            raw = ser.readline().decode("utf-8", "ignore").strip().upper()
            if not raw:
                time.sleep(.2)
                continue
            if "BUSY" in raw:
                emit(command, "busy", raw)
                ser.close()
                return
            if "NO CARRIER" in raw or "NO ANSWER" in raw:
                emit(command, "no_answer", raw)
                ser.close()
                return
            if "CONNECT" in raw or "VOICE CALL: BEGIN" in raw or "OK" in raw:
                connected = True
                break
        if not connected:
            emit(command, "no_answer", "Нет подтверждения соединения")
            try:
                ser.write((hangup + "\r").encode("ascii", "ignore"))
            except Exception:
                pass
            ser.close()
            return
        emit(command, "connected", "Соединение установлено")
        proc = speak(command.get("text", ""), line.get("audio_command", ""))
        try:
            proc.wait(timeout=120)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        time.sleep(1)
        ser.write((hangup + "\r").encode("ascii", "ignore"))
        ser.close()
        emit(command, "done", "Озвучка завершена")
    except Exception as e:
        emit(command, "error", "{}: {}".format(port, e))


def handle(path):
    try:
        with open(path, "rb") as f:
            command = json.loads(f.read().decode("utf-8"))
        line = command.get("line", {})
        if line.get("mode") == "com":
            call_com(command)
        else:
            call_test(command)
        shutil.move(path, os.path.join(ROOT, "processed", os.path.basename(path)))
    except Exception as e:
        log(str(e))
        try:
            shutil.move(path, os.path.join(ROOT, "bad", os.path.basename(path)))
        except Exception:
            pass
    finally:
        with ACTIVE_LOCK:
            ACTIVE.discard(os.path.basename(path))


def loop():
    ensure()
    print("ATS 6-Line Bridge started")
    print("Exchange:", ROOT)
    while True:
        names = sorted([x for x in os.listdir(os.path.join(ROOT, "commands")) if x.lower().endswith(".json")])
        for name in names:
            with ACTIVE_LOCK:
                if name in ACTIVE:
                    continue
                ACTIVE.add(name)
            threading.Thread(target=handle, args=(os.path.join(ROOT, "commands", name),), daemon=True).start()
        time.sleep(.2)


if __name__ == "__main__":
    loop()
