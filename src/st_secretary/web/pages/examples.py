"""Учебный режим (Правки, п. 53): «Заполнить примером» у форм и заглушки документов команды.

Галочка «Учебный режим» в шапке — в браузере (app.js, `learn`); здесь — только примеры. Пример — выдуманные данные,
как в учебном соревновании (training.py): форма заполняется в браузере, а сохраняется, только когда человек нажмёт
«Сохранить». Ответ примера: {"fields": {имя поля: значение}, "rows": {id блока строк: сколько строк нужно},
"clear": [префиксы строк, которые очистить]} — его читает app.js (`fillExample`).
"""

from __future__ import annotations

import random

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

from st_secretary import training
from st_secretary.stub_docs import make_team
from st_secretary.web.common import redirect, with_done
from st_secretary.web.forms import card_to_form
from st_secretary.web.shared import back_to, need_comp, need_file, team_url


def card_example(today) -> dict:
    """Карточка учебного соревнования — для «Нового соревнования» и «Редактирования карточки»."""
    form = card_to_form(training.card(today))
    fields = dict(form["main"])
    for i, o in enumerate(form["officials"]):
        fields |= {f"g-{i}-{k}": v for k, v in o.items() if k != "own"}
    for i, z in enumerate(form["zachety"]):
        fields |= {f"z-{i}-{k}": v for k, v in z.items() if k != "zid"}  # zid — какой зачёт был, его не трогаем
    return {"fields": fields, "rows": {"gsk-rows": len(form["officials"]), "z-rows": len(form["zachety"])},
            "clear": ["g", "z"]}


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
    def example_card():
        return JSONResponse(card_example(app.state.clock().date()))

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
