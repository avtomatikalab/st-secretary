"""Командная строка СТ-Секретаря.

    st-secretary web                                  — открыть программу в браузере (для секретаря)
    st-secretary card-template Карточка.xlsx          — пустая карточка соревнования
    st-secretary card-check Карточка.xlsx             — проверить заполненную карточку
    st-secretary preapp ПАПКА --card Карточка.xlsx    — обработать предзаявки из папки
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import threading
import urllib.request
import webbrowser
from pathlib import Path

from st_secretary.issues import ERROR, FIXED, SEVERITY_LABEL, WARNING

COPY_PATH_HINT = ("Полный путь проще всего скопировать: в Проводнике зажмите Shift, щёлкните по файлу или папке "
                  "правой кнопкой → «Копировать как путь» и вставьте в команду.")


class UserError(Exception):
    """Ошибка, которую человек может исправить сам: показываем текст без технических подробностей."""


def _utf8():
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass


def _need_file(path: str, what: str) -> Path:
    p = Path(path)
    if not p.is_file():
        raise UserError(f"Не найден {what}: «{path}».\nУкажите полный путь в кавычках. {COPY_PATH_HINT}")
    return p


def _need_folder(path: str, what: str) -> Path:
    p = Path(path)
    if not p.is_dir():
        raise UserError(f"Не найдена {what}: «{path}».\nУкажите полный путь в кавычках. {COPY_PATH_HINT}")
    return p


def _load_card(path: str):
    from st_secretary.importers.card_xlsx import CardError, load_card

    p = _need_file(path, "файл карточки соревнования")
    try:
        comp = load_card(p)
    except CardError as e:
        print("Карточка заполнена с ошибками:")
        for i in e.issues:
            print(f"  [{i.label}] {i.field}: {i.text}")
        raise SystemExit(2) from None
    return comp


def cmd_card_template(a) -> int:
    from st_secretary.importers.card_xlsx import write_card

    path = Path(a.out)
    if path.exists() and not a.force:
        print(f"Файл {path} уже есть. Чтобы перезаписать, добавьте --force.")
        return 1
    write_card(path)
    print(f"Создана пустая карточка: {path}\nЗаполните жёлтые ячейки на листах «Карточка», «ГСК», «Зачёты».")
    return 0


def cmd_card_check(a) -> int:
    comp = _load_card(a.card)
    issues = comp.check()
    print(f"{comp.title}, {comp.dates_text}, {comp.place}")
    print("Зачёты: " + ", ".join(f"{z.key} ({z.discipline_name})" for z in comp.zachety))
    if not issues:
        print("Замечаний нет.")
    for i in issues:
        print(f"  [{i.label}] {i.field}: {i.text}")
    return 2 if any(i.severity == ERROR for i in issues) else 0


def cmd_preapp(a) -> int:
    from st_secretary.exporters.preapp_xlsx import write_preapp_report
    from st_secretary.importers.preapp_xlsx import read_preapplication
    from st_secretary.preapp import process

    folder = _need_folder(a.folder, "папка с заявками")
    comp = _load_card(a.card)
    card_issues = comp.check()
    if any(i.severity == ERROR for i in card_issues):
        print("Сначала исправьте карточку:")
        for i in card_issues:
            print(f"  [{i.label}] {i.field}: {i.text}")
        return 2
    files = sorted(p for p in folder.iterdir() if p.suffix.lower() in (".xlsx", ".xls") and not p.name.startswith("~$"))
    if not files:
        print(f"В папке {folder} нет файлов Excel с заявками.")
        return 1
    result = process([read_preapplication(p) for p in files], comp)
    out = Path(a.out) if a.out else folder.parent / "Сводка_предзаявок.xlsx"
    write_preapp_report(result, comp, out)
    print(f"Заявок: {len(files)}, команд: {len(result.teams)}, участников: {len(result.entries)}")
    for sev in (ERROR, WARNING, FIXED):
        print(f"  {SEVERITY_LABEL[sev]}: {result.count(sev)}")
    print(f"Сводка: {out}")
    return 0


# ------------------------------------------------------------------ интерфейс в браузере

HOST = "127.0.0.1"  # только этот компьютер: в заявках персональные данные


def _already_running(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://{HOST}:{port}/health", timeout=1) as r:
            return json.load(r).get("app") == "st-secretary"
    except Exception:  # noqa: BLE001 — не отвечает или занят чужой программой
        return False


def _free_port(start: int) -> int:
    for port in range(start, start + 20):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind((HOST, port))
                return port
            except OSError:
                continue
    raise UserError(f"Не нашлось свободного порта ({start}–{start + 19}). Закройте лишние программы и запустите снова.")


def cmd_web(a) -> int:
    try:
        import uvicorn

        from st_secretary.web.app import create_app
    except ImportError as e:
        raise UserError(f"Не установлены библиотеки для работы в браузере ({e.name}). Выполните: uv sync") from None

    if _already_running(a.port):  # второй запуск — просто открыть уже работающую программу
        url = f"http://{HOST}:{a.port}/"
        print(f"СТ-Секретарь уже работает: {url}")
        print("Его окно открыто отдельно — работайте в нём. Открываю страницу в браузере.")
        if not a.no_browser:
            webbrowser.open(url)
        return 0
    data = Path(a.data).resolve()
    data.mkdir(parents=True, exist_ok=True)
    port = _free_port(a.port)
    url = f"http://{HOST}:{port}/"
    servers: list = []  # сервер создаётся после приложения, а кнопке «Выключить» нужен именно он
    app = create_app(data, shutdown=lambda: setattr(servers[0], "should_exit", True))
    server = uvicorn.Server(uvicorn.Config(app, host=HOST, port=port, log_level="warning"))
    servers.append(server)
    print("СТ-Секретарь работает.")
    print(f"  Адрес в браузере: {url}")
    print(f"  Папка с данными:  {data}")
    print()
    print("Это окно — сама программа: пока оно открыто, страница в браузере работает.")
    print("Выключить программу: кнопка «Выключить» вверху страницы или просто закройте это окно.")
    if not a.no_browser:
        threading.Timer(1.0, webbrowser.open, [url]).start()
    server.run()
    print()
    print("СТ-Секретарь выключен. Всё сохранено в папке с данными.")
    return 0


def main(argv=None) -> int:
    _utf8()
    ap = argparse.ArgumentParser(prog="st-secretary", description="СТ-Секретарь — помощник секретариата соревнований")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("web", help="открыть программу в браузере")
    p.add_argument("--data", default="данные", help="папка с соревнованиями (по умолчанию «данные» рядом с программой)")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--no-browser", action="store_true", help="не открывать браузер автоматически")
    p.set_defaults(func=cmd_web)
    p = sub.add_parser("card-template", help="создать пустую карточку соревнования (xlsx)")
    p.add_argument("out")
    p.add_argument("--force", action="store_true", help="перезаписать существующий файл")
    p.set_defaults(func=cmd_card_template)
    p = sub.add_parser("card-check", help="проверить заполненную карточку")
    p.add_argument("card")
    p.set_defaults(func=cmd_card_check)
    p = sub.add_parser("preapp", help="обработать предварительные заявки из папки")
    p.add_argument("folder")
    p.add_argument("--card", required=True, help="карточка соревнования (xlsx)")
    p.add_argument("--out", help="куда сохранить сводку (по умолчанию рядом с папкой заявок)")
    p.set_defaults(func=cmd_preapp)
    a = ap.parse_args(argv)
    try:
        return a.func(a)
    except UserError as e:
        print(e)
        return 1
    except PermissionError as e:
        print(f"Файл занят другой программой (скорее всего, открыт в Excel): «{e.filename}».\n"
              "Закройте его и запустите команду ещё раз.")
        return 1
    except Exception as e:  # noqa: BLE001
        if os.environ.get("ST_DEBUG"):
            raise
        print(f"Непредвиденная ошибка: {e}\nДанные не изменены. Сообщите разработчикам текст ошибки и команду, "
              "которую запускали (подробности: задайте переменную окружения ST_DEBUG=1).")
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
