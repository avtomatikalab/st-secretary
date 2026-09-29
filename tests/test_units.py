"""Кто выступает в зачёте: команда, связка или спортсмен — по колонкам участия в заявке и формату дисциплины."""

from types import SimpleNamespace as NS

from st_secretary import commission as cm
from st_secretary.competition import Zachet
from st_secretary.qualification import Qual
from st_secretary.units import base_file, zachet_units

Z = Zachet("М/Ж", 2, "0840131811Я")


def entry(fio, n, *, personal="", pair="", pair_num="", team_dist="", z=Z, qual=Qual.II):
    return NS(name=NS(full=fio), qual=qual, chip="", zachet=z, personal=personal, pair=pair, pair_num=pair_num,
              team_dist=team_dist, num_in_team=n)


def team(file, name, number, entries, status=cm.PENDING, rejected=()):
    persons = [NS(entry=e, status=cm.REJECTED if e.name.full in rejected else cm.PENDING) for e in entries]
    return NS(file=file, team=NS(team=name, territory="г. N", representative="Пред"), number=number,
              status=status, persons=persons)


def test_individual_each_person_with_number_like_sekretar_st():
    t = team("Кедр.xlsx", "Кедр", 3, [entry("Иванов Иван", 1, personal="1"), entry("Петрова Анна", 2, personal="лич"),
                                      entry("Сидоров Олег", 3, pair="м")])
    units = zachet_units([t], Z, "individual")
    assert [(u.team, u.number, u.club, len(u.members)) for u in units] == [
        ("Иванов Иван", "3.1", "Кедр", 1), ("Петрова Анна", "3.2", "Кедр", 1)]  # в личной — только отмеченные
    assert units[0].file == "Кедр.xlsx#иванов иван" and base_file(units[0].file) == "Кедр.xlsx"
    unmarked = team("Сосна.xlsx", "Сосна", None, [entry("Орлов Пётр", 1), entry("Лисина Вера", 2)])
    assert [u.team for u in zachet_units([unmarked], Z, "individual")] == ["Орлов Пётр", "Лисина Вера"]  # никто не отмечен


def test_pairs_by_mark_and_number():
    z = Zachet("М/Ж", 2, "0840261811Я")
    t = team("Кедр.xlsx", "Кедр", 5, [entry("А", 1, pair="см", z=z), entry("Б", 2, pair="см", z=z),
                                      entry("В", 3, pair="м", pair_num="2", z=z), entry("Г", 4, pair="м 2", z=z),
                                      entry("Д", 5, personal="1", z=z)])
    units = zachet_units([t], z, "pair")
    assert [(u.team, [m.fio for m in u.members]) for u in units] == [("Кедр (см)", ["А", "Б"]),
                                                                     ("Кедр (м 2)", ["В", "Г"])]
    one = team("Сосна.xlsx", "Сосна", 6, [entry("Е", 1, pair="ж", z=z), entry("Ж", 2, pair="ж", z=z)])
    assert [(u.team, u.file) for u in zachet_units([one], z, "pair")] == [("Сосна", "Сосна.xlsx#связка:ж")]


def test_group_whole_team_or_several_groups_and_rejected_left_out():
    z = Zachet("М/Ж", 3, "0840161811Я")
    t = team("Кедр.xlsx", "Кедр", 1, [entry("А", 1, team_dist="1", z=z), entry("Б", 2, team_dist="1", z=z),
                                      entry("В", 3, team_dist="1", z=z)], rejected=("В",))
    units = zachet_units([t], z, "group")
    assert [(u.file, u.team, [m.fio for m in u.members]) for u in units] == [("Кедр.xlsx", "Кедр", ["А", "Б"])]
    two = team("Сосна.xlsx", "Сосна", 2, [entry("Г", 1, team_dist="1", z=z), entry("Д", 2, team_dist="2", z=z)])
    assert [u.team for u in zachet_units([two], z, "group")] == ["Сосна (1)", "Сосна (2)"]
    no = team("Пихта.xlsx", "Пихта", 3, [entry("Е", 1, z=z)], status=cm.REJECTED)
    assert zachet_units([no], z, "group")[0].admitted is False
    other = team("Ель.xlsx", "Ель", 4, [entry("Ж", 1, z=Zachet("Ж", 3, "0840161811Я"))])
    assert zachet_units([other], z, "group") == []  # участник другого зачёта
