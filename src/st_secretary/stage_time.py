"""ПСР: время на этапе и временной штраф (ВШ) по НВ и КВ этапа.

Правила вида спорта (2021), дистанции комбинированные, п. 1.5: ТШ — наибольший технический штраф этапа, ВШ —
наибольший временной, МШ = ТШ + ВШ. Пока время прохождения этапа не больше НВ, ВШ нет; сверх КВ команда
снимается с этапа и получает МШ. Как расти ВШ между НВ и КВ, Правила не говорят — это задают Условия
соревнования; в программе — правило этапа «N балл(ов) за каждые M с сверх НВ» (по умолчанию 1 балл за 30 с),
но не больше ВШ; неполный интервал — за полный (по умолчанию) или не считается (настройка зачёта).

Время на этапе = убытие − прибытие − отсечки (ожидание очереди на занятый этап) — с телефона судьи этапа или со
страницы судьи, открытой на ноутбуке секретаря. Ожидание очереди вычитается, если так настроен зачёт (по
умолчанию — да; «не вычитать» — очередь на телефоне только задаёт порядок, время этапа — от прибытия до убытия).
Судья вносит только технический штраф (премию — со знаком минус); итог этапа = техштраф + ВШ программа кладёт
в таблицу по тем же правилам, что баллы с телефона: клетка пустая или в ней прежний итог программы — пишется;
секретарь вписал своё (протест) — не затирается, а на странице результатов видно расхождение.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from fractions import Fraction

from st_secretary.psr_run import Stage, parse_points, points_text
from st_secretary.time_run import duration_text, parse_duration

DAY = 24 * 3600
NIGHT_FROM, NIGHT_TO = 20 * 3600, 6 * 3600  # «через полночь» — только прибытие после 20:00 и убытие до 06:00


def clock_seconds(t) -> int | None:
    """«14:05:30» или «14:05» (часы телефона) → секунды от начала суток; иначе None."""
    parts = str(t or "").strip().split(":")
    if len(parts) not in (2, 3) or not all(p.isdigit() for p in parts):
        return None
    h, m, s = (int(p) for p in (parts + ["0"])[:3])
    return h * 3600 + m * 60 + s if h < 24 and m < 60 and s < 60 else None


def span(a: int, b: int) -> int | None:
    """Сколько секунд от a до b; через полночь — только если правдоподобно (прибытие после 20:00, убытие до 06:00),
    иначе это опечатка во времени — None."""
    if b >= a:
        return b - a
    return b - a + DAY if a >= NIGHT_FROM and b <= NIGHT_TO else None


def times_problem(rec: dict) -> str:
    """Убытие раньше прибытия (и это не ночь) — опечатка во времени: итог этапа не считается. Пусто — всё в порядке."""
    a, b = clock_seconds(rec.get("arrive")), clock_seconds(rec.get("leave"))
    if a is None or b is None or span(a, b) is not None:
        return ""
    return f"убытие ({rec.get('leave')}) раньше прибытия ({rec.get('arrive')}) — проверьте время"


def subtract_wait(zdata: dict) -> bool:
    """Настройка зачёта: ожидание очереди на занятый этап вычитается из времени, как отсечка (по умолчанию). «wait_cut»
    = «no» — не вычитается: ни из времени этапа (ПСР, НВ/КВ), ни в «Отсечки» дистанции (спелео, пешеходные)."""
    return zdata.get("wait_cut") != "no"


def stage_seconds(rec: dict, subtract_wait: bool = True) -> int | None:
    """Время на этапе, с: убытие − прибытие − отсечки (через полночь — только ночью); нет прибытия или убытия,
    или убытие раньше прибытия — None. subtract_wait=False — ожидание очереди (отсечки) не вычитается."""
    a, b = clock_seconds(rec.get("arrive")), clock_seconds(rec.get("leave"))
    if a is None or b is None:
        return None
    d = span(a, b)
    if d is None:
        return None
    try:
        cut = parse_duration(rec.get("cutoff")) if subtract_wait else Fraction(0)
    except ValueError:
        cut = Fraction(0)
    return max(0, int(d - cut))


def time_penalty(stage: Stage, secs: int, full_intervals: bool = True) -> Fraction:
    """ВШ за время secs на этапе: 0 до НВ, дальше vsh_points за каждые vsh_step с (неполный — за полный или нет),
    не больше ВШ."""
    over = secs - (stage.nv_minutes or 0) * 60
    if over <= 0:
        return Fraction(0)
    k = math.ceil(over / stage.vsh_step) if full_intervals else math.floor(over / stage.vsh_step)
    return min(stage.time_max or Fraction(0), k * stage.vsh_points)


@dataclass
class StageScore:
    """Итог этапа по записи судьи: total — что идёт в таблицу (None — ещё не посчитать), text — из чего сложилось,
    check — что секретарю проверить (пусто — всё в порядке)."""
    total: Fraction | None
    text: str
    secs: int | None = None
    check: str = ""


def waiting_check(rec: dict, subtract_wait: bool = True) -> str:
    """Команда «ждала очереди», а ожидание закрыли вместе с убытием: время на этапе 0 — похоже, судья забыл
    «Начала этап». Пусто — проверять нечего (и когда ожидание не вычитается — на время оно не влияет)."""
    if not subtract_wait:
        return ""
    try:
        cut = parse_duration(rec.get("cutoff"))
    except ValueError:
        return ""
    if not rec.get("leave") or cut <= 0:
        return ""
    if (rec.get("started") and rec.get("started") == rec.get("leave")) or stage_seconds(rec) == 0:
        return ("всё время на этапе — ожидание очереди (время на этапе 0): «Начала этап» не отмечено — проверьте, "
                "во сколько команда начала этап")
    return ""


def score(stage: Stage, rec: dict, full_intervals: bool = True, subtract_wait: bool = True) -> StageScore:
    """Итог этапа из записи судьи: снята или сверх КВ — МШ; иначе техштраф + ВШ по времени на этапе."""
    secs = stage_seconds(rec, subtract_wait)
    mx = stage.max_penalty
    over_kv = secs is not None and stage.kv_minutes is not None and secs > stage.kv_minutes * 60
    if rec.get("removed") or over_kv:
        why = "снята с этапа" if rec.get("removed") else f"превышено КВ ({duration_text(Fraction(secs or 0))})"
        return StageScore(mx, f"{why} — МШ {points_text(mx)}" if mx is not None else f"{why} — МШ этапа не задан",
                          secs)
    bad = times_problem(rec)
    if bad:
        return StageScore(None, bad, None, bad)
    try:
        tech = parse_points(rec.get("points"))
    except ValueError:
        return StageScore(None, f"техштраф «{rec.get('points')}» — не число", secs)
    if tech is None or secs is None:
        return StageScore(None, "ждёт техштрафа и времени убытия" if tech is None else "нет времени прибытия или "
                                "убытия", secs)
    vsh = time_penalty(stage, secs, full_intervals)
    total = tech + vsh
    return StageScore(total, f"тех. {points_text(tech)} + время {points_text(vsh)} = {points_text(total)} "
                             f"(на этапе {duration_text(Fraction(secs))})", secs, waiting_check(rec, subtract_wait))


def score_for(zdata: dict, stage: Stage, rec: dict) -> StageScore:
    """Итог этапа по настройкам зачёта (неполный интервал ВШ, вычитать ли ожидание очереди)."""
    return score(stage, rec, full_intervals(zdata), subtract_wait(zdata))


def same_points(a: str, b: str) -> bool:
    try:
        return parse_points(a) == parse_points(b)
    except ValueError:
        return a.strip() == b.strip()


def full_intervals(zdata: dict) -> bool:
    return zdata.get("vsh_round") != "down"


def refresh(zdata: dict, stage: Stage, files: list[str] | None = None) -> int:
    """Итоги этапа из записей судьи → таблица: клетка пустая или в ней прежний итог программы — пишется новый.
    Возвращает, сколько команд с расхождением (секретарь вписал своё)."""
    if not stage.auto:
        return 0
    log = zdata.get("judge", {}).get(stage.id, {})
    teams = zdata.setdefault("teams", {})
    conflicts = 0
    for file in files if files is not None else list(log):
        rec = log.get(file)
        if rec is None:
            continue
        sc = score_for(zdata, stage, rec)
        t = teams.setdefault(file, {})
        cell = str(t.get("points", {}).get(stage.id, "")).strip()
        prev = str(t.get("auto", {}).get(stage.id, "")).strip()
        if sc.total is None:
            if sc.check and prev and cell == prev:  # время стало с ошибкой — прежний итог программы убрать
                t.get("points", {}).pop(stage.id, None)
                t.get("auto", {}).pop(stage.id, None)
            continue
        new = points_text(sc.total)
        if cell in ("", prev):
            t.setdefault("points", {})[stage.id] = new
            t.setdefault("auto", {})[stage.id] = new
        elif not same_points(cell, new):
            conflicts += 1
    return conflicts
