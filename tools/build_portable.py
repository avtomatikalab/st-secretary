"""Переносная версия для Windows: один архив — распаковал, дважды щёлкнул, работает. Без uv и без интернета.

    uv run python tools/build_portable.py            → dist/СТ-Секретарь-<версия>-windows.zip

Внутри архива:
    СТ-Секретарь/
        СТ-Секретарь.bat      — запуск (двойной щелчок)
        Прочтите меня.txt     — как запустить, где данные, как обновить
        LICENSE.txt           — лицензия AGPL-3.0 (исходный код — на GitHub)
        program/              — Python (официальная сборка «embeddable» с python.org) и библиотеки
    Папка «данные» появляется рядом при первом запуске; в архиве её нет, поэтому обновление её не трогает.

Почему так, а не один .exe (PyInstaller): exe-упаковщики часто принимает за вирус антивирус, а у официального
python.exe подпись Python Software Foundation. Библиотеки ставятся по uv.lock — те же версии, что в тестах.
Файлы .pyc компилируются заранее и не сверяются с исходниками: программа ничего не пишет в свою папку, даже
если её распаковали в папку, которую синхронизирует облако.

Нужны uv и интернет — только на компьютере, где собирают архив.
"""

from __future__ import annotations

import compileall
import hashlib
import os
import py_compile
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY_VERSION = "3.12.10"  # последняя версия 3.12 со сборкой embeddable; та же 3.12, что в разработке
PY_URL = f"https://www.python.org/ftp/python/{PY_VERSION}/python-{PY_VERSION}-embed-amd64.zip"
PY_SHA256 = "4acbed6dd1c744b0376e3b1cf57ce906f9dc9e95e68824584c8099a63025a3c3"  # sha256 архива с python.org
CACHE = Path(os.environ.get("LOCALAPPDATA", tempfile.gettempdir())) / "st-secretary" / "build-cache"
NAME = "СТ-Секретарь"

LAUNCHER = r"""@echo off
rem ST-Secretary, portable version: double-click to open the program in the browser.
rem Competitions are stored in the "данные" folder next to this file. The "program" folder is Python with
rem the libraries - it is replaced as a whole on update. Paths here are ASCII so the file works with any
rem console code page; the messages are in CP866 (the Russian Windows console).
cd /d "%~dp0"
title СТ-Секретарь
set "ST_PORTABLE=1"
set "PYTHONDONTWRITEBYTECODE=1"
if not exist "program\python\python.exe" (
  echo.
  echo Не найдена папка program рядом с этим файлом.
  echo Распакуйте архив СТ-Секретаря целиком и запускайте файл из распакованной папки, а не из архива.
  echo.
  pause
  exit /b 1
)
echo Запускаю СТ-Секретарь...
echo.
"program\python\python.exe" -m st_secretary web
set "RC=%errorlevel%"
echo.
if not "%RC%"=="0" (
  echo Программа остановилась с ошибкой. Сообщение выше пригодится разработчикам.
)
echo Это окно можно закрыть.
pause
"""

README = """СТ-Секретарь {version} — программа для секретариата соревнований по спортивному туризму

КАК ЗАПУСТИТЬ
1. Распакуйте архив в любую папку, например в «Документы» (правой кнопкой по архиву → «Извлечь все…»).
   Запускать прямо из архива нельзя.
2. Дважды щёлкните «СТ-Секретарь.bat». Откроется чёрное окно — это сама программа — и страница в браузере.
   Если Windows спросит «Не удаётся проверить издателя» или «Windows защитила ваш компьютер» — нажмите
   «Запустить» (или «Подробнее» → «Выполнить в любом случае»).
3. Интернет не нужен. Выключить программу — кнопка «Выключить» вверху страницы или просто закройте чёрное окно.
   Удобно сделать ярлык: правой кнопкой по «СТ-Секретарь.bat» → «Отправить» → «Рабочий стол (создать ярлык)».

ГДЕ ДАННЫЕ
- Соревнования — папка «данные» рядом с этим файлом (появится после первого запуска). Её и сохраняйте в резерв.
- Сканы документов участников и паспортные данные судей — только на этом компьютере, в вашей папке
  пользователя: «СТ-Секретарь — документы участников». В облако и в папку соревнования они не попадают.

КАК ОБНОВИТЬ
Удалите старую папку «program» и распакуйте новый архив в ту же папку, согласившись заменить файлы.
Папка «данные» останется как была: в архиве её нет.

ТАБЛО И ТЕЛЕФОНЫ СУДЕЙ
При первом включении раздачи по Wi-Fi Windows спросит разрешение в брандмауэре для python.exe —
разрешите для частной сети.

Программа свободная (лицензия AGPL-3.0, файл LICENSE.txt), исходный код:
https://github.com/avtomatikalab/st-secretary
В папке program — Python {py} (лицензия PSF, program\\python\\LICENSE.txt) и библиотеки с их лицензиями.
"""


