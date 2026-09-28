"""Командная строка СТ-Секретаря (до появления интерфейса в браузере).

    st-secretary card-template Карточка.xlsx          — пустая карточка соревнования
    st-secretary card-check Карточка.xlsx             — проверить заполненную карточку
    st-secretary preapp ПАПКА --card Карточка.xlsx    — обработать предзаявки из папки
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from st_secretary.issues import ERROR, FIXED, SEVERITY_LABEL, WARNING


def _utf8():
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass


def _load_card(path: str):
    from st_secretary.importers.card_xlsx import CardError, load_card

    try:
        comp = load_card(path)
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

    comp = _load_card(a.card)
    card_issues = comp.check()
    if any(i.severity == ERROR for i in card_issues):
        print("Сначала исправьте карточку:")
        for i in card_issues:
            print(f"  [{i.label}] {i.field}: {i.text}")
        return 2
    folder = Path(a.folder)
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


def main(argv=None) -> int:
    _utf8()
    ap = argparse.ArgumentParser(prog="st-secretary", description="СТ-Секретарь — помощник секретариата соревнований")
    sub = ap.add_subparsers(dest="cmd", required=True)
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
    return a.func(a)


if __name__ == "__main__":
    raise SystemExit(main())
