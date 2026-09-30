"""Чистка данных, которые люди вводят руками: ФИО, даты, пол, телефоны, названия.

Каждая функция возвращает исправленное значение и список того, что было исправлено или вызывает
сомнение, — чтобы секретарь видел все правки, а не получал «молча» изменённые данные.
"""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

_SPACES = re.compile(r"\s+")


def plural(n: int, one: str, few: str, many: str) -> str:
    """Форма слова после числа: 1 год, 2 года, 5 лет, 21 год."""
    n = abs(n) % 100
    if 11 <= n <= 19:
        return many
    n %= 10
    return one if n == 1 else few if 2 <= n <= 4 else many


def years(n: int) -> str:
    """«21 год», «22 года», «25 лет»."""
    return f"{n} {plural(n, 'год', 'года', 'лет')}"


def from_years(n: int) -> str:
    """После «с» и «старше»: «с 21 года», «с 22 лет»."""
    return f"{n} {'года' if n % 10 == 1 and n % 100 != 11 else 'лет'}"
# строчная кириллическая буква, сразу за ней заглавная: «ВикторАндреевич» → «Виктор Андреевич»
_GLUED = re.compile(r"(?<=[а-яё])(?=[А-ЯЁ])")
_TRAILING_DATE = re.compile(r"\s*(\d{1,2})[./-](\d{1,2})[./-](\d{2,4})\s*$")


def clean_spaces(value) -> str:
    """Убрать пробелы по краям и повторы; неразрывный пробел считается обычным."""
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return _SPACES.sub(" ", str(value).replace("\xa0", " ")).strip()


# ------------------------------------------------------------------ ФИО

_PATRONYMIC_M = ("вич", "ич", "оглы", "улы", "уулу")
_PATRONYMIC_F = ("вна", "чна", "шна", "кызы", "гызы", "қызы")


def sex_from_patronymic(patronymic: str) -> str | None:
    """«м» / «ж» по окончанию отчества; None — определить нельзя."""
    p = patronymic.lower()
    if p.endswith(_PATRONYMIC_F):
        return "ж"
    if p.endswith(_PATRONYMIC_M):
        return "м"
    return None


def _cap(word: str) -> str:
    """«иванова-петрова» → «Иванова-Петрова»; «ДЕ ла» не трогаем, если есть заглавные внутри."""
    parts = word.split("-")
    return "-".join(p[:1].upper() + p[1:].lower() if p else p for p in parts)


@dataclass
class PersonName:
    raw: str
    surname: str = ""
    name: str = ""
    patronymic: str = ""
    extracted_birth: str | None = None  # дата, найденная в конце строки ФИО
    fixes: list[str] = field(default_factory=list)
    doubts: list[tuple[str, str]] = field(default_factory=list)  # (что смущает, почему)

    @property
    def full(self) -> str:
        return " ".join(p for p in (self.surname, self.name, self.patronymic) if p)


def normalize_name(raw) -> PersonName:
    """Разобрать ФИО: убрать лишние пробелы, разделить слитные слова, вынуть дату из конца строки,
    поправить регистр. Сомнительное (нет отчества, отчество не похоже на отчество) — в doubts."""
    src = clean_spaces(raw)
    out = PersonName(raw=str(raw) if raw is not None else "")
    text = src
    m = _TRAILING_DATE.search(text)
    if m:
        out.extracted_birth = m.group(0).strip()
        text = text[: m.start()].strip()
        out.fixes.append(f"дата «{out.extracted_birth}» вписана в ФИО — перенесена в дату рождения")
    split = _GLUED.sub(" ", text)
    if split != text:
        out.fixes.append("разделены слитные слова")
        text = split
    words = [w for w in text.split(" ") if w]
    fixed = [_cap(w) if (w.isupper() or w.islower()) else w for w in words]
    if fixed != words and src:
        out.fixes.append("исправлен регистр букв")
    words = fixed
    if len(words) >= 1:
        out.surname = words[0]
    if len(words) >= 2:
        out.name = words[1]
    if len(words) >= 3:
        out.patronymic = " ".join(words[2:])
    if len(words) < 2:
        out.doubts.append(("в ФИО меньше двух слов",
                           "В протоколах и выписках на разряд нужны фамилия и имя, а если есть — и отчество."))
    elif len(words) == 2:
        out.doubts.append(("нет отчества",
                           "Для выписок на присвоение разряда ФИО пишется полностью, как в документе. "
                           "Отчества может и не быть — тогда всё верно."))
    elif sex_from_patronymic(words[-1]) is None:
        out.doubts.append((f"«{out.patronymic}» не похоже на отчество",
                           "Отчества оканчиваются на -вич, -ич, -вна, -чна, -оглы, -кызы. Возможно, отчество "
                           "обрезано («Александров» вместо «Александрович»), в нём опечатка или слова переставлены."))
    if len(words) > 3 and sex_from_patronymic(words[-1]) is None:
        out.doubts.append(("в ФИО больше трёх слов",
                           "Возможно, в ячейку попало лишнее: вторая фамилия, прозвище, должность."))
    return out


