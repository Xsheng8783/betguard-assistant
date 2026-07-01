from __future__ import annotations

import argparse
import json
from pathlib import Path

from betguard.formatter import attach_summaries
from betguard.review import review_text
from betguard.webfill.assisted_fill_real import (
    SAFETY_LOCK_MESSAGE,
    build_assist_fill_execution_plan,
    format_execution_safety_statement,
    format_pretty_assist_fill_plan,
    run_real_site_assisted_fill as run_legacy_real_site_assisted_fill,
)
from betguard.webfill.assisted_fill_mock import format_pretty_mock_report, run_assisted_fill_mock
from betguard.webfill.dry_run_pipeline import (
    build_dry_run_pipeline_from_file,
    build_dry_run_pipeline_from_text,
    format_pretty_pipeline_report,
)
from betguard.webfill.batch_queue import build_batch_queue, format_pretty_batch_queue
from betguard.webfill.batch_mock_runner import run_batch_mock_runner_from_file, format_pretty_batch_mock_report
from betguard.webfill.fill_mapping_report import build_mapping_report, format_pretty_mapping_report
from betguard.webfill.inspector import run_dry_run_inspector
from betguard.webfill.page_routes import PAGE_ROUTE_LABELS
from betguard.webfill.real_site_fill_plan import (
    build_real_site_assisted_fill_plan,
    format_pretty_real_site_fill_plan,
)
from betguard.webfill.real_site_assisted_fill import (
    RISK_LOCK_MESSAGE,
    format_pretty_real_site_assisted_fill,
    run_real_site_assisted_fill,
)
from betguard.webfill.selector_discovery import run_selector_discovery


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Betguard Playwright dry-run tooling")
    parser.add_argument("--url", help="Website URL to open")
    parser.add_argument("--dry-run", action="store_true", help="Run without filling or submitting anything")
    parser.add_argument("--assist-fill", action="store_true", help="Safely fill a real site page after explicit YES confirmation")
    parser.add_argument(
        "--i-understand-human-final-confirm",
        action="store_true",
        help="Required safety acknowledgement for real-site assisted fill",
    )
    parser.add_argument("--mock-assisted-fill", action="store_true", help="Run assisted fill against the local mock page only")
    parser.add_argument("--map-dry-run", action="store_true", help="Build an offline assisted fill mapping report")
    parser.add_argument("--dry-run-pipeline", action="store_true", help="Run the offline assisted fill dry-run pipeline")
    parser.add_argument("--build-batch-queue", action="store_true", help="Build a batch assisted fill queue from reviewed text")
    parser.add_argument("--batch-mock-run", action="store_true", help="Run a batch assisted fill queue against the local mock page")
    parser.add_argument("--real-site-fill-plan", action="store_true", help="Plan the next real-site assisted fill item without operating the site")
    parser.add_argument("--real-site-assisted-fill", action="store_true", help="Safely fill the current batch queue item on the real site")
    parser.add_argument(
        "--i-understand-real-site-fill-risk",
        action="store_true",
        help="Required safety acknowledgement for real-site current-item assisted fill",
    )
    parser.add_argument(
        "--auto-confirm-mock",
        action="store_true",
        help="Mock-only simulation that marks each filled item as human-confirmed before moving on",
    )
    parser.add_argument("--text", dest="pipeline_text", help="Betting text for --dry-run-pipeline")
    parser.add_argument("--file", dest="pipeline_file", help="UTF-8 input file for --dry-run-pipeline")
    parser.add_argument("--fill-plan", dest="fill_plan_path", help="Read an assisted fill plan JSON file")
    parser.add_argument("--queue", dest="queue_path", help="Read a batch queue JSON file")
    parser.add_argument("--selector-report", help="Read a selector discovery JSON report file")
    parser.add_argument("--pretty", action="store_true", help="Print a human-readable report")
    parser.add_argument(
        "--discover-selectors",
        action="store_true",
        help="Read DOM and frames to discover candidate selectors without operating them",
    )
    parser.add_argument(
        "--probe-route",
        help="Read a route from $Global.Menu, such as 二三四星, without clicking or filling anything",
    )
    parser.add_argument(
        "--page",
        choices=PAGE_ROUTE_LABELS,
        help="Page route to inspect or require, such as 二三四星, 全車, or 快速輸入",
    )
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    if args.build_batch_queue:
        if args.pipeline_file is None:
            parser.error("--build-batch-queue requires --file")
        raw_text = Path(args.pipeline_file).read_text(encoding="utf-8")
        review_result = attach_summaries(review_text(raw_text).to_dict())
        queue = build_batch_queue(review_result)
        if args.pretty:
            print(format_pretty_batch_queue(queue))
        else:
            print(json.dumps(queue, ensure_ascii=False, indent=2))
        return

    if args.batch_mock_run:
        if args.pipeline_file is None:
            parser.error("--batch-mock-run requires --file")
        report = run_batch_mock_runner_from_file(
            args.pipeline_file,
            auto_confirm_mock=args.auto_confirm_mock,
        )
        if args.pretty:
            print(format_pretty_batch_mock_report(report))
        else:
            print(json.dumps(report, ensure_ascii=False, indent=2))
        return

    if args.real_site_fill_plan:
        if not args.queue_path or not args.selector_report:
            parser.error("--real-site-fill-plan requires --queue and --selector-report")
        queue = json.loads(Path(args.queue_path).read_text(encoding="utf-8"))
        selector_report = json.loads(Path(args.selector_report).read_text(encoding="utf-8"))
        report = build_real_site_assisted_fill_plan(queue, selector_report)
        if args.pretty:
            print(format_pretty_real_site_fill_plan(report))
        else:
            print(json.dumps(report, ensure_ascii=False, indent=2))
        return

    if args.real_site_assisted_fill:
        if not args.i_understand_real_site_fill_risk:
            print(RISK_LOCK_MESSAGE)
            return
        if not args.queue_path or not args.selector_report or not args.url:
            parser.error("--real-site-assisted-fill requires --queue, --selector-report, and --url")
        queue = json.loads(Path(args.queue_path).read_text(encoding="utf-8"))
        selector_report = json.loads(Path(args.selector_report).read_text(encoding="utf-8"))
        report = run_real_site_assisted_fill(
            queue,
            selector_report,
            url=args.url,
            risk_acknowledged=args.i_understand_real_site_fill_risk,
        )
        if args.pretty:
            print(format_pretty_real_site_assisted_fill(report))
        else:
            print(json.dumps(report, ensure_ascii=False, indent=2))
        return

    if args.assist_fill:
        if not args.i_understand_human_final_confirm:
            print(SAFETY_LOCK_MESSAGE)
            return
        if args.pipeline_text is None or not args.selector_report:
            parser.error("--assist-fill requires --text and --selector-report")
        selector_report = json.loads(Path(args.selector_report).read_text(encoding="utf-8"))
        plan = build_assist_fill_execution_plan(args.pipeline_text, selector_report, page=args.page)
        print(format_pretty_assist_fill_plan(plan))
        if plan.get("status") != "READY":
            if not args.pretty:
                print(json.dumps(plan, ensure_ascii=False, indent=2))
            return
        print()
        print(format_execution_safety_statement(plan))
        answer = input("Type YES to start safe fill. Anything else cancels: ").strip()
        result = run_legacy_real_site_assisted_fill(plan, confirmed=(answer == "YES"))
        if args.pretty:
            print(format_pretty_assist_fill_plan(result))
        else:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    if args.mock_assisted_fill:
        if args.pipeline_text is None:
            parser.error("--mock-assisted-fill requires --text")
        report = run_assisted_fill_mock(args.pipeline_text)
        if args.pretty:
            print(format_pretty_mock_report(report))
        else:
            print(json.dumps(report, ensure_ascii=False, indent=2))
        return

    if args.map_dry_run:
        if not args.fill_plan_path or not args.selector_report:
            parser.error("--map-dry-run requires --fill-plan and --selector-report")
        fill_plan = json.loads(Path(args.fill_plan_path).read_text(encoding="utf-8"))
        selector_report = json.loads(Path(args.selector_report).read_text(encoding="utf-8"))
        report = build_mapping_report(fill_plan, selector_report)
        if args.pretty:
            print(format_pretty_mapping_report(report))
        else:
            print(json.dumps(report, ensure_ascii=False, indent=2))
        return

    if args.dry_run_pipeline:
        if not args.selector_report:
            parser.error("--dry-run-pipeline requires --selector-report")
        if (args.pipeline_text is None) == (args.pipeline_file is None):
            parser.error("--dry-run-pipeline requires exactly one of --text or --file")
        selector_report = json.loads(Path(args.selector_report).read_text(encoding="utf-8"))
        if args.pipeline_text is not None:
            report = build_dry_run_pipeline_from_text(args.pipeline_text, selector_report)
        else:
            report = build_dry_run_pipeline_from_file(args.pipeline_file, selector_report)
        if args.pretty:
            print(format_pretty_pipeline_report(report))
        else:
            print(json.dumps(report, ensure_ascii=False, indent=2))
        return

    if not args.dry_run:
        parser.error("Only --dry-run is supported. Filling and submitting are intentionally disabled.")
    if not args.url:
        parser.error("--url is required unless --map-dry-run is used")

    if args.probe_route and args.page and args.probe_route != args.page:
        parser.error("--probe-route and --page must refer to the same route when both are provided")
    selected_page = args.page or args.probe_route

    if selected_page and not args.discover_selectors:
        parser.error("--page/--probe-route requires --discover-selectors unless --assist-fill is used")

    if args.discover_selectors:
        report = run_selector_discovery(args.url, probe_route=selected_page)
    else:
        report = run_dry_run_inspector(args.url)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
