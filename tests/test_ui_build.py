# -*- coding: utf-8 -*-
"""Стражи интерфейса: сборка app/ui и «сырой» JSX.

Повод для этих тестов — реальный сбой на проде: вкладка «Настройки» падала с
«Произошла ошибка при загрузке интерфейса / технология is not defined». В исходнике
было `... каналом <code>{' '}{технология}/{транк}/номер</code> ...`: текст подсказки,
случайно завёрнутый в фигурные скобки. В JSX это валидное выражение (переменная
`технология`), поэтому сборка проходила, а рантайм падал при рендере одной вкладки.

Дополнительно: app/ui (готовая сборка) лежит в git, потому что сервер обновления без
npm берёт интерфейс из репозитория. Значит правка frontend/src без `npm run build`
оставляет прод на УСТАРЕВШЕМ бандле — это и проверяет test_build_info_matches_sources.
"""
import hashlib
import json
import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"
UI = ROOT / "app" / "ui"

# Файлы, содержимое которых попадает в бандл (см. frontend/vite.config.js).
EXTRA_INPUTS = ["index.html", "tailwind.config.js", "postcss.config.js"]

# {кириллица} без кавычек = expression container → «X is not defined» в браузере.
BARE_CYRILLIC_IDENT = re.compile(r"\{[а-яёА-Я][а-яёА-Я0-9_]*\}")


def _frontend_files():
    if not FRONTEND.is_dir():
        raise unittest.SkipTest("нет каталога frontend/ (минимальная поставка)")

    def walk(d):
        out = []
        for p in sorted(d.iterdir()):
            if p.name == "node_modules" or p.name.startswith("."):
                continue
            if p.is_dir():
                out.extend(walk(p))
            else:
                out.append(p)
        return out

    items = [FRONTEND / rel for rel in EXTRA_INPUTS if (FRONTEND / rel).is_file()]
    items += walk(FRONTEND / "src")
    return sorted(((str(p.relative_to(FRONTEND)).replace("\\", "/"), p) for p in items),
                  key=lambda t: t[0])


def frontend_source_hash():
    """Тот же алгоритм, что и sourceHash() в vite.config.js: rel-path + NUL + байты."""
    h = hashlib.sha256()
    for rel, path in _frontend_files():
        h.update((rel + "\0").encode("utf-8"))
        h.update(path.read_bytes())
    return h.hexdigest()


class JsxSourceTest(unittest.TestCase):
    def test_no_bare_cyrillic_identifiers_in_jsx(self):
        """Никаких `{слово_кириллицей}` — иначе ReferenceError при рендере вкладки."""
        if not FRONTEND.is_dir():
            self.skipTest("нет каталога frontend/")
        bad = []
        for path in FRONTEND.rglob("*.jsx"):
            if "node_modules" in path.parts:
                continue
            for num, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                for m in BARE_CYRILLIC_IDENT.finditer(line):
                    bad.append("%s:%d: %s" % (path.relative_to(ROOT), num, m.group(0)))
        self.assertEqual(
            bad, [],
            "похоже на текст, случайно завёрнутый в JSX-выражение; оставь его обычным "
            "текстом или в строке: <code>технология/транк/номер</code>")

class UiBuildTest(unittest.TestCase):
    def setUp(self):
        if not (UI / "index.html").is_file():
            self.skipTest("нет собранного интерфейса в app/ui")

    def test_index_html_references_existing_assets(self):
        html = (UI / "index.html").read_text(encoding="utf-8")
        refs = re.findall(r'(?:src|href)="\.?/?assets/([^"]+)"', html)
        self.assertTrue(refs, "в index.html нет ни одной ссылки на assets/ — сборка повреждена")
        for ref in refs:
            self.assertTrue((UI / "assets" / ref).is_file(),
                            "index.html ссылается на отсутствующий assets/%s" % ref)

    def test_all_assets_are_referenced(self):
        """vite чистит outDir, поэтому лишних захешированных файлов в репозитории быть не
        должно: иначе на сервере остаются старые бандлы, которые браузер может закэшировать."""
        html = (UI / "index.html").read_text(encoding="utf-8")
        refs = set(re.findall(r'(?:src|href)="\.?/?assets/([^"]+)"', html))
        on_disk = {p.name for p in (UI / "assets").glob("*") if p.suffix in (".js", ".css")}
        self.assertEqual(on_disk - refs, set(),
                         "в app/ui/assets лежат файлы, на которые index.html не ссылается: %s" %
                         ", ".join(sorted(on_disk - refs)))

    def test_build_info_matches_sources(self):
        """app/ui собран именно из тех исходников, что в репозитории."""
        info_path = UI / "build-info.json"
        self.assertTrue(info_path.is_file(),
                        "нет app/ui/build-info.json — пересобери UI (cd frontend && npm run build) "
                        "и закоммить app/ui целиком")
        info = json.loads(info_path.read_text(encoding="utf-8"))
        self.assertEqual(
            info.get("sourceHash"), frontend_source_hash(),
            "сборка app/ui устарела относительно frontend/src: сервер без npm возьмёт "
            "именно её. Выполни: cd frontend && npm run build, затем закоммить app/ui")

    def test_bundle_has_no_dangling_cyrillic_identifier(self):
        """Косвенная проверка самого бандла: в валидном бандле нет `технология` как идентификатора."""
        js = "\n".join(p.read_text(encoding="utf-8", errors="replace")
                       for p in (UI / "assets").glob("*.js"))
        self.assertNotIn("{технология}", js)


if __name__ == "__main__":
    unittest.main()
