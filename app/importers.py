# -*- coding: utf-8 -*-
"""Импорт баз контактов: CSV и Excel (.xlsx) — только стандартная библиотека.

Конвейер:  файл (bytes) -> parse_csv/parse_xlsx -> map_headers ->
rows_to_records -> import_records (запись в БД).

- CSV: автоопределение кодировки (utf-8-sig/cp1251) и разделителя (; , \\t |).
- XLSX: разбор через zipfile + ElementTree (sharedStrings, inlineStr, числа).
- Заголовки распознаются на русском и английском (см. HEADER_ALIASES).
- Файл без заголовков (простой список телефонов) тоже понимается.
"""
import csv
import io
import re
import zipfile
import xml.etree.ElementTree as ET

# Поле -> множество синонимов заголовков (всё в нижнем регистре, без пробелов по краям)
HEADER_ALIASES = {
    "phone": {"телефон", "phone", "mobile", "мобильный", "номер", "номер телефона",
              "тел", "phone_number", "msisdn", "tel", "contact phone", "телефон клиента",
              "номер телефона", "моб. телефон", "моб телефон", "сотовый"},
    "name": {"имя", "фио", "ф.и.о.", "ф и о", "название", "контакт", "клиент",
             "name", "client", "contact", "full name", "fullname", "фио клиента",
             "имя клиента", "наименование", "contact name"},
    "group": {"группа", "group", "сегмент", "segment", "категория", "category"},
    "tags": {"теги", "tags", "метки", "метка", "labels", "label", "ярлыки", "ярлык", "тэги"},
    "note": {"примечание", "комментарий", "комментарии", "note", "notes", "comment",
             "comments", "описание", "description", "коммент"},
    "consent": {"согласие", "consent", "согласие на обзвон", "разрешение", "разрешение на обзвон",
                "согласие на звонки", "можно звонить"},
}

TRUTHY = {"1", "да", "д", "yes", "y", "true", "+", "есть", "ok", "✓"}


def normalize_header(h):
    """Заголовок к каноническому виду для сопоставления с синонимами."""
    s = str(h or "").strip().lower()
    s = re.sub(r"\s+", " ", s)
    return s


def map_headers(headers):
    """Заголовки -> {поле: индекс колонки}. Первое совпадение побеждает."""
    mapping = {}
    for i, h in enumerate(headers):
        key = normalize_header(h)
        for field, aliases in HEADER_ALIASES.items():
            if field not in mapping and key in aliases:
                mapping[field] = i
    return mapping


def normalize_phone(raw):
    """Номер к единому виду: только цифры, РФ -> 7XXXXXXXXXX.

    8XXXXXXXXXX -> 7XXXXXXXXXX, 9XXXXXXXXX (10 цифр) -> 7+номер,
    +7/7XXXXXXXXXX -> как есть, остальное — цифры как есть.
    """
    digits = re.sub(r"\D", "", str(raw or ""))
    if len(digits) == 11 and digits[0] == "8":
        digits = "7" + digits[1:]
    elif len(digits) == 10:
        digits = "7" + digits
    return digits


def phone_valid(phone):
    """E.164-минимум: 10–15 цифр."""
    d = str(phone or "")
    return d.isdigit() and 10 <= len(d) <= 15


def truthy(v):
    if isinstance(v, bool):
        return v
    return str(v or "").strip().lower() in TRUTHY


def clean_tags(raw):
    """Теги к единому виду: 'vip, москва' (уникальные, порядок сохранён)."""
    if isinstance(raw, (list, tuple)):
        parts = [str(x) for x in raw]
    else:
        parts = re.split(r"[,;|/]+", str(raw or ""))
    out = []
    seen = set()
    for p in parts:
        t = p.strip().strip("#").strip()
        if not t or t.lower() in seen:
            continue
        seen.add(t.lower())
        out.append(t[:64])
    return ", ".join(out[:20])


# ---------------- CSV ----------------

def parse_csv(data: bytes):
    """-> (headers, rows). Первая непустая строка считается заголовками."""
    text = None
    for enc in ("utf-8-sig", "utf-8", "cp1251", "cp866"):
        try:
            text = data.decode(enc)
            break
        except Exception:
            continue
    if text is None:
        text = data.decode("utf-8", "ignore")
    sample = "\n".join(text.splitlines()[:8])
    delim = None
    try:
        if sample.strip():
            dialect = csv.Sniffer().sniff(sample, delimiters=";,|\t,")
            if dialect.delimiter in (";", ",", "\t", "|"):
                delim = dialect.delimiter
    except Exception:
        delim = None
    if not delim:
        counts = {d: sample.count(d) for d in (";", ",", "\t", "|")}
        delim = max(counts, key=counts.get)
    reader = csv.reader(io.StringIO(text), delimiter=delim)
    table = [[(c or "") for c in r] for r in reader]
    table = [r for r in table if any(str(c).strip() for c in r)]
    if not table:
        return [], []
    return table[0], table[1:]


