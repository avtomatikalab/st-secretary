"""Интерфейс в браузере: локальный сервер на ноутбуке секретариата.

Страницы открываются по адресу http://127.0.0.1:<порт> только на этом компьютере. Всё хранится в
папке данных (см. store.py). Интернет не нужен: страницы ничего не подгружают извне.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections import Counter, defaultdict
from datetime import date, datetime
from pathlib import Path
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.background import BackgroundTask
from starlette.exceptions import HTTPException

from st_secretary import __version__
from st_secretary import commission as cm
from st_secretary import equipment as eq
from st_secretary import results as res
from st_secretary import staff as sf
from st_secretary.exporters import awards as aw
from st_secretary.exporters import contracts as ct
from st_secretary.exporters import final as fin
from st_secretary.exporters import judges as jd
from st_secretary.competition import LEVEL_LABELS
from st_secretary.exporters.commission_xlsx import write_commission_report
from st_secretary.importers.card_xlsx import CardError, load_card
from st_secretary.importers.preapp_xlsx import read_preapplication
from st_secretary.issues import CHECKED, ERROR, FIXED, INFO, SEVERITY_LABEL, SEVERITY_ORDER, WARNING, Issue
from st_secretary.money import money
from st_secretary.names import initials
from st_secretary.qualification import Qual
from st_secretary.textclean import from_years
from st_secretary.web import preapp_form as pf
from st_secretary.web.review import CHECK, DONE, FIX, SAVE, STATUS_LABEL, is_clean, issue_key
from st_secretary.web.forms import card_to_form, choices, empty_zachet, form_from_data, form_to_card
from st_secretary.web.steps import BY_SLUG, STEPS
from st_secretary.web.store import COMMISSION_REPORT, IMAGE_TYPES, SUMMARY, CompFolder, Store, safe_name

log = logging.getLogger("st_secretary.web")
HERE = Path(__file__).parent
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


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


GRADES = ("", "отлично", "хорошо", "удовлетворительно", "неудовлетворительно")  # оценка судейства


def key_of(e) -> str:
    """Ключ участника в отметках комиссии и проверки снаряжения — ФИО без регистра и «ё»."""
    return cm.person_key(e.name.full)


def _parse_dt(s: str) -> datetime | None:
    """Дата и время из поля браузера («2025-09-20T10:00»)."""
    try:
        return datetime.fromisoformat(s) if s else None
    except ValueError:
        return None


def team_anchor(file: str) -> str:
    """Метка команды в списке заявок (#t-…): по ней страница открывается сразу на этой команде."""
    return "t-" + hashlib.sha1(file.encode("utf-8")).hexdigest()[:10]


def _with_done(url: str, done: str, **extra: str) -> str:
    """Адрес с сообщением о сделанном (?done=…) и подробностями для него; якорь (#…) сохраняется."""
    parts = urlsplit(url)
    q = [(k, v) for k, v in parse_qsl(parts.query) if k != "done" and k not in extra]
    q += [("done", done), *extra.items()]
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
        "adm_saved": ("ok", "Отметки комиссии сохранены."),
        "adm_settings": ("ok", "Настройки комиссии сохранены."),
        "adm_numbers": ("ok", "Номера командам присвоены — их можно поправить вручную в поле «№» у команды."),
        "adm_report": ("ok", "Протокол комиссии и ведомость взносов сохранены в папке соревнования и открываются "
                             "в Excel."),
        "adm_locked": ("err", "Файл «Комиссия_по_допуску.xlsx» сейчас открыт в Excel. Закройте его и нажмите "
                              "кнопку ещё раз."),
        "reentry": ("ok", "Перезаявка записана с временем подачи — она видна у команды ниже."),
        "gear_settings": ("ok", "Перечень снаряжения и баллы сохранены."),
        "res_imported": ("ok", "Протокол загружен: места, составы и выполненные разряды — ниже."),
        "res_saved": ("ok", "Результаты зачёта сохранены."),
        "res_cleared": ("ok", "Результаты зачёта убраны."),
        "res_none": ("err", "Выберите файл протокола и зачёт."),
        "res_bad": ("err", "Файл не похож на итоговый протокол СЕКРЕТАРЬ_ST. Нужен протокол результатов (.xls), "
                           "сохранённый кнопкой «Считать протокол»."),
        "res_noxlrd": ("err", "Для чтения .xls не установлена библиотека xlrd — выполните в папке программы: uv sync."),
        "report_saved": ("ok", "Тексты для отчёта сохранены — нажмите «Открыть» у отчёта главного судьи."),
        "grades_saved": ("ok", "Оценки судейства сохранены — они попадут в справки о судействе и отчёт."),
        "doc_ready": ("ok", "Документ сохранён в папке «Документы по итогам» и открывается."),
        "doc_locked": ("err", "Этот документ сейчас открыт в Word или Excel. Закройте его и нажмите кнопку ещё раз."),
        "gear_saved": ("ok", "Проверка снаряжения сохранена."),
        "docs_added": ("ok", "Документы добавлены. Они хранятся только на этом компьютере."),
        "docs_skipped": ("err", "Часть файлов не добавлена: подходят фото (JPG, PNG), PDF и документы Word/Excel."),
        "docs_none": ("err", "Файлы не выбраны."),
        "docs_removed": ("ok", "Документ убран — он перенесён в папку «Убранные» в документах команды, его можно вернуть."),
        "reentry_late": ("err", "Перезаявка записана, но подана позже, чем за час до старта: по Правилам (п. 8.5) "
                                "такая перезаявка не принимается. Решение — за ГСК."),
        "reentry_repeat": ("err", "Перезаявка записана, но она повторная: по Правилам (п. 8.5) повторные "
                                  "перезаявки не принимаются. Решение — за ГСК."),
        "opened": ("ok", "Открываю…"),
        "ct_saved": ("ok", "Табель сохранён."),
        "ct_settings": ("ok", "Период работы, ставки и начисления сохранены."),
        "ct_customer": ("ok", "Сведения о заказчике сохранены — они попадут в договоры, акты и табель."),
        "ct_added": ("ok", "Человек добавлен в табель — отметьте дни его работы и заполните данные для договора."),
        "ct_need": ("err", "Впишите ФИО и должность."),
        "ct_exists": ("err", "Такой человек уже есть в табеле (судьи из карточки попадают туда сами)."),
        "ct_removed": ("ok", "Убрано из табеля. Личные данные человека остались — пригодятся на других соревнованиях."),
        "ct_person": ("ok", "Сохранено. Личные данные хранятся только на этом компьютере."),
        "ct_doc": ("ok", "Документ сохранён в папке «Договоры и табель» на этом компьютере и открывается."),
        "ct_template": ("ok", f"Шаблон «{q.get('file', 'Шаблон договора.docx')}» — в папке соревнования, открывается "
                              "в Word. Правьте текст как нужно заказчику, поля в двойных фигурных скобках оставьте — "
                              "программа будет брать этот файл."),
        "ct_unknown": ("err", f"Документ открывается, но в шаблоне есть поля, которых программа не знает: "
                              f"{q.get('fields', '')}. Они остались в тексте как есть — поправьте их в шаблоне "
                              "по списку полей ниже."),
    }
    auto_done = done is not None and done.endswith("-done")  # заявка без замечаний — «Проверено» сразу
    if auto_done:
        done = done[:-len("-done")]
    if done in texts:
        kind, text = texts[done]
        if auto_done:
            text = text.replace(" — результат ниже.", ".") + " Замечаний нет — заявка сразу отмечена «Проверено»."
        return {"kind": kind, "text": text}
    return None


