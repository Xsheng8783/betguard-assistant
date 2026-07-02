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
MULTI_FULL_CAR_EACH_PATTERN = re.compile(
    r"^(?P<numbers>\d{1,2}(?:[.\s、,，]+\d{1,2})+)\s*全車各(?P<amount>\d+(?:\.\d+)?)車$"
)
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
BET_KEYWORD_CHARS = set("二三四兩两星元塊支車尾碰今彩天天樂港六合大")
BET_SYMBOLS = set("./-、,，xX*×=()（）")
MULTIPLIER_TRANSLATION = str.maketrans({"＊": "*", "Ｘ": "X", "ｘ": "x"})


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
        if pending is not None and (
            (_looks_like_continuation(cleaned) and not _pending_has_confirmed_amount(pending["raw"]))
            or _looks_like_amount_continuation_for_pending(pending["raw"], cleaned)
        ):
            pending["raw"] = _normalize_dotted_star_before_amount(f"{pending['raw'].rstrip(' .')} {cleaned}")
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
        raw_fragments = [
            split_fragment.strip()
            for fragment in REPEATED_DOT_SPLIT_PATTERN.split(logical["raw"])
            for split_fragment in _split_after_embedded_star_amount(fragment)
        ]
        fragments, inline_notes = _merge_inline_star_amount_fragments(raw_fragments)
        for fragment_index, fragment in enumerate(fragments, start=1):
            fragment = _trim_fragment(fragment)
            if not fragment:
                continue
            fragment_notes = _dedupe(
                list(logical.get("preprocessing_notes", [])) + inline_notes + _suspicious_paste_notes(fragment)
            )
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


def _split_after_embedded_star_amount(fragment: str) -> list[str]:
    value = fragment.strip()
    match = re.fullmatch(
        r"(?P<head>.*?(?:234|2\.3\.4)\.(?:50|100))\.(?P<tail>\d{1,2}(?:[.\s、,，-]+\d{1,2})+.*)",
        value,
    )
    if not match:
        return [fragment]
    return [match.group("head"), match.group("tail")]


def _merge_inline_star_amount_fragments(fragments: list[str]) -> tuple[list[str], list[str]]:
    merged: list[str] = []
    notes: list[str] = []
    for fragment in fragments:
        value = fragment.strip()
        if (
            merged
            and _looks_like_number_fragment_without_amount(merged[-1])
            and _looks_like_inline_star_amount_fragment(value)
        ):
            merged[-1] = _normalize_dotted_star_before_amount(f"{merged[-1].rstrip()} {value}")
            notes.append("merged continuation star amount")
            continue
        merged.append(value)
    return merged, _dedupe(notes)


def _looks_like_number_fragment_without_amount(value: str) -> bool:
    stripped = value.strip()
    if _pending_has_confirmed_amount(stripped):
        return False
    return bool(re.fullmatch(r"\d{1,2}(?:[.\s、,，-]+\d{1,2}){2,}", stripped))


def _looks_like_inline_star_amount_fragment(value: str) -> bool:
    compact = value.replace(" ", "")
    return bool(
        re.fullmatch(
            r"(?:234|2\.3\.4)(?:\.|[xX*×])\d+(?:\.\d+)?(?:支|元|塊)?(?:\u81c2)?",
            compact,
        )
    )


def _clean_line_content(line: str) -> tuple[str, list[str]]:
    notes: list[str] = []
    value = line.strip()
    prefix = LINE_PREFIX_PATTERN.match(value)
    if prefix:
        value = prefix.group("body").strip()
        notes.append("removed LINE time/sender prefix")

    updated = value.translate(MULTIPLIER_TRANSLATION)
    if updated != value:
        if "＊" in value or "Ｘ" in value or "ｘ" in value:
            notes.append("normalized multiplier symbol")
    value = updated

    updated = _normalize_confirmed_star_text(value, notes)
    value = updated

    normalized_ellipsis = _normalize_ellipsis_separator(value)
    if normalized_ellipsis != value:
        notes.append("normalized ellipsis separator")
        value = normalized_ellipsis

    value = _remove_leading_game_label(value, notes)

    normalized_inline_amount = _normalize_inline_star_amount_separator(value)
    if normalized_inline_amount != value:
        notes.append("normalized dotted star amount continuation")
        value = normalized_inline_amount

    normalized_star_line = _normalize_star_amount_continuation(value)
    if normalized_star_line != value:
        notes.append("normalized dotted star amount continuation")
        value = normalized_star_line

    value = _remove_confirmed_equals_metadata(value, notes)
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


