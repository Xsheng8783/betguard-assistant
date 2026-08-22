"""Backfill parser-exact image text from existing human-confirmed truth.

The migration is intentionally deterministic.  It discovers only sources with
an auditable human-confirmation signal, renders structured truth without a
model, and saves a sample only after the existing production parser reproduces
the represented betting semantics exactly.
"""

from __future__ import annotations

import hashlib
import json
import re
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from betguard.expander import expand_tail
from betguard.vision.image_text_acceptance import (
    get_dataset_status,
    save_migrated_human_verified_sample,
    validate_with_existing_parser,
)


_SAMPLE_ID_RE = re.compile(r"^sample-\d{3}$")
_RULE_RE = re.compile(
    r"(?P<categories>[二三四234][二三四234/、,\.\s-]*)[xX×]\s*"
    r"(?P<value>\d+(?:\.\d+)?)"
)
_TAIL_RE = re.compile(r"^(?P<digit>\d)尾$")
_CATEGORY_DIGITS = {"二": "2", "三": "3", "四": "4"}
_HUMAN_LINE_ACTIONS = {"confirmed", "corrected", "added"}


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON_OBJECT_REQUIRED:{path}")
    return value


def _sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _json_sha256(value: Any) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _manifest(dataset_source_root: Path) -> dict[str, dict[str, Any]]:
    records: dict[str, dict[str, Any]] = {}
    for raw in (dataset_source_root / "manifest.jsonl").read_text(encoding="utf-8").splitlines():
        if not raw.strip():
            continue
        record = json.loads(raw)
        sample_id = str(record.get("sample_id") or "")
        if _SAMPLE_ID_RE.fullmatch(sample_id):
            records[sample_id] = record
    return records


def _all_lines_human_confirmed(truth: dict[str, Any]) -> bool:
    lines = [line for line in truth.get("lines", []) if isinstance(line, dict)]
    return bool(lines) and all(
        str(line.get("review_action") or "") in _HUMAN_LINE_ACTIONS for line in lines
    )


def _has_human_final_confirmation(truth: dict[str, Any]) -> bool:
    return any(
        isinstance(edit, dict) and edit.get("source") == "human_final_confirmation"
        for edit in truth.get("human_edits", [])
    )


def _has_recorded_user_review(truth: dict[str, Any]) -> bool:
    if truth.get("human_edits"):
        return True
    for line in truth.get("lines", []):
        fallback = line.get("fallback_candidate") if isinstance(line, dict) else None
        for evidence in (fallback or {}).get("evidence", []) if isinstance(fallback, dict) else []:
            if isinstance(evidence, dict) and evidence.get("source") in {
                "user_confirmation_restore",
                "human_verified_image_ground_truth",
            }:
                return True
    return False


