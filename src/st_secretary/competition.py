"""Карточка соревнования — единственное место, где вводятся реквизиты.

Из неё берутся шапки всех документов, подписи, зачёты (группы), правила допуска, редакция норм.
Проверки карточки ловят ошибки до того, как они попадут в документы.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from st_secretary.issues import ERROR, INFO, WARNING, Issue
from st_secretary.norms import PercentMethod
from st_secretary.qualification import Qual
from st_secretary.reference import Level, discipline_by_code, norm_edition, norm_editions

KINDS = ("Чемпионат", "Первенство", "Кубок", "Другие соревнования", "Физкультурное мероприятие")

LEVEL_LABELS = {
    Level.MUNICIPAL: "Муниципальный",
    Level.REGIONAL: "Субъекта РФ (краевой, областной)",
    Level.INTERREGIONAL: "Межрегиональный",
    Level.ALL_RUSSIAN: "Всероссийский",
}

PERCENT_LABELS = {
    None: "Не задана",
    PercentMethod.TIME: "По времени",
    PercentMethod.POINTS_RELATIVE_TO_WINNER: "Баллы: (1 + (результат − победитель) / |победитель|) × 100",
}

JUDGE_CATEGORIES = ("ССВК", "СС1К", "СС2К", "СС3К", "ЮС", "б/к")

GSK_ROLES = (
    "Главный судья",
    "Главный секретарь",
    "Заместитель главного судьи по судейству",
    "Заместитель главного судьи по безопасности",
    "Заместитель главного судьи по информации",
    "Заместитель главного судьи",
    "Заместитель главного секретаря",
    "Начальник дистанции",
    "Председатель комиссии по допуску",
)

# Какой справочник допуска относится к дисциплине.
_PROFILE_BY_DISCIPLINE = {
    "0840161811Я": "psr",
    "0840131811Я": "speleo", "0840261811Я": "speleo", "0840271811Я": "speleo",
    "0840091811Я": "pedestrian", "0840241811Я": "pedestrian", "0840251811Я": "pedestrian",
}
# Дисциплины, где результат — баллы, начисляемые судьями (нужна методика процента для норм).
POINTS_DISCIPLINES = {"0840161811Я", "0840101811Я", "0840211811Я"}


@dataclass(frozen=True)
class Official:
    role: str
    fio: str
    category: str = ""
    territory: str = ""

    @property
    def signature(self) -> str:
        """Как в подписи протокола: «И.О. Фамилия, СС1К, г. Красноярск»."""
        parts = self.fio.split()
        short = self.fio
        if len(parts) >= 2:
            initials = "".join(f"{p[0]}." for p in parts[1:3])
            short = f"{initials} {parts[0]}"
        tail = ", ".join(x for x in (self.category, self.territory) if x)
        return f"{short}, {tail}" if tail else short


@dataclass(frozen=True)
class Zachet:
    """Зачёт — группа участников на дистанции определённого класса (как «М/Ж_3» в СЕКРЕТАРЬ_ST)."""

    group: str
    distance_class: int
    discipline_code: str
    age_from: int | None = None
    age_to: int | None = None
    age_from_by_gsk: int | None = None  # «по решению ГСК допускаются с N лет»
    min_qual: Qual = Qual.BR
    team_size: int | None = None
    min_men: int = 0
    min_women: int = 0
    fee: int | None = None
    fee_per: str = "команду"

    @property
    def key(self) -> str:
        """Название зачёта в СЕКРЕТАРЬ_ST: ГРУППА_КЛАСС."""
        return f"{self.group}_{self.distance_class}"

    @property
    def discipline_name(self) -> str:
        return discipline_by_code(self.discipline_code).name

    @property
    def admission_profile(self) -> str | None:
        return _PROFILE_BY_DISCIPLINE.get(self.discipline_code)


@dataclass
class Competition:
    title: str
    kind: str
    level: Level
    date_from: date
    date_to: date
    place: str
    host_territory: str  # населённый пункт организаторов — эталон написания «своей» территории
    organizers: list[str] = field(default_factory=list)
    calendar_number: str = ""
    norms_edition: str = "2026-2029"
    percent_method: PercentMethod | None = None
    preapp_deadline: date | None = None
    officials: list[Official] = field(default_factory=list)
    zachety: list[Zachet] = field(default_factory=list)

    @property
    def year(self) -> int:
        return self.date_from.year

    @property
    def dates_text(self) -> str:
        """«20–21 сентября 2025 г.» для шапок документов."""
        months = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа",
                  "сентября", "октября", "ноября", "декабря"]
        a, b = self.date_from, self.date_to
        if a == b:
            return f"{a.day} {months[a.month - 1]} {a.year} г."
        if (a.year, a.month) == (b.year, b.month):
            return f"{a.day}–{b.day} {months[a.month - 1]} {a.year} г."
        if a.year == b.year:
            return f"{a.day} {months[a.month - 1]} – {b.day} {months[b.month - 1]} {a.year} г."
        return f"{a.day} {months[a.month - 1]} {a.year} г. – {b.day} {months[b.month - 1]} {b.year} г."

    def official(self, role: str) -> Official | None:
        return next((o for o in self.officials if o.role == role), None)

    def find_zachet(self, group: str, distance_class: int | None) -> Zachet | None:
        g = _group_key(group)
        found = [z for z in self.zachety if _group_key(z.group) == g
                 and (distance_class is None or z.distance_class == distance_class)]
        return found[0] if len(found) == 1 else None

    def check(self) -> list[Issue]:
        """Проверка самой карточки."""
        out: list[Issue] = []
        src = "Карточка соревнования"

        def err(text, fld=""):
            out.append(Issue(ERROR, text, source=src, field=fld))

        def warn(text, fld=""):
            out.append(Issue(WARNING, text, source=src, field=fld))

        if not self.title:
            err("не указано наименование соревнований", "Наименование")
        if self.kind not in KINDS:
            err(f"вид соревнований «{self.kind}» не из списка: {', '.join(KINDS)}", "Вид")
        if self.date_to < self.date_from:
            err("дата окончания раньше даты начала", "Даты")
        if self.preapp_deadline and self.preapp_deadline > self.date_from:
            warn("приём предзаявок заканчивается позже начала соревнований", "Приём предзаявок до")
        if self.norms_edition not in norm_editions():
            err(f"нет редакции норм «{self.norms_edition}»", "Редакция норм")
        else:
            n = norm_edition(self.norms_edition)
            span = n.edition.split("-")
            if not (int(span[0]) <= self.year <= int(span[-1])):
                warn(f"соревнования {self.year} года, а выбрана редакция норм {n.edition}", "Редакция норм")
        for role in ("Главный судья", "Главный секретарь"):
            o = self.official(role)
            if not o or not o.fio:
                err(f"в составе ГСК не указан {role.lower()}", "ГСК")
            elif not o.category:
                warn(f"{role}: не указана судейская категория (нужна для подписи протоколов)", "ГСК")
        for o in self.officials:
            if o.category and o.category not in JUDGE_CATEGORIES:
                warn(f"{o.role}: категория «{o.category}» не из списка {', '.join(JUDGE_CATEGORIES)}", "ГСК")
        if not self.zachety:
            err("не задан ни один зачёт (группа и класс дистанции)", "Зачёты")
        keys = [z.key for z in self.zachety]
        for k in {k for k in keys if keys.count(k) > 1}:
            err(f"зачёт {k} указан дважды", "Зачёты")
        for z in self.zachety:
            try:
                z.discipline_name
            except KeyError as e:
                err(str(e), f"Зачёт {z.key}")
                continue
            if not 1 <= z.distance_class <= 6:
                err(f"зачёт {z.key}: класс дистанции должен быть от 1 до 6", f"Зачёт {z.key}")
            if z.age_from and z.age_to and z.age_from > z.age_to:
                err(f"зачёт {z.key}: «возраст от» больше «возраст до»", f"Зачёт {z.key}")
            if z.team_size and z.min_men + z.min_women > z.team_size:
                err(f"зачёт {z.key}: мужчин и женщин по минимуму больше, чем состав команды", f"Зачёт {z.key}")
            if z.admission_profile is None:
                out.append(Issue(INFO, f"зачёт {z.key}: для дисциплины «{z.discipline_name}» пока нет справочника "
                                       "допуска — проверяются только возраст и разряд из карточки", src))
            if z.discipline_code in POINTS_DISCIPLINES and self.percent_method is None:
                warn(f"зачёт {z.key}: результат в баллах, а методика «% от победителя» не задана — "
                     "нормативы не будут рассчитаны", "Методика %")
        return out


def _group_key(g: str) -> str:
    return "".join(str(g).split()).upper().replace("\\", "/").replace("Ё", "Е")
