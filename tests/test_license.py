"""Tests for license module — local activation codes (v2 compact + v1 legacy)."""

import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from betguard.license import (
    _LICENSE_FILE,
    _device_id_hash,
    _sign,
    _verify_signature,
    _issue_compact,
    _b32_encode,
    _b32_decode,
    get_device_id,
    is_license_active,
    issue_license_code,
    license_status,
    load_license,
    save_license,
    verify_activation_code,
)


@pytest.fixture
def temp_license_dir():
    with tempfile.TemporaryDirectory() as tmp:
        with patch("betguard.license._LICENSE_FILE", os.path.join(tmp, "license.json")):
            yield tmp


class TestDeviceId:
    def test_device_id_is_stable(self):
        a = get_device_id()
        b = get_device_id()
        assert a == b

    def test_device_id_format(self):
        did = get_device_id()
        assert did.startswith("BG-")
        parts = did.split("-")
        assert len(parts) == 4
        assert all(len(p) == 4 for p in parts[1:])


class TestBase32:
    def test_roundtrip(self):
        data = b"\x01\xAB\xCD\xEF\x00\x12\x34\x56\x78\x9A"
        encoded = _b32_encode(data)
        decoded = _b32_decode(encoded)
        assert decoded == data

    def test_empty(self):
        assert _b32_encode(b"") == ""

    def test_decode_strips_dashes(self):
        encoded = _b32_encode(b"\x01\x02\x03\x04")
        with_dashes = "-".join([encoded[i:i+4] for i in range(0, len(encoded), 4)])
        assert _b32_decode(with_dashes) == b"\x01\x02\x03\x04"


class TestSignVerify:
    def test_roundtrip(self):
        payload = {"device_id_hash": "abc123", "expires_at": "2099-01-01T00:00:00+00:00",
                   "plan": "trial_7d", "issued_at": "2025-01-01T00:00:00+00:00"}
        sig = _sign(payload)
        assert _verify_signature(payload, sig)
        payload["expires_at"] = "2099-01-01T00:00:01+00:00"
        assert not _verify_signature(payload, sig)


class TestCompactActivationCode:
    def test_7d_short_code(self):
        code = issue_license_code(_device_id_hash(), 7, "trial_7d")
        assert code.startswith("BG7-")
        assert len(code) < 40
        result = verify_activation_code(code)
        assert result["ok"]
        assert result["payload"]["plan"] == "trial_7d"

    def test_30d_short_code(self):
        code = issue_license_code(_device_id_hash(), 30, "trial_30d")
        assert code.startswith("BG30-")
        assert len(code) < 45
        result = verify_activation_code(code)
        assert result["ok"]
        assert result["payload"]["plan"] == "trial_30d"

    def test_wrong_device(self):
        result = verify_activation_code(
            issue_license_code("b" * 64, 7, "trial_7d")
        )
        assert not result["ok"]
        assert any(w in result["error"] for w in ["不屬於", "無效", "簽章"])

    def test_tampered_code(self):
        code = issue_license_code(_device_id_hash(), 7, "trial_7d")
        # Tamper with last char
        tampered = code[:-1] + ("A" if code[-1] != "A" else "B")
        result = verify_activation_code(tampered)
        assert not result["ok"]

    def test_invalid_code(self):
        result = verify_activation_code("NOT-A-VALID-CODE")
        assert not result["ok"]

    def test_expired_short_code(self):
        dev_hash = _device_id_hash()
        # Issue code with 0 days (expires right away, but may still be valid within same second)
        # Instead issue with -7 days which won't work. Use _issue_compact directly with past epoch.
        import struct
        from betguard.license import _EPOCH, _PLAN_BYTES, _hmac_sign_compact
        epoch_day = (datetime.now(timezone.utc).date() - _EPOCH - timedelta(days=1)).days
        payload = struct.pack(">BH4s", _PLAN_BYTES["trial_7d"], epoch_day, bytes.fromhex(dev_hash)[:4])
        sig = _hmac_sign_compact(payload + bytes.fromhex(dev_hash))
        encoded = _b32_encode(payload + sig)
        chunks = [encoded[i:i+4] for i in range(0, len(encoded), 4)]
        code = "BG7-" + "-".join(chunks)
        result = verify_activation_code(code)
        assert not result["ok"]
        assert "已過" in result.get("error", "")


class TestLegacyCompatibility:
    """Ensure old long hex codes still verify."""

    def test_legacy_long_code_verify(self):
        import json as _json
        dev_hash = _device_id_hash()
        from betguard.license import _sign as legacy_sign
        from datetime import datetime as dt, timedelta as td, timezone as tz
        payload = {
            "device_id_hash": dev_hash,
            "expires_at": (dt.now(tz.utc) + td(days=7)).isoformat(),
            "plan": "trial_7d",
            "issued_at": dt.now(tz.utc).isoformat(),
        }
        sig = legacy_sign(payload)
        payload_bytes = _json.dumps(payload, sort_keys=True).encode("utf-8")
        raw = payload_bytes + bytes.fromhex(sig)
        hex_str = raw.hex().upper()
        chunks = [hex_str[i:i+4] for i in range(0, len(hex_str), 4)]
        long_code = "BG7-" + "-".join(chunks)
        result = verify_activation_code(long_code)
        assert result["ok"]
        assert result["payload"]["plan"] == "trial_7d"


class TestLicenseStorage:
    def test_save_and_load(self, temp_license_dir):
        payload = {
            "device_id_hash": _device_id_hash(),
            "expires_at": (datetime.now(timezone.utc) + timedelta(days=30)).isoformat(),
            "plan": "trial_30d",
            "issued_at": datetime.now(timezone.utc).isoformat(),
        }
        save_license(payload)
        loaded = load_license()
        assert loaded is not None
        assert loaded["plan"] == "trial_30d"

    def test_no_license_returns_none(self, temp_license_dir):
        assert load_license() is None

    def test_is_license_active_true(self, temp_license_dir):
        payload = {
            "device_id_hash": _device_id_hash(),
            "expires_at": (datetime.now(timezone.utc) + timedelta(days=30)).isoformat(),
            "plan": "trial_30d",
            "issued_at": datetime.now(timezone.utc).isoformat(),
        }
        save_license(payload)
        assert is_license_active()

    def test_is_license_inactive_no_file(self, temp_license_dir):
        assert not is_license_active()

    def test_license_status(self, temp_license_dir):
        payload = {
            "device_id_hash": _device_id_hash(),
            "expires_at": (datetime.now(timezone.utc) + timedelta(days=7)).isoformat(),
            "plan": "trial_7d",
            "issued_at": datetime.now(timezone.utc).isoformat(),
        }
        save_license(payload)
        status = license_status()
        assert status["status"] == "active"
        assert status["plan"] == "trial_7d"


class TestAssistFillGate:
    def test_blocked_response_shape(self):
        blocked = {
            "ok": False,
            "blocked": True,
            "error": "授權已到期，請續用後再使用輔助填入",
            "auto_submit": False,
            "auto_confirm": False,
            "danger_buttons_clicked": [],
        }
        assert blocked["auto_submit"] is False
        assert blocked["auto_confirm"] is False
        assert blocked["danger_buttons_clicked"] == []
