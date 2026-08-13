"""Gate 1B safety tests for Qwen review-only UI evidence."""

from __future__ import annotations

import hashlib
import http.client
import io
import json
import os
import socket
import threading
from contextlib import contextmanager
from pathlib import Path

import pytest
from PIL import Image

try:
    from playwright.sync_api import sync_playwright

    HAS_PLAYWRIGHT = True
except ImportError:
    HAS_PLAYWRIGHT = False

from betguard.webui.assist_panel_vision_html import render_vision_ui_section
from betguard.webui import app as webui_app
from betguard.webui.app import build_workbench_handler


EXPECTED_LOCKED_HASHES = {
    ("raw", "sample-010.jpg"): "fa453fbfb2a3da16bbc04831843db7c9c07524704e66d3475a1add6b7893b526",
    ("raw", "sample-011.jpg"): "5f60cb2ebf751421fa0f0c510e14ee7fbc13e77412b089c6918b90765dfe4687",
    ("raw", "sample-012.jpg"): "b5e4428e6a8fcbc95a417e096c4568c3033857349acf66f30393e18112564284",
    ("raw", "sample-013.jpg"): "ef594a5b228379288de22c4e18e9d90dde751cc13fcc4b71082e279c45c1d388",
    ("ground-truth-draft", "sample-010.json"): "a701c78b63831cc577d5861a5e4cb9c043e3c850535206fcad0286155439cfaa",
    ("ground-truth-draft", "sample-011.json"): "3b1be2685ec6b9e0785b1f79c95147784ae23fc88b0ab93ccf75b8b19392536a",
    ("ground-truth-draft", "sample-012.json"): "ade32fa42863f8585637390eb186159cb90ce898515dd8354c95d3b4b38c0eff",
    ("ground-truth-draft", "sample-013.json"): "5f3a2acc045df5acefe070b87f8cf494893d575837e9bd130658be7502fbe100",
    ("prelabels", "sample-010.json"): "0f3d0c45077008a58eebd35e7027dd3065f763f8c63cc077b0954e5f2cd257f1",
    ("prelabels", "sample-011.json"): "5f98b584c68105218a673c965c20fdd4214940e66c42dff3c0b7c6e74facca25",
    ("prelabels", "sample-012.json"): "851528cb8cd9a213521b25ab3e48f97f77c583b003e6db310111a83acb07d640",
    ("prelabels", "sample-013.json"): "05984f1072e0343577b7ee95d446b75111fd2fe09fe0514a52f015b6ae249b12",
}


def _html() -> str:
    return render_vision_ui_section()


@pytest.fixture()
def page():
    if not HAS_PLAYWRIGHT:
        pytest.skip("Playwright not installed")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context()
        browser_page = context.new_page()
        browser_page.set_default_timeout(3000)
        yield browser_page
        context.close()
        browser.close()


def _between(text: str, start: str, end: str) -> str:
    return text.split(start, 1)[1].split(end, 1)[0]


def _png_bytes() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (40, 40), "white").save(buffer, format="PNG")
    return buffer.getvalue()


def _free_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


