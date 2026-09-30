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
    text = bp.README.format(version="1.0", py=bp.PY_VERSION)
    for must in ("СТ-Секретарь.bat", "«данные»", "Вручную: скачайте архив", "AGPL-3.0",
                 "github.com/avtomatikalab/st-secretary", "документы участников", "program.old",
                 "--no-update-check"):
        assert must in text


def test_python_pinned_with_hash():
    assert bp.PY_VERSION.startswith("3.12.") and len(bp.PY_SHA256) == 64
