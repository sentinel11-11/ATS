# -*- coding: utf-8 -*-
"""deploy/asterisk/install-multicom-trunk.sh — данные письма → конфиг транка, настройки АТС, пул номеров.

Скрипт рендерит /etc/asterisk/pjsip-multicom.conf из шаблона репозитория, а по флагам
--ats/--numbers пишет ещё и настройки провайдера и пул номеров через API АТС. Покрыто то,
на чём обычно и ломаются ручные прогоны: незаменённый плейсхолдер, отсутствие #include
(файл лежит, Asterisk его не видит), двойной include после второго прогона, синтаксис
конфига, испорченный значением из окружения, и provider у импортированных номеров.
"""
import json
import os
import pathlib
import shutil
import subprocess
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "deploy" / "asterisk" / "install-multicom-trunk.sh"
FAKE_ASTERISK = """#!/bin/sh
case "$*" in
  *"show registrations"*|*"show contacts"*) echo "  00083819@95.128.224.47  200 OK  Registered" ;;
  *) echo "ok" ;;
esac
exit 0
"""


def run(args=(), env=None):
    e = dict(os.environ)
    e.update(env or {})
    return subprocess.run(["bash", str(SCRIPT), *args], env=e, cwd=str(ROOT),
                          capture_output=True, text=True, timeout=90, input="")


class RenderTest(unittest.TestCase):
    def test_placeholders_filled_from_letter(self):
        p = run(["--print"])
        self.assertEqual(p.returncode, 0, p.stderr[:400])
        out = p.stdout
        # пароль в --print остаётся плейсхолдером (не светим секрет в терминал)
        left = [l for l in out.splitlines() if "__MCM_" in l
                and "password=" not in l and not l.lstrip().startswith(";")]
        self.assertEqual(left, [], "незаменённые плейсхолдеры: %s" % left)
        self.assertIn("auth_name=00083819", out)
        self.assertIn("server_uri=sip:00083819@95.128.224.47:5060", out)
        self.assertIn("client_uri=sip:00083819@95.128.224.47:5060", out)
        self.assertIn("from_domain=95.128.224.47", out)
        self.assertIn("match=95.128.224.47/32", out)
        self.assertIn("device_state_busy_at=15", out)

    def test_codecs_from_env(self):
        p = run(["--print"], env={"MCM_CODECS": "alaw,ulaw"})
        self.assertEqual(p.returncode, 0, p.stderr[:400])
        self.assertEqual([l for l in p.stdout.splitlines() if l.startswith("allow=")],
                         ["allow=alaw", "allow=ulaw"])
        self.assertNotIn("allow=g729", p.stdout)

    def test_trunk_name_renames_sections(self):
        p = run(["--print"], env={"MCM_TRUNK": "mcm2"})
        self.assertEqual(p.returncode, 0, p.stderr[:400])
        out = p.stdout
        self.assertIn("[mcm2]", out)
        self.assertIn("[mcm2-auth]", out)
        self.assertIn("aors=mcm2-aor", out)
        self.assertIn("outbound_auth=mcm2-auth", out)
        self.assertIn("endpoint=mcm2", out)
        self.assertNotIn("\n[mcm]\n", out)

    def test_dangerous_values_rejected(self):
        """';' и перенос строки в значении = новая строка/секция в конфиге Asterisk."""
        for var, val in (("MCM_LOGIN", "00083819; type=endpoint"),
                         ("MCM_HOST", "1.2.3.4\n[evil]"),
                         ("MCM_PORT", "5060;")):
            with self.subTest(var=var):
                p = run(["--print"], env={var: val})
                self.assertNotEqual(p.returncode, 0, "значение %r должно отклоняться" % val)
                self.assertIn(var, p.stderr)

    def test_password_allows_specials_but_not_newlines(self):
        self.assertEqual(run(["--print"], env={"MCM_PASS": "p@ss;#w0rd!"}).returncode, 0)
        self.assertNotEqual(run(["--print"], env={"MCM_PASS": "p@ss\nw0rd"}).returncode, 0)

    def test_unknown_flag(self):
        p = run(["--no-such-flag"])
        self.assertEqual(p.returncode, 2)
        self.assertIn("--help", p.stderr)


