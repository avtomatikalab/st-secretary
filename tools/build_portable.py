"""Переносная версия для Windows, macOS и Linux: один архив — распаковал, запустил, работает. Без установки и без
интернета.

    uv run python tools/build_portable.py                    → все системы в dist/
    uv run python tools/build_portable.py --target windows   → одна система: windows, macos-arm64, macos-x86_64,
                                                               linux-x86_64 или native (система этого компьютера)
    uv run python tools/build_portable.py ПАПКА              → в другую папку

Файлы — это файлы выпуска на GitHub. Имена латиницей (GitHub заменяет кириллицу; внутри архивов — по-русски) и
без номера версии: тогда ссылка …/releases/latest/download/<имя> всегда ведёт на последнюю версию — её дают
секретарям и судьям. Номер версии — в названии выпуска и внутри программы.
    st-secretary-windows.zip         — Windows 10 и 11
    st-secretary-macos-arm64.tar.gz  — Mac с процессором Apple (M1 и новее), macOS 11+
    st-secretary-macos-x86_64.tar.gz — Mac с процессором Intel, macOS 10.15+
    st-secretary-linux-x86_64.tar.gz — Linux x86_64 (glibc 2.28+: Ubuntu 20.04, Debian 10 и новее)
    st-secretary-manual.pdf          — инструкция секретаря

Внутри архива:
    СТ-Секретарь/
        СТ-Секретарь.bat (Windows) | .command (macOS) | .sh (Linux) — запуск
        Прочтите меня.txt     — как запустить, где данные, как обновить
        Инструкция секретаря.pdf — со снимками экрана (кнопка «Инструкция» в программе открывает её)
        LICENSE.txt           — лицензия AGPL-3.0 (исходный код — на GitHub)
        program/              — Python и библиотеки
    Папка «данные» появляется рядом при первом запуске; в архиве её нет, поэтому обновление её не трогает.

Python в Windows — официальная сборка «embeddable» с python.org (у python.exe подпись Python Software Foundation;
exe-упаковщики вроде PyInstaller антивирусы часто принимают за вирус). В macOS и Linux — python-build-standalone
(та же сборка Python, которую ставит uv), архив .tar.gz: в нём сохраняются права на запуск и ссылки. Файлы Python
переписываются в итоговый архив как есть, без распаковки, поэтому собрать можно на любом компьютере: библиотеки для
другой системы uv ставит по её меткам (--python-platform). Библиотеки — по uv.lock, те же версии, что в тестах.
Файлы .pyc компилируются заранее и не сверяются с исходниками: программа ничего не пишет в свою папку.

Нужны uv и интернет — только на компьютере, где собирают архив.
"""

from __future__ import annotations

import argparse
import compileall
import copy
import hashlib
import io
import os
import platform
import py_compile
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
from urllib.parse import quote, unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
PY_VERSION = "3.12.10"  # последняя версия 3.12 со сборкой embeddable; та же 3.12, что в разработке
PY_URL = f"https://www.python.org/ftp/python/{PY_VERSION}/python-{PY_VERSION}-embed-amd64.zip"
PY_SHA256 = "4acbed6dd1c744b0376e3b1cf57ce906f9dc9e95e68824584c8099a63025a3c3"  # sha256 архива с python.org
# macOS и Linux: python-build-standalone (github.com/astral-sh/python-build-standalone), суммы — из SHA256SUMS выпуска
PBS_TAG = "20260929"
PBS_PY = "3.12.14"
UNIX = {  # цель → (платформа python-build-standalone, платформа для uv pip, файл запуска, sha256, мин. macOS)
    "macos-arm64": ("aarch64-apple-darwin", "aarch64-apple-darwin", "command",
                    "1bb3e53d231ee2c8881e8daf6426f4dd95bff0dda496af0f3af300357aa998d0", "11.0"),
    "macos-x86_64": ("x86_64-apple-darwin", "x86_64-apple-darwin", "command",
                     "62891cf4a32ed18b6b4174def999184b5186371422c52dd92d941401db0f0e41", "10.15"),
    "linux-x86_64": ("x86_64-unknown-linux-gnu", "x86_64-unknown-linux-gnu", "sh",
                     "ef605200f8174e87ecfc308e52a88127543f85dd5c940dc5e92cab244b98a003", ""),
}
TARGETS = ["windows", *UNIX]
CACHE = Path(os.environ.get("LOCALAPPDATA") or Path.home() / ".cache") / "st-secretary" / "build-cache"
NAME = "СТ-Секретарь"
MANUAL = ROOT / "docs" / "Инструкция секретаря.pdf"  # tools/make_manual.py

