"""Карточка соревнования в Excel: шаблон с выпадающими списками и чтение заполненного файла.

Строки ищутся по тексту в колонке «Параметр», поэтому порядок строк можно менять, а лишние
строки-пояснения не мешают. Колонки на листах «ГСК» и «Зачёты» — по заголовкам.
"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.worksheet.datavalidation import DataValidation

from st_secretary.competition import (
    GSK_ROLES,
    JUDGE_CATEGORIES,
    KINDS,
    LEVEL_LABELS,
    PERCENT_LABELS,
    PERCENT_OLD_LABELS,
    UNIT_KINDS,
    Competition,
    Official,
    Zachet,
)
from st_secretary.issues import ERROR, Issue
from st_secretary.qualification import parse_qual
from st_secretary.reference import all_norm_editions, discipline_by_name, disciplines
from st_secretary.textclean import clean_spaces

INPUT_FILL = PatternFill("solid", fgColor="FFF2CC")  # жёлтые ячейки — для ввода, как в СЕКРЕТАРЬ_ST
HEAD_FILL = PatternFill("solid", fgColor="DCE6F1")
BOLD = Font(bold=True)
THIN = Side(style="thin", color="A6A6A6")
BOX = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
WRAP = Alignment(wrap_text=True, vertical="top")

# (подпись в колонке «Параметр», ключ, подсказка)
MAIN_FIELDS = [
    ("Наименование соревнований", "title", "Полностью, как в Положении: «Чемпионат г. Красноярска по спортивному туризму»"),
    ("Вид соревнований", "kind", "Выберите из списка"),
    ("Уровень (статус)", "level", "Влияет на разряды: I — не ниже соревнований субъекта РФ, II — не ниже первенства МО"),
    ("Дата начала", "date_from", "Формат дд.мм.гггг"),
    ("Дата окончания", "date_to", "Формат дд.мм.гггг"),
    ("Место проведения", "place", "Как в шапке протоколов: «окрестности г. Красноярска»"),
    ("Территория организаторов", "host_territory", "Населённый пункт, как его писать в протоколах: «Красноярск»"),
    ("Проводящие организации", "organizers", "Каждую — с новой строки (Alt+Enter) или через «;»"),
    ("Номер в календарном плане", "calendar_number", "ЕКП или календарь субъекта/МО, если есть"),
    ("Редакция разрядных норм", "norms_edition", "Какие нормы действуют на этих соревнованиях"),
    ("Методика «% от победителя»", "percent_method",
     "Для балльных дисциплин (ПСР, горные). «Не задана» — нормативы не считаются"),
    ("Приём предзаявок до", "preapp_deadline", "Дата окончания приёма предварительных заявок"),
    ("Неофициальные соревнования", "unofficial",
     "«да» — клубные, учебные, слёт: свои зачёты и дисциплины, без ранга и разрядов; пусто — официальные"),
    ("Участник — только в одном классе", "one_class",
     "«да» — по Положению спортсмен выступает только в одном классе дистанции (проверка заявок); пусто — в любых"),
]
YES = ("да", "yes", "1", "true", "+", "x", "х", "истина")
RESULT_WORDS = {"points": "баллы", "time": "время", "time_points": "время + баллы"}
UNIT_WORDS = dict(UNIT_KINDS)

ZACHET_COLUMNS = [
    ("Группа", "как в предзаявке и в СЕКРЕТАРЬ_ST: М/Ж, МУЖЧИНЫ, ЮН/ДЕВ…"),
    ("Класс", "1–6; у неофициальных 0 — без класса"),
    ("Дисциплина (ВРВС)", "выберите из списка"),
    ("Возраст от", "лет в год соревнований"),
    ("Возраст до", "пусто — без ограничения"),
    ("По решению ГСК с (лет)", "если Положение разрешает младше по решению ГСК"),
    ("Разряд не ниже", "б/р, 3ю … МС"),
    ("Состав команды", "человек"),
    ("Мужчин не менее", ""),
    ("Женщин не менее", ""),
    ("Взнос, ₽", ""),
    ("Взнос за", "команду / участника"),
    ("Название зачёта", "своё (неофициальные): «Новички»; пусто — группа_класс"),
    ("Код зачёта", "ставит программа — не менять: по нему хранятся данные зачёта"),
    ("Своя дисциплина", "неофициальные: если нет в ВРВС (тогда «Дисциплина (ВРВС)» пусто)"),
    ("Результат", "у своей дисциплины: баллы / время / время + баллы"),
    ("Состав", "у своей дисциплины: личный / связка / команда"),
]

GSK_COLUMNS = ["Должность", "ФИО полностью", "Категория", "Территория"]


def _list_sheet(wb: Workbook) -> dict[str, str]:
    """Скрытый лист со списками для выпадающих меню. Возвращает диапазоны."""
    ws = wb.create_sheet("Списки")
    lists = {
        "kind": list(KINDS),
        "level": list(LEVEL_LABELS.values()),
        "norms": list(all_norm_editions()),
        "percent": list(PERCENT_LABELS.values()),
        "disc": [d.name for d in disciplines() if d.group != "маршрут"],
        "cat": list(JUDGE_CATEGORIES),
        "qual": ["б/р", "3ю", "2ю", "1ю", "III", "II", "I", "КМС", "МС"],
        "fee": ["команду", "участника"],
        "result": list(RESULT_WORDS.values()),
        "unit": list(UNIT_WORDS.values()),
    }
    ranges = {}
    for col, (key, values) in enumerate(lists.items(), start=1):
        letter = ws.cell(1, col).column_letter
        for row, v in enumerate(values, start=1):
            ws.cell(row, col, v)
        ranges[key] = f"Списки!${letter}$1:${letter}${len(values)}"
    ws.sheet_state = "hidden"
    return ranges


def _dropdown(ws, rng: str, cells: str) -> None:
    dv = DataValidation(type="list", formula1=f"={rng}", allow_blank=True, showErrorMessage=True,
                        errorTitle="Значение не из списка", error="Выберите значение из выпадающего списка")
    ws.add_data_validation(dv)
    dv.add(cells)


def write_card(path: str | Path, comp: Competition | None = None) -> Path:
    """Создать карточку: пустую (шаблон) или заполненную данными comp."""
    wb = Workbook()
    ws = wb.active
    ws.title = "Карточка"
    ranges = _list_sheet(wb)
    ws.append(["Параметр", "Значение", "Подсказка"])
    for c in ws[1]:
        c.font, c.fill, c.border = BOLD, HEAD_FILL, BOX
    values = _main_values(comp) if comp else {}
    for label, key, hint in MAIN_FIELDS:
        ws.append([label, values.get(key), hint])
        r = ws.max_row
        ws.cell(r, 2).fill = INPUT_FILL
        for c in ws[r]:
            c.border, c.alignment = BOX, WRAP
        if key in ("date_from", "date_to", "preapp_deadline"):
            ws.cell(r, 2).number_format = "DD.MM.YYYY"
        dd = {"kind": "kind", "level": "level", "norms_edition": "norms", "percent_method": "percent"}.get(key)
        if dd:
            _dropdown(ws, ranges[dd], f"B{r}")
    ws.column_dimensions["A"].width = 30
    ws.column_dimensions["B"].width = 55
    ws.column_dimensions["C"].width = 70

    g = wb.create_sheet("ГСК")
    g.append(GSK_COLUMNS)
    officials = {o.role: o for o in comp.officials} if comp else {}
    for role in GSK_ROLES:
        o = officials.get(role)
        g.append([role, o.fio if o else None, o.category if o else None, o.territory if o else None])
    for o in (comp.officials if comp else []):
        if o.role not in GSK_ROLES:
            g.append([o.role, o.fio, o.category, o.territory])
    _style_table(g, [42, 40, 12, 28])
    _dropdown(g, ranges["cat"], f"C2:C{max(g.max_row, 40)}")

    z = wb.create_sheet("Зачёты")
    z.append([c for c, _ in ZACHET_COLUMNS])
    z.append([h for _, h in ZACHET_COLUMNS])
    for cell in z[2]:
        cell.font = Font(italic=True, color="7F7F7F", size=9)
    for zz in (comp.zachety if comp else []):
        z.append([zz.group, zz.distance_class, None if zz.is_custom else zz.discipline_name, zz.age_from, zz.age_to,
                  zz.age_from_by_gsk, zz.min_qual.label, zz.team_size, zz.min_men or None, zz.min_women or None,
                  zz.fee, zz.fee_per, zz.name or None, zz.zid or None, zz.discipline_text or None,
                  RESULT_WORDS.get(zz.result), UNIT_WORDS.get(zz.unit)])
    _style_table(z, [12, 8, 42, 11, 11, 14, 12, 12, 12, 12, 10, 12, 22, 16, 28, 14, 12], input_from_row=3, rows=20)
    _dropdown(z, ranges["disc"], "C3:C30")
    _dropdown(z, ranges["qual"], "G3:G30")
    _dropdown(z, ranges["fee"], "L3:L30")
    _dropdown(z, ranges["result"], "P3:P30")
    _dropdown(z, ranges["unit"], "Q3:Q30")

    wb.move_sheet("Списки", offset=10)
    path = Path(path)
    wb.save(path)
    return path


def _style_table(ws, widths, input_from_row=2, rows=None):
    for c in ws[1]:
        c.font, c.fill, c.border, c.alignment = BOLD, HEAD_FILL, BOX, WRAP
    last = max(ws.max_row, (input_from_row + rows - 1) if rows else ws.max_row)
    for r in range(input_from_row, last + 1):
        for col in range(1, len(widths) + 1):
            cell = ws.cell(r, col)
            cell.border = BOX
            if not (ws.title == "ГСК" and col == 1):
                cell.fill = INPUT_FILL
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[ws.cell(1, i).column_letter].width = w
    ws.freeze_panes = "A2"


def _main_values(c: Competition) -> dict:
    return {
        "title": c.title, "kind": c.kind, "level": LEVEL_LABELS[c.level], "date_from": c.date_from,
        "date_to": c.date_to, "place": c.place, "host_territory": c.host_territory,
        "organizers": "\n".join(c.organizers), "calendar_number": c.calendar_number or None,
        "norms_edition": c.norms_edition, "percent_method": PERCENT_LABELS[c.percent_method],
        "preapp_deadline": c.preapp_deadline, "unofficial": "да" if c.unofficial else None,
        "one_class": "да" if c.one_class else None,
    }


# ------------------------------------------------------------------ чтение


class CardError(Exception):
    def __init__(self, issues: list[Issue]):
        self.issues = issues
        super().__init__("; ".join(i.text for i in issues))


def _as_date(v) -> date | None:
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    s = clean_spaces(v)
    for fmt in ("%d.%m.%Y", "%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    raise ValueError(f"не удалось распознать дату «{s}»")


def _as_int(v) -> int | None:
    if v is None or clean_spaces(v) == "":
        return None
    return int(float(str(v).replace(",", ".")))


def load_card(path: str | Path) -> Competition:
    """Прочитать карточку. Ошибки заполнения — CardError со списком замечаний."""
    wb = load_workbook(Path(path), data_only=True)
    problems: list[Issue] = []
    src = f"Карточка ({Path(path).name})"
    ws = wb["Карточка"]
    by_label = {clean_spaces(r[0].value): r[1].value for r in ws.iter_rows(min_row=2) if r[0].value}
    raw = {key: by_label.get(label) for label, key, _ in MAIN_FIELDS}

    def need(key, label):
        v = raw.get(key)
        if v is None or clean_spaces(v) == "":
            problems.append(Issue(ERROR, f"не заполнено поле «{label}»", source=src, field=label))
        return v

    for label, key, _ in MAIN_FIELDS:
        if key in ("title", "kind", "level", "date_from", "date_to", "place", "host_territory", "norms_edition"):
            need(key, label)
    level = {v: k for k, v in LEVEL_LABELS.items()}.get(clean_spaces(raw["level"]))
    if raw["level"] and level is None:
        problems.append(Issue(ERROR, f"уровень «{raw['level']}» не из списка", source=src, field="Уровень"))
    percent = ({v: k for k, v in PERCENT_LABELS.items()} | PERCENT_OLD_LABELS).get(
        clean_spaces(raw["percent_method"] or "Не задана"), "?")
    if percent == "?":
        problems.append(Issue(ERROR, f"методика «{raw['percent_method']}» не из списка", source=src, field="Методика"))
        percent = None
    dates = {}
    for key, label in (("date_from", "Дата начала"), ("date_to", "Дата окончания"), ("preapp_deadline", "Приём предзаявок до")):
        try:
            dates[key] = _as_date(raw[key])
        except ValueError as e:
            problems.append(Issue(ERROR, f"{label}: {e}", source=src, field=label))
            dates[key] = None

    officials = []
    for r in wb["ГСК"].iter_rows(min_row=2, values_only=True):
        role, fio, cat, terr = (clean_spaces(x) for x in (list(r) + [None] * 4)[:4])
        if role and fio:
            officials.append(Official(role, fio, cat, terr))

    zachety = []
    zs = wb["Зачёты"]
    head = [clean_spaces(c.value) for c in zs[1]]
    col = {name: head.index(name) for name, _ in ZACHET_COLUMNS if name in head}
    def value(row, name):
        return row[col[name]] if name in col and col[name] < len(row) else None

    for i, row in enumerate(zs.iter_rows(min_row=3, values_only=True), start=3):
        def cell(name, row=row):
            return value(row, name)
        group = clean_spaces(cell("Группа"))
        if not group:
            continue
        where = f"Зачёты, строка {i}"
        try:
            own = clean_spaces(cell("Своя дисциплина"))
            vrvs = clean_spaces(cell("Дисциплина (ВРВС)"))
            code = "" if own and not vrvs else discipline_by_name(vrvs).code
            result = {v: k for k, v in RESULT_WORDS.items()}.get(clean_spaces(cell("Результат")).lower(), "")
            unit = {v: k for k, v in UNIT_WORDS.items()}.get(clean_spaces(cell("Состав")).lower(), "")
            zachety.append(Zachet(
                group=group,
                distance_class=_as_int(cell("Класс")) or 0,
                discipline_code=code,
                age_from=_as_int(cell("Возраст от")),
                age_to=_as_int(cell("Возраст до")),
                age_from_by_gsk=_as_int(cell("По решению ГСК с (лет)")),
                min_qual=parse_qual(cell("Разряд не ниже")),
                team_size=_as_int(cell("Состав команды")),
                min_men=_as_int(cell("Мужчин не менее")) or 0,
                min_women=_as_int(cell("Женщин не менее")) or 0,
                fee=_as_int(cell("Взнос, ₽")),
                fee_per=clean_spaces(cell("Взнос за")) or "команду",
                name=clean_spaces(cell("Название зачёта")), zid=clean_spaces(cell("Код зачёта")),
                discipline_text=own if not code else "", result=result if not code else "",
                unit=unit if not code else "",
            ))
        except (KeyError, ValueError) as e:
            problems.append(Issue(ERROR, f"{where}: {e}", source=src, field="Зачёты"))

    if problems:
        raise CardError(problems)
    organizers = [clean_spaces(x) for x in str(raw["organizers"] or "").replace(";", "\n").split("\n") if clean_spaces(x)]
    return Competition(
        title=clean_spaces(raw["title"]),
        kind=clean_spaces(raw["kind"]),
        level=level,
        date_from=dates["date_from"],
        date_to=dates["date_to"],
        place=clean_spaces(raw["place"]),
        host_territory=clean_spaces(raw["host_territory"]),
        organizers=organizers,
        calendar_number=clean_spaces(raw["calendar_number"]),
        unofficial=clean_spaces(raw.get("unofficial")).lower() in YES,
        one_class=clean_spaces(raw.get("one_class")).lower() in YES,
        norms_edition=clean_spaces(raw["norms_edition"]),
        percent_method=percent,
        preapp_deadline=dates["preapp_deadline"],
        officials=officials,
        zachety=zachety,
    )
