"""Gate 3A browser-local Vision human review workflow tests."""

from __future__ import annotations

import io
import json
from copy import deepcopy

import pytest
from PIL import Image

try:
    from playwright.sync_api import sync_playwright

    HAS_PLAYWRIGHT = True
except ImportError:
    HAS_PLAYWRIGHT = False

from betguard.webui.assist_panel_vision_html import render_vision_ui_section
from betguard.webfill.manual_reparse import reparse_text


@pytest.fixture()
def page():
    if not HAS_PLAYWRIGHT:
        pytest.skip("Playwright not installed")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context()
        browser_page = context.new_page()
        browser_page.set_default_timeout(4000)
        yield browser_page
        context.close()
        browser.close()


def _png_bytes(color: str = "white") -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (100, 100), color).save(buffer, format="PNG")
    return buffer.getvalue()


def _line(line_id: str, text: str, box: list[int]) -> dict:
    x1, y1, x2, y2 = box
    bounding_box = {
        "coordinate_space": "pixel",
        "polygon": [[x1, y1], [x2, y1], [x2, y2], [x1, y2]],
    }
    return {
        "line_id": line_id,
        "order": 1,
        "text": text,
        "bounding_box": bounding_box,
        "tokens": [
            {
                "token_id": f"{line_id}-T01",
                "text": text,
                "start": 0,
                "end": len(text),
                "bounding_box": bounding_box,
            }
        ],
        "warnings": [],
    }


def _job_result() -> dict:
    return {
        "schema_version": "vision-recognition-v1",
        "recognition_id": "gate3a-recognition",
        "request_id": "job-image-gate3a",
        "status": "completed",
        "provider": {"id": "qwen-dashscope", "model_name": "qwen3-vl-plus"},
        "source_image": {
            "image_id": "image-gate3a",
            "sha256": "image-sha-gate3a",
            "width": 100,
            "height": 100,
        },
        "raw_text": "05 06 10 28 3/4X1\n24 08 16 03 2/3X0.1\n34 38 36 13",
        "lines": [
            _line("S01-L01", "05 06 10 28 3/4X1", [5, 5, 75, 20]),
            _line("S02-L01", "24 08 16 03 2/3X0.1", [8, 35, 82, 50]),
            _line("S02-L02", "34 38 36 13", [8, 52, 65, 67]),
        ],
        "preprocessing": {
            "human_confirmation_required": True,
            "auto_confirm": False,
            "auto_submit": False,
            "prompt_version": "combined-bbox-v1",
            "prompt_sha256": "prompt-sha",
            "qwen_request": {
                "model": "qwen3-vl-plus",
                "image_sha256": "image-sha-gate3a",
                "cache_hit": True,
                "request_id": "qwen-request-gate3a",
            },
            "qwen_response": {
                "sections": [
                    {
                        "shared_multiplier": None,
                        "rows": [{
                            "tokens": [],
                            "numbers": [["05", "06", "10", "28"]],
                            "multiplier": "3/4X1",
                            "layout_hint": "normal_row",
                        }],
                    },
                    {
                        "shared_multiplier": None,
                        "rows": [
                            {
                                "tokens": [],
                                "numbers": [["24"], ["08"], ["16"], ["03"]],
                                "multiplier": "2/3X0.1",
                                "layout_hint": "column_bet",
                            },
                            {
                                "tokens": [],
                                "numbers": [["34"], ["38"], ["36"], ["13"]],
                                "multiplier": None,
                                "layout_hint": "column_bet",
                            },
                        ],
                    },
                ]
            },
        },
    }


def _structure_evidence() -> list[dict]:
    safety = {
        "human_confirmation_required": True,
        "auto_apply": False,
        "auto_confirm": False,
        "auto_submit": False,
    }
    return [
        {
            "line_id": "S01-L01",
            "structure_id": "S01",
            "primary_line_id": "S01-L01",
            "member_line_ids": ["S01-L01"],
            "line_role": "primary",
            "game": "539",
            "model_candidate": {
                "numbers": [["05", "06", "10", "28"]],
                "multiplier": "3/4X1",
                "layout_hint": "normal_row",
            },
            "reconstructed_candidate": {
                "number_groups": [["05", "06", "10", "28"]],
                "multiplier_rules": ["3/4X1"],
                "layout": "normal_row",
                "collision": "3/4",
            },
            "status": "consistent",
            "warnings": ["model_collision_evidence_missing"],
            "evidence": {"bbox_debug": {}},
            **safety,
        },
        {
            "line_id": "S02-L01",
            "structure_id": "S02",
            "primary_line_id": "S02-L01",
            "member_line_ids": ["S02-L01", "S02-L02"],
            "line_role": "primary",
            "game": "539",
            "model_candidate": {
                "numbers": [["24", "34"], ["08", "38"], ["16", "36"], ["03", "13"]],
                "multiplier": "2/3X0.1",
                "layout_hint": "column_bet",
            },
            "reconstructed_candidate": {
                "number_groups": [["24", "34"], ["08", "38"], ["16", "36"], ["03", "13"]],
                "multiplier_rules": ["2/3/4X0.1"],
                "layout": "column_bet",
                "collision": "2/3/4",
            },
            "status": "divergent",
            "warnings": ["fragment_multiplier_ambiguous"],
            "evidence": {"bbox_debug": {}},
            **safety,
        },
        {
            "line_id": "S02-L02",
            "structure_id": "S02",
            "primary_line_id": "S02-L01",
            "member_line_ids": ["S02-L01", "S02-L02"],
            "line_role": "continuation",
            "game": "539",
            "status": "incomplete",
            "warnings": ["continuation_of:S02-L01"],
            "evidence": {},
            **safety,
        },
    ]


def _manual_result() -> dict:
    return {
        "ok": True,
        "numbers": [5, 9, 17, 28],
        "stars": [2, 3],
        "amounts": {"2": 100, "3": 100},
        "type": "normal",
        "columns": [],
        "summary": "05,09,17,28｜23星｜100元",
        "accepted_by_human": False,
        "auto_confirm": False,
        "auto_submit": False,
    }


