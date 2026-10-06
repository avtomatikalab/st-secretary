"""«Сообщить»: ошибки, неудобства и предложения пользователей — файлами для разработчика (Правки, п. 43; решение 044).

Папка «Правки и ошибки» рядом с папкой «данные» (в резервные копии и облако не входит). Одно сообщение — два файла
с общим именем: `2026-10-01 15-42-07 Ошибка.md` (шапка «ключ: значение» и разделы — читают и человек, и Claude) и
`….png` (снимок с пометками, если есть). «Отправить разработчику» собирает неотправленные в один zip с
`manifest.json` и открывает письмо на SUPPORT_EMAIL; «Загрузить файл сообщений» добавляет чужой zip без повторов.
"""

from __future__ import annotations

import hashlib
import io
import json
import platform
import re
import shutil
import socket
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

SUPPORT_EMAIL = "support.st.secretary@gmail.com"  # сюда присылают сообщения (п. 43, 45); менять — только здесь
MAIL_SUBJECT = "СТ-Секретарь: сообщения"  # по началу темы письма их находит ежедневная проверка (п. 45)
KINDS = ("Ошибка", "Неудобно", "Предложение")
FOLDER = "Правки и ошибки"
REMOVED = "Убранные"
OUTBOX = "Отправка"
NEW, SENT = "новое", "отправлено"
HEADER = ("Номер", "Статус", "Кто", "Компьютер", "Получено с", "Соревнование", "Страница", "Место", "Версия",
          "Браузер", "Окно", "Снимок")


def folder_for(data_dir: str | Path) -> Path:
    """Папка сообщений — рядом с папкой «данные»."""
    return Path(data_dir).resolve().parent / FOLDER


def computer() -> str:
    return socket.gethostname() or platform.node() or "компьютер"


def _host_tag(host: str) -> str:
    return hashlib.sha1(host.encode("utf-8")).hexdigest()[:4]


def _one_line(s) -> str:
    return " ".join(str(s or "").split())[:300]


@dataclass
class Message:
    path: Path
    head: dict[str, str]
    sections: dict[str, str] = field(default_factory=dict)
    title: str = ""

    @property
    def number(self) -> str:
        return self.head.get("Номер", self.path.stem)

    @property
    def status(self) -> str:
        return self.head.get("Статус", NEW)

    @property
    def sent(self) -> bool:
        return self.status != NEW

    @property
    def kind(self) -> str:
        return next((k for k in KINDS if self.path.stem.endswith(k)), KINDS[0])

    @property
    def when(self) -> str:
        """«01.10.2026 15:42» — из имени файла."""
        try:
            return datetime.strptime(self.path.stem[:19], "%Y-%m-%d %H-%M-%S").strftime("%d.%m.%Y %H:%M")
        except ValueError:
            return ""

    @property
    def png(self) -> Path | None:
        p = self.path.with_suffix(".png")
        return p if p.is_file() else None

    @property
    def text(self) -> str:
        return self.sections.get("Что произошло", "")


def render(title: str, head: dict[str, str], sections: dict[str, str]) -> str:
    lines = [f"# {title}"]
    lines += [f"{k}: {_one_line(head[k])}" for k in HEADER if head.get(k)]
    for name, body in sections.items():
        lines += ["", f"## {name}", (body or "").rstrip() or "—"]
    return "\n".join(lines) + "\n"


def parse(path: Path) -> Message:
    """Файл сообщения → шапка и разделы (то, что пишет render)."""
    text = path.read_text(encoding="utf-8")
    title, head, sections, current = "", {}, {}, None
    for line in text.splitlines():
        if line.startswith("# ") and not title:
            title = line[2:].strip()
        elif line.startswith("## "):
            current = line[3:].strip()
            sections[current] = ""
        elif current is not None:
            sections[current] += line + "\n"
        elif (m := re.match(r"^([^:]{2,30}):\s?(.*)$", line)) and m.group(1) in HEADER:
            head[m.group(1)] = m.group(2).strip()
    sections = {k: v.strip() for k, v in sections.items()}
    return Message(path, head, sections, title)