class _TmpAsteriskDirMixin:
    def make_ast_dir(self):
        d = pathlib.Path(tempfile.mkdtemp(prefix="ats_mcm_trunk_"))
        (d / "pjsip.conf").write_text("[general]\n", encoding="utf-8")
        (d / "manager.conf").write_text("[general]\nenabled = no\n", encoding="utf-8")
        fake = d / "asterisk"
        fake.write_text(FAKE_ASTERISK, encoding="utf-8")
        fake.chmod(0o755)
        self.addCleanup(shutil.rmtree, d, True)
        return {"ALLOW_NONROOT": "1", "AST_DIR": str(d), "ASTERISK_CMD": str(fake),
                "MCM_PASS": "p@ss-w0rd"}, d


class InstallTest(unittest.TestCase, _TmpAsteriskDirMixin):
    def setUp(self):
        self.env, self.dir = self.make_ast_dir()

    def test_writes_file_and_single_include(self):
        p = run(env=self.env)
        self.assertEqual(p.returncode, 0, (p.stdout + p.stderr)[-1500:])
        conf_path = self.dir / "pjsip-multicom.conf"
        conf = conf_path.read_text(encoding="utf-8")
        self.assertIn("password=p@ss-w0rd", conf)
        self.assertIn("auth_name=00083819", conf)
        self.assertNotIn("__MCM_", conf)
        self.assertGreater(len(conf), 1000, "файл подозрительно короткий — шаблон не отрендерился")
        self.assertEqual(oct(conf_path.stat().st_mode & 0o777), "0o640",
                         "секретный конфиг должен быть 0640")
        self.assertEqual((self.dir / "pjsip.conf").read_text().count("pjsip-multicom.conf"), 1)

    def test_rerun_is_idempotent(self):
        self.assertEqual(run(env=self.env).returncode, 0)
        self.assertEqual(run(env=self.env).returncode, 0)
        self.assertEqual((self.dir / "pjsip.conf").read_text().count("pjsip-multicom.conf"), 1,
                         "двойной #include плодит дубли секций при перечитывании")
        self.assertTrue(list(self.dir.glob("pjsip-multicom.conf.bak-*")),
                        "перед перезаписью обязана оставаться резервная копия")

    def test_ami_flag_enables_manager_user(self):
        p = run(env=dict(self.env, AMIPASS="amipass123"), args=["--ami"])
        self.assertEqual(p.returncode, 0, (p.stdout + p.stderr)[-800:])
        mgr = (self.dir / "manager-ats.conf").read_text(encoding="utf-8")
        self.assertIn("secret = amipass123", mgr)
        self.assertIn("#include manager-ats.conf", (self.dir / "manager.conf").read_text())
        self.assertIn("enabled = yes", (self.dir / "manager.conf").read_text())

    def test_ami_password_lands_in_service_env_file(self):
        """--ami кладёт ATS_AMI_SECRET в файл окружения сервиса, а не только в БД."""
        env_file = self.dir / "ats.env"
        p = run(env=dict(self.env, ATS_ENV_FILE=str(env_file), AMIPASS="abcdef123456"),
                args=["--ami"])
        self.assertEqual(p.returncode, 0, (p.stdout + p.stderr)[-800:])
        text = env_file.read_text(encoding="utf-8")
        self.assertEqual(text.count("ATS_AMI_SECRET="), 1, "повторный прогон не дублирует строку")
        self.assertIn("ATS_AMI_SECRET=abcdef123456", text)
        self.assertEqual(oct(env_file.stat().st_mode & 0o777), "0o640")

    def test_ami_password_with_specials_not_written_to_env(self):
        """systemd раскрывает $VAR в EnvironmentFile — такие пароли не пишем."""
        env_file = self.dir / "ats.env"
        p = run(env=dict(self.env, ATS_ENV_FILE=str(env_file), AMIPASS="pa$$word x"),
                args=["--ami"])
        self.assertEqual(p.returncode, 0, (p.stdout + p.stderr)[-600:])
        self.assertNotIn("ATS_AMI_SECRET", env_file.read_text(encoding="utf-8") if env_file.exists() else "")
        self.assertIn("не пишем", p.stdout + p.stderr)

    def test_check_mode_reports_before_install(self):
        p = run(["--check"], env=self.env)
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("не установлен", p.stdout + p.stderr)
        self.assertEqual(run(env=self.env).returncode, 0)
        p2 = run(["--check"], env={k: v for k, v in self.env.items() if k != "MCM_PASS"})
        self.assertEqual(p2.returncode, 0, (p2.stdout + p2.stderr)[-800:])

    @unittest.skipIf(os.geteuid() == 0, "тест на отсутствие root — под root не применим")
    def test_refuses_without_root(self):
        env = {k: v for k, v in self.env.items() if k != "ALLOW_NONROOT"}
        p = run(env=env)
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("root", (p.stderr + p.stdout).lower())


