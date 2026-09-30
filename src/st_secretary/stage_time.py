"""ПСР: время на этапе и временной штраф (ВШ) по НВ и КВ этапа.

Правила вида спорта (2021), дистанции комбинированные, п. 1.5: ТШ — наибольший технический штраф этапа, ВШ —
наибольший временной, МШ = ТШ + ВШ. Пока время прохождения этапа не больше НВ, ВШ нет; сверх КВ команда
снимается с этапа и получает МШ. Как расти ВШ между НВ и КВ, Правила не говорят — это задают Условия
соревнования; в программе — правило этапа «N балл(ов) за каждые M с сверх НВ» (по умолчанию 1 балл за 30 с),
но не больше ВШ; неполный интервал — за полный (по умолчанию) или не считается (настройка зачёта).

Время на этапе = убытие − прибытие − отсечки (ожидание очереди на занятый этап) — с телефона судьи этапа или со
страницы судьи, открытой на ноутбуке секретаря. Судья вносит только технический штраф (премию — со знаком
минус); итог этапа = техштраф + ВШ программа кладёт в таблицу по тем же правилам, что баллы с телефона: клетка
пустая или в ней прежний итог программы — пишется; секретарь вписал своё (протест) — не затирается, а на
странице результатов видно расхождение.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from fractions import Fraction

from st_secretary.psr_run import Stage, parse_points, points_text
from st_secretary.time_run import duration_text, parse_duration

DAY = 24 * 3600


def clock_seconds(t) -> int | None:
    """«14:05:30» или «14:05» (часы телефона) → секунды от начала суток; иначе None."""
    parts = str(t or "").strip().split(":")
    if len(parts) not in (2, 3) or not all(p.isdigit() for p in parts):
        return None
    h, m, s = (int(p) for p in (parts + ["0"])[:3])
    return h * 3600 + m * 60 + s if h < 24 and m < 60 and s < 60 else None


def stage_seconds(rec: dict) -> int | None:
    """Время на этапе, с: убытие − прибытие − отсечки (через полночь — тоже); нет прибытия или убытия — None."""
    a, b = clock_seconds(rec.get("arrive")), clock_seconds(rec.get("leave"))
    if a is None or b is None:
        return None
    try:
        cut = parse_duration(rec.get("cutoff"))
    except ValueError:
        cut = Fraction(0)
    return max(0, int((b - a) % DAY - cut))


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
    """Итог этапа по записи судьи: total — что идёт в таблицу (None — ещё не посчитать), text — из чего сложилось."""
    total: Fraction | None
    text: str
    secs: int | None = None


def score(stage: Stage, rec: dict, full_intervals: bool = True) -> StageScore:
    """Итог этапа из записи судьи: снята или сверх КВ — МШ; иначе техштраф + ВШ по времени на этапе."""
    secs = stage_seconds(rec)
    mx = stage.max_penalty
    over_kv = secs is not None and stage.kv_minutes is not None and secs > stage.kv_minutes * 60
    if rec.get("removed") or over_kv:
        why = "снята с этапа" if rec.get("removed") else f"превышено КВ ({duration_text(Fraction(secs or 0))})"
        return StageScore(mx, f"{why} — МШ {points_text(mx)}" if mx is not None else f"{why} — МШ этапа не задан",
                          secs)
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
                             f"(на этапе {duration_text(Fraction(secs))})", secs)


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
        sc = score(stage, rec, full_intervals(zdata))
        if sc.total is None:
            continue
        t = teams.setdefault(file, {})
        cell = str(t.get("points", {}).get(stage.id, "")).strip()
        prev = str(t.get("auto", {}).get(stage.id, "")).strip()
        new = points_text(sc.total)
        if cell in ("", prev):
            t.setdefault("points", {})[stage.id] = new
            t.setdefault("auto", {})[stage.id] = new
        elif not same_points(cell, new):
            conflicts += 1
    return conflicts
