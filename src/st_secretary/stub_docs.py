"""Заглушки документов участников — проверить комиссию по допуску без настоящих сканов (учебный режим, Правки, п. 53).

На каждого участника команды — паспорт (младше 14 лет — свидетельство о рождении), полис ОМС, страховка и
классификационная книжка (если есть разряд); на команду — заявка с допуском врача. Картинки SVG (рисует браузер,
своя библиотека не нужна) с крупной надписью «ОБРАЗЕЦ — НЕ ДОКУМЕНТ»; номеров документов и других реквизитов нет —
только ФИО, дата рождения и команда из заявки. Кладутся туда же, куда программа кладёт сканы команды (на этом
компьютере, не в облако). Уже созданные файлы не трогаются.
"""

from __future__ import annotations

from pathlib import Path
from xml.sax.saxutils import escape

from st_secretary.admission import full_years
from st_secretary.equipment import short_name
from st_secretary.qualification import Qual

W, H = 1100, 760
COLORS = {"Паспорт": "#f3e7ea", "Свидетельство о рождении": "#eaf1e4", "Полис ОМС": "#e4edf7",
          "Страховка": "#f5f0df", "Классификационная книжка": "#ece6f5", "Заявка с допуском врача": "#ffffff"}
FOOT = "Заглушка для проверки программы «СТ-Секретарь». Не является документом."
FONT = "Arial, 'DejaVu Sans', sans-serif"


def _text(x: int, y: int, s: str, size: int, fill: str = "#1a2027", bold: bool = False, extra: str = "") -> str:
    weight = ' font-weight="bold"' if bold else ""
    return f'<text x="{x}" y="{y}" font-size="{size}" fill="{fill}"{weight}{extra}>{escape(s)}</text>'


def _svg(title: str, body: list[str]) -> str:
    """Лист заглушки: фон, рамка, водяной знак по диагонали и подпись внизу."""
    mark = _text(W // 2, H // 2, "ОБРАЗЕЦ — НЕ ДОКУМЕНТ", 66, "#b3261e", True,
                 f' text-anchor="middle" opacity="0.28" transform="rotate(-20 {W // 2} {H // 2})"')
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" '
            f'font-family="{FONT}">\n<rect width="{W}" height="{H}" fill="{COLORS.get(title, "#f4f4f4")}"/>\n'
            f'<rect x="20" y="20" width="{W - 40}" height="{H - 40}" fill="none" stroke="#7a8591" stroke-width="4"/>\n'
            + "\n".join(body) + f"\n{_text(60, H - 60, FOOT, 24, '#b3261e')}\n{mark}\n</svg>\n")


def sheet(title: str, lines: list[tuple[str, str]], note: str = "") -> str:
    body = [_text(60, 100, title.upper(), 46, bold=True)]
    y = 190
    for label, value in lines:
        body += [_text(60, y, label, 26, "#5b6671"), _text(360, y, value, 34, bold=True)]
        y += 70
    if note:
        body.append(_text(60, H - 110, note, 24, "#5b6671"))
    return _svg(title, body)


def application(team, comp) -> str:
    """Заявка команды: «допущен» и подпись врача напротив каждого (Правила, раздел 3, п. 8.1)."""
    body = [_text(60, 80, "ЗАЯВКА", 42, bold=True), _text(60, 125, f"на участие: {comp.title}", 22),
            _text(60, 165, f"Команда: {team.team}, {team.territory}", 26, bold=True),
            _text(60, 220, "№   ФИО", 22, "#5b6671"), _text(720, 220, "Разряд", 22, "#5b6671"),
            _text(860, 220, "Допуск врача", 22, "#5b6671")]
    y = 220
    for n, e in enumerate(team.entries, start=1):
        y += 46
        body += [_text(60, y, f"{n}.   {e.name.full}", 26),
                 _text(720, y, e.qual.label if e.qual is not None else "?", 26),
                 _text(860, y, "допущен  ✓", 26, "#1d4f91", True)]
    body += [_text(60, H - 150, f"Допущено: {len(team.entries)} чел.   Врач ____________ (подпись)   М.П.", 24),
             _text(60, H - 105, f"Представитель: {team.representative}", 24)]
    return _svg("Заявка с допуском врача", body)


def make_team(folder: Path, team, comp) -> int:
    """Заглушки одной команды в её папку документов; возвращает, сколько файлов создано (готовые не трогаются)."""
    folder.mkdir(parents=True, exist_ok=True)
    made = 0

    def save(svg: str, name: str) -> None:
        nonlocal made
        p = folder / name
        if not p.exists():
            p.write_text(svg, encoding="utf-8")
            made += 1

    save(application(team, comp), "Заявка с допуском врача.svg")
    for e in team.entries:
        who = short_name(e)
        birth = e.birth.strftime("%d.%m.%Y") if e.birth else str(e.birth_year or "—")
        young = e.birth is not None and full_years(e.birth, comp.date_from) < 14
        base = [("Фамилия Имя Отчество", e.name.full), ("Дата рождения", birth), ("Команда", team.team)]
        kind = "Свидетельство о рождении" if young else "Паспорт"
        save(sheet(kind, base, "Серия и номер не указаны — это заглушка."), f"{kind} — {who}.svg")
        save(sheet("Полис ОМС", base), f"Полис ОМС — {who}.svg")
        save(sheet("Страховка", base + [("Период", comp.dates_text)], "Страхование от несчастных случаев"),
             f"Страховка — {who}.svg")
        if e.qual is not None and e.qual != Qual.BR:
            save(sheet("Классификационная книжка", base + [("Разряд", e.qual.label)]),
                 f"Классификационная книжка — {who}.svg")
    return made
