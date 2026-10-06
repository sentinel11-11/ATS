#!/usr/bin/env python3
"""Сверка копии кода на сервере с ревизиями из git — по md5.

Нужен, когда код на сервер принесли файлами (scp/rsync/архив): git-меток там нет,
и по `ls` не понять, какая это ревизия и правили ли код руками.

Манифест на сервере (одна команда):

    find /opt/ats -name '*.py' -not -path '*/venv/*' | sort | xargs md5sum > /tmp/manifest.txt

Здесь, рядом с клоном репозитория:

    python3 deploy/match-manifest.py /tmp/manifest.txt
    python3 deploy/match-manifest.py /tmp/manifest.txt HEAD arena/01a0cb5e-ats

Сопоставление по «хвосту» пути (app/api.py ↔ …/app/app/api.py), поэтому не важно,
в какой именно подкаталог сервера лёг корень проекта. Вывод по каждой ревизии:
совпало / различается (правили руками — или это другая ревизия) / только на сервере /
есть в git, но не на сервере (укороченная копия). Итог: лучшая ревизия — минимум
«различается» при максимуме «совпало».
"""
from __future__ import annotations

import fnmatch
import hashlib
import subprocess
import sys

DEFAULT_REVS = [
    "HEAD",
    "origin/arena/01a0cdee-ats",
    "origin/arena/01a0cb5e-ats",
    "origin/arena/01a072c1-ats",
    "origin/arena/01a0cb1f-ats",
    "origin/arena/01a0b903-ats",
    "origin/master",
]


def sh(args: list[str]) -> str:
    return subprocess.run(args, capture_output=True, text=True).stdout


def tail(path: str, n: int = 2) -> str:
    parts = [p for p in path.replace("\\", "/").split("/") if p and p != "."]
    return "/".join(parts[-n:])


def read_manifest(path: str) -> list[tuple[str, str]]:
    text = sys.stdin.read() if path == "-" else open(path, encoding="utf-8", errors="replace").read()
    out = []
    for line in text.splitlines():
        parts = line.split(None, 1)
        if len(parts) != 2:
            continue
        md5, name = parts[0].strip().lower(), parts[1].strip().lstrip("*").strip()
        if len(md5) == 32:
            out.append((md5, name))
    return out


def rev_index(rev: str, pattern: str) -> dict[str, str]:
    """repo-путь -> md5 содержимого, для всех .py ревизии."""
    idx: dict[str, str] = {}
    for name in sh(["git", "ls-tree", "-r", "--name-only", rev]).splitlines():
        if not name.endswith(".py"):
            continue
        if pattern != "*" and not (fnmatch.fnmatch(name, pattern) or fnmatch.fnmatch(name, "*/" + pattern)):
            continue
        blob = subprocess.run(["git", "cat-file", "blob", "%s:%s" % (rev, name)], capture_output=True).stdout
        idx[name] = hashlib.md5(blob).hexdigest()
    return idx


def compare(entries: list[tuple[str, str]], idx: dict[str, str]) -> tuple[int, dict]:
    """Подбираем, сколько ведущих компонентов пути у серверных файлов соответствует
    корню репозитория (на сервере корень обычно лежит в /opt/ats/app или около того),
    и считаем статистику для лучшего варианта."""
    best = (-1, {})
    for strip in range(0, 8):
        same = differed = extra = 0
        diff_list, extra_list, seen = [], [], set()
        for md5, name in entries:
            parts = [x for x in name.replace("\\", "/").split("/") if x and x != "."]
            if strip >= len(parts):
                continue
            rel = "/".join(parts[strip:])
            seen.add(rel)
            if rel not in idx:
                extra += 1
                extra_list.append(name)
            elif idx[rel] == md5:
                same += 1
            else:
                differed += 1
                diff_list.append(rel)
        score = same * 10 - differed
        if same and score > best[0]:
            best = (score, {"strip": strip, "same": same, "differed": differed, "extra": extra,
                            "diff_list": diff_list, "extra_list": extra_list,
                            "absent": sorted(set(idx) - seen)})
    return best


def main() -> int:
    args = sys.argv[1:]
    pattern = "*"
    if "--paths" in args:
        i = args.index("--paths")
        pattern = args[i + 1]
        del args[i:i + 2]
    if not args:
        print(__doc__)
        return 2
    entries = read_manifest(args[0])
    revs = args[1:] or DEFAULT_REVS
    if not entries:
        print("манифест пуст: `find … | xargs md5sum > файл` и передай файл сюда")
        return 1
    print("манифест: %s файлов; фильтр путей: %s" % (len(entries), pattern))
    scored = []
    for rev in revs:
        if not sh(["git", "rev-parse", "--verify", "--quiet", rev]).strip():
            print("\n%-34s пропуск (нет такой ревизии в этом клоне)" % rev)
            continue
        idx = rev_index(rev, pattern)
        _, st = compare(entries, idx)
        if not st:
            print("\n%-34s ни один файл не сопоставился (фильтр путей?)" % rev)
            continue
        print("\n%-34s совпало %3d | различается %3d | только на сервере %3d | нет на сервере %3d"
              % (rev, st["same"], st["differed"], st["extra"], len(st["absent"])))
        for k in st["diff_list"][:12]:
            print("      различается:       %s" % k)
        for k in st["extra_list"][:12]:
            print("      только на сервере: %s" % k)
        if st["absent"]:
            print("      (в копии нет %d файлов репо, напр.: %s)" % (len(st["absent"]), ", ".join(st["absent"][:4])))
        scored.append((-st["same"], st["differed"], len(st["absent"]), rev))
    if scored:
        ns, d, absent, rev = min(scored)
        same = -ns
        print("\nитог: код на сервере ближе всего к %s — совпало %d из %d, различается %d, нет в копии %d"
              % (rev, same, len(entries), d, absent))
        if d == 0 and absent == 0:
            print("это ровно та ревизия, без ручных правок → можно смело переезжать на новую.")
        elif d == 0:
            print("ручных правок нет, но копия укороченная (нет части файлов репо) — уточни, что за каталог.")
        else:
            print("различающиеся файлы = либо ручные правки на сервере, либо другая ревизия.")
            print("до обновления их надо разобрать: включаем в репозиторий отдельным коммитом")
            print("или осознанно перезаписываем. Команды для разбора дам после манифеста.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
