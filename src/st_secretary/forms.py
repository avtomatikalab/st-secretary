"""Свои формы предзаявок (Правки, п. 37): секретарь один раз показывает программе, где что в его форме.

Форма — запись в «Свои формы заявок.json» на этом компьютере (рядом с личными данными судей, не в облаке):

    {"name": "Кубок г. Красноярска — делегации",
     "headers": [заголовки колонок как в файле], "signature": [они же без регистра, «ё» и знаков],
     "columns": [поле программы для каждой колонки: «fio», «birth», …, «own» — своя колонка, «skip» — пропустить],
     "team_in": "head" (команда одна — в шапке) или "row" (команда в каждой строке — заявка делегации),
     "head": {"team": "B4", "territory": "C4", "representative": "D4", "contacts": "E4"} — ячейки шапки,
     "sheets": [] — все листы с такой же шапкой, или [названия листов],
     "values": {поле: {как в форме: как в программе}} — «МУЖ» → «м», «+» → «1»}

Файл, у которого на каком-то листе строка заголовков совпадает с формой, читается по ней сам. Чтение по форме
даёт ту же «сырую заявку» (RawApplication), что и стандартный бланк, — проверка, комиссия, жеребьёвка работают как
раньше. Заявка делегации (команда в каждой строке) при загрузке делится на файлы команд по стандартному бланку
(split_by_team), исходный файл сохраняется. Стандартный бланк остаётся формой по умолчанию.
"""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

from st_secretary.importers.preapp_xlsx import COLUMNS, RawApplication, RawRow
from st_secretary.textclean import clean_spaces

OWN, SKIP = "own", "skip"
# Поля программы: (ключ, подпись, начала заголовков, по которым программа узнаёт колонку сама)
FIELDS: list[tuple[str, str, tuple[str, ...]]] = [
    ("num", "№ п/п", ("№", "n п п", "номер п п", "пп")),
    ("fio", "ФИО полностью", ("фио", "ф и о", "фамилия имя отчество", "фамилия имя", "участник", "спортсмен")),
    ("last", "Фамилия", ("фамилия",)),
    ("first", "Имя", ("имя",)),
    ("middle", "Отчество", ("отчество",)),
    ("birth", "Дата рождения", ("дата рождения", "дата рожд", "д р", "др")),
    ("birth_year", "Год рождения", ("год рождения", "год рожд", "г р", "гр", "год")),
    ("qual", "Разряд", ("разряд", "спортивная квалификация", "спорт квал", "квалификация", "звание")),
    ("sex", "Пол", ("пол",)),
    ("team", "Команда (в каждой строке)", ("команда", "делегация", "клуб", "название команды")),
    ("territory", "Территория", ("территория", "регион", "субъект", "город", "населенный пункт",
                                 "муниципальное образование")),
    ("representative", "Представитель", ("представитель", "руководитель", "тренер")),
    ("zachet", "Зачёт одной колонкой («М/Ж_3»)", ("зачет", "вид программы", "дистанция")),
    ("group", "Группа (возрастная)", ("группа", "возрастная группа")),
    ("cls", "Класс дистанции", ("класс",)),
    ("chip", "Номер чипа", ("номер чипа", "чип")),
    ("personal", "Участие в личной", ("участие в личной", "личная", "лично")),
    ("pair", "Участие в связке («м», «ж», «см»)", ("участие в дистанции связок", "участие в связке", "связка",
                                                  "связки")),
    ("pair_num", "Номер связки", ("номер связки",)),
    ("team_dist", "Участие в группе (номер группы)", ("участие в дистанции группа", "участие в группе",
                                                      "дистанция группа")),
]
FIELD_LABEL = {k: label for k, label, _ in FIELDS} | {OWN: "своя колонка — сохранить как есть",
                                                     SKIP: "пропустить"}
VALUE_FIELDS = ("qual", "sex", "group", "zachet", "personal", "pair", "team_dist")
HEAD_FIELDS = (("team", "Команда"), ("territory", "Территория"), ("representative", "Представитель"),
               ("contacts", "Телефон, e-mail"))
HEAD_LABELS = {"team": ("команда", "делегация"), "territory": ("территория", "регион"),
               "representative": ("фамилия имя отчество представителя", "представитель", "руководитель"),
               "contacts": ("контактный телефон", "телефон", "контакты")}


