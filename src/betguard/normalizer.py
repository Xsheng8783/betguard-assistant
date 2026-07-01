from __future__ import annotations


def normalize_input(text: str) -> str:
    """Only trim outer whitespace; never rewrite betting content."""
    return text.strip()