@contextmanager
def _running_workbench():
    port = _free_port()
    handler = build_workbench_handler(project_version="gate1b-test", git_commit="test")
    server = webui_app.ThreadingHTTPServer(("127.0.0.1", port), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield port
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def _post_manual_reparse(port: int, payload: dict) -> tuple[int, dict]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        connection.request(
            "POST",
            "/manual-reparse",
            body=body,
            headers={"Content-Type": "application/json"},
        )
        response = connection.getresponse()
        return response.status, json.loads(response.read().decode("utf-8"))
    finally:
        connection.close()


def _post_vision_job(port: int, payload: dict) -> tuple[int, dict]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        connection.request(
            "POST",
            "/api/vision/v1/jobs",
            body=body,
            headers={"Content-Type": "application/json"},
        )
        response = connection.getresponse()
        return response.status, json.loads(response.read().decode("utf-8"))
    finally:
        connection.close()


def _qwen_line(line_id: str, text: str) -> dict:
    return {
        "line_id": line_id,
        "text": text,
        "tokens": [
            {
                "text": text,
                "bounding_box": {
                    "coordinate_space": "pixel",
                    "polygon": [[1, 2], [3, 2], [3, 4], [1, 4]],
                },
            }
        ],
        "warnings": [],
    }


def _qwen_row(text: str) -> dict:
    return {
        "tokens": [{"text": text, "bbox": [1, 2, 3, 4]}],
        "numbers": [[text]],
        "multiplier": f"multiplier-{text}",
        "layout_hint": f"layout-{text}",
    }


def _completed_qwen_result(line_text: str = "99") -> dict:
    return {
        "request_id": "job-image-qwen",
        "status": "completed",
        "provider": {
            "id": "qwen-dashscope",
            "model_name": "qwen3-vl-plus",
            "model_version": "",
        },
        "source_image": {"sha256": "image-sha"},
        "raw_text": line_text,
        "lines": [_qwen_line("S01-L01", line_text)],
        "preprocessing": {
            "human_confirmation_required": True,
            "auto_confirm": False,
            "auto_submit": False,
            "prompt_version": "combined-bbox-v1",
            "prompt_sha256": "prompt-sha",
            "qwen_request": {
                "model": "qwen3-vl-plus",
                "image_sha256": "image-sha",
                "cache_hit": False,
                "request_id": "qwen-request-id",
            },
            "qwen_response": {
                "sections": [
                    {
                        "shared_multiplier": None,
                        "rows": [
                            _qwen_row(line_text)
                        ],
                    }
                ]
            },
        },
    }


def _structure_evidence(
    line_id: str,
    number: str,
    *,
    status: str = "consistent",
) -> dict:
    return {
        "line_id": line_id,
        "source": "deterministic_geometry_v1",
        "model_candidate": {
            "numbers": [[number]],
            "multiplier": None,
            "layout_hint": "normal_row",
            "shared_multiplier": None,
        },
        "reconstructed_candidate": {
            "number_groups": [[number]],
            "multiplier_rules": [],
            "layout": "normal_row",
            "collision": None,
            "shared_multiplier": None,
        },
        "status": status,
        "warnings": [],
        "evidence": {"tokens": [], "bbox_debug": {}, "rules_used": []},
        "human_confirmation_required": True,
        "auto_apply": False,
        "auto_confirm": False,
        "auto_submit": False,
    }


def _mount_qwen_ui(
    page,
    *,
    job_result: dict,
    manual_result: dict | None = None,
    structure_evidence: list[dict] | None = None,
):
    calls: dict[str, list] = {"jobs": [], "manual": [], "urls": [], "errors": []}
    png = _png_bytes()

    def handle(route):
        request = route.request
        calls["urls"].append(request.url)
        if request.url == "http://gate1b.test/":
            route.fulfill(
                status=200,
                content_type="text/html",
                body='<!doctype html><html><head><meta charset="utf-8"></head><body>'
                + render_vision_ui_section()
                + "</body></html>",
            )
            return
        if request.url.endswith("/api/vision/v1/providers"):
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps({
                    "ok": True,
                    "providers": [
                        {"id": "qwen-dashscope", "configured": True},
                        {"id": "openai-vision-paid", "configured": False},
                    ],
                }),
            )
            return
        if request.url.endswith("/api/vision/v1/images") and request.method == "POST":
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps({
                    "ok": True,
                    "image": {
                        "image_id": "image-qwen",
                        "original_filename": "slip.png",
                        "mime_type": "image/png",
                        "width": 40,
                        "height": 40,
                        "byte_size": len(png),
                        "sha256": "image-sha",
                    },
                }),
            )
            return
        if "/api/vision/v1/images/image-qwen" in request.url and request.method == "GET":
            route.fulfill(status=200, content_type="image/png", body=png)
            return
        if request.url.endswith("/api/vision/v1/jobs"):
            calls["jobs"].append(request.post_data_json)
            response_body = {"ok": True, "result": job_result}
            if structure_evidence is not None:
                response_body["structure_evidence"] = structure_evidence
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps(response_body),
            )
            return
        if request.url.endswith("/manual-reparse"):
            calls["manual"].append(request.post_data_json)
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps(manual_result or {
                    "ok": False,
                    "reason": "needs_review",
                    "auto_confirm": False,
                    "auto_submit": False,
                }),
            )
            return
        route.abort()

    page.route("**/*", handle)
    page.on("pageerror", lambda error: calls["errors"].append(str(error)))
    page.goto("http://gate1b.test/")
    try:
        page.wait_for_function(
            "document.getElementById('vision-provider-status').textContent.indexOf('Qwen 看圖已設定') >= 0",
            timeout=3000,
        )
    except Exception:
        pytest.fail(f"Qwen provider status did not initialize: {calls}")
    page.evaluate("document.getElementById('vision-section').style.display = 'block'")
    page.set_input_files(
        "#vision-file-input",
        {"name": "slip.png", "mimeType": "image/png", "buffer": png},
    )
    page.wait_for_selector("#vision-qwen-run-btn:visible")
    return calls


