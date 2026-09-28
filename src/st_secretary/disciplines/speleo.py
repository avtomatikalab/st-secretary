"""Спелео — дистанция – спелео, – связка, – группа (Правила, раздел 3, часть 8).

Результат = время на дистанции − отсечки + штрафные баллы × временной эквивалент (п. 6.2, 6.4).
Эквивалент балла: 15 с, если расчётное время дистанции до 30 минут, иначе 30 с — если в Условиях
не указано иное. Баллы бывают дробными (0,3; 0,1 — приложение 2 к части 8).
Участники со снятием с одного и более этапов занимают места после прошедших дистанцию полностью
(п. 6.5), если в Условиях не оговорено иное.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from fractions import Fraction

from st_secretary.disciplines import Status
from st_secretary.ranking import Placed, assign_places


def point_seconds(expected_minutes: Fraction | int, override: int | None = None) -> int:
    """Временной эквивалент одного штрафного балла, с."""
    if override is not None:
        return override
    return 15 if Fraction(expected_minutes) <= 30 else 30


@dataclass(frozen=True)
class SpeleoRun:
    """Прохождение дистанции участником (связкой, группой)."""

    entry: str
    start_order: int
    start: Fraction | None  # секунды от начала суток (единое судейское время)
    finish: Fraction | None
    cutoffs: Fraction = Fraction(0)  # сумма отсечек, с
    penalty_points: Fraction = Fraction(0)
    removals: int = 0  # число снятий с этапов
    status: Status = Status.FINISHED

    def time_on_distance(self) -> Fraction:
        if self.start is None or self.finish is None:
            raise ValueError(f"{self.entry}: нет времени старта или финиша")
        t = self.finish - self.start - self.cutoffs
        if t <= 0:
            raise ValueError(f"{self.entry}: время на дистанции получилось {t} с — проверьте старт, финиш, отсечки")
        return t

    def result(self, seconds_per_point: int) -> Fraction:
        return self.time_on_distance() + self.penalty_points * seconds_per_point


def standings(
    runs: Sequence[SpeleoRun], seconds_per_point: int, *, removed_by_count: bool = False
) -> list[Placed[SpeleoRun]]:
    """Места. removed_by_count=True — среди участников со снятиями выше тот, у кого их меньше
    (по аналогии с горными дистанциями; включать, только если так записано в Условиях)."""

    def key(r: SpeleoRun):
        group = r.removals if removed_by_count else (1 if r.removals else 0)
        return (group, r.result(seconds_per_point))

    return assign_places(
        runs,
        eligible=lambda r: r.status is Status.FINISHED,
        sort_key=key,
        start_order=lambda r: r.start_order,
    )