def _runtime_sample008_routing() -> dict:
    items = [
        {
            "evidence_id": "GEMMA-0004",
            "raw_text": "08x01\n04 2x5",
            "numbers": "08 01 04",
            "multiplier_text": "2x5",
            "layout_guess": "column",
            "continuation": "yes",
            "special_text": "none",
            "cancelled": "no",
            "uncertain": False,
        },
        {
            "evidence_id": "GEMMA-0005",
            "raw_text": "08x16\n26 2x1",
            "numbers": "08 16 26",
            "multiplier_text": "2x1",
            "layout_guess": "column",
            "continuation": "yes",
            "special_text": "none",
            "cancelled": "no",
            "uncertain": False,
        },
        {
            "evidence_id": "GEMMA-0007",
            "raw_text": "25x26x28x07\n09 3x1",
            "numbers": "25 26 28 07 09",
            "multiplier_text": "3x1",
            "layout_guess": "column",
            "continuation": "yes",
            "special_text": "none",
            "cancelled": "no",
            "uncertain": False,
        },
    ]
    drafts = []
    for index, item in enumerate(items, 1):
        drafts.append({
            "draft_id": f"gemma-raw-{index:04d}",
            "source_evidence_id": item["evidence_id"],
            "raw_text": item["raw_text"],
            "number_groups_raw": item["numbers"],
            "multiplier_raw": item["multiplier_text"],
            "layout_suggestion": item["layout_guess"],
            "continuation_suggestion": item["continuation"],
            "special_play_raw": item["special_text"],
            "cancelled_suggestion": item["cancelled"],
            "uncertain": item["uncertain"],
        })
    return {
        "schema_version": "betguard.vision.reader-routing-result.v1",
        "image_sha256": "8" * 64,
        "routing_decision": "GEMMA_PRIMARY",
        "primary_machine_source": "gemma4-26b-shadow",
        "fallback_reason": None,
        "gemma_evidence": {
            "status": "completed",
            "provider": {"id": "gemma4-26b-shadow"},
            "items": items,
            "cache_hit": True,
            "external_call_count": 0,
            "evidence_only": True,
            "human_confirmed": False,
        },
        "pp_evidence": {
            "status": "completed",
            "provider": {"id": "ppocrv6-shadow"},
            "regions": [],
            "cache_hit": True,
            "local_inference_calls": 0,
            "evidence_only": True,
        },
        "qwen_evidence": None,
        "selected_prefill_source": "gemma4-26b-shadow",
        "review_seed": {
            "schema_version": "betguard.vision.human-review-seed.v1",
            "status": "machine_prefill_available",
            "selected_machine_source": "gemma4-26b-shadow",
            "draft_items": drafts,
            "structured_human_answer_required": True,
            "human_confirmed": False,
            "human_confirmation_required": True,
            "value_authority": "human_confirmed_answer",
            "candidate_created": False,
            "auto_confirm": False,
            "auto_submit": False,
        },
        "field_conflicts": [],
        "cache_status": {
            "gemma": {"checked": True, "cache_hit": True, "identity": {"image_sha256": "8" * 64}},
            "ppocr": {"checked": True, "cache_hit": True, "identity": {"image_sha256": "8" * 64}},
            "qwen": {"checked": False, "cache_hit": False, "identity": None},
        },
        "latency": {
            "gemma_latency_ms": 1.0,
            "pp_latency_ms": 1.0,
            "qwen_latency_ms": 0.0,
            "total_routing_latency_ms": 1.5,
            "gemma_pp_parallel": True,
            "qwen_conditional_after_gemma": False,
        },
        "model_call_counters": {
            "gemma_attempts": 1,
            "gemma_external_calls": 0,
            "gemma_cache_hits": 1,
            "gemma_retries": 0,
            "pp_local_inference_calls": 0,
            "pp_cache_hits": 1,
            "qwen_attempts": 0,
            "qwen_external_calls": 0,
            "qwen_cache_hits": 0,
            "qwen_retries": 0,
            "codex_vision_runtime_calls": 0,
        },
        "human_confirmation_required": True,
        "value_authority": "human_confirmed_answer",
        "auto_confirm": False,
        "auto_submit": False,
    }


def _runtime_manual_only_routing() -> dict:
    routing = _runtime_sample008_routing()
    routing["routing_decision"] = "MANUAL_REVIEW_ONLY"
    routing["fallback_reason"] = "GEMMA_SCHEMA_INVALID"
    routing["selected_prefill_source"] = None
    routing["gemma_evidence"] = {
        "status": "failed",
        "provider": {"id": "gemma4-26b-shadow"},
        "items": [],
        "cache_hit": False,
        "external_call_count": 0,
        "evidence_only": True,
        "human_confirmed": False,
    }
    routing["review_seed"] = {
        **routing["review_seed"],
        "status": "manual_entry_required",
        "selected_machine_source": None,
        "draft_items": [],
    }
    return routing


def _runtime_second_opinion_failure() -> dict:
    routing = _runtime_sample008_routing()
    routing["routing_decision"] = "GEMMA_PRIMARY_WITH_SECOND_OPINION"
    routing["fallback_reason"] = "SECOND_OPINION_REQUESTED"
    routing["qwen_evidence"] = {
        "schema_version": "betguard.vision.qwen-machine-evidence.v1",
        "status": "failed",
        "provider": "qwen-dashscope",
        "recognition_result": {"status": "failed", "lines": []},
        "cache_hit": False,
        "external_call_count": 1,
        "retry_count": 0,
        "evidence_only": True,
        "human_confirmed": False,
    }
    routing["model_call_counters"] = {
        **routing["model_call_counters"],
        "qwen_attempts": 1,
        "qwen_external_calls": 1,
    }
    return routing