LAUNCHER = r"""@echo off
rem ST-Secretary, portable version: double-click to open the program in the browser.
rem Competitions are stored in the "данные" folder next to this file. The "program" folder is Python with
rem the libraries - it is replaced as a whole on update: the program unpacks a new version to "program.new"
rem and exits with code 75, then this file renames program -> program.old, program.new -> program and starts
rem it again. Paths here are ASCII so the file works with any console code page; the messages are in CP866
rem (the Russian Windows console).
cd /d "%~dp0"
title СТ-Секретарь
set "ST_PORTABLE=1"
set "PYTHONDONTWRITEBYTECODE=1"

:start
if not exist "program.new\python\python.exe" goto run
echo Ставлю новую версию СТ-Секретаря...
if exist "program.old" rmdir /s /q "program.old"
set /a TRY=0
:swap
if not exist "program" goto swapped
move "program" "program.old" >nul 2>nul
if not exist "program" goto swapped
set /a TRY+=1
if %TRY% GEQ 10 goto swap_failed
ping -n 2 127.0.0.1 >nul
goto swap
:swapped
move "program.new" "program" >nul 2>nul
if exist "program\python\python.exe" goto installed
if not exist "program" if exist "program.old" move "program.old" "program" >nul 2>nul
echo Новую версию поставить не получилось - запускаю прежнюю.
goto run
:swap_failed
echo Папка program занята другой программой - новая версия встанет при следующем запуске.
goto run
:installed
echo Новая версия поставлена. Прежняя сохранена в папке program.old.

:run
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
"program\python\python.exe" -m st_secretary web %*
set "RC=%errorlevel%"
if "%RC%"=="75" (
  set "ST_NO_BROWSER=1"
  goto start
)
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
- Соревнования — папка «данные» рядом с этим файлом (появится после первого запуска).
- Резервные копии — папка «Резервные копии» рядом: программа делает их сама при каждом запуске; кнопка
  «Скачать копию» на странице соревнования — копия на флешку.
- Журнал программы — папка «Журнал» рядом: если программа закрылась неожиданно, она предложит скачать его —
  отправьте разработчику. В журнале нет паспортных данных.
- Ошибки, неудобства, предложения — кнопка «Сообщить» вверху любой страницы. Сообщения копятся в папке «Правки и
  ошибки» рядом и уходят разработчику одним файлом на {email} («Мои сообщения» на главной → «Отправить»).
- Сканы документов участников и паспортные данные судей — только на этом компьютере, в вашей папке
  пользователя: «СТ-Секретарь — документы участников». В облако и в папку соревнования они не попадают.

КАК ОБНОВИТЬ
Программа сама проверяет при запуске, нет ли новой версии (если есть интернет), и предлагает обновиться на
главной странице — одной кнопкой: сделает резервную копию, скачает новую версию, перезапустится. Соревнования,
резервные копии и документы участников обновление не трогает. Отключить проверку: запускать с ключом
--no-update-check. Программа отправляет GitHub только сам запрос — ни соревнований, ни имён.
Прежняя версия сохраняется в папке «program.old». Если новая не запускается: закройте чёрное окно, удалите папку
«program» и переименуйте «program.old» в «program».
Вручную: скачайте архив со страницы https://github.com/avtomatikalab/st-secretary/releases, удалите старую папку
«program» и распакуйте новый архив в ту же папку, согласившись заменить файлы. Папки «данные» и «Резервные копии»
останутся как были: в архиве их нет.

ТАБЛО И ТЕЛЕФОНЫ СУДЕЙ
При первом включении раздачи по Wi-Fi Windows спросит разрешение в брандмауэре для python.exe —
разрешите для частной сети.

Есть версии для macOS и Linux — на странице https://github.com/avtomatikalab/st-secretary/releases

Программа свободная (лицензия AGPL-3.0, файл LICENSE.txt), исходный код:
https://github.com/avtomatikalab/st-secretary
В папке program — Python {py} (лицензия PSF, program\\python\\LICENSE.txt) и библиотеки с их лицензиями.
"""

