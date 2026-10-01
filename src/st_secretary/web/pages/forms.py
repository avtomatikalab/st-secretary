"""Свои формы предзаявок (Правки, п. 37): загрузить образец, проверить, что программа угадала (колонки, шапка,
значения, листы), посмотреть, как прочитается, сохранить; выгрузить и загрузить форму файлом."""

from __future__ import annotations

import json
import re
import secrets
import time
from pathlib import Path
from urllib.parse import quote, urlencode

from fastapi import Request
from fastapi.responses import Response
from starlette.exceptions import HTTPException

from st_secretary import forms as fm
from st_secretary.importers.preapp_xlsx import _grids
from st_secretary.preapp import process
from st_secretary.textclean import clean_spaces
from st_secretary.web.common import _base, _redirect
from st_secretary.web.store import NAMED_TEMPLATE, CompFolder

SAMPLE_TYPES = (".xlsx", ".xls")
PREVIEW_ROWS, PREVIEW_COLS = 14, 24


def register(app, cx) -> None:
    folder = cx.folder
    page = cx.page
    store = cx.store
    comp_ctx = cx.comp_ctx

    def need_comp(*a, **k):  # из pages/preapps.py
        return cx.need_comp(*a, **k)

    def sample_path(token: str) -> Path | None:
        if not re.fullmatch(r"[0-9a-f]{16}", token or ""):
            return None
        return next((p for p in (store.samples_dir / f"{token}{x}" for x in SAMPLE_TYPES) if p.is_file()), None)

    def clean_samples() -> None:
        """Образцы старше суток — удалить (в них могут быть ФИО и даты рождения)."""
        d = store.samples_dir
        for p in (d.iterdir() if d.is_dir() else []):
            if p.is_file() and time.time() - p.stat().st_mtime > 86400:
                p.unlink(missing_ok=True)

    @app.get("/c/{cid}/forms")
    def forms_page(request: Request, cid: str):
        f = folder(cid)
        clean_samples()
        return page(request, "forms.html", active="preapps", forms=store.forms(), labels=fm.FIELD_LABEL,
                    forms_path=store.forms_path, named=(f.path / NAMED_TEMPLATE).is_file(),
                    **comp_ctx(f))

    @app.post("/c/{cid}/forms/sample")
    async def forms_sample(request: Request, cid: str):
        f = folder(cid)
        form = await request.form()
        up = form.get("sample")
        name = getattr(up, "filename", "") or ""
        if not name or Path(name).suffix.lower() not in SAMPLE_TYPES:
            return _redirect(f"{_base(f)}/forms?done=form_bad_sample")
        token = secrets.token_hex(8)
        store.samples_dir.mkdir(parents=True, exist_ok=True)
        (store.samples_dir / f"{token}{Path(name).suffix.lower()}").write_bytes(await up.read())
        q = {"sample": token, "file": Path(name).name}
        if saved_form(str(form.get("name", ""))):  # «Изменить сохранённую» — образец открывается в её настройке
            q["name"] = str(form.get("name"))
        return _redirect(f"{_base(f)}/forms/edit?{urlencode(q)}")

    def read_grids(token: str):
        p = sample_path(token)
        if p is None:
            return None
        try:
            return _grids(p)
        except Exception:  # noqa: BLE001 — образец не открывается
            return None

    def initial(grids, saved: dict | None) -> dict:
        """Что программа угадала по образцу (или как в сохранённой форме)."""
        state = {"sheet": 0, "row": 0, "columns": [], "team_in": "head", "head": {}, "sheets": [], "values": {},
                 "name": saved.get("name", "") if saved else ""}
        if grids:
            exact = next(((i, r) for i, (_, grid) in enumerate(grids) for r, row in enumerate(grid[:30])
                          if fm.signature(row) == saved["signature"]), None) if saved else None
            # шапка образца похожа на форму, но не та же (п. 41) — строка, больше всего похожая на шапку формы
            similar = fm.similar_header(grids, saved) if saved and not exact else None
            found = [(i, r) for i, (_, grid) in enumerate(grids) if (r := fm.find_header(grid)) is not None]
            if at := exact or similar or (found[0] if found else None):
                state["sheet"], state["row"] = at
            grid = grids[state["sheet"]][1]
            headers = list(grid[state["row"]]) if state["row"] < len(grid) else []
            if saved:  # колонки формы — к шапке образца (у похожей шапки новые колонки угадываются)
                state["columns"] = list(saved["columns"]) if exact else fm.adapt_columns(saved, headers)
            else:
                state["columns"] = fm.guess_columns(headers)
            state["head"] = dict(saved.get("head", {})) if saved else fm.guess_head(grid, state["row"])
        elif saved:
            state["columns"] = list(saved["columns"])
            state["head"] = dict(saved.get("head", {}))
        if saved:
            state.update(team_in=saved.get("team_in", "head"), sheets=list(saved.get("sheets", [])),
                         values={k: dict(v) for k, v in saved.get("values", {}).items()})
        else:
            state["team_in"] = "row" if "team" in state["columns"] else "head"
        return state

    def build(grids, state: dict, saved: dict | None) -> dict:
        """Форма из состояния редактора."""
        if grids:
            grid = grids[state["sheet"]][1]
            headers = [clean_spaces(v) for v in (grid[state["row"]] if state["row"] < len(grid) else [])]
        else:
            headers = list(saved.get("headers", [])) if saved else []
        sig = fm.signature(headers)
        headers = headers[:len(sig)]
        cols = (state["columns"] + [fm.SKIP] * len(sig))[:len(sig)]
        return {"name": state["name"], "headers": headers, "signature": sig, "columns": cols,
                "team_in": state["team_in"], "head": {k: v for k, v in state["head"].items() if v},
                "sheets": state["sheets"], "values": {k: v for k, v in state["values"].items() if v}}

    def from_post(data, grids, saved: dict | None) -> dict:
        sheet =int(data.get("sheet", 0)) if str(data.get("sheet", "")).isdigit() else 0
        row = int(data.get("row", 1)) - 1 if str(data.get("row", "")).isdigit() else 0
        moved = (sheet, row) != (int(data.get("sheet_was", -1) or -1), int(data.get("row_was", 0) or 0) - 1)
        state = {"sheet": sheet if grids and sheet < len(grids) else 0, "row": max(row, 0),
                 "name": " ".join(str(data.get("name", "")).split())[:120],
                 "team_in": "row" if data.get("team_in") == "row" else "head",
                 "head": {k: str(data.get(f"head-{k}", "")).strip().upper() for k, _ in fm.HEAD_FIELDS},
                 "sheets": [str(x) for x in data.getlist("sheets")] if grids else (saved or {}).get("sheets", []),
                 "values": {}}
        if grids and moved:  # другая строка заголовков — колонки и шапку угадать заново
            grid = grids[state["sheet"]][1]
            state["columns"] = fm.guess_columns(list(grid[state["row"]]) if state["row"] < len(grid) else [])
            state["head"] = fm.guess_head(grid, state["row"])
        else:
            idx = sorted(int(k[4:]) for k in data if re.fullmatch(r"col-\d+", k))
            known = {k for k, _, _ in fm.FIELDS} | {fm.OWN, fm.SKIP}
            state["columns"] = [str(data.get(f"col-{i}")) if str(data.get(f"col-{i}")) in known else fm.SKIP
                                for i in idx]
        for k in data:
            m = re.fullmatch(r"val-(\w+)-(\d+)-raw", k)
            if m and m.group(1) in fm.VALUE_FIELDS:
                raw = str(data.get(k))
                to = str(data.get(f"val-{m.group(1)}-{m.group(2)}-to", "")).strip()
                if raw and to != raw:
                    state["values"].setdefault(m.group(1), {})[fm.norm(raw)] = to
        all_same = [n for n, _, _ in fm._sheet_rows(grids, {"signature": build(grids, state, saved)["signature"]})] \
            if grids else []
        if grids and set(state["sheets"]) == set(all_same):
            state["sheets"] = []  # все листы с такой шапкой — и те, что появятся
        return state

    def render(request: Request, f: CompFolder, token: str, file: str, grids, state: dict, saved: dict | None,
               errors: list[str] | None = None, show_preview: bool = True, status_code: int = 200):
        form = build(grids, state, saved)
        values_rows = {}
        if grids:
            found = fm.sample_values(grids, form)
            for fld in fm.VALUE_FIELDS:
                if fld in form["columns"]:
                    have = form["values"].get(fld, {})
                    values_rows[fld] = [(v, have.get(fm.norm(v), fm.suggest_value(fld, v))) for v in found.get(fld, [])]
        elif saved:
            values_rows = {fld: list(v.items()) for fld, v in saved.get("values", {}).items()}
        preview, issues, parts = [], [], 1
        if grids and show_preview and form["signature"]:
            comp = need_comp(f)
            raw = fm.read_with_form(sample_path(token), grids, form)
            apps = fm.split_by_team(raw) if raw.team_in_row else [raw]
            parts = len(apps)
            result = process(apps, comp)
            preview = [e for t in result.teams for e in t.entries][:10]
            issues = result.issues
        same = [n for n, _, _ in fm._sheet_rows(grids, {"signature": form["signature"]})] if grids else []
        sheets = [{"name": n, "rows": [[clean_spaces(v) for v in row[:PREVIEW_COLS]] for row in g[:PREVIEW_ROWS]],
                   "same": n in same} for n, g in (grids or [])]
        # образец новой формы, а такая (или похожая) уже сохранена — предложить открыть её (п. 41)
        known = fm.match(store.forms(), grids) if grids and saved is None else None
        return page(request, "form_edit.html", status_code=status_code, active="preapps", token=token, file=file,
                    state=state, form=form, saved=saved, sheets=sheets, fields=fm.FIELDS, labels=fm.FIELD_LABEL,
                    known=known,
                    own=fm.OWN, skip=fm.SKIP, head_fields=fm.HEAD_FIELDS, values_rows=values_rows,
                    missing=fm.check(form), preview=preview, issues=issues, parts=parts, errors=errors or [],
                    cell_ref=fm.cell_ref, **comp_ctx(f))

    def saved_form(name: str) -> dict | None:
        return next((x for x in store.forms() if x["name"] == name), None) if name else None

    @app.get("/c/{cid}/forms/edit")
    def forms_edit(request: Request, cid: str, sample: str = "", name: str = "", file: str = ""):
        f = folder(cid)
        saved = saved_form(name)
        grids = read_grids(sample) if sample else None
        if sample and grids is None:
            return _redirect(f"{_base(f)}/forms?done=form_bad_sample")
        if not grids and saved is None:
            raise HTTPException(404)
        return render(request, f, sample, file, grids, initial(grids, saved), saved)

    @app.post("/c/{cid}/forms/edit")
    async def forms_save(request: Request, cid: str):
        f = folder(cid)
        data = await request.form()
        token, file = str(data.get("sample", "")), str(data.get("file", ""))
        saved = saved_form(str(data.get("old_name", "")))
        grids = read_grids(token) if token else None
        if token and grids is None:
            return _redirect(f"{_base(f)}/forms?done=form_bad_sample")
        state = from_post(data, grids, saved)
        if data.get("action") != "save":
            return render(request, f, token, file, grids, state, saved)
        form = build(grids, state, saved)
        errors = []
        if not form["name"]:
            errors.append("впишите название формы — по нему её узнают в списке")
        if not form["signature"]:
            errors.append("не выбрана строка заголовков таблицы участников")
        errors += [f"не указано: {x}" for x in fm.check(form)]
        if errors:
            return render(request, f, token, file, grids, state, saved, errors, status_code=422)
        store.save_form(form, saved["name"] if saved else "")
        if (p := sample_path(token)) is not None:
            p.unlink(missing_ok=True)  # образец больше не нужен
        return _redirect(f"{_base(f)}/forms?{urlencode({'done': 'form_saved', 'name': form['name']})}")

    @app.get("/c/{cid}/forms/from-preapp")
    def forms_from_preapp(cid: str, file: str = ""):
        """«Исправить» у заявки, похожей на свою форму (п. 41): заявка — образцом, открыть похожую форму по ней."""
        f = folder(cid)
        path = f.preapp_path(file)
        if path is None or path.suffix.lower() not in SAMPLE_TYPES:
            raise HTTPException(404)
        token = secrets.token_hex(8)
        store.samples_dir.mkdir(parents=True, exist_ok=True)
        (store.samples_dir / f"{token}{path.suffix.lower()}").write_bytes(path.read_bytes())
        q = {"sample": token, "file": path.name}
        grids = read_grids(token)
        if grids and (m := fm.match(store.forms(), grids)):
            q["name"] = m[0]["name"]
        return _redirect(f"{_base(f)}/forms/edit?{urlencode(q)}")

    @app.post("/c/{cid}/forms/delete")
    async def forms_delete(request: Request, cid: str):
        f = folder(cid)
        store.delete_form(str((await request.form()).get("name", "")))
        return _redirect(f"{_base(f)}/forms?done=form_deleted")

    @app.get("/c/{cid}/forms/file")
    def forms_file(cid: str, name: str = ""):
        """Форма файлом — передать другому секретарю."""
        folder(cid)
        form = saved_form(name)
        if form is None:
            raise HTTPException(404)
        body = json.dumps({"st_secretary_form": 1, **form}, ensure_ascii=False, indent=1).encode("utf-8")
        fname = f"Форма заявки — {name}.json"
        return Response(body, media_type="application/json",
                        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(fname)}"})

    @app.post("/c/{cid}/forms/import")
    async def forms_import(request: Request, cid: str):
        f = folder(cid)
        up = (await request.form()).get("form")
        try:
            data = json.loads((await up.read()).decode("utf-8")) if up is not None else None
        except (ValueError, UnicodeDecodeError):
            data = None
        known = {k for k, _, _ in fm.FIELDS} | {fm.OWN, fm.SKIP}
        ok = isinstance(data, dict) and data.get("name") and isinstance(data.get("signature"), list) \
            and isinstance(data.get("columns"), list) and all(c in known for c in data["columns"])
        if not ok:
            return _redirect(f"{_base(f)}/forms?done=form_bad_file")
        form = {k: data[k] for k in ("name", "headers", "signature", "columns", "team_in", "head", "sheets", "values")
                if k in data}
        form["name"] = " ".join(str(form["name"]).split())[:120]
        store.save_form(form)
        return _redirect(f"{_base(f)}/forms?{urlencode({'done': 'form_imported', 'name': form['name']})}")
