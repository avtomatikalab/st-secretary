"""Форма заявки команды ↔ файл заявки по бланку.

В форму подставляется то, как программа поняла заявку: исправленное системой — уже исправленным
(ФИО, даты, разряды), а то, что понять не удалось, — как написано в файле, чтобы человек увидел и поправил.
"""

from __future__ import annotations

import re
from collections import defaultdict

from st_secretary.competition import Competition
from st_secretary.importers.preapp_xlsx import RawApplication
from st_secretary.issues import ERROR, FIXED, SEVERITY_ORDER, WARNING, Issue
from st_secretary.preapp import Entry, TeamApplication
from st_secretary.qualification import Qual
from st_secretary.reference import discipline_by_code
from st_secretary.textclean import clean_spaces, parse_birth_date

HEAD_FIELDS = ["team", "territory", "representative", "contacts", "declared"]
ROW_FIELDS = ["fio", "birth", "qual", "sex", "zachet", "chip", "personal", "pair", "pair_num", "team_dist"]
EMPTY_ROWS = 3  # столько пустых строк у новой заявки

# Поле замечания (Issue.field) → поля формы, которые подсветить
ISSUE_INPUTS = {
    "ФИО": ("fio",), "Дубль": ("fio",), "Дата рождения": ("birth",), "Возраст": ("birth",),
    "Допуск": ("birth", "qual"), "Разряд": ("qual",), "Пол": ("sex",), "Группа": ("zachet",), "Класс": ("zachet",),
    "Группа/класс": ("zachet",), "Участие в группе": ("team_dist",), "Участие": ("personal", "pair", "team_dist"),
    "Команда": ("team",), "Территория": ("territory",), "Контакты": ("contacts",), "Кол-во участников": ("declared",),
}


def zachet_value(group: str, cls) -> str:
    """Зачёт так, как его пишет СЕКРЕТАРЬ_ST: «М/Ж_3»."""
    cls = "" if cls is None else str(cls)
    return f"{group}_{cls}" if group or cls else ""


def split_zachet(value: str) -> tuple[str, str]:
    group, _, cls = value.rpartition("_") if "_" in value else (value, "", "")
    return group, cls


def empty_row(comp: Competition | None = None) -> dict:
    """Пустая строка участника. Если в соревновании один зачёт — он уже выбран; если все дистанции
    командные — отмечено участие в группе 1 (так сделала бы и проверка)."""
    row = {"row": 0, **{f: "" for f in ROW_FIELDS}}
    if comp is not None and len(comp.zachety) == 1:
        row["zachet"] = comp.zachety[0].key
    if comp is not None and comp.zachety and all(
            discipline_by_code(z.discipline_code).rank_format == "group" for z in comp.zachety):
        row["team_dist"] = "1"
    return row


def _is_blank(r: dict) -> bool:
    """Строку никто не заполнял (подставленные зачёт и отметка группы не в счёт)."""
    return not any(r[f] for f in ROW_FIELDS if f not in ("zachet", "team_dist"))


def _entry_row(e: Entry, v: dict) -> dict:
    if e.birth:
        birth = f"{e.birth:%d.%m.%Y}"
    elif e.birth_year:
        birth = str(e.birth_year)
    else:
        birth = clean_spaces(v.get("birth")) or (e.name.extracted_birth or "")
    cls = e.distance_class if e.distance_class is not None else clean_spaces(v.get("cls"))
    return {
        "row": e.row,
        "fio": e.name.full or clean_spaces(v.get("fio")),
        "birth": birth,
        "qual": e.qual.label if e.qual is not None else clean_spaces(v.get("qual")),
        "sex": e.sex or clean_spaces(v.get("sex")),
        "zachet": e.zachet.key if e.zachet else zachet_value(e.group, cls),
        "chip": e.chip, "personal": e.personal, "pair": e.pair, "pair_num": e.pair_num, "team_dist": e.team_dist,
    }


def app_to_form(raw: RawApplication | None, team: TeamApplication | None, comp: Competition | None = None) -> dict:
    """Форма по файлу заявки и тому, как её поняла программа. Нет файла — пустая форма."""
    if raw is None:
        return {"head": {f: "" for f in HEAD_FIELDS}, "rows": [empty_row(comp) for _ in range(EMPTY_ROWS)]}
    head = {"team": clean_spaces(raw.team), "territory": clean_spaces(raw.territory),
            "representative": clean_spaces(raw.representative), "contacts": clean_spaces(raw.contacts),
            "declared": clean_spaces(raw.declared_count)}
    rows = []
    if team is not None:
        head |= {"team": team.team, "territory": team.territory, "representative": team.representative}
        values = {r.row: r.values for r in raw.rows}
        rows = [_entry_row(e, values.get(e.row, {})) for e in team.entries]
    return {"head": head, "rows": rows or [empty_row(comp) for _ in range(EMPTY_ROWS)]}


