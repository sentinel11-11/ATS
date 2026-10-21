# -*- coding: utf-8 -*-
"""P0-страха: runtime-данные и скомпрометированные литералы не возвращаются в git.

Регресс к инциденту из docs/SECURITY_CLEANUP.md: data_v2/ats.db (рабочая база
с контактами/настройками) и data_v2/initial_credentials.txt (пароль админа)
исторически попали в git-индекс публичного репозитория.

Проверки:
1) в git-индексе нет БД, initial_credentials.txt, записей разговоров, дампов;
2) .gitignore действительно исключает data_v2/ и *.db;
3) в отслеживаемых файлах нет дословных скомпрометированных значений
   (тот же набор правил, что в tools/secret_scan.py для CI).

Если git недоступен (распакованный архив без .git) — проверка индекса
пропускается, скан файлов выполняется.
"""
import pathlib
import subprocess
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import secret_scan  # noqa: E402

FORBIDDEN_INDEX_SUFFIXES = (".db", ".db-wal", ".db-shm", ".sqlite", ".sqlite3", ".mp3", ".wav")
FORBIDDEN_INDEX_NAMES = {"initial_credentials.txt"}


def _git_ls_files():
    try:
        out = subprocess.run(["git", "ls-files"], cwd=str(ROOT), capture_output=True,
                             text=True, check=True).stdout
        return [f for f in out.splitlines() if f]
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


class NoRuntimeDataInGit(unittest.TestCase):
    def test_no_runtime_artifacts_in_index(self):
        files = _git_ls_files()
        if files is None:
            self.skipTest("git недоступен (не клон) — проверка индекса пропущена")
        bad = [f for f in files
               if f.endswith(FORBIDDEN_INDEX_SUFFIXES)
               or pathlib.PurePosixPath(f).name in FORBIDDEN_INDEX_NAMES
               or f.startswith("data_v2/")]
        self.assertEqual(
            bad, [],
            "в git-индексе runtime-данные (БД/креды/записи): %s — git rm --cached и "
            "проверь .gitignore (docs/SECURITY_CLEANUP.md §3)" % bad)

    def test_gitignore_excludes_runtime_data(self):
        gi = (ROOT / ".gitignore").read_text(encoding="utf-8")
        self.assertIn("data_v2/", gi, ".gitignore обязан исключать весь data_v2/")
        self.assertIn("*.db", gi, ".gitignore обязан исключать *.db")

    def test_no_compromised_literals_in_tracked_files(self):
        files = _git_ls_files()
        hits = secret_scan.scan_working_tree(files=files, include_weak=False)
        self.assertEqual(
            hits, [],
            "сканом найдены вероятные секреты/скомпрометированные литералы: %s "
            "(значения не печатались; см. tools/secret_scan.py)" % hits)


if __name__ == "__main__":
    unittest.main()
