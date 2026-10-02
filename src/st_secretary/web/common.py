"""Общее для страниц программы: адреса, переадресации, сообщения «Готово…», открыть файл программой системы."""

from __future__ import annotations

import hashlib
import logging
import os
import subprocess
import sys
import threading
from collections import Counter, defaultdict
from datetime import date, datetime
from pathlib import Path
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

from fastapi import Request
from fastapi.responses import RedirectResponse

from st_secretary import commission as cm
from st_secretary import system, version_label
from st_secretary.issues import SEVERITY_ORDER, Issue
from st_secretary.web.store import CompFolder

log = logging.getLogger("st_secretary.web")


HERE = Path(__file__).parent


XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


OPEN_WAIT = 5  # секунд ждать ответа open / xdg-open: нашлась ли программа (обычно отвечают сразу)


class CannotOpen(Exception):
    """На этом компьютере нечем открыть файл или папку; reason — что ответила система."""

    def __init__(self, path: Path, reason: str = ""):
        super().__init__(f"{path}: {reason}")
        self.path, self.reason = Path(path), reason


def open_in_os(path: Path, system_name: str = sys.platform, popen=subprocess.Popen, startfile=None) -> None:
    """Открыть файл или папку программой по умолчанию (Excel, Word, Проводник, Finder).

    Если открыть нечем (нет Excel/Numbers/LibreOffice для .xlsx, нет xdg-open в Linux) — CannotOpen: страница
    объяснит это секретарю и даст скачать файл через браузер, а не скажет «Открываю…» впустую."""
    try:
        if system_name.startswith("win"):
            (startfile or os.startfile)(path)  # нет программы для этого типа файлов — OSError (WinError 1155)
            return
        cmd = ["open", str(path)] if system_name == "darwin" else ["xdg-open", str(path)]
        proc = popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    except OSError as e:
        raise CannotOpen(path, str(e)) from e
    try:
        _, err = proc.communicate(timeout=OPEN_WAIT)
    except subprocess.TimeoutExpired:  # программа запустилась и держит запуск — значит, открылось
        threading.Thread(target=proc.wait, daemon=True).start()
        return
    if proc.returncode:  # macOS: kLSApplicationNotFoundErr; Linux: xdg-open 3/4 — нечем открыть
        raise CannotOpen(path, (err or b"").decode("utf-8", "replace").strip())


OPEN_KINDS = {  # тип файла → (как назвать, чем открыть: Windows, macOS, Linux)
    ".xlsx": ("файлы Excel (.xlsx)", "Excel или LibreOffice", "Excel, Numbers или LibreOffice", "LibreOffice"),
    ".xls": ("файлы Excel (.xls)", "Excel или LibreOffice", "Excel, Numbers или LibreOffice", "LibreOffice"),
    ".docx": ("документы Word (.docx)", "Word или LibreOffice", "Word, Pages или LibreOffice", "LibreOffice"),
    ".doc": ("документы Word (.doc)", "Word или LibreOffice", "Word, Pages или LibreOffice", "LibreOffice"),
    ".pdf": ("файлы PDF", "программу для PDF", "программу для PDF", "программу для PDF"),
}


def cannot_open_text(path: Path, system_name: str = sys.platform) -> dict:
    """Что сказать секретарю, если файл не открылся: какой это файл и что поставить."""
    kind = OPEN_KINDS.get(path.suffix.lower())
    col = 1 if system_name.startswith("win") else 2 if system_name == "darwin" else 3
    if path.is_dir():
        return {"what": "папку", "hint": "Откройте её вручную — путь ниже можно скопировать."}
    if kind:
        return {"what": kind[0], "hint": f"Установите {kind[col]} — или скачайте файл кнопкой ниже и откройте его "
                                        "там, где такая программа есть (или на другом компьютере)."}
    return {"what": f"файлы «{path.suffix or path.name}»",
            "hint": "Скачайте файл кнопкой ниже и откройте его подходящей программой."}


def fmt_date(d) -> str:
    return d.strftime("%d.%m.%Y") if isinstance(d, date) else ("" if d is None else str(d))


