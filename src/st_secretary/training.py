"""Учебное соревнование — на выдуманных данных, чтобы потренироваться до настоящих соревнований.

«Учебный чемпионат г. Энска»: карточка с судейской коллегией, два зачёта (ПСР и спелео-группа), 14 команд
с выдуманными участниками, этапы дистанций. В нескольких заявках ошибки оставлены специально — такие же, как
в настоящих заявках: неверная дата рождения, нет женщины в команде, участник младше допустимого возраста,
один человек в двух командах, разряд и ФИО записаны «как попало». Все имена составлены случайно из частых
имён и фамилий; совпадение с реальными людьми случайно.
"""

from __future__ import annotations

import random
from datetime import date, timedelta

from st_secretary.competition import Competition, Official, Zachet
from st_secretary.norms import PercentMethod
from st_secretary.qualification import Qual
from st_secretary.rank import INDIVIDUAL, PAIR
from st_secretary.reference import Level, norm_editions

TITLE = "Учебный чемпионат г. Энска по спортивному туризму"
TOWN = "Энск"  # как в карточке («своя» территория) — так пишут в заявках

SURNAMES = ["Смирнов", "Кузнецов", "Попов", "Васильев", "Петров", "Соколов", "Михайлов", "Новиков", "Федоров",
            "Морозов", "Волков", "Алексеев", "Лебедев", "Семенов", "Егоров", "Павлов", "Козлов", "Степанов",
            "Николаев", "Орлов", "Андреев", "Макаров", "Никитин", "Захаров", "Зайцев", "Соловьев", "Борисов",
            "Яковлев", "Григорьев", "Романов", "Воробьев", "Сергеев", "Кузьмин", "Фролов", "Александров",
            "Дмитриев", "Королев", "Гусев", "Киселев", "Ильин", "Медведев", "Тарасов", "Белов", "Комаров"]
MALE = [("Алексей", "Алексеев"), ("Сергей", "Сергеев"), ("Иван", "Иванов"), ("Андрей", "Андреев"),
        ("Дмитрий", "Дмитриев"), ("Михаил", "Михайлов"), ("Николай", "Николаев"), ("Павел", "Павлов"),
        ("Олег", "Олегов"), ("Виктор", "Викторов"), ("Юрий", "Юрьев"), ("Роман", "Романов"), ("Егор", "Егоров"),
        ("Кирилл", "Кириллов"), ("Максим", "Максимов"), ("Антон", "Антонов"), ("Денис", "Денисов"),
        ("Евгений", "Евгеньев"), ("Владимир", "Владимиров"), ("Григорий", "Григорьев")]
FEMALE = ["Анна", "Мария", "Елена", "Ольга", "Наталья", "Татьяна", "Ирина", "Светлана", "Екатерина", "Дарья",
          "Полина", "Ксения", "Вера", "Алина", "Юлия", "Марина", "Валентина", "Людмила", "Софья", "Виктория"]
QUALS = [Qual.BR] * 8 + [Qual.III] * 4 + [Qual.II] * 3 + [Qual.I] * 2 + [Qual.KMS] * 2 + [Qual.MS]

PSR_TEAMS = [("Кедр", TOWN), ("Сосна", TOWN), ("Пихта", TOWN), ("Лиственница", "пос. Лесной"),
             ("Бурундуки", TOWN), ("Горный ветер", "Н-ск"), ("Перевал", "энск"), ("Ёлки-палки", TOWN)]
SPELEO_TEAMS = [("Сталактит", TOWN), ("Сталагмит", TOWN), ("Летучие мыши", "Н-ск"), ("Карстовики", "г. Энск"),
                ("Подземка", "пос. Лесной"), ("Грот", TOWN)]

PSR_STAGES = [("Тур 1", "Ориентирование", "300"), ("Тур 1", "Переправа по бревну", "200"),
              ("Тур 1", "Навесная переправа", "250"), ("Тур 1", "Узлы", "100"),
              ("Тур 2", "Транспортировка пострадавшего", "300"), ("Тур 2", "Бивак", "150"),
              ("Тур 2", "Первая помощь", "200"), ("Бонус", "Знание района", "")]
SPELEO_STAGES = ["Колодец 12 м", "Шкуродёр", "Траверс", "Подъём по верёвке"]


def _fio(rng: random.Random, male: bool, used: set[str]) -> str:
    """Случайное ФИО из частых имён; отчество — от имени отца: «Юрьев» + «ич» / «на»."""
    while True:
        sur, father = rng.choice(SURNAMES), rng.choice(MALE)[1]
        fio = (f"{sur} {rng.choice(MALE)[0]} {father}ич" if male
               else f"{sur}а {rng.choice(FEMALE)} {father}на")
        if fio not in used:
            used.add(fio)
            return fio


