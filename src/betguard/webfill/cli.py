from __future__ import annotations

import argparse
import json
import sys
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
from betguard.webfill.batch_audit import export_batch_audit, format_pretty_audit_summary
from betguard.webfill.dry_run_pipeline import (
    build_dry_run_pipeline_from_file,
    build_dry_run_pipeline_from_text,
    format_pretty_pipeline_report,
)
from betguard.webfill.demo_e2e import format_pretty_demo_result, run_e2e_demo
from betguard.webfill.daily_workflow import (
    create_batch_from_text,
    create_review_package,
    format_pretty_new_batch_result,
    format_pretty_review_package_result,
)
from betguard.webfill.batch_queue import build_batch_queue, format_pretty_batch_queue
from betguard.webfill.batch_mock_queue import (
    accept_valid_candidates_for_mock_queue,
    advance_queue_after_human_confirm,
    build_batch_mock_queue,
    format_pretty_batch_mock_queue,
    load_queue_state,
    reject_batch_review,
    run_current_mock_queue_item,
    save_queue_state,
)
from betguard.webfill.batch_mock_runner import run_batch_mock_runner_from_file, format_pretty_batch_mock_report
from betguard.webfill.fill_mapping_report import build_mapping_report, format_pretty_mapping_report
from betguard.webfill.fill_mapping import build_b03_selector_mapping, format_pretty_b03_selector_mapping
from betguard.webfill.inspector import run_dry_run_inspector
from betguard.webfill.live_b03_snapshot import (
    format_pretty_live_b03_selector_mapping,
    run_live_b03_selector_mapping,
)
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
from betguard.webfill.review_console import (
    build_review_console_model,
    format_pretty_review_console,
    write_review_console_html,
)
from betguard.webfill.selector_discovery import run_selector_discovery, run_selector_route_probe


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
    parser.add_argument("--batch-mock-next", action="store_true", help="Mark current mock queue item done and run the next item")
    parser.add_argument("--batch-review-accept-valid", action="store_true", help="Accept valid candidates from a NEEDS_REVIEW batch")
    parser.add_argument("--batch-review-reject", action="store_true", help="Reject a NEEDS_REVIEW batch without running mock fill")
    parser.add_argument("--batch-audit-export", action="store_true", help="Export the batch queue audit JSON")
    parser.add_argument("--review-console", action="store_true", help="Print a local review console summary for a batch queue")
    parser.add_argument("--review-report-html", action="store_true", help="Write a local review console HTML report for a batch queue")
    parser.add_argument("--demo-e2e", action="store_true", help="Generate the offline end-to-end demo pack")
    parser.add_argument("--new-batch-from-file", dest="new_batch_file", help="Create a batch queue from a user input text file")
    parser.add_argument("--new-batch-from-stdin", action="store_true", help="Create a batch queue from stdin")
    parser.add_argument("--review-package", action="store_true", help="Generate review.html, audit.json, and summary.txt for a queue")
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
    parser.add_argument("--out", dest="output_path", help="Output path for JSON export commands")
    parser.add_argument("--out-dir", dest="output_dir", help="Output directory for demo packs")
    parser.add_argument("--overwrite", action="store_true", help="Allow overwriting an existing queue output file")
    parser.add_argument("--pretty", action="store_true", help="Print a human-readable report")
    parser.add_argument("--map-b03-selectors", action="store_true", help="Build a read-only B03 selector mapping report")
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


