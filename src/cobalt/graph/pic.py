"""PICTURE string analysis: storage size, category and numeric attributes."""

from __future__ import annotations

import re
from dataclasses import dataclass

_REPEAT = re.compile(r"(.)\((\d+)\)")

BINARY_USAGES = {
    "COMP",
    "COMP-4",
    "COMP-5",
    "BINARY",
    "COMPUTATIONAL",
    "COMPUTATIONAL-4",
    "COMPUTATIONAL-5",
}
PACKED_USAGES = {"COMP-3", "COMPUTATIONAL-3", "PACKED-DECIMAL"}
FIXED_SIZE_USAGES = {
    "COMP-1": 4,
    "COMPUTATIONAL-1": 4,
    "COMP-2": 8,
    "COMPUTATIONAL-2": 8,
    "INDEX": 4,
    "POINTER": 8,
    "FUNCTION-POINTER": 8,
    "PROCEDURE-POINTER": 8,
}


@dataclass(frozen=True)
class PicInfo:
    """Facts derived from one PICTURE clause."""

    picture: str
    category: str  # alphanumeric | alphabetic | numeric | numeric-edited | alnum-edited
    digits: int  # integer + fraction digit positions (numeric kinds)
    scale: int  # digits after the implied/actual decimal point
    signed: bool
    display_length: int  # characters when USAGE DISPLAY


def expand_picture(picture: str) -> str:
    """Expand repeat counts: ``9(3)V99`` -> ``999V99``."""
    return _REPEAT.sub(lambda m: m.group(1) * int(m.group(2)), picture.upper())


def analyze_pic(picture: str, *, sign_separate: bool = False) -> PicInfo:
    """Classify a PICTURE and count its positions."""
    pic = expand_picture(picture)
    body = pic.replace("CR", "C").replace("DB", "D")
    signed = pic.startswith("S")
    edit_chars = set("Z*+-,./B0$CD")
    if set(body) <= set("X"):
        category = "alphanumeric"
    elif set(body) <= set("A"):
        category = "alphabetic"
    elif set(body) <= set("SVP9"):
        category = "numeric"
    elif set(body) & set("XA"):
        category = "alnum-edited"
    else:
        category = "numeric-edited" if set(body) & edit_chars else "numeric"

    # display length: every symbol is one position except S, V, P (CR/DB are 2)
    length = 0
    for ch in pic.replace("CR", "C").replace("DB", "D"):
        if ch in "SVP":
            continue
        length += 2 if ch in "CD" else 1
    if signed and sign_separate:
        length += 1

    digits = 0
    scale = 0
    if category in ("numeric", "numeric-edited"):
        int_part, _, frac_part = pic.partition("V") if "V" in pic else pic.partition(".")
        frac_digits = sum(frac_part.count(c) for c in "9Z*")
        int_digits = sum(int_part.count(c) for c in "9Z*")
        # floating insertion strings ($$$, +++, ---) have one non-digit position
        for symbol in "$+-":
            run = int_part.count(symbol)
            if run > 1:
                int_digits += run - 1
        digits = int_digits + frac_digits
        scale = frac_digits
    return PicInfo(picture, category, digits, scale, signed, length)


def storage_size(pic: PicInfo | None, usage: str | None, *, sign_separate: bool = False) -> int:
    """Bytes occupied by an elementary item."""
    usage = (usage or "DISPLAY").upper()
    if usage in FIXED_SIZE_USAGES:
        return FIXED_SIZE_USAGES[usage]
    if pic is None:
        return 0
    if usage in PACKED_USAGES:
        return pic.digits // 2 + 1
    if usage in BINARY_USAGES:
        if pic.digits <= 4:
            return 2
        if pic.digits <= 9:
            return 4
        return 8
    return pic.display_length
