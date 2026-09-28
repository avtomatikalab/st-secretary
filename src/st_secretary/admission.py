"""Проверка допуска участника по возрасту и квалификации (справочник reference/data/admission.toml)."""

from __future__ import annotations

from datetime import date

from st_secretary.issues import Issue
from st_secretary.qualification import Qual
from st_secretary.reference import admission_profiles
from st_secretary.textclean import from_years, years


def age_in_year(birth: date, year: int) -> int:
    """Возраст по году: сколько исполняется в календарный год соревнований (раздел 3, п. 5.1)."""
    return year - birth.year


def full_years(birth: date, on: date) -> int:
    """Полных лет на дату."""
    return on.year - birth.year - ((on.month, on.day) < (birth.month, birth.day))


def _profile(key: str) -> dict:
    try:
        return admission_profiles()[key]
    except KeyError:
        raise KeyError(f"Нет профиля допуска {key!r}; есть: {', '.join(admission_profiles())}") from None


def age_groups(profile_key: str, age: int, distance_class: int | None = None) -> list[str]:
    """Возрастные группы, в которые проходит спортсмен (без учёта права выступать в старшей группе)."""
    out = []
    for g in _profile(profile_key)["age_group"]:
        if age < g["min"] or ("max" in g and age > g["max"]):
            continue
        if distance_class is not None:
            if "classes" in g and distance_class not in g["classes"]:
                continue
            if "max_class" in g and distance_class > g["max_class"]:
                continue
        out.append(g["name"])
    return out


def _qual_ok(qual: Qual, need: str | None, alt: str | None = None) -> bool:
    if need is None:
        return True
    if qual >= Qual[need]:
        return True
    return alt is not None and qual >= Qual[alt]


def check_athlete(
    profile_key: str,
    distance_class: int,
    birth: date,
    qual: Qual,
    year: int,
) -> list[Issue]:
    """Минимальные возраст и квалификация для класса дистанции."""
    prof = _profile(profile_key)
    age = age_in_year(birth, year)
    issues: list[Issue] = []

    reqs = [r for r in prof.get("class_requirement", []) if r["class"] == distance_class]
    for r in reqs:
        options = r.get("options") or [
            {"min_age": r["min_age"], "min_qualification": r.get("min_qualification"),
             "alt_min_qualification": r.get("alt_min_qualification")}
        ]
        if not any(age >= o["min_age"] and _qual_ok(qual, o.get("min_qualification"), o.get("alt_min_qualification"))
                   for o in options):
            need = " или ".join(
                f"с {from_years(o['min_age'])}" + (f" и не ниже {Qual[o['min_qualification']].label}" if o.get("min_qualification") else "")
                for o in options
            )
            issues.append(Issue(
                "error", f"{distance_class} класс: нужно {need}; у спортсмена {years(age)} в {year} г., {qual.label}",
                why=f"Правила допускают на дистанцию {distance_class} класса только спортсменов не моложе определённого "
                    "возраста и с разрядом не ниже определённого. Возраст считается по году: сколько исполняется "
                    f"в {year} году.",
                todo="Проверьте дату рождения и разряд (на комиссии — по документам). Если данные верны, "
                     "на этот класс дистанции спортсмена допустить нельзя."))

    if not age_groups(profile_key, age, distance_class):
        issues.append(Issue(
            "error", f"возраст {years(age)} в {year} г. не входит ни в одну возрастную группу для {distance_class} класса",
            why=f"Правила задают возрастные группы для каждого класса дистанции; в {year} году спортсмену исполняется "
                f"{years(age)}, и ни одна группа для {distance_class} класса этот возраст не включает.",
            todo="Проверьте дату рождения. Если она верна — вопрос о допуске решает ГСК на комиссии по допуску."))
    return issues


def check_leader(profile_key: str, distance_class: int, birth: date, start: date) -> list[Issue]:
    """Возраст руководителя команды на день начала соревнований (ПСР: часть 4, п. 2.1.5)."""
    prof = _profile(profile_key)
    if "leader_min_age" not in prof:
        return []
    need = prof["leader_min_age_class_5_6"] if distance_class >= 5 else prof["leader_min_age"]
    years = full_years(birth, start)
    if years < need:
        return [Issue("error", f"руководителю {years} полных лет на {start:%d.%m.%Y}, нужно не менее {need}")]
    return []
