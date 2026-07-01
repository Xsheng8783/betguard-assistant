from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any


REPEATED_DOT_SPLIT_PATTERN = re.compile(r"\.{2,}")
UNIT_WORD_SAFE = "\u652f"
STAR_AMOUNT_CONTINUATION_PATTERN = re.compile(
    r"^(?:"
    r"(?:[234](?:\.[234]){1,2}|[234]{2,3})(?:星)?|"
    r"(?:二|兩|三|四)(?:、(?:二|兩|三|四)){1,2}(?:星)?|"
    r"(?:二三|兩三|三四|二三四|兩三四)(?:星)?"
    r")\s*(?:[xX*]|\.[xX])\s*\d+(?:\.\d+)?(?:支|元|塊)?$"
)
MULTI_CAR_PATTERN = re.compile(
    r"^(?P<numbers>\d{1,2}(?:[\s、,，]+\d{1,2})+)\s*車\s*(?P<amount>\d+(?:\.\d+)?)(?P<kind>支|元|塊)?$"
)
HYPHEN_CAR_PATTERN = re.compile(r"^(?P<number>\d{1,2})-(?P<amount>\d+(?:\.\d+)?)\s*車$")
TIME_ONLY_PATTERN = re.compile(r"^(?:上午|下午)?\s*\d{1,2}:\d{2}$")
LINE_PREFIX_PATTERN = re.compile(r"^(?P<time>(?:上午|下午)?\d{1,2}:\d{2})\s+(?P<sender>\S+)\s+(?P<body>.+)$")
DATE_ONLY_PATTERN = re.compile(r"^\d{4}[/-]\d{1,2}[/-]\d{1,2}$")
CHINESE_DATE_PATTERN = re.compile(r"^\d{1,2}月\d{1,2}日(?:\s+星期[一二三四五六日天])?$")
LINE_EXPORT_PATTERN = re.compile(r"^\d{4}[/-]\d{1,2}[/-]\d{1,2}\s+\d{1,2}:\d{2}\s+.+$")
STANDALONE_GAME_LABELS = {"天天樂", "天天", "539", "港", "hk", "HK"}
STANDALONE_GAME_LABELS.update({"???", "??", "??", "?"})
KNOWN_METADATA_LINES = {
    "已讀",
    "以下是投注",
    "以下投注",
    "投注如下",
    "下牌如下",
}
BET_KEYWORD_CHARS = set("二三四兩星元塊支車尾碰今彩天天樂港六合大")
BET_SYMBOLS = set("./-、,，xX*×=()（）")
MULTIPLIER_TRANSLATION = str.maketrans({"＊": "*", "Ｘ": "X", "ｘ": "x", "ㄨ": "x"})


