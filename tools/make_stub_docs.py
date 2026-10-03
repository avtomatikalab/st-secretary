"""Заглушки документов участников — чтобы проверить программу без настоящих сканов.

Для каждой команды соревнования рисует документы с крупной надписью «ОБРАЗЕЦ — НЕ ДОКУМЕНТ» (st_secretary.stub_docs,
то же, что кнопка «Добавить заглушки документов» в учебном режиме): на каждого участника — паспорт (младше 14 лет —
свидетельство о рождении), полис ОМС, страховку и классификационную книжку (если есть разряд); на команду — заявку с
допуском врача. Кладёт их туда же, куда программа кладёт документы команды. Уже созданные файлы не трогает.

    uv run python tools/make_stub_docs.py "данные/2025-09-20 Чемпионат г. Красноярска …"

Номеров документов и других реквизитов в заглушках нет — только ФИО, дата рождения и команда из заявки.
"""

from __future__ import annotations

import sys
from pathlib import Path

from st_secretary.stub_docs import make_team
from st_secretary.web.store import Store


def main(folder: str) -> None:
    comp_dir = Path(folder).resolve()
    store = Store(comp_dir.parent)
    f = store.get(comp_dir.name)
    if f is None:
        sys.exit(f"Не найдено соревнование: {comp_dir}")
    comp = f.load()
    result = store.preapps(f, comp)
    made = sum(make_team(store.team_docs_dir(f, team.source), team, comp) for team in result.teams)
    print(f"Готово: создано файлов {made}. Папка: {store.docs_root / f.id}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(sys.argv[1])
