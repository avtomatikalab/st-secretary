"""Разрядные нормы (Правки, п. 59, решение 048): таблица редакции, как её читает программа, документ ФСТР — копией в
программе (без интернета) и ссылкой на сайт; своя редакция — копия в Excel → правка → загрузка, хранится на этом
компьютере («Свои нормы» рядом с личными данными судей), в карточке — «своя: …»."""

from __future__ import annotations

from urllib.parse import quote, urlencode

from fastapi import Request
from fastapi.responses import FileResponse, Response
from starlette.exceptions import HTTPException

from st_secretary.importers.norms_xlsx import QUALS, STATUS, read_norms, write_norms
from st_secretary.qualification import Qual
from st_secretary.reference import DOCS, OWN_PREFIX, all_norm_editions, edition_dict, norm_edition, norm_editions
from st_secretary.web.common import XLSX, redirect, with_done
from st_secretary.web.store import OWN_NORMS_COPY, load_json

DOC_TYPES = {".pdf": "application/pdf", ".xls": "application/vnd.ms-excel"}


def norms_url(edition: str, cid: str = "") -> str:
    """Страница норм редакции (из карточки — с возвратом к соревнованию)."""
    q = {"e": edition} | ({"c": cid} if cid else {})
    return "/norms?" + urlencode(q)


def table(n) -> list[dict]:
    """Строки таблицы для страницы: ранг, проценты по разрядам, к каким классам относится строка."""
    out = []
    for r in n.rows:
        cells = [r.thresholds.get(q) for q in (Qual.I, Qual.II, Qual.III, Qual.Y1, Qual.Y2, Qual.Y3)]
        if r.y3_condition and cells[-1] is None:
            cells[-1] = "условие"
        out.append({"label": r.label, "min": r.min_rank, "cells": cells,
                    "classes": [c for c, (lo, hi) in sorted(n.class_rows.items()) if lo <= r.min_rank <= hi]})
    return out


def register(app, cx) -> None:
    store, page = cx.store, cx.page

    def show(request: Request, edition: str, cid: str, errors=(), status_code: int = 200):
        names = all_norm_editions()
        if edition not in names:
            edition = norm_editions()[-1]
        n = norm_edition(edition)
        comp_f = store.get(cid) if cid else None
        # своя редакция: в каких соревнованиях выбрана (по её копии в папке соревнования — карточки не читаем)
        used = [f for f in store.all() if load_json(f.path / OWN_NORMS_COPY).get("name") == edition] if n.own else []
        ctx = cx.comp_ctx(comp_f) | {"active": "card"} if comp_f else {}
        return page(request, "norms.html", status_code=status_code, n=n, d=edition_dict(edition), edition=edition,
                    names=names, own_names=[x for x in names if x.startswith(OWN_PREFIX)], rows=table(n),
                    quals=[q for _, q in QUALS], classes=sorted(n.class_rows), status=STATUS,
                    cid=cid if comp_f else "", comp_folder=comp_f, errors=list(errors), used=used,
                    norms_url=norms_url, **ctx)

    @app.get("/norms")
    def norms_page(request: Request, e: str = "", c: str = ""):
        return show(request, e, c)

    @app.get("/norms/doc/{name}")
    def norms_doc(name: str):
        """Копия документа ФСТР из программы — открывается без интернета (PDF — в браузере, xls — скачать)."""
        known = {norm_edition(x).source_doc for x in norm_editions()} - {""}
        path = DOCS / name
        if name not in known or not path.is_file():
            raise HTTPException(404)
        kind = DOC_TYPES.get("." + name.rsplit(".", 1)[-1].lower(), "application/octet-stream")
        return FileResponse(str(path), media_type=kind, filename=name,
                            content_disposition_type="inline" if kind == "application/pdf" else "attachment")

    @app.get("/norms/xlsx")
    def norms_xlsx(e: str = ""):
        """Копия редакции в Excel — основа своей редакции."""
        if e not in all_norm_editions():
            raise HTTPException(404)
        d = edition_dict(e) | {"name": e}
        name = f"Нормы {e.removeprefix(OWN_PREFIX)} — своя копия.xlsx"
        title = e.removeprefix(OWN_PREFIX) if e.startswith(OWN_PREFIX) else ""
        return Response(write_norms(d, title), media_type=XLSX,
                        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}"})

    @app.post("/norms/upload")
    async def norms_upload(request: Request):
        form = await request.form()
        cid, base_name = str(form.get("c", "")), str(form.get("e", ""))
        up = form.get("file")
        if up is None or not getattr(up, "filename", ""):
            return show(request, base_name, cid, ["Выберите файл своей редакции (Excel)."], 400)
        base = edition_dict(base_name) if base_name in all_norm_editions() else edition_dict(norm_editions()[-1])
        d, errors = read_norms(await up.read(), base)
        if errors:
            return show(request, base_name, cid, errors, 422)
        store.save_own_norms(d)
        return redirect(with_done(norms_url(d["name"], cid), "norms_saved", name=d["name"].removeprefix(OWN_PREFIX)))

    @app.post("/norms/delete")
    async def norms_delete(request: Request):
        form = await request.form()
        name, cid = str(form.get("e", "")), str(form.get("c", ""))
        if not name.startswith(OWN_PREFIX):
            raise HTTPException(400)
        store.delete_own_norms(name)
        return redirect(with_done(norms_url(norm_editions()[-1], cid), "norms_deleted",
                                  name=name.removeprefix(OWN_PREFIX)))
