"""Телефоны судей этапов: ссылки по этапам и слияние присланного с таблицей секретаря.

Судья этапа открывает на телефоне ссылку своего этапа (QR-код с ноутбука), отмечает у команд прибытие и
убытие, вносит баллы этапа, снятие с этапа с причиной. Всё хранится на телефоне и отправляется на ноутбук,
когда телефон в Wi-Fi ноутбука; без связи — копится и уходит позже.

Слияние (merge):
- присланное судьёй записывается в журнал этапа — что, когда, с какого телефона;
- баллы попадают в таблицу секретаря, если клетка пустая или в ней то, что этот этап присылал раньше;
- если секретарь уже вписал другое (например, после протеста), таблица не меняется, а на странице результатов
  появляется расхождение «судья прислал 20, в таблице 25» — решает человек;
- снятие с этапа не меняет статус команды на дистанции (в ПСР это штраф этапа), а видно секретарю.

Ссылки — по случайному коду на этап: без кода с телефона ничего не изменить; код можно отозвать.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass

from st_secretary.issues import INFO, WARNING, Issue
from st_secretary.psr_run import Stage, parse_points

_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"  # без похожих друг на друга символов (l/1, o/0)


def new_token() -> str:
    return "".join(secrets.choice(_ALPHABET) for _ in range(10))


def tokens(data: dict) -> dict[str, dict]:
    """{код: {"z": ключ зачёта, "stage": id этапа, "created": когда}} — из верхнего уровня Результаты_дистанции.json."""
    return data.get("judge_links", {})


def stage_token(data: dict, z: str, sid: str) -> str | None:
    return next((t for t, v in tokens(data).items() if v.get("z") == z and v.get("stage") == sid), None)


def issue_token(data: dict, z: str, sid: str, created: str) -> str:
    """Новая ссылка этапа; прежняя (если была) перестаёт работать."""
    links = data.setdefault("judge_links", {})
    for t in [t for t, v in links.items() if v.get("z") == z and v.get("stage") == sid]:
        del links[t]
    t = new_token()
    links[t] = {"z": z, "stage": sid, "created": created}
    return t


def revoke_token(data: dict, z: str, sid: str) -> None:
    links = data.get("judge_links", {})
    for t in [t for t, v in links.items() if v.get("z") == z and v.get("stage") == sid]:
        del links[t]


@dataclass
class Record:
    """Что судья этапа записал по одной команде."""
    file: str
    points: str = ""
    arrive: str = ""  # «14:05:30» — время прибытия на этап (часы телефона)
    leave: str = ""
    removed: bool = False  # снята с этапа
    reason: str = ""
    note: str = ""
    updated: int = 0  # когда запись изменили на телефоне, мс — для порядка присылок с одного телефона

    @classmethod
    def from_json(cls, d: dict) -> Record:
        def clip(v, n=200):  # пробелы схлопнуть, длину ограничить — с телефона может прийти что угодно
            return " ".join(str(v or "").split())[:n]

        try:
            updated = int(d.get("updated", 0))
        except (TypeError, ValueError):
            updated = 0
        return cls(clip(d.get("file"), 300), clip(d.get("points"), 20), clip(d.get("arrive"), 8), clip(d.get("leave"), 8),
                   bool(d.get("removed")), clip(d.get("reason")), clip(d.get("note"), 500), updated)


def merge(zdata: dict, sid: str, records: list[Record], known_files: set[str], device: str, received: str) -> dict:
    """Присланное с телефона → журнал этапа и таблица баллов. Возвращает {"saved": [файлы], "conflicts": n}."""
    log = zdata.setdefault("judge", {}).setdefault(sid, {})
    teams = zdata.setdefault("teams", {})
    saved, conflicts = [], 0
    for rec in records:
        if rec.file not in known_files:
            continue  # команды нет в зачёте — не записываем чужое
        prev = log.get(rec.file, {})
        if prev and prev.get("device") == device and int(prev.get("updated", 0)) > rec.updated:
            saved.append(rec.file)  # пришла более старая версия с того же телефона — уже есть новее
            continue
        pts_cell = teams.setdefault(rec.file, {}).setdefault("points", {})
        cell = str(pts_cell.get(sid, "")).strip()
        before = str(prev.get("points", "")).strip()
        log[rec.file] = {"points": rec.points, "arrive": rec.arrive, "leave": rec.leave, "removed": rec.removed,
                         "reason": rec.reason, "note": rec.note, "updated": rec.updated, "device": device,
                         "received": received}
        saved.append(rec.file)
        if rec.points:
            try:
                parse_points(rec.points)
            except ValueError:
                continue  # не число — видно в журнале, в таблицу не идёт
            if cell in ("", before):
                pts_cell[sid] = rec.points
            elif not _same(cell, rec.points):
                conflicts += 1
    return {"saved": saved, "conflicts": conflicts}


def _same(a: str, b: str) -> bool:
    try:
        return parse_points(a) == parse_points(b)
    except ValueError:
        return a.strip() == b.strip()


def judge_issues(zdata: dict, stages: list[Stage], team_names: dict[str, str]) -> list[Issue]:
    """Для страницы результатов: где присланное судьёй расходится с таблицей, кого сняли с этапа."""
    out = []
    by_id = {s.id: s for s in stages}
    teams = zdata.get("teams", {})
    for sid, log in zdata.get("judge", {}).items():
        st = by_id.get(sid)
        if st is None:
            continue
        for file, rec in log.items():
            team = team_names.get(file, file)
            cell = str(teams.get(file, {}).get("points", {}).get(sid, "")).strip()
            got = str(rec.get("points", "")).strip()
            when = str(rec.get("received", ""))[11:16]
            if got:
                try:
                    parse_points(got)
                except ValueError:
                    out.append(Issue(WARNING, f"«{team}», {st.title}: судья этапа прислал «{got}» — не число",
                                     team=team))
                    continue
                if cell and not _same(cell, got):
                    out.append(Issue(WARNING, f"«{team}», {st.title}: судья этапа прислал {got} ({when}), в таблице "
                                              f"{cell} — проверьте", team=team))
            if rec.get("removed"):
                why = f": {rec['reason']}" if rec.get("reason") else ""
                out.append(Issue(INFO, f"«{team}», {st.title}: судья этапа отметил снятие с этапа{why}", team=team))
    return out


def stage_summary(zdata: dict, sid: str, total_teams: int) -> dict:
    """Сколько команд судья этапа уже прислал и когда последний раз."""
    log = zdata.get("judge", {}).get(sid, {})
    last = max((str(r.get("received", "")) for r in log.values()), default="")
    return {"teams": sum(1 for r in log.values() if r.get("points") or r.get("arrive") or r.get("removed")),
            "of": total_teams, "last": last}


def from_phone(zdata: dict, sid: str, file: str) -> bool:
    """Клетка таблицы — из телефона судьи (то же значение, что в журнале этапа)."""
    rec = zdata.get("judge", {}).get(sid, {}).get(file)
    cell = str(zdata.get("teams", {}).get(file, {}).get("points", {}).get(sid, "")).strip()
    return bool(rec and cell and _same(cell, str(rec.get("points", ""))))
