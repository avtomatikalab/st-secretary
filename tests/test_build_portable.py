"""Переносная версия: файл запуска и «Прочтите меня» (сама сборка скачивает Python — проверяется вручную,
прогоном всех тестов на распакованном архиве)."""

import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location("build_portable", Path(__file__).parents[1] / "tools" / "build_portable.py")
bp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bp)


def test_launcher_works_with_any_console_code_page():
    data = bp.LAUNCHER.encode("cp866")  # сообщения — в кодировке русской консоли
    assert data.decode("cp866") == bp.LAUNCHER
    for line in bp.LAUNCHER.splitlines():
        if line.lstrip().lower().startswith(("echo", "title", "rem")):
            continue
        assert line.isascii(), line  # пути и команды — латиницей: работают при любой кодовой странице
    assert r'"program\python\python.exe" -m st_secretary web %*' in bp.LAUNCHER
    assert 'cd /d "%~dp0"' in bp.LAUNCHER and "pause" in bp.LAUNCHER


def test_launcher_installs_update_and_restarts():
    from st_secretary.updates import RESTART

    text = bp.LAUNCHER
    assert f'if "%RC%"=="{RESTART}" (' in text and "goto start" in text  # код «перезапустить» → снова с начала
    assert text.index(':start') < text.index('program.new\\python\\python.exe') < text.index(':run')
    assert 'move "program" "program.old"' in text and 'move "program.new" "program"' in text
    assert 'set "ST_NO_BROWSER=1"' in text  # страница уже открыта — второй вкладки не нужно


def test_readme_explains_start_data_and_update():
    text = bp.README.format(version="1.0", py=bp.PY_VERSION, email=bp.support_email())
    assert "«Сообщить»" in text and "support.st.secretary@gmail.com" in text  # Правки, п. 43
    for must in ("СТ-Секретарь.bat", "«данные»", "Вручную: скачайте архив", "AGPL-3.0",
                 "github.com/avtomatikalab/st-secretary", "документы участников", "program.old",
                 "--no-update-check"):
        assert must in text


def test_python_pinned_with_hash():
    assert bp.PY_VERSION.startswith("3.12.") and len(bp.PY_SHA256) == 64


def test_macos_and_linux_builds():
    from st_secretary import updates

    assert bp.TARGETS == ["windows", "macos-arm64", "macos-x86_64", "linux-x86_64"]
    for target, (pbs, _uv, ext, sha, _mac) in bp.UNIX.items():
        assert f"-{target}.tar.gz" in updates.ASSETS.values()  # программа найдёт свой архив в выпуске
        assert len(sha) == 64 and pbs in bp.pbs_url(target) and "%2B" in bp.pbs_url(target)
        assert ext == ("command" if target.startswith("macos") else "sh")
    assert "-windows.zip" in updates.ASSETS.values()  # имя, которое ищет уже выпущенная 0.1.0


def test_unix_launcher_installs_update_and_restarts():
    from st_secretary.updates import RESTART

    text = bp.UNIX_LAUNCHER
    assert text.startswith("#!/bin/bash\n") and "\r" not in text
    assert 'cd "$(dirname "$0")"' in text and "program/python/bin/python3 -m st_secretary web \"$@\"" in text
    assert f'[ "$rc" -eq {RESTART} ]' in text and "continue" in text and "export ST_NO_BROWSER=1" in text
    assert "mv program program.old" in text and "mv program.new program" in text
    assert "xattr -dr com.apple.quarantine" in text  # macOS: «карантин» у файлов из интернета


def test_unix_readme_explains_start_and_install():
    for system in ("macos", "linux"):
        text = bp.UNIX_README.format(version="1.0", py=bp.PBS_PY, system=system, email=bp.support_email(),
                                     run=bp.UNIX_RUN[system].format(install=bp.INSTALL_CMD))
        for must in ("tools/install.sh | sh", "«данные»", "program.old", "--no-update-check", "AGPL-3.0",
                     "документы участников"):
            assert must in text, (system, must)
    assert "Всё равно открыть" in bp.UNIX_RUN["macos"]  # первый запуск без установщика — Gatekeeper


def test_install_script_knows_all_unix_builds():
    text = (Path(__file__).parents[1] / "tools" / "install.sh").read_text(encoding="utf-8")
    assert "\r" not in text and text.startswith("#!/bin/sh\n")
    for target in bp.UNIX:
        assert f'SUFFIX="{target}.tar.gz"' in text
    assert "program.old" in text and "sha256" in text and "ST_INSTALL_ARCHIVE" in text
