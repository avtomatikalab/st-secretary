"""Учебный режим (Правки, п. 53): «Заполнить примером» у форм и заглушки документов команды.

Галочка «Учебный режим» в шапке — в браузере (app.js, `learn`); здесь — только примеры. Пример — выдуманные данные,
как в учебном соревновании (training.py): форма заполняется в браузере, а сохраняется, только когда человек нажмёт
«Сохранить». Ответ примера: {"fields": {имя поля: значение}, "rows": {id блока строк: сколько строк нужно},
"clear": [префиксы строк, которые очистить], "append": строка, которую добавить к уже введённым (п. 57, зачёт)} —
его читает app.js (`fillExample`, `appendExample`). У карточки примеры и по разделам (?part=gsk, ?part=zachet).
"""

from __future__ import annotations

import random
from dataclasses import replace

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

from st_secretary import training
from st_secretary.stub_docs import make_team
from st_secretary.web.common import redirect, with_done
from st_secretary.web.forms import card_to_form
from st_secretary.web.shared import back_to, need_comp, need_file, team_url


def card_example(today, part: str = "") -> dict:
    """Карточка учебного соревнования — для «Нового соревнования» и «Редактирования карточки». part — один раздел
    (п. 57): "gsk" — выдуманные судьи на стандартные должности; "zachet" — добавить учебный зачёт (zachet_example)."""
    if part == "zachet":
        return zachet_example(today)
    form = card_to_form(training.card(today))
    gsk = {}
    for i, o in enumerate(form["officials"]):
        gsk |= {f"g-{i}-{k}": v for k, v in o.items() if k != "own"}
    if part == "gsk":
        return {"fields": gsk, "rows": {"gsk-rows": len(form["officials"])}, "clear": ["g"]}
    fields = dict(form["main"]) | gsk | {"learn_full": "1"}  # «Новое соревнование» — с ГСК и зачётами (п. 57)
    for i, z in enumerate(form["zachety"]):
        fields |= {f"z-{i}-{k}": v for k, v in z.items() if k != "zid"}  # zid — какой зачёт был, его не трогаем
    return {"fields": fields, "rows": {"gsk-rows": len(form["officials"]), "z-rows": len(form["zachety"])},
            "clear": ["g", "z"]}


def zachet_example(today) -> dict:
    """«Заполнить примером» в «Зачётах» (п. 57): учебный зачёт добавляется к тем, что в форме, — первый из списка,
    которого там ещё нет (группа_класс); существующие не трогаются. Выбирает app.js (`append`): он видит форму."""
    psr, speleo = training.card(today).zachety
    zz = [psr, speleo, replace(psr, distance_class=4), replace(speleo, distance_class=1),
          replace(psr, group="МУЖЧИНЫ", min_women=0), replace(speleo, group="ЖЕНЩИНЫ", min_men=0, min_women=1),
          replace(psr, distance_class=5), replace(psr, distance_class=6)]
    rows = [{k: v for k, v in z.items() if k != "zid"}
            for z in card_to_form(replace(training.card(today), zachety=zz))["zachety"]]
    return {"fields": {}, "append": {"box": "z-rows", "prefix": "z", "key": ["group", "distance_class"],
                                     "rows": rows, "none": "Все учебные зачёты уже есть в карточке."}}


def preapp_example(comp, taken: set[str]) -> dict:
    """Заявка команды под зачёт этой карточки — для формы заявки."""
    head, rows = training.example_team(comp, taken, random.randrange(10**6))
    fields = {f"h-{k}": v for k, v in head.items()}
    for i, r in enumerate(rows):
        fields |= {f"p-{i}-{k}": v for k, v in r.items()}
    return {"fields": fields, "rows": {"people-rows": len(rows)}, "clear": ["p"]}


def register(app, cx) -> None:
    """Примеры для форм (/example/card, /c/{cid}/example/…) и заглушки документов (/c/{cid}/docs/stubs)."""
    folder = cx.folder
    store = cx.store

    @app.get("/example/card")
    def example_card(part: str = ""):
        return JSONResponse(card_example(app.state.clock().date(), part))

    @app.get("/c/{cid}/example/preapp")
    def example_preapp(cid: str):
        f = folder(cid)
        comp = need_comp(f)
        taken = {t.team for t in store.review(f, comp)[0].teams} if f.preapp_files() else set()
        return JSONResponse(preapp_example(comp, taken))

    @app.get("/c/{cid}/example/person")
    def example_person(cid: str):
        folder(cid)
        return JSONResponse({"fields": training.example_person(random.randrange(10**6)), "rows": {}, "clear": []})

    @app.post("/c/{cid}/docs/stubs")
    async def docs_stubs(request: Request, cid: str):
        """Заглушки документов команды («ОБРАЗЕЦ — НЕ ДОКУМЕНТ») — проверить комиссию без настоящих сканов."""
        f = folder(cid)
        comp = need_comp(f)
        form = await request.form()
        path = need_file(f, str(form.get("file", "")))
        team = next((t for t in store.review(f, comp)[0].teams if t.source == path.name), None)
        if team is None:
            raise HTTPException(409, "Заявку не удалось прочитать — заглушки не к кому привязать.")
        made = make_team(store.team_docs_dir(f, path.name), team, comp)
        back = back_to(None, f, str(form.get("back", "")), team_url(f, path.name) + "#docs")
        return redirect(with_done(back, "docs_stubs", made=str(made)))