def run(*cmd: str) -> None:
    print("  $", " ".join(cmd[:6]), "…" if len(cmd) > 6 else "")
    subprocess.run(cmd, check=True, cwd=ROOT)


def fetch_python() -> Path:
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / Path(PY_URL).name
    if not path.is_file():
        print(f"Скачиваю {PY_URL}")
        tmp = path.with_suffix(".part")
        with urllib.request.urlopen(PY_URL, timeout=60) as r, tmp.open("wb") as out:  # только python.org
            shutil.copyfileobj(r, out)
        tmp.replace(path)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if PY_SHA256 and digest != PY_SHA256:
        path.unlink()
        raise SystemExit(f"Архив Python не совпадает с ожидаемым (sha256 {digest}) — удалён, запустите сборку снова")
    print(f"Python {PY_VERSION} embeddable, sha256 {digest}")
    return path


def version() -> str:
    ns: dict = {}
    exec((ROOT / "src" / "st_secretary" / "__init__.py").read_text(encoding="utf-8"), ns)  # noqa: S102
    return ns["__version__"]


def build(out_dir: Path) -> Path:
    uv = shutil.which("uv")
    if not uv:
        raise SystemExit("Для сборки нужен uv: https://docs.astral.sh/uv/")
    ver = version()
    work = Path(tempfile.mkdtemp(prefix="st-portable-"))
    top = work / NAME
    py = top / "program" / "python"
    site = py / "Lib" / "site-packages"
    try:
        with zipfile.ZipFile(fetch_python()) as z:
            z.extractall(py)
        pth = next(py.glob("python3*._pth"))
        lines = [x for x in pth.read_text(encoding="utf-8").splitlines() if x.strip() != "#import site"]
        pth.write_text("\n".join(lines + ["Lib\\site-packages", "import site", ""]), encoding="utf-8")

        req = work / "requirements.txt"
        run(uv, "export", "-q", "--frozen", "--no-dev", "--extra", "xls", "--no-hashes", "--no-emit-project",
            "--no-header", "-o", str(req))
        common = ["--target", str(site), "--python-version", "3.12", "--python-platform", "x86_64-pc-windows-msvc"]
        run(uv, "pip", "install", *common, "--only-binary", ":all:", "--no-deps", "-r", str(req))
        run(uv, "pip", "install", *common, "--no-deps", str(ROOT))
        for junk in list(site.glob("*.dist-info/RECORD")) + list(site.rglob("__pycache__")):
            shutil.rmtree(junk) if junk.is_dir() else junk.unlink()
        for bin_dir in site.glob("bin"):  # консольные скрипты с путями сборочного компьютера не нужны
            shutil.rmtree(bin_dir)
        ok = compileall.compile_dir(site, quiet=1, workers=0,
                                    invalidation_mode=py_compile.PycInvalidationMode.UNCHECKED_HASH)
        if not ok:
            raise SystemExit("Не все модули скомпилировались — см. сообщения выше")

        (top / f"{NAME}.bat").write_bytes(LAUNCHER.replace("\n", "\r\n").encode("cp866"))
        (top / "Прочтите меня.txt").write_text(README.format(version=ver, py=PY_VERSION).replace("\n", "\r\n"),
                                               encoding="utf-8-sig", newline="")
        shutil.copy(ROOT / "LICENSE", top / "LICENSE.txt")

        out_dir.mkdir(parents=True, exist_ok=True)
        dest = out_dir / f"{NAME}-{ver}-windows.zip"
        tmp = dest.with_suffix(".part")
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
            for p in sorted(top.rglob("*")):
                if p.is_file():
                    z.write(p, p.relative_to(work).as_posix())
        tmp.replace(dest)
        files = sum(1 for p in top.rglob("*") if p.is_file())
        size = sum(p.stat().st_size for p in top.rglob("*") if p.is_file())
        print(f"Готово: {dest} — {dest.stat().st_size / 2**20:.1f} МБ (распакованная {size / 2**20:.0f} МБ, "
              f"файлов {files})")
        return dest
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    build(Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "dist")
