"""Командная строка: понятные сообщения вместо технических трассировок."""

import io
import threading

from st_secretary.cli import Loading, main


class Console(io.StringIO):
    """Как окно консоли: isatty() — да."""

    def isatty(self):
        return True


def test_loading_animation_runs_in_console_and_is_erased():
    out = Console()
    shown = threading.Event()
    loading = Loading("Загружаю программу", out)
    loading._draw = (lambda draw: lambda i: (draw(i), shown.set()))(loading._draw)
    loading.start()
    assert shown.wait(2)
    loading.stop()
    loading.stop()  # второй раз — ничего не ломает
    text = out.getvalue()
    assert "Загружаю программу..." in text and "[====" in text and " с" in text
    assert text.endswith("\r")  # строка с полоской стёрта — дальше пишется обычный текст
    assert {len(loading.frame(i)) for i in range(40)} == {Loading.WIDTH + 2}  # полоска не «прыгает»


def test_loading_prints_nothing_outside_console():
    out = io.StringIO()
    Loading("Загружаю программу", out).start().stop()
    assert out.getvalue() == ""


def test_missing_folder_and_card_are_explained(tmp_path, capsys):
    code = main(["preapp", str(tmp_path / "папка с заявками"), "--card", "путь к карточке.xlsx"])
    out = capsys.readouterr().out
    assert code == 1 and "Не найдена папка с заявками" in out and "Копировать как путь" in out
    (tmp_path / "заявки").mkdir()
    code = main(["preapp", str(tmp_path / "заявки"), "--card", "путь к карточке.xlsx"])
    out = capsys.readouterr().out
    assert code == 1 and "Не найден файл карточки соревнования" in out and "Traceback" not in out


def test_card_check_missing_file(capsys):
    assert main(["card-check", "нет такого файла.xlsx"]) == 1
    assert "Не найден файл карточки" in capsys.readouterr().out
