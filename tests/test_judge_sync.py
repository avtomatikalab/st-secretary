"""Телефоны судей: ссылки по этапам, слияние присланного с таблицей секретаря, расхождения."""

from st_secretary import judge_sync as js
from st_secretary.psr_run import Stage, stages_of

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


def test_cutoffs_from_phones_sum_over_stages_and_keep_secretary_value():
    zdata = {"stages": [{"id": "s1", "name": "Колодец"}, {"id": "s2", "name": "Шкуродёр"}], "teams": {}}
    files = {"Кедр.xlsx"}
    js.merge(zdata, "s1", [js.Record("Кедр.xlsx", cutoff="2:30", updated=1)], files, "т-1", "2026-10-04T10:00:00")
    js.merge(zdata, "s2", [js.Record("Кедр.xlsx", cutoff="1:00", cut_on="", updated=2)], files, "т-2",
             "2026-10-04T10:20:00")
    assert zdata["teams"]["Кедр.xlsx"]["cutoffs"] == "3:30" and js.from_phone(zdata, "cutoffs", "Кедр.xlsx")
    zdata["teams"]["Кедр.xlsx"]["cutoffs"] = "4:00"  # секретарь поправил по протоколу
    out = js.merge(zdata, "s2", [js.Record("Кедр.xlsx", cutoff="2:00", updated=3)], files, "т-2",
                   "2026-10-04T10:25:00")  # с телефонов теперь 4:30
    assert out["conflicts"] == 1 and zdata["teams"]["Кедр.xlsx"]["cutoffs"] == "4:00"
    assert not js.from_phone(zdata, "cutoffs", "Кедр.xlsx")
    stages = [Stage("s1", "", "Колодец"), Stage("s2", "", "Шкуродёр")]
    texts = [i.text for i in js.judge_issues(zdata, stages, {"Кедр.xlsx": "Кедр"})]
    assert "«Кедр»: отсечки с телефонов судей — 4:30, в таблице 4:00 — проверьте" in texts
    assert js.Record.from_json({"file": "Кедр.xlsx", "cutoff": "5:00", "cut_on": "10:01:02"}).cut_on == "10:01:02"


def test_newer_record_from_same_phone_replaces_earlier():
    """Правки.md, п. 10: правка, сделанная, пока шла отправка, приходит следующей присылкой — и заменяет прежнюю."""
    z = zdata()
    js.merge(z, "s1", [js.Record("Кедр.xlsx", "", "10:05:00", updated=1000)], FILES, "т-1", "2025-09-21T10:06:00")
    js.merge(z, "s1", [js.Record("Кедр.xlsx", "0", "10:05:00", "10:12:00", updated=2000)], FILES, "т-1",
             "2025-09-21T10:12:05")
    rec = z["judge"]["s1"]["Кедр.xlsx"]
    assert (rec["leave"], rec["points"], rec["updated"]) == ("10:12:00", "0", 2000)
    assert z["teams"]["Кедр.xlsx"]["points"]["s1"] == "0"
    js.merge(z, "s1", [js.Record("Кедр.xlsx", "", "10:05:00", updated=1000)], FILES, "т-1", "2025-09-21T10:13:00")
    assert z["judge"]["s1"]["Кедр.xlsx"]["leave"] == "10:12:00"  # запоздавшая старая присылка не откатывает


def test_phone_marks_sent_only_what_was_sent():
    """Правки.md, п. 10: после ответа ноутбука «отправлено» — по снимку того, что ушло, а не по текущей записи
    (иначе правка во время медленной отправки теряется с галочкой «✓ на ноутбуке»)."""
    from pathlib import Path

    html = (Path(js.__file__).parent / "web" / "templates" / "judge.html").read_text(encoding="utf-8")
    sync = html[html.index("function sync()"):html.index("function change(")]
    assert "st.sent[r.file] = r.updated" not in sync
    assert "sentAt[r.file] = r.updated" in sync and "sentAt[f]" in sync
    assert "if (done && pending().length) sync();" in sync  # изменённое за время запроса — сразу вдогонку
    assert "(l.updated || 0) > have" in html  # при загрузке: своё новее ноутбука — отправить снова


def test_leave_without_arrive_is_flagged():
    """Правки.md, п. 13: «Убыла» без «Прибыла» на телефоне больше не нажать; если такая запись пришла — «проверить»."""
    z = zdata()
    js.merge(z, "s1", [js.Record("Кедр.xlsx", "0", "", "10:12:00", updated=1)], FILES, "т", "2025-09-21T10:13:00")
    issues = js.judge_issues(z, stages_of(z), NAMES)
    assert any("убытие (10:12:00) без прибытия" in i.text and i.target == "cell:Кедр.xlsx:s1" for i in issues)


def test_judge_name_and_phone_go_to_stage_journal():
    """Правки.md, п. 12: судья указал себя на телефоне — ФИО и номер в журнале этапа у каждой записи; по этапу —
    кто и когда был на связи; сменились судьи — видны оба."""
    z = {"stages": [{"id": "s1", "name": "Узлы"}], "teams": {}}
    ivan = {"fio": "Иванов  Иван Иванович", "phone": "+7 913 123-45-67", "лишнее": "x"}
    js.merge(z, "s1", [js.Record("Кедр.xlsx", "5", updated=1)], {"Кедр.xlsx"}, "т-1", "2026-10-03T10:00:00",
             judge=ivan)
    rec = z["judge"]["s1"]["Кедр.xlsx"]
    assert (rec["judge"], rec["judge_phone"]) == ("Иванов Иван Иванович", "+7 913 123-45-67")
    assert js.who(rec) == "Иванов Иван Иванович, +7 913 123-45-67"
    js.merge(z, "s1", [js.Record("Кедр.xlsx", "6", updated=2)], {"Кедр.xlsx"}, "т-2", "2026-10-03T12:00:00",
             judge={"fio": "Петрова Анна", "phone": ""})
    assert [w["fio"] for w in js.stage_judges(z, "s1")] == ["Петрова Анна", "Иванов Иван Иванович"]
    assert js.who(z["judge"]["s1"]["Кедр.xlsx"]) == "Петрова Анна"
    js.merge(z, "s1", [js.Record("Кедр.xlsx", "7", updated=3)], {"Кедр.xlsx"}, "т-3", "2026-10-03T12:05:00")
    assert js.who(z["judge"]["s1"]["Кедр.xlsx"]) == "" and len(js.stage_judges(z, "s1")) == 2  # судья не указан
    assert js.phone_same("+7 913 123-45-67", "89131234567") and not js.phone_same("+7 913 123-45-67", "")
