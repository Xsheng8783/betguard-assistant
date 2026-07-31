"""Web UI license endpoint tests."""

import json
import os
from http.client import HTTPConnection

import pytest

from tests.test_webui_workbench import _free_port, _post_json, _running_server
from betguard.webui.app import build_workbench_handler


class TestLicenseEndpoints:
    def test_license_status_returns_200_json(self):
        handler = build_workbench_handler(project_version="test", git_commit="test")
        with _running_server(handler) as port:
            conn = HTTPConnection("127.0.0.1", port, timeout=5)
            conn.request("GET", "/license/status")
            resp = conn.getresponse()
            assert resp.status == 200
            data = json.loads(resp.read())
            assert data["ok"] is True
            assert data["status"] in ("active", "inactive", "expired")
            assert "device_code" in data
            conn.close()

    def test_license_activate_returns_json_on_invalid(self):
        handler = build_workbench_handler(project_version="test", git_commit="test")
        with _running_server(handler) as port:
            status, body = _post_json(port, "/license/activate", {
                "activation_code": "INVALID-CODE",
            })
            assert status == 200
            assert body["ok"] is False
            assert "error" in body

    def test_license_page_returns_200_html(self):
        handler = build_workbench_handler(project_version="test", git_commit="test")
        with _running_server(handler) as port:
            conn = HTTPConnection("127.0.0.1", port, timeout=5)
            conn.request("GET", "/license")
            resp = conn.getresponse()
            assert resp.status == 200
            html = resp.read().decode()
            assert "設備碼" in html
            assert "activation-code" in html
            conn.close()

    def test_dashboard_shows_license_status(self):
        handler = build_workbench_handler(project_version="test", git_commit="test")
        with _running_server(handler) as port:
            conn = HTTPConnection("127.0.0.1", port, timeout=5)
            conn.request("GET", "/")
            resp = conn.getresponse()
            assert resp.status == 200
            html = resp.read().decode()
            # The console HTML includes the license badge at the top of <body>
            assert "Betguard" in html
            conn.close()

    def test_assist_fill_blocked_when_no_license(self):
        # Clean up any existing license
        import os as _os
        from betguard.license import _LICENSE_FILE, save_license
        old_license = None
        if _os.path.exists(_LICENSE_FILE):
            with open(_LICENSE_FILE, "r") as f:
                old_license = f.read()
            _os.remove(_LICENSE_FILE)

        old = _os.environ.get("BETGUARD_SKIP_LICENSE")
        if "BETGUARD_SKIP_LICENSE" in _os.environ:
            del _os.environ["BETGUARD_SKIP_LICENSE"]
        try:
            handler = build_workbench_handler(project_version="test", git_commit="test")
            with _running_server(handler) as port:
                status, body = _post_json(port, "/assist-fill/start", {
                    "queue_path": "runs/nonexistent/queue.json",
                    "item_index": 0,
                })
                assert status == 200
                assert body["ok"] is False
                assert body.get("blocked") is True
                assert body.get("auto_submit") is False
                assert body.get("auto_confirm") is False
                assert body.get("danger_buttons_clicked") == []
                assert "授權" in body.get("error", "")
        finally:
            if old is not None:
                _os.environ["BETGUARD_SKIP_LICENSE"] = old
            if old_license is not None:
                with open(_LICENSE_FILE, "w") as f:
                    f.write(old_license)


