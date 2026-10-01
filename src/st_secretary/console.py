"""Окно программы (терминал): какая версия загружена, итог проверки обновлений и «обновить сейчас?».

При запуске первой строкой блока «СТ-Секретарь работает» — версия: у переносной — номер и система, из исходников
(git — так работает разработчик на двух компьютерах) — ещё и коммит. Под блоком — итог проверки обновлений:
последняя версия / вышла новая / нет интернета / проверка выключена. Если есть новее — предложение обновиться
прямо из окна («д» и Enter): переносная делает то же, что кнопка «Обновить» (резервная копия, скачивание со
сверкой sha256, перезапуск файлом запуска); из исходников — git merge --ff-only с GitHub и uv sync, затем
программа перезапускается сама (старый код с новыми шаблонами работать не должен). Ответа ждём в фоне: сервер
работает, Ctrl+C выключает, как раньше. Пока программа работает (соревнования идут днями), проверка повторяется
раз в CHECK_EVERY; вышло новое — строка в окне и сообщение на главной, обновление — только по желанию.
"""

from __future__ import annotations

import logging
import shutil
import sys
import threading
import time
from collections.abc import Callable

from st_secretary import STAGE, __version__, updates

log = logging.getLogger("st_secretary")
CHECK_EVERY = 4 * 3600  # раз в 4 часа, пока программа работает
YES = {"д", "да", "y", "yes", "l"}  # «l» — «д» в английской раскладке


def system_name(platform: str = sys.platform) -> str:
    return "Windows" if platform.startswith("win") else "macOS" if platform == "darwin" else "Linux"


def commits_word(n: int) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return "коммит"
    return "коммита" if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14) else "коммитов"


def version_line(portable: bool, head: updates.Commit | None = None, source: bool = False,
                 platform: str = sys.platform) -> str:
    if portable:  # «Версия 0.4.0 (бета, переносная, Windows)» (Правки, п. 42)
        return f"Версия {__version__} ({STAGE + ', ' if STAGE else ''}переносная, {system_name(platform)})"
    v = f"{__version__} ({STAGE})" if STAGE else __version__
    if head:
        return f"Версия {v} из исходников — коммит {head.hash} от {head.when}: «{head.subject}»"
    return f"Версия {v}" + (" из исходников" if source else "")


def release_line(rel: updates.Release | None, current: str = __version__) -> str:
    """Итог проверки выпусков на GitHub (rel=None — нет ответа: нет интернета)."""
    if rel is None:
        return "Проверить обновления не удалось — нет интернета. Работе это не мешает."
    if updates.is_newer(rel.version, current):
        return f"Вышла новая версия {rel.version} (у вас {current}). Что нового — на главной странице."
    return "Это последняя версия."


def git_line(news: updates.GitNews | None) -> str:
    if news is None:
        return "Проверить обновления не удалось — нет интернета. Работе это не мешает."
    if not news.behind:
        return "Код совпадает с GitHub — это последняя версия."
    last = f", последний — «{news.last.subject}» ({news.last.when})" if news.last else ""
    return f"На GitHub новее: {news.behind} {commits_word(news.behind)}{last}."


