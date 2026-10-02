"""Итоги соревнований для документов по итогам: места по зачётам, составы, выполненные разряды.

Места берутся из расчёта программы (официальный протокол — psr_run, time_run: «Утвердить результаты» передаёт
места и разряды сюда), из итогового протокола СЕКРЕТАРЬ_ST («Считать протокол» → файл .xls) или вписываются
вручную. Даты рождения и пол участников — из заявок: в протоколе их нет, а для выписок на разряды дата рождения
обязательна (ЕВСК, п. 67.8.2).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

from st_secretary.competition import Competition
from st_secretary.preapp import PreappResult
from st_secretary.qualification import parse_members_with_quals
from st_secretary.rank import CREW, GROUP, INDIVIDUAL, PAIR
from st_secretary.textclean import name_key

ROMAN = {1: "I", 2: "II", 3: "III"}
MANUAL = "вручную"
MONTHS_GEN = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября",
              "ноября", "декабря"]


def person_key(name: str) -> str:
    """ФИО для сравнения (textclean.name_key)."""
    return name_key(name)


@dataclass
class Member:
    fio: str
    qual: str  # разряд на момент соревнований, как в протоколе («КМС», «II», «б/р»)
    birth: date | None = None
    birth_year: int | None = None
    sex: str | None = None

    @property
    def first_last(self) -> str:
        """«Имя Фамилия» — так имена пишут в дипломах."""
        parts = self.fio.split()
        return f"{parts[1]} {parts[0]}" if len(parts) >= 2 else self.fio

    @property
    def birth_text(self) -> str:
        if self.birth:
            return f"{self.birth:%d.%m.%Y}"
        return str(self.birth_year) if self.birth_year else ""


@dataclass
class Placement:
    team: str
    territory: str
    place: int | None  # None — место не присуждено (снят, не финишировал)
    result: str = ""
    norm: str = ""  # выполненный норматив (разряд), пусто — не выполнен
    number: str = ""
    members: list[Member] = field(default_factory=list)

    @property
    def place_text(self) -> str:
        return str(self.place) if self.place else "—"


@dataclass
class ZachetResults:
    key: str  # «М/Ж_3»
    group_text: str  # как группа названа в протоколе: «МУЖЧИНЫ/ЖЕНЩИНЫ. СМЕШАННЫЕ ГРУППЫ»
    rank: str
    source: str  # имя файла протокола или «вручную»
    rows: list[Placement]

    @property
    def medalists(self) -> list[Placement]:
        return sorted((r for r in self.rows if r.place in ROMAN), key=lambda r: r.place)


# ------------------------------------------------------------------ откуда берутся результаты


def from_protocol(proto) -> dict:
    """Итоговый протокол СЕКРЕТАРЬ_ST (importers.sekretar_xls.read_result_protocol) → данные зачёта для хранения."""
    joined = " ".join(proto.header_lines)
    m = re.search(r"код ВРВС\s*\S+\s*(?P<group>.+?)(?:\s*Квалификационный ранг|$)", joined)
    rows = []
    for r in proto.rows:
        try:
            members = [{"fio": n, "qual": q.label} for n, q in parse_members_with_quals(r.composition)]
        except ValueError:  # состав без разрядов в скобках — только ФИО через запятую
            members = [{"fio": n.strip(), "qual": ""} for n in r.composition.split(",") if n.strip()]
        rows.append({"team": r.team, "territory": r.territory, "number": r.number,
                     "place": int(r.place) if r.place.isdigit() else None,
                     "result": _num_text(r.result), "norm": r.norm, "members": members})
    return {"source": proto.path.name, "group_text": (m.group("group").strip(" .") if m else ""),
            "rank": proto.rank_text or "", "rows": rows}


def _num_text(v) -> str:
    if v is None:
        return ""
    return str(int(v)) if v == int(v) else str(float(v)).replace(".", ",")


def load(data: dict, comp: Competition, preapps: PreappResult | None) -> list[ZachetResults]:
    """Результаты по зачётам карточки (в её порядке); участники дополнены датой рождения и полом из заявок."""
    known = {}
    for e in (preapps.entries if preapps else []):
        known.setdefault(person_key(e.name.full), e)
    out = []
    for z in comp.zachety:
        d = data.get("zachety", {}).get(z.key)
        if not d:
            continue
        rows = []
        for r in d.get("rows", []):
            members = []
            for mm in r.get("members", []):
                e = known.get(person_key(mm["fio"]))
                members.append(Member(mm["fio"], mm.get("qual", ""), e.birth if e else None,
                                      e.birth_year if e else None, e.sex if e else None))
            rows.append(Placement(r.get("team", ""), r.get("territory", ""), r.get("place"), r.get("result", ""),
                                  r.get("norm", ""), r.get("number", ""), members))
        rows.sort(key=lambda r: (r.place is None, r.place or 0))
        out.append(ZachetResults(z.key, d.get("group_text", ""), d.get("rank", ""), d.get("source", ""), rows))
    return out


# ------------------------------------------------------------------ тексты для документов

_PREPOSITIONAL = {"Чемпионат": "Чемпионате", "Первенство": "Первенстве", "Кубок": "Кубке",
                  "Соревнования": "Соревнованиях", "Фестиваль": "Фестивале", "Турнир": "Турнире",
                  "Спартакиада": "Спартакиаде", "Этап": "Этапе"}


_GENITIVE = {"Чемпионат": "Чемпионата", "Первенство": "Первенства", "Кубок": "Кубка",
             "Соревнования": "Соревнований", "Фестиваль": "Фестиваля", "Турнир": "Турнира",
             "Спартакиада": "Спартакиады", "Этап": "Этапа"}

# Прилагательные перед названием («Учебный», «Краевой», «Открытое», «Всероссийские») — по окончанию; после г, к, х
# в предложном падеже «-ом» («Всероссийском»), после других — «-ем» («Летнем»). Правки, п. 50.
_ADJ_GENITIVE = {"ый": "ого", "ой": "ого", "ий": "его", "ое": "ого", "ее": "его", "ая": "ой", "яя": "ей",
                 "ые": "ых", "ие": "их"}
_ADJ_PREPOSITIONAL = {"ый": "ом", "ой": "ом", "ий": "ем", "ое": "ом", "ее": "ем", "ая": "ой", "яя": "ей",
                      "ые": "ых", "ие": "их"}


def _inflect(word: str, table: dict[str, str]) -> str:
    """Слово в нужном падеже, если оно из списка; регистр первой буквы сохраняется."""
    found = next((v for k, v in table.items() if k.lower() == word.lower()), None)
    if found is None:
        return word
    return found if word[:1].isupper() else found[:1].lower() + found[1:]


def _adjective(word: str, endings: dict[str, str]) -> str | None:
    """Прилагательное в нужном падеже по окончанию; не прилагательное — None."""
    end = word[-2:].lower()
    if len(word) < 4 or end not in endings:
        return None
    new = endings[end]
    if end == "ий" and word[-3:-2].lower() in "гкх":  # «Всероссийский» → «Всероссийского» / «Всероссийском»
        new = "ого" if endings is _ADJ_GENITIVE else "ом"
    return word[:-2] + new


def _title(comp: Competition, table: dict[str, str], endings: dict[str, str]) -> str:
    """Склоняет прилагательные в начале и первое существительное («Учебный чемпионат г. N» → «Учебного
    чемпионата г. N»); если существительное не из списка — название как есть."""
    words = comp.title.split(" ")
    out: list[str] = []
    for i, word in enumerate(words):
        noun = _inflect(word, table)
        if noun != word:
            return " ".join([*out, noun, *words[i + 1:]]).strip()
        adj = _adjective(word, endings)
        if adj is None:
            break
        out.append(adj)
    return comp.title


def title_in(comp: Competition) -> str:
    """«на Чемпионате г. Красноярска…» — название в предложном падеже (с прилагательными в начале)."""
    return _title(comp, _PREPOSITIONAL, _ADJ_PREPOSITIONAL)


def title_of(comp: Competition) -> str:
    """«в судействе Чемпионата г. Красноярска…» — название в родительном падеже (с прилагательными в начале)."""
    return _title(comp, _GENITIVE, _ADJ_GENITIVE)


def date_text(d: date) -> str:
    return f"{d.day} {MONTHS_GEN[d.month - 1]} {d.year} г."


GROUP_WORDS = {"МУЖЧИНЫ": "мужчины", "ЖЕНЩИНЫ": "женщины"}
# «М/Ж» по составу (Правки, п. 31; протоколы ЧК и ПК края 2021): связки — «СМЕШАННЫЕ СВЯЗКИ», группы — «СМЕШАННЫЕ
# ГРУППЫ», в личной — без «смешанных»
MIXED_WORDS = {PAIR: "смешанные связки", GROUP: "смешанные группы", CREW: "смешанные экипажи"}


def group_words(group: str, rank_format: str | None = None, long: bool = False) -> str:
    """Как назвать группу зачёта: «М/Ж» связок — «смешанные связки», групп — «смешанные группы», личной —
    «мужчины/женщины»; long — как в шапке протокола: «мужчины/женщины, смешанные связки»."""
    if group.upper() != "М/Ж":
        return GROUP_WORDS.get(group.upper(), group)
    if rank_format == INDIVIDUAL:
        return "мужчины/женщины"
    mixed = MIXED_WORDS.get(rank_format or GROUP, MIXED_WORDS[GROUP])
    return f"мужчины/женщины, {mixed}" if long else mixed


def group_label(z: ZachetResults, zachet_group: str, rank_format: str | None = None) -> str:
    """Как назвать группу в дипломе: из шапки протокола («СМЕШАННЫЕ ГРУППЫ»), иначе по зачёту и составу."""
    if z.group_text:
        tail = z.group_text.split(".")[-1].strip()
        return (tail or z.group_text).lower()
    return group_words(zachet_group, rank_format)


def rotations(names: list[str]) -> list[list[str]]:
    """У каждого участника группы — свой диплом, где его имя первое; остальные — по кругу, как в заявке."""
    return [names[i:] + names[:i] for i in range(len(names))] or [[]]
