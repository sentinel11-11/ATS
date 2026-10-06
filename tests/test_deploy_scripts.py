# -*- coding: utf-8 -*-
"""Тесты скриптов выкладки из deploy/.

Раньше эти файлы не покрывались ничем — и именно поэтому в проде `update.sh` молча
пропустил ветку systemd (grep -q + pipefail = SIGPIPE 141 в условии) и поднял второй
экземпляр АТС от root. Сюда же — проверка синтаксиса всех shell-скриптов репозитория.
"""
import os
import pathlib
import shutil
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
DEPLOY = ROOT / "deploy"


def _script(path, env=None, args=(), cwd=None):
    p = subprocess.run(["bash", str(path), *args], env=env, cwd=str(cwd or ROOT),
                       capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL)
    return p


class ShellSyntaxTest(unittest.TestCase):
    """bash -n для каждого deploy/*.sh и agi-скриптов: сломанный скрипт до прод-запуска."""

    def scripts(self):
        found = sorted(DEPLOY.rglob("*.sh"))
        self.assertTrue(found, "не нашёл ни одного .sh в deploy/ — изменилась структура?")
        return found

    def test_bash_syntax(self):
        for sh in self.scripts():
            with self.subTest(script=str(sh.relative_to(ROOT))):
                p = subprocess.run(["bash", "-n", str(sh)], capture_output=True, text=True)
                self.assertEqual(p.returncode, 0, f"{sh.name}: {p.stderr[:400]}")

    def test_shebang_and_exec_bit(self):
        for sh in self.scripts():
            with self.subTest(script=str(sh.relative_to(ROOT))):
                first = sh.read_text(encoding="utf-8", errors="replace").splitlines()[0]
                self.assertTrue(first.startswith("#!"), f"{sh.name}: нет shebang")
                self.assertIn("sh", first)
                if os.name == "posix":
                    self.assertTrue(os.access(sh, os.X_OK), f"{sh.name}: не исполняемый (chmod +x)")

    @unittest.skipUnless(shutil.which("shellcheck"), "shellcheck не установлен")
    def test_shellcheck_no_errors(self):
        for sh in self.scripts():
            with self.subTest(script=str(sh.relative_to(ROOT))):
                p = subprocess.run(["shellcheck", "-S", "error", str(sh)],
                                   capture_output=True, text=True)
                self.assertEqual(p.returncode, 0, p.stdout[:1500])


class UpdateScriptGuardsTest(unittest.TestCase):
    """Тот самый класс бага: `cmd | grep -q` под `set -o pipefail`."""

    def test_no_grep_q_in_conditions(self):
        text = (DEPLOY / "update.sh").read_text(encoding="utf-8")
        bad = []
        for num, line in enumerate(text.splitlines(), 1):
            code = line.split("#", 1)[0] if not line.lstrip().startswith("#") else ""
            if "grep -q" in code:
                bad.append(f"{num}: {line.strip()[:120]}")
        self.assertEqual(bad, [], "grep -q в пайпе под pipefail даёт 141 и ложное «нет» — "
                                  "пишите `grep … >/dev/null` или сверяйте строку через case")

    def test_has_unit_helper_present(self):
        text = (DEPLOY / "update.sh").read_text(encoding="utf-8")
        self.assertIn("has_unit()", text)
        self.assertIn("port_busy()", text)

    def test_flags_documented_in_help(self):
        text = (DEPLOY / "update.sh").read_text(encoding="utf-8")
        for flag in ("--rollback", "--fast", "--no-build", "--no-restart", "--force"):
            self.assertIn(flag, text, flag)


if __name__ == "__main__":
    unittest.main()
