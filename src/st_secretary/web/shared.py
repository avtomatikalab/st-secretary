"""Логика, нужная нескольким страницам (Правки, п. 39, шаг 3): функции от хранилища, папки и карточки.

Раньше она жила внутри модулей страниц и передавалась через `cx` с обёртками; теперь страницы импортируют её явно.
Функциям, которым нужно хранилище, оно передаётся первым аргументом (`store`), записи в «Результаты_дистанции.json»
— замок (`save_zachet(run_lock, …)`): секретарь и телефоны судей пишут в один файл по очереди.
"""

from __future__ import annotations

import hashlib
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import urlencode

from fastapi import Request
from starlette.exceptions import HTTPException

from st_secretary import commission as cm
from st_secretary import equipment as eq
from st_secretary import festival as fv
from st_secretary import judge_sync as js
from st_secretary import psr_run as pr
from st_secretary import start_list as sl
from st_secretary import time_run as tr
from st_secretary import units as un
from st_secretary.importers.card_xlsx import CardError
from st_secretary.issues import ERROR
from st_secretary.reference import norm_edition
from st_secretary.web.board import scheme_response  # noqa: F401 — страницы берут отсюда
from st_secretary.web.common import base_url, fix_url, key_of, parse_dt
from st_secretary.web.review import DONE
from st_secretary.web.store import IMAGE_TYPES, CompFolder, Store


class GoFix(HTTPException):
    """«Сначала сделайте …» — страница «Не получилось» с кнопкой туда, где это делают (Правки, п. 55): главная кнопка
    ведёт к форме (например, к первому полю с ошибкой в карточке), «На главную» — вторая."""

    def __init__(self, status_code: int, detail: str, url: str, label: str):
        super().__init__(status_code, detail)
        self.url, self.label = url, label


PROTEST_HOUR = timedelta(hours=1)  # Правила, раздел 3, п. 8.17 и 8.18: час на протесты после публикации

# ------------------------------------------------------------------ карточка, заявка, зачёт


def need_comp(f: CompFolder):
    """Карточка соревнования без ошибок — иначе 409 с объяснением (заявки не с чем сверять) и кнопкой «Открыть
    карточку» — к первому полю с ошибкой (п. 55)."""
    card = f"{base_url(f)}/card/edit"
    try:
        comp = f.load()
    except CardError:
        raise GoFix(409, "Карточка соревнования заполнена с ошибками — откройте её и исправьте.", card,
                    "Открыть карточку") from None
    errors = [i for i in comp.check() if i.severity == ERROR]
    if errors:
        raise GoFix(409, "Сначала исправьте ошибки в карточке соревнования: без неё заявку не с чем сверять.",
                    fix_url(base_url(f), errors[0]) or card, "Открыть карточку")
    return comp


def need_preapps(f: CompFolder) -> None:
    """Есть хоть одна заявка — иначе 409 с кнопкой «Добавить заявки» (п. 55)."""
    if not f.preapp_files():
        raise GoFix(409, "Пока нет ни одной заявки — добавьте файлы на странице «Предварительные заявки».",
                    f"{base_url(f)}/preapps", "Добавить заявки")


def need_file(f: CompFolder, name: str) -> Path:
    """Файл заявки соревнования по имени — иначе 404."""
    path = f.preapp_path(name)
    if path is None:
        raise HTTPException(404)
    return path


def need_zachet(comp, key: str):
    """Зачёт по коду (пусто — первый) — иначе 404."""
    z = next((x for x in comp.zachety if x.key == key), None) if key else (comp.zachety[0] if comp.zachety else None)
    if z is None:
        raise HTTPException(404)
    return z


def team_url(f: CompFolder, name: str, **q) -> str:
    """Страница заявки команды."""
    return f"{base_url(f)}/preapps/team?{urlencode({'file': name, **q})}"


def back_to(request: Request, f: CompFolder, sent: str, default: str) -> str:
    """Вернуться туда, откуда нажали кнопку (только внутри этого соревнования)."""
    return sent if sent.startswith(base_url(f) + "/") else default