def form_from_data(data) -> dict:
    """Поля, отправленные браузером: h-<поле> — шапка, p-<i>-<поле> — строки участников."""
    head = {f: clean_spaces(data.get(f"h-{f}", "")) for f in HEAD_FIELDS}
    idx = sorted({int(m.group(1)) for k in data if (m := re.fullmatch(r"p-(\d+)-\w+", k))})
    rows = [{"row": 0, **{f: clean_spaces(data.get(f"p-{i}-{f}", "")) for f in ROW_FIELDS}} for i in idx]
    return {"head": head, "rows": rows}


def _birth_value(s: str):
    """Дата — датой, год — числом; то, что датой не является, остаётся текстом (проверка покажет ошибку)."""
    if not s:
        return ""
    pd = parse_birth_date(s)
    return pd.value or pd.year_only or s


def form_to_file(form: dict) -> tuple[dict, list[dict], dict[str, str]]:
    """(шапка, строки участников для записи в файл, ошибки формы по именам полей)."""
    errors: dict[str, str] = {}
    head = dict(form["head"])
    if not head["team"]:
        errors["h-team"] = "Впишите название команды"
    rows = []
    for i, r in enumerate(form["rows"]):
        if _is_blank(r):
            continue  # пустая строка — просто не сохраняется
        if not r["fio"]:
            errors[f"p-{i}-fio"] = "Впишите ФИО или удалите строку"
            continue
        group, cls = split_zachet(r["zachet"])
        rows.append({"fio": r["fio"], "birth": _birth_value(r["birth"]), "qual": r["qual"], "sex": r["sex"],
                     "group": group, "cls": cls, "chip": r["chip"], "personal": r["personal"], "pair": r["pair"],
                     "pair_num": r["pair_num"], "team_dist": r["team_dist"]})
    if not rows and not errors:
        errors["_rows"] = "В заявке нет ни одного участника — впишите хотя бы одного."
    return head, rows, errors


def columns(comp: Competition, rows: list[dict]) -> dict[str, bool]:
    """Какие колонки участия показывать: по дисциплинам соревнования и по тому, что уже заполнено."""
    formats = {discipline_by_code(z.discipline_code).rank_format for z in comp.zachety}
    filled = {f for r in rows for f in ("personal", "pair", "pair_num", "team_dist") if r.get(f)}
    return {"personal": "individual" in formats or "personal" in filled,
            "pair": "pair" in formats or bool(filled & {"pair", "pair_num"}),
            "team_dist": bool(formats & {"group", "crew"}) or "team_dist" in filled}


def choices(comp: Competition, form: dict) -> dict:
    """Списки для полей и какие колонки участия показывать."""
    return {
        "quals": [(q.label, q.label) for q in Qual],
        "sexes": [("м", "м"), ("ж", "ж")],
        "zachety": [(z.key, f"{z.group}, {z.distance_class} кл.") for z in comp.zachety],
        "show": columns(comp, form["rows"]),
    }


def issue_marks(issues: list[Issue]) -> tuple[list[Issue], dict[int, list[Issue]], dict]:
    """Замечания к шапке и по строкам файла (только ошибки и «проверить» — исправленное системой уже
    стоит в полях) и подсветка полей: {строка: {поле: {"sev", "title"}}}, строка 0 — шапка."""
    head, by_row = [], defaultdict(list)
    marks: dict[int, dict[str, dict]] = defaultdict(dict)
    for i in sorted(issues, key=lambda i: SEVERITY_ORDER[i.severity]):
        if i.severity in (ERROR, WARNING):
            (by_row[i.row] if i.row else head).append(i)
        if i.severity not in (ERROR, WARNING, FIXED):
            continue
        title = f"Исправлено системой, в файле было: «{i.before or 'пусто'}»" if i.severity == FIXED else i.text
        for name in ISSUE_INPUTS.get(i.field, ()):
            cur = marks[i.row].get(name)
            if cur is None:
                marks[i.row][name] = {"sev": i.severity, "title": title}
            elif cur["sev"] == i.severity:
                cur["title"] += "\n" + title
    return head, dict(by_row), dict(marks)
