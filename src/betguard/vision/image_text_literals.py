"""Lossless spelling changes for explicit OCR text, never inferred bet values."""

import re


FORMATTER_VERSION = "betguard-image-text-literals-v1"
_CAR_LITERAL_LINE_RE = re.compile(
    r"(?P<indent>[ \t]*)(?P<number>[0-9]{1,2})[ \t]*[xX×][ \t]*"
    r"(?P<amount>[0-9]+(?:\.[0-9]+)?)[ \t]*車(?P<trailing>[ \t]*)"
)


def normalize_parser_safe_literals(text: str) -> tuple[str, int]:
    """Spell ``N × Q車`` as ``N車Q支`` with the same explicit car quantity.

    Bare ``N車10`` means money in the existing parser; dropping the quantity
    marker would change 10 cars into 10 currency units. Do not rewrite that
    already-legal money syntax, uncertain text, or multi-number expressions.
    Preserve unrelated characters, whitespace and line endings byte-for-byte.
    """
    lines = []
    changed = 0
    for line in text.splitlines(keepends=True):
        content = line.rstrip("\r\n")
        ending = line[len(content):]
        match = _CAR_LITERAL_LINE_RE.fullmatch(content)
        if match is None:
            lines.append(line)
            continue
        lines.append(
            f"{match['indent']}{match['number']}車{match['amount']}支"
            f"{match['trailing']}{ending}"
        )
        changed += 1
    return "".join(lines), changed
