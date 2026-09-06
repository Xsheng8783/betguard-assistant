"""Real loopback HTTP -> panel -> existing worker -> B03 fixture -> DOM readback.

Synthetic numbers; multiplier/category and tail expansion rules are those in
the existing confirmed-text and parser contracts. No generated parser oracle.
"""
import json
import threading
from contextlib import contextmanager
from urllib.parse import urlparse

import pytest
from playwright.sync_api import sync_playwright
from betguard.webui import app
from betguard.webfill import web_assist_session as session


@contextmanager
def local_app(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr(app, "_manual_candidates", {})
    monkeypatch.setattr(session, "_worker", None)
    monkeypatch.setenv("BETGUARD_SKIP_LICENSE", "1")
    monkeypatch.setenv("BETGUARD_LOCAL_FILL_HEADLESS", "1")
    handler = app.build_workbench_handler(project_version="test", git_commit="test")
    server = app.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    url = f"http://127.0.0.1:{server.server_address[1]}"
    monkeypatch.setenv("BETGUARD_LOCAL_FILL_URL", url)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    observed = []
    for method in ("_handle_execute_fill", "_handle_zhu_peng_execute"):
        original = getattr(session._AssistWorker, method)
        def wrapper(self, *args, _original=original):
            _original(self, *args)
            if self._page:
                observed.append(self._page.frames[3].evaluate("""() => ({
                  groups:Mo.ZhuPengMgr.Zhus().map(z=>z.Data()),
                  amounts:[...document.querySelectorAll('input')].map(el=>el.value),
                  game:$Global.GameID, submitted:window.submitCount
                })"""))
        monkeypatch.setattr(session._AssistWorker, method, wrapper)
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            context = browser.new_context()
            context.route("**/*", lambda r: r.continue_() if urlparse(r.request.url).hostname == "127.0.0.1" else r.abort())
            page = context.new_page()
            page.goto(url + "/assist-panel")
            yield page, observed
            browser.close()
    finally:
        if session._worker:
            session._worker.dispatch(session.CMD_CLOSE, None)
            session._worker._running = False
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


CASES = [
    ("01 12 23 2×0.1", "539", [[1,12,23]], [10,0,0]),
    ("01 12 23 2×5 3×0.5", "539", [[1,12,23]], [500,50,0]),
    ("01 × 12 × 23 33 2×3 3×1", "539", [[1],[12],[23,33]], [300,100,0]),
    ("01 × 12 × 23 2×0.5", "539", [[1],[12],[23]], [50,0,0]),
    ("01 × 12 × 23 33 2×3\n3×1", "539", [[1],[12],[23,33]], [300,100,0]),
    ("02 × 13 × 6尾 2,3×0.5", "539", [[2],[13],[6,16,26,36]], [50,50,0]),
    ("01　12，23 2/3X1", "539", [[1,12,23]], [100,100,0]),
    ("01 40 49 2×1", "六合", [[1,40,49]], [100,0,0]),
]


@pytest.mark.parametrize("text,game,groups,amounts", CASES)
def test_actual_text_parse_preview_fill_readback(tmp_path, monkeypatch, text, game, groups, amounts):
    with local_app(tmp_path, monkeypatch) as (page, observed):
        page.select_option("#assist-game", game)
        page.fill("#batch-text", text)
        with page.expect_response("**/assist-panel/create-batch") as response:
            page.click("#createBatchBtn")
        candidate = response.value.json()["valid_candidates"][0]
        assert candidate["game"] == game
        if candidate["type"] == "column":
            assert candidate["columns"] == groups
            assert " × " in page.inner_text("#valid-items")
        else:
            assert candidate["numbers"] == groups[0]
        assert candidate["amounts"] == {str(i+2): v for i,v in enumerate(amounts) if v}
        assert game in page.inner_text("#valid-items")
        with page.expect_response("**/assist-fill/start", timeout=30000) as fill:
            page.click("#valid-items .assist-fill-btn")
        result = fill.value.json()
        assert result.get("ok") is True, result
        assert result["numbers_verified"] and result["amounts_verified"] and result["game_verified"]
        page.wait_for_selector(".mark-done-btn")
        assert not page.locator(".assist-completed").count()  # never auto-confirm
        actual = observed[-1]
        assert actual["groups"] == [[f"{n:02d}" for n in g] for g in groups] + [[] for _ in range(7-len(groups))]
        assert [int(v or 0) for v in actual["amounts"]] == amounts
        assert actual["game"] == (13 if game == "539" else 11)
        assert actual["submitted"] == 0
        # Same request cannot fill twice, including after a lost HTTP response.
        request = fill.value.request.post_data_json
        again = page.request.post(page.url.split("/assist-panel")[0]+"/assist-fill/start", data=request).json()
        assert again["ok"] is False
        assert len(observed) == 1


@pytest.mark.parametrize("text,count", [("01半車",1), ("01車1",1), ("01 12 各半車",2), ("01 12 各2車",2)])
def test_car_parses_but_unmapped_fill_stays_disabled(tmp_path,monkeypatch,text,count):
    with local_app(tmp_path,monkeypatch) as (page,observed):
        page.fill("#batch-text",text)
        page.click("#createBatchBtn")
        page.wait_for_selector("#valid-items .item")
        assert page.locator("#valid-items .item").count()==count
        assert page.locator("#valid-items .assist-fill-btn:disabled").count()==count
        assert "尚未支援" in page.inner_text("#valid-items")
        assert page.inner_text("#valid-count")=="0"
        assert not observed


def test_edit_reparse_and_game_change_invalidates_old_result(tmp_path,monkeypatch):
    with local_app(tmp_path,monkeypatch) as (page,observed):
        page.select_option("#assist-game","六合")
        page.fill("#batch-text","01 × 40 × 49 2×?")
        page.click("#createBatchBtn")
        page.wait_for_selector("#review-items .item")
        page.locator("#review-items button").filter(has_text="編輯").click()
        page.fill("#review-items textarea","01 × 40 × 49 2×0.5")
        with page.expect_response("**/manual-reparse") as response:
            page.locator("#review-items button").filter(has_text="重新解析").click()
        assert response.value.json()["game"]=="六合"
        with page.expect_response("**/assist-fill/start",timeout=30000) as response:
            page.click("#valid-items .assist-fill-btn")
        assert response.value.json()["ok"] is True
        assert observed[-1]["groups"][:3]==[["01"],["40"],["49"]]
        assert observed[-1]["game"]==11
        page.select_option("#assist-game","539")
        assert page.locator("#valid-items .assist-fill-btn").is_disabled()
        page.fill("#batch-text","01 40 49 2×1")
        page.click("#createBatchBtn")
        page.wait_for_selector("#review-items .item")
        assert not page.locator("#valid-items .assist-fill-btn").count()


def test_reparse_preserves_other_completed_card_and_original_whitespace(tmp_path,monkeypatch):
    with local_app(tmp_path,monkeypatch) as (page,observed):
        raw="  01 12 23 2×1\n\n02 13 24 二星\n"
        page.fill("#batch-text",raw)
        with page.expect_response("**/assist-panel/create-batch") as response:
            page.click("#createBatchBtn")
        assert response.value.request.post_data_json["text"]==raw
        page.wait_for_selector("#review-items .item")
        with page.expect_response("**/assist-fill/start",timeout=30000) as fill:
            page.click("#valid-items .assist-fill-btn")
        assert fill.value.json()["ok"] is True
        page.wait_for_selector(".mark-done-btn")
        page.click(".mark-done-btn")
        assert page.locator(".assist-completed").count()==1
        page.locator("#review-items button").filter(has_text="編輯").click()
        edited=" 02 13 24 2×0.5\n"
        page.fill("#review-items textarea",edited)
        with page.expect_response("**/manual-reparse") as response:
            page.locator("#review-items button").filter(has_text="重新解析").click()
        assert response.value.request.post_data_json["text"]==edited
        page.wait_for_function("panelState.reviewCandidates.length===0")
        assert page.locator(".assist-completed").count()==1
        assert page.locator(".assist-completed .assist-fill-btn").is_disabled()
        assert page.locator("#valid-items .item").count()==2
        assert len(observed)==1


@pytest.mark.parametrize("column",[False,True])
def test_partial_amount_failure_is_not_success_or_retried(tmp_path,monkeypatch,column):
    with local_app(tmp_path,monkeypatch) as (page,observed):
        original=session._AssistWorker._handle_check_ready
        def break_field(self,result):
            original(self,result)
            self._page.frames[3].evaluate("""() => {
              const el=document.querySelectorAll('input')[1];
              el.addEventListener('change',()=>{el.value='';ko.contextFor(el).$data.PengBet.Value('');});
            }""")
        monkeypatch.setattr(session._AssistWorker,"_handle_check_ready",break_field)
        page.fill("#batch-text","01 × 12 × 23 33 2×3 3×1" if column else "01 12 23 2×3 3×1")
        page.click("#createBatchBtn")
        page.wait_for_selector("#valid-items .assist-fill-btn")
        with page.expect_response("**/assist-fill/start",timeout=30000) as response:
            page.click("#valid-items .assist-fill-btn")
        result=response.value.json()
        assert result["ok"] is False
        assert result["amounts_verified"] is False
        assert not page.locator(".mark-done-btn").count()
        assert len(observed)==1 and observed[0]["submitted"]==0
        page.wait_for_timeout(300)
        assert len(observed)==1


def test_double_click_explicit_refill_and_identical_bets(tmp_path,monkeypatch):
    with local_app(tmp_path,monkeypatch) as (page,observed):
        page.fill("#batch-text","01 12 23 2×1\n01 12 23 2×1")
        page.click("#createBatchBtn")
        page.wait_for_selector("#valid-items .assist-fill-btn")
        assert page.locator("#valid-items .item").count()==2
        with page.expect_response("**/assist-fill/start",timeout=30000) as response:
            page.evaluate("""() => {const b=document.querySelector('.assist-fill-btn');assistPanelFillBtn(b);assistPanelFillBtn(b);}""")
        assert response.value.json()["ok"] is True
        page.wait_for_selector(".mark-done-btn")
        assert len(observed)==1
        with page.expect_response("**/assist-fill/start",timeout=30000) as response:
            page.locator(".assist-fill-btn").first.click()
        assert response.value.json()["ok"] is True
        assert response.value.request.post_data_json["refill"] is True
        page.wait_for_function("!fillBusy")
        with page.expect_response("**/assist-fill/start",timeout=30000) as response:
            page.locator(".assist-fill-btn").nth(1).click()
        assert response.value.json()["ok"] is True
        assert len(observed)==3
        assert all(x["submitted"]==0 for x in observed)


def test_readback_rejects_different_numbers_with_equal_column_lengths(tmp_path,monkeypatch):
    from betguard.webfill import fill_readback
    original=fill_readback.sync_column
    def wrong_column(page,wanted):
        original(page,[9] if wanted==[1] else wanted)
    monkeypatch.setattr(fill_readback,"sync_column",wrong_column)
    with local_app(tmp_path,monkeypatch) as (page,observed):
        page.fill("#batch-text","01 × 12 × 23 33 2×1")
        page.click("#createBatchBtn")
        page.wait_for_selector(".assist-fill-btn")
        with page.expect_response("**/assist-fill/start",timeout=30000) as response:
            page.click(".assist-fill-btn")
        result=response.value.json()
        assert result["ok"] is False and result["numbers_verified"] is False
        assert result["readback_columns"][:3]==[["09"],["12"],["23","33"]]
        assert observed[-1]["submitted"]==0


def test_second_normal_bet_clears_previous_numbers_and_unused_amounts(tmp_path,monkeypatch):
    with local_app(tmp_path,monkeypatch) as (page,observed):
        page.fill("#batch-text","01 12 23 2×5 3×0.5\n02 13 24 2×0.1")
        page.click("#createBatchBtn")
        page.wait_for_selector(".assist-fill-btn")
        for i in range(2):
            with page.expect_response("**/assist-fill/start",timeout=30000) as response:
                page.locator(".assist-fill-btn").nth(i).click()
            assert response.value.json()["ok"] is True
            page.wait_for_function("!fillBusy")
        assert observed[-1]["groups"]==[["02","13","24"]]+[[] for _ in range(6)]
        assert [int(n or 0) for n in observed[-1]["amounts"]]==[10,0,0]


def test_wrong_form_game_is_blocked_before_any_write(tmp_path,monkeypatch):
    with local_app(tmp_path,monkeypatch) as (page,observed):
        original=session._AssistWorker._handle_check_ready
        def change_game(self,result):
            original(self,result)
            self._page.frames[3].evaluate("window.$Global.GameID=13")
        monkeypatch.setattr(session._AssistWorker,"_handle_check_ready",change_game)
        page.select_option("#assist-game","六合")
        page.fill("#batch-text","01 40 49 2×1")
        page.click("#createBatchBtn")
        page.wait_for_selector(".assist-fill-btn")
        with page.expect_response("**/assist-fill/start",timeout=30000) as response:
            page.click(".assist-fill-btn")
        result=response.value.json()
        assert result["ok"] is False and result["game_verified"] is False
        assert all(not g for g in observed[-1]["groups"])
        assert all(not v for v in observed[-1]["amounts"])
