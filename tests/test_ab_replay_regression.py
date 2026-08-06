"""End-to-end regression replay over the fixed formal A/B raw responses.

Golden fixtures: tests/fixtures/ab_formal/sample-XXX-{model}-runN.json
Expected snapshot: tests/fixtures/replay_expected.json

Regenerate the snapshot intentionally (with a change reason recorded in
tests/fixtures/replay_expected.md):
    $env:REGEN_REPLAY=1 ; python -m pytest tests/test_ab_replay_regression.py -k regenerate
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from betguard.vision.replay import (
    compare_replays,
    replay_file,
    safety_violations,
)

FIXTURES = Path(__file__).parent / "fixtures" / "ab_formal"
EXPECTED = Path(__file__).parent / "fixtures" / "replay_expected.json"


def _all_fixtures() -> list[Path]:
    return sorted(FIXTURES.glob("sample-*-run*.json"))


def _replay_all() -> dict[str, dict]:
    return {
        p.stem: replay_file(p)
        for p in _all_fixtures()
    }


def test_fixtures_present() -> None:
    files = list(_all_fixtures())
    assert len(files) == 30, f"expected 30 formal A/B fixtures, got {len(files)}"


def test_regenerate_snapshot() -> None:
    """Explicit regeneration of the expected snapshot (opt-in via env var)."""
    if os.environ.get("REGEN_REPLAY") != "1":
        return
    snapshots = _replay_all()
    EXPECTED.write_text(json.dumps(snapshots, ensure_ascii=False, indent=1), encoding="utf-8")
    note = Path(__file__).parent / "fixtures" / "replay_expected.md"
    if not note.exists():
        note.write_text(
            "# replay_expected.json 變更紀錄\n\n請在下方填寫本次變更原因與日期。\n",
            encoding="utf-8",
        )


def test_replay_matches_expected() -> None:
    if not EXPECTED.exists():
        raise AssertionError(
            "replay_expected.json 不存在；先用 REGEN_REPLAY=1 產生快照"
        )
    expected = json.loads(EXPECTED.read_text(encoding="utf-8"))
    actual = _replay_all()
    diffs: list[str] = []
    for key, act in actual.items():
        exp = expected.get(key)
        if exp is None:
            diffs.append(f"{key}: new fixture without expected snapshot")
            continue
        diffs.extend(compare_replays(exp, act))
    assert diffs == [], "\n".join(diffs)


def test_blocked_never_becomes_executable() -> None:
    if not EXPECTED.exists():
        return
    expected = json.loads(EXPECTED.read_text(encoding="utf-8"))
    actual = _replay_all()
    violations: list[str] = []
    for key, act in actual.items():
        exp = expected.get(key)
        if exp is None:
            continue
        violations.extend(safety_violations(exp, act))
    assert violations == [], "\n".join(violations)


def test_parse_success_rate_reasonable() -> None:
    """Informational guard: parseable raw responses must keep most rows parseable."""
    actual = _replay_all()
    ok = fail = 0
    for rec in actual.values():
        ok += rec["summary"]["parse_ok"]
        fail += rec["summary"]["parse_fail"]
    total = ok + fail
    assert total > 0
    assert ok / total >= 0.9, f"parse success rate {ok}/{total} below 90%"


def test_every_blocked_row_has_block_reason() -> None:
    actual = _replay_all()
    missing: list[str] = []
    for key, rec in actual.items():
        for i, row in enumerate(rec["rows"]):
            if not row.get("executable") and not row.get("block_reason"):
                missing.append(f"{key} row{i}: {row.get('raw_text')!r}")
    assert missing == [], "\n".join(missing)


def test_parse_error_rows_never_executable() -> None:
    actual = _replay_all()
    bad: list[str] = []
    for key, rec in actual.items():
        for i, row in enumerate(rec["rows"]):
            if row.get("parse_error") and row.get("executable"):
                bad.append(f"{key} row{i}: {row.get('raw_text')!r}")
    assert bad == [], "\n".join(bad)