def _mount(
    page,
    *,
    failed: bool = False,
    job_result: dict | None = None,
    structure_evidence: list[dict] | None = None,
    gemma_evidence: dict | None = None,
    runtime_routing: dict | None = None,
    runtime_nested: bool = False,
    second_opinion_routing: dict | None = None,
    second_opinion_nested: bool = False,
):
    calls: dict[str, list] = {
        "jobs": [], "manual": [], "urls": [], "uploads": [],
        "review_create": [], "review_replace": [], "review_confirm": [],
        "review_unconfirm": [], "candidate_create": [],
    }
    upload_count = 0
    first = _png_bytes()
    server_review: dict | None = None
    server_candidate: dict | None = None
    server_candidate_state: str | None = None

    def handle(route):
        nonlocal upload_count, server_review, server_candidate, server_candidate_state
        request = route.request
        calls["urls"].append(request.url)
        if request.url == "http://gate3a.test/":
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
        if request.url.endswith("/api/vision/v1/review-sessions") and request.method == "POST":
            payload = request.post_data_json
            calls["review_create"].append(payload)
            server_review = {
                **payload,
                "bets": [{**bet, "human_confirmed": False} for bet in payload["bets"]],
                "human_answer_revision": 1,
                "human_answer_hash": "a" * 64,
                "created_at": "2026-08-15T00:00:00+00:00",
                "updated_at": "2026-08-15T00:00:00+00:00",
            }
            route.fulfill(status=201, content_type="application/json", body=json.dumps({
                "ok": True, "review": server_review,
                "safety": {"candidate_only": True, "approved_for_fill": False, "queue_written": False, "auto_confirm": False, "auto_submit": False, "webfill_called": False},
            }))
            return
        if "/api/vision/v1/review-sessions/" in request.url and request.url.endswith(("/confirmations", "/unconfirmations")) and request.method == "POST":
            payload = request.post_data_json
            confirmed = request.url.endswith("/confirmations")
            calls["review_confirm" if confirmed else "review_unconfirm"].append(payload)
            assert server_review is not None
            selected = set(payload["human_bet_ids"])
            server_review = {
                **server_review,
                "bets": [
                    {**bet, "human_confirmed": confirmed}
                    if bet["human_bet_id"] in selected else bet
                    for bet in server_review["bets"]
                ],
                "human_answer_revision": server_review["human_answer_revision"] + 1,
                "human_answer_hash": format(server_review["human_answer_revision"] + 1, "x")[-1] * 64,
            }
            if server_candidate is not None:
                server_candidate_state = "STALE"
            route.fulfill(status=200, content_type="application/json", body=json.dumps({
                "ok": True,
                "review": server_review,
                "candidate": (
                    {"candidate": server_candidate, "state": server_candidate_state}
                    if server_candidate is not None else None
                ),
            }))
            return
        if "/api/vision/v1/review-sessions/" in request.url and request.method == "PATCH":
            payload = request.post_data_json
            calls["review_replace"].append(payload)
            assert server_review is not None
            server_review = {
                **server_review,
                "bets": payload["bets"],
                "machine_evidence_refs": payload["machine_evidence_refs"],
                "blocking_unresolved_count": payload["blocking_unresolved_count"],
                "human_answer_revision": server_review["human_answer_revision"] + 1,
                "human_answer_hash": format(server_review["human_answer_revision"] + 1, "x")[-1] * 64,
            }
            if server_candidate is not None:
                server_candidate_state = "STALE"
            route.fulfill(status=200, content_type="application/json", body=json.dumps({
                "ok": True,
                "review": server_review,
                "candidate": (
                    {"candidate": server_candidate, "state": server_candidate_state}
                    if server_candidate is not None else None
                ),
            }))
            return
        if "/api/vision/v1/review-sessions/" in request.url and request.method == "GET":
            if server_review is None:
                route.fulfill(status=404, content_type="application/json", body='{"ok":false,"code":"REVIEW_NOT_FOUND"}')
            else:
                route.fulfill(status=200, content_type="application/json", body=json.dumps({
                    "ok": True,
                    "review": server_review,
                    "candidate": (
                        {"candidate": server_candidate, "state": server_candidate_state}
                        if server_candidate is not None else None
                    ),
                }))
            return
        if request.url.endswith("/api/vision/v1/candidates") and request.method == "POST":
            payload = request.post_data_json
            calls["candidate_create"].append(payload)
            assert server_review is not None
            active_bets = []
            cancelled_audit = []
            for bet in server_review["bets"]:
                candidate_bet = {
                    **bet,
                    "executable": bet["active"] and bet["human_confirmed"],
                    "value_authority": "human_answer",
                }
                candidate_bet.pop("human_confirmed", None)
                (cancelled_audit if bet["cancelled"] else active_bets).append(candidate_bet)
            server_candidate = {
                "schema_version": "vision-candidate-authority-v1",
                "candidate_id": "vc-" + "1" * 32,
                "revision": 1,
                "state_at_creation": "CURRENT",
                "game": server_review["game"],
                "source": {
                    "review_session_id": server_review["review_session_id"],
                    "human_answer_revision": server_review["human_answer_revision"],
                    "human_answer_hash": server_review["human_answer_hash"],
                    "source_image_id": server_review["source_image_id"],
                    "source_image_hash": server_review["source_image_hash"],
                },
                "authority": {
                    "value_authority": "human_answer",
                    "all_active_confirmed": True,
                    "blocking_unresolved_count": 0,
                    "machine_evidence_refs": server_review["machine_evidence_refs"],
                },
                "active_bets": active_bets,
                "cancelled_audit": cancelled_audit,
                "canonical_content_hash": "c" * 64,
                "safety": {
                    "candidate_only": True,
                    "approved_for_fill": False,
                    "queue_written": False,
                    "auto_confirm": False,
                    "auto_submit": False,
                    "webfill_called": False,
                },
            }
            server_candidate_state = "CURRENT"
            route.fulfill(status=201, content_type="application/json", body=json.dumps({
                "ok": True, "candidate": server_candidate, "replayed": len(calls["candidate_create"]) > 1,
            }))
            return
        if request.url.endswith("/api/vision/v1/images") and request.method == "POST":
            upload_count += 1
            image_id = f"image-gate3a-{upload_count}"
            calls["uploads"].append(image_id)
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps({
                    "ok": True,
                    "image": {
                        "image_id": image_id,
                        "original_filename": f"slip-{upload_count}.png",
                        "mime_type": "image/png",
                        "width": 100,
                        "height": 100,
                        "byte_size": len(first),
                        "sha256": f"upload-sha-{upload_count}",
                    },
                }),
            )
            return
        if "/api/vision/v1/images/image-gate3a-" in request.url and request.method == "GET":
            route.fulfill(status=200, content_type="image/png", body=first)
            return
        if request.url.endswith("/api/vision/v1/jobs"):
            job_payload = request.post_data_json
            calls["jobs"].append(job_payload)
            is_second_opinion = bool(job_payload.get("second_opinion_requested"))
            selected_routing = (
                second_opinion_routing
                if is_second_opinion and second_opinion_routing is not None
                else runtime_routing
            )
            if selected_routing is not None:
                nested = second_opinion_nested if is_second_opinion else runtime_nested
                body = {"ok": True}
                if nested:
                    body["result"] = {"routing_result": deepcopy(selected_routing)}
                else:
                    body["routing_result"] = deepcopy(selected_routing)
                route.fulfill(
                    status=200,
                    content_type="application/json",
                    body=json.dumps(body),
                )
                return
            if failed:
                body = {
                    "ok": True,
                    "result": {
                        "status": "failed",
                        "provider": {"id": "qwen-dashscope"},
                        "provider_error": {"message": "private stack trace"},
                        "lines": [],
                        "preprocessing": {},
                    },
                    "gemma_shadow_evidence": gemma_evidence,
                }
            else:
                body = {
                    "ok": True,
                    "result": job_result if job_result is not None else _job_result(),
                    "structure_evidence": (
                        structure_evidence
                        if structure_evidence is not None
                        else _structure_evidence()
                    ),
                    "gemma_shadow_evidence": gemma_evidence,
                }
            route.fulfill(status=200, content_type="application/json", body=json.dumps(body))
            return
        if request.url.endswith("/manual-reparse"):
            calls["manual"].append(request.post_data_json)
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps(_manual_result()),
            )
            return
        if request.method == "DELETE":
            route.fulfill(status=200, content_type="application/json", body='{"ok":true}')
            return
        route.abort()

    page.route("**/*", handle)
    page.goto("http://gate3a.test/")
    page.evaluate("document.getElementById('vision-section').style.display = 'block'")
    page.set_input_files(
        "#vision-file-input",
        {"name": "slip.png", "mimeType": "image/png", "buffer": first},
    )
    page.wait_for_selector("#vision-qwen-run-btn:visible")
    return calls


