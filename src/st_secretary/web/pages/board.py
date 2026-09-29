"""Табло и телефоны судей этапов: раздача по Wi-Fi ноутбука, ссылки этапов, присланное с телефонов."""

from __future__ import annotations

from urllib.parse import urlencode

from fastapi import Request
from fastapi.responses import JSONResponse

from st_secretary import judge_sync as js
from st_secretary import psr_run as pr
from st_secretary import start_list as sl
from st_secretary import time_run as tr
from st_secretary.disciplines import Status
from st_secretary.web.board import BoardServer, create_board_app, qr_svg
from st_secretary.web.common import _base, _parse_dt, _redirect
from st_secretary.web.store import CompFolder


def register(app, cx) -> None:
    board_host = cx.board_host
    comp_ctx = cx.comp_ctx
    folder = cx.folder
    page = cx.page
    run_lock = cx.run_lock
    store = cx.store
    templates = cx.templates
    def _save_zachet(*a, **k):  # из pages/results.py
        return cx._save_zachet(*a, **k)
    def need_comp(*a, **k):  # из pages/preapps.py
        return cx.need_comp(*a, **k)
    def need_zachet(*a, **k):  # из pages/results.py
        return cx.need_zachet(*a, **k)
    def protocol_state(*a, **k):  # из pages/results.py
        return cx.protocol_state(*a, **k)
    def run_ctx(*a, **k):  # из pages/results.py
        return cx.run_ctx(*a, **k)
    def start_ctx(*a, **k):  # из pages/start.py
        return cx.start_ctx(*a, **k)
    def zachet_inputs(*a, **k):  # из pages/results.py
        return cx.zachet_inputs(*a, **k)

    # ------------------------------------------------------------ табло (Wi-Fi ноутбука)

    board_helpers = {"pt": pr.points_text, "status_label": pr.STATUS_LABEL, "FINISHED": Status.FINISHED,
                     "ck": tr.clock_text,
                     "pct": lambda x: f"{float(x):.2f}".replace(".", ",") if x is not None else ""}

    def board_on(f: CompFolder) -> bool:
        return bool(f.run_data().get("board", {}).get("on"))

    def board_list() -> list[dict]:
        out = []
        for f in store.all():
            if board_on(f):
                try:
                    comp = f.load()
                except Exception:  # noqa: BLE001 — повреждённая карточка: на табло её нет
                    continue
                out.append({"id": f.id, "title": comp.title, "dates": comp.dates_text})
        return out

    def board_data(cid: str, preview: bool = False) -> dict | None:
        """Что видно на табло: результаты зачётов с подписью — текущие, предварительные или официальные."""
        f = store.get(cid)
        if f is None or not (preview or board_on(f)):
            return None
        try:
            comp = f.load()
        except Exception:  # noqa: BLE001 — повреждённая карточка: табло без неё
            return None
        now = app.state.clock()
        blocks = []
        for z in comp.zachety:
            _, zdata, run = run_ctx(f, comp, z)
            start = None
            if zdata.get("draw", {}).get("published"):  # стартовый протокол — с момента публикации
                lst = start_ctx(f, comp, z, [r.inp for r in run.rows])[3]
                pub = _parse_dt(lst.published["at"])
                start = {"rows": lst.rows, "hm": sl.hm_text,
                         "label": f"Опубликован {pub:%d.%m в %H:%M}" + (" — после публикации менялся, уточняйте у "
                                                                         "секретаря" if lst.changed else "")}
            if not run.stages:
                if start:
                    blocks.append({"z": z, "run": None, "start": start})
                continue
            st = protocol_state(zdata, run, now)
            if st["official"] and not st["changed_after_official"]:
                label, final = f"Официальные результаты — утверждены {st['official_at']:%d.%m в %H:%M}", True
            elif st["published"] and not st["changed"]:
                label, final = f"Предварительные результаты — опубликованы в {st['published_at']:%H:%M}", False
                if not st["hour_passed"]:
                    label += f", протесты принимаются до {st['until']:%H:%M}"
            else:
                label, final = f"Текущие результаты на {now:%H:%M} — не окончательные", False
            blocks.append({"z": z, "run": run, "label": label, "final": final, **board_helpers, "start": start,
                           "started": any(r.place or r.filled for r in run.rows),
                           "res": lambda r, run=run: pr.result_text(run, r)})
        return {"title": comp.title, "dates": comp.dates_text, "place": comp.place, "zachety": blocks}

    # ------------------------------------------------------------ телефоны судей этапов

    def judge_link(token: str):
        """Этап по коду ссылки: (папка, карточка, зачёт, этап) или None."""
        for f in store.all():
            link = js.tokens(f.run_data()).get(token)
            if not link:
                continue
            try:
                comp = f.load()
            except Exception:  # noqa: BLE001 — карточка не читается: ссылка не работает
                return None
            z = next((x for x in comp.zachety if x.key == link.get("z")), None)
            if z is None:
                return None
            zdata = f.run_data().get("zachety", {}).get(z.key, {})
            stage = next((s for s in pr.stages_of(zdata) if s.id == link.get("stage")), None)
            return (f, comp, z, stage) if stage else None
        return None

    def judge_page(token: str) -> dict | None:
        found = judge_link(token)
        if found is None:
            return None
        f, comp, z, stage = found
        _, zdata, run = run_ctx(f, comp, z)
        log = zdata.get("judge", {}).get(stage.id, {})
        teams = [{"file": r.inp.file, "team": r.inp.team, "number": r.inp.number}
                 for r in sorted(run.rows, key=lambda r: r.start_order)]
        return {"title": comp.title, "zachet": z.key, "stage": {"title": stage.title, "kv": stage.kv_minutes},
                "payload": {"token": token, "sync_url": f"/j/{token}/sync", "teams": teams,
                            "stage": {"kv": stage.kv_minutes, "cutoffs": tr.is_time_discipline(z)},
                            "records": {file: {**rec, "file": file} for file, rec in log.items()}}}

    def judge_receive(token: str, payload: dict) -> dict | None:
        found = judge_link(token)
        if found is None:
            return None
        f, comp, z, stage = found
        files = {t.file for t in zachet_inputs(f, comp, z)}
        records = [js.Record.from_json(r) for r in payload.get("records", [])[:500] if isinstance(r, dict)]
        device = " ".join(str(payload.get("device", "")).split())[:40] or "телефон"
        now = app.state.clock()
        out = {}
        mark = "с" if tr.is_time_discipline(z) else ""  # спелео: снятие с этапа — «с» в клетке этапа
        _save_zachet(f, z.key, lambda zdata: out.update(js.merge(zdata, stage.id, records, files, device,
                                                                 now.isoformat(timespec="seconds"), mark)))
        return {"saved": out.get("saved", []), "time": f"{now:%H:%M:%S}"}

    app.state.board = BoardServer(create_board_app(board_list, board_data, judge_page, judge_receive), host=board_host)

    @app.get("/c/{cid}/judges")
    def judges_page(request: Request, cid: str, z: str = ""):
        f = folder(cid)
        comp = need_comp(f)
        if not comp.zachety:
            return page(request, "judges.html", active="judges", zachet=None, **comp_ctx(f))
        zz = need_zachet(comp, z)
        data, zdata, run = run_ctx(f, comp, zz)
        srv = app.state.board
        urls = srv.urls() if srv.running else []
        stages = []
        for s in run.stages:
            t = js.stage_token(data, zz.key, s.id)
            link = f"{urls[0]}j/{t}" if t and urls else ""
            stages.append({"s": s, "token": t, "link": link, "sum": js.stage_summary(zdata, s.id, len(run.rows))})
        return page(request, "judges.html", active="judges", zachet=zz, zachety=comp.zachety, stages=stages,
                    running=srv.running, urls=urls, error=srv.error, zq=urlencode({"z": zz.key}), run=run,
                    **comp_ctx(f))

    @app.post("/c/{cid}/judges/link")
    async def judges_link(request: Request, cid: str, z: str = ""):
        f = folder(cid)
        zz = need_zachet(need_comp(f), z)
        form = await request.form()
        sid, do = str(form.get("stage", "")), str(form.get("do", "issue"))
        with run_lock:
            data = f.run_data()
            if sid == "*":  # ссылки всем этапам, у которых их ещё нет
                for s in data.get("zachety", {}).get(zz.key, {}).get("stages", []):
                    if not js.stage_token(data, zz.key, str(s["id"])):
                        js.issue_token(data, zz.key, str(s["id"]), app.state.clock().isoformat(timespec="minutes"))
            elif do == "revoke":
                js.revoke_token(data, zz.key, sid)
            else:
                js.issue_token(data, zz.key, sid, app.state.clock().isoformat(timespec="minutes"))
            f.save_run_data(data)
        done = "judge_revoked" if do == "revoke" else "judge_issued"
        return _redirect(f"{_base(f)}/judges?{urlencode({'z': zz.key, 'done': done})}")

    @app.post("/c/{cid}/judges/server")
    async def judges_server(request: Request, cid: str, z: str = ""):
        f = folder(cid)
        ok = app.state.board.start()
        return _redirect(f"{_base(f)}/judges?{urlencode({'z': z, 'done': 'board_started' if ok else 'board_failed'})}")

    @app.get("/c/{cid}/judges/phone/{token}")
    def judges_phone(request: Request, cid: str, token: str):
        """Страница судьи этапа на ноутбуке: посмотреть, что видит судья, или внести за судью (сел телефон)."""
        folder(cid)
        info = judge_page(token)
        if info is not None:
            info["payload"]["sync_url"] = f"{_base(folder(cid))}/judges/phone/{token}/sync"
        return templates.TemplateResponse(request, "judge.html", {"info": info}, status_code=200 if info else 404,
                                          headers={"Cache-Control": "no-store"})

    @app.post("/c/{cid}/judges/phone/{token}/sync")
    async def judges_phone_sync(request: Request, cid: str, token: str):
        folder(cid)
        try:
            payload = await request.json()
        except ValueError:
            payload = None
        if not isinstance(payload, dict):
            return JSONResponse({"ok": False, "error": "не те данные"}, status_code=400)
        res = judge_receive(token, payload)
        if res is None:
            return JSONResponse({"ok": False, "error": "ссылка больше не действует"}, status_code=404)
        return JSONResponse({"ok": True, **res})

    @app.get("/c/{cid}/judges/print")
    def judges_print(request: Request, cid: str, z: str = ""):
        """Карточки с QR-кодами этапов — распечатать и раздать судьям."""
        f = folder(cid)
        comp = need_comp(f)
        zz = need_zachet(comp, z)
        data, _, run = run_ctx(f, comp, zz)
        urls = app.state.board.urls() if app.state.board.running else []
        cards = []
        for s in run.stages:
            t = js.stage_token(data, zz.key, s.id)
            if t and urls:
                link = f"{urls[0]}j/{t}"
                cards.append({"s": s, "link": link, "qr": qr_svg(link)})
        return templates.TemplateResponse(request, "judges_print.html", {"comp": comp, "z": zz, "cards": cards,
                                                                         "running": bool(urls)})

    @app.get("/c/{cid}/board")
    def board_page(request: Request, cid: str):
        f = folder(cid)
        need_comp(f)
        srv = app.state.board
        urls = srv.urls() if srv.running else []
        return page(request, "board_admin.html", active="board", on=board_on(f), running=srv.running, urls=urls,
                    qr=qr_svg(urls[0]) if urls else "", error=srv.error, **comp_ctx(f))

    @app.post("/c/{cid}/board/toggle")
    async def board_toggle(request: Request, cid: str):
        f = folder(cid)
        on = (await request.form()).get("on") == "1"
        with run_lock:
            data = f.run_data()
            data["board"] = {"on": on}
            f.save_run_data(data)
        return _redirect(f"{_base(f)}/board?done={'board_on' if on else 'board_off'}")

    @app.post("/c/{cid}/board/server")
    async def board_server(request: Request, cid: str):
        f = folder(cid)
        do = (await request.form()).get("do")
        if do == "stop":
            app.state.board.stop()
            return _redirect(f"{_base(f)}/board?done=board_stopped")
        ok = app.state.board.start()
        return _redirect(f"{_base(f)}/board?done={'board_started' if ok else 'board_failed'}")

    @app.get("/c/{cid}/board/preview")
    def board_preview(request: Request, cid: str):
        """Как выглядит табло — на этом компьютере, даже если раздача по Wi-Fi не включена."""
        data = board_data(folder(cid).id, preview=True)
        return templates.TemplateResponse(request, "board.html", {"refresh": 30, "prefix": "", "data": data,
                                                                  "items": []})