def norm(s) -> str:
    """Заголовок для сравнения: без регистра, «ё», знаков и лишних пробелов («Д.р.» → «д р»)."""
    s = clean_spaces(s).lower().replace("ё", "е")
    return " ".join(re.sub(r"[^\w№]+", " ", s).split())


def signature(headers: list) -> list[str]:
    out = [norm(h) for h in headers]
    while out and not out[-1]:
        out.pop()
    return out


def _starts(h: str, keys: tuple[str, ...]) -> int:
    """Насколько заголовок похож на поле: 3 — совпал, 2 — начинается так, 0 — нет."""
    best = 0
    for k in keys:
        if h == k:
            return 3
        if h.startswith(k + " ") or (len(k) > 3 and h.startswith(k)):
            best = max(best, 2)
    return best


def guess_field(header) -> str | None:
    h = norm(header)
    if not h:
        return None
    scored = [(s, -i, k) for i, (k, _, keys) in enumerate(FIELDS) if (s := _starts(h, keys))]
    return max(scored)[2] if scored else None


def guess_columns(headers: list) -> list[str]:
    """Поле для каждой колонки: стандартный бланк — как в бланке; иначе по названиям, слева направо, поле — один
    раз (второе «Группа» — номер группы в дистанции); незнакомая колонка — «своя», пустая — пропустить."""
    sig = signature(headers)
    if len(sig) >= len(COLUMNS) and all(not exp or clean_spaces(headers[c]).startswith(exp)
                                        for c, exp in COLUMNS.values()):
        std = [None] * len(sig)
        for key, (c, _) in COLUMNS.items():
            std[c] = key
        return [x or (OWN if sig[i] else SKIP) for i, x in enumerate(std)]
    used: set[str] = set()
    out = []
    for h in sig:
        f = guess_field(h)
        if f in used:
            f = {"group": "team_dist", "fio": "last"}.get(f)
        if f is None or f in used:
            out.append(OWN if h else SKIP)
            continue
        used.add(f)
        out.append(f)
    if "fio" in used and "first" in used:  # «Фамилия», «Имя», «Отчество» раздельно
        out = ["last" if x == "fio" else x for x in out]
    return out


def find_header(grid: list[list]) -> int | None:
    """Строка заголовков таблицы участников: в первых 30 строках — где больше всего узнаваемых колонок (≥ 3)."""
    best, at = 2, None
    for r, row in enumerate(grid[:30]):
        n = sum(1 for v in row if guess_field(v))
        if n > best:
            best, at = n, r
    return at


def cell_ref(r: int, c: int) -> str:
    """(3, 1) → «B4» — как ячейку называет Excel."""
    letters, n = "", c + 1
    while n:
        n, rem = divmod(n - 1, 26)
        letters = chr(65 + rem) + letters
    return f"{letters}{r + 1}"


def parse_ref(ref: str) -> tuple[int, int] | None:
    m = re.fullmatch(r"\s*([A-Za-z]{1,3})\s*(\d{1,4})\s*", str(ref or ""))
    if not m:
        return None
    c = 0
    for ch in m.group(1).upper():
        c = c * 26 + ord(ch) - 64
    return int(m.group(2)) - 1, c - 1


def _cell(grid, r, c):
    return grid[r][c] if 0 <= r < len(grid) and 0 <= c < len(grid[r]) else None


def guess_head(grid: list[list], header_row: int) -> dict[str, str]:
    """Ячейки шапки (команда, территория, представитель, телефон) над таблицей: подпись — значение под ней или
    справа."""
    out: dict[str, str] = {}
    for r in range(min(header_row, 20)):
        for c in range(len(grid[r])):
            h = norm(grid[r][c])
            for key, labels in HEAD_LABELS.items():
                if key in out or not any(h.startswith(x) for x in labels):
                    continue
                below, right = _cell(grid, r + 1, c), _cell(grid, r, c + 1)

                def label(v) -> bool:
                    return any(norm(v).startswith(x) for xs in HEAD_LABELS.values() for x in xs)

                in_row = sum(1 for v in grid[r] if label(v))
                if clean_spaces(right) and not label(right) and in_row < 2:  # «Команда:» и значение справа
                    out[key] = cell_ref(r, c + 1)
                elif r + 1 < header_row and not label(below):  # подписи строкой, значения — под ними (бланк)
                    out[key] = cell_ref(r + 1, c)
    return out


