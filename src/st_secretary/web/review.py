"""Отметки секретаря по заявкам: статус заявки и «проверено» у замечаний.

Статус заявки:
  «Исправить» — в заявке есть ошибки (ставится сам) или секретарь отправил её на исправление;
  «Проверить» — ошибок нет, но заявку ещё не смотрели (ставится сам) или секретарь так отметил;
  «Проверено» — секретарь посмотрел заявку. Пока в ней есть ошибки, «Проверено» не ставится.
    Ставится и само — когда секретарь сохранил заявку в форме и в ней не осталось ни ошибок, ни
    неотмеченных «проверить» (заявку только что смотрели и правили, программе возразить нечего).

Выставленный вручную статус относится к содержимому файла: если команда прислала новый файл или
заявку исправили, статус снова определяется сам — изменённую заявку нужно посмотреть заново.

«Проверено» у замечания «Проверить» относится к самому замечанию (кто, что, какие значения): если
после исправления заявки то же замечание осталось прежним, отметка сохраняется.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from st_secretary.issues import CHECKED, ERROR, WARNING, Issue
from st_secretary.preapp import PreappResult

CHECK, FIX, DONE = "check", "fix", "done"
STATUS_LABEL = {FIX: "Исправить", CHECK: "Проверить", DONE: "Проверено"}
STATUSES = (CHECK, FIX, DONE)


def issue_key(i: Issue) -> str:
    """Чем замечание отличается от других: поле, человек, текст (в тексте — сами значения)."""
    return "|".join((i.field, i.person, i.text))


HAND, SAVE = "hand", "save"  # кто поставил статус: секретарь кнопкой или программа при сохранении заявки


@dataclass
class Review:
    status: str  # FIX / CHECK / DONE
    manual: bool = False  # выставлен (кнопкой или при сохранении) и заявка с тех пор не менялась
    at: str = ""  # когда выставлен, «дд.мм.гггг чч:мм»
    reset: str = ""  # почему выставленный статус больше не действует
    checked: dict[str, str] = field(default_factory=dict)  # ключ замечания → когда отмечено
    by: str = HAND  # HAND — кнопкой, SAVE — сам после сохранения заявки без замечаний

    @property
    def label(self) -> str:
        return STATUS_LABEL[self.status]


def is_clean(issues: list[Issue]) -> bool:
    """Нет ни ошибок, ни неотмеченных «проверить» — заявке можно ставить «Проверено»."""
    return not any(i.severity in (ERROR, WARNING) for i in issues)


def apply_marks(result: PreappResult, marks: dict, hashes: dict[str, str]) -> tuple[PreappResult, dict[str, Review]]:
    """Замечания с учётом отметок «проверено» и статусы заявок по файлам."""
    issues = []
    for i in result.issues:
        if i.severity == WARNING and issue_key(i) in marks.get(i.source, {}).get("checked", {}):
            i = replace(i, severity=CHECKED)
        issues.append(i)
    has_errors = {i.source for i in issues if i.severity == ERROR}
    has_open = {i.source for i in issues if i.severity == WARNING}
    reviews = {}
    for src, digest in hashes.items():
        m = marks.get(src, {})
        manual, by, reset = m.get("status"), m.get("by", HAND), ""
        if manual and m.get("hash") != digest:
            reset = (f"Статус «{STATUS_LABEL.get(manual, manual)}» от {m.get('at', '')} снят: заявку с тех пор "
                     "изменили — посмотрите её снова.")
            manual = None
        if manual == DONE and src in has_errors:
            reset = f"Статус «Проверено» от {m.get('at', '')} снят: в заявке появились ошибки."
            manual = None
        if manual == DONE and by == SAVE and src in has_open:  # ставился потому, что замечаний не было
            reset = f"Статус «Проверено» от {m.get('at', '')} снят: появились замечания «Проверить»."
            manual = None
        status = manual or (FIX if src in has_errors else CHECK)
        reviews[src] = Review(status, bool(manual), m.get("at", "") if manual else "", reset,
                              dict(m.get("checked", {})), by if manual else HAND)
    return PreappResult(result.teams, issues), reviews
