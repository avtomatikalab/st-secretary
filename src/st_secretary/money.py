"""Суммы для договоров, актов и табеля: «4 250,00» и «4250 (четыре тысячи двести пятьдесят) рублей 00 копеек»."""

from __future__ import annotations

from st_secretary.textclean import plural

_ONES = ["", "один", "два", "три", "четыре", "пять", "шесть", "семь", "восемь", "девять"]
_ONES_F = ["", "одна", "две"] + _ONES[3:]  # тысяча — женского рода: одна тысяча, две тысячи
_TEENS = ["десять", "одиннадцать", "двенадцать", "тринадцать", "четырнадцать", "пятнадцать", "шестнадцать",
          "семнадцать", "восемнадцать", "девятнадцать"]
_TENS = ["", "", "двадцать", "тридцать", "сорок", "пятьдесят", "шестьдесят", "семьдесят", "восемьдесят",
         "девяносто"]
_HUNDREDS = ["", "сто", "двести", "триста", "четыреста", "пятьсот", "шестьсот", "семьсот", "восемьсот",
             "девятьсот"]
_SCALES = [(None, False), (("тысяча", "тысячи", "тысяч"), True), (("миллион", "миллиона", "миллионов"), False),
           (("миллиард", "миллиарда", "миллиардов"), False)]


def _triple(n: int, feminine: bool) -> list[str]:
    out = [_HUNDREDS[n // 100]]
    rest = n % 100
    if 10 <= rest <= 19:
        out.append(_TEENS[rest - 10])
    else:
        out += [_TENS[rest // 10], (_ONES_F if feminine else _ONES)[rest % 10]]
    return [w for w in out if w]


def number_words(n: int) -> str:
    """4250 → «четыре тысячи двести пятьдесят»."""
    if n == 0:
        return "ноль"
    if n < 0:
        return "минус " + number_words(-n)
    words: list[str] = []
    for i, (forms, fem) in enumerate(_SCALES):
        part = (n // 1000 ** i) % 1000
        if not part:
            continue
        chunk = _triple(part, fem)
        if forms:
            chunk.append(plural(part, *forms))
        words = chunk + words
    return " ".join(words)


def money(amount: float) -> str:
    """4250 → «4 250,00» (как в актах)."""
    kop = round(amount * 100)
    rub, k = divmod(abs(kop), 100)
    return ("−" if kop < 0 else "") + f"{rub:,}".replace(",", " ") + f",{k:02d}"


def rubles_text(amount: float) -> str:
    """4250 → «4250 (четыре тысячи двести пятьдесят) рублей 00 копеек»."""
    kop = round(amount * 100)
    rub, k = divmod(kop, 100)
    return (f"{rub} ({number_words(rub)}) {plural(rub, 'рубль', 'рубля', 'рублей')} "
            f"{k:02d} {plural(k, 'копейка', 'копейки', 'копеек')}")
