"""Кто выступает в зачёте: команда (группа), связка или отдельный спортсмен — по формату дисциплины ВРВС.

В заявке участие отмечено колонками «Участие в личной», «…в дистанции связок», «…в дистанции-группа»
(СЕКРЕТАРЬ_ST: личная — «1» или «лич», связка — «м», «см», «ж» или «см 2», если связок несколько; группа — номер
группы). По ним участники зачёта делятся на тех, кто выходит на старт и получает место:

- группа (ПСР, спелео-группа, пешеходная группа, горная группа) — участники команды одним составом; если в
  команде несколько групп (разные номера в колонке «группа») — каждая отдельно;
- связка — по двое (или сколько записано) с одной отметкой связки в команде;
- личная (спелео, пешеходная, северная ходьба) — каждый спортсмен отдельно; его номер — «номер команды.номер в
  команде», как вариант 1 нумерации СЕКРЕТАРЬ_ST («XX.Y»).

Если колонки участия в зачёте никто не заполнил — личная дисциплина: каждый участник, связка и группа — вся
команда одним составом (как было раньше и как в заявках ПСР).

Ключ (file) у группы — имя файла заявки, как раньше, чтобы сохранённые баллы не потерялись; у связки и
спортсмена — «файл#…».
"""

from __future__ import annotations

import re

from st_secretary import commission as cm
from st_secretary.psr_run import Member, TeamInput

UNIT_WORDS = {"group": "команда", "crew": "экипаж", "pair": "связка", "individual": "участник"}


def _member(e) -> Member:
    b, year = getattr(e, "birth", None), getattr(e, "birth_year", None)
    birth = b.isoformat() if b else str(year or "")
    return Member(e.name.full, e.qual, e.qual.label if e.qual is not None else "", e.chip, birth)


def _pair_label(e) -> str:
    """«см», «см 2», «м» — как пишут в заявке; номер связки из отдельной колонки добавляется."""
    code = " ".join(str(e.pair).lower().split())
    num = str(e.pair_num or "").strip()
    if num and not code.endswith(num):
        code = f"{code} {num}".strip()
    return code or "1"


def zachet_units(teams: list, z, rank_format: str | None) -> list[TeamInput]:
    """teams — команды комиссии по допуску (TeamCheck): файл, заявка, номер, решение, участники с решениями."""
    fmt = rank_format or "group"
    rows = []
    for t in teams:
        if not t.team:
            continue
        people = [p for p in t.persons if p.entry.zachet and p.entry.zachet.key == z.key and p.status != cm.REJECTED]
        if people:
            rows.append((t, people))
    flag = {"individual": "personal", "pair": "pair"}.get(fmt, "team_dist")
    marked = any(getattr(p.entry, flag) for _, people in rows for p in people)
    out: list[TeamInput] = []
    for t, people in rows:
        admitted = t.status != cm.REJECTED
        number = str(t.number or "")
        base = {"territory": t.team.territory, "admitted": admitted, "representative": t.team.representative,
                "club": t.team.team}
        if fmt == "individual":
            chosen = [p for p in people if p.entry.personal] if marked else people
            for p in chosen:
                e = p.entry
                num = f"{number}.{e.num_in_team}" if number and e.num_in_team else ""
                out.append(TeamInput(f"{t.file}#{cm.person_key(e.name.full)}", e.name.full, number=num,
                                     members=[_member(e)], **base))
            continue
        if fmt == "pair" and marked:
            groups: dict[str, list] = {}
            for p in people:
                if p.entry.pair:
                    groups.setdefault(_pair_label(p.entry), []).append(p)
            for label, ps in groups.items():
                title = t.team.team if len(groups) == 1 else f"{t.team.team} ({label})"
                out.append(TeamInput(f"{t.file}#связка:{label}", title, number=number,
                                     members=[_member(p.entry) for p in ps], **base))
            continue
        # группа (и связка, если участие в связках не отмечено): вся команда; несколько групп — по номерам
        chosen = [p for p in people if p.entry.team_dist or not (p.entry.personal or p.entry.pair)] \
            if fmt != "pair" else people
        if not chosen:
            continue
        nums = sorted({str(p.entry.team_dist).strip() for p in chosen if str(p.entry.team_dist).strip()},
                      key=lambda s: (not s.isdigit(), int(s) if s.isdigit() else 0, s))
        if len(nums) <= 1:
            out.append(TeamInput(t.file, t.team.team, number=number, members=[_member(p.entry) for p in chosen],
                                 **base))
            continue
        for n in nums:
            ps = [p for p in chosen if str(p.entry.team_dist).strip() == n]
            out.append(TeamInput(f"{t.file}#группа:{n}", f"{t.team.team} ({n})", number=number,
                                 members=[_member(p.entry) for p in ps], **base))
    return out


def base_file(key: str) -> str:
    """Файл заявки, из которой участник зачёта (ключ связки и спортсмена — «файл#…»)."""
    return re.split(r"#", key, maxsplit=1)[0]
