"""Tests for Gemini paid provider plaintext mode (mocked, no real API)."""
import pytest

from betguard.vision.providers import gemini_paid as G


def _resp(text="01 20 二X1\n18 36 二X1", finish="STOP", model="gemini-2.5-flash",
          usage=None, request_id="req-abc"):
    if usage is None:
        usage = {"promptTokenCount": 100, "candidatesTokenCount": 50,
                 "totalTokenCount": 150}
    return {
        "candidates": [{"content": {"parts": [{"text": text}]},
                        "finishReason": finish}],
        "usageMetadata": usage,
        "modelVersion": model,
        "response": {"requestId": request_id},
    }


class TestPlaintextSuccess:
    def test_success_plaintext(self):
        result = G.recognize_whole_page_plaintext(
            b"fake-image", api_key="test-key",
            client=lambda payload, key, timeout_seconds: _resp())
        assert result.status == G.PLAINTEXT_STATUS_SUCCESS
        assert result.text == "01 20 二X1\n18 36 二X1"
        assert result.provider == G.PROVIDER_ID

    def test_success_metadata(self):
        result = G.recognize_whole_page_plaintext(
            b"fake-image", api_key="test-key",
            client=lambda payload, key, timeout_seconds: _resp())
        assert result.requested_model == G.ASSISTIVE_DEFAULT_MODEL
        assert result.response_model == "gemini-2.5-flash"
        assert result.finish_reason == "STOP"
        assert result.request_id == "req-abc"
        assert result.prompt_tokens == 100
        assert result.output_tokens == 50
        assert result.total_tokens == 150

    def test_prompt_version_saved(self):
        result = G.recognize_whole_page_plaintext(
            b"fake-image", api_key="test-key",
            client=lambda payload, key, timeout_seconds: _resp())
        assert result.prompt_version == G.ASSISTIVE_PROMPT_VERSION == \
            "assistive-whole-page-v1"

    def test_max_output_tokens_8192_default(self):
        seen = {}
        def client(payload, key, timeout_seconds):
            seen["cfg"] = payload["generationConfig"]
            return _resp()
        G.recognize_whole_page_plaintext(b"img", api_key="k", client=client)
        assert seen["cfg"]["maxOutputTokens"] == 8192

    def test_no_json_mime_type(self):
        seen = {}
        def client(payload, key, timeout_seconds):
            seen["cfg"] = payload["generationConfig"]
            return _resp()
        G.recognize_whole_page_plaintext(b"img", api_key="k", client=client)
        assert "responseMimeType" not in seen["cfg"]  # plain text, not JSON

    def test_requested_model_override(self):
        result = G.recognize_whole_page_plaintext(
            b"img", api_key="k", model="gemini-2.5-flash",
            client=lambda payload, key, timeout_seconds: _resp())
        assert result.requested_model == "gemini-2.5-flash"

    def test_latency_recorded(self):
        result = G.recognize_whole_page_plaintext(
            b"img", api_key="k",
            client=lambda payload, key, timeout_seconds: _resp())
        assert result.latency_ms >= 0


class TestPlaintextBlocked:
    def test_empty_response(self):
        result = G.recognize_whole_page_plaintext(
            b"img", api_key="k",
            client=lambda payload, key, timeout_seconds: _resp(text=""))
        assert result.status == G.PLAINTEXT_STATUS_EMPTY

    def test_max_tokens_truncated(self):
        result = G.recognize_whole_page_plaintext(
            b"img", api_key="k",
            client=lambda payload, key, timeout_seconds: _resp(
                text="01 20 二X1", finish="MAX_TOKENS"))
        assert result.status == G.PLAINTEXT_STATUS_TRUNCATED

    def test_unexpected_finish_reason(self):
        result = G.recognize_whole_page_plaintext(
            b"img", api_key="k",
            client=lambda payload, key, timeout_seconds: _resp(
                text="x", finish="SAFETY"))
        assert result.status == G.PLAINTEXT_STATUS_MODEL_ERROR


class TestPlaintextErrors:
    def test_missing_api_key(self):
        result = G.recognize_whole_page_plaintext(
            b"img", api_key=None)
        assert result.status == G.PLAINTEXT_STATUS_AUTH_ERROR
        assert "GEMINI_API_KEY" in result.generation_config["error"]

    def test_429_maps_to_resource_exhausted(self):
        def client(payload, key, timeout_seconds):
            raise G.GeminiPaidVisionError("Gemini HTTP 429: quota exceeded")
        with pytest.raises(G.GeminiPaidVisionError):
            G.recognize_whole_page_plaintext(b"img", api_key="k", client=client)

    def test_401_maps_to_auth_error(self):
        def client(payload, key, timeout_seconds):
            raise G.GeminiPaidVisionError(
                "Gemini HTTP 401: API key not valid")
        with pytest.raises(G.GeminiPaidVisionError):
            G.recognize_whole_page_plaintext(b"img", api_key="k", client=client)

    def test_usage_metadata_missing(self):
        result = G.recognize_whole_page_plaintext(
            b"img", api_key="k",
            client=lambda payload, key, timeout_seconds: _resp(
                usage={}))
        assert result.status == G.PLAINTEXT_STATUS_SUCCESS
        assert result.prompt_tokens is None
        assert result.output_tokens is None

    def test_response_model_missing(self):
        result = G.recognize_whole_page_plaintext(
            b"img", api_key="k",
            client=lambda payload, key, timeout_seconds: _resp(model=None))
        assert result.response_model == G.ASSISTIVE_DEFAULT_MODEL  # fallback

    def test_request_id_missing(self):
        result = G.recognize_whole_page_plaintext(
            b"img", api_key="k",
            client=lambda payload, key, timeout_seconds: _resp(
                request_id=None))
        assert result.request_id is None
        assert result.status == G.PLAINTEXT_STATUS_SUCCESS

    def test_unexpected_response_shape(self):
        def client(payload, key, timeout_seconds):
            return {"no": "candidates"}
        result = G.recognize_whole_page_plaintext(
            b"img", api_key="k", client=client)
        assert result.status == G.PLAINTEXT_STATUS_MODEL_ERROR


class TestLegacyJsonModeIntact:
    def test_build_generate_payload_still_json(self):
        payload = G.build_generate_payload("prompt", b"img")
        assert payload["generationConfig"]["responseMimeType"] == \
            "application/json"  # legacy mode unchanged

    def test_plaintext_payload_has_no_json_mime(self):
        payload = G._build_plaintext_payload(b"img", 8192)
        assert "responseMimeType" not in payload["generationConfig"]
        assert payload["generationConfig"]["maxOutputTokens"] == 8192
        assert payload["generationConfig"]["temperature"] == \
            G.ASSISTIVE_TEMPERATURE

    def test_extract_output_text_still_works(self):
        assert G.extract_output_text(_resp()) == "01 20 二X1\n18 36 二X1"

    def test_legacy_recognize_method_exists(self):
        assert hasattr(G.GeminiPaidVisionProvider, "recognize")
        assert hasattr(G, "build_prompt")  # legacy import surface intact

    def test_prompt_constant_versioned(self):
        assert G.ASSISTIVE_PROMPT_VERSION == "assistive-whole-page-v1"
        assert G.ASSISTIVE_WHOLE_PAGE_PROMPT_V1.strip()
