import inspect

from betguard import ui_review
from betguard.ui_review import (
    NO_BET_CONTENT_WARNING,
    build_next_step_state,
    build_ui_review_summary,
    sanitize_review_input,
)


CAR = "\u8eca"
YUAN = "\u5143"
TWO = "\u4e8c"
DALETOU = "\u5927\u6a02"


def test_review_summary_has_fields_used_by_streamlit_ui() -> None:
    data = build_ui_review_summary("\n".join(["06-13-23-22/50", f"32{CAR}10{YUAN}"]))

    assert data["total"] == 2
    assert data["ok"] == 2
    assert data["warning"] == 0
    assert data["error"] == 0
    assert data["can_continue"] is True
    assert data["items"][0]["line_no"] == 1
    assert data["items"][0]["raw"] == "06-13-23-22/50"
    assert data["items"][0]["result"]["type"] == "normal"
    assert data["items"][1]["result"]["type"] == "car"
    assert data["items"][1]["result"]["car_units"] == 0.1


def test_ui_logic_accepts_game_parameter() -> None:
    data = build_ui_review_summary(f"40.41 {TWO}100", game=DALETOU)

    assert data["items"][0]["result"]["game"] == DALETOU
    assert data["items"][0]["result"]["status"] == "ok"


def test_ui_logic_539_blocks_40() -> None:
    data = build_ui_review_summary(f"39.40 {TWO}100", game="539")
    result = data["items"][0]["result"]

    assert result["status"] == "error"
    assert "number out of range 40; valid range is 1-39" in result["errors"]
    assert data["can_continue"] is False


def test_ui_logic_daletou_allows_40_41() -> None:
    data = build_ui_review_summary(f"40.41 {TWO}100", game=DALETOU)
    result = data["items"][0]["result"]

    assert result["status"] == "ok"
    assert data["can_continue"] is True


def test_ui_logic_error_sets_can_continue_false() -> None:
    data = build_ui_review_summary(f"39.40 {TWO}100", game="539")

    assert data["error"] == 1
    assert data["can_continue"] is False


def test_ui_logic_all_ok_sets_can_continue_true() -> None:
    data = build_ui_review_summary("\n".join([f"40.41 {TWO}100", f"42.43 {TWO}100"]), game=DALETOU)

    assert data["ok"] == 2
    assert data["warning"] == 0
    assert data["error"] == 0
    assert data["can_continue"] is True


def test_ui_preprocess_ignores_colon_game_hint_539() -> None:
    assert sanitize_review_input("彩種：539") == ""


def test_ui_preprocess_ignores_space_game_hint_daletou() -> None:
    assert sanitize_review_input(f"彩種 {DALETOU}") == ""


def test_ui_preprocess_keeps_bet_text() -> None:
    text = f"40.41 {TWO}100"

    assert sanitize_review_input(text) == text


def test_ui_preprocess_mixed_input_only_reviews_bet_line() -> None:
    data = build_ui_review_summary(f"彩種：{DALETOU}\n40.41 {TWO}100", game=DALETOU)

    assert data["total"] == 1
    assert data["ok"] == 1
    assert data["items"][0]["raw"] == f"40.41 {TWO}100"
    assert data["items"][0]["result"]["status"] == "ok"


def test_ui_preprocess_all_game_hints_returns_no_bet_warning() -> None:
    data = build_ui_review_summary(f"彩種：539\n遊戲：{DALETOU}", game="539")

    assert data["total"] == 0
    assert data["warning"] == 1
    assert data["error"] == 0
    assert data["can_continue"] is False
    assert NO_BET_CONTENT_WARNING in data["warnings"]


def test_next_step_state_ok_shows_webfill_flow() -> None:
    data = build_ui_review_summary(f"40.41 {TWO}100", game=DALETOU)

    state = build_next_step_state(data)

    assert data["can_continue"] is True
    assert state["title"] == "✅ 檢查通過"
    assert "可以進入網站帶入號碼與金額" in state["messages"]
    assert state["steps"] == [
        "開啟網站",
        "手動登入",
        "進入正確彩種與玩法頁",
        "由系統帶入號碼與金額",
        "使用者本人確認畫面",
        "使用者本人按確認 / 送出",
    ]
    assert state["button_text"] == "網站帶入功能準備中"
    assert state["note"] == "目前此版本只做安全檢查，不會操作網站。"


def test_next_step_state_error_shows_fix_flow() -> None:
    data = build_ui_review_summary(f"39.40 {TWO}100", game="539")

    state = build_next_step_state(data)

    assert data["can_continue"] is False
    assert state["title"] == "⛔ 已擋下"
    assert "目前不可進入網站帶入" in state["messages"]
    assert state["steps"] == [
        "查看錯誤原因",
        "修正下牌文字",
        "重新開始檢查",
    ]


def test_ui_review_does_not_add_website_operations() -> None:
    source = inspect.getsource(ui_review)

    assert ".click(" not in source
    assert ".fill(" not in source
    assert ".press(" not in source
    assert "submit(" not in source
