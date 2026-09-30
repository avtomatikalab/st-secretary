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

import math
import re
from fractions import Fraction

from st_secretary import start_list
from st_secretary.competition import Competition, Zachet
from st_secretary.disciplines import Status
from st_secretary.disciplines.speleo import SpeleoRun, standings
from st_secretary.issues import ERROR, INFO, WARNING, Issue
from st_secretary.norms import PercentMethod, achieved_norm, percent_of_winner
from st_secretary.psr_run import TeamInput, TeamResult, ZachetRun, _status, parse_points, stages_of, tours_of, unit_kind
from st_secretary.qualification import Qual
from st_secretary.rank import RankEntry, evsk_participation_ok, qualification_rank
from st_secretary.reference import norm_edition

# Дисциплины со стартом и финишем: спелео, пешеходные, северная ходьба (результат — время) и горные (баллы за
# время, технику и тактику).
SPELEO = {"0840131811Я", "0840261811Я", "0840271811Я"}
PEDESTRIAN = {"0840091811Я", "0840241811Я", "0840251811Я"}
NORDIC = {"0840291811Л"}
MOUNTAIN = {"0840101811Я": 4, "0840211811Я": 2}  # баллов за минуту: связка — 4, группа — 2 (часть 2, п. 6.1.4)
TIME_DISCIPLINES = SPELEO | PEDESTRIAN | NORDIC | set(MOUNTAIN)
REMOVAL = ("с", "снят", "снята", "снятие", "с/э")  # как пишут снятие с этапа в клетке


def is_time_discipline(z: Zachet) -> bool:
    if z.is_custom:  # своя дисциплина (неофициальные): время или время + баллы — как пешеходные
        return z.result in ("time", "time_points")
    return z.discipline_code in TIME_DISCIPLINES


def profile(z: Zachet) -> str:
    """«speleo», «pedestrian», «nordic» или «mountain»."""
    if z.is_custom:
        return "pedestrian"
    c = z.discipline_code
    return "pedestrian" if c in PEDESTRIAN else "nordic" if c in NORDIC else "mountain" if c in MOUNTAIN else "speleo"


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


def settings(z: Zachet, zdata: dict) -> dict:
    """Настройки расчёта зачёта по профилю дисциплины (со значениями по умолчанию)."""
    kind = profile(z)
    chosen = zdata.get("system") or ("nopenalty" if z.is_custom and z.result == "time" else "")  # своя «время» — без баллов
    system = "nopenalty" if chosen == "nopenalty" and kind in ("pedestrian", "nordic") else "penalty"
    return {"profile": kind, "system": system,
            "removal": "okv" if zdata.get("removal") == "okv" else "dsq",  # пешеходные п. 6.2.8; СХ п. 10.4.4
            "rate": MOUNTAIN.get(z.discipline_code)}


RED = ("к", "кк", "да", "1", "+", "x", "х", "есть")  # красная карточка — как отмечают в колонке
NO_TACTICS = ("нет", "не сдана", "да", "1", "+", "x", "х")  # заявка по тактике не сдана


def _flag(v, words: tuple[str, ...]) -> bool:
    return " ".join(str(v or "").lower().split()) in words


def _hundredths(x: Fraction) -> Fraction:
    """Баллы горных — с точностью до 0,01 (часть 2, п. 6.1.5), округление половины вверх."""
    return Fraction(math.floor(x * 100 + Fraction(1, 2)), 100)