def discover_human_confirmed_sources(dataset_source_root: str | Path) -> dict[str, Any]:
    """Find current, auditable human truth sources; never promote a prelabel."""
    root = Path(dataset_source_root)
    manifest = _manifest(root)
    freeze_path = root / "audit" / "gt-v1-freeze.json"
    freeze = _read_json(freeze_path)
    frozen = {
        str(item.get("sample_id")): item
        for item in freeze.get("samples", [])
        if isinstance(item, dict)
    }
    promotion_path = root / "audit" / "sample-007-human-truth-promotion-r1.json"
    promotion = _read_json(promotion_path)

    eligible: list[dict[str, Any]] = []
    rejected_unverified: list[dict[str, str]] = []

    # A reviewed-existing plain-text fixture is human truth, but still must pass
    # the same complete-parser gate before migration.
    for sample_id, entry in sorted(manifest.items()):
        if entry.get("gt_status") != "reviewed_existing":
            continue
        truth_path = root / str(entry.get("ground_truth_path") or "")
        if not truth_path.is_file():
            rejected_unverified.append(
                {"sample_id": sample_id, "reason": "reviewed truth text is missing"}
            )
            continue
        eligible.append(
            {
                "sample_id": sample_id,
                "kind": "reviewed_existing_text",
                "truth_path": truth_path,
                "image_path": root / str(entry["image_path"]),
                "image_sha256": str(entry["sha256"]).lower(),
                "provenance": {
                    "authority_kind": "manifest_reviewed_existing",
                    "manifest_reference": "manifest.jsonl",
                    "manifest_gt_status": "reviewed_existing",
                    "source_truth_reference": str(entry["ground_truth_path"]),
                    "source_truth_sha256": _sha256_path(truth_path),
                },
            }
        )

    truth_root = root / "ground-truth-draft"
    for truth_path in sorted(truth_root.glob("sample-???.json")):
        truth = _read_json(truth_path)
        sample_id = str(truth.get("sample_id") or truth_path.stem)
        entry = manifest.get(sample_id)
        if entry is None:
            rejected_unverified.append(
                {"sample_id": sample_id, "reason": "source image manifest entry is missing"}
            )
            continue

        authority_kind = ""
        authority: dict[str, Any] = {}
        frozen_record = frozen.get(sample_id)
        if frozen_record is not None:
            reviewer_matches = str(truth.get("reviewed_by") or "") == str(
                frozen_record.get("reviewed_by") or ""
            )
            reviewed_at_matches = str(truth.get("reviewed_at") or "") == str(
                frozen_record.get("reviewed_at") or ""
            )
            if (
                truth.get("review_status") == "reviewed"
                and reviewer_matches
                and reviewed_at_matches
                and _all_lines_human_confirmed(truth)
                and entry.get("gt_status") == "frozen_v1"
            ):
                authority_kind = "frozen_human_ground_truth_v1"
                authority = {
                    "freeze_reference": "audit/gt-v1-freeze.json",
                    "freeze_sha256": _sha256_path(freeze_path),
                    "freeze_semantic_sha256": frozen_record.get("sha256"),
                    "reviewed_by": truth.get("reviewed_by"),
                    "reviewed_at": truth.get("reviewed_at"),
                }
        elif sample_id == str(promotion.get("sample_id") or ""):
            truth_sha256 = _sha256_path(truth_path)
            if (
                truth_sha256 == str(promotion.get("canonical_truth_sha256") or "")
                and truth.get("review_status") == "reviewed"
                and truth.get("annotated_by_human") is True
                and truth.get("annotation_status") == "confirmed"
                and _all_lines_human_confirmed(truth)
            ):
                authority_kind = "canonical_human_truth_promotion"
                authority = {
                    "promotion_reference": "audit/sample-007-human-truth-promotion-r1.json",
                    "promotion_sha256": _sha256_path(promotion_path),
                    "authority_source_sha256": promotion.get("authority_source_sha256"),
                    "canonical_truth_sha256": truth_sha256,
                    "reviewed_by": truth.get("reviewed_by"),
                    "reviewed_at": truth.get("reviewed_at"),
                }
        elif (
            truth.get("review_status") == "reviewed"
            and str(truth.get("reviewed_by") or "").strip()
            and _all_lines_human_confirmed(truth)
            and _has_human_final_confirmation(truth)
            and truth.get("annotated_by_human") is True
            and truth.get("annotation_status") == "confirmed"
        ):
            authority_kind = "explicit_human_final_confirmation"
            authority = {
                "reviewed_by": truth.get("reviewed_by"),
                "reviewed_at": truth.get("reviewed_at"),
                "human_final_confirmation": True,
            }
        elif (
            truth.get("review_status") == "reviewed"
            and str(truth.get("reviewed_by") or "").strip() not in {"", "?"}
            and _all_lines_human_confirmed(truth)
            and _has_recorded_user_review(truth)
        ):
            authority_kind = "persisted_complete_human_review"
            authority = {
                "reviewed_by": truth.get("reviewed_by"),
                "reviewed_at": truth.get("reviewed_at"),
                "all_line_actions_human_confirmed": True,
            }

        if not authority_kind:
            if truth.get("review_status") != "reviewed":
                reason = "pending human review"
            elif not _all_lines_human_confirmed(truth):
                reason = "reviewed document still contains unconfirmed lines"
            else:
                reason = "no auditable human-confirmation provenance"
            rejected_unverified.append({"sample_id": sample_id, "reason": reason})
            continue

        eligible.append(
            {
                "sample_id": sample_id,
                "kind": "structured_truth",
                "truth": truth,
                "truth_path": truth_path,
                "image_path": root / str(entry["image_path"]),
                "image_sha256": str(entry["sha256"]).lower(),
                "provenance": {
                    "authority_kind": authority_kind,
                    "source_truth_reference": truth_path.relative_to(root).as_posix(),
                    "source_truth_sha256": _sha256_path(truth_path),
                    **authority,
                },
            }
        )

    eligible.sort(key=lambda item: (item["sample_id"] != "sample-007", item["sample_id"]))
    return {"eligible": eligible, "rejected_unverified": rejected_unverified}


