"""Сверка ПСР-2024 (Чемпионат г. Красноярска, дистанция – комбинированная, 3 класс).

Пересчитывает результаты ядром по поэтапным баллам из рабочей книги СЕКРЕТАРЬ_ST и сравнивает:
  * с суммой, которую посчитала сама рабочая книга;
  * с итоговым протоколом (туры, результат, место, процент, норматив);
  * шапку протокола со справочником ВРВС;
  * квалификационный ранг — с расчётом по нормам.

Реальные файлы в репозиторий не входят. Запуск:
    uv run python tools/reconcile_psr2024.py --data "путь/к/папке «СТ секритариат»"
Опции: --out отчёт.md (по умолчанию <data>/Отчёты/Сверка_ПСР-2024.md),
       --write-fixture tests/fixtures/psr2024.json — обезличенный набор для автотестов.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from fractions import Fraction
from pathlib import Path

from st_secretary.disciplines.psr import PsrStage, PsrTeamCard, standings, tour_totals
from st_secretary.importers.sekretar_xls import read_group_protocol, read_result_protocol
from st_secretary.norms import PercentMethod, achieved_norm, percent_of_winner
from st_secretary.qualification import parse_members_with_quals
from st_secretary.rank import RankEntry, qualification_rank
from st_secretary.reference import Level, check_code_matches_name, discipline_by_code, norm_edition
from xlrd import colname

WORKBOOK = "С семинара/С семинара/секретариат/19-21.09.2025 комби/SEKRETAR_CT_30_08_2023.xls"
PROTOCOL = "С семинара/С семинара/секретариат/19-21.09.2025 комби/Result_PSR_2024.xls"
NORMS = "2022-2025"  # соревнования 2024 года
LEVEL = Level.MUNICIPAL  # чемпионат муниципального образования
DISCIPLINE_CODE = "0840161811Я"  # дистанция – комбинированная


def tour_of(title: str) -> str:
    m = re.match(r"(Тур\s*\d+)", title)
    return m.group(1).replace("  ", " ") if m else title.split()[0]


def pct(x: Fraction) -> str:
    return f"{float(x):.2f}".replace(".", ",") + " %"


def num(x: Fraction | None) -> str:
    if x is None:
        return "—"
    return str(x.numerator) if x.denominator == 1 else f"{float(x):.2f}".replace(".", ",")


def reconcile(data: Path) -> tuple[str, dict]:
    wb = read_group_protocol(data / WORKBOOK)
    proto = read_result_protocol(data / PROTOCOL)
    stages = [PsrStage(c.letter, c.title, tour_of(c.title)) for c in wb.stage_columns]
    named = {c.col: c.letter for c in wb.stage_columns}
    cards = [
        PsrTeamCard(t.team, i, {named[c]: v for c, v in t.values.items() if c in named})
        for i, t in enumerate(wb.teams, start=1)
    ]
    workbook_sum = {t.team: sum(t.values.values(), Fraction(0)) for t in wb.teams}
    ours = {p.item.team: p for p in standings(cards)}
    proto_rows = {r.team.lower(): r for r in proto.rows}

    lines: list[str] = []
    issues: list[str] = []
    out = lines.append
    out("# Сверка ПСР-2024: пересчёт ядром СТ-Секретаря\n")
    out(f"Рабочая книга: `{WORKBOOK}`, лист «{wb.sheet}».  ")
    out(f"Итоговый протокол: `{PROTOCOL}`.  ")
    out(f"Нормы: редакция {NORMS}; уровень соревнований: муниципальный.\n")

    # 1. Рабочая книга
    out("## 1. Рабочая книга СЕКРЕТАРЬ_ST\n")
    out(f"Этапов с названиями: {len(stages)}.")
    for w in wb.warnings:
        out(f"\n**Внимание:** {w}")
        issues.append("Рабочая книга: " + w)
    wrong_places = sorted(workbook_sum, key=lambda t: workbook_sum[t])
    out("\n| Команда | Сумма в рабочей книге | Сумма по этапам (без колонок без названия) | Разница |")
    out("|---|---:|---:|---:|")
    for c in cards:
        diff = workbook_sum[c.team] - c.total
        out(f"| {c.team} | {num(workbook_sum[c.team])} | {num(c.total)} | {num(diff)} |")
    if any(workbook_sum[c.team] != c.total for c in cards):
        issues.append("Рабочая книга: итог включает колонку-подытог — баллы тура учтены дважды, места в книге неверные")
    out(f"\nМеста по сумме рабочей книги: {', '.join(wrong_places[:3])}… — "
        f"по пересчёту: {', '.join(p.item.team for p in standings(cards)[:3])}….\n")

    # 2. Сравнение с итоговым протоколом
    out("## 2. Пересчёт против итогового протокола\n")
    out("| Команда | Результат (пересчёт) | Результат (протокол) | Место (пересчёт) | Место (протокол) | Расхождения по турам |")
    out("|---|---:|---:|---:|---:|---|")
    match = 0
    for p in standings(cards):
        c = p.item
        r = proto_rows.get(c.team.lower())
        if r is None:
            out(f"| {c.team} | {num(c.total)} | нет в протоколе | {p.place} | — | — |")
            issues.append(f"Команда «{c.team}» есть в рабочей книге, но не найдена в протоколе")
            continue
        tours = tour_totals(c, stages)
        diffs = []
        for title, val in r.parts.items():
            key = tour_of(title) if title.startswith("Тур") else title.split()[0]
            mine = tours.get(key, Fraction(0))
            if (val or Fraction(0)) != mine:
                diffs.append(f"{title}: {num(mine)} vs {num(val)}")
        ok = c.total == r.result and str(p.place) == r.place and not diffs
        match += ok
        out(f"| {c.team} | {num(c.total)} | {num(r.result)} | {p.place} | {r.place} | {'; '.join(diffs) or '—'} |")
        if not ok:
            issues.append(f"«{c.team}»: пересчёт {num(c.total)} (место {p.place}), в протоколе {num(r.result)} "
                          f"(место {r.place}); {'; '.join(diffs)}")
    out(f"\nСовпало полностью: {match} из {len(cards)}.\n")

    # 3. Шапка протокола
    out("## 3. Шапка протокола\n")
    out(f"В протоколе: «{proto.discipline}», {proto.distance_class} класс, код ВРВС {proto.vrvs_code}.")
    remark = check_code_matches_name(proto.vrvs_code or "", proto.discipline or "")
    if remark:
        out(f"\n**Ошибка:** {remark}. Правильно: «{discipline_by_code(DISCIPLINE_CODE).name}», код {DISCIPLINE_CODE}.")
        issues.append(f"Шапка протокола: {remark}")

    # 4. Ранг и нормативы
    norms = norm_edition(NORMS)
    entries = []
    for r in proto.rows:
        members = tuple(q for _, q in parse_members_with_quals(r.composition))
        place = int(r.place) if r.place.isdigit() else None
        entries.append(RankEntry(place, members))
    rank = qualification_rank(entries, discipline_by_code(DISCIPLINE_CODE).rank_format, norms)
    out("\n## 4. Квалификационный ранг и нормативы\n")
    out(f"Ранг в протоколе: {proto.rank_text}. Ранг по нормам {NORMS} (1–6 места, группы до 4 человек — баллы ÷ 4): "
        f"**{rank.formatted()}**.")
    if proto.rank_text and rank.value is not None and Fraction(proto.rank_text.replace(",", ".")) != rank.value:
        issues.append(f"Ранг: в протоколе {proto.rank_text}, по составам {rank.formatted()}")
    winner = min(r.result for r in proto.rows if r.result is not None)
    out("\n| Место | Команда | % (пересчёт) | % (протокол) | Норматив (пересчёт) | Норматив (протокол) |")
    out("|---:|---|---:|---:|---|---|")
    for r in proto.rows:
        p_ = percent_of_winner(r.result, winner, PercentMethod.POINTS_RELATIVE_TO_WINNER)
        d = achieved_norm(norms, proto.distance_class or 3, rank.value, p_, LEVEL)
        mine = d.qual.label if d.qual else "—"
        theirs = r.norm or "—"
        out(f"| {r.place} | {r.team} | {pct(p_)} | {pct(r.percent * 100) if r.percent is not None else '—'} | {mine} | {theirs} |")
        if mine != theirs:
            issues.append(f"Норматив «{r.team}»: пересчёт {mine}, в протоколе {theirs}")
    row = achieved_norm(norms, proto.distance_class or 3, rank.value, Fraction(100), LEVEL).row
    out(f"\nСтрока норм: 3 класс, ранг {rank.formatted()} → «{row.label}»: "
        + ", ".join(f"{q.label} ≤ {v} %" for q, v in row.thresholds.items())
        + ". I разряд на муниципальных соревнованиях не присваивается.")
    out("\nПроцент считается как в протоколе: (1 + (результат − результат победителя) / |результат победителя|) × 100. "
        "Методика для балльных дисциплин ждёт подтверждения коллегии судей.")

    out("\n## Итог\n")
    out("\n".join(f"- {i}" for i in issues) if issues else "Расхождений нет.")

    fixture = {
        "source": "ПСР-2024, обезличено tools/reconcile_psr2024.py",
        "discipline_in_protocol": proto.discipline,
        "vrvs_code_in_protocol": proto.vrvs_code,
        "distance_class": proto.distance_class,
        "rank_in_protocol": proto.rank_text,
        "stages": [{"key": s.key, "title": s.name, "tour": s.tour} for s in stages],
        "unnamed_columns": [c.letter for c in wb.unnamed_columns],
        "teams": [],
    }
    labels = {t.team: f"Команда {i:02d}" for i, t in enumerate(wb.teams, start=1)}
    for t in wb.teams:
        r = proto_rows[t.team.lower()]
        fixture["teams"].append({
            "label": labels[t.team],
            "start_order": wb.teams.index(t) + 1,
            "points": {named.get(c, f"unnamed:{colname(c)}"): str(v) for c, v in t.values.items()},
            "workbook_total": str(workbook_sum[t.team]),
            "protocol": {
                "parts": {k: (str(v) if v is not None else None) for k, v in r.parts.items()},
                "result": str(r.result), "place": r.place,
                "percent": str(r.percent) if r.percent is not None else None, "norm": r.norm,
                "members": [q.name for _, q in parse_members_with_quals(r.composition)],
            },
        })
    return "\n".join(lines) + "\n", fixture


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", default=os.environ.get("ST_REF_DATA"), help="папка «СТ секритариат»")
    ap.add_argument("--out", help="куда записать отчёт (Markdown)")
    ap.add_argument("--write-fixture", help="записать обезличенный набор для тестов (JSON)")
    a = ap.parse_args()
    if not a.data:
        ap.error("укажите --data или переменную окружения ST_REF_DATA")
    data = Path(a.data)
    report, fixture = reconcile(data)
    out = Path(a.out) if a.out else data / "Отчёты" / "Сверка_ПСР-2024.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report, encoding="utf-8")
    print(report)
    print(f"Отчёт записан: {out}")
    if a.write_fixture:
        Path(a.write_fixture).write_text(json.dumps(fixture, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"Обезличенный набор: {a.write_fixture}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