def test_qwen_is_called_only_by_explicit_button() -> None:
    html = _html()
    assert 'id="vision-qwen-run-btn"' in html
    assert '>Qwen 看圖</button>' in html
    assert html.count("visionRunQwenJob()") == 1
    assert 'window.visionRunQwenJob = function()' in html
    assert "只會在按下按鈕後呼叫" in html


def test_qwen_job_posts_explicit_provider_id() -> None:
    qwen_job = _between(
        _html(),
        "window.visionRunQwenJob = function()",
        "function _renderQwenFailure",
    )
    assert 'fetch("/api/vision/v1/jobs"' in qwen_job
    assert 'provider_id: "qwen-dashscope"' in qwen_job
    assert "game: selectedGame" in qwen_job
    assert 'getElementById("vision-qwen-game")' in qwen_job
    assert "openai-vision-paid" not in qwen_job
    assert "aided_image_id" not in qwen_job


def test_completed_means_waiting_for_human_not_confirmed() -> None:
    renderer = _between(
        _html(),
        "function _renderQwenEvidence(result)",
        "window.qwenCopyLine",
    )
    assert "AI 辨識完成，待人工核對" in renderer
    assert "僅代表 provider job 完成，不代表人工確認" in renderer
    assert "needs_review" in renderer
    for forbidden in ("正確", "已確認", "可填入"):
        assert forbidden not in renderer


def test_qwen_evidence_keeps_required_provenance_and_structure() -> None:
    renderer = _between(
        _html(),
        "function _renderQwenEvidence(result)",
        "window.qwenCopyLine",
    )
    for field in (
        "provider=",
        "model=",
        "prompt_version=",
        "prompt_sha256=",
        "image_sha256=",
        "cache_hit=",
        "request_id=",
        "RecognitionResult.raw_text",
        "Line.text",
        "token bbox",
        "numbers=",
        "multiplier=",
        "layout_hint=",
        "shared_multiplier=",
        "原始該列 Qwen 證據",
        "AI 結構（比較基準）",
        "規則重建",
        "number_groups=",
        "multiplier_rules=",
        "collision=",
        "比較狀態",
        "warnings",
        "evidence",
        "human_confirmation_required=true",
        "auto_apply=false",
    ):
        assert field in renderer


def test_qwen_reconstruction_is_read_only_and_keyed_only_by_line_id() -> None:
    html = _html()
    mapper = _between(
        html,
        "function _qwenReconstructionByLineId(structureEvidence)",
        "function _qwenComparisonLabel",
    )
    renderer = _between(
        html,
        "function _renderQwenEvidence(result)",
        "window.qwenCopyLine",
    )
    assert "reconstructionByLineId[lineId] = item" in mapper
    assert "reconstructionByLineId[lineId]" in renderer
    assert "line.line_id" in renderer
    assert "auto_apply=false" in renderer
    assert "applyReconstruction" not in html
    assert "reconstructionRows[i]" not in html
    assert "structureEvidence[i].reconstructed_candidate" not in html


def test_qwen_structure_uses_line_id_without_positional_fallback() -> None:
    html = _html()
    mapper = _between(
        html,
        "function _qwenEvidenceByLineId(qwenResponse)",
        "function _renderQwenEvidence(result)",
    )
    renderer = _between(
        html,
        "function _renderQwenEvidence(result)",
        "window.qwenCopyLine",
    )
    assert '"S" + String(s + 1).padStart(2, "0")' in mapper
    assert '"-L" + String(r + 1).padStart(2, "0")' in mapper
    assert "evidenceByLineId[lineId]" in mapper
    assert "evidenceByLineId[lineId]" in renderer
    assert "line.line_id" in renderer
    assert "structure evidence unavailable" in renderer
    assert "evidenceRows[i]" not in html
    assert "flattened[i]" not in html


def test_each_line_has_copy_stage_and_manual_edit_controls() -> None:
    renderer = _between(
        _html(),
        "function _renderQwenEvidence(result)",
        "window.qwenCopyLine",
    )
    assert "qwen-copy-line" in renderer and "複製" in renderer
    assert "qwen-stage-line" in renderer and "帶入修正欄" in renderer
    assert "qwen-line-edit" in renderer and "手動修改" in renderer
    assert "qwen-review-editable" in renderer


def test_stage_line_changes_only_frontend_temporary_field() -> None:
    stage = _between(
        _html(),
        "window.qwenStageLine = function(index)",
        "window.qwenPreviewReparse",
    )
    assert "target.value = inputs[index].value" in stage
    assert "fetch(" not in stage
    for forbidden in (
        "panelState",
        "validCandidates",
        "accepted_by_human",
        "ground-truth-draft",
        "approved_fill_queue",
        "model_raw_text =",
    ):
        assert forbidden not in stage


