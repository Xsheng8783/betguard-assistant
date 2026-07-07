from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any


REPEATED_DOT_SPLIT_PATTERN = re.compile(r"\.{2,}")
# Tiantianle 住碰 header (天天[樂] + x-joined 3-4 digit groups) and its bare tail
# such as "2-3-100". A tail after such a header is a 住碰 continuation and must be
# merged into it (never emitted as an independent Valid bet).
TIANTIAN_ZHUPENG_HEADER_PATTERN = re.compile(r"天天(?:樂)?\s*\d{3,4}(?:\s*[xX×ｘ]\s*\d{3,4})+")
ZHUPENG_TAIL_PATTERN = re.compile(r"\d{1,2}(?:[-.]\d{1,2})*[-.]\d{2,4}")
# A single grouped normal bet written with ".." between number groups, ending in a
# star.amount tail (e.g. 06.01.32..08.05.02..234.100). The parser already handles
# the joined form, so it must NOT be split on ".." into separate fragments.
DOTDOT_GROUPED_BET_PATTERN = re.compile(
    r"\d{1,2}(?:\.\d{1,2})*(?:\.\.\d{1,2}(?:\.\d{1,2})*)*\.\.\d{2,4}\.\d{2,4}"
)
UNIT_WORD_SAFE = "\u652f"
STAR_AMOUNT_CONTINUATION_PATTERN = re.compile(
    r"^(?:"
    r"(?:[234](?:\.[234]){1,2}|[234]{2,3})(?:星)?|"
    r"(?:二|兩|三|四)(?:、(?:二|兩|三|四)){1,2}(?:星)?|"
    r"(?:二三|兩三|三四|二三四|兩三四)(?:星)?"
    r")\s*(?:[xX*]|\.[xX])\s*\d+(?:\.\d+)?(?:支|元|塊)?$"
)
MULTI_CAR_PATTERN = re.compile(
    r"^(?P<numbers>\d{1,2}(?:[\s、,，]+\d{1,2})+)\s*車\s*(?P<amount>\d{1,2}\.\d+|\d+)(?P<kind>支|元|塊)?$"
)
HYPHEN_CAR_PATTERN = re.compile(r"^(?P<number>\d{1,2})\s*-\s*(?P<amount>\d+(?:\.\d+)?)\s*車$")
CONFIRMED_SLASH_GAME_METADATA_PATTERN = re.compile(r"-?/\s*539\s*(?=[:=])")
MULTI_FULL_CAR_EACH_PATTERN = re.compile(
    r"^(?P<numbers>\d{1,2}(?:[.\s、,，]+\d{1,2})+)\s*全車各(?P<amount>\d+(?:\.\d+)?)車$"
)
MULTI_CAR_EACH_PATTERN = re.compile(
    r"^(?P<numbers>\d{1,2}(?:[.\s、,，]+\d{1,2})+)\s*各\s*(?P<amount>\d+(?:\.\d+)?)\s*車$"
)
TIME_ONLY_PATTERN = re.compile(r"^(?:上午|下午)?\s*\d{1,2}:\d{2}$")
LINE_PREFIX_PATTERN = re.compile(r"^(?P<time>(?:上午|下午)?\d{1,2}:\d{2})\s+(?P<sender>\S+)\s+(?P<body>.+)$")
DATE_ONLY_PATTERN = re.compile(r"^\d{4}[/-]\d{1,2}[/-]\d{1,2}$")
CHINESE_DATE_PATTERN = re.compile(r"^\d{1,2}月\d{1,2}日(?:\s+星期[一二三四五六日天])?$")
LINE_EXPORT_PATTERN = re.compile(r"^\d{4}[/-]\d{1,2}[/-]\d{1,2}\s+\d{1,2}:\d{2}\s+.+$")
STANDALONE_GAME_LABELS = {"天天樂", "天天", "539", "港", "hk", "HK", "大", "大樂"}
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
MULTIPLIER_TRANSLATION = str.maketrans({"＊": "*", "Ｘ": "X", "ｘ": "x", "乘": "x", "✖": "x", "️": None})
NUMERIC_STAR_WORDS = {"2": "二", "3": "三", "4": "四"}
GAME_LABEL_TOKENS = {"今彩", "六和", "六合", "539", "天天樂", "天天", "港", "hk", "HK", "大", "大樂"}


