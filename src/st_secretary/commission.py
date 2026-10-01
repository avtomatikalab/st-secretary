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
from st_secretary.rank import GROUP
from st_secretary.textclean import name_key

ADMITTED, PENDING, REJECTED = "admitted", "pending", "rejected"
PERSON_LABEL = {ADMITTED: "Допущен", PENDING: "Ожидает", REJECTED: "Не допущен"}
TEAM_LABEL = {ADMITTED: "Допущена", PENDING: "Ожидает", REJECTED: "Не допущена"}


# Кому нужен документ (Правки, п. 30): всем; только несовершеннолетним (на день начала соревнований); команде (и её
# участникам), если в ней есть несовершеннолетний — например, приказ о полномочиях представителя.
ALL, MINOR, TEAM_MINOR = "all", "minor", "team_minor"
WHEN_LABEL = {ALL: "всем", MINOR: "только несовершеннолетним (на день начала)",
              TEAM_MINOR: "если в команде есть несовершеннолетний"}
SCOPE_LABEL = {"person": "у участника", "team": "у команды"}


@dataclass(frozen=True)
class Doc:
    key: str
    title: str  # полностью — в настройках и подсказках
    short: str  # в заголовке колонки
    default: bool = True  # нужен по умолчанию (по Правилам); остальные включаются по Положению
    when: str = ALL  # кому нужен: ALL, MINOR, TEAM_MINOR
    alt: str = ""  # «или»: другой документ, который его заменяет — достаточно одного из двух
    own: bool = False  # свой документ — из Положения соревнования, не из Правил


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
DOCTOR, MED = "doctor", "med"  # допуск врача в заявке (у команды) → мед. допуск участникам (п. 23 правок)
FEE_METHODS = ("наличные", "перевод", "по счёту")
DECIDED_HERE = ("Возраст", "Допуск")  # замечания, по которым допуск решает комиссия (ГСК), а не отметка секретаря


def person_key(name: str) -> str:
    """Чем участник отличается внутри заявки: ФИО без регистра и «ё» (textclean.name_key)."""
    return name_key(name)


def settings(data: dict) -> dict:
    """Настройки комиссии: какие документы нужны (и свои документы по Положению), начало соревнований (для
    перезаявок)."""
    s = data.get("settings", {})
    return {
        "docs": s.get("docs", [d.key for d in PERSON_DOCS if d.default]),
        "team_docs": s.get("team_docs", [d.key for d in TEAM_DOCS if d.default]),
        "own_docs": clean_own_docs(s.get("own_docs", [])),
        "start_at": s.get("start_at", ""),
    }


def doc_key(title: str) -> str:
    """Ключ своего документа — по названию: тот же документ в разных соревнованиях и в запомненном наборе."""
    import hashlib

    return "x" + hashlib.sha1(person_key(title).encode("utf-8")).hexdigest()[:8]


def short_title(title: str) -> str:
    """Для заголовка колонки: начало названия до скобки или запятой, не длиннее трёх слов."""
    head = title.split("(", maxsplit=1)[0].split(",", maxsplit=1)[0].strip() or title
    words = head.split()
    return " ".join(words[:3]) + ("…" if len(words) > 3 else "")


def clean_own_docs(items) -> list[dict]:
    """Свои документы (Правки, п. 30): [{key, title, short, scope: person|team, when, alt}] — без пустых и повторов."""
    builtin = {d.key for d in (*PERSON_DOCS, *TEAM_DOCS)}
    out, seen = [], set()
    for x in items if isinstance(items, list) else []:
        title = " ".join(str(x.get("title", "")).split()) if isinstance(x, dict) else ""
        if not title:
            continue
        key = str(x.get("key") or doc_key(title))
        if key in seen or key in builtin:
            continue
        seen.add(key)
        short = " ".join(str(x.get("short", "")).split()) or short_title(title)
        out.append({"key": key, "title": title, "short": short, "scope": "team" if x.get("scope") == "team" else "person",
                    "when": x.get("when") if x.get("when") in WHEN_LABEL else ALL, "alt": str(x.get("alt", ""))})
    return out


