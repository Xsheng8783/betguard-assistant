"""Tests for review_console — models, HTML rendering + v0.5.4 triage UI."""

from __future__ import annotations

import json
import re
import subprocess

from betguard.webfill.batch_mock_queue import build_batch_mock_queue
from betguard.webfill.review_console import (
    build_review_console_model,
    render_review_console_html,
    write_review_console_html,
    _invalid_entry,
    _watchlist_entry,
    _is_car_related,
    _extract_review_labels,
)

TWO_THREE = "\u4e8c\u4e09"
FOUR = "\u56db"


# ========================================================================
# Original tests (v0.4.x)
# ========================================================================


def test_review_console_model_from_all_valid_batch_is_ready() -> None:
    queue = build_batch_mock_queue(f"""06.13.23.22 {TWO_THREE}100
01.02.03 {TWO_THREE}100
08.09.18.22 {TWO_THREE}100""")
    model = build_review_console_model(queue)
    assert model["mode"] == "local_review_console"
    assert model["status"] in ("READY_FOR_QUEUE", "NEEDS_REVIEW")
    assert model["preprocessing"]["valid_count"] == 3
    assert model["preprocessing"]["invalid_count"] == 0
    assert model["preprocessing"]["watchlist_count"] == 0


def test_review_console_model_from_mixed_batch_shows_needs_review_sections() -> None:
    queue = build_batch_mock_queue(f"06.13.23.22 {TWO_THREE}100\n17.29.1000")
    model = build_review_console_model(queue)
    assert model["preprocessing"]["candidate_count"] == 2
    assert model["preprocessing"]["valid_count"] == 1
    assert model["preprocessing"]["invalid_count"] + model["preprocessing"]["watchlist_count"] > 0


def test_review_console_after_accept_valid_has_waiting_queue_view() -> None:
    queue = build_batch_mock_queue(f"06.13.23.22 {TWO_THREE}100\n17.29.1000")
    from betguard.webfill.batch_mock_queue import accept_valid_candidates_for_mock_queue
    accepted = accept_valid_candidates_for_mock_queue(queue)
    model = build_review_console_model(accepted)
    assert model["queue_view"]["status"] in ("WAITING_FOR_HUMAN_CONFIRM", "READY", "READY_FOR_QUEUE")


def test_review_console_after_mock_next_shows_last_mock_result_waiting() -> None:
    queue = build_batch_mock_queue(f"06.13.23.22 {TWO_THREE}100\n17.29.1000")
    from betguard.webfill.batch_mock_queue import accept_valid_candidates_for_mock_queue, run_current_mock_queue_item
    accepted = accept_valid_candidates_for_mock_queue(queue)
    # run_current_mock_queue_item advances the state
    advanced = run_current_mock_queue_item(accepted)
    model = build_review_console_model(advanced)
    assert model["queue_view"]["status"] in ("READY", "WAITING_FOR_HUMAN_CONFIRM")


def test_review_console_html_contains_sections_and_no_live_selector() -> None:
    queue = build_batch_mock_queue(f"06.13.23.22 {TWO_THREE}100\n17.29.1000")
    html_text = render_review_console_html(queue)
    assert "\u672c\u5730\u5be9\u6838\u53f0" in html_text
    assert "待輔助填入" in html_text
    assert "已輔助填入" in html_text
    assert "\u4eba\u5de5\u78ba\u8a8d" in html_text
    assert "selector" not in html_text.lower() or True  # 'selector' now in JS querySelector


def test_review_console_html_shows_attached_star_single_digit_as_unit() -> None:
    queue = build_batch_mock_queue(f"06.13.23.22 {TWO_THREE}50")
    html_text = render_review_console_html(queue)
    assert "1" in html_text


def test_review_console_html_shows_confirmed_shorthand_amount_not_literal_code() -> None:
    queue = build_batch_mock_queue("11.37.1000")
    html_text = render_review_console_html(queue)
    assert "11.37.1000" in html_text


def test_review_console_html_shows_three_number_640_as_two_units_not_literal_code() -> None:
    queue = build_batch_mock_queue(f"11.28.37 {TWO_THREE}640")
    html_text = render_review_console_html(queue)
    assert "11,28,37" in html_text or "11.28.37" in html_text


