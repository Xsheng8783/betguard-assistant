"""Tests for assistive_session — human review session lifecycle."""
import json
import os
import tempfile
from pathlib import Path

import pytest

from betguard.vision.assistive_session import (
    AssistiveLine,
    AssistiveSession,
    ConfirmationError,
    ConfirmationStaleError,
    LineStatus,
)


def _make_session(raw: str = "01 20 x1\n18 26 x1\n\n14 16 22 28\n三X0.5"):
    return AssistiveSession.from_recognized(
        raw_model_output=raw,
        source_image_sha256="a" * 64,
        provider="gemini-paid",
        requested_model="gemini-2.5-flash",
        response_model="gemini-2.5-flash",
        prompt_version="assistive-whole-page-v1",
        generation_config={"temperature": 0.1, "max_output_tokens": 8192},
        latency_ms=1200.0,
        token_usage={"prompt_tokens": 100, "output_tokens": 50},
        finish_reason="STOP",
        request_id="req-1",
    )


class TestSessionCreation:
    def test_creates_session_with_lines(self):
        s = _make_session()
        assert s.session_id.startswith("assistive-")
        assert s.schema_version == 1
        assert len(s.lines) == 5

    def test_raw_output_verbatim(self):
        raw = "01 20 x1\n18 26 x1"
        s = _make_session(raw)
        assert s.raw_model_output == raw  # never normalized

    def test_blank_lines_preserved(self):
        s = _make_session()
        blanks = [l for l in s.lines if l.is_blank]
        assert len(blanks) == 1
        assert blanks[0].source_line_no == 3

    def test_paragraph_index(self):
        s = _make_session()
        paras = [l.paragraph_index for l in s.lines if not l.is_blank]
        assert paras[0] == paras[1] == 1
        assert paras[2] == paras[3] == 2

    def test_blank_line_status_confirmed(self):
        s = _make_session()
        blank = [l for l in s.lines if l.is_blank][0]
        assert blank.status == LineStatus.CONFIRMED  # no confirm needed


class TestAllConfirmed:
    def test_false_when_unreviewed(self):
        s = _make_session()
        assert s.all_confirmed is False

    def test_true_when_all_confirmed(self):
        s = _make_session()
        for l in s.non_blank_lines():
            l.confirm("user")
        assert s.all_confirmed is True

    def test_false_when_blocked(self):
        s = _make_session()
        for l in s.non_blank_lines():
            l.set_warnings([{"code": "INVALID_CHARSET",
                             "severity": "BLOCKER"}])
            with pytest.raises(ConfirmationError):
                l.confirm("user")
        assert s.all_confirmed is False

    def test_false_when_no_lines(self):
        s = AssistiveSession.from_recognized(
            raw_model_output="", source_image_sha256="b" * 64,
            provider="p", requested_model="m", response_model="m",
            prompt_version="v", generation_config={}, latency_ms=1.0,
            token_usage={}, finish_reason="STOP", request_id="r")
        assert s.all_confirmed is False


class TestConfirmTransitions:
    def test_confirm_unchanged_is_CONFIRMED(self):
        s = _make_session()
        line = s.non_blank_lines()[0]
        line.confirm("user")
        assert line.status == LineStatus.CONFIRMED

    def test_confirm_edited_is_CORRECTED(self):
        s = _make_session()
        line = s.non_blank_lines()[0]
        line.set_edited_text("01 20 x2")  # differs from raw
        assert line.status == LineStatus.UNREVIEWED
        line.confirm("user")
        assert line.status == LineStatus.CORRECTED

    def test_edit_resets_to_unreviewed(self):
        s = _make_session()
        line = s.non_blank_lines()[0]
        line.confirm("user")
        assert line.status == LineStatus.CONFIRMED
        line.set_edited_text("changed")
        assert line.status == LineStatus.UNREVIEWED
        assert line.parsed_result is None
        assert line.warnings == []

    def test_blocked_cannot_confirm(self):
        s = _make_session()
        line = s.non_blank_lines()[0]
        line.set_warnings([{"code": "NUMBER_OUT_OF_RANGE",
                            "severity": "BLOCKER"}])
        with pytest.raises(ConfirmationError):
            line.confirm("user")

    def test_blocked_status(self):
        s = _make_session()
        line = s.non_blank_lines()[0]
        line.block("user")
        assert line.status == LineStatus.BLOCKED


