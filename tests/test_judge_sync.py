"""Телефоны судей: ссылки по этапам, слияние присланного с таблицей секретаря, расхождения."""

from st_secretary import judge_sync as js
from st_secretary.psr_run import stages_of

FILES = {"Кедр.xlsx", "Сосна.xlsx"}
NAMES = {"Кедр.xlsx": "Кедр", "Сосна.xlsx": "Сосна"}


def zdata():
    return {"stages": [{"id": "s1", "tour": "Тур 1", "name": "Узлы", "kv": "15"}, {"id": "s2", "tour": "Тур 1", "name": "Бивак"}],
            "teams": {"Сосна.xlsx": {"points": {"s1": "25"}}}}


def test_tokens_issue_and_revoke():
    data = {}
    t1 = js.issue_token(data, "М/Ж_3", "s1", "2025-09-21T09:00")
    assert len(t1) == 10 and set(t1) <= set(js._ALPHABET) and js.stage_token(data, "М/Ж_3", "s1") == t1
    t2 = js.issue_token(data, "М/Ж_3", "s1", "2025-09-21T09:05")  # новая ссылка — старая больше не работает
    assert t2 != t1 and list(js.tokens(data)) == [t2]
    js.revoke_token(data, "М/Ж_3", "s1")
    assert js.tokens(data) == {} and js.stage_token(data, "М/Ж_3", "s1") is None


def test_merge_fills_empty_cells_and_keeps_secretary_corrections():
    z = zdata()
    recs = [js.Record("Кедр.xlsx", "20", "10:05:00", "10:17:30", updated=1000),
            js.Record("Сосна.xlsx", "30", updated=1000),  # секретарь уже вписал 25 — не трогаем
            js.Record("Чужая.xlsx", "5", updated=1000)]  # команды нет в зачёте — не записываем
    res = js.merge(z, "s1", recs, FILES, "телефон-1", "2025-09-21T10:20:00")
    assert res == {"saved": ["Кедр.xlsx", "Сосна.xlsx"], "conflicts": 1}
    assert z["teams"]["Кедр.xlsx"]["points"]["s1"] == "20" and z["teams"]["Сосна.xlsx"]["points"]["s1"] == "25"
    assert z["judge"]["s1"]["Кедр.xlsx"]["arrive"] == "10:05:00" and "Чужая.xlsx" not in z["judge"]["s1"]
    issues = [i.text for i in js.judge_issues(z, stages_of(z), NAMES)]
    assert issues == ["«Сосна», Тур 1 · Узлы: судья этапа прислал 30 (10:20), в таблице 25 — проверьте"]
    assert js.from_phone(z, "s1", "Кедр.xlsx") and not js.from_phone(z, "s1", "Сосна.xlsx")

    # судья исправил балл: его прежнее значение в таблице меняется на новое
    js.merge(z, "s1", [js.Record("Кедр.xlsx", "15", updated=2000)], FILES, "телефон-1", "2025-09-21T10:30:00")
    assert z["teams"]["Кедр.xlsx"]["points"]["s1"] == "15"
    # опоздавшая старая версия с того же телефона ничего не меняет
    js.merge(z, "s1", [js.Record("Кедр.xlsx", "20", updated=1000)], FILES, "телефон-1", "2025-09-21T10:31:00")
    assert z["teams"]["Кедр.xlsx"]["points"]["s1"] == "15"
    # секретарь поправил по протесту — следующая присылка судьи таблицу не перезапишет
    z["teams"]["Кедр.xlsx"]["points"]["s1"] = "10"
    js.merge(z, "s1", [js.Record("Кедр.xlsx", "15", updated=3000)], FILES, "телефон-1", "2025-09-21T11:00:00")
    assert z["teams"]["Кедр.xlsx"]["points"]["s1"] == "10"


def test_removed_and_bad_points_and_summary():
    z = zdata()
    js.merge(z, "s2", [js.Record("Кедр.xlsx", "abc", updated=1), js.Record("Сосна.xlsx", "", "11:00:00", removed=True,
                                                                           reason="опасные действия", updated=1)],
             FILES, "телефон-2", "2025-09-21T11:05:00")
    assert "s2" not in z["teams"].get("Кедр.xlsx", {}).get("points", {})  # не число — в таблицу не идёт
    texts = [i.text for i in js.judge_issues(z, stages_of(z), NAMES)]
    assert "«Кедр», Тур 1 · Бивак: судья этапа прислал «abc» — не число" in texts
    assert "«Сосна», Тур 1 · Бивак: судья этапа отметил снятие с этапа: опасные действия" in texts
    assert js.stage_summary(z, "s2", 2) == {"teams": 2, "of": 2, "last": "2025-09-21T11:05:00"}
    assert stages_of(z)[0].kv_minutes == 15 and stages_of(z)[1].kv_minutes is None


def test_record_from_phone_json_is_cleaned():
    r = js.Record.from_json({"file": "Кедр.xlsx", "points": " 12,5 ", "arrive": "10:00:00", "note": "x" * 900,
                             "updated": "17", "removed": 1})
    assert (r.points, r.updated, r.removed, len(r.note)) == ("12,5", 17, True, 500)
    assert js.Record.from_json({"updated": "не число"}).updated == 0


def test_speleo_removal_goes_to_table_as_mark():
    z = zdata()
    js.merge(z, "s2", [js.Record("Кедр.xlsx", "", removed=True, reason="страховка", updated=1)], FILES, "т-1",
             "2025-09-21T11:00:00", removal_mark="с")
    assert z["teams"]["Кедр.xlsx"]["points"]["s2"] == "с"
    js.merge(z, "s2", [js.Record("Кедр.xlsx", "", removed=False, updated=2)], FILES, "т-1", "2025-09-21T11:05:00",
             removal_mark="с")
    assert "s2" not in z["teams"]["Кедр.xlsx"]["points"]  # судья снял отметку — клетка снова пустая
    js.merge(z, "s2", [js.Record("Сосна.xlsx", "", removed=True, updated=1)], FILES, "т-1", "2025-09-21T11:06:00")
    assert "s2" not in z["teams"]["Сосна.xlsx"]["points"]  # ПСР: снятие с этапа в таблицу не идёт