# ------------------------------------------------------------------ даты рождения

_EXCEL_EPOCH = date(1899, 12, 30)


@dataclass
class ParsedDate:
    value: date | None = None
    year_only: int | None = None
    problem: str | None = None  # ошибка — дату использовать нельзя
    note: str | None = None  # что было исправлено/распознано
    why: str = ""  # объяснение для человека: почему дата ошибочна или сомнительна


_MONTHS_IN = ["январе", "феврале", "марте", "апреле", "мае", "июне", "июле", "августе", "сентябре", "октябре",
              "ноябре", "декабре"]
_FORMAT_HINT = "Дату записывают числами: день.месяц.год, например 05.03.2008."


def _why_no_such_date(dd: int, mm: int, y: int) -> str:
    """Почему даты нет в календаре: «1995 год не високосный — в феврале 28 дней»."""
    if not 1 <= mm <= 12:
        if 1 <= dd <= 12:
            return (f"Месяца с номером {mm} не бывает. Похоже, день и месяц переставлены местами — тогда это "
                    f"{mm:02d}.{dd:02d}.{y}.")
        return f"Месяца с номером {mm} не бывает — в году 12 месяцев."
    if dd < 1:
        return "Дня с номером 0 не бывает."
    days = calendar.monthrange(y, mm)[1]
    if mm == 2 and dd == 29:
        return f"{y} год не високосный: в феврале того года было 28 дней, 29-го числа не было."
    return f"В {_MONTHS_IN[mm - 1]} {days} {plural(days, 'день', 'дня', 'дней')}, а в заявке — {dd}-е число."


def parse_birth_date(raw, *, today: date | None = None) -> ParsedDate:
    """Дата рождения из ячейки: дата Excel, число-серийник, «дд.мм.гггг», «дд/мм/гггг», «гггг-мм-дд»,
    только год. Несуществующая дата (29.02.1995) — ошибка, а не «ближайшая похожая»."""
    today = today or date.today()
    year_only_why = ("Для протоколов и выписок на разряд нужна полная дата. По одному году не посчитать, "
                     "исполнилось ли участнику 18 лет к началу соревнований.")
    if raw is None or clean_spaces(raw) == "":
        return ParsedDate(problem="не указана дата рождения",
                          why="Без даты рождения не проверить возраст для допуска, а в протоколах и выписках "
                              "на разряд она обязательна.")
    if isinstance(raw, datetime):
        d = raw.date()
    elif isinstance(raw, date):
        d = raw
    elif isinstance(raw, (int, float)) and not isinstance(raw, bool):
        n = float(raw)
        if 1900 <= n <= today.year and n.is_integer():
            return ParsedDate(year_only=int(n), note="указан только год рождения", why=year_only_why)
        if 1 < n < 80000:
            d = _EXCEL_EPOCH + timedelta(days=int(n))
        else:
            return ParsedDate(problem=f"не удалось распознать дату «{raw}»", why=_FORMAT_HINT)
    else:
        s = clean_spaces(raw)
        if re.fullmatch(r"\d{4}", s):
            return ParsedDate(year_only=int(s), note="указан только год рождения", why=year_only_why)
        m = re.fullmatch(r"(\d{1,2})[./\-\s](\d{1,2})[./\-\s](\d{2}|\d{4})(?:\s*г\.?)?", s)
        iso = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})(?:[ T].*)?", s)
        if m:
            dd, mm, yy = int(m.group(1)), int(m.group(2)), m.group(3)
            if len(yy) == 2:
                return ParsedDate(problem=f"год указан двумя цифрами «{s}» — уточните век",
                                  why="По двум цифрам не понять, 19.. это или 20.. год, а от года зависят возраст "
                                      "и допуск. Программа век не угадывает.")
            y = int(yy)
        elif iso:
            y, mm, dd = int(iso.group(1)), int(iso.group(2)), int(iso.group(3))
        else:
            return ParsedDate(problem=f"не удалось распознать дату «{s}»", why=_FORMAT_HINT)
        try:
            d = date(y, mm, dd)
        except ValueError:
            return ParsedDate(problem=f"такой даты не существует: «{s}»", why=_why_no_such_date(dd, mm, y))
    if d > today:
        return ParsedDate(problem=f"дата рождения в будущем: {d:%d.%m.%Y}",
                          why="Дата позже начала соревнований — скорее всего, опечатка в годе.")
    age = today.year - d.year
    if age > 90 or age < 5:
        return ParsedDate(d, note=f"необычный возраст ({years(age)}) — проверьте дату",
                          why=f"Участнику {years(age)} — так бывает редко, возможно, опечатка в годе рождения.")
    return ParsedDate(d)