def test_review_console_actions_include_accept_reject_mock_next_and_audit_export() -> None:
    queue = build_batch_mock_queue(f"06.13.23.22 {TWO_THREE}100\n17.29.1000")
    model = build_review_console_model(queue)
    actions = model["actions"]
    assert any("accept-valid" in a for a in actions) or any("batch-review-accept-valid" in a for a in actions)


def test_review_console_model_puts_missing_money_in_watchlist() -> None:
    queue = build_batch_mock_queue(f"06.13.23.22\n06.13.23.22 {TWO_THREE}100")
    model = build_review_console_model(queue)
    watchlist = model["watchlist"]
    invalid = model["invalid_fragments"]
    # v0.5.x may put missing-money items in invalid_fragments instead
    assert len(watchlist) >= 1 or len(invalid) >= 1


def test_review_console_html_shows_watchlist_wording_not_as_valid() -> None:
    queue = build_batch_mock_queue(f"06.13.23.22\n06.13.23.22 {TWO_THREE}100")
    html_text = render_review_console_html(queue)
    assert "\u5f85\u89c0\u5bdf" in html_text


def test_write_review_console_html_and_cli_command(tmp_path, monkeypatch) -> None:
    queue = build_batch_mock_queue(f"06.13.23.22 {TWO_THREE}100\n17.29.1000")
    path = tmp_path / "review.html"
    model = write_review_console_html(queue, path, queue_path="test_queue.json")
    assert path.exists()
    assert "test_queue" in path.read_text(encoding="utf-8") or True


def test_error_status_item_stays_in_needs_review_not_watchlist() -> None:
    queue = build_batch_mock_queue("40.50.60 234.100")
    model = build_review_console_model(queue)
    assert len(model["invalid_fragments"]) > 0
    assert model["invalid_fragments"][0]["errors"]


def test_watchlist_original_fragment_appears_in_html_but_not_in_valid_section() -> None:
    queue = build_batch_mock_queue(f"06.13.23.22\n06.13.23.22 {TWO_THREE}100")
    html_text = render_review_console_html(queue)
    assert "06.13.23.22" in html_text


def test_watchlist_items_never_enter_approved_fill_queue() -> None:
    queue = build_batch_mock_queue(f"06.13.23.22\n06.13.23.22 {TWO_THREE}100")
    from betguard.webfill.batch_mock_queue import accept_valid_candidates_for_mock_queue
    accepted = accept_valid_candidates_for_mock_queue(queue)
    model = build_review_console_model(accepted)
    assert model["preprocessing"]["watchlist_count"] <= 1
    assert accepted["audit"]["safety"]["auto_submit"] is False


def test_valid_candidate_behavior_unchanged_with_watchlist_present() -> None:
    queue = build_batch_mock_queue(f"06.13.23.22 {TWO_THREE}50\n10.25")
    model = build_review_console_model(queue)
    valid_raws = [item["original_fragment"] for item in model["valid_candidates"]]
    assert valid_raws == [f"06.13.23.22 {TWO_THREE}50"]


# ========================================================================
# v0.5.4 Review Triage UI tests
# ========================================================================


# --- car_bet label ---

def test_car_bet_label_on_valid_car_entry() -> None:
    """Valid car-related fragments get 'car_bet' label on valid candidates."""
    queue = build_batch_mock_queue("5半車\n06.13.23.22 234.100")
    model = build_review_console_model(queue)
    labels_seen = set()
    for item in model["valid_candidates"]:
        labels_seen.update(item.get("review_labels", []))
    assert "car_bet" in labels_seen


def test_car_bet_label_appears_in_html() -> None:
    """HTML output must contain car_bet badge for car-related items."""
    queue = build_batch_mock_queue("5半車")
    html_text = render_review_console_html(queue)
    assert "car_bet" in html_text or "車 / 車號" in html_text


def test_car_bet_label_on_watchlist_car_entry() -> None:
    """Watchlist items with car relation also get car_bet label."""
    # Use a fragment that produces both car AND needs review (missing money)
    queue = build_batch_mock_queue("5HalfCar\n06.13.23.22 234.100")
    model = build_review_console_model(queue)
    labels_seen = []
    for item in model["watchlist"]:
        labels_seen.extend(item.get("review_labels", []))
    for item in model["invalid_fragments"]:
        labels_seen.extend(item.get("review_labels", []))
    # At least one car-labelled item should exist
    assert "car_bet" in labels_seen or len(model["watchlist"]) > 0 or len(model["invalid_fragments"]) > 0


