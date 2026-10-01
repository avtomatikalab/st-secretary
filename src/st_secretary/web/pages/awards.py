"""Награждение и документы по итогам: места по зачётам (из программы, СЕКРЕТАРЬ_ST или вручную),
оценки судей, тексты отчёта, дипломы, наклейки, справки, выписки, отчёт главного судьи."""

from __future__ import annotations

import re
import shutil
import tempfile
from pathlib import Path

from fastapi import Request
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask
from starlette.exceptions import HTTPException

from st_secretary import commission as cm
from st_secretary import results as res
from st_secretary.exporters import awards as aw
from st_secretary.exporters import final as fin
from st_secretary.exporters import judges as jd
from st_secretary.web.common import DOCX, GRADES, XLSX, base_url, redirect
from st_secretary.web.store import CompFolder


def register(app, cx) -> None:
    comp_ctx = cx.comp_ctx
    folder = cx.folder
    page = cx.page
    store = cx.store
    def commission(*a, **k):  # из pages/admission.py
        return cx.commission(*a, **k)
    def need_comp(*a, **k):  # из pages/preapps.py
        return cx.need_comp(*a, **k)

    # ------------------------------------------------------------ награждение и документы по итогам

    def awards_ctx(f: CompFolder, comp):
        preapps = store.preapps(f, comp) if f.preapp_files() else None
        data = f.results_data()
        return data, res.load(data, comp, preapps), preapps

    def zachet_teams(preapps, key: str) -> list[dict]:
        """Команды зачёта по заявкам (кто в нём заявлен) — для ввода мест вручную."""
        out = []
        for t in (preapps.teams if preapps else []):
            members = [e for e in t.entries if e.zachet and e.zachet.key == key]
            if members:
                out.append({"team": t.team, "territory": t.territory,
                            "members": [{"fio": e.name.full, "qual": e.qual.label if e.qual is not None else ""}
                                        for e in members]})
        return out

    @app.get("/c/{cid}/awards")
    def awards_page(request: Request, cid: str):
        f = folder(cid)
        comp = need_comp(f)
        data, zres, preapps = awards_ctx(f, comp)
        by_key = {z.key: z for z in zres}
        blocks = []
        for z in comp.zachety:
            stored = data.get("zachety", {}).get(z.key, {})
            manual = {r["team"]: r for r in stored.get("rows", [])} if stored.get("source") == res.MANUAL else {}
            teams = [{**t, **{k: manual.get(t["team"], {}).get(k) for k in ("place", "result", "norm")}}
                     for t in zachet_teams(preapps, z.key)]
            blocks.append({"z": z, "res": by_key.get(z.key), "teams": teams})
        grades = data.get("judges", {})
        judges = [{"o": o, "grade": grades.get(res.person_key(o.fio), "")} for o in comp.officials if o.fio]
        report = {**{k: "" for k in fin.REPORT_DEFAULTS}, **data.get("report", {})}
        return page(request, "awards.html", active="awards", blocks=blocks, docs=AWARD_DOCS, judges=judges,
                    grades=GRADES, report=report, report_defaults=fin.REPORT_DEFAULTS,
                    extracts=fin.extract_count(zres),
                    has_results=any(b["res"] for b in blocks), medals=aw.medal_count(zres),
                    norms=["", "3ю", "2ю", "1ю", "III", "II", "I", "КМС"], out_dir=f.out_dir, **comp_ctx(f))

    @app.post("/c/{cid}/awards/import")
    async def awards_import(request: Request, cid: str):
        f = folder(cid)
        comp = need_comp(f)
        form = await request.form()
        key = str(form.get("zachet", ""))
        up = form.get("protocol")
        if key not in {z.key for z in comp.zachety} or up is None or not getattr(up, "filename", ""):
            return redirect(f"{base_url(f)}/awards?done=res_none")
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / Path(up.filename).name
            p.write_bytes(await up.read())
            try:
                from st_secretary.importers.sekretar_xls import read_result_protocol
                proto = read_result_protocol(p)
            except ImportError:
                return redirect(f"{base_url(f)}/awards?done=res_noxlrd")
            except Exception:  # noqa: BLE001 — не протокол или не .xls: объяснить, а не упасть
                return redirect(f"{base_url(f)}/awards?done=res_bad")
        if not proto.rows:
            return redirect(f"{base_url(f)}/awards?done=res_bad")
        data = f.results_data()
        data.setdefault("zachety", {})[key] = res.from_protocol(proto)
        f.save_results_data(data)
        return redirect(f"{base_url(f)}/awards?done=res_imported")

    @app.post("/c/{cid}/awards/manual")
    async def awards_manual(request: Request, cid: str):
        f = folder(cid)
        comp = need_comp(f)
        form = await request.form()
        key = str(form.get("zachet", ""))
        if key not in {z.key for z in comp.zachety}:
            raise HTTPException(404)
        _, _, preapps = awards_ctx(f, comp)
        teams = {t["team"]: t for t in zachet_teams(preapps, key)}
        rows = []
        idx = sorted({int(m.group(1)) for k in form if (m := re.fullmatch(r"r-(\d+)-team", k))})
        for i in idx:
            t = teams.get(str(form.get(f"r-{i}-team")))
            if t is None:
                continue
            place = str(form.get(f"r-{i}-place", "")).strip()
            rows.append({**t, "number": "", "place": int(place) if place.isdigit() and int(place) > 0 else None,
                         "result": str(form.get(f"r-{i}-result", "")).strip(),
                         "norm": str(form.get(f"r-{i}-norm", "")).strip()})
        data = f.results_data()
        data.setdefault("zachety", {})[key] = {"source": res.MANUAL, "group_text": "", "rank": "", "rows": rows}
        f.save_results_data(data)
        return redirect(f"{base_url(f)}/awards?done=res_saved")

    @app.post("/c/{cid}/awards/clear")
    async def awards_clear(request: Request, cid: str):
        f = folder(cid)
        key = str((await request.form()).get("zachet", ""))
        data = f.results_data()
        data.get("zachety", {}).pop(key, None)
        f.save_results_data(data)
        return redirect(f"{base_url(f)}/awards?done=res_cleared")

    def build_award_doc(f: CompFolder, kind: str, path: Path | None = None) -> Path:
        comp = need_comp(f)
        if kind not in AWARD_DOCS:
            raise HTTPException(404)
        name, _, builder = AWARD_DOCS[kind]
        if path is None:
            f.out_dir.mkdir(exist_ok=True)
            path = f.out_dir / name
        return builder(f, comp, path)

    @app.post("/c/{cid}/awards/doc/{kind}")
    def awards_doc_open(cid: str, kind: str):
        f = folder(cid)
        try:
            path = build_award_doc(f, kind)
        except PermissionError:
            return redirect(f"{base_url(f)}/awards?done=doc_locked")
        app.state.opener(path)
        return redirect(f"{base_url(f)}/awards?done=doc_ready")

    @app.get("/c/{cid}/awards/file/{kind}")
    def awards_doc_download(cid: str, kind: str):
        f = folder(cid)
        if kind not in AWARD_DOCS:
            raise HTTPException(404)
        tmp = Path(tempfile.mkdtemp(prefix="st-secretary-")) / AWARD_DOCS[kind][0]
        build_award_doc(f, kind, tmp)
        media = XLSX if tmp.suffix == ".xlsx" else DOCX
        return FileResponse(tmp, filename=tmp.name, media_type=media,
                            background=BackgroundTask(shutil.rmtree, tmp.parent, ignore_errors=True))

    def _results(f: CompFolder, comp):
        return awards_ctx(f, comp)[1]

    # документы по итогам: вид → (имя файла, что это, как собрать) — у каждого экземпляра программы свои
    AWARD_DOCS = {
        "diplomas": ("Дипломы.docx", "Тексты дипломов за I–III места — по странице на диплом, под печать на бланках. "
                                     "У группы каждому участнику — свой диплом, его имя первое.",
                     lambda f, comp, p: aw.write_diplomas(_results(f, comp), comp, p)),
        "stickers": ("Наклейки на медали.xlsx", "По наклейке на каждого призёра и две запасные; две колонки — "
                                                "напечатать и вырезать.",
                     lambda f, comp, p: aw.write_stickers(_results(f, comp), comp, p)),
        "awardees": ("Список награждаемых.docx", "Для ведущего награждения: по зачётам, в порядке вызова "
                                                 "III → II → I место.",
                     lambda f, comp, p: aw.write_awardees(_results(f, comp), comp, p)),
        "judging": ("Справки о судействе.docx", "На каждого судью из карточки (лист «ГСК») — по две на листе, "
                                                "с оценкой судейства из таблицы выше.",
                    lambda f, comp, p: jd.write_judging_certificates(comp, f.results_data().get("judges", {}), p)),
        "sk": ("Справка о составе СК.docx", "Состав и квалификация судейской коллегии (ЕВСК, п. 67.9) — по карточке.",
               lambda f, comp, p: jd.write_sk_certificate(comp, p)),
        "subjects": ("Справка о количестве субъектов.docx", "Для всероссийских и межрегиональных (ЕВСК, п. 67.14): "
                                                            "территории допущенных команд.",
                     lambda f, comp, p: jd.write_subjects_certificate(comp, admitted_territories(f, comp), p)),
        "extracts": ("Выписки на разряды.xlsx", "Выписки из протокола для присвоения разрядов (ЕВСК, п. 67.8.2): "
                                                "только выполнившие норматив, с датой рождения из заявки.",
                     lambda f, comp, p: fin.write_extracts(_results(f, comp), comp, p)),
        "report": ("Отчёт главного судьи.docx", "Состав участников, результаты, жалобы, материальная база, "
                                                "судейская коллегия с оценками — по отчёту 2025 г.",
                   lambda f, comp, p: write_report(f, comp, p)),
    }

    def admitted_people(f: CompFolder, comp) -> tuple[list, list[str]]:
        """Участники и территории команд, допущенных комиссией; если комиссия не велась — все заявившиеся."""
        if not f.preapp_files():
            return [], []
        _, teams = commission(f, comp)
        ok = [t for t in teams if t.status == cm.ADMITTED and t.team] or [t for t in teams if t.team]
        people = [p.entry for t in ok for p in t.persons if p.status != cm.REJECTED]
        return people, [t.team.territory for t in ok]

    def write_report(f: CompFolder, comp, path: Path) -> Path:
        data = f.results_data()
        people, terr = admitted_people(f, comp)
        return fin.write_report(comp, _results(f, comp), people, terr, data.get("judges", {}),
                                data.get("report", {}), path)

    @app.post("/c/{cid}/awards/report")
    async def awards_report_texts(request: Request, cid: str):
        f = folder(cid)
        form = await request.form()
        data = f.results_data()
        data["report"] = {k: str(form.get(k, "")).strip() for k in fin.REPORT_DEFAULTS}
        f.save_results_data(data)
        return redirect(f"{base_url(f)}/awards?done=report_saved#report")

    def admitted_territories(f: CompFolder, comp) -> list[str]:
        """Территории команд, допущенных комиссией; если комиссия не велась — всех заявившихся."""
        if not f.preapp_files():
            return []
        _, teams = commission(f, comp)
        ok = [t for t in teams if t.status == cm.ADMITTED and t.team]
        return [t.team.territory for t in (ok or [t for t in teams if t.team])]

    @app.post("/c/{cid}/awards/grades")
    async def awards_grades(request: Request, cid: str):
        f = folder(cid)
        comp = need_comp(f)
        form = await request.form()
        data = f.results_data()
        grades = data.setdefault("judges", {})
        for i, o in enumerate(o for o in comp.officials if o.fio):
            g = str(form.get(f"g-{i}", ""))
            if g in GRADES:
                grades[res.person_key(o.fio)] = g
        f.save_results_data(data)
        return redirect(f"{base_url(f)}/awards?done=grades_saved#judges")
