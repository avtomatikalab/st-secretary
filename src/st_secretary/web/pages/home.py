"""Главная: список соревнований, новое и загрузка карточки, учебное соревнование, восстановление из копии,
инструкция, судейская практика по всем соревнованиям."""

from __future__ import annotations

import re
import shutil
import tempfile
from pathlib import Path
from urllib.parse import quote

from fastapi import Request
from fastapi.responses import FileResponse, JSONResponse, Response
from starlette.background import BackgroundTask
from starlette.exceptions import HTTPException

from st_secretary import __version__, training
from st_secretary import backup as bk
from st_secretary import commission as cm
from st_secretary import festival as fv
from st_secretary import practice as pt_
from st_secretary.importers.card_xlsx import CardError, load_card
from st_secretary.web.common import HERE, XLSX, base_url, log, redirect, with_done
from st_secretary.web.forms import (
    card_to_form,
    choices,
    form_from_data,
    form_to_card,
    official_rows,
    officials_form,
    officials_from_rows,
)
from st_secretary.web.store import CONTRACTS


def register(app, cx) -> None:
    comp_ctx = cx.comp_ctx
    page = cx.page
    store = cx.store

    # ------------------------------------------------------------ список соревнований

    def render_home(request: Request, import_errors: list[str] | None = None, status_code: int = 200):
        items = []
        for f in store.all():
            ctx = comp_ctx(f)
            items.append({**ctx, "files": len(f.preapp_files())})
        fests = store.festivals()
        by_id = {it["folder"].id: it for it in items}
        groups = [{**x, "comps": [by_id[m] for m in x["members"] if m in by_id]} for x in fests]
        in_fest = {m for x in fests for m in x["members"]}
        journal = app.state.journal
        return page(request, "home.html", status_code=status_code, items=items, data_dir=store.root,
                    import_errors=import_errors, crashed_before=bool(journal and journal.crashed_before),
                    crashed_at=journal.previous if journal else "", festivals=groups, fb_unsent=cx.feedback_unsent(),
                    loose=[it for it in items if it["folder"].id not in in_fest])

    @app.get("/")
    def home(request: Request):
        return render_home(request)

    @app.get("/health")
    def health():
        return JSONResponse({"app": "st-secretary", "version": __version__})

    @app.post("/shutdown")
    def shutdown(request: Request):
        """Кнопка «Выключить»: сначала отдать страницу «выключено», потом остановить сервер."""
        if app.state.shutdown is None:
            raise HTTPException(409, "Эту копию программы выключают там, где её запускали.")
        log.info("Выключение по кнопке на странице")
        return page(request, "stopped.html", stopped=True, background=BackgroundTask(app.state.shutdown))

    @app.get("/journal.zip")
    def journal_download():
        """Журнал программы одним архивом — отправить разработчику, если программа закрылась неожиданно."""
        journal = app.state.journal
        if journal is None:
            raise HTTPException(404)
        name = f"СТ-Секретарь — журнал {app.state.clock():%Y-%m-%d %H-%M}.zip"
        return Response(journal.zip_bytes(), media_type="application/zip",
                        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}"})

    @app.post("/journal/hide")
    def journal_hide():
        if app.state.journal is not None:
            app.state.journal.crashed_before = False
        return redirect("/")

    @app.post("/open-data")
    def open_data(request: Request):
        store.root.mkdir(parents=True, exist_ok=True)
        app.state.opener(store.root)
        return redirect("/?done=opened")

    @app.get("/new")
    def new_form(request: Request):
        return page(request, "new.html", form=card_to_form(None), errors={}, ch=choices())

    @app.post("/new")
    async def new_create(request: Request):
        form = form_from_data(await request.form())
        comp, errors = form_to_card(form)
        if errors:
            return page(request, "new.html", status_code=422, form=form, errors=errors, ch=choices())
        f = store.create(comp)
        store.apply_doc_set(f)  # документы комиссии — как в прошлый раз на этом компьютере (Правки, п. 30)
        return redirect(f"{base_url(f)}/card/edit?done=created")

    @app.get("/help")
    def help_pdf():
        """Инструкция секретаря (PDF): рядом с программой (переносная версия) или в docs/ (исходники)."""
        name = "Инструкция секретаря.pdf"
        for p in (Path.cwd() / name, Path.cwd() / "docs" / name, HERE.parents[2] / "docs" / name):
            if p.is_file():
                return FileResponse(p, media_type="application/pdf", filename=name, content_disposition_type="inline")
        raise HTTPException(404)

    @app.post("/training")
    def training_create():
        """Учебное соревнование на выдуманных данных — потренироваться до настоящих соревнований."""
        f = training.create(store, app.state.clock().date())
        return redirect(f"{base_url(f)}?done=training")

    @app.post("/import")
    async def import_card(request: Request):
        up = (await request.form()).get("card")
        if up is None or not getattr(up, "filename", ""):
            return render_home(request, ["Выберите файл карточки соревнования (Excel)."], 400)
        data = await up.read()
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "card.xlsx"
            p.write_bytes(data)
            try:
                comp = load_card(p)
            except CardError as e:
                return render_home(request, [f"{i.field}: {i.text}" if i.field else i.text for i in e.issues], 422)
            except Exception:  # noqa: BLE001
                return render_home(request, [f"«{up.filename}» не похож на карточку соревнования. Нужен файл .xlsx "
                                             "с листами «Карточка», «ГСК», «Зачёты» — его можно получить кнопкой "
                                             "«Новое соревнование» или командой card-template."], 422)
        f = store.create(comp, card_bytes=data)
        store.apply_doc_set(f)
        return redirect(f"{base_url(f)}?done=imported")

    @app.post("/restore")
    async def restore_backup(request: Request):
        """Соревнование из резервной копии (.zip) — новой папкой, ничего не затирая."""
        up = (await request.form()).get("backup")
        if up is None or not getattr(up, "filename", ""):
            return redirect("/?done=restore_none")
        data = await up.read()
        try:
            if bk.is_festival(data):  # копия фестиваля — все его соревнования и сам фестиваль
                rec, names = bk.restore_festival(data, store.root, app.state.clock())
                fid = store.set_festival(None, rec["title"], names, data=rec)
                return redirect(f"/festival/{quote(fid, safe='')}?done=restored")
            name = bk.restore(data, store.root, app.state.clock())
        except bk.BackupError as e:
            return redirect(with_done("/", "restore_bad", why=str(e)))
        return redirect(f"/c/{quote(name, safe='')}?done=restored")

    # ------------------------------------------------------------ фестиваль (решение 039, неспорная часть)

    def need_festival(fid: str) -> dict:
        x = store.festival(fid)
        if x is None:
            raise HTTPException(404)
        return x

    def festival_people(folders: list) -> list[dict]:
        """Кто заявлен в нескольких соревнованиях фестиваля: человек (ФИО + дата рождения) — где заявлен."""
        by: dict[str, dict] = {}
        for f in folders:
            try:
                comp = f.load()
                result, _ = store.review(f, comp)
            except Exception:  # noqa: BLE001 — карточка или заявки не читаются: в сводке их нет
                continue
            for t in result.teams:
                for e in t.entries:
                    d = by.setdefault(cm.person_id(e), {"fio": e.name.full, "birth": e.birth or e.birth_year,
                                                        "where": []})
                    d["where"].append({"comp": comp.title, "base": base_url(f), "team": t.team,
                                       "zachet": e.zachet.title if e.zachet else "зачёт не найден"})
        out = [d for d in by.values() if len({w["comp"] for w in d["where"]}) > 1]
        return sorted(out, key=lambda d: d["fio"])

    @app.post("/festival/new")
    async def festival_new(request: Request):
        form = await request.form()
        members = [str(m) for m in form.getlist("member") if store.get(str(m))]
        if len(members) < 2:
            return redirect("/?done=festival_few")
        fid = store.set_festival(None, str(form.get("title", "")), members)
        return redirect(f"/festival/{quote(fid, safe='')}?done=festival_made")

    @app.get("/festival/{fid}")
    def festival_page(request: Request, fid: str):
        x = need_festival(fid)
        folders = [store.get(m) for m in x["members"]]
        items = []
        for f in folders:
            if not f:
                continue
            it = {**comp_ctx(f), "files": len(f.preapp_files())}
            own = set(fv.own_roles(x, f.id))
            it["own"] = [o for o in (it["comp"].officials if it["comp"] else []) if o.role in own]
            items.append(it)
        others = [comp_ctx(f) for f in store.all() if f.id not in x["members"]]
        modes = fv.modes(x)
        groups = fest_groups(x) if "festival" in (modes["fee"], modes["numbers"]) else []
        fees = fv.fee_rows(x, groups) if modes["fee"] == "festival" else []
        return page(request, "festival.html", fest=x, items=items, others=others,
                    people=festival_people([f for f in folders if f]), gsk_rows=official_rows(fv.officials(x)),
                    ch=choices(), errors={}, modes=modes, mode_labels=fv.MODES, fee_per=fv.FEE_PER, fees=fees,
                    fee_methods=cm.FEE_METHODS, number_notes=fv.number_problems(groups) if groups else [],
                    fee_total={"due": sum(r["due"] for r in fees), "paid": sum(r["paid"] for r in fees)})

    def fest_groups(x: dict) -> list[tuple]:
        """[(соревнование, название, команды комиссии)] — для общих номеров и взноса за фестиваль."""
        return [(g.id, comp.title, teams) for g, comp, _, teams in cx.festival_members(x)]

    @app.post("/festival/{fid}/fees")
    async def festival_fees(request: Request, fid: str):
        """Оплата взноса за фестиваль: по командам (название + территория)."""
        need_festival(fid)
        form = await request.form()
        idx = sorted({int(k.split("-")[1]) for k in form if re.fullmatch(r"f-\d+-key", k)})
        got = {}
        for i in idx:
            paid = re.sub(r"\D", "", str(form.get(f"f-{i}-paid", "")).split(",")[0].split(".")[0])
            method = str(form.get(f"f-{i}-method", ""))
            got[str(form.get(f"f-{i}-key"))] = {"paid": int(paid) if paid else 0,
                                                 "method": method if method in cm.FEE_METHODS else ""}
        store.update_festival(fid, lambda x: x.__setitem__("fees", {**x.get("fees", {}), **got}))
        return redirect(f"/festival/{quote(fid, safe='')}?done=festival_fees#fees")

    @app.get("/festival/{fid}/fees.xlsx")
    def festival_fees_xlsx(fid: str):
        from st_secretary.exporters.commission_xlsx import write_festival_fees

        x = need_festival(fid)
        sec = next((o for o in fv.officials(x) if o.role == "Главный секретарь"), None)
        tmp = Path(tempfile.mkdtemp(prefix="st-secretary-")) / "Взносы фестиваля.xlsx"
        write_festival_fees(f"Фестиваль «{x['title']}»", fv.fee_rows(x, fest_groups(x)),
                            sec.signature if sec else "", tmp)
        return FileResponse(tmp, filename=tmp.name, media_type=XLSX,
                            background=BackgroundTask(shutil.rmtree, tmp.parent, ignore_errors=True))

    @app.post("/festival/{fid}/gsk")
    async def festival_gsk(request: Request, fid: str):
        """ГСК фестиваля — в карточки всех его соревнований (кроме должностей, где у соревнования своя замена)."""
        need_festival(fid)
        err: dict = {}
        people = officials_from_rows(officials_form(await request.form()), err)
        if err:
            return redirect(f"/festival/{quote(fid, safe='')}?done=festival_gsk_role#gsk")
        rec = store.update_festival(fid, lambda x: x.__setitem__("officials", [fv.official_dict(o) for o in people]))
        locked = store.sync_gsk(rec)
        if locked:
            return redirect(with_done(f"/festival/{quote(fid, safe='')}#gsk", "festival_gsk_locked",
                                        comps="», «".join(locked)))
        return redirect(f"/festival/{quote(fid, safe='')}?done=festival_gsk#gsk")

    @app.post("/festival/{fid}/modes")
    async def festival_modes(request: Request, fid: str):
        """Режимы фестиваля: договоры и табель, взнос, стартовые номера (Правки, п. 19)."""
        x = need_festival(fid)
        form = await request.form()
        new = {k: str(form.get(k)) for k in fv.MODES if str(form.get(k)) in fv.MODES[k]}
        amount = re.sub(r"\D", "", str(form.get("fee_amount", "")).split(",")[0].split(".")[0])
        per = str(form.get("fee_per", ""))
        joint = new.get("contracts") == "festival" and fv.mode(x, "contracts") != "festival" \
            and not x.get("contracts")  # впервые один договор на фестиваль — собрать из договоров соревнований
        datas = [f.read_json(CONTRACTS) for m in x["members"] if (f := store.get(m))] if joint else []

        def change(rec):
            rec["modes"] = {**fv.modes(rec), **new}
            rec["fee"] = {"amount": int(amount) if amount else None, "per": per if per in fv.FEE_PER else fv.FEE_PER[0]}
            if joint:
                rec["contracts"] = fv.merge_contracts(datas)

        store.update_festival(fid, change)
        return redirect(f"/festival/{quote(fid, safe='')}?done=festival_modes#modes")

    @app.post("/festival/{fid}/edit")
    async def festival_edit(request: Request, fid: str):
        x = need_festival(fid)
        form = await request.form()
        members = list(x["members"])
        if form.get("add") and store.get(str(form.get("add"))):
            members.append(str(form.get("add")))
        if form.get("remove"):
            members = [m for m in members if m != str(form.get("remove"))]
        if form.get("do") == "split":
            members = []
        store.set_festival(fid, str(form.get("title", x["title"])), members)
        return redirect("/?done=festival_split" if not members else f"/festival/{quote(fid, safe='')}?done=festival_saved")

    @app.get("/festival/{fid}/backup.zip")
    def festival_backup(fid: str):
        x = need_festival(fid)
        folders = [store.get(m).path for m in x["members"] if store.get(m)]
        data = bk.make_festival(folders, x, app.state.clock())
        name = f"Фестиваль {x['title']} — {app.state.clock():%Y-%m-%d %H-%M}.zip"
        return Response(data, media_type="application/zip",
                        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}"})

    @app.post("/open-backups")
    def open_backups():
        d = bk.backups_dir(store.root)
        d.mkdir(parents=True, exist_ok=True)
        app.state.opener(d)
        return redirect("/?done=opened")

    # ------------------------------------------------------------ судейская практика (по всем соревнованиям)

    def practice_all() -> list:
        recs = []
        for f in store.all():
            try:
                comp = f.load()
            except Exception:  # noqa: BLE001 — карточка не читается: соревнование пропускается
                continue
            recs += pt_.records_of(comp, f.id, f.results_data().get("judges", {}), f.contracts().get("extra", []))
        return pt_.judges(recs)

    @app.get("/practice")
    def practice_page(request: Request):
        people = practice_all()
        today = app.state.clock().date()
        return page(request, "practice.html", people=people, competitions=len(store.all()),
                    progress=lambda j: pt_.progress(j, today), today=today)

    @app.get("/practice.xlsx")
    def practice_download():
        tmp = Path(tempfile.mkdtemp(prefix="st-secretary-")) / "Судейская практика.xlsx"
        pt_.write_practice(practice_all(), tmp, app.state.clock().date())
        return FileResponse(tmp, filename=tmp.name, media_type=XLSX,
                            background=BackgroundTask(shutil.rmtree, tmp.parent, ignore_errors=True))

    @app.post("/practice/open")
    def practice_open():
        path = store.root / "Судейская практика.xlsx"
        store.root.mkdir(parents=True, exist_ok=True)
        try:
            pt_.write_practice(practice_all(), path, app.state.clock().date())
        except PermissionError:
            return redirect("/practice?done=doc_locked")
        app.state.opener(path)
        return redirect("/practice?done=opened")
