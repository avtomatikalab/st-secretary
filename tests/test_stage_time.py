"""ПСР: время на этапе, временной штраф по НВ и КВ, итог этапа «тех. + время» с телефона судьи
(Правки.md, п. 3–5; Правила 2021, дистанции комбинированные, п. 1.5)."""

from fractions import Fraction

from st_secretary import judge_sync as js
from st_secretary import stage_time as stt
from st_secretary.psr_run import stages_of

FILES = {"Кедр.xlsx", "Сосна.xlsx"}


def zdata(**kw):
    stage = {"id": "s1", "tour": "Тур 1", "name": "Навесная переправа", "nv": "5", "kv": "10", "tsh": "20", "vsh": "10"}
    stage.update(kw)
    return {"stages": [stage, {"id": "s2", "tour": "Тур 1", "name": "Узлы"}], "teams": {}}


def test_stage_settings_nv_tsh_vsh_and_rule():
    s1, s2 = stages_of(zdata(vsh_n="2", vsh_m="60"))
    assert s1.auto and s1.nv_minutes == 5 and s1.kv_minutes == 10
    assert s1.max_penalty == 30  # МШ = ТШ + ВШ
    assert (s1.vsh_points, s1.vsh_step) == (2, 60)
    assert not s2.auto  # без НВ и ВШ — итог этапа вносят как раньше
    assert stages_of(zdata(max="25"))[0].max_penalty == 25  # МШ задан явно
    assert (stages_of(zdata())[0].vsh_points, stages_of(zdata())[0].vsh_step) == (1, 30)  # по умолчанию 1 балл / 30 с


def test_stage_seconds_minus_waiting_and_over_midnight():
    assert stt.stage_seconds({"arrive": "10:00:00", "leave": "10:07:12", "cutoff": "1:00"}) == 372
    assert stt.stage_seconds({"arrive": "23:58:00", "leave": "00:03:00"}) == 300
    assert stt.stage_seconds({"arrive": "10:00:00"}) is None


def test_time_penalty_after_nv_capped_by_vsh():
    s = stages_of(zdata())[0]
    assert stt.time_penalty(s, 300) == 0  # ровно НВ — штрафа нет
    assert stt.time_penalty(s, 301) == 1  # неполные 30 с — за полные (по умолчанию)
    assert stt.time_penalty(s, 330) == 1 and stt.time_penalty(s, 331) == 2
    assert stt.time_penalty(s, 301, full_intervals=False) == 0 and stt.time_penalty(s, 330, full_intervals=False) == 1
    assert stt.time_penalty(s, 5000) == 10  # не больше ВШ


def test_score_tech_plus_time_or_max_penalty():
    s = stages_of(zdata())[0]
    sc = stt.score(s, {"points": "10", "arrive": "10:00:00", "leave": "10:07:12"})
    assert sc.total == 15 and sc.text == "тех. 10 + время 5 = 15 (на этапе 7:12)"  # 132 с сверх НВ → 5 × 30 с
    assert stt.score(s, {"points": "-5", "arrive": "10:00:00", "leave": "10:04:00"}).total == -5  # премия, без ВШ
    over = stt.score(s, {"points": "0", "arrive": "10:00:00", "leave": "10:10:01"})
    assert over.total == 30 and over.text.startswith("превышено КВ")
    assert stt.score(s, {"removed": True}).total == 30  # снята с этапа — МШ
    assert stt.score(s, {"points": "", "arrive": "10:00:00"}).total is None  # ещё на этапе


def test_phone_record_goes_to_table_as_tech_plus_time():
    z = zdata()
    stage = stages_of(z)[0]
    rec = js.Record("Кедр.xlsx", "10", "10:00:00", "10:07:12", cutoff="1:00", updated=1)  # 6:12 на этапе → 72 с → 3
    res = js.merge(z, "s1", [rec], FILES, "телефон", "2026-10-03T10:09:00", stage=stage, distance_cutoffs=False)
    assert res["conflicts"] == 0 and z["teams"]["Кедр.xlsx"]["points"]["s1"] == "13"
    assert "cutoffs" not in z["teams"]["Кедр.xlsx"]  # ПСР: отсечки этапа — не в колонку «Отсечки» дистанции
    assert js.from_phone(z, "s1", "Кедр.xlsx", stage)
    assert js.stage_score_text(z, stage, "Кедр.xlsx").startswith("тех. 10 + время 3 = 13")

    z["teams"]["Кедр.xlsx"]["points"]["s1"] = "20"  # протест: секретарь вписал своё
    rec2 = js.Record("Кедр.xlsx", "12", "10:00:00", "10:07:12", cutoff="1:00", updated=2)
    res = js.merge(z, "s1", [rec2], FILES, "телефон", "2026-10-03T10:10:00", stage=stage, distance_cutoffs=False)
    assert res["conflicts"] == 1 and z["teams"]["Кедр.xlsx"]["points"]["s1"] == "20"  # не затирается
    issues = js.judge_issues(z, stages_of(z), {"Кедр.xlsx": "Кедр"})
    assert any("тех. 12 + время 3 = 15" in i.text and "в таблице 20" in i.text for i in issues)


