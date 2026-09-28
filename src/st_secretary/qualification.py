"""Спортивные разряды и звания: единые коды, порядок старшинства, разбор любых написаний."""

from __future__ import annotations

import re
from enum import IntEnum


class Qual(IntEnum):
    """Спортивная квалификация. Значение отражает старшинство: больше — выше."""

    BR = 0  # без разряда
    Y3 = 1  # III юношеский
    Y2 = 2  # II юношеский
    Y1 = 3  # I юношеский
    III = 4
    II = 5
    I = 6  # noqa: E741 — так разряд называется в документах
    KMS = 7
    MS = 8  # МС; МСМК и ЗМС для расчётов по нормам приравниваются к МС

    @property
    def label(self) -> str:
        """Написание в протоколах."""
        return _LABELS[self]

    @property
    def is_junior(self) -> bool:
        return self in (Qual.Y1, Qual.Y2, Qual.Y3)


_LABELS = {
    Qual.BR: "б/р",
    Qual.Y3: "3ю",
    Qual.Y2: "2ю",
    Qual.Y1: "1ю",
    Qual.III: "III",
    Qual.II: "II",
    Qual.I: "I",
    Qual.KMS: "КМС",
    Qual.MS: "МС",
}

# Ключ — нормализованная строка (нижний регистр, без пробелов, точек и дефисов, латинские I → «i»).
_ALIASES = {
    "": Qual.BR, "бр": Qual.BR, "б/р": Qual.BR, "б\\р": Qual.BR, "безразряда": Qual.BR,
    "нет": Qual.BR, "-": Qual.BR, "—": Qual.BR, "0": Qual.BR, "br": Qual.BR,
    "3ю": Qual.Y3, "3юн": Qual.Y3, "iiiю": Qual.Y3, "iiiюн": Qual.Y3, "y3": Qual.Y3,
    "2ю": Qual.Y2, "2юн": Qual.Y2, "iiю": Qual.Y2, "iiюн": Qual.Y2, "y2": Qual.Y2,
    "1ю": Qual.Y1, "1юн": Qual.Y1, "iю": Qual.Y1, "iюн": Qual.Y1, "y1": Qual.Y1,
    "iii": Qual.III, "3": Qual.III, "3р": Qual.III, "3разряд": Qual.III,
    "ii": Qual.II, "2": Qual.II, "2р": Qual.II, "2разряд": Qual.II,
    "i": Qual.I, "1": Qual.I, "1р": Qual.I, "1разряд": Qual.I,
    "кмс": Qual.KMS, "kms": Qual.KMS,
    "мс": Qual.MS, "ms": Qual.MS, "мср": Qual.MS, "мсмк": Qual.MS, "змс": Qual.MS,
}

# Кириллические и прочие «похожие» символы, которыми набирают римские цифры.
_ROMAN_LOOKALIKES = str.maketrans({"І": "i", "і": "i", "Ӏ": "i", "|": "i"})


def _normalize(text: str) -> str:
    s = str(text).strip().translate(_ROMAN_LOOKALIKES).lower()
    s = s.replace("ё", "е")
    s = re.sub(r"[\s.\-–_]+", "", s)
    s = s.replace("юношеский", "ю").replace("юнош", "ю")
    return s


def parse_qual(text: str | int | float | None) -> Qual:
    """Разобрать разряд из заявки или протокола.

    Числа 1, 2, 3 трактуются как взрослые разряды I, II, III (так их записывает СЕКРЕТАРЬ_ST).
    Нераспознанное написание — ошибка: разряд влияет на допуск и ранг, угадывать нельзя.
    """
    if text is None:
        return Qual.BR
    if isinstance(text, float) and text.is_integer():
        text = int(text)
    key = _normalize(str(text))
    try:
        return _ALIASES[key]
    except KeyError:
        raise ValueError(f"Не удалось распознать разряд: {text!r}") from None


def parse_members_with_quals(composition: str) -> list[tuple[str, Qual]]:
    """Разобрать строку состава вида «Иванов Иван Иванович(КМС), Петров Пётр(б/р)».

    Возвращает пары (ФИО, разряд). Используется при чтении протоколов прошлых лет.
    """
    result = []
    for m in re.finditer(r"([^,()]+?)\s*\(([^()]*)\)", composition):
        name = " ".join(m.group(1).split())
        result.append((name, parse_qual(m.group(2))))
    if not result and composition.strip():
        raise ValueError(f"Не найден ни один участник с разрядом в скобках: {composition!r}")
    return result
