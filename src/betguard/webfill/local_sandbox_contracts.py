"""Contracts for the loopback-only assisted-fill sandbox.

This module deliberately contains no browser automation.  It defines the
small public request, the fixed local profile, and the authority snapshot
that must be reloaded by the server immediately before a fill.
"""

from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from typing import Any, Mapping
from urllib.parse import urlsplit


LOCAL_SANDBOX_SITE_ID = "site-betguard-local-sandbox"
LOCAL_SANDBOX_PROFILE_ID = "profile-betguard-local-sandbox-v1"
LOCAL_SANDBOX_HOST = "127.0.0.1"
LOCAL_SANDBOX_PATH = "/sandbox-fill"
PUBLIC_EXECUTE_KEYS = {"schema_version", "action_id", "idempotency_key"}
PUBLIC_EXECUTE_SCHEMA_VERSION = "betguard-local-sandbox-execute-request-v1"

_ACTION_RE = re.compile(r"^lsfa-[a-f0-9]{32}$")
_IDEMPOTENCY_RE = re.compile(r"^lsfi-[A-Za-z0-9._:-]{8,128}$")
_HASH_RE = re.compile(r"^[a-f0-9]{64}$")
_HUMAN_BET_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_FORBIDDEN_CLIENT_KEYS = {
    "bets", "numbers", "number_groups", "multiplier", "continuation",
    "special_play", "selector", "candidate", "candidate_payload",
    "queue", "queue_payload", "claim", "claim_payload", "prepare",
    "prepare_payload", "mapping", "mapping_payload", "operations", "value",
}


class LocalSandboxContractError(Exception):
    """A conservative, user-safe local sandbox contract failure."""

    def __init__(self, code: str, message: str, http_status: int = 422) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.http_status = http_status

    def to_dict(self) -> dict[str, Any]:
        return {"error": {"code": self.code, "message": self.message}}


def canonical_sha256(value: Any) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def decode_public_execute_request(payload: Mapping[str, Any]) -> dict[str, str]:
    """Accept only the version plus two opaque tokens submitted by the UI."""

    if not isinstance(payload, Mapping) or set(payload) != PUBLIC_EXECUTE_KEYS:
        raise LocalSandboxContractError(
            "SANDBOX_REQUEST_INVALID",
            "request accepts only schema_version, action_id and idempotency_key",
            400,
        )
    lowered = {str(key).lower() for key in payload}
    if lowered & _FORBIDDEN_CLIENT_KEYS:
        raise LocalSandboxContractError(
            "SANDBOX_CLIENT_VALUE_REJECTED", "client values and selectors are forbidden", 400
        )
    if payload.get("schema_version") != PUBLIC_EXECUTE_SCHEMA_VERSION:
        raise LocalSandboxContractError(
            "SANDBOX_REQUEST_INVALID", "sandbox request schema invalid", 400
        )
    action_id = payload.get("action_id")
    key = payload.get("idempotency_key")
    if not isinstance(action_id, str) or not _ACTION_RE.fullmatch(action_id):
        raise LocalSandboxContractError("SANDBOX_REQUEST_INVALID", "action_id invalid", 400)
    if not isinstance(key, str) or not _IDEMPOTENCY_RE.fullmatch(key):
        raise LocalSandboxContractError(
            "SANDBOX_REQUEST_INVALID", "idempotency_key invalid", 400
        )
    return {
        "schema_version": PUBLIC_EXECUTE_SCHEMA_VERSION,
        "action_id": action_id,
        "idempotency_key": key,
    }


