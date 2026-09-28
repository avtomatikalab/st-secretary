"""Обработка предварительных заявок: чистка, сверка с карточкой соревнования, проверки допуска.

Принцип: всё, что система исправила сама, попадает в отчёт как «Исправлено» — секретарь видит
каждую правку. То, что исправить нельзя без человека, — «Проверить» или «Ошибка».
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import date

from st_secretary.admission import age_in_year, check_athlete, full_years
from st_secretary.competition import Competition, Zachet
from st_secretary.importers.preapp_xlsx import RawApplication
from st_secretary.issues import ERROR, FIXED, INFO, WARNING, Issue
from st_secretary.qualification import Qual, parse_qual
from st_secretary.reference import discipline_by_code
from st_secretary.textclean import (
    PersonName,
    clean_spaces,
    edit_distance,
    normalize_name,
    normalize_sex,
    normalize_team,
    normalize_territory,
    parse_birth_date,
    sex_from_patronymic,
    split_contacts,
)


@dataclass
class Entry:
    """Участник после чистки."""

    source: str
    row: int
    num_in_team: int
    team: str
    territory: str
    representative: str
    name: PersonName
    birth: date | None
    birth_year: int | None
    qual: Qual | None
    sex: str | None
    group: str
    distance_class: int | None
    chip: str
    personal: str
    pair: str
    pair_num: str
    team_dist: str
    zachet: Zachet | None = None

    def age_in(self, year: int) -> int | None:
        if self.birth:
            return age_in_year(self.birth, year)
        if self.birth_year:
            return year - self.birth_year
        return None


@dataclass
class TeamApplication:
    source: str
    team: str
    territory: str
    representative: str
    phone: str
    email: str
    declared_count: int | None
    entries: list[Entry] = field(default_factory=list)


@dataclass
class PreappResult:
    teams: list[TeamApplication]
    issues: list[Issue]

    @property
    def entries(self) -> list[Entry]:
        return [e for t in self.teams for e in t.entries]

    def count(self, severity: str) -> int:
        return sum(1 for i in self.issues if i.severity == severity)


def _most_common(values: list[str]) -> str:
    vals = [v for v in values if v]
    return Counter(vals).most_common(1)[0][0] if vals else ""


def _to_int(v) -> int | None:
    s = clean_spaces(v)
    if not s:
        return None
    try:
        return int(float(s.replace(",", ".")))
    except ValueError:
        return None


def process(apps: list[RawApplication], comp: Competition) -> PreappResult:
    issues: list[Issue] = []
    teams: list[TeamApplication] = []
    all_group_format = bool(comp.zachety) and all(
        discipline_by_code(z.discipline_code).rank_format == "group" for z in comp.zachety)
    known_territories = Counter()
    for a in apps:
        for r in a.rows:
            known_territories[normalize_territory(r.values["territory"])] += 1

    for app in apps:
        src = app.path.name
        if app.problems:
            for p in app.problems:
                issues.append(Issue(ERROR, p, source=src))
            continue
        if not app.rows:
            issues.append(Issue(ERROR, "в заявке нет ни одного участника", source=src))
            continue
        t = _team_header(app, comp, known_territories, issues)
        teams.append(t)
        for n, raw in enumerate(app.rows, start=1):
            t.entries.append(_entry(raw.row, n, raw.values, t, comp, all_group_format, issues))
        _team_checks(t, comp, issues)

    _cross_checks(teams, issues)
    return PreappResult(teams, issues)


# ------------------------------------------------------------------ команда


def _team_header(app: RawApplication, comp: Competition, known: Counter, issues: list[Issue]) -> TeamApplication:
    src = app.path.name
    row_teams = [normalize_team(r.values["team"]) for r in app.rows]
    head_team = normalize_team(app.team)
    team = head_team or _most_common(row_teams)
    if not head_team:
        issues.append(Issue(WARNING, "в шапке заявки не указано название команды — взято из строк участников",
                            source=src, team=team, field="Команда"))
    for raw_t, clean_t in zip((r.values["team"] for r in app.rows), row_teams):
        if clean_t and clean_t != team:
            issues.append(Issue(FIXED, f"название команды в строке «{clean_spaces(raw_t)}» приведено к «{team}»",
                                source=src, team=team, field="Команда", before=clean_spaces(raw_t), after=team))
    if head_team and clean_spaces(app.team) != head_team:
        issues.append(Issue(FIXED, "убраны лишние пробелы/кавычки в названии команды", source=src, team=team,
                            field="Команда", before=clean_spaces(app.team), after=head_team))

    # территория
    row_terr = [normalize_territory(r.values["territory"]) for r in app.rows]
    head_terr = normalize_territory(app.territory)
    territory = _most_common(row_terr) or head_terr
    territory = _fix_territory_typo(territory, comp, known, src, team, issues)
    if head_terr and head_terr != territory and _fix_territory_typo(head_terr, comp, known, src, team, []) != territory:
        issues.append(Issue(WARNING, f"территория в шапке «{head_terr}», в строках «{territory}» — "
                                     "уточните, как писать в протоколах", source=src, team=team, field="Территория"))
    for raw_terr in {clean_spaces(r.values['territory']) for r in app.rows}:
        if raw_terr and raw_terr != territory and normalize_territory(raw_terr) == territory:
            issues.append(Issue(FIXED, f"территория «{raw_terr}» записана как «{territory}»", source=src, team=team,
                                field="Территория", before=raw_terr, after=territory))

    rep = normalize_name(app.representative or _most_common([clean_spaces(r.values["representative"]) for r in app.rows]))
    phone, email = split_contacts(app.contacts)
    if not phone and not email:
        issues.append(Issue(WARNING, "нет контактов представителя (телефон, e-mail)", source=src, team=team,
                            field="Контакты"))
    declared = _to_int(app.declared_count)
    if declared is not None and declared != len(app.rows):
        issues.append(Issue(WARNING, f"в шапке указано участников: {declared}, в таблице: {len(app.rows)}",
                            source=src, team=team, field="Кол-во участников"))
    return TeamApplication(src, team, territory, rep.full, phone, email, declared)


def _fix_territory_typo(value: str, comp: Competition, known: Counter, src: str, team: str,
                        issues: list[Issue]) -> str:
    """Опечатка в названии населённого пункта: правим, только если значение похоже на территорию
    организаторов или на написание, которое встречается ЧАЩЕ, чем это значение. Территорию
    организаторов не правим никогда; короткие названия (Омск/Томск) не трогаем."""
    if not value or len(value) < 6 or value == comp.host_territory:
        return value
    candidates = [comp.host_territory] + [t for t, n in known.most_common() if n >= 3 and n > known[value]]
    for cand in candidates:
        if cand and cand != value and len(cand) >= 6 and edit_distance(cand, value) <= 2:
            issues.append(Issue(FIXED, f"территория «{value}» исправлена на «{cand}» (похоже на опечатку)",
                                source=src, team=team, field="Территория", before=value, after=cand))
            return cand
    return value


# ------------------------------------------------------------------ участник


def _entry(row: int, num: int, v: dict, t: TeamApplication, comp: Competition, all_group: bool,
           issues: list[Issue]) -> Entry:
    src = t.source
    name = normalize_name(v["fio"])
    who = name.full or clean_spaces(v["fio"])

    def add(sev, text, fld="", before="", after=""):
        issues.append(Issue(sev, text, source=src, team=t.team, person=who, field=fld, before=before, after=after))

    for f in name.fixes:
        if f.startswith("дата"):
            continue
        add(FIXED, f"ФИО: {f}", "ФИО", clean_spaces(v["fio"]), name.full)
    for d in name.doubts:
        add(WARNING, f"ФИО: {d}", "ФИО")

    # дата рождения
    raw_birth = v["birth"]
    if (raw_birth is None or clean_spaces(raw_birth) == "") and name.extracted_birth:
        raw_birth = name.extracted_birth
        add(FIXED, f"дата рождения «{name.extracted_birth}» была вписана в ФИО — перенесена в колонку даты",
            "Дата рождения", clean_spaces(v["fio"]), name.extracted_birth)
    pd = parse_birth_date(raw_birth, today=comp.date_from)
    if pd.problem:
        add(ERROR, f"дата рождения: {pd.problem}", "Дата рождения", clean_spaces(raw_birth))
    elif pd.note:
        add(WARNING, f"дата рождения: {pd.note}", "Дата рождения", clean_spaces(raw_birth))
    elif pd.value and isinstance(raw_birth, str) and "/" in raw_birth:
        add(FIXED, f"дата «{clean_spaces(raw_birth)}» прочитана как день/месяц/год: {pd.value:%d.%m.%Y}",
            "Дата рождения", clean_spaces(raw_birth), f"{pd.value:%d.%m.%Y}")
    birth_year = pd.value.year if pd.value else pd.year_only

    # разряд
    qual = None
    try:
        qual = parse_qual(v["qual"])
        raw_q = clean_spaces(v["qual"])
        if raw_q and raw_q != qual.label:
            add(FIXED, f"разряд «{raw_q}» записан как «{qual.label}»", "Разряд", raw_q, qual.label)
    except ValueError:
        add(ERROR, f"не распознан разряд «{clean_spaces(v['qual'])}»", "Разряд", clean_spaces(v["qual"]))

    # пол
    sex = normalize_sex(v["sex"])
    by_patr = sex_from_patronymic(name.patronymic) if name.patronymic else None
    if sex is None and by_patr:
        sex = by_patr
        add(FIXED, f"пол не указан — определён по отчеству: «{sex}»", "Пол", clean_spaces(v["sex"]), sex)
    elif sex is None:
        add(ERROR, "не указан пол", "Пол", clean_spaces(v["sex"]))
    elif by_patr and by_patr != sex:
        add(WARNING, f"указан пол «{sex}», а отчество «{name.patronymic}» — {'мужское' if by_patr == 'м' else 'женское'}",
            "Пол", sex)

    # группа и класс → зачёт
    group = clean_spaces(v["group"])
    cls = _to_int(v["cls"])
    if cls is None and clean_spaces(v["cls"]):
        add(ERROR, f"класс дистанции «{clean_spaces(v['cls'])}» не число", "Класс")
    classes = sorted({z.distance_class for z in comp.zachety})
    if not group and len(comp.zachety) == 1:
        group = comp.zachety[0].group
        add(FIXED, f"группа не указана — проставлена «{group}» (в соревновании один зачёт)", "Группа", "", group)
    if cls is None and len(classes) == 1:
        cls = classes[0]
        add(FIXED, f"класс не указан — проставлен {cls} (в соревновании один класс)", "Класс", "", str(cls))
    zachet = comp.find_zachet(group, cls) if group else None
    if zachet:
        if group != zachet.group:
            add(FIXED, f"группа «{group}» записана как в карточке: «{zachet.group}»", "Группа", group, zachet.group)
        group = zachet.group
    else:
        avail = ", ".join(z.key for z in comp.zachety)
        add(ERROR, f"группа «{group or '—'}» и класс «{cls or '—'}» не совпадают ни с одним зачётом ({avail})",
            "Группа/класс")

    # участие в дистанциях (колонки L–O)
    personal, pair, pair_num, team_dist = (clean_spaces(v[k]) for k in ("personal", "pair", "pair_num", "team_dist"))
    if all_group and not team_dist:
        team_dist = "1"
        add(FIXED, "участие в дистанции-группе не отмечено — проставлено «1» (соревнования только командные)",
            "Участие в группе", "", "1")
    if not (personal or pair or team_dist):
        add(ERROR, "не указано, в какой дистанции участвует (личная, связки, группа)", "Участие")

    e = Entry(src, row, num, t.team, t.territory, t.representative, name, pd.value, birth_year, qual, sex,
              group, cls, clean_spaces(v["chip"]), personal, pair, pair_num, team_dist, zachet)
    _admission(e, comp, add)
    return e


def _admission(e: Entry, comp: Competition, add) -> None:
    z = e.zachet
    age = e.age_in(comp.year)
    if z is None or age is None:
        return
    if z.age_from and age < z.age_from:
        if z.age_from_by_gsk and age >= z.age_from_by_gsk:
            add(WARNING, f"{age} лет в {comp.year} г.: в зачёт {z.key} — с {z.age_from} лет; по Положению "
                         f"допуск с {z.age_from_by_gsk} лет только по решению ГСК", "Возраст")
        else:
            add(ERROR, f"{age} лет в {comp.year} г.: в зачёт {z.key} допускаются с {z.age_from} лет", "Возраст")
    if z.age_to and age > z.age_to:
        add(ERROR, f"{age} лет в {comp.year} г.: в зачёт {z.key} — не старше {z.age_to} лет", "Возраст")
    if e.qual is not None and e.qual < z.min_qual:
        add(ERROR, f"разряд {e.qual.label} ниже требуемого для зачёта {z.key} ({z.min_qual.label})", "Разряд")
    if z.admission_profile and e.birth and e.qual is not None:
        for i in check_athlete(z.admission_profile, z.distance_class, e.birth, e.qual, comp.year):
            # возрастные группы Правил мягче, чем в Положении: предупреждаем, а решение — за ГСК
            add(WARNING if "возрастную группу" in i.text else i.severity, f"Правила: {i.text}", "Допуск")


def _team_checks(t: TeamApplication, comp: Competition, issues: list[Issue]) -> None:
    by_zachet: dict[str, list[Entry]] = {}
    for e in t.entries:
        if e.zachet:
            by_zachet.setdefault(e.zachet.key, []).append(e)
    for key, members in by_zachet.items():
        z = members[0].zachet
        if discipline_by_code(z.discipline_code).rank_format != "group":
            continue
        n = len(members)
        men = sum(1 for e in members if e.sex == "м")
        women = sum(1 for e in members if e.sex == "ж")
        if z.team_size and n != z.team_size:
            issues.append(Issue(ERROR if n < z.team_size else WARNING,
                                f"в команде {n} чел., по Положению — {z.team_size}", source=t.source, team=t.team,
                                field="Состав команды"))
        if men < z.min_men:
            issues.append(Issue(ERROR, f"мужчин в команде {men}, нужно не менее {z.min_men}", source=t.source,
                                team=t.team, field="Состав команды"))
        if women < z.min_women:
            issues.append(Issue(ERROR, f"женщин в команде {women}, нужно не менее {z.min_women}", source=t.source,
                                team=t.team, field="Состав команды"))
        if z.admission_profile == "psr":
            adults = [e for e in members if e.birth and full_years(e.birth, comp.date_from) >= 18]
            if not adults:
                issues.append(Issue(ERROR, "в команде нет участника 18 лет и старше — руководителем команды быть "
                                           "некому (Правила, часть 4, п. 2.1.5)", source=t.source, team=t.team,
                                    field="Руководитель"))
            rep = [e for e in members if e.name.full == t.representative]
            if rep and rep[0].birth and full_years(rep[0].birth, comp.date_from) < 18:
                issues.append(Issue(ERROR, "представитель участвует в команде, но ему нет 18 лет",
                                    source=t.source, team=t.team, field="Руководитель"))


def _cross_checks(teams: list[TeamApplication], issues: list[Issue]) -> None:
    seen: dict[tuple, TeamApplication] = {}
    for t in teams:
        for e in t.entries:
            key = (e.name.full.lower().replace("ё", "е"), e.birth or e.birth_year)
            if key in seen and seen[key] is not t:
                issues.append(Issue(ERROR, f"участник заявлен и в команде «{seen[key].team}» — на соревновании "
                                           "можно выступать только за одну команду (раздел 3, п. 8.1)",
                                    source=t.source, team=t.team, person=e.name.full, field="Дубль"))
            seen.setdefault(key, t)
    names = Counter(t.team.lower() for t in teams)
    for t in teams:
        if names[t.team.lower()] > 1:
            issues.append(Issue(WARNING, "команда с таким названием прислала больше одной заявки", source=t.source,
                                team=t.team, field="Команда"))
    if not teams:
        issues.append(Issue(INFO, "нет ни одной обработанной заявки"))
