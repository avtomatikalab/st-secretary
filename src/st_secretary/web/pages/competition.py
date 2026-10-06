"""Страница соревнования: обзор шагов, карточка (просмотр и правка), резервная копия, открыть в
Проводнике и Excel, описания шагов в разработке."""

from __future__ import annotations

from dataclasses import replace
from urllib.parse import urlsplit

from fastapi import Request
from fastapi.responses import FileResponse
from starlette.exceptions import HTTPException

from st_secretary import backup as bk
from st_secretary import festival as fv
from st_secretary.issues import ERROR, FIXED, WARNING
from st_secretary.web.common import base_url, redirect, with_done
from st_secretary.web.forms import GROUP_SUGGESTIONS, card_to_form, choices, form_from_data, form_to_card
from st_secretary.web.review import DONE
from st_secretary.web.shared import adm_totals, commission


def register(app, cx) -> None:
    """Обзор соревнования, карточка (/c/{cid}/card…), резервные копии, открыть папку."""
    comp_ctx = cx.comp_ctx
    folder = cx.folder
    page = cx.page
    store = cx.store

    # ------------------------------------------------------------ соревнование

    @app.post("/c/{cid}/backup")
    def backup_now(cid: str):
        f = folder(cid)
        path = bk.make(f.path, bk.backups_dir(store.root), app.state.clock())
        return redirect(with_done(f"{base_url(f)}#backup", "backup_made", file=path.name))

    @app.get("/c/{cid}/backup.zip")
    def backup_download(cid: str):
        """Копия — сразу в браузер (например, сохранить на флешку); она же остаётся в «Резервных копиях»."""
        f = folder(cid)
        path = bk.make(f.path, bk.backups_dir(store.root), app.state.clock())
        return FileResponse(path, filename=path.name, media_type="application/zip")

    @app.get("/c/{cid}")
    def overview(request: Request, cid: str):
        """Обзор соревнования: карточка, заявки, допуск, дальше по порядку."""
        f = folder(cid)
        ctx = comp_ctx(f)
        comp = ctx["comp"]
        card_issues = comp.check() if comp else []
        files = f.preapp_files()
        pre = None
        if comp and files and not any(i.severity == ERROR for i in card_issues):
            r, reviews = store.review(f, comp)
            pre = {"teams": len(r.teams), "entries": len(r.entries), "errors": r.count(ERROR),
                   "warnings": r.count(WARNING), "fixed": r.count(FIXED),
                   "done": sum(v.status == DONE for v in reviews.values()), "files": len(reviews)}
        adm = adm_totals(commission(store, f, comp)[1]) if pre else None
        backups = bk.listing(bk.backups_dir(store.root), f.path.name)
        return page(request, "overview.html", active="", card_issues=card_issues, files=files, pre=pre, adm=adm,
                    backups=backups[:5], backups_count=len(backups), backups_dir=bk.backups_dir(store.root), **ctx)

    @app.post("/c/{cid}/open/{what}")
    def open_thing(request: Request, cid: str, what: str):
        f = folder(cid)
        target = {"folder": f.path, "card": f.card_path, "preapps": f.preapp_dir}.get(what)
        if target is None:
            raise HTTPException(404)
        if what == "preapps":
            f.preapp_dir.mkdir(exist_ok=True)
        app.state.opener(target)
        back = urlsplit(request.headers.get("referer", "")).path
        return redirect(f"{back if back.startswith('/c/') else base_url(f)}?done=opened")

    @app.get("/c/{cid}/card")
    def card_view(request: Request, cid: str):
        """Карточка соревнования для просмотра; правка — кнопкой «Редактировать»."""
        f = folder(cid)
        ctx = comp_ctx(f)
        comp = ctx["comp"]
        ch = choices()
        return page(request, "card_view.html", active="card", issues=comp.check() if comp else [],
                    percent_labels=dict(ch["percent"]), **ctx)

    def fest_gsk(f) -> dict | None:
        """Соревнование фестиваля: какие должности ГСК у него свои (Правки, п. 19)."""
        fest = store.festival_of(f.id)
        return {"own": fv.own_roles(fest, f.id)} if fest else None

    @app.get("/c/{cid}/card/edit")
    def card_edit(request: Request, cid: str):
        f = folder(cid)
        ctx = comp_ctx(f)
        if ctx["comp"] is None:  # файл не читается — исправлять в Excel, форма пустой не открывается
            return redirect(f"{base_url(f)}/card")
        return page(request, "card.html", active="card", form=card_to_form(ctx["comp"]), errors={}, ch=choices(),
                    issues=ctx["comp"].check(), card_version=f.version(), own=store.own_values(),
                    fest_gsk=fest_gsk(f), territories=store.territories(ctx["comp"], f.id), **ctx)

    @app.post("/c/{cid}/card/edit")
    async def card_save(request: Request, cid: str):
        """Сохранить карточку из формы (на фестивале — с ГСК фестиваля и «своей» заменой)."""
        f = folder(cid)
        data = await request.form()
        form = form_from_data(data)
        comp, errors = form_to_card(form)
        sent_version = str(data.get("version", ""))
        conflict = comp is not None and sent_version and sent_version != f.version() and not data.get("force")
        save_error = None
        if comp is not None and not conflict and (fest := store.festival_of(f.id)):
            # фестиваль: «своя» — должности, отмеченные галочкой, и те, которых нет в ГСК фестиваля
            common = {o.role for o in fv.officials(fest)}
            own = list(dict.fromkeys(r["role"] for r in form["officials"] if r["fio"] and r["role"]
                                     and (r["own"] or r["role"] not in common)))
            rec = store.update_festival(fest["id"], lambda x: x.setdefault("own_gsk", {}).__setitem__(f.id, own))
            comp = replace(comp, officials=fv.effective(rec, f.id, comp.officials))
        if comp is not None and not conflict:
            try:
                f.save(comp)
                store.keep_norms_copy(f, comp)  # своя редакция норм — копией в папку соревнования (п. 59)
                store.remember_own(comp, GROUP_SUGGESTIONS)  # неофициальные: свои значения — в подсказки
                return redirect(f"{base_url(f)}/card?done=saved")
            except PermissionError:
                save_error = ("Файл карточки сейчас открыт в Excel, поэтому сохранить не получилось. Закройте его "
                              "в Excel и нажмите «Сохранить» ещё раз — всё, что вы ввели, осталось на странице.")
        ctx = comp_ctx(f)
        return page(request, "card.html", status_code=422 if errors else 409, active="card", form=form,
                    errors=errors, ch=choices(), issues=[], card_version=sent_version, conflict=conflict,
                    save_error=save_error, own=store.own_values(), fest_gsk=fest_gsk(f),
                    territories=store.territories(comp or ctx["comp"], f.id), **ctx)

    @app.post("/c/{cid}/card/forget")
    async def card_forget(request: Request, cid: str):
        """Убрать своё значение из подсказок (запомнено на этом компьютере)."""
        f = folder(cid)
        form = await request.form()
        store.forget_own(str(form.get("kind", "")), str(form.get("value", "")))
        return redirect(f"{base_url(f)}/card/edit#own-values")