class TestSessionMutations:
    def test_update_line_edited_invalidates_parse_and_confirm(self):
        s = _make_session()
        for l in s.non_blank_lines():
            l.confirm("user")
        s.set_parsed_output([{"line": "x"}])
        s.final_confirm("user")
        assert s.final_confirmed_at is not None
        line_id = s.non_blank_lines()[0].line_id
        s.update_line_edited(line_id, "new text")
        assert s.parsed_output is None
        assert s.final_confirmed_at is None
        assert s.edited_output_sha256 is None
        assert s.parsed_output_sha256 is None
        assert s._get_line(line_id).status == LineStatus.UNREVIEWED

    def test_final_confirm_requires_all_confirmed(self):
        s = _make_session()
        with pytest.raises(ConfirmationError):
            s.final_confirm("user")

    def test_final_confirm_requires_parsed_output(self):
        s = _make_session()
        for l in s.non_blank_lines():
            l.confirm("user")
        with pytest.raises(ConfirmationError):
            s.final_confirm("user")  # parsed_output missing

    def test_final_confirm_sets_hashes(self):
        s = _make_session()
        for l in s.non_blank_lines():
            l.confirm("user")
        s.set_parsed_output([{"gt": "01 20 x1"}])
        s.final_confirm("alice")
        assert s.edited_output_sha256 and len(s.edited_output_sha256) == 64
        assert s.parsed_output_sha256 and len(s.parsed_output_sha256) == 64
        assert s.final_confirmed_at is not None
        assert s.final_confirmed_by == "alice"

    def test_verify_freshness_ok(self):
        s = _make_session()
        for l in s.non_blank_lines():
            l.confirm("user")
        s.set_parsed_output([{"gt": "01 20 x1"}])
        s.final_confirm("alice")
        s.verify_confirmation_freshness()  # no raise

    def test_verify_freshness_stale_after_edit(self):
        s = _make_session()
        for l in s.non_blank_lines():
            l.confirm("user")
        s.set_parsed_output([{"gt": "01 20 x1"}])
        s.final_confirm("alice")
        # tamper directly on the line object, bypassing the session API —
        # the stored confirmation hashes are now stale
        s.lines[0].edited_text = "tampered"
        with pytest.raises(ConfirmationStaleError):
            s.verify_confirmation_freshness()

    def test_edit_via_api_clears_confirmation_so_verify_passes(self):
        s = _make_session()
        for l in s.non_blank_lines():
            l.confirm("user")
        s.set_parsed_output([{"gt": "01 20 x1"}])
        s.final_confirm("alice")
        line_id = s.non_blank_lines()[0].line_id
        s.update_line_edited(line_id, "edited via api")
        assert s.final_confirmed_at is None
        # nothing confirmed anymore -> nothing stale
        s.verify_confirmation_freshness()

    def test_edited_document_reconstruction_preserves_blanks(self):
        s = _make_session()
        doc = s.build_edited_document()
        assert doc == "01 20 x1\n18 26 x1\n\n14 16 22 28\n三X0.5"


class TestPersistence:
    def test_save_load_round_trip(self):
        with tempfile.TemporaryDirectory() as td:
            s = _make_session()
            s.save(Path(td))
            loaded = AssistiveSession.load(s.session_id, Path(td))
            assert loaded.session_id == s.session_id
            assert loaded.raw_model_output == s.raw_model_output
            assert len(loaded.lines) == len(s.lines)
            assert loaded.lines[0].edited_text == s.lines[0].edited_text

    def test_save_uses_session_id_filename(self):
        with tempfile.TemporaryDirectory() as td:
            s = _make_session()
            path = s.save(Path(td))
            assert path.name == f"{s.session_id}.json"

    def test_atomic_write_no_temp_left(self):
        with tempfile.TemporaryDirectory() as td:
            s = _make_session()
            s.save(Path(td))
            leftovers = [f for f in os.listdir(td) if f.endswith(".tmp")]
            assert leftovers == []

    def test_no_credential_or_base64_in_json(self):
        with tempfile.TemporaryDirectory() as td:
            s = _make_session()
            s.save(Path(td))
            raw = (Path(td) / f"{s.session_id}.json").read_text("utf-8")
            data = json.loads(raw)
            assert "api_key" not in raw.lower()
            assert "access_token" not in raw.lower()
            assert "base64" not in raw.lower()
            # no image payload
            assert data.get("source_image_sha256") == "a" * 64

    def test_delete(self):
        with tempfile.TemporaryDirectory() as td:
            s = _make_session()
            path = s.save(Path(td))
            assert path.exists()
            s.delete()
            assert not path.exists()

    def test_corrupt_json_raises(self):
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "bad.json").write_text("{not json", encoding="utf-8")
            with pytest.raises(Exception):
                AssistiveSession.load("bad", Path(td))


class TestLineModel:
    def test_line_id_and_source(self):
        s = _make_session()
        first = s.lines[0]
        assert first.line_id == "L001"
        assert first.source_line_no == 1

    def test_set_warnings_updates_updated_at(self):
        s = _make_session()
        line = s.non_blank_lines()[0]
        before = line.updated_at
        line.set_warnings([{"code": "X", "severity": "WARNING"}])
        assert line.warnings[0]["code"] == "X"
        assert line.updated_at >= before
