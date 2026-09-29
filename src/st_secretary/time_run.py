"""Дисциплины «по времени»: спелео (Правила, раздел 3, часть 8) и пешеходные в штрафной системе (часть 7).

Результат = время на дистанции (финиш − старт − отсечки) + штрафные баллы × временной эквивалент:
1 балл = 15 с при расчётном времени до 30 минут, иначе 30 с — если в Условиях не указано иное (спелео п. 6.2,
6.4; пешеходные п. 6.3.6). Баллы бывают дробными (0,3; 0,1 — приложение 2 к части 8).

Секретарь вносит время старта и финиша (или загружает из SI Reader), отсечки и штрафные баллы этапов из
протоколов судей; снятие с этапа — «с» в клетке этапа. Со снятиями с этапов — места после прошедших
дистанцию полностью (спелео п. 6.5). Время на дистанции больше КВ — место не присуждается (п. 8.18).

Хранение — то же, что у ПСР (Результаты_дистанции.json): этапы, клетки этапов, плюс у команды
«start», «finish», «cutoffs», «chip», а у зачёта — «spp» (эквивалент балла) и «kv» (КВ дистанции, мин).
"""

from __future__ import annotations

import re
from fractions import Fraction

from st_secretary.competition import Competition, Zachet
from st_secretary.disciplines import Status
from st_secretary.disciplines.speleo import SpeleoRun, standings
from st_secretary.issues import ERROR, INFO, WARNING, Issue
from st_secretary.norms import PercentMethod, achieved_norm, percent_of_winner
from st_secretary.psr_run import (
    TeamInput,
    TeamResult,
    ZachetRun,
    _status,
    parse_points,
    stages_of,
    tours_of,
)
from st_secretary.qualification import Qual
from st_secretary.rank import RankEntry, evsk_participation_ok, qualification_rank
from st_secretary.reference import discipline_by_code, norm_edition

# Дисциплины, где результат — время: спелео и пешеходные (штрафная система).
TIME_DISCIPLINES = {"0840131811Я", "0840261811Я", "0840271811Я", "0840091811Я", "0840241811Я", "0840251811Я"}
REMOVAL = ("с", "снят", "снята", "снятие", "с/э")  # как пишут снятие с этапа в клетке


def is_time_discipline(z: Zachet) -> bool:
    return z.discipline_code in TIME_DISCIPLINES


def parse_clock(s) -> Fraction | None:
    """«10:05:23», «10:05:23,4», «10:05» → секунды от начала суток. Пусто → None; не время → ValueError."""
    t = str(s or "").strip().replace(",", ".")
    if not t or t.strip("-:") == "":
        return None
    m = re.fullmatch(r"(\d{1,2}):(\d{2})(?::(\d{2}(?:\.\d+)?))?", t)
    if not m or int(m.group(1)) > 47 or int(m.group(2)) > 59:
        raise ValueError(t)
    sec = Fraction(m.group(3)) if m.group(3) else Fraction(0)
    if sec >= 60:
        raise ValueError(t)
    return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + sec


def parse_duration(s) -> Fraction:
    """Отсечки: «5:30» (мм:сс), «1:05:30» (ч:мм:сс) или «5» (минуты). Пусто → 0; не длительность → ValueError."""
    t = str(s or "").strip().replace(",", ".")
    if not t:
        return Fraction(0)
    if re.fullmatch(r"\d+(\.\d+)?", t):
        return Fraction(t) * 60
    parts = t.split(":")
    if len(parts) not in (2, 3) or not all(re.fullmatch(r"\d+(\.\d+)?", p) for p in parts):
        raise ValueError(t)
    vals = [Fraction(p) for p in parts]
    if any(v >= 60 for v in vals[1:]):
        raise ValueError(t)
    return vals[0] * 60 + vals[1] if len(vals) == 2 else vals[0] * 3600 + vals[1] * 60 + vals[2]


def clock_text(sec: Fraction | None, tenths: bool = True) -> str:
    """Секунды → «1:05:23» (с десятыми, если есть)."""
    if sec is None:
        return ""
    neg = sec < 0
    sec = abs(sec)
    whole = int(sec)
    frac = sec - whole
    h, m, s = whole // 3600, whole % 3600 // 60, whole % 60
    out = f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"
    if tenths and frac:
        out += "," + str(int(frac * 10))
    return ("−" if neg else "") + out


def duration_text(sec: Fraction) -> str:
    """Отсечки для клетки: «4:30» (мм:сс) или «1:04:30»."""
    return clock_text(sec)


