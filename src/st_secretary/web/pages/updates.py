"""Новая версия: сообщение на главной, страница выпуска, установка кнопкой (переносная версия) и перезапуск.

Проверку делает cli.py в фоне при запуске и кладёт ответ в app.state.update (Release или None); пока ответа
нет — app.state.update_state == "pending". Установка — updates.Installer в app.state.installer (только
переносная версия; из исходников — None).
"""

from __future__ import annotations

from fastapi import Request
from fastapi.responses import JSONResponse, Response
from starlette.background import BackgroundTask
from starlette.exceptions import HTTPException

from st_secretary import __version__, updates
from st_secretary import backup as bk
from st_secretary.web.common import _redirect, log


def register(app, cx) -> None:
    page = cx.page
    store = cx.store

    def can_install() -> bool:
        rel = app.state.update
        return bool(rel and rel.installable and app.state.installer and app.state.shutdown is not None)

    def ctx() -> dict:
        return {"rel": app.state.update, "current": __version__, "can_install": can_install(),
                "installer": app.state.installer, "portable": app.state.installer is not None}

    @app.get("/update/banner")
    def update_banner(request: Request):
        """Кусок главной страницы: 202 — GitHub ещё не ответил, 204 — показывать нечего."""
        if app.state.update_state == "pending":
            return Response(status_code=202)
        if app.state.update is None or app.state.update_hidden:
            return Response(status_code=204)
        return cx.templates.TemplateResponse(request, "_update_banner.html", ctx())

    @app.post("/update/later")
    def update_later():
        app.state.update_hidden = True  # до следующего запуска программы
        return _redirect("/")

    @app.get("/update")
    def update_page(request: Request):
        if app.state.update is None:
            return _redirect("/")
        return page(request, "update.html", **ctx())

    @app.post("/update/install")
    def update_install():
        if not can_install():
            raise HTTPException(409, "Эту копию программы обновляют вручную — см. страницу выпуска.")

        def backup_first():
            try:
                bk.auto([f.path for f in store.all()], bk.backups_dir(store.root), app.state.clock())
            except OSError as e:
                raise updates.UpdateError(f"резервная копия не сделана ({e}) — обновление отменено") from e

        app.state.installer.start(app.state.update, before=backup_first)
        return _redirect("/update")

    @app.get("/update/status")
    def update_status():
        inst = app.state.installer
        return JSONResponse(inst.status() if inst else {"state": "idle"})

    @app.post("/update/restart")
    def update_restart():
        """Новая версия распакована: выключиться с кодом «перезапустить» — файл запуска поставит её."""
        inst = app.state.installer
        if inst is None or inst.state != "ready" or app.state.shutdown is None:
            raise HTTPException(409, "Новая версия ещё не готова.")
        log.info("Перезапуск для обновления до %s", inst.version)
        app.state.restart = True
        return JSONResponse({"ok": True}, background=BackgroundTask(app.state.shutdown))
