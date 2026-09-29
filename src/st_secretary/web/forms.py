"""Карточка соревнования в браузере: поля формы ↔ Competition.

Форма всегда перерисовывается из того, что ввёл человек: при ошибке ничего не теряется.
Ошибки показываются у самого поля (ключ — имя поля формы) и списком над формой.
"""

from __future__ import annotations

from datetime import date, datetime

from st_secretary.competition import (
    GSK_ROLES,
    JUDGE_CATEGORIES,
    KINDS,
    LEVEL_LABELS,
    PERCENT_LABELS,
    Competition,
    Official,
    Zachet,
)
from st_secretary.norms import PercentMethod
from st_secretary.qualification import Qual, parse_qual
from st_secretary.reference import Level, disciplines, norm_editions
from st_secretary.textclean import clean_spaces

MAIN_FIELDS = ["title", "kind", "level", "date_from", "date_to", "place", "host_territory", "organizers",
               "calendar_number", "norms_edition", "percent_method", "preapp_deadline"]
REQUIRED = {"title": "Наименование", "kind": "Вид", "level": "Уровень", "date_from": "Дата начала",
            "date_to": "Дата окончания", "place": "Место проведения",
            "host_territory": "Территория организаторов", "norms_edition": "Редакция норм"}
OFFICIAL_FIELDS = ["role", "fio", "category", "territory"]
ZACHET_FIELDS = ["group", "distance_class", "discipline_code", "age_from", "age_to", "age_from_by_gsk",
                 "min_qual", "team_size", "min_men", "min_women", "fee", "fee_per"]
QUAL_LABELS = [q.label for q in Qual]
FEE_PER = ("команду", "участника")
GROUP_SUGGESTIONS = ("М/Ж", "МУЖЧИНЫ", "ЖЕНЩИНЫ", "ЮНИОРЫ", "ЮНИОРКИ", "ЮНОШИ", "ДЕВУШКИ", "МАЛЬЧИКИ",
                     "ДЕВОЧКИ", "ЮН/ДЕВ")


def choices() -> dict:
    """Варианты для выпадающих списков: пары (значение, подпись)."""
    return {
        "kinds": [(k, k) for k in KINDS],
        "levels": [(lv.name, label) for lv, label in LEVEL_LABELS.items()],
        "norms": [(n, n) for n in norm_editions()],
        "percent": [("" if m is None else m.name, label) for m, label in PERCENT_LABELS.items()],
        "disciplines": [(d.code, d.name) for d in disciplines() if d.group != "маршрут"],
        "categories": [(c, c) for c in JUDGE_CATEGORIES],
        "quals": [(q, q) for q in QUAL_LABELS],
        "fee_per": [(f, f) for f in FEE_PER],
        "classes": [(str(c), str(c)) for c in range(1, 7)],
        "roles": GSK_ROLES,
        "groups": GROUP_SUGGESTIONS,
    }


def empty_zachet() -> dict:
    return {f: "" for f in ZACHET_FIELDS} | {"min_qual": "б/р", "fee_per": "команду"}


def _s(v) -> str:
    return "" if v is None else str(v)


def card_to_form(comp: Competition | None) -> dict:
    """Значения полей формы для показа карточки (или пустой формы нового соревнования)."""
    if comp is None:
        main = {k: "" for k in MAIN_FIELDS} | {"kind": KINDS[0], "level": Level.MUNICIPAL.name,
                                               "norms_edition": norm_editions()[-1]}
        return {"main": main, "officials": [_official_row(r, None) for r in GSK_ROLES], "zachety": [empty_zachet()]}
    main = {
        "title": comp.title, "kind": comp.kind, "level": comp.level.name,
        "date_from": comp.date_from.isoformat(), "date_to": comp.date_to.isoformat(),
        "place": comp.place, "host_territory": comp.host_territory, "organizers": "\n".join(comp.organizers),
        "calendar_number": comp.calendar_number, "norms_edition": comp.norms_edition,
        "percent_method": comp.percent_method.name if comp.percent_method else "",
        "preapp_deadline": comp.preapp_deadline.isoformat() if comp.preapp_deadline else "",
    }
    rest = list(comp.officials)
    officials = []
    for role in GSK_ROLES:  # стандартные должности — всегда на своих местах, как в Excel
        o = next((x for x in rest if x.role == role), None)
        if o:
            rest.remove(o)
        officials.append(_official_row(role, o))
    officials += [_official_row(o.role, o) for o in rest]
    zachety = [{
        "group": z.group, "distance_class": _s(z.distance_class), "discipline_code": z.discipline_code,
        "age_from": _s(z.age_from), "age_to": _s(z.age_to), "age_from_by_gsk": _s(z.age_from_by_gsk),
        "min_qual": z.min_qual.label, "team_size": _s(z.team_size), "min_men": _s(z.min_men or ""),
        "min_women": _s(z.min_women or ""), "fee": _s(z.fee), "fee_per": z.fee_per,
    } for z in comp.zachety] or [empty_zachet()]
    return {"main": main, "officials": officials, "zachety": zachety}


def _official_row(role: str, o: Official | None) -> dict:
    return {"role": role, "fio": o.fio if o else "", "category": o.category if o else "",
            "territory": o.territory if o else ""}


