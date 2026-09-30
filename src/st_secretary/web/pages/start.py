"""Жеребьёвка и стартовые протоколы: способ, время старта, порядок, публикация."""

from __future__ import annotations

import re
import secrets
from datetime import date
from pathlib import Path
from urllib.parse import urlencode

from fastapi import Request
from fastapi.responses import FileResponse
from starlette.exceptions import HTTPException

from st_secretary import commission as cm
from st_secretary import start_list as sl
from st_secretary import time_run as tr
from st_secretary.exporters import start_protocol as sp
from st_secretary.reference import norm_edition
from st_secretary.web.common import XLSX, _base, _parse_dt, _redirect, _with_done
from st_secretary.web.store import CompFolder, safe_name


def register(app, cx) -> None:
    comp_ctx = cx.comp_ctx
    folder = cx.folder
    page = cx.page
    PROTEST_HOUR = cx.PROTEST_HOUR  # из pages/results.py
    def _save_zachet(*a, **k):  # из pages/results.py
        return cx._save_zachet(*a, **k)
    def need_comp(*a, **k):  # из pages/preapps.py
        return cx.need_comp(*a, **k)
    def need_zachet(*a, **k):  # из pages/results.py
        return cx.need_zachet(*a, **k)
    def zachet_inputs(*a, **k):  # из pages/results.py
        return cx.zachet_inputs(*a, **k)

    # ------------------------------------------------------------ жеребьёвка и стартовые протоколы

    def start_ctx(f: CompFolder, comp, z, teams: list | None = None):
        """(данные зачёта, команды, ранги составов, стартовый протокол)."""
        zdata = f.run_data().get("zachety", {}).get(z.key, {})
        teams = zachet_inputs(f, comp, z) if teams is None else teams
        try:
            norms = norm_edition(comp.norms_edition)
        except KeyError:
            norms = None
        fmt = z.rank_format
        ranks = {t.file: sl.team_rank(t.members, fmt, norms) for t in teams}
        return zdata, teams, ranks, sl.build(z, zdata, teams, ranks, start_default(f, comp)[0])

    def start_default(f: CompFolder, comp) -> tuple[date, str]:
        """День и время старта по умолчанию — «Начало соревнований» у комиссии по допуску или первый день."""
        adm = _parse_dt(cm.settings(f.admission())["start_at"])
        if adm is None:
            return comp.date_from, ""
        return adm.date(), f"{adm:%H:%M}" if adm.hour or adm.minute else ""

    def start_name(z) -> str:
        return f"Стартовый протокол {safe_name(z.key.replace('/', '-'))}.xlsx"

    @app.get("/c/{cid}/start")
    def start_page(request: Request, cid: str, z: str = ""):
        f = folder(cid)
        comp = need_comp(f)
        if not comp.zachety:
            return page(request, "start.html", active="start", zachet=None, **comp_ctx(f))
        zz = need_zachet(comp, z)
        _, teams, _, lst = start_ctx(f, comp, zz)
        pub = _parse_dt(lst.published["at"]) if lst.published else None
        day, first = start_default(f, comp)
        return page(request, "start.html", active="start", zachet=zz, zachety=comp.zachety, sl=lst,
                    methods=sl.METHODS, hm=sl.hm_text, rank_text=sl.rank_word, draw_line=sp.draw_line(lst),
                    zq=urlencode({"z": zz.key}), pub_at=pub, until=pub + PROTEST_HOUR if pub else None,
                    defaults={"day": day.isoformat(), "first": first}, is_time=tr.is_time_discipline(zz),
                    publish_by=lst.first_start - PROTEST_HOUR if lst.first_start else None, now=app.state.clock(),
                    not_admitted=[t.team for t in teams if not t.admitted], **comp_ctx(f))

    @app.post("/c/{cid}/start/draw")
    async def start_draw(request: Request, cid: str, z: str = ""):
        """Настройки жеребьёвки и времени старта; с action=draw — провести жеребьёвку (порядок заново)."""
        f = folder(cid)
        comp = need_comp(f)
        zz = need_zachet(comp, z)
        form = await request.form()
        back = f"{_base(f)}/start?{urlencode({'z': zz.key})}"
        first = str(form.get("first", "")).strip()
        try:
            sl.parse_hm(first)
        except ValueError:
            return _redirect(_with_done(back + "#times", "start_bad_time"))
        method = str(form.get("method", "random"))
        new = {"method": method if method in sl.METHODS else "random", "groups": str(form.get("groups", "2")).strip(),
               "strong": "first" if form.get("strong") == "first" else "last",
               "day": str(form.get("day", "")).strip(), "first": first,
               "interval": str(form.get("interval", "")).strip().replace(",", ".")}
        new = {k: v for k, v in new.items() if k in form}  # у жеребьёвки и времени старта — разные формы
        drawing = form.get("action") == "draw"
        order, seed, groups = [], None, {}
        if drawing:
            zdata, teams, ranks, _ = start_ctx(f, comp, zz)
            admitted = [t for t in teams if t.admitted]
            if not admitted:
                return _redirect(_with_done(back, "start_empty"))
            st = sl.settings({"draw": {**zdata.get("draw", {}), **new}})
            seed = secrets.randbelow(900000) + 100000  # шесть цифр — легко записать и проверить
            order, groups = sl.draw_groups(admitted, st["method"], seed, ranks, st["groups"], st["strong"] == "last")
        now = app.state.clock()

        def update(d):
            dr = d.setdefault("draw", {})
            dr.update(new)
            if drawing:
                dr.update(order=order, at=now.isoformat(timespec="minutes"), seed=seed, done_method=new["method"],
                          groups_of=groups)
                for k in ("times", "edited"):  # ручные правки — от прежнего порядка
                    dr.pop(k, None)

        _save_zachet(f, zz.key, update)
        return _redirect(_with_done(back + ("#order" if drawing else "#times"),
                                    "start_drawn" if drawing else "start_times"))

    @app.post("/c/{cid}/start/order")
    async def start_order(request: Request, cid: str, z: str = ""):
        """Порядок вручную (номера, вытянутые на жеребьёвке, или перестановка) и время старта отдельных команд."""
        f = folder(cid)
        comp = need_comp(f)
        zz = need_zachet(comp, z)
        form = await request.form()
        _, _, _, lst = start_ctx(f, comp, zz)
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

        _save_zachet(f, zz.key, update)
        return _redirect(_with_done(f"{_base(f)}/start?{urlencode({'z': zz.key})}#order", "start_saved"))

    @app.post("/c/{cid}/start/publish")
    def start_publish(cid: str, z: str = ""):
        """Стартовый протокол в Excel; время публикации — начало часа на протесты по допуску (п. 8.17)."""
        f = folder(cid)
        comp = need_comp(f)
        zz = need_zachet(comp, z)
        _, _, _, lst = start_ctx(f, comp, zz)
        back = f"{_base(f)}/start?{urlencode({'z': zz.key})}#publish"
        if not lst.rows:
            return _redirect(_with_done(back, "start_empty"))
        now = app.state.clock()
        f.protocols_dir.mkdir(exist_ok=True)
        path = f.protocols_dir / start_name(zz)
        try:
            sp.write_start_protocol(comp, lst, path, now)
        except PermissionError:
            return _redirect(_with_done(back, "doc_locked"))

        def update(d):
            dr = d.setdefault("draw", {})
            if not dr.get("at"):  # без жеребьёвки — по номерам: порядок фиксируется публикацией
                dr.update(order=[r.inp.file for r in lst.rows], at=now.isoformat(timespec="minutes"),
                          done_method="number")
            dr["published"] = {"at": now.isoformat(timespec="minutes"), "fp": lst.fingerprint, "file": path.name}

        _save_zachet(f, zz.key, update)
        app.state.opener(path)
        return _redirect(_with_done(back, "start_published", until=f"{now + PROTEST_HOUR:%H:%M}"))

    @app.get("/c/{cid}/start/file")
    def start_file(cid: str, z: str = ""):
        f = folder(cid)
        zz = need_zachet(need_comp(f), z)
        info = f.run_data().get("zachety", {}).get(zz.key, {}).get("draw", {}).get("published")
        p = f.protocols_dir / Path(info["file"]).name if info else None
        if p is None or not p.is_file():
            raise HTTPException(404)
        return FileResponse(p, filename=p.name, media_type=XLSX)

    cx.update(start_ctx=start_ctx)
