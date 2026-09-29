"""SPORTident Reader: файл считанных чипов (si_reader.csv, формат «Config+ (card readout)»).

Точка с запятой, первая строка — заголовок: No;Read on;SIID;Start no;Clear CN;…;Start CN;Start DOW;Start time;…;
Finish CN;Finish DOW;Finish time;…;No. of records;1.CN;1.DOW;1.Time;2.CN;… Так его читает и СЕКРЕТАРЬ_ST
(макрос ImportFromSIReader), только берёт последнюю строку; здесь — все карты сразу: чип привязан к команде.
Если чип считывали несколько раз — берётся последнее считывание.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path

from st_secretary.time_run import parse_clock


@dataclass
class Card:
    siid: str
    start: Fraction | None
    finish: Fraction | None
    punches: list[tuple[str, Fraction]] = field(default_factory=list)  # (номер станции, время)
    read_on: str = ""


def _decode(data: bytes) -> str:
    for enc in ("utf-8-sig", "cp1251"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1")


def _clock(v: str) -> Fraction | None:
    try:
        return parse_clock(v)
    except ValueError:
        return None


def read_si_reader(src: str | Path | bytes) -> list[Card]:
    """Карты из файла SI Reader. Не тот файл — ValueError с объяснением."""
    data = src if isinstance(src, bytes) else Path(src).read_bytes()
    lines = [ln for ln in _decode(data).splitlines() if ln.strip()]
    if not lines:
        raise ValueError("файл пустой")
    head = [h.strip() for h in lines[0].split(";")]
    idx = {h: i for i, h in enumerate(head)}
    need = ("SIID", "Start time", "Finish time")
    if not all(h in idx for h in need):
        raise ValueError("это не файл SI Reader: нужен экспорт «Config+ (card readout)» с колонками "
                         "SIID, Start time, Finish time")
    rec = idx.get("No. of records", idx.get("No. of punches"))
    cards: dict[str, Card] = {}
    for ln in lines[1:]:
        f = [x.strip() for x in ln.split(";")]
        if len(f) <= idx["Finish time"] or not f[idx["SIID"]]:
            continue
        punches = []
        if rec is not None and rec < len(f) and f[rec].isdigit():
            for k in range(int(f[rec])):
                base = rec + 1 + 3 * k
                if base + 2 < len(f) and (t := _clock(f[base + 2])) is not None:
                    punches.append((f[base], t))
        siid = f[idx["SIID"]]
        cards[siid] = Card(siid, _clock(f[idx["Start time"]]), _clock(f[idx["Finish time"]]), punches,
                           f[idx["Read on"]] if "Read on" in idx and idx["Read on"] < len(f) else "")
    return list(cards.values())


def parse_pairs(text: str) -> list[tuple[str, str]]:
    """«31-32; 41-42» → [("31", "32"), ("41", "42")] — станции начала и конца отсечки."""
    out = []
    for part in str(text or "").replace(",", ";").split(";"):
        a, sep, b = part.strip().partition("-")
        if sep and a.strip().isdigit() and b.strip().isdigit():
            out.append((a.strip(), b.strip()))
    return out


def cutoffs_from(card: Card, pairs: list[tuple[str, str]]) -> Fraction | None:
    """Сумма отсечек по парам станций (отметка на станции начала и следующая — на станции конца).
    None — отметок отсечек на чипе нет."""
    total, found = Fraction(0), False
    for a, b in pairs:
        start = None
        for cn, t in card.punches:
            if cn == a and start is None:
                start = t
            elif cn == b and start is not None:
                total += (t - start) if t >= start else (t + 86400 - start)
                found, start = True, None
    return total if found else None
