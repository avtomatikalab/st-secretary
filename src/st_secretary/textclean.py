"""Чистка данных, которые люди вводят руками: ФИО, даты, пол, телефоны, названия.

Каждая функция возвращает исправленное значение и список того, что было исправлено или вызывает
сомнение, — чтобы секретарь видел все правки, а не получал «молча» изменённые данные.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

_SPACES = re.compile(r"\s+")
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
    doubts: list[str] = field(default_factory=list)

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
        out.doubts.append("в ФИО меньше двух слов")
    elif len(words) == 2:
        out.doubts.append("нет отчества (для выписок на разряд нужно, если оно есть)")
    elif sex_from_patronymic(words[-1]) is None:
        out.doubts.append(f"«{out.patronymic}» не похоже на отчество — возможно, обрезано или опечатка")
    if len(words) > 3 and sex_from_patronymic(words[-1]) is None:
        out.doubts.append("в ФИО больше трёх слов")
    return out


# ------------------------------------------------------------------ даты рождения

_EXCEL_EPOCH = date(1899, 12, 30)


@dataclass
class ParsedDate:
    value: date | None = None
    year_only: int | None = None
    problem: str | None = None  # ошибка — дату использовать нельзя
    note: str | None = None  # что было исправлено/распознано


def parse_birth_date(raw, *, today: date | None = None) -> ParsedDate:
    """Дата рождения из ячейки: дата Excel, число-серийник, «дд.мм.гггг», «дд/мм/гггг», «гггг-мм-дд»,
    только год. Несуществующая дата (29.02.1995) — ошибка, а не «ближайшая похожая»."""
    today = today or date.today()
    if raw is None or clean_spaces(raw) == "":
        return ParsedDate(problem="не указана дата рождения")
    if isinstance(raw, datetime):
        d = raw.date()
    elif isinstance(raw, date):
        d = raw
    elif isinstance(raw, (int, float)) and not isinstance(raw, bool):
        n = float(raw)
        if 1900 <= n <= today.year and n.is_integer():
            return ParsedDate(year_only=int(n), note="указан только год рождения")
        if 1 < n < 80000:
            d = _EXCEL_EPOCH + timedelta(days=int(n))
        else:
            return ParsedDate(problem=f"не удалось распознать дату «{raw}»")
    else:
        s = clean_spaces(raw)
        if re.fullmatch(r"\d{4}", s):
            return ParsedDate(year_only=int(s), note="указан только год рождения")
        m = re.fullmatch(r"(\d{1,2})[./\-\s](\d{1,2})[./\-\s](\d{2}|\d{4})(?:\s*г\.?)?", s)
        iso = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})(?:[ T].*)?", s)
        if m:
            dd, mm, yy = int(m.group(1)), int(m.group(2)), m.group(3)
            if len(yy) == 2:
                return ParsedDate(problem=f"год указан двумя цифрами «{s}» — уточните век")
            y = int(yy)
        elif iso:
            y, mm, dd = int(iso.group(1)), int(iso.group(2)), int(iso.group(3))
        else:
            return ParsedDate(problem=f"не удалось распознать дату «{s}»")
        try:
            d = date(y, mm, dd)
        except ValueError:
            return ParsedDate(problem=f"такой даты не существует: «{s}»")
    if d > today:
        return ParsedDate(problem=f"дата рождения в будущем: {d:%d.%m.%Y}")
    if today.year - d.year > 90 or today.year - d.year < 5:
        return ParsedDate(d, note=f"необычный возраст ({today.year - d.year} лет) — проверьте дату")
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