def _looks_like_tiantian_zhupeng_header(raw: str) -> bool:
    return bool(TIANTIAN_ZHUPENG_HEADER_PATTERN.search(raw))


def _looks_like_zhupeng_tail(cleaned: str) -> bool:
    return bool(ZHUPENG_TAIL_PATTERN.fullmatch(cleaned.replace(" ", "")))


def _is_dotdot_grouped_number_bet(raw: str) -> bool:
    return bool(DOTDOT_GROUPED_BET_PATTERN.fullmatch(raw.strip()))


def _join_dotdot_grouped_bet(raw: str) -> str:
    parts = REPEATED_DOT_SPLIT_PATTERN.split(raw.strip())
    return f"{'.'.join(parts[:-1])} {parts[-1]}".strip()


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

    hk_context = False
    for line_no, raw_line in enumerate(original_text.splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        if is_metadata_line(line):
            if _mentions_539_game_label(line):
                hk_context = False
            ignored_metadata_lines.append({"line_no": line_no, "raw": line})
            continue

        cleaned, notes = _clean_line_content(line)
        if not cleaned:
            ignored_metadata_lines.append({"line_no": line_no, "raw": line, "reason": "empty after cleanup"})
            continue
        if is_metadata_line(cleaned):
            if _mentions_539_game_label(cleaned):
                hk_context = False
            ignored_metadata_lines.append({"line_no": line_no, "raw": line, "cleaned": cleaned})
            continue

        # --- 臂 long-string protection ---
        # When a line contains "臂" AND has 12+ digits (suggesting multiple
        # bets stuck together), keep it as ONE Needs Review item instead of
        # splitting into fragments.  The parser may extract parts of it
        # successfully, but we don't want those partials going to fill flow.
        if "臂" in cleaned and len(re.findall(r"\d", cleaned)) >= 30:
            logical_lines.append(
                {
                    "line_no": line_no,
                    "raw": cleaned,
                    "original_lines": [line],
                    "preprocessing_notes": notes
                    + ["review_label:customer_bi_raw_line",
                       "arm long string preserved as single review item"],
                }
            )
            if pending is not None:
                logical_lines.append(pending)
                pending = None
            continue
        # --- end arm protection ---

        if "removed LINE time/sender prefix" in notes:
            hk_context = False
        if cleaned.startswith("港") or cleaned.lower().startswith("hk"):
            hk_context = True
        elif hk_context and re.match(r"\d{1,2}[.\-/\s]", cleaned):
            notes.append("game prefix requires manual review")

        entry = {
            "line_no": line_no,
            "raw": cleaned,
            "original_lines": [line],
            "preprocessing_notes": notes,
        }
        if pending is not None:
            car_merge = _merge_car_number_lines(pending["raw"], cleaned)
            if car_merge is not None:
                pending["raw"] = car_merge
                pending["original_lines"].append(line)
                pending["preprocessing_notes"].extend(notes)
                pending["preprocessing_notes"].append("merged car number line")
                continue
        if (
            pending is not None
            and _looks_like_star_amount_continuation(cleaned.replace(" ", ""))
            and pending["raw"].replace(" ", "").endswith(cleaned.replace(" ", ""))
        ):
            pending["original_lines"].append(line)
            pending["preprocessing_notes"].extend(notes)
            pending["preprocessing_notes"].append("duplicate star amount line ignored")
            continue

        if pending is not None and (
            (_looks_like_continuation(cleaned) and not _pending_has_confirmed_amount(pending["raw"]))
            or _looks_like_amount_continuation_for_pending(pending["raw"], cleaned)
            or _looks_like_per_star_continuation_for_numbers(pending["raw"], cleaned)
        ):
            pending["raw"] = _normalize_dotted_star_before_amount(f"{pending['raw'].rstrip(' .')} {cleaned}")
            pending["original_lines"].append(line)
            pending["preprocessing_notes"].extend(notes)
            pending["preprocessing_notes"].append("merged continuation line")
            continue

        if pending is not None and _looks_like_decimal_amount_continuation(pending["raw"], cleaned):
            pending["raw"] = f"{pending['raw'].rstrip(' .')} x{cleaned}"
            pending["original_lines"].append(line)
            pending["preprocessing_notes"].extend(notes)
            pending["preprocessing_notes"].append("merged decimal amount line")
            continue

        if (
            pending is not None
            and _looks_like_tiantian_zhupeng_header(pending["raw"])
            and _looks_like_zhupeng_tail(cleaned)
        ):
            pending["raw"] = f"{pending['raw'].rstrip(' .')} {cleaned}"
            pending["original_lines"].append(line)
            pending["preprocessing_notes"].extend(notes)
            pending["preprocessing_notes"].append("merged 住碰 continuation")
            continue

        if pending is not None:
            logical_lines.append(pending)
        pending = entry

    if pending is not None:
        logical_lines.append(pending)

    candidate_bet_lines: list[dict[str, Any]] = []
    for logical_index, logical in enumerate(logical_lines, start=1):
        # Arm long-string protection: keep the whole line as one fragment
        # so the parser/validator can report it as a single Needs Review item.
        notes = logical.get("preprocessing_notes", [])
        if "review_label:customer_bi_raw_line" in notes:
            candidate_bet_lines.append(
                {
                    "line_no": logical.get("line_no"),
                    "logical_index": logical_index,
                    "fragment_index": 1,
                    "raw": logical["raw"],
                    "original_lines": logical.get("original_lines"),
                    "preprocessing_notes": notes,
                }
            )
            continue
        if _is_dotdot_grouped_number_bet(logical["raw"]):
            # One grouped normal bet written with ".." between number groups. Join
            # the number groups and keep the trailing star.amount so the parser
            # reads a single bet (e.g. 06.01.32..08.05.02..234.100 ->
            # "06.01.32.08.05.02 234.100") instead of broken fragments.
            raw_fragments = [_join_dotdot_grouped_bet(logical["raw"])]
        else:
            raw_fragments = [
                final_fragment.strip()
                for fragment in REPEATED_DOT_SPLIT_PATTERN.split(logical["raw"])
                for split_fragment in _split_after_embedded_star_amount(fragment)
                for broken_split in _split_broken_prefix_star_fragment(split_fragment)
                for final_fragment in _split_normal_and_car_fragment(broken_split)
            ]
        fragments, inline_notes = _merge_inline_star_amount_fragments(raw_fragments)
        for fragment_index, fragment in enumerate(fragments, start=1):
            fragment = _trim_fragment(fragment)
            if not fragment:
                continue
            fragment, name_marker_notes = _strip_trailing_name_marker(fragment)
            fragment_notes = _dedupe(
                list(logical.get("preprocessing_notes", []))
                + inline_notes
                + name_marker_notes
                + _suspicious_paste_notes(fragment)
                + _review_classification_labels(fragment)
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
    if re.fullmatch(r"539\s*[-－]\s*[一二三四五六日]", value):
        return True
    if re.fullmatch(r"(?:320|440|640|880)[二兩三四]{2,3}星\d+(?:\.\d+)?(?:支|元|塊)?", value):
        return True
    label_tokens = [token for token in re.split(r"[，,、\s]+", value) if token]
    if label_tokens and all(token in GAME_LABEL_TOKENS for token in label_tokens):
        return True
    return _looks_like_speaker_name(value)


def _mentions_539_game_label(value: str) -> bool:
    return bool(re.search(r"539|天天|今彩", value))


def _looks_like_speaker_name(value: str) -> bool:
    value = re.sub(
        r"[←-⇿☀-➿⬀-⯿️\U0001F000-\U0001FAFF]+$", "", value
    ).strip()
    if not value:
        return False
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


def _split_broken_prefix_star_fragment(fragment: str) -> list[str]:
    match = re.fullmatch(
        r"(?P<broken>.*?\d{3,}\.\d{2,4})\.(?P<bet>\d{1,2}(?:[.,，、]\d{1,2})+\.?[兩二三四]{1,3}\d+(?:\.\d+)?(?:支|元|塊)?)",
        fragment.strip(),
    )
    if not match:
        return [fragment]
    return [match.group("broken"), match.group("bet")]


def _split_normal_and_car_fragment(fragment: str) -> list[str]:
    match = re.fullmatch(
        r"(?P<bet>\d{1,2}(?:[.\-]\d{1,2})+\s*-\s*(?:50|100|200|500|1000|1500))"
        r"\s+(?P<car>\d{1,2}-0?\.\d+)",
        fragment.strip(),
    )
    if not match:
        return [fragment]
    return [match.group("bet"), match.group("car")]


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
    pending_car_marker = False
    for fragment in fragments:
        value = fragment.strip()
        if pending_car_marker:
            pending_car_marker = False
            car_next = re.fullmatch(r"(?P<car>\d{1,2}\.0\.\d+)臂?", value)
            if car_next:
                merged.append(f"{car_next.group('car')}車")
                notes.append("attached standalone 車 to next car shorthand")
                continue
            merged.append("車")
        if value == "車":
            pending_car_marker = True
            continue
        if (
            merged
            and _looks_like_number_fragment_without_amount(merged[-1])
            and _looks_like_inline_star_amount_fragment(value)
        ):
            merged[-1] = _normalize_dotted_star_before_amount(
                f"{merged[-1].rstrip()} {_normalize_comma_star_amount_fragment(value)}"
            )
            notes.append("merged continuation star amount")
            continue
        if (
            merged
            and _looks_like_number_fragment_without_amount(merged[-1])
            and value == "234"
        ):
            merged[-1] = f"{merged[-1].rstrip()} 234"
            notes.append("merged continuation star amount")
            continue
        if merged and _looks_like_arm_amount_continuation(merged[-1], value):
            merged[-1] = f"{merged[-1].rstrip()} {value}"
            notes.append("merged continuation star amount")
            continue
        merged.append(value)
    if pending_car_marker:
        merged.append("車")
    return merged, _dedupe(notes)


def _normalize_comma_star_amount_fragment(value: str) -> str:
    compact = value.replace(" ", "")
    match = re.fullmatch(
        r"(?P<stars>[234](?:[,，、][234]){1,2})\.(?P<amount>\d+(?:\.\d+)?(?:支|元|塊)?)",
        compact,
    )
    if match:
        stars = re.sub(r"[,，、]", ".", match.group("stars"))
        return f"{stars} {match.group('amount')}"

    dotted_x = re.fullmatch(
        r"(?P<stars>[234](?:\.[234]){1,2})[xX×*](?P<amount>\d+(?:\.\d+)?)(?P<kind>支|元|塊)?",
        compact,
    )
    if dotted_x:
        stars = dotted_x.group("stars").replace(".", "")
        return f"{stars}星X{dotted_x.group('amount')}{dotted_x.group('kind') or ''}"
    return value


def _looks_like_arm_amount_continuation(pending_raw: str, value: str) -> bool:
    if not re.fullmatch(r"\d+(?:\.\d+)?(?:支|元|塊)?臂?", value.strip()):
        return False
    return bool(re.search(r"(?:^|\s)234$", pending_raw.strip()))


def _strip_decorative_star_parens(value: str, notes: list[str]) -> str:
    pattern = re.compile(
        r"[（(]\s*((?:[234]星[xX×*]?\d+(?:\.\d+)?(?:支|元|塊)?\s*)+)[）)]?\s*$"
    )
    match = pattern.search(value)
    if not match:
        return value
    notes.append("removed decorative parentheses")
    return (value[: match.start()].strip() + " " + match.group(1).strip()).strip()


def _normalize_numeric_per_star_groups(value: str, notes: list[str]) -> str:
    pattern = re.compile(
        r"(?:(?<=\s)|^)([234](?:\.[234]){0,2})星([xX×*])?(\d+(?:\.\d+)?)(支|元|塊)?(?=\s|$)"
    )
    matches = pattern.findall(value)
    whole_line_is_groups = bool(
        re.fullmatch(
            r"(?:[234](?:\.[234]){0,2}星[xX×*]?\d+(?:\.\d+)?(?:支|元|塊)?\s*)+",
            value.strip(),
        )
    )
    if len(matches) < 2 and not whole_line_is_groups:
        return value

    def _replace(match: re.Match[str]) -> str:
        star = "".join(NUMERIC_STAR_WORDS[char] for char in match.group(1) if char.isdigit())
        amount = match.group(3)
        kind = match.group(4)
        if not kind:
            kind = "支" if (match.group(2) or "." in amount) else "元"
        return f"{star}星{amount}{kind}"

    updated = pattern.sub(_replace, value)
    if updated != value:
        notes.append("normalized numeric per-star amounts")
    return updated


def _normalize_hyphen_star_amount(value: str, notes: list[str]) -> str:
    match = re.fullmatch(
        r"(?P<numbers>\d{1,2}(?:-\d{1,2})+)--(?P<stars>[234](?:-[234]){1,2})[xX](?P<amount>\d+(?:\.\d+)?)",
        value.strip(),
    )
    if not match:
        return value
    stars = match.group("stars").replace("-", "")
    notes.append("normalized double-hyphen star amount")
    return f"{match.group('numbers')} {stars}星X{match.group('amount')}"


def _normalize_x_decimal_amount(value: str, notes: list[str]) -> str:
    updated = re.sub(r"(?<=[xX×*])\.(?=\d)", "0.", value)
    if updated != value:
        notes.append("normalized decimal amount after multiplier")
    return updated


def _normalize_comma_decimal_after_star(value: str, notes: list[str]) -> str:
    updated = re.sub(r"(?<=星)(\d+),(\d+)\s*$", r"\1.\2", value)
    if updated != value:
        notes.append("normalized comma decimal amount")
    return updated


def _normalize_one_unit_word(value: str, notes: list[str]) -> str:
    updated = re.sub(r"(?<=星)一支\s*$", "1支", value)
    if updated != value:
        notes.append("normalized 一支 unit")
    return updated


def _remove_trailing_unit_game_metadata(value: str, notes: list[str]) -> str:
    updated = re.sub(r"(?<=[支元塊])\s*539\s*$", "", value)
    if updated != value:
        notes.append("removed game metadata 539")
    return updated


def _remove_trailing_gai_after_confirmed_amount(value: str, notes: list[str]) -> str:
    if not value.endswith("改"):
        return value
    rest = value[:-1].strip()
    if _is_confirmed_hyphen_amount(rest):
        notes.append("ignored trailing 改 after confirmed amount")
        return rest
    return value


def _normalize_write_word_multiplier(value: str, notes: list[str]) -> str:
    updated = re.sub(r"(?<=星)寫(?=\d)", "X", value)
    if updated != value:
        notes.append("normalized 寫 multiplier")
    return updated


def _normalize_per_star_each(value: str, notes: list[str]) -> str:
    """Expand per-star '各' (each) patterns to individual star-amount pairs.

    Converts "23星各2" → "二星2 三星2", "234星各5" → "二星5 三星5 四星5".
    Also normalizes leftover single numeric stars in the same fragment.
    """
    updated = value
    match = re.search(r"(?<!\d)([234]{2,3})星各(\d+(?:\.\d+)?)(?!\d)", updated)
    if match:
        star_str = match.group(1)
        each = match.group(2)
        parts = [f"{NUMERIC_STAR_WORDS[ch]}星{each}" for ch in star_str]
        replacement = " ".join(parts)
        updated = updated[:match.start()] + replacement + updated[match.end():]
        notes.append("normalized per-star each")

        # After 各 expansion, also normalize remaining single numeric stars
        # in the same fragment (e.g. "4星X1" → "四星1")
        updated2 = re.sub(
            r"(?:(?<=\s)|^)([234])星[xX×*](\d+(?:\.\d+)?)",
            lambda m: f"{NUMERIC_STAR_WORDS[m.group(1)]}星{m.group(2)}",
            updated,
        )
        updated2 = re.sub(
            r"(?:(?<=\s)|^)([234])星(?!各)",
            lambda m: f"{NUMERIC_STAR_WORDS[m.group(1)]}星",
            updated2,
        )
        if updated2 != updated:
            notes.append("normalized remaining numeric stars")
        return updated2

    return updated


def _normalize_235_star_typo(value: str, notes: list[str]) -> str:
    match = re.fullmatch(r"235\s*-\s*(\d+(?:\.\d+)?)", value.strip())
    if not match:
        return value
    notes.append("normalized 235 star typo to 234")
    return f"234.{match.group(1)}"


def _remove_trailing_comma_game_metadata(value: str, notes: list[str]) -> str:
    updated = re.sub(r"\s*[，,]\s*539\s*坪?\s*$", "", value).strip()
    if updated != value:
        notes.append("removed game metadata 539")
    return updated


def _remove_star_typo_five(value: str, notes: list[str]) -> str:
    updated = re.sub(
        r"(?P<stars>二三四|234)五(?=\s*[xX×*]?\d+(?:\.\d+)?(?:支|元|塊)?\s*$)",
        lambda match: match.group("stars"),
        value,
    )
    if updated != value:
        notes.append("ignored typo 五 after 二三四")
    return updated


def _strip_trailing_name_marker(fragment: str) -> tuple[str, list[str]]:
    stripped = fragment.strip()
    marker = stripped[-1:] if stripped[-1:] in {"臂", "嫌"} else None
    if marker is None:
        return fragment, []
    rest = stripped[:-1].strip()
    numbers = r"\d{1,2}(?:[.\s、,，-]+\d{1,2})+"
    complete_bet_patterns = [
        rf"{numbers}\s+234\s+\d+(?:\.\d+)?(?:支|元|塊)?",
        rf"{numbers}[.\s](?:234|23|34)[.xX×*]\d+(?:\.\d+)?(?:支|元|塊)?",
        rf"{numbers}/234/\d+(?:\.\d+)?(?:支|元|塊)?",
        rf"{numbers}\.?(?:[兩二三四]{{1,3}}星?[xX×*]?\d+(?:\.\d+)?(?:支|元|塊)?){{1,3}}",
    ]
    for pattern in complete_bet_patterns:
        if re.fullmatch(pattern, rest):
            return rest, [f"ignored trailing name marker {marker}"]
    return fragment, []


def _looks_like_number_fragment_without_amount(value: str) -> bool:
    stripped = value.strip()
    if _pending_has_confirmed_amount(stripped):
        return False
    return bool(re.fullmatch(r"\d{1,2}(?:[.\s、,，-]+\d{1,2}){2,}", stripped))


def _looks_like_inline_star_amount_fragment(value: str) -> bool:
    compact = value.replace(" ", "")
    return bool(
        re.fullmatch(
            r"(?:234|[234](?:\.[234]){1,2}|[234](?:[,，、][234]){1,2})(?:\.|[xX*×])\d+(?:\.\d+)?(?:支|元|塊)?(?:\u81c2)?",
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
        notes.append("normalized multiplier symbol")
    value = updated

    updated = _normalize_confirmed_star_text(value, notes)
    value = updated

    updated = _remove_star_typo_five(value, notes)
    value = updated

    value = _normalize_write_word_multiplier(value, notes)
    value = _normalize_per_star_each(value, notes)
    value = _normalize_235_star_typo(value, notes)
    value = _strip_decorative_star_parens(value, notes)
    value = _normalize_numeric_per_star_groups(value, notes)
    value = _normalize_hyphen_star_amount(value, notes)
    value = _normalize_x_decimal_amount(value, notes)
    value = _normalize_comma_decimal_after_star(value, notes)
    value = _normalize_one_unit_word(value, notes)
    value = _remove_trailing_unit_game_metadata(value, notes)
    value = _remove_trailing_gai_after_confirmed_amount(value, notes)

    normalized_ellipsis = _normalize_ellipsis_separator(value)
    if normalized_ellipsis != value:
        notes.append("normalized ellipsis separator")
        value = normalized_ellipsis

    value = _remove_leading_game_label(value, notes)
    value = _remove_slash_game_metadata(value, notes)

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
    value = _remove_trailing_comma_game_metadata(value, notes)
    return re.sub(r"[ \t]+", " ", value).strip(), _dedupe(notes)


def _remove_trailing_game_label(value: str, notes: list[str]) -> str:
    updated = re.sub(r"\s*[（(]\s*(?:天天樂|天天|539|hk|HK|港)\s*[）)]?\s*$", "", value).strip()
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


def _remove_slash_game_metadata(value: str, notes: list[str]) -> str:
    updated = CONFIRMED_SLASH_GAME_METADATA_PATTERN.sub(" ", value)
    if updated != value:
        notes.append("removed game metadata 539")
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
    updated = re.sub(
        r"\s*[、,，]?\s*(?:天天樂|天天|539|hk|HK)?\s*坪\s*$", "", value
    ).strip()
    if updated != value:
        notes.append("removed trailing equals metadata")
        value = updated
    updated = re.sub(r"\s*[、,，]\s*$", "", value).strip()
    if updated != value:
        notes.append("removed trailing equals metadata")
    return updated


def _looks_like_per_star_continuation_for_numbers(pending_raw: str, value: str) -> bool:
    compact = value.replace(" ", "")
    if not re.fullmatch(r"(?:[二兩三四]{1,3}星[xX×*]?\d+(?:\.\d+)?(?:支|元|塊)?){1,3}", compact):
        return False
    return bool(
        re.fullmatch(
            r"\d{1,2}(?:[\s.、,，-]+\d{1,2})+"
            r"(?:\s+[二兩三四]{1,3}星[xX×*]?\d+(?:\.\d+)?(?:支|元|塊)?)*",
            pending_raw.strip(),
        )
    )


def _looks_like_decimal_amount_continuation(pending_raw: str, value: str) -> bool:
    if not re.fullmatch(r"0\.\d+", value.strip()):
        return False
    return _looks_like_number_fragment_without_amount(pending_raw)


def _merge_car_number_lines(pending_raw: str, value: str) -> str | None:
    number = re.fullmatch(r"(\d{1,2})號", pending_raw.strip())
    units = re.fullmatch(r"專車(\d+(?:\.\d+)?)", value.strip())
    if number and units:
        return f"{number.group(1)}車{units.group(1)}支"
    return None


def _looks_like_continuation(value: str) -> bool:
    compact = value.replace(" ", "")
    if re.fullmatch(r"(?:[二兩三四]{1,3}星[xX×*]?\d+(?:\.\d+)?(?:支|元|塊)?){2,}", compact):
        return True
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
    # Check 1: numeric star with EXPLICIT multiplier (e.g. " 234x100", " 23X0.5")
    # Requiring the multiplier avoids false-positives like "32" being read
    # as star=3 amount=2 when it is actually part of a multi-digit number.
    if re.search(
        r"\s(?:[234](?:[.,、，]?[234]){0,2}|[234]星)[xX×*]\d+(?:\.\d+)?(?:支|元|塊)?$",
        value,
    ):
        return True
    # Check 2: explicit star group followed by amount (e.g. " 234 100", " 234.100")
    if re.search(r"\s(?:234|23|34)[.\s]+\d+(?:\.\d+)?(?:支|元|塊)?$", value):
        return True
    # Check 3: Chinese star suffix (e.g. "二三100")
    if re.search(
        r"(?:二三四|兩三四|二三|兩三|三四|二星|兩星|三星|四星)\d+(?:\.\d+)?(?:支|元|塊)?$",
        value.replace(" ", ""),
    ):
        return True
    return False


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

def _review_classification_labels(value: str) -> list[str]:
    """Return review-only classification labels for a fragment.

    These are metadata labels that help reviewers understand WHY a fragment
    needs review. They do NOT change parser validity, validator rules, or
    queue status.  All labels are prefixed with 'review_label:' for easy
    identification in review.html.
    """
    labels: list[str] = []
    compact = value.replace(" ", "")
    lower = value.lower()

    # 1) non_539_candidate: HK/港/六合 markers
    _non539_tokens = ["港", "六合", "六和", "六", "hk"]
    if any(compact.startswith(t) for t in _non539_tokens) or any(
        t in lower for t in ["hk", "六合", "六和"]
    ):
        labels.append("review_label:non_539_candidate")

    # 2) suspected_non_539_due_to_range: numbers 40-49
    nums = re.findall(r"\b(4[0-9])\b", value)
    if nums:
        labels.append("review_label:suspected_non_539_due_to_range")

    # 3) suspected_tiantianle: 天/天天/天天樂 suffix
    if re.search(r"(?:天天樂|天天|天)\s*$", value):
        labels.append("review_label:suspected_tiantianle")

    # 4) person_name_suffix: 臂/改/嫌 at end
    if re.search(r"[臂改嫌]\s*$", value):
        labels.append("review_label:person_name_suffix")

    # 5) ambiguous_long_token: long digit sequences (5+) that look like
    #    glued numbers (e.g. 35234, 1500)
    long_tokens = re.findall(r"\b\d{4,}\b", value)
    if long_tokens:
        labels.append("review_label:ambiguous_long_token")

    # 6) per_star_amount_split: per-star amount patterns
    #    Matches both raw form ("23星各2 4星X1") and normalized form ("二星2 三星2 四星1").
    if re.search(
        r"(?:[234二三四兩]{2,3}|二三四|兩三四)[星]?\s*各\s*\d+",
        value,
    ) or re.search(
        r"(?:[二三四兩]星\d+(?:\.\d+)?\s*){2,}",
        value,
    ):
        labels.append("review_label:per_star_amount_split")

    # 7) write_shorthand: 寫 multiplier patterns like 28寫10
    if re.search(r"\d{2}\s*寫\s*\d+", value):
        labels.append("review_label:write_shorthand")

    # 8) tail_write_shorthand: tail + per-column write patterns
    #    e.g. "其它8尾各寫2" or "尾X寫N"
    if re.search(r"尾\s*各?\s*寫\s*\d+", value):
        labels.append("review_label:tail_write_shorthand")

    return labels



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
        and not _is_confirmed_spaced_decimal_hyphen(value)
        and not _is_confirmed_inline_star_amount_tail(value)
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
            r"\s*\d{1,2}(?:[.\-\s、,，]+\d{1,2})+\s*-\s*(?:50|100|200|500|1000|1500)\s*",
            value,
        )
    )


def _is_confirmed_inline_star_amount_tail(value: str) -> bool:
    return bool(
        re.fullmatch(
            r"\s*\d{1,2}(?:[.\-\s、,，]+\d{1,2})+\s+(?:234|23|34)[.\s]\d+(?:\.\d+)?(?:支|元|塊)?\s*",
            value,
        )
    )


def _is_confirmed_spaced_decimal_hyphen(value: str) -> bool:
    return bool(
        re.fullmatch(
            r"\s*\d{1,2}(?:[.\-\s、,，]+\d{1,2})+\s+-\s*0?\.\d+\s*",
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
    return unit > 0


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

    star_each_car = re.fullmatch(
        r"(?P<numbers>\d{1,2}(?:[\s.、,，]+\d{1,2})+)\s*"
        r"(?P<stars>二三四|兩三四|二三|兩三|三四)(?P<amount>\d+(?:\.\d+)?)"
        r"各(?P<each>\d+(?:\.\d+)?)元?",
        value.strip(),
    )
    if star_each_car:
        numbers = [
            number
            for number in re.split(r"[\s.、,，]+", star_each_car.group("numbers").strip())
            if number
        ]
        normal_bet = f"{star_each_car.group('numbers')}{star_each_car.group('stars')}{star_each_car.group('amount')}"
        return [normal_bet] + [f"{number}車{star_each_car.group('each')}元" for number in numbers]

    full_each = MULTI_FULL_CAR_EACH_PATTERN.fullmatch(value.strip())
    if full_each:
        amount = full_each.group("amount")
        numbers = [number for number in re.split(r"[.\s、,，]+", full_each.group("numbers").strip()) if number]
        return [f"{number}車{amount}支" for number in numbers]

    each = MULTI_CAR_EACH_PATTERN.fullmatch(value.strip())
    if each:
        amount = each.group("amount")
        numbers = [number for number in re.split(r"[.\s、,，]+", each.group("numbers").strip()) if number]
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
