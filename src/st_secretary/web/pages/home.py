"""Главная: список соревнований, новое и загрузка карточки, учебное соревнование, восстановление из копии,
инструкция, судейская практика по всем соревнованиям."""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from urllib.parse import quote

from fastapi import Request
from fastapi.responses import FileResponse, JSONResponse
from starlette.background import BackgroundTask
from starlette.exceptions import HTTPException

from st_secretary import __version__, training
from st_secretary import backup as bk
from st_secretary import practice as pt_
from st_secretary.importers.card_xlsx import CardError, load_card
from st_secretary.web.common import HERE, XLSX, _base, _redirect, _with_done, log
from st_secretary.web.forms import card_to_form, choices, form_from_data, form_to_card


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
        return page(request, "home.html", status_code=status_code, items=items, data_dir=store.root,
                    import_errors=import_errors)

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

    @app.post("/open-data")
    def open_data(request: Request):
        store.root.mkdir(parents=True, exist_ok=True)
        app.state.opener(store.root)
        return _redirect("/?done=opened")

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
        return _redirect(f"{_base(f)}/card/edit?done=created")

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
        return _redirect(f"{_base(f)}?done=training")

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
        return _redirect(f"{_base(f)}?done=imported")

    @app.post("/restore")
    async def restore_backup(request: Request):
        """Соревнование из резервной копии (.zip) — новой папкой, ничего не затирая."""
        up = (await request.form()).get("backup")
        if up is None or not getattr(up, "filename", ""):
            return _redirect("/?done=restore_none")
        try:
            name = bk.restore(await up.read(), store.root, app.state.clock())
        except bk.BackupError as e:
            return _redirect(_with_done("/", "restore_bad", why=str(e)))
        return _redirect(f"/c/{quote(name, safe='')}?done=restored")

    @app.post("/open-backups")
    def open_backups():
        d = bk.backups_dir(store.root)
        d.mkdir(parents=True, exist_ok=True)
        app.state.opener(d)
        return _redirect("/?done=opened")

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
        return page(request, "practice.html", people=people, competitions=len(store.all()))

    @app.get("/practice.xlsx")
    def practice_download():
        tmp = Path(tempfile.mkdtemp(prefix="st-secretary-")) / "Судейская практика.xlsx"
        pt_.write_practice(practice_all(), tmp)
        return FileResponse(tmp, filename=tmp.name, media_type=XLSX,
                            background=BackgroundTask(shutil.rmtree, tmp.parent, ignore_errors=True))

    @app.post("/practice/open")
    def practice_open():
        path = store.root / "Судейская практика.xlsx"
        store.root.mkdir(parents=True, exist_ok=True)
        try:
            pt_.write_practice(practice_all(), path)
        except PermissionError:
            return _redirect("/practice?done=doc_locked")
        app.state.opener(path)
        return _redirect("/practice?done=opened")
