"""Server-authoritative Local Sandbox Assisted Fill workflow."""

from __future__ import annotations

import hashlib
import json
import secrets
import threading
from copy import deepcopy
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol

from betguard.webfill.local_sandbox_contracts import (
    LocalSandboxContractError,
    authority_identity,
    canonical_sha256,
    decode_public_execute_request,
    validate_authority_snapshot,
)
from betguard.webfill.local_sandbox_provider import (
    SandboxBrowser,
    SandboxPartialFillError,
    expected_readback,
)


class AuthorityLoader(Protocol):
    def __call__(self, review_session_id: str, actor: str, session_id: str) -> Mapping[str, Any]: ...


class MappingPreviewAuthorityLoader:
    """Reload all existing Gate 3B/3C stores from a server-only reference.

    ``register_reference`` is an application composition API, not a public
    request decoder.  It stores identities/fences only and never accepts bet
    values, selectors, or logical plans.
    """

    def __init__(self, mapping_store: Any, prepare_store: Any, queue_store: Any, claim_store: Any) -> None:
        self._mapping = mapping_store
        self._prepare = prepare_store
        self._queue = queue_store
        self._claims = claim_store
        self._references: dict[str, dict[str, Any]] = {}
        self._lock = threading.RLock()

    def register_reference(
        self,
        *,
        review_session_id: str,
        mapping_preview_id: str,
        claim_generation: int,
        fencing_token: str,
        authenticated_principal: str,
        consumer_id: str,
        server_session_id: str,
    ) -> None:
        values = (
            review_session_id, mapping_preview_id, fencing_token,
            authenticated_principal, consumer_id, server_session_id,
        )
        if any(not isinstance(value, str) or not value for value in values):
            raise ValueError("authority reference identities must be non-empty")
        if not isinstance(claim_generation, int) or isinstance(claim_generation, bool) or claim_generation < 1:
            raise ValueError("claim_generation invalid")
        reference = {
            "mapping_preview_id": mapping_preview_id,
            "claim_generation": claim_generation,
            "fencing_token": fencing_token,
            "authenticated_principal": authenticated_principal,
            "consumer_id": consumer_id,
            "server_session_id": server_session_id,
        }
        with self._lock:
            previous = self._references.get(review_session_id)
            if previous is not None and previous != reference:
                raise LocalSandboxContractError(
                    "SANDBOX_AUTHORITY_CONFLICT", "review reference is already bound", 409
                )
            self._references[review_session_id] = reference

    def __call__(self, review_session_id: str, actor: str, session_id: str) -> Mapping[str, Any]:
        with self._lock:
            reference = deepcopy(self._references.get(review_session_id))
        if reference is None:
            raise LocalSandboxContractError(
                "SANDBOX_AUTHORITY_NOT_FOUND", "server authority reference not found", 404
            )
        if reference["authenticated_principal"] != actor or reference["server_session_id"] != session_id:
            raise LocalSandboxContractError(
                "SANDBOX_ACTION_OWNER_INVALID", "authority owner/session mismatch", 403
            )
        owner = {
            "claim_generation": reference["claim_generation"],
            "fencing_token": reference["fencing_token"],
            "authenticated_principal": actor,
            "consumer_id": reference["consumer_id"],
            "server_session_id": session_id,
        }
        mapping_result = self._mapping.get_mapping_preview_state(
            reference["mapping_preview_id"], **owner
        )
        if mapping_result.get("state") != "PREVIEWED" or mapping_result.get("status") != "VALID_MAPPING_PREVIEW":
            raise LocalSandboxContractError(
                "SANDBOX_MAPPING_INVALID", "Mapping Preview is no longer valid", 409
            )
        human = self._mapping.get_human_preview(reference["mapping_preview_id"], **owner)
        mapping_artifact = mapping_result["artifact"]
        prepare_id = mapping_artifact["authority"]["prepare"]["prepare_id"]
        prepare_result = self._prepare.get_prepare_state(prepare_id, **owner)
        if prepare_result.get("state") != "PREPARED" or prepare_result.get("status") != "VALID_PREPARED":
            raise LocalSandboxContractError(
                "SANDBOX_PREPARE_INVALID", "Prepare is no longer valid", 409
            )
        prepared = prepare_result["artifact"]
        claim_id = prepared["authority"]["claim"]["claim_id"]
        claim = self._claims.get_authoritative_claim(
            claim_id=claim_id,
            claim_generation=reference["claim_generation"],
            authenticated_principal=actor,
            consumer_id=reference["consumer_id"],
            server_session_id=session_id,
            fencing_token=reference["fencing_token"],
        )
        queue_id = prepared["authority"]["queue"]["queue_entry_id"]
        queue_item = self._queue.get_entry(queue_id)
        queued = self._queue.list_entries(state="QUEUED")
        if queue_item is None or queue_item["state"] != "QUEUED" or not queued:
            raise LocalSandboxContractError("SANDBOX_QUEUE_INVALID", "Queue entry unavailable", 409)
        fifo_head = queued[0]["queue_entry"]["queue_entry_id"] == queue_id
        validation = claim["validation"]
        source = validation["source"]
        if source["review_session_id"] != review_session_id:
            raise LocalSandboxContractError(
                "SANDBOX_REVIEW_INVALID", "Review identity differs from Candidate authority", 409
            )
        human_operations = [row["human_confirmed_value"] for row in human["bets"]]
        if human_operations != prepared["logical_plan"]["operations"]:
            raise LocalSandboxContractError(
                "SANDBOX_MAPPING_INVALID", "Human Preview differs from Prepare operations", 409
            )
        operations = [_sandbox_operation(operation) for operation in human_operations]
        exclusions = [
            {
                "human_bet_id": item["human_bet_id"],
                "excluded_reason": item["excluded_reason"],
            }
            for item in human["cancelled_exclusions"]
        ]
        entry = queue_item["queue_entry"]
        return {
            "schema_version": "betguard-local-sandbox-authority-v1",
            "review": {
                "review_session_id": source["review_session_id"],
                "status": "CONFIRMED",
                "human_answer_revision": source["human_answer_revision"],
                "human_answer_hash": source["human_answer_hash"],
            },
            "candidate": {
                "candidate_id": validation["candidate_id"],
                "candidate_revision": validation["candidate_revision"],
                "canonical_content_hash": validation["canonical_content_hash"],
                "validation_status": validation["validation_status"],
                "lifecycle_state": validation["lifecycle_state"],
            },
            "queue": {
                "queue_entry_id": queue_id,
                "enqueue_sequence": entry["enqueue_sequence"],
                "queue_revision": prepared["authority"]["queue"]["queue_revision"],
                "entry_integrity_hash": entry["entry_integrity_hash"],
                "state": queue_item["state"],
                "fifo_head": fifo_head,
            },
            "claim": {
                "claim_id": claim_id,
                "claim_generation": claim["claim_generation"],
                "state": claim["status"],
                "fencing_token_hash": hashlib.sha256(reference["fencing_token"].encode()).hexdigest(),
                "lease_expires_at": claim["lease_expires_at"],
            },
            "prepare": {
                "prepare_id": prepare_id,
                "state": prepare_result["state"],
                "record_integrity_hash": prepared["record_integrity_hash"],
                "deterministic_plan_hash": prepared["deterministic_plan_hash"],
                "authority_binding_hash": prepared["authority_binding_hash"],
            },
            "mapping": {
                "mapping_preview_id": mapping_artifact["mapping_preview_id"],
                "state": mapping_result["state"],
                "status": mapping_result["status"],
                "record_integrity_hash": mapping_artifact["record_integrity_hash"],
            },
            "operations": operations,
            "cancelled_exclusions": exclusions,
            "safety": {
                "server_reloaded": True,
                "client_values_used": False,
                "browser_receipt_value_authority": False,
                "submit_authorized": False,
                "external_site_authorized": False,
            },
        }


