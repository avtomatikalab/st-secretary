"""Журнал программы в файл — чтобы понять, почему программа закрылась или «вылетела».

Папка «Журнал» рядом с папкой «данные»:
- «СТ-Секретарь.log» (5 файлов по 1 МБ по кругу): запуск (версия, система, Python, папки, порт), остановка и как
  (кнопка «Выключить», закрыли окно, Ctrl+C, обновление), все ошибки с полным текстом, предупреждения (например,
  не открылся файл), ошибки сервера табло и телефонов судей;
- «сбои.log» — аварийные падения самого Python (faulthandler), когда обычного текста ошибки нет;
- «работает» — лежит, пока программа запущена. Если при запуске он уже есть, прошлый раз программа закрылась не
  штатно: секретарю предлагается скачать журнал и отправить разработчику.
Закрыть окно программы — штатный способ выключить её, поэтому закрытие окна (Windows) и терминала (macOS, Linux)
тоже записывается как остановка. Журнал — только на этом компьютере (в GitHub и облако не попадает, если папку
программы не держать в облаке); паспортные данные в него не пишутся.
"""

from __future__ import annotations

import faulthandler
import io
import logging
import os
import platform
import signal
import sys
import threading
import zipfile
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOG_NAME = "СТ-Секретарь.log"
FAULTS_NAME = "сбои.log"
RUNNING = "работает"
FOLDER = "Журнал"

log = logging.getLogger("st_secretary")


def folder_for(data_dir: Path) -> Path:
    """Папка журнала — рядом с папкой «данные»."""
    return Path(data_dir).resolve().parent / FOLDER


