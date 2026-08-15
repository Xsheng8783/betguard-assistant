"""Immutable logical Webfill target profiles for Gate 3C dry-run prepare.

Profiles describe semantic capabilities only.  DOM selectors, URLs, browser
state and submission authority are intentionally outside this store.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import threading
import time
import unicodedata
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator, Mapping

from betguard.vision.candidate_authority import canonical_json_bytes, canonical_sha256


PROFILE_SCHEMA_VERSION = "vision-webfill-target-profile-v1"
_PROFILE_ID_RE = re.compile(r"^wtp-[a-z0-9][a-z0-9._-]{2,63}$")
_HASH_RE = re.compile(r"^[a-f0-9]{64}$")
_PROCESS_LOCKS: dict[str, threading.RLock] = {}
_PROCESS_LOCKS_GUARD = threading.Lock()


class WebfillTargetProfileError(Exception):
    def __init__(self, code: str, message: str, http_status: int = 422) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.http_status = http_status


def _fail(code: str, message: str, status: int = 422) -> WebfillTargetProfileError:
    return WebfillTargetProfileError(code, message, status)


def _assert_json_safe(value: Any, label: str = "profile") -> None:
    if isinstance(value, bool) or value is None:
        return
    if isinstance(value, int):
        return
    if isinstance(value, float):
        raise _fail("PREPARE_TARGET_PROFILE_INTEGRITY_INVALID", f"{label} contains float")
    if isinstance(value, str):
        if unicodedata.normalize("NFC", value) != value:
            raise _fail("PREPARE_TARGET_PROFILE_INTEGRITY_INVALID", f"{label} is not NFC")
        return
    if isinstance(value, list):
        for item in value:
            _assert_json_safe(item, label)
        return
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str) or unicodedata.normalize("NFC", key) != key:
                raise _fail("PREPARE_TARGET_PROFILE_INTEGRITY_INVALID", f"{label} key is invalid")
            _assert_json_safe(item, label)
        return
    raise _fail("PREPARE_TARGET_PROFILE_INTEGRITY_INVALID", f"{label} contains non-JSON value")


def _write_immutable(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(canonical_json_bytes(dict(value)))
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError as exc:
            raise _fail("PREPARE_TARGET_PROFILE_VERSION_MISMATCH", "immutable profile version already exists", 409) from exc
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _write_atomic(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(canonical_json_bytes(dict(value)))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _read(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise _fail("PREPARE_TARGET_PROFILE_INTEGRITY_INVALID", f"cannot read {path.name}", 500) from exc
    if not isinstance(value, dict):
        raise _fail("PREPARE_TARGET_PROFILE_INTEGRITY_INVALID", "profile root must be object", 500)
    return value


class WebfillTargetProfileStore:
    """Versioned immutable profile registry with one atomic active pointer."""

    def __init__(self, base_dir: Path | str) -> None:
        self.base_dir = Path(base_dir)
        self._profiles = self.base_dir / "target-profiles"
        self._active = self.base_dir / "target-profile-active-version"
        self._profiles.mkdir(parents=True, exist_ok=True)
        self._active.mkdir(parents=True, exist_ok=True)
        self._locks = self.base_dir / "locks"
        self._locks.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def coordination(self) -> Iterator["_TargetProfileCoordinator"]:
        """Hold the registry lock across active-profile read and Prepare commit."""

        with self._profile_lock():
            yield _TargetProfileCoordinator(self)

    def register_profile(self, profile: Mapping[str, Any], *, activate: bool = False) -> dict[str, Any]:
        value = deepcopy(dict(profile)) if isinstance(profile, Mapping) else None
        self._validate(value)
        # Immutable publication itself is race-safe and exact-idempotent.  The
        # active pointer update is separately serialized below.
        result = self._register_locked(value, activate=False)
        if activate:
            self.activate_version(value["target_profile_id"], value["target_profile_version"])
        return result

    def activate_version(self, profile_id: str, version: int) -> dict[str, Any]:
        with self._profile_lock():
            return self._activate_locked(profile_id, version)

    def get_profile(self, profile_id: str, version: int, *, require_active: bool = True) -> dict[str, Any]:
        with self._profile_lock():
            return self._get_profile_locked(profile_id, version, require_active=require_active)

    def _get_profile_locked(self, profile_id: str, version: int, *, require_active: bool = True) -> dict[str, Any]:
        if not isinstance(profile_id, str) or not _PROFILE_ID_RE.fullmatch(profile_id):
            raise _fail("PREPARE_TARGET_PROFILE_NOT_FOUND", "target profile id is invalid", 404)
        if isinstance(version, bool) or not isinstance(version, int) or version < 1:
            raise _fail("PREPARE_TARGET_PROFILE_VERSION_MISMATCH", "target profile version is invalid", 409)
        path = self._path(profile_id, version)
        if not path.exists():
            raise _fail("PREPARE_TARGET_PROFILE_NOT_FOUND", "target profile was not found", 404)
        profile = _read(path)
        self._validate(profile)
        if require_active:
            active = self._get_active_profile_locked(profile_id)
            if active["target_profile_version"] != version or active["profile_integrity_hash"] != profile["profile_integrity_hash"]:
                raise _fail("PREPARE_TARGET_PROFILE_VERSION_MISMATCH", "target profile is not the active version", 409)
        return deepcopy(profile)

    def get_active_profile(self, profile_id: str) -> dict[str, Any]:
        with self._profile_lock():
            return self._get_active_profile_locked(profile_id)

    def _get_active_profile_locked(self, profile_id: str) -> dict[str, Any]:
        if not isinstance(profile_id, str) or not _PROFILE_ID_RE.fullmatch(profile_id):
            raise _fail("PREPARE_TARGET_PROFILE_NOT_FOUND", "target profile id is invalid", 404)
        pointer_path = self._active / f"{profile_id}.json"
        if not pointer_path.exists():
            raise _fail("PREPARE_TARGET_PROFILE_NOT_FOUND", "active target profile was not found", 404)
        pointer = _read(pointer_path)
        expected = {"schema_version", "target_profile_id", "target_profile_version", "profile_integrity_hash"}
        if set(pointer) != expected or pointer.get("schema_version") != "vision-webfill-target-profile-active-v1" or pointer.get("target_profile_id") != profile_id:
            raise _fail("PREPARE_TARGET_PROFILE_INTEGRITY_INVALID", "active target profile pointer is invalid", 500)
        version = pointer.get("target_profile_version")
        integrity = pointer.get("profile_integrity_hash")
        if isinstance(version, bool) or not isinstance(version, int) or version < 1 or not isinstance(integrity, str) or not _HASH_RE.fullmatch(integrity):
            raise _fail("PREPARE_TARGET_PROFILE_INTEGRITY_INVALID", "active target profile pointer identity invalid", 500)
        profile = self._get_profile_locked(profile_id, version, require_active=False)
        if profile["profile_integrity_hash"] != pointer["profile_integrity_hash"]:
            raise _fail("PREPARE_TARGET_PROFILE_INTEGRITY_INVALID", "active target profile pointer hash mismatch", 500)
        return profile

    def _register_locked(self, value: Mapping[str, Any], *, activate: bool) -> dict[str, Any]:
        path = self._path(value["target_profile_id"], value["target_profile_version"])
        if path.exists():
            existing = _read(path)
            self._validate(existing)
            if existing != value:
                raise _fail("PREPARE_TARGET_PROFILE_VERSION_MISMATCH", "profile version has different immutable content", 409)
        else:
            try:
                _write_immutable(path, value)
            except WebfillTargetProfileError as exc:
                if exc.code != "PREPARE_TARGET_PROFILE_VERSION_MISMATCH" or not path.exists():
                    raise
                existing = _read(path)
                self._validate(existing)
                if existing != value:
                    raise
        if activate:
            self._activate_locked(value["target_profile_id"], value["target_profile_version"])
        return deepcopy(dict(value))

    def _activate_locked(self, profile_id: str, version: int) -> dict[str, Any]:
        profile = self._get_profile_locked(profile_id, version, require_active=False)
        pointer = {"schema_version": "vision-webfill-target-profile-active-v1", "target_profile_id": profile_id, "target_profile_version": version, "profile_integrity_hash": profile["profile_integrity_hash"]}
        _write_atomic(self._active / f"{profile_id}.json", pointer)
        return deepcopy(pointer)

    def _path(self, profile_id: str, version: int) -> Path:
        return self._profiles / profile_id / f"{version:08d}.json"

    @contextmanager
    def _profile_lock(self) -> Iterator[None]:
        key = str(self.base_dir.resolve())
        with _PROCESS_LOCKS_GUARD:
            process_lock = _PROCESS_LOCKS.setdefault(key, threading.RLock())
        if not process_lock.acquire(timeout=5.0):
            raise _fail("PREPARE_STORE_BUSY", "target profile store is busy", 409)
        stream = None
        try:
            lock_path = self._locks / "profiles.lock"
            stream = open(lock_path, "a+b")
            if stream.tell() == 0:
                stream.write(b"\0"); stream.flush()
            deadline = time.monotonic() + 5.0
            while True:
                try:
                    stream.seek(0)
                    if os.name == "nt":
                        import msvcrt
                        msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError as exc:
                    if time.monotonic() >= deadline:
                        raise _fail("PREPARE_STORE_BUSY", "target profile store is busy", 409) from exc
                    time.sleep(0.01)
            yield
        finally:
            if stream is not None:
                try:
                    stream.seek(0)
                    if os.name == "nt":
                        import msvcrt
                        msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
                except OSError:
                    pass
                stream.close()
            process_lock.release()

    @staticmethod
    def _validate(profile: Any) -> None:
        if not isinstance(profile, Mapping):
            raise _fail("PREPARE_TARGET_PROFILE_INTEGRITY_INVALID", "profile must be object")
        _assert_json_safe(profile)
        expected = {"schema_version", "target_profile_id", "target_profile_version", "game", "capabilities", "ordering_contract", "adapter_boundary", "created_at", "profile_integrity_hash"}
        if set(profile) != expected or profile.get("schema_version") != PROFILE_SCHEMA_VERSION:
            raise _fail("PREPARE_TARGET_PROFILE_INTEGRITY_INVALID", "profile keys or schema invalid")
        profile_id = profile["target_profile_id"]
        version = profile["target_profile_version"]
        if not isinstance(profile_id, str) or not _PROFILE_ID_RE.fullmatch(profile_id) or isinstance(version, bool) or not isinstance(version, int) or version < 1:
            raise _fail("PREPARE_TARGET_PROFILE_INTEGRITY_INVALID", "profile identity invalid")
        if not isinstance(profile["game"], str) or not profile["game"]:
            raise _fail("PREPARE_TARGET_PROFILE_INTEGRITY_INVALID", "profile game invalid")
        try:
            timestamp = datetime.fromisoformat(profile["created_at"])
        except (TypeError, ValueError) as exc:
            raise _fail("PREPARE_TARGET_PROFILE_INTEGRITY_INVALID", "profile timestamp invalid") from exc
        if "T" not in profile["created_at"] or timestamp.tzinfo is None or timestamp.utcoffset() is None:
            raise _fail("PREPARE_TARGET_PROFILE_INTEGRITY_INVALID", "profile timestamp must be timezone-aware RFC3339")
        capabilities = profile["capabilities"]
        cap_keys = {"supported_bet_types", "supported_special_play_kinds", "supports_continuation", "supports_multiple_multiplier_rules", "maximum_active_bets", "maximum_number_groups_per_bet", "maximum_numbers_per_group"}
        if not isinstance(capabilities, Mapping) or set(capabilities) != cap_keys:
            raise _fail("PREPARE_TARGET_PROFILE_INTEGRITY_INVALID", "profile capabilities invalid")
        bet_types = capabilities["supported_bet_types"]
        specials = capabilities["supported_special_play_kinds"]
        if not isinstance(bet_types, list) or not bet_types or any(not isinstance(item, str) for item in bet_types) or len(set(bet_types)) != len(bet_types) or not set(bet_types) <= {"normal", "column"}:
            raise _fail("PREPARE_TARGET_PROFILE_INTEGRITY_INVALID", "supported bet types invalid")
        if not isinstance(specials, list) or not specials or any(not isinstance(item, str) or not item for item in specials) or len(set(specials)) != len(specials):
            raise _fail("PREPARE_TARGET_PROFILE_INTEGRITY_INVALID", "supported special plays invalid")
        for key in ("supports_continuation", "supports_multiple_multiplier_rules"):
            if not isinstance(capabilities[key], bool):
                raise _fail("PREPARE_TARGET_PROFILE_INTEGRITY_INVALID", f"{key} invalid")
        for key in ("maximum_active_bets", "maximum_number_groups_per_bet", "maximum_numbers_per_group"):
            if isinstance(capabilities[key], bool) or not isinstance(capabilities[key], int) or capabilities[key] < 1:
                raise _fail("PREPARE_TARGET_PROFILE_INTEGRITY_INVALID", f"{key} invalid")
        if profile["ordering_contract"] != {"bet_order": "preserve_candidate_active_bets", "group_order": "preserve", "number_order": "preserve", "multiplier_rule_order": "preserve"}:
            raise _fail("PREPARE_TARGET_PROFILE_INTEGRITY_INVALID", "ordering contract invalid")
        if profile["adapter_boundary"] != {"logical_contract_only": True, "dom_mapping_present": False, "browser_execution_authorized": False, "submit_authorized": False}:
            raise _fail("PREPARE_TARGET_PROFILE_INTEGRITY_INVALID", "adapter boundary invalid")
        integrity = profile["profile_integrity_hash"]
        if not isinstance(integrity, str) or not _HASH_RE.fullmatch(integrity):
            raise _fail("PREPARE_TARGET_PROFILE_INTEGRITY_INVALID", "profile hash invalid")
        projection = dict(profile)
        projection.pop("profile_integrity_hash")
        if canonical_sha256(projection) != integrity:
            raise _fail("PREPARE_TARGET_PROFILE_INTEGRITY_INVALID", "profile hash mismatch")


class _TargetProfileCoordinator:
    def __init__(self, store: WebfillTargetProfileStore) -> None:
        self._store = store

    def get_profile_locked(self, profile_id: str, version: int, *, require_active: bool = True) -> dict[str, Any]:
        return self._store._get_profile_locked(profile_id, version, require_active=require_active)

    def get_active_profile_locked(self, profile_id: str) -> dict[str, Any]:
        return self._store._get_active_profile_locked(profile_id)
