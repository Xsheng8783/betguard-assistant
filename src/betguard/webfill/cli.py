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
from betguard.webfill.batch_queue import build_batch_queue, format_pretty_batch_queue, mark_current_done_by_human, WAITING_FOR_HUMAN_CONFIRM as BQ_WAITING_FOR_HUMAN_CONFIRM
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
from betguard.webfill.fill_preview import PREVIEW_SAFETY, build_fill_preview, format_pretty_fill_preview
from betguard.webfill.fill_mapping_report import (
    build_mapping_report,
    format_concise_mapping_report,
    format_pretty_mapping_report,
)
from betguard.webfill.fill_mapping import build_b03_selector_mapping, format_pretty_b03_selector_mapping
from betguard.webfill.fill_plan import to_numbers_only_plan
from betguard.webfill.inspector import run_dry_run_inspector
from betguard.webfill.live_b03_snapshot import (
    format_pretty_live_b03_selector_mapping,
    run_live_b03_selector_mapping,
)
from betguard.webfill.page_routes import PAGE_ROUTE_LABELS
from betguard.webfill.real_site_fill_plan import (
    FINAL_DECISION as REAL_SITE_FILL_PLAN_FINAL_DECISION,
    build_real_site_assisted_fill_plan,
    format_pretty_real_site_fill_plan,
)
from betguard.webfill.real_site_assisted_fill import (
    RISK_LOCK_MESSAGE,
    format_pretty_real_site_assisted_fill,
    run_real_site_assisted_fill,
)
from betguard.webfill.real_site_fill_preflight import (
    build_real_site_fill_preflight_report,
    format_concise_real_site_fill_preflight,
    format_pretty_real_site_fill_preflight,
)
from betguard.webfill.real_site_fill_readiness import (
    build_readiness_checklist,
    format_concise_readiness_checklist,
    format_pretty_readiness_checklist,
)
from betguard.webfill.review_console import (
    build_review_console_model,
    format_pretty_review_console,
    write_review_console_html,
)
from betguard.webfill.selector_discovery import run_selector_discovery, run_selector_route_probe
from betguard.webfill.site_profile import (
    build_site_profile,
    format_pretty_site_profile_validation,
    load_site_profile,
    profile_as_selector_report,
    save_site_profile,
    validate_site_profile,
)


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
    parser.add_argument(
        "--numbers-only-fill-plan",
        action="store_true",
        help="Convert an assisted_fill_plan_v0 JSON into a numbers-only plan (amounts removed for manual entry)",
    )
    parser.add_argument("--save-site-profile", action="store_true", help="Convert a local selector_report JSON into a local site_profile JSON")
    parser.add_argument("--site-profile-report", action="store_true", help="Read a local site_profile JSON and print a validation report")
    parser.add_argument("--dry-run-pipeline", action="store_true", help="Run the offline assisted fill dry-run pipeline")
    parser.add_argument("--build-batch-queue", action="store_true", help="Build a batch assisted fill queue from reviewed text")
    parser.add_argument("--batch-mock-run", action="store_true", help="Run a batch assisted fill queue against the local mock page")
    parser.add_argument("--batch-mock-next", action="store_true", help="Mark current mock queue item done and run the next item")
    parser.add_argument("--batch-review-accept-valid", action="store_true", help="Accept valid candidates from a NEEDS_REVIEW batch")
    parser.add_argument("--batch-review-reject", action="store_true", help="Reject a NEEDS_REVIEW batch without running mock fill")
    parser.add_argument("--batch-audit-export", action="store_true", help="Export the batch queue audit JSON")
    parser.add_argument("--fill-preview", action="store_true", help="Show a read-only fill preview from the approved fill queue")
    parser.add_argument("--review-console", action="store_true", help="Print a local review console summary for a batch queue")
    parser.add_argument("--review-report-html", action="store_true", help="Write a local review console HTML report for a batch queue")
    parser.add_argument("--demo-e2e", action="store_true", help="Generate the offline end-to-end demo pack")
    parser.add_argument("--new-batch-from-file", dest="new_batch_file", help="Create a batch queue from a user input text file")
    parser.add_argument("--new-batch-from-stdin", action="store_true", help="Create a batch queue from stdin")
    parser.add_argument("--review-package", action="store_true", help="Generate review.html, audit.json, and summary.txt for a queue")
    parser.add_argument("--real-site-fill-plan", action="store_true", help="Plan the next real-site assisted fill item without operating the site")
    parser.add_argument("--real-site-fill-preflight", action="store_true", help="Local-only safety preflight for one approved_fill_queue item; never opens a browser")
    parser.add_argument("--real-site-fill-readiness", action="store_true", help="Local-only PASS/BLOCKED readiness checklist for one approved_fill_queue item; never opens a browser")
    parser.add_argument("--item-index", dest="item_index", type=int, help="approved_fill_queue item index for --real-site-fill-preflight")
    parser.add_argument("--real-site-assisted-fill", action="store_true", help="Safely fill the current batch queue item on the real site")
    parser.add_argument(
        "--i-understand-real-site-fill-risk",
        action="store_true",
        help="Required safety acknowledgement for real-site current-item assisted fill",
    )
    parser.add_argument(
        "--batch-human-confirm-current-done",
        action="store_true",
        help="Mark the current WAITING_FOR_HUMAN_CONFIRM item as DONE and unlock the next PENDING item. No browser, no fill, no submit.",
    )
    parser.add_argument(
        "--i-confirm-current-item-is-complete",
        action="store_true",
        help="Required safety flag for --batch-human-confirm-current-done. Attests the current item was manually checked and is complete.",
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
    parser.add_argument("--profile", dest="profile_path", help="Read a local site_profile JSON file")
    parser.add_argument("--site-name", help="Site name to record in a saved site_profile")
    parser.add_argument("--page-name", help="Page name to record in a saved site_profile")
    parser.add_argument("--captured-at", help="Optional capture timestamp to record in a saved site_profile")
    parser.add_argument("--out", dest="output_path", help="Output path for JSON export commands")
    parser.add_argument("--out-dir", dest="output_dir", help="Output directory for demo packs")
    parser.add_argument("--overwrite", action="store_true", help="Allow overwriting an existing queue output file")
    parser.add_argument("--pretty", action="store_true", help="Print a human-readable report")
    parser.add_argument(
        "--concise",
        action="store_true",
        help="With --map-dry-run, print a short safety summary instead of the full candidate dump",
    )
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


def _human_confirm_current_done(queue: dict[str, Any]) -> dict[str, Any]:
    """Mark the single WAITING_FOR_HUMAN_CONFIRM item as DONE and unlock
    the next PENDING item.  No browser, no fill, no submit.

    Safety gate: rejects if the WAITING item lacks ``fill_completed_at``
    (a timestamp written by real-site-assisted-fill).  This prevents
    accidentally marking an item DONE before real-site fill occurred.
    """
    from betguard.webfill.batch_audit import sync_batch_audit

    waiting = [
        item for item in queue.get("items", [])
        if item.get("status") == BQ_WAITING_FOR_HUMAN_CONFIRM
    ]
    if not waiting:
        raise ValueError("no item is WAITING_FOR_HUMAN_CONFIRM")
    if len(waiting) > 1:
        raise ValueError("multiple items are WAITING_FOR_HUMAN_CONFIRM; cannot auto-confirm")

    item = waiting[0]
    if not item.get("fill_completed_at"):
        raise ValueError(
            "item is WAITING_FOR_HUMAN_CONFIRM but has no fill_completed_at. "
            "Run real-site-assisted-fill first. "
            "Do NOT mark DONE before real-site fill."
        )

    updated = mark_current_done_by_human(queue)
    sync_batch_audit(updated)
    return updated


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
            refusal = {
                "status": "REFUSED",
                "errors": [
                    "--auto-confirm-mock is a test-only simulation and is disabled for normal CLI use; "
                    "use --batch-review-accept-valid and --batch-mock-next so every item goes through "
                    "the human-approved fill queue"
                ],
                "final_decision": {
                    "real_site_operation": False,
                    "auto_submit": False,
                    "human_required_each_item": True,
                },
            }
            if args.pretty:
                print("Batch Mock Run")
                print("")
                for error in refusal["errors"]:
                    print(f"Error: {error}")
            else:
                print(json.dumps(refusal, ensure_ascii=False, indent=2))
            return
        queue = build_batch_mock_queue(raw_text)
        if queue.get("status") == "READY_FOR_QUEUE":
            try:
                queue = run_current_mock_queue_item(queue)
            except ValueError as exc:
                queue = dict(queue)
                queue["errors"] = [str(exc)]
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

    if args.batch_human_confirm_current_done:
        if not args.i_confirm_current_item_is_complete:
            parser.error(
                "--batch-human-confirm-current-done requires --i-confirm-current-item-is-complete"
            )
        if not args.queue_path:
            parser.error("--batch-human-confirm-current-done requires --queue")
        queue = load_queue_state(args.queue_path)
        try:
            queue = _human_confirm_current_done(queue)
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

    if args.fill_preview:
        if not args.queue_path:
            parser.error("--fill-preview requires --queue")
        queue = load_queue_state(args.queue_path)
        try:
            preview = build_fill_preview(queue)
        except ValueError as exc:
            preview = {
                "mode": "fill_preview",
                "errors": [str(exc)],
                "entries": [],
                "safety": dict(PREVIEW_SAFETY),
            }
        if args.pretty:
            if preview.get("errors"):
                print("Fill Preview")
                print("")
                for error in preview["errors"]:
                    print(f"Error: {error}")
            else:
                print(format_pretty_fill_preview(preview))
        else:
            print(json.dumps(preview, ensure_ascii=False, indent=2))
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
        if not args.queue_path:
            parser.error("--real-site-fill-plan requires --queue")
        if args.selector_report and args.profile_path:
            parser.error("--real-site-fill-plan accepts only one of --selector-report or --profile")
        if not args.selector_report and not args.profile_path:
            parser.error("--real-site-fill-plan requires one of --selector-report or --profile")
        queue = json.loads(Path(args.queue_path).read_text(encoding="utf-8"))
        if args.profile_path:
            profile = load_site_profile(args.profile_path)
            validation = validate_site_profile(profile)
            if validation.get("status") != "OK":
                report = {
                    "mode": "real_site_assisted_fill_plan",
                    "status": "BLOCKED",
                    "item": None,
                    "planned_actions": [],
                    "missing": [],
                    "errors": ["site profile validation failed"] + list(validation.get("errors", [])),
                    "final_decision": dict(REAL_SITE_FILL_PLAN_FINAL_DECISION),
                }
                if args.pretty:
                    print(format_pretty_real_site_fill_plan(report))
                else:
                    print(json.dumps(report, ensure_ascii=False, indent=2))
                return
            selector_report = profile_as_selector_report(profile)
        else:
            selector_report = json.loads(Path(args.selector_report).read_text(encoding="utf-8"))
        report = build_real_site_assisted_fill_plan(queue, selector_report)
        if args.pretty:
            print(format_pretty_real_site_fill_plan(report))
        else:
            print(json.dumps(report, ensure_ascii=False, indent=2))
        return

    if args.real_site_fill_preflight:
        if not args.queue_path or not args.profile_path:
            parser.error("--real-site-fill-preflight requires --queue and --profile")
        queue = json.loads(Path(args.queue_path).read_text(encoding="utf-8"))
        profile = load_site_profile(args.profile_path)
        report = build_real_site_fill_preflight_report(
            queue,
            profile,
            item_index=args.item_index if args.item_index is not None else 0,
        )
        if args.concise:
            print(format_concise_real_site_fill_preflight(report))
        elif args.pretty:
            print(format_pretty_real_site_fill_preflight(report))
        else:
            print(json.dumps(report, ensure_ascii=False, indent=2))
        return

    if args.real_site_fill_readiness:
        if not args.queue_path or not args.profile_path:
            parser.error("--real-site-fill-readiness requires --queue and --profile")
        queue = json.loads(Path(args.queue_path).read_text(encoding="utf-8"))
        profile = load_site_profile(args.profile_path)
        report = build_readiness_checklist(
            queue,
            profile,
            item_index=args.item_index if args.item_index is not None else 0,
        )
        if args.concise:
            print(format_concise_readiness_checklist(report))
        elif args.pretty:
            print(format_pretty_readiness_checklist(report))
        else:
            print(json.dumps(report, ensure_ascii=False, indent=2))
        return

    if args.real_site_assisted_fill:
        if not args.i_understand_real_site_fill_risk:
            print(RISK_LOCK_MESSAGE)
            return
        if not args.queue_path or not args.profile_path or not args.url:
            parser.error("--real-site-assisted-fill requires --queue, --profile, and --url")
        queue = json.loads(Path(args.queue_path).read_text(encoding="utf-8"))
        profile = load_site_profile(args.profile_path)
        report = run_real_site_assisted_fill(
            queue,
            profile,
            item_index=args.item_index if args.item_index is not None else 0,
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

    if args.save_site_profile:
        if not args.selector_report or not args.output_path or not args.site_name or not args.page_name:
            parser.error("--save-site-profile requires --selector-report, --out, --site-name, and --page-name")
        selector_report = json.loads(Path(args.selector_report).read_text(encoding="utf-8"))
        profile = build_site_profile(
            selector_report,
            site_name=args.site_name,
            page_name=args.page_name,
            captured_at=args.captured_at,
        )
        save_site_profile(profile, args.output_path)
        if args.pretty:
            validation = validate_site_profile(profile)
            print(format_pretty_site_profile_validation(profile, validation))
            print(f"Site profile written: {args.output_path}")
        else:
            print(json.dumps(profile, ensure_ascii=False, indent=2))
        return

    if args.site_profile_report:
        if not args.profile_path:
            parser.error("--site-profile-report requires --profile")
        profile = load_site_profile(args.profile_path)
        validation = validate_site_profile(profile)
        if args.pretty:
            print(format_pretty_site_profile_validation(profile, validation))
        else:
            print(json.dumps(validation, ensure_ascii=False, indent=2))
        return

    if args.numbers_only_fill_plan:
        if not args.fill_plan_path:
            parser.error("--numbers-only-fill-plan requires --fill-plan")
        fill_plan = json.loads(Path(args.fill_plan_path).read_text(encoding="utf-8"))
        numbers_only_plan = to_numbers_only_plan(fill_plan)
        if args.output_path:
            Path(args.output_path).write_text(
                json.dumps(numbers_only_plan, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            print(f"Numbers-only fill plan written: {args.output_path}")
        else:
            print(json.dumps(numbers_only_plan, ensure_ascii=False, indent=2))
        return

    if args.map_dry_run:
        if not args.fill_plan_path:
            parser.error("--map-dry-run requires --fill-plan")
        if args.selector_report and args.profile_path:
            parser.error("--map-dry-run accepts only one of --selector-report or --profile")
        if not args.selector_report and not args.profile_path:
            parser.error("--map-dry-run requires one of --selector-report or --profile")
        fill_plan = json.loads(Path(args.fill_plan_path).read_text(encoding="utf-8"))
        if args.profile_path:
            selector_report = profile_as_selector_report(load_site_profile(args.profile_path))
        else:
            selector_report = json.loads(Path(args.selector_report).read_text(encoding="utf-8"))
        report = build_mapping_report(fill_plan, selector_report)
        if args.concise:
            print(format_concise_mapping_report(report))
        elif args.pretty:
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
    if args.discover_selectors and args.output_path:
        Path(args.output_path).write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"Selector report written: {args.output_path}")
        return
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
