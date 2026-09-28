"""Время: разбор, форматирование, перевод из формата Excel. Всё хранится в секундах (Fraction)."""

from __future__ import annotations

import math
import re
from fractions import Fraction

_DAY = 86400


def parse_time(text: str) -> Fraction:
    """«1:02:03», «02:03», «1:02:03,4», «1:02:03.4» → секунды."""
    s = str(text).strip().replace(",", ".")
    m = re.fullmatch(r"(?:(\d+):)?(\d{1,2}):(\d{1,2}(?:\.\d+)?)", s)
    if not m:
        raise ValueError(f"Не удалось разобрать время: {text!r}")
    h = int(m.group(1) or 0)
    mnt = int(m.group(2))
    sec = Fraction(m.group(3))
    if mnt >= 60 or sec >= 60:
        raise ValueError(f"Минуты и секунды должны быть меньше 60: {text!r}")
    return h * 3600 + mnt * 60 + sec


def from_excel(day_fraction: float, *, precision: Fraction = Fraction(1, 10)) -> Fraction:
    """Время из ячейки Excel (доля суток) → секунды, округлённые до precision (по умолчанию 0,1 с)."""
    seconds = Fraction(day_fraction) * _DAY
    steps = math.floor(seconds / precision + Fraction(1, 2))
    return steps * precision


def format_time(seconds: Fraction, *, tenths: bool = False) -> str:
    """Секунды → «Ч:ММ:СС» (или «Ч:ММ:СС,д»). Отрицательное время — ошибка данных."""
    if seconds < 0:
        raise ValueError("Отрицательное время")
    unit = Fraction(1, 10) if tenths else Fraction(1)
    total = math.floor(Fraction(seconds) / unit + Fraction(1, 2))
    if tenths:
        whole, d = divmod(total, 10)
    else:
        whole, d = total, None
    h, rest = divmod(whole, 3600)
    m, s = divmod(rest, 60)
    out = f"{h}:{m:02d}:{s:02d}"
    return f"{out},{d}" if d is not None else out
