#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Сканер секретов ATS: рабочее дерево, git-индекс и (опционально) история.

Использование:
    python3 tools/secret_scan.py                 # рабочее дерево (tracked + untracked, кроме игнора)
    python3 tools/secret_scan.py --staged        # только git-индекс (для pre-commit)
    python3 tools/secret_scan.py --history       # вся история git (медленно; отчёт без значений)

Коды выхода: 0 — чисто, 1 — найдены попадания high-уверенности, 2 — ошибка запуска.

Принцип: ПЕЧАТАЮТСЯ ТОЛЬКО файл/строка/номер строки и тип правила — сами значения
в вывод не попадают (чтобы отчёт/CI-лог сам не стал утечкой).
"""
import argparse
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]

# --- Известные скомпрометированные литералы (docs/SECURITY_CLEANUP.md, история git).
# Значения удалены из дерева; здесь — их точные формы для регресс-защиты.
COMPROMISED_LITERALS = [
    "cs4kjXnPopzx",          # пароль админа из initial_credentials.txt (отозван)
    "00083819",              # SIP-логин регистрации Мультиком
    "95.128.224.47",         # IP регистратора
    "95.128.224.0/21",       # сеть регистратора
]
# DID-номера компании: префиксы реальных боевых номеров из письма оператора
COMPROMISED_PHONE_PREFIXES = ["96902299", "98631493", "936257"]

HIGH_CONFIDENCE_RULES = [
    (re.compile(r"-----BEGIN (RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----"), "private_key"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "aws_access_key"),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b"), "github_token"),
    (re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"), "slack_token"),
    # пароли/ключи, присвоенные литералом в конфигах и коде (не env-имя, не плейсхолдер)
    (re.compile(r"""(?im)^\s*(password|passwd|secret|api_key|apikey|apikey|token)\s*[=:]\s*["']?[A-Za-z0-9+/_-]{12,}["']?\s*$"""),
     "literal_credential"),
]
WEAK_RULES = [
    # CandidSoft: длинные hex/base64 рядом со словами secret/token — ручная проверка
    (re.compile(r"(?i)(secret|token|api[_-]?key)[^\n]{0,40}[A-Fa-f0-9]{32,}"), "hex_near_secret_word"),
]

ALLOWLIST_SUBSTRINGS = (
    # сам сканер и доки чистки упоминают формы значений законно
    "tools/secret_scan.py", "docs/SECURITY_CLEANUP.md", "tests/test_no_runtime_secrets.py",
)
PLACEHOLDER_TOKENS = {
    "REDACTED-ROTATED", "__MCM_PASS__", "ЗАМЕНИТЕ_НА_СЛУЧАЙНЫЙ_ПАРОЛЬ", "ПАРОЛЬ_ИЗ_ПИСЬМА",
    "TestAdmin123!",  # тестовый пароль тестовых БД (не секрет)
}
BINARY_SUFFIXES = {".db", ".png", ".jpg", ".jpeg", ".gif", ".ico", ".woff", ".woff2",
                   ".mp3", ".wav", ".ogg", ".opus", ".m4a", ".zip", ".gz", ".exe", ".dll",
                   ".pyc", ".lock", ".min.js"}


def _git(args, check=True):
    return subprocess.run(["git"] + args, cwd=str(ROOT), capture_output=True,
                          text=True, check=check)


def tracked_files():
    try:
        out = _git(["ls-files", "-z"]).stdout
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None
    return [f for f in out.split("\0") if f]


def staged_files():
    try:
        out = _git(["diff", "--cached", "--name-only", "-z"]).stdout
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None
    return [f for f in out.split("\0") if f]


def is_placeholder(line):
    s = line.strip()
    return any(t in s for t in PLACEHOLDER_TOKENS) or "..." in s or "…" in s


def scan_text(rel, text, rules):
    hits = []
    for rx, kind in rules:
        for m in rx.finditer(text):
            lineno = text.count("\n", 0, m.start()) + 1
            line = text.splitlines()[lineno - 1] if text.splitlines() else ""
            if is_placeholder(line):
                continue
            hits.append((rel, lineno, kind))
    for lit in COMPROMISED_LITERALS:
        for m in re.finditer(re.escape(lit), text):
            lineno = text.count("\n", 0, m.start()) + 1
            hits.append((rel, lineno, "compromised_literal:" + lit[:4] + "…"))
    for pref in COMPROMISED_PHONE_PREFIXES:
        for m in re.finditer(re.escape(pref) + r"\d\d", text):
            lineno = text.count("\n", 0, m.start()) + 1
            hits.append((rel, lineno, "compromised_did:" + pref[:4] + "…"))
    return hits


def scan_working_tree(files=None, include_weak=False):
    if files is None:
        files = tracked_files() or [str(p.relative_to(ROOT)) for p in ROOT.rglob("*")
                                    if p.is_file() and ".git" not in p.parts]
    rules = list(HIGH_CONFIDENCE_RULES) + (list(WEAK_RULES) if include_weak else [])
    hits = []
    for rel in files:
        if any(a in rel for a in ALLOWLIST_SUBSTRINGS):
            continue
        path = ROOT / rel
        if not path.is_file() or path.suffix.lower() in BINARY_SUFFIXES:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        hits.extend(scan_text(rel, text, rules))
    return hits


def scan_history(max_hits=50):
    """git log --all -p через те же правила. Медленно; значения не печатаем."""
    try:
        proc = _git(["log", "--all", "-p", "--no-color"], check=False)
    except FileNotFoundError:
        return None
    hits = []
    cur_file = "?"
    count = 0
    for line in proc.stdout.splitlines():
        if line.startswith("+++ b/"):
            cur_file = line[6:]
            continue
        if line[:1] not in ("+", "-") or line.startswith(("+++", "---")):
            continue
        if any(a in cur_file for a in ALLOWLIST_SUBSTRINGS):
            continue
        for h in scan_text(cur_file, line, HIGH_CONFIDENCE_RULES):
            if count < max_hits:
                hits.append((h[0], h[2]))
            count += 1
    return hits, count


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--staged", action="store_true", help="только индекс (pre-commit)")
    ap.add_argument("--history", action="store_true", help="сканировать историю git")
    ap.add_argument("--weak", action="store_true", help="включить правила низкой уверенности")
    ap.add_argument("--max-hits", type=int, default=50)
    args = ap.parse_args()

    if args.history:
        res = scan_history(args.max_hits)
        if res is None:
            print("git недоступен", file=sys.stderr)
            return 2
        hits, total = res
        for f, kind in hits:
            print(f"HISTORY {f}: {kind}")
        print(f"история: {total} попаданий (показано {len(hits)}); значения не выводятся")
        return 1 if total else 0

    files = staged_files() if args.staged else None
    hits = scan_working_tree(files=files, include_weak=args.weak)
    for rel, lineno, kind in hits:
        print(f"{rel}:{lineno}: {kind}")
    if hits:
        print(f"найдено {len(hits)} попаданий — проверьте вручную (значения не печатались)")
        return 1
    print("секретов не найдено")
    return 0


if __name__ == "__main__":
    sys.exit(main())