class Journal:
    def __init__(self, folder: Path):
        self.folder = Path(folder)
        self.crashed_before = False  # прошлый запуск закончился не штатно
        self.previous = ""  # когда был прошлый запуск (из файла «работает»)
        self.stopped = False
        self.handler: logging.Handler | None = None
        self.console: logging.Handler | None = None
        self._faults = None
        self._hooks = None
        self._ctrl = None

    # ------------------------------------------------------------------ запуск и остановка

    def start(self, **info) -> Journal:
        """Начать журнал: файл с ротацией, сбои Python, метка «работает» (была — прошлый раз закрылась неожиданно),
        запись о запуске."""
        self.folder.mkdir(parents=True, exist_ok=True)
        marker = self.folder / RUNNING
        self.crashed_before = marker.exists()
        if self.crashed_before:
            try:
                self.previous = marker.read_text(encoding="utf-8").strip()
            except OSError:
                self.previous = ""
        h = RotatingFileHandler(self.folder / LOG_NAME, maxBytes=1_000_000, backupCount=5, encoding="utf-8")
        h.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s"))
        h.setLevel(logging.INFO)
        self.handler = h
        root = logging.getLogger()
        # раньше предупреждения без настроенного журнала печатались в окно программы — пусть печатаются и дальше
        self.console = logging.StreamHandler()
        self.console.setLevel(logging.WARNING)
        root.addHandler(h)
        root.addHandler(self.console)
        root.setLevel(logging.INFO)
        self.attach_uvicorn()
        self._faults = (self.folder / FAULTS_NAME).open("a", encoding="utf-8")
        faulthandler.enable(self._faults)
        self._hooks = (sys.excepthook, threading.excepthook)
        sys.excepthook = self._excepthook
        threading.excepthook = self._thread_excepthook
        marker.write_text(f"{datetime.now():%d.%m.%Y %H:%M:%S}, процесс {os.getpid()}", encoding="utf-8")
        if self.crashed_before:
            log.warning("Прошлый запуск (%s) закончился неожиданно — программа не выключалась штатно",
                        self.previous or "время неизвестно")
        from st_secretary import version_label

        log.info("Запуск: СТ-Секретарь %s; %s; Python %s%s", version_label(), platform.platform(), platform.python_version(),
                 "".join(f"; {k}: {v}" for k, v in info.items()))
        self._install_close_handlers()
        return self

    def attach_uvicorn(self) -> None:
        """uvicorn настраивает свои журналы сам и не передаёт сообщения наверх — подключить файл к ним напрямую
        (вызывать после создания uvicorn.Config: он перенастраивает журналы)."""
        for name in ("uvicorn", "uvicorn.error"):
            lg = logging.getLogger(name)
            if self.handler and self.handler not in lg.handlers:
                lg.addHandler(self.handler)

    def stop(self, how: str) -> None:
        """Штатная остановка: записать, как остановили, и убрать метку «работает» (можно звать несколько раз)."""
        if self.stopped:
            return
        self.stopped = True
        log.info("Остановка: %s", how)
        try:
            (self.folder / RUNNING).unlink(missing_ok=True)
        except OSError:
            pass
        if self.handler:
            self.handler.flush()

    def close(self) -> None:
        """Отключить журнал (для тестов и повторного запуска в том же процессе)."""
        root = logging.getLogger()
        for lg in (root, logging.getLogger("uvicorn"), logging.getLogger("uvicorn.error")):
            for hd in (self.handler, self.console):
                if hd in lg.handlers:
                    lg.removeHandler(hd)
        if self.handler:
            self.handler.close()
        if self._hooks:
            sys.excepthook, threading.excepthook = self._hooks
        if self._faults:
            faulthandler.disable()
            self._faults.close()
        if self._ctrl is not None:  # Windows: снять обработчик закрытия окна, пока он ещё существует
            try:
                import ctypes

                ctypes.windll.kernel32.SetConsoleCtrlHandler(self._ctrl, False)
            except (AttributeError, OSError):
                pass
            self._ctrl = None
        elif hasattr(signal, "SIGHUP") and threading.current_thread() is threading.main_thread():
            try:
                signal.signal(signal.SIGHUP, signal.SIG_DFL)
            except (ValueError, OSError):
                pass

    # ------------------------------------------------------------------ ошибки, которые никто не поймал

    def _excepthook(self, kind, value, tb):
        log.critical("Необработанная ошибка — программа остановилась", exc_info=(kind, value, tb))
        if self._hooks:
            self._hooks[0](kind, value, tb)

    def _thread_excepthook(self, args):
        log.critical("Ошибка в фоновом потоке «%s»", getattr(args.thread, "name", "?"),
                     exc_info=(args.exc_type, args.exc_value, args.exc_traceback))

    def _install_close_handlers(self) -> None:
        """Закрыли окно программы — это штатное выключение: записать и убрать метку «работает»."""
        if sys.platform.startswith("win"):
            try:
                import ctypes

                routine = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_uint)
                why = {2: "закрыли окно программы", 5: "выход из системы Windows", 6: "выключение компьютера"}

                def on_console(event):
                    if event in why:
                        self.stop(why[event])
                    return 0  # дальше — как обычно (процесс завершится)

                self._ctrl = routine(on_console)  # ссылка нужна, пока программа работает
                ctypes.windll.kernel32.SetConsoleCtrlHandler(self._ctrl, True)
            except (AttributeError, OSError):
                pass
        elif threading.current_thread() is threading.main_thread():
            def on_hup(signum, frame):
                self.stop("закрыли окно терминала")
                signal.signal(signal.SIGHUP, signal.SIG_DFL)
                os.kill(os.getpid(), signal.SIGHUP)

            try:
                signal.signal(signal.SIGHUP, on_hup)
            except (AttributeError, ValueError, OSError):
                pass

    # ------------------------------------------------------------------ скачать

    def zip_bytes(self) -> bytes:
        """Все файлы журнала одним архивом — «Скачать журнал программы»."""
        if self.handler:
            self.handler.flush()
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for p in sorted(self.folder.glob("*.log*")):
                z.write(p, p.name)
        return buf.getvalue()
