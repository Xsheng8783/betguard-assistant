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