def own_doc(x: dict) -> Doc:
    return Doc(x["key"], x["title"], x["short"], True, x["when"], x["alt"], True)


def all_docs(data: dict) -> tuple[list[Doc], list[Doc]]:
    """Все документы на выбор: из Правил и свои (по Положению) — (участника, команды)."""
    own = settings(data)["own_docs"]
    return ([*PERSON_DOCS, *[own_doc(x) for x in own if x["scope"] == "person"]],
            [*TEAM_DOCS, *[own_doc(x) for x in own if x["scope"] == "team"]])


def required_docs(data: dict) -> tuple[list[Doc], list[Doc]]:
    s = settings(data)
    pdocs, tdocs = all_docs(data)
    return [d for d in pdocs if d.key in s["docs"]], [d for d in tdocs if d.key in s["team_docs"]]


def alternatives(docs: list[Doc]) -> dict[str, set[str]]:
    """Пары «или» в обе стороны: {документ: документы, любой из которых его заменяет}."""
    keys = {d.key for d in docs}
    out: dict[str, set[str]] = {}
    for d in docs:
        if d.alt and d.alt in keys and d.alt != d.key:
            out.setdefault(d.key, set()).add(d.alt)
            out.setdefault(d.alt, set()).add(d.key)
    return out


def is_minor(e: Entry, comp: Competition) -> bool:
    """Несовершеннолетний на день начала соревнований (дата рождения неизвестна — не считается)."""
    age = age_on_start(e, comp)
    return age is not None and age < 18


def needs(d: Doc, team_minor: bool, minor: bool | None = None) -> bool:
    """Нужен ли документ: участнику (minor — несовершеннолетний ли он) или команде (minor=None)."""
    if d.when == MINOR:
        return minor if minor is not None else team_minor
    if d.when == TEAM_MINOR:
        return team_minor
    return True


def person_id(e: Entry) -> str:
    """Один человек в разных командах: ФИО + дата (или год) рождения (Правки, п. 20)."""
    born = e.birth.isoformat() if e.birth else str(e.birth_year or "")
    return f"{person_key(e.name.full)}|{born}"


def docs_by_person(result: PreappResult, files: list[str], data: dict) -> dict[str, dict[str, str]]:
    """Где у человека отмечены документы: {человек: {документ: файл заявки}} — по всем командам соревнования."""
    teams = {t.source: t for t in result.teams}
    out: dict[str, dict[str, str]] = {}
    for file in files:
        team = teams.get(file)
        people = data.get("teams", {}).get(file, {}).get("people", {})
        for e in (team.entries if team else []):
            for k, v in people.get(person_key(e.name.full), {}).get("docs", {}).items():
                if v:
                    out.setdefault(person_id(e), {}).setdefault(k, file)
    return out


def reentry_added(reentries: list[dict]) -> set[str]:
    """Кого включили в заявку перезаявкой (ключи ФИО) — в заявке с печатью врача их не было."""
    out: set[str] = set()
    for r in reentries:
        if "added" in r:
            out.update(r["added"])
            continue
        for part in str(r.get("text", "")).split("; "):  # записи прежних версий программы — по тексту
            if part.startswith("включён(а): "):
                out.add(person_key(part.removeprefix("включён(а): ")))
    return out


def doctor_covers(m: dict, key: str, pdocs: list[Doc], tdocs: list[Doc], by_delegation: bool = False) -> bool:
    """Мед. допуск участника ставится сам — по допуску врача в заявке: у команды отмечен «Допуск врача» (или у её
    делегации — заявка делегации с печатью врача, Правки, п. 20), участник был в заявке (не добавлен перезаявкой)
    и секретарь не снимал у него эту галочку (врач его не допустил)."""
    team_doctor = any(d.key == DOCTOR for d in tdocs) and bool(m.get("team_docs", {}).get(DOCTOR))
    return (any(d.key == MED for d in pdocs) and (team_doctor or by_delegation)
            and not m.get("people", {}).get(key, {}).get("med_off")
            and key not in reentry_added(m.get("reentries", [])))


