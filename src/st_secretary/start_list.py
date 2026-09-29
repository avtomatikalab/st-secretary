"""Жеребьёвка и стартовый протокол (Правила, раздел 3, п. 8.4).

Очерёдность стартов определяется жеребьёвкой — для возрастных групп, мужчин и женщин раздельно (в программе —
по зачётам). Способы по Правилам: общая (единая для всех; допускается компьютерная — случайными числами),
групповая (участники делятся на группы по квалификации, внутри группы — жребий), командная, свободный старт.
Жеребьёвку часто проводят на совещании ГСК с представителями — тогда секретарь вносит вытянутый порядок
вручную. По окончании жеребьёвки составляется стартовый протокол; он публикуется не позднее чем за час до
старта. С публикации идёт час на протесты по допуску (п. 8.17).

Хранение — «draw» у зачёта в Результаты_дистанции.json: способ, порядок (файлы заявок), время жеребьёвки,
число для генератора случайных чисел (жребий можно повторить и проверить), день и время первого старта,
интервал, ручные правки времени, отметка о публикации. Порядок старта берут таблица результатов (при равенстве —
выше стартовавший раньше), телефоны судей и, в дисциплинах по времени, время старта по протоколу.
"""

from __future__ import annotations

import hashlib
import random
import re
from dataclasses import dataclass, field
from datetime import date, datetime, time
from fractions import Fraction
from typing import Any

from st_secretary.issues import INFO, WARNING, Issue
from st_secretary.qualification import Qual

METHODS = {
    "random": "Общая жеребьёвка — случайный порядок (компьютерная, п. 8.4)",
    "rank": "Групповая — по рангу состава: команды делятся на группы, внутри группы — жребий",
    "manual": "По жеребьёвке на совещании — номера вытянули представители, секретарь вносит порядок",
    "number": "По стартовым номерам (без жеребьёвки)",
}
DAY = 86400


def parse_hm(s) -> int | None:
    """«10:00», «9.30», «10:00:30» → секунды от начала суток; пусто → None; не время → ValueError."""
    t = str(s or "").strip().replace(".", ":")
    if not t:
        return None
    m = re.fullmatch(r"(\d{1,2}):(\d{2})(?::(\d{2}))?", t)
    if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59 or int(m.group(3) or 0) > 59:
        raise ValueError(t)
    return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3) or 0)


def hm_text(sec: int | None) -> str:
    """Секунды → «10:05» (или «10:05:30», если секунды не нулевые). Время суток — всегда две цифры часа."""
    if sec is None:
        return ""
    sec %= DAY
    out = f"{sec // 3600:02d}:{sec % 3600 // 60:02d}"
    return out + (f":{sec % 60:02d}" if sec % 60 else "")


def settings(zdata: dict) -> dict:
    """Настройки жеребьёвки зачёта (со значениями по умолчанию)."""
    d = zdata.get("draw", {})
    groups = str(d.get("groups", "2"))
    return {"method": d.get("method") if d.get("method") in METHODS else "random",
            "groups": int(groups) if groups.isdigit() and 1 <= int(groups) <= 10 else 2,
            "strong": "first" if d.get("strong") == "first" else "last",
            "day": str(d.get("day", "")), "first": str(d.get("first", "")),
            "interval": str(d.get("interval", ""))}


def _num_key(t) -> tuple:
    n = re.sub(r"\D", "", str(t.number or ""))
    return (0, int(n), t.file) if n else (1, 0, t.file)


def team_rank(members, rank_format: str | None, norms) -> Fraction | None:
    """Ранг состава, как в стартовом протоколе СЕКРЕТАРЬ_ST: баллы разрядов участников (таблица ранга норм),
    делённые как для ранга вида программы (связка — на 2, группа — на 4 или на число участников)."""
    if norms is None or not rank_format or not members:
        return None
    from st_secretary.rank import rank_divisor

    try:
        div = rank_divisor(rank_format, len(members))
    except ValueError:
        div = len(members)
    return sum((norms.rank_points.get(m.qual or Qual.BR, Fraction(0)) for m in members), Fraction(0)) / div


def draw(teams: list, method: str, seed: int, ranks: dict[str, Fraction | None] | None = None,
         groups: int = 2, strong_last: bool = True) -> list[str]:
    """Порядок старта (файлы заявок). Жребий повторяется при том же seed и том же составе зачёта."""
    base = sorted(teams, key=_num_key)
    rng = random.Random(seed)
    if method == "random":
        rng.shuffle(base)
        return [t.file for t in base]
    if method == "rank":
        ranks = ranks or {}
        by_rank = sorted(base, key=lambda t: ranks.get(t.file) or Fraction(0))  # слабые — первыми
        n = len(by_rank)
        k = max(1, min(groups, n or 1))
        out, start = [], 0
        for g in range(k):  # группы почти равные по числу команд
            size = n // k + (1 if g < n % k else 0)
            part = by_rank[start:start + size]
            start += size
            rng.shuffle(part)
            out.append(part)
        if not strong_last:
            out.reverse()
        return [t.file for part in out for t in part]
    return [t.file for t in base]  # по номерам; «вручную» — начинаем с порядка номеров


def ordered(teams: list, zdata: dict) -> list:
    """Команды в порядке старта: по жеребьёвке, кто в неё не попал — следом по номерам."""
    order = zdata.get("draw", {}).get("order") or []
    pos = {f: i for i, f in enumerate(order)}
    return sorted(teams, key=lambda t: (0, pos[t.file], ()) if t.file in pos else (1, 0, _num_key(t)))


