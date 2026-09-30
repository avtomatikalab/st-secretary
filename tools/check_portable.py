"""Проверка собранной переносной версии на той системе, для которой она собрана (GitHub Actions: Windows, macOS,
Linux; можно и вручную).

    uv run python tools/check_portable.py dist/st-secretary-<система>.(zip|tar.gz)

1. Архив распаковывается во временную папку — как у секретаря.
2. Переносной Python из архива распаковывает тот же архив как обновление (updates.unpack → program.new).
3. Файл запуска (.bat, .command, .sh) ставит program.new (прежняя — program.old) и запускает программу.
4. Программа отвечает, открываются главная и учебное соревнование; кнопка «Выключить» её останавливает.
5. macOS и Linux: установка одной командой (tools/install.sh) из этого же архива — и повторно, как обновление.
Реальных данных нет: учебное соревнование — выдуманное, папки данных и документов — временные.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NAME = "СТ-Секретарь"
PORT = 8799


def say(text: str) -> None:
    print(f"== {text}", flush=True)


def fail(text: str) -> None:
    raise SystemExit(f"ОШИБКА: {text}")


def extract(archive: Path, dest: Path) -> Path:
    if archive.name.endswith(".zip"):
        with zipfile.ZipFile(archive) as z:
            z.extractall(dest)
    else:
        with tarfile.open(archive) as t:
            t.extractall(dest, filter="data")
    return dest / NAME


def python_in(root: Path) -> Path:
    for p in (root / "program" / "python" / "python.exe", root / "program" / "python" / "bin" / "python3"):
        if p.is_file():
            return p
    fail(f"в {root} нет program/python")
    raise AssertionError


def get(path: str, method: str = "GET", timeout: float = 5) -> tuple[int, str]:
    req = urllib.request.Request(f"http://127.0.0.1:{PORT}{path}", method=method, data=b"" if method == "POST" else None)
    with urllib.request.urlopen(req, timeout=timeout) as r:  # только 127.0.0.1
        return r.status, r.read().decode("utf-8", "replace")


def wait_health(version: str, proc: subprocess.Popen, log: Path, seconds: int = 120) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if proc.poll() is not None:
            fail(f"программа завершилась с кодом {proc.returncode}:\n{log.read_text(errors='replace')}")
        try:
            health = json.loads(get("/health", timeout=2)[1])
            if health.get("version") == version:
                return
        except OSError:
            pass
        time.sleep(1)
    fail(f"программа не ответила за {seconds} с:\n{log.read_text(errors='replace')}")


def launch(root: Path, docs: Path, log: Path) -> subprocess.Popen:
    args = ["--no-browser", "--no-update-check", "--port", str(PORT), "--docs", str(docs)]
    if sys.platform.startswith("win"):
        cmd = ["cmd", "/c", str(root / f"{NAME}.bat"), *args]
    else:
        launcher = next(root.glob(f"{NAME}.command"), None) or root / f"{NAME}.sh"
        if not os.access(launcher, os.X_OK):
            fail(f"{launcher.name} без права на запуск")
        cmd = [str(launcher), *args]
    return subprocess.Popen(cmd, cwd=root, stdin=subprocess.DEVNULL, stdout=log.open("wb"), stderr=subprocess.STDOUT)


def main() -> None:
    found = [Path(a).resolve() for a in sys.argv[1:] if Path(a).is_file()]  # в CI — шаблоны для всех систем
    if len(found) != 1:
        fail(f"нужен ровно один архив, а найдено: {found or sys.argv[1:]}")
    archive = found[0]
    init = (ROOT / "src" / "st_secretary" / "__init__.py").read_text(encoding="utf-8")
    version = re.search(r'__version__\s*=\s*"([^"]+)"', init).group(1)  # архив собран из этих исходников
    work = Path(tempfile.mkdtemp(prefix="st-check-"))
    try:
        say(f"распаковываю {archive.name}")
        root = extract(archive, work / "unpacked")
        py = python_in(root)

        say("переносной Python ставит обновление из архива (program.new)")
        code = ("import sys; from pathlib import Path; from st_secretary import updates; "
                "print(updates.unpack(Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]))")
        subprocess.run([str(py), "-c", code, str(archive), str(root), version], check=True)
        if not (root / "program.new").is_dir():
            fail("program.new не появилась")

        say("файл запуска ставит новую версию и запускает программу")
        log = work / "launcher.log"
        proc = launch(root, work / "docs", log)
        try:
            wait_health(version, proc, log)
            if (root / "program.new").exists() or not (root / "program.old").is_dir():
                fail(f"файл запуска не поменял папки: {sorted(p.name for p in root.iterdir())}")
            status, html = get("/")
            if status != 200 or NAME not in html:
                fail("главная страница не открылась")
            status, html = get("/training", method="POST", timeout=30)
            if status != 200 or "Учебный" not in html:
                fail("учебное соревнование не создалось")
            say("страницы открываются, выключаю кнопкой «Выключить»")
            get("/shutdown", method="POST")
            proc.wait(timeout=60)
        finally:
            if proc.poll() is None:
                proc.kill()
        print(log.read_text(errors="replace"))
        if proc.returncode != 0:
            fail(f"файл запуска завершился с кодом {proc.returncode}")

        if not sys.platform.startswith("win"):
            say("установка одной командой (tools/install.sh) из этого архива")
            dest = work / "home" / NAME
            env = {**os.environ, "ST_INSTALL_ARCHIVE": str(archive), "ST_INSTALL_DIR": str(dest),
                   "ST_INSTALL_NO_START": "1", "HOME": str(work / "home")}
            (work / "home").mkdir()
            for attempt in ("установка", "обновление"):
                r = subprocess.run(["sh", str(ROOT / "tools" / "install.sh")], env=env, capture_output=True, text=True,
                                   check=False)
                print(r.stdout, r.stderr)
                if r.returncode != 0:
                    fail(f"install.sh ({attempt}) завершился с кодом {r.returncode}")
            python_in(dest)
            if not (dest / "program.old").is_dir():
                fail("повторная установка не сохранила прежнюю версию в program.old")
            r = subprocess.run([str(python_in(dest)), "-c", "import st_secretary; print(st_secretary.__version__)"],
                               capture_output=True, text=True, check=True)
            if r.stdout.strip() != version:
                fail(f"после установки версия {r.stdout.strip()}, а не {version}")
        say(f"всё в порядке: {archive.name}")
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    main()