def own_marks(result: PreappResult, files: list[str], data: dict,
              doctor_files: set[str] | None = None) -> dict[str, dict[str, str]]:
    """Документы, отмеченные у людей в этом соревновании (сами отметки и мед. допуск по допуску врача), —
    {человек: {документ: команда}}: на фестивале они засчитываются в других его соревнованиях (Правки, п. 20)."""
    pdocs, tdocs = required_docs(data)
    teams = {t.source: t for t in result.teams}
    out: dict[str, dict[str, str]] = {}
    for file in files:
        team = teams.get(file)
        m = data.get("teams", {}).get(file, {})
        for e in (team.entries if team else []):
            key = person_key(e.name.full)
            docs = {k for k, v in m.get("people", {}).get(key, {}).get("docs", {}).items() if v}
            if doctor_covers(m, key, pdocs, tdocs, file in (doctor_files or ())):
                docs.add(MED)
            for k in docs:
                out.setdefault(person_id(e), {}).setdefault(k, team.team)
    return out


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
    auto_docs: set[str] = field(default_factory=set)  # отмечены сами: мед. допуск — по допуску врача в заявке
    shared_docs: dict[str, str] = field(default_factory=dict)  # отмечены у этого человека в другой команде: док → команда
    shared_comp: dict[str, str] = field(default_factory=dict)  # …в другом соревновании фестиваля: док → соревнование
    not_needed: set[str] = field(default_factory=set)  # документ ему не нужен (только несовершеннолетним и т. п.)
    covered: dict[str, str] = field(default_factory=dict)  # не нужен — есть документ «или»: док → чем заменён

    @property
    def label(self) -> str:
        return PERSON_LABEL[self.status]

    @property
    def why(self) -> str:
        """Что с участником не так или на каком основании допущен — коротко, для таблицы и протокола."""
        miss = ", ".join(d.short.lower() for d in self.missing)
        if self.status == REJECTED:
            return "не допущен" + (f": {self.reason}" if self.reason else "")
        if self.decision == ADMITTED and self.missing and not self.reason and not self.errors:
            return f"допуск решением комиссии — укажите основание (нет: {miss})"
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
    problem_targets: list[str] = field(default_factory=list)  # где исправить каждую из problems (Issue.target)
    unreviewed: bool = False  # секретарь не отметил заявку «Проверено» (п. 24 правок)
    fee_by: str = ""  # взнос платит делегация одной строкой — её название (Правки, п. 20)
    team_not_needed: set[str] = field(default_factory=set)  # документ команды не нужен (нет несовершеннолетних)
    team_covered: dict[str, str] = field(default_factory=dict)  # не нужен — есть документ «или»: док → чем заменён

    @property
    def by_decision(self) -> bool:
        """Допущена решением комиссии без проверки секретаря: заявка не проверена или нет документов команды."""
        return self.decision == ADMITTED and bool(self.unreviewed or self.missing_team_docs)

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
             extra: dict[str, list[str]] | None = None, reviewed: dict[str, bool] | None = None,
             outside: dict[str, dict[str, tuple[str, str]]] | None = None,
             doctor_files: set[str] | None = None) -> list[TeamCheck]:
    """Состояние допуска по каждому файлу заявки (в порядке списка заявок).

    result — заявки с отметками «проверено» (замечания, отмеченные проверенными, уже не WARNING);
    extra — что ещё мешает допуску команды (например, итоги проверки снаряжения), по файлам;
    reviewed — отметил ли секретарь заявку «Проверено» (None — не учитывать): без этого команда сама не допускается;
    outside — документы людей, отмеченные в других соревнованиях фестиваля: {человек: {документ: (команда,
    соревнование)}}; doctor_files — заявки, чья делегация сдала заявку с печатью врача (Правки, п. 20)."""
    pdocs, tdocs = required_docs(data)
    alts = alternatives([*pdocs, *tdocs])
    titles = {d.key: d.short for d in (*pdocs, *tdocs)}
    teams = {t.source: t for t in result.teams}
    marked = docs_by_person(result, files, data)  # документы человека отмечают один раз — в любой его команде
    by_source: dict[str, list[Issue]] = {}
    for i in result.issues:
        by_source.setdefault(i.source, []).append(i)
    out = []
    for file in files:
        m = data.get("teams", {}).get(file, {})
        team = teams.get(file)
        issues = by_source.get(file, [])
        persons = []
        tdoc = {d.key: bool(m.get("team_docs", {}).get(d.key)) for d in tdocs}
        team_minor = any(is_minor(e, comp) for e in (team.entries if team else []))
        for e in (team.entries if team else []):
            key = person_key(e.name.full)
            pm = m.get("people", {}).get(key, {})
            mine = [i for i in issues if i.row == e.row]
            docs = {d.key: bool(pm.get("docs", {}).get(d.key)) for d in pdocs}
            auto = {MED} if MED in docs and not docs[MED] and \
                doctor_covers(m, key, pdocs, tdocs, file in (doctor_files or ())) else set()
            docs |= dict.fromkeys(auto, True)
            elsewhere = marked.get(person_id(e), {})
            shared = {d.key: teams[elsewhere[d.key]].team for d in pdocs if not docs[d.key]
                      and elsewhere.get(d.key, file) != file and elsewhere[d.key] in teams}
            far = (outside or {}).get(person_id(e), {})
            far = {d.key: far[d.key] for d in pdocs if not docs[d.key] and d.key not in shared and d.key in far}
            shared |= {k: v[0] for k, v in far.items()}
            docs |= dict.fromkeys(shared, True)
            # «проверено» на предзаявках снимает сомнение в данных (ФИО, территория), но не решает допуск:
            # возраст младше, чем в Положении, — только решением ГСК на комиссии
            waiting = [i for i in mine if i.severity == WARNING or (i.severity == CHECKED and i.field in DECIDED_HERE)]
            # кому документ не нужен (только несовершеннолетним) и чем заменён («или») — Правки, п. 30
            skip = {d.key for d in pdocs if not needs(d, team_minor, is_minor(e, comp))}
            covered = {}
            for d in pdocs:
                for a in sorted(alts.get(d.key, ())) if d.key not in skip and not docs[d.key] else ():
                    if docs.get(a):
                        covered[d.key] = f"есть «{titles[a]}»"
                    elif tdoc.get(a):
                        covered[d.key] = f"у команды есть «{titles[a]}»"
            p = PersonCheck(e, key, PENDING, docs,
                            [d for d in pdocs if not docs[d.key] and d.key not in skip and d.key not in covered],
                            [i for i in mine if i.severity == ERROR], waiting,
                            pm.get("decision", ""), pm.get("reason", ""), auto, shared,
                            {k: v[1] for k, v in far.items()}, skip, covered)
            if p.decision == REJECTED:
                p.status = REJECTED
            elif p.errors:
                p.status = PENDING
            elif (p.decision == ADMITTED and (p.reason or not p.missing)) or (not p.missing and not p.warnings):
                p.status = ADMITTED  # решением комиссии без документов — только с основанием
            persons.append(p)

        tskip = {d.key for d in tdocs if not needs(d, team_minor)}
        tcovered = {}
        for d in tdocs:
            for a in sorted(alts.get(d.key, ())) if d.key not in tskip and not tdoc[d.key] else ():
                those = [p for p in persons if a not in p.not_needed]  # документ участника «или» — у всех, кому нужен
                if tdoc.get(a):
                    tcovered[d.key] = f"есть «{titles[a]}»"
                elif any(x.key == a for x in pdocs) and those and all(p.docs.get(a) for p in those):
                    tcovered[d.key] = f"у всех, кому нужна, есть «{titles[a]}»"
        head = [i for i in issues if not i.row]
        t = TeamCheck(file, team, PENDING, persons, tdoc,
                      [d for d in tdocs if not tdoc[d.key] and d.key not in tskip and d.key not in tcovered],
                      [i for i in head if i.severity == ERROR], [i for i in head if i.severity == WARNING], [],
                      _int(m.get("number")), m.get("decision", ""), m.get("note", ""),
                      fee_due=0, fee_paid=_int(m.get("fee_paid")) or 0, fee_method=m.get("fee_method", ""),
                      reentries=list(m.get("reentries", [])))
        t.team_not_needed, t.team_covered = tskip, tcovered
        t.fee_due = fee_due(t, comp)
        t.unreviewed = team is not None and reviewed is not None and not reviewed.get(file, False)
        t.extra = list((extra or {}).get(file, []))
        found = _team_problems(t, comp)
        t.problems = [x for x, _ in found] + (t.extra if team else [])
        t.problem_targets = [w for _, w in found] + ([f"equipment:{t.file}"] * len(t.extra) if team else [])
        if t.decision == REJECTED or (persons and all(p.status == REJECTED for p in persons)):
            t.status = REJECTED
        elif not t.problems:
            t.status = ADMITTED
        out.append(t)
    return out


