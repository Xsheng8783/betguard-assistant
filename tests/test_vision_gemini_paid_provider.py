"""Tests for the Gemini paid vision provider (mock client only)."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from betguard.vision.contracts import RecognitionRequest
from betguard.vision.providers.gemini_paid import (
    DEFAULT_MODEL,
    GeminiPaidVisionProvider,
    build_generate_payload,
    cache_key,
    extract_output_text,
    has_api_key,
)


def _request(image_path: str, **meta) -> RecognitionRequest:
    return RecognitionRequest(
        request_id="r1",
        image_id="img1",
        image_path=image_path,
        metadata=meta,
    )


def _png_bytes(tmp_path: Path) -> Path:
    p = tmp_path / "sample.png"
    p.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)
    return p


def _model_output_json(**extra) -> str:
    data = {
        "schema_version": "1.1",
        "lines": [
            {
                "line_id": "L01",
                "entry_id": "E01",
                "region": "whole_page",
                "layout_hint": "normal_like",
                "number_groups": [["01", "20"]],
                "multiplier_text": "×1",
                "raw_text": "01 20 ×1",
                "alternatives": [],
                "uncertain": False,
                "uncertain_reason": None,
            }
        ],
    }
    data.update(extra)
    return json.dumps(data, ensure_ascii=False)


def _gemini_response(text: str) -> dict:
    return {
        "candidates": [
            {"content": {"parts": [{"text": text}]}}
        ]
    }


class TestHasApiKey:
    def test_false_without_env(self, monkeypatch):
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        assert has_api_key() is False

    def test_true_with_env(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "test-key")
        assert has_api_key() is True


class TestBuildPayload:
    def test_contains_prompt_and_inline_image(self, tmp_path):
        img = _png_bytes(tmp_path)
        payload = build_generate_payload("prompt", img.read_bytes())
        parts = payload["contents"][0]["parts"]
        assert parts[0]["text"] == "prompt"
        assert parts[1]["inline_data"]["mime_type"] == "image/png"
        assert parts[1]["inline_data"]["data"]  # base64 non-empty

    def test_jpeg_mime(self, tmp_path):
        from betguard.vision.providers.gemini_paid import _mime_for_path
        assert _mime_for_path("x.jpg") == "image/jpeg"
        assert _mime_for_path("x.jpeg") == "image/jpeg"
        assert _mime_for_path("x.webp") == "image/webp"
        assert _mime_for_path("x.png") == "image/png"


class TestExtract:
    def test_extracts_text(self):
        assert extract_output_text(_gemini_response("hello")) == "hello"

    def test_raises_on_bad_shape(self):
        with pytest.raises(Exception):
            extract_output_text({})


class TestRecognize:
    def test_no_api_key_returns_auth_failed(self, tmp_path, monkeypatch):
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        img = _png_bytes(tmp_path)
        provider = GeminiPaidVisionProvider()
        result = provider.recognize(_request(str(img)))
        assert "gemini-paid" in result.recognition_id
        assert result.provider_error.code == "AUTHENTICATION_FAILED"
        assert not result.lines

    def test_mock_success_pipeline(self, tmp_path, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "test-key")
        img = _png_bytes(tmp_path)

        def fake_client(payload, api_key, timeout_seconds=60):
            assert api_key == "test-key"
            assert payload["_model"] == DEFAULT_MODEL
            return _gemini_response(_model_output_json())

        provider = GeminiPaidVisionProvider(client=fake_client)
        result = provider.recognize(_request(str(img)))
        assert result.lines
        assert result.lines[0].text == "01 20 ×1"
        assert result.preprocessing["openai_lines"][0]["needs_human_confirmation"] is True
        assert result.preprocessing["human_confirmation_required"] is True
        assert result.preprocessing["auto_submit"] is False
        assert result.preprocessing["auto_confirm"] is False

    def test_invalid_schema_returns_failed(self, tmp_path, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "test-key")
        img = _png_bytes(tmp_path)

        def fake_client(payload, api_key, timeout_seconds=60):
            return _gemini_response('{"schema_version": "9.9"}')

        provider = GeminiPaidVisionProvider(client=fake_client)
        result = provider.recognize(_request(str(img)))
        assert result.provider_error.code == "GEMINI_RESPONSE_SCHEMA_INVALID"
        assert not result.lines

    def test_http_error_maps_rate_limited(self, tmp_path, monkeypatch):
        from betguard.vision.providers.gemini_paid import GeminiPaidVisionError
        monkeypatch.setenv("GEMINI_API_KEY", "test-key")
        img = _png_bytes(tmp_path)

        def fake_client(payload, api_key, timeout_seconds=60):
            raise GeminiPaidVisionError("Gemini HTTP 429: quota exceeded")

        provider = GeminiPaidVisionProvider(client=fake_client)
        result = provider.recognize(_request(str(img)))
        assert result.provider_error.code == "RATE_LIMITED"

    def test_cache_hit_uses_cache(self, tmp_path, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "test-key")
        img = _png_bytes(tmp_path)
        cache_dir = tmp_path / "cache"

        calls = {"n": 0}

        def fake_client(payload, api_key, timeout_seconds=60):
            calls["n"] += 1
            return _gemini_response(_model_output_json())

        provider = GeminiPaidVisionProvider(client=fake_client, cache_dir=cache_dir)
        provider.recognize(_request(str(img)))
        provider.recognize(_request(str(img)))
        assert calls["n"] == 1  # second call served from cache

    def test_cache_key_namespaced(self):
        assert cache_key("sha1", "gemini-2.5-flash").startswith("gemini-paid:sha1:")


class TestNoExternal:
    def test_no_openai_import(self):
        from betguard.vision.providers import gemini_paid as mod
        with open(mod.__file__, encoding="utf-8") as f:
            content = f.read()
        # must not depend on the OpenAI SDK; importing helpers from
        # openai_paid (our own module) is allowed but the OpenAI SDK import
        # must never be triggered by gemini_paid
        assert "import openai" not in content
        assert "from openai" not in content

    def test_provider_id(self):
        assert GeminiPaidVisionProvider.provider_id == "gemini-paid"
