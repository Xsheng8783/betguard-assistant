import inspect

from betguard.webfill import car_readonly_snapshot
from betguard.webfill.car_readonly_snapshot import build_car_readonly_snapshot_placeholder


def test_car_readonly_snapshot_placeholder_detects_car_text_without_selectors() -> None:
    snapshot = build_car_readonly_snapshot_placeholder("全車 車號 金額 送出注單 確認")

    assert snapshot["page_kind"] == "car"
    assert snapshot["readonly"] is True
    assert snapshot["real_site_operation"] is False
    assert snapshot["auto_submit"] is False
    assert snapshot["car_page_detected"] is True
    assert snapshot["selector_candidates"] == {}
    assert "送出注單" in snapshot["danger_texts_detected"]
    assert "確認" in snapshot["danger_texts_detected"]


def test_car_readonly_snapshot_source_has_no_live_operations() -> None:
    source = inspect.getsource(car_readonly_snapshot)

    assert ".click(" not in source
    assert ".fill(" not in source
    assert ".press(" not in source
    assert "submit(" not in source
