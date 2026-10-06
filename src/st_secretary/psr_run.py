"""ПСР в программе: этапы дистанции, баллы команд по этапам, результат, места, ранг, нормативы.

Секретарь вносит итоговые баллы этапов из протоколов судей (штраф — плюс, премия — минус), как в листе
«Протокол_группа» СЕКРЕТАРЬ_ST. Программа считает сумму по турам и по дистанции, места (Правила, раздел 3,
п. 8.18 и часть 4, п. 6.1), квалификационный ранг по составам (разряды — из заявок), процент от победителя
по методике из карточки и выполненный разряд. Если для этапов заданы МШ, а для дистанции — километраж,
число видов туризма и КВ, — ещё и фактически пройденный класс (часть 4, п. 6.1.3, табл. 3).

Хранение — «Результаты.json» в папке соревнования: по зачётам — этапы, баллы, статусы, протесты,
время публикации предварительного и официального протокола.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from fractions import Fraction

from st_secretary import start_list
from st_secretary.competition import Competition, Zachet
from st_secretary.disciplines import Status
from st_secretary.disciplines.psr import PsrTeamCard, class_points, distance_class, standings
from st_secretary.issues import ERROR, INFO, WARNING, Issue
from st_secretary.norms import achieved_norm, percent_of_winner
from st_secretary.qualification import Qual
from st_secretary.rank import INDIVIDUAL, PAIR, RankEntry, RankResult, evsk_participation_ok, qualification_rank
from st_secretary.reference import norm_edition

STATUS_LABEL = {
    Status.FINISHED: "прошла дистанцию", Status.REMOVED: "снята", Status.DNF: "не финишировала",
    Status.OVER_TIME: "превысила КВ", Status.DNS: "не стартовала", Status.OUT_OF_COMPETITION: "вне конкурса",
}
STATUS_SHORT = {Status.FINISHED: "", Status.REMOVED: "снята", Status.DNF: "не финиш.", Status.OVER_TIME: "> КВ",
                Status.DNS: "не старт.", Status.OUT_OF_COMPETITION: "в/к"}


def unit_kind(rank_format: str | None) -> str:
    """Кто получает место: «person» — личная, «pair» — связка, «team» — команда (группа, экипаж)."""
    return {INDIVIDUAL: "person", PAIR: "pair"}.get(rank_format or "", "team")


def parse_points(s) -> Fraction | None:
    """«12», «-20», «12,5», «−5» → число; пусто → None. Не число — ValueError."""
    t = str(s if s is not None else "").strip().replace("−", "-").replace("–", "-").replace(",", ".").replace(" ", "")
    if not t:
        return None
    if not re.fullmatch(r"[-+]?\d+(\.\d+)?", t):
        raise ValueError(t)
    return Fraction(t)


def points_text(x: Fraction | None) -> str:
    """Как в протоколе: целые — без дробной части, иначе с запятой (до сотых)."""
    if x is None:
        return ""
    if x.denominator == 1:
        return str(x.numerator)
    return f"{float(x):.2f}".rstrip("0").rstrip(".").replace(".", ",")


@dataclass(frozen=True)
class Stage:
    id: str
    tour: str
    name: str
    max_penalty: Fraction | None = None  # МШ этапа (задан или ТШ + ВШ) — снятие с этапа, фактический класс
    kv_minutes: int | None = None  # КВ этапа — таймер у судьи на телефоне; сверх КВ — снятие с этапа (МШ)
    # временной штраф (stage_time.py): НВ, ВШ и правило «vsh_points балл(ов) за каждые vsh_step с сверх НВ»
    nv_minutes: Fraction | None = None
    tech_max: Fraction | None = None  # ТШ — наибольший технический штраф
    time_max: Fraction | None = None  # ВШ — наибольший временной штраф
    vsh_points: Fraction = Fraction(1)
    vsh_step: int = 30
    desc: str = ""  # описание этапа (Правки, п. 65): судье этого этапа на телефоне и в печати для судей

    @property
    def title(self) -> str:
        return f"{self.tour} · {self.name}" if self.tour else self.name

    @property
    def auto(self) -> bool:
        """Итог этапа считает программа: техштраф судьи + ВШ по времени на этапе (заданы НВ и ВШ)."""
        return self.nv_minutes is not None and self.time_max is not None


def _num(v) -> Fraction | None:
    try:
        return parse_points(v)
    except ValueError:
        return None


def stages_of(zdata: dict) -> list[Stage]:
    out = []
    for s in zdata.get("stages", []):
        mx, tsh, vsh = _num(s.get("max")), _num(s.get("tsh")), _num(s.get("vsh"))
        if mx is None and tsh is not None and vsh is not None:
            mx = tsh + vsh  # МШ = ТШ + ВШ (Правила, дистанции комбинированные, п. 1.5)
        kv = str(s.get("kv", "")).strip()
        nv = _num(s.get("nv"))
        n, step = _num(s.get("vsh_n")), str(s.get("vsh_m", "")).strip()
        out.append(Stage(str(s["id"]), str(s.get("tour", "")).strip(), str(s.get("name", "")).strip(), mx,
                         int(kv) if kv.isdigit() and int(kv) > 0 else None,
                         nv if nv is not None and nv >= 0 else None, tsh, vsh,
                         n if n is not None and n > 0 else Fraction(1),
                         int(step) if step.isdigit() and int(step) > 0 else 30, str(s.get("desc", "")).strip()))
    return out


def tours_of(stages: list[Stage]) -> list[str]:
    return list(dict.fromkeys(s.tour for s in stages))


@dataclass
class Member:
    fio: str
    qual: Qual | None
    qual_label: str
    chip: str = ""  # номер чипа SPORTident из заявки (если свой)
    birth: str = ""  # дата (или год) рождения — узнать одного человека в разных зачётах (расписание стартов)


@dataclass
class TeamInput:
    """Команда зачёта: из заявки и комиссии по допуску."""
    file: str  # файл заявки — ключ команды
    team: str
    territory: str
    number: str  # стартовый номер (из комиссии по допуску)
    members: list[Member]
    admitted: bool = True
    representative: str = ""
    club: str = ""  # команда спортсмена или связки (у команды — пусто: название и есть команда)


@dataclass
class TeamResult:
    inp: TeamInput
    start_order: int
    points: dict[str, Fraction]
    raw: dict[str, str]  # как ввели — чтобы показать и то, что не разобралось
    bad: list[str]  # этапы, где введено не число
    status: Status
    note: str
    total: Fraction = Fraction(0)
    tours: dict[str, Fraction] = field(default_factory=dict)
    place: int | None = None
    percent: Fraction | None = None
    percent_base: Fraction | None = None  # результат для % от победителя — без составляющих «не в %» (Правки, п. 48)
    norm: str = ""  # выполненный норматив; у связки и группы с разными — по участникам через запятую
    member_norms: list[str] = field(default_factory=list)  # норматив каждого участника по его возрасту (п. 46)
    actual_class: int | None = None
    # дисциплины «по времени» (спелео, пешеходные в штрафной системе) — см. time_run.py
    start: Fraction | None = None  # время старта, с от начала суток
    finish: Fraction | None = None
    cutoffs: Fraction = Fraction(0)  # сумма отсечек, с
    distance_time: Fraction | None = None  # время на дистанции (финиш − старт − отсечки), с
    removals: int = 0  # снятий с этапов
    marks: dict = field(default_factory=dict)  # ПСР: этап → «снята» / «сверх КВ» (в клетке МШ, stage_time.marks)
    codes: dict = field(default_factory=dict)  # этап → «п. 1×2, 3» — пункты таблицы штрафов от судьи (для протокола)
    by_item: dict = field(default_factory=dict)  # пункт таблицы штрафов → баллы за дистанцию (протокол спелео, п. 33)
    chip: str = ""
    auto_status: bool = False  # статус поставлен программой (превышено КВ)
    planned_start: bool = False  # старт не вписан — взят из стартового протокола
    extra: dict = field(default_factory=dict)  # штрафное время, заявленное время, тактика (по дисциплине)
    red: bool = False  # северная ходьба: красная карточка
    no_tactics: bool = False  # горные: заявка по тактике не сдана

    @property
    def filled(self) -> int:
        return len(self.points)


@dataclass
class ZachetRun:
    zachet: Zachet
    stages: list[Stage]
    rows: list[TeamResult]  # в порядке протокола
    rank: RankResult | None
    issues: list[Issue]
    winner: Fraction | None = None
    norms_ok: bool = False  # условие ЕВСК о числе участников выполнено
    kind: str = "points"  # "points" — ПСР (баллы этапов), "time" — со стартом и финишем (см. time_run.py)
    seconds_per_point: int | None = None  # для «time»
    scoring: str = "points"  # чем выражен результат: "points" (ПСР, горные) или "time" (спелео, пешеходные, СХ)
    unit: str = "team"  # кто получает место: "team", "pair" (связка), "person" (личная)
    show_codes: bool = False  # в протоколе «По этапам» — номера пунктов таблицы штрафов (настройка зачёта)
    profile: str = "psr"  # psr, speleo, pedestrian, nordic, mountain
    system: str = "penalty"  # штрафная или бесштрафовая система оценки нарушений
    adds: list[dict] = field(default_factory=list)  # дополнительные составляющие результата (time_run.adds_of)
    norms_why: str = ""  # почему разряды не присваиваются (условие ЕВСК о числе участников)

    @property
    def tours(self) -> list[str]:
        return tours_of(self.stages)

    @property
    def complete(self) -> bool:
        """Все этапы у всех команд, прошедших дистанцию, заполнены."""
        return bool(self.stages) and all(r.filled == len(self.stages) and not r.bad
                                         for r in self.rows if r.status is Status.FINISHED)


def status_of(v) -> Status:
    try:
        return Status(v)
    except ValueError:
        return Status.FINISHED


def parse_cells(d: dict, known: set[str], removal: tuple[str, ...] = ()) -> tuple[dict, dict, list, int]:
    """Клетки этапов команды → (как вписано, баллы, этапы с не-числом, снятий). removal — как пишут снятие с этапа
    («с» у дисциплин по времени); у ПСР снятие — МШ в клетке, слова нет. Общее для ПСР и дисциплин по времени."""
    raw = {k: str(v) for k, v in d.get("points", {}).items() if k in known and str(v).strip() != ""}
    pts, bad, removals = {}, [], 0
    for sid, v in raw.items():
        if v.strip().lower() in removal:
            removals += 1
            continue
        try:
            x = parse_points(v)
        except ValueError:
            bad.append(sid)
            continue
        if x is not None:
            pts[sid] = x
    return raw, pts, bad, removals


def age_of(m: Member, year: int) -> int | None:
    """Возраст в календарный год соревнований (так считаются возрастные условия ЕВСК); без года рождения — None."""
    y = m.birth[:4]
    return year - int(y) if y.isdigit() else None


def set_norm(r: TeamResult, norms, comp: Competition, z: Zachet, rank: Fraction, nordic: bool = False) -> None:
    """Выполненный норматив — у каждого участника по его возрасту (Правки, п. 46: юношеские разряды — только с
    возрастом, взрослые — с минимального возраста). Одинаковый у всех — одна клетка, как у СЕКРЕТАРЬ_ST; разный — по
    участникам через запятую в порядке состава («II, 2ю»)."""
    labels = []
    for m in r.inp.members or [None]:
        age = age_of(m, comp.year) if m is not None else None
        d = achieved_norm(norms, z.distance_class, rank, r.percent, comp.level, age=age, nordic_walking=nordic)
        labels.append(d.qual.label if d.qual else "")
    r.member_norms = labels if r.inp.members else []
    r.norm = labels[0] if len(set(labels)) == 1 else ", ".join(x or "-" for x in labels)


def norms_for(comp: Competition, z: Zachet, issues: list[Issue]):
    """Редакция разрядных норм соревнования; у неофициальных — нет (ни ранга, ни нормативов, решение 038)."""
    norms = None
    try:
        norms = norm_edition(comp.norms_edition)
    except KeyError:
        issues.append(Issue(ERROR, f"нет редакции норм «{comp.norms_edition}» — нормативы не считаются", source=z.key,
                            target="card"))
    if comp.unofficial:
        norms = None
        issues.append(Issue(INFO, "неофициальные соревнования — квалификационный ранг, % от победителя и разряды "
                                  "не считаются", source=z.key))
    return norms


def _psr_row(i: int, t: TeamInput, d: dict, stages, zdata: dict, z: Zachet, issues: list[Issue]) -> TeamResult:
    """Строка команды ПСР: баллы этапов, снятия (МШ), пункты таблицы штрафов от судей, сумма и туры; замечания —
    не число в клетке, больше 2 × МШ."""
    from st_secretary import judge_sync as js  # оба берут Stage отсюда
    from st_secretary import stage_time as stt

    raw, pts, bad, _ = parse_cells(d, {s.id for s in stages})
    status = status_of(d.get("status", Status.FINISHED.value if t.admitted else Status.DNS.value))
    r = TeamResult(t, i, pts, raw, bad, status, str(d.get("note", "")))
    r.marks = stt.marks(zdata, stages, t.file, raw)
    r.codes = {s.id: c for s in stages if (c := js.pen_codes(js.pens_of(zdata, s.id, t.file)))}
    r.total = sum(pts.values(), Fraction(0))
    r.tours = {tour: sum((pts.get(s.id, Fraction(0)) for s in stages if s.tour == tour), Fraction(0))
               for tour in tours_of(stages)}
    for sid in bad:
        st = next(s for s in stages if s.id == sid)
        issues.append(Issue(ERROR, f"«{t.team}», {st.title}: «{raw[sid]}» — не число", source=z.key, team=t.team,
                            target=f"cell:{t.file}:{sid}"))
    for s in stages:
        x = pts.get(s.id)
        if x is not None and s.max_penalty is not None and x > 2 * s.max_penalty:
            issues.append(Issue(WARNING, f"«{t.team}», {s.title}: {points_text(x)} — больше 2 × МШ "
                                         f"({points_text(2 * s.max_penalty)})", source=z.key, team=t.team,
                                target=f"cell:{t.file}:{s.id}"))
    return r


def _psr_places(rows: list[TeamResult], stages, zdata: dict, z: Zachet, issues: list[Issue]) -> list[TeamResult]:
    """Места по сумме баллов. Команда без единого внесённого балла места не получает: её баллы ещё не внесены, а не
    «0 — лучший результат»."""
    cards = [PsrTeamCard(r.inp.file, r.start_order, r.points, r.status if r.points else Status.DNS) for r in rows]
    empty = [r.inp.team for r in rows if r.status is Status.FINISHED and not r.points and not r.bad]
    if stages and empty and len(empty) < len(rows):
        issues.append(Issue(INFO, f"баллы не внесены — место не присуждается: {', '.join(empty)}", source=z.key))
    by_file = {r.inp.file: r for r in rows}
    ordered = []
    for p in standings(cards, tie_by_start_order=zdata.get("tie") == "start"):
        r = by_file[p.item.team]
        r.place = p.place
        ordered.append(r)
    return ordered


def _actual_class(run: ZachetRun, zdata: dict, z: Zachet, issues: list[Issue]) -> None:
    """Фактически пройденный класс (п. 6.1.3): без этапов, где получен МШ."""
    stages = run.stages
    dist = zdata.get("distance", {})
    try:
        km, modes, kv = (parse_points(dist.get(k)) for k in ("km", "modes", "kv_hours"))
    except ValueError:
        km = modes = kv = None
    if stages and kv is not None and z.distance_class and all(s.max_penalty is not None for s in stages):
        for r in run.rows:
            if r.status is not Status.FINISHED:
                continue
            passed = [s.max_penalty for s in stages if r.points.get(s.id, Fraction(0)) < s.max_penalty]
            r.actual_class = distance_class(class_points(passed, km or 0, int(modes or 0)), kv)
            if r.actual_class is not None and r.actual_class < z.distance_class:
                issues.append(Issue(INFO, f"«{r.inp.team}»: фактически пройден {r.actual_class} класс "
                                          f"(заявлен {z.distance_class})", source=z.key, team=r.inp.team))


def compute(comp: Competition, z: Zachet, zdata: dict, teams: list[TeamInput]) -> ZachetRun:
    """Расчёт зачёта ПСР: строки команд → места → ранг → процент и разряды → фактический класс."""
    stages = stages_of(zdata)
    stored = zdata.get("teams", {})
    issues: list[Issue] = []
    rows = [_psr_row(i, t, stored.get(t.file, {}), stages, zdata, z, issues)  # порядок старта — по жеребьёвке
            for i, t in enumerate(start_list.ordered(teams, zdata), start=1)]
    ordered = _psr_places(rows, stages, zdata, z, issues)

    # квалификационный ранг: все команды, что стартовали (места 1–6 дают баллы)
    started = [r for r in ordered if r.status not in (Status.DNS, Status.OUT_OF_COMPETITION)]
    rank = None
    fmt = z.rank_format
    norms = norms_for(comp, z, issues)
    if norms and fmt:
        if any(m.qual is None for r in started for m in r.inp.members):
            issues.append(Issue(WARNING, "у части участников не распознан разряд — в ранге они считаются без разряда",
                                source=z.key))
        rank = qualification_rank([RankEntry(r.place, tuple(m.qual or Qual.BR for m in r.inp.members))
                                   for r in started], fmt, norms)

    # процент от победителя и норматив
    run = ZachetRun(z, stages, ordered, rank, issues, unit=unit_kind(fmt))
    run.show_codes = bool(zdata.get("protocol_codes"))
    placed = [r for r in ordered if r.place is not None]
    if placed:
        run.winner = placed[0].total
    ok, why = evsk_participation_ok(comp.level, len(started), None, judged_points=True)
    subjects_unknown = not ok and "не указано число субъектов" in (why or "")  # его проверяет человек по справке
    run.norms_ok = (ok or subjects_unknown) and not comp.unofficial
    if comp.unofficial:
        return run  # только места: процента, нормативов и фактического класса нет
    if why:
        issues.append(Issue(INFO, f"нормативы: {why}" + (" — проверьте по справке о количестве субъектов"
                                                          if subjects_unknown else " — разряды не присваиваются"),
                            source=z.key))
    if comp.percent_method is None:
        issues.append(Issue(INFO, "в карточке не выбрана методика «% от победителя» — процент и нормативы не "
                                  "считаются", source=z.key))
    elif run.winner is not None:
        for r in placed:
            try:
                r.percent = percent_of_winner(r.total, run.winner, comp.percent_method)
            except ValueError as e:
                issues.append(Issue(WARNING, f"процент не считается: {e}", source=z.key))
                break
            if norms and rank and rank.value is not None and run.norms_ok:
                set_norm(r, norms, comp, z, rank.value)
        if rank and rank.value is None and rank.reason:
            issues.append(Issue(INFO, f"ранг не определяется: {rank.reason}", source=z.key))
    _actual_class(run, zdata, z, issues)
    return run


# ------------------------------------------------------------------ этапы из СЕКРЕТАРЬ_ST


def split_title(title: str) -> tuple[str, str]:
    """«Тур 2 Мера (Вышка 51 м)» → («Тур 2», «Мера (Вышка 51 м)»); «Бонус Ориентирование» → («Бонус», …)."""
    m = re.match(r"\s*Тур\s*(\d+)\s*[.:,-]?\s*(.*)", title)
    if m:
        return f"Тур {m.group(1)}", m.group(2).strip() or f"Тур {m.group(1)}"
    first, _, rest = title.strip().partition(" ")
    return first, rest.strip() or first


def _team_key(name: str) -> str:
    return re.sub(r"[^а-яёa-z0-9]", "", name.lower().replace("ё", "е"))


def import_group_protocol(sheet, teams: list[TeamInput]) -> tuple[dict, list[str]]:
    """Лист «Протокол_группа» СЕКРЕТАРЬ_ST → этапы и баллы зачёта. Команды сопоставляются по названию."""
    stages = []
    ids = {}
    for n, c in enumerate(sheet.stage_columns, start=1):
        tour, name = split_title(c.title)
        sid = f"s{n}"
        ids[c.col] = sid
        stages.append({"id": sid, "tour": tour, "name": name})
    by_name = {_team_key(t.team): t for t in teams}
    out_teams, notes = {}, list(sheet.warnings)
    for row in sheet.teams:
        t = by_name.get(_team_key(row.team))
        if t is None:
            notes.append(f"Команда «{row.team}» из рабочей книги не найдена среди заявок зачёта — её баллы не перенесены")
            continue
        out_teams[t.file] = {"points": {ids[c]: points_text(v) for c, v in row.values.items() if c in ids}}
    return {"stages": stages, "teams": out_teams}, notes


def result_text(run: ZachetRun, r: TeamResult) -> str:
    """Результат для протокола и табло: баллы (ПСР) или время (спелео, пешеходные); иначе — статус."""
    if r.status is not Status.FINISHED:
        return STATUS_LABEL[r.status]
    if run.kind == "time":
        if not r.place:
            return ""
        if run.scoring == "points":  # горные: баллы с точностью до 0,01
            return f"{float(r.total):.2f}".replace(".", ",")
        from st_secretary.time_run import clock_text

        return clock_text(r.total)
    return points_text(r.total) if r.points else ""
