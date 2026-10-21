# -*- coding: utf-8 -*-
"""deploy/asterisk/install-multicom-trunk.sh — данные письма → конфиг транка, настройки АТС, пул номеров.

Скрипт рендерит /etc/asterisk/pjsip-multicom.conf из шаблона репозитория, а по флагам
--ats/--numbers пишет ещё и настройки провайдера и пул номеров через API АТС. Покрыто то,
на чём обычно и ломаются ручные прогоны: незаменённый плейсхолдер, отсутствие #include
(файл лежит, Asterisk его не видит), двойной include после второго прогона, синтаксис
конфига, испорченный значением из окружения, и provider у импортированных номеров.
"""
import json
import re
import socket
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
  *"show registrations"*|*"show contacts"*) echo "  00000000@sip.example-operator.ru  200 OK  Registered" ;;
  *"show endpoints"*) echo "  mcm                                    sip.example-operator.ru:5060" ;;
  *"dialplan show"*) echo "Context 'ats-out'" ;;
  *) echo "ok" ;;
esac
exit 0
"""


def run(args=(), env=None):
    e = dict(os.environ)
    e.update(env or {})
    return subprocess.run(["bash", str(SCRIPT), *args], env=e, cwd=str(ROOT),
                          capture_output=True, text=True, timeout=90, input="")


class ScriptStaticTest(unittest.TestCase):
    """Статические проверки самого скрипта — до запуска."""

    def test_bash_syntax(self):
        r = subprocess.run(["bash", "-n", str(SCRIPT)], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr[:400])

    def test_no_dangling_string_continuations(self):
        """Соседние строки-литералы bash склеивает в одну команду только через «\\»: без него
        следующая строка становится отдельной командой, и «command not found» всплывает в
        рантайме (в syntax-чеке при этом всё чисто). Реальный случай: подсказка про ATS_TOKEN
        после переноса без слэша сломала бы вывод ровно там, где он важнее всего."""
        lines = SCRIPT.read_text(encoding="utf-8").splitlines()
        bad = []
        for i, line in enumerate(lines[:-1]):
            if not re.match(r'^\s*(log|warn|die|info)\s+"', line):
                continue
            if line.rstrip().endswith("\\"):
                continue
            if re.match(r'^\s*"[^"]*"\s*$', lines[i + 1]):
                bad.append(i + 1)
        self.assertEqual(bad, [], "строки продолжения без слэша: %s" % bad)

    def test_no_hardcoded_asterisk_log_path_in_messages(self):
        """Пути к логу берутся из AST_LOG_DIR: на коробке файла с «угаданным» именем нет,
        и человек сутки ходил по несуществующему messages.log."""
        txt = SCRIPT.read_text(encoding="utf-8")
        offenders = [l.strip()[:90] for l in txt.splitlines()
                     if "/var/log/asterisk/" in l and not l.strip().startswith("#")
                     and 'AST_LOG_DIR:-/var/log/asterisk' not in l
                     and "Ubuntu" not in l]
        self.assertEqual(offenders, [], "хардкод пути лога в выводах: %s" % offenders)


class RenderTest(unittest.TestCase):
    def test_placeholders_filled_from_letter(self):
        p = run(["--print"])
        self.assertEqual(p.returncode, 0, p.stderr[:400])
        out = p.stdout
        # пароль в --print остаётся плейсхолдером (не светим секрет в терминал)
        left = [l for l in out.splitlines() if "__MCM_" in l
                and "password=" not in l and not l.lstrip().startswith(";")]
        self.assertEqual(left, [], "незаменённые плейсхолдеры: %s" % left)
        self.assertIn("username=00000000", out)
        self.assertIn("auth_type=userpass", out)
        self.assertNotIn("auth_name=", out, "auth_name — поле из Asterisk 12/13; на 16+ объект auth с ним не загружается")
        self.assertIn("server_uri=sip:00000000@sip.example-operator.ru:5060", out)
        self.assertIn("client_uri=sip:00000000@sip.example-operator.ru:5060", out)
        self.assertIn("from_domain=sip.example-operator.ru", out)
        self.assertIn("match=sip.example-operator.ru/32", out)
        self.assertIn("device_state_busy_at=15", out)

    def test_codecs_from_env(self):
        p = run(["--print"], env={"MCM_CODECS": "alaw,ulaw"})
        self.assertEqual(p.returncode, 0, p.stderr[:400])
        self.assertEqual([l for l in p.stdout.splitlines() if l.startswith("allow=")],
                         ["allow=alaw", "allow=ulaw"])
        self.assertNotIn("allow=g729", p.stdout)

    def test_template_uses_only_real_asterisk20_options(self):
        """Имена полей в шаблоне — только те, что Asterisk 20 действительно регистрирует.

        Списки сверены с исходными файлами: res/res_pjsip/pjsip_configuration.c (endpoint),
        res/res_pjsip/location.c (aor), res/res_pjsip/config_auth.c (auth),
        res/res_pjsip/config_transport.c (transport),
        res/res_pjsip_outbound_registration.c (registration). Это не придирка ради придирки:
        на живой коробке 'line_request' и 'auth_name' молча роняли создание объекта целиком,
        и в CLI это выглядело как «No objects found» при полностью исправном файле.
        allow/disallow — не ACO-опции (ими заведует codec handler), их проверяем наугад.
        """
        allowed = {
            "transport": {"bind", "protocol", "local_net", "external_signaling_address",
                          "gateway", "specify_server_uri", "allow_transfer", "tos_video",
                          "cos_video", "tos_audio", "cos_audio", "sdp_owner", "cert_file",
                          "privkey_file"},
            "auth": {"username", "password", "auth_type", "realm", "nonce_lifetime", "md5_cred",
                     "password_digest", "refresh_token", "oauth_clientid", "oauth_secret",
                     "nonce", "qop", "algorithm", "cnonce", "supported_algorithms_uac",
                     "supported_algorithms_uas"},
            "registration": {"server_uri", "client_uri", "uri", "contact_user", "transport",
                             "outbound_auth", "outbound_proxy", "retry_interval", "expiration",
                             "forbidden_retry_interval", "fatal_retry_interval", "max_retries",
                             "auth_rejection_permanent", "support_path", "support_outbound",
                             "line", "endpoint", "user_agent", "disable_register",
                             "max_random_initial_delay", "contact_header_params",
                             "security_negotiation", "security_mechanisms"},
            "aor": {"max_contacts", "remove_existing", "remove_unavailable", "default_expiration",
                    "minimum_expiration", "maximum_expiration", "qualify_frequency",
                    "qualify_timeout", "qualify_type", "authenticate_qualify", "mailboxes",
                    "voicemail_extension", "contact", "max_contacts_uris"},
            "endpoint": {"context", "direct_media", "direct_media_method", "force_rport",
                         "rewrite_contact", "rtp_symmetric", "dtmf_mode", "from_user",
                         "from_domain", "outbound_auth", "aors", "identify", "transport",
                         "device_state_busy_at", "device_state", "language", "tos_audio",
                         "cos_audio", "tos_video", "cos_video", "moh_suggest", "media_encryption",
                         "media_encryption_optimistic", "allow_subscribe", "subscribe_context",
                         "call_transfer", "call_pickup", "group_call_pickup", "immediate",
                         "rtp_start", "rtp_ipv6", "ice_support", "use_ptime", "avp",
                         "send_rpid", "send_pai", "rtp_timeout", "rtp_keepalive", "rtp_rekey",
                         "disallow_nonsdp", "bundle", "connected_line_method", "enable_100rel",
                         "disable_trickle_ice", "timer", "allow_overlap", "trust_id_inbound",
                         "trust_id_outbound", "suppress_q850_reason_headers", "use_epp",
                         "one_way_audio", "record_on_feature", "record_off_feature",
                         "forbid_contact_userpass", "allow_contact_userpass", "acl", "contact_acl",
                         "realm", "useragent_as", "follow_early_bridge", "unidentified_only",
                         "stir_shaken", "stir_shaken_profile", "preferred_only", "md5_cred",
                         "auth", "mailboxes", "vm_mailbox", "aggregate_mwi", "callerid"},
            "identify": {"endpoint", "match", "match_http_method", "match_request_uri",
                         "prepend_inbound", "prune_on_boot", "notify_method", "notify_cid",
                         "notify_contact_user"},
        }
        tpl = (ROOT / "deploy" / "asterisk" / "pjsip-multicom.conf").read_text(encoding="utf-8")
        sections, cur = {}, None
        for raw in tpl.splitlines():
            line = raw.split(";", 1)[0].strip()      # инлайн-комментарий Asterisk отрезает сам
            if not line:
                continue
            if line.startswith("[") and line.endswith("]"):
                cur = line[1:-1]
                sections[cur] = {}
            elif cur and "=" in line:
                key, val = line.split("=", 1)
                sections[cur][key.strip()] = val.strip()
        self.assertTrue(sections, "в шаблоне ни одной секции — парсер теста сломался")
        unknown = []
        for name, keys in sections.items():
            obj = keys.get("type")
            self.assertIn(obj, allowed, "секция [%s] с неизвестным type=%s" % (name, obj))
            for key in keys:
                if key in ("type", "allow", "disallow"):
                    continue
                if key not in allowed[obj]:
                    unknown.append("[%s] %s" % (name, key))
        self.assertEqual(unknown, [], "поля, которых нет в Asterisk 20: %s" % unknown)
        self.assertNotIn("auth_name=", tpl)

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
        for var, val in (("MCM_LOGIN", "00000000; type=endpoint"),
                         ("MCM_HOST", "1.2.3.4\n[evil]"),
                         ("MCM_PORT", "5060;")):
            with self.subTest(var=var):
                p = run(["--print"], env={var: val})
                self.assertNotEqual(p.returncode, 0, "значение %r должно отклоняться" % val)
                self.assertIn(var, p.stderr)

    def test_password_allows_specials_but_not_newlines(self):
        self.assertEqual(run(["--print"], env={"MCM_PASS": "p@ss;#w0rd!"}).returncode, 0)
        self.assertNotEqual(run(["--print"], env={"MCM_PASS": "p@ss\nw0rd"}).returncode, 0)

    def test_print_hides_password_and_reveal_shows_it(self):
        """--print копируют в чат/тикет — пароля в выводе быть не должно."""
        hidden = run(["--print"], env={"MCM_PASS": "Sup3rSIPpass"})
        self.assertEqual(hidden.returncode, 0)
        self.assertNotIn("Sup3rSIPpass", hidden.stdout)
        self.assertIn("password=***", hidden.stdout)
        shown = run(["--print", "--reveal"], env={"MCM_PASS": "Sup3rSIPpass"})
        self.assertIn("password=Sup3rSIPpass", shown.stdout)

    def test_unknown_flag(self):
        p = run(["--no-such-flag"])
        self.assertEqual(p.returncode, 2)
        self.assertIn("--help", p.stderr)


class _TmpAsteriskDirMixin:
    def make_ast_dir(self):
        d = pathlib.Path(tempfile.mkdtemp(prefix="ats_mcm_trunk_"))
        (d / "pjsip.conf").write_text("[general]\n", encoding="utf-8")
        (d / "manager.conf").write_text("[general]\nenabled = no\n", encoding="utf-8")
        (d / "extensions.conf").write_text("[general]\n", encoding="utf-8")
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
        self.assertIn("username=00000000", conf)
        self.assertNotIn("auth_name=", conf)
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

    def test_check_flags_legacy_auth_name_field(self):
        """Живой случай: transport-udp есть, endpoints пустые. Причина — auth_name вместо
        username: Asterisk 20 такое поле объекта auth не знает, auth не создаётся, и за ним
        не создаются endpoint и registration, которые на него ссылаются."""
        self.assertEqual(run(env=self.env).returncode, 0)
        conf = self.dir / "pjsip-multicom.conf"
        conf.write_text(conf.read_text(encoding="utf-8").replace("username=00000000", "auth_name=00000000"),
                        encoding="utf-8")
        p = run(["--check"], env=self.env)
        self.assertEqual(p.returncode, 1)
        self.assertIn("auth_name", p.stderr)
        self.assertIn("endpoint", p.stderr)
        # исправленный файл проходит поле username без предупреждений
        self.assertEqual(run(env=self.env).returncode, 0)
        ok = run(["--check"], env=self.env)
        self.assertNotIn("поле username не заполнено", ok.stderr)
        self.assertNotIn("auth_name", ok.stderr)

    def test_install_refuses_config_asterisk_cannot_load(self):
        """Валидатор обязан останавливать установку ДО записи: иначе «No objects found»
        ищут сутки, а причина — одно слово в шаблоне (проверено на живой коробке)."""
        tpl = self.dir / "bad.tpl"
        base = (ROOT / "deploy" / "asterisk" / "pjsip-multicom.conf").read_text(encoding="utf-8")
        tpl.write_text(base + "line_request=registration\n", encoding="utf-8")
        p = run(env=dict(self.env, TPL=str(tpl)))
        self.assertNotEqual(p.returncode, 0)
        out = p.stdout + p.stderr
        self.assertIn("Asterisk 20 такое не примет", out)
        self.assertIn("registration", out)
        self.assertFalse((self.dir / "pjsip-multicom.conf").exists(),
                         "писать заведомо нерабочий конфиг нельзя")
        tpl.write_text(base + "dtmf_mode=rfc2833\n", encoding="utf-8")
        p2 = run(env=dict(self.env, TPL=str(tpl), REG_TIMEOUT="0"))
        self.assertNotEqual(p2.returncode, 0)
        self.assertIn("rfc4733", p2.stdout + p2.stderr, "должно подсказать правильное значение")

    def test_include_first_line_in_distro_sample(self):
        """Дистрибутивный pjsip.conf — километровый закомментированный образец. #include,
        дописанный в его хвост, попадает в чужую секцию и молча игнорируется (живой случай:
        файл есть, права есть, «No objects found»). Значит include ставим первой строкой."""
        sample = "\n".join(["; sample %d" % i for i in range(1, 400)]
                           + ["[global]", "; enabled=yes", "#include pjsip-multicom.conf"]) + "\n"
        (self.dir / "pjsip.conf").write_text(sample, encoding="utf-8")
        self.assertEqual(run(env=self.env).returncode, 0)
        text = (self.dir / "pjsip.conf").read_text(encoding="utf-8")
        self.assertEqual(text.splitlines()[0], "#include pjsip-multicom.conf")
        self.assertEqual(text.count("pjsip-multicom.conf"), 1,
                         "хвостовую строку надо убрать, а не оставить вторую рядом с первой")
        self.assertIn("[global]", text, "содержимое образца терять нельзя")
        self.assertEqual(run(env=self.env).returncode, 0)
        self.assertEqual((self.dir / "pjsip.conf").read_text(encoding="utf-8").count("pjsip-multicom.conf"), 1)

    def test_pjsip_standalone_leaves_only_include(self):
        """--pjsip-standalone: в pjsip.conf остаётся только наш include, оригинал — рядом."""
        (self.dir / "pjsip.conf").write_text("[global]\n; long\n; distro\n; sample\n", encoding="utf-8")
        self.assertEqual(run(["--pjsip-standalone"], env=self.env).returncode, 0)
        lines = [l for l in (self.dir / "pjsip.conf").read_text(encoding="utf-8").splitlines() if l.strip()]
        self.assertEqual(lines, ["#include pjsip-multicom.conf"])
        self.assertTrue(list(self.dir.glob("pjsip.conf.dist-*")), "оригинал обязан быть сохранён")
        size = (self.dir / "pjsip.conf").stat().st_size
        self.assertEqual(run(["--pjsip-standalone"], env=self.env).returncode, 0)
        self.assertEqual((self.dir / "pjsip.conf").stat().st_size, size, "повторный прогон не перезаписывает")
        self.assertEqual(len(list(self.dir.glob("pjsip.conf.dist-*"))), 1, "бэкап плодить нельзя")

    def test_ami_password_hidden_unless_reveal(self):
        """Пароль AMI в выводе — утечка в терминал/скриншот; по умолчанию маскируется."""
        env = dict(self.env, AMIPASS="sup3rsecret-ami", ATS_ENV_FILE=str(self.dir / "ats.env"))
        p = run(["--ami"], env=env)
        self.assertEqual(p.returncode, 0, (p.stdout + p.stderr)[-800:])
        self.assertNotIn("sup3rsecret-ami", p.stdout + p.stderr)
        self.assertIn("--reveal", p.stdout + p.stderr, "должно быть сказано, как посмотреть пароль")
        self.assertIn("sup3rsecret-ami", (self.dir / "ats.env").read_text(encoding="utf-8"),
                      "маскировка вывода не означает, что секрет не записан")
        r = run(["--ami", "--reveal"], env=env)
        self.assertIn("sup3rsecret-ami", r.stdout + r.stderr)

    def test_dialplan_flag_installs_dialplan_agi_include(self):
        """--dialplan: контексты ats-out/from-mcm должны попасть в Asterisk, иначе звонков нет."""
        env = dict(self.env, AGI_DIR=str(self.dir / "agi-bin"))
        p = run(env=env, args=["--dialplan"])
        self.assertEqual(p.returncode, 0, (p.stdout + p.stderr)[-1200:])
        ext = (self.dir / "extensions-ats.conf").read_text(encoding="utf-8")
        self.assertIn("[ats-out]", ext)
        self.assertIn("[from-mcm]", ext)
        self.assertIn("MixMonitor", ext)
        self.assertIn("ats_say.py", ext, "озвучка должна вызываться из диалплана")
        self.assertIn("espeak-ng", (self.dir / "ats-tts.conf").read_text(encoding="utf-8"))
        self.assertEqual((self.dir / "extensions.conf").read_text().count("extensions-ats.conf"), 1)
        self.assertTrue((self.dir / "agi-bin" / "ats_say.py").exists())
        self.assertTrue(oct((self.dir / "agi-bin" / "ats_say.py").stat().st_mode & 0o777).endswith("50"))
        # повторный прогон: идемпотентность include и бэкап предыдущей версии
        self.assertEqual(run(env=env, args=["--dialplan"]).returncode, 0)
        self.assertEqual((self.dir / "extensions.conf").read_text().count("extensions-ats.conf"), 1)
        self.assertTrue(list(self.dir.glob("extensions-ats.conf.bak-*")))

    def test_dialplan_substitutes_inbound_target(self):
        """Куда уходят входящие — параметр, а не вечный «101&102»: на коробке софтфонов нет,
        и входящий с транка упирался бы в 480/503 прямо после регистрации."""
        env = dict(self.env, AGI_DIR=str(self.dir / "agi-bin"),
                   MCM_INBOUND_DIAL="PJSIP/89161234567@mcm")
        p = run(env=env, args=["--dialplan"])
        self.assertEqual(p.returncode, 0, (p.stdout + p.stderr)[-600:])
        ext = (self.dir / "extensions-ats.conf").read_text(encoding="utf-8")
        self.assertIn("Dial(PJSIP/89161234567@mcm,30)", ext)
        self.assertNotIn("__MCM_INBOUND_DIAL__", ext, "незаменённая метка = невалидный диалплан")
        self.assertIn("входящие → PJSIP/89161234567@mcm", p.stdout)

    def test_default_inbound_dial_keeps_the_ampersand(self):
        """В значении есть «&» — спецсимвол правой части sed. Без экранирования вместо
        «101&PJSIP/102» в файл попадёт скопированный кусок строки."""
        env = dict(self.env, AGI_DIR=str(self.dir / "agi-bin"))
        self.assertEqual(run(env=env, args=["--dialplan"]).returncode, 0)
        ext = (self.dir / "extensions-ats.conf").read_text(encoding="utf-8")
        self.assertIn("Dial(PJSIP/101&PJSIP/102,30)", ext)

    def test_warns_only_when_inbound_target_is_absent(self):
        env = dict(self.env, AGI_DIR=str(self.dir / "agi-bin"))
        p = run(env=env, args=["--dialplan"])
        self.assertIn("таких endpoint'ов в Asterisk нет", p.stdout + p.stderr,
                      "101/102 никуда не денутся, если их не заводить")
        q = run(env=dict(env, MCM_INBOUND_DIAL="PJSIP/89161234567@mcm"), args=["--dialplan"])
        self.assertNotIn("таких endpoint'ов", q.stdout + q.stderr,
                         "явный целевой канал — предупреждать не о чем")

    def test_warns_when_inbound_target_is_a_pool_number(self):
        """Входящие на номер из того же пула — это петля, и скрипт обязан сказать об этом."""
        # Номера — вымышленные (555…): реальные DID компании (префиксы 96902299/
        # 98631493/936257) в отслеживаемых файлах запрещены сканером
        # tools/secret_scan.py (test_no_runtime_secrets) — это утёкшие PII.
        env = dict(self.env, AGI_DIR=str(self.dir / "agi-bin"),
                   MCM_NUMBERS="5550000101 5550000102",
                   MCM_INBOUND_DIAL="PJSIP/85550000101@mcm")
        p = run(env=env, args=["--dialplan"])
        out = p.stdout + p.stderr
        self.assertIn("из пула Мультиком", out,
                      "номер из пула в качестве цели входящие — это сам себе звонок")
        self.assertIn("сам себе", out)
        q = run(env=dict(env, MCM_INBOUND_DIAL="PJSIP/89161234568@mcm"), args=["--dialplan"])
        self.assertNotIn("сам себе", q.stdout + q.stderr,
                         "чужой мобильный — предупреждать не о чем")

    def test_dialplan_without_asterisk_still_lays_files(self):
        """Asterisk не установлен — файлы всё равно раскладываем, но не падаем."""
        env = dict(self.env, AGI_DIR=str(self.dir / "agi-bin"), ASTERISK_CMD=str(self.dir / "nope"))
        p = run(env=env, args=["--dialplan"])
        self.assertEqual(p.returncode, 0, (p.stdout + p.stderr)[-800:])
        self.assertTrue((self.dir / "extensions-ats.conf").exists())

    def test_help_lists_all_flags(self):
        p = run(["--help"])
        self.assertEqual(p.returncode, 0)
        for flag in ("--print", "--dry-run", "--check", "--firewall", "--ami", "--ats",
                     "--restart", "--numbers", "--numbers-only", "--dialplan"):
            self.assertIn(flag, p.stdout, "в справке нет флага %s" % flag)

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

    response = {"ok": True}

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if n else b"{}"
        try:
            body = json.loads(raw.decode("utf-8"))
        except ValueError:
            body = {"_raw": raw.decode("utf-8", "ignore")}
        type(self).captured.append((self.path, dict(self.headers), body))
        self._json(type(self).response)


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
        p = run(["--numbers-only"], env=dict(self.env, MCM_NUMBERS="5550000001 5550000000"))
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
        run(["--numbers-only"], env=dict(self.env, MCM_NUMBERS="abc 5550000001"))
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

    def test_ats_resolves_ami_secret_from_env_file(self):
        """Прерванный прогон (--ami был, --ats нет) обязан работать без AMIPASS: пароль
        уже лежит в ats.env. Иначе 'нужен AMIPASS' обрывает настройку на середине."""
        env_file = self.dir / "ats.env"
        env_file.write_text("ATS_AMI_SECRET=amicc-from-env\n", encoding="utf-8")
        _ApiHandler.captured = []
        p = run(["--ats"], env=dict(self.env, ATS_ENV_FILE=str(env_file), ATS_KEEP_PROVIDER="1",
                                    AMIPASS=""))
        self.assertEqual(p.returncode, 0, (p.stdout + p.stderr)[-900:])
        self.assertIn("пароль взят из", p.stdout + p.stderr)
        settings = [b for path, _h, b in _ApiHandler.captured if path == "/api/v2/settings/raw"]
        self.assertEqual(len(settings), 1)
        self.assertEqual(settings[0]["provider_config"]["ami"]["secret"], "amicc-from-env")

    def test_ats_without_any_secret_explains_both_sources(self):
        p = run(["--ats"], env=dict(self.env, AMIPASS="", ATS_ENV_FILE=str(self.dir / "нет-такого-файла")))
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("нужен пароль AMI", p.stderr)
        self.assertIn("--ami", p.stderr)

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

    def test_keep_provider_writes_ami_without_switching_provider(self):
        """ATS_KEEP_PROVIDER=1: настраиваем ami, не роняя боевой обзвон через другого провайдера."""
        p = run(["--ats"], env=dict(self.env, AMIPASS="amipass123", ATS_KEEP_PROVIDER="1"))
        self.assertEqual(p.returncode, 0, (p.stdout + p.stderr)[-1200:])
        settings = [b for path, _h, b in _ApiHandler.captured if path == "/api/v2/settings/raw"]
        self.assertEqual(len(settings), 1)
        cfg = settings[0]["provider_config"]
        self.assertNotIn("provider", cfg, "провайдер не должен меняться в keep-режиме")
        self.assertEqual(cfg["ami"]["trunk"], "mcm")
        self.assertEqual(cfg["ami"]["context"], "ats-out")

    def test_unregistered_is_not_reported_as_registered(self):
        """Регресс на живую коробку: статус «Unregistered» содержит слово «registered», и
        наивный grep рапортовал «регистрация установлена», когда транк не зарегистрирован."""
        fake = self.dir / "asterisk-unreg"
        fake.write_text(
            '#!/bin/sh\n'
            'case "$*" in\n'
            '  *"show registrations"*) echo " mcm-reg/sip:00000000@sip.example-operator.ru:5060                 mcm-auth                    Unregistered      (exp. 30s ago)" ;;\n'
            '  *) echo "ok" ;;\n'
            'esac\nexit 0\n', encoding="utf-8")
        fake.chmod(0o755)
        env = dict(self.env, ASTERISK_CMD=str(fake), REG_TIMEOUT="1")
        p = run(env=env)
        self.assertEqual(p.returncode, 1, "Unregistered — это не успех")
        self.assertNotIn("регистрация подтверждена", p.stdout)
        self.assertIn("Unregistered", p.stderr)
        self.assertIn("tcpdump", p.stderr, "должно подсказать, как проверить, уходит ли REGISTER")
        c = run(["--check"], env=env)
        self.assertEqual(c.returncode, 1)
        self.assertIn("статус Unregistered", c.stderr)

    def test_registered_status_is_accepted(self):
        fake = self.dir / "asterisk-reg"
        fake.write_text(
            '#!/bin/sh\n'
            'case "$*" in\n'
            '  *"show registrations"*) echo " mcm-reg/sip:00000000@sip.example-operator.ru:5060                 mcm-auth                    Registered      (exp. 3550s)" ;;\n'
            '  *) echo "ok" ;;\n'
            'esac\nexit 0\n', encoding="utf-8")
        fake.chmod(0o755)
        p = run(env=dict(self.env, ASTERISK_CMD=str(fake), REG_TIMEOUT="1"))
        self.assertEqual(p.returncode, 0, (p.stdout + p.stderr)[-800:])
        self.assertIn("регистрация подтверждена оператором (Registered)", p.stdout)
        self.assertIn("регистрация: Registered", run(["--check"], env=dict(self.env, ASTERISK_CMD=str(fake))).stdout)

    def test_rejected_status_names_the_reason(self):
        fake = self.dir / "asterisk-rej"
        fake.write_text(
            '#!/bin/sh\n'
            'case "$*" in\n'
            '  *"show registrations"*) echo " mcm-reg/sip:00000000@sip.example-operator.ru:5060                 mcm-auth                    Rejected        (exp. 60s)" ;;\n'
            '  *) echo "ok" ;;\n'
            'esac\nexit 0\n', encoding="utf-8")
        fake.chmod(0o755)
        p = run(env=dict(self.env, ASTERISK_CMD=str(fake), REG_TIMEOUT="1"))
        self.assertEqual(p.returncode, 1)
        self.assertIn("попытка закрыта со статусом Rejected", p.stderr)
        self.assertIn("No response received", p.stderr,
                    "Rejected бывает и от таймаутов — надо показывать обе версии")
        # код отказа Asterisk пишет в лог сам — скрипт обязан его показать, а не
        # отправлять человека гадать, пароль виноват или allowlist
        logs = self.dir / "logs"
        (logs / "messages.log").parent.mkdir(exist_ok=True)
        (logs / "messages.log").write_text(
            "[2026-10-14 20:49:02] WARNING[1234]: res_pjsip_outbound_registration.c: "
            "Fatal response '401' received from 'sip:00000000@sip.example-operator.ru:5060' on "
            "registration attempt to 'sip:00000000@sip.example-operator.ru:5060', stopping outbound registration\n",
            encoding="utf-8")
        c = run(["--check"], env=dict(self.env, ASTERISK_CMD=str(fake), AST_LOG_DIR=str(logs)))
        self.assertEqual(c.returncode, 1)
        self.assertIn("Fatal response '401'", c.stdout + c.stderr)
        self.assertIn("stopping outbound registration", c.stdout + c.stderr)
        self.assertIn("module reload res_pjsip.so", c.stdout + c.stderr,
                      "должно сказать, что Asterisk больше не повторяет попытку")

    def test_missing_registration_does_not_stop_the_rest(self):
        """Без регистрации настройки АТС и номера всё равно должны примениться:
        раньше exit 1 обрывал установку на середине, и --ats/--numbers не выполнялись."""
        fake = self.dir / "asterisk-empty"
        fake.write_text("#!/bin/sh\necho \"No objects found.\"\nexit 0\n", encoding="utf-8")
        fake.chmod(0o755)
        env = dict(self.env, AMIPASS="amipass123", ASTERISK_CMD=str(fake), REG_TIMEOUT="1",
                   MCM_NUMBERS="5550000001", ATS_KEEP_PROVIDER="1")
        p = run(["--ats", "--numbers"], env=env)
        self.assertEqual(p.returncode, 1, "код возврата должен сигнализировать о проблеме")
        self.assertIn("регистрации нет", p.stderr)
        paths = [path for path, _h, _b in _ApiHandler.captured]
        self.assertIn("/api/v2/settings/raw", paths, "настройки не записаны из-за отсутствия регистрации")
        self.assertIn("/api/v2/numbers/save", paths, "номера не заведены из-за отсутствия регистрации")
        self.assertIn("не прочитаны либо не прошли валидацию", p.stderr,
                    "должно сказать, что Asterisk не загрузил секции")
        self.assertIn("объектов регистрации нет", p.stderr,
                      "по статусу видно, что секция [mcm-reg] вообще не создана")

    def test_error_answer_is_not_counted_as_added(self):
        """Ответ 'ok: false' — это провал, а не успех: поиск по подстроке '\"ok\"'
        засчитывал добавление номера даже когда API отказался его принимать."""
        _ApiHandler.response = {"ok": False, "error": "bad_number"}
        try:
            p = run(["--numbers-only"], env=dict(self.env, MCM_NUMBERS="5550000001"))
        finally:
            _ApiHandler.response = {"ok": True}
        self.assertEqual(p.returncode, 1, (p.stdout + p.stderr)[-500:])
        out = p.stdout + p.stderr
        self.assertIn("добавлено 0", out)
        self.assertIn("HTTP 200", out, "в отчёте должен быть и код ответа, и текст ошибки")
        self.assertIn("bad_number", out)

    def test_gives_up_when_service_is_unreachable(self):
        """Молчаливый ноль вместо «добавлено 0, проблем 15»: если порт не отвечает,
        надо сказать про перезапуск сервиса, а не слать 15 заведомо пустых запросов."""
        probe = socket.socket()
        probe.bind(("127.0.0.1", 0))
        dead_port = probe.getsockname()[1]
        probe.close()
        p = run(["--numbers-only"], env=dict(self.env, ATS_URL="http://127.0.0.1:%d" % dead_port,
                                             ATS_UP_TIMEOUT="1", MCM_NUMBERS="5550000001"))
        self.assertEqual(p.returncode, 1)
        out = p.stdout + p.stderr
        self.assertIn("не отвечает", out)
        self.assertIn("--numbers-only", out, "должно предложить команду повтора")
        self.assertEqual(len(_ApiHandler.captured), 0, "без живого порта слать POST незачем")

    def test_silence_and_refusal_are_different_problems(self):
        """Rejected — общий статус, а чинить надо разное. По логу различаем «молчат» (их
        allowlist/порт/транспорт — пароль ни при чём) и «ответили отказом» (наш username/
        password). Иначе на тишину отвечают ротацией секрета и теряют ещё сутки."""
        fake = self.dir / "ast-rej"
        fake.write_text(
            '#!/bin/sh\ncase "$*" in\n'
            '  *"show registrations"*) echo " mcm-reg/sip:00000000@sip.example-operator.ru:5060  mcm-auth  Rejected  (exp. 55s)" ;;\n'
            '  *) echo "  Endpoint: mcm  Unavailable  0 of 15" ;;\nesac\nexit 0\n', encoding="utf-8")
        fake.chmod(0o755)
        logs = self.dir / "logs"
        logs.mkdir(exist_ok=True)
        env = dict(self.env, ASTERISK_CMD=str(fake), AST_LOG_DIR=str(logs), REG_TIMEOUT="1")
        (logs / "messages.log").write_text(
            "[Oct 14 21:31:43] WARNING[700904] res_pjsip_outbound_registration.c: No response received "
            "from 'sip:00000000@sip.example-operator.ru:5060' on registration attempt to "
            "'sip:00000000@sip.example-operator.ru:5060', retrying in '60'\n", encoding="utf-8")
        p = run(["--check"], env=env)
        out = p.stdout + p.stderr
        self.assertIn("по логу это тишина", out)
        self.assertIn("пароль тут ни при чём", out)
        self.assertIn("/dev/tcp/sip.example-operator.ru/5060", out, "нужна команда пробы порта")
        self.assertIn("timeout 90 tcpdump", out, "и проба по pcap")
        self.assertNotEqual(p.returncode, 0)
        (logs / "messages.log").write_text(
            "[Oct 14 21:40:00] WARNING[1] res_pjsip_outbound_registration.c: Fatal response '401' received "
            "from 'sip:00000000@sip.example-operator.ru:5060' on registration attempt to '…', stopping outbound "
            "registration\n", encoding="utf-8")
        q = run(["--check"], env=env)
        self.assertIn("по логу это отказ", q.stdout + q.stderr)
        self.assertNotIn("пароль тут ни при чём", q.stdout + q.stderr,
                         "на отказе нельзя утверждать, что пароль ни при чём")

    def test_check_says_when_installed_file_is_older_than_template(self):
        """Конфиг на коробке переживает обновление репозитория: поля валидны, но новых нет —
        без max_retries Asterisk после 10 неудач перестаёт пробовать, и статус выглядит
        как «оператор молчит». --check обязан это сказать и не показать при этом пароль."""
        self.assertEqual(run(env=self.env).returncode, 0)
        f = self.dir / "pjsip-multicom.conf"
        keep = [l for l in f.read_text(encoding="utf-8").splitlines()
                if not re.match(r"^(max_retries|auth_rejection_permanent|forbidden_retry_interval"
                                r"|fatal_retry_interval)=", l)]
        self.assertLess(len(keep), len(f.read_text(encoding="utf-8").splitlines()))
        f.write_text("\n".join(keep) + "\n", encoding="utf-8")
        p = run(["--check"], env=self.env)
        out = p.stdout + p.stderr
        self.assertIn("нет полей, которые есть в шаблоне", out)
        self.assertIn("max_retries", out)
        self.assertNotIn("p@ss-w0rd", out, "сравнение имён полей не должно печатать значения")
        # и не ругается, когда файл свежий
        self.assertEqual(run(env=self.env).returncode, 0)
        self.assertNotIn("нет полей, которые есть в шаблоне", run(["--check"], env=self.env).stdout)

    def test_final_hint_names_a_log_file_that_exists(self):
        """Финальная подсказка не должна звать в выдуманный путь: на проде файла
        /var/log/asterisk/messages.log не было (там messages.log.log), и grep падал."""
        fake = self.dir / "ast-empty"
        fake.write_text('#!/bin/sh\necho "No objects found."\nexit 0\n', encoding="utf-8")
        fake.chmod(0o755)
        logs = self.dir / "varlog"
        logs.mkdir(exist_ok=True)
        (logs / "messages.log.log").write_text("x\n", encoding="utf-8")
        env = dict(self.env, ASTERISK_CMD=str(fake), AST_LOG_DIR=str(logs), REG_TIMEOUT="1")
        p = run(env=env)
        out = p.stdout + p.stderr
        self.assertNotEqual(p.returncode, 0, "регистрации нет — код должен быть 1")
        self.assertIn(str(logs / "messages.log.log"), out)
        self.assertNotIn("/var/log/asterisk/messages.log", out)

    def test_ats_token_helper_needs_real_token(self):
        """deploy/ats-token.sh: без поля token в ответе — код 1 и причина в stderr."""
        env_file = self.dir / "pw"
        env_file.write_text("secret123\n", encoding="utf-8")
        p = subprocess.run(["bash", str(ROOT / "deploy" / "ats-token.sh")],
                           env=dict(os.environ, ATS_URL="http://127.0.0.1:%d" % self.port,
                                    ATS_LOGIN="admin", ATS_PASSWORD_FILE=str(env_file)),
                           capture_output=True, text=True, timeout=60, cwd=str(ROOT))
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("не выдал токен", p.stderr)
        self.assertEqual(p.stdout.strip(), "", "мусор в stdout сломает конструкцию TOK=$(…)")

    def test_ats_token_helper_reports_unreachable(self):
        p = subprocess.run(["bash", str(ROOT / "deploy" / "ats-token.sh")],
                           env=dict(os.environ, ATS_URL="http://127.0.0.1:1",
                                    ATS_LOGIN="admin", ATS_PASSWORD_FILE="/etc/hostname"),
                           capture_output=True, text=True, timeout=60, cwd=str(ROOT))
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("не прошёл", p.stderr)

    def test_ats_requires_ami_password(self):
        env = {k: v for k, v in self.env.items() if k != "AMIPASS"}
        p = run(["--ats"], env=env)
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("AMIPASS", p.stderr)


if __name__ == "__main__":
    unittest.main()