def base_url(f: CompFolder) -> str:
    return "/c/" + quote(f.id, safe="")


def step_url(base: str, step) -> str:
    return f"{base}/{step.slug}"


def redirect(url: str) -> RedirectResponse:
    return RedirectResponse(url, status_code=303)


GRADES = ("", "отлично", "хорошо", "удовлетворительно", "неудовлетворительно")  # оценка судейства


PROTEST_DECISIONS = ("удовлетворён", "удовлетворён частично", "отклонён", "не рассматривается")


def key_of(e) -> str:
    """Ключ участника в отметках комиссии и проверки снаряжения — ФИО без регистра и «ё»."""
    return cm.person_key(e.name.full)


def parse_dt(s: str) -> datetime | None:
    """Дата и время из поля браузера («2025-09-20T10:00»)."""
    try:
        return datetime.fromisoformat(s) if s else None
    except ValueError:
        return None


def team_anchor(file: str) -> str:
    """Метка команды в списке заявок (#t-…): по ней страница открывается сразу на этой команде."""
    return "t-" + hashlib.sha1(file.encode("utf-8")).hexdigest()[:10]


def with_done(url: str, done: str, **extra: str) -> str:
    """Адрес с сообщением о сделанном (?done=…) и подробностями для него; якорь (#…) сохраняется."""
    parts = urlsplit(url)
    q = [(k, v) for k, v in parse_qsl(parts.query) if k != "done" and k not in extra]
    q += [("done", done), *extra.items()]
    return urlunsplit(parts._replace(query=urlencode(q)))


def _start_time_text(q) -> str:
    """Время старта после жеребьёвки — в сообщении (Правки, п. 40): «с 10:00 через 5 мин» или «без времени»."""
    if "first" not in q:
        return ""
    first, iv = q.get("first", "").strip(), q.get("interval", "").strip().replace(".", ",")
    if not first:
        return " Время старта не задано — в протоколе только очерёдность; задать — в разделе «Время старта» ниже."
    step = f" через {iv} мин" if iv not in ("", "0") else " — все одновременно"
    return f" Время старта — с {first}{step}; поменять — в разделе «Время старта» ниже."