def save(folder: Path, kind: str, text: str, meta: dict, png: bytes | None = None,
         now: datetime | None = None, host: str | None = None) -> Message:
    """Сохранить сообщение: .md (и .png). meta — собранное само: who, competition, page, place, version, browser, misses,
    window, page_title, path (список строк), errors (текст)."""
    now = now or datetime.now()
    host = host or computer()
    kind = kind if kind in KINDS else KINDS[0]
    folder.mkdir(parents=True, exist_ok=True)
    stem = f"{now:%Y-%m-%d %H-%M-%S} {kind}"
    n = 2
    while (folder / f"{stem}.md").exists():
        stem, n = f"{now:%Y-%m-%d %H-%M-%S} {kind} ({n})", n + 1
    number = f"{now:%Y%m%d-%H%M%S}-{_host_tag(host)}" + (f"-{n - 1}" if n > 2 else "")
    if png:
        (folder / f"{stem}.png").write_bytes(png)
    head = {"Номер": number, "Статус": NEW, "Кто": meta.get("who", ""), "Компьютер": host,
            "Соревнование": meta.get("competition", ""), "Страница": meta.get("page", ""),
            "Место": meta.get("place", ""), "Версия": meta.get("version", ""), "Браузер": meta.get("browser", ""),
            "Окно": meta.get("window", ""), "Снимок": f"{stem}.png" if png else ""}
    sections = {"Что произошло": text.strip(), "Перед этим": "\n".join(meta.get("path", [])),
                "Ошибки и журнал": meta.get("errors", "")}
    if meta.get("misses"):  # телефоны судей: поиск штрафа без результата (Правки, п. 62)
        sections["Судьи искали и не нашли"] = meta["misses"]
    title = f"{kind} · {meta.get('page_title') or 'страница'} · {now:%d.%m.%Y %H:%M}"
    path = folder / f"{stem}.md"
    path.write_text(render(title, head, sections), encoding="utf-8")
    return parse(path)


def messages(folder: Path) -> list[Message]:
    """Сообщения папки — новые сверху (убранные — в подпапке, их нет)."""
    if not folder.is_dir():
        return []
    out = []
    for p in folder.glob("*.md"):
        try:
            out.append(parse(p))
        except (OSError, UnicodeDecodeError):
            continue
    return sorted(out, key=lambda m: m.path.name, reverse=True)


def unsent_count(folder: Path) -> int:
    """Сколько сообщений ещё не отправлено разработчику (для главной и панели)."""
    return sum(1 for m in messages(folder) if not m.sent)


def set_status(m: Message, status: str) -> None:
    m.head["Статус"] = status
    m.path.write_text(render(m.title, m.head, m.sections), encoding="utf-8")


def remove(folder: Path, name: str) -> bool:
    """Убрать сообщение — в «Убранные» (не удалять)."""
    p = folder / Path(name).name
    if p.suffix != ".md" or not p.is_file():
        return False
    (folder / REMOVED).mkdir(exist_ok=True)
    for q in (p, p.with_suffix(".png")):
        if q.is_file():
            shutil.move(str(q), folder / REMOVED / q.name)
    return True


def _zip(chosen: list[Message], journal_tail: str, info: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for m in chosen:
            z.write(m.path, m.path.name)
            if m.png:
                z.write(m.png, m.png.name)
        if journal_tail:
            z.writestr("Журнал — последние записи.txt", journal_tail)
        manifest = {**info, "messages": [{"number": m.number, "file": m.path.name, "kind": m.kind,
                                          "image": m.png.name if m.png else ""} for m in chosen]}
        z.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=1))
    return buf.getvalue()


