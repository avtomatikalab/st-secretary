"""Хранение соревнований на диске: одна папка — одно соревнование.

    данные/
      2025-09-20 Чемпионат г. Красноярска по спортивному туризму/
        Карточка_соревнования.xlsx   ← её можно открыть и поправить в Excel
        Предзаявки/                  ← файлы заявок команд (и исправленные в программе)
          Убранные/                  ← заявки, убранные из обработки (не удаляются)
          Прежние версии/            ← файл до исправления или до замены новым — с датой и временем
        Отметки_предзаявок.json      ← статусы заявок и «проверено» у замечаний
        Сводка_предзаявок.xlsx       ← создаётся по кнопке
        Комиссия_по_допуску.json     ← документы, решения, номера, взносы, перезаявки
        Комиссия_по_допуску.xlsx     ← протокол комиссии и ведомость взносов, по кнопке
        Проверка_снаряжения.json     ← перечень снаряжения, отметки, баллы
        Итоги.json                   ← места по зачётам, оценки судей, данные отчёта
        Документы по итогам/         ← дипломы, наклейки, справки, выписки на разряды, отчёт
        Договоры_и_табель.json       ← бригада, дни, ставки, заказчик (без паспортов)
        Шаблон договора.docx         ← свой шаблон договора и акта, если есть (иначе — встроенный)

Папку можно открыть в Проводнике, скопировать на флешку, передать коллеге. Персональные данные
(даты рождения, телефоны) хранятся только в ней.

Сканы документов участников, паспорта и счета судей и всё, где они есть (договоры, табель), — отдельно,
только на этом компьютере (папка «СТ-Секретарь — документы участников» в профиле):

    СТ-Секретарь — документы участников/
      Судьи и персонал — личные данные.json   ← паспорт, ИНН, СНИЛС, счёт; общий для всех соревнований
      <папка соревнования>/
        <заявка команды>/                     ← сканы и фото документов команды
        Договоры и табель/                    ← договоры с актами, табель-наряд
"""

from __future__ import annotations

import hashlib
import json
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
from st_secretary.web.review import HAND, STATUSES, Review, apply_marks

CARD = "Карточка_соревнования.xlsx"
PREAPPS = "Предзаявки"
REMOVED = "Убранные"
VERSIONS = "Прежние версии"
MARKS = "Отметки_предзаявок.json"
SUMMARY = "Сводка_предзаявок.xlsx"
ADMISSION = "Комиссия_по_допуску.json"
COMMISSION_REPORT = "Комиссия_по_допуску.xlsx"
EQUIPMENT = "Проверка_снаряжения.json"
RESULTS = "Итоги.json"  # места по зачётам (из протокола СЕКРЕТАРЬ_ST или вручную), оценки судей, данные отчёта
OUT_DIR = "Документы по итогам"  # дипломы, справки, выписки, отчёт — Word и Excel
CONTRACTS = "Договоры_и_табель.json"
CONTRACT_TEMPLATE = "Шаблон договора.docx"
PERSONAL = "Судьи и персонал — личные данные.json"
CONTRACTS_DIR = "Договоры и табель"
# Сканы и фото документов участников (паспорта, полисы, справки): только на этом компьютере и не в облачной
# папке — поэтому отдельно от данных соревнования (их часто держат на Google Диске или передают на флешке).
DOCS_ROOT_NAME = "СТ-Секретарь — документы участников"
DOC_TYPES = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".heic", ".pdf", ".doc", ".docx", ".xls", ".xlsx",
             ".odt", ".rtf", ".txt"}
IMAGE_TYPES = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}  # браузер показывает сам (HEIC — нет)


