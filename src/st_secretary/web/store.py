"""Хранение соревнований на диске: одна папка — одно соревнование.

    данные/
      2025-09-20 Чемпионат г. Красноярска по спортивному туризму/
        Карточка_соревнования.xlsx   ← её можно открыть и поправить в Excel
        Предзаявки/                  ← файлы заявок команд (и исправленные в программе)
          Убранные/                  ← заявки, убранные из обработки (не удаляются)
          Прежние версии/            ← файл до исправления или до замены новым — с датой и временем
        Сводка_предзаявок.xlsx       ← создаётся по кнопке

Папку можно открыть в Проводнике, скопировать на флешку, передать коллеге. Персональные данные
(даты рождения, телефоны) хранятся только в ней.
"""

from __future__ import annotations

import os
import re
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from st_secretary.competition import Competition
from st_secretary.exporters.preapp_xlsx import write_preapp_report
from st_secretary.importers.card_xlsx import load_card, write_card
from st_secretary.importers.preapp_xlsx import read_preapplication, write_preapplication
from st_secretary.preapp import PreappResult, process

CARD = "Карточка_соревнования.xlsx"
PREAPPS = "Предзаявки"
REMOVED = "Убранные"
VERSIONS = "Прежние версии"
SUMMARY = "Сводка_предзаявок.xlsx"
EXCEL = (".xlsx", ".xls")

_BAD_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')  # недопустимы в именах файлов Windows


def safe_name(name: str, limit: int = 120) -> str:
    return " ".join(_BAD_CHARS.sub(" ", name).split())[:limit].rstrip(" .")


def folder_name(comp: Competition) -> str:
    """«2025-09-20 Чемпионат г. Красноярска…» — в Проводнике соревнования идут по датам."""
    return f"{comp.date_from:%Y-%m-%d} {safe_name(comp.title, 80)}".strip()


