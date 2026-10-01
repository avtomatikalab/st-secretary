"""Склонение ФИО и должностей для документов: «Дана Иванову Ивану Ивановичу…» (справки о судействе),
«в лице директора Иванова Ивана Ивановича», «в качестве главного судьи» (договоры).

Правила — по окончаниям русских фамилий, имён и отчеств; пол — по отчеству (или по имени). Необычные
фамилии (Шевченко, Черных, Дюма) не склоняются. Документы всё равно открываются в Word, где редкий
случай можно поправить руками.
"""

from __future__ import annotations

from st_secretary.textclean import sex_from_patronymic

_VOWELS = "аеёиоуыэюя"
_HUSHING = "гкхжшчщ"  # после них — «и», а не «ы»: Ольга → Ольги, Бурмага → Бурмаги
# Имена с беглой гласной: Павел → Павла, Павлу; Лев → Льва; Пётр → Петра.
_FLEETING = {"павел": "Павл", "лев": "Льв", "пётр": "Петр", "петр": "Петр"}


def _keep_case(stem: str, word: str) -> str:
    return stem if word[:1].isupper() else stem.lower()


def guess_sex(fio: str) -> str | None:
    parts = fio.split()
    if len(parts) >= 3 and (s := sex_from_patronymic(parts[-1])):
        return s
    if len(parts) >= 2:
        name = parts[1].lower()
        if name.endswith(("а", "я")) and name not in ("никита", "илья", "фома", "лука", "кузьма", "савва", "данила"):
            return "ж"
        if name == "любовь":
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
    if low in _FLEETING:
        return _keep_case(_FLEETING[low], n) + "у"
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
    """Фамилия в дательном падеже (кому) — с учётом пола; несклоняемые — как есть."""
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


# ------------------------------------------------------------------ родительный падеж


def _a_ending(word: str) -> str:
    """Слово на «-а/-я» в родительном: Анна → Анны, Ольга → Ольги, Илья → Ильи, Бурмага → Бурмаги."""
    if word.lower().endswith("я") or word[-2:-1].lower() in _HUSHING:
        return word[:-1] + "и"
    return word[:-1] + "ы"


def _patronymic_gen(p: str) -> str:
    low = p.lower()
    if low.endswith("ич"):
        return p + "а"
    if low.endswith("на"):
        return p[:-1] + "ы"
    return p


def _name_gen(n: str, sex: str | None) -> str:
    low = n.lower()
    if low in _FLEETING:
        return _keep_case(_FLEETING[low], n) + "а"
    if low.endswith("ия"):
        return n[:-1] + "и"  # Мария → Марии
    if low.endswith(("а", "я")):
        return _a_ending(n)
    if sex == "ж":
        return n[:-1] + "и" if low.endswith("ь") else n  # Любовь → Любови
    if low.endswith(("й", "ь")):
        return n[:-1] + "я"  # Дмитрий → Дмитрия, Игорь → Игоря
    if low[-1] in _VOWELS:
        return n
    return n + "а"  # Иван → Ивана


def _surname_gen(s: str, sex: str | None) -> str:
    """Фамилия в родительном падеже (кого) — с учётом пола; несклоняемые — как есть."""
    low = s.lower()
    if low.endswith(("ых", "их", "ко", "аго", "яго")) or low[-1] in "еиоуэю":
        return s
    if sex == "ж":
        if low.endswith(("ова", "ева", "ёва", "ина", "ына")):
            return s[:-1] + "ой"  # Иванова → Ивановой
        if low.endswith("ая"):
            return s[:-2] + "ой"  # Бельская → Бельской
        if low.endswith("яя"):
            return s[:-2] + "ей"
        if low.endswith(("а", "я")):
            return _a_ending(s)
        return s
    if low.endswith(("ский", "цкий", "ой", "ый", "ий")):
        return s[:-2] + "ого"  # Бельский → Бельского
    if low.endswith(("й", "ь")):
        return s[:-1] + "я"  # Гайдай → Гайдая, Коваль → Коваля
    if low.endswith(("а", "я")):
        return _a_ending(s)
    return s + "а"  # Бакин → Бакина


def genitive(fio: str, sex: str | None = None) -> str:
    """«Иванов Иван Иванович» → «Иванова Ивана Ивановича» («в лице директора …»)."""
    parts = fio.split()
    if not parts:
        return fio
    sex = sex or guess_sex(fio)
    out = [_surname_gen(parts[0], sex)]
    if len(parts) >= 2:
        out.append(_name_gen(parts[1], sex))
    if len(parts) >= 3:
        out += [_patronymic_gen(p) for p in parts[2:]]
    return " ".join(out)


def initials(fio: str, surname_first: bool = True) -> str:
    """«Иванов Иван Иванович» → «Иванов И.И.» (или «И.И. Иванов»)."""
    parts = fio.split()
    if len(parts) < 2:
        return fio
    ini = "".join(f"{p[0]}." for p in parts[1:3])
    return f"{parts[0]} {ini}" if surname_first else f"{ini} {parts[0]}"


# ------------------------------------------------------------------ должности

_ROLE_GEN = {"главный": "главного", "старший": "старшего", "технический": "технического", "спортивный": "спортивного",
             "судья": "судьи", "секретарь": "секретаря", "заместитель": "заместителя", "начальник": "начальника",
             "председатель": "председателя", "член": "члена", "инспектор": "инспектора", "делегат": "делегата",
             "врач": "врача", "комендант": "коменданта", "информатор": "информатора", "рабочий": "рабочего",
             "водитель": "водителя", "фельдшер": "фельдшера", "хронометрист": "хронометриста",
             "стартер": "стартера", "стартёр": "стартёра", "директор": "директора"}
_ROLE_ADJ = ("главный", "старший", "технический", "спортивный")


def _role_word(w: str) -> str | None:
    """Слово должности в родительном; через дефис — каждая часть («судья-хронометрист»)."""
    parts = w.split("-")
    gens = [_ROLE_GEN.get(p.lower()) for p in parts]
    return None if None in gens else "-".join(gens)


def role_genitive(role: str) -> str | None:
    """«Главный судья» → «главного судьи», «Заместитель главного секретаря» → «заместителя главного секретаря».
    Склоняются слова до первого существительного (дальше — уже родительный). None — незнакомая должность."""
    words = role.split()
    out = []
    for i, w in enumerate(words):
        gen = _role_word(w)
        if gen is None:
            return None
        out.append(gen)
        if w.lower() not in _ROLE_ADJ:
            return " ".join(out + words[i + 1:])
    return None
