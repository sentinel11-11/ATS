# -*- coding: utf-8 -*-
"""Точка входа ATS v2:
    python -m app.run [--host ..] [--port ..] [--provider sim|uis|ami] [--init-only]
"""
import argparse
import sys

from . import api, config, db
from .engine import Engine


def _backup_db(keep=12):
    """Бэкап SQLite-базы при старте (WAL-safe через sqlite3 backup API)."""
    import sqlite3
    import shutil
    from datetime import datetime
    try:
        bdir = config.DATA_DIR / "backups"
        bdir.mkdir(parents=True, exist_ok=True)
        dst = bdir / "ats-{}.db".format(datetime.now().strftime("%Y%m%d-%H%M%S"))
        conn = db.connect()
        dest = sqlite3.connect(str(dst))
        try:
            conn.backup(dest)
        finally:
            dest.close()
        backups = sorted(bdir.glob("ats-*.db"))
        for old_b in backups[:-keep]:
            try:
                old_b.unlink()
            except Exception:
                pass
        print("[ATS v2] Бэкап БД:", dst.name)
    except Exception as e:
        print("[ATS v2] Бэкап БД пропущен:", e)


def main(argv=None):
    ap = argparse.ArgumentParser(description="ATS v2")
    ap.add_argument("--host", default=None)
    ap.add_argument("--port", type=int, default=None)
    ap.add_argument("--provider", default=None, choices=["sim", "uis", "ami"])
    ap.add_argument("--init-only", action="store_true")
    args = ap.parse_args(argv)

    db.init_db()
    _backup_db()
    st = db.get_settings()
    if args.provider:
        st["provider"] = args.provider
        db.save_settings(st)
    host = args.host or st.get("host") or config.HOST_DEFAULT
    port = args.port or int(st.get("port", config.PORT_DEFAULT))

    if args.init_only:
        print("База инициализирована:", config.DB_PATH)
        return 0

    # Переключение провайдера конфигурацией: при недоступности движок сам откатится на sim
    engine = Engine()
    api.ENGINE = engine

    from .server import create_server
    try:
        httpd = create_server(host, port)
    except OSError as e:
        print("Не удалось открыть порт {}: {}".format(port, e))
        return 1

    print("=" * 60)
    print("ATS v2 Dispatcher")
    print("Local:  http://127.0.0.1:{}".format(port))
    print("LAN:    http://{}:{}".format(_lan_ip(), port))
    print("Provider:", engine.provider.name)
    print("UI:      /  (логин/пароль — data_v2/initial_credentials.txt при первом запуске)")
    print("=" * 60)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nОстановка...")
    finally:
        engine.stop()
        httpd.server_close()
    return 0


def _lan_ip():
    import socket
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


if __name__ == "__main__":
    sys.exit(main())