def time_of_day_text(sec: Fraction | None) -> str:
    """Время суток всегда «чч:мм:сс» (с десятыми, если есть): «00:12:10», а не «12:10» — иначе прочтётся как 12 ч."""
    if sec is None:
        return ""
    whole = int(sec)
    out = f"{whole // 3600 % 24:02d}:{whole % 3600 // 60:02d}:{whole % 60:02d}"
    frac = sec - whole
    return out + ("," + str(int(frac * 10)) if frac else "")


def apply_si(zdata: dict, cards: list, teams: list[TeamInput]) -> dict:
    """Карты SI Reader → старт, финиш и отсечки команд зачёта (по чипу команды или участника из заявки).
    Возвращает {"teams": сколько команд заполнено, "unknown": [чипы без команды], "replaced": [команды,
    у которых вручную вписанное время заменено временем чипа]}."""
    from st_secretary.importers.si_reader import cutoffs_from, parse_pairs

    stored = zdata.setdefault("teams", {})
    owner = {}
    for t in teams:
        chip = str(stored.get(t.file, {}).get("chip", "")).strip()
        for c in ([chip] if chip else []) + [m.chip for m in t.members if m.chip]:
            owner.setdefault(c, t)
    pairs = parse_pairs(zdata.get("cutoff_pairs", ""))
    done, unknown, replaced = set(), [], []
    for card in cards:
        t = owner.get(card.siid)
        if t is None:
            unknown.append(card.siid)
            continue
        d = stored.setdefault(t.file, {})
        new = {"start": time_of_day_text(card.start), "finish": time_of_day_text(card.finish)}
        if pairs and (cut := cutoffs_from(card, pairs)) is not None:
            new["cutoffs"] = duration_text(cut)
        new = {k: v for k, v in new.items() if v}
        manual = [k for k, v in new.items() if d.get(k) and not d.get("si") and str(d.get(k)) != v]
        if manual:
            replaced.append(t.team)
        d.update(new)
        d["chip"] = d.get("chip") or card.siid
        d["si"] = {"siid": card.siid, "read_on": card.read_on}
        done.add(t.file)
    return {"teams": len(done), "unknown": unknown, "replaced": replaced}


