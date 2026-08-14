"""Server-owned HumanReview and immutable Candidate authority domain.

This module deliberately has no dependency on the Web UI, manual candidate
registry, queue, or webfill packages.  Browser summaries are never Candidate
authority: :meth:`VisionCandidateAuthorityStore.create_candidate` reloads the
persisted HumanReview snapshot and accepts only its identity/revision/hash.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import unicodedata
import uuid
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence


HUMAN_REVIEW_SCHEMA_VERSION = "vision-human-review-v1"
HUMAN_REVIEW_EVENT_SCHEMA_VERSION = "vision-human-review-event-v1"
CANDIDATE_SCHEMA_VERSION = "vision-candidate-authority-v1"
CANDIDATE_EVENT_SCHEMA_VERSION = "vision-candidate-lifecycle-event-v1"

_HASH_RE = re.compile(r"^[a-f0-9]{64}$")
_HUMAN_BET_ID_RE = re.compile(r"^H-[0-9]{3,}$")
_TWO_DIGIT_RE = re.compile(r"^[0-9]{2}$")
_MULTIPLIER_RE = re.compile(
    r"^[234](?:/[234]){0,2}X(?:0\.[0-9]+|[1-9][0-9]*(?:\.[0-9]+)?)$"
)
_SUPPORTED_GAMES = {"539": 39, "六合": 49}
_LIFECYCLE_EVENTS = {"CREATED", "STALE", "INVALIDATED", "SUPERSEDED", "REVOKED"}


class CandidateAuthorityError(Exception):
    """Stable, handler-friendly fail-closed domain error."""

    def __init__(self, code: str, message: str, http_status: int) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.http_status = http_status

    def to_dict(self) -> dict[str, Any]:
        return {"ok": False, "error": {"code": self.code, "message": self.message}}


def _error(code: str, message: str, status: int = 422) -> CandidateAuthorityError:
    return CandidateAuthorityError(code, message, status)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _require_exact_keys(value: Mapping[str, Any], expected: set[str], label: str) -> None:
    actual = set(value)
    if actual != expected:
        unknown = sorted(actual - expected)
        missing = sorted(expected - actual)
        raise _error(
            "CANDIDATE_SCHEMA_INVALID",
            f"{label} keys invalid; missing={missing}, unknown={unknown}",
        )


def _canonical_value(value: Any) -> Any:
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        raise _error("CANDIDATE_SCHEMA_INVALID", "floating-point values are forbidden")
    if isinstance(value, str):
        return unicodedata.normalize("NFC", value)
    if isinstance(value, list):
        return [_canonical_value(item) for item in value]
    if isinstance(value, dict):
        if any(not isinstance(key, str) for key in value):
            raise _error("CANDIDATE_SCHEMA_INVALID", "object keys must be strings")
        return {
            unicodedata.normalize("NFC", key): _canonical_value(item)
            for key, item in value.items()
        }
    raise _error(
        "CANDIDATE_SCHEMA_INVALID",
        f"unsupported JSON value type: {type(value).__name__}",
    )


def canonical_json_bytes(value: Any) -> bytes:
    canonical = _canonical_value(value)
    return json.dumps(
        canonical,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _json_copy(value: Any) -> Any:
    return json.loads(canonical_json_bytes(value).decode("utf-8"))


def _safe_name(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _write_atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(canonical_json_bytes(value))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp_name, path)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)


def _write_immutable_json(path: Path, value: Mapping[str, Any]) -> None:
    """Publish a complete file without replacing an existing immutable file."""

    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(canonical_json_bytes(value))
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(tmp_name, path)
        except FileExistsError as exc:
            raise _error("IMMUTABLE_RECORD_EXISTS", f"immutable record already exists: {path.name}", 409) from exc
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise _error("AUTHORITY_STORE_CORRUPT", f"cannot read {path.name}: {exc}", 500) from exc
    if not isinstance(value, dict):
        raise _error("AUTHORITY_STORE_CORRUPT", f"{path.name} root must be an object", 500)
    return value


class VisionCandidateAuthorityStore:
    """Filesystem-backed authority store with CAS and immutable records."""

    def __init__(self, base_dir: Path | str) -> None:
        self.base_dir = Path(base_dir)
        self._reviews_dir = self.base_dir / "human-reviews"
        self._review_events_dir = self.base_dir / "human-review-events"
        self._candidates_dir = self.base_dir / "candidates"
        self._candidate_events_dir = self.base_dir / "candidate-events"
        self._candidate_commits_dir = self.base_dir / "candidate-commits"
        self._review_candidate_dir = self.base_dir / "review-candidate"
        self._idempotency_dir = self.base_dir / "idempotency"
        self._locks_dir = self.base_dir / "locks"
        for directory in (
            self._reviews_dir,
            self._review_events_dir,
            self._candidates_dir,
            self._candidate_events_dir,
            self._candidate_commits_dir,
            self._review_candidate_dir,
            self._idempotency_dir,
            self._locks_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # HumanReview persistence
    # ------------------------------------------------------------------

    def create_human_review(
        self,
        *,
        review_session_id: str,
        source_image_id: str,
        source_image_hash: str,
        game: str,
        bets: list[dict[str, Any]],
        machine_evidence_refs: list[dict[str, Any]],
        blocking_unresolved_count: int,
        actor: str,
    ) -> dict[str, Any]:
        self._validate_review_identity(review_session_id, source_image_id, source_image_hash, game, actor)
        with self._lock(f"review:{review_session_id}"):
            path = self._review_path(review_session_id)
            if path.exists():
                raise _error("REVIEW_ALREADY_EXISTS", "HumanReview already exists", 409)
            normalized_bets = self._validate_review_bets(bets, game)
            # Initial persistence never trusts a browser's confirmation claim.
            # Confirmation is a separate, explicit CAS transition below.
            normalized_bets = [dict(bet, human_confirmed=False) for bet in normalized_bets]
            refs = self._validate_machine_evidence_refs(machine_evidence_refs)
            blockers = self._validated_derived_blocker_count(
                blocking_unresolved_count, normalized_bets
            )
            timestamp = _now()
            review = self._build_review_snapshot(
                review_session_id=review_session_id,
                human_answer_revision=1,
                source_image_id=source_image_id,
                source_image_hash=source_image_hash,
                game=game,
                bets=normalized_bets,
                machine_evidence_refs=refs,
                blocking_unresolved_count=blockers,
                actor=actor,
                created_at=timestamp,
                updated_at=timestamp,
            )
            _write_atomic_json(path, review)
            self._append_review_event(review, "CREATED", actor)
            return deepcopy(review)

    def get_human_review(self, review_session_id: str) -> dict[str, Any] | None:
        path = self._review_path(review_session_id)
        if not path.exists():
            return None
        review = _read_json(path)
        self._validate_stored_review(review)
        return deepcopy(review)

    def replace_human_review(
        self,
        *,
        review_session_id: str,
        expected_revision: int,
        expected_human_answer_hash: str,
        bets: list[dict[str, Any]],
        machine_evidence_refs: list[dict[str, Any]] | None,
        blocking_unresolved_count: int,
        actor: str,
    ) -> dict[str, Any]:
        self._require_nonempty(actor, "actor")
        self._require_hash(expected_human_answer_hash, "expected_human_answer_hash")
        if not isinstance(expected_revision, int) or isinstance(expected_revision, bool) or expected_revision < 1:
            raise _error("CANDIDATE_SCHEMA_INVALID", "expected_revision must be a positive integer")
        with self._lock(f"review:{review_session_id}"):
            current = self.get_human_review(review_session_id)
            if current is None:
                raise _error("REVIEW_NOT_FOUND", "HumanReview not found", 404)
            if (
                current["human_answer_revision"] != expected_revision
                or current["human_answer_hash"] != expected_human_answer_hash
            ):
                raise _error("REVIEW_STALE", "HumanReview revision/hash mismatch", 409)
            normalized_bets = self._validate_review_bets(bets, current["game"])
            current_by_id = {bet["human_bet_id"]: bet for bet in current["bets"]}
            current_ids = [bet["human_bet_id"] for bet in current["bets"]]
            next_ids = [bet["human_bet_id"] for bet in normalized_bets]
            removed_ids = set(current_ids) - set(next_ids)
            reordered_existing = (
                not removed_ids
                and set(current_ids) == set(next_ids)
                and current_ids != next_ids
            )
            invalidate_remaining = bool(removed_ids) or reordered_existing
            reconciled_bets: list[dict[str, Any]] = []
            for bet in normalized_bets:
                previous = current_by_id.get(bet["human_bet_id"])
                unchanged = (
                    previous is not None
                    and self._review_bet_values(previous) == self._review_bet_values(bet)
                )
                reconciled_bets.append(
                    dict(
                        bet,
                        human_confirmed=(
                            previous["human_confirmed"] is True
                            if unchanged and not invalidate_remaining
                            else False
                        ),
                    )
                )
            normalized_bets = reconciled_bets
            refs = (
                deepcopy(current["machine_evidence_refs"])
                if machine_evidence_refs is None
                else self._validate_machine_evidence_refs(machine_evidence_refs)
            )
            blockers = self._validated_derived_blocker_count(
                blocking_unresolved_count, normalized_bets
            )
            all_confirmed = bool(normalized_bets) and all(
                bet["human_confirmed"] is True for bet in normalized_bets
            )
            updated = self._build_review_snapshot(
                review_session_id=review_session_id,
                human_answer_revision=expected_revision + 1,
                source_image_id=current["source_image_id"],
                source_image_hash=current["source_image_hash"],
                game=current["game"],
                bets=normalized_bets,
                machine_evidence_refs=refs,
                blocking_unresolved_count=blockers,
                actor=actor,
                created_at=current["created_at"],
                updated_at=_now(),
                confirmed_by=current["confirmed_by"] if all_confirmed else None,
                confirmed_at=current["confirmed_at"] if all_confirmed else None,
            )
            _write_atomic_json(self._review_path(review_session_id), updated)
            self._append_review_event(updated, "REPLACED", actor)
            self._mark_current_candidate_stale(review_session_id, actor)
            return deepcopy(updated)

    def confirm_human_review_bets(
        self,
        *,
        review_session_id: str,
        expected_human_answer_revision: int,
        expected_human_answer_hash: str,
        human_bet_ids: list[str],
        actor: str,
    ) -> dict[str, Any]:
        """Explicitly confirm valid values already persisted on the server."""

        return self._set_human_review_confirmation(
            review_session_id=review_session_id,
            expected_human_answer_revision=expected_human_answer_revision,
            expected_human_answer_hash=expected_human_answer_hash,
            human_bet_ids=human_bet_ids,
            confirmed=True,
            actor=actor,
        )

    def unconfirm_human_review_bets(
        self,
        *,
        review_session_id: str,
        expected_human_answer_revision: int,
        expected_human_answer_hash: str,
        human_bet_ids: list[str],
        actor: str,
    ) -> dict[str, Any]:
        """Explicitly revoke confirmation without accepting replacement values."""

        return self._set_human_review_confirmation(
            review_session_id=review_session_id,
            expected_human_answer_revision=expected_human_answer_revision,
            expected_human_answer_hash=expected_human_answer_hash,
            human_bet_ids=human_bet_ids,
            confirmed=False,
            actor=actor,
        )

    def _set_human_review_confirmation(
        self,
        *,
        review_session_id: str,
        expected_human_answer_revision: int,
        expected_human_answer_hash: str,
        human_bet_ids: list[str],
        confirmed: bool,
        actor: str,
    ) -> dict[str, Any]:
        self._require_nonempty(review_session_id, "review_session_id")
        self._require_nonempty(actor, "actor")
        self._require_hash(expected_human_answer_hash, "expected_human_answer_hash")
        if (
            not isinstance(expected_human_answer_revision, int)
            or isinstance(expected_human_answer_revision, bool)
            or expected_human_answer_revision < 1
        ):
            raise _error(
                "CANDIDATE_SCHEMA_INVALID",
                "expected Human Answer revision must be positive",
            )
        if (
            not isinstance(human_bet_ids, list)
            or not human_bet_ids
            or any(
                not isinstance(item, str) or not _HUMAN_BET_ID_RE.fullmatch(item)
                for item in human_bet_ids
            )
            or len(human_bet_ids) != len(set(human_bet_ids))
        ):
            raise _error(
                "CANDIDATE_SCHEMA_INVALID",
                "human_bet_ids must be a non-empty unique ID list",
            )

        with self._lock(f"review:{review_session_id}"):
            current = self.get_human_review(review_session_id)
            if current is None:
                raise _error("REVIEW_NOT_FOUND", "HumanReview not found", 404)
            if (
                current["human_answer_revision"] != expected_human_answer_revision
                or current["human_answer_hash"] != expected_human_answer_hash
            ):
                raise _error("REVIEW_STALE", "HumanReview revision/hash mismatch", 409)
            wanted = set(human_bet_ids)
            known = {bet["human_bet_id"] for bet in current["bets"]}
            unknown = sorted(wanted - known)
            if unknown:
                raise _error(
                    "HUMAN_BET_NOT_FOUND", f"unknown human_bet_ids: {unknown}", 404
                )

            bets = deepcopy(current["bets"])
            changed = False
            for index, bet in enumerate(bets):
                if bet["human_bet_id"] not in wanted:
                    continue
                before = deepcopy(bet)
                if confirmed:
                    bet = self._resolve_bet_by_explicit_confirmation(
                        bet, current["game"]
                    )
                    bets[index] = bet
                if bet["human_confirmed"] is not confirmed:
                    bet["human_confirmed"] = confirmed
                if bet != before:
                    changed = True
            if not changed:
                return deepcopy(current)

            timestamp = _now()
            all_confirmed = bool(bets) and all(
                bet["human_confirmed"] is True for bet in bets
            )
            updated = self._build_review_snapshot(
                review_session_id=review_session_id,
                human_answer_revision=expected_human_answer_revision + 1,
                source_image_id=current["source_image_id"],
                source_image_hash=current["source_image_hash"],
                game=current["game"],
                bets=bets,
                machine_evidence_refs=current["machine_evidence_refs"],
                blocking_unresolved_count=self._derive_blocking_unresolved_count(bets),
                actor=actor,
                created_at=current["created_at"],
                updated_at=timestamp,
                confirmed_by=actor if all_confirmed else None,
                confirmed_at=timestamp if all_confirmed else None,
            )
            _write_atomic_json(self._review_path(review_session_id), updated)
            self._append_review_event(
                updated, "CONFIRMED" if confirmed else "UNCONFIRMED", actor
            )
            self._mark_current_candidate_stale(review_session_id, actor)
            return deepcopy(updated)

    # ------------------------------------------------------------------
    # Candidate creation / replay
    # ------------------------------------------------------------------

    def create_candidate(
        self,
        *,
        review_session_id: str,
        expected_human_answer_revision: int,
        expected_human_answer_hash: str,
        idempotency_key: str,
        actor: str,
    ) -> dict[str, Any]:
        self._require_nonempty(review_session_id, "review_session_id")
        self._require_nonempty(actor, "actor")
        self._require_hash(expected_human_answer_hash, "expected_human_answer_hash")
        if (
            not isinstance(expected_human_answer_revision, int)
            or isinstance(expected_human_answer_revision, bool)
            or expected_human_answer_revision < 1
        ):
            raise _error("CANDIDATE_SCHEMA_INVALID", "expected Human Answer revision must be positive")
        expected_key = (
            f"{review_session_id}:{expected_human_answer_revision}:"
            f"{expected_human_answer_hash}"
        )
        if idempotency_key != expected_key:
            raise _error("IDEMPOTENCY_CONFLICT", "idempotency key does not match review identity", 409)

        request_identity = {
            "review_session_id": review_session_id,
            "human_answer_revision": expected_human_answer_revision,
            "human_answer_hash": expected_human_answer_hash,
        }
        with self._lock(f"review:{review_session_id}"):
            # Idempotency is scoped to the *current* authoritative HumanReview.
            # Once that review changes, the old Candidate is STALE and an old
            # tuple must not replay it as if it were current.
            review = self.get_human_review(review_session_id)
            if review is None:
                raise _error("REVIEW_NOT_FOUND", "HumanReview not found", 404)
            if (
                review["human_answer_revision"] != expected_human_answer_revision
                or review["human_answer_hash"] != expected_human_answer_hash
            ):
                raise _error("REVIEW_STALE", "HumanReview revision/hash mismatch", 409)

            replay = self._load_idempotency(idempotency_key)
            if replay is not None:
                if replay["request_identity"] != request_identity:
                    raise _error("IDEMPOTENCY_CONFLICT", "idempotency key was used for another request", 409)
                candidate = self.get_candidate(replay["candidate_id"], replay["candidate_revision"])
                if candidate is None:
                    self._commit_candidate_if_complete(
                        replay["candidate_id"],
                        replay["candidate_revision"],
                        idempotency_key,
                    )
                    candidate = self.get_candidate(
                        replay["candidate_id"], replay["candidate_revision"]
                    )
                if candidate is None:
                    raise _error(
                        "AUTHORITY_STORE_CORRUPT",
                        "idempotency record points to incomplete Candidate transaction",
                        500,
                    )
                if self._derived_candidate_state(
                    replay["candidate_id"], replay["candidate_revision"]
                ) != "CURRENT":
                    raise _error(
                        "REVIEW_STALE",
                        "idempotent Candidate is no longer CURRENT",
                        409,
                    )
                return {"candidate": candidate, "replayed": True}

            self._require_review_ready(review)

            candidate_id = self._candidate_id_for_review(review_session_id)
            latest = self._latest_candidate(candidate_id) if candidate_id else None
            if candidate_id is None:
                candidate_id = f"vc-{uuid.uuid4().hex}"
                self._bind_review_candidate(review_session_id, candidate_id)

            candidate = self._find_resumable_candidate(candidate_id, review)
            if candidate is None:
                candidate_revision = self._next_candidate_revision(candidate_id)
                previous_hash = latest["canonical_content_hash"] if latest else None
                candidate = self._build_candidate(
                    review,
                    candidate_id=candidate_id,
                    revision=candidate_revision,
                    previous_revision_hash=previous_hash,
                    actor=actor,
                )
                self._validate_candidate_snapshot(candidate)
                _write_immutable_json(
                    self._candidate_path(candidate_id, candidate_revision), candidate
                )
            else:
                candidate_revision = int(candidate["revision"])

            if latest is not None:
                self._ensure_candidate_event(
                    candidate_id,
                    int(latest["revision"]),
                    latest["canonical_content_hash"],
                    "SUPERSEDED",
                    actor,
                    reason_code="fresh_human_answer_revision",
                    superseded_by_revision=candidate_revision,
                )
            self._ensure_candidate_event(
                candidate_id,
                candidate_revision,
                candidate["canonical_content_hash"],
                "CREATED",
                actor,
                reason_code=None,
                superseded_by_revision=None,
            )
            self._write_idempotency(
                idempotency_key,
                request_identity,
                candidate_id,
                candidate_revision,
            )
            self._commit_candidate_if_complete(
                candidate_id, candidate_revision, idempotency_key
            )
            return {"candidate": deepcopy(candidate), "replayed": False}

    def get_candidate(self, candidate_id: str, revision: int | None = None) -> dict[str, Any] | None:
        self._require_candidate_id(candidate_id)
        if revision is None:
            latest = self._latest_candidate(candidate_id)
            return deepcopy(latest) if latest is not None else None
        self._require_candidate_revision(revision)
        if not self._candidate_commit_path(candidate_id, revision).exists():
            return None
        path = self._candidate_path(candidate_id, revision)
        if not path.exists():
            return None
        candidate = _read_json(path)
        self._validate_candidate_snapshot(candidate)
        self._validate_candidate_commit(candidate_id, revision, candidate)
        return deepcopy(candidate)

    def get_candidate_for_review(self, review_session_id: str) -> dict[str, Any] | None:
        candidate_id = self._candidate_id_for_review(review_session_id)
        if candidate_id is None:
            return None
        candidate = self.get_candidate(candidate_id)
        if candidate is None:
            # A mapping may precede the final transaction commit marker.  Such
            # an interrupted write is recoverable and must not expose a
            # partially published Candidate.
            return None
        state = self._derived_candidate_state(candidate_id, int(candidate["revision"]))
        current_review = self.get_human_review(review_session_id)
        if current_review is None:
            raise _error("AUTHORITY_STORE_CORRUPT", "Candidate review is missing", 500)
        source = candidate["source"]
        if (
            source["human_answer_revision"] != current_review["human_answer_revision"]
            or source["human_answer_hash"] != current_review["human_answer_hash"]
        ):
            # This identity comparison is the fail-closed backstop if a process
            # stops after the atomic HumanReview replace but before its STALE
            # lifecycle event is durably appended.
            state = "STALE"
        return {"candidate": candidate, "state": state}

    def get_lifecycle_events(self, candidate_id: str) -> list[dict[str, Any]]:
        self._require_candidate_id(candidate_id)
        directory = self._candidate_events_dir / candidate_id
        if not directory.exists():
            return []
        events = [_read_json(path) for path in sorted(directory.glob("*.json"))]
        for expected, event in enumerate(events, start=1):
            self._validate_candidate_event(event, candidate_id)
            if event.get("event_sequence") != expected:
                raise _error("AUTHORITY_STORE_CORRUPT", "candidate event sequence gap", 500)
        return deepcopy(events)

    def _validate_candidate_event(
        self, event: Mapping[str, Any], candidate_id: str
    ) -> None:
        expected = {
            "schema_version", "event_id", "candidate_id", "candidate_revision",
            "event_sequence", "event_type", "candidate_content_hash", "reason_code",
            "occurred_at", "actor", "superseded_by_revision",
        }
        _require_exact_keys(event, expected, "Candidate lifecycle event")
        if event["schema_version"] != CANDIDATE_EVENT_SCHEMA_VERSION:
            raise _error("AUTHORITY_STORE_CORRUPT", "lifecycle schema mismatch", 500)
        if event["candidate_id"] != candidate_id:
            raise _error("AUTHORITY_STORE_CORRUPT", "lifecycle candidate mismatch", 500)
        if not isinstance(event["event_id"], str) or not re.fullmatch(
            r"vce-[a-f0-9]{32}", event["event_id"]
        ):
            raise _error("AUTHORITY_STORE_CORRUPT", "lifecycle event_id invalid", 500)
        self._require_candidate_revision(event["candidate_revision"])
        self._require_candidate_revision(event["event_sequence"])
        if event["event_type"] not in _LIFECYCLE_EVENTS:
            raise _error("AUTHORITY_STORE_CORRUPT", "lifecycle event_type invalid", 500)
        self._require_hash(event["candidate_content_hash"], "candidate_content_hash")
        self._require_nonempty(event["occurred_at"], "occurred_at")
        self._require_nonempty(event["actor"], "actor")
        if event["reason_code"] is not None and not isinstance(event["reason_code"], str):
            raise _error("AUTHORITY_STORE_CORRUPT", "reason_code invalid", 500)
        superseded = event["superseded_by_revision"]
        if event["event_type"] == "SUPERSEDED":
            self._require_candidate_revision(superseded)
            if superseded <= event["candidate_revision"]:
                raise _error("AUTHORITY_STORE_CORRUPT", "superseded revision invalid", 500)
        elif superseded is not None:
            raise _error("AUTHORITY_STORE_CORRUPT", "unexpected superseded revision", 500)

    def append_lifecycle_event(
        self,
        *,
        candidate_id: str,
        revision: int,
        event_type: str,
        reason_code: str | None,
        actor: str,
    ) -> dict[str, Any]:
        if event_type not in {"STALE", "INVALIDATED", "REVOKED"}:
            raise _error("LIFECYCLE_EVENT_INVALID", "public lifecycle event is not allowed", 422)
        with self._lock(f"candidate:{candidate_id}"):
            candidate = self.get_candidate(candidate_id, revision)
            if candidate is None:
                raise _error("CANDIDATE_NOT_FOUND", "Candidate revision not found", 404)
            state = self._derived_candidate_state(candidate_id, revision)
            allowed = {
                "STALE": {"CURRENT"},
                "INVALIDATED": {"CURRENT", "STALE"},
                "REVOKED": {"CURRENT", "STALE", "INVALIDATED"},
            }
            if state not in allowed[event_type]:
                raise _error("LIFECYCLE_CONFLICT", f"cannot append {event_type} from {state}", 409)
            return self._append_candidate_event(
                candidate_id,
                revision,
                candidate["canonical_content_hash"],
                event_type,
                actor,
                reason_code=reason_code,
                superseded_by_revision=None,
            )

    # ------------------------------------------------------------------
    # Validation and construction
    # ------------------------------------------------------------------

    def _build_review_snapshot(
        self,
        *,
        review_session_id: str,
        human_answer_revision: int,
        source_image_id: str,
        source_image_hash: str,
        game: str,
        bets: list[dict[str, Any]],
        machine_evidence_refs: list[dict[str, Any]],
        blocking_unresolved_count: int,
        actor: str,
        created_at: str,
        updated_at: str,
        confirmed_by: str | None = None,
        confirmed_at: str | None = None,
    ) -> dict[str, Any]:
        projection = {
            "game": game,
            "source_image_hash": source_image_hash,
            "bets": bets,
        }
        all_confirmed = bool(bets) and all(bet["human_confirmed"] is True for bet in bets)
        ready = all_confirmed and self._review_values_are_candidate_ready(
            bets, game, blocking_unresolved_count
        )
        return {
            "schema_version": HUMAN_REVIEW_SCHEMA_VERSION,
            "review_session_id": review_session_id,
            "human_answer_revision": human_answer_revision,
            "human_answer_hash": canonical_sha256(projection),
            "source_image_id": source_image_id,
            "source_image_hash": source_image_hash,
            "game": game,
            "bets": deepcopy(bets),
            "machine_evidence_refs": deepcopy(machine_evidence_refs),
            "blocking_unresolved_count": blocking_unresolved_count,
            "all_bets_confirmed": all_confirmed,
            "ready_for_candidate": ready,
            "confirmed_by": confirmed_by if all_confirmed else None,
            "confirmed_at": confirmed_at if all_confirmed else None,
            "created_at": created_at,
            "updated_at": updated_at,
        }

    @staticmethod
    def _review_bet_values(bet: Mapping[str, Any]) -> dict[str, Any]:
        return {
            key: deepcopy(value)
            for key, value in bet.items()
            if key != "human_confirmed"
        }

    def _review_values_are_candidate_ready(
        self,
        bets: list[dict[str, Any]],
        game: str,
        blocking_unresolved_count: int,
    ) -> bool:
        if blocking_unresolved_count != 0 or not any(bet["active"] for bet in bets):
            return False
        try:
            for bet in bets:
                self._validate_candidate_bet(bet, game)
        except CandidateAuthorityError:
            return False
        return True

    def _build_candidate(
        self,
        review: dict[str, Any],
        *,
        candidate_id: str,
        revision: int,
        previous_revision_hash: str | None,
        actor: str,
    ) -> dict[str, Any]:
        active_bets = [self._candidate_bet(bet) for bet in review["bets"] if bet["active"]]
        cancelled = [self._candidate_bet(bet) for bet in review["bets"] if bet["cancelled"]]
        safety = {
            "candidate_only": True,
            "approved_for_fill": False,
            "queue_written": False,
            "auto_confirm": False,
            "auto_submit": False,
            "webfill_called": False,
        }
        content_projection = {
            "schema_version": CANDIDATE_SCHEMA_VERSION,
            "game": review["game"],
            "source_image_hash": review["source_image_hash"],
            "active_bets": active_bets,
            "cancelled_audit": cancelled,
            "safety": safety,
        }
        created_at = _now()
        return {
            "schema_version": CANDIDATE_SCHEMA_VERSION,
            "candidate_id": candidate_id,
            "revision": revision,
            "state_at_creation": "CURRENT",
            "game": review["game"],
            "previous_revision_hash": previous_revision_hash,
            "source": {
                "review_session_id": review["review_session_id"],
                "human_answer_revision": review["human_answer_revision"],
                "human_answer_hash": review["human_answer_hash"],
                "source_image_id": review["source_image_id"],
                "source_image_hash": review["source_image_hash"],
            },
            "authority": {
                "value_authority": "human_answer",
                "confirmed_by": review["confirmed_by"],
                "confirmed_at": review["confirmed_at"],
                "all_active_confirmed": True,
                "blocking_unresolved_count": 0,
                "machine_evidence_refs": deepcopy(review["machine_evidence_refs"]),
            },
            "active_bets": active_bets,
            "cancelled_audit": cancelled,
            "canonical_content_hash": canonical_sha256(content_projection),
            "created_at": created_at,
            "safety": safety,
        }

    @staticmethod
    def _candidate_bet(review_bet: dict[str, Any]) -> dict[str, Any]:
        return {key: deepcopy(value) for key, value in review_bet.items() if key != "human_confirmed"}

    def _require_review_ready(self, review: dict[str, Any]) -> None:
        if not review.get("ready_for_candidate"):
            raise _error("CANDIDATE_NOT_READY", "HumanReview is not fully confirmed and resolved", 422)
        if review.get("blocking_unresolved_count") != 0:
            raise _error("CANDIDATE_NOT_READY", "HumanReview has unresolved blockers", 422)
        active = [bet for bet in review["bets"] if bet["active"]]
        if not active:
            raise _error("CANDIDATE_NOT_READY", "HumanReview has no active bet", 422)
        for bet in review["bets"]:
            if bet["human_confirmed"] is not True:
                raise _error("CANDIDATE_NOT_READY", f"{bet['human_bet_id']} is not confirmed", 422)
            self._validate_candidate_bet(bet, review["game"])

    def _validate_review_identity(
        self,
        review_session_id: str,
        source_image_id: str,
        source_image_hash: str,
        game: str,
        actor: str,
    ) -> None:
        self._require_nonempty(review_session_id, "review_session_id")
        self._require_nonempty(source_image_id, "source_image_id")
        self._require_hash(source_image_hash, "source_image_hash")
        self._require_game(game)
        self._require_nonempty(actor, "actor")

    @staticmethod
    def _require_nonempty(value: Any, label: str) -> None:
        if not isinstance(value, str) or not value.strip():
            raise _error("CANDIDATE_SCHEMA_INVALID", f"{label} must be a non-empty string")

    @staticmethod
    def _require_hash(value: Any, label: str) -> None:
        if not isinstance(value, str) or not _HASH_RE.fullmatch(value):
            raise _error("CANDIDATE_SCHEMA_INVALID", f"{label} must be lowercase SHA-256")

    @staticmethod
    def _require_game(game: Any) -> int:
        if game not in _SUPPORTED_GAMES:
            raise _error("GAME_INVALID", "unsupported or missing game", 422)
        return _SUPPORTED_GAMES[game]

    @staticmethod
    def _validate_blocker_count(value: Any) -> int:
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise _error("CANDIDATE_SCHEMA_INVALID", "blocking_unresolved_count must be a non-negative integer")
        return value

    def _derive_blocking_unresolved_count(self, bets: Sequence[Mapping[str, Any]]) -> int:
        """Count active Human Answer records that are not executable yet."""

        return sum(
            1
            for bet in bets
            if bet.get("active") is True and bet.get("executable") is not True
        )

    def _validated_derived_blocker_count(
        self, supplied: Any, bets: Sequence[Mapping[str, Any]]
    ) -> int:
        supplied_count = self._validate_blocker_count(supplied)
        derived = self._derive_blocking_unresolved_count(bets)
        if supplied_count != derived:
            raise _error(
                "BLOCKING_UNRESOLVED_MISMATCH",
                f"blocking_unresolved_count must equal server-derived value {derived}",
            )
        return derived

    def _validate_review_bets(self, bets: Any, game: str) -> list[dict[str, Any]]:
        if not isinstance(bets, list):
            raise _error("CANDIDATE_SCHEMA_INVALID", "bets must be a list")
        result: list[dict[str, Any]] = []
        seen: set[str] = set()
        for index, bet in enumerate(bets):
            normalized = self._validate_review_bet(bet, game, f"bets[{index}]")
            human_bet_id = normalized["human_bet_id"]
            if human_bet_id in seen:
                raise _error("CANDIDATE_SCHEMA_INVALID", f"duplicate human_bet_id: {human_bet_id}")
            seen.add(human_bet_id)
            result.append(normalized)
        return result

    def _validate_review_bet(self, bet: Any, game: str, label: str) -> dict[str, Any]:
        if not isinstance(bet, dict):
            raise _error("CANDIDATE_SCHEMA_INVALID", f"{label} must be an object")
        public_keys = {
            "human_bet_id", "bet_type", "number_groups", "multiplier",
            "special_play", "continuation", "cancelled", "active", "executable",
            "value_authority", "human_confirmed",
        }
        public_keys -= {"executable", "value_authority"}
        stored_keys = public_keys | {"executable", "value_authority"}
        actual_keys = frozenset(bet)
        if actual_keys not in {frozenset(public_keys), frozenset(stored_keys)}:
            _require_exact_keys(bet, public_keys, label)
        supplied_executable = bet.get("executable") if actual_keys == frozenset(stored_keys) else None
        supplied_authority = bet.get("value_authority") if actual_keys == frozenset(stored_keys) else None
        if not isinstance(bet["human_bet_id"], str) or not _HUMAN_BET_ID_RE.fullmatch(bet["human_bet_id"]):
            raise _error("CANDIDATE_SCHEMA_INVALID", f"{label}.human_bet_id invalid")
        self._require_nonempty(bet["bet_type"], f"{label}.bet_type")
        self._validate_number_groups(bet["number_groups"], game, allow_empty=True)
        self._validate_multiplier(bet["multiplier"], active=False)
        self._validate_special_play(bet["special_play"], active=False)
        self._validate_continuation(bet["continuation"], active=False)
        for key in ("cancelled", "active", "human_confirmed"):
            if not isinstance(bet[key], bool):
                raise _error("CANDIDATE_SCHEMA_INVALID", f"{label}.{key} must be boolean")
        if bet["cancelled"] == bet["active"]:
            raise _error("CANDIDATE_SCHEMA_INVALID", f"{label} must be exactly active or cancelled")
        normalized = _json_copy({key: bet[key] for key in public_keys})
        normalized["value_authority"] = "human_answer"
        normalized["executable"] = False
        if normalized["active"]:
            candidate_probe = dict(normalized, executable=True)
            try:
                self._validate_candidate_bet(candidate_probe, game)
            except CandidateAuthorityError:
                pass
            else:
                normalized["executable"] = True
        if supplied_authority is not None and supplied_authority != "human_answer":
            raise _error(
                "MACHINE_VALUE_AUTHORITY_FORBIDDEN",
                f"{label} value authority must be Human Answer",
            )
        if supplied_executable is not None and supplied_executable is not normalized["executable"]:
            raise _error(
                "AUTHORITY_STORE_CORRUPT",
                f"{label}.executable disagrees with server-derived value",
                500,
            )
        return normalized

    def _resolve_bet_by_explicit_confirmation(
        self, bet: Mapping[str, Any], game: str
    ) -> dict[str, Any]:
        """Resolve scopes only through an explicit human confirmation action."""

        resolved = deepcopy(dict(bet))
        # These are server-derived fields. Recompute them after the explicit
        # scope transition instead of treating the previous derived values as
        # client input.
        resolved.pop("executable", None)
        resolved.pop("value_authority", None)
        if resolved["active"]:
            multiplier = deepcopy(resolved["multiplier"])
            special_play = deepcopy(resolved["special_play"])
            continuation = deepcopy(resolved["continuation"])
            # No value is invented: this transition only records the human's
            # explicit decision that the already persisted scopes are final.
            multiplier["resolved"] = True
            special_play["resolved"] = True
            continuation["resolved"] = True
            resolved["multiplier"] = multiplier
            resolved["special_play"] = special_play
            resolved["continuation"] = continuation
        resolved = self._validate_review_bet(resolved, game, "confirmed_bet")
        self._validate_candidate_bet(resolved, game)
        return resolved

    def _validate_candidate_bet(self, bet: dict[str, Any], game: str) -> None:
        if bet["cancelled"]:
            if bet["active"] or bet["executable"]:
                raise _error("CANCELLED_IN_ACTIVE_BETS", "cancelled bet is active or executable")
            return
        if not bet["active"] or not bet["executable"]:
            raise _error("CANDIDATE_NOT_READY", f"{bet['human_bet_id']} is not executable")
        if bet["bet_type"] not in {"normal", "column"}:
            raise _error("BET_TYPE_GROUP_MISMATCH", f"unsupported active bet_type: {bet['bet_type']}")
        groups = self._validate_number_groups(bet["number_groups"], game, allow_empty=False)
        if bet["bet_type"] == "normal" and len(groups) != 1:
            raise _error("BET_TYPE_GROUP_MISMATCH", "normal bet must contain exactly one number group")
        if bet["bet_type"] == "column" and len(groups) < 2:
            raise _error("COLUMN_STRUCTURE_INVALID", "column bet must contain at least two groups")
        self._validate_multiplier(bet["multiplier"], active=True)
        self._validate_special_play(bet["special_play"], active=True)
        self._validate_continuation(bet["continuation"], active=True)

    def _validate_number_groups(self, value: Any, game: str, *, allow_empty: bool) -> list[list[str]]:
        max_number = self._require_game(game)
        if not isinstance(value, list):
            raise _error("CANDIDATE_SCHEMA_INVALID", "number_groups must be a list")
        if not allow_empty and not value:
            raise _error("CANDIDATE_NOT_READY", "number_groups is empty")
        all_numbers: set[str] = set()
        for group in value:
            if not isinstance(group, list) or not group:
                raise _error("COLUMN_STRUCTURE_INVALID", "number group must be a non-empty list")
            local: set[str] = set()
            for number in group:
                if not isinstance(number, str) or not _TWO_DIGIT_RE.fullmatch(number):
                    raise _error("CANDIDATE_SCHEMA_INVALID", "numbers must be two-digit strings")
                numeric = int(number)
                if numeric < 1 or numeric > max_number:
                    raise _error("NUMBER_OUT_OF_RANGE", f"number {number} is invalid for {game}")
                if number in local or number in all_numbers:
                    raise _error("NUMBER_DUPLICATE", f"duplicate number {number}")
                local.add(number)
                all_numbers.add(number)
        return value

    def _validate_multiplier(self, value: Any, *, active: bool) -> None:
        if not isinstance(value, dict):
            raise _error("CANDIDATE_SCHEMA_INVALID", "multiplier must be an object")
        _require_exact_keys(value, {"ordered_rules", "scope", "resolved"}, "multiplier")
        rules = value["ordered_rules"]
        if not isinstance(rules, list) or any(not isinstance(rule, str) for rule in rules):
            raise _error("MULTIPLIER_INVALID", "ordered_rules must be a string list")
        if len(rules) != len(set(rules)):
            raise _error("MULTIPLIER_INVALID", "duplicate multiplier rules")
        if active and not rules:
            raise _error("MULTIPLIER_INVALID", "active bet requires a multiplier rule")
        if any(not _MULTIPLIER_RE.fullmatch(rule) for rule in rules):
            raise _error("MULTIPLIER_INVALID", "multiplier rule is incomplete or invalid")
        if value["scope"] is not None and not isinstance(value["scope"], str):
            raise _error("CANDIDATE_SCHEMA_INVALID", "multiplier scope must be string or null")
        if not isinstance(value["resolved"], bool):
            raise _error("CANDIDATE_SCHEMA_INVALID", "multiplier resolved must be boolean")
        if active and not value["resolved"]:
            raise _error("SCOPE_UNRESOLVED", "multiplier is unresolved")

    def _validate_special_play(self, value: Any, *, active: bool) -> None:
        if not isinstance(value, dict):
            raise _error("CANDIDATE_SCHEMA_INVALID", "special_play must be an object")
        _require_exact_keys(value, {"kind", "raw_text", "scope", "resolved"}, "special_play")
        self._require_nonempty(value["kind"], "special_play.kind")
        for field in ("raw_text", "scope"):
            if value[field] is not None and not isinstance(value[field], str):
                raise _error("CANDIDATE_SCHEMA_INVALID", f"special_play.{field} must be string or null")
        if not isinstance(value["resolved"], bool):
            raise _error("CANDIDATE_SCHEMA_INVALID", "special_play.resolved must be boolean")
        if active and not value["resolved"]:
            raise _error("SCOPE_UNRESOLVED", "special play scope is unresolved")

    def _validate_continuation(self, value: Any, *, active: bool) -> None:
        if not isinstance(value, dict):
            raise _error("CANDIDATE_SCHEMA_INVALID", "continuation must be an object")
        _require_exact_keys(value, {"present", "resolved"}, "continuation")
        if not isinstance(value["present"], bool) or not isinstance(value["resolved"], bool):
            raise _error("CANDIDATE_SCHEMA_INVALID", "continuation fields must be boolean")
        if active and not value["resolved"]:
            raise _error("SCOPE_UNRESOLVED", "continuation is unresolved")

    def _validate_machine_evidence_refs(self, refs: Any) -> list[dict[str, Any]]:
        if not isinstance(refs, list):
            raise _error("CANDIDATE_SCHEMA_INVALID", "machine_evidence_refs must be a list")
        result: list[dict[str, Any]] = []
        allowed = {
            "provider_id", "model", "request_id", "cache_hit", "evidence_hash",
            "artifact_ref", "value_authority",
        }
        required = {"provider_id", "evidence_hash", "value_authority"}
        for index, ref in enumerate(refs):
            if not isinstance(ref, dict):
                raise _error("CANDIDATE_SCHEMA_INVALID", f"machine ref {index} must be object")
            unknown = set(ref) - allowed
            missing = required - set(ref)
            if unknown or missing:
                raise _error("MACHINE_VALUE_AUTHORITY_FORBIDDEN", f"machine ref keys invalid; missing={sorted(missing)}, unknown={sorted(unknown)}")
            self._require_nonempty(ref["provider_id"], "provider_id")
            self._require_hash(ref["evidence_hash"], "evidence_hash")
            if ref["value_authority"] is not False:
                raise _error("MACHINE_VALUE_AUTHORITY_FORBIDDEN", "machine evidence cannot be value authority")
            for field in ("model", "request_id", "artifact_ref"):
                if field in ref and ref[field] is not None and not isinstance(ref[field], str):
                    raise _error("CANDIDATE_SCHEMA_INVALID", f"{field} must be string or null")
            if "cache_hit" in ref and ref["cache_hit"] is not None and not isinstance(ref["cache_hit"], bool):
                raise _error("CANDIDATE_SCHEMA_INVALID", "cache_hit must be boolean or null")
            complete = {field: ref.get(field) for field in allowed}
            complete["provider_id"] = ref["provider_id"]
            complete["evidence_hash"] = ref["evidence_hash"]
            complete["value_authority"] = False
            result.append(_json_copy(complete))
        return result

    def _validate_candidate_snapshot(self, candidate: dict[str, Any]) -> None:
        expected = {
            "schema_version", "candidate_id", "revision", "state_at_creation", "game",
            "previous_revision_hash", "source", "authority", "active_bets",
            "cancelled_audit", "canonical_content_hash", "created_at", "safety",
        }
        _require_exact_keys(candidate, expected, "Candidate")
        if candidate["schema_version"] != CANDIDATE_SCHEMA_VERSION:
            raise _error("CANDIDATE_SCHEMA_INVALID", "Candidate schema version mismatch")
        if not isinstance(candidate["candidate_id"], str) or not re.fullmatch(r"vc-[a-f0-9]{32}", candidate["candidate_id"]):
            raise _error("CANDIDATE_SCHEMA_INVALID", "candidate_id invalid")
        if not isinstance(candidate["revision"], int) or isinstance(candidate["revision"], bool) or candidate["revision"] < 1:
            raise _error("CANDIDATE_SCHEMA_INVALID", "Candidate revision invalid")
        if candidate["state_at_creation"] != "CURRENT":
            raise _error("CANDIDATE_SCHEMA_INVALID", "Candidate snapshot state must be CURRENT")
        self._require_game(candidate["game"])
        if candidate["previous_revision_hash"] is not None:
            self._require_hash(candidate["previous_revision_hash"], "previous_revision_hash")
        self._require_hash(candidate["canonical_content_hash"], "canonical_content_hash")
        source = candidate["source"]
        if not isinstance(source, dict):
            raise _error("CANDIDATE_SCHEMA_INVALID", "Candidate source invalid")
        _require_exact_keys(source, {"review_session_id", "human_answer_revision", "human_answer_hash", "source_image_id", "source_image_hash"}, "Candidate.source")
        self._require_nonempty(source["review_session_id"], "review_session_id")
        self._require_nonempty(source["source_image_id"], "source_image_id")
        if (
            not isinstance(source["human_answer_revision"], int)
            or isinstance(source["human_answer_revision"], bool)
            or source["human_answer_revision"] < 1
        ):
            raise _error("CANDIDATE_SCHEMA_INVALID", "source Human Answer revision invalid")
        self._require_hash(source["human_answer_hash"], "human_answer_hash")
        self._require_hash(source["source_image_hash"], "source_image_hash")
        authority = candidate["authority"]
        if not isinstance(authority, dict) or authority.get("value_authority") != "human_answer":
            raise _error("MACHINE_VALUE_AUTHORITY_FORBIDDEN", "Candidate authority invalid")
        expected_authority = {"value_authority", "confirmed_by", "confirmed_at", "all_active_confirmed", "blocking_unresolved_count", "machine_evidence_refs"}
        _require_exact_keys(authority, expected_authority, "Candidate.authority")
        self._require_nonempty(authority["confirmed_by"], "confirmed_by")
        self._require_nonempty(authority["confirmed_at"], "confirmed_at")
        self._validate_machine_evidence_refs(authority["machine_evidence_refs"])
        if authority["all_active_confirmed"] is not True or authority["blocking_unresolved_count"] != 0:
            raise _error("CANDIDATE_NOT_READY", "Candidate authority is not fully confirmed")
        if not isinstance(candidate["active_bets"], list) or not candidate["active_bets"]:
            raise _error("CANDIDATE_NOT_READY", "Candidate has no active bets")
        for bet in candidate["active_bets"]:
            review_bet = dict(bet, human_confirmed=True)
            self._validate_review_bet(review_bet, candidate["game"], "active_bet")
            self._validate_candidate_bet(review_bet, candidate["game"])
        if not isinstance(candidate["cancelled_audit"], list):
            raise _error("CANDIDATE_SCHEMA_INVALID", "cancelled_audit must be a list")
        for bet in candidate["cancelled_audit"]:
            review_bet = dict(bet, human_confirmed=True)
            self._validate_review_bet(review_bet, candidate["game"], "cancelled_bet")
            self._validate_candidate_bet(review_bet, candidate["game"])
        safety = candidate["safety"]
        expected_safety = {"candidate_only", "approved_for_fill", "queue_written", "auto_confirm", "auto_submit", "webfill_called"}
        if not isinstance(safety, dict):
            raise _error("CANDIDATE_SCHEMA_INVALID", "Candidate safety invalid")
        _require_exact_keys(safety, expected_safety, "Candidate.safety")
        if safety != {
            "candidate_only": True,
            "approved_for_fill": False,
            "queue_written": False,
            "auto_confirm": False,
            "auto_submit": False,
            "webfill_called": False,
        }:
            raise _error("CANDIDATE_SCHEMA_INVALID", "Candidate safety flags are unsafe")
        projection = {
            "schema_version": CANDIDATE_SCHEMA_VERSION,
            "game": candidate["game"],
            "source_image_hash": source["source_image_hash"],
            "active_bets": candidate["active_bets"],
            "cancelled_audit": candidate["cancelled_audit"],
            "safety": safety,
        }
        if canonical_sha256(projection) != candidate["canonical_content_hash"]:
            raise _error("CANDIDATE_SCHEMA_INVALID", "Candidate content hash mismatch")

    def _validate_stored_review(self, review: dict[str, Any]) -> None:
        expected = {
            "schema_version", "review_session_id", "human_answer_revision", "human_answer_hash",
            "source_image_id", "source_image_hash", "game", "bets",
            "machine_evidence_refs", "blocking_unresolved_count", "all_bets_confirmed",
            "ready_for_candidate", "confirmed_by", "confirmed_at", "created_at", "updated_at",
        }
        _require_exact_keys(review, expected, "HumanReview")
        if review["schema_version"] != HUMAN_REVIEW_SCHEMA_VERSION:
            raise _error("AUTHORITY_STORE_CORRUPT", "HumanReview schema mismatch", 500)
        self._validate_review_identity(
            review["review_session_id"], review["source_image_id"],
            review["source_image_hash"], review["game"],
            review["confirmed_by"] or "unconfirmed",
        )
        bets = self._validate_review_bets(review["bets"], review["game"])
        self._validate_machine_evidence_refs(review["machine_evidence_refs"])
        if (
            not isinstance(review["human_answer_revision"], int)
            or isinstance(review["human_answer_revision"], bool)
            or review["human_answer_revision"] < 1
        ):
            raise _error("AUTHORITY_STORE_CORRUPT", "HumanReview revision invalid", 500)
        derived_blockers = self._derive_blocking_unresolved_count(bets)
        if review["blocking_unresolved_count"] != derived_blockers:
            raise _error("AUTHORITY_STORE_CORRUPT", "HumanReview blocker count mismatch", 500)
        derived_confirmed = bool(bets) and all(
            bet["human_confirmed"] is True for bet in bets
        )
        derived_ready = derived_confirmed and self._review_values_are_candidate_ready(
            bets, review["game"], derived_blockers
        )
        if (
            review["all_bets_confirmed"] is not derived_confirmed
            or review["ready_for_candidate"] is not derived_ready
        ):
            raise _error("AUTHORITY_STORE_CORRUPT", "HumanReview derived flags mismatch", 500)
        if derived_confirmed:
            self._require_nonempty(review["confirmed_by"], "confirmed_by")
            self._require_nonempty(review["confirmed_at"], "confirmed_at")
        elif review["confirmed_by"] is not None or review["confirmed_at"] is not None:
            raise _error("AUTHORITY_STORE_CORRUPT", "unconfirmed review has confirmation metadata", 500)
        projection = {
            "game": review["game"],
            "source_image_hash": review["source_image_hash"],
            "bets": bets,
        }
        if canonical_sha256(projection) != review["human_answer_hash"]:
            raise _error("AUTHORITY_STORE_CORRUPT", "HumanReview hash mismatch", 500)

    # ------------------------------------------------------------------
    # Files and append-only event ledgers
    # ------------------------------------------------------------------

    def _review_path(self, review_session_id: str) -> Path:
        return self._reviews_dir / f"{_safe_name(review_session_id)}.json"

    def _candidate_path(self, candidate_id: str, revision: int) -> Path:
        return self._candidates_dir / candidate_id / f"revision-{revision:06d}.json"

    def _candidate_commit_path(self, candidate_id: str, revision: int) -> Path:
        return self._candidate_commits_dir / candidate_id / f"revision-{revision:06d}.json"

    @staticmethod
    def _require_candidate_id(candidate_id: Any) -> None:
        if not isinstance(candidate_id, str) or not re.fullmatch(
            r"vc-[a-f0-9]{32}", candidate_id
        ):
            raise _error("CANDIDATE_SCHEMA_INVALID", "candidate_id invalid")

    @staticmethod
    def _require_candidate_revision(revision: Any) -> None:
        if (
            not isinstance(revision, int)
            or isinstance(revision, bool)
            or revision < 1
        ):
            raise _error("CANDIDATE_SCHEMA_INVALID", "Candidate revision invalid")

    def _next_candidate_revision(self, candidate_id: str) -> int:
        self._require_candidate_id(candidate_id)
        directory = self._candidates_dir / candidate_id
        revisions: list[int] = []
        for path in directory.glob("revision-*.json") if directory.exists() else []:
            try:
                revisions.append(int(path.stem.split("-")[-1]))
            except ValueError:
                raise _error("AUTHORITY_STORE_CORRUPT", "invalid Candidate filename", 500)
        return max(revisions, default=0) + 1

    def _find_resumable_candidate(
        self, candidate_id: str, review: Mapping[str, Any]
    ) -> dict[str, Any] | None:
        directory = self._candidates_dir / candidate_id
        if not directory.exists():
            return None
        for path in reversed(sorted(directory.glob("revision-*.json"))):
            candidate = _read_json(path)
            self._validate_candidate_snapshot(candidate)
            revision = int(candidate["revision"])
            if self._candidate_commit_path(candidate_id, revision).exists():
                continue
            source = candidate["source"]
            if (
                source["review_session_id"] == review["review_session_id"]
                and source["human_answer_revision"] == review["human_answer_revision"]
                and source["human_answer_hash"] == review["human_answer_hash"]
            ):
                return candidate
        return None

    def _latest_candidate(self, candidate_id: str) -> dict[str, Any] | None:
        self._require_candidate_id(candidate_id)
        directory = self._candidate_commits_dir / candidate_id
        commits = sorted(directory.glob("revision-*.json")) if directory.exists() else []
        if not commits:
            return None
        revision = int(commits[-1].stem.split("-")[-1])
        candidate = _read_json(self._candidate_path(candidate_id, revision))
        self._validate_candidate_snapshot(candidate)
        self._validate_candidate_commit(candidate_id, revision, candidate)
        return candidate

    def _validate_candidate_commit(
        self, candidate_id: str, revision: int, candidate: Mapping[str, Any]
    ) -> None:
        commit = _read_json(self._candidate_commit_path(candidate_id, revision))
        _require_exact_keys(
            commit,
            {
                "candidate_id", "candidate_revision", "canonical_content_hash",
                "idempotency_key_hash",
            },
            "Candidate commit",
        )
        if (
            commit.get("candidate_id") != candidate_id
            or commit.get("candidate_revision") != revision
            or commit.get("canonical_content_hash") != candidate["canonical_content_hash"]
            or not isinstance(commit.get("idempotency_key_hash"), str)
            or not _HASH_RE.fullmatch(commit["idempotency_key_hash"])
        ):
            raise _error("AUTHORITY_STORE_CORRUPT", "Candidate commit mismatch", 500)

    def _candidate_id_for_review(self, review_session_id: str) -> str | None:
        path = self._review_candidate_dir / f"{_safe_name(review_session_id)}.json"
        if not path.exists():
            return None
        record = _read_json(path)
        if record.get("review_session_id") != review_session_id or not isinstance(record.get("candidate_id"), str):
            raise _error("AUTHORITY_STORE_CORRUPT", "review-candidate mapping invalid", 500)
        self._require_candidate_id(record["candidate_id"])
        return record["candidate_id"]

    def _bind_review_candidate(self, review_session_id: str, candidate_id: str) -> None:
        path = self._review_candidate_dir / f"{_safe_name(review_session_id)}.json"
        record = {"review_session_id": review_session_id, "candidate_id": candidate_id}
        if path.exists():
            if _read_json(path) != record:
                raise _error("CANDIDATE_REVISION_CONFLICT", "review is bound to another Candidate", 409)
            return
        _write_immutable_json(path, record)

    def _append_review_event(self, review: dict[str, Any], event_type: str, actor: str) -> dict[str, Any]:
        directory = self._review_events_dir / _safe_name(review["review_session_id"])
        existing = sorted(directory.glob("*.json")) if directory.exists() else []
        sequence = len(existing) + 1
        event = {
            "schema_version": HUMAN_REVIEW_EVENT_SCHEMA_VERSION,
            "event_sequence": sequence,
            "event_type": event_type,
            "review_session_id": review["review_session_id"],
            "human_answer_revision": review["human_answer_revision"],
            "human_answer_hash": review["human_answer_hash"],
            "occurred_at": _now(),
            "actor": actor,
        }
        _write_immutable_json(directory / f"{sequence:06d}.json", event)
        return event

    def _append_candidate_event(
        self,
        candidate_id: str,
        revision: int,
        content_hash: str,
        event_type: str,
        actor: str,
        *,
        reason_code: str | None,
        superseded_by_revision: int | None,
    ) -> dict[str, Any]:
        if event_type not in _LIFECYCLE_EVENTS:
            raise _error("LIFECYCLE_EVENT_INVALID", f"unknown lifecycle event: {event_type}", 422)
        self._require_nonempty(actor, "actor")
        directory = self._candidate_events_dir / candidate_id
        existing = sorted(directory.glob("*.json")) if directory.exists() else []
        sequence = len(existing) + 1
        event = {
            "schema_version": CANDIDATE_EVENT_SCHEMA_VERSION,
            "event_id": f"vce-{uuid.uuid4().hex}",
            "candidate_id": candidate_id,
            "candidate_revision": revision,
            "event_sequence": sequence,
            "event_type": event_type,
            "candidate_content_hash": content_hash,
            "reason_code": reason_code,
            "occurred_at": _now(),
            "actor": actor,
            "superseded_by_revision": superseded_by_revision,
        }
        _write_immutable_json(directory / f"{sequence:06d}.json", event)
        return deepcopy(event)

    def _ensure_candidate_event(
        self,
        candidate_id: str,
        revision: int,
        content_hash: str,
        event_type: str,
        actor: str,
        *,
        reason_code: str | None,
        superseded_by_revision: int | None,
    ) -> dict[str, Any]:
        for event in self.get_lifecycle_events(candidate_id):
            if (
                event["candidate_revision"] == revision
                and event["event_type"] == event_type
                and event["candidate_content_hash"] == content_hash
                and event["reason_code"] == reason_code
                and event["superseded_by_revision"] == superseded_by_revision
            ):
                return event
        return self._append_candidate_event(
            candidate_id,
            revision,
            content_hash,
            event_type,
            actor,
            reason_code=reason_code,
            superseded_by_revision=superseded_by_revision,
        )

    def _commit_candidate_if_complete(
        self, candidate_id: str, revision: int, idempotency_key: str
    ) -> None:
        """Publish the final visibility marker after all authority records exist."""

        self._require_candidate_id(candidate_id)
        self._require_candidate_revision(revision)
        commit_path = self._candidate_commit_path(candidate_id, revision)
        if commit_path.exists():
            candidate = _read_json(self._candidate_path(candidate_id, revision))
            self._validate_candidate_snapshot(candidate)
            self._validate_candidate_commit(candidate_id, revision, candidate)
            return
        snapshot_path = self._candidate_path(candidate_id, revision)
        if not snapshot_path.exists():
            raise _error("AUTHORITY_STORE_CORRUPT", "Candidate snapshot missing", 500)
        candidate = _read_json(snapshot_path)
        self._validate_candidate_snapshot(candidate)
        idempotency = self._load_idempotency(idempotency_key)
        if (
            idempotency is None
            or idempotency.get("candidate_id") != candidate_id
            or idempotency.get("candidate_revision") != revision
        ):
            raise _error("AUTHORITY_STORE_CORRUPT", "Candidate identity commit missing", 500)
        created = any(
            event["candidate_revision"] == revision
            and event["event_type"] == "CREATED"
            and event["candidate_content_hash"] == candidate["canonical_content_hash"]
            for event in self.get_lifecycle_events(candidate_id)
        )
        if not created:
            raise _error("AUTHORITY_STORE_CORRUPT", "Candidate CREATED event missing", 500)
        marker = {
            "candidate_id": candidate_id,
            "candidate_revision": revision,
            "canonical_content_hash": candidate["canonical_content_hash"],
            "idempotency_key_hash": hashlib.sha256(
                idempotency_key.encode("utf-8")
            ).hexdigest(),
        }
        _write_immutable_json(commit_path, marker)

    def _mark_current_candidate_stale(self, review_session_id: str, actor: str) -> None:
        candidate_id = self._candidate_id_for_review(review_session_id)
        if candidate_id is None:
            return
        latest = self._latest_candidate(candidate_id)
        if latest is None:
            raise _error("AUTHORITY_STORE_CORRUPT", "Candidate mapping has no snapshot", 500)
        if self._derived_candidate_state(candidate_id, int(latest["revision"])) == "CURRENT":
            self._append_candidate_event(
                candidate_id,
                int(latest["revision"]),
                latest["canonical_content_hash"],
                "STALE",
                actor,
                reason_code="human_answer_revised",
                superseded_by_revision=None,
            )

    def _derived_candidate_state(self, candidate_id: str, revision: int) -> str:
        state = "MISSING"
        for event in self.get_lifecycle_events(candidate_id):
            if event["candidate_revision"] != revision:
                continue
            event_type = event["event_type"]
            if event_type == "CREATED":
                state = "CURRENT"
            elif (
                event_type == "SUPERSEDED"
                and event["superseded_by_revision"] is not None
                and not self._candidate_commit_path(
                    candidate_id, int(event["superseded_by_revision"])
                ).exists()
            ):
                continue
            else:
                state = event_type
        return state

    def _idempotency_path(self, key: str) -> Path:
        return self._idempotency_dir / f"{_safe_name(key)}.json"

    def _load_idempotency(self, key: str) -> dict[str, Any] | None:
        path = self._idempotency_path(key)
        if not path.exists():
            return None
        record = _read_json(path)
        if record.get("idempotency_key") != key:
            raise _error("IDEMPOTENCY_CONFLICT", "idempotency hash collision", 409)
        return record

    def _write_idempotency(
        self,
        key: str,
        request_identity: dict[str, Any],
        candidate_id: str,
        candidate_revision: int,
    ) -> None:
        record = {
            "idempotency_key": key,
            "request_identity": request_identity,
            "candidate_id": candidate_id,
            "candidate_revision": candidate_revision,
        }
        path = self._idempotency_path(key)
        if path.exists():
            if _read_json(path) != record:
                raise _error("IDEMPOTENCY_CONFLICT", "idempotency key already used", 409)
            return
        _write_immutable_json(path, record)

    @contextmanager
    def _lock(self, scope: str) -> Iterator[None]:
        lock_path = self._locks_dir / f"{_safe_name(scope)}.lock"
        try:
            lock_path.mkdir()
        except FileExistsError as exc:
            raise _error("STORE_BUSY", "authority record is locked", 409) from exc
        try:
            yield
        finally:
            try:
                lock_path.rmdir()
            except OSError:
                pass
