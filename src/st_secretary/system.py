"""Слова, которые зависят от системы: как называется файл запуска программы и её окно."""

from __future__ import annotations

import sys


def launcher(system: str = sys.platform) -> str:
    """Файл, которым запускают переносную версию."""
    if system.startswith("win"):
        return "СТ-Секретарь.bat"
    return "СТ-Секретарь.command" if system == "darwin" else "СТ-Секретарь.sh"


def console(system: str = sys.platform) -> str:
    """Окно, в котором работает программа («его не закрывайте»)."""
    if system.startswith("win"):
        return "чёрное окно"
    return "окно Терминала" if system == "darwin" else "окно терминала"