class LocalSandboxWorkflow:
    """Mint identity-only human actions and execute them exactly once."""

    def __init__(self, base_dir: Path | str, authority_loader: AuthorityLoader, browser: SandboxBrowser) -> None:
        self.base_dir = Path(base_dir)
        self._loader = authority_loader
        self._browser = browser
        self._actions = self.base_dir / "actions"
        self._transactions = self.base_dir / "transactions"
        self._results = self.base_dir / "results"
        for directory in (self._actions, self._transactions, self._results):
            directory.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    def bind_human_fill_action(
        self, review_session_id: str, *, authenticated_actor: str, interactive_session_id: str
    ) -> dict[str, Any]:
        _require_owner(review_session_id, authenticated_actor, interactive_session_id)
        snapshot = validate_authority_snapshot(
            self._loader(review_session_id, authenticated_actor, interactive_session_id)
        )
        action_id = f"lsfa-{secrets.token_hex(16)}"
        action = {
            "schema_version": "betguard-local-sandbox-action-v1",
            "action_id": action_id,
            "sandbox_session_id": f"lss-{secrets.token_hex(16)}",
            "review_session_id": review_session_id,
            "authenticated_actor": authenticated_actor,
            "interactive_session_id": interactive_session_id,
            "authority_identity": authority_identity(snapshot),
            "authority_snapshot_hash": canonical_sha256(snapshot),
            "consumed_idempotency_hash": None,
            "contains_values": False,
        }
        _write_immutable(self._actions / f"{action_id}.json", action)
        return {
            "status": "READY_FOR_EXPLICIT_HUMAN_ACTION",
            "action_id": action_id,
            "sandbox_session_id": action["sandbox_session_id"],
            "submit_authorized": False,
            "auto_submit": False,
        }

    def execute_public(
        self,
        payload: Mapping[str, Any],
        *,
        authenticated_actor: str,
        interactive_session_id: str,
    ) -> dict[str, Any]:
        request = decode_public_execute_request(payload)
        return self.execute_bound_action(
            request["action_id"], request["idempotency_key"],
            authenticated_actor=authenticated_actor,
            interactive_session_id=interactive_session_id,
        )

    def execute_bound_action(
        self,
        action_id: str,
        idempotency_key: str,
        *,
        authenticated_actor: str,
        interactive_session_id: str,
    ) -> dict[str, Any]:
        request = decode_public_execute_request({
            "schema_version": "betguard-local-sandbox-execute-request-v1",
            "action_id": action_id, "idempotency_key": idempotency_key,
        })
        idem_hash = hashlib.sha256(request["idempotency_key"].encode()).hexdigest()
        result_path = self._results / f"{idem_hash}.json"
        transaction_path = self._transactions / f"{idem_hash}.json"
        with self._lock:
            if result_path.exists():
                result = _read(result_path)
                if result.get("action_id") != action_id:
                    raise LocalSandboxContractError(
                        "SANDBOX_IDEMPOTENCY_CONFLICT", "idempotency key belongs to another action", 409
                    )
                return deepcopy(result)
            action_path = self._actions / f"{action_id}.json"
            if not action_path.exists():
                raise LocalSandboxContractError("SANDBOX_ACTION_NOT_FOUND", "action not found", 404)
            action = _read(action_path)
            _validate_action(action, action_id)
            if (
                action["authenticated_actor"] != authenticated_actor
                or action["interactive_session_id"] != interactive_session_id
            ):
                raise LocalSandboxContractError(
                    "SANDBOX_ACTION_OWNER_INVALID", "action owner/session mismatch", 403
                )
            consumed = action["consumed_idempotency_hash"]
            if consumed is not None and consumed != idem_hash:
                raise LocalSandboxContractError(
                    "SANDBOX_ACTION_ALREADY_USED", "action is already bound to another click", 409
                )
            if transaction_path.exists():
                transaction = _read(transaction_path)
                result = self._manual_result(
                    action, idempotency_key, transaction.get("filled_field_pointers", []),
                    "incomplete_previous_attempt", 0,
                )
                _write_immutable(result_path, result)
                return deepcopy(result)
            snapshot = self._reload_exact(action)
            transaction = {
                "schema_version": "betguard-local-sandbox-transaction-v1",
                "action_id": action_id,
                "idempotency_key_hash": idem_hash,
                "state": "STARTED",
                "authority_snapshot_hash": canonical_sha256(snapshot),
                "filled_field_pointers": [],
            }
            _write_immutable(transaction_path, transaction)
            action["consumed_idempotency_hash"] = idem_hash
            _write_atomic(action_path, action)
            try:
                receipt = self._browser.fill(snapshot["operations"])
                self._verify_receipt(receipt, snapshot)
                after = validate_authority_snapshot(
                    self._loader(
                        action["review_session_id"], authenticated_actor,
                        interactive_session_id,
                    )
                )
                if after != snapshot:
                    raise SandboxPartialFillError(
                        "authority changed during fill",
                        filled_field_pointers=receipt.get("filled_field_pointers", []),
                    )
                result = {
                    "status": "FILLED_VERIFIED",
                    "action_id": action_id,
                    "sandbox_session_id": action["sandbox_session_id"],
                    "filled_field_pointers": deepcopy(receipt["filled_field_pointers"]),
                    "verification_status": "EXACT_READBACK",
                    "cancelled_excluded_count": len(snapshot["cancelled_exclusions"]),
                    "submit_performed": False,
                    "external_site_calls": 0,
                    "auto_submit": False,
                    "replayed": False,
                }
            except SandboxPartialFillError as exc:
                result = self._manual_result(
                    action, idempotency_key, exc.filled_field_pointers,
                    "partial_fill_or_verification_failure",
                    len(snapshot["cancelled_exclusions"]),
                )
            except Exception:
                result = self._manual_result(
                    action, idempotency_key, [], "browser_crash_or_authority_failure",
                    len(snapshot["cancelled_exclusions"]),
                )
            _write_immutable(result_path, result)
            return deepcopy(result)

    def _reload_exact(self, action: Mapping[str, Any]) -> dict[str, Any]:
        snapshot = validate_authority_snapshot(
            self._loader(
                action["review_session_id"], action["authenticated_actor"],
                action["interactive_session_id"],
            )
        )
        if (
            authority_identity(snapshot) != action["authority_identity"]
            or canonical_sha256(snapshot) != action["authority_snapshot_hash"]
        ):
            raise LocalSandboxContractError(
                "SANDBOX_AUTHORITY_CHANGED", "bound authority changed before fill", 409
            )
        return snapshot

    @staticmethod
    def _verify_receipt(receipt: Mapping[str, Any], snapshot: Mapping[str, Any]) -> None:
        if not isinstance(receipt, Mapping):
            raise SandboxPartialFillError("browser receipt invalid")
        expected_pointers = [
            f"/bets/{index}/{field}"
            for index in range(len(snapshot["operations"]))
            for field in ("human_bet_id", "bet_type", "number_groups", "multiplier", "continuation", "special_play")
        ]
        if (
            receipt.get("filled_field_pointers") != expected_pointers
            or receipt.get("readback") != expected_readback(snapshot["operations"])
            or receipt.get("submit_event_count") != 0
            or receipt.get("external_request_count") != 0
        ):
            raise SandboxPartialFillError(
                "receipt verification failed",
                filled_field_pointers=receipt.get("filled_field_pointers", []),
            )

    @staticmethod
    def _manual_result(
        action: Mapping[str, Any], idempotency_key: str,
        pointers: list[str], reason: str, cancelled_count: int,
    ) -> dict[str, Any]:
        del idempotency_key
        return {
            "status": "MANUAL_REVIEW_REQUIRED",
            "action_id": action["action_id"],
            "sandbox_session_id": action["sandbox_session_id"],
            "filled_field_pointers": deepcopy(pointers),
            "verification_status": "NOT_VERIFIED",
            "manual_review_reason": reason,
            "cancelled_excluded_count": cancelled_count,
            "submit_performed": False,
            "external_site_calls": 0,
            "auto_submit": False,
            "replayed": False,
        }


