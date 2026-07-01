from betguard.formatter import attach_summaries, format_bet_summary, ok_summaries_text, review_to_csv
from betguard.review import review_text


TWO_THREE = "\u4e8c\u4e09"
CAR = "\u8eca"
YUAN = "\u5143"
MULTIPLY = "\u00d7"


def result_for(text: str) -> dict:
    return review_text(text).to_dict()["items"][0]["result"]


def test_normal_summary() -> None:
    summary = format_bet_summary(result_for("06-13-23-22/50"))

    assert summary == "一般：6, 13, 23, 22｜二三四星｜0.5支｜50元"


def test_column_summary() -> None:
    summary = format_bet_summary(result_for(f"17.20/28/34 {TWO_THREE}{MULTIPLY}1"))

    assert summary == "柱碰：第1柱 17,20｜第2柱 28｜第3柱 34｜二三星｜1支｜100元"


def test_car_summary() -> None:
    summary = format_bet_summary(result_for(f"32{CAR}10{YUAN}"))

    assert summary == "車：32｜0.1車｜10元"


def test_error_summary() -> None:
    summary = format_bet_summary(result_for(f"13.38.13 {TWO_THREE}100"))

    assert summary == "錯誤：duplicate number 13"


def test_review_exports_include_summary_and_ok_text() -> None:
    data = attach_summaries(review_text("\n".join(["06-13-23-22/50", f"32{CAR}10{YUAN}"])).to_dict())

    assert "一般：6, 13, 23, 22" in ok_summaries_text(data)
    csv_text = review_to_csv(data)
    assert "line_no,status,type,raw,summary,warnings,errors" in csv_text
    assert "車：32｜0.1車｜10元" in csv_text