# ------------------------------------------------------------------ прочее


def normalize_sex(raw) -> str | None:
    s = clean_spaces(raw).lower().rstrip(".")
    if s in ("м", "муж", "мужской", "m", "male"):
        return "м"
    if s in ("ж", "жен", "женский", "f", "female"):
        return "ж"
    return None


def normalize_phone(raw) -> str:
    """Российский номер к виду «+7 913 170-76-08»; прочее оставляем как есть."""
    s = clean_spaces(raw)
    digits = re.sub(r"\D", "", s)
    if len(digits) == 11 and digits[0] in "78":
        d = digits[1:]
        return f"+7 {d[:3]} {d[3:6]}-{d[6:8]}-{d[8:]}"
    if len(digits) == 10 and digits[0] == "9":
        return f"+7 {digits[:3]} {digits[3:6]}-{digits[6:8]}-{digits[8:]}"
    return s


def split_contacts(raw) -> tuple[str, str]:
    """«89131707608, ivan@mail.ru» → (телефон, e-mail)."""
    s = clean_spaces(raw)
    emails = re.findall(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", s)
    rest = s
    for e in emails:
        rest = rest.replace(e, " ")
    phone = normalize_phone(rest.strip(" ,;")) if re.search(r"\d{6,}", re.sub(r"\D", "", rest)) else ""
    return phone, ", ".join(emails)


def normalize_territory(raw) -> str:
    """«г. Красноярск», «г.Томск» → «Красноярск», «Томск»."""
    s = clean_spaces(raw)
    s = re.sub(r"^(г\.|город)\s*", "", s, flags=re.IGNORECASE)
    return s


def normalize_team(raw) -> str:
    """Название команды: пробелы, непарные кавычки по краям."""
    s = clean_spaces(raw)
    for q in ('"', "«", "»", "“", "”", "'"):
        if s.count(q) == 1 and (s.startswith(q) or s.endswith(q)):
            s = s.strip(q).strip()
    if s.startswith("«") and s.endswith("»") and s.count("«") == 1:
        s = s[1:-1].strip()
    # «Черепашки -ниндзя» → «Черепашки-ниндзя»; симметричное «Ураган - 2» не трогаем
    s = re.sub(r"(\S)\s+-(?=\S)", r"\1-", s)
    s = re.sub(r"(\S)-\s+(?=\S)", r"\1-", s)
    return s


def edit_distance(a: str, b: str) -> int:
    """Расстояние Левенштейна (для поиска опечаток в названиях)."""
    a, b = a.lower(), b.lower()
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


# ------------------------------------------------------------------ алфавитный порядок


def alpha_key(text) -> tuple:
    """Ключ сортировки по-русски для названий команд, ФИО, файлов: регистр не важен, «Ё» — сразу после «Е»
    (в Юникоде «ё» стоит после «я» — «Ёлки-палки» уезжали в конец), разложенные имена файлов с macOS («Е» + «̈»)
    — как обычные, числа — по значению («Команда 2» раньше «Команда 10»)."""
    import unicodedata

    s = unicodedata.normalize("NFC", str(text or "")).casefold()
    out = []
    for part in re.split(r"(\d+)", s):
        if not part:
            continue
        if part.isdigit():
            out.append((0, int(part), ()))
        else:
            out.append((1, 0, tuple(2 * ord("е") + 1 if c == "ё" else 2 * ord(c) for c in part)))
    return tuple(out)
