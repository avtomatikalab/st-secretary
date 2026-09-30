"""Таблицы штрафов — у секретаря и у судьи этапа на телефоне (Правки, п. 15).

Встроенные таблицы (reference/data/penalties):
- пешеходные — Правила 2021, дистанции пешеходные, таблица 11 «Система оценки нарушений»: № п/п, нарушение,
  бесштрафовая и штрафная система, разъяснение;
- спелео — Правила 2021, часть 8 раздела 3, приложение 2: 10 групп со штрафом (снятие, 30, 15, 1, 0,3, 2, 1, 0,1)
  и пояснения к таблице — подпункты вида 7.2.9; подпункт получает штраф своей группы.
В ПСР (и горных) общей таблицы в Правилах нет — таблица штрафов этапов в Условиях соревнования: секретарь
загружает свою из Excel (номер, нарушение, баллы, разъяснение) или берёт за основу таблицу пешеходных.

Какая таблица у зачёта — zdata["penalty_table"]: «auto» (по дисциплине, по умолчанию), «pedestrian», «speleo»,
«custom» (своя из Excel — zdata["penalty_custom"]) или «none».
"""

from __future__ import annotations

import io
import re
import tomllib
from dataclasses import asdict, dataclass
from fractions import Fraction
from importlib.resources import files
from pathlib import Path

BUILTIN = {"pedestrian": "Пешеходные (Правила, таблица 11)", "speleo": "Спелео (Правила, часть 8, приложение 2)"}
CHOICES = {"auto": "по дисциплине", **BUILTIN, "custom": "своя таблица из Excel", "none": "без таблицы"}
MAX_ROWS = 1000


@dataclass
class Penalty:
    code: str  # номер пункта: «6.2.9», «12.1»
    title: str  # нарушение
    points: str  # штраф как в таблице: «1 балл», «0,3», «Снятие с этапа (блока этапов)», «10 минут»
    value: float | None = None  # штраф числом (баллы), если он в баллах
    nopenalty: str = ""  # пешеходные: бесштрафовая система
    note: str = ""  # разъяснение
    section: str = ""  # подзаголовок (спелео: «Узлы», «ПТК», …)
    group: bool = False  # строка-группа (спелео — группа со штрафом; пешеходные — заголовок «Потеря … :»)


@dataclass
class PenaltyTable:
    key: str
    title: str
    source: str
    rows: list[Penalty]
    systems: bool = False  # две системы оценки (пешеходные): бесштрафовая и штрафная

    def payload(self) -> dict:
        """Для телефона судьи: таблица целиком (работает без связи)."""
        return {"key": self.key, "title": self.title, "source": self.source, "systems": self.systems,
                "rows": [{k: v for k, v in asdict(r).items() if v not in ("", None, False)} for r in self.rows]}


def builtin(key: str) -> PenaltyTable:
    data = tomllib.loads((files("st_secretary.reference") / "data" / "penalties" / f"{key}.toml")
                         .read_text(encoding="utf-8"))
    return PenaltyTable(key, data["title"], data["source"], [Penalty(**r) for r in data["row"]],
                        bool(data.get("systems")))


def default_key(z) -> str:
    """Таблица по дисциплине: пешеходные и спелео — из Правил; остальным (ПСР, горные, СХ) — нет."""
    from st_secretary import time_run as tr

    if not tr.is_time_discipline(z):
        return ""
    p = tr.profile(z)
    return p if p in BUILTIN else ""


def choice(zdata: dict) -> str:
    c = str(zdata.get("penalty_table", "auto"))
    return c if c in CHOICES else "auto"


def table_for(z, zdata: dict) -> PenaltyTable | None:
    """Таблица штрафов зачёта (None — не выбрана)."""
    c = choice(zdata)
    key = default_key(z) if c == "auto" else c
    if key in BUILTIN:
        return builtin(key)
    custom = zdata.get("penalty_custom") or {}
    if key == "custom" and custom.get("rows"):
        return PenaltyTable("custom", custom.get("title") or "Своя таблица", custom.get("source", ""),
                            [Penalty(**r) for r in custom["rows"]])
    return None


