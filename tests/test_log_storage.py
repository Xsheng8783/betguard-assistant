from datetime import datetime

from betguard.log import read_recent_logs, read_review_log, save_review_log


OK_SUMMARY = "一般：6, 13, 23, 22｜二三四星｜0.5支｜50元"


def review_data(*, can_continue: bool = True) -> dict:
    status = "ok" if can_continue else "error"
    return {
        "total": 1,
        "ok": 1 if can_continue else 0,
        "warning": 0,
        "error": 0 if can_continue else 1,
        "can_continue": can_continue,
        "items": [
            {
                "line_no": 1,
                "raw": "06-13-23-22/50" if can_continue else "13.38.13 二三100",
                "summary": OK_SUMMARY if can_continue else "錯誤：duplicate number 13",
                "result": {
                    "status": status,
                    "type": "normal",
                    "warnings": [],
                    "errors": [] if can_continue else ["duplicate number 13"],
                },
            }
        ],
    }


def test_save_review_log_creates_file(tmp_path) -> None:
    path = save_review_log(
        "06-13-23-22/50",
        review_data(),
        log_dir=tmp_path,
        created_at=datetime(2026, 6, 30, 22, 30, 15),
    )

    assert path.exists()
    assert path.name == "review_20260630_223015.json"


def test_review_log_json_contains_required_fields(tmp_path) -> None:
    path = save_review_log(
        "06-13-23-22/50",
        review_data(),
        log_dir=tmp_path,
        created_at=datetime(2026, 6, 30, 22, 30, 15),
    )

    payload = read_review_log(path)

    assert payload["created_at"] == "2026-06-30T22:30:15"
    assert payload["source"] == "streamlit_review_ui"
    assert payload["raw_text"] == "06-13-23-22/50"
    assert payload["summary"]["total"] == 1
    assert payload["summary"]["can_continue"] is True
    assert payload["ok_summaries"] == [OK_SUMMARY]
    assert payload["items"][0]["line_no"] == 1


def test_read_recent_logs_returns_latest_first(tmp_path) -> None:
    save_review_log("first", review_data(), log_dir=tmp_path, created_at=datetime(2026, 6, 30, 22, 30, 15))
    save_review_log("second", review_data(), log_dir=tmp_path, created_at=datetime(2026, 6, 30, 22, 30, 16))
    save_review_log("third", review_data(), log_dir=tmp_path, created_at=datetime(2026, 6, 30, 22, 30, 17))

    recent = read_recent_logs(2, log_dir=tmp_path)

    assert [log["filename"] for log in recent] == ["review_20260630_223017.json", "review_20260630_223016.json"]
    assert [log["raw_text"] for log in recent] == ["third", "second"]


def test_can_continue_false_is_still_saved(tmp_path) -> None:
    path = save_review_log(
        "13.38.13 二三100",
        review_data(can_continue=False),
        log_dir=tmp_path,
        created_at=datetime(2026, 6, 30, 22, 30, 15),
    )

    payload = read_review_log(path)

    assert payload["summary"]["can_continue"] is False
    assert payload["summary"]["error"] == 1
    assert payload["items"][0]["result"]["errors"] == ["duplicate number 13"]


def test_save_review_log_creates_missing_log_directory(tmp_path) -> None:
    log_dir = tmp_path / "missing" / "review_logs"

    assert not log_dir.exists()

    path = save_review_log(
        "06-13-23-22/50",
        review_data(),
        log_dir=log_dir,
        created_at=datetime(2026, 6, 30, 22, 30, 15),
    )

    assert log_dir.exists()
    assert path.exists()