# Тексты сообщений «Готово…» над страницей: код (?done=…) → (вид, текст). {имя} — значение из адреса (?имя=…, пусто —
# если нет), {start_time} — время старта после жеребьёвки, {version} — версия, {launcher} — файл запуска программы.
MESSAGES: dict[str, tuple[str, str]] = {
    "created": ("ok", "Соревнование создано. Заполните судейскую коллегию и зачёты и нажмите «Сохранить»."),
    "backup_made": ("ok", "Резервная копия сохранена: «{file}» — в папке «Резервные копии» рядом с "
                          "папкой «данные». Паспортные данные и сканы в копию не входят."),
    "restored": ("ok", "Соревнование восстановлено из резервной копии — отдельной папкой, прежние данные не "
                       "тронуты."),
    "restore_none": ("err", "Выберите файл резервной копии (.zip)."),
    "restore_bad": ("err", "Не получилось восстановить: {why}. Нужен архив, который сделала кнопка "
                           "«Сделать копию» или «Скачать копию»."),
    "training": ("ok", "Учебное соревнование создано: 14 команд с выдуманными участниками, этапы дистанций заданы. "
                       "В четырёх заявках ошибки оставлены специально — найдите их в «Предварительных заявках»."),
    "imported": ("ok", "Карточка загружена из Excel. Посмотрите замечания проверки, если они есть."),
    "saved": ("ok", "Карточка сохранена."),
    "removed": ("ok", "Заявка «{name}» убрана из обработки — файл перенесён в папку "
                      "«Предзаявки\\Убранные», его можно вернуть."),
    "summary": ("ok", "Сводка сохранена в папке соревнования и открывается в Excel."),
    "psaved": ("ok", "Заявка сохранена и проверена заново — результат ниже. Прежний вариант файла лежит в папке "
                     "«Предзаявки\\Прежние версии»."),
    "status": ("ok", "Статус заявки изменён."),
    "checked": ("ok", "Отмечено: проверено. Замечание больше не считается в «Проверить»."),
    "unchecked": ("ok", "Отметка «проверено» снята — замечание снова в «Проверить»."),
    "pcreated": ("ok", "Заявка сохранена в файл «{file}» в папке «Предзаявки» и проверена — "
                       "результат ниже."),
    "locked": ("err", "Сводка сейчас открыта в Excel. Закройте её там и нажмите кнопку ещё раз."),
    "adm_saved": ("ok", "Отметки комиссии сохранены."),
    "adm_settings": ("ok", "Настройки комиссии сохранены."),
    "adm_delegation": ("ok", "Делегация сохранена."),
    "named_saved": ("ok", "Свой бланк именной заявки загружен — «Именная заявка (Word)» у команд теперь по нему."),
    "named_deleted": ("ok", "Свой бланк убран — именная заявка снова по Правилам."),
    "named_bad": ("err", "Нужен файл Word (.docx). Старый .doc откройте в Word и сохраните как .docx."),
    "form_saved": ("ok", "Форма «{name}» сохранена на этом компьютере. Файлы с такой же шапкой "
                         "программа теперь читает по ней сама — просто добавляйте заявки."),
    "form_deleted": ("ok", "Форма удалена."),
    "form_imported": ("ok", "Форма «{name}» загружена из файла."),
    "form_bad_sample": ("err", "Выберите файл Excel (.xlsx или .xls) — образец заявки по вашей форме."),
    "form_bad_file": ("err", "Это не файл формы заявки СТ-Секретаря (его делает кнопка «Скачать форму файлом»)."),
    "adm_numbers": ("ok", "Номера командам присвоены — их можно поправить вручную в поле «№» у команды."),
    "adm_numbers_fest": ("ok", "Номера присвоены во всех соревнованиях фестиваля: у команды один номер везде. "
                               "Поправить — в поле «№» у команды, номер поменяется и в других соревнованиях."),
    "adm_report": ("ok", "Протокол комиссии и ведомость взносов сохранены в папке соревнования и открываются "
                         "в Excel."),
    "adm_locked": ("err", "Файл «Комиссия_по_допуску.xlsx» сейчас открыт в Excel. Закройте его и нажмите "
                          "кнопку ещё раз."),
    "reentry": ("ok", "Перезаявка записана с временем подачи — она видна у команды ниже."),
    "gear_settings": ("ok", "Перечень снаряжения и баллы сохранены."),
    "res_imported": ("ok", "Протокол загружен: места, составы и выполненные разряды — ниже."),
    "res_saved": ("ok", "Результаты зачёта сохранены."),
    "res_cleared": ("ok", "Результаты зачёта убраны."),
    "res_none": ("err", "Выберите файл протокола и зачёт."),
    "res_bad": ("err", "Файл не похож на итоговый протокол СЕКРЕТАРЬ_ST. Нужен протокол результатов (.xls), "
                       "сохранённый кнопкой «Считать протокол»."),
    "res_noxlrd": ("err", "Для чтения .xls не установлена библиотека xlrd — выполните в папке программы: uv sync."),
    "report_saved": ("ok", "Тексты для отчёта сохранены — нажмите «Открыть» у отчёта главного судьи."),
    "grades_saved": ("ok", "Оценки судейства сохранены — они попадут в справки о судействе и отчёт."),
    "doc_ready": ("ok", "Документ сохранён в папке «Документы по итогам» и открывается."),
    "doc_locked": ("err", "Этот документ сейчас открыт в Word или Excel. Закройте его и нажмите кнопку ещё раз."),
    "gear_saved": ("ok", "Проверка снаряжения сохранена."),
    "docs_added": ("ok", "Документы добавлены. Они хранятся только на этом компьютере."),
    "docs_skipped": ("err", "Часть файлов не добавлена: подходят фото (JPG, PNG), PDF и документы Word/Excel."),
    "docs_none": ("err", "Файлы не выбраны."),
    "docs_removed": ("ok", "Документ убран — он перенесён в папку «Убранные» в документах команды, его можно вернуть."),
    "reentry_late": ("err", "Перезаявка записана, но подана позже, чем за час до старта: по Правилам (п. 8.5) "
                            "такая перезаявка не принимается. Решение — за ГСК."),
    "reentry_repeat": ("err", "Перезаявка записана, но она повторная: по Правилам (п. 8.5) повторные "
                              "перезаявки не принимаются. Решение — за ГСК."),
    "opened": ("ok", "Открываю…"),
    "fb_sent": ("ok", "Файл с сообщениями собран, папка с ним открыта. В письме разработчику приложите этот файл "
                      "(перетащите в письмо) и отправьте."),
    "fb_nothing": ("ok", "Новых сообщений нет — отправлять нечего."),
    "fb_removed": ("ok", "Сообщение убрано — файл в папке «Убранные»."),
    "fb_imported": ("ok", "Добавлено сообщений: {added}; уже были: {skipped}."),
    "fb_bad": ("err", "Это не файл сообщений СТ-Секретаря (.zip) или он повреждён."),
    "updated": ("ok", "Программа обновлена до версии {version}. Соревнования на месте. Прежняя версия "
                      "сохранена в папке «program.old» рядом с «{launcher}»."),
    "run_stages": ("ok", "Этапы дистанции сохранены."),
    "run_spp_bad": ("err", "Этапы сохранены, а «1 штрафной балл =» — нет: впишите в «своё» целое число секунд от 1 "
                           "до 600, как в Условиях."),
    "si_nofile": ("err", "Выберите файл si_reader.csv из SPORTident Reader."),
    "si_bad": ("err", "Файл не прочитан: {why}. Нужен экспорт SportIdent Reader «Config+ (card readout)»."),
    "run_saved": ("ok", "Баллы сохранены."),
    "run_nofile": ("err", "Выберите файл рабочей книги СЕКРЕТАРЬ_ST (.xls)."),
    "run_badbook": ("err", "В файле нет листа «Протокол_группа» или он не похож на протокол СЕКРЕТАРЬ_ST. Нужна "
                           "рабочая книга, в которой вносились баллы по этапам."),
    "run_imported": ("ok", "Из рабочей книги перенесено этапов: {n}, команд с баллами: "
                           "{t}. {notes}"),
    "run_empty": ("err", "Пока нечего публиковать: ни одна команда не получила место — внесите баллы."),
    "run_published": ("ok", "Предварительный протокол сохранён в папку «Протоколы» и открывается — распечатайте "
                            "и вывесите. Протесты по результатам принимаются до {until} (п. 8.17)."),
    "run_protest": ("ok", "Протест записан с временем подачи."),
    "run_protest_late": ("err", "Протест записан, но подан позже часа после публикации предварительного протокола: "
                                "по Правилам (п. 8.17) такой протест не принимается. Решение — за ГСК."),
    "run_protest_empty": ("err", "Впишите, с чем не согласна команда."),
    "run_decided": ("ok", "Решение по протесту записано."),
    "run_not_published": ("err", "Сначала опубликуйте предварительный протокол — с него начинается час на протесты."),
    "run_changed": ("err", "После публикации предварительного протокола баллы или статусы менялись. Опубликуйте "
                           "предварительный протокол заново — с новым часом на протесты."),
    "run_open_protests": ("err", "Есть протесты без решения — сначала запишите решения по ним."),
    "run_official": ("ok", "Результаты утверждены: официальный протокол сохранён в папку «Протоколы» и открывается. "
                           "Места и разряды переданы в «Награждение и документы по итогам»."),
    "start_drawn": ("ok", "Жеребьёвка проведена — порядок старта ниже.{start_time} Порядок сразу действует "
                          "в таблице результатов и на телефонах судей. Опубликуйте стартовый протокол не позднее "
                          "чем за час до старта."),
    "start_saved": ("ok", "Порядок и время старта сохранены."),
    "start_times": ("ok", "Время старта сохранено."),
    "start_drawn_fit": ("ok", "Жеребьёвка проведена.{start_time} Чтобы у участников нескольких зачётов "
                              "был перерыв, программа "
                              "поменяла местами соседей или сдвинула время старта — что именно, написано в разделе "
                              "«Жеребьёвка». Опубликуйте стартовый протокол не позднее чем за час до старта."),
    "start_times_fit": ("ok", "Время старта сохранено. Чтобы у участников нескольких зачётов был перерыв, программа "
                              "сдвинула время старта некоторых команд — что именно, написано в разделе «Жеребьёвка»."),
    "start_bad_time": ("err", "Время первого старта — в виде чч:мм, например 10:00."),
    "start_empty": ("err", "В зачёте нет допущенных команд — жеребьёвать некого."),
    "start_published": ("ok", "Стартовый протокол сохранён в папку «Протоколы» и открывается — распечатайте и "
                              "вывесите. Протесты по допуску — до {until} (п. 8.17)."),
    "judge_issued": ("ok", "Ссылка этапа готова. Прежняя ссылка этого этапа (если была) больше не работает."),
    "judge_revoked": ("ok", "Ссылка этапа отозвана — с неё больше ничего не придёт. Уже присланное сохранено."),
    "judge_phone": ("ok", "Номер записан в личные данные судьи (Договоры и табель)."),
    "festival_made": ("ok", "Фестиваль создан. Соревнования остались как были — их данные не менялись."),
    "festival_saved": ("ok", "Фестиваль сохранён."),
    "festival_split": ("ok", "Фестиваль разъединён — соревнования остались как были."),
    "festival_few": ("err", "Для фестиваля отметьте хотя бы два соревнования."),
    "festival_gsk": ("ok", "ГСК фестиваля сохранена и записана в карточки его соревнований (кроме должностей, где у "
                           "соревнования своя замена)."),
    "festival_gsk_role": ("err", "ГСК не сохранена: у человека не указана должность."),
    "festival_gsk_locked": ("err", "ГСК фестиваля сохранена, но карточки «{comps}» сейчас открыты в "
                                   "Excel и не обновились. Закройте их и сохраните ГСК ещё раз."),
    "festival_modes": ("ok", "Режимы фестиваля сохранены."),
    "festival_fees": ("ok", "Взносы фестиваля сохранены."),
    "penalty_saved": ("ok", "Таблица штрафов зачёта сохранена — на телефонах судей она появится при следующем "
                            "открытии ссылки этапа."),
    "penalty_loaded": ("ok", "Своя таблица штрафов загружена: {n} строк. Она выбрана для зачёта."),
    "penalty_bad": ("err", "Таблицу штрафов загрузить не удалось: {why}."),
    "pen_code_saved": ("ok", "Пункт таблицы сопоставлен — баллы по нему видны в журнале этапа."),
    "pen_code_bad": ("err", "Пункта «{code}» в таблице штрафов зачёта нет."),
    "board_on": ("ok", "Табло включено для этого соревнования."),
    "board_off": ("ok", "Табло для этого соревнования выключено — его результатов на табло не видно."),
    "board_started": ("ok", "Табло раздаётся по Wi-Fi — адрес и QR-код ниже. Если Windows спросит разрешение "
                            "в брандмауэре — разрешите для частной сети."),
    "board_stopped": ("ok", "Раздача табло остановлена."),
    "board_failed": ("err", "Табло не запустилось — подробности ниже."),
    "ct_saved": ("ok", "Табель сохранён."),
    "ct_settings": ("ok", "Период работы, ставки и начисления сохранены."),
    "ct_customer": ("ok", "Сведения о заказчике сохранены — они попадут в договоры, акты и табель."),
    "ct_added": ("ok", "Человек добавлен в табель — отметьте дни его работы и заполните данные для договора."),
    "ct_need": ("err", "Впишите ФИО и должность."),
    "ct_exists": ("err", "Такой человек уже есть в табеле (судьи из карточки попадают туда сами)."),
    "ct_removed": ("ok", "Убрано из табеля. Личные данные человека остались — пригодятся на других соревнованиях."),
    "ct_person": ("ok", "Сохранено. Личные данные хранятся только на этом компьютере."),
    "ct_doc": ("ok", "Документ сохранён в папке «Договоры и табель» на этом компьютере и открывается."),
    "ct_template": ("ok", "Шаблон «{file}» — в папке соревнования, открывается "
                          "в Word. Правьте текст как нужно заказчику, поля в двойных фигурных скобках оставьте — "
                          "программа будет брать этот файл."),
    "ct_unknown": ("err", "Документ открывается, но в шаблоне есть поля, которых программа не знает: "
                          "{fields}. Они остались в тексте как есть — поправьте их в шаблоне "
                          "по списку полей ниже."),
}
# если значения нет в адресе: у этих сообщений — своё, у остальных — пусто
DEFAULTS = {"fb_imported": {"added": "0", "skipped": "0"}, "run_imported": {"n": "0", "t": "0"},
            "ct_template": {"file": "Шаблон договора.docx"}}