UNIX_LAUNCHER = r"""#!/bin/bash
# СТ-Секретарь, переносная версия для macOS и Linux: запуск — программа откроется в браузере.
# Соревнования хранятся в папке «данные» рядом с этим файлом. Папка program — Python с библиотеками, при
# обновлении она заменяется целиком: программа кладёт новую версию в program.new и выходит с кодом 75, а этот
# файл переименовывает program -> program.old, program.new -> program и запускает программу снова.
cd "$(dirname "$0")" || exit 1
export ST_PORTABLE=1 PYTHONDONTWRITEBYTECODE=1
printf '\033]0;СТ-Секретарь\007'
if [ "$(uname)" = "Darwin" ]; then
  # файлы из интернета macOS помечает «карантином» и не даёт запустить Python — снять пометку с папки программы
  xattr -dr com.apple.quarantine . 2>/dev/null
fi
rc=0
while true; do
  if [ -x program.new/python/bin/python3 ]; then
    echo "Ставлю новую версию СТ-Секретаря..."
    rm -rf program.old
    tries=0
    while [ -e program ] && ! mv program program.old 2>/dev/null; do
      tries=$((tries + 1))
      [ "$tries" -ge 10 ] && break
      sleep 1
    done
    if [ -e program ]; then
      echo "Папка program занята другой программой - новая версия встанет при следующем запуске."
    elif mv program.new program; then
      echo "Новая версия поставлена. Прежняя сохранена в папке program.old."
    else
      mv program.old program
      echo "Новую версию поставить не получилось - запускаю прежнюю."
    fi
  fi
  if [ ! -x program/python/bin/python3 ]; then
    echo
    echo "Не найдена папка program рядом с этим файлом."
    echo "Распакуйте архив СТ-Секретаря целиком и запускайте файл из распакованной папки."
    read -r -p "Нажмите Enter, чтобы закрыть окно. " _
    exit 1
  fi
  echo "Запускаю СТ-Секретарь..."
  echo
  program/python/bin/python3 -m st_secretary web "$@"
  rc=$?
  if [ "$rc" -eq 75 ]; then
    export ST_NO_BROWSER=1
    continue
  fi
  break
done
echo
if [ "$rc" -ne 0 ]; then
  echo "Программа остановилась с ошибкой. Сообщение выше пригодится разработчикам."
fi
echo "Это окно можно закрыть."
"""

INSTALL_CMD = "curl -fsSL https://raw.githubusercontent.com/avtomatikalab/st-secretary/main/tools/install.sh | sh"

UNIX_RUN = {
    "macos": """КАК ЗАПУСТИТЬ
Проще всего — установить одной командой: откройте Терминал (Cmd+Пробел → «Терминал»), вставьте строку
   {install}
и нажмите Enter. Программа встанет в папку «СТ-Секретарь» в вашей домашней папке, на Рабочем столе появится
ярлык «СТ-Секретарь». Этой же командой программу потом можно обновить.

Или из этого архива:
1. Распакуйте архив (двойной щелчок; Safari обычно распаковывает сам) и перенесите папку «СТ-Секретарь»
   в свою домашнюю папку.
2. Дважды щёлкните «СТ-Секретарь.command». Откроется окно Терминала — это сама программа — и страница в
   браузере. В первый раз macOS скажет, что не может проверить разработчика: нажмите «Готово», откройте
   «Системные настройки» → «Конфиденциальность и безопасность», внизу нажмите «Всё равно открыть» для
   «СТ-Секретарь.command» и подтвердите. Это нужно один раз.
3. Интернет не нужен. Выключить программу — кнопка «Выключить» вверху страницы или закройте окно Терминала.
   Удобно положить «СТ-Секретарь.command» в Dock (перетащите в правую часть Dock).
""",
    "linux": """КАК ЗАПУСТИТЬ
Проще всего — установить одной командой: откройте терминал, вставьте строку
   {install}
и нажмите Enter. Программа встанет в папку «СТ-Секретарь» в вашей домашней папке и появится в меню программ.
Этой же командой программу потом можно обновить.

Или из этого архива:
1. Распакуйте архив в домашнюю папку (правой кнопкой → «Извлечь сюда»).
2. Запустите «СТ-Секретарь.sh»: правой кнопкой → «Запустить как программу» (или двойной щелчок →
   «Запустить в терминале»), или в терминале: ./СТ-Секретарь.sh
   Откроется окно терминала — это сама программа — и страница в браузере.
3. Интернет не нужен. Выключить программу — кнопка «Выключить» вверху страницы или закройте окно терминала.
""",
}

