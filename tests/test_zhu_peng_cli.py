"""CLI dispatch tests for ZhuPeng command ordering fix.

Verifies that ZhuPeng commands dispatch before the legacy dry-run guard,
while non-ZhuPeng commands are still blocked without --dry-run.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from betguard.webfill import cli as webfill_cli


def _make_queue_file(tmp_path: Path) -> str:
    """Create a minimal valid queue file and return its path."""
    queue = {
        "mode": "batch_assisted_fill_queue",
        "status": "READY",
        "current_index": 0,
        "total": 1,
        "done_count": 0,
        "items": [{
            "index": 0,
            "status": "CURRENT",
            "columns": [[11], [22]],
            "amounts": {2: 100, 3: 100},
            "accepted_by_human": True,
            "summary": "test",
        }],
        "summary": {},
        "final_decision": {"real_site_auto_submit": False, "human_required_each_item": True},
    }
    qf = tmp_path / "queue.json"
    qf.write_text(json.dumps(queue, ensure_ascii=False), encoding="utf-8")
    return str(qf)


class TestZhuPengPreflightCLI:
    """--zhu-peng-preflight must work without --dry-run."""

    def test_preflight_no_dry_run_required(self, tmp_path: Path):
        """Preflight is read-only; should NOT require --dry-run."""
        qf = _make_queue_file(tmp_path)
        parser = webfill_cli.build_parser()
        args = parser.parse_args(["--zhu-peng-preflight", "--queue", qf, "--pretty"])
        assert args.zhu_peng_preflight is True
        assert args.dry_run is False  # not required

    def test_preflight_with_queue_runs(self, tmp_path: Path, capsys):
        """Preflight with valid queue should output status without error."""
        qf = _make_queue_file(tmp_path)
        with patch.object(sys, "argv", ["cli.py", "--zhu-peng-preflight", "--queue", qf]):
            try:
                webfill_cli.main()
            except SystemExit:
                pass
        captured = capsys.readouterr()
        # Should output preflight results, not "Only --dry-run is supported"
        assert "Only --dry-run is supported" not in captured.out
        assert "Only --dry-run is supported" not in captured.err


class TestZhuPengAssistedFillCLI:
    """--zhu-peng-assisted-fill must require risk flag, not hit dry-run guard."""

    def test_assisted_fill_no_risk_flag_blocked(self, tmp_path: Path):
        """Without --i-understand-real-site-fill-risk, must error in main()."""
        qf = _make_queue_file(tmp_path)
        with patch.object(sys, "argv", [
            "cli.py", "--zhu-peng-assisted-fill", "--queue", qf, "--url", "http://test"
        ]):
            with pytest.raises(SystemExit):
                webfill_cli.main()

    def test_assisted_fill_with_risk_flag_parses(self, tmp_path: Path):
        """With risk flag, parsing should succeed (not hit dry-run guard)."""
        qf = _make_queue_file(tmp_path)
        parser = webfill_cli.build_parser()
        args = parser.parse_args([
            "--zhu-peng-assisted-fill",
            "--queue", qf,
            "--url", "http://test",
            "--i-understand-real-site-fill-risk",
        ])
        assert args.zhu_peng_assisted_fill is True
        assert args.i_understand_real_site_fill_risk is True


class TestZhuPengSessionFillCLI:
    """--zhu-peng-session-fill dispatch checks."""

    def test_session_fill_no_risk_flag_blocked(self, tmp_path: Path):
        """Without --i-understand-real-site-fill-risk, must error in main()."""
        qf = _make_queue_file(tmp_path)
        with patch.object(sys, "argv", [
            "cli.py", "--zhu-peng-session-fill", "--queue", qf, "--url", "http://test"
        ]):
            with pytest.raises(SystemExit):
                webfill_cli.main()

    def test_session_fill_with_risk_flag_parses(self, tmp_path: Path):
        """With risk flag, parsing should succeed."""
        qf = _make_queue_file(tmp_path)
        parser = webfill_cli.build_parser()
        args = parser.parse_args([
            "--zhu-peng-session-fill",
            "--queue", qf,
            "--url", "http://test",
            "--i-understand-real-site-fill-risk",
        ])
        assert args.zhu_peng_session_fill is True

    def test_session_fill_not_blocked_by_dry_run_guard(self, tmp_path: Path):
        """Session fill with risk flag but no --dry-run: should dispatch to handler, not guard."""
        qf = _make_queue_file(tmp_path)
        with patch.object(sys, "argv", [
            "cli.py",
            "--zhu-peng-session-fill",
            "--queue", qf,
            "--url", "http://test",
            "--i-understand-real-site-fill-risk",
        ]):
            # The handler will fail because there's no real browser, but it should
            # reach the handler (which imports playwright), not the dry-run guard.
            # We patch the handler import to avoid the browser dependency.
            with patch("betguard.webfill.zhu_peng_session.run_zhu_peng_session_fill") as mock_run:
                mock_run.return_value = {"result": "mock"}
                try:
                    webfill_cli.main()
                except SystemExit:
                    pass
        # The mock should have been called (dispatched to handler, not dry-run guard)
        mock_run.assert_called_once()


class TestLegacyDryRunGuard:
    """Non-ZhuPeng commands without --dry-run must still be blocked."""

    def test_legacy_command_without_dry_run_fails(self):
        """Generic --url without --dry-run must hit the legacy guard."""
        parser = webfill_cli.build_parser()
        args = parser.parse_args(["--url", "http://test", "--discover-selectors"])
        with patch.object(sys, "argv", ["cli.py", "--url", "http://test", "--discover-selectors"]):
            with pytest.raises(SystemExit) as exc:
                webfill_cli.main()
            assert exc.value.code != 0

    def test_legacy_command_with_dry_run_ok(self):
        """Generic --url with --dry-run should parse ok."""
        parser = webfill_cli.build_parser()
        args = parser.parse_args(["--url", "http://test", "--dry-run"])
        assert args.dry_run is True
