import pytest

from st_secretary.qualification import Qual, parse_members_with_quals, parse_qual


@pytest.mark.parametrize(
    "text, expected",
    [
        ("б/р", Qual.BR), ("Б/Р", Qual.BR), ("", Qual.BR), (None, Qual.BR), ("-", Qual.BR),
        ("3ю", Qual.Y3), ("3 юн", Qual.Y3), ("2ю", Qual.Y2), ("1ю", Qual.Y1), ("I юн.", Qual.Y1),
        ("III", Qual.III), ("3", Qual.III), (3.0, Qual.III), ("II", Qual.II), ("2", Qual.II),
        ("I", Qual.I), ("1", Qual.I), ("І", Qual.I),  # кириллическая «І»
        ("КМС", Qual.KMS), ("кмс", Qual.KMS), ("МС", Qual.MS), ("МСМК", Qual.MS),
    ],
)
def test_parse(text, expected):
    assert parse_qual(text) is expected


def test_unknown_is_error():
    with pytest.raises(ValueError):
        parse_qual("чемпион двора")


def test_order():
    assert Qual.BR < Qual.Y3 < Qual.Y2 < Qual.Y1 < Qual.III < Qual.II < Qual.I < Qual.KMS < Qual.MS


def test_members():
    got = parse_members_with_quals("Иванов Иван(КМС), Петрова  Анна (б/р), Сидоров Пётр(III)")
    assert got == [("Иванов Иван", Qual.KMS), ("Петрова Анна", Qual.BR), ("Сидоров Пётр", Qual.III)]
