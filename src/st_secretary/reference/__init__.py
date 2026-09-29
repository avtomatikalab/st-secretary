"""Справочники: ВРВС, разрядные нормы, требования допуска.

Данные лежат в reference/data/*.toml рядом с кодом; в каждом файле указан первоисточник.
Справочники не вводятся вручную на соревновании — это и есть защита от «забытых» настроек
(например, кода ВРВС чужой дисциплины в шапке протокола).
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from enum import IntEnum
from fractions import Fraction
from functools import cache
from importlib.resources import files

from st_secretary.qualification import Qual

_DATA = files("st_secretary.reference") / "data"


def _load(relpath: str) -> dict:
    return tomllib.loads((_DATA / relpath).read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- ВРВС


@dataclass(frozen=True)
class Discipline:
    code: str
    name: str
    group: str
    rank_format: str | None  # individual / pair / group / crew; None — для маршрутов


def _norm_name(name: str) -> str:
    return " ".join(name.lower().replace("–", "-").replace("—", "-").replace("ё", "е").split())


@cache
def disciplines() -> tuple[Discipline, ...]:
    data = _load("vrvs.toml")
    return tuple(
        Discipline(d["code"], d["name"], d["group"], d.get("rank_format")) for d in data["discipline"]
    )


def discipline_by_code(code: str) -> Discipline:
    wanted = "".join(code.split()).upper()
    for d in disciplines():
        if d.code == wanted:
            return d
    raise KeyError(f"Код ВРВС {code!r} не найден в справочнике вида спорта «спортивный туризм»")


def discipline_by_name(name: str) -> Discipline:
    key = _norm_name(name)
    for d in disciplines():
        if _norm_name(d.name) == key:
            return d
    raise KeyError(f"Дисциплина {name!r} не найдена в ВРВС")


def check_code_matches_name(code: str, name: str) -> str | None:
    """Проверить пару «код ВРВС — наименование дисциплины» из шапки протокола.

    Возвращает текст замечания или None, если всё верно.
    """
    try:
        by_code = discipline_by_code(code)
    except KeyError as e:
        return str(e)
    if _norm_name(by_code.name) == _norm_name(name):
        return None
    try:
        expected = discipline_by_name(name).code
        hint = f"; для «{name}» нужен код {expected}"
    except KeyError:
        hint = f"; наименование «{name}» не совпадает с ВРВС"
    return f"Код {code} в ВРВС означает «{by_code.name}»{hint}"


# --------------------------------------------------------------------------- Уровень соревнований


class Level(IntEnum):
    """Уровень (статус) соревнований — для условий выполнения норм."""

    MUNICIPAL = 1
    REGIONAL = 2  # субъект РФ
    INTERREGIONAL = 3
    ALL_RUSSIAN = 4


_LEVEL_BY_NAME = {"any": Level.MUNICIPAL, "municipal": Level.MUNICIPAL, "regional": Level.REGIONAL,
                  "interregional": Level.INTERREGIONAL, "all_russian": Level.ALL_RUSSIAN}


# --------------------------------------------------------------------------- Разрядные нормы

_THRESHOLD_KEYS = {"I": Qual.I, "II": Qual.II, "III": Qual.III, "Y1": Qual.Y1, "Y2": Qual.Y2, "Y3": Qual.Y3}
_POINT_KEYS = {"MS": Qual.MS, "KMS": Qual.KMS, **_THRESHOLD_KEYS}


@dataclass(frozen=True)
class NormRow:
    min_rank: Fraction
    label: str
    thresholds: dict[Qual, int]  # разряд → максимальный процент от результата победителя
    y3_condition: bool  # III юношеский — текстовым условием, а не процентом


@dataclass(frozen=True)
class NormEdition:
    edition: str
    title: str
    rows: tuple[NormRow, ...]
    class_rows: dict[int, tuple[Fraction, Fraction]]  # класс → (min первой строки, min последней)
    min_age: dict[str, int]
    min_level: dict[Qual, Level]  # минимальный уровень соревнований для разряда
    nordic_i_allowed: bool
    nordic_min_age: int
    junior_iii_text: str | None
    rank_points: dict[Qual, Fraction]  # баллы ранга для личных дисциплин
    rank_min_participants: int
    rank_max_place: int


def norm_editions() -> tuple[str, ...]:
    return tuple(sorted(p.name.removesuffix(".toml") for p in (_DATA / "norms").iterdir()
                        if p.name.endswith(".toml")))


@cache
def norm_edition(edition: str) -> NormEdition:
    if edition not in norm_editions():
        raise KeyError(f"Нет редакции норм {edition!r}; есть: {', '.join(norm_editions())}")
    d = _load(f"norms/{edition}.toml")
    rows = tuple(
        NormRow(
            min_rank=Fraction(r["min"]),
            label=r["label"],
            thresholds={q: int(r[k]) for k, q in _THRESHOLD_KEYS.items() if k in r},
            y3_condition=bool(r.get("Y3_condition", False)),
        )
        for r in d["row"]
    )
    mins = [r.min_rank for r in rows]
    if mins != sorted(mins) or len(set(mins)) != len(mins):
        raise ValueError(f"Нормы {edition}: строки должны идти по возрастанию ранга без повторов")
    nw = d["nordic_walking"]
    status = d["status"]
    min_level = {Qual.I: _LEVEL_BY_NAME[status["I"]], Qual.II: _LEVEL_BY_NAME[status["II"]],
                 Qual.III: _LEVEL_BY_NAME[status["III"]]}
    for q in (Qual.Y1, Qual.Y2, Qual.Y3):
        min_level[q] = _LEVEL_BY_NAME[status["junior"]]
    rp = d["rank_points"]
    return NormEdition(
        edition=d["edition"],
        title=d["title"],
        rows=rows,
        class_rows={int(k): (Fraction(v[0]), Fraction(v[1])) for k, v in d["class_rows"].items()},
        min_age=dict(d["min_age"]),
        min_level=min_level,
        nordic_i_allowed=bool(nw["I_allowed"]),
        nordic_min_age=int(nw.get("min_age_I_II_III", nw.get("min_age_II_III", 18))),
        junior_iii_text=d.get("junior_III", {}).get("text"),
        rank_points={q: Fraction(str(rp[k])) for k, q in _POINT_KEYS.items()} | {Qual.BR: Fraction(0)},
        rank_min_participants=int(rp["min_participants"]),
        rank_max_place=int(rp["max_place"]),
    )


# --------------------------------------------------------------------------- Баллы практики судейства


@cache
def judge_points() -> dict:
    """Квалификационные требования к спортивным судьям (приказ № 1101): баллы по должностям и статусам
    соревнований, условия присвоения и подтверждения (tools/import_1101.py)."""
    import json

    return json.loads((_DATA / "judge_points_1101.json").read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- Требования допуска


@cache
def admission_profiles() -> dict:
    return _load("admission.toml")["profile"]