def points_value(text) -> float | None:
    """«1 балл», «0,3», «10 баллов (см. п. 6.3.2)» → число баллов; «Снятие…», «10 минут», пусто — None."""
    t = str(text or "").strip().lower().replace(",", ".")
    m = re.match(r"^(-?\d+(?:\.\d+)?)\s*(балл\w*)?(\s|\(|$)", t)
    if not m or "мин" in t.split("(")[0]:
        return None
    return float(Fraction(m.group(1)))


# ------------------------------------------------------------------ своя таблица: Excel


HEAD = ("№", "Нарушение", "Штраф (баллы)", "Разъяснение")


def read_excel(data: bytes) -> list[Penalty]:
    """Своя таблица штрафов из Excel: колонки «№», «Нарушение», «Штраф/баллы», «Разъяснение» (по заголовку;
    без заголовка — первые четыре по порядку). Пустые строки пропускаются. ValueError — не таблица."""
    from openpyxl import load_workbook

    try:
        ws = load_workbook(io.BytesIO(data), read_only=True, data_only=True).worksheets[0]
    except Exception as e:  # не Excel
        raise ValueError("это не файл Excel (.xlsx)") from e
    rows = [["" if c is None else " ".join(str(c).split()) for c in r] for r in ws.iter_rows(values_only=True)]
    rows = [r for r in rows if any(r)]
    cols = [0, 1, 2, 3]

    def find(low: list[str], *words: str, default: int) -> int:  # слова — по старшинству; «бесштрафовая» — не баллы
        return next((j for w in words for j, c in enumerate(low) if w in c and not c.startswith("бесштраф")), default)

    for i, r in enumerate(rows[:10]):  # строка заголовка — где есть «нарушен»
        low = [c.lower() for c in r]
        if any("нарушен" in c for c in low):
            cols = [find(low, "№", "номер", "пункт", default=0), find(low, "нарушен", "название", default=1),
                    find(low, "балл", "штрафн", "штраф", default=2), find(low, "разъясн", "примеч", "поясн", default=3)]
            rows = rows[i + 1:]
            break
    out = []
    for r in rows[:MAX_ROWS]:
        code, title, pts, note = ((r[j] if j < len(r) else "") for j in cols)
        if not title:
            continue
        code = re.sub(r"\.0$", "", code)  # 1.0 из Excel — «1»
        out.append(Penalty(code[:20], title[:500], pts[:80], points_value(pts), note=note[:2000], group=not pts))
    if not out:
        raise ValueError("в файле не нашлось строк с нарушениями (колонки: №, Нарушение, Штраф, Разъяснение)")
    return out


def write_excel(table: PenaltyTable, path: str | Path) -> Path:
    """Таблица в Excel — посмотреть, поправить под Условия и загрузить как свою."""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font

    wb = Workbook()
    ws = wb.active
    ws.title = "Таблица штрафов"
    head = (["№", "Нарушение", "Бесштрафовая система", "Штрафная система", "Разъяснение"] if table.systems
            else list(HEAD))
    ws.append(head)
    for c in ws[1]:
        c.font = Font(bold=True)
    for r in table.rows:
        title = f"{r.section}: {r.title}" if r.section else r.title
        ws.append([r.code, title, r.nopenalty, r.points, r.note] if table.systems else [r.code, title, r.points, r.note])
        if r.group:
            for c in ws[ws.max_row]:
                c.font = Font(bold=True)
    for letter, w in zip("ABCDE", [8, 60, 22, 22, 70] if table.systems else [8, 60, 16, 70], strict=False):
        ws.column_dimensions[letter].width = w
    for row in ws.iter_rows(min_row=2):
        for c in row:
            c.alignment = Alignment(vertical="top", wrap_text=True)
    if isinstance(path, (str, Path)):
        path = Path(path)
    wb.save(path)  # путь или файл в памяти (скачать из браузера)
    return path
