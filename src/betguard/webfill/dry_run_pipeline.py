from __future__ import annotations

from pathlib import Path
from typing import Any

from betguard.review import review_text
from betguard.webfill.fill_mapping import build_dry_run_mapping
from betguard.webfill.fill_mapping_report import build_mapping_report
from betguard.webfill.fill_plan import build_fill_plan


FINAL_DECISION = {
    "executable": False,
    "reason": "dry-run only; human review required",
}


def build_dry_run_pipeline_from_text(text: str, selector_report: dict[str, Any]) -> dict[str, Any]:
    review_summary = review_text(text).to_dict()
    items: list[dict[str, Any]] = []
    safe = 0
    blocked = 0

    for item in review_summary.get("items", []):
        pipeline_item = _build_item(item, selector_report)
        items.append(pipeline_item)
        if pipeline_item["status"] == "SAFE":
            safe += 1
        else:
            blocked += 1

    return {
        "mode": "assisted_fill_dry_run_pipeline",
        "summary": {
            "total": len(items),
            "safe": safe,
            "blocked": blocked,
            "executable": False,
        },
        "items": items,
        "final_decision": dict(FINAL_DECISION),
    }


def build_dry_run_pipeline_from_file(path: str | Path, selector_report: dict[str, Any]) -> dict[str, Any]:
    return build_dry_run_pipeline_from_text(Path(path).read_text(encoding="utf-8"), selector_report)


def format_pretty_pipeline_report(report: dict[str, Any]) -> str:
    summary = report.get("summary", {})
    lines = [
        "Assisted Fill Dry-run Pipeline",
        "",
        "Summary:",
        f"- total: {summary.get('total', 0)}",
        f"- safe: {summary.get('safe', 0)}",
        f"- blocked: {summary.get('blocked', 0)}",
        "- executable: false",
        "",
        "Items:",
    ]

    for index, item in enumerate(report.get("items", []), start=1):
        lines.append(f"[{index}] {item.get('status', 'BLOCKED')}")
        lines.append(f"    original: {item.get('raw', '')}")
        if item.get("parsed"):
            lines.append(f"    parsed: {item['parsed']}")
        reason = item.get("reason")
        if item.get("status") == "SAFE":
            lines.append("    mapping: all selectors found")
        elif reason:
            lines.append(f"    reason: {reason}")
        lines.append("    final: executable=false")

    final_decision = report.get("final_decision", FINAL_DECISION)
    lines.extend(
        [
            "",
            "Final Decision:",
            f"- executable: {str(final_decision.get('executable')).lower()}",
            f"- reason: {final_decision.get('reason')}",
        ]
    )
    return "\n".join(lines)


def _build_item(item: dict[str, Any], selector_report: dict[str, Any]) -> dict[str, Any]:
    result = item.get("result", {})
    base = {
        "line_no": item.get("line_no"),
        "raw": item.get("raw"),
        "review_result": result,
        "parsed": _parsed_summary(result),
        "final_decision": dict(FINAL_DECISION),
    }

    if result.get("status") != "ok":
        return {
            **base,
            "status": "BLOCKED",
            "reason": _review_block_reason(result),
        }

    fill_plan = build_fill_plan(result)
    mapping = build_dry_run_mapping(fill_plan, selector_report)
    mapping_report = build_mapping_report(fill_plan, selector_report, mapping_result=mapping)
    status = mapping_report.get("status", "BLOCKED")

    return {
        **base,
        "status": status,
        "reason": _mapping_block_reason(mapping_report) if status != "SAFE" else "",
        "fill_plan": fill_plan,
        "mapping": mapping,
        "mapping_report": mapping_report,
    }


def _review_block_reason(result: dict[str, Any]) -> str:
    messages = list(result.get("errors", [])) + list(result.get("warnings", []))
    return "; ".join(str(message) for message in messages) or "review result is not ok"


def _mapping_block_reason(report: dict[str, Any]) -> str:
    if report.get("errors"):
        return "; ".join(str(error) for error in report["errors"])
    if report.get("missing"):
        return "; ".join(_format_missing(item) for item in report["missing"])
    if report.get("warnings"):
        return "; ".join(str(warning) for warning in report["warnings"])
    return "mapping blocked"


def _parsed_summary(result: dict[str, Any]) -> str:
    if result.get("type") == "normal":
        numbers = ",".join(_format_number(number) for number in result.get("numbers", []))
        stars = _format_stars(result.get("stars", []))
        money = result.get("money")
        money_text = f"{money}元" if money is not None else "missing money"
        return f"一般 {numbers}｜{stars}｜{money_text}"
    return str(result.get("type", "unknown"))


def _format_number(number: Any) -> str:
    return f"{int(number):02d}"


def _format_stars(stars: list[Any]) -> str:
    labels = {2: "二", 3: "三", 4: "四"}
    text = "".join(labels.get(int(star), str(star)) for star in stars)
    return f"{text}星" if text else "missing stars"


def _format_missing(item: dict[str, Any]) -> str:
    if item.get("type") == "number":
        return f"number {item.get('label')} selector missing"
    if item.get("type") == "amount":
        return f"amount field {item.get('star')} missing"
    if item.get("type") == "danger":
        return "danger buttons not verified"
    return str(item)
