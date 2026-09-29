"""Склонение ФИО для документов: «Дана Иванову Ивану Ивановичу…» (справки о судействе).

Правила — по окончаниям русских фамилий, имён и отчеств; пол — по отчеству (или по имени). Необычные
фамилии (Шевченко, Черных, Дюма) не склоняются. Документы всё равно открываются в Word, где редкий
случай можно поправить руками.
"""

from __future__ import annotations

from st_secretary.textclean import sex_from_patronymic

_VOWELS = "аеёиоуыэюя"


def guess_sex(fio: str) -> str | None:
    parts = fio.split()
    if len(parts) >= 3 and (s := sex_from_patronymic(parts[-1])):
        return s
    if len(parts) >= 2:
        name = parts[1].lower()
        if name.endswith(("а", "я")) and name not in ("никита", "илья", "фома", "лука", "кузьма", "савва", "данила"):
            return "ж"
        if name.endswith("ь") and name in ("любовь",):
            return "ж"
        return "м"
    return None


def _patronymic(p: str) -> str:
    low = p.lower()
    if low.endswith(("ич",)):
        return p + "у"
    if low.endswith("на"):
        return p[:-1] + "е"
    return p  # «оглы», «кызы» не склоняются


def _name(n: str, sex: str | None) -> str:
    low = n.lower()
    if low.endswith("ия"):
        return n[:-1] + "и"  # Мария → Марии, Юлия → Юлии
    if low.endswith(("а", "я")):
        return n[:-1] + "е"  # Анна → Анне, Никита → Никите, Илья → Илье
    if sex == "ж":
        return n[:-1] + "и" if low.endswith("ь") else n  # Любовь → Любови; Рахиль не трогаем
    if low.endswith(("й", "ь")):
        return n[:-1] + "ю"  # Андрей → Андрею, Игорь → Игорю
    if low[-1] in _VOWELS:
        return n
    return n + "у"  # Иван → Ивану


def _surname(s: str, sex: str | None) -> str:
    low = s.lower()
    if low.endswith(("ых", "их", "ко", "аго", "яго")) or low[-1] in "еиоуэю":
        return s  # Черных, Шевченко — не склоняются
    if sex == "ж":
        if low.endswith(("ова", "ева", "ёва", "ина", "ына")):
            return s[:-1] + "ой"  # Иванова → Ивановой
        if low.endswith("ая"):
            return s[:-2] + "ой"  # Толстая, Бельская → Толстой, Бельской
        if low.endswith("яя"):
            return s[:-2] + "ей"
        if low.endswith(("а", "я")):
            return s[:-1] + "е"  # Бурмага → Бурмаге, Зозуля → Зозуле
        return s  # женские на согласный (Кузьмич, Шевчук) не склоняются
    if low.endswith(("ский", "цкий", "ой", "ый", "ий")):
        return s[:-2] + "ому"  # Бельский → Бельскому, Толстой → Толстому
    if low.endswith(("й", "ь")):
        return s[:-1] + "ю"  # Гайдай → Гайдаю, Коваль → Ковалю
    if low.endswith(("а", "я")):
        return s[:-1] + "е"  # Бурмага → Бурмаге
    return s + "у"  # Иванов → Иванову


def dative(fio: str, sex: str | None = None) -> str:
    """«Иванов Иван Иванович» → «Иванову Ивану Ивановичу»; «Иванова Анна Сергеевна» → «Ивановой Анне Сергеевне»."""
    parts = fio.split()
    if not parts:
        return fio
    sex = sex or guess_sex(fio)
    out = [_surname(parts[0], sex)]
    if len(parts) >= 2:
        out.append(_name(parts[1], sex))
    if len(parts) >= 3:
        out += [_patronymic(p) for p in parts[2:]]
    return " ".join(out)


def he_she(sex: str | None) -> tuple[str, str]:
    """(«он», «участвовал») или («она», «участвовала»)."""
    return ("она", "участвовала") if sex == "ж" else ("он", "участвовал")