def suggest_value(field: str, raw) -> str:
    """Как программа прочитает значение сама — предложение для таблицы «как в форме → как в программе»."""
    s = clean_spaces(raw)
    if field == "qual":
        from st_secretary.qualification import parse_qual

        try:
            q = parse_qual(s)
        except Exception:  # noqa: BLE001 — незнакомое значение: пусть секретарь подскажет
            return ""
        return q.label if q is not None else ""
    if field == "sex":
        from st_secretary.textclean import normalize_sex

        return normalize_sex(s) or ""
    if field in ("personal", "team_dist"):
        low = s.lower()
        if low in ("+", "да", "x", "х", "v", "✓", "участвует"):
            return "1"
        if low in ("-", "нет", "0"):
            return ""
        return s
    if field == "pair":
        low = s.lower().replace("ё", "е")
        return {"смешанная": "см", "мужская": "м", "женская": "ж", "+": "см", "да": "см"}.get(low, s)
    return s


def map_value(form: dict, field: str, raw):
    """Значение из формы → как в программе (по таблице значений формы); нет в таблице — как есть."""
    return form.get("values", {}).get(field, {}).get(norm(raw), raw)


# ------------------------------------------------------------------ узнать форму и прочитать файл


def _sheet_rows(grids, form) -> list[tuple[str, list[list], int]]:
    """Листы, где есть строка заголовков формы: [(название, строки, номер строки заголовков)]."""
    sig = form.get("signature", [])
    want = form.get("sheets") or []
    out = []
    for name, grid in grids:
        if want and name not in want:
            continue
        at = next((r for r, row in enumerate(grid[:30]) if signature(row) == sig), None)
        if at is not None:
            out.append((name, grid, at))
    return out


def match(forms: list[dict], grids) -> tuple[dict, bool] | None:
    """Какая форма у файла: (форма, True) — шапка совпала; (форма, False) — похожа, но не совпадает."""
    similar = None
    for form in forms:
        if _sheet_rows(grids, form):
            return form, True
        sig = set(x for x in form.get("signature", []) if x)
        for _, grid in grids:
            for row in grid[:30]:
                got = set(x for x in signature(row) if x)
                if sig and got and len(sig & got) / len(sig | got) >= 0.6:
                    similar = similar or (form, False)
    return similar


_CLASS_IN_NAME = re.compile(r"(\d)\s*[- ]?\s*(?:кл|класс)", re.IGNORECASE)
_ZACHET = re.compile(r"^(.*?)[\s_\-]*(\d)$")


def read_with_form(path: str | Path, grids, form: dict) -> RawApplication:
    """Заявка по своей форме — в тех же полях, что стандартный бланк (COLUMNS) + свои колонки."""
    from st_secretary.importers.preapp_xlsx import SHEET_ROWS

    app = RawApplication(Path(path))
    app.form = form.get("name", "")
    app.team_in_row = form.get("team_in") == "row"
    cols = list(form.get("columns", []))
    headers = list(form.get("headers", []))
    sheets = _sheet_rows(grids, form)
    for idx, (name, grid, head) in enumerate(sheets):
        app.sheets.append(name)
        if idx == 0:  # шапка — на первом листе
            for key, _ in HEAD_FIELDS:
                rc = parse_ref(form.get("head", {}).get(key, ""))
                setattr(app, key, clean_spaces(_cell(grid, *rc)) if rc else "")
        sheet_class = m.group(1) if (m := _CLASS_IN_NAME.search(name)) else ""
        for r in range(head + 1, len(grid)):
            row = grid[r]
            got = {f: _cell(grid, r, c) for c, f in enumerate(cols) if f not in (OWN, SKIP)}
            first = clean_spaces(_cell(grid, r, 0)).upper()
            if first in ("ОБРАЗЕЦ", "0", "0.0") or first.startswith("ИТОГО"):
                continue
            fio = clean_spaces(got.get("fio")) or " ".join(
                clean_spaces(got.get(k)) for k in ("last", "first", "middle") if clean_spaces(got.get(k)))
            if not fio or norm(fio).startswith(("итого", "всего")):
                continue  # пустые строки бланка и строка «Итого»
            values = {key: None for key in COLUMNS}
            for key in COLUMNS:
                if key in got:
                    values[key] = got[key]
            values["fio"] = fio
            if clean_spaces(got.get("birth_year")) and not clean_spaces(got.get("birth")):
                values["birth"] = got["birth_year"]
            if clean_spaces(got.get("zachet")) and not clean_spaces(values["group"]):
                z = clean_spaces(map_value(form, "zachet", got["zachet"]))
                m = _ZACHET.match(z)
                values["group"], zc = (m.group(1).strip(" _-"), m.group(2)) if m and m.group(1).strip() else (z, "")
                values["cls"] = values["cls"] if clean_spaces(values["cls"]) else zc
            for f in VALUE_FIELDS:
                if f in values and values[f] is not None:
                    values[f] = map_value(form, f, values[f])
            if not clean_spaces(values["cls"]) and sheet_class:
                values["cls"] = sheet_class
            for key in ("team", "territory", "representative"):
                if not clean_spaces(values[key]):
                    values[key] = getattr(app, key)
            extra = {(headers[c] if c < len(headers) and clean_spaces(headers[c]) else f"колонка {c + 1}"):
                     row[c] for c, f in enumerate(cols) if f == OWN and c < len(row) and clean_spaces(row[c])}
            app.rows.append(RawRow(idx * SHEET_ROWS + r + 1, values, extra))
    if app.team_in_row:
        teams = {norm(r.values["team"]) for r in app.rows if clean_spaces(r.values["team"])}
        if len(teams) > 1:
            app.notes.append((f"заявка делегации: команд в файле — {len(teams)}; по одной команде на файл программа "
                              "делит заявку при загрузке",
                              "Перетащите этот файл в «Добавить заявки» ещё раз — программа разделит его по командам "
                              "(исходный файл сохранится в папке «Заявки делегаций (исходные)»)."))
        if not app.team:
            app.team = _most_common([clean_spaces(r.values["team"]) for r in app.rows])
    return app


