# -*- coding: utf-8 -*-
"""Поведенческий тест логики сохранения настроек UI (C1, P1-аудита).

frontend/src/settingsSave.js — чистый ESM-модуль без JSX/зависимостей, поэтому
его можно исполнить node и проверить все комбинации успех/сбой двух запросов
(/settings/raw + /settings/save). До фикса успех показывался по
rawRes.ok OR basicRes.ok — частичный сбой выглядел как «всё сохранено».
"""
import json
import pathlib
import shutil
import subprocess
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
MODULE = ROOT / "frontend" / "src" / "settingsSave.js"

NODE_HARNESS = """
import { summarizeSaveResults } from './frontend/src/settingsSave.js';
const cases = JSON.parse(process.argv[1]);
const out = cases.map(([raw, basic]) => summarizeSaveResults(raw, basic));
console.log(JSON.stringify(out));
"""


class SettingsSaveLogicTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not MODULE.is_file():
            raise unittest.SkipTest("нет frontend/src/settingsSave.js (минимальная поставка)")
        if shutil.which("node") is None:
            raise unittest.SkipTest("node недоступен — поведенческий тест пропущен")

    def _run(self, cases):
        proc = subprocess.run(
            ["node", "--input-type=module", "-e", NODE_HARNESS, "--", json.dumps(cases)],
            cwd=str(ROOT), capture_output=True, text=True, timeout=30)
        self.assertEqual(proc.returncode, 0,
                         "node упал: %s" % proc.stderr[-2000:])
        return json.loads(proc.stdout)

    def test_all_four_outcomes(self):
        res = self._run([
            [{"ok": True}, {"ok": True}],               # оба успешны
            [{"ok": False, "error": "bad"}, {"ok": True}],   # сбой raw
            [{"ok": True}, {"ok": False, "error": "bad"}],   # сбой basic
            [{"ok": False, "error": "e1"}, {"ok": False, "error": "e2"}],  # оба сбой
            [None, None],                                # сеть/нет ответа
        ])
        both_ok, raw_fail, basic_fail, both_fail, no_resp = res

        self.assertTrue(both_ok["ok"])
        self.assertEqual(len(both_ok["failedSections"]), 0)
        self.assertIn("успешно сохранены", both_ok["message"])

        # частичный сбой обязан быть провалом с честным описанием
        self.assertFalse(raw_fail["ok"])
        self.assertEqual(len(raw_fail["savedSections"]), 1)
        self.assertEqual(len(raw_fail["failedSections"]), 1)
        self.assertEqual(raw_fail["failedSections"][0]["section"], "Конфигурация провайдера")
        self.assertIn("частично", raw_fail["message"].lower())
        self.assertNotIn("успешно сохранены", raw_fail["message"])

        self.assertFalse(basic_fail["ok"])
        self.assertEqual(basic_fail["failedSections"][0]["section"], "Основные настройки")
        self.assertIn("частично", basic_fail["message"].lower())

        self.assertFalse(both_fail["ok"])
        self.assertEqual(len(both_fail["failedSections"]), 2)
        self.assertIn("ничего не сохранено", both_fail["message"])

        self.assertFalse(no_resp["ok"])
        self.assertEqual(len(no_resp["failedSections"]), 2)
        self.assertEqual(no_resp["failedSections"][0]["error"], "нет ответа")

    def test_source_has_no_or_shortcut(self):
        """Регресс-страха: OR-условие успеха не возвращается в страницу настроек."""
        page = (ROOT / "frontend" / "src" / "pages" / "Settings.jsx").read_text(encoding="utf-8")
        self.assertNotIn("rawRes.ok) || (basicRes", page,
                         "вернулся P1-дефект: успех по ИЛИ вместо И")
        self.assertNotIn("rawRes?.error || basicRes?.error || '?'", page)
        self.assertIn("summarizeSaveResults", page,
                      "страница настроек должна использовать общую логику оценки")


if __name__ == "__main__":
    unittest.main()
