"""Обновления: есть ли на GitHub выпуск новее этой версии; переносная версия ставит его сама.

При запуске программа один раз спрашивает у GitHub, какая версия последняя (api.github.com, в фоне, ответа ждёт
не дольше 5 секунд). Отправляется только сам запрос — ни соревнований, ни имён, ни файлов. Нет интернета —
ничего не показывается, работе это не мешает. Отключить: st-secretary web --no-update-check (или переменная
окружения ST_NO_UPDATE_CHECK=1).

Переносная версия (файл запуска «СТ-Секретарь.bat» в Windows, «СТ-Секретарь.command» в macOS,
«СТ-Секретарь.sh» в Linux) обновляется кнопкой на главной странице:
1. резервная копия соревнований, где что-то менялось с прошлой копии (backup.auto);
2. архив выпуска для своей системы скачивается только с github.com/avtomatikalab/st-secretary и сверяется с
   контрольной суммой sha256, которую GitHub публикует для каждого файла выпуска; не совпала — ничего не ставится;
3. папка program из архива распаковывается рядом как program.new, в ней проверяется номер версии;
4. программа выключается с кодом RESTART, файл запуска переименовывает program → program.old и
   program.new → program и запускает новую версию; открытая страница сама переходит на неё.
Данные (папки «данные», «Резервные копии») обновление не трогает. Прежняя версия остаётся в program.old — откат
вручную (см. «Прочтите меня»). Сам файл запуска обновление не меняет: его построчно читает работающая консоль.
Если программа запущена из исходников (git), предлагается только ссылка на выпуск.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import platform
import re
import shutil
import sys
import tarfile
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
# архивы переносной версии среди файлов выпуска: (система, процессор) → конец имени файла
ASSETS = {
    ("win32", "x86_64"): "-windows.zip",
    ("win32", "arm64"): "-windows.zip",  # Windows на ARM запускает x86_64 сам
    ("darwin", "arm64"): "-macos-arm64.tar.gz",
    ("darwin", "x86_64"): "-macos-x86_64.tar.gz",
    ("linux", "x86_64"): "-linux-x86_64.tar.gz",
}
LAUNCHERS = (".bat", ".cmd", ".exe", ".command", ".sh")  # файлы запуска обновление не трогает
RESTART = 75  # код выхода «перезапустить»: файл запуска ставит program.new и запускает программу снова
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
    asset_url: str = ""  # архив переносной версии для этой системы
    asset_size: int = 0
    sha256: str = ""  # контрольная сумма архива, которую публикует GitHub

    @property
    def installable(self) -> bool:
        """Можно поставить кнопкой: архив лежит в выпусках проекта и у него есть контрольная сумма."""
        return self.asset_url.startswith(DOWNLOADS) and bool(re.fullmatch(r"[0-9a-f]{64}", self.sha256))


def asset_suffix(system: str = sys.platform, machine: str = platform.machine()) -> str | None:
    """Конец имени архива для этой системы; None — готового архива для неё нет (только из исходников)."""
    system = "win32" if system.startswith("win") else "linux" if system.startswith("linux") else system
    arch = {"amd64": "x86_64", "x64": "x86_64", "aarch64": "arm64"}.get(machine.lower(), machine.lower())
    return ASSETS.get((system, arch))


def from_api(data: dict, suffix: str | None = None) -> Release | None:
    """Ответ GitHub (releases/latest) → Release с архивом для этой системы (suffix — для другой);
    черновики и предварительные выпуски не предлагаются."""
    suffix = suffix or asset_suffix()
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
        if suffix and str(a.get("name", "")).endswith(suffix):
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
    """Папка переносной версии (где файл запуска), если программа запущена из неё; иначе None.
    Python в ней: program/python/python.exe (Windows) или program/python/bin/python3 (macOS, Linux)."""
    if not env.get("ST_PORTABLE"):
        return None
    py = Path(os.path.abspath(executable)).parent  # без resolve: в macOS и Linux python3 — ссылка
    if py.name.lower() == "bin":
        py = py.parent
    if py.name.lower() != "python" or py.parent.name.lower() != "program":
        return None
    return py.parent.parent


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


def _python_in(program: Path) -> Path | None:
    for exe in (program / "python" / "python.exe", program / "python" / "bin" / "python3"):
        if exe.is_file():
            return exe
    return None


def _version_in(program: Path) -> str:
    """Номер версии программы в папке program (Windows: python/Lib/…, macOS и Linux: python/lib/python3.X/…)."""
    for init in [program / "python" / "Lib" / "site-packages" / "st_secretary" / "__init__.py",
                 *(program / "python" / "lib").glob("python3*/site-packages/st_secretary/__init__.py")]:
        if init.is_file():
            m = re.search(r'__version__\s*=\s*"([^"]+)"', init.read_text(encoding="utf-8"))
            return m.group(1) if m else ""
    return ""


def _extract(archive: Path, dest: Path) -> None:
    """Весь архив (.zip или .tar.gz) → dest; пути и ссылки — только внутрь dest, иначе UpdateError."""
    if zipfile.is_zipfile(archive):
        with zipfile.ZipFile(archive) as z:
            for info in z.infolist():
                parts = info.filename.rstrip("/").split("/")
                if any(p in ("", ".", "..") or ":" in p or "\\" in p for p in parts):
                    raise UpdateError(f"в архиве странный путь «{info.filename}» — ничего не поставлено")
            z.extractall(dest)
    elif tarfile.is_tarfile(archive):
        with tarfile.open(archive) as t:
            try:
                t.extractall(dest, filter="data")  # права на запуск сохраняются, ссылки наружу — запрещены
            except tarfile.FilterError as e:
                raise UpdateError(f"в архиве странный путь «{e.tarinfo.name}» — ничего не поставлено") from e
    else:
        raise UpdateError("скачанный файл — не архив программы")


def unpack(archive: Path, root: Path, version: str) -> Path:
    """Папка program из архива → root/program.new (её поставит файл запуска при перезапуске); рядом — новые
    «Прочтите меня», инструкция и лицензия. Файл запуска не трогается: его читает работающая консоль."""
    tmp, new = root / "program.update.tmp", root / "program.new"
    for d in (tmp, new):
        if d.exists():
            shutil.rmtree(d)
    try:
        _extract(archive, tmp)
        tops = list(tmp.iterdir())
        if len(tops) != 1 or not tops[0].is_dir():
            raise UpdateError("в архиве не одна папка программы — это не архив СТ-Секретаря")
        program = tops[0] / "program"
        found = _version_in(program)
        if _python_in(program) is None or found != version:
            raise UpdateError(f"в архиве не та версия программы ({found or 'не найдена'} вместо {version})")
        program.rename(new)
        for f in tops[0].iterdir():  # инструкция может быть открыта — тогда останется прежняя
            if f.is_file() and not f.name.lower().endswith(LAUNCHERS):
                try:
                    shutil.copyfile(f, root / f.name)
                except OSError as e:
                    log.warning("Не обновлён %s: %s", f.name, e)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
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
        """Установка в фоне: скачать архив со сверкой sha256, распаковать рядом как program.new, подготовить
        перезапуск; ошибка — понятным текстом."""
        archive = self.root / ("program.new" + (".tar.gz" if rel.asset_url.endswith(".tar.gz") else ".zip"))
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


# ------------------------------------------------------------------ из исходников (git): разработчик, два компьютера


def source_root(start: str | Path = __file__) -> Path | None:
    """Папка исходников с git, если программа запущена из них (не переносная); иначе None."""
    for p in Path(start).resolve().parents:
        if (p / ".git").exists() and (p / "pyproject.toml").is_file():
            return p
    return None


@dataclass
class Commit:
    hash: str
    when: str  # «30.09.2026 23:56»
    subject: str


_FMT = "--format=%h%x1f%cd%x1f%s"
_DATE = "--date=format:%d.%m.%Y %H:%M"


def _git(root: Path, *args: str, run=None, timeout: float = 30) -> tuple[int, str, str]:
    import subprocess

    run = run or subprocess.run
    try:
        r = run(["git", "-C", str(root), *args], capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=timeout, check=False)
    except (OSError, subprocess.SubprocessError) as e:  # git нет или не ответил
        return 1, "", str(e)
    return r.returncode, (r.stdout or "").strip(), (r.stderr or "").strip()


def _commit(out: str) -> Commit | None:
    parts = out.split("\x1f")
    return Commit(*parts[:3]) if len(parts) >= 3 else None


def git_head(root: Path, run=None) -> Commit | None:
    code, out, _ = _git(root, "log", "-1", _FMT, _DATE, run=run)
    return _commit(out) if code == 0 else None


def git_upstream(root: Path, run=None) -> str:
    """Ветка на GitHub, с которой сверяться: upstream текущей ветки, иначе origin/main."""
    code, out, _ = _git(root, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}", run=run)
    return out if code == 0 and out else "origin/main"


@dataclass
class GitNews:
    behind: int  # сколько коммитов на GitHub новее загруженного
    last: Commit | None  # последний из них
    upstream: str


def git_check(root: Path, run=None) -> GitNews | None:
    """git fetch и сколько коммитов на GitHub новее; None — нет интернета или git не ответил."""
    code, _, _ = _git(root, "fetch", "--quiet", run=run, timeout=60)
    if code != 0:
        return None
    up = git_upstream(root, run)
    code, out, _ = _git(root, "rev-list", "--count", f"HEAD..{up}", run=run)
    if code != 0 or not out.isdigit():
        return None
    n = int(out)
    last = None
    if n:
        c, o, _ = _git(root, "log", "-1", _FMT, _DATE, up, run=run)
        last = _commit(o) if c == 0 else None
    return GitNews(n, last, up)


def git_update(root: Path, run=None, uv: str | None = None) -> tuple[bool, str]:
    """Обновить исходники только «вперёд» (fast-forward) и библиотеки (uv sync). Есть несохранённые изменения или
    история разошлась — ничего не трогать и сказать, что сделать. (ok, текст для окна программы)"""
    code, out, err = _git(root, "status", "--porcelain", "--untracked-files=no", run=run)
    if code != 0:
        return False, f"git не отвечает ({err[:100]}) — обновите вручную: git pull"
    if out:
        return False, ("в папке программы есть несохранённые изменения (git status) — обновление не трогаю: "
                       "сохраните (commit) или отмените их и обновите вручную: git pull --rebase")
    up = git_upstream(root, run)
    code, _, err = _git(root, "merge", "--ff-only", up, run=run, timeout=120)
    if code != 0:
        return False, (f"здесь есть свои коммиты, которых нет на GitHub (история разошлась) — ничего не трогаю: "
                       f"git pull --rebase вручную ({err.splitlines()[-1][:100] if err else up})")
    if uv:
        import subprocess

        try:
            (run or subprocess.run)([uv, "sync"], cwd=root, capture_output=True, timeout=900, check=False)
        except (OSError, subprocess.SubprocessError):
            pass  # библиотеки подтянутся при следующем «uv run»
    head = git_head(root, run)
    return True, f"обновлено до коммита {head.hash}: «{head.subject}»" if head else "обновлено"