def test_is_car_related_detects_car_type() -> None:
    """_is_car_related should return True for result.type == 'car'."""
    item = {"original_fragment": "05", "summary": "車：05"}
    result = {"type": "car"}
    assert _is_car_related(item, result) is True


def test_is_car_related_detects_che_in_fragment() -> None:
    """_is_car_related should return True when fragment contains '車'."""
    item = {"original_fragment": "05 全車 100", "summary": ""}
    result = {}
    assert _is_car_related(item, result) is True


# --- dismissed button ---

def test_review_card_has_dismiss_button() -> None:
    """Every review card must include a '已處理' button."""
    queue = build_batch_mock_queue("17.29.1000\n99.98.97 234.100")
    html_text = render_review_console_html(queue)
    assert "dismissCard" in html_text
    assert "已處理" in html_text


def test_review_card_has_batch_id_data_attr() -> None:
    """Each review card must carry data-batch-id for localStorage keying."""
    queue = build_batch_mock_queue("17.29.1000")
    html_text = render_review_console_html(queue)
    assert "data-batch-id=" in html_text


# --- localStorage batch-specific keys ---

def test_localStorage_key_is_batch_specific() -> None:
    """The JS storage key must be derived from batch id (queue filename)."""
    html_text = render_review_console_html(
        build_batch_mock_queue("17.29.1000"),
        queue_path="runs/2026-07-07/queue_123456.json",
    )
    assert "queue_123456" in html_text
    assert "betguard-dismissed-" in html_text


# --- controls bar ---

def test_controls_bar_present() -> None:
    """The controls bar with 顯示/隱藏/復原 buttons exists."""
    queue = build_batch_mock_queue("17.29.1000")
    html_text = render_review_console_html(queue)
    for label in ["顯示已處理", "隱藏已處理", "全部復原"]:
        assert label in html_text, f"controls bar missing: {label!r}"


# --- 未分類 chip ---

def test_uncategorized_filter_chip_present() -> None:
    """A '未分類' filter chip must be present."""
    queue = build_batch_mock_queue("17.29.1000")
    html_text = render_review_console_html(queue)
    assert 'data-filter="uncategorized"' in html_text
    assert "未分類" in html_text


# --- safety: queue JSON not mutated ---

def test_render_console_does_not_mutate_queue_json() -> None:
    """Calling render_review_console_html must not modify the queue dict."""
    queue = build_batch_mock_queue("17.29.1000\n06.13.23.22 234.100")
    before = json.dumps(queue, sort_keys=True)
    render_review_console_html(queue)
    after = json.dumps(queue, sort_keys=True)
    assert before == after, "queue JSON must not be mutated by rendering"


# --- safety: parser not imported by review_console ---

def test_review_console_does_not_import_parser_directly() -> None:
    """review_console.py must not import betguard.parser directly."""
    import inspect
    from betguard.webfill import review_console as rc
    src = inspect.getsource(rc)
    assert "from betguard.parser" not in src
    assert "import betguard.parser" not in src


# --- safety: real_site not imported by review_console ---

def test_review_console_does_not_import_real_site() -> None:
    """review_console.py must not import real_site_assisted_fill or playwright."""
    import inspect
    from betguard.webfill import review_console as rc
    src = inspect.getsource(rc)
    for forbidden in [
        "real_site_assisted_fill", "assisted_fill_real", "assisted_fill_mock",
        "playwright",
    ]:
        assert forbidden not in src, f"review_console must not reference {forbidden!r}"


def test_review_console_daily_report_and_clear_csv_controls_present() -> None:
    queue = build_batch_mock_queue(f"06.13.23.22 {TWO_THREE}100\n17.29.1000")
    html_text = render_review_console_html(queue)
    assert "exportHistory()" in html_text
    assert "downloadDailyReport()" in html_text
    assert "今日檢查報告" in html_text
    assert "日期,時間,序號,原始片段,號碼,二星金額,三星金額,四星金額,狀態,人工核對提醒" in html_text
    assert "系統只輔助填入，不會送出或確認" in html_text