class _Params(dict):
    """Значения для {имя} в тексте сообщения; чего нет — по умолчанию или пусто."""

    def __init__(self, *a, defaults: dict | None = None, **k):
        super().__init__(*a, **k)
        self.defaults = defaults or {}

    def __missing__(self, key: str) -> str:
        return self.defaults.get(key, "")


def _si_done(q) -> tuple[str, str]:
    """Чтение файла SPORTident: сколько чипов и команд; чипы без команды и замены вписанного вручную."""
    text = f"Прочитано чипов: {q.get('n', '0')}, заполнено команд: {q.get('t', '0')}."
    if q.get("unknown"):
        text += (f" Чипы без команды: {q.get('unknown')} — впишите чип в колонку «Чип» у команды и загрузите файл "
                 "ещё раз.")
    if q.get("replaced"):
        text += f" Время по чипу заменило вписанное вручную у команд: {q.get('replaced')} — проверьте."
    return ("err" if q.get("unknown") else "ok"), text


def _uploaded(q) -> dict:
    """Загрузка заявок: сколько добавлено, заменено, пропущено, разделено; файлы, открытые в Excel."""
    parts = []
    if q.get("added", "0") != "0":
        parts.append(f"добавлено заявок: {q['added']}")
    if q.get("replaced", "0") != "0":
        parts.append(f"заменено исправленными: {q['replaced']}")
    if q.get("skipped", "0") != "0":
        parts.append(f"пропущено файлов не Excel: {q['skipped']}")
    text = ("Готово: " + ", ".join(parts) + ".") if parts else ""
    if q.get("replaced", "0") != "0":
        text += " Прежние варианты заменённых файлов — в папке «Предзаявки\\Прежние версии»."
    if q.get("split"):
        text += (f" Заявки делегаций разделены по командам (файл → команд): {q['split']}. Исходные файлы — в "
                 "папке «Предзаявки\\Заявки делегаций (исходные)».")
    if q.get("locked", "0") != "0":
        return {"kind": "err", "text": f"{text} Не заменено файлов: {q['locked']} — они сейчас открыты в Excel. "
                                       "Закройте их и перетащите заявки ещё раз.".strip()}
    return {"kind": "ok", "text": text} if parts else {"kind": "err", "text": "Файлы не выбраны."}