UNIX_README = """СТ-Секретарь {version} — программа для секретариата соревнований по спортивному туризму
Версия для {system}.

{run}
ГДЕ ДАННЫЕ
- Соревнования — папка «данные» рядом с этим файлом (появится после первого запуска).
- Резервные копии — папка «Резервные копии» рядом: программа делает их сама при каждом запуске; кнопка
  «Скачать копию» на странице соревнования — копия на флешку.
- Журнал программы — папка «Журнал» рядом: если программа закрылась неожиданно, она предложит скачать его —
  отправьте разработчику. В журнале нет паспортных данных.
- Ошибки, неудобства, предложения — кнопка «Сообщить» вверху любой страницы. Сообщения копятся в папке «Правки и
  ошибки» рядом и уходят разработчику одним файлом на {email} («Мои сообщения» на главной → «Отправить»).
- Сканы документов участников и паспортные данные судей — только на этом компьютере, в вашей домашней папке:
  «СТ-Секретарь — документы участников». В облако и в папку соревнования они не попадают.

КАК ОБНОВИТЬ
Программа сама проверяет при запуске, нет ли новой версии (если есть интернет), и предлагает обновиться на
главной странице — одной кнопкой: сделает резервную копию, скачает новую версию, перезапустится. Соревнования,
резервные копии и документы участников обновление не трогает. Отключить проверку: запускать с ключом
--no-update-check. Программа отправляет GitHub только сам запрос — ни соревнований, ни имён.
Прежняя версия сохраняется в папке «program.old». Если новая не запускается: закройте окно программы, удалите
папку «program» и переименуйте «program.old» в «program».

ТАБЛО И ТЕЛЕФОНЫ СУДЕЙ
Если включён брандмауэр, при первой раздаче по Wi-Fi система спросит, разрешить ли python3 входящие
подключения, — разрешите.

Программа свободная (лицензия AGPL-3.0, файл LICENSE.txt), исходный код:
https://github.com/avtomatikalab/st-secretary
В папке program — Python {py} (сборка python-build-standalone, лицензия PSF) и библиотеки с их лицензиями.
"""


def run(*cmd: str, env: dict | None = None) -> None:
    print("  $", " ".join(cmd[:6]), "…" if len(cmd) > 6 else "")
    subprocess.run(cmd, check=True, cwd=ROOT, env={**os.environ, **(env or {})})


def fetch(url: str, sha256: str) -> Path:
    """Файл из интернета в кэш сборки, со сверкой sha256 (не совпало — удаляется, сборка останавливается)."""
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / unquote(Path(urlsplit(url).path).name)
    if not path.is_file():
        print(f"Скачиваю {url}")
        tmp = path.with_name(path.name + ".part")
        with urllib.request.urlopen(url, timeout=120) as r, tmp.open("wb") as out:  # python.org и GitHub
            shutil.copyfileobj(r, out)
        tmp.replace(path)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != sha256:
        path.unlink()
        raise SystemExit(f"{path.name} не совпадает с ожидаемым (sha256 {digest}) — удалён, запустите сборку снова")
    return path


def fetch_python() -> Path:
    path = fetch(PY_URL, PY_SHA256)
    print(f"Python {PY_VERSION} embeddable, sha256 {PY_SHA256}")
    return path


def pbs_url(target: str) -> str:
    name = f"cpython-{PBS_PY}+{PBS_TAG}-{UNIX[target][0]}-install_only_stripped.tar.gz"
    return f"https://github.com/astral-sh/python-build-standalone/releases/download/{PBS_TAG}/{quote(name)}"


