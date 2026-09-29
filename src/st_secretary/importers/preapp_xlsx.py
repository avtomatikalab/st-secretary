"""Чтение и запись предварительных заявок по бланку БЛАНК_ПРЕД_ЗАЯВКИ_ST (xlsx или xls).

Бланк: лист «Заявка»; строка с заголовками «Команда | Территория | Представитель | Контакты |
Кол-во участников» и строка значений под ней; таблица участников с заголовком «№ п/п» — колонки
A–O те же, что на листе «Заявка» книги СЕКРЕТАРЬ_ST. Строка «ОБРАЗЕЦ» и пример под ней пропускаются.

Заявка, исправленная в программе, записывается в ту же раскладку (write_preapplication) — её можно
открыть в Excel, и программа читает её так же, как файлы команд.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

from st_secretary.textclean import clean_spaces

# Колонки таблицы участников (как в бланке и в СЕКРЕТАРЬ_ST): ключ → (номер колонки, ожидаемое начало заголовка)
COLUMNS = {
    "num": (0, "№"),
    "team": (1, "Команда"),
    "territory": (2, "Территория"),
    "representative": (3, "Представитель"),
    "fio": (4, "Фамилия"),
    "birth": (5, "Дата"),
    "qual": (6, "Разряд"),
    "sex": (7, "Пол"),
    "group": (8, "Группа"),
    "cls": (9, "Класс"),
    "chip": (10, "Номер чипа"),
    "personal": (11, "Участие в личной"),
    "pair": (12, "Участие в дистанции связок"),
    "pair_num": (13, "Номер связки"),
    "team_dist": (14, ""),  # «Участие в дистанции-группа» или просто «Группа»
}

BLANK_HOW = ("Программа ищет лист «Заявка» (или первый лист), строку заголовков «Команда | Территория | …» "
             "и таблицу участников с заголовком «№ п/п … Фамилия, имя» — как в бланке предзаявки.")


@dataclass
class RawRow:
    row: int  # номер строки в файле (как видит человек)
    values: dict[str, object]


@dataclass
class RawApplication:
    path: Path
    team: str = ""
    territory: str = ""
    representative: str = ""
    contacts: str = ""
    declared_count: object = None
    rows: list[RawRow] = field(default_factory=list)
    problems: list[tuple[str, str]] = field(default_factory=list)  # (что не так, почему) — файл не прочитан как бланк


def _grid(path: Path) -> list[list[object]]:
    """Лист «Заявка» (или первый) как список строк значений."""
    if path.suffix.lower() == ".xls":
        import xlrd  # необязательная зависимость

        book = xlrd.open_workbook(str(path))
        names = book.sheet_names()
        sh = book.sheet_by_name("Заявка") if "Заявка" in names else book.sheet_by_index(0)
        grid = []
        for r in range(sh.nrows):
            row = []
            for c in range(sh.ncols):
                cell = sh.cell(r, c)
                v = cell.value
                if cell.ctype == xlrd.XL_CELL_DATE:
                    v = xlrd.xldate.xldate_as_datetime(v, book.datemode)
                row.append(v)
            grid.append(row)
        return grid
    from openpyxl import load_workbook

    wb = load_workbook(path, data_only=True, read_only=True)
    ws = wb["Заявка"] if "Заявка" in wb.sheetnames else wb.worksheets[0]
    grid = [list(r) for r in ws.iter_rows(values_only=True)]
    wb.close()
    return grid


def read_preapplication(path: str | Path) -> RawApplication:
    path = Path(path)
    app = RawApplication(path)
    try:
        grid = _grid(path)
    except Exception as e:  # noqa: BLE001 — любой нечитаемый файл должен попасть в отчёт, а не уронить обработку
        app.problems.append((f"файл не открывается: {e}",
                             "Файл повреждён, защищён паролем или это не таблица Excel."))
        return app

    def cell(r, c):
        return grid[r][c] if r < len(grid) and c < len(grid[r]) else None

    head_team = next((r for r in range(min(len(grid), 15)) if clean_spaces(cell(r, 1)) == "Команда"
                      and clean_spaces(cell(r, 2)).startswith("Территория")), None)
    head = next((r for r in range(min(len(grid), 25)) if clean_spaces(cell(r, 0)).startswith("№")
                 and clean_spaces(cell(r, 4)).startswith("Фамилия")), None)
    if head is None:
        app.problems.append(("не найдена таблица участников (строка заголовков «№ п/п … Фамилия, имя»)",
                             f"Похоже, заявка заполнена не по бланку. {BLANK_HOW}"))
        return app
    for c, expected in COLUMNS.values():
        if expected and not clean_spaces(cell(head, c)).startswith(expected):
            app.problems.append((f"колонка {chr(65 + c)}: ожидался заголовок «{expected}…», "
                                 f"а в файле «{clean_spaces(cell(head, c))}» — бланк изменён",
                                 "Колонки бланка сдвинуты или переименованы. Если читать такой файл, данные попадут "
                                 "не в те графы — поэтому программа его не обрабатывает."))
    if app.problems:
        return app
    if head_team is not None and head_team + 1 < head:
        r = head_team + 1
        app.team, app.territory, app.representative = (clean_spaces(cell(r, c)) for c in (1, 2, 3))
        app.contacts = clean_spaces(cell(r, 4))
        app.declared_count = cell(r, 5)
    for r in range(head + 1, len(grid)):
        first = clean_spaces(cell(r, 0))
        if first.upper() == "ОБРАЗЕЦ" or first == "0":
            continue  # строка «ОБРАЗЕЦ» и пример под ней
        values = {key: cell(r, c) for key, (c, _) in COLUMNS.items()}
        if not clean_spaces(values["fio"]):
            continue  # пустые строки бланка (№ проставлен заранее)
        app.rows.append(RawRow(r + 1, values))
    return app


# ------------------------------------------------------------------ запись

HEAD_LABELS = ["Команда", "Территория", "Фамилия, имя, отчество представителя",
               "Контактный телефон, адрес эл. почты", "Кол-во участников в делегации"]
TABLE_LABELS = ["№ п/п", "Команда", "Территория", "Представитель", "Фамилия, имя, отчество", "Дата рождения",
                "Разряд по СТ", "Пол", "Группа", "Класс дистанции", "Номер чипа (указать, если чип свой)",
                "Участие в личной дистанции", "Участие в дистанции связок", "Номер связки (если больше одной связки)",
                "Участие в дистанции-группа (номер группы)"]
TABLE_ROW = 6  # строка заголовков таблицы участников; участники — с 7-й
FIRST_ROW = TABLE_ROW + 1


def _value(v):
    """Числа — числами, даты — датами (как их ввёл бы человек в Excel), остальное — текстом."""
    if isinstance(v, (date, datetime, int)):
        return v
    s = clean_spaces(v)
    if not s:
        return None
    return int(s) if s.isdigit() and len(s) < 10 and not (len(s) > 1 and s[0] == "0") else s


def write_preapplication(path: str | Path, head: dict, rows: list[dict], note: str = "",
                         qual_labels: list[str] | None = None) -> Path:
    """Заявка в раскладке бланка. head — team, territory, representative, contacts, declared;
    rows — ключи COLUMNS без num/team/territory/representative (их программа проставляет из шапки)."""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.worksheet.datavalidation import DataValidation

    wb = Workbook()
    ws = wb.active
    ws.title = "Заявка"
    ws["A1"] = "Предварительная заявка"
    ws["A1"].font = Font(bold=True, size=14)
    if note:
        ws["A2"] = note
        ws["A2"].font = Font(italic=True, color="5B6671")
    fill = PatternFill("solid", fgColor="DCE6F1")
    thin = Side(style="thin", color="A6A6A6")
    box = Border(left=thin, right=thin, top=thin, bottom=thin)
    wrap = Alignment(wrap_text=True, vertical="top")
    for c, label in enumerate(HEAD_LABELS, start=2):
        h = ws.cell(3, c, label)
        h.font, h.fill, h.border, h.alignment = Font(bold=True), fill, box, wrap
        ws.cell(4, c, _value(head.get(("team", "territory", "representative", "contacts", "declared")[c - 2])))
        ws.cell(4, c).border = box
    for c, label in enumerate(TABLE_LABELS, start=1):
        h = ws.cell(TABLE_ROW, c, label)
        h.font, h.fill, h.border, h.alignment = Font(bold=True), fill, box, wrap
    common = {"team": head.get("team", ""), "territory": head.get("territory", ""),
              "representative": head.get("representative", "")}
    for n, row in enumerate(rows, start=1):
        r = FIRST_ROW + n - 1
        values = {**row, **common, "num": n}
        for key, (c, _) in COLUMNS.items():
            cell = ws.cell(r, c + 1, _value(values.get(key)))
            cell.border = box
            if isinstance(cell.value, (date, datetime)):
                cell.number_format = "DD.MM.YYYY"
    last = max(FIRST_ROW + len(rows) + 20, 40)
    if qual_labels:
        dv = DataValidation(type="list", formula1='"' + ",".join(qual_labels) + '"', allow_blank=True)
        dv.add(f"G{FIRST_ROW}:G{last}")
        ws.add_data_validation(dv)
    sex = DataValidation(type="list", formula1='"м,ж"', allow_blank=True)
    sex.add(f"H{FIRST_ROW}:H{last}")
    ws.add_data_validation(sex)
    for c, w in zip("ABCDEFGHIJKLMNO", [6, 18, 16, 30, 34, 13, 9, 6, 10, 10, 14, 12, 12, 12, 14]):
        ws.column_dimensions[c].width = w
    ws.row_dimensions[TABLE_ROW].height = 45
    ws.freeze_panes = f"A{FIRST_ROW}"
    path = Path(path)
    wb.save(path)
    return path
