"""Хранение соревнований на диске: одна папка — одно соревнование.

    данные/
      Фестивали.json                 ← группы соревнований одного выезда (решение 039)
      2025-09-20 Чемпионат г. Красноярска по спортивному туризму/
        Карточка_соревнования.xlsx   ← её можно открыть и поправить в Excel
        Предзаявки/                  ← файлы заявок команд (и исправленные в программе)
          Убранные/                  ← заявки, убранные из обработки (не удаляются)
          Прежние версии/            ← файл до исправления или до замены новым — с датой и временем
          Заявки делегаций (исходные)/ ← заявка делегации до деления на файлы команд (п. 37)
        Отметки_предзаявок.json      ← статусы заявок и «проверено» у замечаний
        Сводка_предзаявок.xlsx       ← создаётся по кнопке
        Комиссия_по_допуску.json     ← документы, решения, номера, взносы, перезаявки
        Комиссия_по_допуску.xlsx     ← протокол комиссии и ведомость взносов, по кнопке
        Проверка_снаряжения.json     ← перечень снаряжения, отметки, баллы
        Итоги.json                   ← места по зачётам, оценки судей, данные отчёта
        Документы по итогам/         ← дипломы, наклейки, справки, выписки на разряды, отчёт
        Результаты_дистанции.json    ← этапы, баллы команд по этапам, статусы, протесты
        Протоколы/                   ← предварительные и официальные протоколы результатов
        Договоры_и_табель.json       ← бригада, дни, ставки, заказчик (без паспортов)
        Шаблон договора.docx         ← свой шаблон договора и акта, если есть (иначе — встроенный)
        Именная заявка — бланк соревнования.docx ← свой бланк именной заявки, если есть (п. 36)
    Рядом с «данными»: «Журнал» (журнал программы), «Резервные копии», «Правки и ошибки» (кнопка «Сообщить»).

Папку можно открыть в Проводнике, скопировать на флешку, передать коллеге. Персональные данные
(даты рождения, телефоны) хранятся только в ней.

Сканы документов участников, паспорта и счета судей и всё, где они есть (договоры, табель), — отдельно,
только на этом компьютере (папка «СТ-Секретарь — документы участников» в профиле):

    СТ-Секретарь — документы участников/
      Судьи и персонал — личные данные.json   ← паспорт, ИНН, СНИЛС, счёт; общий для всех соревнований
      Свои формы заявок.json, Формы заявок — образцы/ ← свои формы предзаявок (п. 37)
      Документы комиссии.json                 ← свои документы комиссии и последний набор (п. 30)
      Свои значения (неофициальные).json      ← свои группы, дисциплины, названия зачётов (решение 038)
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

from st_secretary import festival as fv
from st_secretary.competition import Competition
from st_secretary.exporters.preapp_xlsx import write_preapp_report
from st_secretary.importers.card_xlsx import load_card, write_card
from st_secretary.importers.preapp_xlsx import read_preapplication, write_preapplication
from st_secretary.preapp import PreappResult, process
from st_secretary.textclean import alpha_key, name_key
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
RUN = "Результаты_дистанции.json"  # этапы, баллы команд, статусы, протесты, публикация протоколов
PROTOCOLS_DIR = "Протоколы"  # предварительные и официальные протоколы результатов
CONTRACT_TEMPLATE = "Шаблон договора.docx"
PERSONAL = "Судьи и персонал — личные данные.json"
# Свои группы, дисциплины и названия зачётов неофициальных соревнований — подсказки в карточке (решение 038)
OWN_VALUES = "Свои значения (неофициальные).json"
OWN_KINDS = ("groups", "names", "disciplines")
# Фестивали — группы соревнований одного выезда (решение 039): в папке «данные», соревнования не трогаются
FESTIVALS = "Фестивали.json"
# Свои формы предзаявок (Правки, п. 37) — на этом компьютере, рядом с личными данными судей
FORMS = "Свои формы заявок.json"
NAMED_TEMPLATE = "Именная заявка — бланк соревнования.docx"  # свой бланк именной заявки (Word) соревнования, п. 36
DOC_SET = "Документы комиссии.json"  # свои документы комиссии и последний набор — на этом компьютере (Правки, п. 30)
DELEGATION_SOURCES = "Заявки делегаций (исходные)"
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


def keep_extra(rows: list[dict], old_rows: list) -> list[dict]:
    """Строкам исправленной заявки — свои колонки прежнего файла: по ФИО, иначе по месту в списке."""
    def key(s) -> str:
        return name_key(s)

    by_name = {key(r.values.get("fio")): r.extra for r in old_rows if r.extra}
    out = []
    for i, row in enumerate(rows):
        extra = row.get("extra") or by_name.get(key(row.get("fio")))
        if extra is None and i < len(old_rows) and key(old_rows[i].values.get("fio")) not in \
                {key(r.get("fio")) for r in rows}:
            extra = old_rows[i].extra
        out.append({**row, "extra": extra or {}})
    return out


def read_festivals(root: Path) -> list[dict]:
    """Записи фестивалей из «Фестивали.json» как есть (см. festival.py)."""
    try:
        data = json.loads((Path(root) / FESTIVALS).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    items = data.get("festivals", []) if isinstance(data, dict) else []
    return [x for x in items if isinstance(x, dict) and x.get("id")]


def write_festivals(root: Path, items: list[dict]) -> None:
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    save_json(root / FESTIVALS, {"festivals": items})


def update_festival(root: Path, fid: str, change) -> dict | None:
    """Прочитать, изменить (change(запись)) и записать фестиваль. Возвращает изменённую запись."""
    items = read_festivals(root)
    x = next((i for i in items if i["id"] == fid), None)
    if x is not None:
        change(x)
        write_festivals(root, items)
    return x

_BAD_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')  # недопустимы в именах файлов Windows


def load_json(path: Path) -> dict:
    """JSON-файл программы как словарь; нет файла или он испорчен — пустой словарь."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save_json(path: Path, data) -> None:
    """Записать JSON: сначала во временный файл «~имя» рядом, потом заменить — оборвавшаяся запись не портит
    данные."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"~{path.name}")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)


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
                      key=lambda p: alpha_key(p.name))  # «Ёлки-палки» — после «Е», а не в конце

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

    def save_preapp(self, name: str | None, head: dict, rows: list[dict], qual_labels: list[str] | None = None,
                    forms: list[dict] | None = None) -> str:
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
        if old is not None:  # свои колонки заявки (справа от бланка) — переносятся к тем же участникам
            rows = keep_extra(rows, read_preapplication(old, forms).rows)
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
        save_json(self.marks_path, data)

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
        self.write_json(ADMISSION, data)

    def read_json(self, name: str) -> dict:
        return load_json(self.path / name)

    def write_json(self, name: str, data: dict) -> None:
        save_json(self.path / name, data)

    def equipment(self) -> dict:
        """Проверка снаряжения: {"settings": {...}, "teams": {файл заявки: {...}}} (см. equipment.py)."""
        return self.read_json(EQUIPMENT)

    def save_equipment(self, data: dict) -> None:
        self.write_json(EQUIPMENT, data)

    def results_data(self) -> dict:
        """Итоги: {"zachety": {ключ зачёта: {...}}, "judges": {...}, "report": {...}} (см. results.py)."""
        return self.read_json(RESULTS)

    def save_results_data(self, data: dict) -> None:
        self.write_json(RESULTS, data)

    @property
    def out_dir(self) -> Path:
        return self.path / OUT_DIR

    def festival(self) -> dict | None:
        """Фестиваль, в который входит соревнование (запись из «Фестивали.json» в папке «данные»)."""
        return next((x for x in read_festivals(self.path.parent) if self.id in x.get("members", [])), None)

    def contracts(self) -> dict:
        """Договоры и табель: {"period", "accrual", "rates", "customer", "people", "extra"} (см. staff.py).
        На фестивале бригада («extra») — общая, а при «один договор и табель на фестиваль» — и всё остальное."""
        data = self.read_json(CONTRACTS)
        fest = self.festival()
        if fest is None:
            return data
        if fv.mode(fest, "contracts") == "festival":
            data = dict(fest.get("contracts", {}))
        data["extra"] = list(fest.get("brigade", []))
        return data

    def save_contracts(self, data: dict) -> None:
        """Сохранить бригаду, дни и ставки: на фестивале бригада общая, договоры — у фестиваля или у соревнования
        (режим фестиваля)."""
        fest = self.festival()
        if fest is None:
            self.write_json(CONTRACTS, data)
            return
        data = dict(data)
        brigade = data.pop("extra", [])
        joint = fv.mode(fest, "contracts") == "festival"

        def change(x):
            x["brigade"] = brigade
            if joint:
                x["contracts"] = data

        update_festival(self.path.parent, fest["id"], change)
        if not joint:  # свои дни и ставки — в папке соревнования; его прежняя бригада там же остаётся
            own = self.read_json(CONTRACTS).get("extra")
            self.write_json(CONTRACTS, {**data, **({"extra": own} if own is not None else {})})

    @property
    def contract_template(self) -> Path:
        return self.path / CONTRACT_TEMPLATE

    def run_data(self) -> dict:
        """Результаты дистанции: {"zachety": {ключ зачёта: {"stages", "teams", …}}} (см. psr_run.py)."""
        return self.read_json(RUN)

    def save_run_data(self, data: dict) -> None:
        self.write_json(RUN, data)

    @property
    def protocols_dir(self) -> Path:
        return self.path / PROTOCOLS_DIR


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
        key = (f.version(), tuple((p.name, p.stat().st_mtime_ns, p.stat().st_size) for p in files),
               self.forms_version())
        hit = self._preapp_cache.get(f.id)
        if hit and hit[0] == key:
            return hit[1]
        forms = self.forms()
        result = process([read_preapplication(p, forms) for p in files], comp)
        self._preapp_cache[f.id] = (key, result)
        return result

    # ------------------------------------------------------------ свои формы заявок (Правки, п. 37)

    @property
    def forms_path(self) -> Path:
        return self.docs_root / FORMS

    def forms_version(self) -> int:
        try:
            return self.forms_path.stat().st_mtime_ns
        except OSError:
            return 0

    def forms(self) -> list[dict]:
        """Свои формы заявок — на этом компьютере (forms.py)."""
        try:
            data = json.loads(self.forms_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        items = data.get("forms", []) if isinstance(data, dict) else []
        return [x for x in items if isinstance(x, dict) and x.get("name") and x.get("signature")]

    def save_form(self, form: dict, old_name: str = "") -> None:
        """Сохранить форму (с тем же названием — заменить; old_name — её прежнее название при переименовании)."""
        items = [x for x in self.forms() if x["name"] not in (form["name"], old_name)] + [form]
        self.docs_root.mkdir(parents=True, exist_ok=True)
        save_json(self.forms_path, {"forms": items})

    def delete_form(self, name: str) -> None:
        items = [x for x in self.forms() if x["name"] != name]
        save_json(self.forms_path, {"forms": items})

    @property
    def doc_set_path(self) -> Path:
        return self.docs_root / DOC_SET

    def doc_set(self) -> dict:
        """Запомненное на этом компьютере (Правки, п. 30): свои документы комиссии {"docs": [...]} и последний набор
        {"last": {"docs": [...], "team_docs": [...]}} — новое соревнование начинается с него, собирать заново не нужно."""
        from st_secretary.commission import clean_own_docs

        try:
            data = json.loads(self.doc_set_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        data = data if isinstance(data, dict) else {}
        last = data.get("last") if isinstance(data.get("last"), dict) else {}
        return {"docs": clean_own_docs(data.get("docs", [])),
                "last": {k: [str(x) for x in last.get(k, [])] for k in ("docs", "team_docs") if k in last}}

    def save_doc_set(self, own: list[dict], forget: set[str], last: dict) -> None:
        """Запомнить свои документы (с тем же ключом — заменить; forget — убрать) и набор, выбранный последним."""
        mine = {d["key"]: d for d in own}
        docs = [mine.pop(d["key"], d) for d in self.doc_set()["docs"] if d["key"] not in forget] + list(mine.values())
        self.docs_root.mkdir(parents=True, exist_ok=True)
        save_json(self.doc_set_path, {"docs": docs, "last": last})

    def apply_doc_set(self, f: CompFolder) -> None:
        """Новое соревнование: документы комиссии — как в прошлый раз на этом компьютере (если набор запомнен)."""
        s = self.doc_set()
        if not s["last"]:
            return
        chosen = set(s["last"].get("docs", [])) | set(s["last"].get("team_docs", []))
        data = f.admission()
        data["settings"] = {**data.get("settings", {}), **s["last"],
                            "own_docs": [d for d in s["docs"] if d["key"] in chosen]}
        f.save_admission(data)

    @property
    def samples_dir(self) -> Path:
        """Образцы, по которым секретарь показывает форму, — только на этом компьютере, удаляются после сохранения."""
        return self.docs_root / "Формы заявок — образцы"

    def split_delegation(self, f: CompFolder, name: str) -> list[str]:
        """Заявка делегации по своей форме (команда в каждой строке) → файлы команд по стандартному бланку;
        исходный файл — в «Предзаявки/Заявки делегаций (исходные)». Возвращает имена файлов команд ([] — делить
        нечего)."""
        from st_secretary import forms as fm

        path = f.preapp_path(name)
        if path is None:
            return []
        raw = read_preapplication(path, self.forms())
        parts = fm.split_by_team(raw) if raw.team_in_row and not raw.problems else []
        if len(parts) < 2:
            return []
        now = datetime.now()
        keep = f.preapp_dir / DELEGATION_SOURCES
        keep.mkdir(exist_ok=True)
        dest, n = keep / path.name, 2
        while dest.exists():
            dest, n = keep / f"{path.stem} ({n}){path.suffix}", n + 1
        out = []
        for part in parts:
            target = f.preapp_dir / f"{safe_name(part.team, 80) or 'Команда'}.xlsx"
            if target == path:  # файл делегации назван как одна из её команд
                target = target.with_name(f"{target.stem} (команда).xlsx")
            if target.exists():
                f.keep_version(target)  # делегация прислала исправленную заявку — прежняя в «Прежних версиях»
            head ={"team": part.team, "territory": part.territory, "representative": part.representative,
                    "contacts": part.contacts, "declared": len(part.rows)}
            note = (f"Из заявки делегации «{path.name}» (форма «{raw.form}»), {now:%d.%m.%Y %H:%M}. Исходный файл — "
                    f"в папке «{PREAPPS}\\{DELEGATION_SOURCES}».")
            tmp = f.preapp_dir / f"~$деление {now:%H%M%S%f}.xlsx"
            write_preapplication(tmp, head, fm.standard_rows(part), note)
            os.replace(tmp, target)
            out.append(target.name)
        shutil.move(path, dest)
        return out

    # ------------------------------------------------------------ документы команд (сканы, фото)

    def team_docs_dir(self, f: CompFolder, file: str) -> Path:
        """Папка документов команды: <корень>/<соревнование>/<имя файла заявки без расширения>."""
        return self.docs_root / f.id / safe_name(Path(file).stem)

    def delegation_docs_dir(self, f: CompFolder, title: str) -> Path:
        """Сканы делегации (Правки, п. 20): одна папка на делегацию — на фестивале общая для его соревнований,
        внутри — по людям."""
        fest = f.festival()
        scope = safe_name(f"Фестиваль {fest.get('title', '')}") if fest else f.id
        return self.docs_root / scope / safe_name(f"Делегация {title}")

    def person_docs_dir(self, f: CompFolder, title: str, e) -> Path:
        """Папка человека в папке делегации: «Фамилия Имя Отчество 05.06.1996»."""
        born = f"{e.birth:%d.%m.%Y}" if getattr(e, "birth", None) else str(getattr(e, "birth_year", "") or "")
        return self.delegation_docs_dir(f, title) / safe_name(f"{e.name.full} {born}".strip())

    def person_docs(self, f: CompFolder, title: str, entries: list) -> list[tuple[str, Path]]:
        """Сканы людей из папки делегации: [(ФИО, файл)]."""
        out = []
        for e in entries:
            d = self.person_docs_dir(f, title, e)
            if d.is_dir():
                out += [(e.name.full, p) for p in sorted(d.iterdir(), key=lambda p: p.name)
                        if p.is_file() and p.suffix.lower() in DOC_TYPES and not p.name.startswith("~$")]
        return out

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

    # ------------------------------------------------------------ фестивали

    @property
    def festivals_path(self) -> Path:
        return self.root / FESTIVALS

    def festivals(self) -> list[dict]:
        """Записи фестивалей (festival.py); соревнования, которых уже нет, пропускаются."""
        have = {f.id for f in self.all()}
        return [{**x, "id": str(x["id"]), "title": str(x.get("title") or "Фестиваль"),
                 "members": [m for m in x.get("members", []) if m in have]} for x in read_festivals(self.root)]

    def festival(self, fid: str) -> dict | None:
        return next((x for x in self.festivals() if x["id"] == fid), None)

    def festival_of(self, cid: str) -> dict | None:
        return next((x for x in self.festivals() if cid in x["members"]), None)

    def update_festival(self, fid: str, change) -> dict | None:
        return update_festival(self.root, fid, change)

    def set_festival(self, fid: str | None, title: str, members: list[str], data: dict | None = None) -> str:
        """Создать или изменить фестиваль; соревнование — только в одном фестивале. Пустой фестиваль — удаляется.
        Вошедшие соревнования: их ГСК сверяется с ГСК фестиваля (отличия — «своя» замена), бригада — в общую;
        вышедшие получают бригаду фестиваля в свои договоры. data — сохранённые данные фестиваля (из копии)."""
        import secrets

        fid = fid or "ф" + secrets.token_hex(4)
        items, rec = [], None
        for x in read_festivals(self.root):
            if x["id"] == fid:
                rec = x
                continue
            gone = [m for m in x.get("members", []) if m in members]
            for m in gone:
                self._leave_festival(x, m)
            x["members"] = [m for m in x.get("members", []) if m not in members]
            if x["members"]:
                items.append(x)
        rec = {**(rec or {}), **(data or {})}
        before = list(rec.get("members", [])) if not data else []
        for m in before:
            if m not in members:
                self._leave_festival(rec, m)
        joined = [m for m in members if m not in before]
        if members:
            rec.update(id=fid, title=" ".join(title.split())[:200] or "Фестиваль", members=members)
            if joined:
                self._join_festival(rec, joined, restored=bool(data))
            items.append(rec)
        write_festivals(self.root, items)
        if members and joined:
            self.sync_gsk(rec)
        return fid

    def _comp(self, cid: str) -> tuple[CompFolder, Competition] | None:
        f = self.get(cid)
        try:
            return (f, f.load()) if f else None
        except Exception:  # noqa: BLE001 — карточка не читается: ГСК этого соревнования не трогаем
            return None

    def _join_festival(self, rec: dict, joined: list[str], restored: bool = False) -> None:
        """Соревнование в фестиваль: ГСК фестиваля — из самого раннего, своя замена — «своя», бригада и номера — по
        режимам."""
        loaded = {m: x for m in rec["members"] if (x := self._comp(m))}
        if not rec.get("officials"):  # ГСК фестиваля — из самого раннего соревнования
            first = min((c for _, c in loaded.values() if c.officials), key=lambda c: c.date_from, default=None)
            rec["officials"] = [fv.official_dict(o) for o in first.officials] if first else []
        fest_off = fv.officials(rec)
        own = rec.setdefault("own_gsk", {})
        extras = [rec.get("brigade", [])]
        for m in joined:
            if m not in loaded:
                continue
            f, comp = loaded[m]
            roles = list(dict.fromkeys(own.get(m, []) + fv.differing_roles(fest_off, comp.officials)))
            if roles:
                own[m] = roles
            raw = f.read_json(CONTRACTS)
            extras.append(raw.get("extra", []))
            if fv.mode(rec, "contracts") == "festival" and not restored:
                rec["contracts"] = fv.merge_contracts([rec.get("contracts", {}), raw])
        rec["brigade"] = fv.union_brigade(extras)

    def _leave_festival(self, rec: dict, cid: str) -> None:
        """Соревнование вышло из фестиваля: ГСК в его карточке уже полная, бригада фестиваля — в его договоры."""
        rec.get("own_gsk", {}).pop(cid, None)
        f = self.get(cid)
        if f is None:
            return
        raw = f.read_json(CONTRACTS)
        raw["extra"] = fv.union_brigade([raw.get("extra", []), rec.get("brigade", [])])
        f.write_json(CONTRACTS, raw)

    def sync_gsk(self, rec: dict) -> list[str]:
        """Записать действующую ГСК (фестиваля и свои замены) в карточки соревнований фестиваля. Возвращает
        соревнования, чьи карточки записать не удалось (открыты в Excel)."""
        from dataclasses import replace

        locked = []
        for m in rec.get("members", []):
            x = self._comp(m)
            if x is None:
                continue
            f, comp = x
            new = fv.effective(rec, m, comp.officials)
            if new != comp.officials:
                try:
                    f.save(replace(comp, officials=new))
                except PermissionError:
                    locked.append(comp.title)
        return locked

    @property
    def own_values_path(self) -> Path:
        return self.docs_root / OWN_VALUES

    def own_values(self) -> dict:
        """Запомненное на этом компьютере: {"groups": […], "names": […], "disciplines": [{name, result, unit}]}."""
        try:
            data = json.loads(self.own_values_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        data = data if isinstance(data, dict) else {}
        out = {k: [x for x in data.get(k, []) if isinstance(x, str) and x] for k in ("groups", "names")}
        out["disciplines"] = [d for d in data.get("disciplines", []) if isinstance(d, dict) and d.get("name")]
        return out

    def _save_own(self, data: dict) -> None:
        self.docs_root.mkdir(parents=True, exist_ok=True)
        save_json(self.own_values_path, data)

    def remember_own(self, comp: Competition, known_groups=()) -> None:
        """Карточку неофициальных соревнований сохранили — запомнить свои группы, названия зачётов, дисциплины."""
        if not comp.unofficial:
            return
        data = self.own_values()
        known = {g.upper() for g in known_groups}
        changed = False
        for z in comp.zachety:
            if z.group and z.group.upper() not in known and z.group not in data["groups"]:
                data["groups"].append(z.group)
                changed = True
            if z.name and z.name not in data["names"]:
                data["names"].append(z.name)
                changed = True
            if z.is_custom:
                d = {"name": z.discipline_text, "result": z.result, "unit": z.unit}
                old = next((x for x in data["disciplines"] if x["name"] == z.discipline_text), None)
                if old != d:
                    data["disciplines"] = [x for x in data["disciplines"] if x["name"] != z.discipline_text] + [d]
                    changed = True
        if changed:
            self._save_own({k: v[-200:] for k, v in data.items()})

    def forget_own(self, kind: str, value: str) -> None:
        """Убрать лишнее из запомненного."""
        if kind not in OWN_KINDS:
            return
        data = self.own_values()
        data[kind] = [x for x in data[kind] if (x.get("name") if isinstance(x, dict) else x) != value]
        self._save_own(data)

    def save_personal(self, key: str, values: dict) -> None:
        data = self.personal()
        data[key] = {k: v for k, v in values.items() if v}
        self.docs_root.mkdir(parents=True, exist_ok=True)
        save_json(self.personal_path, data)

    def contracts_dir(self, f: CompFolder) -> Path:
        """Договоры и табель соревнования — там же, где сканы: в них паспорта и счета. Один договор на весь
        фестиваль — в папке фестиваля."""
        fest = f.festival()
        if fv.mode(fest, "contracts") == "festival":
            return self.docs_root / safe_name(f"Фестиваль {fest.get('title', '')}") / CONTRACTS_DIR
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