def _team_problems(t: TeamCheck, comp: Competition) -> list[tuple[str, str]]:
    if t.team is None:
        return ["заявку не удалось прочитать — исправьте её или заполните в программе"]
    problems = []  # (текст, где исправить)
    if t.unreviewed and t.decision != ADMITTED:
        problems.append(("заявку секретарь не проверил — откройте её и отметьте «Проверено»", f"preapp:{t.file}"))
    if t.by_decision and not t.note:
        problems.append(("допуск решением комиссии — укажите основание в поле замечания", f"adm:{t.file}/note"))
    if t.errors:
        problems.append(("ошибки в заявке команды: " + "; ".join(i.text for i in t.errors), f"preapp:{t.file}"))
    if t.missing_team_docs and t.decision != ADMITTED:
        problems.append(("нет: " + ", ".join(d.short.lower() for d in t.missing_team_docs),
                         f"adm:{t.file}/td-{t.missing_team_docs[0].key}"))
    waiting = [p for p in t.persons if p.status == PENDING]
    if waiting:
        problems.append((f"участники ждут решения: {len(waiting)}",
                         f"adm:{t.file}/p-{t.persons.index(waiting[0])}-decision"))
    # после недопуска участников состав команды может перестать соответствовать Положению
    rejected = any(p.status == REJECTED for p in t.persons)
    for key, members in _by_zachet([p.entry for p in t.persons if p.status != REJECTED]).items():
        z = members[0].zachet
        if rejected and z.rank_format == GROUP and z.team_size \
                and len(members) < z.team_size:
            problems.append((f"в зачёте {key} осталось {len(members)} чел., нужно {z.team_size} — нужна перезаявка",
                             f"reentry:{t.file}"))
    return problems


