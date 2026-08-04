"""Assistive human-review session lifecycle (MVP v1).

Core data model for the human-in-the-loop recognition flow:

    full image -> Gemini whole-page plaintext -> human edit/confirm
    -> deterministic parser -> anomaly warnings -> per-line confirm
    -> final confirm

Design rules:
1. raw_model_output is stored verbatim, never normalized/overwritten.
2. Blank lines preserve paragraph structure (is_blank=True), need no
   human confirmation, and must survive document reconstruction.
3. all_confirmed is a COMPUTED property (never stored).
4. Editing edited_text resets the line to UNREVIEWED, invalidates
   parsed_result / session parsed_output / final confirmation / hashes,
   and recomputes warnings.
5. edited != raw does NOT auto-set CORRECTED — the user must explicitly
   confirm again.
6. Line confirmation rules:
     raw == edited : UNREVIEWED -> CONFIRMED
     raw != edited : UNREVIEWED -> CORRECTED
     BLOCKER present: cannot become CONFIRMED or CORRECTED
7. final_confirm requires all_confirmed, no BLOCKER, parsed_output
   exists, edited_output_sha256 and parsed_output_sha256 computed.
8. verify_confirmation_freshness() recomputes hashes; mismatch raises
   CONFIRMATION_STALE.
9. Persistence: user_data/assistive_sessions/<session_id>.json, temp
   file + atomic replace, no credentials, no base64 image.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Optional

SCHEMA_VERSION = 1
DEFAULT_SESSION_DIR = "assistive_sessions"


class LineStatus(str, Enum):
    UNREVIEWED = "UNREVIEWED"
    CONFIRMED = "CONFIRMED"
    CORRECTED = "CORRECTED"
    BLOCKED = "BLOCKED"


class ConfirmationError(Exception):
    """Raised when a confirmation action is invalid."""


class ConfirmationStaleError(ConfirmationError):
    """Raised by verify_confirmation_freshness on hash mismatch."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def new_session_id() -> str:
    return f"assistive-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f')}"


