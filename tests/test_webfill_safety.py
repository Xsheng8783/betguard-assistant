from betguard.webfill.inspector import extract_labels_from_elements, guess_current_game, scan_text_for_labels
from betguard.webfill.safety import DANGER_WORDS, is_dangerous_action


def test_dangerous_action_words_are_blocked() -> None:
    assert is_dangerous_action("送出注單") is True
    assert is_dangerous_action("確認") is True
    assert is_dangerous_action("加入注單") is True
    assert is_dangerous_action("刪除") is True


def test_new_danger_words_blocked() -> None:
    """「取消」「完成」must be blocked — added for ZhuPeng safety unification."""
    assert is_dangerous_action("取消") is True
    assert is_dangerous_action("完成") is True


def test_all_danger_words_blocked_by_iteration() -> None:
    """Every word in DANGER_WORDS must be blocked."""
    for word in DANGER_WORDS:
        assert is_dangerous_action(word), f"'{word}' should be blocked"


def test_danger_words_in_compound_text() -> None:
    """Danger words embedded in longer text must still be caught."""
    assert is_dangerous_action("確認送出")
    assert is_dangerous_action("確定下注")
    assert is_dangerous_action("取消全部")


def test_safe_labels_are_not_blocked() -> None:
    assert is_dangerous_action("二三四星") is False
    assert is_dangerous_action("539") is False
    assert is_dangerous_action("01") is False


def test_scan_text_for_labels_reports_dry_run_markers() -> None:
    text = "今彩539 二三四星 單碰 連碰 柱碰 二星 三星 四星 送出注單"

    found = scan_text_for_labels(text)

    assert found["current_game_guess"] == "539"
    assert found["game_539"] is True
    assert found["star_tab"] is True
    assert found["normal_tab"] is True
    assert found["linked_tab"] is True
    assert found["column_tab"] is True
    assert found["amount_fields"] == ["二星", "三星", "四星"]
    assert "送出注單" in found["danger_buttons"]


def test_scan_text_for_number_labels_uses_digit_boundaries() -> None:
    found = scan_text_for_labels("今彩539 01 02 03")

    assert found["matching_number_labels"] == ["01", "02", "03"]
    assert found["number_buttons_count"] == 3


def test_scan_text_for_labels_detects_tiantian_without_539() -> None:
    text = "天天樂 二三四星 連碰 柱碰 二星 三星 四星 送出注單"

    found = scan_text_for_labels(text)

    assert found["current_game_guess"] == "天天樂"
    assert found["game_539"] is False
    assert found["game_tiantian"] is True
    assert "天天樂" in found["available_labels"]
    assert "連碰" in found["available_labels"]
    assert "柱碰" in found["available_labels"]



def test_scan_text_for_labels_detects_other_game_labels() -> None:
    assert scan_text_for_labels("大樂 二三四星")["current_game_guess"] == "大樂"
    assert scan_text_for_labels("六合 二三四星")["current_game_guess"] == "六合"
    assert scan_text_for_labels("港 二三四星")["current_game_guess"] == "港"



def make_element(**overrides) -> dict:
    element = {
        "tag": "button",
        "text": "",
        "value": "",
        "id": "",
        "name": "",
        "className": "",
        "href": "",
        "type": "",
    }
    element.update(overrides)
    return element


def test_guess_current_game_from_elements_detects_tiantian() -> None:
    elements = [make_element(text="天天樂")]

    assert guess_current_game("", elements) == "天天樂"


def test_extract_labels_from_elements_detects_all_01_to_39() -> None:
    elements = [make_element(text=f"{number:02d}") for number in range(1, 40)]

    labels = extract_labels_from_elements(elements)

    assert labels["matching_number_labels"] == [f"{number:02d}" for number in range(1, 40)]
    assert len(labels["matching_number_labels"]) == 39


def test_extract_labels_from_elements_detects_danger_button() -> None:
    elements = [make_element(tag="button", text="送出注單")]

    labels = extract_labels_from_elements(elements)

    assert "送出注單" in labels["danger_buttons"]


def test_extract_labels_from_elements_detects_amount_fields() -> None:
    elements = [
        make_element(tag="input", id="二星金額"),
        make_element(tag="input", name="三星金額"),
        make_element(tag="input", className="四星金額"),
    ]

    labels = extract_labels_from_elements(elements)

    assert labels["amount_fields"] == ["二星", "三星", "四星"]
