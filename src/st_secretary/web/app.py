"""Интерфейс в браузере: локальный сервер на ноутбуке секретариата.

Страницы открываются по адресу http://127.0.0.1:<порт> только на этом компьютере. Всё хранится в
папке данных (см. store.py). Интернет не нужен: страницы ничего не подгружают извне.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import tempfile
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.background import BackgroundTask
from starlette.exceptions import HTTPException

from st_secretary import __version__
from st_secretary.competition import LEVEL_LABELS
from st_secretary.importers.card_xlsx import CardError, load_card
from st_secretary.importers.preapp_xlsx import read_preapplication
from st_secretary.issues import CHECKED, ERROR, FIXED, INFO, SEVERITY_LABEL, SEVERITY_ORDER, WARNING, Issue
from st_secretary.qualification import Qual
from st_secretary.textclean import from_years
from st_secretary.web import preapp_form as pf
from st_secretary.web.review import CHECK, DONE, FIX, STATUS_LABEL, issue_key
from st_secretary.web.forms import card_to_form, choices, empty_zachet, form_from_data, form_to_card
from st_secretary.web.steps import BY_SLUG, STEPS
from st_secretary.web.store import SUMMARY, CompFolder, Store

log = logging.getLogger("st_secretary.web")
HERE = Path(__file__).parent
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def open_in_os(path: Path) -> None:
    """Открыть файл или папку программой по умолчанию (Excel, Проводник)."""
    if sys.platform.startswith("win"):
        os.startfile(path)  # noqa: S606 — путь всегда внутри папки данных
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])


def _fmt_date(d) -> str:
    return d.strftime("%d.%m.%Y") if isinstance(d, date) else ("" if d is None else str(d))


def _base(f: CompFolder) -> str:
    return "/c/" + quote(f.id, safe="")


def _step_url(base: str, step) -> str:
    return f"{base}/{step.slug}" if step.ready else f"{base}/step/{step.slug}"


def _redirect(url: str) -> RedirectResponse:
    return RedirectResponse(url, status_code=303)


def _with_done(url: str, done: str) -> str:
    """Адрес с сообщением о сделанном (?done=…); якорь (#…) сохраняется."""
    parts = urlsplit(url)
    q = [(k, v) for k, v in parse_qsl(parts.query) if k != "done"] + [("done", done)]
    return urlunsplit(parts._replace(query=urlencode(q)))


def _flash(request: Request) -> dict | None:
    q = request.query_params
    done = q.get("done")
    if done == "uploaded":
        parts = []
        if q.get("added", "0") != "0":
            parts.append(f"добавлено заявок: {q['added']}")
        if q.get("replaced", "0") != "0":
            parts.append(f"заменено исправленными: {q['replaced']}")
        if q.get("skipped", "0") != "0":
            parts.append(f"пропущено файлов не Excel: {q['skipped']}")
        text = ("Готово: " + ", ".join(parts) + ".") if parts else ""
        if q.get("replaced", "0") != "0":
            text += " Прежние варианты заменённых файлов — в папке «Предзаявки\\Прежние версии»."
        if q.get("locked", "0") != "0":
            return {"kind": "err", "text": f"{text} Не заменено файлов: {q['locked']} — они сейчас открыты в Excel. "
                                           "Закройте их и перетащите заявки ещё раз.".strip()}
        return {"kind": "ok", "text": text} if parts else {"kind": "err", "text": "Файлы не выбраны."}
    texts = {
        "created": ("ok", "Соревнование создано. Заполните судейскую коллегию и зачёты и нажмите «Сохранить»."),
        "imported": ("ok", "Карточка загружена из Excel. Посмотрите замечания проверки, если они есть."),
        "saved": ("ok", "Карточка сохранена."),
        "removed": ("ok", f"Заявка «{q.get('name', '')}» убрана из обработки — файл перенесён в папку "
                          "«Предзаявки\\Убранные», его можно вернуть."),
        "summary": ("ok", "Сводка сохранена в папке соревнования и открывается в Excel."),
        "psaved": ("ok", "Заявка сохранена и проверена заново — результат ниже. Прежний вариант файла лежит в папке "
                         "«Предзаявки\\Прежние версии»."),
        "status": ("ok", "Статус заявки изменён."),
        "checked": ("ok", "Отмечено: проверено. Замечание больше не считается в «Проверить»."),
        "unchecked": ("ok", "Отметка «проверено» снята — замечание снова в «Проверить»."),
        "pcreated": ("ok", f"Заявка сохранена в файл «{q.get('file', '')}» в папке «Предзаявки» и проверена — "
                           "результат ниже."),
        "locked": ("err", "Сводка сейчас открыта в Excel. Закройте её там и нажмите кнопку ещё раз."),
        "opened": ("ok", "Открываю…"),
    }
    if done in texts:
        kind, text = texts[done]
        return {"kind": kind, "text": text}
    return None


def create_app(data_dir: str | Path, opener=None, shutdown=None) -> FastAPI:
    """shutdown — как остановить сервер (кнопка «Выключить»); None — кнопки нет (тесты, запуск не из окна)."""
    store = Store(data_dir)
    app = FastAPI(title="СТ-Секретарь", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.shutdown = shutdown
    app.state.store = store
    app.state.opener = opener or open_in_os
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
    templates = Jinja2Templates(directory=HERE / "templates")
    templates.env.filters["d"] = _fmt_date
    templates.env.filters["from_years"] = from_years
    # Метка для адресов стилей и скриптов: меняется вместе с файлами, чтобы браузер не держал старую версию.
    static_version = f"{__version__}-{max(int(p.stat().st_mtime) for p in (HERE / 'static').iterdir())}"
    templates.env.globals.update(steps=STEPS, step_url=_step_url, version=static_version, labels=SEVERITY_LABEL,
                                 level_labels=LEVEL_LABELS, empty_zachet=empty_zachet(),
                                 ERROR=ERROR, WARNING=WARNING, CHECKED=CHECKED, FIXED=FIXED, INFO=INFO,
                                 issue_key=issue_key, status_label=STATUS_LABEL, CHECK=CHECK, FIX=FIX, DONE=DONE)

    def page(request: Request, name: str, status_code: int = 200, background=None, **ctx):
        ctx = {"flash": _flash(request), "can_stop": app.state.shutdown is not None, **ctx}
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

    # ------------------------------------------------------------ соревнование

    @app.get("/c/{cid}")
    def overview(request: Request, cid: str):
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
        return page(request, "overview.html", active="", card_issues=card_issues, files=files, pre=pre, **ctx)

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
        return _redirect(f"{back if back.startswith('/c/') else _base(f)}?done=opened")

    @app.get("/c/{cid}/card")
    def card_view(request: Request, cid: str):
        """Карточка соревнования для просмотра; правка — кнопкой «Редактировать»."""
        f = folder(cid)
        ctx = comp_ctx(f)
        comp = ctx["comp"]
        ch = choices()
        return page(request, "card_view.html", active="card", issues=comp.check() if comp else [],
                    percent_labels=dict(ch["percent"]), **ctx)

    @app.get("/c/{cid}/card/edit")
    def card_edit(request: Request, cid: str):
        f = folder(cid)
        ctx = comp_ctx(f)
        if ctx["comp"] is None:  # файл не читается — исправлять в Excel, форма пустой не открывается
            return _redirect(f"{_base(f)}/card")
        return page(request, "card.html", active="card", form=card_to_form(ctx["comp"]), errors={}, ch=choices(),
                    issues=ctx["comp"].check(), card_version=f.version(), **ctx)

    @app.post("/c/{cid}/card/edit")
    async def card_save(request: Request, cid: str):
        f = folder(cid)
        data = await request.form()
        form = form_from_data(data)
        comp, errors = form_to_card(form)
        sent_version = str(data.get("version", ""))
        conflict = comp is not None and sent_version and sent_version != f.version() and not data.get("force")
        save_error = None
        if comp is not None and not conflict:
            try:
                f.save(comp)
                return _redirect(f"{_base(f)}/card?done=saved")
            except PermissionError:
                save_error = ("Файл карточки сейчас открыт в Excel, поэтому сохранить не получилось. Закройте его "
                              "в Excel и нажмите «Сохранить» ещё раз — всё, что вы ввели, осталось на странице.")
        ctx = comp_ctx(f)
        return page(request, "card.html", status_code=422 if errors else 409, active="card", form=form,
                    errors=errors, ch=choices(), issues=[], card_version=sent_version, conflict=conflict,
                    save_error=save_error, **ctx)

    # ------------------------------------------------------------ предзаявки

    def preapp_result(f: CompFolder):
        try:
            comp = f.load()
        except CardError:
            raise HTTPException(409, "Карточка соревнования заполнена с ошибками — откройте её и исправьте.") from None
        if any(i.severity == ERROR for i in comp.check()):
            raise HTTPException(409, "Сначала исправьте ошибки в карточке соревнования.")
        if not f.preapp_files():
            raise HTTPException(409, "Пока нет ни одной заявки — добавьте файлы на странице «Предварительные заявки».")
        result, reviews = store.review(f, comp)
        return comp, result, {src: r.label for src, r in reviews.items()}

    @app.get("/c/{cid}/preapps")
    def preapps_page(request: Request, cid: str):
        f = folder(cid)
        ctx = comp_ctx(f)
        comp = ctx["comp"]
        files = f.preapp_files()
        blocked, result, view = [], None, None
        if comp:
            blocked = [i for i in comp.check() if i.severity == ERROR]
            if not blocked and files:
                result, reviews = store.review(f, comp)
                view = _preapp_view(files, result, reviews)
        plain_rows = [{"file": p.name, "team": None, "counts": {}} for p in files]
        return page(request, "preapps.html", active="preapps", files=files, blocked=blocked, result=result,
                    view=view, plain_rows=plain_rows, **ctx)

    @app.post("/c/{cid}/preapps/upload")
    async def preapps_upload(request: Request, cid: str):
        f = folder(cid)
        added = replaced = skipped = locked = 0
        for up in (await request.form()).getlist("files"):
            name = getattr(up, "filename", "") or ""
            if not name:
                continue
            data = await up.read()
            try:
                if f.add_preapp(name, data):
                    replaced += 1
                else:
                    added += 1
            except ValueError:
                skipped += 1
            except PermissionError:
                locked += 1
        q = urlencode({"done": "uploaded", "added": added, "replaced": replaced, "skipped": skipped, "locked": locked})
        return _redirect(f"{_base(f)}/preapps?{q}")

    @app.post("/c/{cid}/preapps/remove")
    async def preapps_remove(request: Request, cid: str):
        f = folder(cid)
        name = str((await request.form()).get("name", ""))
        try:
            f.remove_preapp(name)
        except FileNotFoundError:
            raise HTTPException(404) from None
        return _redirect(f"{_base(f)}/preapps?{urlencode({'done': 'removed', 'name': name})}")

    # ------------------------------------------------------------ заявка в форме

    def need_comp(f: CompFolder):
        try:
            comp = f.load()
        except CardError:
            raise HTTPException(409, "Карточка соревнования заполнена с ошибками — откройте её и исправьте.") from None
        if any(i.severity == ERROR for i in comp.check()):
            raise HTTPException(409, "Сначала исправьте ошибки в карточке соревнования: без неё заявку не с чем сверять.")
        return comp

    def render_edit(request: Request, f: CompFolder, comp, form: dict, *, file: str = "", version: str = "",
                    issues: list[Issue] | None = None, errors: dict | None = None, status_code: int = 200, **extra):
        head_issues, row_issues, marks = pf.issue_marks(issues or [])
        counts = Counter(i.severity for i in issues or [])
        return page(request, "preapp_edit.html", status_code=status_code, active="preapps", form=form,
                    errors=errors or {}, file=file, version=version, head_issues=head_issues, row_issues=row_issues,
                    marks=marks, counts=counts, checked=issues is not None, ch=pf.choices(comp, form),
                    empty_row=pf.empty_row(comp), **comp_ctx(f), **extra)

    def team_url(f: CompFolder, name: str, **q) -> str:
        return f"{_base(f)}/preapps/team?{urlencode({'file': name, **q})}"

    def need_file(f: CompFolder, name: str) -> Path:
        path = f.preapp_path(name)
        if path is None:
            raise HTTPException(404)
        return path

    def back_to(request: Request, f: CompFolder, sent: str, default: str) -> str:
        """Вернуться туда, откуда нажали кнопку (только внутри этого соревнования)."""
        return sent if sent.startswith(_base(f) + "/") else default

    @app.get("/c/{cid}/preapps/team")
    def preapp_team(request: Request, cid: str, file: str = ""):
        """Карточка команды: заявка для просмотра, замечания, статус; правка — отдельной кнопкой."""
        f = folder(cid)
        comp = need_comp(f)
        path = need_file(f, file)
        result, reviews = store.review(f, comp)
        team = next((t for t in result.teams if t.source == path.name), None)
        issues = sorted((i for i in result.issues if i.source == path.name),
                        key=lambda i: (SEVERITY_ORDER[i.severity], i.row))
        worst: dict[int, str] = {}
        for i in issues:
            if i.row and i.severity in (ERROR, WARNING, CHECKED) and i.row not in worst:
                worst[i.row] = i.severity
        return page(request, "preapp_team.html", active="preapps", file=path.name, team=team, issues=issues,
                    review=reviews[path.name], counts=Counter(i.severity for i in issues), worst=worst,
                    show=pf.columns(comp, [vars(e) for e in team.entries] if team else []),
                    here=team_url(f, path.name), **comp_ctx(f))

    @app.post("/c/{cid}/preapps/status")
    async def preapp_status(request: Request, cid: str):
        f = folder(cid)
        comp = need_comp(f)
        data = await request.form()
        path = need_file(f, str(data.get("file", "")))
        status = str(data.get("status", ""))
        if status not in STATUS_LABEL:
            raise HTTPException(400, "Неизвестный статус заявки.")
        result, _ = store.review(f, comp)
        mine = [i for i in result.issues if i.source == path.name]
        if status == DONE and any(i.severity == ERROR for i in mine):
            raise HTTPException(409, "В заявке есть ошибки — пока они не исправлены, отметить её «Проверено» нельзя. "
                                     "Исправьте заявку или поставьте статус «Исправить».")
        if status == DONE:  # проверена вся заявка — значит, и каждое «проверить» в ней
            f.set_checked(path.name, [issue_key(i) for i in mine if i.severity == WARNING])
        f.set_status(path.name, status)
        back = back_to(request, f, str(data.get("back", "")), team_url(f, path.name))
        return _redirect(_with_done(back, "status"))

    @app.post("/c/{cid}/preapps/check")
    async def preapp_check(request: Request, cid: str):
        f = folder(cid)
        data = await request.form()
        path = need_file(f, str(data.get("file", "")))
        on = data.get("on", "1") == "1"
        f.set_checked(path.name, [str(data.get("key", ""))], on)
        back = back_to(request, f, str(data.get("back", "")), team_url(f, path.name))
        return _redirect(_with_done(back, "checked" if on else "unchecked"))

    @app.get("/c/{cid}/preapps/edit")
    def preapp_edit(request: Request, cid: str, file: str = ""):
        f = folder(cid)
        comp = need_comp(f)
        path = need_file(f, file)
        result, _ = store.review(f, comp)
        team = next((t for t in result.teams if t.source == path.name), None)
        form = pf.app_to_form(read_preapplication(path), team, comp)
        return render_edit(request, f, comp, form, file=path.name, version=f.file_version(path),
                           issues=[i for i in result.issues if i.source == path.name], team=team)

    @app.get("/c/{cid}/preapps/new")
    def preapp_new(request: Request, cid: str):
        f = folder(cid)
        comp = need_comp(f)
        return render_edit(request, f, comp, pf.app_to_form(None, None, comp))

    @app.post("/c/{cid}/preapps/save")
    async def preapp_save(request: Request, cid: str):
        f = folder(cid)
        comp = need_comp(f)
        data = await request.form()
        name, version = str(data.get("file", "")), str(data.get("version", ""))
        form = pf.form_from_data(data)
        head, rows, errors = pf.form_to_file(form)
        path = f.preapp_path(name) if name else None
        conflict = not errors and path is not None and version and version != f.file_version(path) \
            and not data.get("force")
        save_error = None
        if not errors and not conflict:
            try:
                saved = f.save_preapp(name if path else None, head, rows, [q.label for q in Qual])
                return _redirect(team_url(f, saved, done="psaved" if path else "pcreated"))
            except PermissionError:
                save_error = ("Файл заявки сейчас открыт в Excel, поэтому сохранить не получилось. Закройте его в "
                              "Excel и нажмите «Сохранить» ещё раз — всё, что вы ввели, осталось на странице.")
        return render_edit(request, f, comp, form, file=name if path else "", version=version, errors=errors,
                           status_code=422 if errors else 409, conflict=conflict, save_error=save_error)

    @app.post("/c/{cid}/preapps/open")
    async def preapp_open(request: Request, cid: str):
        f = folder(cid)
        data = await request.form()
        path = need_file(f, str(data.get("name", "")))
        app.state.opener(path)
        return _redirect(_with_done(back_to(request, f, str(data.get("back", "")), team_url(f, path.name)), "opened"))

    @app.post("/c/{cid}/preapps/summary")
    def preapps_summary_open(request: Request, cid: str):
        f = folder(cid)
        comp, result, statuses = preapp_result(f)
        try:
            path = f.write_summary(result, comp, statuses=statuses)
        except PermissionError:
            return _redirect(f"{_base(f)}/preapps?done=locked")
        app.state.opener(path)
        return _redirect(f"{_base(f)}/preapps?done=summary")

    @app.get("/c/{cid}/preapps/summary.xlsx")
    def preapps_summary_download(cid: str):
        f = folder(cid)
        comp, result, statuses = preapp_result(f)
        tmp = Path(tempfile.mkdtemp(prefix="st-secretary-")) / SUMMARY
        f.write_summary(result, comp, tmp, statuses)
        return FileResponse(tmp, filename=SUMMARY, media_type=XLSX,
                            background=BackgroundTask(shutil.rmtree, tmp.parent, ignore_errors=True))

    # ------------------------------------------------------------ шаги в разработке

    @app.get("/c/{cid}/step/{slug}")
    def step_page(request: Request, cid: str, slug: str):
        f = folder(cid)
        step = BY_SLUG.get(slug)
        if step is None or step.ready:
            raise HTTPException(404)
        return page(request, "step.html", active=slug, step=step, **comp_ctx(f))

    # ------------------------------------------------------------ ошибки

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


def _preapp_view(files: list[Path], result, reviews: dict) -> dict:
    """Данные для страницы предзаявок: замечания и статусы по файлам (командам), счётчики для фильтров."""
    by_source: dict[str, list[Issue]] = defaultdict(list)
    for i in result.issues:
        by_source[i.source].append(i)
    teams = {t.source: t for t in result.teams}
    groups = []
    for p in files:
        issues = sorted(by_source.get(p.name, []), key=lambda i: (SEVERITY_ORDER[i.severity], i.person))
        worst: dict[str, str] = {}
        for i in issues:
            if i.person and (i.person not in worst or SEVERITY_ORDER[i.severity] < SEVERITY_ORDER[worst[i.person]]):
                worst[i.person] = i.severity
        groups.append({"file": p.name, "team": teams.get(p.name), "issues": issues, "review": reviews[p.name],
                       "counts": Counter(i.severity for i in issues), "worst": worst})
    # фильтр команд: «Ошибки» — есть ошибки или отправлена на исправление, «Проверить» — есть что проверить
    # или ещё не просмотрена, «Проверено» — отмечена секретарём
    by_filter = Counter()
    for g in groups:
        by_filter[g["review"].status] += 1
    return {"groups": groups, "general": by_source.get("", []), "by_filter": by_filter,
            "totals": Counter(i.severity for i in result.issues)}