def _wait_authority_ready(page) -> None:
    page.wait_for_function(
        "qwenGetReviewSession() && qwenGetReviewSession().server_state === 'ready'"
    )


def _confirm_structure(page, structure_id: str) -> None:
    before = page.evaluate("qwenGetReviewSession().human_answer_revision")
    page.click(
        f'.qwen-review-card[data-structure-id="{structure_id}"] .qwen-confirm-structure'
    )
    page.wait_for_function(
        "([structureId, revision]) => {"
        "const session=qwenGetReviewSession();"
        "return session && session.human_answer_revision > revision && "
        "session.structures.some(card => card.structure_id === structureId && card.human_confirmed === true);"
        "}",
        arg=[structure_id, before],
    )


def _create_candidate(page) -> dict:
    # Candidate creation remains a backend boundary in the three-step MVP;
    # invoke the compatibility API directly rather than exposing a technical
    # Candidate button to normal users.
    page.evaluate("qwenCompleteReview()")
    page.wait_for_function("qwenGetReviewSummary() !== null")
    return page.evaluate("qwenGetReviewSummary()")


def _run_qwen(page) -> None:
    page.click("#vision-qwen-run-btn")
    page.wait_for_selector("#qwen-review-session .qwen-review-card")


def _run_runtime_reader(page) -> None:
    page.click("#vision-run-btn")
    page.wait_for_selector("#qwen-review-session .qwen-review-card")


@pytest.mark.parametrize("nested", [False, True])
def test_runtime_router_top_level_and_legacy_nested_seed_sample008_review(
    page, nested: bool
) -> None:
    calls = _mount(
        page,
        runtime_routing=_runtime_sample008_routing(),
        runtime_nested=nested,
    )
    _run_runtime_reader(page)

    session = page.evaluate("qwenGetReviewSession()")
    assert len(session["structures"]) == 3
    assert session["runtime_routing"]["selected_prefill_source"] == "gemma4-26b-shadow"
    assert [card["staged_structure"]["number_groups"] for card in session["structures"]] == [
        [["08"], ["01", "04"]],
        [["08"], ["16", "26"]],
        [["25"], ["26"], ["28"], ["07", "09"]],
    ]
    assert all(card["human_confirmed"] is False for card in session["structures"])
    assert "Qwen 辨識失敗" not in page.text_content("#vision-results-body")
    counters = session["runtime_routing"]["model_call_counters"]
    assert counters["gemma_external_calls"] == 0
    assert counters["gemma_cache_hits"] == 1
    assert counters["qwen_external_calls"] == 0
    assert counters["codex_vision_runtime_calls"] == 0
    assert calls["jobs"][-1]["second_opinion_requested"] is False


def test_dense_partial_gemma_seed_creates_cards_and_shows_folded_diagnostics(
    page,
) -> None:
    routing = _runtime_sample008_routing()
    routing["gemma_evidence"].update(
        raw_item_count=4,
        accepted_item_count=3,
        rejected_item_count=1,
        rejected_items=[
            {"item_index": 2, "reason_code": "GEMMA_ITEM_LAYOUT_INVALID"}
        ],
        rejected_reason_codes=["GEMMA_ITEM_LAYOUT_INVALID"],
        partial_machine_read=True,
        needs_review=True,
    )
    routing["review_seed"].update(
        needs_review=True,
        partial_machine_read=True,
        machine_read_diagnostics={
            "raw_item_count": 4,
            "accepted_item_count": 3,
            "rejected_item_count": 1,
            "rejected_reason_codes": ["GEMMA_ITEM_LAYOUT_INVALID"],
        },
    )
    _mount(page, runtime_routing=routing)
    _run_runtime_reader(page)

    session = page.evaluate("qwenGetReviewSession()")
    assert len(session["structures"]) == 3
    assert all(card["human_confirmed"] is False for card in session["structures"])
    assert page.text_content("#runtime-reader-status") == (
        "AI 已讀到部分投注，仍有內容需要人工補充。"
    )
    assert page.get_attribute("#runtime-reader-advanced", "open") is None
    advanced = page.text_content("#runtime-reader-advanced")
    assert '"raw_item_count": 4' in advanced
    assert '"accepted_item_count": 3' in advanced
    assert '"rejected_item_count": 1' in advanced
    assert "GEMMA_ITEM_LAYOUT_INVALID" in advanced
    assert page.locator("#qwen-add-manual-structure").count() == 1
    assert session["runtime_routing"]["model_call_counters"]["qwen_external_calls"] == 0


def test_bet_level_seed_renders_only_promoted_drafts_and_keeps_fragments_advanced(
    page,
) -> None:
    routing = _runtime_sample008_routing()
    draft = deepcopy(routing["review_seed"]["draft_items"][1])
    draft.update(
        layout_suggestion="column",
        number_groups_suggestion=[["08"], ["16", "26"]],
        multiplier_rules_suggestion=["2X1"],
    )
    # The server-compiled draft is authoritative for presentation; the raw model
    # guess remains evidence only and may disagree.
    routing["gemma_evidence"]["items"][1]["layout_guess"] = "normal"
    fragments = [
        {
            "fragment_id": "gemma-fragment-0001",
            "source_evidence_id": "GEMMA-0001",
            "reason_code": "CONTINUATION_FRAGMENT_REQUIRES_REVIEW",
            "raw_text": "06 07",
            "needs_review": True,
            "human_confirmed": False,
        },
        {
            "fragment_id": "gemma-fragment-0002",
            "source_evidence_id": "GEMMA-0003",
            "reason_code": "MULTIPLIER_OR_CATEGORY_FRAGMENT_REQUIRES_REVIEW",
            "raw_text": "2/3X0.5",
            "needs_review": True,
            "human_confirmed": False,
        },
    ]
    routing["review_seed"].update(
        bet_drafts=[draft],
        # A stale compatibility alias must not create duplicate cards.
        unresolved_machine_fragments=fragments,
        needs_review=True,
        partial_machine_read=True,
        machine_read_diagnostics={
            "raw_item_count": 3,
            "accepted_item_count": 3,
            "rejected_item_count": 0,
            "rejected_reason_codes": [],
            "bet_draft_count": 1,
            "unresolved_fragment_count": 2,
            "partial_machine_read": True,
            "needs_review": True,
        },
    )
    _mount(page, runtime_routing=routing)
    _run_runtime_reader(page)

    session = page.evaluate("qwenGetReviewSession()")
    assert len(session["structures"]) == 1
    assert session["structures"][0]["staged_structure"]["number_groups"] == [
        ["08"],
        ["16", "26"],
    ]
    assert session["structures"][0]["human_confirmed"] is False
    assert session["unlinked_gemma_items"] == fragments
    assert page.text_content("#runtime-unresolved-fragment-notice") == (
        "AI 另外讀到 2 個未能安全組成投注的片段，請檢查。"
    )
    assert page.get_attribute("#runtime-reader-advanced", "open") is None
    assert "CONTINUATION_FRAGMENT_REQUIRES_REVIEW" in page.text_content(
        "#runtime-reader-advanced"
    )
    _wait_authority_ready(page)
    page.click("#qwen-add-manual-structure")
    page.wait_for_function("qwenGetReviewSession().structures.length === 2")
    assert page.evaluate("qwenGetReviewSession().structures[1].human_confirmed") is False