def without_check(teams: list[TeamCheck]) -> list[str]:
    """Допущены без проверки секретаря — решением комиссии: заявка не проверена, нет документов команды или
    участника. Для счётчика на странице комиссии."""
    out = []
    for t in teams:
        if t.status == ADMITTED and t.by_decision:
            what = (["заявку секретарь не проверил"] if t.unreviewed else []) + (
                [f"нет: {', '.join(d.short.lower() for d in t.missing_team_docs)}"] if t.missing_team_docs else [])
            out.append(f"«{t.title}» — {'; '.join(what)} (основание: {t.note})")
        out += [f"{p.entry.name.full} («{t.title}») — {p.why}" for p in t.persons
                if p.status == ADMITTED and p.decision == ADMITTED and p.missing]
    return out


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


# ------------------------------------------------------------------ делегации (Правки, п. 20; решение 040)

FEE_TEAMS, FEE_ONE = "teams", "one"
DELEGATION_FEE = {FEE_TEAMS: "по командам", FEE_ONE: "одной строкой за делегацию"}


def delegation_key(team: TeamApplication) -> str:
    """Делегация — территория и представитель в заявке (без регистра, «ё» и лишних пробелов)."""
    def n(s) -> str:
        return name_key(s)

    return f"{n(team.territory)}|{n(team.representative)}"


def delegation_title(team: TeamApplication) -> str:
    """«Красноярск · Лебедев Антон Игоревич» — так делегация называется на странице и в папке сканов."""
    return " · ".join(x for x in (team.territory or "территория не указана", team.representative) if x)


def doctor_files(result: PreappResult, data: dict) -> set[str]:
    """Заявки команд, чья делегация сдала заявку с печатью врача («Допуск врача» у делегации)."""
    marks = data.get("delegations", {})
    return {t.source for t in result.teams if marks.get(delegation_key(t), {}).get("doctor")}