def test_reparse_preview_requires_explicit_button_and_selected_game() -> None:
    html = _html()
    assert "重新解析預覽" in html
    assert '<option value="539">539</option>' in html
    assert '<option value="六合">六合彩</option>' in html
    assert html.count('id="vision-qwen-game"') == 1
    assert 'id="qwen-review-game"' not in html
    assert "document_mode 不代表遊戲類型" in html
    qwen_job_and_render = _between(
        html,
        "window.visionRunQwenJob = function()",
        "window.qwenPreviewReparse = function()",
    )
    assert 'fetch("/manual-reparse"' not in qwen_job_and_render
    manual = _between(
        html,
        "window.qwenPreviewReparse = function()",
        "function _renderPendingConfirmation",
    )
    assert 'fetch("/manual-reparse"' in manual
    assert "game: selectedGame" in manual
    assert "register_candidate: false" in manual
    assert 'game: "auto"' not in manual
    assert "parser／validator" in manual


def test_vision_job_handler_passes_game_separately_from_document_mode(monkeypatch) -> None:
    import betguard.vision.service as vision_service

    captured: dict = {}

    def fake_run_job(
        image_id,
        provider_id,
        fixture,
        *,
        aided_image_id,
        document_mode,
        game,
    ):
        captured.update({
            "image_id": image_id,
            "provider_id": provider_id,
            "fixture": fixture,
            "aided_image_id": aided_image_id,
            "document_mode": document_mode,
            "game": game,
        })
        return {"ok": True, "result": {"status": "completed"}}

    monkeypatch.setattr(vision_service, "run_job", fake_run_job)
    with _running_workbench() as port:
        status, body = _post_vision_job(port, {
            "image_id": "image-qwen",
            "provider_id": "qwen-dashscope",
            "fixture": "bet_slip",
            "document_mode": "column",
            "game": "六合",
        })
    assert status == 200
    assert body["ok"] is True
    assert captured["game"] == "六合"
    assert captured["document_mode"] == "column"


def test_reparse_preview_renders_parser_fields_and_stays_non_candidate() -> None:
    preview = _between(
        _html(),
        "window.qwenPreviewReparse = function()",
        "function _renderPendingConfirmation",
    )
    for field in (
        "data.numbers",
        "data.stars",
        "data.amounts",
        "data.type",
        "data.columns",
        "data.summary",
        "auto_confirm=false",
        "auto_submit=false",
    ):
        assert field in preview
    assert "解析預覽完成，尚未加入可填入候選" in preview
    assert "register_candidate: false" in preview


def test_invalid_qwen_response_displays_failure_and_review_state() -> None:
    html = _html()
    failure = _between(
        html,
        "function _renderQwenFailure(message)",
        "function _qwenEvidenceByLineId",
    )
    renderer = _between(
        html,
        "function _renderQwenEvidence(result)",
        "window.qwenCopyLine",
    )
    assert "Qwen 辨識失敗" in failure
    assert "needs_review" in failure
    assert 'result.status !== "completed"' in renderer
    assert "!sections.length || !lines.length" in renderer
    assert "invalid Qwen response schema" in renderer
    assert "診斷編號：" in renderer
    assert "failureDiagnostic.diagnostic_id || result.request_id" in renderer


def test_structurally_valid_but_wrong_qwen_output_never_becomes_candidate() -> None:
    qwen_flow = _between(
        _html(),
        "window.visionRunQwenJob = function()",
        "function _renderPendingConfirmation",
    )
    assert "needs_review" in qwen_flow
    for forbidden in (
        "panelState",
        "validCandidates",
        "create-batch",
        "approved_fill_queue",
        "ground-truth-draft",
    ):
        assert forbidden not in qwen_flow
    assert "register_candidate: false" in qwen_flow


def test_qwen_flow_has_no_auto_actions_paid_fallback_or_webfill() -> None:
    qwen_flow = _between(
        _html(),
        "window.visionRunQwenJob = function()",
        "function _renderPendingConfirmation",
    )
    assert "auto_confirm=false" in qwen_flow
    assert "auto_submit=false" in qwen_flow
    assert "openai-vision-paid" not in qwen_flow
    assert "assist-fill" not in qwen_flow.lower()
    assert "webfill" not in qwen_flow.lower()


