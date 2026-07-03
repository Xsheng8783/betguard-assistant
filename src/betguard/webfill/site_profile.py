"""Local, offline persistence wrapper around a previously produced selector_report.

This module never inspects or operates a live page. It only reads and writes
plain JSON-serializable dicts that were already produced elsewhere, and
reshapes a saved profile back into the selector_report shape expected by the
existing dry-run mapping and mapping report functions.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


PROFILE_VERSION = 1

REQUIRED_TOP_LEVEL_KEYS = (
    "profile_version",
    "site_name",
    "page_name",
    "captured_at",
    "number_candidates",
    "amount_field_candidates",
    "danger_candidates",
    "market_state",
)

REQUIRED_AMOUNT_FIELD_STARS = ("二星", "三星", "四星")
REQUIRED_NUMBER_COUNT = 39

STATUS_OK = "OK"
STATUS_BLOCKED = "BLOCKED"


def build_site_profile(
    selector_report: dict[str, Any],
    *,
    site_name: str,
    page_name: str,
    captured_at: str | None = None,
) -> dict[str, Any]:
    number_candidates = dict(selector_report.get("number_candidates") or {})
    amount_field_candidates = dict(selector_report.get("amount_field_candidates") or {})
    danger_candidates = list(selector_report.get("danger_candidates") or [])
    market_state = dict(selector_report.get("market_state") or {})

    return {
        "profile_version": PROFILE_VERSION,
        "site_name": site_name,
        "page_name": page_name,
        "captured_at": captured_at,
        "number_candidates": number_candidates,
        "amount_field_candidates": amount_field_candidates,
        "danger_candidates": danger_candidates,
        "market_state": market_state,
        "metadata": {
            "number_count": _found_count(number_candidates),
            "amount_field_count": _found_count(amount_field_candidates),
            "danger_candidate_count": len(danger_candidates),
        },
    }


def validate_site_profile(profile: dict[str, Any]) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []

    missing_keys = [key for key in REQUIRED_TOP_LEVEL_KEYS if key not in profile]
    if missing_keys:
        errors.append(f"missing required top-level keys: {', '.join(missing_keys)}")

    number_candidates = profile.get("number_candidates")
    number_candidates = number_candidates if isinstance(number_candidates, dict) else {}
    number_count = _found_count(number_candidates)
    if number_count < REQUIRED_NUMBER_COUNT:
        errors.append(f"fewer than {REQUIRED_NUMBER_COUNT} number candidates found: {number_count}")

    amount_field_candidates = profile.get("amount_field_candidates")
    amount_field_candidates = amount_field_candidates if isinstance(amount_field_candidates, dict) else {}
    missing_amount_fields = [
        star for star in REQUIRED_AMOUNT_FIELD_STARS if not amount_field_candidates.get(star)
    ]
    if missing_amount_fields:
        errors.append(f"missing amount field candidates: {', '.join(missing_amount_fields)}")

    danger_candidates = profile.get("danger_candidates")
    danger_candidates = danger_candidates if isinstance(danger_candidates, list) else []
    if not danger_candidates:
        errors.append("missing danger candidates; profile cannot be considered safe")

    status = STATUS_BLOCKED if errors else STATUS_OK
    return {
        "status": status,
        "errors": errors,
        "warnings": warnings,
    }


def save_site_profile(profile: dict[str, Any], path: str | Path) -> None:
    Path(path).write_text(json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8")


def load_site_profile(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def profile_as_selector_report(profile: dict[str, Any]) -> dict[str, Any]:
    return {
        "number_candidates": dict(profile.get("number_candidates") or {}),
        "amount_field_candidates": dict(profile.get("amount_field_candidates") or {}),
        "danger_candidates": list(profile.get("danger_candidates") or []),
        "market_state": dict(profile.get("market_state") or {}),
    }


def _found_count(candidates: dict[str, Any]) -> int:
    return len([label for label, records in candidates.items() if records])


def format_pretty_site_profile_validation(profile: dict[str, Any], validation: dict[str, Any]) -> str:
    metadata = profile.get("metadata") if isinstance(profile.get("metadata"), dict) else {}
    lines = [
        "Site Profile Validation",
        "",
        f"Site: {profile.get('site_name') or ''}",
        f"Page: {profile.get('page_name') or ''}",
        f"Captured at: {profile.get('captured_at') or ''}",
        "",
        "Counts:",
        f"- number candidates: {metadata.get('number_count', 0)}",
        f"- amount field candidates: {metadata.get('amount_field_count', 0)}",
        f"- danger candidates: {metadata.get('danger_candidate_count', 0)}",
        "",
        f"Status: {validation.get('status', STATUS_BLOCKED)}",
    ]

    if validation.get("errors"):
        lines.extend(["", "Errors:"])
        lines.extend(f"- {error}" for error in validation["errors"])

    if validation.get("warnings"):
        lines.extend(["", "Warnings:"])
        lines.extend(f"- {warning}" for warning in validation["warnings"])

    return "\n".join(lines)
