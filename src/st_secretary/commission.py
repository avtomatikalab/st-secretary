"""Комиссия по допуску участников (Правила, раздел 3, пп. 8.1–8.5): документы, решения, взносы, номера.

Участники берутся из предварительных заявок — набирать заново ничего не нужно. Отметки комиссии
(документы, решения, взносы, номера, перезаявки) хранятся отдельно от заявок и привязаны к файлу
заявки и ФИО участника: если заявку исправили, отметки остальных участников остаются.

Участник допускается сам, когда все нужные документы отмечены, а в заявке по нему нет ни ошибок, ни
неотмеченных «проверить». Иначе нужно решение комиссии: «допустить» (например, по решению ГСК о
возрасте) или «не допускать» с причиной. С ошибками в заявке допустить нельзя — сначала исправить её.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime

from st_secretary.admission import full_years
from st_secretary.competition import Competition
from st_secretary.issues import CHECKED, ERROR, WARNING, Issue
from st_secretary.preapp import Entry, PreappResult, TeamApplication
from st_secretary.qualification import Qual
from st_secretary.reference import discipline_by_code

ADMITTED, PENDING, REJECTED = "admitted", "pending", "rejected"
PERSON_LABEL = {ADMITTED: "Допущен", PENDING: "Ожидает", REJECTED: "Не допущен"}
TEAM_LABEL = {ADMITTED: "Допущена", PENDING: "Ожидает", REJECTED: "Не допущена"}


@dataclass(frozen=True)
class Doc:
    key: str
    title: str  # полностью — в настройках и подсказках
    short: str  # в заголовке колонки
    default: bool = True  # нужен по умолчанию (по Правилам); остальные включаются по Положению


# Правила, раздел 3, п. 8.1: документы на каждого участника и требования к заявке.
PERSON_DOCS = (
    Doc("id", "Документ, удостоверяющий личность и возраст (паспорт, свидетельство о рождении)", "Паспорт"),
    Doc("med", "Медицинский допуск (отметка врача в заявке или справка)", "Мед. допуск"),
    Doc("book", "Зачётная классификационная книжка", "Книжка"),
    Doc("oms", "Полис обязательного медицинского страхования", "ОМС"),
    Doc("ins", "Полис страхования жизни и здоровья от несчастных случаев", "Страховка"),
    Doc("pd", "Согласие на обработку персональных данных (за несовершеннолетних — от родителей)", "Согласие", False),
)
TEAM_DOCS = (
    Doc("app", "Заявка по форме, подписанная направляющей организацией", "Заявка"),
    Doc("doctor", "Допуск врача в заявке: «допущен» напротив каждого, подпись и печать", "Допуск врача"),
)
FEE_METHODS = ("наличные", "перевод", "по счёту")
DECIDED_HERE = ("Возраст", "Допуск")  # замечания, по которым допуск решает комиссия (ГСК), а не отметка секретаря


def person_key(name: str) -> str:
    """Чем участник отличается внутри заявки: ФИО без регистра и «ё»."""
    return " ".join(name.lower().replace("ё", "е").split())


def settings(data: dict) -> dict:
    """Настройки комиссии: какие документы нужны, начало соревнований (для перезаявок)."""
    s = data.get("settings", {})
    return {
        "docs": s.get("docs", [d.key for d in PERSON_DOCS if d.default]),
        "team_docs": s.get("team_docs", [d.key for d in TEAM_DOCS if d.default]),
        "start_at": s.get("start_at", ""),
    }


def required_docs(data: dict) -> tuple[list[Doc], list[Doc]]:
    s = settings(data)
    return [d for d in PERSON_DOCS if d.key in s["docs"]], [d for d in TEAM_DOCS if d.key in s["team_docs"]]


# ------------------------------------------------------------------ состояние участников и команд


@dataclass
class PersonCheck:
    entry: Entry
    key: str
    status: str
    docs: dict[str, bool]
    missing: list[Doc]
    errors: list[Issue]  # ошибки в заявке — допустить нельзя, пока не исправлены
    warnings: list[Issue]  # неотмеченные «проверить» — нужно решение комиссии
    decision: str = ""  # решение комиссии: "", ADMITTED, REJECTED
    reason: str = ""  # причина недопуска или основание решения

    @property
    def label(self) -> str:
        return PERSON_LABEL[self.status]

    @property
    def why(self) -> str:
        """Что с участником не так или на каком основании допущен — коротко, для таблицы и протокола."""
        miss = ", ".join(d.short.lower() for d in self.missing)
        if self.status == REJECTED:
            return "не допущен" + (f": {self.reason}" if self.reason else "")
        if self.status == ADMITTED:
            if self.decision == ADMITTED and (self.missing or self.warnings):
                return ("допущен решением комиссии" + (f": {self.reason}" if self.reason else "")
                        + (f" (нет: {miss})" if miss else ""))
            return ""
        parts = []
        if self.errors:
            parts.append("ошибка в заявке — сначала исправьте её")
        if self.missing:
            parts.append(f"нет: {miss}")
        if self.warnings and self.decision != ADMITTED:
            parts.append("нужно решение комиссии: " + "; ".join(w.text for w in self.warnings))
        return "; ".join(parts)


@dataclass
class TeamCheck:
    file: str
    team: TeamApplication | None
    status: str
    persons: list[PersonCheck]
    team_docs: dict[str, bool]
    missing_team_docs: list[Doc]
    errors: list[Issue]  # ошибки команды в заявке (состав, руководитель) и чтения файла
    warnings: list[Issue]
    problems: list[str]  # почему команда ещё не допущена
    number: int | None = None
    decision: str = ""
    note: str = ""
    fee_due: int = 0
    fee_paid: int = 0
    fee_method: str = ""
    reentries: list[dict] = field(default_factory=list)
    extra: list[str] = field(default_factory=list)  # что ещё мешает допуску (проверка снаряжения)

    @property
    def label(self) -> str:
        return TEAM_LABEL[self.status]

    @property
    def title(self) -> str:
        return self.team.team if self.team else self.file

    @property
    def counted(self) -> list[PersonCheck]:
        """Кто входит в команду для протокола: все, кроме недопущенных (у недопущенной команды — все)."""
        if self.status == REJECTED:
            return self.persons
        return [p for p in self.persons if p.status != REJECTED]

    @property
    def fee_status(self) -> str:
        if not self.fee_due:
            return "нет взноса"
        if self.fee_paid >= self.fee_due:
            return "оплачено"
        return "частично" if self.fee_paid else "не оплачено"


def _int(v) -> int | None:
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return None


def evaluate(result: PreappResult, files: list[str], comp: Competition, data: dict,
             extra: dict[str, list[str]] | None = None) -> list[TeamCheck]:
    """Состояние допуска по каждому файлу заявки (в порядке списка заявок).

    result — заявки с отметками «проверено» (замечания, отмеченные проверенными, уже не WARNING);
    extra — что ещё мешает допуску команды (например, итоги проверки снаряжения), по файлам."""
    pdocs, tdocs = required_docs(data)
    teams = {t.source: t for t in result.teams}
    by_source: dict[str, list[Issue]] = {}
    for i in result.issues:
        by_source.setdefault(i.source, []).append(i)
    out = []
    for file in files:
        m = data.get("teams", {}).get(file, {})
        team = teams.get(file)
        issues = by_source.get(file, [])
        persons = []
        for e in (team.entries if team else []):
            key = person_key(e.name.full)
            pm = m.get("people", {}).get(key, {})
            mine = [i for i in issues if i.row == e.row]
            docs = {d.key: bool(pm.get("docs", {}).get(d.key)) for d in pdocs}
            # «проверено» на предзаявках снимает сомнение в данных (ФИО, территория), но не решает допуск:
            # возраст младше, чем в Положении, — только решением ГСК на комиссии
            waiting = [i for i in mine if i.severity == WARNING or (i.severity == CHECKED and i.field in DECIDED_HERE)]
            p = PersonCheck(e, key, PENDING, docs, [d for d in pdocs if not docs[d.key]],
                            [i for i in mine if i.severity == ERROR], waiting,
                            pm.get("decision", ""), pm.get("reason", ""))
            if p.decision == REJECTED:
                p.status = REJECTED
            elif p.errors:
                p.status = PENDING
            elif p.decision == ADMITTED or (not p.missing and not p.warnings):
                p.status = ADMITTED
            persons.append(p)

        tdoc = {d.key: bool(m.get("team_docs", {}).get(d.key)) for d in tdocs}
        head = [i for i in issues if not i.row]
        t = TeamCheck(file, team, PENDING, persons, tdoc, [d for d in tdocs if not tdoc[d.key]],
                      [i for i in head if i.severity == ERROR], [i for i in head if i.severity == WARNING], [],
                      _int(m.get("number")), m.get("decision", ""), m.get("note", ""),
                      fee_due=0, fee_paid=_int(m.get("fee_paid")) or 0, fee_method=m.get("fee_method", ""),
                      reentries=list(m.get("reentries", [])))
        t.fee_due = fee_due(t, comp)
        t.extra = list((extra or {}).get(file, []))
        t.problems = _team_problems(t, comp) + (t.extra if team else [])
        if t.decision == REJECTED or (persons and all(p.status == REJECTED for p in persons)):
            t.status = REJECTED
        elif not t.problems:
            t.status = ADMITTED
        out.append(t)
    return out


def _team_problems(t: TeamCheck, comp: Competition) -> list[str]:
    if t.team is None:
        return ["заявку не удалось прочитать — исправьте её или заполните в программе"]
    problems = []
    if t.errors:
        problems.append("ошибки в заявке команды: " + "; ".join(i.text for i in t.errors))
    if t.missing_team_docs and t.decision != ADMITTED:
        problems.append("нет: " + ", ".join(d.short.lower() for d in t.missing_team_docs))
    waiting = [p for p in t.persons if p.status == PENDING]
    if waiting:
        problems.append(f"участники ждут решения: {len(waiting)}")
    # после недопуска участников состав команды может перестать соответствовать Положению
    rejected = any(p.status == REJECTED for p in t.persons)
    for key, members in _by_zachet([p.entry for p in t.persons if p.status != REJECTED]).items():
        z = members[0].zachet
        if rejected and discipline_by_code(z.discipline_code).rank_format == "group" and z.team_size \
                and len(members) < z.team_size:
            problems.append(f"в зачёте {key} осталось {len(members)} чел., нужно {z.team_size} — нужна перезаявка")
    return problems


def _by_zachet(entries: list[Entry]) -> dict[str, list[Entry]]:
    out: dict[str, list[Entry]] = {}
    for e in entries:
        if e.zachet:
            out.setdefault(e.zachet.key, []).append(e)
    return out


def fee_due(t: TeamCheck, comp: Competition) -> int:
    """Взнос по карточке соревнования: за команду — один раз на зачёт, за участника — за каждого."""
    total = 0
    for members in _by_zachet([p.entry for p in t.persons if p.status != REJECTED]).values():
        z = members[0].zachet
        if z.fee:
            total += z.fee if z.fee_per == "команду" else z.fee * len(members)
    return total


# ------------------------------------------------------------------ сводка для протокола

QUAL_COLUMNS = (("МС", Qual.MS), ("КМС", Qual.KMS), ("I", Qual.I), ("II", Qual.II), ("III", Qual.III),
                ("1ю", Qual.Y1), ("2ю", Qual.Y2), ("3ю", Qual.Y3), ("б/р", Qual.BR))
AGE_COLUMNS = (("<18", 0, 17), ("18–21", 18, 21), ("22–25", 22, 25), ("26–40", 26, 40), (">40", 41, 200))


def age_on_start(e: Entry, comp: Competition) -> int | None:
    """Полных лет на день начала соревнований (если известен только год — по году)."""
    if e.birth:
        return full_years(e.birth, comp.date_from)
    return comp.year - e.birth_year if e.birth_year else None


def protocol_row(t: TeamCheck, comp: Competition) -> dict:
    """Строка протокола комиссии по допуску (форма из приложений к разделу 3 Правил)."""
    people = [p.entry for p in t.counted]
    quals = Counter(e.qual for e in people if e.qual is not None)
    ages = [age_on_start(e, comp) for e in people]
    remarks = [f"{p.entry.name.full} — {p.why}" for p in t.persons if p.why]
    remarks += [f"нет: {', '.join(d.short.lower() for d in t.missing_team_docs)}"] if t.missing_team_docs else []
    remarks += [i.text for i in t.errors] + t.extra
    decision = {ADMITTED: "допущена", REJECTED: "не допущена", PENDING: "ожидает решения"}[t.status]
    if t.note:
        decision += f"; {t.note}"
    return {
        "number": t.number, "team": t.title, "territory": t.team.territory if t.team else "",
        "total": len(people),
        "quals": [quals.get(q, 0) for _, q in QUAL_COLUMNS],
        "men": sum(e.sex == "м" for e in people), "women": sum(e.sex == "ж" for e in people),
        "ages": [sum(1 for a in ages if a is not None and lo <= a <= hi) for _, lo, hi in AGE_COLUMNS],
        "remarks": "; ".join(remarks), "decision": decision,
    }


# ------------------------------------------------------------------ перезаявки


def reentry_record(before: list[Entry], after: list[dict], at: datetime, start_at: datetime | None,
                   earlier: list[dict]) -> dict:
    """Запись о перезаявке (п. 8.5): что изменилось, когда подана, не поздно ли, не повторная ли.

    after — строки сохранённой заявки: {"fio", "group", "cls"}."""
    old = {person_key(e.name.full): e for e in before}
    new = {person_key(r["fio"]): r for r in after}
    out = [f"выбыл(а): {old[k].name.full}" for k in old if k not in new]
    out += [f"включён(а): {new[k]['fio']}" for k in new if k not in old]
    for k in old.keys() & new.keys():
        was = old[k].zachet.key if old[k].zachet else f"{old[k].group}_{old[k].distance_class or ''}"
        now = f"{new[k]['group']}_{new[k]['cls']}"
        if was != now:
            out.append(f"{old[k].name.full}: зачёт {was} → {now}")
    late = bool(start_at and (start_at - at).total_seconds() < 3600)
    return {"at": at.strftime("%d.%m.%Y %H:%M"), "text": "; ".join(out) or "состав и зачёты не изменились",
            "late": late, "repeat": bool(earlier)}
