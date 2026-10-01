"""Комиссия по допуску: документы и решения, номера, взносы, протокол комиссии; проверка снаряжения;
документы (сканы) команд — только на этом компьютере."""

from __future__ import annotations

import re
import shutil
import tempfile
from collections import Counter
from datetime import datetime
from pathlib import Path
from urllib.parse import quote, urlencode

from fastapi import Request
from fastapi.responses import FileResponse, JSONResponse
from starlette.background import BackgroundTask
from starlette.exceptions import HTTPException

from st_secretary import commission as cm
from st_secretary import equipment as eq
from st_secretary import festival as fv
from st_secretary.exporters.commission_xlsx import write_commission_report
from st_secretary.issues import ERROR
from st_secretary.web.common import XLSX, _base, _redirect, _with_done, key_of, team_anchor
from st_secretary.web.review import DONE
from st_secretary.web.store import COMMISSION_REPORT, IMAGE_TYPES, CompFolder


def register(app, cx) -> None:
    comp_ctx = cx.comp_ctx
    folder = cx.folder
    page = cx.page
    store = cx.store
    templates = cx.templates
    def back_to(*a, **k):  # из pages/preapps.py
        return cx.back_to(*a, **k)
    def need_comp(*a, **k):  # из pages/preapps.py
        return cx.need_comp(*a, **k)
    def need_file(*a, **k):  # из pages/preapps.py
        return cx.need_file(*a, **k)
    def team_url(*a, **k):  # из pages/preapps.py
        return cx.team_url(*a, **k)

    # ------------------------------------------------------------ комиссия по допуску

    def commission(f: CompFolder, comp):
        """Отметки комиссии и состояние допуска по каждой заявке (в порядке списка заявок). На фестивале с одним
        взносом за фестиваль взноса у команд соревнования нет — он в ведомости фестиваля."""
        result, reviews = store.review(f, comp)
        data = f.admission()
        files = [p.name for p in f.preapp_files()]
        gear = eq.admission_problems(eq.evaluate(result, files, f.equipment(), key_of), f.equipment())
        fest = f.festival()
        teams = cm.evaluate(result, files, comp, data, gear, {k: r.status == DONE for k, r in reviews.items()},
                            outside=festival_marks(fest, f.id) if fest else None,
                            doctor_files=cm.doctor_files(result, data))
        if fv.mode(fest, "fee") == "festival":
            for t in teams:
                t.fee_due = t.fee_paid = 0
        else:
            cm.apply_delegation_fees(teams, data)  # делегация платит одной строкой — оплата по её командам
        return data, teams

    def festival_marks(fest: dict, cid: str) -> dict[str, dict[str, tuple[str, str]]]:
        """Документы людей, отмеченные в других соревнованиях фестиваля: {человек: {документ: (команда,
        соревнование)}} — человек проверяется один раз (Правки, п. 20)."""
        out: dict[str, dict[str, tuple[str, str]]] = {}
        for m in fest["members"]:
            g = store.get(m) if m != cid else None
            try:
                comp = g.load() if g else None
            except Exception:  # noqa: BLE001 — карточка не читается
                comp = None
            if comp is None or any(i.severity == ERROR for i in comp.check()):
                continue
            result, _ = store.review(g, comp)
            data = g.admission()
            marks = cm.own_marks(result, [p.name for p in g.preapp_files()], data, cm.doctor_files(result, data))
            for pid, docs in marks.items():
                for k, team in docs.items():
                    out.setdefault(pid, {}).setdefault(k, (team, comp.title))
        return out

    def festival_members(fest: dict) -> list[tuple]:
        """Соревнования фестиваля с комиссией: [(папка, карточка, отметки комиссии, команды)]; соревнования с
        ошибками в карточке пропускаются."""
        out = []
        for m in fest["members"]:
            g = store.get(m)
            try:
                comp = g.load() if g else None
            except Exception:  # noqa: BLE001 — карточка не читается
                comp = None
            if comp is None or any(i.severity == ERROR for i in comp.check()):
                continue
            out.append((g, comp, *commission(g, comp)))
        return out

    def shared_numbers(f: CompFolder) -> dict | None:
        """Фестиваль, если стартовые номера в нём общие (Правки, п. 19)."""
        fest = f.festival()
        return fest if fest and fv.mode(fest, "numbers") == "festival" else None

    def number_clash(f: CompFolder, t, teams: list) -> list[str]:
        """С кем совпал номер команды: в соревновании или, при общих номерах, во всём фестивале."""
        if t.number is None:
            return []
        fest = shared_numbers(f)
        if fest is None:
            return [x.title for x in teams if x.number == t.number and x.file != t.file]
        me = fv.ident_of(f.id, t)
        return list(dict.fromkeys(
            x.title + ("" if g.id == f.id else f" ({comp.title})") for g, comp, _, ts in festival_members(fest)
            for x in ts if x.number == t.number and fv.ident_of(g.id, x) != me))

    def adm_totals(teams: list) -> dict:
        people = [p for t in teams for p in t.persons]
        return {"teams": len(teams), "teams_ok": sum(t.status == cm.ADMITTED for t in teams),
                "teams_by": Counter(t.status for t in teams),
                "people": len(people), "people_ok": sum(p.status == cm.ADMITTED for p in people),
                "people_wait": sum(p.status == cm.PENDING for p in people),
                "people_no": sum(p.status == cm.REJECTED for p in people),
                "fee_due": sum(t.fee_due for t in teams), "fee_paid": sum(t.fee_paid for t in teams),
                "no_check": cm.without_check(teams)}

    def adm_parts(f: CompFolder, data: dict) -> dict:
        pdocs, tdocs = cm.required_docs(data)
        fest = f.festival()
        return {"pdocs": pdocs, "tdocs": tdocs, "adm_settings": cm.settings(data), "fee_methods": cm.FEE_METHODS,
                "base": _base(f), "docs_n": {p.name: len(store.team_docs(f, p.name)) for p in f.preapp_files()},
                "gear_on": bool(eq.settings(f.equipment())["items"]),
                "fest_fee": fest if fv.mode(fest, "fee") == "festival" else None,
                "fest_numbers": shared_numbers(f)}

    @app.get("/c/{cid}/admission")
    def admission_page(request: Request, cid: str, by: str = ""):
        f = folder(cid)
        ctx = comp_ctx(f)
        comp = ctx["comp"]
        blocked = [i for i in comp.check() if i.severity == ERROR] if comp else ctx["card_errors"]
        teams, data, notes = [], f.admission(), []
        if comp and not blocked:
            data, teams = commission(f, comp)
            if fest := shared_numbers(f):
                notes = fv.number_problems([(g.id, c.title, ts) for g, c, _, ts in festival_members(fest)])
        return page(request, "admission.html", active="admission", blocked=blocked, teams=teams,
                    totals=adm_totals(teams), all_person_docs=cm.PERSON_DOCS, all_team_docs=cm.TEAM_DOCS,
                    by_delegation=by == "delegation", delegations=cm.delegations(teams, data), number_notes=notes,
                    deleg_fee=cm.DELEGATION_FEE, deleg_anchor=deleg_anchor, **{**ctx, **adm_parts(f, data)})

    def deleg_anchor(key: str) -> str:
        import hashlib

        return "d-" + hashlib.sha1(key.encode("utf-8")).hexdigest()[:10]

    @app.post("/c/{cid}/admission/delegation")
    async def admission_delegation(request: Request, cid: str):
        """Делегация: взнос одной строкой или по командам, оплата, заявка делегации с печатью врача (Правки, п. 20)."""
        f = folder(cid)
        form = await request.form()
        key = str(form.get("key", ""))
        data = f.admission()
        d = data.setdefault("delegations", {}).setdefault(key, {})
        d["fee_mode"] = cm.FEE_ONE if form.get("fee_mode") == cm.FEE_ONE else cm.FEE_TEAMS
        paid = re.sub(r"\D", "", str(form.get("fee_paid", "")).split(",")[0].split(".")[0])
        d["fee_paid"] = int(paid) if paid else 0
        method = str(form.get("fee_method", ""))
        d["fee_method"] = method if method in cm.FEE_METHODS else ""
        d["doctor"] = bool(form.get("doctor"))
        f.save_admission(data)
        return _redirect(f"{_base(f)}/admission?by=delegation&done=adm_delegation#{deleg_anchor(key)}")

    @app.post("/c/{cid}/admission/delegation/folder")
    async def admission_delegation_folder(request: Request, cid: str):
        """Папка сканов делегации: внутри — папка на каждого её участника (ФИО и дата рождения)."""
        f = folder(cid)
        comp = need_comp(f)
        key = str((await request.form()).get("key", ""))
        data, teams = commission(f, comp)
        d = next((x for x in cm.delegations(teams, data) if x.key == key), None)
        if d is None:
            raise HTTPException(404)
        target = store.delegation_docs_dir(f, d.title)
        for t in d.teams:
            for p in t.persons:
                store.person_docs_dir(f, d.title, p.entry).mkdir(parents=True, exist_ok=True)
        app.state.opener(target)
        return _redirect(f"{_base(f)}/admission?by=delegation&done=opened#{deleg_anchor(key)}")

    @app.post("/c/{cid}/admission/team")
    async def admission_team(request: Request, cid: str):
        """Отметки по одной команде. Со страницы приходят сами при каждом изменении (ответ — обновлённый
        блок команды); без скриптов — обычная форма с кнопкой «Сохранить»."""
        f = folder(cid)
        comp = need_comp(f)
        form = await request.form()
        path = need_file(f, str(form.get("file", "")))
        data = f.admission()
        pdocs, tdocs = cm.required_docs(data)
        tm = data.setdefault("teams", {}).setdefault(path.name, {})
        everything = form.get("do") == "all_docs"  # «Отметить все документы»
        # мед. допуск, стоявший сам по допуску врача в заявке, — не отметка секретаря: сняли — врач не допустил
        keys = [str(form.get(k)) for k in form if re.fullmatch(r"p-\d+-key", k)]
        _, before = commission(f, comp)
        shared = {p.key: set(p.shared_docs) for t in before if t.file == path.name for p in t.persons}
        # мед. допуск, стоявший сам — по допуску врача у команды или у её делегации
        auto_med = {p.key for t in before if t.file == path.name for p in t.persons if cm.MED in p.auto_docs}
        auto_med &= set(keys)
        tm["team_docs"] = {**tm.get("team_docs", {}),
                           **{d.key: everything or bool(form.get(f"td-{d.key}")) for d in tdocs}}
        number = str(form.get("number", "")).strip()
        renumbered = tm.get("number") != (int(number) if number.isdigit() else None)
        tm["number"] = int(number) if number.isdigit() else None
        if "fee_paid" in form:  # взнос за фестиваль — не здесь: поля нет, отметки не трогаем
            paid = str(form.get("fee_paid", "")).replace(" ", "").replace(",", ".").strip()
            tm["fee_paid"] = int(float(paid)) if paid.replace(".", "", 1).isdigit() else 0
            tm["fee_method"] = str(form.get("fee_method", ""))
        tm["decision"] = str(form.get("decision", "")) if form.get("decision") in (cm.ADMITTED, cm.REJECTED) else ""
        tm["note"] = str(form.get("note", "")).strip()
        people = tm.setdefault("people", {})
        idx = sorted({int(k.split("-")[1]) for k in form if re.fullmatch(r"p-\d+-key", k)})
        for i in idx:
            key = str(form.get(f"p-{i}-key"))
            pm = people.setdefault(key, {})
            posted = {d.key: everything or bool(form.get(f"p-{i}-d-{d.key}")) for d in pdocs}
            if key in auto_med and cm.MED in posted:
                if not posted[cm.MED]:
                    pm["med_off"] = True  # врач участника не допустил — сама галочка больше не ставится
                posted[cm.MED] = False  # стоит сама, пока у команды «Допуск врача»; сняли его — уйдёт
            for d in shared.get(key, ()):  # отмечено у этого человека в другой команде — там и снимается
                posted.pop(d, None)
            pm["docs"] = {**pm.get("docs", {}), **posted}
            decision = str(form.get(f"p-{i}-decision", ""))
            pm["decision"] = decision if decision in (cm.ADMITTED, cm.REJECTED) else ""
            pm["reason"] = str(form.get(f"p-{i}-reason", "")).strip() if pm["decision"] else ""
        f.save_admission(data)
        data, teams = commission(f, comp)
        t = next(x for x in teams if x.file == path.name)
        if renumbered and (fest := shared_numbers(f)):  # общие номера: тот же номер — этой команде везде
            me = fv.ident_of(f.id, t)
            for g, _, gdata, ts in festival_members(fest):
                files = [x.file for x in ts if g.id != f.id and fv.ident_of(g.id, x) == me]
                for file in files:
                    gdata.setdefault("teams", {}).setdefault(file, {})["number"] = t.number
                if files:
                    g.save_admission(gdata)
        clash = number_clash(f, t, teams)
        if request.headers.get("x-autosave"):
            parts = {**adm_parts(f, data), "clash": clash, "in_check": bool(form.get("in_check"))}
            return JSONResponse({"team": templates.get_template("_admission_team.html").render(t=t, **parts),
                                 "tiles": templates.get_template("_admission_tiles.html").render(
                                     totals=adm_totals(teams), **parts),
                                 "saved": datetime.now().strftime("%H:%M:%S"), "status": t.status,
                                 "by": {"all": len(teams), **{s: sum(x.status == s for x in teams)
                                                             for s in (cm.PENDING, cm.ADMITTED, cm.REJECTED)}}})
        if form.get("in_check"):
            return _redirect(f"{_base(f)}/admission/check?{urlencode({'file': path.name, 'done': 'adm_saved'})}")
        return _redirect(f"{_base(f)}/admission?done=adm_saved#{team_anchor(path.name)}")

    @app.post("/c/{cid}/admission/settings")
    async def admission_settings(request: Request, cid: str):
        f = folder(cid)
        form = await request.form()
        data = f.admission()
        data["settings"] = {"docs": [d.key for d in cm.PERSON_DOCS if form.get(f"doc-{d.key}")],
                            "team_docs": [d.key for d in cm.TEAM_DOCS if form.get(f"tdoc-{d.key}")],
                            "start_at": str(form.get("start_at", "")).strip()}
        f.save_admission(data)
        return _redirect(f"{_base(f)}/admission?done=adm_settings")

    @app.post("/c/{cid}/admission/numbers")
    async def admission_numbers(request: Request, cid: str):
        """Номера командам по порядку списка: только тем, у кого нет, или всем заново."""
        f = folder(cid)
        comp = need_comp(f)
        again = (await request.form()).get("mode") == "all"
        if fest := shared_numbers(f):  # общие номера фестиваля — сразу во всех его соревнованиях
            members = festival_members(fest)
            new = fv.assign_numbers([(g.id, ts) for g, _, _, ts in members], again)
            for g, _, gdata, ts in members:
                for t in ts:
                    if (g.id, t.file) in new:
                        gdata.setdefault("teams", {}).setdefault(t.file, {})["number"] = new[(g.id, t.file)]
                g.save_admission(gdata)
            return _redirect(f"{_base(f)}/admission?done=adm_numbers_fest")
        data, teams = commission(f, comp)
        taken = set() if again else {t.number for t in teams if t.number is not None}
        n = 1
        for t in teams:
            if t.number is not None and not again:
                continue
            while n in taken:
                n += 1
            data.setdefault("teams", {}).setdefault(t.file, {})["number"] = n
            taken.add(n)
        f.save_admission(data)
        return _redirect(f"{_base(f)}/admission?done=adm_numbers")

    def commission_report(f: CompFolder, path: Path | None = None) -> Path:
        comp = need_comp(f)
        if not f.preapp_files():
            raise HTTPException(409, "Пока нет ни одной заявки — добавьте их на странице «Предварительные заявки».")
        data, teams = commission(f, comp)
        gdata, gear = gear_ctx(f, comp)
        fest = f.festival()
        note = (f"Стартовый взнос — один за фестиваль «{fest['title']}»: ведомость взносов — на странице фестиваля."
                if fv.mode(fest, "fee") == "festival" else "")
        return write_commission_report(teams, comp, data, path or f.commission_report_path,
                                       gear if eq.settings(gdata)["items"] else None, gdata, fee_note=note)

    @app.post("/c/{cid}/admission/report")
    def admission_report_open(cid: str):
        f = folder(cid)
        try:
            path = commission_report(f)
        except PermissionError:
            return _redirect(f"{_base(f)}/admission?done=adm_locked")
        app.state.opener(path)
        return _redirect(f"{_base(f)}/admission?done=adm_report")

    @app.get("/c/{cid}/admission/report.xlsx")
    def admission_report_download(cid: str):
        f = folder(cid)
        tmp = Path(tempfile.mkdtemp(prefix="st-secretary-")) / COMMISSION_REPORT
        commission_report(f, tmp)
        return FileResponse(tmp, filename=COMMISSION_REPORT, media_type=XLSX,
                            background=BackgroundTask(shutil.rmtree, tmp.parent, ignore_errors=True))

    # ------------------------------------------------------------ проверка снаряжения

    def gear_ctx(f: CompFolder, comp) -> tuple[dict, list]:
        result, _ = store.review(f, comp)
        data = f.equipment()
        return data, eq.evaluate(result, [p.name for p in f.preapp_files()], data, key_of)

    def gear_totals(gear: list, data: dict) -> dict:
        limit = eq.settings(data)["limit"]
        by = Counter("new" if not g.checked else "out" if g.total > limit else "ok" for g in gear)
        return {"teams": len(gear), "checked": sum(g.checked for g in gear), "by": by}

    def gear_parts(f: CompFolder, data: dict) -> dict:
        s = eq.settings(data)
        return {"s": s, "items_person": [i for i in s["items"] if i.kind != eq.GROUP],
                "items_group": [i for i in s["items"] if i.kind == eq.GROUP], "kind_label": eq.KIND_LABEL,
                "short_name": eq.short_name, "key_of": key_of, "verdict": eq.verdict, "missing_text": eq.missing_text,
                "base": _base(f)}

    @app.get("/c/{cid}/equipment")
    def equipment_page(request: Request, cid: str):
        f = folder(cid)
        comp = need_comp(f)
        data, gear = gear_ctx(f, comp)
        return page(request, "equipment.html", active="admission", gear=gear, totals=gear_totals(gear, data),
                    kinds=list(eq.KIND_LABEL.items()), **{**comp_ctx(f), **gear_parts(f, data)})

    @app.post("/c/{cid}/equipment/settings")
    async def equipment_settings(request: Request, cid: str):
        f = folder(cid)
        form = await request.form()
        data = f.equipment()
        if form.get("do") == "template":
            data["settings"] = eq.template_settings(eq.PSR_KRSK_2025)
        else:
            idx = sorted({int(m.group(1)) for k in form if (m := re.fullmatch(r"it-(\d+)-name", k))})
            items, used = [], set()
            for i in idx:
                name = str(form.get(f"it-{i}-name", "")).strip()
                if not name:
                    continue
                qty = str(form.get(f"it-{i}-qty", "1")).strip()
                iid = str(form.get(f"it-{i}-id", "")).strip() or f"n{i}"
                while iid in used:
                    iid += "x"
                used.add(iid)
                kind = str(form.get(f"it-{i}-kind", eq.PERSONAL))
                items.append({"id": iid, "kind": kind if kind in eq.KIND_LABEL else eq.PERSONAL, "name": name,
                              "unit": str(form.get(f"it-{i}-unit", "")).strip() or "шт.",
                              "qty": int(qty) if qty.isdigit() and int(qty) > 0 else 1})
            pen = {k: str(form.get(f"pen-{k}", "")).strip() for k in eq.KIND_LABEL}
            limit = str(form.get("limit", "")).strip()
            data["settings"] = {"items": items, "penalty": {k: int(v) for k, v in pen.items() if v.isdigit()},
                                "limit": int(limit) if limit.isdigit() else eq.RULES["limit"]}
        f.save_equipment(data)
        return _redirect(f"{_base(f)}/equipment?done=gear_settings")

    @app.post("/c/{cid}/equipment/team")
    async def equipment_team(request: Request, cid: str):
        f = folder(cid)
        comp = need_comp(f)
        form = await request.form()
        path = need_file(f, str(form.get("file", "")))
        data = f.equipment()
        s = eq.settings(data)
        tm = data.setdefault("teams", {}).setdefault(path.name, {})
        everything = form.get("do") == "all"  # «Начать проверку: всё есть» — дальше снимать отметки с того, чего нет

        def value(name: str, it) -> object:
            if everything:
                return "all"
            if it.qty == 1:
                return "all" if form.get(name) else 0
            v = str(form.get(name, "")).strip()
            return int(v) if v.isdigit() else 0

        tm["group"] = {it.id: value(f"g-{it.id}", it) for it in s["items"] if it.kind == eq.GROUP} or {"_": 0}
        people = tm.setdefault("people", {})
        idx = sorted({int(k.split("-")[1]) for k in form if re.fullmatch(r"p-\d+-key", k)})
        if everything and not idx:  # у заявки ещё не отрисованы строки участников — отметить по заявке
            result, _ = store.review(f, comp)
            team = next((t for t in result.teams if t.source == path.name), None)
            for e in (team.entries if team else []):
                people[key_of(e)] = {it.id: "all" for it in s["items"] if it.kind != eq.GROUP}
        for i in idx:
            people[str(form.get(f"p-{i}-key"))] = {it.id: value(f"p-{i}-{it.id}", it)
                                                   for it in s["items"] if it.kind != eq.GROUP}
        tm["note"] = str(form.get("note", "")).strip()
        f.save_equipment(data)
        data, gear = gear_ctx(f, comp)
        g = next(x for x in gear if x.file == path.name)
        if request.headers.get("x-autosave"):
            parts = gear_parts(f, data)
            totals = gear_totals(gear, data)
            return JSONResponse({"team": templates.get_template("_equipment_team.html").render(g=g, **parts),
                                 "tiles": templates.get_template("_equipment_tiles.html").render(totals=totals,
                                                                                                 **parts),
                                 "saved": datetime.now().strftime("%H:%M:%S"),
                                 "by": {"all": totals["teams"], **{k: totals["by"][k] for k in ("new", "ok", "out")}}})
        return _redirect(f"{_base(f)}/equipment?done=gear_saved#{team_anchor(path.name)}")

    # ------------------------------------------------------------ документы команд

    def docs_back(f: CompFolder, sent: str, file: str) -> str:
        return back_to(None, f, sent, team_url(f, file) + "#docs")

    @app.post("/c/{cid}/docs/upload")
    async def docs_upload(request: Request, cid: str):
        f = folder(cid)
        form = await request.form()
        path = need_file(f, str(form.get("file", "")))
        added = skipped = 0
        for up in form.getlist("files"):
            name = getattr(up, "filename", "") or ""
            if not name:
                continue
            try:
                store.add_team_doc(f, path.name, name, await up.read())
                added += 1
            except ValueError:
                skipped += 1
        back = docs_back(f, str(form.get("back", "")), path.name)
        return _redirect(_with_done(back, "docs_added" if added and not skipped else "docs_skipped" if skipped
                                    else "docs_none"))

    @app.get("/c/{cid}/docs/view")
    def docs_view(cid: str, file: str = "", name: str = ""):
        """Файл документа — чтобы показать его прямо на странице (фото, PDF)."""
        f = folder(cid)
        path = need_file(f, file)
        doc = store.team_doc(f, path.name, name)
        if doc is None:
            raise HTTPException(404)
        return FileResponse(doc, content_disposition_type="inline", filename=doc.name,
                            headers={"Cache-Control": "no-store"})  # персональные данные — не оставлять в кэше

    @app.post("/c/{cid}/docs/remove")
    async def docs_remove(request: Request, cid: str):
        f = folder(cid)
        form = await request.form()
        path = need_file(f, str(form.get("file", "")))
        try:
            store.remove_team_doc(f, path.name, str(form.get("name", "")))
        except FileNotFoundError:
            raise HTTPException(404) from None
        return _redirect(_with_done(docs_back(f, str(form.get("back", "")), path.name), "docs_removed"))

    @app.post("/c/{cid}/docs/open")
    async def docs_open(request: Request, cid: str):
        """Открыть документ или папку документов команды программой компьютера."""
        f = folder(cid)
        form = await request.form()
        path = need_file(f, str(form.get("file", "")))
        name = str(form.get("name", ""))
        if name:
            target = store.team_doc(f, path.name, name)
            if target is None:
                raise HTTPException(404)
        else:
            target = store.team_docs_dir(f, path.name)
            target.mkdir(parents=True, exist_ok=True)
        app.state.opener(target)
        return _redirect(_with_done(docs_back(f, str(form.get("back", "")), path.name), "opened"))

    @app.get("/c/{cid}/admission/check")
    def admission_check(request: Request, cid: str, file: str = ""):
        """Проверка в одном окне: документы команды слева, отметки комиссии справа."""
        f = folder(cid)
        comp = need_comp(f)
        path = need_file(f, file)
        data, teams = commission(f, comp)
        order = [t.file for t in teams]
        at = order.index(path.name)
        near = {k: teams[j] for k, j in (("prev", at - 1), ("next", at + 1)) if 0 <= j < len(order)}
        return page(request, "admission_check.html", active="admission", focus=True, t=teams[at],
                    docs=doc_list(f, path.name, teams[at]),
                    position=(at + 1, len(order)), prev_team=near.get("prev"), next_team=near.get("next"),
                    docs_dir=store.team_docs_dir(f, path.name), here=f"{_base(f)}/admission/check?file={quote(path.name)}",
                    **{**comp_ctx(f), **adm_parts(f, data)})

    def doc_list(f: CompFolder, file: str, t=None) -> list[dict]:
        """Сканы команды и — если есть — её участников из папки делегации (Правки, п. 20)."""
        out = []
        for p in store.team_docs(f, file):
            ext = p.suffix.lower()
            kind = "image" if ext in IMAGE_TYPES else "pdf" if ext == ".pdf" else "other"
            out.append({"name": p.name, "label": p.stem, "kind": kind,
                        "url": f"{_base(f)}/docs/view?{urlencode({'file': file, 'name': p.name})}"})
        if t is not None and t.team:
            for fio, p in store.person_docs(f, cm.delegation_title(t.team), [x.entry for x in t.persons]):
                ext = p.suffix.lower()
                kind = "image" if ext in IMAGE_TYPES else "pdf" if ext == ".pdf" else "other"
                out.append({"name": p.name, "label": f"{fio}: {p.stem}", "kind": kind,
                            "url": f"{_base(f)}/docs/person?{urlencode({'file': file, 'fio': fio, 'name': p.name})}"})
        return out

    @app.get("/c/{cid}/docs/person")
    def docs_person(cid: str, file: str = "", fio: str = "", name: str = ""):
        """Скан участника из папки делегации — показать на странице проверки."""
        f = folder(cid)
        comp = need_comp(f)
        path = need_file(f, file)
        _, teams = commission(f, comp)
        t = next((x for x in teams if x.file == path.name), None)
        e = next((p.entry for p in (t.persons if t else []) if p.entry.name.full == fio), None)
        if t is None or e is None or not t.team:
            raise HTTPException(404)
        d = store.person_docs_dir(f, cm.delegation_title(t.team), e)
        doc = d / Path(str(name).replace("\\", "/")).name
        if not name or not doc.is_file():
            raise HTTPException(404)
        return FileResponse(doc, content_disposition_type="inline", filename=doc.name,
                            headers={"Cache-Control": "no-store"})

    cx.update(adm_totals=adm_totals, commission=commission, doc_list=doc_list, festival_members=festival_members)
