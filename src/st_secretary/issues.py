"""Замечания проверок — единый формат для всех модулей (допуск, предзаявки, документы)."""

from __future__ import annotations

from dataclasses import dataclass

ERROR = "error"  # документ выпускать нельзя, пока не исправлено
WARNING = "warning"  # нужно решение или уточнение человеком
FIXED = "fixed"  # система исправила сама (показывается, чтобы человек мог перепроверить)
INFO = "info"

SEVERITY_LABEL = {ERROR: "Ошибка", WARNING: "Проверить", FIXED: "Исправлено", INFO: "Справка"}
SEVERITY_ORDER = {ERROR: 0, WARNING: 1, FIXED: 2, INFO: 3}


@dataclass(frozen=True)
class Issue:
    severity: str
    text: str
    source: str = ""  # файл или раздел
    team: str = ""
    person: str = ""
    field: str = ""
    before: str = ""
    after: str = ""

    @property
    def label(self) -> str:
        return SEVERITY_LABEL.get(self.severity, self.severity)


def worst(issues) -> str | None:
    """Самый серьёзный уровень среди замечаний (None — замечаний нет)."""
    levels = [i.severity for i in issues]
    return min(levels, key=SEVERITY_ORDER.__getitem__) if levels else None
