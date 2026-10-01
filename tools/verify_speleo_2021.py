"""Сверка расчётов спелео с протоколами ЧК и ПК Красноярского края 2021 (СЕКРЕТАРЬ_ST) — Правки, п. 34.

Эталон — «Эталонные_данные/Спелео/2021_ЧК_ПК_края/протоколы.json» в папке ST_REF_DATA (перенесён со сканов, без
ФИО). Каждый протокол прогоняется через расчёт программы (time_run.compute): время на дистанции, штрафные баллы ×
эквивалент, составляющие («Топосъёмка»), места, % от победителя, ранг, нормативы — и сравнивается с протоколом.

    uv run python tools/verify_speleo_2021.py [путь к отчёту .md]

Без пути отчёт печатается. Реальные данные в репозиторий не входят.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import replace
from datetime import date
from pathlib import Path

from st_secretary import time_run as tr
from st_secretary.competition import Competition, Official, Zachet
from st_secretary.norms import PercentMethod
from st_secretary.psr_run import Member, TeamInput
from st_secretary.qualification import parse_qual
from st_secretary.reference import Level

REF = "Эталонные_данные/Спелео/2021_ЧК_ПК_края/протоколы.json"
EDITION = "2022-2025"  # в 2021 действовала прежняя редакция норм — в программе её нет (см. отчёт)
START = 10 * 3600


def hms(sec) -> str:
    if sec is None:
        return ""
    whole = int(sec)
    return f"{whole // 3600}:{whole % 3600 // 60:02d}:{whole % 60:02d}"


def pct(x) -> str:
    return f"{float(x):.2f}".replace(".", ",") if x is not None else ""


def run_protocol(p: dict, spp_override: int | None = None):
    """Протокол из эталона → расчёт программы (зачёт, ряды в порядке протокола)."""
    z = Zachet("М/Ж", p["class"], p["code"])
    comp = Competition(title="Сверка спелео-2021", kind="Чемпионат", level=Level.REGIONAL, date_from=date(2021, 4, 17),
                       date_to=date(2021, 4, 18), place="", host_territory="Красноярский край", organizers=[],
                       norms_edition=EDITION, percent_method=PercentMethod.TIME, preapp_deadline=None,
                       officials=[Official("Главный судья", "—", "", "")], zachety=[z])
    teams, data = [], {}
    adds = [{"id": f"a{i}", "name": n, "kind": "time"} for i, n in enumerate(p.get("adds", []), start=1)]
    for n, row in enumerate(p["rows"], start=1):
        file = f"r{n}.xlsx"
        members = [Member(f"Участник {n}.{i}", parse_qual(m["qual"]) if m.get("qual") else None, m.get("qual") or "",
                          birth=str(m.get("year") or "")) for i, m in enumerate(row["members"], start=1)]
        teams.append(TeamInput(file, row["delegation"], "", str(n), members, admitted=True, club=row["delegation"]))
        d: dict = {"status": "removed" if row.get("status") else "finished"}
        if row.get("time"):
            h, m, s = (int(x) for x in row["time"].split(":"))
            fin = START + h * 3600 + m * 60 + s
            d |= {"start": hms(START), "finish": hms(fin)}
        if row.get("points") and row["points"] != "0":
            d["points"] = {"s1": row["points"]}
        for a in adds:
            if a["name"] in row.get("adds", {}):
                d[f"add-{a['id']}"] = row["adds"][a["name"]]
        data[file] = d
    spp = spp_override or p.get("spp") or 30
    zdata = {"stages": [{"id": "s1", "name": "Этапы"}], "spp": str(spp) if spp in (15, 30) else "30",
             "adds": adds, "teams": data}
    run = tr.compute(replace(comp, zachety=[z]), z, zdata, teams)
    by_file = {r.inp.file: r for r in run.rows}
    return run, [by_file[f"r{n}.xlsx"] for n in range(1, len(p["rows"]) + 1)]


def compare(p: dict) -> dict:
    run, rows = run_protocol(p)
    out = {"id": p["id"], "title": f"{p['page']}: {p['code']}, {p['class']} класс, {p['group']}", "rows": [],
           "rank": (p.get("rank"), run.rank.formatted() if run.rank and run.rank.value is not None else None)}
    for ref, r in zip(p["rows"], rows, strict=True):
        ours = {"result": hms(r.total) if r.place else "", "place": r.place, "percent": pct(r.percent),
                "norm": r.norm or "-"}
        theirs = {"result": ref["result"] if ref.get("place") else "", "place": ref.get("place"),
                  "percent": ref.get("percent") or "", "norm": (ref.get("norm") or "-").rstrip("*")}
        diff = [k for k in ("result", "place", "percent") if ours[k] != theirs[k]]
        if "norm" in ref and ours["norm"] != theirs["norm"]:
            diff.append("norm")
        out["rows"].append({"n": ref.get("place") or "—", "ours": ours, "theirs": theirs, "diff": diff})
    return out


def report(ref: dict) -> str:
    lines = ["# Сверка спелео-2021: расчёт СТ-Секретаря против протоколов ЧК и ПК края (СЕКРЕТАРЬ_ST)", "",
             f"Эталон: `{REF}` (со сканов, без ФИО). Нормы и ранг — редакция {EDITION} (в программе нет редакции, "
             "действовавшей в апреле 2021); уровень — субъект РФ. Время — целые секунды, как на скане.", ""]
    for p in ref["protocols"]:
        c = compare(p)
        bad = [x for x in c["rows"] if x["diff"]]
        rank_ref, rank_ours = c["rank"]
        lines += [f"## {c['id']} — {c['title']}", "",
                  f"Ранг: протокол — {rank_ref or 'не подсчитывался'}, программа — {rank_ours or 'не определён'}. "
                  f"Строк: {len(c['rows'])}, совпали полностью: {len(c['rows']) - len(bad)}.", ""]
        if bad:
            lines += ["| Место | Что | Протокол | Программа |", "|---|---|---|---|"]
            for x in bad:
                for k in x["diff"]:
                    lines.append(f"| {x['n']} | {k} | {x['theirs'][k]} | {x['ours'][k]} |")
            lines.append("")
    return "\n".join(lines) + "\n"


def main() -> int:
    root = os.environ.get("ST_REF_DATA")
    if not root:
        print("Задайте ST_REF_DATA — папку с эталонными данными.", file=sys.stderr)
        return 2
    ref = json.loads((Path(root) / REF).read_text(encoding="utf-8"))
    text = report(ref)
    if len(sys.argv) > 1:
        Path(sys.argv[1]).write_text(text, encoding="utf-8")
        print(f"Отчёт: {sys.argv[1]}")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
