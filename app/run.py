# -*- coding: utf-8 -*-
"""Точка входа ATS v2:
    python -m app.run [--host ..] [--port ..] [--provider sim|uis|ami]
                      [--admin-password ..] [--init-only] [--clean-demo]
                      [--list-users] [--set-password LOGIN PAROL]
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
    ap.add_argument("--admin-password", default=None,
                    help="Сменить пароль администратора (admin) и запустить сервер; с --init-only — только сменить")
    ap.add_argument("--clean-demo", action="store_true",
                    help="Удалить демо-данные (сид-контакты, «Демо-кампания», демо-оператора) и выйти")
    ap.add_argument("--list-users", action="store_true",
                    help="Показать пользователей (логин, роль, доступ) и выйти")
    ap.add_argument("--set-password", nargs=2, default=None, metavar=("LOGIN", "PAROL"),
                    help="Сменить пароль пользователя и выйти: --set-password operator1 NovyjParol123")
    args = ap.parse_args(argv)

    db.init_db()
    _backup_db()
    if args.clean_demo:
        r = db.clean_demo()
        parts = ["{}: {}".format(k, v) for k, v in r.items() if v]
        print("[ATS v2] Демо-данные удалены." if not parts else
              "[ATS v2] Демо-данные удалены: " + ", ".join(parts))
        return 0
    if args.admin_password:
        if db.set_admin_password(args.admin_password):
            print("[ATS v2] Пароль администратора (admin) обновлён.")
        else:
            print("[ATS v2] Пользователь admin не найден — пароль не изменён.")
    if args.list_users:
        rows = db.fetch("SELECT u.login, u.role, u.active, o.name AS op_name, o.ext "
                        "FROM users u LEFT JOIN operators o ON o.user_id=u.id ORDER BY u.id")
        if not rows:
            print("[ATS v2] Пользователей нет.")
        for r in rows:
            print("[ATS v2] {:<16} роль={:<8} доступ={} {}".format(
                r["login"], r["role"], "да" if r["active"] else "НЕТ",
                ("(" + (r["op_name"] or "") + (", внутр. " + r["ext"] if r["ext"] else "") + ")")
                if r["op_name"] else ""))
        return 0
    if args.set_password:
        login, pwd = args.set_password
        if len(pwd) < 6:
            print("[ATS v2] Пароль слишком короткий (минимум 6 символов) — не изменён.")
            return 1
        if db.set_user_password(login, pwd):
            print("[ATS v2] Пароль пользователя '{}' обновлён.".format(
                str(login).strip().lower()))
        else:
            print("[ATS v2] Пользователь '{}' не найден — пароль не изменён.".format(login))
            return 1
        return 0
    st = db.get_settings()
    if args.provider:
        st["provider"] = args.provider
        db.save_settings(st)
    host = args.host or st.get("host") or config.HOST_DEFAULT
    port = args.port or int(st.get("port", config.PORT_DEFAULT))

    if args.init_only:
        print("База инициализирована:", config.DB_PATH)
        return 0

    # Провайдер из конфигурации. При недоступности сервер НЕ стартует
    # (тихий откат на sim запрещён; для стенда: ATS_ALLOW_SIM_FALLBACK=1).
    try:
        engine = Engine()
    except Exception as e:
        print("[ATS v2] Не удалось запустить движок: {}".format(e))
        return 1
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