def test_settings_change_recounts_program_totals_only():
    z = zdata()
    stage = stages_of(z)[0]
    js.merge(z, "s1", [js.Record("Кедр.xlsx", "10", "10:00:00", "10:07:12", updated=1),
                       js.Record("Сосна.xlsx", "0", "10:10:00", "10:15:00", updated=1)],
             FILES, "телефон", "2026-10-03T10:20:00", stage=stage, distance_cutoffs=False)
    z["teams"]["Сосна.xlsx"]["points"]["s1"] = "7"  # у «Сосны» — ручное значение
    z["stages"][0]["nv"] = "6"  # НВ поправили: 72 с сверх НВ → 3
    assert stt.refresh(z, stages_of(z)[0]) == 1  # «Сосна»: ручное 7 при расчёте 0 — расхождение, не затираем
    assert z["teams"]["Кедр.xlsx"]["points"]["s1"] == "13" and z["teams"]["Сосна.xlsx"]["points"]["s1"] == "7"
    z["vsh_round"] = "down"  # неполный интервал не считать: 72 с → 2
    stt.refresh(z, stages_of(z)[0])
    assert z["teams"]["Кедр.xlsx"]["points"]["s1"] == "12"


def test_time_discipline_cutoffs_still_go_to_distance_column():
    z = {"stages": [{"id": "s1", "name": "Колодец"}], "teams": {}}
    js.merge(z, "s1", [js.Record("Кедр.xlsx", "", "10:00:00", "10:20:00", cutoff="3:00", updated=1)], FILES,
             "телефон", "2026-10-03T10:30:00", removal_mark="с")
    assert z["teams"]["Кедр.xlsx"]["cutoffs"] == "3:00"
    assert Fraction(stt.stage_seconds({"arrive": "10:00:00", "leave": "10:20:00", "cutoff": "3:00"})) == 17 * 60


def test_waiting_closed_together_with_leave_is_flagged():
    """Правки.md, п. 7: «ждёт очереди», «Начала этап» не нажато, «Убыла» — время на этапе 0; секретарю — «проверить»."""
    s = stages_of(zdata())[0]
    forgot = {"points": "0", "arrive": "10:00:00", "leave": "10:20:00", "cutoff": "20:00", "started": "10:20:00"}
    sc = stt.score(s, forgot)
    assert sc.total == 0 and "ожидание очереди" in sc.check and "Начала этап" in sc.check
    old_version = {**forgot, "started": ""}  # закрыто прежней версией телефона — тоже видно по времени 0
    assert stt.waiting_check(old_version)
    fine = {**forgot, "started": "10:12:00", "cutoff": "12:00"}  # начали в 10:12 — 8 мин на этапе, ВШ 6
    assert stt.score(s, fine).total == 6 and stt.score(s, fine).check == ""
    z = zdata()
    js.merge(z, "s1", [js.Record("Кедр.xlsx", **{k: v for k, v in forgot.items()}, updated=1)], FILES, "т",
             "2026-10-03T10:21:00", stage=stages_of(z)[0], distance_cutoffs=False)
    assert z["judge"]["s1"]["Кедр.xlsx"]["started"] == "10:20:00"
    issues = js.judge_issues(z, stages_of(z), {"Кедр.xlsx": "Кедр"})
    assert any("ожидание очереди" in i.text and i.target == "cell:Кедр.xlsx:s1" for i in issues)


def test_leave_before_arrive_is_a_typo_not_midnight():
    """Правки.md, п. 9: прибыла 14:25, убыла 14:15 — не «23:50 через полночь» и не МШ, а «проверьте время»."""
    s = stages_of(zdata())[0]
    typo = {"points": "0", "arrive": "14:25:00", "leave": "14:15:00"}
    assert stt.stage_seconds(typo) is None
    sc = stt.score(s, typo)
    assert sc.total is None and "раньше прибытия" in sc.check
    assert stt.stage_seconds({"arrive": "23:58:00", "leave": "00:03:00"}) == 300  # ночью — через полночь можно
    z = zdata()
    stage = stages_of(z)[0]
    js.merge(z, "s1", [js.Record("Кедр.xlsx", "0", "14:10:00", "14:15:00", updated=1)], FILES, "т",
             "2026-10-03T14:16:00", stage=stage, distance_cutoffs=False)
    assert z["teams"]["Кедр.xlsx"]["points"]["s1"] == "0"
    js.merge(z, "s1", [js.Record("Кедр.xlsx", "0", "14:25:00", "14:15:00", updated=2)], FILES, "т",
             "2026-10-03T14:17:00", stage=stage, distance_cutoffs=False)  # прибытие «исправили» с опечаткой
    assert "s1" not in z["teams"]["Кедр.xlsx"]["points"]  # ни МШ, ни прежнего итога — пусто, пока не исправят
    issues = js.judge_issues(z, stages_of(z), {"Кедр.xlsx": "Кедр"})
    assert any("раньше прибытия" in i.text and i.target == "cell:Кедр.xlsx:s1" for i in issues)