def require_loopback_url(url: str, *, expected_port: int | None = None) -> str:
    """Reject aliases such as localhost, IPv6 loopback, redirects, and HTTPS."""

    if not isinstance(url, str):
        raise LocalSandboxContractError("SANDBOX_EXTERNAL_URL_REJECTED", "URL invalid", 400)
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError as exc:
        raise LocalSandboxContractError(
            "SANDBOX_EXTERNAL_URL_REJECTED", "URL authority invalid", 400
        ) from exc
    if (
        parsed.scheme != "http"
        or parsed.hostname != LOCAL_SANDBOX_HOST
        or parsed.username is not None
        or parsed.password is not None
        or port is None
        or (expected_port is not None and port != expected_port)
        or parsed.path != LOCAL_SANDBOX_PATH
        or parsed.query
        or parsed.fragment
    ):
        raise LocalSandboxContractError(
            "SANDBOX_EXTERNAL_URL_REJECTED",
            "only the exact 127.0.0.1 sandbox URL is allowed",
            403,
        )
    return url


def local_sandbox_profile() -> dict[str, Any]:
    """The immutable logical-to-local-page contract; it has no submit action."""

    profile = {
        "schema_version": "betguard-local-sandbox-profile-v1",
        "site_id": LOCAL_SANDBOX_SITE_ID,
        "profile_id": LOCAL_SANDBOX_PROFILE_ID,
        "host": LOCAL_SANDBOX_HOST,
        "path": LOCAL_SANDBOX_PATH,
        "allowed_actions": ["SET_ALLOWLISTED_VALUE", "DISPATCH_INPUT", "DISPATCH_CHANGE", "READ_ALLOWLISTED_VALUE"],
        "bet_fields": [
            "human_bet_id", "bet_type", "number_groups", "multiplier",
            "continuation", "special_play",
        ],
        "maximum_bets": 100,
        "submit_action": None,
        "safety": {
            "loopback_only": True,
            "external_navigation": False,
            "arbitrary_selector": False,
            "arbitrary_javascript": False,
            "cookies_or_storage": False,
            "submit": False,
            "auto_submit": False,
        },
    }
    profile["profile_integrity_hash"] = canonical_sha256(profile)
    return profile


