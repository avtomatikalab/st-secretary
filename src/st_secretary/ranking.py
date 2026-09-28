"""Распределение мест — общее правило (Правила, раздел 3, п. 8.18) с настройками дисциплин.

* Одинаковый результат — одинаковое место; в протоколе такие участники идут в порядке старта;
  после них пропускается столько мест, сколько участников разделили место, минус один.
* Не пересёк финиш, превысил контрольное время, снят с дистанции — место не присуждается.
* Порядок сортировки задаёт профиль дисциплины: например, в спелео и горных участники со снятиями
  с этапов идут после прошедших дистанцию полностью, а в горных при равенстве баллов выше тот,
  кто стартовал раньше (tie_by_start_order=True).
"""

from __future__ import annotations

from collections.abc import Callable, Hashable, Sequence
from dataclasses import dataclass
from typing import Generic, TypeVar

T = TypeVar("T")


@dataclass(frozen=True)
class Placed(Generic[T]):
    item: T
    place: int | None  # None — место не присуждено


def assign_places(
    items: Sequence[T],
    *,
    eligible: Callable[[T], bool],
    sort_key: Callable[[T], Hashable],
    start_order: Callable[[T], int],
    tie_by_start_order: bool = False,
) -> list[Placed[T]]:
    """Вернуть участников в порядке протокола с местами.

    sort_key — меньше значит лучше (кортеж: например, (число снятий, результат)).
    Неучаствующие в распределении мест идут в конце в порядке старта.
    """
    ranked = sorted((i for i in items if eligible(i)), key=lambda i: (sort_key(i), start_order(i)))
    others = sorted((i for i in items if not eligible(i)), key=start_order)
    out: list[Placed[T]] = []
    prev_key = object()
    place = 0
    for n, item in enumerate(ranked, start=1):
        key = sort_key(item)
        if tie_by_start_order or key != prev_key:
            place = n
        out.append(Placed(item, place))
        prev_key = key
    out.extend(Placed(i, None) for i in others)
    return out