def build_b03_mapping_from_discovery(url: str, selected_page: str) -> dict:
    return run_live_b03_selector_mapping(url)


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
        if (args.pipeline_text is None) == (args.pipeline_file is None):
            parser.error("--batch-mock-run requires exactly one of --text or --file")
        raw_text = args.pipeline_text if args.pipeline_text is not None else Path(args.pipeline_file).read_text(encoding="utf-8")
        if args.auto_confirm_mock:
            report = run_batch_mock_runner_from_file(
                args.pipeline_file,
                auto_confirm_mock=args.auto_confirm_mock,
            ) if args.pipeline_file is not None else None
            if report is None:
                from betguard.webfill.batch_mock_runner import run_batch_mock_runner

                report = run_batch_mock_runner(raw_text, auto_confirm_mock=True)
            if args.pretty:
                print(format_pretty_batch_mock_report(report))
            else:
                print(json.dumps(report, ensure_ascii=False, indent=2))
            return
        queue = build_batch_mock_queue(raw_text)
        if queue.get("status") == "READY_FOR_QUEUE":
            queue = run_current_mock_queue_item(queue)
        queue_path = Path(args.queue_path or "queue_state.json")
        save_queue_state(queue, queue_path)
        queue["queue_state_path"] = str(queue_path)
        if args.pretty:
            print(format_pretty_batch_mock_queue(queue))
        else:
            print(json.dumps(queue, ensure_ascii=False, indent=2))
        return

    if args.batch_mock_next:
        if not args.queue_path:
            parser.error("--batch-mock-next requires --queue")
        queue = load_queue_state(args.queue_path)
        try:
            queue = advance_queue_after_human_confirm(queue)
        except ValueError as exc:
            queue = dict(queue)
            queue["errors"] = [str(exc)]
        save_queue_state(queue, args.queue_path)
        queue["queue_state_path"] = args.queue_path
        if args.pretty:
            print(format_pretty_batch_mock_queue(queue))
        else:
            print(json.dumps(queue, ensure_ascii=False, indent=2))
        return

    if args.batch_review_accept_valid:
        if not args.queue_path:
            parser.error("--batch-review-accept-valid requires --queue")
        queue = load_queue_state(args.queue_path)
        try:
            queue = accept_valid_candidates_for_mock_queue(queue, run_first=True)
        except ValueError as exc:
            queue = dict(queue)
            queue["errors"] = [str(exc)]
        save_queue_state(queue, args.queue_path)
        queue["queue_state_path"] = args.queue_path
        if args.pretty:
            print(format_pretty_batch_mock_queue(queue))
        else:
            print(json.dumps(queue, ensure_ascii=False, indent=2))
        return

    if args.batch_review_reject:
        if not args.queue_path:
            parser.error("--batch-review-reject requires --queue")
        queue = reject_batch_review(load_queue_state(args.queue_path))
        save_queue_state(queue, args.queue_path)
        queue["queue_state_path"] = args.queue_path
        if args.pretty:
            print(format_pretty_batch_mock_queue(queue))
        else:
            print(json.dumps(queue, ensure_ascii=False, indent=2))
        return

    if args.batch_audit_export:
        if not args.queue_path or not args.output_path:
            parser.error("--batch-audit-export requires --queue and --out")
        queue = load_queue_state(args.queue_path)
        audit = export_batch_audit(queue, args.output_path)
        if args.pretty:
            print(format_pretty_audit_summary(audit))
        else:
            print(json.dumps(audit, ensure_ascii=False, indent=2))
        return

    if args.new_batch_file or args.new_batch_from_stdin:
        if bool(args.new_batch_file) == bool(args.new_batch_from_stdin):
            parser.error("use exactly one of --new-batch-from-file or --new-batch-from-stdin")
        if not args.queue_path:
            parser.error("--new-batch-from-file/--new-batch-from-stdin requires --queue")
        raw_text = Path(args.new_batch_file).read_text(encoding="utf-8") if args.new_batch_file else sys.stdin.read()
        result = create_batch_from_text(raw_text, queue_path=args.queue_path, overwrite=args.overwrite)
        if args.pretty:
            print(format_pretty_new_batch_result(result))
        else:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    if args.review_package:
        if not args.queue_path or not args.output_dir:
            parser.error("--review-package requires --queue and --out-dir")
        queue = load_queue_state(args.queue_path)
        result = create_review_package(queue, queue_path=args.queue_path, out_dir=args.output_dir)
        if args.pretty:
            print(format_pretty_review_package_result(result))
        else:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    if args.demo_e2e:
        out_dir = args.output_dir or "demo_out"
        result = run_e2e_demo(out_dir=out_dir)
        if args.pretty:
            print(format_pretty_demo_result(result))
        else:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    if args.review_console or args.review_report_html:
        if not args.queue_path:
            parser.error("--review-console/--review-report-html requires --queue")
        queue = load_queue_state(args.queue_path)
        if args.review_report_html:
            if not args.output_path:
                parser.error("--review-report-html requires --out")
            model = write_review_console_html(queue, args.output_path, queue_path=args.queue_path)
            if args.pretty:
                print(format_pretty_review_console(model))
                print(f"HTML report: {args.output_path}")
            else:
                print(json.dumps(model, ensure_ascii=False, indent=2))
            return
        model = build_review_console_model(queue, queue_path=args.queue_path)
        if args.pretty:
            print(format_pretty_review_console(model))
        else:
            print(json.dumps(model, ensure_ascii=False, indent=2))
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

    if selected_page and not args.discover_selectors and not args.map_b03_selectors:
        parser.error("--page/--probe-route requires --discover-selectors unless --assist-fill is used")

    if args.map_b03_selectors:
        report = run_live_b03_selector_mapping(args.url)
        if args.pretty:
            print(format_pretty_live_b03_selector_mapping(report))
        else:
            print(json.dumps(report, ensure_ascii=False, indent=2))
        return

    if args.discover_selectors:
        if selected_page:
            report = run_selector_route_probe(args.url, selected_page)
        else:
            report = run_selector_discovery(args.url)
    else:
        report = run_dry_run_inspector(args.url)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