def test_spacing_only_dense_numbers_do_not_invent_a_column_separator(page) -> None:
    routing = _runtime_sample008_routing()
    item = routing["gemma_evidence"]["items"][0]
    item.update(
        raw_text="06 07 08 38",
        numbers="06 07 08 38",
        layout_guess="column",
        continuation="no",
    )
    routing["gemma_evidence"]["items"] = [item]
    draft = routing["review_seed"]["draft_items"][0]
    draft.update(
        raw_text="06 07 08 38",
        number_groups_raw="06 07 08 38",
        layout_suggestion="column",
        continuation_suggestion="no",
    )
    routing["review_seed"]["draft_items"] = [draft]
    _mount(page, runtime_routing=routing)
    _run_runtime_reader(page)

    staged = page.evaluate(
        "qwenGetReviewSession().structures[0].staged_structure.number_groups"
    )
    assert staged == []
    assert page.evaluate("qwenGetReviewSession().structures[0].human_confirmed") is False


def test_runtime_manual_only_keeps_manual_review_available_and_unconfirmed(page) -> None:
    _mount(page, runtime_routing=_runtime_manual_only_routing())
    page.click("#vision-run-btn")
    page.wait_for_selector("#qwen-add-manual-structure")

    assert "AI建議不可用，仍可手動輸入。" in page.text_content("#vision-results-body")
    assert "Qwen 辨識失敗" not in page.text_content("#vision-results-body")
    assert page.evaluate("qwenGetReviewSession().structures.length") == 0
    _wait_authority_ready(page)
    page.click("#qwen-add-manual-structure")
    page.wait_for_function("qwenGetReviewSession().structures.length === 1")
    assert page.evaluate("qwenGetReviewSession().structures[0].human_confirmed") is False


def test_explicit_qwen_second_opinion_uses_nested_helper_and_preserves_human_answer(
    page,
) -> None:
    calls = _mount(
        page,
        runtime_routing=_runtime_sample008_routing(),
        second_opinion_routing=_runtime_second_opinion_failure(),
        second_opinion_nested=True,
    )
    _run_runtime_reader(page)
    before = page.evaluate("qwenGetReviewSession().structures")

    page.click("#vision-qwen-run-btn")
    page.wait_for_selector("#runtime-second-opinion-status")

    assert page.text_content("#runtime-second-opinion-status") == (
        "Qwen 第二意見失敗；既有 Human Answer 未變更。"
    )
    assert "Qwen 辨識失敗" not in page.text_content("#vision-results-body")
    assert page.evaluate("qwenGetReviewSession().structures") == before
    assert calls["jobs"][-1]["second_opinion_requested"] is True
    assert page.evaluate(
        "qwenGetReviewSession().runtime_routing.model_call_counters.qwen_external_calls"
    ) == 1


def test_qwen_second_opinion_timeout_restores_button_and_preserves_human_answer(
    page,
) -> None:
    _mount(page, runtime_routing=_runtime_sample008_routing())
    _run_runtime_reader(page)
    before = page.evaluate("qwenGetReviewSession().structures")
    page.evaluate(
        """
        (() => {
        const originalFetch = window.fetch;
        window.fetch = function(url, options) {
          if (String(url).endsWith('/api/vision/v1/jobs') &&
              JSON.parse(options.body).second_opinion_requested === true) {
            const timeout = new Error('mock timeout');
            timeout.name = 'AbortError';
            return Promise.reject(timeout);
          }
          return originalFetch.call(window, url, options);
        };
        return true;
        })()
        """
    )

    page.click("#vision-qwen-run-btn")
    page.wait_for_function(
        "document.getElementById('vision-qwen-run-btn').textContent === '取得 Qwen 第二意見'"
    )

    assert page.is_enabled("#vision-qwen-run-btn")
    assert page.text_content("#runtime-second-opinion-status") == (
        "Qwen 第二意見失敗；既有 Human Answer 未變更。"
    )
    assert page.evaluate("qwenGetReviewSession().structures") == before


def _sample011_s02_result() -> dict:
    line_id = "S02-L01"
    token_specs = [
        ("34", [54, 225, 95, 257]),
        (".", [95, 230, 108, 252]),
        ("35", [112, 225, 152, 257]),
        (".", [152, 230, 165, 252]),
        ("36", [170, 225, 210, 257]),
        (".", [210, 230, 223, 252]),
        ("38", [228, 225, 268, 257]),
        (" ", [268, 228, 282, 254]),
        ("3", [285, 215, 315, 257]),
        ("/", [315, 225, 332, 257]),
        ("4", [332, 225, 362, 257]),
        ("x", [365, 225, 385, 257]),
        ("1", [388, 225, 415, 257]),
    ]
    tokens = []
    for index, (text, box) in enumerate(token_specs, start=1):
        x1, y1, x2, y2 = box
        tokens.append({
            "token_id": f"{line_id}-T{index:02d}",
            "text": text,
            "start": 0,
            "end": len(text),
            "bounding_box": {
                "coordinate_space": "pixel",
                "polygon": [[x1, y1], [x2, y1], [x2, y2], [x1, y2]],
            },
        })
    result = _job_result()
    result["recognition_id"] = "sample-011-s02-cache"
    result["raw_text"] = "34 . 35 . 36 . 38   3 / 4 x 1"
    result["lines"] = [{
        "line_id": line_id,
        "order": 1,
        "text": result["raw_text"],
        "bounding_box": {
            "coordinate_space": "pixel",
            "polygon": [[54, 215], [415, 215], [415, 257], [54, 257]],
        },
        "tokens": tokens,
        "warnings": [],
    }]
    result["source_image"] = {
        "image_id": "sample-011",
        "sha256": "7f15be60d6876febcd5b455c361a868bad8ddab0c3b7a442d029f0140ad738a6",
        "width": 1000,
        "height": 1000,
    }
    result["preprocessing"]["qwen_request"]["image_sha256"] = result["source_image"]["sha256"]
    result["preprocessing"]["qwen_response"] = {
        "sections": [{
            "shared_multiplier": None,
            "rows": [{
                "tokens": [
                    {"text": text, "bbox": box}
                    for text, box in token_specs
                ],
                "numbers": [["34"], ["35"], ["36"], ["38"]],
                "multiplier": "3/4x1",
                "layout_hint": "row_bet",
            }],
        }],
    }
    return result


