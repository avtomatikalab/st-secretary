"""Интерфейс в браузере: локальный сервер на ноутбуке секретариата.

Страницы открываются по адресу http://127.0.0.1:<порт> только на этом компьютере. Всё хранится в
папке данных (см. store.py). Интернет не нужен: страницы ничего не подгружают извне.
"""

from __future__ import annotations

import secrets
import threading
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.exceptions import HTTPException

from st_secretary import __version__, system, updates
from st_secretary.competition import LEVEL_LABELS, RESULT_KINDS, UNIT_KINDS
from st_secretary.importers.card_xlsx import CardError
from st_secretary.issues import CHECKED, ERROR, FIXED, INFO, SEVERITY_LABEL, WARNING, Issue
from st_secretary.textclean import from_years
from st_secretary.web.common import (
    HERE,
    CannotOpen,
    _base,
    _flash,
    _fmt_date,
    _step_url,
    cannot_open_text,
    fix_url,
    log,
    open_in_os,
    team_anchor,
)
from st_secretary.web.forms import empty_zachet
from st_secretary.web.pages import (
    admission,
    awards,
    board,
    competition,
    contracts,
    home,
    preapps,
    results,
    start,
    verify,
)
from st_secretary.web.pages import updates as update_pages
from st_secretary.web.review import CHECK, DONE, FIX, STATUS_LABEL, issue_key
from st_secretary.web.steps import STEPS
from st_secretary.web.store import CompFolder, Store


def create_app(data_dir: str | Path, opener=None, shutdown=None, docs_dir: str | Path | None = None,
               board_host: str = "0.0.0.0") -> FastAPI:  # табло раздаётся в сеть ноутбука
    """shutdown — как остановить сервер (кнопка «Выключить»); None — кнопки нет (тесты, запуск не из окна).
    docs_dir — где хранить сканы документов участников (по умолчанию — папка в профиле, не в облаке).
    board_host — где слушает табло (все адреса ноутбука; в тестах — только 127.0.0.1)."""
    store = Store(data_dir, docs_dir)
    app = FastAPI(title="СТ-Секретарь", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.shutdown = shutdown
    app.state.store = store
    app.state.opener = opener or open_in_os
    app.state.clock = datetime.now  # часы — отдельно, чтобы в тестах проверять «час на протесты»
    # новая версия: ответ GitHub кладёт cli.py (проверка в фоне при запуске); установка — только в переносной
    app.state.update, app.state.update_state, app.state.update_hidden = None, "off", False
    root = updates.portable_root()
    app.state.installer = updates.Installer(root) if root else None
    app.state.restart = False  # выключиться с кодом «перезапустить» (новая версия распакована)
    app.state.journal = None  # журнал программы в файл (journal.py) — подключает cli.py
    run_lock = threading.RLock()  # Результаты_дистанции.json: пишут и страница секретаря, и телефоны судей
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
    templates = Jinja2Templates(directory=HERE / "templates")
    templates.env.filters["d"] = _fmt_date
    templates.env.filters["from_years"] = from_years
    templates.env.filters["team_anchor"] = team_anchor
    # Метка для адресов стилей и скриптов: меняется вместе с файлами, чтобы браузер не держал старую версию.
    static_version = f"{__version__}-{max(int(p.stat().st_mtime) for p in (HERE / 'static').iterdir())}"
    templates.env.globals.update(steps=STEPS, step_url=_step_url, version=static_version, app_version=__version__,
                                 labels=SEVERITY_LABEL,
                                 level_labels=LEVEL_LABELS, empty_zachet=empty_zachet(),
                                 ERROR=ERROR, WARNING=WARNING, CHECKED=CHECKED, FIXED=FIXED, INFO=INFO,
                                 issue_key=issue_key, status_label=STATUS_LABEL, CHECK=CHECK, FIX=FIX, DONE=DONE,
                                 launcher=system.launcher(), console=system.console(), fix_url=fix_url,
                                 result_kinds=RESULT_KINDS, unit_kinds=UNIT_KINDS)

    def page(request: Request, name: str, status_code: int = 200, background=None, **ctx):
        ctx = {"flash": _flash(request), "can_stop": app.state.shutdown is not None,
               "journal_on": app.state.journal is not None, **ctx}
        return templates.TemplateResponse(request, name, ctx, status_code=status_code, background=background)

    def folder(cid: str) -> CompFolder:
        f = store.get(cid)
        if f is None:
            raise HTTPException(404)
        return f

    def comp_ctx(f: CompFolder) -> dict:
        comp, card_errors = None, []
        try:
            comp = f.load()
        except CardError as e:
            card_errors = e.issues
        except Exception as e:  # noqa: BLE001 — повреждённый файл: показать, а не упасть
            card_errors = [Issue(ERROR, f"файл карточки не читается: {e}")]
        return {"folder": f, "comp": comp, "card_errors": card_errors, "base": _base(f)}

    class Ctx:  # общее для страниц: хранилище, шаблоны, помощники; страницы добавляют свои
        def update(self, **kw):
            self.__dict__.update(kw)

    cx = Ctx()
    cx.update(store=store, templates=templates, run_lock=run_lock, page=page, folder=folder, comp_ctx=comp_ctx,
              board_host=board_host)
    # страницы по шагам работы (pages/); общие помощники страниц — в cx, их берут и другие страницы
    for module in (home, competition, preapps, admission, awards, contracts, results, start, board, verify,
                   update_pages):
        module.register(app, cx)

    # ------------------------------------------------------------ ошибки

    failed_opens: dict[str, Path] = {}  # код → файл, который не открылся (скачать через браузер)

    @app.exception_handler(CannotOpen)
    async def cannot_open(request: Request, exc: CannotOpen):
        """Кнопка «Открыть», а открыть нечем: сказать это прямо и дать скачать файл (он уже сохранён)."""
        log.warning("Не открылся %s: %s", exc.path, exc.reason)
        token = secrets.token_urlsafe(12)
        failed_opens[token] = exc.path
        while len(failed_opens) > 50:
            failed_opens.pop(next(iter(failed_opens)))
        ref = urlsplit(request.headers.get("referer", ""))
        back = (ref.path + (f"?{ref.query}" if ref.query else "")) if ref.path.startswith("/") else "/"
        return page(request, "cannot_open.html", status_code=200, path=exc.path, reason=exc.reason, back=back,
                    token=token, is_dir=exc.path.is_dir(), **cannot_open_text(exc.path))

    @app.get("/open-failed/{token}")
    def open_failed_download(token: str):
        path = failed_opens.get(token)
        if path is None or not path.is_file():
            raise HTTPException(404)
        inline = path.suffix.lower() == ".pdf"  # PDF браузер покажет сам
        return FileResponse(path, filename=path.name, content_disposition_type="inline" if inline else "attachment")

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException):
        if exc.status_code == 404:
            return page(request, "error.html", status_code=404, title="Страница не найдена",
                        text="Такого соревнования или страницы нет. Возможно, папку соревнования переименовали "
                             "или перенесли.")
        return page(request, "error.html", status_code=exc.status_code, title="Не получилось", text=str(exc.detail))

    @app.exception_handler(Exception)
    async def crash(request: Request, exc: Exception):
        log.exception("Ошибка при обработке %s", request.url.path)
        return page(request, "error.html", status_code=500, title="Что-то пошло не так",
                    text=f"{type(exc).__name__}: {exc}", crash=True)

    return app
