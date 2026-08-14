"""Read-only Candidate consumption authority validation.

The validator accepts identity only.  It reloads the immutable Candidate and
current HumanReview from :mod:`candidate_authority`, then validates freshness,
content identity, lifecycle state, and the complete authority contract.  It
does not approve or execute any downstream action.
"""

from __future__ import annotations

import re
from copy import deepcopy
from typing import Any, Mapping

from betguard.vision.candidate_authority import (
    CANDIDATE_SCHEMA_VERSION,
    CandidateAuthorityError,
    VisionCandidateAuthorityStore,
    canonical_sha256,
)


CONSUMPTION_VALIDATION_SCHEMA_VERSION = (
    "vision-candidate-consumption-validation-v1"
)

_CANDIDATE_ID_RE = re.compile(r"^vc-[a-f0-9]{32}$")
_HASH_RE = re.compile(r"^[a-f0-9]{64}$")


def _fail(code: str, message: str, status: int) -> CandidateAuthorityError:
    return CandidateAuthorityError(code, message, status)


class CandidateConsumptionAuthorityValidator:
    """Validate a persisted Candidate without creating side effects."""

    def __init__(self, store: VisionCandidateAuthorityStore) -> None:
        if not isinstance(store, VisionCandidateAuthorityStore):
            raise TypeError("store must be VisionCandidateAuthorityStore")
        self._store = store

    def validate_request(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        """Decode the exact identity-only public request contract."""

        if not isinstance(payload, Mapping):
            raise _fail(
                "CANDIDATE_REQUEST_INVALID", "request must be a JSON object", 400
            )
        allowed = {
            "candidate_id", "expected_candidate_revision", "expected_content_hash"
        }
        actual = set(payload)
        if "candidate_id" not in actual or not actual <= allowed:
            raise _fail(
                "CANDIDATE_REQUEST_INVALID",
                "request accepts only Candidate identity fields",
                400,
            )
        return self.validate(
            candidate_id=payload["candidate_id"],
            expected_candidate_revision=payload.get("expected_candidate_revision"),
            expected_content_hash=payload.get("expected_content_hash"),
        )

    def validate(
        self,
        *,
        candidate_id: str,
        expected_candidate_revision: int | None = None,
        expected_content_hash: str | None = None,
    ) -> dict[str, Any]:
        """Return a read-only validated envelope for an exact Candidate identity."""

        self._validate_identity(
            candidate_id, expected_candidate_revision, expected_content_hash
        )
        try:
            latest = self._store.get_candidate(candidate_id)
        except CandidateAuthorityError as exc:
            raise self._translate_store_error(exc) from exc
        if latest is None:
            raise _fail(
                "CANDIDATE_NOT_FOUND",
                "immutable Candidate revision was not found or is not committed",
                404,
            )
        candidate = latest
        if (
            expected_candidate_revision is not None
            and expected_candidate_revision != latest["revision"]
        ):
            try:
                requested = self._store.get_candidate(
                    candidate_id, expected_candidate_revision
                )
            except CandidateAuthorityError as exc:
                raise self._translate_store_error(exc) from exc
            if requested is None:
                raise _fail(
                    "CANDIDATE_STALE",
                    "expected Candidate revision does not exist",
                    409,
                )
            candidate = requested
        actual_revision = candidate["revision"]
        actual_hash = candidate["canonical_content_hash"]
        if candidate["candidate_id"] != candidate_id:
            raise _fail(
                "CANDIDATE_SCHEMA_INVALID",
                "persisted Candidate identity does not match storage identity",
                422,
            )
        if (
            expected_candidate_revision is not None
            and actual_revision != expected_candidate_revision
        ):
            raise _fail(
                "CANDIDATE_STALE", "Candidate revision does not match", 409
            )
        if expected_content_hash is not None and actual_hash != expected_content_hash:
            raise _fail(
                "CANDIDATE_HASH_MISMATCH",
                "expected content hash does not match persisted Candidate",
                409,
            )

        recomputed_hash = self._recompute_content_hash(candidate)
        if recomputed_hash != candidate["canonical_content_hash"]:
            raise _fail(
                "CANDIDATE_HASH_MISMATCH",
                "persisted Candidate content hash failed recomputation",
                409,
            )

        state = self._candidate_lifecycle_state(candidate_id, actual_revision)
        if state != "CURRENT":
            codes = {
                "STALE": "CANDIDATE_STALE",
                "INVALIDATED": "CANDIDATE_INVALIDATED",
                "SUPERSEDED": "CANDIDATE_SUPERSEDED",
                "REVOKED": "CANDIDATE_REVOKED",
            }
            raise _fail(
                codes.get(state, "CANDIDATE_STALE"),
                f"Candidate lifecycle state is {state}",
                409,
            )

        source = candidate["source"]
        try:
            review = self._store.get_human_review(source["review_session_id"])
        except CandidateAuthorityError as exc:
            raise self._translate_store_error(exc) from exc
        if review is None:
            raise _fail(
                "CANDIDATE_STALE",
                "authoritative HumanReview no longer exists",
                409,
            )
        self._validate_review_freshness(candidate, review)
        self._validate_consumption_semantics(candidate)
        self._validate_review_projection(candidate, review)
        return {
            "schema_version": CONSUMPTION_VALIDATION_SCHEMA_VERSION,
            "validation_status": "VALID_CURRENT",
            "candidate_id": candidate_id,
            "candidate_revision": actual_revision,
            "canonical_content_hash": actual_hash,
            "lifecycle_state": "CURRENT",
            "game": candidate["game"],
            "bets": deepcopy(candidate["active_bets"]),
            "cancelled_audit": deepcopy(candidate["cancelled_audit"]),
            "source": deepcopy(candidate["source"]),
            "authority": deepcopy(candidate["authority"]),
            "safety": {
                "candidate_only": True,
                "approved_for_fill": False,
                "approved_for_queue": False,
                "auto_confirm": False,
                "auto_submit": False,
            },
        }

    @staticmethod
    def _validate_identity(
        candidate_id: Any,
        expected_candidate_revision: Any | None,
        expected_content_hash: Any | None,
    ) -> None:
        if not isinstance(candidate_id, str) or not _CANDIDATE_ID_RE.fullmatch(
            candidate_id
        ):
            raise _fail(
                "CANDIDATE_SCHEMA_INVALID", "candidate_id is invalid", 422
            )
        if expected_candidate_revision is not None and (
            not isinstance(expected_candidate_revision, int)
            or isinstance(expected_candidate_revision, bool)
            or expected_candidate_revision < 1
        ):
            raise _fail(
                "CANDIDATE_SCHEMA_INVALID",
                "expected_candidate_revision must be a positive integer",
                422,
            )
        if expected_content_hash is not None and (
            not isinstance(expected_content_hash, str)
            or not _HASH_RE.fullmatch(expected_content_hash)
        ):
            raise _fail(
                "CANDIDATE_SCHEMA_INVALID",
                "expected_content_hash must be lowercase SHA-256",
                422,
            )

    @staticmethod
    def _translate_store_error(exc: CandidateAuthorityError) -> CandidateAuthorityError:
        message = exc.message.lower()
        if "content hash mismatch" in message or "candidate commit mismatch" in message:
            return _fail("CANDIDATE_HASH_MISMATCH", exc.message, 409)
        if exc.code == "MACHINE_VALUE_AUTHORITY_FORBIDDEN":
            return _fail("CANDIDATE_AUTHORITY_INVALID", exc.message, 422)
        if any(
            token in message
            for token in (
                "candidate authority", "candidate.authority", "machine evidence",
                "confirmation metadata",
                "all_active_confirmed", "blocking_unresolved_count",
            )
        ):
            return _fail("CANDIDATE_AUTHORITY_INVALID", exc.message, 422)
        if exc.code in {
            "COLUMN_STRUCTURE_INVALID", "BET_TYPE_GROUP_MISMATCH",
            "MULTIPLIER_INVALID", "SCOPE_UNRESOLVED",
            "CANCELLED_IN_ACTIVE_BETS", "NUMBER_OUT_OF_RANGE",
            "NUMBER_DUPLICATE",
        } or any(
            token in message
            for token in (
                "active bet", "cancelled bet", "cancelled_bet", "executable", "bet_type",
                "number_groups", "number group", "multiplier", "special play",
                "continuation", "no active bet",
            )
        ):
            return _fail("CANDIDATE_STRUCTURE_INVALID", exc.message, 422)
        return _fail("CANDIDATE_SCHEMA_INVALID", exc.message, 422)

    def _candidate_lifecycle_state(self, candidate_id: str, revision: int) -> str:
        try:
            events = self._store.get_lifecycle_events(candidate_id)
        except CandidateAuthorityError as exc:
            raise self._translate_store_error(exc) from exc
        state = "MISSING"
        for event in events:
            if event["candidate_revision"] != revision:
                continue
            if event["event_type"] == "CREATED":
                state = "CURRENT"
            else:
                state = event["event_type"]
        return state

    @staticmethod
    def _recompute_content_hash(candidate: Mapping[str, Any]) -> str:
        projection = {
            "schema_version": CANDIDATE_SCHEMA_VERSION,
            "game": candidate["game"],
            "source_image_hash": candidate["source"]["source_image_hash"],
            "active_bets": candidate["active_bets"],
            "cancelled_audit": candidate["cancelled_audit"],
            "safety": candidate["safety"],
        }
        return canonical_sha256(projection)

    @staticmethod
    def _validate_review_freshness(
        candidate: Mapping[str, Any], review: Mapping[str, Any]
    ) -> None:
        source = candidate["source"]
        exact = (
            review["review_session_id"] == source["review_session_id"]
            and review["human_answer_revision"]
            == source["human_answer_revision"]
            and review["human_answer_hash"] == source["human_answer_hash"]
            and review["source_image_id"] == source["source_image_id"]
            and review["source_image_hash"] == source["source_image_hash"]
        )
        if not exact:
            raise _fail(
                "CANDIDATE_STALE",
                "Candidate source no longer matches authoritative HumanReview",
                409,
            )

    @staticmethod
    def _validate_review_projection(
        candidate: Mapping[str, Any], review: Mapping[str, Any]
    ) -> None:
        """Prove Candidate values and provenance still derive from HumanReview."""

        review_bets = [
            {key: deepcopy(value) for key, value in bet.items() if key != "human_confirmed"}
            for bet in review["bets"]
        ]
        expected_active = [bet for bet in review_bets if bet["active"] is True]
        expected_cancelled = [bet for bet in review_bets if bet["cancelled"] is True]
        authority = candidate["authority"]
        exact = (
            candidate["game"] == review["game"]
            and candidate["active_bets"] == expected_active
            and candidate["cancelled_audit"] == expected_cancelled
            and authority["confirmed_by"] == review["confirmed_by"]
            and authority["confirmed_at"] == review["confirmed_at"]
            and authority["machine_evidence_refs"]
            == review["machine_evidence_refs"]
        )
        if not exact:
            raise _fail(
                "CANDIDATE_AUTHORITY_INVALID",
                "Candidate values or provenance do not match persisted HumanReview",
                422,
            )
        if (
            review["ready_for_candidate"] is not True
            or review["all_bets_confirmed"] is not True
            or review["blocking_unresolved_count"] != 0
        ):
            raise _fail(
                "CANDIDATE_STALE",
                "authoritative HumanReview is not confirmed and resolved",
                409,
            )

    @staticmethod
    def _validate_consumption_semantics(candidate: Mapping[str, Any]) -> None:
        if candidate["state_at_creation"] != "CURRENT":
            raise _fail(
                "CANDIDATE_SCHEMA_INVALID", "snapshot state is invalid", 422
            )
        authority = candidate["authority"]
        if (
            authority["value_authority"] != "human_answer"
            or authority["all_active_confirmed"] is not True
            or authority["blocking_unresolved_count"] != 0
        ):
            raise _fail(
                "CANDIDATE_AUTHORITY_INVALID",
                "Candidate does not have complete Human Answer authority",
                422,
            )
        if any(
            ref.get("value_authority") is not False
            for ref in authority["machine_evidence_refs"]
        ):
            raise _fail(
                "CANDIDATE_AUTHORITY_INVALID",
                "machine evidence cannot supply Candidate values",
                422,
            )

        active_ids: set[str] = set()
        for bet in candidate["active_bets"]:
            if (
                bet["cancelled"] is not False
                or bet["active"] is not True
                or bet["executable"] is not True
                or bet["value_authority"] != "human_answer"
            ):
                raise _fail(
                    "CANDIDATE_STRUCTURE_INVALID",
                    "active bet authority flags are invalid",
                    422,
                )
            if bet["human_bet_id"] in active_ids:
                raise _fail(
                    "CANDIDATE_STRUCTURE_INVALID",
                    "duplicate active human_bet_id",
                    422,
                )
            active_ids.add(bet["human_bet_id"])
            groups = bet["number_groups"]
            if (
                (bet["bet_type"] == "normal" and len(groups) != 1)
                or (bet["bet_type"] == "column" and len(groups) < 2)
                or not bet["multiplier"]["ordered_rules"]
                or bet["multiplier"]["resolved"] is not True
                or bet["special_play"]["resolved"] is not True
                or bet["continuation"]["resolved"] is not True
            ):
                raise _fail(
                    "CANDIDATE_STRUCTURE_INVALID",
                    "active bet structure is unresolved or invalid",
                    422,
                )

        cancelled_ids: set[str] = set()
        for bet in candidate["cancelled_audit"]:
            if (
                bet["cancelled"] is not True
                or bet["active"] is not False
                or bet["executable"] is not False
                or bet["value_authority"] != "human_answer"
            ):
                raise _fail(
                    "CANDIDATE_STRUCTURE_INVALID",
                    "cancelled bet entered an executable scope",
                    422,
                )
            human_bet_id = bet["human_bet_id"]
            if human_bet_id in active_ids or human_bet_id in cancelled_ids:
                raise _fail(
                    "CANDIDATE_STRUCTURE_INVALID",
                    "cancelled bet identity overlaps another Candidate bet",
                    422,
                )
            cancelled_ids.add(human_bet_id)

        expected_safety = {
            "candidate_only": True,
            "approved_for_fill": False,
            "queue_written": False,
            "auto_confirm": False,
            "auto_submit": False,
            "webfill_called": False,
        }
        if candidate["safety"] != expected_safety:
            raise _fail(
                "CANDIDATE_AUTHORITY_INVALID",
                "persisted Candidate safety flags are unsafe",
                422,
            )
