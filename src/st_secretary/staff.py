"""Судейская и комендантская бригада: кто работал по каким дням, ставки, суммы — для табеля-наряда,
договоров и актов (муниципальный заказчик оплачивает судейство по договорам оказания услуг).

Люди — судьи из карточки соревнования (лист «ГСК») и те, кого добавили на странице договоров (комендант,
рабочие, судьи, которых нет в карточке). Дни — отметки «р» в табеле; сумма = дней × ставка за день.
Ставки — по должности и судейской категории (нормы расходов заказчика), задаются на соревнование.

Табель, договор и акт считаются из одних и тех же отметок, поэтому дни в них совпадают сами. В 2025 году
было иначе: у двух человек в табеле 3 отметки, а к оплате 4 дня; в договоре главного судьи срок
«19–21 сентября», а в акте к нему — «18–22 сентября, 5 дн.».

Паспортные и банковские данные сюда не входят — они в отдельном файле на этом компьютере (см. store.py).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta

from st_secretary.competition import Competition, date_range_text
from st_secretary.issues import ERROR, WARNING, Issue
from st_secretary.names import role_genitive
from st_secretary.results import person_key

# Категория для табеля и ставок: «СС1К» → «1».
CAT_SHORT = {"ССВК": "ВК", "СС1К": "1", "СС2К": "2", "СС3К": "3", "ЮС": "ЮС", "б/к": "б/к", "": "б/к"}
CAT_WORDS = {"ВК": "всероссийской категории", "1": "1 категории", "2": "2 категории", "3": "3 категории",
             "ЮС": "категории «юный спортивный судья»", "б/к": ""}
CATEGORIES = ("ССВК", "СС1К", "СС2К", "СС3К", "ЮС", "б/к")

# Подсказки должностей для тех, кого нет в карточке.
EXTRA_ROLES = ("Судья", "Судья этапа", "Старший судья этапа", "Комендант", "Рабочий комендантской бригады", "Врач",
               "Водитель")

# Образец ставок, ₽ за день: по договорам и табелю Чемпионата г. Красноярска 2025 г. (нормы расходов Красспорта).
# Есть не все сочетания должности и категории — недостающие вписываются на странице.
RATES_KRSK_2025 = {
    "главный судья|1": 850, "главный секретарь|1": 850,
    "заместитель главного судьи|2": 630, "заместитель главного секретаря|1": 700,
    "начальник дистанции|б/к": 690, "судья|2": 575, "судья|3": 450,
    "комендант|б/к": 690, "рабочий комендантской бригады|б/к": 460, "рабочий|б/к": 460,
}
ACCRUAL = 30  # начисления на оплату, % (так в табеле 2025 г.)

MARK = "р"  # отметка рабочего дня в табеле


def cat_short(category: str) -> str:
    return CAT_SHORT.get((category or "").strip(), (category or "").strip() or "б/к")


def rate_key(role: str, category: str) -> str:
    return f"{' '.join(role.lower().split())}|{cat_short(category)}"


@dataclass
class Person:
    key: str
    fio: str
    role: str
    category: str  # как в карточке: СС1К, СС2К, б/к…
    from_card: bool
    days: list[date] = field(default_factory=list)  # отмеченные дни в пределах периода работы
    rate: int | None = None  # ₽ за день
    unpaid: bool = False  # работает без оплаты — в табель и договоры не идёт
    outside: list[date] = field(default_factory=list)  # отмечены, но вне периода работы

    @property
    def cat(self) -> str:
        return cat_short(self.category)

    @property
    def paid(self) -> bool:
        return not self.unpaid and bool(self.days)

    @property
    def amount(self) -> int:
        return len(self.days) * (self.rate or 0) if self.paid else 0

    @property
    def rate_key(self) -> str:
        return rate_key(self.role, self.category)

    @property
    def role_text(self) -> str:
        """«главного судьи 1 категории» — для договора и акта («в качестве …»)."""
        gen = role_genitive(self.role) or self.role[:1].lower() + self.role[1:]
        words = CAT_WORDS.get(self.cat, "")
        return f"{gen} {words}".strip()

    @property
    def span(self) -> tuple[date, date] | None:
        return (self.days[0], self.days[-1]) if self.days else None

    @property
    def span_text(self) -> str:
        """Срок оказания услуг — с первого по последний отмеченный день: «18–22 сентября 2025 г.»."""
        return date_range_text(*self.span) if self.days else ""


def default_period(comp: Competition) -> tuple[date, date]:
    """Период работы бригады: день до начала (подготовка) и день после окончания (снятие дистанции)."""
    return comp.date_from - timedelta(days=1), comp.date_to + timedelta(days=1)


def _day(s) -> date | None:
    try:
        return date.fromisoformat(str(s))
    except ValueError:
        return None


def settings(comp: Competition, data: dict) -> dict:
    p = data.get("period", {})
    a, b = default_period(comp)
    start, end = _day(p.get("from")) or a, _day(p.get("to")) or b
    if end < start:
        start, end = end, start
    if (end - start).days > 60:  # опечатка в годе — не рисовать табель на полгода
        end = start + timedelta(days=60)
    days = [start + timedelta(days=i) for i in range((end - start).days + 1)]
    acc = data.get("accrual", ACCRUAL)
    return {"days": days, "from": start, "to": end, "rates": dict(data.get("rates", {})),
            "accrual": acc if isinstance(acc, (int, float)) else ACCRUAL}


def people(comp: Competition, data: dict) -> list[Person]:
    """Бригада: судьи из карточки (в её порядке), затем добавленные на странице договоров."""
    s = settings(comp, data)
    marks = data.get("people", {})
    period = set(s["days"])
    comp_days = [d for d in s["days"] if comp.date_from <= d <= comp.date_to]
    rows = [(o.fio, o.role, o.category, True) for o in comp.officials if o.fio]
    rows += [(x.get("fio", ""), x.get("role", ""), x.get("category", ""), False) for x in data.get("extra", [])
             if x.get("fio")]
    out, seen = [], set()
    for fio, role, category, from_card in rows:
        key = person_key(fio)
        if key in seen:  # один человек дважды (в карточке и добавлен вручную) — первая строка
            continue
        seen.add(key)
        m = marks.get(key, {})
        stored = [d for d in (_day(x) for x in m.get("days", [])) if d] if "days" in m else comp_days
        days = sorted(d for d in set(stored) if d in period)
        p = Person(key, " ".join(fio.split()), role.strip(), (category or "б/к").strip(), from_card, days,
                   unpaid=bool(m.get("unpaid")), outside=sorted(d for d in set(stored) if d not in period))
        rate = s["rates"].get(p.rate_key)
        p.rate = int(rate) if isinstance(rate, (int, float)) or str(rate).isdigit() else None
        out.append(p)
    return out


def rate_pairs(team: list[Person]) -> list[tuple[str, str, str]]:
    """Сочетания «должность — категория» бригады (без повторов): для таблицы ставок."""
    seen: dict[str, tuple[str, str, str]] = {}
    for p in team:
        seen.setdefault(p.rate_key, (p.rate_key, p.role, p.cat))
    return list(seen.values())


def totals(team: list[Person], accrual: float) -> dict:
    paid = [p for p in team if p.paid]
    total = sum(p.amount for p in paid)
    acc = round(total * accrual / 100, 2)
    return {"people": len(paid), "days": sum(len(p.days) for p in paid), "sum": total, "accrual": acc,
            "total": round(total + acc, 2)}


# ------------------------------------------------------------------ личные данные (для договоров)

PERSONAL_FIELDS = [
    ("birth", "Дата рождения", "дд.мм.гггг"),
    ("passport", "Паспорт: серия и номер", "04 11 123456"),
    ("issued_by", "Кем выдан", ""),
    ("issued_on", "Дата выдачи", "дд.мм.гггг"),
    ("dept_code", "Код подразделения", "240-006"),
    ("address", "Адрес регистрации", "660000, Красноярский край, г. Красноярск, ул. …, д. …, кв. …"),
    ("inn", "ИНН", "12 цифр"),
    ("snils", "СНИЛС", "123-456-789 01"),
    ("account", "Номер счёта", "20 цифр"),
    ("bank", "Банк", "ПАО Сбербанк"),
    ("bik", "БИК банка", "9 цифр"),
    ("phone", "Телефон", "+7 …"),
    ("judge_id", "№ удостоверения судьи (для табеля)", ""),
]
FOR_CONTRACT = ("passport", "issued_by", "issued_on", "address", "inn", "snils", "account", "bik")


def _digits(s: str) -> str:
    return re.sub(r"\D", "", s or "")


def inn_ok(inn: str) -> bool:
    """Контрольные цифры ИНН физического лица (12 цифр)."""
    d = [int(c) for c in _digits(inn)]
    if len(d) != 12:
        return False

    def check(weights):
        return sum(w * x for w, x in zip(weights, d)) % 11 % 10

    return (check((7, 2, 4, 10, 3, 5, 9, 4, 6, 8)) == d[10]
            and check((3, 7, 2, 4, 10, 3, 5, 9, 4, 6, 8)) == d[11])


def snils_ok(snils: str) -> bool:
    """Контрольное число СНИЛС (последние 2 цифры)."""
    d = _digits(snils)
    if len(d) != 11:
        return False
    s = sum(int(c) * (9 - i) for i, c in enumerate(d[:9]))
    check = s if s < 100 else 0 if s in (100, 101) else s % 101 % 100
    return check == int(d[9:])


def account_ok(account: str, bik: str) -> bool:
    """Контрольный ключ счёта по БИК банка (счёт в кредитной организации)."""
    a, b = _digits(account), _digits(bik)
    if len(a) != 20 or len(b) != 9:
        return False
    s = b[-3:] + a
    return sum(int(c) * (7, 1, 3)[i % 3] for i, c in enumerate(s)) % 10 == 0


def _date_ok(s: str) -> bool:
    m = re.fullmatch(r"\s*(\d{1,2})\.(\d{1,2})\.(\d{4})\s*", s or "")
    if not m:
        return False
    try:
        date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
    except ValueError:
        return False
    return True


def personal_problems(pd: dict) -> list[tuple[str, str]]:
    """Ошибки в личных данных: (поле, что не так). Пустые поля — не ошибка (впишут в договор от руки)."""
    out = []
    v = {k: str(pd.get(k, "") or "").strip() for k, _, _ in PERSONAL_FIELDS}
    if v["passport"] and len(_digits(v["passport"])) != 10:
        out.append(("passport", "в серии и номере паспорта должно быть 10 цифр"))
    for k in ("birth", "issued_on"):
        if v[k] and not _date_ok(v[k]):
            out.append((k, "дата — в виде дд.мм.гггг, и такая дата должна существовать"))
    if v["dept_code"] and not re.fullmatch(r"\d{3}-?\d{3}", v["dept_code"]):
        out.append(("dept_code", "код подразделения — 6 цифр, «240-006»"))
    if v["inn"] and not inn_ok(v["inn"]):
        out.append(("inn", "ИНН не сходится: у человека 12 цифр и две последние — контрольные; скорее всего, опечатка"))
    if v["snils"] and not snils_ok(v["snils"]):
        out.append(("snils", "СНИЛС не сходится по контрольному числу — скорее всего, опечатка"))
    if v["bik"] and len(_digits(v["bik"])) != 9:
        out.append(("bik", "в БИК 9 цифр"))
    if v["account"]:
        if len(_digits(v["account"])) != 20:
            out.append(("account", "в номере счёта 20 цифр"))
        elif len(_digits(v["bik"])) == 9 and not account_ok(v["account"], v["bik"]):
            out.append(("account", "номер счёта не сходится с БИК банка — опечатка в счёте или в БИК"))
    return out


def missing_personal(pd: dict) -> list[str]:
    """Каких данных для договора нет (подписи полей)."""
    labels = {k: lbl for k, lbl, _ in PERSONAL_FIELDS}
    return [labels[k] for k in FOR_CONTRACT if not str(pd.get(k, "") or "").strip()]


# ------------------------------------------------------------------ проверка перед печатью


def check(team: list[Person], personal: dict[str, dict], customer: dict) -> list[Issue]:
    """Замечания по бригаде: дни вне периода и без дней, нет ставки, незнакомая должность, ошибки и пропуски в данных
    для договора, человек дважды; нет данных заказчика."""
    src = "Договоры и табель"
    out: list[Issue] = []
    for p in team:
        if p.outside:
            out.append(Issue(WARNING, f"{p.fio}: отмечены дни вне периода работы бригады "
                                      f"({', '.join(f'{d:%d.%m}' for d in p.outside)}) — они не учитываются",
                             source=src, person=p.fio, target=f"tabel:{p.key}"))
        if p.unpaid:
            continue
        if not p.days:
            out.append(Issue(WARNING, f"{p.fio}: не отмечено ни одного дня — в табель и договоры не попадёт; "
                                      "если работает без оплаты, отметьте «без оплаты»", source=src, person=p.fio,
                             target=f"tabel:{p.key}"))
            continue
        if p.rate is None:
            out.append(Issue(ERROR, f"{p.fio}: не задана ставка для «{p.role}, {p.cat}» — сумма не посчитается",
                             source=src, person=p.fio, target=f"rate:{p.rate_key}"))
        if role_genitive(p.role) is None:
            out.append(Issue(WARNING, f"{p.fio}: должность «{p.role}» программа не умеет склонять — в договоре она "
                                      "будет как есть; проверьте фразу «в качестве …»", source=src, person=p.fio,
                             target="card:gsk" if p.from_card else f"person:{p.key}:role"))
        pd = personal.get(p.key, {})
        for fld, why in personal_problems(pd):
            label = next(lbl for k, lbl, _ in PERSONAL_FIELDS if k == fld)
            out.append(Issue(ERROR, f"{p.fio}: {label.lower()} — {why}", source=src, person=p.fio, field=fld,
                             target=f"person:{p.key}:{fld}"))
        miss = missing_personal(pd)
        if miss:
            first = next(k for k in FOR_CONTRACT if not str(pd.get(k, "") or "").strip())
            out.append(Issue(WARNING, f"{p.fio}: для договора нет данных — {', '.join(m.lower() for m in miss)}; "
                                      "в договоре останутся пустые строки", source=src, person=p.fio,
                             target=f"person:{p.key}:{first}"))
    names = [p.key for p in team]
    for k in {k for k in names if names.count(k) > 1}:
        out.append(Issue(ERROR, f"{k}: человек указан дважды", source=src, target="card:gsk"))
    if not customer.get("name") or not customer.get("head_fio"):
        out.append(Issue(WARNING, "не заполнены сведения о заказчике — в договорах останутся пустые места",
                         source=src, target="customer"))
    return out