def create_app(data_dir: str | Path, opener=None, shutdown=None, docs_dir: str | Path | None = None) -> FastAPI:
    """shutdown — как остановить сервер (кнопка «Выключить»); None — кнопки нет (тесты, запуск не из окна).
    docs_dir — где хранить сканы документов участников (по умолчанию — папка в профиле, не в облаке)."""
    store = Store(data_dir, docs_dir)
    app = FastAPI(title="СТ-Секретарь", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.shutdown = shutdown
    app.state.store = store
    app.state.opener = opener or open_in_os
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
    templates = Jinja2Templates(directory=HERE / "templates")
    templates.env.filters["d"] = _fmt_date
    templates.env.filters["from_years"] = from_years
    templates.env.filters["team_anchor"] = team_anchor
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
        adm = adm_totals(commission(f, comp)[1]) if pre else None
        return page(request, "overview.html", active="", card_issues=card_issues, files=files, pre=pre, adm=adm,
                    **ctx)

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
        form = pf.app_to_form(read_preapplication(path), team, comp)
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
        head, rows, errors = pf.form_to_file(form)
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
                saved = f.save_preapp(name if path else None, head, rows, [q.label for q in Qual])
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

    # ------------------------------------------------------------ комиссия по допуску

    def commission(f: CompFolder, comp):
        """Отметки комиссии и состояние допуска по каждой заявке (в порядке списка заявок)."""
        result, _ = store.review(f, comp)
        data = f.admission()
        files = [p.name for p in f.preapp_files()]
        gear = eq.admission_problems(eq.evaluate(result, files, f.equipment(), key_of), f.equipment())
        return data, cm.evaluate(result, files, comp, data, gear)

    def adm_totals(teams: list) -> dict:
        people = [p for t in teams for p in t.persons]
        return {"teams": len(teams), "teams_ok": sum(t.status == cm.ADMITTED for t in teams),
                "teams_by": Counter(t.status for t in teams),
                "people": len(people), "people_ok": sum(p.status == cm.ADMITTED for p in people),
                "people_wait": sum(p.status == cm.PENDING for p in people),
                "people_no": sum(p.status == cm.REJECTED for p in people),
                "fee_due": sum(t.fee_due for t in teams), "fee_paid": sum(t.fee_paid for t in teams)}

    def adm_parts(f: CompFolder, data: dict) -> dict:
        pdocs, tdocs = cm.required_docs(data)
        return {"pdocs": pdocs, "tdocs": tdocs, "adm_settings": cm.settings(data), "fee_methods": cm.FEE_METHODS,
                "base": _base(f), "docs_n": {p.name: len(store.team_docs(f, p.name)) for p in f.preapp_files()},
                "gear_on": bool(eq.settings(f.equipment())["items"])}

    @app.get("/c/{cid}/admission")
    def admission_page(request: Request, cid: str):
        f = folder(cid)
        ctx = comp_ctx(f)
        comp = ctx["comp"]
        blocked = [i for i in comp.check() if i.severity == ERROR] if comp else ctx["card_errors"]
        teams, data = [], f.admission()
        if comp and not blocked:
            data, teams = commission(f, comp)
        return page(request, "admission.html", active="admission", blocked=blocked, teams=teams,
                    totals=adm_totals(teams), all_person_docs=cm.PERSON_DOCS, all_team_docs=cm.TEAM_DOCS,
                    **{**ctx, **adm_parts(f, data)})

    @app.post("/c/{cid}/admission/team")
    async def admission_team(request: Request, cid: str):
        """Отметки по одной команде. Со страницы приходят сами при каждом изменении (ответ — обновлённый
        блок команды); без скриптов — обычная форма с кнопкой «Сохранить»."""
        f = folder(cid)
        comp = need_comp(f)
        form = await request.form()
        path = need_file(f, str(form.get("file", "")))
        data = f.admission()
        pdocs, tdocs = cm.required_docs(data)
        tm = data.setdefault("teams", {}).setdefault(path.name, {})
        everything = form.get("do") == "all_docs"  # «Отметить все документы»
        tm["team_docs"] = {**tm.get("team_docs", {}),
                           **{d.key: everything or bool(form.get(f"td-{d.key}")) for d in tdocs}}
        number = str(form.get("number", "")).strip()
        tm["number"] = int(number) if number.isdigit() else None
        paid = str(form.get("fee_paid", "")).replace(" ", "").replace(",", ".").strip()
        tm["fee_paid"] = int(float(paid)) if paid.replace(".", "", 1).isdigit() else 0
        tm["fee_method"] = str(form.get("fee_method", ""))
        tm["decision"] = str(form.get("decision", "")) if form.get("decision") in (cm.ADMITTED, cm.REJECTED) else ""
        tm["note"] = str(form.get("note", "")).strip()
        people = tm.setdefault("people", {})
        idx = sorted({int(k.split("-")[1]) for k in form.keys() if re.fullmatch(r"p-\d+-key", k)})
        for i in idx:
            pm = people.setdefault(str(form.get(f"p-{i}-key")), {})
            pm["docs"] = {**pm.get("docs", {}),
                          **{d.key: everything or bool(form.get(f"p-{i}-d-{d.key}")) for d in pdocs}}
            decision = str(form.get(f"p-{i}-decision", ""))
            pm["decision"] = decision if decision in (cm.ADMITTED, cm.REJECTED) else ""
            pm["reason"] = str(form.get(f"p-{i}-reason", "")).strip() if pm["decision"] else ""
        f.save_admission(data)
        data, teams = commission(f, comp)
        t = next(x for x in teams if x.file == path.name)
        clash = [x.title for x in teams if x.number is not None and x.number == t.number and x.file != t.file]
        if request.headers.get("x-autosave"):
            parts = {**adm_parts(f, data), "clash": clash, "in_check": bool(form.get("in_check"))}
            return JSONResponse({"team": templates.get_template("_admission_team.html").render(t=t, **parts),
                                 "tiles": templates.get_template("_admission_tiles.html").render(
                                     totals=adm_totals(teams), **parts),
                                 "saved": datetime.now().strftime("%H:%M:%S"), "status": t.status,
                                 "by": {"all": len(teams), **{s: sum(x.status == s for x in teams)
                                                             for s in (cm.PENDING, cm.ADMITTED, cm.REJECTED)}}})
        if form.get("in_check"):
            return _redirect(f"{_base(f)}/admission/check?{urlencode({'file': path.name, 'done': 'adm_saved'})}")
        return _redirect(f"{_base(f)}/admission?done=adm_saved#{team_anchor(path.name)}")

    @app.post("/c/{cid}/admission/settings")
    async def admission_settings(request: Request, cid: str):
        f = folder(cid)
        form = await request.form()
        data = f.admission()
        data["settings"] = {"docs": [d.key for d in cm.PERSON_DOCS if form.get(f"doc-{d.key}")],
                            "team_docs": [d.key for d in cm.TEAM_DOCS if form.get(f"tdoc-{d.key}")],
                            "start_at": str(form.get("start_at", "")).strip()}
        f.save_admission(data)
        return _redirect(f"{_base(f)}/admission?done=adm_settings")

    @app.post("/c/{cid}/admission/numbers")
    async def admission_numbers(request: Request, cid: str):
        """Номера командам по порядку списка: только тем, у кого нет, или всем заново."""
        f = folder(cid)
        comp = need_comp(f)
        again = (await request.form()).get("mode") == "all"
        data, teams = commission(f, comp)
        taken = set() if again else {t.number for t in teams if t.number is not None}
        n = 1
        for t in teams:
            if t.number is not None and not again:
                continue
            while n in taken:
                n += 1
            data.setdefault("teams", {}).setdefault(t.file, {})["number"] = n
            taken.add(n)
        f.save_admission(data)
        return _redirect(f"{_base(f)}/admission?done=adm_numbers")

    def commission_report(f: CompFolder, path: Path | None = None) -> Path:
        comp = need_comp(f)
        if not f.preapp_files():
            raise HTTPException(409, "Пока нет ни одной заявки — добавьте их на странице «Предварительные заявки».")
        data, teams = commission(f, comp)
        gdata, gear = gear_ctx(f, comp)
        return write_commission_report(teams, comp, data, path or f.commission_report_path,
                                       gear if eq.settings(gdata)["items"] else None, gdata)

    @app.post("/c/{cid}/admission/report")
    def admission_report_open(cid: str):
        f = folder(cid)
        try:
            path = commission_report(f)
        except PermissionError:
            return _redirect(f"{_base(f)}/admission?done=adm_locked")
        app.state.opener(path)
        return _redirect(f"{_base(f)}/admission?done=adm_report")

    @app.get("/c/{cid}/admission/report.xlsx")
    def admission_report_download(cid: str):
        f = folder(cid)
        tmp = Path(tempfile.mkdtemp(prefix="st-secretary-")) / COMMISSION_REPORT
        commission_report(f, tmp)
        return FileResponse(tmp, filename=COMMISSION_REPORT, media_type=XLSX,
                            background=BackgroundTask(shutil.rmtree, tmp.parent, ignore_errors=True))

    # ------------------------------------------------------------ проверка снаряжения

    def gear_ctx(f: CompFolder, comp) -> tuple[dict, list]:
        result, _ = store.review(f, comp)
        data = f.equipment()
        return data, eq.evaluate(result, [p.name for p in f.preapp_files()], data, key_of)

    def gear_totals(gear: list, data: dict) -> dict:
        limit = eq.settings(data)["limit"]
        by = Counter("new" if not g.checked else "out" if g.total > limit else "ok" for g in gear)
        return {"teams": len(gear), "checked": sum(g.checked for g in gear), "by": by}

    def gear_parts(f: CompFolder, data: dict) -> dict:
        s = eq.settings(data)
        return {"s": s, "items_person": [i for i in s["items"] if i.kind != eq.GROUP],
                "items_group": [i for i in s["items"] if i.kind == eq.GROUP], "kind_label": eq.KIND_LABEL,
                "short_name": eq.short_name, "key_of": key_of, "verdict": eq.verdict, "missing_text": eq.missing_text,
                "base": _base(f)}

    @app.get("/c/{cid}/equipment")
    def equipment_page(request: Request, cid: str):
        f = folder(cid)
        comp = need_comp(f)
        data, gear = gear_ctx(f, comp)
        return page(request, "equipment.html", active="admission", gear=gear, totals=gear_totals(gear, data),
                    kinds=list(eq.KIND_LABEL.items()), **{**comp_ctx(f), **gear_parts(f, data)})

    @app.post("/c/{cid}/equipment/settings")
    async def equipment_settings(request: Request, cid: str):
        f = folder(cid)
        form = await request.form()
        data = f.equipment()
        if form.get("do") == "template":
            data["settings"] = eq.template_settings(eq.PSR_KRSK_2025)
        else:
            idx = sorted({int(m.group(1)) for k in form.keys() if (m := re.fullmatch(r"it-(\d+)-name", k))})
            items, used = [], set()
            for i in idx:
                name = str(form.get(f"it-{i}-name", "")).strip()
                if not name:
                    continue
                qty = str(form.get(f"it-{i}-qty", "1")).strip()
                iid = str(form.get(f"it-{i}-id", "")).strip() or f"n{i}"
                while iid in used:
                    iid += "x"
                used.add(iid)
                kind = str(form.get(f"it-{i}-kind", eq.PERSONAL))
                items.append({"id": iid, "kind": kind if kind in eq.KIND_LABEL else eq.PERSONAL, "name": name,
                              "unit": str(form.get(f"it-{i}-unit", "")).strip() or "шт.",
                              "qty": int(qty) if qty.isdigit() and int(qty) > 0 else 1})
            pen = {k: str(form.get(f"pen-{k}", "")).strip() for k in eq.KIND_LABEL}
            limit = str(form.get("limit", "")).strip()
            data["settings"] = {"items": items, "penalty": {k: int(v) for k, v in pen.items() if v.isdigit()},
                                "limit": int(limit) if limit.isdigit() else eq.RULES["limit"]}
        f.save_equipment(data)
        return _redirect(f"{_base(f)}/equipment?done=gear_settings")

    @app.post("/c/{cid}/equipment/team")
    async def equipment_team(request: Request, cid: str):
        f = folder(cid)
        comp = need_comp(f)
        form = await request.form()
        path = need_file(f, str(form.get("file", "")))
        data = f.equipment()
        s = eq.settings(data)
        tm = data.setdefault("teams", {}).setdefault(path.name, {})
        everything = form.get("do") == "all"  # «Начать проверку: всё есть» — дальше снимать отметки с того, чего нет

        def value(name: str, it) -> object:
            if everything:
                return "all"
            if it.qty == 1:
                return "all" if form.get(name) else 0
            v = str(form.get(name, "")).strip()
            return int(v) if v.isdigit() else 0

        tm["group"] = {it.id: value(f"g-{it.id}", it) for it in s["items"] if it.kind == eq.GROUP} or {"_": 0}
        people = tm.setdefault("people", {})
        idx = sorted({int(k.split("-")[1]) for k in form.keys() if re.fullmatch(r"p-\d+-key", k)})
        if everything and not idx:  # у заявки ещё не отрисованы строки участников — отметить по заявке
            result, _ = store.review(f, comp)
            team = next((t for t in result.teams if t.source == path.name), None)
            for e in (team.entries if team else []):
                people[key_of(e)] = {it.id: "all" for it in s["items"] if it.kind != eq.GROUP}
        for i in idx:
            people[str(form.get(f"p-{i}-key"))] = {it.id: value(f"p-{i}-{it.id}", it)
                                                   for it in s["items"] if it.kind != eq.GROUP}
        tm["note"] = str(form.get("note", "")).strip()
        f.save_equipment(data)
        data, gear = gear_ctx(f, comp)
        g = next(x for x in gear if x.file == path.name)
        if request.headers.get("x-autosave"):
            parts = gear_parts(f, data)
            totals = gear_totals(gear, data)
            return JSONResponse({"team": templates.get_template("_equipment_team.html").render(g=g, **parts),
                                 "tiles": templates.get_template("_equipment_tiles.html").render(totals=totals,
                                                                                                 **parts),
                                 "saved": datetime.now().strftime("%H:%M:%S"),
                                 "by": {"all": totals["teams"], **{k: totals["by"][k] for k in ("new", "ok", "out")}}})
        return _redirect(f"{_base(f)}/equipment?done=gear_saved#{team_anchor(path.name)}")

    # ------------------------------------------------------------ документы команд

    def docs_back(f: CompFolder, sent: str, file: str) -> str:
        return back_to(None, f, sent, team_url(f, file) + "#docs")

    @app.post("/c/{cid}/docs/upload")
    async def docs_upload(request: Request, cid: str):
        f = folder(cid)
        form = await request.form()
        path = need_file(f, str(form.get("file", "")))
        added = skipped = 0
        for up in form.getlist("files"):
            name = getattr(up, "filename", "") or ""
            if not name:
                continue
            try:
                store.add_team_doc(f, path.name, name, await up.read())
                added += 1
            except ValueError:
                skipped += 1
        back = docs_back(f, str(form.get("back", "")), path.name)
        return _redirect(_with_done(back, "docs_added" if added and not skipped else "docs_skipped" if skipped
                                    else "docs_none"))

    @app.get("/c/{cid}/docs/view")
    def docs_view(cid: str, file: str = "", name: str = ""):
        """Файл документа — чтобы показать его прямо на странице (фото, PDF)."""
        f = folder(cid)
        path = need_file(f, file)
        doc = store.team_doc(f, path.name, name)
        if doc is None:
            raise HTTPException(404)
        return FileResponse(doc, content_disposition_type="inline", filename=doc.name,
                            headers={"Cache-Control": "no-store"})  # персональные данные — не оставлять в кэше

    @app.post("/c/{cid}/docs/remove")
    async def docs_remove(request: Request, cid: str):
        f = folder(cid)
        form = await request.form()
        path = need_file(f, str(form.get("file", "")))
        try:
            store.remove_team_doc(f, path.name, str(form.get("name", "")))
        except FileNotFoundError:
            raise HTTPException(404) from None
        return _redirect(_with_done(docs_back(f, str(form.get("back", "")), path.name), "docs_removed"))

    @app.post("/c/{cid}/docs/open")
    async def docs_open(request: Request, cid: str):
        """Открыть документ или папку документов команды программой компьютера."""
        f = folder(cid)
        form = await request.form()
        path = need_file(f, str(form.get("file", "")))
        name = str(form.get("name", ""))
        if name:
            target = store.team_doc(f, path.name, name)
            if target is None:
                raise HTTPException(404)
        else:
            target = store.team_docs_dir(f, path.name)
            target.mkdir(parents=True, exist_ok=True)
        app.state.opener(target)
        return _redirect(_with_done(docs_back(f, str(form.get("back", "")), path.name), "opened"))

    @app.get("/c/{cid}/admission/check")
    def admission_check(request: Request, cid: str, file: str = ""):
        """Проверка в одном окне: документы команды слева, отметки комиссии справа."""
        f = folder(cid)
        comp = need_comp(f)
        path = need_file(f, file)
        data, teams = commission(f, comp)
        order = [t.file for t in teams]
        at = order.index(path.name)
        near = {k: teams[j] for k, j in (("prev", at - 1), ("next", at + 1)) if 0 <= j < len(order)}
        return page(request, "admission_check.html", active="admission", focus=True, t=teams[at],
                    docs=doc_list(f, path.name),
                    position=(at + 1, len(order)), prev_team=near.get("prev"), next_team=near.get("next"),
                    docs_dir=store.team_docs_dir(f, path.name), here=f"{_base(f)}/admission/check?file={quote(path.name)}",
                    **{**comp_ctx(f), **adm_parts(f, data)})

    def doc_list(f: CompFolder, file: str) -> list[dict]:
        out = []
        for p in store.team_docs(f, file):
            ext = p.suffix.lower()
            kind = "image" if ext in IMAGE_TYPES else "pdf" if ext == ".pdf" else "other"
            out.append({"name": p.name, "label": p.stem, "kind": kind,
                        "url": f"{_base(f)}/docs/view?{urlencode({'file': file, 'name': p.name})}"})
        return out

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
            return _redirect(f"{_base(f)}/awards?done=res_none")
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / Path(up.filename).name
            p.write_bytes(await up.read())
            try:
                from st_secretary.importers.sekretar_xls import read_result_protocol
                proto = read_result_protocol(p)
            except ImportError:
                return _redirect(f"{_base(f)}/awards?done=res_noxlrd")
            except Exception:  # noqa: BLE001 — не протокол или не .xls: объяснить, а не упасть
                return _redirect(f"{_base(f)}/awards?done=res_bad")
        if not proto.rows:
            return _redirect(f"{_base(f)}/awards?done=res_bad")
        data = f.results_data()
        data.setdefault("zachety", {})[key] = res.from_protocol(proto)
        f.save_results_data(data)
        return _redirect(f"{_base(f)}/awards?done=res_imported")

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
        idx = sorted({int(m.group(1)) for k in form.keys() if (m := re.fullmatch(r"r-(\d+)-team", k))})
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
        return _redirect(f"{_base(f)}/awards?done=res_saved")

    @app.post("/c/{cid}/awards/clear")
    async def awards_clear(request: Request, cid: str):
        f = folder(cid)
        key = str((await request.form()).get("zachet", ""))
        data = f.results_data()
        data.get("zachety", {}).pop(key, None)
        f.save_results_data(data)
        return _redirect(f"{_base(f)}/awards?done=res_cleared")

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
            return _redirect(f"{_base(f)}/awards?done=doc_locked")
        app.state.opener(path)
        return _redirect(f"{_base(f)}/awards?done=doc_ready")

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
        return _redirect(f"{_base(f)}/awards?done=report_saved#report")

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
        return _redirect(f"{_base(f)}/awards?done=grades_saved#judges")

    # ------------------------------------------------------------ договоры, акты, табель

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
        return {"base": _base(f), "weekday": ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"], "money": money,
                "missing_personal": sf.missing_personal, "personal_problems": sf.personal_problems, **ctx}

    @app.get("/c/{cid}/contracts")
    def contracts_page(request: Request, cid: str):
        f = folder(cid)
        comp = need_comp(f)
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
                    **{**comp_ctx(f), **staff_parts(f, ctx)})

    @app.post("/c/{cid}/contracts/days")
    async def contracts_days(request: Request, cid: str):
        """Табель: отметки дней и «без оплаты». С автосохранением — возвращает обновлённый табель."""
        f = folder(cid)
        comp = need_comp(f)
        form = await request.form()
        data = f.contracts()
        s = sf.settings(comp, data)
        marks = data.setdefault("people", {})
        idx = sorted({int(m.group(1)) for k in form.keys() if (m := re.fullmatch(r"p-(\d+)-key", k))})
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
        return _redirect(f"{_base(f)}/contracts?done=ct_saved#tabel")

    @app.post("/c/{cid}/contracts/settings")
    async def contracts_settings(request: Request, cid: str):
        f = folder(cid)
        comp = need_comp(f)
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
            for k in form.keys():
                if k.startswith("rate-"):
                    v = re.sub(r"[^\d]", "", str(form.get(k, "")).split(",")[0].split(".")[0])
                    if v:
                        rates[k[5:]] = int(v)
            data["rates"] = {**data.get("rates", {}), **rates}
            for k in [k[5:] for k in form.keys() if k.startswith("rate-") and not str(form.get(k, "")).strip()]:
                data["rates"].pop(k, None)
        f.save_contracts(data)
        return _redirect(f"{_base(f)}/contracts?done=ct_settings#settings")

    @app.post("/c/{cid}/contracts/customer")
    async def contracts_customer(request: Request, cid: str):
        f = folder(cid)
        form = await request.form()
        data = f.contracts()
        data["customer"] = {k: str(form.get(k, "")).strip() for k, _, _ in ct.CUSTOMER_FIELDS}
        f.save_contracts(data)
        return _redirect(f"{_base(f)}/contracts?done=ct_customer#customer")

    @app.post("/c/{cid}/contracts/add")
    async def contracts_add(request: Request, cid: str):
        f = folder(cid)
        comp = need_comp(f)
        form = await request.form()
        fio = " ".join(str(form.get("fio", "")).split())
        role = " ".join(str(form.get("role", "")).split())
        cat = str(form.get("category", "б/к"))
        if not fio or not role:
            return _redirect(f"{_base(f)}/contracts?done=ct_need#add")
        data = f.contracts()
        if res.person_key(fio) in {p.key for p in sf.people(comp, data)}:
            return _redirect(f"{_base(f)}/contracts?done=ct_exists#add")
        data.setdefault("extra", []).append({"fio": fio, "role": role,
                                             "category": cat if cat in sf.CATEGORIES else "б/к"})
        f.save_contracts(data)
        return _redirect(f"{_base(f)}/contracts?done=ct_added#tabel")

    def need_person(f: CompFolder, comp, key: str):
        p = next((x for x in sf.people(comp, f.contracts()) if x.key == key), None)
        if p is None:
            raise HTTPException(404)
        return p

    @app.get("/c/{cid}/contracts/person")
    def contracts_person(request: Request, cid: str, key: str = ""):
        """Личные данные для договора — только на этом компьютере; страницу браузер не запоминает."""
        f = folder(cid)
        comp = need_comp(f)
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
        comp = need_comp(f)
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
        return _redirect(f"{_base(f)}/contracts/person?{urlencode({'key': p.key, 'done': 'ct_person'})}")

    @app.post("/c/{cid}/contracts/remove")
    async def contracts_remove(request: Request, cid: str):
        f = folder(cid)
        key = str((await request.form()).get("key", ""))
        data = f.contracts()
        data["extra"] = [x for x in data.get("extra", []) if res.person_key(x.get("fio", "")) != key]
        data.get("people", {}).pop(key, None)
        f.save_contracts(data)
        return _redirect(f"{_base(f)}/contracts?done=ct_removed#tabel")

    def build_contract_doc(f: CompFolder, kind: str, key: str = "", path: Path | None = None) -> tuple[Path, set]:
        comp = need_comp(f)
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
            return f"{_base(f)}/contracts/person?{urlencode({'key': key})}"
        return f"{_base(f)}/contracts#docs"

    @app.post("/c/{cid}/contracts/doc/{kind}")
    async def contracts_doc_open(request: Request, cid: str, kind: str):
        f = folder(cid)
        key = str((await request.form()).get("key", ""))
        back = contracts_back(f, key)
        try:
            path, unknown = build_contract_doc(f, kind, key)
        except PermissionError:
            return _redirect(_with_done(back, "doc_locked"))
        app.state.opener(path)
        if unknown:
            return _redirect(_with_done(back, "ct_unknown", fields=", ".join(sorted(unknown))))
        return _redirect(_with_done(back, "ct_doc"))

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
        return _redirect(_with_done(f"{_base(f)}/contracts#docs", "ct_template", file=target.name))

    @app.post("/c/{cid}/contracts/folder")
    def contracts_folder(cid: str):
        f = folder(cid)
        d = store.contracts_dir(f)
        d.mkdir(parents=True, exist_ok=True)
        app.state.opener(d)
        return _redirect(f"{_base(f)}/contracts?done=opened#docs")

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