def form_from_data(data) -> dict:
    """Поля из отправленной формы. data — словарь (FormData) «имя поля → значение»."""
    main = {k: _s(data.get(k)).strip() for k in MAIN_FIELDS}
    return {"main": main, "officials": _rows(data, "g", OFFICIAL_FIELDS), "zachety": _rows(data, "z", ZACHET_FIELDS)}


def _rows(data, prefix: str, fields: list[str]) -> list[dict]:
    idx = sorted({int(parts[1]) for k in data
                  if (parts := k.split("-"))[0] == prefix and len(parts) == 3 and parts[1].isdigit()})
    return [{f: _s(data.get(f"{prefix}-{i}-{f}")).strip() for f in fields} for i in idx]


def parse_date(s: str) -> date | None:
    s = clean_spaces(s)
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%d.%m.%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    raise ValueError("не удалось распознать дату, нужен формат дд.мм.гггг")


def form_to_card(form: dict) -> tuple[Competition | None, dict[str, str]]:
    """Собрать карточку из полей формы. Возвращает (карточка, {}) или (None, {поле: ошибка})."""
    m, err = form["main"], {}
    for k in REQUIRED:
        if not clean_spaces(m.get(k)):
            err[k] = "обязательное поле"
    if m["kind"] and m["kind"] not in KINDS:
        err["kind"] = "выберите из списка"
    level = Level.__members__.get(m["level"])
    if m["level"] and level is None:
        err["level"] = "выберите из списка"
    dates = {}
    for k in ("date_from", "date_to", "preapp_deadline"):
        try:
            dates[k] = parse_date(m[k])
        except ValueError as e:
            err[k] = str(e)
            dates[k] = None
    if dates["date_from"] and dates["date_to"] and dates["date_to"] < dates["date_from"]:
        err["date_to"] = "дата окончания раньше даты начала"
    if m["norms_edition"] and m["norms_edition"] not in norm_editions():
        err["norms_edition"] = "выберите из списка"
    percent = None
    if m["percent_method"]:
        percent = PercentMethod.__members__.get(m["percent_method"])
        if percent is None:
            err["percent_method"] = "выберите из списка"

    officials = []
    for i, row in enumerate(form["officials"]):
        fio = clean_spaces(row["fio"])
        if not fio:
            continue  # незаполненная должность — просто пропускаем
        if not clean_spaces(row["role"]):
            err[f"g-{i}-role"] = "укажите должность"
        # категорию не из списка не запрещаем: проверка карточки предупредит, а данные не пропадут
        officials.append(Official(clean_spaces(row["role"]), fio, row["category"], clean_spaces(row["territory"])))

    codes = {c for c, _ in choices()["disciplines"]}
    zachety = []
    for i, row in enumerate(form["zachety"]):
        if not any(row[f] for f in ZACHET_FIELDS if f not in ("min_qual", "fee_per")):
            continue  # пустой блок зачёта
        p = f"z-{i}-"
        group = clean_spaces(row["group"])
        if not group:
            err[p + "group"] = "укажите группу"
        cls = _int(row["distance_class"], p + "distance_class", err, 1, 6, required=True)
        if row["discipline_code"] not in codes:
            err[p + "discipline_code"] = "выберите дисциплину"
        nums = {f: _int(row[f], p + f, err, 0, 150) for f in ("age_from", "age_to", "age_from_by_gsk", "team_size",
                                                              "min_men", "min_women")}
        fee = _int(row["fee"], p + "fee", err, 0, 10_000_000)
        try:
            qual = parse_qual(row["min_qual"])
        except ValueError:
            err[p + "min_qual"] = "выберите из списка"
            qual = Qual.BR
        if row["fee_per"] and row["fee_per"] not in FEE_PER:
            err[p + "fee_per"] = "выберите из списка"
        if p + "group" not in err and p + "distance_class" not in err and p + "discipline_code" not in err:
            zachety.append(Zachet(group, cls, row["discipline_code"], age_from=nums["age_from"],
                                  age_to=nums["age_to"], age_from_by_gsk=nums["age_from_by_gsk"], min_qual=qual,
                                  team_size=nums["team_size"], min_men=nums["min_men"] or 0,
                                  min_women=nums["min_women"] or 0, fee=fee, fee_per=row["fee_per"] or "команду"))
    if err:
        return None, err
    organizers = [clean_spaces(x) for x in m["organizers"].replace(";", "\n").split("\n") if clean_spaces(x)]
    return Competition(
        title=clean_spaces(m["title"]), kind=m["kind"], level=level, date_from=dates["date_from"],
        date_to=dates["date_to"], place=clean_spaces(m["place"]), host_territory=clean_spaces(m["host_territory"]),
        organizers=organizers, calendar_number=clean_spaces(m["calendar_number"]), norms_edition=m["norms_edition"],
        percent_method=percent, preapp_deadline=dates["preapp_deadline"], officials=officials, zachety=zachety,
    ), {}


def _int(v: str, name: str, err: dict, lo: int, hi: int, required: bool = False) -> int | None:
    s = clean_spaces(v).replace(" ", "")
    if not s:
        if required:
            err[name] = "обязательное поле"
        return None
    try:
        n = int(s)
    except ValueError:
        err[name] = "нужно целое число"
        return None
    if not lo <= n <= hi:
        err[name] = f"допустимо от {lo} до {hi}"
        return None
    return n