@dataclass
class Delegation:
    key: str
    territory: str
    representative: str
    phone: str
    teams: list[TeamCheck]
    fee_mode: str = FEE_TEAMS
    fee_paid: int = 0
    fee_method: str = ""
    doctor: bool = False

    @property
    def title(self) -> str:
        return delegation_title(self.teams[0].team) if self.teams and self.teams[0].team else self.territory

    @property
    def fee_due(self) -> int:
        return sum(t.fee_due for t in self.teams)

    @property
    def people(self) -> int:
        return sum(len(t.counted) for t in self.teams)

    @property
    def fee_status(self) -> str:
        if not self.fee_due:
            return "нет взноса"
        paid = self.fee_paid if self.fee_mode == FEE_ONE else sum(t.fee_paid for t in self.teams)
        return "оплачено" if paid >= self.fee_due else "частично" if paid else "не оплачено"


def delegations(teams: list[TeamCheck], data: dict) -> list[Delegation]:
    """Делегации комиссии: команды одной территории и представителя, их взнос, допуск врача делегации."""
    marks = data.get("delegations", {})
    out: dict[str, Delegation] = {}
    for t in teams:
        if not t.team:
            continue
        k = delegation_key(t.team)
        if k not in out:
            m = marks.get(k, {})
            out[k] = Delegation(k, t.team.territory, t.team.representative, t.team.phone, [],
                                FEE_ONE if m.get("fee_mode") == FEE_ONE else FEE_TEAMS, _int(m.get("fee_paid")) or 0,
                                str(m.get("fee_method", "")), bool(m.get("doctor")))
        out[k].teams.append(t)
        if not out[k].phone and t.team.phone:
            out[k].phone = t.team.phone
    return sorted(out.values(), key=lambda d: (-len(d.teams), d.territory.lower()))


def apply_delegation_fees(teams: list[TeamCheck], data: dict) -> list[Delegation]:
    """Делегации, платящие одной строкой: оплата делегации раскладывается по её командам по порядку (сколько
    причитается с каждой), чтобы итоги и отметки команд сходились с ведомостью; у команд — «платит делегация»."""
    found = delegations(teams, data)
    for d in found:
        if d.fee_mode != FEE_ONE:
            continue
        left = d.fee_paid
        for i, t in enumerate(d.teams):
            t.fee_by = d.title
            t.fee_method = d.fee_method
            t.fee_paid = left if i == len(d.teams) - 1 else min(left, t.fee_due)
            left -= t.fee_paid
    return found


def fee_lines(teams: list[TeamCheck], found: list[Delegation]) -> list[dict]:
    """Строки ведомости взносов: команды, а делегации «одной строкой» — одной строкой на месте первой команды."""
    one = {t.file: d for d in found if d.fee_mode == FEE_ONE for t in d.teams}
    out, done = [], set()
    for t in teams:
        d = one.get(t.file)
        if d is None:
            out.append({"number": t.number, "team": t.title, "territory": t.team.territory if t.team else "",
                        "representative": t.team.representative if t.team else "", "people": len(t.counted),
                        "due": t.fee_due, "paid": t.fee_paid, "method": t.fee_method, "status": t.fee_status})
        elif d.key not in done:
            done.add(d.key)
            out.append({"number": ", ".join(str(x.number) for x in d.teams if x.number is not None),
                        "team": "Делегация: " + ", ".join(x.title for x in d.teams), "territory": d.territory,
                        "representative": d.representative, "people": d.people, "due": d.fee_due,
                        "paid": d.fee_paid, "method": d.fee_method, "status": d.fee_status})
    return out


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
    remarks += ["заявку секретарь не проверил"] if t.unreviewed else []
    decision = {ADMITTED: "допущена", REJECTED: "не допущена", PENDING: "ожидает решения"}[t.status]
    if t.status == ADMITTED and t.by_decision:
        decision = "допущена решением комиссии" + (f": {t.note}" if t.note else "")
    elif t.note:
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
            "late": late, "repeat": bool(earlier), "added": [k for k in new if k not in old]}
