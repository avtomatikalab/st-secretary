"""Сверка документов: документы программы и присланные файлы — против карточки и друг друга."""

from __future__ import annotations

import tempfile
from pathlib import Path

from fastapi import Request

from st_secretary import staff as sf
from st_secretary import verify as vf
from st_secretary.issues import ERROR, WARNING
from st_secretary.textclean import alpha_key
from st_secretary.web.store import CompFolder


def register(app, cx) -> None:
    comp_ctx = cx.comp_ctx
    folder = cx.folder
    page = cx.page
    store = cx.store
    def need_comp(*a, **k):  # из pages/preapps.py
        return cx.need_comp(*a, **k)

    # ------------------------------------------------------------ сверка документов

    def program_docs(f: CompFolder) -> list[tuple[str, Path]]:
        """Документы, которые сохранила программа (их могли поправить в Word и Excel): по итогам и договоры."""
        out = []
        for label, d in (("Документы по итогам", f.out_dir), ("Договоры и табель", store.contracts_dir(f))):
            if d.is_dir():
                out += [(f"{label}\\{p.name}", p) for p in sorted(d.iterdir(), key=lambda p: alpha_key(p.name))
                        if p.is_file() and p.suffix.lower() in vf.READABLE and not p.name.startswith("~$")]
        return out

    def known_people(f: CompFolder, comp) -> list:
        people = [vf.Known(o.fio, o.category, "в карточке") for o in comp.officials if o.fio]
        people += [vf.Known(p.fio, p.category if p.category != "б/к" else "", "в табеле программы")
                   for p in sf.people(comp, f.contracts()) if not p.from_card]
        if f.preapp_files():
            people += [vf.Known(e.name.full, "", "в заявке") for e in store.preapps(f, comp).entries]
        return people

    def verify_page(request: Request, f: CompFolder, comp, extra: list | None = None):
        docs = [vf.read_doc(p, name) for name, p in program_docs(f)] + (extra or [])
        report = vf.verify(docs, comp, known_people(f, comp))
        issues = report.all_issues
        return page(request, "verify.html", active="verify", report=report, checked=len(docs),
                    uploaded=[d.name for d in (extra or [])],
                    errors=sum(i.severity == ERROR for i in issues), warnings=sum(i.severity == WARNING for i in issues),
                    unread=[d for d, _ in report.files if d.error], **comp_ctx(f))

    @app.get("/c/{cid}/verify")
    def verify_get(request: Request, cid: str):
        f = folder(cid)
        return verify_page(request, f, need_comp(f))

    @app.post("/c/{cid}/verify")
    async def verify_upload(request: Request, cid: str):
        """Проверить присланные файлы вместе с документами программы. Файлы не сохраняются."""
        f = folder(cid)
        comp = need_comp(f)
        form = await request.form()
        extra = []
        with tempfile.TemporaryDirectory() as td:
            for up in form.getlist("files"):
                name = Path(getattr(up, "filename", "") or "").name
                if not name:
                    continue
                p = Path(td) / f"{len(extra)}{Path(name).suffix}"
                p.write_bytes(await up.read())
                extra.append(vf.read_doc(p, name))
        resp = verify_page(request, f, comp, extra)
        resp.headers["Cache-Control"] = "no-store"
        return resp