def flash(request: Request) -> dict | None:
    """Сообщение «Готово…» над страницей по коду ?done=… (MESSAGES); None — сообщения нет."""
    q = request.query_params
    done = q.get("done")
    if done == "uploaded":
        return _uploaded(q)
    auto_done = done is not None and done.endswith("-done")  # заявка без замечаний — «Проверено» сразу
    if auto_done:
        done = done[:-len("-done")]
    if done == "si_done":
        kind, text = _si_done(q)
    elif done in MESSAGES:
        kind, text = MESSAGES[done]
        text = text.format_map(_Params(q, start_time=_start_time_text(q), version=version_label(),
                                       launcher=system.launcher(), defaults=DEFAULTS.get(done))).strip()
    else:
        return None
    if auto_done:
        text = text.replace(" — результат ниже.", ".") + " Замечаний нет — заявка сразу отмечена «Проверено»."
    return {"kind": kind, "text": text}


def preapp_view(files: list[Path], result, reviews: dict) -> dict:
    """Данные для страницы предзаявок: замечания и статусы по файлам (командам), счётчики для фильтров."""
    by_source: dict[str, list[Issue]] = defaultdict(list)
    for i in result.issues:
        by_source[i.source].append(i)
    teams = {t.source: t for t in result.teams}
    groups = []
    for p in files:
        issues = sorted(by_source.get(p.name, []), key=lambda i: (SEVERITY_ORDER[i.severity], i.person))
        worst: dict[str, str] = {}
        for i in issues:
            if i.person and (i.person not in worst or SEVERITY_ORDER[i.severity] < SEVERITY_ORDER[worst[i.person]]):
                worst[i.person] = i.severity
        groups.append({"file": p.name, "team": teams.get(p.name), "issues": issues, "review": reviews[p.name],
                       "counts": Counter(i.severity for i in issues), "worst": worst})
    # фильтр команд: «Ошибки» — есть ошибки или отправлена на исправление, «Проверить» — есть что проверить
    # или ещё не просмотрена, «Проверено» — отмечена секретарём
    by_filter = Counter()
    for g in groups:
        by_filter[g["review"].status] += 1
    return {"groups": groups, "general": by_source.get("", []), "by_filter": by_filter,
            "totals": Counter(i.severity for i in result.issues)}


