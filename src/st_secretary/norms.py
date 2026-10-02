"""Выполненный норматив: «результат в процентах от результата победителя (не более)».

Строка таблицы выбирается по классу дистанции и квалификационному рангу. Для каждого класса
в нормах отмечен свой диапазон строк (они перекрываются): например, для 3 класса — от «20-24,9»
до «160 и более». Ранг ниже первой строки класса — нормы не выполняются.

Процент сравнивается с порогом точно, без округления.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from fractions import Fraction

from st_secretary.qualification import Qual
from st_secretary.reference import Level, NormEdition, NormRow

# Порядок проверки: от старшего разряда к младшему.
_ORDER = (Qual.I, Qual.II, Qual.III, Qual.Y1, Qual.Y2, Qual.Y3)


class PercentMethod(Enum):
    """Как считать «процент от результата победителя».

    Для результатов во времени всё однозначно. Для балльных результатов (ПСР, горные) методика
    в документах не определена — её нужно выбрать явно в настройках соревнования; система не
    выберет её сама.
    """

    TIME = "time"  # результат / результат победителя × 100 (время, меньше — лучше)
    # 100 % + превышение над победителем в долях модуля результата победителя:
    # (1 + (r − w) / |w|) × 100. Так посчитан протокол ПСР г. Красноярска 2024 г.
    POINTS_RELATIVE_TO_WINNER = "points_relative_to_winner"


def percent_of_winner(result: Fraction, winner: Fraction, method: PercentMethod) -> Fraction:
    result, winner = Fraction(result), Fraction(winner)
    if method is PercentMethod.TIME:
        if winner <= 0:
            raise ValueError("Время победителя должно быть больше нуля")
        return result / winner * 100
    if method is PercentMethod.POINTS_RELATIVE_TO_WINNER:
        if winner == 0:
            raise ValueError("Результат победителя равен 0 баллов — процент по этой методике не определён")
        return (1 + (result - winner) / abs(winner)) * 100
    raise ValueError(f"Неизвестная методика {method}")


def norm_row(norms: NormEdition, distance_class: int, rank: Fraction | None) -> NormRow | None:
    """Строка таблицы норм для класса и ранга; None — нормы не выполняются."""
    if rank is None:
        return None
    try:
        first, last = norms.class_rows[distance_class]
    except KeyError:
        raise ValueError(f"В нормах {norms.edition} нет класса дистанции {distance_class}") from None
    candidates = [r for r in norms.rows if first <= r.min_rank <= last and r.min_rank <= rank]
    return candidates[-1] if candidates else None


@dataclass(frozen=True)
class NormDecision:
    qual: Qual | None
    row: NormRow | None
    notes: tuple[str, ...] = ()


JUNIOR_UNTIL = 18  # юношеские разряды — до 18 лет (база знаний, «Возраст выполнения»)


def achieved_norm(
    norms: NormEdition,
    distance_class: int,
    rank: Fraction | None,
    percent: Fraction,
    level: Level,
    *,
    age: int | None = None,
    nordic_walking: bool = False,
) -> NormDecision:
    """Высший разряд, норматив которого выполнен.

    age — возраст в календарный год соревнований; если не задан (норматив команды),
    юношеские разряды не рассматриваются, возрастные ограничения не проверяются.
    """
    row = norm_row(norms, distance_class, rank)
    if row is None:
        return NormDecision(None, None, ("ранг ниже диапазона таблицы для этого класса",))
    notes = []
    for q in _ORDER:
        limit = row.thresholds.get(q)
        if limit is None or percent > limit:
            continue
        if level < norms.min_level[q]:
            notes.append(f"{q.label}: не допускается уровнем соревнований")
            continue
        if q.is_junior:
            if age is None or age >= JUNIOR_UNTIL:
                continue
            if age < norms.min_age["junior"]:
                continue
        elif age is not None:
            if age < norms.min_age[q.name]:
                notes.append(f"{q.label}: выполняется с {norms.min_age[q.name]} лет")
                continue
        if nordic_walking:
            if q is Qual.I and not norms.nordic_i_allowed:
                continue
            if not q.is_junior and age is not None and age < norms.nordic_min_age:
                continue
        return NormDecision(q, row, tuple(notes))
    return NormDecision(None, row, tuple(notes))
