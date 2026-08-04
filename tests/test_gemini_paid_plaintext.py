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


def _noop_sleep(seconds):
    """Injected sleep_fn — tests must never actually sleep."""
    raise AssertionError("sleep_fn must not be called in this test")


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


class TestPlaintextRetry:
    def test_429_then_success_returns_success(self):
        """First call 429, second succeeds -> SUCCESS, response preserved."""
        calls = {"n": 0}
        slept = []

        def client(payload, key, timeout_seconds):
            calls["n"] += 1
            if calls["n"] == 1:
                raise G.GeminiPaidVisionError("Gemini HTTP 429: quota")
            return _resp()

        def sleeper(seconds):
            slept.append(seconds)

        result = G.recognize_whole_page_plaintext(
            b"img", api_key="k", client=client, sleep_fn=sleeper)
        assert calls["n"] == 2
        assert slept  # backoff happened
        assert result.status == G.PLAINTEXT_STATUS_SUCCESS
        assert result.text == "01 20 二X1\n18 36 二X1"  # success kept

    def test_three_429_returns_resource_exhausted(self):
        """Three consecutive 429s -> RESOURCE_EXHAUSTED, NO exception."""
        slept = []

        def client(payload, key, timeout_seconds):
            raise G.GeminiPaidVisionError("Gemini HTTP 429: quota")

        def sleeper(seconds):
            slept.append(seconds)

        result = G.recognize_whole_page_plaintext(
            b"img", api_key="k", client=client, sleep_fn=sleeper)
        assert result.status == G.PLAINTEXT_STATUS_RATE_LIMITED
        assert len(slept) == 2  # two backoffs between three attempts
        assert "429" in result.generation_config["error"]


class TestPlaintextErrors:
    def test_missing_api_key(self):
        result = G.recognize_whole_page_plaintext(
            b"img", api_key=None)
        assert result.status == G.PLAINTEXT_STATUS_AUTH_ERROR
        assert "GEMINI_API_KEY" in result.generation_config["error"]

    def test_401_auth_error(self):
        def client(payload, key, timeout_seconds):
            raise G.GeminiPaidVisionError(
                "Gemini HTTP 401: API key not valid")
        result = G.recognize_whole_page_plaintext(
            b"img", api_key="k", client=client)
        assert result.status == G.PLAINTEXT_STATUS_AUTH_ERROR
        assert result.text == ""

    def test_403_auth_error(self):
        def client(payload, key, timeout_seconds):
            raise G.GeminiPaidVisionError(
                "Gemini HTTP 403: permission denied")
        result = G.recognize_whole_page_plaintext(
            b"img", api_key="k", client=client)
        assert result.status == G.PLAINTEXT_STATUS_AUTH_ERROR

    def test_404_auth_or_config_error(self):
        def client(payload, key, timeout_seconds):
            raise G.GeminiPaidVisionError(
                "Gemini HTTP 404: model not found")
        result = G.recognize_whole_page_plaintext(
            b"img", api_key="k", client=client)
        assert result.status == G.PLAINTEXT_STATUS_AUTH_ERROR

    def test_network_error_blocked_model_response(self):
        def client(payload, key, timeout_seconds):
            raise G.GeminiPaidVisionError("Gemini network error: timeout")
        result = G.recognize_whole_page_plaintext(
            b"img", api_key="k", client=client)
        assert result.status == G.PLAINTEXT_STATUS_MODEL_ERROR

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


class TestPlaintextMime:
    def test_jpeg_uses_image_jpeg(self):
        seen = {}
        def client(payload, key, timeout_seconds):
            seen["mime"] = payload["contents"][0]["parts"][1][
                "inline_data"]["mime_type"]
            return _resp()
        G.recognize_whole_page_plaintext(
            b"\xff\xd8\xff", api_key="k", client=client,
            mime_type="image/jpeg")
        assert seen["mime"] == "image/jpeg"

    def test_png_uses_image_png(self):
        seen = {}
        def client(payload, key, timeout_seconds):
            seen["mime"] = payload["contents"][0]["parts"][1][
                "inline_data"]["mime_type"]
            return _resp()
        G.recognize_whole_page_plaintext(
            b"\x89PNG", api_key="k", client=client,
            mime_type="image/png")
        assert seen["mime"] == "image/png"

    def test_webp_uses_image_webp(self):
        seen = {}
        def client(payload, key, timeout_seconds):
            seen["mime"] = payload["contents"][0]["parts"][1][
                "inline_data"]["mime_type"]
            return _resp()
        G.recognize_whole_page_plaintext(
            b"RIFFWEBP", api_key="k", client=client,
            mime_type="image/webp")
        assert seen["mime"] == "image/webp"

    def test_unsupported_mime_clear_error(self):
        result = G.recognize_whole_page_plaintext(
            b"img", api_key="k", mime_type="image/gif")
        assert result.status == G.PLAINTEXT_STATUS_AUTH_ERROR
        assert "unsupported mime_type" in result.generation_config["error"]
        assert "image/png" in result.generation_config["error"]


class TestPayloadImmutability:
    def test_post_generate_does_not_mutate_payload(self):
        payload = {"contents": [{"parts": [{"text": "x"}]}],
                   "_model": "gemini-2.5-flash"}
        snapshot = json_dumps(payload)

        import urllib.request
        import urllib.error
        original_urlopen = urllib.request.urlopen

        class _FakeResp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return b'{"candidates": [{"content": {"parts": [{"text": "ok"}]}, "finishReason": "STOP"}]}'

            def decode(self, enc=None, errors=None):
                return b'{"candidates": [{"content": {"parts": [{"text": "ok"}]}, "finishReason": "STOP"}]}'

        def fake_urlopen(req, timeout=None):
            return _FakeResp()

        urllib.request.urlopen = fake_urlopen
        try:
            result = G._post_generate(payload, "key", 30)
            assert result["candidates"][0]["content"]["parts"][0]["text"] == "ok"
        finally:
            urllib.request.urlopen = original_urlopen
        # original payload unchanged
        assert json_dumps(payload) == snapshot
        assert "_model" in payload  # still there, not popped

    def test_429_retry_uses_specified_model(self):
        """Retries after 429 keep using gemini-2.5-flash and the original
        payload is never mutated."""
        seen_urls = []
        calls = {"n": 0}

        def client(payload, key, timeout_seconds):
            calls["n"] += 1
            if calls["n"] == 1:
                raise G.GeminiPaidVisionError("Gemini HTTP 429: quota")
            return _resp()

        def sleeper(seconds):
            pass

        result = G.recognize_whole_page_plaintext(
            b"img", api_key="k", model="gemini-2.5-flash",
            client=client, sleep_fn=sleeper)
        assert result.status == G.PLAINTEXT_STATUS_SUCCESS
        assert result.requested_model == "gemini-2.5-flash"
        assert calls["n"] == 2


def json_dumps(obj):
    import json
    return json.dumps(obj, sort_keys=True)


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
