"""Резервные копии: копия папки соревнования, автоматические копии при запуске, восстановление без затирания."""

import io
import os
import time
import zipfile
from datetime import datetime

import pytest

from st_secretary import backup as bk

NOW = datetime(2026, 10, 3, 18, 30)


@pytest.fixture
def comp(tmp_path):
    f = tmp_path / "данные" / "2026-10-03 Учебный чемпионат"
    (f / "Предзаявки").mkdir(parents=True)
    (f / bk.CARD).write_bytes(b"card")
    (f / "Предзаявки" / "Кедр.xlsx").write_bytes(b"team")
    (f / "Результаты_дистанции.json").write_text("{}", encoding="utf-8")
    (f / "~$Кедр.xlsx").write_bytes(b"excel lock")  # временный файл Excel — не нужен в копии
    return f


def test_make_list_and_restore_without_overwriting(comp, tmp_path):
    dest = bk.backups_dir(comp.parent)
    assert dest == tmp_path / "Резервные копии"
    path = bk.make(comp, dest, NOW)
    assert path.name == "2026-10-03 Учебный чемпионат — 2026-10-03 18-30.zip"
    top = "2026-10-03 Учебный чемпионат/"
    assert set(zipfile.ZipFile(path).namelist()) == {top + "Предзаявки/Кедр.xlsx", top + "Результаты_дистанции.json",
                                                     top + "Карточка_соревнования.xlsx"}  # без «~$…» Excel
    again = bk.make(comp, dest, NOW)  # та же минута — отдельный файл
    assert again != path and again.is_file()
    assert {b.path for b in bk.listing(dest, comp.name)} == {path, again}

    other = tmp_path / "другой ноутбук"
    assert bk.restore(path.read_bytes(), other, NOW) == comp.name
    assert (other / comp.name / "Предзаявки" / "Кедр.xlsx").read_bytes() == b"team"
    name = bk.restore(path.read_bytes(), other, NOW)  # такая папка уже есть — рядом, не поверх
    assert name == f"{comp.name} (восстановлено 03.10 18-30)"
    assert bk.restore(path.read_bytes(), other, NOW).endswith(", 2)")
    assert (other / comp.name / bk.CARD).read_bytes() == b"card"


def _zip(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for n, b in files.items():
            z.writestr(n, b)
    return buf.getvalue()


@pytest.mark.parametrize("files,why", [
    ({"a/../../evil.txt": b"x", f"a/{bk.CARD}": b"c"}, "неподходящий путь"),
    ({"/abs/evil.txt": b"x"}, "неподходящий путь"),
    ({"C:/evil/x.txt": b"x", f"C:/{bk.CARD}": b"c"}, "неподходящий путь"),
    ({f"a/{bk.CARD}": b"c", f"b/{bk.CARD}": b"c"}, "одна папка"),
    ({"a/Предзаявки/x.xlsx": b"x"}, "нет «Карточка_соревнования.xlsx»"),
    ({bk.CARD: b"c"}, "неподходящий путь"),
])
def test_restore_rejects_foreign_archives(tmp_path, files, why):
    with pytest.raises(bk.BackupError, match=why):
        bk.restore(_zip(files), tmp_path / "данные", NOW)
    assert not (tmp_path / "данные").exists() or not any((tmp_path / "данные").iterdir())


def test_restore_not_a_zip(tmp_path):
    with pytest.raises(bk.BackupError, match="не архив"):
        bk.restore(b"not a zip", tmp_path, NOW)


def test_auto_only_when_changed_and_keeps_last(comp, tmp_path):
    dest = tmp_path / "Резервные копии"
    manual = bk.make(comp, dest, datetime(2026, 10, 1, 9, 0))
    old = time.time() - 10 * 86400
    for p in comp.rglob("*"):
        os.utime(p, (old, old))  # ничего не менялось после ручной копии
    assert bk.auto([comp], dest, NOW) == []
    for i in range(4):
        later = datetime(2026, 10, 3, 19, i)
        t = later.timestamp() - 30
        os.utime(comp / "Результаты_дистанции.json", (t, t))  # внесли баллы
        assert len(bk.auto([comp], dest, later, keep=2)) == 1
    have = bk.listing(dest, comp.name)
    assert [b.auto for b in have] == [True, True, False]  # автоматических — две последние, ручная — на месте
    assert manual in [b.path for b in have] and have[0].at == datetime(2026, 10, 3, 19, 3)
