"""Fixed loopback HTTP page for Local Sandbox Assisted Fill."""

from __future__ import annotations

import html
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Mapping

from betguard.webfill.local_sandbox_contracts import (
    LOCAL_SANDBOX_HOST,
    LOCAL_SANDBOX_PATH,
    LocalSandboxContractError,
    decode_public_execute_request,
    local_sandbox_profile,
)


EXECUTE_PATH = "/sandbox-fill/execute"


def render_sandbox_page(*, action_id: str = "", idempotency_key: str = "") -> str:
    """Render only fixed allowlisted fields; no bet values are embedded."""

    slots = []
    for index in range(1, local_sandbox_profile()["maximum_bets"] + 1):
        prefix = f"sandbox-bet-{index:03d}"
        slots.append(f"""
        <fieldset id="{prefix}" data-sandbox-bet-slot="{index}">
          <legend>Bet {index}</legend>
          <label>Human bet id <input id="{prefix}-human-bet-id" data-sandbox-field="human_bet_id"></label>
          <label>Bet type <select id="{prefix}-bet-type" data-sandbox-field="bet_type">
            <option value=""></option><option value="normal">normal</option><option value="column">column</option>
          </select></label>
          <label>Nested number groups <textarea id="{prefix}-number-groups" data-sandbox-field="number_groups"></textarea></label>
          <label>Multiplier <textarea id="{prefix}-multiplier" data-sandbox-field="multiplier"></textarea></label>
          <label>Continuation <textarea id="{prefix}-continuation" data-sandbox-field="continuation"></textarea></label>
          <label>Special play <textarea id="{prefix}-special-play" data-sandbox-field="special_play"></textarea></label>
        </fieldset>""")
    escaped_action = html.escape(action_id, quote=True)
    escaped_key = html.escape(idempotency_key, quote=True)
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; form-action 'none'; base-uri 'none'; frame-ancestors 'none'">
<title>BetGuard Local Sandbox Fill</title>
<style>body{{font:14px system-ui;max-width:900px;margin:2rem auto}} fieldset{{margin:1rem 0}} label{{display:block;margin:.5rem}} textarea,input,select{{display:block;width:100%;box-sizing:border-box}} textarea{{height:4rem}} #status{{white-space:pre-wrap}}</style>
</head><body data-site-id="site-betguard-local-sandbox">
<h1>Local Sandbox Assisted Fill</h1>
<p>This page accepts fill operations only from the local BetGuard server. It has no submit control.</p>
<button type="button" id="sandbox-fill-action" data-action-id="{escaped_action}" data-idempotency-key="{escaped_key}">Sandbox Fill</button>
<output id="status" aria-live="polite"></output>
<div id="sandbox-bets">{''.join(slots)}</div>
<span id="sandbox-mutation-count" data-count="0" hidden>0</span>
<span id="sandbox-submit-count" data-count="0" hidden>0</span>
<script>
(() => {{
  'use strict';
  const mutation = document.getElementById('sandbox-mutation-count');
  const submit = document.getElementById('sandbox-submit-count');
  document.getElementById('sandbox-bets').addEventListener('input', () => {{
    mutation.dataset.count = String(Number(mutation.dataset.count) + 1);
    mutation.textContent = mutation.dataset.count;
  }});
  document.getElementById('sandbox-bets').addEventListener('change', () => {{
    mutation.dataset.count = String(Number(mutation.dataset.count) + 1);
    mutation.textContent = mutation.dataset.count;
  }});
  document.addEventListener('submit', event => {{
    submit.dataset.count = String(Number(submit.dataset.count) + 1);
    submit.textContent = submit.dataset.count;
    event.preventDefault();
  }});
  document.getElementById('sandbox-fill-action').addEventListener('click', async event => {{
    const button = event.currentTarget;
    button.disabled = true;
    try {{
      const response = await fetch('{EXECUTE_PATH}', {{
        method: 'POST', credentials: 'omit', cache: 'no-store', redirect: 'error',
        headers: {{'Content-Type': 'application/json'}},
        body: JSON.stringify({{schema_version: 'betguard-local-sandbox-execute-request-v1', action_id: button.dataset.actionId, idempotency_key: button.dataset.idempotencyKey}})
      }});
      const result = await response.json();
      document.getElementById('status').textContent = JSON.stringify(result, null, 2);
    }} catch (_error) {{ document.getElementById('status').textContent = 'LOCAL_SANDBOX_REQUEST_FAILED'; }}
  }});
}})();
</script></body></html>"""


class LocalSandboxPageServer:
    """A server that binds only 127.0.0.1 and never redirects."""

    def __init__(
        self,
        execute_callback: Callable[[Mapping[str, Any]], Mapping[str, Any]],
        *,
        action_id: str = "",
        idempotency_key: str = "",
    ) -> None:
        self._callback = execute_callback
        self._action_id = action_id
        self._idempotency_key = idempotency_key
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def url(self) -> str:
        if self._httpd is None:
            raise RuntimeError("sandbox server has not started")
        return f"http://{LOCAL_SANDBOX_HOST}:{self._httpd.server_port}{LOCAL_SANDBOX_PATH}"

    def start(self) -> "LocalSandboxPageServer":
        if self._httpd is not None:
            return self
        owner = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_GET(self) -> None:  # noqa: N802
                if not self._host_is_exact() or self.path != LOCAL_SANDBOX_PATH:
                    self._send_json(403, {"error": {"code": "SANDBOX_EXTERNAL_URL_REJECTED", "message": "exact loopback sandbox required"}})
                    return
                body = render_sandbox_page(
                    action_id=owner._action_id, idempotency_key=owner._idempotency_key
                ).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("Referrer-Policy", "no-referrer")
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self) -> None:  # noqa: N802
                if not self._host_is_exact() or self.path != EXECUTE_PATH:
                    self._send_json(403, {"error": {"code": "SANDBOX_EXTERNAL_URL_REJECTED", "message": "exact loopback sandbox required"}})
                    return
                if self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower() != "application/json":
                    self._send_json(415, {"error": {"code": "SANDBOX_REQUEST_INVALID", "message": "JSON required"}})
                    return
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    if length < 2 or length > 4096:
                        raise ValueError("invalid length")
                    payload = json.loads(self.rfile.read(length).decode("utf-8"))
                    request = decode_public_execute_request(payload)
                    result = dict(owner._callback(request))
                    self._send_json(200, result)
                except LocalSandboxContractError as exc:
                    self._send_json(exc.http_status, exc.to_dict())
                except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
                    self._send_json(400, {"error": {"code": "SANDBOX_REQUEST_INVALID", "message": "request JSON invalid"}})

            def _host_is_exact(self) -> bool:
                expected = f"{LOCAL_SANDBOX_HOST}:{self.server.server_port}"
                return self.client_address[0] == LOCAL_SANDBOX_HOST and self.headers.get("Host") == expected

            def _send_json(self, status: int, value: Mapping[str, Any]) -> None:
                body = json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, _format: str, *_args: Any) -> None:
                return

        self._httpd = ThreadingHTTPServer((LOCAL_SANDBOX_HOST, 0), Handler)
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()
        return self

    def close(self) -> None:
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)
        self._httpd = None
        self._thread = None

    def __enter__(self) -> "LocalSandboxPageServer":
        return self.start()

    def __exit__(self, *_args: Any) -> None:
        self.close()
