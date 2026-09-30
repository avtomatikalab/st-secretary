"""Протокол результатов ПСР (предварительный и официальный) — Excel, два листа.

«Протокол» — как итоговый протокол СЕКРЕТАРЬ_ST: шапка (проводящие организации, наименование, даты и место,
дисциплина, класс, код ВРВС, группа, квалификационный ранг), места, команды, составы с разрядами, баллы по
турам, результат, процент от победителя, выполненный разряд; подписи главного судьи и главного секретаря.
«По этапам» — баллы каждой команды по каждому этапу: для стенда информации и для протестов.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from st_secretary.competition import Competition
from st_secretary.psr_run import ZachetRun, points_text, result_text
from st_secretary.time_run import clock_text

THIN = Side(style="thin", color="7F7F7F")
BOX = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
CENTER = Alignment(horizontal="center", vertical="center", wrap_text=True)
WRAP = Alignment(vertical="center", wrap_text=True)
HEAD = PatternFill("solid", fgColor="DCE6F1")
GROUP_WORDS = {"М/Ж": "мужчины/женщины, смешанные группы", "МУЖЧИНЫ": "мужчины", "ЖЕНЩИНЫ": "женщины"}

PRELIMINARY, OFFICIAL = "preliminary", "official"


def _pct(x) -> str:
    return f"{float(x):.2f}".replace(".", ",") if x is not None else ""


def write_protocol(comp: Competition, run: ZachetRun, kind: str, at: datetime, path: str | Path,
                   protests_until: datetime | None = None) -> Path:
    z = run.zachet
    wb = Workbook()
    ws = wb.active
    ws.title = "Протокол"
    timed = run.kind == "time"  # спелео, пешеходные: время на дистанции, штраф, снятия
    tours = [] if timed else run.tours
    middle = ["Время на дистанции", "Штраф, баллы", "Снятий"] if timed else tours
    show_class = any(r.actual_class for r in run.rows)
    show_marks = not timed and any(r.marks for r in run.rows)  # ПСР: сняты с этапов (МШ) — отметка в протоколе
    person = run.unit == "person"  # личная дисциплина: место у спортсмена
    who = ["Участник", "Команда", "Территория", "Разряд"] if person else ["Команда", "Территория", "Состав (разряд)"]
    norms = [] if comp.unofficial else ["% от победителя", "Выполнен разряд"]  # неофициальные — без разрядов
    head = (["Место", "№"] + who + middle
            + (["Снятия с этапов"] if show_marks else [])
            + ["Результат"] + norms + (["Факт. класс"] if show_class else []))
    width = len(head)

    def line(r, text, bold=False, size=11):
        c = ws.cell(r, 1, text)
        c.font, c.alignment = Font(bold=bold, size=size), Alignment(horizontal="center", wrap_text=True)
        ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=width)

    r = 1
    for org in comp.organizers:
        line(r, org.upper(), size=10)
        r += 1
    line(r, comp.title, True, 13)
    line(r + 1, f"{comp.dates_text}, {comp.place}")
    title = "ПРЕДВАРИТЕЛЬНЫЙ ПРОТОКОЛ РЕЗУЛЬТАТОВ" if kind == PRELIMINARY else "ПРОТОКОЛ РЕЗУЛЬТАТОВ"
    line(r + 2, title, True, 13)
    line(r + 3, z.header_text)
    rank = run.rank.formatted() if run.rank else "не определялся"
    zname = f"Зачёт «{z.name}». " if z.name else ""
    line(r + 4, f"{zname}Группа: {GROUP_WORDS.get(z.group.upper(), z.group)}"
         + ("" if comp.unofficial else f". Квалификационный ранг: {rank}"))
    r += 6
    for c, h in enumerate(head, start=1):
        cell = ws.cell(r, c, h)
        cell.font, cell.fill, cell.border, cell.alignment = Font(bold=True, size=10), HEAD, BOX, CENTER
    r += 1
    for t in run.rows:
        members = ", ".join(f"{m.fio} ({m.qual_label or 'б/р'})" for m in t.inp.members)
        who_values = ([t.inp.team, t.inp.club, t.inp.territory, ", ".join(m.qual_label or "б/р" for m in t.inp.members)]
                      if person else [t.inp.team, t.inp.territory, members])
        result = result_text(run, t) or ("время не внесено" if timed else "баллы не внесены")
        mid = ([clock_text(t.distance_time), points_text(sum(t.points.values())) if t.points else "", t.removals or ""]
               if timed else [points_text(t.tours.get(x)) for x in tours])
        values = ([t.place or "—", t.inp.number or ""] + who_values + mid
                  + ([mark_text(run, t)] if show_marks else [])
                  + [result] + ([_pct(t.percent), t.norm or ""] if norms else [])
                  + ([t.actual_class or ""] if show_class else []))
        res_col = 3 + len(who) + len(middle) + (1 if show_marks else 0)
        for c, v in enumerate(values, start=1):
            cell = ws.cell(r, c, v)
            cell.border, cell.font = BOX, Font(size=10, bold=(c in (1, res_col)))
            cell.alignment = WRAP if 3 <= c < 3 + len(who) or (show_marks and c == res_col - 1) else CENTER
        r += 1
    r += 1
    notes = []
    if kind == PRELIMINARY:
        notes.append(f"Опубликован {at:%d.%m.%Y в %H:%M}. Протесты по результатам принимаются в течение 1 часа — "
                     f"до {protests_until:%H:%M} (Правила, раздел 3, п. 8.17)." if protests_until else
                     f"Опубликован {at:%d.%m.%Y в %H:%M}.")
    else:
        notes.append(f"Результаты утверждены {at:%d.%m.%Y в %H:%M}.")
    if comp.unofficial:
        notes.append("Неофициальные соревнования: квалификационный ранг и разряды не определяются.")
    elif run.rank and run.rank.value is None and run.rank.reason:
        notes.append(f"Квалификационный ранг не определялся: {run.rank.reason}.")
    for n in notes:
        ws.cell(r, 1, n).font = Font(size=10, italic=True)
        r += 1
    r += 1
    for role in ("Главный судья", "Главный секретарь"):
        o = comp.official(role)
        ws.cell(r, 1, f"{role} ________________ / {o.signature if o else ' ' * 30} /").font = Font(size=11)
        r += 2
    for c, w in enumerate([7, 6] + ([28, 20, 16, 8] if person else [22, 16, 46]) + [10 if timed else 8] * len(middle)
                          + ([22] if show_marks else []) + [11] + ([10, 10] if norms else []) + ([8] if show_class else []),
                          start=1):
        ws.column_dimensions[get_column_letter(c)].width = w
    ws.page_setup.orientation = "landscape"
    ws.page_setup.fitToWidth, ws.page_setup.fitToHeight = 1, 0
    ws.sheet_properties.pageSetUpPr.fitToPage = True

    st = wb.create_sheet("По этапам")
    st.cell(1, 1, f"{comp.title} — баллы по этапам, зачёт {z.key}").font = Font(bold=True, size=12)
    what = "Предварительные результаты" if kind == PRELIMINARY else "Результаты" if comp.unofficial else "Официальные результаты"
    st.cell(2, 1, f"{what} на {at:%d.%m.%Y %H:%M}")
    heads = ["Место", "№", "Участник" if person else "Команда"] + [s.title for s in run.stages] + ["Итого"]
    for c, h in enumerate(heads, start=1):
        cell = st.cell(4, c, h)
        cell.font, cell.fill, cell.border = Font(bold=True, size=9), HEAD, BOX
        cell.alignment = Alignment(horizontal="center", vertical="bottom", wrap_text=True, text_rotation=90 if c > 3 else 0)
    for i, t in enumerate(run.rows, start=5):
        cells = [t.raw.get(s.id, "") if timed else  # «с» — снятие
                 f"{points_text(t.points.get(s.id))} {t.marks[s.id]}" if s.id in t.marks else points_text(t.points.get(s.id))
                 for s in run.stages]
        if run.show_codes:  # номера пунктов таблицы штрафов у баллов — по желанию (настройка зачёта)
            cells = [f"{c} ({t.codes[s.id]})" if s.id in t.codes and c != "" else c for c, s in zip(cells, run.stages,
                                                                                                    strict=True)]
        values = [t.place or "—", t.inp.number or "", t.inp.team] + cells + [result_text(run, t)]
        for c, v in enumerate(values, start=1):
            cell = st.cell(i, c, v)
            cell.border, cell.font = BOX, Font(size=9, bold=c in (1, len(values)))
            cell.alignment = CENTER if c != 3 else WRAP
    st.column_dimensions["C"].width = 22
    st.row_dimensions[4].height = 120
    for c in range(4, len(heads) + 1):
        marked = 4 <= c < 4 + len(run.stages) and any(run.stages[c - 4].id in t.marks for t in run.rows)
        coded = run.show_codes and 4 <= c < 4 + len(run.stages) and any(run.stages[c - 4].id in t.codes for t in run.rows)
        st.column_dimensions[get_column_letter(c)].width = 16 if coded else 13 if marked else 5
    if any(t.marks for t in run.rows):
        st.cell(5 + len(run.rows) + 1, 1, "«снята» — команда снята с этапа, «сверх КВ» — превышено КВ этапа: "
                                          "в клетке МШ этапа.").font = Font(size=9, italic=True)
    st.page_setup.orientation = "landscape"
    st.page_setup.fitToWidth, st.page_setup.fitToHeight = 1, 0
    st.sheet_properties.pageSetUpPr.fitToPage = True
    path = Path(path)
    wb.save(path)
    return path


def mark_text(run: ZachetRun, t) -> str:
    """ПСР: с каких этапов команда снята (в клетке МШ) — «Узлы; Бивак (сверх КВ)»."""
    return "; ".join(s.name + ("" if t.marks[s.id] == "снята" else f" ({t.marks[s.id]})")
                     for s in run.stages if s.id in t.marks)


def awards_rows(run: ZachetRun, source: str) -> dict:
    """Официальные результаты → данные страницы «Награждение» (как из протокола СЕКРЕТАРЬ_ST)."""
    rows = []
    for t in run.rows:  # спортсмен и связка: «команда» — их команда, награждаются участники
        rows.append({"team": t.inp.club or t.inp.team, "territory": t.inp.territory, "number": t.inp.number,
                     "place": t.place,
                     "result": result_text(run, t),
                     "norm": t.norm, "members": [{"fio": m.fio, "qual": m.qual_label} for m in t.inp.members]})
    rank = run.rank.formatted() if run.rank and run.rank.value is not None else ""
    return {"source": source, "group_text": "", "rank": rank, "rows": rows}