def bundle(folder: Path, journal_tail: str, version: str, now: datetime | None = None, host: str | None = None,
           numbers: list[str] | None = None) -> tuple[Path, list[Message]] | None:
    """«Отправить разработчику»: неотправленные (или numbers — «Собрать заново») — в один zip в «Отправка»; они
    помечаются «отправлено <дата>». None — отправлять нечего."""
    now = now or datetime.now()
    host = host or computer()
    allm = messages(folder)
    chosen = [m for m in allm if m.number in numbers] if numbers is not None else [m for m in allm if not m.sent]
    if not chosen:
        return None
    out = folder / OUTBOX
    out.mkdir(parents=True, exist_ok=True)
    safe_host = re.sub(r'[\\/:*?"<>|]', "-", host)
    path = out / f"СТ-Секретарь — сообщения {safe_host} {now:%Y-%m-%d %H-%M}.zip"
    info = {"app": "st-secretary", "version": version, "system": platform.platform(), "computer": host,
            "made": now.isoformat(timespec="seconds"), "subject": subject(len(chosen), version)}
    path.write_bytes(_zip(chosen, journal_tail, info))
    for m in chosen:
        if not m.sent:
            set_status(m, f"{SENT} {now:%d.%m.%Y}")
    (out / "последняя.json").write_text(json.dumps({"file": path.name, "numbers": [m.number for m in chosen]},
                                                   ensure_ascii=False), encoding="utf-8")
    return path, chosen


def last_bundle(folder: Path) -> dict | None:
    try:
        return json.loads((folder / OUTBOX / "последняя.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def all_zip(folder: Path, journal_tail: str, version: str) -> bytes:
    """«Скачать все одним файлом» — без пометок «отправлено»."""
    return _zip(messages(folder), journal_tail, {"app": "st-secretary", "version": version,
                                                 "system": platform.platform(), "computer": computer()})


def import_bundle(folder: Path, data: bytes) -> tuple[int, int]:
    """Присланный zip другого секретаря → в свою папку: (добавлено, уже было). Повтор — по номеру сообщения."""
    folder.mkdir(parents=True, exist_ok=True)
    have = {m.number for m in messages(folder)} | {m.number for m in messages(folder / REMOVED)}
    added = skipped = 0
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        try:
            manifest = json.loads(z.read("manifest.json").decode("utf-8"))
        except (KeyError, ValueError):
            manifest = {}
        source = str(manifest.get("computer", ""))
        for name in z.namelist():
            base = Path(name).name
            if not base.endswith(".md") or base != name:  # только сообщения в корне архива — без путей
                continue
            tmp = folder / f"~{base}"
            tmp.write_bytes(z.read(name))
            try:
                m = parse(tmp)
            except UnicodeDecodeError:
                tmp.unlink(missing_ok=True)
                continue
            if m.number in have or not m.head.get("Номер"):
                tmp.unlink(missing_ok=True)
                skipped += 1
                continue
            target = folder / base
            n = 2
            while target.exists():
                target, n = folder / f"{Path(base).stem} ({n}).md", n + 1
            if source:
                m.head["Получено с"] = source
            m.path = target
            target.write_text(render(m.title, m.head, m.sections), encoding="utf-8")
            tmp.unlink(missing_ok=True)
            png = Path(base).with_suffix(".png").name
            if png in z.namelist():
                target.with_suffix(".png").write_bytes(z.read(png))
            have.add(m.number)
            added += 1
    return added, skipped


def subject(n: int, version: str) -> str:
    return f"{MAIL_SUBJECT} — {n} шт., версия {version}"


def mailto(n: int, version: str, file_name: str) -> str:
    """Письмо разработчику: адрес, тема, текст «приложите файл» (вложение mailto не умеет)."""
    body = (f"Здравствуйте! Сообщения из СТ-Секретаря: {n} шт.\n\nПриложите к письму файл «{file_name}» из "
            "открывшейся папки (перетащите его в это письмо).\n")
    return f"mailto:{SUPPORT_EMAIL}?subject={quote(subject(n, version))}&body={quote(body)}"


def journal_tail(journal_folder: Path, lines: int = 30) -> str:
    """Последние строки журнала программы (журнал без паспортных данных)."""
    p = journal_folder / "СТ-Секретарь.log"
    try:
        return "\n".join(p.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:])
    except OSError:
        return ""
