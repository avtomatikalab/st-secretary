"""Проверка снаряжения (ПСР — Правила, раздел 3, часть 4, табл. 4 и п. 6.2.1).

Перечень обязательного снаряжения задаётся на соревнование (из Положения): личное — у каждого
участника, групповое — на команду, специальное — у каждого участника (проверяет техническая комиссия).
За каждый отсутствующий предмет — штрафные баллы; команда, набравшая больше порога, снимается
с соревнований.

По Правилам: личное — 1 балл, групповое — 5 баллов за предмет; «более 30 баллов штрафа при проверке
снаряжения» — снятие. Положение может установить свои значения (Правила, часть 4, п. 6.2.2) — поэтому
баллы и порог настраиваются.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from st_secretary.preapp import Entry, PreappResult, TeamApplication

PERSONAL, GROUP, SPECIAL = "personal", "group", "special"
KIND_LABEL = {PERSONAL: "Личное", GROUP: "Групповое", SPECIAL: "Специальное (на 1 чел.)"}
RULES = {"penalty": {PERSONAL: 1, GROUP: 5, SPECIAL: 1}, "limit": 30}  # Правила, часть 4, табл. 4

# Образец — перечень «ПСР КРСК 2025» (обязательное снаряжение; баллы и порог из того же документа).
PSR_KRSK_2025 = {
    "penalty": {PERSONAL: 1, GROUP: 3, SPECIAL: 1}, "limit": 10,
    "items": [
        (PERSONAL, "Ботинки", "пара", 1), (PERSONAL, "Носки шерстяные", "пара", 2),
        (PERSONAL, "Спортивный костюм тёплый", "шт.", 1), (PERSONAL, "Куртка тёплая (на среднюю t = −10 °C)", "шт.", 1),
        (PERSONAL, "Шапка тёплая", "шт.", 1), (PERSONAL, "Рюкзак", "шт.", 1), (PERSONAL, "Гермомешок для вещей", "шт.", 1),
        (PERSONAL, "Блокнот, ручка", "компл.", 1), (PERSONAL, "Фонарь налобный с запасным питанием на 30 часов", "компл.", 1),
        (PERSONAL, "Средство визуального и акустического воздействия на медведей и собак", "компл.", 1),
        (PERSONAL, "Рукавицы или перчатки для страховки", "пара", 1), (PERSONAL, "Самоспас", "компл.", 1),
        (PERSONAL, "Нож", "шт.", 1),
        (GROUP, "Котелок", "шт.", 1), (GROUP, "Топор, пила", "шт.", 1), (GROUP, "Часы", "шт.", 1),
        (GROUP, "Спальники на группу (не менее 2-х)", "компл.", 1), (GROUP, "Коврики на группу (не менее 2-х)", "компл.", 1),
        (GROUP, "Тент не менее 3×4 м (полиэтилен)", "шт.", 1), (GROUP, "Медаптечка с прилагающимся списком", "компл.", 1),
        (GROUP, "Непромокаемые мешки (не менее 60 л)", "шт.", 2),
        (GROUP, "Сотовый телефон с запасом питания на 30 часов в режиме GPS", "шт.", 1),
        (GROUP, "Компас", "шт.", 1), (GROUP, "Верёвка основная D = 10 мм, L = 45–50 м", "шт.", 1),
        (GROUP, "Карабин с муфтой", "шт.", 6), (GROUP, "Репшнур хозяйственный L = 15–20 м", "шт.", 1),
        (GROUP, "Запас еды на группу для непрерывной работы в течение 30 часов", "компл.", 1),
        (SPECIAL, "Страховочная система", "компл.", 1), (SPECIAL, "Защитная каска", "шт.", 1),
        (SPECIAL, "Усы самостраховки", "шт.", 2), (SPECIAL, "Карабины с муфтой", "шт.", 6), (SPECIAL, "Зажим", "шт.", 2),
        (SPECIAL, "Спусковое устройство", "шт.", 1), (SPECIAL, "Ролик", "шт.", 1), (SPECIAL, "Наколенники", "компл.", 1),
    ],
}


@dataclass(frozen=True)
class Item:
    id: str
    kind: str
    name: str
    unit: str
    qty: int


def settings(data: dict) -> dict:
    s = data.get("settings", {})
    pen = {**RULES["penalty"], **{k: int(v) for k, v in s.get("penalty", {}).items() if str(v).isdigit()}}
    return {"items": [Item(**i) for i in s.get("items", [])], "penalty": pen,
            "limit": int(s.get("limit", RULES["limit"]))}


def template_settings(tpl: dict) -> dict:
    """Настройки из образца перечня."""
    return {"penalty": dict(tpl["penalty"]), "limit": tpl["limit"],
            "items": [{"id": f"i{n}", "kind": k, "name": name, "unit": unit, "qty": qty}
                      for n, (k, name, unit, qty) in enumerate(tpl["items"], start=1)]}


@dataclass
class Missing:
    item: Item
    count: int  # сколько предметов не хватает
    person: str = ""  # у кого (для личного и специального)


@dataclass
class TeamGear:
    file: str
    team: TeamApplication | None
    checked: bool  # проверку начали (хоть что-то отмечено)
    missing: list[Missing] = field(default_factory=list)
    points: dict[str, int] = field(default_factory=dict)  # по видам снаряжения
    note: str = ""
    lacks: dict[tuple[str, str], int] = field(default_factory=dict)  # (ФИО или "", предмет) → сколько нет

    @property
    def total(self) -> int:
        return sum(self.points.values())

    @property
    def title(self) -> str:
        return self.team.team if self.team else self.file


def present(value, qty: int) -> int:
    """Сколько предметов в наличии: отмеченная галочка — все, число — столько, сколько вписано."""
    if value is True or value == "all":
        return qty
    try:
        return max(0, min(qty, int(value)))
    except (TypeError, ValueError):
        return 0


def evaluate(result: PreappResult, files: list[str], data: dict, key_of) -> list[TeamGear]:
    """Итоги проверки по каждой заявке. key_of(entry) — ключ участника (как в комиссии по допуску)."""
    s = settings(data)
    teams = {t.source: t for t in result.teams}
    out = []
    for file in files:
        team = teams.get(file)
        m = data.get("teams", {}).get(file, {})
        g = TeamGear(file, team, bool(m.get("group") or m.get("people")), note=m.get("note", ""))
        pts = {PERSONAL: 0, GROUP: 0, SPECIAL: 0}
        for it in s["items"]:
            if it.kind == GROUP:
                lack = it.qty - present(m.get("group", {}).get(it.id), it.qty)
                if lack:
                    g.missing.append(Missing(it, lack))
                    pts[GROUP] += lack * s["penalty"][GROUP]
                continue
            for e in (team.entries if team else []):
                lack = it.qty - present(m.get("people", {}).get(key_of(e), {}).get(it.id), it.qty)
                if lack:
                    g.missing.append(Missing(it, lack, e.name.full))
                    pts[it.kind] += lack * s["penalty"][it.kind]
        g.points = pts
        g.lacks = {(m.person, m.item.id): m.count for m in g.missing}
        out.append(g)
    return out


def verdict(g: TeamGear, limit: int) -> str:
    if not g.checked:
        return "не проверено"
    return f"снятие: {g.total} баллов, допустимо не более {limit}" if g.total > limit else "допущена"


def missing_text(g: TeamGear) -> str:
    """«Котелок; Иванов Иван Иванович — нож, каска» — для акта и протокола."""
    group = [f"{m.item.name}{f' ×{m.count}' if m.count > 1 else ''}" for m in g.missing if not m.person]
    by_person: dict[str, list[str]] = {}
    for m in g.missing:
        if m.person:
            by_person.setdefault(m.person, []).append(f"{m.item.name.lower()}{f' ×{m.count}' if m.count > 1 else ''}")
    parts = group + [f"{p} — {', '.join(v)}" for p, v in by_person.items()]
    return "; ".join(parts)


def admission_problems(gear: list[TeamGear], data: dict) -> dict[str, list[str]]:
    """Что мешает допуску по итогам проверки снаряжения (если перечень задан)."""
    s = settings(data)
    if not s["items"]:
        return {}
    out = {}
    for g in gear:
        if not g.checked:
            out[g.file] = ["снаряжение не проверено"]
        elif g.total > s["limit"]:
            out[g.file] = [f"снаряжение: {g.total} баллов штрафа, допустимо не более {s['limit']} — по Правилам снятие "
                           "с соревнований"]
    return out


def short_name(e: Entry) -> str:
    """«Иванов И.П.» — для заголовков колонок."""
    n = e.name
    return f"{n.surname} {n.name[:1]}.{n.patronymic[:1] + '.' if n.patronymic else ''}".strip()