def card(today: date) -> Competition:
    """Карточка: ближайшие суббота и воскресенье, приём заявок — до среды."""
    sat = today + timedelta(days=(5 - today.weekday()) % 7 or 7)
    year = sat.year
    edition = next((e for e in norm_editions() if int(e[:4]) <= year <= int(e[-4:])), "2026-2029")
    return Competition(
        title=TITLE, kind="Чемпионат", level=Level.MUNICIPAL, date_from=sat, date_to=sat + timedelta(days=1),
        place="окрестности г. Энска", host_territory="Энск",
        organizers=["Учебная федерация спортивного туризма г. Энска"], calendar_number="",
        norms_edition=edition, percent_method=PercentMethod.POINTS_RELATIVE_TO_WINNER,
        preapp_deadline=sat - timedelta(days=3),
        officials=[Official("Главный судья", "Орлов Виктор Павлович", "СС1К", "г. Энск"),
                   Official("Главный секретарь", "Лебедева Ольга Николаевна", "СС1К", "г. Энск"),
                   Official("Заместитель главного судьи по судейству", "Никитин Андрей Сергеевич", "СС2К", "г. Энск"),
                   Official("Заместитель главного судьи по безопасности", "Зайцев Павел Олегович", "СС2К", "г. Энск"),
                   Official("Начальник дистанции", "Егоров Максим Юрьевич", "СС3К", "г. Энск"),
                   Official("Заместитель главного секретаря", "Фролова Дарья Михайловна", "СС3К", "г. Энск")],
        zachety=[Zachet("М/Ж", 3, "0840161811Я", age_from=22, age_from_by_gsk=16, min_qual=Qual.BR, team_size=4,
                        min_men=1, min_women=1, fee=3000),
                 Zachet("М/Ж", 2, "0840271811Я", age_from=14, min_qual=Qual.BR, team_size=4, min_men=1,
                        min_women=0, fee=2000)],
    )


def applications(comp: Competition, seed: int = 2026) -> list[tuple[dict, list[dict]]]:
    """Заявки команд: (шапка, строки участников) — в раскладке формы программы."""
    rng = random.Random(seed)
    used: set[str] = set()
    year = comp.date_from.year
    out = []

    def person(male: bool, age_from: int, age_to: int, z: Zachet, chip: str = "") -> dict:
        fio = _fio(rng, male, used)
        born = date(year - rng.randint(age_from, age_to), rng.randint(1, 12), rng.randint(1, 28))
        return {"fio": fio, "birth": born, "qual": rng.choice(QUALS).label, "sex": "м" if male else "ж",
                "group": z.group, "cls": str(z.distance_class), "chip": chip, "personal": "", "pair": "",
                "pair_num": "", "team_dist": "1"}

    psr, speleo = comp.zachety
    for team, terr in PSR_TEAMS:
        sexes = [True, False, True, rng.random() < 0.5]
        rows = [person(m, 22, 45, psr) for m in sexes]
        out.append((team, terr, rows))
    for n, (team, terr) in enumerate(SPELEO_TEAMS, start=1):
        sexes = [True, True, False, rng.random() < 0.6]
        rows = [person(m, 16, 40, speleo, chip=f"81{n:02d}{i:03d}" if i == 0 else "") for i, m in enumerate(sexes)]
        out.append((team, terr, rows))

    by = {t: rows for t, _, rows in out}
    # ошибки — специально, для тренировки
    by["Бурундуки"][1]["birth"] = "31.02.1998"  # такой даты нет
    for r in by["Ёлки-палки"]:  # в команде нет ни одной женщины
        if r["sex"] == "ж":
            r["fio"], r["sex"] = _fio(rng, True, used), "м"
    by["Перевал"][3]["birth"] = date(year - 15, 3, 14)  # 15 лет — младше, чем допускает даже ГСК
    by["Сосна"][0]["qual"] = "кмс"  # разряд и ФИО «как попало» — программа приведёт к правильному виду
    by["Сосна"][1]["fio"] = by["Сосна"][1]["fio"].upper()
    by["Сосна"][2]["qual"] = "3ю"
    by["Пихта"][2] = dict(by["Кедр"][0])  # один человек в двух командах
    by["Горный ветер"][3]["sex"] = ""  # пол не указан — видно по отчеству

    apps = []
    for i, (team, terr, rows) in enumerate(out, start=1):
        rep = rows[0]["fio"]
        head = {"team": team, "territory": terr, "representative": rep,
                "contacts": f"8 900 000-00-{i:02d}, team{i}@example.com", "declared": str(len(rows))}
        apps.append((head, rows))
    return apps


def run_data() -> dict:
    """Этапы дистанций — чтобы сразу вносить баллы и время."""
    return {"zachety": {
        "М/Ж_3": {"stages": [{"id": f"s{i}", "tour": t, "name": n, "max": mx, "kv": ""}
                             for i, (t, n, mx) in enumerate(PSR_STAGES, start=1)], "tie": "same"},
        "М/Ж_2": {"stages": [{"id": f"s{i}", "tour": "", "name": n, "max": "", "kv": "15"}
                             for i, n in enumerate(SPELEO_STAGES, start=1)],
                  "expected": "40", "kv": "90", "spp": "", "cutoff_pairs": ""},
    }}


# ------------------------------------------------------------------ «Заполнить примером» (учебный режим, п. 53)