def compute(comp: Competition, z: Zachet, zdata: dict, teams: list[TeamInput]) -> ZachetRun:
    stages = stages_of(zdata)
    known = {s.id for s in stages}
    stored = zdata.get("teams", {})
    issues: list[Issue] = []
    kv = parse_points(zdata.get("kv")) if str(zdata.get("kv", "")).strip().isdigit() else None
    spp_set = str(zdata.get("spp", "")).strip()
    expected = str(zdata.get("expected", "")).strip()
    spp = int(spp_set) if spp_set in ("15", "30") else (15 if expected.isdigit() and int(expected) <= 30 else 30)

    def order_key(t: TeamInput):
        n = re.sub(r"\D", "", t.number)
        return (0, int(n)) if n else (1, 0)

    rows: list[TeamResult] = []
    for i, t in enumerate(sorted(teams, key=order_key), start=1):
        d = stored.get(t.file, {})
        raw = {k: str(v) for k, v in d.get("points", {}).items() if k in known and str(v).strip() != ""}
        pts, bad, removals = {}, [], 0
        for sid, v in raw.items():
            if v.strip().lower() in REMOVAL:
                removals += 1
                continue
            try:
                x = parse_points(v)
            except ValueError:
                bad.append(sid)
                continue
            if x is not None:
                pts[sid] = x
        status = _status(d.get("status", Status.FINISHED.value if t.admitted else Status.DNS.value))
        r = TeamResult(t, i, pts, raw, bad, status, str(d.get("note", "")), removals=removals,
                       chip=str(d.get("chip", "")).strip() or next((m.chip for m in t.members if getattr(m, "chip", "")), ""))
        r.total = sum(pts.values(), Fraction(0))  # пока — сумма баллов; ниже станет результатом, с
        r.tours = {tour: sum((pts.get(s.id, Fraction(0)) for s in stages if s.tour == tour), Fraction(0))
                   for tour in tours_of(stages)}
        for field, parse in (("start", parse_clock), ("finish", parse_clock), ("cutoffs", parse_duration)):
            try:
                val = parse(d.get(field, ""))
            except ValueError:
                issues.append(Issue(ERROR, f"«{t.team}»: {'старт' if field == 'start' else 'финиш' if field == 'finish' else 'отсечки'} "
                                           f"«{d.get(field)}» — не время (нужно {'чч:мм:сс' if field != 'cutoffs' else 'мм:сс'})",
                                    source=z.key, team=t.team))
                bad.append(field)
                continue
            setattr(r, field, val if val is not None else (Fraction(0) if field == "cutoffs" else None))
        for sid in [b for b in bad if b in known]:
            st = next(s for s in stages if s.id == sid)
            issues.append(Issue(ERROR, f"«{t.team}», {st.title}: «{raw[sid]}» — не число и не «с» (снятие)",
                                source=z.key, team=t.team))
        if r.start is not None and r.finish is not None:
            finish = r.finish if r.finish >= r.start else r.finish + 86400  # финиш после полуночи
            r.distance_time = finish - r.start - r.cutoffs
            if r.distance_time <= 0:
                issues.append(Issue(ERROR, f"«{t.team}»: время на дистанции получилось {clock_text(r.distance_time)} — "
                                           "проверьте старт, финиш и отсечки", source=z.key, team=t.team))
                r.distance_time = None
            elif kv is not None and r.distance_time > kv * 60 and status is Status.FINISHED:
                r.status, r.auto_status = Status.OVER_TIME, True
        rows.append(r)

    # места: без времени — место не присуждается (результат ещё не внесён)
    runs, by_entry = [], {}
    for r in rows:
        # ошибка во времени старта, финиша или отсечках — результата нет, пока не исправят
        ok = r.status is Status.FINISHED and r.distance_time is not None and \
            not any(b in ("start", "finish", "cutoffs") for b in r.bad)
        runs.append(SpeleoRun(r.inp.file, r.start_order, Fraction(0) if ok else None,
                              r.distance_time if ok else None, Fraction(0), r.total, r.removals,
                              Status.FINISHED if ok else Status.DNS))
        by_entry[r.inp.file] = r
    ordered = []
    for p in standings(runs, spp, removed_by_count=zdata.get("removed_order") == "count"):
        r = by_entry[p.item.entry]
        r.place = p.place
        if r.distance_time is not None and r.status is Status.FINISHED:
            r.total = r.distance_time + r.total * spp  # результат, с
        ordered.append(r)
    empty = [r.inp.team for r in ordered if r.status is Status.FINISHED and r.place is None
             and not any(b in ("start", "finish", "cutoffs") for b in r.bad)]
    if empty and len(empty) < len(ordered):
        issues.append(Issue(INFO, f"нет времени старта или финиша — место не присуждается: {', '.join(empty)}",
                            source=z.key))
    for r in ordered:
        if r.auto_status:
            issues.append(Issue(WARNING, f"«{r.inp.team}»: время на дистанции {clock_text(r.distance_time)} больше КВ "
                                         f"({kv} мин) — место не присуждается (п. 8.18)", source=z.key, team=r.inp.team))

    started = [r for r in ordered if r.status not in (Status.DNS, Status.OUT_OF_COMPETITION)]
    rank, norms = None, None
    fmt = discipline_by_code(z.discipline_code).rank_format
    try:
        norms = norm_edition(comp.norms_edition)
    except KeyError:
        issues.append(Issue(ERROR, f"нет редакции норм «{comp.norms_edition}» — нормативы не считаются", source=z.key))
    if norms and fmt:
        rank = qualification_rank([RankEntry(r.place, tuple(m.qual or Qual.BR for m in r.inp.members))
                                   for r in started], fmt, norms)
    run = ZachetRun(z, stages, ordered, rank, issues, kind="time", seconds_per_point=spp)
    placed = [r for r in ordered if r.place is not None]
    ok, why = evsk_participation_ok(comp.level, len(started), None, judged_points=False)
    subjects_unknown = not ok and "не указано число субъектов" in (why or "")
    run.norms_ok = ok or subjects_unknown
    if why:
        issues.append(Issue(INFO, f"нормативы: {why}" + (" — проверьте по справке о количестве субъектов"
                                                          if subjects_unknown else " — разряды не присваиваются"),
                            source=z.key))
    if placed:
        run.winner = placed[0].total
        for r in placed:
            r.percent = percent_of_winner(r.total, run.winner, PercentMethod.TIME)
            if r.removals:
                continue  # со снятиями с этапов дистанция пройдена не полностью — норматив не присваивается
            if norms and rank and rank.value is not None and run.norms_ok:
                d = achieved_norm(norms, z.distance_class, rank.value, r.percent, comp.level)
                r.norm = d.qual.label if d.qual else ""
    if any(r.removals for r in placed):
        issues.append(Issue(INFO, "со снятиями с этапов разряды не присваиваются — дистанция пройдена не полностью",
                            source=z.key))
    if rank and rank.value is None and rank.reason:
        issues.append(Issue(INFO, f"ранг не определяется: {rank.reason}", source=z.key))
    return run