def compute(comp: Competition, z: Zachet, zdata: dict, teams: list[TeamInput]) -> ZachetRun:
    stages = stages_of(zdata)
    known = {s.id for s in stages}
    stored = zdata.get("teams", {})
    issues: list[Issue] = []
    st = settings(z, zdata)
    kind, penalty_system = st["profile"], st["system"] == "penalty"
    kv = parse_points(zdata.get("kv")) if str(zdata.get("kv", "")).strip().isdigit() else None
    okv = kv * 60 if kv is not None else None  # ОКВ, с
    spp_set = str(zdata.get("spp", "")).strip()
    expected = str(zdata.get("expected", "")).strip()
    if kind == "nordic":
        spp = 15  # северная ходьба: 1 балл = 15 с (п. 10.3.2)
    else:
        spp = int(spp_set) if spp_set in ("15", "30") else (15 if expected.isdigit() and int(expected) <= 30 else 30)
    unit = unit_kind(z.rank_format)

    planned = start_list.planned_starts(zdata, teams)  # «Время старта — время, указанное в стартовом протоколе»
    rows: list[TeamResult] = []
    for i, t in enumerate(start_list.ordered(teams, zdata), start=1):
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
        r.total = sum(pts.values(), Fraction(0))  # пока — сумма баллов; ниже станет результатом
        r.tours = {tour: sum((pts.get(s.id, Fraction(0)) for s in stages if s.tour == tour), Fraction(0))
                   for tour in tours_of(stages)}
        fields = [("start", parse_clock), ("finish", parse_clock), ("cutoffs", parse_duration)]
        if kind in ("pedestrian", "nordic"):
            fields.append(("pen_time", parse_duration))
        if kind == "mountain":
            fields.append(("declared", parse_duration))
        for field, parse in fields:
            try:
                val = parse(d.get(field, ""))
            except ValueError:
                what = {"start": "старт", "finish": "финиш", "cutoffs": "отсечки", "pen_time": "штрафное время",
                        "declared": "заявленное время"}[field]
                issues.append(Issue(ERROR, f"«{t.team}»: {what} «{d.get(field)}» — не время (нужно "
                                           f"{'чч:мм:сс' if field in ('start', 'finish') else 'мм:сс'})",
                                    source=z.key, team=t.team, target=f"cell:{t.file}:{field}"))
                bad.append(field)
                continue
            if field in ("start", "finish", "cutoffs"):
                setattr(r, field, val if val is not None else (Fraction(0) if field == "cutoffs" else None))
            else:
                r.extra[field] = val or Fraction(0)
        r.red = kind == "nordic" and _flag(d.get("red"), RED)
        r.no_tactics = kind == "mountain" and _flag(d.get("no_tactics"), NO_TACTICS)
        if r.start is None and "start" not in r.bad and t.file in planned:
            r.start, r.planned_start = Fraction(planned[t.file]), True
        for sid in [b for b in bad if b in known]:
            s = next(s for s in stages if s.id == sid)
            issues.append(Issue(ERROR, f"«{t.team}», {s.title}: «{raw[sid]}» — не число и не «с» (снятие)",
                                source=z.key, team=t.team, target=f"cell:{t.file}:{sid}"))
        if pts and not penalty_system:
            issues.append(Issue(ERROR, f"«{t.team}»: в бесштрафовой системе штрафных баллов нет — в клетках этапов "
                                       "только «с» (снятие); штрафное время — в колонке «Штраф. время»",
                                source=z.key, team=t.team, target=f"cell:{t.file}:{next(iter(pts))}"))
            bad.append("points")
        if r.start is not None and r.finish is not None:
            finish = r.finish if r.finish >= r.start else r.finish + 86400  # финиш после полуночи
            r.distance_time = finish - r.start - r.cutoffs
            if r.distance_time <= 0:
                issues.append(Issue(ERROR, f"«{t.team}»: время на дистанции получилось {clock_text(r.distance_time)} — "
                                           "проверьте старт, финиш и отсечки", source=z.key, team=t.team,
                                    target=f"cell:{t.file}:finish"))
                r.distance_time = None
            elif kv is not None and r.distance_time > kv * 60 and status is Status.FINISHED:
                r.status, r.auto_status = Status.OVER_TIME, True
        # снятие с дистанции по правилам дисциплины (пешеходные — за снятие с этапа, СХ — за красную карточку)
        if r.status is Status.FINISHED and st["removal"] == "dsq" and (
                (kind == "pedestrian" and removals) or r.red):
            r.status, r.auto_status = Status.REMOVED, True
        rows.append(r)

    # результат: секунды (спелео, пешеходные, СХ) или баллы (горные); места
    runs, by_entry = [], {}
    for r in rows:
        ok = r.status is Status.FINISHED and r.distance_time is not None and \
            not any(b in ("start", "finish", "cutoffs", "pen_time", "declared", "points") for b in r.bad)
        score, group = None, 0
        if ok:
            points = r.total if penalty_system else Fraction(0)
            if kind == "mountain":
                rate = st["rate"]
                tactics = Fraction(0)
                if r.no_tactics and okv is not None:
                    tactics += Fraction(okv, 60) * rate / 2  # заявка по тактике не сдана — 50 % ОКВ (п. 6.5.4)
                declared = r.extra.get("declared") or Fraction(0)
                if declared:
                    tactics += Fraction(2, 10) * math.floor(abs(r.distance_time - declared) / declared * 100)
                r.extra["tactics"] = tactics
                score = _hundredths(r.distance_time / 60 * rate + points + tactics)
                group = r.removals
            else:
                score = r.distance_time + points * spp + r.extra.get("pen_time", Fraction(0))
                if kind == "pedestrian" and st["removal"] == "okv" and okv is not None:
                    score += r.removals * okv  # ОКВ за каждое снятие с этапа (п. 6.2.8 б)
                if kind == "nordic" and r.red and okv is not None:
                    score += okv  # красная карточка — штрафное время, равное ОКВ (п. 10.4.4 б)
                group = r.removals if kind == "speleo" else 0
        runs.append(SpeleoRun(r.inp.file, r.start_order, Fraction(0) if ok else None, score, Fraction(0),
                              Fraction(0), group, Status.FINISHED if ok else Status.DNS))
        by_entry[r.inp.file] = (r, score)
    ordered = []
    by_count = kind == "mountain" or zdata.get("removed_order") == "count"  # горные — всегда (п. 6.1.6)
    for p in standings(runs, 1, removed_by_count=by_count):
        r, score = by_entry[p.item.entry]
        r.place = p.place
        if score is not None:
            r.total = score
        ordered.append(r)
    empty = [r.inp.team for r in ordered if r.status is Status.FINISHED and r.place is None
             and not any(b in ("start", "finish", "cutoffs") for b in r.bad) and r.distance_time is None]
    if empty and len(empty) < len(ordered):
        issues.append(Issue(INFO, f"нет времени старта или финиша — место не присуждается: {', '.join(empty)}",
                            source=z.key))
    for r in ordered:
        if r.auto_status and r.status is Status.OVER_TIME:
            issues.append(Issue(WARNING, f"«{r.inp.team}»: время на дистанции {clock_text(r.distance_time)} больше КВ "
                                         f"({kv} мин) — место не присуждается (п. 8.18)", source=z.key, team=r.inp.team,
                                target=f"cell:{r.inp.file}:finish"))
        elif r.auto_status and r.status is Status.REMOVED:
            why = "красная карточка — результат аннулирован (п. 10.4.4 а)" if r.red else \
                f"снятий с этапов: {r.removals} — снятие с дистанции (п. 6.2.8 а)"
            issues.append(Issue(WARNING, f"«{r.inp.team}»: {why}", source=z.key, team=r.inp.team,
                                target=f"cell:{r.inp.file}:status"))
    if kind in ("pedestrian", "nordic", "mountain") and okv is None and (
            any(r.red for r in rows) or any(r.no_tactics for r in rows)
            or (kind == "pedestrian" and st["removal"] == "okv" and any(r.removals for r in rows))):
        issues.append(Issue(ERROR, "не задан КВ (ОКВ) дистанции — штрафное время по ОКВ не посчитать", source=z.key,
                            target="stages:f-kvd"))

    started = [r for r in ordered if r.status not in (Status.DNS, Status.OUT_OF_COMPETITION)]
    rank, norms = None, None
    fmt = z.rank_format
    try:
        norms = norm_edition(comp.norms_edition)
    except KeyError:
        issues.append(Issue(ERROR, f"нет редакции норм «{comp.norms_edition}» — нормативы не считаются", source=z.key,
                            target="card"))
    if comp.unofficial:  # неофициальные: ни ранга, ни нормативов (решение 038)
        norms = None
        issues.append(Issue(INFO, "неофициальные соревнования — квалификационный ранг, % от победителя и разряды "
                                  "не считаются", source=z.key))
    if norms and fmt:
        rank = qualification_rank([RankEntry(r.place, tuple(m.qual or Qual.BR for m in r.inp.members))
                                   for r in started], fmt, norms)
    scoring = "points" if kind == "mountain" else "time"
    run = ZachetRun(z, stages, ordered, rank, issues, kind="time", seconds_per_point=spp, scoring=scoring,
                    unit=unit, profile=kind, system=st["system"])
    placed = [r for r in ordered if r.place is not None]
    # ЕВСК п. 25.4: баллы, начисляемые судьями (штрафная система, горные), — не менее 6 участников, иначе 3
    ok, why = evsk_participation_ok(comp.level, len(started), None, judged_points=penalty_system)
    subjects_unknown = not ok and "не указано число субъектов" in (why or "")
    run.norms_ok = (ok or subjects_unknown) and not comp.unofficial
    if comp.unofficial:
        return run
    if why:
        issues.append(Issue(INFO, f"нормативы: {why}" + (" — проверьте по справке о количестве субъектов"
                                                          if subjects_unknown else " — разряды не присваиваются"),
                            source=z.key))
    method = comp.percent_method if scoring == "points" else PercentMethod.TIME
    if scoring == "points" and method is None:
        issues.append(Issue(INFO, "в карточке не выбрана методика «% от победителя» — процент и нормативы не "
                                  "считаются", source=z.key))
    if placed and method is not None:
        run.winner = placed[0].total
        for r in placed:
            try:
                r.percent = percent_of_winner(r.total, run.winner, method)
            except ValueError as e:
                issues.append(Issue(WARNING, f"процент не считается: {e}", source=z.key))
                break
            if r.removals:
                continue  # со снятиями с этапов дистанция пройдена не полностью — норматив не присваивается
            if norms and rank and rank.value is not None and run.norms_ok:
                d = achieved_norm(norms, z.distance_class, rank.value, r.percent, comp.level)
                r.norm = d.qual.label if d.qual else ""
    elif placed:
        run.winner = placed[0].total
    if any(r.removals for r in placed):
        issues.append(Issue(INFO, "со снятиями с этапов разряды не присваиваются — дистанция пройдена не полностью",
                            source=z.key))
    if rank and rank.value is None and rank.reason:
        issues.append(Issue(INFO, f"ранг не определяется: {rank.reason}", source=z.key))
    return run