def test_qwen_prompt_and_provider_code_are_not_modified_by_gate1b() -> None:
    repo = Path(__file__).resolve().parents[1]
    assert (repo / "src/betguard/vision/qwen_prompts.py").exists()
    assert (repo / "src/betguard/vision/providers/qwen_dashscope.py").exists()
    ui_source = (
        repo / "src/betguard/webui/assist_panel_vision_html.py"
    ).read_text(encoding="utf-8")
    assert "qwen_prompts" not in ui_source
    assert "QwenDashScopeProvider" not in ui_source


def test_backend_read_only_preview_keeps_manual_registry_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    existing = {"manual-existing": {"summary": "preserve me"}}
    monkeypatch.setattr(webui_app, "_manual_candidates", existing.copy())
    before = dict(webui_app._manual_candidates)

    with _running_workbench() as port:
        status, result = _post_manual_reparse(
            port,
            {
                "text": "40 49 2X1",
                "game": "六合",
                "register_candidate": False,
            },
        )

    assert status == 200
    assert result["ok"] is True
    assert result["numbers"] == [40, 49]
    assert webui_app._manual_candidates == before
    assert "manual_candidate_id" not in result
    assert result.get("accepted_by_human") is False
    assert result["auto_confirm"] is False
    assert result["auto_submit"] is False


def test_backend_game_selection_controls_40_to_49_validation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(webui_app, "_manual_candidates", {})
    with _running_workbench() as port:
        _status_liuhe, liuhe = _post_manual_reparse(
            port,
            {
                "text": "40 49 2X1",
                "game": "六合",
                "register_candidate": False,
            },
        )
        _status_539, game_539 = _post_manual_reparse(
            port,
            {
                "text": "40 49 2X1",
                "game": "539",
                "register_candidate": False,
            },
        )

    assert liuhe["ok"] is True
    assert liuhe["numbers"] == [40, 49]
    assert game_539["ok"] is False
    assert game_539["reason"] == "needs_review"
    assert "valid range is 1-39" in game_539["error"]
    assert webui_app._manual_candidates == {}


def test_backend_default_manual_reparse_still_registers_candidate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(webui_app, "_manual_candidates", {})
    with _running_workbench() as port:
        status, result = _post_manual_reparse(
            port,
            {"text": "05 09 2X1", "game": "539"},
        )

    assert status == 200
    assert result["ok"] is True
    assert result["accepted_by_human"] is True
    assert result["auto_confirm"] is False
    assert result["auto_submit"] is False
    candidate_id = result["manual_candidate_id"]
    assert candidate_id in webui_app._manual_candidates
    assert webui_app._manual_candidates[candidate_id]["numbers"] == [5, 9]


def test_backend_read_only_handler_has_no_queue_draft_or_webfill_path() -> None:
    source = (
        Path(__file__).resolve().parents[1] / "src/betguard/webui/app.py"
    ).read_text(encoding="utf-8")
    handler = _between(
        source,
        "        def _handle_manual_reparse(self) -> None:",
        "        def _handle_window_pin(self) -> None:",
    )
    assert 'data.get("register_candidate", True)' in handler
    assert "result.get(\"ok\") and register_candidate" in handler
    for forbidden in (
        "approved_fill_queue",
        "ground-truth-draft",
        "RUNS_DIR",
        "get_assist_session",
        "_handle_assist_fill",
        "dispatch(",
    ):
        assert forbidden not in handler


def test_locked_sample_hashes_are_unchanged() -> None:
    configured = os.environ.get("BETGUARD_DATASET", "").strip()
    configured_dataset = Path(configured) if configured else None
    configured_has_locked_samples = bool(
        configured_dataset
        and all(
            (configured_dataset / folder / name).exists()
            for folder, name in EXPECTED_LOCKED_HASHES
        )
    )
    dataset = (
        configured_dataset
        if configured_has_locked_samples
        else Path(r"C:\BetguardOCRDataset")
    )
    if not all((dataset / folder / name).exists() for folder, name in EXPECTED_LOCKED_HASHES):
        pytest.skip("locked OCR dataset is not available")
    actual = {
        (folder, name): hashlib.sha256((dataset / folder / name).read_bytes()).hexdigest()
        for folder, name in EXPECTED_LOCKED_HASHES
    }
    assert actual == EXPECTED_LOCKED_HASHES