def fix_url(base: str, i) -> str:
    """«Исправить» у замечания: адрес страницы, где это правят, с полем для курсора (?focus=имя или id поля;
    «блок/поле» — поле внутри блока, например команды на странице допуска). Пусто — вести некуда (например,
    расхождение в чужом документе при сверке: исправляют в самом документе)."""
    get = i.get if isinstance(i, dict) else lambda k, d="": getattr(i, k, d)
    target = get("target", "") or ""
    kind, _, rest = target.partition(":")
    z = {"z": get("source", "")} if get("source", "") else {}

    def at(path: str, focus: str = "", anchor: str = "", **params) -> str:
        q = {**params, **({"focus": focus} if focus else {})}
        return f"{base}{path}" + (f"?{urlencode(q)}" if q else "") + (f"#{anchor}" if anchor else "")

    if kind == "rate":
        return at("/contracts", f"rate-{rest}", "settings")
    if kind == "tabel":
        return at("/contracts", f"tabel-{team_anchor(rest)}", "tabel")
    if kind == "person":
        key, _, fld = rest.partition(":")
        return at("/contracts/person", f"f-{fld}" if fld else "", key=key)
    if kind == "customer":
        return at("/contracts", "f-c-name", "customer")
    if kind == "card":
        return at("/card/edit", anchor=rest)
    if kind == "cell":
        file, _, fld = rest.rpartition(":")
        return at("/results", f"c-{team_anchor(file)}-{fld}", "points", **z)
    if kind == "stages":
        return at("/results", rest, "stages", **z)
    if kind == "judgelog":  # журнал этапа на странице «Телефоны судей этапов»
        return at("/judges", "", f"log-{rest}", **z)
    if kind == "start":
        section = {"first": "times", "order": "order", "publish": "publish"}.get(rest, "")
        return at("/start", rest if rest == "first" else "", section, **z)
    if kind == "preapp":
        return at("/preapps/team", file=rest)
    if kind == "adm":
        file, _, fld = rest.partition("/")
        return at("/admission", f"{team_anchor(file)}/{fld}", team_anchor(file))
    if kind == "reentry":
        return at("/preapps/edit", file=rest, reentry="1")
    if kind == "form":  # заявка похожа на свою форму, но не совпала — изменить форму по этому файлу (п. 41)
        return at("/forms/from-preapp", file=rest)
    if kind == "equipment":
        return at("/equipment", anchor=team_anchor(rest))
    return ""