def _sample011_s02_structure(
    *,
    status: str = "incomplete",
    rules: list[str] | None = None,
    model_multiplier: str = "3/4x1",
) -> list[dict]:
    return [{
        "line_id": "S02-L01",
        "structure_id": "S02",
        "primary_line_id": "S02-L01",
        "member_line_ids": ["S02-L01"],
        "line_role": "primary",
        "game": "539",
        "model_candidate": {
            "numbers": [["34"], ["35"], ["36"], ["38"]],
            "multiplier": model_multiplier,
            "layout_hint": "row_bet",
            "shared_multiplier": None,
        },
        "reconstructed_candidate": {
            "number_groups": [["34", "35", "36", "38"]],
            "multiplier_rules": ["3/4X1"] if rules is None else rules,
            "layout": "normal_row",
            "collision": None,
            "shared_multiplier": None,
        },
        "status": status,
        "warnings": ["model_layout_unsupported"],
        "evidence": {},
        "needs_review": True,
        "human_confirmation_required": True,
        "auto_apply": False,
        "auto_confirm": False,
        "auto_submit": False,
    }]


def test_new_qwen_result_creates_new_browser_review_session(page) -> None:
    _mount(page)
    _run_qwen(page)
    session = page.evaluate("qwenGetReviewSession()")
    assert session["schema_version"] == "vision-review-session-v1"
    assert session["source_image_id"] == "image-gate3a-1"
    assert session["game"] == "539"


def test_each_primary_structure_creates_exactly_one_bet_card(page) -> None:
    _mount(page)
    _run_qwen(page)
    assert len(page.evaluate("qwenGetReviewSession().structures")) == 2
    assert page.locator(".qwen-review-card").count() == 1
    assert page.locator(".qwen-review-compact-item").count() == 2
    assert page.locator('.qwen-review-card[data-structure-id="S01"]').count() == 1
    assert page.locator('.qwen-review-compact-item[data-structure-id="S02"]').count() == 1


def test_continuation_line_never_creates_an_independent_card(page) -> None:
    _mount(page)
    _run_qwen(page)
    session = page.evaluate("qwenGetReviewSession()")
    assert [card["structure_id"] for card in session["structures"]] == ["S01", "S02"]
    assert session["structures"][1]["source_line_ids"] == ["S02-L01", "S02-L02"]
    assert page.locator('.qwen-review-card[data-structure-id="S02-L02"]').count() == 0


def test_pending_structure_changes_to_confirmed_only_in_session(page) -> None:
    calls = _mount(page)
    _run_qwen(page)
    assert page.get_attribute('.qwen-review-card[data-structure-id="S01"]', "data-review-state") == "pending"
    _confirm_structure(page, "S01")
    assert page.evaluate("qwenGetReviewSession().structures[0].review_state") == "confirmed"
    assert calls["review_confirm"][-1]["human_bet_ids"] == ["H-001"]
    assert page.locator('.qwen-review-card[data-structure-id="S02"]').count() == 1
    assert "已確認 1 / 總共 2" in page.text_content("#qwen-review-progress")


def test_edit_reparse_and_adopt_updates_only_staged_structure(page) -> None:
    _mount(page)
    _run_qwen(page)
    original = page.evaluate("qwenGetRecognitionResult()")
    page.click('.qwen-review-card[data-structure-id="S01"] .qwen-edit-structure')
    page.fill('.qwen-card-editable[data-card-index="0"]', "05 09 17 28 2/3X1")
    page.click('.qwen-card-editable[data-card-index="0"] + div .qwen-card-reparse')
    page.wait_for_selector('.qwen-review-card[data-structure-id="S01"] .qwen-adopt-edit')
    before_adopt = page.evaluate("qwenGetReviewSession().structures[0].staged_structure")
    assert before_adopt["number_groups"] == [["05", "06", "10", "28"]]
    page.click('.qwen-review-card[data-structure-id="S01"] .qwen-adopt-edit')
    staged = page.evaluate("qwenGetReviewSession().structures[0].staged_structure")
    assert staged["number_groups"] == [["05", "09", "17", "28"]]
    assert staged["multiplier_rules"] == ["2/3X1"]
    assert page.evaluate("qwenGetRecognitionResult()") == original


def test_card_manual_reparse_is_explicit_and_register_candidate_false(page) -> None:
    calls = _mount(page)
    _run_qwen(page)
    assert calls["manual"] == []
    page.click('.qwen-review-card[data-structure-id="S01"] .qwen-edit-structure')
    page.fill('.qwen-card-editable[data-card-index="0"]', "05 09 17 28 2/3X1")
    assert calls["manual"] == []
    page.click('.qwen-card-editable[data-card-index="0"] + div .qwen-card-reparse')
    page.wait_for_selector(".qwen-adopt-edit")
    assert calls["manual"] == [{
        "text": "05 09 17 28 2/3X1",
        "game": "539",
        "register_candidate": False,
    }]


def test_whole_review_cannot_finish_until_every_card_is_confirmed(page) -> None:
    _mount(page)
    _run_qwen(page)
    assert page.locator("#qwen-complete-review").count() == 0
    page.click('.qwen-review-card[data-structure-id="S01"] .qwen-confirm-structure')
    assert page.locator("#qwen-complete-review").count() == 0


def test_all_confirmed_cards_create_candidate_preview(page) -> None:
    calls = _mount(page)
    _run_qwen(page)
    _confirm_structure(page, "S01")
    _confirm_structure(page, "S02")
    assert page.is_enabled("#qwen-complete-review")
    summary = _create_candidate(page)
    assert summary["schema_version"] == "vision-candidate-authority-v1"
    assert summary["game"] == "539"
    assert summary["source"]["source_image_id"] == "image-gate3a-1"
    assert summary["source"]["source_image_hash"] == "upload-sha-1"
    assert len(summary["active_bets"]) == 2
    assert set(calls["candidate_create"][0]) == {
        "review_session_id",
        "expected_human_answer_revision",
        "expected_human_answer_hash",
        "idempotency_key",
    }
    assert "Candidate" in page.text_content("#qwen-review-complete-status")
    assert "vc-" + "1" * 32 in page.text_content("#qwen-created-candidate-metadata")


def test_candidate_preview_never_writes_queue(page) -> None:
    calls = _mount(page)
    _run_qwen(page)
    for structure_id in ("S01", "S02"):
        _confirm_structure(page, structure_id)
    before = list(calls["urls"])
    _create_candidate(page)
    new_urls = calls["urls"][len(before):]
    assert new_urls == ["http://gate3a.test/api/vision/v1/candidates"]
    assert not any("queue" in url or "webfill" in url or "assist-fill" in url for url in calls["urls"])


def test_candidate_preview_has_no_accepted_by_human_field(page) -> None:
    _mount(page)
    _run_qwen(page)
    for structure_id in ("S01", "S02"):
        _confirm_structure(page, structure_id)
    _create_candidate(page)
    assert "accepted_by_human" not in json.dumps(page.evaluate("qwenGetReviewSummary()"))
    assert "manual_candidate_id" not in json.dumps(page.evaluate("qwenGetReviewSummary()"))


