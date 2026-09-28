"""Отметки секретаря по заявкам: статус заявки и «проверено» у замечаний.

Статус заявки:
  «Исправить» — в заявке есть ошибки (ставится сам) или секретарь отправил её на исправление;
  «Проверить» — ошибок нет, но заявку ещё не смотрели (ставится сам) или секретарь так отметил;
  «Проверено» — секретарь посмотрел заявку. Пока в ней есть ошибки, «Проверено» не ставится.

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


@dataclass
class Review:
    status: str  # FIX / CHECK / DONE
    manual: bool = False  # выставлен человеком (и заявка с тех пор не менялась)
    at: str = ""  # когда выставлен вручную, «дд.мм.гггг чч:мм»
    reset: str = ""  # почему ручной статус больше не действует
    checked: dict[str, str] = field(default_factory=dict)  # ключ замечания → когда отмечено

    @property
    def label(self) -> str:
        return STATUS_LABEL[self.status]


def apply_marks(result: PreappResult, marks: dict, hashes: dict[str, str]) -> tuple[PreappResult, dict[str, Review]]:
    """Замечания с учётом отметок «проверено» и статусы заявок по файлам."""
    issues = []
    for i in result.issues:
        if i.severity == WARNING and issue_key(i) in marks.get(i.source, {}).get("checked", {}):
            i = replace(i, severity=CHECKED)
        issues.append(i)
    has_errors = {i.source for i in issues if i.severity == ERROR}
    reviews = {}
    for src, digest in hashes.items():
        m = marks.get(src, {})
        manual, reset = m.get("status"), ""
        if manual and m.get("hash") != digest:
            reset = (f"Статус «{STATUS_LABEL.get(manual, manual)}» от {m.get('at', '')} снят: заявку с тех пор "
                     "изменили — посмотрите её снова.")
            manual = None
        if manual == DONE and src in has_errors:
            reset = f"Статус «Проверено» от {m.get('at', '')} снят: в заявке появились ошибки."
            manual = None
        status = manual or (FIX if src in has_errors else CHECK)
        reviews[src] = Review(status, bool(manual), m.get("at", "") if manual else "", reset,
                              dict(m.get("checked", {})))
    return PreappResult(result.teams, issues), reviews
