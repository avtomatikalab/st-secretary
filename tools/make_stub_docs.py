"""Заглушки документов участников — чтобы проверить программу без настоящих сканов.

Для каждой команды соревнования рисует документы с крупной надписью «ОБРАЗЕЦ — НЕ ДОКУМЕНТ»:
на каждого участника — паспорт (младше 14 лет — свидетельство о рождении), полис ОМС, страховку
(PDF) и классификационную книжку (если есть разряд); на команду — заявку с допуском врача (PDF).
Кладёт их туда же, куда программа кладёт документы команды. Уже созданные файлы не трогает.

    uv run --with pillow python tools/make_stub_docs.py "данные/2025-09-20 Чемпионат г. Красноярска …"

Номеров документов и других реквизитов в заглушках нет — только ФИО, дата рождения и команда из заявки.
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from st_secretary.admission import full_years
from st_secretary.equipment import short_name
from st_secretary.qualification import Qual
from st_secretary.web.store import Store

W, H = 1100, 760
FONTS = [Path(r"C:\Windows\Fonts\arial.ttf"), Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
         Path("/Library/Fonts/Arial Unicode.ttf")]
COLORS = {"Паспорт": "#f3e7ea", "Свидетельство о рождении": "#eaf1e4", "Полис ОМС": "#e4edf7",
          "Страховка": "#f5f0df", "Классификационная книжка": "#ece6f5", "Заявка с допуском врача": "#ffffff"}


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    for p in FONTS:
        bp = p.with_name(p.stem + "bd" + p.suffix) if bold and p.name == "arial.ttf" else p
        if bp.exists():
            return ImageFont.truetype(str(bp), size)
    return ImageFont.load_default()


def sheet(title: str, lines: list[tuple[str, str]], note: str = "") -> Image.Image:
    img = Image.new("RGB", (W, H), COLORS.get(title, "#f4f4f4"))
    d = ImageDraw.Draw(img)
    d.rectangle([20, 20, W - 20, H - 20], outline="#7a8591", width=4)
    d.text((60, 50), title.upper(), fill="#1a2027", font=font(46, bold=True))
    y = 150
    for label, value in lines:
        d.text((60, y), label, fill="#5b6671", font=font(26))
        d.text((360, y - 4), value, fill="#1a2027", font=font(34, bold=True))
        y += 70
    if note:
        d.text((60, H - 130), note, fill="#5b6671", font=font(24))
    d.text((60, H - 80), "Заглушка для проверки программы «СТ-Секретарь». Не является документом.", fill="#b3261e",
           font=font(24))
    stamp = Image.new("RGBA", (W, H), (0, 0, 0, 0))  # водяной знак по диагонали
    ImageDraw.Draw(stamp).text((W // 2, H // 2), "ОБРАЗЕЦ — НЕ ДОКУМЕНТ", fill=(179, 38, 30, 70),
                                font=font(66, bold=True), anchor="mm")
    img.paste(stamp.rotate(20, resample=Image.BICUBIC), (0, 0), stamp.rotate(20, resample=Image.BICUBIC))
    return img


def application(team, comp) -> Image.Image:
    """Заявка команды: «допущен» и подпись врача напротив каждого (Правила, раздел 3, п. 8.1)."""
    img = Image.new("RGB", (W, H), "#ffffff")
    d = ImageDraw.Draw(img)
    d.text((60, 40), "ЗАЯВКА", fill="#1a2027", font=font(42, bold=True))
    d.text((60, 100), f"на участие: {comp.title}", fill="#1a2027", font=font(22))
    d.text((60, 135), f"Команда: {team.team}, {team.territory}", fill="#1a2027", font=font(26, bold=True))
    y = 200
    d.text((60, y), "№   ФИО                                                 Разряд     Допуск врача", fill="#5b6671",
           font=font(22))
    for n, e in enumerate(team.entries, start=1):
        y += 50
        d.text((60, y), f"{n}.   {e.name.full}", fill="#1a2027", font=font(26))
        d.text((720, y), e.qual.label if e.qual is not None else "?", fill="#1a2027", font=font(26))
        d.text((860, y), "допущен  ✓", fill="#1d4f91", font=font(26, bold=True))
    d.text((60, H - 170), f"Допущено: {len(team.entries)} чел.   Врач ____________ (подпись)   М.П.", fill="#1a2027",
           font=font(24))
    d.text((60, H - 120), f"Представитель: {team.representative}", fill="#1a2027", font=font(24))
    d.text((60, H - 70), "Заглушка для проверки программы «СТ-Секретарь». Не является документом.", fill="#b3261e",
           font=font(22))
    return img


def main(folder: str) -> None:
    comp_dir = Path(folder).resolve()
    store = Store(comp_dir.parent)
    f = store.get(comp_dir.name)
    if f is None:
        sys.exit(f"Не найдено соревнование: {comp_dir}")
    comp = f.load()
    result = store.preapps(f, comp)
    made = 0
    for team in result.teams:
        d = store.team_docs_dir(f, team.source)
        d.mkdir(parents=True, exist_ok=True)

        def save(img: Image.Image, name: str, d=d) -> None:
            nonlocal made
            p = d / name
            if not p.exists():
                img.save(p, **({"quality": 82} if p.suffix == ".jpg" else {"resolution": 100} if p.suffix == ".pdf" else {}))
                made += 1

        save(application(team, comp), "Заявка с допуском врача.pdf")
        for e in team.entries:
            who = short_name(e)
            birth = e.birth.strftime("%d.%m.%Y") if e.birth else str(e.birth_year or "—")
            young = e.birth is not None and full_years(e.birth, comp.date_from) < 14
            base = [("Фамилия Имя Отчество", e.name.full), ("Дата рождения", birth), ("Команда", team.team)]
            kind = "Свидетельство о рождении" if young else "Паспорт"
            save(sheet(kind, base, "Серия и номер не указаны — это заглушка."), f"{kind} — {who}.jpg")
            save(sheet("Полис ОМС", base), f"Полис ОМС — {who}.png")
            save(sheet("Страховка", base + [("Период", comp.dates_text)], "Страхование от несчастных случаев"),
                 f"Страховка — {who}.pdf")
            if e.qual is not None and e.qual != Qual.BR:
                save(sheet("Классификационная книжка", base + [("Разряд", e.qual.label)]),
                     f"Классификационная книжка — {who}.png")
    print(f"Готово: создано файлов {made}. Папка: {store.docs_root / f.id}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(sys.argv[1])