def test_browser_qwen_evidence_requires_explicit_actions(page) -> None:
    calls = _mount_qwen_ui(
        page,
        job_result=_completed_qwen_result("99"),
        manual_result={
            "ok": True,
            "numbers": [40, 49],
            "stars": [2],
            "amounts": {"2": 100},
            "type": "normal",
            "columns": [],
            "summary": "40,49｜2星｜100元",
            "accepted_by_human": False,
            "auto_confirm": False,
            "auto_submit": False,
        },
    )
    assert calls["jobs"] == []
    assert calls["manual"] == []
    assert page.get_attribute("#vision-preview-img", "src") == "/api/vision/v1/images/image-qwen"

    page.select_option("#vision-qwen-game", "六合")
    page.click("#vision-qwen-run-btn")
    page.wait_for_selector("#qwen-evidence-status")
    assert page.text_content("#qwen-evidence-status") == "AI 辨識完成，待人工核對"
    assert page.text_content("#qwen-raw-text") == "99"
    assert "needs_review" in page.text_content("#vision-results-body")
    assert calls["jobs"] == [
        {"image_id": "image-qwen", "provider_id": "qwen-dashscope", "game": "六合"}
    ]
    assert calls["manual"] == []

    page.fill(".qwen-line-edit", "40 49 2X1")
    page.click(".qwen-stage-line")
    assert page.input_value("#qwen-review-editable") == "40 49 2X1"
    assert calls["manual"] == []
    assert not any("webfill" in url or "assist-fill" in url for url in calls["urls"])

    page.click("#qwen-manual-reparse-btn")
    page.wait_for_function("document.getElementById('qwen-manual-reparse-btn').disabled === false")
    assert calls["manual"] == [{
        "text": "40 49 2X1",
        "game": "六合",
        "register_candidate": False,
    }]
    preview = page.text_content("#qwen-manual-reparse-result")
    assert "解析預覽完成，尚未加入可填入候選" in preview
    for expected in (
        "numbers=[40,49]",
        "stars=[2]",
        'amounts={"2":100}',
        "type=normal",
        "columns=[]",
        "summary=40,49｜2星｜100元",
        "auto_confirm=false",
        "auto_submit=false",
    ):
        assert expected in preview


def test_browser_qwen_job_sends_explicit_539_from_visible_selector(page) -> None:
    calls = _mount_qwen_ui(page, job_result=_completed_qwen_result("11"))
    assert page.is_visible("#vision-qwen-game")
    assert page.input_value("#vision-qwen-game") == "539"
    page.click("#vision-qwen-run-btn")
    page.wait_for_selector("#qwen-evidence-status")
    assert calls["jobs"] == [{
        "image_id": "image-qwen",
        "provider_id": "qwen-dashscope",
        "game": "539",
    }]


def test_browser_structure_evidence_follows_line_id_when_lines_reordered(page) -> None:
    result = _completed_qwen_result("11")
    result["raw_text"] = "22\n11"
    result["lines"] = [
        _qwen_line("S01-L02", "22"),
        _qwen_line("S01-L01", "11"),
    ]
    result["preprocessing"]["qwen_response"]["sections"][0]["rows"] = [
        _qwen_row("11"),
        _qwen_row("22"),
    ]

    _mount_qwen_ui(page, job_result=result)
    page.click("#vision-qwen-run-btn")
    page.wait_for_selector("#qwen-evidence-status")

    row_2 = page.text_content(
        '.qwen-evidence-line[data-line-id="S01-L02"] .qwen-row-structure'
    )
    row_1 = page.text_content(
        '.qwen-evidence-line[data-line-id="S01-L01"] .qwen-row-structure'
    )
    assert 'numbers=[["22"]]' in row_2
    assert "multiplier-22" in row_2
    assert "layout-22" in row_2
    assert 'numbers=[["11"]]' in row_1
    assert "multiplier-11" in row_1
    assert "layout-11" in row_1


def test_browser_reconstruction_follows_line_id_when_lines_reordered(page) -> None:
    result = _completed_qwen_result("11")
    result["raw_text"] = "22\n11"
    result["lines"] = [
        _qwen_line("S01-L02", "22"),
        _qwen_line("S01-L01", "11"),
    ]
    result["preprocessing"]["qwen_response"]["sections"][0]["rows"] = [
        _qwen_row("11"),
        _qwen_row("22"),
    ]
    structures = [
        _structure_evidence("S01-L01", "11"),
        _structure_evidence("S01-L02", "22", status="divergent"),
    ]

    _mount_qwen_ui(page, job_result=result, structure_evidence=structures)
    page.click("#vision-qwen-run-btn")
    page.wait_for_selector("#qwen-evidence-status")

    row_2 = page.text_content(
        '.qwen-evidence-line[data-line-id="S01-L02"] .qwen-reconstructed-structure'
    )
    status_2 = page.text_content(
        '.qwen-evidence-line[data-line-id="S01-L02"] .qwen-structure-comparison'
    )
    row_1 = page.text_content(
        '.qwen-evidence-line[data-line-id="S01-L01"] .qwen-reconstructed-structure'
    )
    assert 'number_groups=[["22"]]' in row_2
    assert "結構分歧（divergent）" in status_2
    assert "needs_review" in status_2
    assert 'number_groups=[["11"]]' in row_1


