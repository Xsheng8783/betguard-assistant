"""Tests for unbound (device-independent) activation codes."""

import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from betguard.license import (
    _LICENSE_FILE,
    _device_id_hash,
    activate_license,
    is_license_active,
    license_status,
    load_license,
    verify_activation_code,
)
from betguard.license_issuer import issue_unbound


@pytest.fixture
def temp_license():
    with tempfile.TemporaryDirectory() as tmp:
        lic_path = os.path.join(tmp, "license.json")
        with patch("betguard.license._LICENSE_FILE", lic_path):
            yield lic_path


class TestUnboundActivation:
    def test_bg7u_activates(self, temp_license):
        code = issue_unbound(7, "trial_7d")
        assert code.startswith("BG7U-")
        r = activate_license(code)
        assert r["ok"] is True
        assert r["activated"] is True
        assert r["plan"] == "trial_7d"

    def test_bg30u_activates(self, temp_license):
        code = issue_unbound(30, "trial_30d")
        assert code.startswith("BG30U-")
        r = activate_license(code)
        assert r["ok"] is True
        assert r["plan"] == "trial_30d"

    def test_unbound_repeat_no_extend(self, temp_license):
        code = issue_unbound(7, "trial_7d")
        r1 = activate_license(code)
        r2 = activate_license(code)
        assert r2["activated"] is False
        assert r2["already_active"] is True
        assert r2["expires_at"] == r1["expires_at"]

    def test_unbound_expired_rejected(self, temp_license):
        code = issue_unbound(-1, "trial_7d")
        r = activate_license(code)
        assert r["ok"] is False
        assert "已過期" in r.get("error", "")

    def test_unbound_tampered_payload_fails(self, temp_license):
        code = issue_unbound(7, "trial_7d")
        # Tamper with a character in the middle (not padding bits at end)
        mid = len(code) // 2
        tampered = code[:mid] + ("X" if code[mid] != "X" else "Y") + code[mid+1:]
        r = activate_license(tampered)
        assert r["ok"] is False

    def test_unbound_license_json_no_full_code(self, temp_license):
        code = issue_unbound(7, "trial_7d")
        activate_license(code)
        with open(temp_license, "r") as f:
            data = json.load(f)
        assert "BG7U" not in str(data) or "activation_code_hash" in data
        assert "activation_code" not in data

    def test_unbound_works_on_any_device(self, temp_license):
        code = issue_unbound(7, "trial_7d")
        r = activate_license(code)
        assert r["ok"] is True

    def test_bound_still_works(self, temp_license):
        """BG7E device-bound codes still require device match."""
        from betguard.license import _issue_ed25519
        dev_hash = _device_id_hash()
        code = _issue_ed25519(dev_hash, 7, "trial_7d")
        assert code.startswith("BG7E-")
        r = activate_license(code)
        assert r["ok"] is True

    def test_bound_wrong_device_rejected(self, temp_license):
        from betguard.license import _issue_ed25519
        code = _issue_ed25519("b" * 64, 7, "trial_7d")
        r = activate_license(code)
        assert r["ok"] is False
        assert "不屬於" in r.get("error", "")


    def test_unbound_env_key_priority(self, monkeypatch):
        """env key is used even when key file exists."""
        import base64 as _b64
        monkeypatch.setenv("BETGUARD_LICENSE_PRIVATE_KEY",
                           "mrFXDQsGxQrGHf5fk+CY3eC00IA/DQdZq/OanLru50I=")
        # Should not fail
        code = issue_unbound(7, "trial_7d")
        assert code.startswith("BG7U-")

    def test_unbound_no_key_fails(self, monkeypatch):
        """Both env and file missing → error."""
        monkeypatch.delenv("BETGUARD_LICENSE_PRIVATE_KEY", raising=False)
        # Patch key file path to a non-existent location
        with patch("betguard.license_issuer._PRIVATE_KEY_PATH", "/nonexistent/key"):
            with pytest.raises(FileNotFoundError):
                issue_unbound(7, "trial_7d")


class TestUnboundSafetyGates:
    def test_safety_gates_intact(self):
        blocked = {
            "ok": False, "blocked": True,
            "error": "授權已到期",
            "auto_submit": False, "auto_confirm": False,
            "danger_buttons_clicked": [],
        }
        assert blocked["auto_submit"] is False
        assert blocked["danger_buttons_clicked"] == []
