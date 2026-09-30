"""Обновления: есть ли на GitHub выпуск новее этой версии; переносная версия ставит его сама.

При запуске программа один раз спрашивает у GitHub, какая версия последняя (api.github.com, в фоне, ответа ждёт
не дольше 5 секунд). Отправляется только сам запрос — ни соревнований, ни имён, ни файлов. Нет интернета —
ничего не показывается, работе это не мешает. Отключить: st-secretary web --no-update-check (или переменная
окружения ST_NO_UPDATE_CHECK=1).

Переносная версия (запуск через «СТ-Секретарь.bat») обновляется кнопкой на главной странице:
1. резервная копия соревнований, где что-то менялось с прошлой копии (backup.auto);
2. архив выпуска скачивается только с github.com/avtomatikalab/st-secretary и сверяется с контрольной суммой
   sha256, которую GitHub публикует для каждого файла выпуска; не совпала — ничего не ставится;
3. папка program из архива распаковывается рядом как program.new, в ней проверяется номер версии;
4. программа выключается с кодом RESTART, «СТ-Секретарь.bat» переименовывает program → program.old и
   program.new → program и запускает новую версию; открытая страница сама переходит на неё.
Данные (папки «данные», «Резервные копии») обновление не трогает. Прежняя версия остаётся в program.old — откат
вручную (см. «Прочтите меня»). Сам «СТ-Секретарь.bat» обновление не меняет: его читает работающая консоль.
Если программа запущена из исходников (git), предлагается только ссылка на выпуск.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import sys
import threading
import urllib.request
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from st_secretary import __version__

log = logging.getLogger("st_secretary.updates")

REPO = "avtomatikalab/st-secretary"
LATEST_API = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_PAGE = f"https://github.com/{REPO}/releases"
DOWNLOADS = f"https://github.com/{REPO}/releases/download/"  # архив скачивается только отсюда
ASSET_SUFFIX = "-windows.zip"  # архив переносной версии среди файлов выпуска
RESTART = 75  # код выхода «перезапустить»: «СТ-Секретарь.bat» ставит program.new и запускает программу снова
TIMEOUT = 5  # секунд на ответ GitHub при проверке


class UpdateError(Exception):
    """Обновление не поставилось; текст — для человека."""


def parse_version(text: str) -> tuple[int, ...] | None:
    """«v0.1.0» → (0, 1, 0); предварительные выпуски («0.2.0-rc1») и непонятное — None."""
    m = re.fullmatch(r"v?(\d+(?:\.\d+)*)", (text or "").strip())
    return tuple(int(x) for x in m.group(1).split(".")) if m else None


def is_newer(candidate: str, current: str) -> bool:
    a, b = parse_version(candidate), parse_version(current)
    if a is None or b is None:
        return False
    n = max(len(a), len(b))
    return a + (0,) * (n - len(a)) > b + (0,) * (n - len(b))


@dataclass
class Release:
    version: str
    page: str  # страница выпуска на GitHub
    notes: str = ""  # что нового — текст выпуска
    published: datetime | None = None
    asset_url: str = ""  # архив переносной версии для Windows
    asset_size: int = 0
    sha256: str = ""  # контрольная сумма архива, которую публикует GitHub

    @property
    def installable(self) -> bool:
        """Можно поставить кнопкой: архив лежит в выпусках проекта и у него есть контрольная сумма."""
        return self.asset_url.startswith(DOWNLOADS) and bool(re.fullmatch(r"[0-9a-f]{64}", self.sha256))


def from_api(data: dict) -> Release | None:
    """Ответ GitHub (releases/latest) → Release; черновики и предварительные выпуски не предлагаются."""
    if data.get("draft") or data.get("prerelease"):
        return None
    version = str(data.get("tag_name") or "").removeprefix("v")
    if parse_version(version) is None:
        return None
    rel = Release(version, str(data.get("html_url") or RELEASES_PAGE), str(data.get("body") or "").strip())
    try:
        rel.published = datetime.fromisoformat(str(data["published_at"]))
    except (KeyError, ValueError):
        pass
    for a in data.get("assets") or []:
        if str(a.get("name", "")).endswith(ASSET_SUFFIX):
            digest = str(a.get("digest") or "")
            rel.asset_url = str(a.get("browser_download_url") or "")
            rel.asset_size = int(a.get("size") or 0)
            rel.sha256 = digest.removeprefix("sha256:").lower() if digest.startswith("sha256:") else ""
            break
    return rel


def _open(url: str, timeout: float, opener, accept: str):
    req = urllib.request.Request(url, headers={"Accept": accept, "User-Agent": f"st-secretary/{__version__}"})
    return opener(req, timeout=timeout)


def latest(url: str = LATEST_API, timeout: float = TIMEOUT, opener=urllib.request.urlopen) -> Release | None:
    """Последний выпуск на GitHub; None — нет интернета, GitHub не ответил или выпусков ещё нет."""
    try:
        with _open(url, timeout, opener, "application/vnd.github+json") as r:
            return from_api(json.load(r))
    except Exception as e:  # noqa: BLE001 — без интернета проверка просто молчит
        log.info("Проверка обновлений не удалась: %s", e)
        return None


def check(current: str = __version__, **kw) -> Release | None:
    """Выпуск новее текущей версии или None."""
    rel = latest(**kw)
    return rel if rel and is_newer(rel.version, current) else None


def portable_root(executable: str = sys.executable, env=os.environ) -> Path | None:
    """Папка переносной версии (где «СТ-Секретарь.bat»), если программа запущена из неё; иначе None."""
    if not env.get("ST_PORTABLE"):
        return None
    exe = Path(executable).resolve()
    if exe.parent.name.lower() != "python" or exe.parent.parent.name.lower() != "program":
        return None
    return exe.parents[2]


# ------------------------------------------------------------------ установка (переносная версия)


def download(rel: Release, dest: Path, progress: Callable[[int, int], None] | None = None,
             opener=urllib.request.urlopen) -> Path:
    """Архив выпуска → dest, со сверкой sha256; не совпало — файла нет, UpdateError."""
    if not rel.installable:
        raise UpdateError("у этого выпуска нет архива с контрольной суммой — скачайте его со страницы выпуска")
    tmp = dest.with_name(dest.name + ".part")
    h, done = hashlib.sha256(), 0
    try:
        with _open(rel.asset_url, 60, opener, "application/octet-stream") as r, tmp.open("wb") as out:
            total = int(r.headers.get("Content-Length") or rel.asset_size or 0)
            while block := r.read(1 << 16):
                out.write(block)
                h.update(block)
                done += len(block)
                if progress:
                    progress(done, total)
        if h.hexdigest() != rel.sha256:
            raise UpdateError("архив скачался с ошибкой (не совпала контрольная сумма) — попробуйте ещё раз")
        tmp.replace(dest)
    finally:
        tmp.unlink(missing_ok=True)
    return dest


def _version_in(program: Path) -> str:
    init = program / "python" / "Lib" / "site-packages" / "st_secretary" / "__init__.py"
    m = re.search(r'__version__\s*=\s*"([^"]+)"', init.read_text(encoding="utf-8")) if init.is_file() else None
    return m.group(1) if m else ""


def unpack(archive: Path, root: Path, version: str) -> Path:
    """Папка program из архива → root/program.new (её «СТ-Секретарь.bat» поставит при перезапуске); рядом —
    новые «Прочтите меня», инструкция и лицензия. Файл запуска (.bat) не трогается."""
    part, new = root / "program.new.part", root / "program.new"
    for d in (part, new):
        if d.exists():
            shutil.rmtree(d)
    docs: list[tuple[zipfile.ZipInfo, Path]] = []
    try:
        with zipfile.ZipFile(archive) as z:
            files = [i for i in z.infolist() if not i.is_dir()]
            tops = {i.filename.split("/", 1)[0] for i in files}
            if len(tops) != 1:
                raise UpdateError("в архиве не одна папка программы — это не архив СТ-Секретаря")
            top = tops.pop() + "/"
            for info in files:
                parts = info.filename[len(top):].split("/")
                if any(p in ("", ".", "..") or ":" in p or "\\" in p for p in parts):
                    raise UpdateError(f"в архиве странный путь «{info.filename}» — ничего не поставлено")
                if parts[0] == "program" and len(parts) > 1:
                    target = part.joinpath(*parts[1:])
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with z.open(info) as src, target.open("wb") as out:
                        shutil.copyfileobj(src, out)
                elif len(parts) == 1 and not parts[0].lower().endswith((".bat", ".cmd", ".exe")):
                    docs.append((info, root / parts[0]))
            found = _version_in(part)
            if not (part / "python" / "python.exe").is_file() or found != version:
                raise UpdateError(f"в архиве не та версия программы ({found or 'не найдена'} вместо {version})")
            part.rename(new)
            for info, target in docs:  # инструкция может быть открыта — тогда останется прежняя
                try:
                    with z.open(info) as src, target.open("wb") as out:
                        shutil.copyfileobj(src, out)
                except OSError as e:
                    log.warning("Не обновлён %s: %s", target.name, e)
    finally:
        if part.exists():
            shutil.rmtree(part, ignore_errors=True)
    return new


class Installer:
    """Скачивание и распаковка в фоне; страница обновления спрашивает, как идёт (state, done, total, error)."""

    BUSY = ("backup", "download", "unpack")

    def __init__(self, root: Path):
        self.root = root
        self.state = "idle"  # idle → backup → download → unpack → ready | error
        self.done = self.total = 0
        self.error = ""
        self.version = ""
        self._lock = threading.Lock()

    def start(self, rel: Release, before: Callable[[], None] | None = None, opener=urllib.request.urlopen,
              wait: bool = False) -> bool:
        """before — что сделать до скачивания (резервная копия); False — установка уже идёт или готова."""
        with self._lock:
            if self.state in (*self.BUSY, "ready"):
                return False
            self.state, self.done, self.total, self.error, self.version = "backup", 0, rel.asset_size, "", rel.version
        t = threading.Thread(target=self._run, args=(rel, before, opener), daemon=True)
        t.start()
        if wait:
            t.join()
        return True

    def _progress(self, done: int, total: int) -> None:
        self.done, self.total = done, total

    def _run(self, rel: Release, before, opener) -> None:
        archive = self.root / "program.new.zip"
        try:
            if before:
                before()
            self.state = "download"
            download(rel, archive, self._progress, opener)
            self.state = "unpack"
            unpack(archive, self.root, rel.version)
            self.state = "ready"
        except Exception as e:  # noqa: BLE001 — показать на странице, программа продолжает работать
            log.warning("Обновление не поставлено: %s", e)
            self.error = str(e) if isinstance(e, UpdateError) else f"{type(e).__name__}: {e}"
            self.state = "error"
        finally:
            archive.unlink(missing_ok=True)

    def status(self) -> dict:
        return {"state": self.state, "done": self.done, "total": self.total, "error": self.error,
                "version": self.version}
