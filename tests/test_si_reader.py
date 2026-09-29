"""SPORTident Reader (si_reader.csv, «Config+ (card readout)») → старт, финиш, отсечки команд — на выдуманных чипах."""

from fractions import Fraction

import pytest

from st_secretary import time_run as tr
from st_secretary.importers.si_reader import cutoffs_from, parse_pairs, read_si_reader
from st_secretary.psr_run import Member, TeamInput
from st_secretary.qualification import Qual

# заголовок — как проверяет СЕКРЕТАРЬ_ST (макрос ImportFromSIReader), с BOM
HEAD = ("No;Read on;SIID;Start no;Clear CN;Clear DOW;Clear time;Clear_r CN;Clear_r DOW;Clear_r time;Check CN;Check DOW;"
        "Check time;Start CN;Start DOW;Start time;Start_r CN;Start_r DOW;Start_r time;Finish CN;Finish DOW;Finish time;"
        "Finish_r CN;Finish_r DOW;Finish_r time;Class;First name;Last name;Club;Country;Email;Date of birth;Sex;Phone;"
        "Street;ZIP;City;Hardware version;Software version;Battery date;Battery voltage;Clear count;Character set;"
        "SEL_FEEDBACK;No. of records;")


def line(no, siid, start, finish, punches=()):
    f = [str(no), "21.09.2025 12:00:00", siid, ""] + [""] * 11 + [start] + [""] * 5 + [finish] + [""] * 22  # до «No. of records»
    f.append(str(len(punches)))
    for cn, t in punches:
        f += [cn, "Su", t]
    return ";".join(f)


def csv(*lines):
    return ("﻿" + HEAD + "\n" + "\n".join(lines) + "\n").encode("utf-8")


def test_read_cards_last_read_wins_and_cutoffs():
    data = csv(line(1, "2001234", "10:00:00", "10:40:00", [("31", "10:10:00"), ("32", "10:14:30"),
                                                          ("41", "10:20:00"), ("42", "10:21:00")]),
               line(2, "2005555", "10:05:00", "--:--:--"),
               line(3, "2005555", "10:05:00", "10:50:00.5"))  # перечитали чип — берётся последнее
    cards = {c.siid: c for c in read_si_reader(data)}
    assert set(cards) == {"2001234", "2005555"}
    assert cards["2005555"].finish == Fraction(10 * 3600 + 50 * 60) + Fraction(1, 2)
    assert cutoffs_from(cards["2001234"], parse_pairs("31-32; 41-42")) == 330  # 4:30 + 1:00
    assert cutoffs_from(cards["2005555"], parse_pairs("31-32")) is None
    assert parse_pairs("31-32, 41 - 42; x-1") == [("31", "32"), ("41", "42")]
    with pytest.raises(ValueError, match="это не файл SI Reader"):
        read_si_reader("Номер;Время\n1;2\n".encode())


def test_apply_to_teams_by_chip():
    teams = [TeamInput("Кедр.xlsx", "Кедр", "", "1", [Member("А", Qual.II, "II", "2001234"), Member("Б", Qual.II, "II")]),
             TeamInput("Сосна.xlsx", "Сосна", "", "2", [Member("В", Qual.II, "II")])]
    zdata = {"cutoff_pairs": "31-32", "teams": {"Сосна.xlsx": {"chip": "2005555", "start": "10:04:00"}}}
    cards = read_si_reader(csv(line(1, "2001234", "00:02:00", "00:20:00", [("31", "00:05:00"), ("32", "00:07:30")]),
                               line(2, "2005555", "10:05:00", "10:50:00"), line(3, "2009999", "11:00:00", "11:30:00")))
    res = tr.apply_si(zdata, cards, teams)
    assert res == {"teams": 2, "unknown": ["2009999"], "replaced": ["Сосна"]}  # у «Сосны» старт вписан вручную иначе
    k = zdata["teams"]["Кедр.xlsx"]
    assert (k["start"], k["finish"], k["cutoffs"], k["chip"]) == ("00:02:00", "00:20:00", "2:30", "2001234")
    assert tr.parse_clock(k["start"]) == 120  # «00:02:00» — не 2 часа дня
    assert zdata["teams"]["Сосна.xlsx"]["start"] == "10:05:00" and zdata["teams"]["Сосна.xlsx"]["si"]["siid"] == "2005555"
    # повторный импорт того же файла — вручную вписанным уже не считается
    assert tr.apply_si(zdata, cards, teams)["replaced"] == []