@dataclass(frozen=True)
class CompFolder:
    path: Path

    @property
    def id(self) -> str:
        return self.path.name

    @property
    def card_path(self) -> Path:
        return self.path / CARD

    @property
    def preapp_dir(self) -> Path:
        return self.path / PREAPPS

    @property
    def summary_path(self) -> Path:
        return self.path / SUMMARY

    def version(self) -> str:
        """Метка версии карточки: меняется, когда файл сохранили — здесь или в Excel."""
        return str(self.card_path.stat().st_mtime_ns)

    def load(self) -> Competition:
        return load_card(self.card_path)

    def save(self, comp: Competition) -> None:
        write_card(self.card_path, comp)

    def preapp_files(self) -> list[Path]:
        if not self.preapp_dir.is_dir():
            return []
        return sorted((p for p in self.preapp_dir.iterdir()
                       if p.is_file() and p.suffix.lower() in EXCEL and not p.name.startswith("~$")),
                      key=lambda p: p.name.lower())

    def preapp_path(self, name: str) -> Path | None:
        """Файл заявки по имени — только из папки «Предзаявки» этого соревнования."""
        p = self.preapp_dir / Path(str(name).replace("\\", "/")).name
        return p if name and p.suffix.lower() in EXCEL and not p.name.startswith("~$") and p.is_file() else None

    @staticmethod
    def file_version(path: Path) -> str:
        """Метка версии файла: меняется, когда файл сохранили — здесь или в Excel."""
        return str(path.stat().st_mtime_ns)

    def keep_version(self, path: Path) -> Path:
        """Перенести файл заявки в «Предзаявки/Прежние версии» с датой и временем в имени — не удалять."""
        dest_dir = self.preapp_dir / VERSIONS
        dest_dir.mkdir(exist_ok=True)
        stamp = f"{datetime.now():%Y-%m-%d %H-%M-%S}"
        dest, n = dest_dir / f"{path.stem} ({stamp}){path.suffix}", 2
        while dest.exists():
            dest, n = dest_dir / f"{path.stem} ({stamp} {n}){path.suffix}", n + 1
        shutil.move(path, dest)
        return dest

    def _unique(self, path: Path) -> Path:
        out, n = path, 2
        while out.exists():
            out, n = path.with_name(f"{path.stem} ({n}){path.suffix}"), n + 1
        return out

    def add_preapp(self, filename: str, data: bytes) -> bool:
        """Сохранить файл заявки. True — заменён файл с тем же именем (команда прислала исправленную);
        прежний вариант переносится в «Прежние версии»."""
        name = safe_name(Path(filename.replace("\\", "/")).name)
        if Path(name).suffix.lower() not in EXCEL or name.startswith("~$"):
            raise ValueError(f"«{name}» — не файл Excel")
        self.preapp_dir.mkdir(exist_ok=True)
        target = self.preapp_dir / name
        replaced = target.exists()
        if replaced:
            self.keep_version(target)
        target.write_bytes(data)
        return replaced

    def save_preapp(self, name: str | None, head: dict, rows: list[dict], qual_labels: list[str] | None = None) -> str:
        """Записать заявку из формы программы. name — файл, который исправляли (None — новая заявка).
        Прежний файл переносится в «Прежние версии»; .xls сохраняется как .xlsx. Возвращает имя файла."""
        self.preapp_dir.mkdir(exist_ok=True)
        now = datetime.now()
        old = self.preapp_path(name) if name else None
        if old is not None:
            target = old.with_suffix(".xlsx")
            if target.name.lower() != old.name.lower() and target.exists():  # был .xls, а .xlsx с тем же именем занят
                target = self._unique(target)
            note = (f"Исправлено в программе СТ-Секретарь {now:%d.%m.%Y %H:%M}. "
                    f"Прежний вариант файла — в папке «{PREAPPS}\\{VERSIONS}».")
        else:
            target = self._unique(self.preapp_dir / f"{safe_name(head.get('team', ''), 80) or 'Заявка'}.xlsx")
            note = f"Заполнено в программе СТ-Секретарь {now:%d.%m.%Y %H:%M}."
        tmp = self.preapp_dir / f"~$сохранение {now:%H%M%S%f}.xlsx"  # «~$» — такие файлы в список заявок не попадают
        write_preapplication(tmp, head, rows, note, qual_labels)
        try:
            if old is not None:
                self.keep_version(old)  # файл открыт в Excel → PermissionError, ничего не изменилось
            os.replace(tmp, target)
        finally:
            tmp.unlink(missing_ok=True)
        return target.name

    def remove_preapp(self, name: str) -> Path:
        """Убрать заявку из обработки: файл переносится в «Предзаявки/Убранные», а не удаляется."""
        src = self.preapp_dir / Path(name).name
        if not src.is_file():
            raise FileNotFoundError(name)
        dest_dir = self.preapp_dir / REMOVED
        dest_dir.mkdir(exist_ok=True)
        dest = dest_dir / src.name
        if dest.exists():
            dest = dest_dir / f"{src.stem} ({datetime.now():%d.%m %H-%M-%S}){src.suffix}"
        shutil.move(src, dest)
        return dest

    def write_summary(self, result: PreappResult, comp: Competition, path: Path | None = None) -> Path:
        return write_preapp_report(result, comp, path or self.summary_path)


class Store:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self._preapp_cache: dict[str, tuple[tuple, PreappResult]] = {}

    def all(self) -> list[CompFolder]:
        if not self.root.is_dir():
            return []
        found = [CompFolder(p) for p in self.root.iterdir() if p.is_dir() and (p / CARD).is_file()]
        return sorted(found, key=lambda f: f.id, reverse=True)  # новые сверху

    def get(self, cid: str) -> CompFolder | None:
        if not cid or cid in (".", "..") or cid != Path(cid).name or _BAD_CHARS.search(cid):
            return None
        f = CompFolder(self.root / cid)
        return f if f.card_path.is_file() else None

    def create(self, comp: Competition, card_bytes: bytes | None = None) -> CompFolder:
        """Новая папка соревнования. card_bytes — готовая карточка (загружена из Excel как есть)."""
        self.root.mkdir(parents=True, exist_ok=True)
        base = folder_name(comp)
        name, n = base, 2
        while (self.root / name).exists():
            name, n = f"{base} ({n})", n + 1
        f = CompFolder(self.root / name)
        f.path.mkdir()
        f.preapp_dir.mkdir()
        if card_bytes is not None:
            f.card_path.write_bytes(card_bytes)
        else:
            f.save(comp)
        return f

    def preapps(self, f: CompFolder, comp: Competition) -> PreappResult:
        """Обработать заявки. Результат запоминается, пока не изменились файлы заявок и карточка."""
        files = f.preapp_files()
        key = (f.version(), tuple((p.name, p.stat().st_mtime_ns, p.stat().st_size) for p in files))
        hit = self._preapp_cache.get(f.id)
        if hit and hit[0] == key:
            return hit[1]
        result = process([read_preapplication(p) for p in files], comp)
        self._preapp_cache[f.id] = (key, result)
        return result