def _remove_leading_game_label(value: str, notes: list[str]) -> str:
    updated = re.sub(r"^\s*(?:天天樂|天天)\s+", "", value).strip()
    if updated != value:
        notes.append("removed game label metadata")
    return updated


def _normalize_confirmed_star_text(value: str, notes: list[str]) -> str:
    updated = re.sub(
        r"(?P<star>(?:[234](?:[.,、，]?[234]){0,2}|[二兩两三四]+星?|二三四|兩三四|二三|兩三|三四))ㄨ(?=\d)",
        lambda match: f"{match.group('star')}x",
        value,
    )
    if updated != value:
        notes.append("normalized ㄨ to x")
        value = updated

    updated = re.sub(r"(?<=\d)两(?=三(?:星)?[xX×*]?\d)", "兩", value)
    if updated != value:
        notes.append("normalized 两 star token")
    return updated


def _remove_confirmed_equals_metadata(value: str, notes: list[str]) -> str:
    if "=" not in value:
        return value
    updated = re.sub(r"\s*[、,，]\s*$", "", value).strip()
    if updated != value:
        notes.append("removed trailing equals metadata")
        value = updated
    updated = re.sub(r"\s*[、,，]\s*(?:539\s*)?(?:hk|HK)?坪\s*$", "", value).strip()
    updated = re.sub(r"\s*(?:539\s*)?(?:hk|HK)?坪\s*$", "", updated).strip()
    if updated != value:
        notes.append("removed trailing equals metadata")
    return updated


def _looks_like_continuation(value: str) -> bool:
    compact = value.replace(" ", "")
    if _looks_like_star_amount_continuation(compact):
        return True
    if STAR_AMOUNT_CONTINUATION_PATTERN.fullmatch(compact):
        return True
    star = r"(?:[2345]{1,3}星?|[二兩三四五]+星?|二三四|兩三四|二三|兩三|三四)"
    if re.fullmatch(rf"{star}[-xX×*]\d+(?:\.\d+)?(?:元|塊|支)?", compact):
        return True
    if re.fullmatch(rf"{star}\d+(?:\.\d+)?(?:元|塊|支)?", compact):
        return True
    return False


def _pending_has_confirmed_amount(value: str) -> bool:
    return bool(
        re.search(
            r"\s(?:[234](?:[.,、，]?[234]){0,2}|[234]星)[xX×*]?\d+(?:\.\d+)?(?:支|元|塊)?$",
            value,
        )
        or re.search(
            r"(?:二三四|兩三四|二三|兩三|三四|二星|兩星|三星|四星)\d+(?:\.\d+)?(?:支|元|塊)?$",
            value.replace(" ", ""),
        )
    )


def _looks_like_star_amount_continuation(compact: str) -> bool:
    return bool(
        re.fullmatch(r"[234](?:[.,、，][234]){1,2}[xX×*]\d+(?:\.\d+)?(?:支|元|塊)?", compact)
        or re.fullmatch(r"(?:[234](?:\.[234]){1,2}|[234]{2,3})\.\d+(?:支|元|塊)?", compact)
        or re.fullmatch(r"(?:[234](?:\.[234]){1,2}|[234]{2,3})\.\d+(?:支|元|塊)?臂", compact)
        or re.fullmatch(r"(?:[234](?:\.[234]){1,2}|[234]{2,3})[xX×*]\d+(?:\.\d+)?(?:支|元|塊)?", compact)
    )


def _looks_like_amount_continuation_for_pending(pending_raw: str, value: str) -> bool:
    if not re.fullmatch(r"\d+(?:\.\d+)?(?:支|元|塊)?", value.strip()):
        return False
    compact = pending_raw.replace(" ", "")
    return bool(re.search(r"(?:[234](?:[.,、，][234]){1,2}|[234]{2,3})$", compact))


def _normalize_ellipsis_separator(value: str) -> str:
    updated = re.sub(r"\s*…+\s*", " ", value).strip()
    updated = re.sub(r"(?<=\s)\.(?=\d)", "", updated)
    return _normalize_dotted_star_before_amount(updated)


def _normalize_dotted_star_before_amount(value: str) -> str:
    return re.sub(
        r"(?<=\s)([234](?:\.[234]){1,2})(?=\s+\d)",
        lambda match: match.group(1).replace(".", ""),
        value,
    )