def _number(value: Any) -> str:
    raw = str(value or "").strip()
    if not re.fullmatch(r"\d{1,2}", raw):
        raise ValueError(f"INVALID_NUMBER:{raw}")
    number = int(raw)
    if number < 1 or number > 49:
        raise ValueError(f"NUMBER_OUT_OF_RANGE:{raw}")
    return f"{number:02d}"


def _decimal_text(value: Any) -> str:
    try:
        decimal_value = Decimal(str(value).strip())
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"INVALID_MULTIPLIER_VALUE:{value}") from exc
    if not decimal_value.is_finite() or decimal_value < 0:
        raise ValueError(f"INVALID_MULTIPLIER_VALUE:{value}")
    text = format(decimal_value, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def _categories(value: Any) -> list[int]:
    text = "".join(_CATEGORY_DIGITS.get(char, char) for char in str(value or ""))
    result = [int(char) for char in text if char in "234"]
    if not result or len(result) != len(set(result)):
        raise ValueError(f"INVALID_MULTIPLIER_CATEGORIES:{value}")
    return result


def _rules(line: dict[str, Any]) -> list[dict[str, Any]]:
    structured = [rule for rule in line.get("multiplier_rules", []) if isinstance(rule, dict)]
    if structured:
        normalized: list[dict[str, Any]] = []
        for rule in structured:
            if rule.get("categories") is not None and rule.get("value") is not None:
                normalized.append(
                    {
                        "categories": _categories(rule.get("categories")),
                        "value": _decimal_text(rule.get("value")),
                    }
                )
                continue
            # Some promoted human literals intentionally retained an
            # ``unresolved_human_literal`` marker rather than machine-derived
            # fields.  Parse the human rule text deterministically here.
            normalized.extend(_rules_from_text(str(rule.get("rule_text") or "")))
        return normalized

    raw = str(line.get("multiplier_text") or "").strip()
    if not raw:
        return []
    return _rules_from_text(raw)


def _rules_from_text(raw: str) -> list[dict[str, Any]]:
    matches = list(_RULE_RE.finditer(raw))
    if not matches:
        raise ValueError(f"UNPARSEABLE_MULTIPLIER:{raw}")
    leftover = _RULE_RE.sub("", raw).strip(" /、,.-")
    if leftover:
        raise ValueError(f"UNPARSEABLE_MULTIPLIER:{raw}")
    return [
        {
            "categories": _categories(match.group("categories")),
            "value": _decimal_text(match.group("value")),
        }
        for match in matches
    ]


def _render_rules(rules: list[dict[str, Any]]) -> str:
    if not rules:
        return ""
    if len(rules) == 1:
        categories = ",".join(str(value) for value in rules[0]["categories"])
        return f"{categories} × {rules[0]['value']}"
    pieces: list[str] = []
    for rule in rules:
        for category in rule["categories"]:
            pieces.append(f"{category}星{rule['value']}支")
    return " ".join(pieces)


def _canonical_rule_map(rules: list[dict[str, Any]]) -> dict[str, str]:
    mapped: dict[str, str] = {}
    for rule in rules:
        for category in rule["categories"]:
            key = str(category)
            if key in mapped and mapped[key] != rule["value"]:
                raise ValueError(f"CONFLICTING_MULTIPLIER_RULE:{key}")
            mapped[key] = rule["value"]
    return mapped


def _cancelled_records(truth: dict[str, Any]) -> list[dict[str, Any]]:
    return [record for record in truth.get("cancelled_bets", []) if isinstance(record, dict)]


def render_structured_human_truth(truth: dict[str, Any]) -> dict[str, Any]:
    """Render structured truth into existing-parser text without using a model."""
    if truth.get("review_status") != "reviewed" or not _all_lines_human_confirmed(truth):
        return {"ok": False, "reason": "HUMAN_CONFIRMATION_INCOMPLETE"}
    shared = truth.get("shared_multiplier_rules")
    if shared:
        return {"ok": False, "reason": "SHARED_MULTIPLIER_SCOPE_NOT_ROUND_TRIPPABLE"}

    text_lines: list[str] = []
    expected_bets: list[dict[str, Any]] = []
    for line in truth.get("lines", []):
        if not isinstance(line, dict) or line.get("cancelled") is True:
            continue
        try:
            groups = [
                [_number(number) for number in group]
                for group in line.get("number_groups", [])
                if isinstance(group, list) and group
            ]
            if not groups:
                raise ValueError("EMPTY_NUMBER_GROUPS")
            if line.get("play_type") == "car_bet":
                raise ValueError("CAR_PLAY_STRUCTURE_NOT_LOSSLESS_IN_EXISTING_PARSER")
            rules = _rules(line)
            _canonical_rule_map(rules)
            layout = str(line.get("layout_hint") or "")
            bet_type = "column" if layout == "column_bet" or len(groups) > 1 else "normal"
            if bet_type == "normal" and len(groups) != 1:
                raise ValueError("NORMAL_LAYOUT_HAS_MULTIPLE_GROUPS")

            special_raw = str(line.get("special_play") or line.get("play_text") or "").strip()
            parser_groups = [[int(number) for number in group] for group in groups]
            special = {"kind": "none", "raw_text": ""}
            rendered_groups = [" ".join(group) for group in groups]
            if special_raw:
                tail = _TAIL_RE.fullmatch(special_raw)
                if not tail:
                    raise ValueError(f"SPECIAL_PLAY_NOT_ROUND_TRIPPABLE:{special_raw}")
                digit = int(tail.group("digit"))
                special = {"kind": "tail", "raw_text": special_raw, "digit": digit}
                rendered_groups.append(special_raw)
                parser_groups.append(expand_tail(digit))
                bet_type = "column"

            body = " × ".join(rendered_groups) if bet_type == "column" else rendered_groups[0]
            rule_text = _render_rules(rules)
            continuation = line.get("continuation") is True
            if continuation and not rule_text:
                raise ValueError("CONTINUATION_WITHOUT_RENDERABLE_MULTIPLIER")
            if continuation:
                text_lines.extend([body, rule_text])
            else:
                text_lines.append(f"{body} {rule_text}".strip())

            expected_bets.append(
                {
                    "source_line_id": line.get("line_id"),
                    "type": bet_type,
                    "number_groups": [[int(number) for number in group] for group in groups],
                    "parser_number_groups": parser_groups,
                    "multiplier_rules": rules,
                    "multiplier_rule_map": _canonical_rule_map(rules),
                    "special_play": special,
                    "continuation": continuation,
                    "cancelled": False,
                }
            )
        except ValueError as exc:
            return {
                "ok": False,
                "reason": str(exc),
                "source_line_id": line.get("line_id"),
            }

    cancelled = _cancelled_records(truth)
    physical_count = len(expected_bets) + len(cancelled)
    declared = truth.get("truth_counts") if isinstance(truth.get("truth_counts"), dict) else {}
    if declared:
        declared_tuple = (
            int(declared.get("human_records") or 0),
            int(declared.get("active_bets") or 0),
            int(declared.get("cancelled_bets") or 0),
        )
        actual_tuple = (physical_count, len(expected_bets), len(cancelled))
        if declared_tuple != actual_tuple:
            return {
                "ok": False,
                "reason": "DECLARED_TRUTH_COUNTS_MISMATCH",
                "declared": declared_tuple,
                "actual": actual_tuple,
            }

    return {
        "ok": True,
        "human_verified_betguard_text": "\n".join(text_lines),
        "source_semantic_result": {
            "schema_version": "betguard-migrated-human-truth-semantics-v1",
            "sample_id": truth.get("sample_id"),
            "physical_record_count": physical_count,
            "active_bet_count": len(expected_bets),
            "cancelled_bet_count": len(cancelled),
            "bets": expected_bets,
            "cancelled_records": cancelled,
            "cancelled_text_syntax_available": False,
            "cancelled_text_policy": "metadata_only_existing_parser_has_no_cancelled_syntax",
        },
    }


def _actual_rule_map(result: dict[str, Any]) -> dict[str, str]:
    mapped: dict[str, str] = {}
    bets = result.get("bets")
    if isinstance(bets, dict) and bets:
        for category, amount in bets.items():
            if isinstance(amount, dict) and amount.get("unit") is not None:
                mapped[str(category)] = _decimal_text(amount["unit"])
        return mapped
    if result.get("unit") is not None:
        value = _decimal_text(result["unit"])
        for category in result.get("stars", []):
            mapped[str(category)] = value
    return mapped


def semantic_round_trip(rendered: dict[str, Any]) -> dict[str, Any]:
    if not rendered.get("ok"):
        return {"exact": False, "reason": rendered.get("reason")}
    text = str(rendered["human_verified_betguard_text"])
    validation = validate_with_existing_parser(text)
    if not validation.get("ok"):
        return {
            "exact": False,
            "reason": "EXISTING_PARSER_REJECTED_RENDERED_TEXT",
            "parser_errors": validation.get("parser_errors", []),
        }

    expected = rendered["source_semantic_result"]["bets"]
    actual = validation["parser_normalized_result"]["bets"]
    if len(expected) != len(actual):
        return {
            "exact": False,
            "reason": "ACTIVE_BET_COUNT_MISMATCH",
            "expected": len(expected),
            "actual": len(actual),
        }

    differences: list[dict[str, Any]] = []
    for index, (source_bet, parser_bet) in enumerate(zip(expected, actual), start=1):
        result = parser_bet.get("result") if isinstance(parser_bet, dict) else {}
        expected_groups = source_bet["parser_number_groups"]
        actual_groups = (
            result.get("columns")
            if result.get("type") == "column"
            else [result.get("numbers") or []]
        )
        actual_continuation = (
            len(parser_bet.get("original_lines") or []) > 1
            and "merged continuation line" in (parser_bet.get("preprocessing_notes") or [])
        )
        special = source_bet["special_play"]
        special_exact = special["kind"] == "none" or special["raw_text"] in str(
            parser_bet.get("raw") or ""
        )
        checks = {
            "type": result.get("type") == source_bet["type"],
            "number_groups": actual_groups == expected_groups,
            "multiplier_rules": _actual_rule_map(result) == source_bet["multiplier_rule_map"],
            "special_play": special_exact,
            "continuation": actual_continuation == source_bet["continuation"],
        }
        if not all(checks.values()):
            differences.append(
                {
                    "bet_index": index,
                    "source_line_id": source_bet.get("source_line_id"),
                    "checks": checks,
                }
            )

    proof = {
        "exact": not differences,
        "schema_version": "betguard-human-truth-round-trip-v1",
        "represented_fields": [
            "number_groups",
            "normal_or_column",
            "multiplier_rules",
            "special_play",
            "continuation",
        ],
        "cancelled_handling": "metadata_only_existing_parser_has_no_cancelled_syntax",
        "expected_semantic_sha256": _json_sha256(rendered["source_semantic_result"]),
        "parser_normalized_sha256": _json_sha256(validation["parser_normalized_result"]),
        "parser_normalized_result": validation["parser_normalized_result"],
        "differences": differences,
    }
    if differences:
        proof["reason"] = "SEMANTIC_ROUND_TRIP_MISMATCH"
    return proof


def round_trip_reviewed_existing_text(text: str) -> dict[str, Any]:
    validation = validate_with_existing_parser(text)
    if not validation.get("ok"):
        return {
            "exact": False,
            "reason": "EXISTING_PARSER_REJECTED_HUMAN_TEXT",
            "parser_errors": validation.get("parser_errors", []),
        }
    normalized = validation["parser_normalized_result"]
    return {
        "exact": True,
        "schema_version": "betguard-human-truth-round-trip-v1",
        "represented_fields": ["existing_human_verified_betguard_text"],
        "cancelled_handling": "not_declared_by_source_text",
        "expected_semantic_sha256": _json_sha256(normalized),
        "parser_normalized_sha256": _json_sha256(normalized),
        "parser_normalized_result": normalized,
        "differences": [],
    }


def backfill_existing_human_truth(
    dataset_source_root: str | Path,
    *,
    acceptance_dataset_root: str | Path,
) -> dict[str, Any]:
    source_root = Path(dataset_source_root)
    destination = Path(acceptance_dataset_root)
    before = get_dataset_status(destination)
    discovery = discover_human_confirmed_sources(source_root)
    imported: list[dict[str, Any]] = []
    rejected_round_trip: list[dict[str, Any]] = []

    for source in discovery["eligible"]:
        sample_id = source["sample_id"]
        if source["kind"] == "structured_truth":
            rendered = render_structured_human_truth(source["truth"])
            if not rendered.get("ok"):
                rejected_round_trip.append(
                    {"sample_id": sample_id, "reason": rendered.get("reason")}
                )
                continue
            text = rendered["human_verified_betguard_text"]
            source_semantic = rendered["source_semantic_result"]
            round_trip = semantic_round_trip(rendered)
        else:
            text = source["truth_path"].read_text(encoding="utf-8").strip()
            round_trip = round_trip_reviewed_existing_text(text)
            source_semantic = {
                "schema_version": "betguard-reviewed-existing-text-semantics-v1",
                "sample_id": sample_id,
                "parser_normalized_result": round_trip.get("parser_normalized_result"),
            }
            round_trip["expected_semantic_sha256"] = _json_sha256(source_semantic)
        if not round_trip.get("exact"):
            rejected_round_trip.append(
                {
                    "sample_id": sample_id,
                    "reason": round_trip.get("reason"),
                    "parser_errors": round_trip.get("parser_errors", []),
                    "differences": round_trip.get("differences", []),
                }
            )
            continue

        saved = save_migrated_human_verified_sample(
            source["image_path"],
            text,
            expected_image_sha256=source["image_sha256"],
            source_sample_id=sample_id,
            source_truth_provenance=source["provenance"],
            source_semantic_result=source_semantic,
            semantic_round_trip=round_trip,
            ai_original_text=None,
            dataset_root=destination,
        )
        if not saved.get("ok"):
            rejected_round_trip.append(
                {
                    "sample_id": sample_id,
                    "reason": saved.get("error", {}).get("code", "SAVE_FAILED"),
                }
            )
            continue
        imported.append(
            {
                "sample_id": sample_id,
                "source_image_sha256": source["image_sha256"],
                "revision_id": saved["sample"]["revision_id"],
                "active_bet_count": source_semantic.get("active_bet_count"),
                "physical_record_count": source_semantic.get("physical_record_count"),
                "ai_original_text_available": False,
            }
        )

    after = get_dataset_status(destination)
    return {
        "schema_version": "betguard-existing-human-truth-backfill-report-v1",
        "found_human_confirmed_samples": len(discovery["eligible"]),
        "round_trip_exact_samples": len(imported),
        "imported": imported,
        "rejected_round_trip": rejected_round_trip,
        "rejected_unverified": discovery["rejected_unverified"],
        "dataset_unique_count_before": before["unique_human_verified_images"],
        "dataset_unique_count_after": after["unique_human_verified_images"],
        "acceptance_dataset_ready": after["acceptance_dataset_ready"],
    }