# ---------------- XLSX (stdlib: zip + XML) ----------------

_XLSX_NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


def _col_to_index(col):
    n = 0
    for ch in col:
        n = n * 26 + (ord(ch) - 64)
    return n - 1


def _num_text(txt):
    try:
        f = float(txt)
        return str(int(f)) if f.is_integer() else str(f)
    except Exception:
        return txt


def parse_xlsx(data: bytes):
    """Разбор первого листа .xlsx -> (headers, rows). Значения — строки."""
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except Exception:
        raise ValueError("не похоже на .xlsx (повреждён или это старый .xls — сохраните как .xlsx)")
    # общие строки
    shared = []
    try:
        sroot = ET.fromstring(zf.read("xl/sharedStrings.xml"))
        for si in sroot.findall("m:si", _XLSX_NS):
            shared.append("".join(si.itertext()))
    except KeyError:
        pass
    # первый лист
    try:
        ws_data = zf.read("xl/worksheets/sheet1.xml")
    except KeyError:
        names = sorted(n for n in zf.namelist()
                       if n.startswith("xl/worksheets/sheet") and n.endswith(".xml"))
        if not names:
            raise ValueError("в .xlsx нет листов с данными")
        ws_data = zf.read(names[0])
    wroot = ET.fromstring(ws_data)
    grid = {}
    max_r = max_c = 0
    for row in wroot.findall(".//m:row", _XLSX_NS):
        for c in row.findall("m:c", _XLSX_NS):
            m = re.match(r"^([A-Z]+)(\d+)$", c.get("r", ""))
            if not m:
                continue
            ci = _col_to_index(m.group(1))
            ri = int(m.group(2))
            t = c.get("t", "")
            v_el = c.find("m:v", _XLSX_NS)
            val = ""
            if t == "s" and v_el is not None and (v_el.text or "").strip():
                try:
                    val = shared[int(v_el.text)]
                except Exception:
                    val = ""
            elif t == "inlineStr":
                is_el = c.find("m:is", _XLSX_NS)
                val = "".join(is_el.itertext()) if is_el is not None else ""
            elif t == "b":
                val = "1" if (v_el is not None and (v_el.text or "").strip() == "1") else "0"
            elif t == "e":
                val = ""
            elif v_el is not None and v_el.text is not None:
                val = _num_text(v_el.text.strip())
            grid[(ri, ci)] = val
            if ri > max_r:
                max_r = ri
            if ci > max_c:
                max_c = ci
    if not grid:
        return [], []
    table = []
    for ri in range(1, max_r + 1):
        table.append([grid.get((ri, ci), "") for ci in range(max_c + 1)])
    table = [r for r in table if any(str(x).strip() for x in r)]
    if not table:
        return [], []
    return table[0], table[1:]


# ---------------- записи ----------------

def _looks_like_phone_cell(v):
    return phone_valid(normalize_phone(v))


def rows_to_records(headers, rows, consent_default=False):
    """Строки таблицы -> ([records], mapping-info).

    record: {row, phone, name, group, tags, note, consent}.
    Если заголовков нет (первая строка — данные), первая колонка с
    телефонами считается номером, остальные данные тоже забираются.
    """
    mapping = map_headers(headers)
    data_rows = list(rows)
    no_header = False
    if "phone" not in mapping and headers:
        # может, заголовков вообще нет: ищем колонку-телефон в первой строке
        for k, cell in enumerate(headers):
            if _looks_like_phone_cell(cell):
                mapping = {"phone": k}
                # соседняя непустая колонка — вероятно имя
                for k2, cell2 in enumerate(headers):
                    if k2 != k and str(cell2 or "").strip():
                        mapping.setdefault("name", k2)
                        break
                data_rows = [headers] + data_rows
                no_header = True
                break
    if "phone" not in mapping:
        raise ValueError("нет колонки с телефоном (нужен заголовок «Телефон»/«Phone» "
                         "или просто список номеров)")
    records = []
    for i, r in enumerate(data_rows):
        def cell(field):
            idx = mapping.get(field)
            if idx is None or idx >= len(r):
                return ""
            return str(r[idx] if r[idx] is not None else "")
        records.append({
            "row": i + (1 if no_header else 2),  # человеческий номер строки в файле
            "phone": cell("phone"),
            "name": cell("name").strip(),
            "group": cell("group").strip(),
            "tags": cell("tags"),
            "note": cell("note").strip(),
            "consent": truthy(cell("consent")) if "consent" in mapping else bool(consent_default),
        })
    info = {"mapping": {k: (headers[v] if v < len(headers) else "?") for k, v in mapping.items()},
            "no_header": no_header}
    return records, info


