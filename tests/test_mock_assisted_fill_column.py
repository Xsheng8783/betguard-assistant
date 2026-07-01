from betguard.webfill.mock_assisted_fill import build_mock_fill_report


def test_column_bet_can_build_mock_fill_report() -> None:
    report = build_mock_fill_report("17.20/28/34 二三1")

    assert report["status"] == "COMPLETED_MOCK_ONLY"
    assert report["bet_type"] == "column"
    assert report["selected_columns"] == [
        {"column": 1, "numbers": [17, 20]},
        {"column": 2, "numbers": [28]},
        {"column": 3, "numbers": [34]},
    ]
    assert report["filled_amounts"] == {"二星": 100, "三星": 100}
    assert report["danger_buttons_clicked"] == []


def test_car_bet_can_build_mock_fill_report_without_b03_fields() -> None:
    report = build_mock_fill_report("32車10元")

    assert report["status"] == "COMPLETED_MOCK_ONLY"
    assert report["bet_type"] == "car"
    assert report["page_kind"] == "car"
    assert report["selected_car_number"] == 32
    assert report["car_units"] == 0.1
    assert report["filled_amount"] == 10
    assert report["selected_numbers"] == []
    assert report["selected_columns"] == []
    assert report["filled_amounts"] == {}
    assert report["danger_buttons_clicked"] == []
    assert report["errors"] == []


def test_car_bare_amount_can_build_mock_fill_report() -> None:
    report = build_mock_fill_report("32車10")

    assert report["status"] == "COMPLETED_MOCK_ONLY"
    assert report["bet_type"] == "car"
    assert report["selected_car_number"] == 32
    assert report["car_units"] == 0.1
    assert report["filled_amount"] == 10
    assert report["selected_numbers"] == []
    assert report["selected_columns"] == []


def test_car_leading_zero_number_can_build_mock_fill_report() -> None:
    report = build_mock_fill_report("08車20元")

    assert report["status"] == "COMPLETED_MOCK_ONLY"
    assert report["bet_type"] == "car"
    assert report["selected_car_number"] == 8
    assert report["car_units"] == 0.2
    assert report["filled_amount"] == 20
