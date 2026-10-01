"""«Сообщить» и «Мои сообщения» (Правки, п. 43; решение 044): приём сообщения с панели на любой странице, список,
отправка разработчику одним файлом (письмо mailto:), загрузка чужого файла сообщений."""

from __future__ import annotations

import base64
import binascii
import platform
from datetime import datetime
from urllib.parse import unquote, urlsplit

from fastapi import Request
from fastapi.responses import FileResponse, JSONResponse, Response
from starlette.exceptions import HTTPException

from st_secretary import feedback as fb
from st_secretary import journal, updates, version_label
from st_secretary.web.common import redirect

MAX_IMAGE = 15 * 1024 * 1024  # снимок экрана с пометками — не больше 15 МБ


def _lines(items, limit: int = 10) -> list[str]:
    return [" ".join(str(x).split())[:200] for x in (items if isinstance(items, list) else [])][-limit:]


def register(app, cx) -> None:
    """«Сообщить» и «Мои сообщения» (/feedback…)."""
    store = cx.store
    page = cx.page

    def fb_folder():
        return fb.folder_for(store.root)

    def tail() -> str:
        return fb.journal_tail(journal.folder_for(store.root))

    def version() -> str:
        return version_label()

    def where() -> str:
        """«0.4.0 бета, переносная, Windows 10» — для шапки сообщения."""
        kind = "переносная" if updates.portable_root() else "из исходников"
        return f"{version()}, {kind}, {platform.system()} {platform.release()}"

    def unsent() -> int:
        return fb.unsent_count(fb_folder())

    @app.post("/feedback")
    async def feedback_save(request: Request):
        """Сообщение с панели «Сообщить»: текст, снимок (по желанию) и собранное само — без значений полей."""
        try:
            data = await request.json()
        except ValueError:
            data = None
        if not isinstance(data, dict):
            return JSONResponse({"ok": False, "error": "не те данные"}, status_code=400)
        text = str(data.get("text", "")).strip()[:5000]
        if not text:
            return JSONResponse({"ok": False, "error": "Напишите, что произошло — хотя бы одну строку."}, status_code=422)
        png = None
        shot = str(data.get("image", ""))
        if shot.startswith("data:image/png;base64,"):
            try:
                png = base64.b64decode(shot.split(",", 1)[1], validate=True)
            except (binascii.Error, ValueError):
                png = None
            if png and (len(png) > MAX_IMAGE or not png.startswith(b"\x89PNG")):
                png = None
        pg = data.get("page") if isinstance(data.get("page"), dict) else {}
        url = unquote(str(pg.get("url", "")))[:300]  # «/c/2026-10-03 Учебный…/start?z=М/Ж_3» — читаемо
        path = urlsplit(url).path
        competition = path.split("/")[2] if path.startswith("/c/") and len(path.split("/")) > 2 else ""
        tpl, module = str(pg.get("template", ""))[:80], str(pg.get("module", ""))[:120]
        # сначала шаблон и модуль — по ним место в коде находится сразу; адрес — последним (длинный)
        code = (tpl + (f" ({module.replace('st_secretary.', '').replace('.', '/')}.py)" if module else "")).strip()
        if pg.get("section"):
            code += f", раздел {str(pg['section'])[:60]}"
        page_line = f"{code} — {url}" if code else url
        errors = []
        if pg.get("error"):
            errors.append("Ошибка на странице «Что-то пошло не так»:\n" + str(pg["error"])[:8000])
        js_errors = _lines(data.get("js_errors"), 10)
        if js_errors:
            errors.append("Ошибки JavaScript на странице:\n" + "\n".join(js_errors))
        log = tail()
        if log:
            errors.append("Журнал программы, последние строки:\n" + log)
        meta = {"who": str(data.get("who", ""))[:80], "competition": competition, "page": page_line,
                "place": str(data.get("place", ""))[:300], "version": where(),
                "browser": str(request.headers.get("user-agent", ""))[:200], "window": str(pg.get("window", ""))[:20],
                "page_title": str(pg.get("title", ""))[:120], "path": _lines(data.get("path"), 10),
                "errors": "\n\n".join(errors)}
        m = fb.save(fb_folder(), str(data.get("kind", "")), text, meta, png, now=app.state.clock())
        return JSONResponse({"ok": True, "file": m.path.name, "folder": str(fb_folder()), "unsent": unsent(),
                             "image": bool(png)})

    @app.get("/feedback")
    def feedback_page(request: Request, mail: str = ""):
        folder = fb_folder()
        items = fb.messages(folder)
        last = fb.last_bundle(folder)
        mail_link = ""
        if last:
            mail_link = fb.mailto(len(last.get("numbers", [])), version(), last.get("file", ""))
        return page(request, "feedback.html", items=items, folder=None, fb_dir=folder, unsent=unsent(),
                    last=last, mail_link=mail_link, auto_mail=mail == "1", email=fb.SUPPORT_EMAIL,
                    subject=fb.subject(len(last.get("numbers", [])), version()) if last else "",
                    kinds=fb.KINDS)

    def send(numbers: list[str] | None = None):
        made = fb.bundle(fb_folder(), tail(), version(), now=app.state.clock(), numbers=numbers)
        if made is None:
            return redirect("/feedback?done=fb_nothing")
        path, _ = made
        app.state.opener(path.parent)  # папка с файлом — перетащить его в письмо
        return redirect("/feedback?done=fb_sent&mail=1")

    @app.post("/feedback/send")
    def feedback_send():
        """«Отправить разработчику»: неотправленные — одним zip, письмо с адресом и темой."""
        return send()

    @app.post("/feedback/again")
    def feedback_again():
        """«Собрать заново» — тот же набор, если письмо не ушло."""
        last = fb.last_bundle(fb_folder())
        return send(list(last.get("numbers", []))) if last else redirect("/feedback?done=fb_nothing")

    @app.get("/feedback/all.zip")
    def feedback_all():
        name = f"СТ-Секретарь — все сообщения {datetime.now():%Y-%m-%d}.zip"
        from urllib.parse import quote

        return Response(fb.all_zip(fb_folder(), tail(), version()), media_type="application/zip",
                        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}"})

    @app.post("/feedback/open")
    def feedback_open():
        folder = fb_folder()
        folder.mkdir(parents=True, exist_ok=True)
        app.state.opener(folder)
        return redirect("/feedback?done=opened")

    @app.post("/feedback/remove")
    async def feedback_remove(request: Request):
        name = str((await request.form()).get("name", ""))
        fb.remove(fb_folder(), name)
        return redirect("/feedback?done=fb_removed")

    @app.post("/feedback/import")
    async def feedback_import(request: Request):
        """Владелец добавляет присланные файлы сообщений других секретарей в свой список (без повторов)."""
        up = (await request.form()).get("file")
        try:
            added, skipped = fb.import_bundle(fb_folder(), await up.read()) if up is not None else (0, 0)
        except Exception:  # noqa: BLE001 — не zip или повреждён: сказать, а не упасть
            return redirect("/feedback?done=fb_bad")
        return redirect(f"/feedback?done=fb_imported&added={added}&skipped={skipped}")

    @app.get("/feedback/img")
    def feedback_img(name: str = ""):
        p = fb_folder() / name.replace("/", "").replace("\\", "")
        if p.suffix != ".png" or not p.is_file():
            raise HTTPException(404)
        return FileResponse(p, media_type="image/png")