def default_docs_root() -> Path:
    return Path.home() / DOCS_ROOT_NAME
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
        if old is not None:
            self.rename_marks(old.name, target.name)  # был .xls — отметки переходят к .xlsx
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

    def write_summary(self, result: PreappResult, comp: Competition, path: Path | None = None,
                      statuses: dict[str, str] | None = None) -> Path:
        return write_preapp_report(result, comp, path or self.summary_path, statuses)

    # ------------------------------------------------------------ отметки секретаря

    @property
    def marks_path(self) -> Path:
        return self.path / MARKS

    def marks(self) -> dict:
        """{файл заявки: {"status", "hash", "at", "checked": {ключ замечания: когда}}}."""
        try:
            data = json.loads(self.marks_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def _save_marks(self, data: dict) -> None:
        tmp = self.path / f"~{MARKS}"
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, self.marks_path)

    @staticmethod
    def file_hash(path: Path) -> str:
        """Отпечаток содержимого: по нему видно, что заявку изменили после отметки."""
        return hashlib.sha1(path.read_bytes()).hexdigest()[:20]

    def set_status(self, name: str, status: str, by: str = HAND) -> None:
        """by — HAND (кнопкой) или SAVE (сам после сохранения заявки без замечаний)."""
        path = self.preapp_path(name)
        if path is None or status not in STATUSES:
            raise ValueError(name)
        data = self.marks()
        entry = data.setdefault(path.name, {})
        entry.update(status=status, hash=self.file_hash(path), at=f"{datetime.now():%d.%m.%Y %H:%M}", by=by)
        self._save_marks(data)

    def set_checked(self, name: str, keys: list[str], on: bool = True) -> None:
        """Отметить замечания «проверено» (или снять отметку)."""
        path = self.preapp_path(name)
        if path is None:
            raise ValueError(name)
        data = self.marks()
        checked = data.setdefault(path.name, {}).setdefault("checked", {})
        for k in keys:
            if on:
                checked.setdefault(k, f"{datetime.now():%d.%m.%Y %H:%M}")
            else:
                checked.pop(k, None)
        self._save_marks(data)

    def rename_marks(self, old: str, new: str) -> None:
        data = self.marks()
        if old != new and old in data:
            data[new] = data.pop(old)
            self._save_marks(data)
        for read, write in ((self.admission, self.save_admission), (self.equipment, self.save_equipment)):
            d = read()
            if old != new and old in d.get("teams", {}):
                d["teams"][new] = d["teams"].pop(old)
                write(d)

    # ------------------------------------------------------------ комиссия по допуску

    @property
    def admission_path(self) -> Path:
        return self.path / ADMISSION

    @property
    def commission_report_path(self) -> Path:
        return self.path / COMMISSION_REPORT

    def admission(self) -> dict:
        """Отметки комиссии: {"settings": {...}, "teams": {файл заявки: {...}}} (см. commission.py)."""
        try:
            data = json.loads(self.admission_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def save_admission(self, data: dict) -> None:
        self._write_json(ADMISSION, data)

    def _read_json(self, name: str) -> dict:
        try:
            data = json.loads((self.path / name).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def _write_json(self, name: str, data: dict) -> None:
        tmp = self.path / f"~{name}"  # сначала во временный файл: оборвавшаяся запись не портит данные
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, self.path / name)

    def equipment(self) -> dict:
        """Проверка снаряжения: {"settings": {...}, "teams": {файл заявки: {...}}} (см. equipment.py)."""
        return self._read_json(EQUIPMENT)

    def save_equipment(self, data: dict) -> None:
        self._write_json(EQUIPMENT, data)

    def results_data(self) -> dict:
        """Итоги: {"zachety": {ключ зачёта: {...}}, "judges": {...}, "report": {...}} (см. results.py)."""
        return self._read_json(RESULTS)

    def save_results_data(self, data: dict) -> None:
        self._write_json(RESULTS, data)

    @property
    def out_dir(self) -> Path:
        return self.path / OUT_DIR

    def contracts(self) -> dict:
        """Договоры и табель: {"period", "accrual", "rates", "customer", "people", "extra"} (см. staff.py)."""
        return self._read_json(CONTRACTS)

    def save_contracts(self, data: dict) -> None:
        self._write_json(CONTRACTS, data)

    @property
    def contract_template(self) -> Path:
        return self.path / CONTRACT_TEMPLATE


class Store:
    def __init__(self, root: str | Path, docs_root: str | Path | None = None):
        self.root = Path(root)
        self.docs_root = Path(docs_root) if docs_root else default_docs_root()
        self._preapp_cache: dict[str, tuple[tuple, PreappResult]] = {}
        self._hash_cache: dict[tuple, str] = {}

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

    # ------------------------------------------------------------ документы команд (сканы, фото)

    def team_docs_dir(self, f: CompFolder, file: str) -> Path:
        """Папка документов команды: <корень>/<соревнование>/<имя файла заявки без расширения>."""
        return self.docs_root / f.id / safe_name(Path(file).stem)

    def team_docs(self, f: CompFolder, file: str) -> list[Path]:
        d = self.team_docs_dir(f, file)
        if not d.is_dir():
            return []
        return sorted((p for p in d.iterdir() if p.is_file() and p.suffix.lower() in DOC_TYPES
                       and not p.name.startswith("~$")), key=lambda p: (p.stat().st_mtime, p.name))

    def team_doc(self, f: CompFolder, file: str, name: str) -> Path | None:
        """Файл документа по имени — только из папки этой команды."""
        p = self.team_docs_dir(f, file) / Path(str(name).replace("\\", "/")).name
        return p if name and p.suffix.lower() in DOC_TYPES and p.is_file() else None

    def add_team_doc(self, f: CompFolder, file: str, filename: str, data: bytes) -> str:
        name = safe_name(Path(filename.replace("\\", "/")).name) or "документ"
        if Path(name).suffix.lower() not in DOC_TYPES:
            raise ValueError(f"«{name}» — не фото, не PDF и не документ")
        d = self.team_docs_dir(f, file)
        d.mkdir(parents=True, exist_ok=True)
        target, n = d / name, 2
        while target.exists():  # у команд часто одинаковые имена файлов («скан.pdf») — не затираем
            target, n = d / f"{Path(name).stem} ({n}){Path(name).suffix}", n + 1
        target.write_bytes(data)
        return target.name

    def remove_team_doc(self, f: CompFolder, file: str, name: str) -> Path:
        """Убрать документ: переносится в «Убранные» внутри папки команды, не удаляется."""
        src = self.team_doc(f, file, name)
        if src is None:
            raise FileNotFoundError(name)
        dest_dir = src.parent / REMOVED
        dest_dir.mkdir(exist_ok=True)
        dest, n = dest_dir / src.name, 2
        while dest.exists():
            dest, n = dest_dir / f"{src.stem} ({n}){src.suffix}", n + 1
        shutil.move(src, dest)
        return dest

    # ------------------------------------------------------------ судьи и персонал: паспорта, счета

    @property
    def personal_path(self) -> Path:
        return self.docs_root / PERSONAL

    def personal(self) -> dict[str, dict]:
        """{ключ ФИО: {"birth", "passport", …}} — общий для всех соревнований: судьи приезжают из года в год."""
        try:
            data = json.loads(self.personal_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def save_personal(self, key: str, values: dict) -> None:
        data = self.personal()
        data[key] = {k: v for k, v in values.items() if v}
        self.docs_root.mkdir(parents=True, exist_ok=True)
        tmp = self.docs_root / f"~{PERSONAL}"
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, self.personal_path)

    def contracts_dir(self, f: CompFolder) -> Path:
        """Договоры и табель соревнования — там же, где сканы: в них паспорта и счета."""
        return self.docs_root / f.id / CONTRACTS_DIR

    def review(self, f: CompFolder, comp: Competition) -> tuple[PreappResult, dict[str, Review]]:
        """Заявки с отметками секретаря: «проверено» у замечаний и статусы по файлам."""
        hashes = {}
        for p in f.preapp_files():
            st = p.stat()
            key = (str(p), st.st_mtime_ns, st.st_size)
            if key not in self._hash_cache:
                self._hash_cache[key] = f.file_hash(p)
            hashes[p.name] = self._hash_cache[key]
        return apply_marks(self.preapps(f, comp), f.marks(), hashes)