def test_start_then_cancel_edit_preserves_server_confirmation(page) -> None:
    calls = _mount(page)
    _run_qwen(page)
    _confirm_structure(page, "S01")
    revision = page.evaluate("qwenGetReviewSession().human_answer_revision")
    replace_count = len(calls["review_replace"])
    unconfirm_count = len(calls["review_unconfirm"])

    page.click('.qwen-review-compact-item[data-structure-id="S01"]')
    page.click('.qwen-review-card[data-structure-id="S01"] .qwen-edit-structure')
    assert page.evaluate("qwenGetReviewSession().structures[0].human_confirmed") is True
    page.click('.qwen-review-card[data-structure-id="S01"] .qwen-card-cancel')

    card = page.evaluate("qwenGetReviewSession().structures[0]")
    assert card["human_confirmed"] is True
    assert card["review_state"] == "confirmed"
    assert page.evaluate("qwenGetReviewSession().human_answer_revision") == revision
    assert len(calls["review_replace"]) == replace_count
    assert len(calls["review_unconfirm"]) == unconfirm_count


def test_reload_preserves_stale_candidate_wrapper_state(page) -> None:
    _mount(page)
    _run_qwen(page)
    _confirm_structure(page, "S01")
    _confirm_structure(page, "S02")
    candidate = _create_candidate(page)

    page.click('.qwen-review-compact-item[data-structure-id="S01"]')
    page.check('.qwen-review-card[data-structure-id="S01"] .qwen-card-cancelled')
    _wait_authority_ready(page)
    page.wait_for_selector('#qwen-existing-candidate-stale[data-candidate-state="STALE"]')
    assert candidate["candidate_id"] in json.dumps(page.evaluate("qwenGetReviewSummary()"))

    page.reload()
    page.wait_for_selector('#qwen-existing-candidate-stale[data-candidate-state="STALE"]')
    assert page.locator("#qwen-review-complete-status").count() == 0
    assert page.get_attribute("#qwen-existing-candidate-stale", "data-candidate-state") == "STALE"


def test_adopted_manual_edit_is_retained_in_review_summary(page) -> None:
    calls = _mount(page)
    _run_qwen(page)
    page.click('.qwen-review-card[data-structure-id="S01"] .qwen-edit-structure')
    page.fill('.qwen-card-editable[data-card-index="0"]', "05 09 17 28 2/3X1")
    page.click('.qwen-card-editable[data-card-index="0"] + div .qwen-card-reparse')
    page.wait_for_selector(".qwen-adopt-edit")
    page.click(".qwen-adopt-edit")
    _wait_authority_ready(page)
    _confirm_structure(page, "S01")
    _confirm_structure(page, "S02")
    summary = _create_candidate(page)
    first = summary["active_bets"][0]
    assert first["number_groups"] == [["05", "09", "17", "28"]]
    assert first["multiplier"]["ordered_rules"] == ["2/3X1"]
    assert calls["review_replace"][-1]["bets"][0]["number_groups"] == [["05", "09", "17", "28"]]


def test_uploading_new_image_clears_old_session_state_and_preview(page) -> None:
    _mount(page)
    _run_qwen(page)
    page.click('.qwen-review-card[data-structure-id="S01"] .qwen-confirm-structure')
    old_id = page.evaluate("qwenGetReviewSession().review_session_id")
    page.set_input_files(
        "#vision-file-input",
        {"name": "new.png", "mimeType": "image/png", "buffer": _png_bytes("gray")},
    )
    page.wait_for_function("document.getElementById('vision-filename').textContent === 'slip-2.png'")
    assert page.evaluate("qwenGetReviewSession()") is None
    _run_qwen(page)
    new_session = page.evaluate("qwenGetReviewSession()")
    assert new_session["review_session_id"] != old_id
    assert all(card["review_state"] == "pending" for card in new_session["structures"])
    assert all(card["manual_edits"] == [] for card in new_session["structures"])
    assert new_session["candidate_preview"] is None


def test_deleting_image_clears_review_session(page) -> None:
    _mount(page)
    _run_qwen(page)
    page.click('.qwen-review-card[data-structure-id="S01"] .qwen-confirm-structure')
    page.click("#vision-delete-btn")
    page.wait_for_function(
        "qwenGetReviewSession() === null && document.getElementById('vision-results').style.display === 'none'"
    )
    assert page.evaluate("qwenGetReviewSession()") is None
    assert not page.is_visible("#vision-results")
    assert not page.is_visible("#vision-structure-highlight")


def test_recognition_result_remains_immutable_through_review(page) -> None:
    _mount(page)
    expected = _job_result()
    _run_qwen(page)
    page.click('.qwen-review-card[data-structure-id="S01"] .qwen-confirm-structure')
    page.click('.qwen-review-card[data-structure-id="S02"] .qwen-edit-structure')
    page.click('.qwen-review-card[data-structure-id="S02"] .qwen-card-cancel')
    assert page.evaluate("qwenGetRecognitionResult()") == expected


def test_qwen_failed_ux_is_safe_and_debug_details_are_collapsed(page) -> None:
    _mount(page, failed=True)
    page.click("#vision-qwen-run-btn")
    page.wait_for_selector("#qwen-failure-message")
    assert page.text_content("#qwen-failure-message") == "AI建議不可用，仍可手動輸入。"
    assert page.get_attribute(".qwen-failure-details", "open") is None
    assert "private stack trace" not in page.text_content("#qwen-failure-message")
    session = page.evaluate("qwenGetReviewSession()")
    assert session["structures"] == []
    assert session["provider_failure"]
    assert page.locator("#qwen-add-manual-structure").count() == 1


def test_review_session_and_summary_keep_all_safety_flags(page) -> None:
    _mount(page)
    _run_qwen(page)
    session = page.evaluate("qwenGetReviewSession()")
    assert session["safety"] == {
        "human_confirmation_required": True,
        "auto_apply": False,
        "auto_confirm": False,
        "auto_submit": False,
    }
    for structure_id in ("S01", "S02"):
        _confirm_structure(page, structure_id)
    summary = _create_candidate(page)
    assert summary["safety"] == {
        "candidate_only": True,
        "approved_for_fill": False,
        "queue_written": False,
        "auto_confirm": False,
        "auto_submit": False,
        "webfill_called": False,
    }


def test_review_workflow_never_calls_webfill_or_paid_provider(page) -> None:
    calls = _mount(page)
    _run_qwen(page)
    _confirm_structure(page, "S01")
    _confirm_structure(page, "S02")
    _create_candidate(page)
    assert calls["jobs"] == [{
        "image_id": "image-gate3a-1",
        "provider_id": "runtime-reader-router",
        "game": "539",
        "second_opinion_requested": True,
    }]
    assert not any("webfill" in url or "assist-fill" in url for url in calls["urls"])


