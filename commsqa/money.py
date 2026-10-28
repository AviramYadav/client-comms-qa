"""Formatting and date helpers shared by the document generator and the QA rules."""
from __future__ import annotations

from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

# Fixed-date national holidays only (illustrative settlement calendar).
HOLIDAYS = {date(2026, 1, 26), date(2026, 8, 15), date(2026, 10, 2)}


def d2(x) -> Decimal:
    return Decimal(str(x)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def fmt_inr(x) -> str:
    """Indian digit grouping: 12345678.5 -> 1,23,45,678.50"""
    value = d2(x)
    sign = "-" if value < 0 else ""
    whole, frac = f"{abs(value):.2f}".split(".")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        whole = ",".join(groups + [tail])
    return f"{sign}{whole}.{frac}"


def parse_amount(text: str) -> Decimal:
    return d2(text.replace(",", "").strip())


def is_business_day(d: date) -> bool:
    return d.weekday() < 5 and d not in HOLIDAYS


def add_business_days(d: date, n: int) -> date:
    while n > 0:
        d += timedelta(days=1)
        if is_business_day(d):
            n -= 1
    return d


# ISIN check digit: letters -> numbers (A=10..Z=35), then Luhn over the digit string.
def isin_check_digit(first11: str) -> str:
    digits = "".join(str(int(c, 36)) for c in first11)
    total = 0
    for i, ch in enumerate(reversed(digits)):
        n = int(ch)
        if i % 2 == 0:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return str((10 - total % 10) % 10)


def isin_valid(isin: str) -> bool:
    return len(isin) == 12 and isin[:2].isalpha() and isin[-1].isdigit() and isin_check_digit(isin[:11]) == isin[-1]
