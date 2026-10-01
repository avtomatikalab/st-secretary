"""Фестиваль — несколько соревнований за один выезд (решение 039, Правки, п. 19).

Каждое соревнование остаётся своей папкой; фестиваль — запись в «Фестивали.json» в папке «данные»:

    {"id", "title", "members": [папки соревнований],
     "officials": [{role, fio, category, territory}],   ГСК фестиваля — общая для всех его соревнований
     "own_gsk": {папка: [должности]},                   у соревнования своя замена этих должностей
     "modes": {"contracts", "fee", "numbers"},          режимы (MODES)
     "brigade": [{fio, role, category}],                судейская бригада сверх ГСК — общая
     "contracts": {...},                                договоры и табель, если «один на весь фестиваль»
     "fee": {"amount", "per"}, "fees": {команда: {paid, method}},   взнос, если «один за фестиваль»
     "start_break": мин}                                перерыв между стартами участника (Правки, п. 25.3)

ГСК: в карточке каждого соревнования лежит действующий состав — ГСК фестиваля, а у должностей, отмеченных «своя»,
свой человек. Поэтому протоколы, подписи и копия соревнования работают как раньше, а после разъединения у
соревнования остаётся полная ГСК.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date

from st_secretary.competition import Competition, Official
from st_secretary.textclean import name_key

MODES = {
    "contracts": {"each": "по каждому соревнованию — свой договор и табель",
                  "festival": "один договор и один табель на весь фестиваль"},
    "fee": {"each": "по каждому соревнованию — как в карточках (взнос за зачёт)",
            "festival": "один взнос за фестиваль — ведомость на странице фестиваля"},
    "numbers": {"festival": "общие на фестиваль — у команды один номер во всех соревнованиях",
                "each": "свои в каждом соревновании"},
}
DEFAULT_MODES = {"contracts": "each", "fee": "each", "numbers": "festival"}
FEE_PER = ("участника", "команду")


def modes(fest: dict | None) -> dict[str, str]:
    m = (fest or {}).get("modes", {})
    return {k: m.get(k) if m.get(k) in MODES[k] else DEFAULT_MODES[k] for k in MODES}


def mode(fest: dict | None, kind: str) -> str:
    """Режим фестиваля; не в фестивале — «each» (как у отдельного соревнования)."""
    return modes(fest)[kind] if fest else "each"


# ------------------------------------------------------------------ ГСК фестиваля


def officials(fest: dict) -> list[Official]:
    return [Official(str(o.get("role", "")), str(o.get("fio", "")), str(o.get("category", "")),
                     str(o.get("territory", ""))) for o in fest.get("officials", []) if o.get("fio")]


def official_dict(o: Official) -> dict:
    return {"role": o.role, "fio": o.fio, "category": o.category, "territory": o.territory}


def own_roles(fest: dict, cid: str) -> list[str]:
    return list(fest.get("own_gsk", {}).get(cid, []))


def effective(fest: dict, cid: str, card: list[Official]) -> list[Official]:
    """Действующая ГСК соревнования: ГСК фестиваля, должности «своя» — из карточки соревнования (на своих местах),
    свои должности, которых у фестиваля нет, — в конце."""
    own = set(own_roles(fest, cid))
    mine = [o for o in card if o.role in own]
    out, used = [], set()
    for o in officials(fest):
        if o.role in own:
            if o.role not in used:
                out += [x for x in mine if x.role == o.role]
                used.add(o.role)
        else:
            out.append(o)
    return out + [x for x in mine if x.role not in used]


def differing_roles(fest_officials: list[Official], card: list[Official]) -> list[str]:
    """Должности, где у соревнования не тот человек, что у фестиваля (и своих должностей у фестиваля нет):
    при объединении они становятся «своими» — ничего не теряется."""
    def by_role(xs):
        out: dict[str, list] = {}
        for o in xs:
            out.setdefault(o.role, []).append((o.fio, o.category, o.territory))
        return out

    a, b = by_role(fest_officials), by_role(card)
    return [r for r in b if a.get(r) != b[r]]


# ------------------------------------------------------------------ один договор и табель на фестиваль


def joint_comp(fest: dict, comps: list[Competition]) -> Competition:
    """Соревнование «весь фестиваль» — для общего договора и табеля: даты от первого до последнего дня, ГСК
    фестиваля и свои замены, все зачёты (дисциплины). В договоре: «Фестиваля «…» (Чемпионат…; Кубок…)»."""
    comps = sorted(comps, key=lambda c: c.date_from)
    first = comps[0]
    title = " ".join(str(fest.get("title", "")).split()) or "Фестиваль"
    if not title.lower().startswith(("фестиваль", "слёт", "слет", "спартакиада", "соревнования")):
        title = f"Фестиваль «{title}»"
    title += f" ({'; '.join(c.title for c in comps)})"
    people, seen = [], set()
    for o in officials(fest) + [o for c in comps for o in c.officials]:
        k = name_key(o.fio)
        if k not in seen:
            seen.add(k)
            people.append(o)
    places = list(dict.fromkeys(c.place for c in comps if c.place))
    return replace(first, title=title, date_from=min(c.date_from for c in comps),
                   date_to=max(c.date_to for c in comps), place="; ".join(places), officials=people,
                   zachety=[z for c in comps for z in c.zachety])


def merge_contracts(datas: list[dict]) -> dict:
    """Договоры и табель соревнований → один на фестиваль (режим переключили): ставки, заказчик, начисления — у
    кого заданы первыми; период — от самого раннего до самого позднего; дни людей — объединяются."""
    out: dict = {"rates": {}, "people": {}}
    period = []
    for d in datas:
        for k, v in d.get("rates", {}).items():
            out["rates"].setdefault(k, v)
        if d.get("customer") and not out.get("customer"):
            out["customer"] = d["customer"]
        if "accrual" in d and "accrual" not in out:
            out["accrual"] = d["accrual"]
        p = d.get("period", {})
        period += [x for x in (p.get("from"), p.get("to")) if x]
        for key, m in d.get("people", {}).items():
            o = out["people"].setdefault(key, {})
            if "days" in m:
                o["days"] = sorted(set(o.get("days", [])) | set(m["days"]))
            o["unpaid"] = bool(o.get("unpaid")) or bool(m.get("unpaid"))
    good = sorted(x for x in period if _iso(x))
    if good:
        out["period"] = {"from": good[0], "to": good[-1]}
    return out


def _iso(s) -> bool:
    try:
        date.fromisoformat(str(s))
    except ValueError:
        return False
    return True


def union_brigade(extras: list[list[dict]]) -> list[dict]:
    """Бригада фестиваля — все добавленные на страницах договоров соревнований, без повторов (по ФИО)."""
    out, seen = [], set()
    for xs in extras:
        for x in xs:
            k = name_key(x.get("fio", ""))
            if k and k not in seen:
                seen.add(k)
                out.append(dict(x))
    return out


# ------------------------------------------------------------------ команды и стартовые номера


def team_identity(team: str, territory: str) -> str:
    """Одна команда в разных соревнованиях фестиваля: название и территория без регистра, «ё», кавычек и пробелов."""
    def norm(s: str) -> str:
        s = str(s or "").lower().replace("ё", "е")
        return " ".join("".join(ch for ch in s if ch not in "«»\"'„“”").split())

    return f"{norm(team)}|{norm(territory)}"


def ident_of(cid: str, t) -> str:
    """Команда комиссии (TeamCheck) на фестивале; нечитаемая заявка — сама по себе."""
    return team_identity(t.team.team, t.team.territory) if t.team else f"{cid}|{t.file}"


def assign_numbers(groups: list[tuple[str, list]], again: bool = False) -> dict[tuple[str, str], int]:
    """Общие стартовые номера фестиваля: groups — [(соревнование, команды комиссии)] в порядке фестиваля.
    Одна команда (название + территория) — один номер во всех соревнованиях; у кого номер уже есть, тот его и
    даёт остальным; новые — по порядку, не занятые никем. again — перенумеровать всех заново.
    Возвращает {(соревнование, файл заявки): номер} — что записать."""
    number: dict[str, int] = {}
    if not again:
        for cid, teams in groups:
            for t in teams:
                if t.number is not None:
                    number.setdefault(ident_of(cid, t), t.number)
    taken, n, out = set(number.values()), 1, {}
    for cid, teams in groups:
        for t in teams:
            k = ident_of(cid, t)
            if k not in number:
                while n in taken:
                    n += 1
                number[k] = n
                taken.add(n)
            if again or t.number is None:
                out[(cid, t.file)] = number[k]
    return out


def number_problems(groups: list[tuple[str, str, list]]) -> list[str]:
    """Общие номера: один номер у разных команд, у одной команды — разные номера. groups — [(соревнование, его
    название, команды комиссии)]."""
    by_number: dict[int, set] = {}
    by_team: dict[str, dict[int, list]] = {}
    names: dict[str, str] = {}
    for cid, title, teams in groups:
        for t in teams:
            if t.number is None:
                continue
            k = ident_of(cid, t)
            names.setdefault(k, t.title)
            by_number.setdefault(t.number, set()).add(k)
            by_team.setdefault(k, {}).setdefault(t.number, []).append(title)
    out = [f"номер {n} — у разных команд: «{'», «'.join(sorted(names[k] for k in ks))}»"
           for n, ks in sorted(by_number.items()) if len(ks) > 1]
    out += [f"у команды «{names[k]}» разные номера: " + "; ".join(f"{n} — {', '.join(ts)}" for n, ts in sorted(v.items()))
            for k, v in by_team.items() if len(v) > 1]
    return out


# ------------------------------------------------------------------ один взнос за фестиваль


def fee_rows(fest: dict, groups: list[tuple[str, str, list]]) -> list[dict]:
    """Ведомость взноса за фестиваль: по командам (название + территория) всех соревнований. За участника —
    каждый человек (ФИО + дата рождения) один раз, сколько бы соревнований и зачётов у него ни было; недопущенные
    не считаются. groups — [(соревнование, его название, команды комиссии)]."""
    from st_secretary import commission as cm

    fee = fest.get("fee") or {}
    amount = int(fee.get("amount") or 0)
    per_person = fee.get("per", FEE_PER[0]) == "участника"
    paid = fest.get("fees", {})
    rows: dict[str, dict] = {}
    for cid, title, teams in groups:
        for t in teams:
            if not t.team:
                continue
            k = ident_of(cid, t)
            r = rows.setdefault(k, {"key": k, "team": t.team.team, "territory": t.team.territory,
                                    "representative": t.team.representative, "comps": [], "people": set(),
                                    "numbers": set()})
            r["comps"].append(title)
            if t.number is not None:
                r["numbers"].add(t.number)
            r["people"] |= {cm.person_id(p.entry) for p in t.persons if p.status != cm.REJECTED}
    out = []
    for r in rows.values():
        n = len(r["people"])
        r["people"] = n
        r["number"] = ", ".join(str(x) for x in sorted(r.pop("numbers")))
        r["due"] = amount * n if per_person else (amount if n else 0)
        p = paid.get(r["key"], {})
        r["paid"] = int(p.get("paid") or 0)
        r["method"] = str(p.get("method", ""))
        r["status"] = "нет взноса" if not r["due"] else "оплачено" if r["paid"] >= r["due"] else \
            "частично" if r["paid"] else "не оплачено"
        out.append(r)
    return sorted(out, key=lambda r: (r["number"] == "", int(r["number"].split(",")[0]) if r["number"] else 0,
                                      r["team"].lower()))