def planned_starts(zdata: dict, teams: list) -> dict[str, int]:
    """Время старта по стартовому протоколу (секунды от начала суток) — у кого оно есть."""
    return {r.inp.file: r.time for r in _rows(zdata, [t for t in teams if t.admitted], {}) if r.time is not None}


@dataclass
class StartRow:
    pos: int
    inp: Any  # psr_run.TeamInput
    time: int | None  # секунды от начала суток
    manual_time: bool
    rank: Fraction | None
    drawn: bool  # была при жеребьёвке


@dataclass
class StartList:
    zachet: Any
    rows: list[StartRow]
    issues: list[Issue]
    settings: dict
    drawn_at: datetime | None = None
    seed: int | None = None
    method: str = ""
    start_day: date | None = None
    published: dict | None = None
    out: list[str] = field(default_factory=list)  # были при жеребьёвке, но больше не в зачёте
    edited_at: datetime | None = None  # порядок после жеребьёвки меняли вручную

    @property
    def drawn(self) -> bool:
        return self.drawn_at is not None

    @property
    def first_start(self) -> datetime | None:
        times = [r.time for r in self.rows if r.time is not None]
        if not times or self.start_day is None:
            return None
        t = min(times)
        return datetime.combine(self.start_day, time(t // 3600, t % 3600 // 60))

    @property
    def fingerprint(self) -> str:
        """Отпечаток протокола: по нему видно, что после публикации порядок или время меняли."""
        s = ";".join(f"{r.pos}|{r.inp.file}|{r.time}" for r in self.rows)
        return hashlib.sha1(s.encode("utf-8")).hexdigest()[:16]

    @property
    def changed(self) -> bool:
        return bool(self.published and self.published.get("fp") != self.fingerprint)


def _rows(zdata: dict, teams: list, ranks: dict) -> list[StartRow]:
    d = zdata.get("draw", {})
    st = settings(zdata)
    order = d.get("order") or []
    try:
        first = parse_hm(st["first"])
    except ValueError:
        first = None
    iv = st["interval"].strip().replace(",", ".")
    interval = round(float(iv) * 60) if re.fullmatch(r"\d+(\.\d+)?", iv) else 0
    manual = d.get("times", {})
    rows = []
    for i, t in enumerate(ordered(teams, zdata), start=1):
        own = None
        try:
            own = parse_hm(manual.get(t.file))
        except ValueError:
            pass
        auto = first + (i - 1) * interval if first is not None else None
        rows.append(StartRow(i, t, own if own is not None else auto, own is not None, ranks.get(t.file),
                             t.file in order))
    return rows


def build(z, zdata: dict, teams: list, ranks: dict[str, Fraction | None] | None = None,
          default_day: date | None = None) -> StartList:
    """Стартовый протокол зачёта: допущенные команды в порядке жеребьёвки, время старта, замечания."""
    d = zdata.get("draw", {})
    st = settings(zdata)
    admitted = [t for t in teams if t.admitted]
    rows = _rows(zdata, admitted, ranks or {})
    issues: list[Issue] = []
    at = None
    if d.get("at"):
        try:
            at = datetime.fromisoformat(d["at"])
        except ValueError:
            at = None
    day = default_day
    if st["day"]:
        try:
            day = date.fromisoformat(st["day"])
        except ValueError:
            day = None
    sl = StartList(z, rows, issues, st, at, d.get("seed"), d.get("done_method", ""), day, d.get("published"))
    if d.get("edited"):
        try:
            sl.edited_at = datetime.fromisoformat(d["edited"])
        except ValueError:
            pass
    have = {t.file for t in admitted}
    names = {t.file: t.team for t in teams}
    sl.out = [names.get(f, f) for f in d.get("order", []) if f not in have]
    if at:
        late = [r.inp.team for r in rows if not r.drawn]
        if late:
            issues.append(Issue(WARNING, f"не было при жеребьёвке — поставлены в конец: {', '.join(late)}. "
                                         "Проведите жеребьёвку заново или поставьте их вручную", source=z.key))
        if sl.out:
            issues.append(Issue(INFO, f"были при жеребьёвке, но сейчас не в зачёте (не допущены или перешли): "
                                      f"{', '.join(sl.out)} — убраны из протокола", source=z.key))
    no_number = [r.inp.team for r in rows if not str(r.inp.number or "").strip()]
    if no_number:
        issues.append(Issue(INFO, f"нет стартового номера: {', '.join(no_number)} — номера присваивает комиссия по "
                                  "допуску (кнопка «Присвоить тем, у кого нет»)", source=z.key))
    try:
        parse_hm(st["first"])
    except ValueError:
        issues.append(Issue(WARNING, f"время первого старта «{st['first']}» — не время (нужно чч:мм)", source=z.key))
    bad = [r.inp.team for r in rows if d.get("times", {}).get(r.inp.file) and not r.manual_time]
    if bad:
        issues.append(Issue(WARNING, f"время старта вписано не по форме чч:мм — не учтено: {', '.join(bad)}",
                            source=z.key))
    times = [r.time for r in rows if r.time is not None]
    iv = st["interval"].strip()
    if iv and iv not in ("0",) and len(times) != len(set(times)):
        same = sorted({hm_text(t) for t in times if times.count(t) > 1})
        issues.append(Issue(WARNING, f"одинаковое время старта у нескольких команд: {', '.join(same)}", source=z.key))
    if sl.published and sl.first_start:
        pub = datetime.fromisoformat(sl.published["at"])
        if (sl.first_start - pub).total_seconds() < 3600:
            issues.append(Issue(WARNING, f"стартовый протокол опубликован в {pub:%H:%M} — меньше чем за час до "
                                         f"старта ({sl.first_start:%H:%M}); по Правилам (п. 8.4) — не позднее чем "
                                         "за час", source=z.key))
    return sl