def validate_authority_snapshot(value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate the exact, server-loaded Review→Mapping authority snapshot."""

    expected = {
        "schema_version", "review", "candidate", "queue", "claim", "prepare",
        "mapping", "operations", "cancelled_exclusions", "safety",
    }
    if not isinstance(value, Mapping) or set(value) != expected:
        raise LocalSandboxContractError(
            "SANDBOX_AUTHORITY_INVALID", "authority snapshot shape invalid", 409
        )
    if value.get("schema_version") != "betguard-local-sandbox-authority-v1":
        raise LocalSandboxContractError(
            "SANDBOX_AUTHORITY_INVALID", "authority schema invalid", 409
        )
    review = _exact_mapping(value["review"], {
        "review_session_id", "status", "human_answer_revision", "human_answer_hash",
    }, "Review")
    if review["status"] != "CONFIRMED" or not _positive_int(review["human_answer_revision"]):
        raise LocalSandboxContractError("SANDBOX_REVIEW_UNCONFIRMED", "Review is not confirmed", 409)
    _require_text(review["review_session_id"], "review_session_id")
    _require_hash(review["human_answer_hash"], "human_answer_hash")

    candidate = _exact_mapping(value["candidate"], {
        "candidate_id", "candidate_revision", "canonical_content_hash",
        "validation_status", "lifecycle_state",
    }, "Candidate")
    if candidate["validation_status"] != "VALID_CURRENT" or candidate["lifecycle_state"] != "CURRENT":
        raise LocalSandboxContractError("SANDBOX_CANDIDATE_INVALID", "Candidate is not VALID_CURRENT", 409)
    _require_text(candidate["candidate_id"], "candidate_id")
    if not _positive_int(candidate["candidate_revision"]):
        raise LocalSandboxContractError("SANDBOX_CANDIDATE_INVALID", "Candidate revision invalid", 409)
    _require_hash(candidate["canonical_content_hash"], "canonical_content_hash")

    queue = _exact_mapping(value["queue"], {
        "queue_entry_id", "enqueue_sequence", "queue_revision", "entry_integrity_hash",
        "state", "fifo_head",
    }, "Queue")
    if queue["state"] != "QUEUED" or queue["fifo_head"] is not True:
        raise LocalSandboxContractError("SANDBOX_QUEUE_INVALID", "Queue is not the FIFO head", 409)
    if not _positive_int(queue["enqueue_sequence"]) or not _positive_int(queue["queue_revision"]):
        raise LocalSandboxContractError("SANDBOX_QUEUE_INVALID", "Queue revision invalid", 409)
    _require_text(queue["queue_entry_id"], "queue_entry_id")
    _require_hash(queue["entry_integrity_hash"], "entry_integrity_hash")

    claim = _exact_mapping(value["claim"], {
        "claim_id", "claim_generation", "state", "fencing_token_hash", "lease_expires_at",
    }, "Claim")
    if claim["state"] != "ACTIVE" or not _positive_int(claim["claim_generation"]):
        raise LocalSandboxContractError("SANDBOX_CLAIM_INVALID", "Claim is not active", 409)
    _require_text(claim["claim_id"], "claim_id")
    _require_text(claim["lease_expires_at"], "lease_expires_at")
    _require_hash(claim["fencing_token_hash"], "fencing_token_hash")

    prepare = _exact_mapping(value["prepare"], {
        "prepare_id", "state", "record_integrity_hash", "deterministic_plan_hash",
        "authority_binding_hash",
    }, "Prepare")
    if prepare["state"] != "PREPARED":
        raise LocalSandboxContractError("SANDBOX_PREPARE_INVALID", "Prepare is not valid", 409)
    _require_text(prepare["prepare_id"], "prepare_id")
    for key in ("record_integrity_hash", "deterministic_plan_hash", "authority_binding_hash"):
        _require_hash(prepare[key], key)

    mapping = _exact_mapping(value["mapping"], {
        "mapping_preview_id", "state", "status", "record_integrity_hash",
    }, "Mapping")
    if mapping["state"] != "PREVIEWED" or mapping["status"] != "VALID_MAPPING_PREVIEW":
        raise LocalSandboxContractError("SANDBOX_MAPPING_INVALID", "Mapping is not valid", 409)
    _require_text(mapping["mapping_preview_id"], "mapping_preview_id")
    _require_hash(mapping["record_integrity_hash"], "mapping record_integrity_hash")

    operations = value["operations"]
    exclusions = value["cancelled_exclusions"]
    if not isinstance(operations, list) or not operations:
        raise LocalSandboxContractError("SANDBOX_MAPPING_INVALID", "no fill operations", 409)
    if not isinstance(exclusions, list):
        raise LocalSandboxContractError("SANDBOX_MAPPING_INVALID", "cancelled exclusions invalid", 409)
    seen: set[str] = set()
    normalized = [_validate_operation(operation, index, seen) for index, operation in enumerate(operations, 1)]
    excluded_ids: set[str] = set()
    for exclusion in exclusions:
        item = _exact_mapping(exclusion, {"human_bet_id", "excluded_reason"}, "cancelled exclusion")
        _require_text(item["human_bet_id"], "cancelled human_bet_id")
        if item["human_bet_id"] in seen or item["human_bet_id"] in excluded_ids:
            raise LocalSandboxContractError("SANDBOX_CANCELLED_INVALID", "cancelled bet identity overlaps", 409)
        if item["excluded_reason"] != "cancelled_non_executable":
            raise LocalSandboxContractError("SANDBOX_CANCELLED_INVALID", "cancelled reason invalid", 409)
        excluded_ids.add(item["human_bet_id"])
    if value["safety"] != {
        "server_reloaded": True, "client_values_used": False,
        "browser_receipt_value_authority": False, "submit_authorized": False,
        "external_site_authorized": False,
    }:
        raise LocalSandboxContractError("SANDBOX_AUTHORITY_INVALID", "safety contract invalid", 409)
    result = deepcopy(dict(value))
    result["operations"] = normalized
    return result


def authority_identity(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    """Return identity-only binding data; no bet values are persisted in actions."""

    return deepcopy({key: snapshot[key] for key in (
        "review", "candidate", "queue", "claim", "prepare", "mapping",
    )})


def _validate_operation(value: Any, expected_index: int, seen: set[str]) -> dict[str, Any]:
    operation = _exact_mapping(value, {
        "operation_index", "human_bet_id", "bet_type", "number_groups", "multiplier",
        "continuation", "special_play", "cancelled", "active", "value_authority",
    }, "operation")
    if operation["operation_index"] != expected_index:
        raise LocalSandboxContractError("SANDBOX_MAPPING_INVALID", "operation order invalid", 409)
    human_id = operation["human_bet_id"]
    if not isinstance(human_id, str) or not _HUMAN_BET_RE.fullmatch(human_id) or human_id in seen:
        raise LocalSandboxContractError("SANDBOX_MAPPING_INVALID", "Human Bet identity invalid", 409)
    seen.add(human_id)
    bet_type = operation["bet_type"]
    groups = operation["number_groups"]
    if bet_type not in {"normal", "column"} or not isinstance(groups, list) or not groups:
        raise LocalSandboxContractError("SANDBOX_MAPPING_INVALID", "bet grouping invalid", 409)
    if (bet_type == "normal" and len(groups) != 1) or (bet_type == "column" and len(groups) < 2):
        raise LocalSandboxContractError("SANDBOX_MAPPING_INVALID", "bet grouping was flattened", 409)
    for group in groups:
        if not isinstance(group, list) or not group or any(not isinstance(n, str) for n in group):
            raise LocalSandboxContractError("SANDBOX_MAPPING_INVALID", "number group invalid", 409)
    multiplier = operation["multiplier"]
    if not isinstance(multiplier, Mapping) or set(multiplier) != {"ordered_rules", "scope", "resolved"}:
        raise LocalSandboxContractError("SANDBOX_MAPPING_INVALID", "multiplier structure invalid", 409)
    if not isinstance(multiplier["ordered_rules"], list) or not multiplier["ordered_rules"] or multiplier["resolved"] is not True:
        raise LocalSandboxContractError("SANDBOX_MAPPING_INVALID", "multiplier rules invalid", 409)
    continuation = operation["continuation"]
    special = operation["special_play"]
    if not isinstance(continuation, Mapping) or continuation.get("resolved") is not True or not isinstance(continuation.get("present"), bool):
        raise LocalSandboxContractError("SANDBOX_MAPPING_INVALID", "continuation invalid", 409)
    if not isinstance(special, Mapping) or special.get("resolved") is not True or not isinstance(special.get("kind"), str):
        raise LocalSandboxContractError("SANDBOX_MAPPING_INVALID", "special play invalid", 409)
    if operation["cancelled"] is not False or operation["active"] is not True or operation["value_authority"] != "validated_candidate":
        raise LocalSandboxContractError("SANDBOX_CANCELLED_INVALID", "non-executable operation present", 409)
    return deepcopy(dict(operation))


def _exact_mapping(value: Any, keys: set[str], label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != keys:
        raise LocalSandboxContractError("SANDBOX_AUTHORITY_INVALID", f"{label} shape invalid", 409)
    return value


def _positive_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 1


def _require_text(value: Any, label: str) -> None:
    if not isinstance(value, str) or not value:
        raise LocalSandboxContractError("SANDBOX_AUTHORITY_INVALID", f"{label} invalid", 409)


def _require_hash(value: Any, label: str) -> None:
    if not isinstance(value, str) or not _HASH_RE.fullmatch(value):
        raise LocalSandboxContractError("SANDBOX_AUTHORITY_INVALID", f"{label} invalid", 409)