def test_browser_multi_row_section_renders_one_reconstructed_structure(page) -> None:
    result = _completed_qwen_result("24")
    result["raw_text"] = "24 03 17 20\n34 23 27 30\n37 35"
    result["lines"] = [
        _qwen_line("S01-L01", "24 03 17 20"),
        _qwen_line("S01-L02", "34 23 27 30"),
        _qwen_line("S01-L03", "37 35"),
    ]
    result["preprocessing"]["qwen_response"]["sections"][0]["rows"] = [
        {
            "tokens": [],
            "numbers": [["24"], ["03"], ["17"], ["20"]],
            "multiplier": None,
            "layout_hint": "column_bet",
        },
        {
            "tokens": [],
            "numbers": [["34"], ["23"], ["27"], ["30"]],
            "multiplier": None,
            "layout_hint": "column_bet",
        },
        {
            "tokens": [],
            "numbers": [["37"], ["35"]],
            "multiplier": None,
            "layout_hint": "column_bet",
        },
    ]
    columns = [
        ["24", "34"], ["03", "23"], ["17", "27", "37"], ["20", "30", "35"],
    ]
    primary = _structure_evidence("S01-L01", "24")
    primary.update({
        "structure_id": "S01",
        "primary_line_id": "S01-L01",
        "member_line_ids": ["S01-L01", "S01-L02", "S01-L03"],
        "line_role": "primary",
        "game": "539",
    })
    primary["model_candidate"].update({
        "numbers": columns,
        "layout_hint": "column_bet",
    })
    primary["reconstructed_candidate"].update({
        "number_groups": columns,
        "layout": "column_bet",
    })
    structures = [primary]
    for line_id in ("S01-L02", "S01-L03"):
        structures.append({
            "line_id": line_id,
            "structure_id": "S01",
            "primary_line_id": "S01-L01",
            "member_line_ids": ["S01-L01", "S01-L02", "S01-L03"],
            "line_role": "continuation",
            "game": "539",
            "status": "incomplete",
            "warnings": ["continuation_of:S01-L01"],
            "evidence": {},
            "human_confirmation_required": True,
            "auto_apply": False,
            "auto_confirm": False,
            "auto_submit": False,
        })

    _mount_qwen_ui(page, job_result=result, structure_evidence=structures)
    page.click("#vision-qwen-run-btn")
    page.wait_for_selector("#qwen-evidence-status")

    assert page.locator(".qwen-model-comparison-structure").count() == 1
    assert page.locator(".qwen-reconstructed-structure").count() == 1
    assert page.locator(".qwen-reconstruction-continuation").count() == 2
    model_rendered = page.text_content(".qwen-model-comparison-structure")
    reconstructed_rendered = page.text_content(".qwen-reconstructed-structure")
    comparison = page.text_content(".qwen-structure-comparison")
    raw_primary_row = page.text_content(
        '.qwen-evidence-line[data-line-id="S01-L01"] .qwen-row-structure'
    )
    expected_columns = '[["24","34"],["03","23"],["17","27","37"],["20","30","35"]]'
    assert "AI 結構（比較基準）" in model_rendered
    assert f"numbers={expected_columns}" in model_rendered
    assert f"number_groups={expected_columns}" in reconstructed_rendered
    assert 'numbers=[["24"],["03"],["17"],["20"]]' not in model_rendered
    assert "原始該列 Qwen 證據" in raw_primary_row
    assert 'numbers=[["24"],["03"],["17"],["20"]]' in raw_primary_row
    assert "結構一致（consistent）" in comparison
    assert "primary_line_id=S01-L01" in reconstructed_rendered
    assert 'member_line_ids=["S01-L01","S01-L02","S01-L03"]' in reconstructed_rendered
    assert all(
        "不建立獨立投注結構" in text
        for text in page.locator(".qwen-reconstruction-continuation").all_text_contents()
    )


def test_browser_missing_reconstruction_does_not_borrow_another_line(page) -> None:
    result = _completed_qwen_result("11")
    result["lines"] = [
        _qwen_line("S01-L01", "11"),
        _qwen_line("S01-L03", "33"),
    ]
    structures = [_structure_evidence("S01-L01", "11")]

    _mount_qwen_ui(page, job_result=result, structure_evidence=structures)
    page.click("#vision-qwen-run-btn")
    page.wait_for_selector("#qwen-evidence-status")

    missing = page.text_content(
        '.qwen-evidence-line[data-line-id="S01-L03"] .qwen-reconstructed-structure'
    )
    assert "structure evidence unavailable" in missing
    assert "證據不足（incomplete）" in missing
    assert "11" not in missing