class _ApiHandler(BaseHTTPRequestHandler):
    captured = []

    def log_message(self, *a):
        pass

    def _json(self, obj):
        payload = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if n else b"{}"
        try:
            body = json.loads(raw.decode("utf-8"))
        except ValueError:
            body = {"_raw": raw.decode("utf-8", "ignore")}
        type(self).captured.append((self.path, dict(self.headers), body))
        self._json({"ok": True})


class ApiTest(unittest.TestCase, _TmpAsteriskDirMixin):
    @classmethod
    def setUpClass(cls):
        cls.srv = HTTPServer(("127.0.0.1", 0), _ApiHandler)
        cls.port = cls.srv.server_address[1]
        cls.thread = threading.Thread(target=cls.srv.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()

    def setUp(self):
        _ApiHandler.captured = []
        self.env, self.dir = self.make_ast_dir()
        self.env.update({"ATS_URL": "http://127.0.0.1:%d" % self.port, "ATS_TOKEN": "tok"})

    def test_numbers_only_tags_ami_and_e164(self):
        p = run(["--numbers-only"], env=dict(self.env, MCM_NUMBERS="9690229926 9690229930"))
        self.assertEqual(p.returncode, 0, (p.stdout + p.stderr)[-800:])
        posts = [(path, body) for path, h, body in _ApiHandler.captured]
        self.assertEqual([x[0] for x in posts], ["/api/v2/numbers/save"] * 2)
        for _, body in posts:
            self.assertEqual(body["number"], "+7" + body["label"].split()[-1])
            self.assertEqual(body["provider"], "ami", "с другим provider движок ami их не возьмёт")
            self.assertEqual(body["daily_limit"], 120)
        for _, headers, _b in _ApiHandler.captured:
            self.assertEqual(headers.get("X-Ats-Token"), "tok")

    def test_garbage_in_number_list_is_skipped(self):
        run(["--numbers-only"], env=dict(self.env, MCM_NUMBERS="abc 9690229926"))
        self.assertEqual(len(_ApiHandler.captured), 1, "мусор не должен уходить в API")

    def test_number_provider_validated(self):
        p = run(["--numbers-only"], env=dict(self.env, MCM_NUMBER_PROVIDER="zabbix"))
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("MCM_NUMBER_PROVIDER", p.stderr)

    def test_token_required(self):
        env = {k: v for k, v in self.env.items() if k != "ATS_TOKEN"}
        p = run(["--numbers-only"], env=env)
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("ATS_TOKEN", p.stderr)

    def test_ats_flag_writes_provider_and_ami_settings(self):
        p = run(["--ats"], env=dict(self.env, AMIPASS="amipass123"))
        self.assertEqual(p.returncode, 0, (p.stdout + p.stderr)[-1200:])
        settings = [b for path, _h, b in _ApiHandler.captured if path == "/api/v2/settings/raw"]
        self.assertEqual(len(settings), 1)
        cfg = settings[0]["provider_config"]
        self.assertEqual(cfg["provider"], "ami")
        self.assertEqual(cfg["ami"]["trunk"], "mcm")
        self.assertEqual(cfg["ami"]["secret"], "amipass123")
        self.assertEqual(cfg["ami"]["number_format"], "ru8")
        self.assertEqual(cfg["ami"]["caller_id_format"], "d10")
        self.assertEqual(cfg["ami"]["context"], "ats-out")
        self.assertEqual(cfg["ami"]["inbound_contexts"], "from-mcm,ats-in")
        # конфиг транка при этом тоже записан — один прогон на весь ввод данных письма
        self.assertTrue((self.dir / "pjsip-multicom.conf").is_file())

    def test_ats_requires_ami_password(self):
        env = {k: v for k, v in self.env.items() if k != "AMIPASS"}
        p = run(["--ats"], env=env)
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("AMIPASS", p.stderr)


if __name__ == "__main__":
    unittest.main()
