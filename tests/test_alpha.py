"""Алфавитный порядок названий и ФИО (Правки.md, п. 27): «Ё» сразу после «Е», числа по значению, имена с macOS."""

import unicodedata

from st_secretary.textclean import alpha_key


def test_yo_right_after_ye_and_case_ignored():
    names = ["Сосна", "кедр", "Ёлки-палки", "Бурундуки", "Горный ветер", "Еловый бор", "Жуки", "Перевал"]
    assert sorted(names, key=alpha_key) == ["Бурундуки", "Горный ветер", "Еловый бор", "Ёлки-палки", "Жуки", "кедр",
                                            "Перевал", "Сосна"]


def test_numbers_by_value_and_macos_names():
    assert sorted(["Команда 10", "Команда 2", "Команда 1"], key=alpha_key) == ["Команда 1", "Команда 2", "Команда 10"]
    nfd = unicodedata.normalize("NFD", "Ёлки-палки.xlsx")  # так имена файлов бывают на macOS
    assert nfd != "Ёлки-палки.xlsx" and alpha_key(nfd) == alpha_key("Ёлки-палки.xlsx")
    assert sorted(["Кедр.xlsx", nfd, "Бурундуки.xlsx"], key=alpha_key)[1] == nfd


def test_preapp_files_in_alphabet_order(tmp_path):
    from st_secretary.web.store import CompFolder

    f = CompFolder(tmp_path / "Соревнование")
    f.preapp_dir.mkdir(parents=True)
    for n in ("Сосна.xlsx", "Кедр.xlsx", "Ёлки-палки.xlsx", "Бурундуки.xlsx"):
        (f.preapp_dir / n).write_bytes(b"x")
    assert [p.stem for p in f.preapp_files()] == ["Бурундуки", "Ёлки-палки", "Кедр", "Сосна"]