EXAMPLE_TEAMS = ["Кедр", "Сосна", "Пихта", "Лиственница", "Бурундуки", "Ёлки-палки", "Перевал", "Горный ветер",
                 "Сталактит", "Сталагмит", "Летучие мыши", "Карстовики", "Подземка", "Сталкер", "Азимут", "Меридиан"]


def example_team(comp: Competition, taken: set[str], seed: int | None = None) -> tuple[dict, list[dict]]:
    """Заявка-пример для формы заявки этого соревнования: команда, которой ещё нет (taken — названия заявленных),
    четыре выдуманных участника под первый зачёт карточки — возраст, пол и состав по его требованиям."""
    rng = random.Random(seed)
    z = comp.zachety[0]
    name = next((t for t in rng.sample(EXAMPLE_TEAMS, len(EXAMPLE_TEAMS)) if t not in taken),
                f"Команда {rng.randint(10, 99)}")
    lo = max(z.age_from or 18, 14 if z.age_from else 18)
    hi = max(lo + 1, min(z.age_to or lo + 25, lo + 25))
    size = 4 if not z.team_size else min(max(z.team_size, 2), 6)
    women = max(z.min_women, 1 if size > 2 else 0)
    sexes = [False] * women + [True] * (size - women)
    rng.shuffle(sexes)
    used: set[str] = set()
    year = comp.date_from.year
    fmt = z.rank_format
    rows = []
    for i, male in enumerate(sexes):
        born = date(year - rng.randint(lo, hi), rng.randint(1, 12), rng.randint(1, 28))
        rows.append({"fio": _fio(rng, male, used), "birth": born.strftime("%d.%m.%Y"),
                     "qual": rng.choice(QUALS).label, "sex": "м" if male else "ж",
                     "zachet": z.name or f"{z.group}_{z.distance_class}",
                     "chip": "", "personal": "1" if fmt == INDIVIDUAL else "",
                     "pair": ("см" if fmt == PAIR else ""), "pair_num": ("1" if fmt == PAIR and i < 2 else
                                                                        "2" if fmt == PAIR else ""),
                     "team_dist": "" if fmt in (INDIVIDUAL, PAIR) else "1"})
    head = {"team": name, "territory": comp.host_territory or TOWN, "representative": rows[0]["fio"],
            "contacts": "8 900 000-00-00, example@example.com", "declared": str(len(rows))}
    return head, rows


def _inn(rng: random.Random) -> str:
    d = [rng.randint(0, 9) for _ in range(10)]
    for weights in ((7, 2, 4, 10, 3, 5, 9, 4, 6, 8), (3, 7, 2, 4, 10, 3, 5, 9, 4, 6, 8)):
        d.append(sum(w * x for w, x in zip(weights, d)) % 11 % 10)
    return "".join(map(str, d))


def _snils(rng: random.Random) -> str:
    d = "".join(str(rng.randint(0, 9)) for _ in range(9))
    s = sum(int(c) * (9 - i) for i, c in enumerate(d))
    check = s if s < 100 else 0 if s in (100, 101) else s % 101 % 100
    return f"{d[:3]}-{d[3:6]}-{d[6:]} {check:02d}"


def _account(rng: random.Random, bik: str) -> str:
    a = [rng.randint(0, 9) for _ in range(20)]
    a[:5] = [4, 0, 8, 1, 7]  # счёт физического лица в рублях
    for k in range(10):  # контрольный ключ — девятая цифра (как staff.account_ok)
        a[8] = k
        if sum(int(c) * (7, 1, 3)[i % 3] for i, c in enumerate(bik[-3:] + "".join(map(str, a)))) % 10 == 0:
            break
    return "".join(map(str, a))


EXAMPLE_BIK = "049999999"  # выдуманный БИК — у примера и счёт выдуманный


def example_person(seed: int | None = None) -> dict:
    """Личные данные-пример для карточки человека (договоры): всё выдуманное, но с верными контрольными цифрами
    ИНН, СНИЛС и счёта — чтобы проверка карточки не ругалась."""
    rng = random.Random(seed)
    born = date(rng.randint(1965, 1998), rng.randint(1, 12), rng.randint(1, 28))
    issued = date(born.year + 20, rng.randint(1, 12), rng.randint(1, 28))
    return {"birth": born.strftime("%d.%m.%Y"), "passport": f"04 99 {rng.randint(100000, 999999)}",
            "issued_by": "ГУ МВД России по Учебному краю (пример)", "issued_on": issued.strftime("%d.%m.%Y"),
            "dept_code": "999-999", "address": "660000, Учебный край, г. Энск, ул. Примерная, д. 1, кв. 1",
            "inn": _inn(rng), "snils": _snils(rng), "account": _account(rng, EXAMPLE_BIK),
            "bank": "Учебный банк (пример)", "bik": EXAMPLE_BIK, "phone": "+7 900 000-00-00", "judge_id": ""}


def create(store, today: date):
    """Папка учебного соревнования в хранилище программы; даты — ближайшие выходные после today."""
    comp = card(today)
    f = store.create(comp)
    for head, rows in applications(comp):
        f.save_preapp(None, head, rows, [q.label for q in Qual])
    f.save_run_data(run_data())
    return f