def test_main_cards_use_human_status_and_keep_warnings_in_advanced_details(page) -> None:
    _mount(page)
    _run_qwen(page)
    cards_text = page.text_content("#qwen-review-cards")
    assert "AI 結構一致，仍請確認" in cards_text
    page.click('.qwen-review-compact-item[data-structure-id="S02"]')
    cards_text = page.text_content("#qwen-review-cards")
    assert "AI 與規則結果不同，請檢查" in cards_text
    assert "fragment_multiplier_ambiguous" not in page.text_content(
        '.qwen-review-card[data-structure-id="S02"] .qwen-review-human-status'
    )
    advanced = page.text_content(
        '.qwen-review-card[data-structure-id="S02"] .qwen-card-advanced'
    )
    assert "fragment_multiplier_ambiguous" in advanced
    assert page.get_attribute("#qwen-advanced-evidence", "open") is None


def test_all_four_technical_statuses_have_required_human_labels() -> None:
    html = render_vision_ui_section()
    for label in (
        "AI 結構一致，仍請確認",
        "AI 與規則結果不同，請檢查",
        "資料需要人工檢查",
        "此玩法目前需要人工處理",
    ):
        assert label in html


def test_normal_and_column_cards_render_daily_review_shapes(page) -> None:
    _mount(page)
    _run_qwen(page)
    normal = page.locator('.qwen-review-card[data-structure-id="S01"]')
    assert "05 06 10 28" in normal.locator(".qwen-review-numbers").text_content()
    assert "3/4X1" in normal.locator(".qwen-review-play").text_content()
    page.click('.qwen-review-compact-item[data-structure-id="S02"]')
    column = page.locator('.qwen-review-card[data-structure-id="S02"]')
    assert column.locator(".qwen-review-column").all_text_contents() == [
        "24 34", "08 38", "16 36", "03 13",
    ]
    assert "2/3/4X0.1" in column.locator(".qwen-review-play").text_content()
    assert "2/3/4" in column.locator(".qwen-review-collision").text_content()
    page.click('.qwen-review-compact-item[data-structure-id="S01"]')
    page.locator('.qwen-review-card[data-structure-id="S01"] .qwen-edit-structure').click()
    assert page.input_value('.qwen-card-editable[data-card-index="0"]') == (
        "05 06 10 28 三四X1"
    )
    page.click('.qwen-review-card[data-structure-id="S01"] .qwen-card-cancel')
    page.click('.qwen-review-compact-item[data-structure-id="S02"]')
    column = page.locator('.qwen-review-card[data-structure-id="S02"]')
    column.locator(".qwen-edit-structure").click()
    assert page.input_value('.qwen-card-editable[data-card-index="1"]') == (
        "24 34 / 08 38 / 16 36 / 03 13 二三四X0.1"
    )


def test_incomplete_card_keeps_complete_reconstructed_multiplier(page) -> None:
    _mount(
        page,
        job_result=_sample011_s02_result(),
        structure_evidence=_sample011_s02_structure(),
    )
    _run_qwen(page)

    card = page.locator('.qwen-review-card[data-structure-id="S02"]')
    assert "34 35 36 38" in card.locator(".qwen-review-numbers").text_content()
    assert "3/4X1" in card.locator(".qwen-review-play").text_content()
    assert "資料需要人工檢查" in card.locator(
        ".qwen-review-human-status"
    ).text_content()
    session_card = page.evaluate("qwenGetReviewSession().structures[0]")
    assert session_card["source_status"] == "incomplete"
    assert session_card["staged_structure"]["multiplier_rules"] == ["3/4X1"]
    assert session_card["review_state"] == "pending"


def test_incomplete_card_reports_multiplier_missing_only_when_rules_are_empty(page) -> None:
    _mount(
        page,
        job_result=_sample011_s02_result(),
        structure_evidence=_sample011_s02_structure(rules=[]),
    )
    _run_qwen(page)

    card = page.locator('.qwen-review-card[data-structure-id="S02"]')
    play_text = card.locator(".qwen-review-play").text_content()
    assert "未辨識" in play_text
    assert "需要人工處理" in play_text
    assert "3/4X1" not in play_text


def test_divergent_card_stages_reconstruction_and_preserves_both_evidence(page) -> None:
    _mount(
        page,
        job_result=_sample011_s02_result(),
        structure_evidence=_sample011_s02_structure(
            status="divergent",
            model_multiplier="3X1",
        ),
    )
    _run_qwen(page)

    card = page.locator('.qwen-review-card[data-structure-id="S02"]')
    assert "3/4X1" in card.locator(".qwen-review-play").text_content()
    assert "AI 與規則結果不同，請檢查" in card.locator(
        ".qwen-review-human-status"
    ).text_content()
    advanced = card.locator(".qwen-card-advanced").text_content()
    assert '\"multiplier\":\"3X1\"' in advanced
    assert '\"multiplier_rules\":[\"3/4X1\"]' in advanced
    assert page.evaluate("qwenGetReviewSession().structures[0].review_state") == "pending"


def test_sample011_s02_review_is_immutable_and_has_no_side_effect(page) -> None:
    result = _sample011_s02_result()
    calls = _mount(
        page,
        job_result=result,
        structure_evidence=_sample011_s02_structure(),
    )
    _run_qwen(page)

    assert page.evaluate("qwenGetRecognitionResult()") == result
    assert calls["manual"] == []
    assert not any(
        fragment in url
        for url in calls["urls"]
        for fragment in ("queue", "webfill", "assist-fill")
    )
    session = page.evaluate("qwenGetReviewSession()")
    assert session["safety"] == {
        "human_confirmation_required": True,
        "auto_apply": False,
        "auto_confirm": False,
        "auto_submit": False,
    }
    serialized = json.dumps(session)
    assert "accepted_by_human" not in serialized
    assert "manual_candidate_id" not in serialized


def test_card_canonical_text_uses_existing_parser_contract() -> None:
    normal = reparse_text("05 06 10 28 三四X1", game="539")
    assert normal["ok"] is True
    assert normal["numbers"] == [5, 6, 10, 28]
    assert normal["stars"] == [3, 4]
    assert normal["type"] == "normal"

    column = reparse_text(
        "24 34 / 08 38 / 16 36 / 03 13 二三四X0.1",
        game="539",
    )
    assert column["ok"] is True
    assert column["columns"] == [[24, 34], [8, 38], [16, 36], [3, 13]]
    assert column["stars"] == [2, 3, 4]
    assert column["type"] == "column"


def test_clicking_card_highlights_only_reliable_structure_bbox(page) -> None:
    _mount(page)
    _run_qwen(page)
    page.click('.qwen-review-compact-item[data-structure-id="S02"]')
    assert page.is_visible("#vision-structure-highlight")
    assert float(page.eval_on_selector("#vision-structure-highlight", "el => parseFloat(el.style.height)")) > 0