def preprocess_batch_input(text_or_lines: str | Iterable[str]) -> dict[str, Any]:
    """Split pasted chat text into parser-ready betting fragments.

    The preprocessor deliberately does not decide whether a fragment is valid.
    Suspicious betting-looking fragments stay in candidate_bet_lines so the
    parser/validator can produce auditable errors instead of silently dropping
    them.
    """

    original_text = _coerce_original_text(text_or_lines)
    ignored_metadata_lines: list[dict[str, Any]] = []
    logical_lines: list[dict[str, Any]] = []
    pending: dict[str, Any] | None = None

    for line_no, raw_line in enumerate(original_text.splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        if is_metadata_line(line):
            ignored_metadata_lines.append({"line_no": line_no, "raw": line})
            continue

        cleaned, notes = _clean_line_content(line)
        if not cleaned:
            ignored_metadata_lines.append({"line_no": line_no, "raw": line, "reason": "empty after cleanup"})
            continue
        if is_metadata_line(cleaned):
            ignored_metadata_lines.append({"line_no": line_no, "raw": line, "cleaned": cleaned})
            continue

        entry = {
            "line_no": line_no,
            "raw": cleaned,
            "original_lines": [line],
            "preprocessing_notes": notes,
        }
        if pending is not None and _looks_like_continuation(cleaned):
            pending["raw"] = f"{pending['raw'].rstrip(' .')} {cleaned}"
            pending["original_lines"].append(line)
            pending["preprocessing_notes"].extend(notes)
            pending["preprocessing_notes"].append("merged continuation line")
            continue

        if pending is not None:
            logical_lines.append(pending)
        pending = entry

    if pending is not None:
        logical_lines.append(pending)

    candidate_bet_lines: list[dict[str, Any]] = []
    for logical_index, logical in enumerate(logical_lines, start=1):
        fragments = [fragment.strip() for fragment in REPEATED_DOT_SPLIT_PATTERN.split(logical["raw"])]
        for fragment_index, fragment in enumerate(fragments, start=1):
            fragment = _trim_fragment(fragment)
            if not fragment:
                continue
            fragment_notes = _dedupe(list(logical.get("preprocessing_notes", [])) + _suspicious_paste_notes(fragment))
            expanded_fragments = _expand_multi_car_fragment(fragment)
            for expanded_index, expanded in enumerate(expanded_fragments, start=1):
                expanded_notes = fragment_notes
                if len(expanded_fragments) > 1:
                    expanded_notes = _dedupe(expanded_notes + ["expanded multi-car line"])
                candidate_bet_lines.append(
                {
                    "line_no": logical.get("line_no"),
                    "logical_index": logical_index,
                    "fragment_index": fragment_index if len(expanded_fragments) == 1 else expanded_index,
                    "raw": expanded,
                    "original_line": logical.get("original_lines", [""])[0],
                    "original_lines": list(logical.get("original_lines", [])),
                    "preprocessing_notes": expanded_notes,
                }
            )

    return {
        "original_text": original_text,
        "candidate_bet_lines": candidate_bet_lines,
        "ignored_metadata_lines": ignored_metadata_lines,
        "summary": {
            "candidate_count": len(candidate_bet_lines),
            "ignored_metadata_count": len(ignored_metadata_lines),
        },
    }


def is_metadata_line(line: str) -> bool:
    value = line.strip()
    if not value:
        return True
    if value in STANDALONE_GAME_LABELS or value.strip(" .。") in STANDALONE_GAME_LABELS:
        return True
    if value in KNOWN_METADATA_LINES:
        return True
    if TIME_ONLY_PATTERN.match(value):
        return True
    if DATE_ONLY_PATTERN.match(value):
        return True
    if CHINESE_DATE_PATTERN.match(value):
        return True
    if LINE_EXPORT_PATTERN.match(value):
        return True
    return _looks_like_speaker_name(value)


def _looks_like_speaker_name(value: str) -> bool:
    if any(char.isdigit() for char in value):
        return False
    if any(char in BET_SYMBOLS for char in value):
        return False
    if any(char in BET_KEYWORD_CHARS for char in value):
        return False
    if len(value) > 40:
        return False
    if re.fullmatch(r"[A-Za-z][A-Za-z ._-]*", value):
        return True
    if re.fullmatch(r"[\u4e00-\u9fff]{2,5}", value):
        return True
    return False


def _coerce_original_text(text_or_lines: str | Iterable[str]) -> str:
    if isinstance(text_or_lines, str):
        return text_or_lines
    return "\n".join(str(line) for line in text_or_lines)


def _trim_fragment(fragment: str) -> str:
    return fragment.strip(" \t\r\n.。;；")


def _clean_line_content(line: str) -> tuple[str, list[str]]:
    notes: list[str] = []
    value = line.strip()
    prefix = LINE_PREFIX_PATTERN.match(value)
    if prefix:
        value = prefix.group("body").strip()
        notes.append("removed LINE time/sender prefix")

    updated = value.translate(MULTIPLIER_TRANSLATION)
    if updated != value:
        if "ㄨ" in value:
            notes.append("normalized ㄨ multiplier to x")
        if "＊" in value or "Ｘ" in value or "ｘ" in value:
            notes.append("normalized multiplier symbol")
    value = updated

    normalized_star_line = _normalize_star_amount_continuation(value)
    if normalized_star_line != value:
        notes.append("normalized dotted star amount continuation")
        value = normalized_star_line

    value = _remove_trailing_game_label(value, notes)
    return re.sub(r"[ \t]+", " ", value).strip(), _dedupe(notes)


def _remove_trailing_game_label(value: str, notes: list[str]) -> str:
    updated = re.sub(r"\s*[（(]\s*(?:天天樂|天天|539|hk|HK|港)\s*[）)]\s*$", "", value).strip()
    if updated != value:
        notes.append("removed trailing game label")
        value = updated

    updated = re.sub(r"\s*(?:天天樂|天天)\s*$", "", value).strip()
    if updated != value:
        notes.append("removed trailing game label")
        value = updated
    return value


def _looks_like_continuation(value: str) -> bool:
    compact = value.replace(" ", "")
    if STAR_AMOUNT_CONTINUATION_PATTERN.fullmatch(compact):
        return True
    star = r"(?:[2345]{1,3}星?|[二兩三四五]+星?|二三四|兩三四|二三|兩三|三四)"
    if re.fullmatch(rf"{star}[-xX×*]\d+(?:\.\d+)?(?:元|塊|支)?", compact):
        return True
    if re.fullmatch(rf"{star}\d+(?:\.\d+)?(?:元|塊|支)?", compact):
        return True
    return False


def _normalize_star_amount_continuation(value: str) -> str:
    compact = value.replace(" ", "")
    match = re.fullmatch(r"(?P<stars>[234](?:\.[234]){1,2})\.[xX](?P<amount>\d+(?:\.\d+)?(?:支|元|塊)?)", compact)
    if not match:
        return value
    return f"{match.group('stars').replace('.', '')}星X{match.group('amount')}"


def _suspicious_paste_notes(value: str) -> list[str]:
    notes: list[str] = []
    compact = value.replace(" ", "")
    if STAR_AMOUNT_CONTINUATION_PATTERN.fullmatch(compact):
        notes.append("standalone star amount line requires manual review")
    if "、" in value and "/" not in value and re.search(r"234\s*星\s*[xX]\s*\d", value):
        notes.append("numeric star code with dunhao numbers requires manual review")
    if "、" in value and "/" in value:
        after_dunhao = value.split("、", 1)[1]
        if "/" in after_dunhao:
            notes.append("ambiguous slash/dunhao column grouping requires manual review")
    if any(token in value for token in ("半車", "坪", "改", "嫌")):
        notes.append("suspicious pasted token requires manual review")
    if value.lower().startswith("港"):
        notes.append("game prefix requires manual review")
    if (
        re.search(r"-\d+(?:\.\d+)?$", compact)
        and not re.search(r"/\d+$", compact)
        and not _is_confirmed_hyphen_amount(value)
    ):
        notes.append("hyphen amount requires manual review")
    if re.fullmatch(r"\d{1,2}[xX×*]\d+(?:\.\d+)?", compact):
        notes.append("single-number multiplier requires manual review")
    if (
        re.search(r"=\s*\d+", value)
        and "." in value.split("=")[0]
        and len(re.findall(r"\d{1,2}", value.split("=")[0])) >= 3
        and not _is_confirmed_star_equals_amount(value)
    ):
        notes.append("equals amount with three or more numbers requires manual review")
    return _dedupe(notes)


def _is_confirmed_hyphen_amount(value: str) -> bool:
    return bool(
        re.fullmatch(
            r"\s*\d{1,2}(?:[.\-\s、,，]+\d{1,2}){2,}\s*-\s*(?:50|100)\s*",
            value,
        )
    )


def _is_confirmed_star_equals_amount(value: str) -> bool:
    return bool(
        re.fullmatch(
            r"\s*\d{1,2}(?:[.\-\s、,，]+\d{1,2})+\.?\s*=\s*[234](?:[.、,，]\s*[234]){0,2}\s*=\s*\d+(?:\.\d+)?(?:支|元|塊)?\s*",
            value,
        )
    )


def _expand_multi_car_fragment(value: str) -> list[str]:
    hyphen_car = HYPHEN_CAR_PATTERN.fullmatch(value.strip())
    if hyphen_car:
        return [f"{hyphen_car.group('number')}車{hyphen_car.group('amount')}支"]

    match = MULTI_CAR_PATTERN.fullmatch(value.strip())
    if not match:
        return [value]
    amount = match.group("amount")
    kind = match.group("kind") or UNIT_WORD_SAFE
    numbers = [number for number in re.split(r"[\s、,，]+", match.group("numbers").strip()) if number]
    return [f"{number}車{amount}{kind}" for number in numbers]


def _dedupe(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value not in seen:
            result.append(value)
            seen.add(value)
    return result
