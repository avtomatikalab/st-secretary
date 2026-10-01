"""Обработка предварительных заявок: чистка, сверка с карточкой соревнования, проверки допуска.

Принцип: всё, что система исправила сама, попадает в отчёт как «Исправлено» — секретарь видит
каждую правку. То, что исправить нельзя без человека, — «Проверить» или «Ошибка», и у каждого такого
замечания есть «почему» (правило или откуда взялось значение) и «что сделать».
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import date

from st_secretary.admission import age_in_year, check_athlete, full_years
from st_secretary.competition import Competition, Zachet
from st_secretary.importers.preapp_xlsx import RawApplication
from st_secretary.issues import ERROR, FIXED, INFO, WARNING, Issue
from st_secretary.names import initials
from st_secretary.qualification import Qual, parse_qual
from st_secretary.rank import GROUP, PAIR
from st_secretary.textclean import (
    PersonName,
    clean_spaces,
    edit_distance,
    from_years,
    normalize_name,
    normalize_sex,
    normalize_team,
    normalize_territory,
    parse_birth_date,
    sex_from_patronymic,
    split_contacts,
    years,
)

ASK = "Уточните у представителя команды (телефон — в шапке заявки)"
QUAL_HOW = ", ".join(q.label for q in Qual)


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
    extra: dict[str, str] = field(default_factory=dict)  # свои колонки заявки справа от бланка: заголовок → значение

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
        z.rank_format == GROUP for z in comp.zachety)
    known_territories = Counter()
    for a in apps:
        for r in a.rows:
            known_territories[normalize_territory(r.values["territory"])] += 1

    for app in apps:
        src = app.path.name
        if app.problems:
            for text, why in app.problems:
                if getattr(app, "similar_form", ""):  # похожа на свою форму — поправить форму по этому файлу (п. 41)
                    issues.append(Issue(ERROR, text, source=src, why=why, target=f"form:{src}",
                                        todo=f"«Исправить» откроет форму «{app.similar_form}» по этому файлу: поправьте "
                                             "колонки и сохраните — или сохраните как новую форму."))
                    continue
                issues.append(Issue(ERROR, text, source=src, why=why,
                                    todo="Попросите команду прислать заявку по бланку или заполните её в программе "
                                         "(кнопка «Исправить заявку» откроет пустую форму для этого файла)."))
            continue
        for text, why in getattr(app, "notes", []):  # прочитано не всё (лист без таблицы по бланку) — не молчать
            issues.append(Issue(WARNING, text, source=src, why=why))
        if not app.rows:
            issues.append(Issue(ERROR, "в заявке нет ни одного участника", source=src,
                                why="В таблице участников не заполнена ни одна строка в колонке «Фамилия, имя».",
                                todo="Попросите команду прислать заполненную заявку или впишите участников в программе."))
            continue
        t = _team_header(app, comp, known_territories, issues)
        teams.append(t)
        for n, raw in enumerate(app.rows, start=1):
            e = _entry(raw.row, n, raw.values, t, comp, all_group_format, issues)
            e.extra = {k: clean_spaces(v) for k, v in getattr(raw, "extra", {}).items() if clean_spaces(v)}
            t.entries.append(e)
        _team_checks(t, comp, issues)

    _cross_checks(teams, issues, comp)
    return PreappResult(teams, issues)


# ------------------------------------------------------------------ команда


def _team_header(app: RawApplication, comp: Competition, known: Counter, issues: list[Issue]) -> TeamApplication:
    src = app.path.name
    row_teams = [normalize_team(r.values["team"]) for r in app.rows]
    head_team = normalize_team(app.team)
    team = head_team or _most_common(row_teams)
    if not head_team:
        issues.append(Issue(WARNING, "в шапке заявки не указано название команды", source=src, team=team,
                            field="Команда",
                            why=f"Название взято из колонки «Команда» у участников: «{team}».",
                            todo="Проверьте, что название верное, — так оно будет написано в протоколах. "
                                 "Если нет — исправьте в заявке."))
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
        issues.append(Issue(WARNING, f"территория в шапке «{head_terr}», а у участников «{territory}»", source=src,
                            team=team, field="Территория",
                            why=f"В протоколах у команды одна территория. Пока взята та, что у участников: «{territory}».",
                            todo="Уточните у представителя, как писать территорию (как принято в Положении: населённый "
                                 "пункт или субъект РФ), и исправьте в заявке."))
    for raw_terr in {clean_spaces(r.values['territory']) for r in app.rows}:
        if raw_terr and raw_terr != territory and normalize_territory(raw_terr) == territory:
            issues.append(Issue(FIXED, f"территория «{raw_terr}» записана как «{territory}»", source=src, team=team,
                                field="Территория", before=raw_terr, after=territory))

    rep = normalize_name(app.representative or _most_common([clean_spaces(r.values["representative"]) for r in app.rows]))
    phone, email = split_contacts(app.contacts)
    if not phone and not email:
        issues.append(Issue(WARNING, "нет контактов представителя (телефон, e-mail)", source=src, team=team,
                            field="Контакты",
                            why="В шапке заявки пустое поле «Контактный телефон, адрес эл. почты». Если в заявке "
                                "найдутся ошибки, связаться с командой будет трудно.",
                            todo="Найдите телефон представителя (в письме с заявкой, у организаторов) и впишите его "
                                 "в заявку."))
    declared = _to_int(app.declared_count)
    if declared is not None and declared != len(app.rows):
        issues.append(Issue(WARNING, f"в шапке указано участников: {declared}, в таблице: {len(app.rows)}",
                            source=src, team=team, field="Кол-во участников",
                            why="Возможно, участника забыли вписать в таблицу — или состав изменили, а число в шапке "
                                "не поправили.",
                            todo="Уточните состав у представителя. "
                                 f"{'Добавьте участника' if declared > len(app.rows) else 'Уберите лишнего'} "
                                 "или исправьте число в шапке заявки."))
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

    def add(sev, text, fld="", before="", after="", why="", todo=""):
        issues.append(Issue(sev, text, source=src, team=t.team, person=who, field=fld, before=before, after=after,
                            why=why, todo=todo, row=row))

    for f in name.fixes:
        if f.startswith("дата"):
            continue
        add(FIXED, f"ФИО: {f}", "ФИО", clean_spaces(v["fio"]), name.full)
    for text, why in name.doubts:
        add(WARNING, f"ФИО: {text}", "ФИО", why=why,
            todo="Сверьте ФИО с документом участника или уточните у представителя и исправьте в заявке. "
                 "Если всё верно — ничего делать не нужно.")

    # дата рождения
    raw_birth = v["birth"]
    if (raw_birth is None or clean_spaces(raw_birth) == "") and name.extracted_birth:
        raw_birth = name.extracted_birth
        add(FIXED, f"дата рождения «{name.extracted_birth}» была вписана в ФИО — перенесена в колонку даты",
            "Дата рождения", clean_spaces(v["fio"]), name.extracted_birth)
    pd = parse_birth_date(raw_birth, today=comp.date_from)
    if pd.problem:
        add(ERROR, f"дата рождения: {pd.problem}", "Дата рождения", clean_spaces(raw_birth), why=pd.why,
            todo="Уточните дату рождения у представителя команды и исправьте в заявке: день.месяц.год, "
                 "например 05.03.2008. На комиссии по допуску сверьте с документом.")
    elif pd.note:
        add(WARNING, f"дата рождения: {pd.note}", "Дата рождения", clean_spaces(raw_birth), why=pd.why,
            todo="Уточните полную дату рождения у представителя и впишите её в заявку." if pd.year_only else
                 "Сверьте дату с документом участника. Если она верна — ничего делать не нужно.")
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
        add(ERROR, f"не распознан разряд «{clean_spaces(v['qual'])}»", "Разряд", clean_spaces(v["qual"]),
            why=f"Разряд записывают так: {QUAL_HOW} (понятны и «2 юн», «кмс», «1 разряд»). Это написание программа "
                "не узнаёт, а угадывать нельзя — от разряда зависят допуск и ранг соревнований.",
            todo="Уточните разряд у представителя и выберите его из списка в заявке.")

    # пол
    sex = normalize_sex(v["sex"])
    by_patr = sex_from_patronymic(name.patronymic) if name.patronymic else None
    if sex is None and by_patr:
        sex = by_patr
        add(FIXED, f"пол не указан — определён по отчеству: «{sex}»", "Пол", clean_spaces(v["sex"]), sex)
    elif sex is None:
        raw_sex = clean_spaces(v["sex"])
        add(ERROR, "не указан пол" if not raw_sex else f"пол «{raw_sex}» не понятен", "Пол", raw_sex,
            why="Пол нужен для зачётов и для проверки состава команды (мужчин и женщин не менее…). "
                "Определить его по отчеству не получилось: отчества нет или оно необычное.",
            todo="Уточните у представителя и выберите «м» или «ж» в заявке.")
    elif by_patr and by_patr != sex:
        kind = "мужское" if by_patr == "м" else "женское"
        add(WARNING, f"указан пол «{sex}», а отчество «{name.patronymic}» — {kind}", "Пол", sex,
            why=f"Отчество «{name.patronymic}» — {kind}, а в колонке «Пол» стоит «{sex}». Обычно это опечатка в колонке "
                "«Пол»; реже — ошибка в отчестве. От пола зависит проверка состава команды.",
            todo=f"{ASK} и исправьте пол или отчество в заявке.")

    # группа и класс → зачёт
    group = clean_spaces(v["group"])
    cls = _to_int(v["cls"])
    if cls is None and clean_spaces(v["cls"]):
        add(ERROR, f"класс дистанции «{clean_spaces(v['cls'])}» не число", "Класс",
            why="Класс дистанции записывают цифрой от 1 до 6 — так же, как в карточке соревнования.",
            todo="Выберите зачёт участника из списка в заявке.")
    classes = sorted({z.distance_class for z in comp.zachety})
    if not group and len(comp.zachety) == 1:
        group = comp.zachety[0].group
        add(FIXED, f"группа не указана — проставлена «{group}» (в соревновании один зачёт)", "Группа", "", group)
    if cls is None and len(classes) == 1:
        cls = classes[0]
        add(FIXED, f"класс не указан — проставлен {cls} (в соревновании один класс)", "Класс", "", str(cls))
    zachet = comp.find_zachet(group, cls) if group else None
    if zachet and zachet.name and group.casefold() == zachet.name.casefold():
        group = zachet.name  # свой зачёт (неофициальные) — записан названием
    elif zachet:
        if group != zachet.group:
            add(FIXED, f"группа «{group}» записана как в карточке: «{zachet.group}»", "Группа", group, zachet.group)
        group = zachet.group
    else:
        avail = ", ".join(z.title for z in comp.zachety)
        add(ERROR, f"группа «{group or '—'}» и класс «{cls or '—'}» не совпадают ни с одним зачётом соревнования",
            "Группа/класс",
            why=f"В карточке соревнования есть зачёты: {avail}. Участник должен попасть ровно в один из них — "
                "иначе его не будет ни в стартовом протоколе, ни в сводке для СЕКРЕТАРЬ_ST.",
            todo="Уточните у представителя, в каком зачёте выступает участник, и выберите зачёт в заявке. Если по "
                 "Положению такой зачёт есть, а в карточке его нет, — добавьте зачёт в карточку.")

    # участие в дистанциях (колонки L–O)
    personal, pair, pair_num, team_dist = (clean_spaces(v[k]) for k in ("personal", "pair", "pair_num", "team_dist"))
    pair = pair_code(pair)
    if all_group and not team_dist:
        team_dist = "1"
        add(FIXED, "участие в дистанции-группе не отмечено — проставлено «1» (соревнования только командные)",
            "Участие в группе", "", "1")
    if not (personal or pair or team_dist):
        add(ERROR, "не указано, в какой дистанции участвует (личная, связки, группа)", "Участие",
            why="В колонках «Участие в личной дистанции», «…в дистанции связок» и «…в дистанции-группа» нет ни "
                "одной отметки — участник не попадёт ни в один стартовый протокол.",
            todo=f"{ASK} и отметьте дистанции в заявке.")

    e = Entry(src, row, num, t.team, t.territory, t.representative, name, pd.value, birth_year, qual, sex,
              group, cls, clean_spaces(v["chip"]), personal, pair, pair_num, team_dist, zachet)
    _admission(e, comp, add)
    return e


def _admission(e: Entry, comp: Competition, add) -> None:
    z = e.zachet
    age = e.age_in(comp.year)
    if z is None or age is None:
        return
    by_year = f"Возраст считается по году рождения: сколько исполняется в {comp.year} году."
    if z.age_from and age < z.age_from:
        if z.age_from_by_gsk and age >= z.age_from_by_gsk:
            add(WARNING, f"в {comp.year} г. исполняется {years(age)}, а в зачёт {z.key} — с {from_years(z.age_from)}: "
                         "допуск только по решению ГСК", "Возраст",
                why=f"По Положению (карточка соревнования) в зачёт {z.key} допускаются с {from_years(z.age_from)}, "
                    f"а с {from_years(z.age_from_by_gsk)} — по решению ГСК. {by_year}",
                todo="Если дата рождения верна, в заявке ничего исправлять не нужно. Вынесите участника на комиссию "
                     "по допуску: ГСК решает, допускать ли его, и решение записывается в протокол комиссии.")
        else:
            add(ERROR, f"в {comp.year} г. исполняется {years(age)}: в зачёт {z.key} допускаются с {from_years(z.age_from)}",
                "Возраст",
                why=f"Минимальный возраст для зачёта {z.key} — из Положения (карточка соревнования). {by_year}",
                todo="Проверьте дату рождения. Если она верна — в этом зачёте участник выступать не может: "
                     "уточните у представителя замену или другой зачёт.")
    if z.age_to and age > z.age_to:
        add(ERROR, f"в {comp.year} г. исполняется {years(age)}: в зачёт {z.key} — не старше {from_years(z.age_to)}",
            "Возраст",
            why=f"Предельный возраст для зачёта {z.key} — из Положения (карточка соревнования). {by_year}",
            todo="Проверьте дату рождения. Если она верна — уточните у представителя другой зачёт или замену.")
    if e.qual is not None and e.qual < z.min_qual:
        add(ERROR, f"разряд {e.qual.label} ниже требуемого для зачёта {z.key} ({z.min_qual.label})", "Разряд",
            why=f"Для зачёта {z.key} по Положению нужен разряд не ниже {z.min_qual.label} (карточка соревнования).",
            todo="Уточните у представителя: возможно, разряд уже повышен, а в заявке указан старый — тогда на "
                 "комиссии нужен документ о разряде. Если разряд верный — в этом зачёте участник выступать не может.")
    if z.admission_profile and e.birth and e.qual is not None and z.distance_class and not comp.unofficial:
        for i in check_athlete(z.admission_profile, z.distance_class, e.birth, e.qual, comp.year):
            # возрастные группы Правил мягче, чем в Положении: предупреждаем, а решение — за ГСК
            add(WARNING if "возрастную группу" in i.text else i.severity, f"Правила: {i.text}", "Допуск",
                why=i.why, todo=i.todo)


_MIXED_PAIR = re.compile(r"(?:смеш\w*|см|м\s*[/+]\s*ж|ж\s*[/+]\s*м|мж)\.?(?=[\s\d]|$)\s*(.*)$", re.IGNORECASE)


def pair_code(value: str) -> str:
    """Отметка участия в связках: «см», «смеш», «смешанная», «м/ж» — смешанная связка «см» (Правки, п. 31); номер
    связки сохраняется («СМ2» → «см 2»). Остальное — как в заявке («м», «ж», «1», «+»)."""
    s = clean_spaces(value)
    m = _MIXED_PAIR.fullmatch(s)
    return f"см {m.group(1).strip()}".strip() if m else s


def pair_label(e) -> str:
    """Какая это связка в команде: «см», «см 2», «м» — как в заявке; номер связки из отдельной колонки добавляется."""
    code = " ".join(str(e.pair).lower().split())
    num = str(e.pair_num or "").strip()
    if num and not code.endswith(num):
        code = f"{code} {num}".strip()
    return code or "1"


def _pair_checks(members: list[Entry], z, add) -> None:
    """Состав связок (Правки, п. 31): в смешанной связке («см») — мужчин и женщин не меньше, чем в Положении
    (карточка: «мужчин не менее», «женщин не менее»); без требования — любые."""
    marked = [e for e in members if e.pair]
    pairs: dict[str, list[Entry]] = {}
    for e in marked or members:
        pairs.setdefault(pair_label(e) if marked else "1", []).append(e)
    for label, ps in pairs.items():
        if not label.startswith("см") or not (z.min_men or z.min_women):
            continue
        men = sum(1 for e in ps if e.sex == "м")
        women = sum(1 for e in ps if e.sex == "ж")
        if men < z.min_men or women < z.min_women:
            add(ERROR, f"смешанная связка «{label}»: мужчин {men}, женщин {women} — по Положению нужно не менее "
                       f"{z.min_men} м и {z.min_women} ж", "Состав связки",
                "Требование к составу связки — из Положения (карточка соревнования, зачёт "
                f"{z.key}). «см» в колонке «Участие в дистанции связок» — смешанная связка.",
                "Проверьте пол участников и отметки связок в заявке; если всё верно — состав нужно менять.")


def _team_checks(t: TeamApplication, comp: Competition, issues: list[Issue]) -> None:
    by_zachet: dict[str, list[Entry]] = {}
    for e in t.entries:
        if e.zachet:
            by_zachet.setdefault(e.zachet.key, []).append(e)

    def add(sev, text, fld, why, todo):
        issues.append(Issue(sev, text, source=t.source, team=t.team, field=fld, why=why, todo=todo))

    for members in by_zachet.values():
        z = members[0].zachet
        if z.rank_format == PAIR:
            _pair_checks(members, z, add)
        if z.rank_format != GROUP:
            continue
        n = len(members)
        men = sum(1 for e in members if e.sex == "м")
        women = sum(1 for e in members if e.sex == "ж")
        if z.team_size and n != z.team_size:
            add(ERROR if n < z.team_size else WARNING, f"в команде {n} чел., по Положению — {z.team_size}",
                "Состав команды",
                f"В зачёте {z.key} команда выступает составом {z.team_size} чел. (карточка соревнования, из Положения).",
                "Уточните у представителя, кого ещё заявляют, и добавьте участников в заявку — без полного состава "
                "команда не допускается." if n < z.team_size else
                "Уточните у представителя, кто выступает в основном составе, и уберите лишних из заявки "
                "(если Положение разрешает запасных — отметьте это на комиссии).")
        rule = "Требование к составу команды — из Положения (карточка соревнования). Пол берётся из колонки «Пол», " \
               "а если она пустая — по отчеству."
        if men < z.min_men:
            add(ERROR, f"мужчин в команде {men}, нужно не менее {z.min_men}", "Состав команды", rule,
                "Проверьте пол участников в заявке. Если он указан верно — состав нужно менять: уточните у представителя.")
        if women < z.min_women:
            add(ERROR, f"женщин в команде {women}, нужно не менее {z.min_women}", "Состав команды", rule,
                "Проверьте пол участников в заявке. Если он указан верно — состав нужно менять: уточните у представителя.")
        if z.admission_profile == "psr":
            adults = [e for e in members if e.birth and full_years(e.birth, comp.date_from) >= 18]
            if not adults:
                add(ERROR, "в команде нет участника 18 лет и старше", "Руководитель",
                    "Руководителем команды на дистанции может быть только участник, которому на день начала "
                    f"соревнований ({comp.date_from:%d.%m.%Y}) исполнилось 18 лет (Правила, часть 4, п. 2.1.5). "
                    "Считаются полные годы — поэтому нужна полная дата рождения.",
                    "Проверьте даты рождения. Если они верны — команде нужен совершеннолетний участник: уточните "
                    "у представителя.")
            rep = [e for e in members if e.name.full == t.representative]
            if rep and rep[0].birth and full_years(rep[0].birth, comp.date_from) < 18:
                add(ERROR, "представитель участвует в команде, но ему нет 18 лет", "Руководитель",
                    f"Представитель указан и среди участников, но на {comp.date_from:%d.%m.%Y} ему не исполнилось 18 лет.",
                    "Уточните у представителя, кто руководитель команды: им может быть только совершеннолетний "
                    "участник.")


def _one_class(teams: list[TeamApplication], issues: list[Issue]) -> None:
    """Положение: «участник — только в одном классе дистанции» (Правки, п. 35) — человек (ФИО и дата рождения) во
    всех заявках, в том числе в одной заявке на разных листах (бланк делегации по классам)."""
    classes: dict[tuple, set[int]] = {}
    for t in teams:
        for e in t.entries:
            if e.zachet and e.zachet.distance_class:
                classes.setdefault(_person(e), set()).add(e.zachet.distance_class)
    for t in teams:
        for e in t.entries:
            got = sorted(classes.get(_person(e), ()))
            if len(got) > 1:
                where = " и ".join(str(c) for c in got)
                issues.append(Issue(
                    ERROR, f"{initials(e.name.full)}: заявлен во {where} классе — по Положению можно только в одном",
                    source=t.source, team=t.team, person=e.name.full, field="Класс", row=e.row,
                    why="В карточке соревнования отмечено «Участник — только в одном классе дистанции» (из Положения: "
                        "«разрешено участие одного спортсмена в соревнованиях в одном классе»).",
                    todo="Уточните у представителя, в каком классе выступает участник, и уберите его из другого "
                         "зачёта в заявке."))


def _person(e: Entry) -> tuple:
    return e.name.full.lower().replace("ё", "е"), e.birth or e.birth_year


def _cross_checks(teams: list[TeamApplication], issues: list[Issue], comp: Competition | None = None) -> None:
    if comp is not None and comp.one_class:
        _one_class(teams, issues)
    seen: dict[tuple, TeamApplication] = {}
    for t in teams:
        for e in t.entries:
            key = _person(e)
            if key in seen and seen[key] is not t:
                other = seen[key]
                issues.append(Issue(
                    ERROR, f"участник заявлен и в команде «{other.team}»", source=t.source, team=t.team,
                    person=e.name.full, field="Дубль", row=e.row,
                    why=f"Тот же человек (ФИО и дата рождения совпадают) есть в заявке команды «{other.team}», файл "
                        f"«{other.source}». На соревновании можно выступать только за одну команду "
                        "(Правила, раздел 3, п. 8.1).",
                    todo="Свяжитесь с представителями обеих команд и уберите участника из одной из заявок."))
            seen.setdefault(key, t)
    names = Counter(t.team.lower() for t in teams)
    for t in teams:
        if names[t.team.lower()] > 1:
            issues.append(Issue(
                WARNING, "команда с таким названием прислала больше одной заявки", source=t.source, team=t.team,
                field="Команда",
                why="Возможно, это исправленная заявка под другим именем файла — или две разные команды выбрали "
                    "одинаковое название.",
                todo="Если заявка повторная — уберите старый файл кнопкой «Убрать» на вкладке «Файлы заявок». "
                     "Если команды разные — попросите одну из них уточнить название (например, «Ураган-1» и «Ураган-2»)."))
    if not teams:
        issues.append(Issue(INFO, "нет ни одной обработанной заявки"))