def _sandbox_operation(operation: Mapping[str, Any]) -> dict[str, Any]:
    return deepcopy({
        "operation_index": operation["operation_index"],
        "human_bet_id": operation["human_bet_id"],
        "bet_type": operation["bet_type"],
        "number_groups": operation["number_groups"],
        "multiplier": operation["multiplier"],
        "continuation": operation["continuation"],
        "special_play": operation["special_play"],
        "cancelled": operation["cancelled"],
        "active": operation["active"],
        "value_authority": operation["value_authority"],
    })


def _require_owner(review: Any, actor: Any, session: Any) -> None:
    if any(not isinstance(value, str) or not value for value in (review, actor, session)):
        raise LocalSandboxContractError("SANDBOX_REQUEST_INVALID", "owner/session invalid", 400)


def _validate_action(action: Any, expected_id: str) -> None:
    keys = {
        "schema_version", "action_id", "sandbox_session_id", "review_session_id",
        "authenticated_actor", "interactive_session_id", "authority_identity",
        "authority_snapshot_hash", "consumed_idempotency_hash", "contains_values",
    }
    if (
        not isinstance(action, Mapping) or set(action) != keys
        or action.get("schema_version") != "betguard-local-sandbox-action-v1"
        or action.get("action_id") != expected_id
        or action.get("contains_values") is not False
    ):
        raise LocalSandboxContractError("SANDBOX_STORE_CORRUPT", "action record invalid", 500)


def _read(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise LocalSandboxContractError("SANDBOX_STORE_CORRUPT", "record unreadable", 500) from exc
    if not isinstance(value, dict):
        raise LocalSandboxContractError("SANDBOX_STORE_CORRUPT", "record invalid", 500)
    return value


def _write_immutable(path: Path, value: Mapping[str, Any]) -> None:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    try:
        with path.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(encoded)
            stream.flush()
    except FileExistsError:
        if _read(path) != value:
            raise LocalSandboxContractError("SANDBOX_STORE_CORRUPT", "immutable record differs", 500)


def _write_atomic(path: Path, value: Mapping[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    temporary.write_text(encoded, encoding="utf-8", newline="\n")
    temporary.replace(path)
