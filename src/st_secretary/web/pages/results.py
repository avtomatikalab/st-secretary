"""Протоколы этапов и результаты: этапы, баллы и время, загрузка из СЕКРЕТАРЬ_ST и SI Reader;
предварительный протокол → час на протесты → официальный."""

from __future__ import annotations

import re
import tempfile
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode

from fastapi import Request
from fastapi.responses import FileResponse, JSONResponse
from starlette.exceptions import HTTPException

from st_secretary import judge_sync as js
from st_secretary import psr_run as pr
from st_secretary import stage_time as stt
from st_secretary import time_run as tr
from st_secretary.disciplines import Status
from st_secretary.exporters import results_protocol as rp
from st_secretary.importers.si_reader import read_si_reader
from st_secretary.web.common import PROTEST_DECISIONS, XLSX, base_url, parse_dt, redirect, team_anchor, with_done
from st_secretary.web.shared import (
    PROTEST_HOUR,
    fingerprint,
    need_comp,
    need_zachet,
    protocol_state,
    run_ctx,
    save_zachet,
    zachet_inputs,
)
from st_secretary.web.store import CompFolder, safe_name


def register(app, cx) -> None:
    """Протоколы этапов и результаты (/c/{cid}/results…): этапы, баллы, SI, протесты, публикация и утверждение."""
    store = cx.store
    comp_ctx = cx.comp_ctx
    folder = cx.folder
    page = cx.page
    run_lock = cx.run_lock
    templates = cx.templates

    # ------------------------------------------------------------ протоколы этапов и результаты (ПСР)




    # дополнительные колонки таблицы по дисциплине: (поле, подпись)
    EXTRA_FIELDS = {"pedestrian": (("pen_time", "Штраф. время"),),
                    "nordic": (("pen_time", "Штраф. время"), ("red", "Кр. карт.")),
                    "mountain": (("declared", "Заявл. время"), ("no_tactics", "ТЗ"))}

    def results_parts(f: CompFolder, z, zdata: dict, run) -> dict:
        stage_by = {s.id: s for s in run.stages}
        return {"base": base_url(f), "z": z, "zdata": zdata, "run": run, "pt": pr.points_text, "ck": tr.clock_text,
                "tod": tr.time_of_day_text,
                "extra_fields": EXTRA_FIELDS.get(run.profile, ()) + tuple((f"add-{a['id']}", a["name"]) for a in run.adds),
                "add_points": {f"add-{a['id']}" for a in run.adds if a["kind"] == "points"}, "add_kinds": tr.ADD_KINDS,
                "res": lambda r: pr.result_text(run, r), "is_time": run.kind == "time",
                "from_phone": lambda sid, file: js.from_phone(zdata, sid, file, stage_by.get(sid)),
                "stage_score": lambda s, file: js.stage_score_text(zdata, s, file),
                "removal": lambda s, file: stt.removal(zdata, s, file),
                "judge_who": lambda sid, file: js.who(zdata.get("judge", {}).get(sid, {}).get(file, {})),
                "pen_text": lambda sid, file: js.pen_text(js.pens_of(zdata, sid, file)),
                "grid": sorted(run.rows, key=lambda r: r.start_order), "status_label": pr.STATUS_LABEL,
                "status_short": pr.STATUS_SHORT, "statuses": list(pr.STATUS_LABEL), "FINISHED": Status.FINISHED,
                "zq": urlencode({"z": z.key}), "pct": lambda x: f"{float(x):.2f}".replace(".", ",") if x is not None else ""}

    @app.get("/c/{cid}/results")
    def results_page(request: Request, cid: str, z: str = ""):
        f = folder(cid)
        comp = need_comp(f)
        if not comp.zachety:
            return page(request, "results.html", active="results", zachet=None, **comp_ctx(f))
        zz = need_zachet(comp, z)
        _, zdata, run = run_ctx(store, f, comp, zz)
        return page(request, "results.html", active="results", zachet=zz, zachety=comp.zachety,
                    state=protocol_state(zdata, run, app.state.clock()), decisions=PROTEST_DECISIONS,
                    **{**comp_ctx(f), **results_parts(f, zz, zdata, run)})


    @app.post("/c/{cid}/results/stages")
    async def results_stages(request: Request, cid: str, z: str = ""):
        """Этапы дистанции (тур, название, МШ), параметры дистанции для фактического класса, правило равенства."""
        f = folder(cid)
        zz = need_zachet(need_comp(f), z)
        form = await request.form()
        idx = sorted({int(m.group(1)) for k in form if (m := re.fullmatch(r"st-(\d+)-name", k))})

        def update(zdata):
            """Этапы, параметры дистанции и настройки расчёта — в данные зачёта."""
            used = {str(s.get("id")) for s in zdata.get("stages", [])}
            stages = []
            for i in idx:
                name = " ".join(str(form.get(f"st-{i}-name", "")).split())
                if not name:
                    continue
                sid = str(form.get(f"st-{i}-id", "")).strip()
                if not sid:
                    n = 1
                    while f"s{n}" in used:
                        n += 1
                    sid = f"s{n}"
                    used.add(sid)
                row = {"id": sid, "tour": " ".join(str(form.get(f"st-{i}-tour", "")).split()), "name": name,
                       "max": str(form.get(f"st-{i}-max", "")).strip(), "kv": str(form.get(f"st-{i}-kv", "")).strip()}
                for k in ("nv", "tsh", "vsh", "vsh_n", "vsh_m"):  # ПСР: временной штраф по НВ (stage_time.py)
                    v = str(form.get(f"st-{i}-{k}", "")).strip()
                    if v:
                        row[k] = v
                stages.append(row)
            zdata["stages"] = stages
            if "vsh_round" in form:
                zdata["vsh_round"] = "down" if form.get("vsh_round") == "down" else "up"
            if "wait_cut" in form:  # ожидание очереди на этапе: вычитать из времени или нет (Правки, п. 3)
                zdata["wait_cut"] = "no" if form.get("wait_cut") == "no" else "yes"
            for s in pr.stages_of(zdata):  # НВ, ВШ или правило поменяли — итоги этапов по записям судей заново
                stt.refresh(zdata, s)
            if tr.is_time_discipline(zz):  # спелео, пешеходные: отсечки с телефонов — в «Отсечки» или убрать
                js.refresh_cutoffs(zdata)
            zdata["distance"] = {k: str(form.get(k, "")).strip() for k in ("km", "modes", "kv_hours")}
            zdata["tie"] = "start" if form.get("tie") == "start" else "same"
            for k in ("spp", "expected", "kv", "cutoff_pairs"):  # по времени: эквивалент балла, расчётное время, КВ, отсечки SI
                if k in form:
                    zdata[k] = str(form.get(k, "")).strip()
            if "system" in form:  # пешеходные, северная ходьба: штрафная или бесштрафовая система
                zdata["system"] = "nopenalty" if form.get("system") == "nopenalty" else "penalty"
            if "removal" in form:  # снятие с этапа (пешеходные) или красная карточка (СХ): а) или б)
                zdata["removal"] = "okv" if form.get("removal") == "okv" else "dsq"
            if "removed_order" in form:
                zdata["removed_order"] = "count" if form.get("removed_order") == "count" else "after"
            if any(re.fullmatch(r"add-\d+-name", k) for k in form):  # составляющие результата (Правки, п. 32)
                taken = {str(a.get("id")) for a in zdata.get("adds", [])}
                adds = []
                for i in sorted({int(m.group(1)) for k in form if (m := re.fullmatch(r"add-(\d+)-name", k))}):
                    name = " ".join(str(form.get(f"add-{i}-name", "")).split())
                    if not name:
                        continue
                    aid = str(form.get(f"add-{i}-id", "")).strip()
                    if not aid:
                        n = 1
                        while f"a{n}" in taken:
                            n += 1
                        aid = f"a{n}"
                        taken.add(aid)
                    adds.append({"id": aid, "name": name,
                                 "kind": "points" if form.get(f"add-{i}-kind") == "points" else "time"})
                zdata["adds"] = adds

        save_zachet(run_lock, f, zz.key, update)
        return redirect(f"{base_url(f)}/results?{urlencode({'z': zz.key, 'done': 'run_stages'})}#stages")

    @app.post("/c/{cid}/results/import")
    async def results_import(request: Request, cid: str, z: str = ""):
        """Этапы и баллы из рабочей книги СЕКРЕТАРЬ_ST (лист «Протокол_группа»)."""
        f = folder(cid)
        comp = need_comp(f)
        zz = need_zachet(comp, z)
        up = (await request.form()).get("book")
        back = f"{base_url(f)}/results?{urlencode({'z': zz.key})}"
        if up is None or not getattr(up, "filename", ""):
            return redirect(with_done(back, "run_nofile"))
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / Path(up.filename).name
            p.write_bytes(await up.read())
            try:
                from st_secretary.importers.sekretar_xls import read_group_protocol
                sheet = read_group_protocol(p)
            except ImportError:
                return redirect(with_done(back, "res_noxlrd"))
            except Exception:  # noqa: BLE001 — не та книга: объяснить, а не упасть
                return redirect(with_done(back, "run_badbook"))
        imported, notes = pr.import_group_protocol(sheet, zachet_inputs(store, f, comp, zz))
        save_zachet(run_lock, f, zz.key, lambda zdata: zdata.update(imported))
        return redirect(with_done(back, "run_imported", n=str(len(imported["stages"])),
                                    t=str(len(imported["teams"])), notes=" ".join(notes)[:900]))

    @app.post("/c/{cid}/results/si")
    async def results_si(request: Request, cid: str, z: str = ""):
        """SPORTident Reader (si_reader.csv): старт, финиш и отсечки команд по чипам."""
        f = folder(cid)
        comp = need_comp(f)
        zz = need_zachet(comp, z)
        up = (await request.form()).get("csv")
        back = f"{base_url(f)}/results?{urlencode({'z': zz.key})}#points"
        if up is None or not getattr(up, "filename", ""):
            return redirect(with_done(back, "si_nofile"))
        try:
            cards = read_si_reader(await up.read())
        except ValueError as e:
            return redirect(with_done(back, "si_bad", why=str(e)))
        teams = zachet_inputs(store, f, comp, zz)
        out = {}
        save_zachet(run_lock, f, zz.key, lambda zdata: out.update(tr.apply_si(zdata, cards, teams)))
        return redirect(with_done(back, "si_done", n=str(len(cards)), t=str(out["teams"]),
                                    unknown=", ".join(out["unknown"])[:600], replaced=", ".join(out["replaced"])[:600]))

    @app.post("/c/{cid}/results/unmark")
    def results_unmark(cid: str, z: str = "", file: str = "", sid: str = ""):
        """Отметка «снята» / «сверх КВ» у команды на этапе: секретарь снимает её (МШ уходит, итог этапа — по
        техштрафу и времени) или возвращает (МШ)."""
        f = folder(cid)
        zz = need_zachet(need_comp(f), z)

        def update(zdata):
            stage = next((s for s in pr.stages_of(zdata) if s.id == sid), None)
            if stage is not None and file:
                stt.toggle_mark(zdata, stage, file)

        save_zachet(run_lock, f, zz.key, update)
        q = {"z": zz.key, "done": "run_saved", "focus": f"c-{team_anchor(file)}-{sid}"}
        return redirect(f"{base_url(f)}/results?{urlencode(q)}#points")

    @app.post("/c/{cid}/results/points")
    async def results_points(request: Request, cid: str, z: str = ""):
        """Баллы команд по этапам и статусы. С автосохранением — возвращает суммы, места и таблицу результатов."""
        f = folder(cid)
        comp = need_comp(f)
        zz = need_zachet(comp, z)
        form = await request.form()
        idx = sorted({int(m.group(1)) for k in form if (m := re.fullmatch(r"p-(\d+)-file", k))})

        def update(zdata):
            """Баллы этапов и поля команд из таблицы — в данные зачёта."""
            ids = [str(s["id"]) for s in zdata.get("stages", [])]
            teams = zdata.setdefault("teams", {})
            for i in idx:
                t = teams.setdefault(str(form.get(f"p-{i}-file")), {})
                pts = t.setdefault("points", {})
                for sid in ids:
                    v = " ".join(str(form.get(f"p-{i}-{sid}", "")).split())
                    if v:
                        pts[sid] = v
                    else:
                        pts.pop(sid, None)
                st = str(form.get(f"p-{i}-status", Status.FINISHED.value))
                t["status"] = st if st in {s.value for s in Status} else Status.FINISHED.value
                for fld in ("start", "finish", "cutoffs", "chip", "pen_time", "red", "declared", "no_tactics",
                            *(f"add-{a['id']}" for a in tr.adds_of(zdata))):
                    if f"p-{i}-{fld}" in form:
                        t[fld] = " ".join(str(form.get(f"p-{i}-{fld}", "")).split())

        save_zachet(run_lock, f, zz.key, update)
        if request.headers.get("x-autosave"):
            _, zdata, run = run_ctx(store, f, comp, zz)
            parts = {"comp": comp, **results_parts(f, zz, zdata, run)}
            cells = {r.inp.file: {"total": pr.result_text(run, r) if run.kind == "time"
                                  else pr.points_text(r.total),
                                  "place": str(r.place) if r.place else pr.STATUS_SHORT[r.status] or "—",
                                  "bad": r.bad} for r in run.rows}
            return JSONResponse({"cells": cells, "saved": datetime.now().strftime("%H:%M:%S"),
                                 "results": templates.get_template("_results_table.html").render(**parts)})
        return redirect(f"{base_url(f)}/results?{urlencode({'z': zz.key, 'done': 'run_saved'})}#points")

    # ------------------------------------------------------------ предварительный протокол → протесты → официальный



    def protocol_name(z, kind: str, at: datetime) -> str:
        key = safe_name(z.key.replace("/", "-"))
        return (f"Предварительный протокол {key} {at:%d.%m %H-%M}.xlsx" if kind == "preliminary"
                else f"Протокол результатов {key}.xlsx")

    @app.post("/c/{cid}/results/publish")
    def results_publish(cid: str, z: str = ""):
        """Предварительный протокол: время публикации — начало часа на протесты."""
        f = folder(cid)
        comp = need_comp(f)
        zz = need_zachet(comp, z)
        _, _, run = run_ctx(store, f, comp, zz)
        now = app.state.clock()
        back = f"{base_url(f)}/results?{urlencode({'z': zz.key})}"
        if not any(r.place for r in run.rows):
            return redirect(with_done(back, "run_empty"))
        f.protocols_dir.mkdir(exist_ok=True)
        path = f.protocols_dir / protocol_name(zz, "preliminary", now)
        rp.write_protocol(comp, run, rp.PRELIMINARY, now, path, now + PROTEST_HOUR)

        def update(d):
            d["published"] = {"at": now.isoformat(timespec="minutes"), "fp": fingerprint(run), "file": path.name}
            d.pop("official", None)  # новые предварительные результаты — новый час на протесты

        save_zachet(run_lock, f, zz.key, update)
        app.state.opener(path)
        return redirect(with_done(back + "#protocol", "run_published", until=f"{now + PROTEST_HOUR:%H:%M}"))

    @app.post("/c/{cid}/results/protest")
    async def results_protest(request: Request, cid: str, z: str = ""):
        """Протест: главный секретарь проставляет время подачи (п. 8.17)."""
        f = folder(cid)
        comp = need_comp(f)
        zz = need_zachet(comp, z)
        form = await request.form()
        at = parse_dt(str(form.get("at", ""))) or app.state.clock()
        text = " ".join(str(form.get("text", "")).split())
        back = f"{base_url(f)}/results?{urlencode({'z': zz.key})}"
        if not text:
            return redirect(with_done(back + "#protocol", "run_protest_empty"))
        _, zdata, run = run_ctx(store, f, comp, zz)
        state = protocol_state(zdata, run, app.state.clock())
        late = bool(state["until"] and at > state["until"])

        def update(d):
            d.setdefault("protests", []).append({"id": f"p{len(d.get('protests', [])) + 1}",
                                                 "at": at.isoformat(timespec="minutes"),
                                                 "team": str(form.get("team", "")), "text": text, "late": late,
                                                 "decision": "", "note": ""})

        save_zachet(run_lock, f, zz.key, update)
        return redirect(with_done(back + "#protocol", "run_protest_late" if late else "run_protest"))

    @app.post("/c/{cid}/results/protest/decide")
    async def results_protest_decide(request: Request, cid: str, z: str = ""):
        f = folder(cid)
        zz = need_zachet(need_comp(f), z)
        form = await request.form()
        pid, decision = str(form.get("id", "")), str(form.get("decision", ""))

        def update(d):
            for p in d.get("protests", []):
                if p.get("id") == pid:
                    p["decision"] = decision if decision in PROTEST_DECISIONS else ""
                    p["note"] = " ".join(str(form.get("note", "")).split())
                    p["decided_at"] = app.state.clock().isoformat(timespec="minutes") if p["decision"] else ""

        save_zachet(run_lock, f, zz.key, update)
        return redirect(f"{base_url(f)}/results?{urlencode({'z': zz.key, 'done': 'run_decided'})}#protocol")

    @app.post("/c/{cid}/results/approve")
    async def results_approve(request: Request, cid: str, z: str = ""):
        """Официальный протокол (п. 8.18): после часа на протесты и решений по ним. Результаты уходят в награждение."""
        f = folder(cid)
        comp = need_comp(f)
        zz = need_zachet(comp, z)
        _, zdata, run = run_ctx(store, f, comp, zz)
        now = app.state.clock()
        st = protocol_state(zdata, run, now)
        back = f"{base_url(f)}/results?{urlencode({'z': zz.key})}#protocol"
        if not st["published"]:
            return redirect(with_done(back, "run_not_published"))
        if st["changed"]:
            return redirect(with_done(back, "run_changed"))
        if st["open_protests"]:
            return redirect(with_done(back, "run_open_protests"))
        f.protocols_dir.mkdir(exist_ok=True)
        path = f.protocols_dir / protocol_name(zz, "official", now)
        try:
            rp.write_protocol(comp, run, rp.OFFICIAL, now, path)
        except PermissionError:
            return redirect(with_done(back, "doc_locked"))
        save_zachet(run_lock, f, zz.key, lambda d: d.update(official={"at": now.isoformat(timespec="minutes"),
                                                             "fp": fingerprint(run), "file": path.name}))
        awards = f.results_data()
        awards.setdefault("zachety", {})[zz.key] = rp.awards_rows(run, f"СТ-Секретарь, утверждён {now:%d.%m.%Y %H:%M}")
        f.save_results_data(awards)
        app.state.opener(path)
        return redirect(with_done(back, "run_official"))

    @app.get("/c/{cid}/results/file/{kind}")
    def results_file(cid: str, kind: str, z: str = ""):
        f = folder(cid)
        zz = need_zachet(need_comp(f), z)
        info = f.run_data().get("zachety", {}).get(zz.key, {}).get("published" if kind == "preliminary" else "official")
        p = f.protocols_dir / Path(info["file"]).name if info else None
        if p is None or not p.is_file():
            raise HTTPException(404)
        return FileResponse(p, filename=p.name, media_type=XLSX)