# ------------------------------------------------------------------ комиссия по допуску


def commission(store: Store, f: CompFolder, comp):
    """Отметки комиссии и состояние допуска по каждой заявке (в порядке списка заявок). На фестивале с одним
    взносом за фестиваль взноса у команд соревнования нет — он в ведомости фестиваля."""
    result, reviews = store.review(f, comp)
    data = f.admission()
    files = [p.name for p in f.preapp_files()]
    gear = eq.admission_problems(eq.evaluate(result, files, f.equipment(), key_of), f.equipment())
    fest = f.festival()
    teams = cm.evaluate(result, files, comp, data, gear, {k: r.status == DONE for k, r in reviews.items()},
                        outside=festival_marks(store, fest, f.id) if fest else None,
                        doctor_files=cm.doctor_files(result, data))
    if fv.mode(fest, "fee") == "festival":
        for t in teams:
            t.fee_due = t.fee_paid = 0
    else:
        cm.apply_delegation_fees(teams, data)  # делегация платит одной строкой — оплата по её командам
    return data, teams


def festival_marks(store: Store, fest: dict, cid: str) -> dict[str, dict[str, tuple[str, str]]]:
    """Документы людей, отмеченные в других соревнованиях фестиваля: {человек: {документ: (команда,
    соревнование)}} — человек проверяется один раз (Правки, п. 20)."""
    out: dict[str, dict[str, tuple[str, str]]] = {}
    for m in fest["members"]:
        g = store.get(m) if m != cid else None
        try:
            comp = g.load() if g else None
        except Exception:  # noqa: BLE001 — карточка не читается
            comp = None
        if comp is None or any(i.severity == ERROR for i in comp.check()):
            continue
        result, _ = store.review(g, comp)
        data = g.admission()
        marks = cm.own_marks(result, [p.name for p in g.preapp_files()], data, cm.doctor_files(result, data))
        for pid, docs in marks.items():
            for k, team in docs.items():
                out.setdefault(pid, {}).setdefault(k, (team, comp.title))
    return out


def festival_members(store: Store, fest: dict) -> list[tuple]:
    """Соревнования фестиваля с комиссией: [(папка, карточка, отметки комиссии, команды)]; соревнования с
    ошибками в карточке пропускаются."""
    out = []
    for m in fest["members"]:
        g = store.get(m)
        try:
            comp = g.load() if g else None
        except Exception:  # noqa: BLE001 — карточка не читается
            comp = None
        if comp is None or any(i.severity == ERROR for i in comp.check()):
            continue
        out.append((g, comp, *commission(store, g, comp)))
    return out


def adm_totals(teams: list) -> dict:
    """Плитки комиссии: команды и участники по решениям, взносы, допущенные без проверки секретаря."""
    people = [p for t in teams for p in t.persons]
    return {"teams": len(teams), "teams_ok": sum(t.status == cm.ADMITTED for t in teams),
            "teams_by": Counter(t.status for t in teams),
            "people": len(people), "people_ok": sum(p.status == cm.ADMITTED for p in people),
            "people_wait": sum(p.status == cm.PENDING for p in people),
            "people_no": sum(p.status == cm.REJECTED for p in people),
            "fee_due": sum(t.fee_due for t in teams), "fee_paid": sum(t.fee_paid for t in teams),
            "no_check": cm.without_check(teams)}


def doc_list(store: Store, f: CompFolder, file: str, t=None) -> list[dict]:
    """Сканы команды и — если есть — её участников из папки делегации (Правки, п. 20)."""
    out = []
    for p in store.team_docs(f, file):
        ext = p.suffix.lower()
        kind = "image" if ext in IMAGE_TYPES else "pdf" if ext == ".pdf" else "other"
        out.append({"name": p.name, "label": p.stem, "kind": kind,
                    "url": f"{base_url(f)}/docs/view?{urlencode({'file': file, 'name': p.name})}"})
    if t is not None and t.team:
        for fio, p in store.person_docs(f, cm.delegation_title(t.team), [x.entry for x in t.persons]):
            ext = p.suffix.lower()
            kind = "image" if ext in IMAGE_TYPES else "pdf" if ext == ".pdf" else "other"
            out.append({"name": p.name, "label": f"{fio}: {p.stem}", "kind": kind,
                        "url": f"{base_url(f)}/docs/person?{urlencode({'file': file, 'fio': fio, 'name': p.name})}"})
    return out