class AssistiveLine:
    """One physical row of the recognition output (blank lines included)."""

    def __init__(
        self,
        *,
        line_id: str,
        source_line_no: int,
        paragraph_index: int,
        raw_text: str = "",
        edited_text: Optional[str] = None,
        is_blank: bool = False,
        status: LineStatus = LineStatus.UNREVIEWED,
        warnings: Optional[list[dict[str, Any]]] = None,
        parsed_result: Optional[dict[str, Any]] = None,
        created_at: Optional[str] = None,
        updated_at: Optional[str] = None,
    ) -> None:
        self.line_id = line_id
        self.source_line_no = source_line_no
        self.paragraph_index = paragraph_index
        self.raw_text = raw_text
        # blank lines: edited_text == raw_text == "" and stays blank
        self.edited_text = "" if is_blank else (edited_text if edited_text
                                                is not None else raw_text)
        self.is_blank = is_blank
        self.status = status if not is_blank else LineStatus.CONFIRMED
        self.warnings = warnings or []
        self.parsed_result = parsed_result
        self.created_at = created_at or _now()
        self.updated_at = updated_at or self.created_at

    # ── state transitions ────────────────────────────────────────────────

    def _blockers(self) -> list[str]:
        return [w["code"] for w in self.warnings
                if w.get("severity") == "BLOCKER"]

    def confirm(self, by: str) -> None:
        """raw == edited -> CONFIRMED; raw != edited -> CORRECTED."""
        if self.is_blank:
            return
        if self._blockers():
            raise ConfirmationError(
                f"line {self.line_id} has BLOCKER warnings: "
                f"{self._blockers()}")
        self.status = (LineStatus.CONFIRMED if self.edited_text == self.raw_text
                       else LineStatus.CORRECTED)
        self.updated_at = _now()

    def block(self, by: str) -> None:
        if self.is_blank:
            return
        self.status = LineStatus.BLOCKED
        self.updated_at = _now()

    def set_edited_text(self, text: str) -> None:
        """User edit: reset to UNREVIEWED, invalidate parse + warnings."""
        if self.is_blank:
            return
        self.edited_text = text
        self.status = LineStatus.UNREVIEWED
        self.parsed_result = None
        self.warnings = []
        self.updated_at = _now()

    def set_warnings(self, warnings: list[dict[str, Any]]) -> None:
        self.warnings = warnings
        self.updated_at = _now()

    def to_dict(self) -> dict[str, Any]:
        return {
            "line_id": self.line_id,
            "source_line_no": self.source_line_no,
            "paragraph_index": self.paragraph_index,
            "raw_text": self.raw_text,
            "edited_text": self.edited_text,
            "is_blank": self.is_blank,
            "status": self.status.value,
            "warnings": self.warnings,
            "parsed_result": self.parsed_result,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "AssistiveLine":
        return cls(
            line_id=d["line_id"],
            source_line_no=d["source_line_no"],
            paragraph_index=d["paragraph_index"],
            raw_text=d.get("raw_text", ""),
            edited_text=d.get("edited_text"),
            is_blank=d.get("is_blank", False),
            status=LineStatus(d["status"]) if "status" in d
            else LineStatus.UNREVIEWED,
            warnings=d.get("warnings") or [],
            parsed_result=d.get("parsed_result"),
            created_at=d.get("created_at"),
            updated_at=d.get("updated_at"),
        )


class AssistiveSession:
    """One recognition session with full lifecycle + persistence."""

    def __init__(
        self,
        *,
        session_id: Optional[str] = None,
        source_image_sha256: str = "",
        provider: str = "",
        requested_model: str = "",
        response_model: str = "",
        prompt_version: str = "",
        generation_config: Optional[dict[str, Any]] = None,
        latency_ms: Optional[float] = None,
        token_usage: Optional[dict[str, Any]] = None,
        finish_reason: str = "",
        request_id: str = "",
        raw_model_output: str = "",
        lines: Optional[list[AssistiveLine]] = None,
        session_dir: Optional[Path] = None,
    ) -> None:
        self.schema_version = SCHEMA_VERSION
        self.session_id = session_id or new_session_id()
        self.created_at = _now()
        self.updated_at = self.created_at
        self.source_image_sha256 = source_image_sha256
        self.provider = provider
        self.requested_model = requested_model
        self.response_model = response_model
        self.prompt_version = prompt_version
        self.generation_config = generation_config or {}
        self.latency_ms = latency_ms
        self.token_usage = token_usage or {}
        self.finish_reason = finish_reason
        self.request_id = request_id
        self.raw_model_output = raw_model_output  # verbatim, never touched
        self.lines = lines or []
        self.parsed_output: Optional[list[dict[str, Any]]] = None
        self.edited_output_sha256: Optional[str] = None
        self.parsed_output_sha256: Optional[str] = None
        self.final_confirmed_at: Optional[str] = None
        self.final_confirmed_by: Optional[str] = None
        self._session_dir = session_dir

    # ── factory ──────────────────────────────────────────────────────────

    @classmethod
    def from_recognized(
        cls,
        *,
        raw_model_output: str,
        source_image_sha256: str,
        provider: str,
        requested_model: str,
        response_model: str,
        prompt_version: str,
        generation_config: dict[str, Any],
        latency_ms: float,
        token_usage: dict[str, Any],
        finish_reason: str,
        request_id: str,
        session_dir: Optional[Path] = None,
    ) -> "AssistiveSession":
        """Build a session from raw model output, splitting into lines while
        preserving blank lines and paragraph structure."""
        lines: list[AssistiveLine] = []
        paragraph_index = 0
        in_paragraph = False
        raw_lines = raw_model_output.splitlines()
        for i, raw_line in enumerate(raw_lines):
            is_blank = raw_line.strip() == ""
            if not is_blank and not in_paragraph:
                paragraph_index += 1  # new paragraph opens at first non-blank
                in_paragraph = True
            elif is_blank:
                in_paragraph = False
            lines.append(AssistiveLine(
                line_id=f"L{i + 1:03d}",
                source_line_no=i + 1,
                paragraph_index=paragraph_index,
                raw_text=raw_line,
                edited_text="" if is_blank else raw_line,
                is_blank=is_blank,
            ))
        return cls(
            raw_model_output=raw_model_output,
            source_image_sha256=source_image_sha256,
            provider=provider,
            requested_model=requested_model,
            response_model=response_model,
            prompt_version=prompt_version,
            generation_config=generation_config,
            latency_ms=latency_ms,
            token_usage=token_usage,
            finish_reason=finish_reason,
            request_id=request_id,
            lines=lines,
            session_dir=session_dir,
        )

    # ── computed ─────────────────────────────────────────────────────────

    @property
    def all_confirmed(self) -> bool:
        non_blank = [l for l in self.lines if not l.is_blank]
        if not non_blank:
            return False
        return all(l.status in (LineStatus.CONFIRMED, LineStatus.CORRECTED)
                   for l in non_blank)

    def blockers(self) -> list[str]:
        codes: list[str] = []
        for l in self.lines:
            for w in l.warnings:
                if w.get("severity") == "BLOCKER" and w["code"] not in codes:
                    codes.append(w["code"])
        return codes

    def non_blank_lines(self) -> list[AssistiveLine]:
        return [l for l in self.lines if not l.is_blank]

    # ── document reconstruction ──────────────────────────────────────────

    def build_edited_document(self) -> str:
        """Reconstruct the full edited document, preserving blank lines."""
        return "\n".join(l.edited_text for l in self.lines)

    # ── user actions ─────────────────────────────────────────────────────

    def update_line_edited(self, line_id: str, new_text: str) -> None:
        line = self._get_line(line_id)
        line.set_edited_text(new_text)
        self.parsed_output = None
        self._clear_confirmation()

    def confirm_line(self, line_id: str, by: str) -> None:
        line = self._get_line(line_id)
        line.confirm(by)
        self.updated_at = _now()
        self._clear_confirmation()

    def block_line(self, line_id: str, by: str) -> None:
        line = self._get_line(line_id)
        line.block(by)
        self.updated_at = _now()
        self._clear_confirmation()

    def set_line_warnings(self, line_id: str,
                          warnings: list[dict[str, Any]]) -> None:
        self._get_line(line_id).set_warnings(warnings)

    def set_parsed_output(self, parsed: list[dict[str, Any]]) -> None:
        self.parsed_output = parsed
        self.updated_at = _now()

    def final_confirm(self, by: str) -> None:
        if not self.all_confirmed:
            raise ConfirmationError("not all lines confirmed")
        if self.blockers():
            raise ConfirmationError(f"BLOCKER warnings present: "
                                    f"{self.blockers()}")
        if self.parsed_output is None:
            raise ConfirmationError("parsed_output missing")
        edited_doc = self.build_edited_document()
        self.edited_output_sha256 = _sha256(edited_doc)
        self.parsed_output_sha256 = _sha256(
            json.dumps(self.parsed_output, ensure_ascii=False,
                       sort_keys=True))
        self.final_confirmed_at = _now()
        self.final_confirmed_by = by
        self.updated_at = self.final_confirmed_at

    # ── freshness ────────────────────────────────────────────────────────

    def verify_confirmation_freshness(self) -> None:
        """Recompute hashes; raise ConfirmationStaleError on mismatch."""
        if self.final_confirmed_at is None:
            return  # nothing confirmed yet — nothing to be stale
        edited_doc = self.build_edited_document()
        if _sha256(edited_doc) != self.edited_output_sha256:
            raise ConfirmationStaleError(
                "CONFIRMATION_STALE: edited document changed since confirm")
        if self.parsed_output is not None:
            h = _sha256(json.dumps(self.parsed_output, ensure_ascii=False,
                                   sort_keys=True))
            if h != self.parsed_output_sha256:
                raise ConfirmationStaleError(
                    "CONFIRMATION_STALE: parsed output changed since confirm")

    # ── persistence ──────────────────────────────────────────────────────

    def _session_path(self, session_dir: Path | None = None) -> Path:
        d = session_dir or self._session_dir
        if d is None:
            raise ConfirmationError("no session_dir configured")
        return d / f"{self.session_id}.json"

    def save(self, session_dir: Path | None = None) -> Path:
        d = session_dir or self._session_dir
        if d is None:
            raise ConfirmationError("no session_dir configured")
        d.mkdir(parents=True, exist_ok=True)
        self._session_dir = d  # bind for later delete()
        path = d / f"{self.session_id}.json"
        data = self.to_dict()
        fd, tmp = tempfile.mkstemp(dir=str(d), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, path)  # atomic on same filesystem
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
        return path

    @classmethod
    def load(cls, session_id: str,
             session_dir: Path) -> "AssistiveSession":
        path = session_dir / f"{session_id}.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        s = cls(
            session_id=data["session_id"],
            source_image_sha256=data.get("source_image_sha256", ""),
            provider=data.get("provider", ""),
            requested_model=data.get("requested_model", ""),
            response_model=data.get("response_model", ""),
            prompt_version=data.get("prompt_version", ""),
            generation_config=data.get("generation_config") or {},
            latency_ms=data.get("latency_ms"),
            token_usage=data.get("token_usage") or {},
            finish_reason=data.get("finish_reason", ""),
            request_id=data.get("request_id", ""),
            raw_model_output=data.get("raw_model_output", ""),
            lines=[AssistiveLine.from_dict(l) for l in data.get("lines", [])],
            session_dir=session_dir,
        )
        s.created_at = data.get("created_at", s.created_at)
        s.updated_at = data.get("updated_at", s.updated_at)
        s.parsed_output = data.get("parsed_output")
        s.edited_output_sha256 = data.get("edited_output_sha256")
        s.parsed_output_sha256 = data.get("parsed_output_sha256")
        s.final_confirmed_at = data.get("final_confirmed_at")
        s.final_confirmed_by = data.get("final_confirmed_by")
        return s

    def delete(self) -> None:
        if self._session_dir is None:
            raise ConfirmationError("no session_dir configured")
        path = self._session_path()
        if path.exists():
            path.unlink()

    # ── serialization ────────────────────────────────────────────────────

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "session_id": self.session_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "source_image_sha256": self.source_image_sha256,
            "provider": self.provider,
            "requested_model": self.requested_model,
            "response_model": self.response_model,
            "prompt_version": self.prompt_version,
            "generation_config": self.generation_config,
            "latency_ms": self.latency_ms,
            "token_usage": self.token_usage,
            "finish_reason": self.finish_reason,
            "request_id": self.request_id,
            "raw_model_output": self.raw_model_output,
            "lines": [l.to_dict() for l in self.lines],
            "parsed_output": self.parsed_output,
            "edited_output_sha256": self.edited_output_sha256,
            "parsed_output_sha256": self.parsed_output_sha256,
            "final_confirmed_at": self.final_confirmed_at,
            "final_confirmed_by": self.final_confirmed_by,
        }

    # ── helpers ──────────────────────────────────────────────────────────

    def _get_line(self, line_id: str) -> AssistiveLine:
        for l in self.lines:
            if l.line_id == line_id:
                return l
        raise ConfirmationError(f"unknown line_id {line_id}")

    def _clear_confirmation(self) -> None:
        self.edited_output_sha256 = None
        self.parsed_output_sha256 = None
        self.final_confirmed_at = None
        self.final_confirmed_by = None


def default_session_dir(base_dir: Path) -> Path:
    return base_dir / "assistive_sessions"
