from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from betguard.game_rules import GAME_RULES
from betguard.review import (
    build_report,
    format_input_diagnostic_report,
    format_pretty_review,
    review_text,
)
from betguard.webfill.fill_mapping import build_dry_run_mapping
from betguard.webfill.fill_plan import build_fill_plan


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="betguard",
        description="Parse betting text and return a risk-control JSON report.",
    )
    parser.add_argument("text", nargs="?", help="LINE betting text, for example: 06-13-23-22/50")
    parser.add_argument("--file", dest="file_path", help="Read multi-line betting text from a UTF-8 file.")
    parser.add_argument("--text", dest="batch_text", help="Review multi-line betting text passed as one argument.")
    parser.add_argument("--game", choices=sorted(GAME_RULES), default="539", help="Game rules to use. Defaults to 539.")
    parser.add_argument("--pretty", action="store_true", help="Print a human-readable review summary.")
    parser.add_argument("--fill-plan", action="store_true", help="Output an assisted fill plan preview JSON.")
    parser.add_argument("--map-selectors", help="Read a selector discovery JSON report and output a dry-run mapping.")
    parser.add_argument(
        "--input-format-report",
        action="store_true",
        help="Print a read-only line-by-line diagnostic of every input line "
        "(Raw status / Display status / Type / Numbers / Stars / Unit / "
        "Money / Reason / Raw errors / Raw warnings). No queue or fill state "
        "is touched.",
    )
    args = parser.parse_args(argv)

    selected_inputs = [
        args.text is not None,
        args.file_path is not None,
        args.batch_text is not None,
    ]
    if sum(selected_inputs) != 1:
        parser.error("provide exactly one of positional text, --file, or --text")
    if args.map_selectors and not args.fill_plan:
        parser.error("--map-selectors requires --fill-plan")
    if args.map_selectors and (args.file_path is not None or args.batch_text is not None):
        parser.error("--map-selectors v0 supports single positional text only")
    if args.input_format_report and args.fill_plan:
        parser.error("--input-format-report and --fill-plan are mutually exclusive")

    if args.input_format_report:
        if args.file_path is not None:
            raw_text = Path(args.file_path).read_text(encoding="utf-8")
        else:
            raw_text = args.batch_text
        summary = review_text(raw_text, game=args.game)
        print(format_input_diagnostic_report(summary))
        return 0

    if args.file_path is not None:
        raw_text = Path(args.file_path).read_text(encoding="utf-8")
        summary = review_text(raw_text, game=args.game)
        if args.fill_plan:
            plan = build_fill_plan(summary.to_dict())
            print(json.dumps(plan, ensure_ascii=False, indent=2))
        elif args.pretty:
            print(format_pretty_review(summary))
        else:
            print(json.dumps(summary.to_dict(), ensure_ascii=False, indent=2))
        return 0 if summary.can_continue else 1

    if args.batch_text is not None:
        summary = review_text(args.batch_text, game=args.game)
        if args.fill_plan:
            plan = build_fill_plan(summary.to_dict())
            print(json.dumps(plan, ensure_ascii=False, indent=2))
        elif args.pretty:
            print(format_pretty_review(summary))
        else:
            print(json.dumps(summary.to_dict(), ensure_ascii=False, indent=2))
        return 0 if summary.can_continue else 1

    report = build_report(args.text, game=args.game)
    result = report.to_dict()
    if args.fill_plan:
        plan = build_fill_plan(result)
        if args.map_selectors:
            selector_report = json.loads(Path(args.map_selectors).read_text(encoding="utf-8"))
            mapping = build_dry_run_mapping(plan, selector_report)
            print(json.dumps(mapping, ensure_ascii=False, indent=2))
            return 1 if mapping.get("errors") else 0
        print(json.dumps(plan, ensure_ascii=False, indent=2))
        return 1 if plan.get("errors") else 0
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if report.validation.errors else 0


if __name__ == "__main__":
    sys.exit(main())