# ------------------------------------------------------------------ зачёт: состав, расчёт, запись


def zachet_inputs(store: Store, f: CompFolder, comp, z) -> list:
    """Кто выступает в зачёте (команды, связки или спортсмены — по дисциплине): из заявок, номера и допуск — из
    комиссии по допуску; не допущенные участники не в составе."""
    if not f.preapp_files():
        return []
    _, teams = commission(store, f, comp)
    return un.zachet_units(teams, z, z.rank_format)


def run_ctx(store: Store, f: CompFolder, comp, z) -> tuple[dict, dict, object]:
    """(все данные дистанции, данные зачёта, расчёт зачёта) — ПСР или по времени, с замечаниями телефонов судей."""
    data = f.run_data()
    zdata = data.get("zachety", {}).get(z.key, {})
    compute = tr.compute if tr.is_time_discipline(z) else pr.compute  # спелео, пешеходные — по времени
    run = compute(comp, z, zdata, zachet_inputs(store, f, comp, z))
    run.issues += js.judge_issues(zdata, run.stages, {r.inp.file: r.inp.team for r in run.rows})
    return data, zdata, run


def save_zachet(run_lock, f: CompFolder, key: str, update) -> None:
    """Изменить данные зачёта: update(zdata) — под замком (секретарь и телефоны судей пишут в один файл)."""
    with run_lock:
        data = f.run_data()
        zdata = data.setdefault("zachety", {}).setdefault(key, {})
        update(zdata)
        f.save_run_data(data)


def fingerprint(run) -> str:
    """Отпечаток результатов: по нему видно, что после публикации баллы или статусы меняли."""
    s = ";".join(f"{r.inp.file}|{r.place}|{r.total}|{r.status.value}" for r in run.rows)
    return hashlib.sha1(s.encode("utf-8")).hexdigest()[:16]


def protocol_state(zdata: dict, run, now: datetime) -> dict:
    """Публикация и утверждение протокола: когда, час на протесты, менялось ли после, открытые протесты."""
    pub, off = zdata.get("published"), zdata.get("official")
    at = parse_dt(pub["at"]) if pub else None
    until = at + PROTEST_HOUR if at else None
    protests = zdata.get("protests", [])
    return {"published": pub, "published_at": at, "until": until, "official": off,
            "official_at": parse_dt(off["at"]) if off else None,
            "hour_passed": bool(until and now >= until),
            "changed": bool(pub and pub.get("fp") != fingerprint(run)),
            "changed_after_official": bool(off and off.get("fp") != fingerprint(run)),
            "open_protests": [p for p in protests if not p.get("decision")], "protests": protests,
            "now": now}


# ------------------------------------------------------------------ стартовый протокол


def start_default(f: CompFolder, comp) -> tuple[date, str]:
    """День и время старта по умолчанию — «Начало соревнований» у комиссии по допуску или первый день."""
    adm = parse_dt(cm.settings(f.admission())["start_at"])
    if adm is None:
        return comp.date_from, ""
    return adm.date(), f"{adm:%H:%M}" if adm.hour or adm.minute else ""


def start_ctx(store: Store, f: CompFolder, comp, z, teams: list | None = None):
    """(данные зачёта, команды, ранги составов, стартовый протокол)."""
    zdata = f.run_data().get("zachety", {}).get(z.key, {})
    teams = zachet_inputs(store, f, comp, z) if teams is None else teams
    try:
        norms = norm_edition(comp.norms_edition)
    except KeyError:
        norms = None
    fmt = z.rank_format
    ranks = {t.file: sl.team_rank(t.members, fmt, norms) for t in teams}
    return zdata, teams, ranks, sl.build(z, zdata, teams, ranks, start_default(f, comp)[0])
