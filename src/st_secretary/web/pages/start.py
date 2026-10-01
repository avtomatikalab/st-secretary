"""Жеребьёвка и стартовые протоколы: способ, время старта, порядок, публикация."""

from __future__ import annotations

import re
import secrets
from dataclasses import replace
from datetime import date
from pathlib import Path
from urllib.parse import urlencode

from fastapi import Request
from fastapi.responses import FileResponse, JSONResponse
from starlette.exceptions import HTTPException

from st_secretary import start_list as sl
from st_secretary import time_run as tr
from st_secretary.exporters import start_protocol as sp
from st_secretary.rank import INDIVIDUAL, PAIR
from st_secretary.web.common import XLSX, base_url, parse_dt, redirect, with_done
from st_secretary.web.shared import PROTEST_HOUR, need_comp, need_zachet, save_zachet, start_ctx, start_default
from st_secretary.web.store import CompFolder, safe_name

DEFAULT_FIRST, DEFAULT_INTERVAL = "10:00", "5"  # время старта по умолчанию: первый старт, интервал в минутах


def register(app, cx) -> None:
    """Жеребьёвка и стартовые протоколы (/c/{cid}/start…), расписание стартов."""
    run_lock = cx.run_lock
    comp_ctx = cx.comp_ctx
    folder = cx.folder
    page = cx.page
    store = cx.store

    # ------------------------------------------------------------ жеребьёвка и стартовые протоколы



    def time_fields(f: CompFolder, comp, zdata: dict) -> dict:
        """Время старта в полях (Правки, п. 40): сохранённое, а пока его не сохраняли — настоящее значение по
        умолчанию (не серая подсказка): «Начало соревнований» комиссии по допуску, иначе первый день и 10:00;
        интервал — 5 мин. Пустое сохранённое время — «без времени, только очерёдность»."""
        day, first = start_default(f, comp)
        saved = zdata.get("draw", {})
        return {"day": str(saved.get("day") or day.isoformat()),
                "first": str(saved["first"]) if "first" in saved else first or DEFAULT_FIRST,
                "interval": str(saved["interval"]) if "interval" in saved else DEFAULT_INTERVAL,
                "saved": "first" in saved}

    def start_break(f: CompFolder) -> int:
        """Перерыв между стартами одного участника в разных зачётах, мин (для всего соревнования; на фестивале — для
        всех его соревнований)."""
        fest = f.festival()
        v = str((fest or {}).get("start_break", "") if fest else f.run_data().get("start_break", "")).strip()
        return int(v) if v.isdigit() else 0

    def expected_secs(f: CompFolder, z) -> int | None:
        """Расчётное время дистанции зачёта (задаётся в дисциплинах по времени), с."""
        v = str(f.run_data().get("zachety", {}).get(z.key, {}).get("expected", "")).strip()
        return int(v) * 60 if v.isdigit() else None

    def all_lists(f: CompFolder, comp) -> list[tuple]:
        """Стартовые протоколы всех зачётов соревнования и — на фестивале — его остальных соревнований:
        [(папка, карточка, зачёт, протокол, расчётное время, с)]."""
        out = [(f, comp, x, start_ctx(store, f, comp, x)[3], expected_secs(f, x)) for x in comp.zachety]
        fest = f.festival()
        for m in (fest or {}).get("members", []):
            g = store.get(m) if m != f.id else None
            try:
                other = g.load() if g else None
            except Exception:  # noqa: BLE001 — карточка не читается: её стартов не видно
                other = None
            for x in (other.zachety if other is not None else []):
                lst = start_ctx(store, g, other, x)[3]
                # зачёт другого соревнования: свой ключ и название с соревнованием — «М/Ж_3 (Кубок …)»
                lst.zachet = replace(x, zid=f"{g.id}/{x.key}", name=f"{x.title} ({other.title})")
                out.append((g, other, x, lst, expected_secs(g, x)))
        return out

    def others_busy(f: CompFolder, comp, z) -> dict:
        """Старты людей в других зачётах (и соревнованиях фестиваля) — для перерыва участника."""
        return sl.busy_starts([(lst, exp) for g, _, x, lst, exp in all_lists(f, comp)
                               if not (g.id == f.id and x.key == z.key)])

    def start_name(z) -> str:
        return f"Стартовый протокол {safe_name(z.key.replace('/', '-'))}.xlsx"

    @app.get("/c/{cid}/start")
    def start_page(request: Request, cid: str, z: str = ""):
        """Страница жеребьёвки зачёта: способ, время старта, порядок, перерыв участника, публикация."""
        f = folder(cid)
        comp = need_comp(f)
        if not comp.zachety:
            return page(request, "start.html", active="start", zachet=None, **comp_ctx(f))
        zz = need_zachet(comp, z)
        _, teams, _, lst = start_ctx(store, f, comp, zz)
        brk = start_break(f)
        everything = all_lists(f, comp)
        if len(everything) > 1:  # один человек в нескольких зачётах — не слишком близко (Правки, п. 25.3)
            lists = [(lst if g.id == f.id and x.key == zz.key else s, exp) for g, _, x, s, exp in everything]
            lst.issues += [i for keys, i in sl.person_conflicts(lists, brk) if zz.key in keys]
        pub = parse_dt(lst.published["at"]) if lst.published else None
        zdata = f.run_data().get("zachety", {}).get(zz.key, {})
        return page(request, "start.html", active="start", zachet=zz, zachety=comp.zachety, sl=lst,
                    methods=sl.METHODS, hm=sl.hm_text, rank_text=sl.rank_word, draw_line=sp.draw_line(lst),
                    zq=urlencode({"z": zz.key}), pub_at=pub, until=pub + PROTEST_HOUR if pub else None,
                    times=time_fields(f, comp, zdata), is_time=tr.is_time_discipline(zz),
                    publish_by=lst.first_start - PROTEST_HOUR if lst.first_start else None, now=app.state.clock(),
                    not_admitted=[t.team for t in teams if not t.admitted], start_break=brk,
                    fit_notes=zdata.get("draw", {}).get("fit", []),
                    **comp_ctx(f))

    @app.post("/c/{cid}/start/draw")
    async def start_draw(request: Request, cid: str, z: str = ""):
        """Настройки жеребьёвки и времени старта; с action=draw — провести жеребьёвку (порядок заново)."""
        f = folder(cid)
        comp = need_comp(f)
        zz = need_zachet(comp, z)
        form = await request.form()
        back = f"{base_url(f)}/start?{urlencode({'z': zz.key})}"
        first = str(form.get("first", "")).strip()
        try:
            sl.parse_hm(first)
        except ValueError:
            return redirect(with_done(back + "#times", "start_bad_time"))
        method = str(form.get("method", "random"))
        new = {"method": method if method in sl.METHODS else "random", "groups": str(form.get("groups", "2")).strip(),
               "strong": "first" if form.get("strong") == "first" else "last",
               "day": str(form.get("day", "")).strip(), "first": first,
               "interval": str(form.get("interval", "")).strip().replace(",", ".")}
        new = {k: v for k, v in new.items() if k in form}  # у жеребьёвки и времени старта — разные формы
        if "spread_set" in form:  # галочка «развести делегацию» (снятая галочка в форму не приходит)
            new["spread"] = bool(form.get("spread"))
        if "break" in form:  # перерыв между стартами участника — для всего соревнования (на фестивале — для всех)
            brk = str(form.get("break", "")).strip()
            value = int(brk) if brk.isdigit() and int(brk) <= 600 else 0
            if fest := f.festival():
                store.update_festival(fest["id"], lambda x: x.__setitem__("start_break", value))
            else:
                with cx.run_lock:
                    data = f.run_data()
                    data["start_break"] = value
                    f.save_run_data(data)
        drawing = form.get("action") == "draw"
        zdata, teams, ranks, lst = start_ctx(store, f, comp, zz)
        if drawing:  # время старта не задавали — жеребьёвка берёт значения по умолчанию из полей (Правки, п. 40)
            defaults = time_fields(f, comp, zdata)
            new.update({k: defaults[k] for k in ("first", "interval") if k not in new})
        st = sl.settings({"draw": {**zdata.get("draw", {}), **new}})
        order, seed, groups, blocks = [r.inp.file for r in lst.rows], None, {}, None
        units = {t.file: t for t in teams if t.admitted}
        if drawing:
            if not units:
                return redirect(with_done(back, "start_empty"))
            seed = secrets.randbelow(900000) + 100000  # шесть цифр — легко записать и проверить
            order, groups = sl.draw_groups(list(units.values()), st["method"], seed, ranks, st["groups"],
                                           st["strong"] == "last")
            blocks = sl.blocks_of(order, st["method"], groups, ranks)  # переставлять — не ломая жребий
            if st["spread"]:  # команды одной делегации — как можно дальше друг от друга (п. 25.4)
                order = sl.spread(order, {f_: sl.delegation_of(t) for f_, t in units.items()}, blocks)
        # перерыв участника (п. 25.3): после жеребьёвки — перестановка соседей или сдвиг; при смене времени — сдвиг
        brk, times, notes = start_break(f), {}, []
        if brk and (drawing or {"first", "interval", "day"} & set(new)) and units:
            try:
                first = sl.parse_hm(st["first"])
            except ValueError:
                first = None
            iv = st["interval"].strip().replace(",", ".")
            interval = round(float(iv) * 60) if re.fullmatch(r"\d+(\.\d+)?", iv) else 0
            day = date.fromisoformat(st["day"]) if re.fullmatch(r"\d{4}-\d\d-\d\d", st["day"]) else \
                start_default(f, comp)[0]
            order, times, notes = sl.fit_break([x for x in order if x in units], units, day, first, interval,
                                               others_busy(f, comp, zz), brk, expected_secs(f, zz), blocks)
        now = app.state.clock()

        def update(d):
            dr = d.setdefault("draw", {})
            dr.update(new)
            if drawing:
                dr.update(order=order, at=now.isoformat(timespec="minutes"), seed=seed, done_method=new["method"],
                          groups_of=groups)
                for k in ("times", "edited"):  # ручные правки — от прежнего порядка
                    dr.pop(k, None)
            if drawing or notes:
                dr["fit"] = notes
            if times:
                dr["times"] = {**dr.get("times", {}), **{k: sl.hm_text(v) for k, v in times.items()}}

        save_zachet(run_lock, f, zz.key, update)
        done = ("start_drawn" if drawing else "start_times") + ("_fit" if notes else "")
        if drawing:  # в сообщении — с какого времени и через сколько стартуют
            return redirect(with_done(back + "#order", done, first=st["first"], interval=st["interval"]))
        return redirect(with_done(back + "#times", done))

    @app.post("/c/{cid}/start/order")
    async def start_order(request: Request, cid: str, z: str = ""):
        """Порядок вручную (номера, вытянутые на жеребьёвке, или перестановка) и время старта отдельных команд."""
        f = folder(cid)
        comp = need_comp(f)
        zz = need_zachet(comp, z)
        form = await request.form()
        _, _, _, lst = start_ctx(store, f, comp, zz)
        known = {r.inp.file for r in lst.rows}
        rows = []
        for i in range(len(lst.rows)):
            file = str(form.get(f"file-{i}", ""))
            if file not in known:
                continue
            pos = re.sub(r"\D", "", str(form.get(f"pos-{i}", "")))
            rows.append((int(pos) if pos else 10**6, i, file, str(form.get(f"time-{i}", "")).strip()))
        rows.sort()
        order = [r[2] for r in rows]
        times = {r[2]: r[3] for r in rows if r[3]}
        before = [r.inp.file for r in lst.rows]
        now = app.state.clock()

        def update(d):
            dr = d.setdefault("draw", {})
            if not dr.get("at"):  # жеребьёвку провели на совещании — порядок внесён вручную
                dr.update(at=now.isoformat(timespec="minutes"), done_method="manual", method="manual")
            elif order != before and dr.get("done_method") != "manual":
                dr["edited"] = now.isoformat(timespec="minutes")
            dr["order"] = order + [x for x in dr.get("order", []) if x not in order]
            dr["times"] = times

        save_zachet(run_lock, f, zz.key, update)
        return redirect(with_done(f"{base_url(f)}/start?{urlencode({'z': zz.key})}#order", "start_saved"))

    @app.post("/c/{cid}/start/publish")
    def start_publish(cid: str, z: str = ""):
        """Стартовый протокол в Excel; время публикации — начало часа на протесты по допуску (п. 8.17)."""
        f = folder(cid)
        comp = need_comp(f)
        zz = need_zachet(comp, z)
        _, _, _, lst = start_ctx(store, f, comp, zz)
        back = f"{base_url(f)}/start?{urlencode({'z': zz.key})}#publish"
        if not lst.rows:
            return redirect(with_done(back, "start_empty"))
        now = app.state.clock()
        f.protocols_dir.mkdir(exist_ok=True)
        path = f.protocols_dir / start_name(zz)
        try:
            sp.write_start_protocol(comp, lst, path, now)
        except PermissionError:
            return redirect(with_done(back, "doc_locked"))

        def update(d):
            dr = d.setdefault("draw", {})
            if not dr.get("at"):  # без жеребьёвки — по номерам: порядок фиксируется публикацией
                dr.update(order=[r.inp.file for r in lst.rows], at=now.isoformat(timespec="minutes"),
                          done_method="number")
            dr["published"] = {"at": now.isoformat(timespec="minutes"), "fp": lst.fingerprint, "file": path.name}

        save_zachet(run_lock, f, zz.key, update)
        app.state.opener(path)
        return redirect(with_done(back, "start_published", until=f"{now + PROTEST_HOUR:%H:%M}"))

    @app.get("/c/{cid}/start/file")
    def start_file(cid: str, z: str = ""):
        f = folder(cid)
        zz = need_zachet(need_comp(f), z)
        info = f.run_data().get("zachety", {}).get(zz.key, {}).get("draw", {}).get("published")
        p = f.protocols_dir / Path(info["file"]).name if info else None
        if p is None or not p.is_file():
            raise HTTPException(404)
        return FileResponse(p, filename=p.name, media_type=XLSX)

    # ------------------------------------------------------------ наглядное расписание стартов (Правки, п. 25.5)

    def interval_secs(st: dict) -> int:
        iv = st["interval"].strip().replace(",", ".")
        return round(float(iv) * 60) if re.fullmatch(r"\d+(\.\d+)?", iv) else 0

    def schedule_data(f: CompFolder, comp) -> dict:
        """Все зачёты соревнования (на фестивале — всех его соревнований): старты, люди, делегации."""
        lanes = []
        for g, c, z, lst, exp in all_lists(f, comp):
            st = lst.settings
            try:
                first = sl.parse_hm(st["first"])
            except ValueError:
                first = None
            iv = interval_secs(st)
            lanes.append({
                "id": f"{g.id}/{z.key}", "cid": g.id, "comp": c.title, "zkey": z.key, "title": z.title,
                "day": lst.start_day.isoformat() if lst.start_day else "", "first": first, "interval": iv,
                "expected": exp or 0, "dur": exp or iv or 300, "block": z.rank_format in (INDIVIDUAL, PAIR),
                "spread": bool(st.get("spread")), "url": f"{base_url(g)}/start?{urlencode({'z': z.key})}",
                "rows": [{"file": r.inp.file, "name": r.inp.team, "num": str(r.inp.number or ""), "t": r.time,
                          "manual": r.manual_time, "people": [sl.person_key(m) for m in r.inp.members],
                          "fio": [m.fio for m in r.inp.members], "deleg": sl.delegation_of(r.inp)}
                         for r in lst.rows]})
        return {"break": start_break(f) * 60, "lanes": lanes}

    @app.get("/c/{cid}/schedule")
    def schedule_page(request: Request, cid: str):
        f = folder(cid)
        comp = need_comp(f)
        return page(request, "schedule.html", active="start", data=schedule_data(f, comp), **comp_ctx(f))

    @app.post("/c/{cid}/schedule")
    async def schedule_save(request: Request, cid: str):
        """«Сохранить» расписание: порядок и время старта в каждом зачёте (как «Порядок старта»), сдвиг блока —
        время первого старта зачёта."""
        f = folder(cid)
        comp = need_comp(f)
        body = await request.json()
        scope = {g.id: (g, c) for g, c, *_ in all_lists(f, comp)}
        now = app.state.clock()
        for lane in body.get("lanes", []) if isinstance(body, dict) else []:
            g, c = scope.get(str(lane.get("cid", "")), (None, None))
            z = next((x for x in (c.zachety if c else []) if x.key == lane.get("zkey")), None)
            if z is None:
                continue
            _, _, _, lst = start_ctx(store, g, c, z)
            known = [r.inp.file for r in lst.rows]
            order = [x for x in lane.get("order", []) if x in known]
            order += [x for x in known if x not in order]
            times = {}
            for k, v in (lane.get("times") or {}).items():
                try:
                    if k in known and sl.parse_hm(v) is not None:
                        times[k] = str(v)
                except ValueError:
                    pass
            first = str(lane.get("first") or "")
            try:
                first = first if sl.parse_hm(first) is not None else None
            except ValueError:
                first = None

            def update(d, order=order, times=times, first=first, before=known):
                dr = d.setdefault("draw", {})
                if not dr.get("at"):  # порядок задан в расписании — как внесённый вручную
                    dr.update(at=now.isoformat(timespec="minutes"), done_method="manual", method="manual")
                elif order != before and dr.get("done_method") != "manual":
                    dr["edited"] = now.isoformat(timespec="minutes")
                dr["order"] = order + [x for x in dr.get("order", []) if x not in order]
                dr["times"] = times
                if first:
                    dr["first"] = first

            save_zachet(run_lock, g, z.key, update)
        return JSONResponse({"ok": True, "saved": now.strftime("%H:%M:%S")})