def _most_common(xs: list[str]) -> str:
    xs = [x for x in xs if x]
    return Counter(xs).most_common(1)[0][0] if xs else ""


def split_by_team(app: RawApplication) -> list[RawApplication]:
    """Заявка делегации → заявки команд (команда — в каждой строке). Одна команда — заявка как есть."""
    groups: dict[str, list[RawRow]] = {}
    names: dict[str, str] = {}
    for r in app.rows:
        k = norm(r.values["team"]) or norm(app.team)
        groups.setdefault(k, []).append(r)
        names.setdefault(k, clean_spaces(r.values["team"]) or app.team)
    if len(groups) < 2:
        return [app]
    out = []
    for k, rows in groups.items():
        part = RawApplication(app.path, names[k], _most_common([clean_spaces(r.values["territory"]) for r in rows])
                              or app.territory,
                              _most_common([clean_spaces(r.values["representative"]) for r in rows])
                              or app.representative, app.contacts, len(rows), rows)
        out.append(part)
    return out


def standard_rows(app: RawApplication) -> list[dict]:
    """Строки для записи в стандартный бланк (write_preapplication): поля бланка и свои колонки."""
    skip = ("num", "team", "territory", "representative")
    return [{**{k: v for k, v in r.values.items() if k not in skip}, "extra": dict(r.extra)} for r in app.rows]


def sample_values(grids, form: dict, limit: int = 40) -> dict[str, list[str]]:
    """Разные значения колонок формы в образце — для таблицы «как в форме → как в программе»."""
    cols = list(form.get("columns", []))
    out: dict[str, list[str]] = {}
    for _, grid, head in _sheet_rows(grids, form):
        for r in range(head + 1, len(grid)):
            first = clean_spaces(_cell(grid, r, 0)).upper()
            if first in ("ОБРАЗЕЦ", "0", "0.0"):
                continue
            for c, f in enumerate(cols):
                v = clean_spaces(_cell(grid, r, c))
                if f in VALUE_FIELDS and v and v not in out.setdefault(f, []) and len(out[f]) < limit:
                    out[f].append(v)
    return out


def check(form: dict) -> list[str]:
    """Чего не хватает форме: ФИО, дата или год рождения, зачёт (или группа и класс), команда."""
    cols = set(form.get("columns", []))
    out = []
    if "fio" not in cols and "last" not in cols:
        out.append("ФИО (одной колонкой или «Фамилия» + «Имя» + «Отчество»)")
    if "birth" not in cols and "birth_year" not in cols:
        out.append("дата или год рождения")
    if "zachet" not in cols and "group" not in cols:
        out.append("зачёт (одной колонкой) или группа")
    if form.get("team_in") == "row" and "team" not in cols:
        out.append("команда в каждой строке — колонка «Команда»")
    if form.get("team_in") != "row" and not form.get("head", {}).get("team") and "team" not in cols:
        out.append("команда — ячейка шапки или колонка «Команда»")
    return out
