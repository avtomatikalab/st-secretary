"""Страницы карточки соревнования: подписи, примеры, подсказки, ссылки (Правки, п. 56–65). Данные — выдуманные."""

from dataclasses import replace

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient
from test_web import base

from st_secretary.web.app import create_app


@pytest.fixture
def client(tmp_path):
    return TestClient(create_app(tmp_path / "данные", opener=lambda p: None, docs_dir=tmp_path / "документы",
                                 board_host="127.0.0.1"))


def test_one_class_label_is_plain(client, psr_card):
    """П. 56: галочка «один класс» подписана понятно — и в редактировании, и в просмотре карточки."""
    f = client.app.state.store.create(replace(psr_card, one_class=True))
    edit = client.get(base(f) + "/card/edit").text
    assert "Один человек — только в одном классе" in edit and "нельзя сразу во 2 и 3 кл." in edit
    assert "так бывает в Положении или ИБ" in edit
    view = client.get(base(f) + "/card").text
    assert "Один человек — только в одном классе" in view and "проверка заявок покажет ошибку" in view