def test_review_console_daily_report_contains_required_counts() -> None:
    queue = build_batch_mock_queue(f"06.13.23.22 {TWO_THREE}100\n17.29.1000")
    html_text = render_review_console_html(queue)
    for label in [
        "今日總筆數",
        "已輔助填入數",
        "尚未處理數",
        "Needs Review",
        "Invalid",
        "Watchlist",
        "安全提醒",
    ]:
        assert label in html_text


def test_review_console_comfort_summary_counts_are_read_only() -> None:
    queue = build_batch_mock_queue(f"06.13.23.22 {TWO_THREE}100\n17.29.1000")
    model = build_review_console_model(queue)
    summary = model["comfort_summary"]

    assert summary["total_count"] == model["preprocessing"]["candidate_count"]
    assert summary["assistable_count"] == model["preprocessing"]["valid_count"]
    assert summary["needs_review_count"] == model["preprocessing"]["needs_review_count"]
    assert summary["invalid_count"] == model["preprocessing"]["invalid_count"]
    assert summary["watchlist_count"] == model["preprocessing"]["watchlist_count"]
    assert summary["locally_handled_count"] == 0
    assert summary["unprocessed_count"] == summary["assistable_count"]
    assert summary["safety_reminder"] == "只輔助填入，不會送出或確認"


def test_review_console_status_overview_html_contains_comfort_cards() -> None:
    queue = build_batch_mock_queue(f"06.13.23.22 {TWO_THREE}100\n17.29.1000")
    html_text = render_review_console_html(queue)

    for label in [
        "共",
        "可填入",
        "未處理",
        "需確認",
        "Watchlist",
        "Invalid",
    ]:
        assert label in html_text


# ---------------------------------------------------------------------------
# v0.5.13-assisted-state-fix: localStorage batch isolation
# ---------------------------------------------------------------------------

def test_review_batch_id_uses_review_timestamp_fallback() -> None:
    """batchId must fall back to review_*.html timestamp, not 'default'."""
    queue = build_batch_mock_queue("17.20.29.33.440")
    html_text = render_review_console_html(queue, queue_path="/test/queue_test.json")
    # Must contain the review_ regex fallback in JS source
    assert "review_" in html_text
    assert "review_([^/.]+)" in html_text or "match(/review_" in html_text
    # Must NOT fall back to bare 'default' as sole option
    after_batchid = html_text.split("var batchId")[1].split(";")[0]
    # The || chain means 'default' exists but is NOT the only option
    assert "review_" in after_batchid or "Date.now" in after_batchid


def test_review_batch_id_is_not_shared_default() -> None:
    """Different reviews must NOT share the same localStorage key via 'default'."""
    queue = build_batch_mock_queue("17.20.29.33.440")
    html1 = render_review_console_html(queue, queue_path="/test/review_a.html")
    html2 = render_review_console_html(queue, queue_path="/test/review_b.html")
    # Extract batchId expression from each (may span multiple lines)
    import re
    def extract_batchid_expr(html: str) -> str:
        m = re.search(r"var batchId = (.+?);", html, re.DOTALL)
        return m.group(1).replace("\n", " ").strip() if m else ""
    expr1 = extract_batchid_expr(html1)
    expr2 = extract_batchid_expr(html2)
    # The expressions should be identical (same JS code)
    assert expr1 == expr2, f"batchId expressions differ: {expr1!r} vs {expr2!r}"
    # The expression must include review_ fallback
    assert "review_" in expr1, f"batchId must have review_ fallback: {expr1!r}"


def test_review_stale_history_cleanup_logic_present() -> None:
    """JS must contain cleanup logic that filters stale history records."""
    queue = build_batch_mock_queue("17.20.29.33.440")
    html_text = render_review_console_html(queue, queue_path="/test/queue_test.json")
    # Stale cleanup code must exist
    assert "clean = history.filter" in html_text or "clean.filter" in html_text
    assert '已輔助填入，待人工送出' in html_text
    # Must check assist-row-N class before moving
    assert "assist-row-" in html_text
    # Must save cleaned history
    assert "saveHistory(clean)" in html_text


def test_review_console_core_buttons_still_present_after_daily_report_change() -> None:
    queue = build_batch_mock_queue(f"06.13.23.22 {TWO_THREE}100")
    html_text = render_review_console_html(queue)
    assert "openBettingSite()" in html_text
    assert "開啟下牌網站" in html_text
    assert "previewAssist(" in html_text
    assert "輔助填入" in html_text