def version() -> str:
    ns: dict = {}
    exec((ROOT / "src" / "st_secretary" / "__init__.py").read_text(encoding="utf-8"), ns)  # noqa: S102
    return ns["__version__"]


def support_email() -> str:
    """Адрес для сообщений пользователей — из feedback.SUPPORT_EMAIL (одна константа, Правки, п. 43)."""
    text = (ROOT / "src" / "st_secretary" / "feedback.py").read_text(encoding="utf-8")
    return re.search(r'^SUPPORT_EMAIL = "([^"]+)"', text, re.MULTILINE).group(1)


def native() -> str:
    if sys.platform.startswith("win"):
        return "windows"
    if sys.platform == "darwin":
        return "macos-arm64" if platform.machine().lower() in ("arm64", "aarch64") else "macos-x86_64"
    return "linux-x86_64"


def install_libs(uv: str, req: Path, site: Path, uv_platform: str, env: dict | None = None) -> None:
    """Библиотеки по uv.lock и сама программа → site (для чужой системы — по её меткам), .pyc заранее."""
    common = ["--target", str(site), "--python-version", "3.12", "--python-platform", uv_platform]
    run(uv, "pip", "install", *common, "--only-binary", ":all:", "--no-deps", "-r", str(req), env=env)
    run(uv, "pip", "install", *common, "--no-deps", str(ROOT), env=env)
    for junk in list(site.glob("*.dist-info/RECORD")) + list(site.rglob("__pycache__")):
        shutil.rmtree(junk) if junk.is_dir() else junk.unlink()
    for bin_dir in site.glob("bin"):  # консольные скрипты с путями сборочного компьютера не нужны
        shutil.rmtree(bin_dir)
    ok = compileall.compile_dir(site, quiet=1, workers=0,
                                invalidation_mode=py_compile.PycInvalidationMode.UNCHECKED_HASH)
    if not ok:
        raise SystemExit("Не все модули скомпилировались — см. сообщения выше")


def build_windows(uv: str, req: Path, work: Path, ver: str, out_dir: Path) -> Path:
    top = work / NAME
    py = top / "program" / "python"
    with zipfile.ZipFile(fetch_python()) as z:
        z.extractall(py)
    pth = next(py.glob("python3*._pth"))
    lines = [x for x in pth.read_text(encoding="utf-8").splitlines() if x.strip() != "#import site"]
    pth.write_text("\n".join(lines + ["Lib\\site-packages", "import site", ""]), encoding="utf-8")
    install_libs(uv, req, py / "Lib" / "site-packages", "x86_64-pc-windows-msvc")

    (top / f"{NAME}.bat").write_bytes(LAUNCHER.replace("\n", "\r\n").encode("cp866"))
    readme = README.format(version=ver, py=PY_VERSION, email=support_email())
    (top / "Прочтите меня.txt").write_text(readme.replace("\n", "\r\n"),
                                           encoding="utf-8-sig", newline="")
    shutil.copy(ROOT / "LICENSE", top / "LICENSE.txt")
    if MANUAL.is_file():
        shutil.copy(MANUAL, top / MANUAL.name)

    dest = out_dir / "st-secretary-windows.zip"  # файл выпуска на GitHub: латиница, без версии
    tmp = dest.with_name(dest.name + ".part")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for p in sorted(top.rglob("*")):
            if p.is_file():
                z.write(p, p.relative_to(work).as_posix())
    tmp.replace(dest)
    return dest


