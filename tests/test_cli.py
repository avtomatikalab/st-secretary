"""Командная строка: понятные сообщения вместо технических трассировок."""

from st_secretary.cli import main


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
