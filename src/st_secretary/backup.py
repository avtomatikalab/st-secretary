"""Резервные копии соревнований: одной кнопкой, автоматически при запуске программы и восстановление.

Копия — zip-архив папки соревнования (карточка, заявки, отметки комиссии, результаты, протоколы, документы
по итогам). Хранится в папке «Резервные копии» рядом с папкой «данные»; её же можно скачать через браузер —
например, на флешку. Сканы документов участников и паспортные данные судей в копию не входят: они хранятся
отдельно, только на этом компьютере (папка «СТ-Секретарь — документы участников»).

При запуске программы делается автоматическая копия каждого соревнования, в котором что-то изменилось со
времени прошлой копии; автоматических копий хранится по 10 на соревнование, сделанные вручную не удаляются.

Восстановление никогда ничего не затирает: соревнование из копии ложится новой папкой. Архив проверяется:
одна папка соревнования с карточкой внутри, без путей наружу («..», диски), разумный размер.
"""

from __future__ import annotations

import io
import os
import re
import shutil
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath

BACKUPS = "Резервные копии"
CARD = "Карточка_соревнования.xlsx"
AUTO = " (авто)"
KEEP_AUTO = 10
MAX_FILES = 20000
MAX_BYTES = 2 * 2**30  # распакованный размер: 2 ГБ — больше у соревнования не бывает
_STAMP = re.compile(r" — (\d{4}-\d{2}-\d{2} \d{2}-\d{2}(?:-\d{2})?)( \(авто\))?\.zip$")


class BackupError(Exception):
    """Файл не похож на резервную копию СТ-Секретаря — текст для человека."""


@dataclass(frozen=True)
class Backup:
    path: Path
    at: datetime
    auto: bool

    @property
    def size_text(self) -> str:
        n = self.path.stat().st_size
        return f"{n / 2**20:.1f} МБ".replace(".", ",") if n >= 2**20 else f"{max(1, n // 1024)} КБ"


def backups_dir(data_root: Path) -> Path:
    return Path(data_root).resolve().parent / BACKUPS


def _files(folder: Path) -> list[Path]:
    """Что входит в копию: всё в папке соревнования, кроме временных файлов Excel и Word («~$…»)."""
    return sorted(p for p in folder.rglob("*") if p.is_file() and not p.name.startswith("~$")
                  and not p.name.endswith(".part"))


def make(folder: Path, dest: Path, now: datetime, auto: bool = False) -> Path:
    """Архив папки соревнования в dest; внутри — папка с тем же именем."""
    dest.mkdir(parents=True, exist_ok=True)
    stamp = f"{now:%Y-%m-%d %H-%M}"
    path = dest / f"{folder.name} — {stamp}{AUTO if auto else ''}.zip"
    if path.exists():  # две копии в одну минуту
        path = dest / f"{folder.name} — {now:%Y-%m-%d %H-%M-%S}{AUTO if auto else ''}.zip"
    tmp = path.with_name(path.name + ".part")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
        for p in _files(folder):
            z.write(p, (PurePosixPath(folder.name) / p.relative_to(folder).as_posix()).as_posix())
    os.replace(tmp, path)
    return path


def listing(dest: Path, folder_name: str) -> list[Backup]:
    """Копии одного соревнования, новые сверху."""
    if not dest.is_dir():
        return []
    out = []
    for p in dest.glob("*.zip"):
        if not p.name.startswith(folder_name + " — "):
            continue
        m = _STAMP.search(p.name)
        if not m or p.name[:m.start()] != folder_name:
            continue
        fmt = "%Y-%m-%d %H-%M-%S" if m.group(1).count("-") == 4 else "%Y-%m-%d %H-%M"
        out.append(Backup(p, datetime.strptime(m.group(1), fmt), bool(m.group(2))))
    return sorted(out, key=lambda b: (b.at, b.path.name), reverse=True)


def changed_since(folder: Path, when: datetime) -> bool:
    return any(datetime.fromtimestamp(p.stat().st_mtime) > when for p in _files(folder))


def auto(folders: list[Path], dest: Path, now: datetime, keep: int = KEEP_AUTO) -> list[Path]:
    """Автоматические копии соревнований, где что-то изменилось со времени последней копии; старые — удаляются."""
    made = []
    for folder in folders:
        have = listing(dest, folder.name)
        if have and not changed_since(folder, have[0].at):
            continue
        made.append(make(folder, dest, now, auto=True))
        for old in [b for b in listing(dest, folder.name) if b.auto][keep:]:
            old.path.unlink(missing_ok=True)
    return made


def restore(data: bytes, data_root: Path, now: datetime) -> str:
    """Соревнование из архива — новой папкой в data_root. Возвращает имя папки."""
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise BackupError("это не архив .zip") from None
    names = [i for i in z.infolist() if not i.is_dir()]
    if not names:
        raise BackupError("архив пустой")
    if len(names) > MAX_FILES or sum(i.file_size for i in names) > MAX_BYTES:
        raise BackupError("архив слишком большой для копии соревнования")
    tops = set()
    for i in names:
        parts = PurePosixPath(i.filename.replace("\\", "/")).parts
        if not parts or i.filename.startswith(("/", "\\")) or ".." in parts or ":" in parts[0] or len(parts) < 2:
            raise BackupError(f"в архиве неподходящий путь: «{i.filename}»")
        tops.add(parts[0])
    if len(tops) != 1:
        raise BackupError("в архиве должна быть одна папка соревнования")
    top = tops.pop()
    if f"{top}/{CARD}" not in {i.filename.replace("\\", "/") for i in names}:
        raise BackupError(f"в папке архива нет «{CARD}» — это не копия соревнования")
    data_root.mkdir(parents=True, exist_ok=True)
    name = top if not (data_root / top).exists() else f"{top} (восстановлено {now:%d.%m %H-%M})"
    n = 2
    while (data_root / name).exists():
        name = f"{top} (восстановлено {now:%d.%m %H-%M}, {n})"
        n += 1
    target = data_root / name
    tmp = data_root / f"~восстановление {now:%H%M%S%f}"
    try:
        for i in names:
            rel = PurePosixPath(i.filename.replace("\\", "/")).relative_to(top)
            out = tmp.joinpath(*rel.parts)
            out.parent.mkdir(parents=True, exist_ok=True)
            with z.open(i) as src, out.open("wb") as dst:
                dst.write(src.read())
        os.replace(tmp, target)
    finally:
        if tmp.exists():
            shutil.rmtree(tmp, ignore_errors=True)
    return name
