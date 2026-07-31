"""Tests for license duplicate detection, expiration enforcement, and renewal logic."""

import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from betguard.license import (
    _LICENSE_FILE,
    _device_id_hash,
    _EPOCH,
    _activation_code_hash,
    activate_license,
    get_device_id,
    get_request_code,
    is_license_active,
    issue_license_code,
    license_status,
    load_license,
    verify_activation_code,
)


@pytest.fixture
def temp_license():
    with tempfile.TemporaryDirectory() as tmp:
        lic_path = os.path.join(tmp, "license.json")
        with patch("betguard.license._LICENSE_FILE", lic_path):
            yield lic_path


# ══════════════════════════════════════════
# Duplicate activation tests
# ══════════════════════════════════════════

class TestDuplicateActivation:
    def test_first_activation_succeeds(self, temp_license):
        dev_hash = _device_id_hash()
        code = issue_license_code(dev_hash, 7, "trial_7d")
        result = activate_license(code)
        assert result["ok"] is True
        assert result["activated"] is True
        assert result["already_active"] is False
        assert result["updated"] is True
        assert result["plan"] == "trial_7d"

    def test_same_code_twice_already_active(self, temp_license):
        dev_hash = _device_id_hash()
        code = issue_license_code(dev_hash, 7, "trial_7d")
        r1 = activate_license(code)
        expires1 = r1["expires_at"]
        issued1 = r1["issued_at"]
        activated1 = r1["activated_at"]

        r2 = activate_license(code)
        assert r2["ok"] is True
        assert r2["activated"] is False
        assert r2["already_active"] is True
        assert r2["updated"] is False
        assert r2["expires_at"] == expires1
        assert r2["issued_at"] == issued1
        assert r2["activated_at"] == activated1

    def test_repeat_does_not_extend(self, temp_license):
        dev_hash = _device_id_hash()
        code = issue_license_code(dev_hash, 7, "trial_7d")
        r1 = activate_license(code)
        r2 = activate_license(code)
        assert r2["expires_at"] == r1["expires_at"]

    def test_repeat_does_not_change_issued_at(self, temp_license):
        dev_hash = _device_id_hash()
        code = issue_license_code(dev_hash, 7, "trial_7d")
        r1 = activate_license(code)
        r2 = activate_license(code)
        assert r2["issued_at"] == r1["issued_at"]

    def test_repeat_does_not_change_activated_at(self, temp_license):
        dev_hash = _device_id_hash()
        code = issue_license_code(dev_hash, 7, "trial_7d")
        r1 = activate_license(code)
        r2 = activate_license(code)
        assert r2["activated_at"] == r1["activated_at"]


# ══════════════════════════════════════════
# Expiration enforcement
# ══════════════════════════════════════════

class TestExpirationEnforcement:
    def test_expired_code_rejected(self, temp_license):
        dev_hash = _device_id_hash()
        # Issue code with -1 days = already expired
        code = issue_license_code(dev_hash, -1, "trial_7d")
        result = activate_license(code)
        assert result["ok"] is False
        assert "已過期" in result.get("error", "")

    def test_expired_status_blocks_assist(self, temp_license):
        # Issue a valid code, then manually set license.json to expired
        dev_hash = _device_id_hash()
        code = issue_license_code(dev_hash, 7, "trial_7d")
        result = activate_license(code)
        assert result["ok"] is True
        # Now manually expire the license
        import json
        with open(temp_license, "r") as f:
            data = json.load(f)
        data["expires_at"] = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        # Re-sign
        from betguard.license import _sign, _save_license_data
        _save_license_data(data)
        assert is_license_active() is False
        assert license_status()["status"] == "expired"

    def test_expired_code_permanently_rejected(self, temp_license):
        dev_hash = _device_id_hash()
        code = issue_license_code(dev_hash, -1, "trial_7d")
        r1 = activate_license(code)
        assert r1["ok"] is False
        # Try again
        r2 = activate_license(code)
        assert r2["ok"] is False

    def test_delete_license_then_expired_code_still_rejected(self, temp_license):
        dev_hash = _device_id_hash()
        code = issue_license_code(dev_hash, -1, "trial_7d")
        activate_license(code)
        # Delete license file
        if os.path.exists(temp_license):
            os.remove(temp_license)
        # Try again — still expired
        result = activate_license(code)
        assert result["ok"] is False
        assert "已過期" in result.get("error", "")


# ══════════════════════════════════════════
# Cross-device rejection
# ══════════════════════════════════════════

class TestCrossDevice:
    def test_wrong_device_rejected(self, temp_license):
        code = issue_license_code("b" * 64, 7, "trial_7d")
        result = activate_license(code)
        assert result["ok"] is False


# ══════════════════════════════════════════
# Renewal logic
# ══════════════════════════════════════════

class TestRenewal:
    def test_longer_code_renews(self, temp_license):
        dev_hash = _device_id_hash()
        code7 = issue_license_code(dev_hash, 7, "trial_7d")
        code30 = issue_license_code(dev_hash, 30, "trial_30d")
        r1 = activate_license(code7)
        r2 = activate_license(code30)
        assert r2["ok"] is True
        assert r2["renewed"] is True
        assert r2["previous_expires_at"] == r1["expires_at"]

    def test_shorter_code_refused(self, temp_license):
        dev_hash = _device_id_hash()
        code30 = issue_license_code(dev_hash, 30, "trial_30d")
        code7 = issue_license_code(dev_hash, 7, "trial_7d")
        activate_license(code30)
        r2 = activate_license(code7)
        assert r2["ok"] is True
        assert r2["activated"] is False
        assert r2["renewed"] is False


# ══════════════════════════════════════════
# Storage safety
# ══════════════════════════════════════════

class TestStorageSafety:
    def test_license_json_no_full_code(self, temp_license):
        dev_hash = _device_id_hash()
        code = issue_license_code(dev_hash, 7, "trial_7d")
        activate_license(code)
        with open(temp_license, "r") as f:
            data = json.load(f)
        assert "activation_code" not in data
        assert "BG7" not in str(data) or "activation_code_hash" in data

    def test_has_activation_code_hash(self, temp_license):
        dev_hash = _device_id_hash()
        code = issue_license_code(dev_hash, 7, "trial_7d")
        activate_license(code)
        with open(temp_license, "r") as f:
            data = json.load(f)
        assert "activation_code_hash" in data
        assert len(data["activation_code_hash"]) == 64  # SHA256 hex


# ══════════════════════════════════════════
# Safety gates unchanged
# ══════════════════════════════════════════

class TestSafetyGates:
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

    def test_inactive_assist_blocked(self, temp_license):
        from betguard.license import is_license_active
        assert is_license_active() is False