def test_waiting_not_subtracted_from_stage_time():
    """Правки.md, п. 3: настройка зачёта «ожидание очереди — не вычитать»: время этапа — от прибытия до убытия;
    смена настройки пересчитывает итоги программы, ручные итоги секретаря не трогает."""
    rec = {"points": "10", "arrive": "10:00:00", "leave": "10:07:12", "cutoff": "1:00"}
    assert stt.stage_seconds(rec) == 372 and stt.stage_seconds(rec, subtract_wait=False) == 432
    z = zdata()
    stage = stages_of(z)[0]
    js.merge(z, "s1", [js.Record("Кедр.xlsx", "10", "10:00:00", "10:07:12", cutoff="1:00", updated=1),
                       js.Record("Сосна.xlsx", "0", "10:10:00", "10:15:00", cutoff="2:00", updated=1)],
             FILES, "телефон", "2026-10-03T10:20:00", stage=stage, distance_cutoffs=False)
    assert z["teams"]["Кедр.xlsx"]["points"]["s1"] == "13"  # вычитать (по умолчанию): 6:12 — 72 с сверх НВ → 3
    z["teams"]["Сосна.xlsx"]["points"]["s1"] = "7"  # у «Сосны» — ручное значение
    z["wait_cut"] = "no"
    stt.refresh(z, stage)
    assert z["teams"]["Кедр.xlsx"]["points"]["s1"] == "15"  # 7:12 — 132 с сверх НВ → 5
    assert z["teams"]["Сосна.xlsx"]["points"]["s1"] == "7"  # ручное не трогаем
    assert js.stage_score_text(z, stage, "Кедр.xlsx").startswith("тех. 10 + время 5 = 15 (на этапе 7:12)")
    forgot = {"points": "0", "arrive": "10:00:00", "leave": "10:20:00", "cutoff": "20:00", "started": "10:20:00"}
    assert stt.score_for(z, stage, forgot).check == ""  # ожидание не вычитается — «Начала этап» не важно
    z["wait_cut"] = "yes"
    stt.refresh(z, stage)
    assert z["teams"]["Кедр.xlsx"]["points"]["s1"] == "13"


def test_waiting_not_subtracted_not_in_distance_cutoffs():
    """Правки.md, п. 3: «не вычитать» — ожидание с телефонов не идёт в «Отсечки» дистанции (спелео, пешеходные);
    переключили — колонка пересчитывается, вписанное секретарём остаётся."""
    z = {"stages": [{"id": "s1", "name": "Колодец"}], "teams": {}, "wait_cut": "no"}
    js.merge(z, "s1", [js.Record("Кедр.xlsx", "", "10:00:00", "10:20:00", cutoff="3:00", updated=1),
                       js.Record("Сосна.xlsx", "", "10:05:00", "10:25:00", cutoff="2:00", updated=1)], FILES,
             "телефон", "2026-10-03T10:30:00", removal_mark="с")
    assert "cutoffs" not in z["teams"]["Кедр.xlsx"] and "cutoffs" not in z["teams"]["Сосна.xlsx"]
    z["teams"]["Сосна.xlsx"]["cutoffs"] = "5:00"  # секретарь вписал своё
    assert not any("отсечки" in i.text for i in js.judge_issues(z, stages_of(z), {}))  # не вычитается — не сверяем
    z["wait_cut"] = "yes"
    js.refresh_cutoffs(z)
    assert z["teams"]["Кедр.xlsx"]["cutoffs"] == "3:00" and z["teams"]["Сосна.xlsx"]["cutoffs"] == "5:00"
    assert any("отсечки с телефонов судей — 2:00" in i.text for i in js.judge_issues(z, stages_of(z), {}))
    z["wait_cut"] = "no"
    js.refresh_cutoffs(z)
    assert "cutoffs" not in z["teams"]["Кедр.xlsx"] and z["teams"]["Сосна.xlsx"]["cutoffs"] == "5:00"
