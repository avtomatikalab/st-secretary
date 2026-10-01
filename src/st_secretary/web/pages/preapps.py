"""Предварительные заявки: загрузка, проверка, статусы и отметки «проверено», карточка команды, заявка
в форме (исправить или заполнить), перезаявки, сводка для СЕКРЕТАРЬ_ST."""

from __future__ import annotations

import shutil
import tempfile
from collections import Counter
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode

from fastapi import Request
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask
from starlette.exceptions import HTTPException

from st_secretary import commission as cm
from st_secretary.importers.card_xlsx import CardError
from st_secretary.importers.preapp_xlsx import read_preapplication
from st_secretary.issues import CHECKED, ERROR, SEVERITY_ORDER, WARNING, Issue
from st_secretary.qualification import Qual
from st_secretary.web import preapp_form as pf
from st_secretary.web.common import XLSX, _base, _parse_dt, _preapp_view, _redirect, _with_done, team_anchor
from st_secretary.web.review import DONE, SAVE, STATUS_LABEL, is_clean, issue_key
from st_secretary.web.store import SUMMARY, CompFolder, safe_name


def register(app, cx) -> None:
    comp_ctx = cx.comp_ctx
    folder = cx.folder
    page = cx.page
    store = cx.store
    def doc_list(*a, **k):  # из pages/admission.py
        return cx.doc_list(*a, **k)

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
        split: list[str] = []
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
                # заявка делегации по своей форме (команда в каждой строке) — по файлу на команду (Правки, п. 37)
                teams = store.split_delegation(f, safe_name(Path(name.replace("\\", "/")).name))
                if teams:
                    split.append(f"{name} → {len(teams)}")
            except ValueError:
                skipped += 1
            except PermissionError:
                locked += 1
        q = urlencode({"done": "uploaded", "added": added, "replaced": replaced, "skipped": skipped, "locked": locked,
                       "split": "; ".join(split)})
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
        active = extra.pop("active", "preapps")  # перезаявка открывается из комиссии — пункт меню тот же
        return page(request, "preapp_edit.html", status_code=status_code, active=active, form=form,
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
        # соседние команды — в том же порядке, что в списке заявок: идти по заявкам подряд, не возвращаясь к списку
        order = [p.name for p in f.preapp_files()]
        names = {t.source: t.team for t in result.teams}
        at = order.index(path.name)
        near = {k: {"file": order[j], "title": names.get(order[j], order[j]), "review": reviews[order[j]]}
                for k, j in (("prev", at - 1), ("next", at + 1)) if 0 <= j < len(order)}
        return page(request, "preapp_team.html", active="preapps", file=path.name, team=team, issues=issues,
                    prev_team=near.get("prev"), next_team=near.get("next"), position=(at + 1, len(order)),
                    review=reviews[path.name], counts=Counter(i.severity for i in issues), worst=worst,
                    show=pf.columns(comp, [vars(e) for e in team.entries] if team else []),
                    here=team_url(f, path.name), docs=doc_list(f, path.name),
                    docs_dir=store.team_docs_dir(f, path.name),
                    check_url=f"{_base(f)}/admission/check?{urlencode({'file': path.name})}", **comp_ctx(f))

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

    def reentry_info(f: CompFolder, name: str) -> dict:
        """Для формы перезаявки: время сейчас, начало соревнований, не поздно ли, не повторная ли (п. 8.5)."""
        data = f.admission()
        start = _parse_dt(cm.settings(data)["start_at"])
        now = datetime.now()
        return {"now": now.strftime("%d.%m.%Y %H:%M"), "start": start.strftime("%d.%m.%Y %H:%M") if start else "",
                "late": bool(start and (start - now).total_seconds() < 3600),
                "earlier": data.get("teams", {}).get(name, {}).get("reentries", [])}

    @app.get("/c/{cid}/preapps/edit")
    def preapp_edit(request: Request, cid: str, file: str = "", reentry: int = 0):
        f = folder(cid)
        comp = need_comp(f)
        path = need_file(f, file)
        result, _ = store.review(f, comp)
        team = next((t for t in result.teams if t.source == path.name), None)
        form = pf.app_to_form(read_preapplication(path, store.forms()), team, comp)
        return render_edit(request, f, comp, form, file=path.name, version=f.file_version(path),
                           issues=[i for i in result.issues if i.source == path.name], team=team,
                           reentry=reentry_info(f, path.name) if reentry else None,
                           **({"active": "admission"} if reentry else {}))

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
        head, rows, errors = pf.form_to_file(form, comp)
        path = f.preapp_path(name) if name else None
        conflict = not errors and path is not None and version and version != f.file_version(path) \
            and not data.get("force")
        save_error = None
        reentry = bool(path and data.get("reentry"))
        if not errors and not conflict:
            try:
                before = []
                if reentry:  # состав до перезаявки — чтобы записать, что изменилось
                    result, _ = store.review(f, comp)
                    before = next((t.entries for t in result.teams if t.source == path.name), [])
                saved = f.save_preapp(name if path else None, head, rows, [q.label for q in Qual], store.forms())
                done = "psaved" if path else "pcreated"
                result, _ = store.review(f, comp)  # проверка заново — уже с сохранённым файлом
                if is_clean([i for i in result.issues if i.source == saved]):
                    f.set_status(saved, DONE, by=SAVE)  # заявку только что смотрели, замечаний нет
                    done += "-done"
                if reentry:
                    rec = log_reentry(f, saved, before, rows)
                    done = "reentry_late" if rec["late"] else "reentry_repeat" if rec["repeat"] else "reentry"
                    return _redirect(f"{_base(f)}/admission?done={done}#{team_anchor(saved)}")
                return _redirect(team_url(f, saved, done=done))
            except PermissionError:
                save_error = ("Файл заявки сейчас открыт в Excel, поэтому сохранить не получилось. Закройте его в "
                              "Excel и нажмите «Сохранить» ещё раз — всё, что вы ввели, осталось на странице.")
        return render_edit(request, f, comp, form, file=name if path else "", version=version, errors=errors,
                           status_code=422 if errors else 409, conflict=conflict, save_error=save_error,
                           reentry=reentry_info(f, name) if reentry else None)

    def log_reentry(f: CompFolder, name: str, before: list, rows: list[dict]) -> dict:
        data = f.admission()
        tm = data.setdefault("teams", {}).setdefault(name, {})
        start = _parse_dt(cm.settings(data)["start_at"])
        rec = cm.reentry_record(before, [{"fio": r["fio"], "group": r["group"], "cls": r["cls"]} for r in rows],
                                datetime.now(), start, tm.get("reentries", []))
        tm.setdefault("reentries", []).append(rec)
        f.save_admission(data)
        return rec

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

    cx.update(back_to=back_to, need_comp=need_comp, need_file=need_file, team_url=team_url)
