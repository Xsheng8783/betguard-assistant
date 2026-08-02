"""Test FakeProvider and its fixtures."""

from __future__ import annotations

import sys
import uuid

import pytest

from betguard.vision.contracts import (
    RecognitionRequest,
    RecognitionResult,
    RecognitionStatus,
)
from betguard.vision.errors import ErrorCode
from betguard.vision.providers.fake import (
    FakeProvider,
    bet_slip_fixture,
    multi_line_fixture,
    no_confidence_fixture,
)


def _make_request() -> RecognitionRequest:
    return RecognitionRequest(
        request_id=f"req-{uuid.uuid4().hex[:8]}",
        image_id="test-img-001",
    )


# ── Fixture validation ───────────────────────────────────────────────────────


class TestBetSlipFixture:
    """The main bet slip fixture must match the specification."""

    def test_has_nine_lines(self):
        r = bet_slip_fixture()
        assert len(r.lines) == 9

    def test_status_is_completed(self):
        r = bet_slip_fixture()
        assert r.status == RecognitionStatus.COMPLETED

    def test_raw_text_not_empty(self):
        r = bet_slip_fixture()
        assert "05" in r.raw_text
        assert "39" in r.raw_text

    def test_token_19_has_alternatives(self):
        r = bet_slip_fixture()
        # Line 2 (order=2) has token "19" with alternatives
        line2 = r.lines[1]  # zero-indexed
        tok_19 = [t for t in line2.tokens if t.text == "19"]
        assert len(tok_19) == 1
        alts = tok_19[0].alternatives
        assert len(alts) >= 2
        alt_texts = {a.text for a in alts}
        assert "18" in alt_texts
        assert "17" in alt_texts

    def test_token_29_has_alternatives(self):
        r = bet_slip_fixture()
        line3 = r.lines[2]  # order=3
        tok_29 = [t for t in line3.tokens if t.text == "29"]
        assert len(tok_29) == 1
        alt_texts = {a.text for a in tok_29[0].alternatives}
        assert "27" in alt_texts
        assert "39" in alt_texts

    def test_multiply_token_has_alternatives(self):
        r = bet_slip_fixture()
        line6 = r.lines[5]  # order=6, "2×5"
        tok_x = [t for t in line6.tokens if t.text == "×"]
        assert len(tok_x) == 1
        alt_texts = {a.text for a in tok_x[0].alternatives}
        assert "X" in alt_texts
        assert "-" in alt_texts

    def test_ge2che_token_has_alternatives(self):
        r = bet_slip_fixture()
        line8 = r.lines[7]  # order=8, "25 各2車"
        tok_suffix = [t for t in line8.tokens if t.text == "各2車"]
        assert len(tok_suffix) == 1
        alt_texts = {a.text for a in tok_suffix[0].alternatives}
        assert "各2碰" in alt_texts
        assert "各2連" in alt_texts

    def test_low_confidence_on_ambiguous_tokens(self):
        r = bet_slip_fixture()
        line2 = r.lines[1]
        tok_19 = [t for t in line2.tokens if t.text == "19"][0]
        assert tok_19.confidence.level.value == "low"

    def test_high_confidence_on_clear_tokens(self):
        r = bet_slip_fixture()
        line1 = r.lines[0]  # "05    09"
        tok_05 = [t for t in line1.tokens if t.text == "05"][0]
        assert tok_05.confidence.level.value == "high"

    def test_no_confidence_fixture_all_null(self):
        r = no_confidence_fixture()
        for line in r.lines:
            assert line.confidence.value is None
            for token in line.tokens:
                assert token.confidence.value is None

    def test_multi_line_fixture_has_three_lines(self):
        r = multi_line_fixture()
        assert len(r.lines) == 3
        assert r.lines[0].bounding_box is not None

    def test_all_fixtures_pass_validation(self):
        """Every fixture must pass RecognitionResult.validate()."""
        for fixture_fn in [bet_slip_fixture, no_confidence_fixture, multi_line_fixture]:
            r = fixture_fn()
            r.validate()  # must not raise


# ── FakeProvider modes ───────────────────────────────────────────────────────


class TestFakeProviderModes:
    def test_bet_slip_mode(self):
        provider = FakeProvider(mode=FakeProvider.MODE_BET_SLIP)
        result = provider.recognize(_make_request())
        assert result.status == RecognitionStatus.COMPLETED
        assert len(result.lines) == 9

    def test_timeout_mode(self):
        provider = FakeProvider(mode=FakeProvider.MODE_TIMEOUT)
        result = provider.recognize(_make_request())
        assert result.status == RecognitionStatus.FAILED
        assert result.provider_error is not None
        assert result.provider_error.code == ErrorCode.TIMEOUT
        assert result.provider_error.retryable is True

    def test_provider_error_mode(self):
        provider = FakeProvider(mode=FakeProvider.MODE_PROVIDER_ERROR)
        result = provider.recognize(_make_request())
        assert result.status == RecognitionStatus.FAILED
        assert result.provider_error is not None
        assert result.provider_error.code == ErrorCode.INTERNAL_ERROR
        assert result.provider_error.retryable is False

    def test_no_confidence_mode(self):
        provider = FakeProvider(mode=FakeProvider.MODE_NO_CONFIDENCE)
        result = provider.recognize(_make_request())
        for line in result.lines:
            assert line.confidence.value is None

    def test_multi_line_mode(self):
        provider = FakeProvider(mode=FakeProvider.MODE_MULTI_LINE)
        result = provider.recognize(_make_request())
        assert len(result.lines) == 3

    def test_default_success_mode(self):
        provider = FakeProvider(mode=FakeProvider.MODE_SUCCESS)
        result = provider.recognize(_make_request())
        assert result.status == RecognitionStatus.COMPLETED

    def test_implements_protocol(self):
        """FakeProvider satisfies ImageRecognitionProvider protocol."""
        from betguard.vision.providers.base import ImageRecognitionProvider
        provider = FakeProvider()
        assert isinstance(provider, ImageRecognitionProvider)


# ── Isolation: no parser/validator/webfill/playwright imports ────────────────


class TestModuleIsolation:
    """Vision module must NOT import parser, validator, webfill, or Playwright.

    These checks verify source files do not contain prohibited imports.
    sys.modules checks are unreliable because other test files may already
    have loaded those modules.
    """

    _PROHIBITED = [
        "betguard.parser",
        "betguard.validator",
        "betguard.webfill",
        "betguard.normalizer",
        "playwright",
    ]

    def _check_source(self, file_path: str | None, label: str) -> None:
        if not file_path:
            pytest.skip(f"{label} source file not found")
        with open(file_path, encoding="utf-8") as f:
            content = f.read()
        for mod in self._PROHIBITED:
            # Check for "from mod import" or "import mod"
            assert f"from {mod}" not in content, f"{label} imports from {mod}"
            assert f"import {mod}" not in content, f"{label} imports {mod}"

    def test_contracts_no_prohibited_imports(self):
        import betguard.vision.contracts as c
        self._check_source(c.__file__, "contracts.py")

    def test_fake_provider_no_prohibited_imports(self):
        import betguard.vision.providers.fake as f
        self._check_source(f.__file__, "fake.py")

    def test_base_provider_no_prohibited_imports(self):
        import betguard.vision.providers.base as b
        self._check_source(b.__file__, "base.py")

    def test_errors_no_prohibited_imports(self):
        import betguard.vision.errors as e
        self._check_source(e.__file__, "errors.py")

    def test_init_no_prohibited_imports(self):
        import betguard.vision as v
        self._check_source(v.__file__, "__init__.py")
