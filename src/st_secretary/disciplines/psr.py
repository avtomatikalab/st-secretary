"""ПСР — дистанция – комбинированная (Правила, раздел 3, часть 4).

Результат команды — сумма баллов по всем этапам: технический штраф (ТШ), временной штраф (ВШ),
премии (ПР — отрицательные баллы). Победитель — наименьшая сумма (п. 6.1.1; Положение может
установить иное). Порядок мест при равенстве задаётся в дополнительной информации (п. 6.1.2),
по умолчанию — общее правило: одинаковое место.

Здесь — только места по сумме баллов. Временной штраф по НВ и КВ этапа (время на этапе → ВШ) считает
stage_time.py (решение 035); расчёт зачёта целиком — psr_run.py.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from fractions import Fraction

from st_secretary.disciplines import Status
from st_secretary.ranking import Placed, assign_places


@dataclass(frozen=True)
class PsrStage:
    key: str  # уникальный код этапа в соревновании
    name: str
    tour: str | None = None


@dataclass
class PsrTeamCard:
    """Баллы команды по этапам. Отсутствующий этап = 0 баллов."""

    team: str
    start_order: int
    points: dict[str, Fraction] = field(default_factory=dict)
    status: Status = Status.FINISHED

    @property
    def total(self) -> Fraction:
        return sum(self.points.values(), Fraction(0))


def tour_totals(card: PsrTeamCard, stages: Sequence[PsrStage]) -> dict[str | None, Fraction]:
    """Суммы по турам (для протокола в разрезе туров)."""
    out: dict[str | None, Fraction] = {}
    for st in stages:
        out[st.tour] = out.get(st.tour, Fraction(0)) + card.points.get(st.key, Fraction(0))
    return out


def unknown_stage_keys(cards: Iterable[PsrTeamCard], stages: Sequence[PsrStage]) -> set[str]:
    """Этапы, по которым есть баллы, но которых нет в перечне этапов, — признак ошибки данных."""
    known = {s.key for s in stages}
    return {k for c in cards for k in c.points if k not in known}


def standings(cards: Sequence[PsrTeamCard], *, tie_by_start_order: bool = False) -> list[Placed[PsrTeamCard]]:
    return assign_places(
        cards,
        eligible=lambda c: c.status is Status.FINISHED,
        sort_key=lambda c: c.total,
        start_order=lambda c: c.start_order,
        tie_by_start_order=tie_by_start_order,
    )


# ------------------------------------------------------------------ Класс дистанции (п. 4.3, табл. 3)

# Минимальные показатели для класса: (КВ всей дистанции, ч; сумма баллов).
CLASS_MINIMUMS = {1: (4, 400), 2: (8, 800), 3: (30, 2000), 4: (50, 3000), 5: (70, 4000), 6: (100, 6000)}


def class_points(passed_stage_max_penalties: Iterable[Fraction | int], km: Fraction | int,
                 travel_modes: int) -> Fraction:
    """Сумма баллов для определения класса (п. 4.3.1).

    passed_stage_max_penalties — МШ этапов, пройденных участниками (без этапов, где получен МШ);
    km — пройденные километры (20 баллов за км);
    travel_modes — число видов туризма (способов передвижения), этапы которых дают более 5 %
    баллов дистанции (200 баллов за каждый).
    """
    return sum((Fraction(x) for x in passed_stage_max_penalties), Fraction(0)) + 20 * Fraction(km) + 200 * travel_modes


def distance_class(points: Fraction, control_time_hours: Fraction) -> int | None:
    """Наивысший класс, минимальные показатели которого выполнены по обоим пунктам табл. 3."""
    result = None
    for cls, (kv, pts) in sorted(CLASS_MINIMUMS.items()):
        if Fraction(control_time_hours) >= kv and Fraction(points) >= pts:
            result = cls
    return result


# ------------------------------------------------------------------ Технический штраф этапа (п. 4.1.3)

TECH_PENALTY_STEPS = (10, 20, 30, 40, 50, 60, 70, 80)


def stage_tech_penalty(factor_scores: Iterable[int], coefficient: Fraction) -> int:
    """ТШ этапа: сумма оценок 8 факторов (табл. 2: 0, 5 или 10) × коэффициент 0,5–1,0,
    округление в сторону увеличения до 10, 20 … 80."""
    scores = list(factor_scores)
    if len(scores) != 8 or any(s not in (0, 5, 10) for s in scores):
        raise ValueError("Нужно 8 оценок факторов, каждая 0, 5 или 10 (табл. 2 части 4)")
    k = Fraction(coefficient)
    if not Fraction(1, 2) <= k <= 1:
        raise ValueError("Коэффициент значимости этапа должен быть от 0,5 до 1,0")
    value = sum(scores) * k
    for step in TECH_PENALTY_STEPS:
        if value <= step:
            return step
    return TECH_PENALTY_STEPS[-1]  # сумма факторов не превышает 80 — сюда не попадём
