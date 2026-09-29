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
import threading
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
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
from st_secretary import judge_sync as js
from st_secretary import practice as pt_
from st_secretary import psr_run as pr
from st_secretary import results as res
from st_secretary import staff as sf
from st_secretary import time_run as tr
from st_secretary import verify as vf
from st_secretary.exporters import awards as aw
from st_secretary.exporters import contracts as ct
from st_secretary.exporters import final as fin
from st_secretary.exporters import judges as jd
from st_secretary.exporters import results_protocol as rp
from st_secretary.competition import LEVEL_LABELS
from st_secretary.disciplines import Status
from st_secretary.exporters.commission_xlsx import write_commission_report
from st_secretary.importers.card_xlsx import CardError, load_card
from st_secretary.importers.si_reader import read_si_reader
from st_secretary.importers.preapp_xlsx import read_preapplication
from st_secretary.issues import CHECKED, ERROR, FIXED, INFO, SEVERITY_LABEL, SEVERITY_ORDER, WARNING, Issue
from st_secretary.money import money
from st_secretary.names import initials
from st_secretary.qualification import Qual
from st_secretary.textclean import from_years
from st_secretary.web import preapp_form as pf
from st_secretary.web.board import BoardServer, create_board_app, qr_svg
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
PROTEST_DECISIONS = ("удовлетворён", "удовлетворён частично", "отклонён", "не рассматривается")


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
        "run_stages": ("ok", "Этапы дистанции сохранены."),
        "si_nofile": ("err", "Выберите файл si_reader.csv из SPORTident Reader."),
        "si_bad": ("err", f"Файл не прочитан: {q.get('why', '')}. Нужен экспорт SportIdent Reader «Config+ (card readout)»."),
        "si_done": ("ok" if not q.get("unknown") else "err",
                    f"Прочитано чипов: {q.get('n', '0')}, заполнено команд: {q.get('t', '0')}."
                    + (f" Чипы без команды: {q.get('unknown')} — впишите чип в колонку «Чип» у команды и загрузите файл "
                       "ещё раз." if q.get("unknown") else "")
                    + (f" Время по чипу заменило вписанное вручную у команд: {q.get('replaced')} — проверьте."
                       if q.get("replaced") else "")),
        "run_saved": ("ok", "Баллы сохранены."),
        "run_nofile": ("err", "Выберите файл рабочей книги СЕКРЕТАРЬ_ST (.xls)."),
        "run_badbook": ("err", "В файле нет листа «Протокол_группа» или он не похож на протокол СЕКРЕТАРЬ_ST. Нужна "
                               "рабочая книга, в которой вносились баллы по этапам."),
        "run_imported": ("ok", f"Из рабочей книги перенесено этапов: {q.get('n', '0')}, команд с баллами: "
                               f"{q.get('t', '0')}. {q.get('notes', '')}".strip()),
        "run_empty": ("err", "Пока нечего публиковать: ни одна команда не получила место — внесите баллы."),
        "run_published": ("ok", f"Предварительный протокол сохранён в папку «Протоколы» и открывается — распечатайте "
                                f"и вывесите. Протесты по результатам принимаются до {q.get('until', '')} (п. 8.17)."),
        "run_protest": ("ok", "Протест записан с временем подачи."),
        "run_protest_late": ("err", "Протест записан, но подан позже часа после публикации предварительного протокола: "
                                    "по Правилам (п. 8.17) такой протест не принимается. Решение — за ГСК."),
        "run_protest_empty": ("err", "Впишите, с чем не согласна команда."),
        "run_decided": ("ok", "Решение по протесту записано."),
        "run_not_published": ("err", "Сначала опубликуйте предварительный протокол — с него начинается час на протесты."),
        "run_changed": ("err", "После публикации предварительного протокола баллы или статусы менялись. Опубликуйте "
                               "предварительный протокол заново — с новым часом на протесты."),
        "run_open_protests": ("err", "Есть протесты без решения — сначала запишите решения по ним."),
        "run_official": ("ok", "Результаты утверждены: официальный протокол сохранён в папку «Протоколы» и открывается. "
                               "Места и разряды переданы в «Награждение и документы по итогам»."),
        "judge_issued": ("ok", "Ссылка этапа готова. Прежняя ссылка этого этапа (если была) больше не работает."),
        "judge_revoked": ("ok", "Ссылка этапа отозвана — с неё больше ничего не придёт. Уже присланное сохранено."),
        "board_on": ("ok", "Табло включено для этого соревнования."),
        "board_off": ("ok", "Табло для этого соревнования выключено — его результатов на табло не видно."),
        "board_started": ("ok", "Табло раздаётся по Wi-Fi — адрес и QR-код ниже. Если Windows спросит разрешение "
                                "в брандмауэре — разрешите для частной сети."),
        "board_stopped": ("ok", "Раздача табло остановлена."),
        "board_failed": ("err", "Табло не запустилось — подробности ниже."),
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
    run_lock = threading.RLock()  # Результаты_дистанции.json: пишут и страница секретаря, и телефоны судей
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

    # ------------------------------------------------------------ протоколы этапов и результаты (ПСР)

    def zachet_inputs(f: CompFolder, comp, z) -> list:
        """Команды зачёта: из заявок, номера и допуск — из комиссии по допуску; не допущенные участники не в составе."""
        if not f.preapp_files():
            return []
        _, teams = commission(f, comp)
        out = []
        for t in teams:
            if not t.team:
                continue
            people = [p for p in t.persons if p.entry.zachet and p.entry.zachet.key == z.key and p.status != cm.REJECTED]
            if not people:
                continue
            out.append(pr.TeamInput(t.file, t.team.team, t.team.territory, str(t.number or ""),
                                    [pr.Member(p.entry.name.full, p.entry.qual,
                                               p.entry.qual.label if p.entry.qual is not None else "", p.entry.chip)
                                     for p in people],
                                    admitted=t.status != cm.REJECTED))
        return out

    def need_zachet(comp, key: str):
        z = next((x for x in comp.zachety if x.key == key), None) if key else (comp.zachety[0] if comp.zachety else None)
        if z is None:
            raise HTTPException(404)
        return z

    def run_ctx(f: CompFolder, comp, z) -> tuple[dict, dict, object]:
        data = f.run_data()
        zdata = data.get("zachety", {}).get(z.key, {})
        compute = tr.compute if tr.is_time_discipline(z) else pr.compute  # спелео, пешеходные — по времени
        run = compute(comp, z, zdata, zachet_inputs(f, comp, z))
        run.issues += js.judge_issues(zdata, run.stages, {r.inp.file: r.inp.team for r in run.rows})
        return data, zdata, run

    def results_parts(f: CompFolder, z, zdata: dict, run) -> dict:
        return {"base": _base(f), "z": z, "zdata": zdata, "run": run, "pt": pr.points_text, "ck": tr.clock_text,
                "res": lambda r: pr.result_text(run, r), "is_time": run.kind == "time",
                "from_phone": lambda sid, file: js.from_phone(zdata, sid, file),
                "grid": sorted(run.rows, key=lambda r: r.start_order), "status_label": pr.STATUS_LABEL,
                "status_short": pr.STATUS_SHORT, "statuses": list(pr.STATUS_LABEL), "FINISHED": Status.FINISHED,
                "zq": urlencode({"z": z.key}), "pct": lambda x: f"{float(x):.2f}".replace(".", ",") if x is not None else ""}

    @app.get("/c/{cid}/results")
    def results_page(request: Request, cid: str, z: str = ""):
        f = folder(cid)
        comp = need_comp(f)
        if not comp.zachety:
            return page(request, "results.html", active="results", zachet=None, **comp_ctx(f))
        zz = need_zachet(comp, z)
        _, zdata, run = run_ctx(f, comp, zz)
        return page(request, "results.html", active="results", zachet=zz, zachety=comp.zachety,
                    state=protocol_state(zdata, run, app.state.clock()), decisions=PROTEST_DECISIONS,
                    **{**comp_ctx(f), **results_parts(f, zz, zdata, run)})

    def _save_zachet(f: CompFolder, key: str, update) -> None:
        with run_lock:  # секретарь и телефоны судей пишут в один файл — по очереди
            data = f.run_data()
            zdata = data.setdefault("zachety", {}).setdefault(key, {})
            update(zdata)
            f.save_run_data(data)

    @app.post("/c/{cid}/results/stages")
    async def results_stages(request: Request, cid: str, z: str = ""):
        """Этапы дистанции (тур, название, МШ), параметры дистанции для фактического класса, правило равенства."""
        f = folder(cid)
        zz = need_zachet(need_comp(f), z)
        form = await request.form()
        idx = sorted({int(m.group(1)) for k in form.keys() if (m := re.fullmatch(r"st-(\d+)-name", k))})

        def update(zdata):
            used = {str(s.get("id")) for s in zdata.get("stages", [])}
            stages = []
            for i in idx:
                name = " ".join(str(form.get(f"st-{i}-name", "")).split())
                if not name:
                    continue
                sid = str(form.get(f"st-{i}-id", "")).strip()
                if not sid:
                    n = 1
                    while f"s{n}" in used:
                        n += 1
                    sid = f"s{n}"
                    used.add(sid)
                stages.append({"id": sid, "tour": " ".join(str(form.get(f"st-{i}-tour", "")).split()), "name": name,
                               "max": str(form.get(f"st-{i}-max", "")).strip(),
                               "kv": str(form.get(f"st-{i}-kv", "")).strip()})
            zdata["stages"] = stages
            zdata["distance"] = {k: str(form.get(k, "")).strip() for k in ("km", "modes", "kv_hours")}
            zdata["tie"] = "start" if form.get("tie") == "start" else "same"
            for k in ("spp", "expected", "kv", "cutoff_pairs"):  # по времени: эквивалент балла, расчётное время, КВ, отсечки SI
                if k in form:
                    zdata[k] = str(form.get(k, "")).strip()
            if "removed_order" in form:
                zdata["removed_order"] = "count" if form.get("removed_order") == "count" else "after"

        _save_zachet(f, zz.key, update)
        return _redirect(f"{_base(f)}/results?{urlencode({'z': zz.key, 'done': 'run_stages'})}#stages")

    @app.post("/c/{cid}/results/import")
    async def results_import(request: Request, cid: str, z: str = ""):
        """Этапы и баллы из рабочей книги СЕКРЕТАРЬ_ST (лист «Протокол_группа»)."""
        f = folder(cid)
        comp = need_comp(f)
        zz = need_zachet(comp, z)
        up = (await request.form()).get("book")
        back = f"{_base(f)}/results?{urlencode({'z': zz.key})}"
        if up is None or not getattr(up, "filename", ""):
            return _redirect(_with_done(back, "run_nofile"))
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / Path(up.filename).name
            p.write_bytes(await up.read())
            try:
                from st_secretary.importers.sekretar_xls import read_group_protocol
                sheet = read_group_protocol(p)
            except ImportError:
                return _redirect(_with_done(back, "res_noxlrd"))
            except Exception:  # noqa: BLE001 — не та книга: объяснить, а не упасть
                return _redirect(_with_done(back, "run_badbook"))
        imported, notes = pr.import_group_protocol(sheet, zachet_inputs(f, comp, zz))
        _save_zachet(f, zz.key, lambda zdata: zdata.update(imported))
        return _redirect(_with_done(back, "run_imported", n=str(len(imported["stages"])),
                                    t=str(len(imported["teams"])), notes=" ".join(notes)[:900]))

    @app.post("/c/{cid}/results/si")
    async def results_si(request: Request, cid: str, z: str = ""):
        """SPORTident Reader (si_reader.csv): старт, финиш и отсечки команд по чипам."""
        f = folder(cid)
        comp = need_comp(f)
        zz = need_zachet(comp, z)
        up = (await request.form()).get("csv")
        back = f"{_base(f)}/results?{urlencode({'z': zz.key})}#points"
        if up is None or not getattr(up, "filename", ""):
            return _redirect(_with_done(back, "si_nofile"))
        try:
            cards = read_si_reader(await up.read())
        except ValueError as e:
            return _redirect(_with_done(back, "si_bad", why=str(e)))
        teams = zachet_inputs(f, comp, zz)
        out = {}
        _save_zachet(f, zz.key, lambda zdata: out.update(tr.apply_si(zdata, cards, teams)))
        return _redirect(_with_done(back, "si_done", n=str(len(cards)), t=str(out["teams"]),
                                    unknown=", ".join(out["unknown"])[:600], replaced=", ".join(out["replaced"])[:600]))

    @app.post("/c/{cid}/results/points")
    async def results_points(request: Request, cid: str, z: str = ""):
        """Баллы команд по этапам и статусы. С автосохранением — возвращает суммы, места и таблицу результатов."""
        f = folder(cid)
        comp = need_comp(f)
        zz = need_zachet(comp, z)
        form = await request.form()
        idx = sorted({int(m.group(1)) for k in form.keys() if (m := re.fullmatch(r"p-(\d+)-file", k))})

        def update(zdata):
            ids = [str(s["id"]) for s in zdata.get("stages", [])]
            teams = zdata.setdefault("teams", {})
            for i in idx:
                t = teams.setdefault(str(form.get(f"p-{i}-file")), {})
                pts = t.setdefault("points", {})
                for sid in ids:
                    v = " ".join(str(form.get(f"p-{i}-{sid}", "")).split())
                    if v:
                        pts[sid] = v
                    else:
                        pts.pop(sid, None)
                st = str(form.get(f"p-{i}-status", Status.FINISHED.value))
                t["status"] = st if st in {s.value for s in Status} else Status.FINISHED.value
                for fld in ("start", "finish", "cutoffs", "chip"):  # дисциплины по времени
                    if f"p-{i}-{fld}" in form:
                        t[fld] = " ".join(str(form.get(f"p-{i}-{fld}", "")).split())

        _save_zachet(f, zz.key, update)
        if request.headers.get("x-autosave"):
            _, zdata, run = run_ctx(f, comp, zz)
            parts = {"comp": comp, **results_parts(f, zz, zdata, run)}
            cells = {r.inp.file: {"total": pr.result_text(run, r) if run.kind == "time" else pr.points_text(r.total),
                                  "place": str(r.place) if r.place else pr.STATUS_SHORT[r.status] or "—",
                                  "bad": r.bad} for r in run.rows}
            return JSONResponse({"cells": cells, "saved": datetime.now().strftime("%H:%M:%S"),
                                 "results": templates.get_template("_results_table.html").render(**parts)})
        return _redirect(f"{_base(f)}/results?{urlencode({'z': zz.key, 'done': 'run_saved'})}#points")

    # ------------------------------------------------------------ предварительный протокол → протесты → официальный

    PROTEST_HOUR = timedelta(hours=1)  # Правила, раздел 3, п. 8.17 и 8.18

    def fingerprint(run) -> str:
        """Отпечаток результатов: по нему видно, что после публикации баллы или статусы меняли."""
        s = ";".join(f"{r.inp.file}|{r.place}|{r.total}|{r.status.value}" for r in run.rows)
        return hashlib.sha1(s.encode("utf-8")).hexdigest()[:16]

    def protocol_state(zdata: dict, run, now: datetime) -> dict:
        pub, off = zdata.get("published"), zdata.get("official")
        at = _parse_dt(pub["at"]) if pub else None
        until = at + PROTEST_HOUR if at else None
        protests = zdata.get("protests", [])
        return {"published": pub, "published_at": at, "until": until, "official": off,
                "official_at": _parse_dt(off["at"]) if off else None,
                "hour_passed": bool(until and now >= until),
                "changed": bool(pub and pub.get("fp") != fingerprint(run)),
                "changed_after_official": bool(off and off.get("fp") != fingerprint(run)),
                "open_protests": [p for p in protests if not p.get("decision")], "protests": protests,
                "now": now}

    def protocol_name(z, kind: str, at: datetime) -> str:
        key = safe_name(z.key.replace("/", "-"))
        return (f"Предварительный протокол {key} {at:%d.%m %H-%M}.xlsx" if kind == "preliminary"
                else f"Протокол результатов {key}.xlsx")

    @app.post("/c/{cid}/results/publish")
    def results_publish(cid: str, z: str = ""):
        """Предварительный протокол: время публикации — начало часа на протесты."""
        f = folder(cid)
        comp = need_comp(f)
        zz = need_zachet(comp, z)
        _, zdata, run = run_ctx(f, comp, zz)
        now = app.state.clock()
        back = f"{_base(f)}/results?{urlencode({'z': zz.key})}"
        if not any(r.place for r in run.rows):
            return _redirect(_with_done(back, "run_empty"))
        f.protocols_dir.mkdir(exist_ok=True)
        path = f.protocols_dir / protocol_name(zz, "preliminary", now)
        rp.write_protocol(comp, run, rp.PRELIMINARY, now, path, now + PROTEST_HOUR)

        def update(d):
            d["published"] = {"at": now.isoformat(timespec="minutes"), "fp": fingerprint(run), "file": path.name}
            d.pop("official", None)  # новые предварительные результаты — новый час на протесты

        _save_zachet(f, zz.key, update)
        app.state.opener(path)
        return _redirect(_with_done(back + "#protocol", "run_published", until=f"{now + PROTEST_HOUR:%H:%M}"))

    @app.post("/c/{cid}/results/protest")
    async def results_protest(request: Request, cid: str, z: str = ""):
        """Протест: главный секретарь проставляет время подачи (п. 8.17)."""
        f = folder(cid)
        comp = need_comp(f)
        zz = need_zachet(comp, z)
        form = await request.form()
        at = _parse_dt(str(form.get("at", ""))) or app.state.clock()
        text = " ".join(str(form.get("text", "")).split())
        back = f"{_base(f)}/results?{urlencode({'z': zz.key})}"
        if not text:
            return _redirect(_with_done(back + "#protocol", "run_protest_empty"))
        _, zdata, run = run_ctx(f, comp, zz)
        state = protocol_state(zdata, run, app.state.clock())
        late = bool(state["until"] and at > state["until"])

        def update(d):
            d.setdefault("protests", []).append({"id": f"p{len(d.get('protests', [])) + 1}",
                                                 "at": at.isoformat(timespec="minutes"),
                                                 "team": str(form.get("team", "")), "text": text, "late": late,
                                                 "decision": "", "note": ""})

        _save_zachet(f, zz.key, update)
        return _redirect(_with_done(back + "#protocol", "run_protest_late" if late else "run_protest"))

    @app.post("/c/{cid}/results/protest/decide")
    async def results_protest_decide(request: Request, cid: str, z: str = ""):
        f = folder(cid)
        zz = need_zachet(need_comp(f), z)
        form = await request.form()
        pid, decision = str(form.get("id", "")), str(form.get("decision", ""))

        def update(d):
            for p in d.get("protests", []):
                if p.get("id") == pid:
                    p["decision"] = decision if decision in PROTEST_DECISIONS else ""
                    p["note"] = " ".join(str(form.get("note", "")).split())
                    p["decided_at"] = app.state.clock().isoformat(timespec="minutes") if p["decision"] else ""

        _save_zachet(f, zz.key, update)
        return _redirect(f"{_base(f)}/results?{urlencode({'z': zz.key, 'done': 'run_decided'})}#protocol")

    @app.post("/c/{cid}/results/approve")
    async def results_approve(request: Request, cid: str, z: str = ""):
        """Официальный протокол (п. 8.18): после часа на протесты и решений по ним. Результаты уходят в награждение."""
        f = folder(cid)
        comp = need_comp(f)
        zz = need_zachet(comp, z)
        _, zdata, run = run_ctx(f, comp, zz)
        now = app.state.clock()
        st = protocol_state(zdata, run, now)
        back = f"{_base(f)}/results?{urlencode({'z': zz.key})}#protocol"
        if not st["published"]:
            return _redirect(_with_done(back, "run_not_published"))
        if st["changed"]:
            return _redirect(_with_done(back, "run_changed"))
        if st["open_protests"]:
            return _redirect(_with_done(back, "run_open_protests"))
        f.protocols_dir.mkdir(exist_ok=True)
        path = f.protocols_dir / protocol_name(zz, "official", now)
        try:
            rp.write_protocol(comp, run, rp.OFFICIAL, now, path)
        except PermissionError:
            return _redirect(_with_done(back, "doc_locked"))
        _save_zachet(f, zz.key, lambda d: d.update(official={"at": now.isoformat(timespec="minutes"),
                                                             "fp": fingerprint(run), "file": path.name}))
        awards = f.results_data()
        awards.setdefault("zachety", {})[zz.key] = rp.awards_rows(run, f"СТ-Секретарь, утверждён {now:%d.%m.%Y %H:%M}")
        f.save_results_data(awards)
        app.state.opener(path)
        return _redirect(_with_done(back, "run_official"))

    @app.get("/c/{cid}/results/file/{kind}")
    def results_file(cid: str, kind: str, z: str = ""):
        f = folder(cid)
        zz = need_zachet(need_comp(f), z)
        info = f.run_data().get("zachety", {}).get(zz.key, {}).get("published" if kind == "preliminary" else "official")
        p = f.protocols_dir / Path(info["file"]).name if info else None
        if p is None or not p.is_file():
            raise HTTPException(404)
        return FileResponse(p, filename=p.name, media_type=XLSX)

    # ------------------------------------------------------------ табло (Wi-Fi ноутбука)

    board_helpers = {"pt": pr.points_text, "status_label": pr.STATUS_LABEL, "FINISHED": Status.FINISHED,
                     "ck": tr.clock_text,
                     "pct": lambda x: f"{float(x):.2f}".replace(".", ",") if x is not None else ""}

    def board_on(f: CompFolder) -> bool:
        return bool(f.run_data().get("board", {}).get("on"))

    def board_list() -> list[dict]:
        out = []
        for f in store.all():
            if board_on(f):
                try:
                    comp = f.load()
                except Exception:  # noqa: BLE001 — повреждённая карточка: на табло её нет
                    continue
                out.append({"id": f.id, "title": comp.title, "dates": comp.dates_text})
        return out

    def board_data(cid: str, preview: bool = False) -> dict | None:
        """Что видно на табло: результаты зачётов с подписью — текущие, предварительные или официальные."""
        f = store.get(cid)
        if f is None or not (preview or board_on(f)):
            return None
        try:
            comp = f.load()
        except Exception:  # noqa: BLE001 — повреждённая карточка: табло без неё
            return None
        now = app.state.clock()
        blocks = []
        for z in comp.zachety:
            _, zdata, run = run_ctx(f, comp, z)
            if not run.stages:
                continue
            st = protocol_state(zdata, run, now)
            if st["official"] and not st["changed_after_official"]:
                label, final = f"Официальные результаты — утверждены {st['official_at']:%d.%m в %H:%M}", True
            elif st["published"] and not st["changed"]:
                label, final = f"Предварительные результаты — опубликованы в {st['published_at']:%H:%M}", False
                if not st["hour_passed"]:
                    label += f", протесты принимаются до {st['until']:%H:%M}"
            else:
                label, final = f"Текущие результаты на {now:%H:%M} — не окончательные", False
            blocks.append({"z": z, "run": run, "label": label, "final": final, **board_helpers,
                           "res": lambda r, run=run: pr.result_text(run, r)})
        return {"title": comp.title, "dates": comp.dates_text, "place": comp.place, "zachety": blocks}

    # ------------------------------------------------------------ телефоны судей этапов

    def judge_link(token: str):
        """Этап по коду ссылки: (папка, карточка, зачёт, этап) или None."""
        for f in store.all():
            link = js.tokens(f.run_data()).get(token)
            if not link:
                continue
            try:
                comp = f.load()
            except Exception:  # noqa: BLE001 — карточка не читается: ссылка не работает
                return None
            z = next((x for x in comp.zachety if x.key == link.get("z")), None)
            if z is None:
                return None
            zdata = f.run_data().get("zachety", {}).get(z.key, {})
            stage = next((s for s in pr.stages_of(zdata) if s.id == link.get("stage")), None)
            return (f, comp, z, stage) if stage else None
        return None

    def judge_page(token: str) -> dict | None:
        found = judge_link(token)
        if found is None:
            return None
        f, comp, z, stage = found
        _, zdata, run = run_ctx(f, comp, z)
        log = zdata.get("judge", {}).get(stage.id, {})
        teams = [{"file": r.inp.file, "team": r.inp.team, "number": r.inp.number}
                 for r in sorted(run.rows, key=lambda r: r.start_order)]
        return {"title": comp.title, "zachet": z.key, "stage": {"title": stage.title, "kv": stage.kv_minutes},
                "payload": {"token": token, "sync_url": f"/j/{token}/sync", "teams": teams,
                            "stage": {"kv": stage.kv_minutes},
                            "records": {file: {**rec, "file": file} for file, rec in log.items()}}}

    def judge_receive(token: str, payload: dict) -> dict | None:
        found = judge_link(token)
        if found is None:
            return None
        f, comp, z, stage = found
        files = {t.file for t in zachet_inputs(f, comp, z)}
        records = [js.Record.from_json(r) for r in payload.get("records", [])[:500] if isinstance(r, dict)]
        device = " ".join(str(payload.get("device", "")).split())[:40] or "телефон"
        now = app.state.clock()
        out = {}
        mark = "с" if tr.is_time_discipline(z) else ""  # спелео: снятие с этапа — «с» в клетке этапа
        _save_zachet(f, z.key, lambda zdata: out.update(js.merge(zdata, stage.id, records, files, device,
                                                                 now.isoformat(timespec="seconds"), mark)))
        return {"saved": out.get("saved", []), "time": f"{now:%H:%M:%S}"}

    app.state.board = BoardServer(create_board_app(board_list, board_data, judge_page, judge_receive), host=board_host)

    @app.get("/c/{cid}/judges")
    def judges_page(request: Request, cid: str, z: str = ""):
        f = folder(cid)
        comp = need_comp(f)
        if not comp.zachety:
            return page(request, "judges.html", active="judges", zachet=None, **comp_ctx(f))
        zz = need_zachet(comp, z)
        data, zdata, run = run_ctx(f, comp, zz)
        srv = app.state.board
        urls = srv.urls() if srv.running else []
        stages = []
        for s in run.stages:
            t = js.stage_token(data, zz.key, s.id)
            link = f"{urls[0]}j/{t}" if t and urls else ""
            stages.append({"s": s, "token": t, "link": link, "sum": js.stage_summary(zdata, s.id, len(run.rows))})
        return page(request, "judges.html", active="judges", zachet=zz, zachety=comp.zachety, stages=stages,
                    running=srv.running, urls=urls, error=srv.error, zq=urlencode({"z": zz.key}), run=run,
                    **comp_ctx(f))

    @app.post("/c/{cid}/judges/link")
    async def judges_link(request: Request, cid: str, z: str = ""):
        f = folder(cid)
        zz = need_zachet(need_comp(f), z)
        form = await request.form()
        sid, do = str(form.get("stage", "")), str(form.get("do", "issue"))
        with run_lock:
            data = f.run_data()
            if sid == "*":  # ссылки всем этапам, у которых их ещё нет
                for s in data.get("zachety", {}).get(zz.key, {}).get("stages", []):
                    if not js.stage_token(data, zz.key, str(s["id"])):
                        js.issue_token(data, zz.key, str(s["id"]), app.state.clock().isoformat(timespec="minutes"))
            elif do == "revoke":
                js.revoke_token(data, zz.key, sid)
            else:
                js.issue_token(data, zz.key, sid, app.state.clock().isoformat(timespec="minutes"))
            f.save_run_data(data)
        done = "judge_revoked" if do == "revoke" else "judge_issued"
        return _redirect(f"{_base(f)}/judges?{urlencode({'z': zz.key, 'done': done})}")

    @app.post("/c/{cid}/judges/server")
    async def judges_server(request: Request, cid: str, z: str = ""):
        f = folder(cid)
        ok = app.state.board.start()
        return _redirect(f"{_base(f)}/judges?{urlencode({'z': z, 'done': 'board_started' if ok else 'board_failed'})}")

    @app.get("/c/{cid}/judges/phone/{token}")
    def judges_phone(request: Request, cid: str, token: str):
        """Страница судьи этапа на ноутбуке: посмотреть, что видит судья, или внести за судью (сел телефон)."""
        folder(cid)
        info = judge_page(token)
        if info is not None:
            info["payload"]["sync_url"] = f"{_base(folder(cid))}/judges/phone/{token}/sync"
        return templates.TemplateResponse(request, "judge.html", {"info": info}, status_code=200 if info else 404,
                                          headers={"Cache-Control": "no-store"})

    @app.post("/c/{cid}/judges/phone/{token}/sync")
    async def judges_phone_sync(request: Request, cid: str, token: str):
        folder(cid)
        try:
            payload = await request.json()
        except ValueError:
            payload = None
        if not isinstance(payload, dict):
            return JSONResponse({"ok": False, "error": "не те данные"}, status_code=400)
        res = judge_receive(token, payload)
        if res is None:
            return JSONResponse({"ok": False, "error": "ссылка больше не действует"}, status_code=404)
        return JSONResponse({"ok": True, **res})

    @app.get("/c/{cid}/judges/print")
    def judges_print(request: Request, cid: str, z: str = ""):
        """Карточки с QR-кодами этапов — распечатать и раздать судьям."""
        f = folder(cid)
        comp = need_comp(f)
        zz = need_zachet(comp, z)
        data, _, run = run_ctx(f, comp, zz)
        urls = app.state.board.urls() if app.state.board.running else []
        cards = []
        for s in run.stages:
            t = js.stage_token(data, zz.key, s.id)
            if t and urls:
                link = f"{urls[0]}j/{t}"
                cards.append({"s": s, "link": link, "qr": qr_svg(link)})
        return templates.TemplateResponse(request, "judges_print.html", {"comp": comp, "z": zz, "cards": cards,
                                                                         "running": bool(urls)})

    @app.get("/c/{cid}/board")
    def board_page(request: Request, cid: str):
        f = folder(cid)
        need_comp(f)
        srv = app.state.board
        urls = srv.urls() if srv.running else []
        return page(request, "board_admin.html", active="board", on=board_on(f), running=srv.running, urls=urls,
                    qr=qr_svg(urls[0]) if urls else "", error=srv.error, **comp_ctx(f))

    @app.post("/c/{cid}/board/toggle")
    async def board_toggle(request: Request, cid: str):
        f = folder(cid)
        on = (await request.form()).get("on") == "1"
        with run_lock:
            data = f.run_data()
            data["board"] = {"on": on}
            f.save_run_data(data)
        return _redirect(f"{_base(f)}/board?done={'board_on' if on else 'board_off'}")

    @app.post("/c/{cid}/board/server")
    async def board_server(request: Request, cid: str):
        f = folder(cid)
        do = (await request.form()).get("do")
        if do == "stop":
            app.state.board.stop()
            return _redirect(f"{_base(f)}/board?done=board_stopped")
        ok = app.state.board.start()
        return _redirect(f"{_base(f)}/board?done={'board_started' if ok else 'board_failed'}")

    @app.get("/c/{cid}/board/preview")
    def board_preview(request: Request, cid: str):
        """Как выглядит табло — на этом компьютере, даже если раздача по Wi-Fi не включена."""
        data = board_data(folder(cid).id, preview=True)
        return templates.TemplateResponse(request, "board.html", {"refresh": 30, "prefix": "", "data": data,
                                                                  "items": []})

    # ------------------------------------------------------------ судейская практика (по всем соревнованиям)

    def practice_all() -> list:
        recs = []
        for f in store.all():
            try:
                comp = f.load()
            except Exception:  # noqa: BLE001 — карточка не читается: соревнование пропускается
                continue
            recs += pt_.records_of(comp, f.id, f.results_data().get("judges", {}), f.contracts().get("extra", []))
        return pt_.judges(recs)

    @app.get("/practice")
    def practice_page(request: Request):
        people = practice_all()
        return page(request, "practice.html", people=people, competitions=len(store.all()))

    @app.get("/practice.xlsx")
    def practice_download():
        tmp = Path(tempfile.mkdtemp(prefix="st-secretary-")) / "Судейская практика.xlsx"
        pt_.write_practice(practice_all(), tmp)
        return FileResponse(tmp, filename=tmp.name, media_type=XLSX,
                            background=BackgroundTask(shutil.rmtree, tmp.parent, ignore_errors=True))

    @app.post("/practice/open")
    def practice_open():
        path = store.root / "Судейская практика.xlsx"
        store.root.mkdir(parents=True, exist_ok=True)
        try:
            pt_.write_practice(practice_all(), path)
        except PermissionError:
            return _redirect("/practice?done=doc_locked")
        app.state.opener(path)
        return _redirect("/practice?done=opened")

    # ------------------------------------------------------------ сверка документов

    def program_docs(f: CompFolder) -> list[tuple[str, Path]]:
        """Документы, которые сохранила программа (их могли поправить в Word и Excel): по итогам и договоры."""
        out = []
        for label, d in (("Документы по итогам", f.out_dir), ("Договоры и табель", store.contracts_dir(f))):
            if d.is_dir():
                out += [(f"{label}\\{p.name}", p) for p in sorted(d.iterdir())
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