class TestLicenseVisibleEntry:
    """Tests for the visible license entry feature (feature/license-visible-entry)."""

    def _cleanup_license(self):
        import os as _os
        from betguard.license import _LICENSE_FILE
        self._old_license = None
        if _os.path.exists(_LICENSE_FILE):
            with open(_LICENSE_FILE, "r", encoding="utf-8") as f:
                self._old_license = f.read()
            _os.remove(_LICENSE_FILE)
        self._old_skip = os.environ.get("BETGUARD_SKIP_LICENSE")
        if "BETGUARD_SKIP_LICENSE" in os.environ:
            del os.environ["BETGUARD_SKIP_LICENSE"]

    def _restore_license(self):
        from betguard.license import _LICENSE_FILE
        if self._old_skip is not None:
            os.environ["BETGUARD_SKIP_LICENSE"] = self._old_skip
        if self._old_license is not None:
            with open(_LICENSE_FILE, "w", encoding="utf-8") as f:
                f.write(self._old_license)

    def test_license_page_has_all_visible_elements(self):
        """GET /license returns 200 and the HTML includes device code, shared
        input, activation button, and a way back to the homepage."""
        handler = build_workbench_handler(project_version="test", git_commit="test")
        with _running_server(handler) as port:
            conn = HTTPConnection("127.0.0.1", port, timeout=5)
            conn.request("GET", "/license")
            resp = conn.getresponse()
            assert resp.status == 200
            html = resp.read().decode()
            conn.close()
            assert "設備碼" in html
            assert "BG-" in html
            assert "activation-code" in html
            assert "請輸入 BG7E / BG30E 或 BG7 / BG30 啟用碼" in html
            assert "啟用 Betguard" in html
            assert 'href="/"' in html
            assert "BG7E" in html or "BG7" in html

    def test_version_page_returns_200(self):
        """GET /version returns 200 with version info."""
        handler = build_workbench_handler(project_version="test", git_commit="test")
        with _running_server(handler) as port:
            conn = HTTPConnection("127.0.0.1", port, timeout=5)
            conn.request("GET", "/version")
            resp = conn.getresponse()
            assert resp.status == 200
            html = resp.read().decode()
            conn.close()
            assert "版本" in html
            assert "Commit" in html

    def test_license_page_title(self):
        handler = build_workbench_handler(project_version="test", git_commit="test")
        with _running_server(handler) as port:
            conn = HTTPConnection("127.0.0.1", port, timeout=5)
            conn.request("GET", "/license")
            resp = conn.getresponse()
            html = resp.read().decode()
            conn.close()
            assert "Betguard 牌單助手授權啟用" in html

    def test_license_page_has_single_shared_input(self):
        """One shared input — not two separate BG7/BG30 boxes."""
        handler = build_workbench_handler(project_version="test", git_commit="test")
        with _running_server(handler) as port:
            conn = HTTPConnection("127.0.0.1", port, timeout=5)
            conn.request("GET", "/license")
            resp = conn.getresponse()
            html = resp.read().decode()
            conn.close()
            assert html.count('id="activation-code"') == 1
            assert "請輸入 BG7E / BG30E 或 BG7 / BG30 啟用碼" in html

    def test_license_page_error_messages_in_page(self):
        """Error messages are rendered in-page (not alert-only)."""
        handler = build_workbench_handler(project_version="test", git_commit="test")
        with _running_server(handler) as port:
            conn = HTTPConnection("127.0.0.1", port, timeout=5)
            conn.request("GET", "/license")
            resp = conn.getresponse()
            html = resp.read().decode()
            conn.close()
            assert "啟用失敗" in html
            assert "伺服器連線失敗" in html
            assert "alert(" not in html

    def test_activate_with_valid_bg7_code(self):
        """Valid BG7 activation -> status active, plan trial_7d, expires_at set."""
        from betguard.license import (
            _device_id_hash, issue_license_code, license_status,
        )
        self._cleanup_license()
        try:
            code = issue_license_code(_device_id_hash(), 7, "trial_7d")
            assert code.startswith("BG7-")
            handler = build_workbench_handler(project_version="test", git_commit="test")
            with _running_server(handler) as port:
                status, body = _post_json(port, "/license/activate", {
                    "activation_code": code,
                })
                assert status == 200
                assert body["ok"] is True
                assert body["status"] == "active"
                assert body["plan"] == "trial_7d"
                assert body.get("expires_at")
                st = license_status()
                assert st["status"] == "active"
                assert st["plan"] == "trial_7d"
                assert st.get("expires_at")
        finally:
            self._restore_license()

    def test_activate_with_valid_bg30_code(self):
        """Valid BG30 activation -> status active, plan trial_30d, expires_at set."""
        from betguard.license import (
            _device_id_hash, issue_license_code, license_status,
        )
        self._cleanup_license()
        try:
            code = issue_license_code(_device_id_hash(), 30, "trial_30d")
            assert code.startswith("BG30-")
            handler = build_workbench_handler(project_version="test", git_commit="test")
            with _running_server(handler) as port:
                status, body = _post_json(port, "/license/activate", {
                    "activation_code": code,
                })
                assert status == 200
                assert body["ok"] is True
                assert body["status"] == "active"
                assert body["plan"] == "trial_30d"
                assert body.get("expires_at")
                st = license_status()
                assert st["status"] == "active"
                assert st["plan"] == "trial_30d"
                assert st.get("expires_at")
        finally:
            self._restore_license()

    def test_activate_with_invalid_format(self):
        """Invalid format shows a clear error."""
        self._cleanup_license()
        try:
            handler = build_workbench_handler(project_version="test", git_commit="test")
            with _running_server(handler) as port:
                status, body = _post_json(port, "/license/activate", {
                    "activation_code": "NOT-A-CODE-AT-ALL",
                })
                assert status == 200
                assert body["ok"] is False
                assert body.get("error")
        finally:
            self._restore_license()

    def test_activate_with_foreign_device_code(self):
        """A code issued for a different device shows 'not this device' error.

        Uses the v1 legacy hex format (signature does not bind the device
        hash), so the request reaches the device-binding check. v2 compact
        codes are HMAC-bound to the device hash and are rejected earlier at
        the signature check by design — that is existing behavior, unchanged.
        """
        import hashlib
        import json as _json
        from datetime import datetime, timedelta, timezone
        from betguard.license import _sign
        self._cleanup_license()
        try:
            foreign_hash = "f" * 64  # not this machine's hash
            payload = {
                "device_id_hash": foreign_hash,
                "expires_at": (datetime.now(timezone.utc) + timedelta(days=7)).isoformat(),
                "plan": "trial_7d",
                "issued_at": datetime.now(timezone.utc).isoformat(),
            }
            raw = _json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()
            sig = bytes.fromhex(_sign(payload))
            code = (raw + sig).hex().upper()
            handler = build_workbench_handler(project_version="test", git_commit="test")
            with _running_server(handler) as port:
                status, body = _post_json(port, "/license/activate", {
                    "activation_code": code,
                })
                assert status == 200
                assert body["ok"] is False
                assert "本裝置" in body.get("error", "")
        finally:
            self._restore_license()

    def test_dashboard_has_license_entry(self):
        """Homepage contains the license entry block linking to /license."""
        self._cleanup_license()
        try:
            handler = build_workbench_handler(project_version="test", git_commit="test")
            with _running_server(handler) as port:
                conn = HTTPConnection("127.0.0.1", port, timeout=5)
                conn.request("GET", "/")
                resp = conn.getresponse()
                html = resp.read().decode()
                conn.close()
                assert 'href="/license"' in html
                assert "尚未啟用" in html or "授權有效" in html or "授權已到期" in html
        finally:
            self._restore_license()

    def test_dashboard_license_entry_active_shows_plan(self):
        """When active, the dashboard entry shows plan + expiry + info link."""
        from betguard.license import (
            _device_id_hash, issue_license_code, save_license,
            verify_activation_code,
        )
        self._cleanup_license()
        try:
            code = issue_license_code(_device_id_hash(), 7, "trial_7d")
            result = verify_activation_code(code)
            assert result["ok"] is True
            save_license(result["payload"])
            handler = build_workbench_handler(project_version="test", git_commit="test")
            with _running_server(handler) as port:
                conn = HTTPConnection("127.0.0.1", port, timeout=5)
                conn.request("GET", "/")
                resp = conn.getresponse()
                html = resp.read().decode()
                conn.close()
                assert "授權有效" in html
                assert "7 天方案" in html
                assert 'href="/license"' in html
        finally:
            self._restore_license()
