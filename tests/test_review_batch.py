from betguard.review import format_pretty_review, review_text


TWO_THREE = "\u4e8c\u4e09"
TAIL = "\u5c3e"
IDEOGRAPHIC_COMMA = "\u3001"
YUAN = "\u5143"
CAR = "\u8eca"
MULTIPLY = "\u00d7"


def test_batch_mixed_normal_column_tail_car_and_error() -> None:
    text = "\n".join(
        [
            "06-13-23-22/50",
            f"17.20/28/34 {TWO_THREE}{MULTIPLY}1",
            f"{TAIL}2{IDEOGRAPHIC_COMMA}5 \u4e8c50{YUAN}",
            f"32{CAR}10{YUAN}",
            f"13.38.13 {TWO_THREE}100",
        ]
    )

    summary = review_text(text)
    data = summary.to_dict()

    assert data["total"] == 5
    assert data["ok"] == 4
    assert data["warning"] == 0
    assert data["error"] == 1
    assert data["can_continue"] is False
    assert data["items"][0]["line_no"] == 1
    assert data["items"][0]["result"]["type"] == "normal"
    assert data["items"][1]["result"]["type"] == "column"
    assert data["items"][2]["result"]["type"] == "column"
    assert data["items"][3]["result"]["type"] == "car"
    assert "duplicate number 13" in data["items"][4]["result"]["errors"]


def test_batch_skips_blank_lines_without_renumbering_source_lines() -> None:
    summary = review_text("\n06-13-23-22/50\n\n32車10元\n")
    data = summary.to_dict()

    assert data["total"] == 2
    assert [item["line_no"] for item in data["items"]] == [2, 4]


def test_batch_error_sets_can_continue_false() -> None:
    summary = review_text(f"40{CAR}1")

    assert summary.to_dict()["error"] == 1
    assert summary.can_continue is False


def test_batch_all_ok_sets_can_continue_true() -> None:
    summary = review_text("\n".join(["06-13-23-22/50", f"32{CAR}10{YUAN}"]))

    assert summary.to_dict()["ok"] == 2
    assert summary.can_continue is True


def test_batch_warning_sets_can_continue_false() -> None:
    summary = review_text("06.13.23")

    data = summary.to_dict()
    assert data["warning"] == 1
    assert data["can_continue"] is False


def test_pretty_review_includes_summary_and_errors() -> None:
    summary = review_text(f"13.38.13 {TWO_THREE}100")
    pretty = format_pretty_review(summary)

    assert "[1] ERROR normal" in pretty
    assert "duplicate number 13" in pretty
    assert "total=1 ok=0 warning=0 error=1 can_continue=false" in pretty