def import_records(records, database_id=0, source="import"):
    """Запись записей в БД. -> {added, updated, skipped, errors[]}."""
    from . import db
    from .config import now_iso
    added = 0
    updated = 0
    skipped = 0
    errors = []
    seen = set()
    try:
        database_id = int(database_id or 0)
    except Exception:
        database_id = 0
    for rec in records:
        if not isinstance(rec, dict):
            skipped += 1
            errors.append({"row": 0, "phone": "", "reason": "не строка"})
            continue
        row_no = rec.get("row", 0)
        phone = normalize_phone(rec.get("phone"))
        if not phone:
            skipped += 1
            errors.append({"row": row_no, "phone": "", "reason": "нет телефона"})
            continue
        if not phone_valid(phone):
            skipped += 1
            errors.append({"row": row_no, "phone": phone, "reason": "не похоже на телефон (10–15 цифр)"})
            continue
        if phone in seen:
            skipped += 1
            errors.append({"row": row_no, "phone": phone, "reason": "дубль в файле"})
            continue
        seen.add(phone)
        consent = 1 if truthy(rec.get("consent")) else 0
        tags = clean_tags(rec.get("tags", ""))
        name = str(rec.get("name", ""))[:200]
        grp = str(rec.get("group", rec.get("grp", "")))[:200]
        note = str(rec.get("note", ""))[:500]
        existing = db.fetch1("SELECT * FROM contacts WHERE phone=?", (phone,))
        try:
            if existing:
                merged_tags = clean_tags(
                    (existing.get("tags") or "") + (", " if existing.get("tags") and tags else "") + tags)
                upd = {"name": name or existing.get("name", ""), "grp": grp or existing.get("grp", ""),
                       "note": note or existing.get("note", ""), "consent": consent,
                       "tags": merged_tags, "updated": now_iso()}
                if database_id:
                    upd["database_id"] = database_id
                db.update("contacts", upd, "id=?", (existing["id"],))
                updated += 1
            else:
                db.insert("contacts", {"name": name, "phone": phone, "grp": grp, "note": note,
                                       "consent": consent, "consent_source": source,
                                       "blacklisted": 0, "complaints": 0,
                                       "tags": tags, "database_id": database_id,
                                       "created": now_iso(), "updated": now_iso()})
                added += 1
        except Exception:
            skipped += 1
            errors.append({"row": row_no, "phone": phone, "reason": "ошибка БД (возможно дубль)"})
    return {"added": added, "updated": updated, "skipped": skipped, "errors": errors[:100],
            "errors_total": len(errors)}


def parse_file(filename, data: bytes, consent_default=False):
    """Только разбор файла -> (records, info, fmt, headers). Без записи в БД."""
    name = str(filename or "").lower()
    if name.endswith(".xlsx"):
        headers, rows = parse_xlsx(data)
        fmt = "xlsx"
    elif name.endswith(".xls"):
        raise ValueError("старый формат .xls не поддерживается — откройте в Excel "
                         "и сохраните как .xlsx (или .csv)")
    elif name.endswith((".csv", ".txt")):
        headers, rows = parse_csv(data)
        fmt = "csv"
    else:
        # пробуем как CSV (часто .dat/.lst — тот же текст)
        try:
            headers, rows = parse_csv(data)
            fmt = "csv"
        except Exception:
            raise ValueError("неизвестный формат — загрузите .xlsx или .csv")
    if not headers and not rows:
        raise ValueError("файл пуст или не удалось прочитать данные")
    records, info = rows_to_records(headers, rows, consent_default=consent_default)
    return records, info, fmt, headers


def import_parsed(records, info, fmt, headers, database_id=0):
    """Запись уже разобранных записей в БД -> результат + info о разборе."""
    res = import_records(records, database_id=database_id)
    res["format"] = fmt
    res["headers"] = [str(h) for h in headers]
    res["mapping"] = info["mapping"]
    res["no_header"] = info["no_header"]
    res["total_rows"] = len(records)
    return res


def import_file(filename, data: bytes, database_id=0, consent_default=False):
    """Полный конвейер: файл -> БД. -> результат + info о разборе."""
    records, info, fmt, headers = parse_file(filename, data, consent_default=consent_default)
    return import_parsed(records, info, fmt, headers, database_id=database_id)