def test_browser_missing_structure_does_not_borrow_next_row(page) -> None:
    result = _completed_qwen_result("11")
    result["raw_text"] = "11\n33"
    result["lines"] = [
        _qwen_line("S01-L01", "11"),
        _qwen_line("S01-L03", "33"),
    ]
    result["preprocessing"]["qwen_response"]["sections"][0]["rows"] = [
        _qwen_row("11"),
        _qwen_row("22"),
    ]

    _mount_qwen_ui(page, job_result=result)
    page.click("#vision-qwen-run-btn")
    page.wait_for_selector("#qwen-evidence-status")

    missing = page.text_content(
        '.qwen-evidence-line[data-line-id="S01-L03"] .qwen-row-structure'
    )
    assert "structure evidence unavailable" in missing
    assert "needs_review" in missing
    assert "22" not in missing
    assert "numbers=" not in missing
    assert "multiplier=" not in missing
    assert "layout_hint=" not in missing


def test_browser_unknown_line_id_displays_structure_unavailable(page) -> None:
    result = _completed_qwen_result("11")
    result["lines"] = [_qwen_line("unknown-line", "11")]

    _mount_qwen_ui(page, job_result=result)
    page.click("#vision-qwen-run-btn")
    page.wait_for_selector("#qwen-evidence-status")

    structure = page.text_content(
        '.qwen-evidence-line[data-line-id="unknown-line"] .qwen-row-structure'
    )
    assert structure == "structure evidence unavailable｜needs_review"


def test_browser_invalid_qwen_result_displays_failure(page) -> None:
    failed = {
        "request_id": "job-failed",
        "status": "failed",
        "provider": {"id": "qwen-dashscope", "model_name": "qwen3-vl-plus"},
        "provider_error": {"message": "invalid Qwen response schema"},
        "lines": [],
        "preprocessing": {
            "human_confirmation_required": True,
            "auto_confirm": False,
            "auto_submit": False,
        },
    }
    calls = _mount_qwen_ui(page, job_result=failed)
    assert calls["jobs"] == []
    page.click("#vision-qwen-run-btn")
    page.wait_for_selector("#qwen-evidence-status")
    assert page.text_content("#qwen-evidence-status") == "Qwen 辨識失敗"
    assert "invalid Qwen response schema" in page.text_content("#vision-results-body")
    assert "診斷編號：job-failed" in page.text_content(".qwen-failure-details")
    assert page.locator("#qwen-manual-reparse-btn").count() == 0
    assert calls["manual"] == []


def test_browser_truncated_qwen_result_has_safe_human_message_and_diagnostics(
    page,
) -> None:
    failed = {
        "request_id": "job-e52e3fcff11a4ae2b278596cc0d5ed17",
        "status": "failed",
        "provider": {"id": "qwen-dashscope", "model_name": "qwen3-vl-plus"},
        "provider_error": {
            "code": "QWEN_OUTPUT_TRUNCATED",
            "message": "QWEN_OUTPUT_TRUNCATED",
            "retryable": False,
        },
        "lines": [],
        "preprocessing": {
            "failure_diagnostic": {
                "classification": "QWEN_OUTPUT_TRUNCATED",
                "request_id": "job-e52e3fcff11a4ae2b278596cc0d5ed17",
                "finish_reason": "length",
            },
            "human_confirmation_required": True,
            "auto_confirm": False,
            "auto_submit": False,
        },
    }
    calls = _mount_qwen_ui(page, job_result=failed)

    page.click("#vision-qwen-run-btn")
    page.wait_for_selector("#qwen-evidence-status")

    assert page.text_content("#qwen-failure-message") == (
        "這張牌單內容較多，AI 回傳未完成。"
        "目前無法完整辨識，請勿直接確認結果。"
    )
    advanced = page.text_content(".qwen-failure-details")
    assert "QWEN_OUTPUT_TRUNCATED" in advanced
    assert "request_id=job-e52e3fcff11a4ae2b278596cc0d5ed17" in advanced
    assert "finish_reason=length" in advanced
    assert "sections must be a non-empty list" not in advanced
    assert page.get_attribute(".qwen-failure-details", "open") is None
    assert page.locator("#qwen-manual-reparse-btn").count() == 0
    assert page.evaluate("qwenGetReviewSession()") is None
    assert calls["manual"] == []
