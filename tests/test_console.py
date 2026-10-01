"""Окно программы: версия, итог проверки обновлений, «обновить сейчас?» (Правки.md, п. 28). git подменяется."""

import subprocess
from types import SimpleNamespace

from st_secretary import __version__, updates
from st_secretary.console import Console, commits_word, git_line, release_line, version_line
from st_secretary.updates import Commit, GitNews, Release


def test_version_line_portable_and_source():
    assert version_line(True, platform="darwin") == f"Версия {__version__} (бета, переносная, macOS)"
    head = Commit("9d547bf", "30.09.2026 23:56", "Убытие раньше прибытия")
    assert version_line(False, head, True) == (f"Версия {__version__} (бета) из исходников — коммит 9d547bf от "
                                               "30.09.2026 23:56: «Убытие раньше прибытия»")
    assert version_line(False, None, True) == f"Версия {__version__} (бета) из исходников"


def test_update_result_lines():
    assert release_line(None).startswith("Проверить обновления не удалось — нет интернета")
    assert release_line(Release("99.0.0", "x"), "0.2.0") == ("Вышла новая версия 99.0.0 (у вас 0.2.0). Что нового — "
                                                             "на главной странице.")
    assert release_line(Release("0.2.0", "x"), "0.2.0") == "Это последняя версия."
    assert git_line(GitNews(0, None, "origin/main")) == "Код совпадает с GitHub — это последняя версия."
    news = GitNews(5, Commit("abc", "30.09.2026 23:56", "Очередь"), "origin/main")
    assert git_line(news) == "На GitHub новее: 5 коммитов, последний — «Очередь» (30.09.2026 23:56)."
    assert [commits_word(n) for n in (1, 2, 5, 11, 21, 22)] == ["коммит", "коммита", "коммитов", "коммитов", "коммит",
                                                                  "коммита"]


class Git:
    """Подменённый git: ответы по первому слову команды."""

    def __init__(self, **answers):
        self.answers, self.calls = answers, []

    def __call__(self, cmd, **kw):
        self.calls.append(cmd)
        word = cmd[3] if cmd[0] == "git" else cmd[1]
        code, out = self.answers.get(word, (0, ""))
        return subprocess.CompletedProcess(cmd, code, out, "ошибка" if code else "")


def test_git_update_only_fast_forward(tmp_path):
    dirty = Git(status=(0, " M src/app.py"))
    ok, msg = updates.git_update(tmp_path, dirty)
    assert not ok and "несохранённые изменения" in msg and not any("merge" in c for c in dirty.calls)
    diverged = Git(**{"rev-parse": (1, ""), "merge": (1, "")})
    ok, msg = updates.git_update(tmp_path, diverged)
    assert not ok and "разошлась" in msg
    assert ["git", "-C", str(tmp_path), "merge", "--ff-only", "origin/main"] in diverged.calls  # нет upstream
    good = Git(**{"rev-parse": (0, "origin/main"), "log": (0, "1a2b3c4\x1f01.10.2026 10:00\x1fОчередь")})
    ok, msg = updates.git_update(tmp_path, good, uv="uv")
    assert ok and "1a2b3c4" in msg and ["uv", "sync"] in good.calls


def test_git_check_offline_and_behind(tmp_path):
    assert updates.git_check(tmp_path, Git(fetch=(1, ""))) is None  # нет интернета
    news = updates.git_check(tmp_path, Git(**{"rev-parse": (0, "origin/main"), "rev-list": (0, "3"),
                                              "log": (0, "abc\x1f01.10.2026 10:00\x1fЖурнал")}))
    assert news.behind == 3 and news.last.subject == "Журнал"


def app_stub(**kw):
    return SimpleNamespace(state=SimpleNamespace(update=None, update_state="off", installer=None, restart=False,
                                                 restart_source=False, **kw))


def test_console_offers_git_update_and_restarts(tmp_path, monkeypatch):
    monkeypatch.setattr(updates, "latest", lambda: Release("0.0.1", "x"))
    monkeypatch.setattr(updates, "git_check", lambda root, run=None: GitNews(2, Commit("abc", "01.10", "П. 28"), "origin/main"))
    monkeypatch.setattr(updates, "git_update", lambda root, run=None, uv=None: (True, "обновлено до коммита abc"))
    out, stopped = [], []
    app = app_stub()
    c = Console(app, lambda: stopped.append(1), source=tmp_path, out=out.append, ask=lambda: "д", interactive=True)
    what = c.check_once()
    assert what == "git" and out == ["На GitHub новее: 2 коммита, последний — «П. 28» (01.10)."]
    assert c.check_once() == "" and len(out) == 1  # о тех же коммитах второй раз не говорим
    c.update(what)
    assert app.state.restart_source and stopped == [1] and out[-1] == "Готово: обновлено до коммита abc"


def test_console_first_check_says_latest_once(monkeypatch):
    monkeypatch.setattr(updates, "latest", lambda: Release(__version__, "x"))
    out = []
    c = Console(app_stub(), lambda: None, source=None, portable=True, out=out.append, interactive=False)
    assert c.check_once() == "" and c.check_once() == ""
    assert out == ["Это последняя версия."]  # раз в 4 часа не повторяется
    monkeypatch.setattr(updates, "latest", lambda: None)
    c2 = Console(app_stub(), lambda: None, source=None, portable=True, out=out.append, interactive=False)
    c2.check_once()
    assert out[-1].startswith("Проверить обновления не удалось — нет интернета")
    off = Console(app_stub(), lambda: None, enabled=False, source=None, portable=True, out=out.append)
    assert off.head_lines()[1] == "Проверка обновлений выключена."


def test_stage_mark_beside_version_not_in_it():
    """Правки, п. 42: пометка «бета» — отдельно от номера: номер понимает проверка обновлений (pre-release с «-beta»
    обновление не увидит), пометку видит человек — в окне программы, подвале страниц, журнале."""
    from st_secretary import STAGE, version_label
    from st_secretary.updates import parse_version

    assert parse_version(__version__) is not None and "бета" not in __version__
    assert STAGE == "бета" and version_label() == f"{__version__} бета"
    assert version_line(True, platform="win32") == f"Версия {__version__} (бета, переносная, Windows)"


def test_stage_mark_in_page_footer(tmp_path):
    import pytest

    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from st_secretary.web.app import create_app

    client = TestClient(create_app(tmp_path / "данные", opener=lambda p: None, docs_dir=tmp_path / "документы",
                                   board_host="127.0.0.1"))
    assert f"СТ-Секретарь {__version__} бета · автор" in client.get("/").text
    assert client.get("/health").json()["version"] == __version__  # для программ — номер без пометки