def _normalize_inline_star_amount_separator(value: str) -> str:
    updated = re.sub(
        r"(?<=\s)([234](?:\.[234]){1,2})\.{2,}(\d+(?:\.\d+)?(?:支|元|塊)?)\s*$",
        r"\1 \2",
        value,
    )
    return _normalize_dotted_star_before_amount(updated)


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
    if (
        "、" in value
        and "/" not in value
        and re.search(r"234\s*星\s*[xX]\s*\d", value)
        and not _is_confirmed_numeric_star_dunhao_amount(value)
    ):
        notes.append("numeric star code with dunhao numbers requires manual review")
    if "、" in value and "/" in value and not _is_confirmed_column_dunhao_amount(value):
        after_dunhao = value.split("、", 1)[1]
        if "/" in after_dunhao:
            notes.append("ambiguous slash/dunhao column grouping requires manual review")
    if "改" in value:
        notes.append("suspicious pasted token requires manual review")
    if any(token in value for token in ("半車", "坪", "嫌")):
        if _is_confirmed_car_metadata_format(value):
            notes.append("car metadata ignored")
        else:
            notes.append("suspicious pasted token requires manual review")
    if value.lower().startswith("港"):
        notes.append("game prefix requires manual review")
    if (
        re.search(r"-\d+(?:\.\d+)?$", compact)
        and not re.search(r"/\d+$", compact)
        and not _is_confirmed_hyphen_amount(value)
        and not _is_confirmed_car_shorthand(value)
    ):
        notes.append("hyphen amount requires manual review")
    if re.fullmatch(r"\d{1,2}[xX×*]\d+(?:\.\d+)?", compact) and not _is_confirmed_car_shorthand(value):
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
            r"\s*\d{1,2}(?:[.\-\s、,，]+\d{1,2})+\.?\s*(?:=\s*[234](?:[.、,，]\s*[234]){0,2}\s*)?=\s*\d+(?:\.\d+)?(?:支|元|塊)?\s*",
            value,
        )
    )


def _is_confirmed_column_dunhao_amount(value: str) -> bool:
    return bool(re.fullmatch(r"\s*\d{1,2}(?:[./、,，-]+\d{1,2})+\s+[234]{2,3}星?[xX×*]\d+(?:\.\d+)?\s*", value))


def _is_confirmed_numeric_star_dunhao_amount(value: str) -> bool:
    return bool(re.fullmatch(r"\s*\d{1,2}(?:[、,，]\d{1,2})+\s+234星?[xX×*]\d+(?:\.\d+)?\s*", value))


def _is_confirmed_car_shorthand(value: str) -> bool:
    compact = value.replace(" ", "")
    operator = re.fullmatch(r"\d{1,2}(?P<op>-|[xX×*])(?P<unit>\d+(?:\.\d+)?)", compact)
    return bool(
        (operator and _is_confirmed_single_number_car_operator(operator.group("op"), operator.group("unit")))
        or re.fullmatch(r"\d{1,2}/\d+(?:\.\d+)?車嫌?", compact)
        or re.fullmatch(r"\d{1,2}半車坪?", compact)
        or re.fullmatch(r"\d{1,2}全車\d+(?:\.\d+)?", compact)
    )


def _is_confirmed_single_number_car_operator(op: str, unit_text: str) -> bool:
    unit = float(unit_text)
    if op == "-":
        return unit < 1
    return unit < 1 or unit == 5


def _is_confirmed_car_metadata_format(value: str) -> bool:
    compact = value.replace(" ", "")
    return bool(
        re.fullmatch(r"\d{1,2}半車坪?", compact)
        or re.fullmatch(r"\d{1,2}/\d+(?:\.\d+)?車嫌?", compact)
    )


def _expand_multi_car_fragment(value: str) -> list[str]:
    hyphen_car = HYPHEN_CAR_PATTERN.fullmatch(value.strip())
    if hyphen_car:
        return [f"{hyphen_car.group('number')}車{hyphen_car.group('amount')}支"]

    full_each = MULTI_FULL_CAR_EACH_PATTERN.fullmatch(value.strip())
    if full_each:
        amount = full_each.group("amount")
        numbers = [number for number in re.split(r"[.\s、,，]+", full_each.group("numbers").strip()) if number]
        return [f"{number}車{amount}支" for number in numbers]

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