class Console:
    """Проверка обновлений и предложение обновиться в окне программы."""

    def __init__(self, app, shutdown: Callable[[], None], *, enabled: bool = True, source=None, portable=False,
                 out: Callable[[str], None] = print, ask: Callable[[], str] = input, interactive: bool | None = None,
                 run=None):
        self.app, self.shutdown, self.enabled = app, shutdown, enabled
        self.source = source if source is not None else (None if portable else updates.source_root())
        self.portable = portable
        self.out, self.ask, self.run = out, ask, run
        self.interactive = sys.stdin.isatty() if interactive is None else interactive
        self.ready = threading.Event()  # блок «работает» напечатан — итог проверки печатать под ним
        self.seen = ""  # о чём уже сказали (версия или число коммитов) — не повторять
        self.first = True
        self.asking = False

    # ------------------------------------------------------------------ строки блока «работает»

    def head_lines(self) -> list[str]:
        head = updates.git_head(self.source, self.run) if self.source else None
        lines = [version_line(self.portable, head, bool(self.source))]
        lines.append("Проверяю обновления…" if self.enabled else "Проверка обновлений выключена.")
        log.info(lines[0])
        return lines

    # ------------------------------------------------------------------ проверка

    def check_once(self) -> str:
        """Одна проверка: для страницы — выпуск на GitHub (app.state.update), для окна — строка итога.
        Возвращает, что предложить обновить: "release", "git" или ""."""
        latest = updates.latest()
        self.app.state.update = latest if latest and updates.is_newer(latest.version, __version__) else None
        self.app.state.update_state = "done"
        if self.source:
            news = updates.git_check(self.source, self.run)
            line, key = git_line(news), f"git:{news.behind}" if news and news.behind else ""
            offer = "git" if news and news.behind else ""
        else:
            line, key = release_line(latest), f"rel:{latest.version}" if self.app.state.update else ""
            offer = "release" if self.app.state.update and self.portable and self.app.state.update.installable else ""
        log.info("Проверка обновлений: %s", line)
        new = bool(key) and key != self.seen  # о новом ещё не говорили
        if self.first or new:  # первая проверка — всегда итог; дальше — только если вышло новое
            self.out(line)
        self.first = False
        if key:
            self.seen = key
        return offer if new else ""

    def offer(self, what: str) -> None:
        if not what:
            return
        if not self.interactive:
            self.out("Обновить: кнопка «Обновить» на главной странице." if what == "release"
                     else "Обновить: git pull и перезапустите программу.")
            return
        if self.asking:
            return
        self.out("Обновить сейчас? Введите д и нажмите Enter" +
                 (" (или кнопка «Обновить» на главной странице)." if what == "release" else ".") +
                 " Не хотите — просто работайте дальше.")
        self.asking = True
        threading.Thread(target=self._wait_answer, args=(what,), daemon=True, name="обновить?").start()

    def _wait_answer(self, what: str) -> None:
        try:
            answer = self.ask()
        except (EOFError, KeyboardInterrupt, OSError, RuntimeError):
            return
        finally:
            self.asking = False
        if answer.strip().lower() in YES:
            self.update(what)

    def update(self, what: str) -> bool:
        """Обновить из окна программы: исходники — git, переносная — новый архив (сначала копия данных); True —
        обновлено, нужен перезапуск."""
        if what == "git":
            self.out("Обновляю исходники с GitHub…")
            ok, msg = updates.git_update(self.source, self.run, uv=shutil.which("uv"))
            self.out(("Готово: " if ok else "Не обновлено: ") + msg)
            log.info("Обновление из окна (git): %s", msg)
            if ok:
                self.app.state.restart_source = True
                self.shutdown()
            return ok
        rel, inst = self.app.state.update, self.app.state.installer
        if rel is None or inst is None:
            return False
        self.out(f"Обновляю до {rel.version}: резервная копия, скачивание, проверка…")

        def backup_first():
            from datetime import datetime

            from st_secretary import backup

            store = self.app.state.store
            backup.auto([f.path for f in store.all()], backup.backups_dir(store.root), datetime.now())

        inst.start(rel, before=backup_first, wait=True)
        if inst.state != "ready":
            self.out(f"Не обновлено: {inst.error or 'не получилось'}. Можно ещё раз — кнопкой на главной странице.")
            return False
        self.app.state.restart = True
        self.shutdown()
        return True

    # ------------------------------------------------------------------ в фоне, пока программа работает

    def start(self) -> None:
        if not self.enabled:
            self.app.state.update_state = "off"
            return
        self.app.state.update_state = "pending"
        threading.Thread(target=self._loop, daemon=True, name="обновления").start()

    def _loop(self) -> None:
        self.ready.wait(60)
        while True:
            try:
                self.offer(self.check_once())
            except Exception as e:  # noqa: BLE001 — проверка обновлений не должна мешать работе
                log.warning("Проверка обновлений не удалась: %s", e)
                self.app.state.update_state = "done"
            time.sleep(CHECK_EVERY)