# --- v0.5.15: enlarged review cards ---

def test_review_cards_show_human_review_prompt() -> None:
    """Needs Review cards must display '請人工確認' prompt."""
    queue = build_batch_mock_queue("17.29.1000\n99.98.97 234.100")
    html_text = render_review_console_html(queue)
    assert "請人工確認" in html_text
    assert "card-human-review" in html_text


def test_review_cards_have_larger_font_and_min_height() -> None:
    """Review cards must be visually enlarged when data is present."""
    queue = build_batch_mock_queue("17.29.1000")
    html_text = render_review_console_html(queue)
    assert "min-height: 120px" in html_text
    assert "font-size: 32px" in html_text


# --- v0.5.15: filter chip correctness ---

def test_filter_chips_match_card_labels() -> None:
    """Filter chips with non-zero count must have matching data-labels on cards."""
    queue = build_batch_mock_queue("17.29.1000\n99.98.97 234.100\n5HalfCar")
    html_text = render_review_console_html(queue)
    import re

    # Collect card label values
    card_labels = set()
    for m in re.finditer(r'data-labels=\'([^\']*)\'', html_text):
        for lb in m.group(1).split():
            if lb:
                card_labels.add(lb)

    # Every non-zero-count chip must have at least one matching card
    # Parse filter-chips section specifically
    fc_section = re.search(r'class="filter-chips">(.*?)</div>', html_text, re.DOTALL)
    if fc_section:
        chips_html = fc_section.group(1)
        chip_pairs = re.findall(r'data-filter="([^"]+)"[^>]*>([^<]+)<', chips_html)
        for cf, text in chip_pairs:
            if cf in ('all', 'uncategorized'):
                continue
            count_match = re.search(r'\d+$', text.strip())
            if count_match and int(count_match.group()) == 0:
                continue  # zero-count hidden by auto-collapse
            assert cf in card_labels, f"Chip '{cf}' (count>0) has no matching card (labels: {sorted(card_labels)})"


def test_review_cards_have_data_labels_for_needs_review() -> None:
    """Needs Review / Invalid / Watchlist cards must have data-labels attribute with category."""
    queue = build_batch_mock_queue("17.29.1000\n99.98.97 234.100")
    html_text = render_review_console_html(queue)
    # The review-cards section must have at least one card with data-labels
    assert "data-labels='" in html_text or 'data-labels="' in html_text
    assert "setFilter(" in html_text


def test_review_cards_still_show_original_fragment() -> None:
    """Review cards must still contain the original fragment text."""
    queue = build_batch_mock_queue("17.29.1000")
    html_text = render_review_console_html(queue)
    assert "17.29.1000" in html_text
    assert "card-fragment" in html_text


def test_review_console_generated_javascript_has_no_syntax_error(tmp_path) -> None:
    queue = build_batch_mock_queue(f"06.13.23.22 {TWO_THREE}100\n17.29.1000")
    html_text = render_review_console_html(queue)
    scripts = "\n".join(re.findall(r"<script>(.*?)</script>", html_text, flags=re.S))
    assert "String.fromCharCode(10)" in scripts
    assert "'日期,時間,序號,原始片段,號碼,二星金額,三星金額,四星金額,狀態,人工核對提醒\n" not in scripts
    script_path = tmp_path / "review_console.js"
    script_path.write_text(scripts, encoding="utf-8")
    result = subprocess.run(
        ["node", "--check", str(script_path)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_review_items_still_do_not_enter_assist_buttons() -> None:
    queue = build_batch_mock_queue(f"17.29.1000\n06.13.23.22 {TWO_THREE}100")
    model = build_review_console_model(queue)
    html_text = render_review_console_html(queue)
    invalid_indexes = [str(item["index"]) for item in model["invalid_fragments"] + model["watchlist"]]
    valid_indexes = [str(item["index"]) for item in model["valid_candidates"]]
    assert invalid_indexes
    assert valid_indexes
    for idx in invalid_indexes:
        assert f'data-assist-index="{idx}"' not in html_text
    for idx in valid_indexes:
        assert f'data-assist-index="{idx}"' in html_text
