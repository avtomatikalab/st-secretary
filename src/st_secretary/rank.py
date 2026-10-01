"""Квалификационный ранг вида программы и условия ЕВСК о числе участников.

Нормы по виду спорта, «иные условия», п. 1: баллы начисляются за каждого спортсмена, занявшего
с 1 по 6 место, при участии в виде программы не менее 6 спортсменов (связок, экипажей, групп).
Для связок баллы делятся на 2, для групп — на 4, а если в группе больше 4 человек — на число
спортсменов в группе (сноска «*» к таблице баллов).

Проверено на протоколе Всероссийских соревнований ПСР-2025 (группы по 6 человек): ранг 71,7.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from fractions import Fraction

from st_secretary.qualification import Qual
from st_secretary.reference import Level, NormEdition

# Формат состава вида программы (rank_format у дисциплины ВРВС и у зачёта): от него зависят деление команды на
# спортсменов и связки, деление баллов ранга, блоки в расписании стартов. Других значений нет.
INDIVIDUAL, PAIR, GROUP, CREW = "individual", "pair", "group", "crew"
RANK_FORMATS = (INDIVIDUAL, PAIR, GROUP, CREW)


@dataclass(frozen=True)
class RankEntry:
    """Участник вида программы (спортсмен, связка, группа) для расчёта ранга."""

    place: int | None  # None — место не присуждено (снят, не финишировал, превысил КВ)
    members: tuple[Qual, ...]


@dataclass(frozen=True)
class RankResult:
    value: Fraction | None  # None — ранг не определяется
    reason: str | None = None
    contributions: tuple[Fraction, ...] = field(default=())  # вклад каждого учтённого участника

    def formatted(self) -> str:
        """Как в протоколе: одна цифра после запятой."""
        if self.value is None:
            return "не определялся"
        tenths = math.floor(self.value * 10 + Fraction(1, 2))  # округление «пять — вверх»
        return f"{tenths // 10},{tenths % 10}" if tenths % 10 else f"{tenths // 10}"


def rank_divisor(rank_format: str, team_size: int) -> int:
    """На сколько делятся личные баллы для связки/группы/экипажа."""
    if rank_format == INDIVIDUAL:
        return 1
    if rank_format == PAIR:
        return 2
    if rank_format == GROUP:
        return max(4, team_size)
    if rank_format == CREW:
        if team_size == 2:
            return 2
        if team_size > 4:
            return team_size
        raise ValueError(f"Экипаж из {team_size} человек: правило деления баллов ранга не определено")
    raise ValueError(f"Неизвестный формат ранга {rank_format!r}")


def qualification_rank(entries: Sequence[RankEntry], rank_format: str, norms: NormEdition) -> RankResult:
    """Квалификационный ранг вида программы по выбранной редакции норм."""
    if len(entries) < norms.rank_min_participants:
        return RankResult(
            None,
            f"в виде программы участвовало {len(entries)} (нужно не менее {norms.rank_min_participants})",
        )
    contributions = []
    for e in entries:
        if e.place is None or e.place > norms.rank_max_place:
            continue
        points = sum((norms.rank_points[q] for q in e.members), Fraction(0))
        contributions.append(points / rank_divisor(rank_format, len(e.members)))
    return RankResult(sum(contributions, Fraction(0)), None, tuple(contributions))


def evsk_participation_ok(
    level: Level, participants: int, subjects: int | None, judged_points: bool
) -> tuple[bool, str | None]:
    """Условие выполнения норм по числу участников — Положение о ЕВСК, п. 25 (ред. от 09.07.2025).

    judged_points — результат содержит баллы/очки, начисляемые судьями (ПСР, горные, штрафная система).
    Возвращает (выполнено, пояснение).
    """
    if level is Level.ALL_RUSSIAN:
        need, need_subj, ref = 6, 6, "п. 25.2.1"
    elif level is Level.INTERREGIONAL:
        need, need_subj, ref = 6, 4, "п. 25.3.1"
    else:
        need, need_subj, ref = (6 if judged_points else 3), None, ("п. 25.4.2" if judged_points else "п. 25.4.1")
    if participants < need:
        return False, f"участников {participants}, по {ref} Положения о ЕВСК нужно не менее {need}"
    if need_subj is not None:
        if subjects is None:
            return False, f"не указано число субъектов РФ ({ref}: не менее {need_subj})"
        if subjects < need_subj:
            return False, f"субъектов РФ {subjects}, по {ref} Положения о ЕВСК нужно не менее {need_subj}"
    return True, None
