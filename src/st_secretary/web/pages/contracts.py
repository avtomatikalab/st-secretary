"""Договоры, акты, табель судей и персонала: дни, ставки, заказчик, личные данные (только на этом
компьютере), договоры и акты из шаблона Word."""

from __future__ import annotations

import re
import shutil
import tempfile
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode

from fastapi import Request
from fastapi.responses import FileResponse, JSONResponse
from starlette.background import BackgroundTask
from starlette.exceptions import HTTPException

from st_secretary import festival as fv
from st_secretary import results as res
from st_secretary import staff as sf
from st_secretary.exporters import contracts as ct
from st_secretary.money import money
from st_secretary.names import initials
from st_secretary.web.common import DOCX, XLSX, base_url, redirect, with_done
from st_secretary.web.store import CompFolder, safe_name


def register(app, cx) -> None:
    comp_ctx = cx.comp_ctx
    folder = cx.folder
    page = cx.page
    store = cx.store
    templates = cx.templates
    def need_comp(*a, **k):  # из pages/preapps.py
        return cx.need_comp(*a, **k)

    # ------------------------------------------------------------ договоры, акты, табель

    def staff_comp(f: CompFolder):
        """Чьи договоры и табель: соревнования — или, если на фестивале «один договор и табель на весь фестиваль»,
        всего фестиваля (даты от первого до последнего дня, ГСК всех его соревнований)."""
        comp = need_comp(f)
        fest = f.festival()
        if fv.mode(fest, "contracts") != "festival":
            return comp
        comps = []
        for m in fest["members"]:
            other = store.get(m) if m != f.id else f
            try:
                if other:
                    comps.append(comp if other is f else other.load())
            except Exception:  # noqa: BLE001 — карточка не читается: её дней и ГСК в общем табеле нет
                pass
        return fv.joint_comp(fest, comps or [comp])

    def template_for(f: CompFolder, role: str) -> Path | None:
        """Свой шаблон договора для должности или общий: в папке соревнования, затем в папке «данные»;
        None — встроенный."""
        return ct.find_template([f.path, store.root], role)

    def staff_ctx(f: CompFolder, comp) -> dict:
        data = f.contracts()
        s = sf.settings(comp, data)
        team = sf.people(comp, data)
        personal = store.personal()
        customer = data.get("customer", {})
        issues = sf.check(team, personal, customer)
        used: dict[Path, list[str]] = {}  # свой шаблон → для кого
        for p in team:
            if p.paid and (tpl := template_for(f, p.role)):
                used.setdefault(tpl, []).append(p.role)
        templates_info = []
        for tpl, roles in used.items():
            try:
                unknown = sorted(ct.template_fields(tpl) - {k for k, _ in ct.FIELDS})
            except Exception:  # noqa: BLE001 — повреждённый файл шаблона: сказать, а не упасть
                unknown = ["(файл шаблона не открывается)"]
            templates_info.append({"path": tpl, "roles": sorted(set(roles)), "unknown": unknown,
                                   "own": tpl.parent == f.path})
        return {"data": data, "s": s, "team": team, "personal": personal, "customer": customer, "issues": issues,
                "totals": sf.totals(team, s["accrual"]), "templates": templates_info,
                "per_person": {p.key: [i for i in issues if i.person == p.fio] for p in team}}

    def staff_parts(f: CompFolder, ctx: dict) -> dict:
        return {"base": base_url(f), "weekday": ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"], "money": money,
                "missing_personal": sf.missing_personal, "personal_problems": sf.personal_problems, **ctx}

    @app.get("/c/{cid}/contracts")
    def contracts_page(request: Request, cid: str):
        f = folder(cid)
        comp = staff_comp(f)
        ctx = staff_ctx(f, comp)
        prefill = {}
        if not ctx["customer"]:  # заказчик обычно тот же, что в прошлый раз
            for other in store.all():
                if other.id != f.id and (c := other.contracts().get("customer")):
                    prefill = {"from": other.id, **c}
                    break
        return page(request, "contracts.html", active="contracts", pairs=sf.rate_pairs(ctx["team"]),
                    sample=sf.RATES_KRSK_2025, customer_fields=ct.CUSTOMER_FIELDS, fields=ct.FIELDS,
                    prefill=prefill, extra_roles=sf.EXTRA_ROLES, categories=sf.CATEGORIES,
                    docs_dir=store.contracts_dir(f), personal_path=store.personal_path,
                    joint=comp.title if fv.mode(f.festival(), "contracts") == "festival" else "",
                    **{**comp_ctx(f), **staff_parts(f, ctx)})

    @app.post("/c/{cid}/contracts/days")
    async def contracts_days(request: Request, cid: str):
        """Табель: отметки дней и «без оплаты». С автосохранением — возвращает обновлённый табель."""
        f = folder(cid)
        comp = staff_comp(f)
        form = await request.form()
        data = f.contracts()
        s = sf.settings(comp, data)
        marks = data.setdefault("people", {})
        idx = sorted({int(m.group(1)) for k in form if (m := re.fullmatch(r"p-(\d+)-key", k))})
        for i in idx:
            key = str(form.get(f"p-{i}-key", ""))
            m = marks.setdefault(key, {})
            outside = [d for d in m.get("days", []) if sf._day(d) not in s["days"]]  # вне периода — не терять
            m["days"] = outside + [d.isoformat() for d in s["days"] if form.get(f"p-{i}-d-{d:%Y%m%d}")]
            m["unpaid"] = bool(form.get(f"p-{i}-unpaid"))
        f.save_contracts(data)
        if request.headers.get("x-autosave"):
            parts = {"comp": comp, **staff_parts(f, staff_ctx(f, comp))}
            return JSONResponse({"team": templates.get_template("_contracts_tabel.html").render(**parts),
                                 "tiles": templates.get_template("_contracts_tiles.html").render(**parts),
                                 "saved": datetime.now().strftime("%H:%M:%S")})
        return redirect(f"{base_url(f)}/contracts?done=ct_saved#tabel")

    @app.post("/c/{cid}/contracts/settings")
    async def contracts_settings(request: Request, cid: str):
        f = folder(cid)
        comp = staff_comp(f)
        form = await request.form()
        data = f.contracts()
        if form.get("do") == "sample":  # ставки по образцу — только для пустых
            rates = data.setdefault("rates", {})
            for key, _, _ in sf.rate_pairs(sf.people(comp, data)):
                if key in sf.RATES_KRSK_2025 and not rates.get(key):
                    rates[key] = sf.RATES_KRSK_2025[key]
        else:
            a, b = sf._day(form.get("from", "")), sf._day(form.get("to", ""))
            if a and b:
                data["period"] = {"from": min(a, b).isoformat(), "to": max(a, b).isoformat()}
            acc = str(form.get("accrual", "")).strip().replace(",", ".")
            try:
                data["accrual"] = float(acc) if acc else sf.ACCRUAL
            except ValueError:
                pass
            rates = {}
            for k in form:
                if k.startswith("rate-"):
                    v = re.sub(r"[^\d]", "", str(form.get(k, "")).split(",")[0].split(".")[0])
                    if v:
                        rates[k[5:]] = int(v)
            data["rates"] = {**data.get("rates", {}), **rates}
            for k in [k[5:] for k in form if k.startswith("rate-") and not str(form.get(k, "")).strip()]:
                data["rates"].pop(k, None)
        f.save_contracts(data)
        return redirect(f"{base_url(f)}/contracts?done=ct_settings#settings")

    @app.post("/c/{cid}/contracts/customer")
    async def contracts_customer(request: Request, cid: str):
        f = folder(cid)
        form = await request.form()
        data = f.contracts()
        data["customer"] = {k: str(form.get(k, "")).strip() for k, _, _ in ct.CUSTOMER_FIELDS}
        f.save_contracts(data)
        return redirect(f"{base_url(f)}/contracts?done=ct_customer#customer")

    @app.post("/c/{cid}/contracts/add")
    async def contracts_add(request: Request, cid: str):
        f = folder(cid)
        comp = staff_comp(f)
        form = await request.form()
        fio = " ".join(str(form.get("fio", "")).split())
        role = " ".join(str(form.get("role", "")).split())
        cat = str(form.get("category", "б/к"))
        if not fio or not role:
            return redirect(f"{base_url(f)}/contracts?done=ct_need#add")
        data = f.contracts()
        if res.person_key(fio) in {p.key for p in sf.people(comp, data)}:
            return redirect(f"{base_url(f)}/contracts?done=ct_exists#add")
        data.setdefault("extra", []).append({"fio": fio, "role": role,
                                             "category": cat if cat in sf.CATEGORIES else "б/к"})
        f.save_contracts(data)
        return redirect(f"{base_url(f)}/contracts?done=ct_added#tabel")

    def need_person(f: CompFolder, comp, key: str):
        p = next((x for x in sf.people(comp, f.contracts()) if x.key == key), None)
        if p is None:
            raise HTTPException(404)
        return p

    @app.get("/c/{cid}/contracts/person")
    def contracts_person(request: Request, cid: str, key: str = ""):
        """Личные данные для договора — только на этом компьютере; страницу браузер не запоминает."""
        f = folder(cid)
        comp = staff_comp(f)
        p = need_person(f, comp, key)
        ctx = staff_ctx(f, comp)
        values = ctx["personal"].get(p.key, {})
        errors = {fld: why for fld, why in sf.personal_problems(values)}
        resp = page(request, "contracts_person.html", active="contracts", p=p, values=values, errors=errors,
                    personal_fields=sf.PERSONAL_FIELDS, categories=sf.CATEGORIES, extra_roles=sf.EXTRA_ROLES,
                    personal_path=store.personal_path, **{**comp_ctx(f), **staff_parts(f, ctx)})
        resp.headers["Cache-Control"] = "no-store"
        return resp

    @app.post("/c/{cid}/contracts/person")
    async def contracts_person_save(request: Request, cid: str):
        f = folder(cid)
        comp = staff_comp(f)
        form = await request.form()
        p = need_person(f, comp, str(form.get("key", "")))
        store.save_personal(p.key, {k: " ".join(str(form.get(k, "")).split()) for k, _, _ in sf.PERSONAL_FIELDS})
        if not p.from_card:  # должность и категорию добавленных вручную можно поменять здесь
            data = f.contracts()
            for x in data.get("extra", []):
                if res.person_key(x.get("fio", "")) == p.key:
                    role = " ".join(str(form.get("role", "")).split())
                    cat = str(form.get("category", ""))
                    x["role"] = role or x.get("role", "")
                    x["category"] = cat if cat in sf.CATEGORIES else x.get("category", "б/к")
            f.save_contracts(data)
        return redirect(f"{base_url(f)}/contracts/person?{urlencode({'key': p.key, 'done': 'ct_person'})}")

    @app.post("/c/{cid}/contracts/remove")
    async def contracts_remove(request: Request, cid: str):
        f = folder(cid)
        key = str((await request.form()).get("key", ""))
        data = f.contracts()
        data["extra"] = [x for x in data.get("extra", []) if res.person_key(x.get("fio", "")) != key]
        data.get("people", {}).pop(key, None)
        f.save_contracts(data)
        return redirect(f"{base_url(f)}/contracts?done=ct_removed#tabel")

    def build_contract_doc(f: CompFolder, kind: str, key: str = "", path: Path | None = None) -> tuple[Path, set]:
        comp = staff_comp(f)
        ctx = staff_ctx(f, comp)
        team, personal, customer = ctx["team"], ctx["personal"], ctx["customer"]
        out = store.contracts_dir(f)
        if kind == "tabel":
            target = path or out / "Табель-наряд.xlsx"
            target.parent.mkdir(parents=True, exist_ok=True)
            return ct.write_tabel(comp, team, ctx["s"]["days"], ctx["s"]["accrual"], customer, personal, target), set()
        if kind == "all":
            target = path or out / "Договоры и акты.docx"
            target.parent.mkdir(parents=True, exist_ok=True)
            items = [(template_for(f, p.role), ct.contract_values(comp, p, customer, personal.get(p.key, {})))
                     for p in team if p.paid]
            return target, ct.write_contracts(items, target)
        if kind == "person":
            p = next((x for x in team if x.key == key), None)
            if p is None:
                raise HTTPException(404)
            target = path or out / f"{safe_name(f'{initials(p.fio)} — {p.role.lower()}', 100)}.docx"
            target.parent.mkdir(parents=True, exist_ok=True)
            values = ct.contract_values(comp, p, customer, personal.get(p.key, {}))
            return target, ct.write_contract(template_for(f, p.role), values, target)
        raise HTTPException(404)

    def contracts_back(f: CompFolder, key: str) -> str:
        if key:
            return f"{base_url(f)}/contracts/person?{urlencode({'key': key})}"
        return f"{base_url(f)}/contracts#docs"

    @app.post("/c/{cid}/contracts/doc/{kind}")
    async def contracts_doc_open(request: Request, cid: str, kind: str):
        f = folder(cid)
        key = str((await request.form()).get("key", ""))
        back = contracts_back(f, key)
        try:
            path, unknown = build_contract_doc(f, kind, key)
        except PermissionError:
            return redirect(with_done(back, "doc_locked"))
        app.state.opener(path)
        if unknown:
            return redirect(with_done(back, "ct_unknown", fields=", ".join(sorted(unknown))))
        return redirect(with_done(back, "ct_doc"))

    @app.get("/c/{cid}/contracts/file/{kind}")
    def contracts_doc_download(cid: str, kind: str, key: str = ""):
        f = folder(cid)
        name = {"tabel": "Табель-наряд.xlsx", "all": "Договоры и акты.docx"}.get(kind, "Договор.docx")
        tmp = Path(tempfile.mkdtemp(prefix="st-secretary-")) / name
        path, _ = build_contract_doc(f, kind, key, tmp)
        return FileResponse(path, filename=path.name, media_type=XLSX if path.suffix == ".xlsx" else DOCX,
                            headers={"Cache-Control": "no-store"},
                            background=BackgroundTask(shutil.rmtree, tmp.parent, ignore_errors=True))

    @app.post("/c/{cid}/contracts/template")
    async def contracts_template(request: Request, cid: str):
        """Шаблон в папку соревнования — поправить в Word под форму заказчика. role — шаблон для одной должности
        (копия общего), иначе — общий (копия встроенного)."""
        f = folder(cid)
        role = " ".join(str((await request.form()).get("role", "")).split())
        target = f.path / ct.template_name(role)
        if not target.exists():
            general = f.path / ct.template_name()
            if role and general.is_file():
                shutil.copyfile(general, target)
            else:
                ct.default_template().save(str(target))
        app.state.opener(target)
        return redirect(with_done(f"{base_url(f)}/contracts#docs", "ct_template", file=target.name))

    @app.post("/c/{cid}/contracts/folder")
    def contracts_folder(cid: str):
        f = folder(cid)
        d = store.contracts_dir(f)
        d.mkdir(parents=True, exist_ok=True)
        app.state.opener(d)
        return redirect(f"{base_url(f)}/contracts?done=opened#docs")