def build_unix(target: str, uv: str, req: Path, work: Path, ver: str, out_dir: Path) -> Path:
    """Архив .tar.gz: Python из python-build-standalone переписывается в него как есть (права на запуск и
    ссылки сохраняются, на диск сборочного компьютера не распаковывается), рядом — библиотеки для этой системы."""
    _, uv_platform, ext, sha, macos_min = UNIX[target]
    pbs = fetch(pbs_url(target), sha)
    site = work / "site-packages"
    install_libs(uv, req, site, uv_platform, {"MACOSX_DEPLOYMENT_TARGET": macos_min} if macos_min else None)
    system = "macOS" if target.startswith("macos") else "Linux"
    readme = UNIX_README.format(version=ver, py=PBS_PY, system=f"{system} ({target.split('-')[1]})",
                                email=support_email(),
                                run=UNIX_RUN["macos" if system == "macOS" else "linux"].format(install=INSTALL_CMD))
    now = int(time.time())

    def info(name: str, mode: int, size: int = 0, kind: bytes = tarfile.REGTYPE) -> tarfile.TarInfo:
        ti = tarfile.TarInfo(name)
        ti.mode, ti.size, ti.type, ti.mtime = mode, size, kind, now
        return ti

    dest = out_dir / f"st-secretary-{target}.tar.gz"
    tmp = dest.with_name(dest.name + ".part")
    prefix = f"{NAME}/program/"
    with tarfile.open(tmp, "w:gz", compresslevel=9, format=tarfile.PAX_FORMAT) as out:
        for d in (NAME, f"{NAME}/program"):
            out.addfile(info(d, 0o755, kind=tarfile.DIRTYPE))
        with tarfile.open(pbs, "r:gz") as src:
            for m in src:
                if not (m.name == "python" or m.name.startswith("python/")):
                    raise SystemExit(f"Неожиданный файл в архиве Python: {m.name}")
                m2 = copy.copy(m)
                m2.name = prefix + m.name
                if m.islnk():  # жёсткая ссылка — путь внутри архива
                    m2.linkname = prefix + m.linkname
                m2.uid = m2.gid = 0
                m2.uname = m2.gname = ""
                out.addfile(m2, src.extractfile(m) if m.isreg() else None)
        sp = f"{prefix}python/lib/python3.12/site-packages/"
        for p in sorted(site.rglob("*")):
            name = sp + p.relative_to(site).as_posix()
            if p.is_dir():
                out.addfile(info(name, 0o755, kind=tarfile.DIRTYPE))
            else:
                with p.open("rb") as f:
                    out.addfile(info(name, 0o755 if ".so" in p.suffixes else 0o644, p.stat().st_size), f)
        files = [(f"{NAME}.{ext}", UNIX_LAUNCHER.encode(), 0o755), ("Прочтите меня.txt", readme.encode(), 0o644),
                 ("LICENSE.txt", (ROOT / "LICENSE").read_bytes(), 0o644)]
        if MANUAL.is_file():
            files.append((MANUAL.name, MANUAL.read_bytes(), 0o644))
        for name, data, mode in files:
            out.addfile(info(f"{NAME}/{name}", mode, len(data)), io.BytesIO(data))
    tmp.replace(dest)
    return dest


def build(out_dir: Path, targets: list[str] | None = None) -> list[Path]:
    uv = shutil.which("uv")
    if not uv:
        raise SystemExit("Для сборки нужен uv: https://docs.astral.sh/uv/")
    targets = [native() if t == "native" else t for t in (targets or TARGETS)]
    ver = version()
    out_dir.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="st-portable-"))
    made = []
    try:
        req = work / "requirements.txt"
        run(uv, "export", "-q", "--frozen", "--no-dev", "--extra", "xls", "--no-hashes", "--no-emit-project",
            "--no-header", "-o", str(req))
        for t in targets:
            print(f"== {t}")
            (work / t).mkdir()
            dest = (build_windows(uv, req, work / t, ver, out_dir) if t == "windows"
                    else build_unix(t, uv, req, work / t, ver, out_dir))
            print(f"Готово: {dest} — версия {ver}, {dest.stat().st_size / 2**20:.1f} МБ")
            made.append(dest)
        if MANUAL.is_file():
            shutil.copy(MANUAL, out_dir / "st-secretary-manual.pdf")
        return made
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Переносная версия СТ-Секретаря для Windows, macOS и Linux")
    ap.add_argument("out", nargs="?", default=str(ROOT / "dist"), help="куда положить архивы (по умолчанию dist/)")
    ap.add_argument("--target", action="append", choices=[*TARGETS, "native"],
                    help="для какой системы (можно несколько раз; по умолчанию — все; native — система этого "
                         "компьютера)")
    a = ap.parse_args()
    build(Path(a.out), a.target)
