from __future__ import annotations

import csv
import io
import json
from typing import Any


CHINESE_STARS = {
    2: "二",
    3: "三",
    4: "四",
}


def format_bet_summary(result: dict[str, Any]) -> str:
    status = result.get("status")
    errors = result.get("errors", [])
    if status == "error" and errors:
        return "錯誤：" + "；".join(errors)

    bet_type = result.get("type")
    if bet_type == "normal":
        return _format_normal_summary(result)
    if bet_type == "column":
        return _format_column_summary(result)
    if bet_type == "car":
        return _format_car_summary(result)
    if errors:
        return "錯誤：" + "；".join(errors)
    return "無法產生摘要"


def review_to_json(data: dict[str, Any]) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)


def review_to_csv(data: dict[str, Any]) -> str:
    output = io.StringIO()
    writer = csv.DictWriter(
        output,
        fieldnames=["line_no", "status", "type", "raw", "summary", "warnings", "errors"],
        lineterminator="\n",
    )
    writer.writeheader()
    for item in data.get("items", []):
        result = item.get("result", {})
        writer.writerow(
            {
                "line_no": item.get("line_no"),
                "status": result.get("status"),
                "type": result.get("type"),
                "raw": item.get("raw"),
                "summary": item.get("summary") or format_bet_summary(result),
                "warnings": "; ".join(result.get("warnings", [])),
                "errors": "; ".join(result.get("errors", [])),
            }
        )
    return output.getvalue()


def ok_summaries_text(data: dict[str, Any]) -> str:
    summaries = [
        item.get("summary") or format_bet_summary(item.get("result", {}))
        for item in data.get("items", [])
        if item.get("result", {}).get("status") == "ok"
    ]
    return "\n".join(summaries)


def attach_summaries(data: dict[str, Any]) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    for item in data.get("items", []):
        copied = dict(item)
        copied["summary"] = format_bet_summary(item.get("result", {}))
        items.append(copied)
    copied_data = dict(data)
    copied_data["items"] = items
    return copied_data


def _format_normal_summary(result: dict[str, Any]) -> str:
    numbers = ", ".join(_format_number(number) for number in result.get("numbers", []))
    return "｜".join(
        [
            f"一般：{numbers}",
            _format_stars(result.get("stars", [])),
            _format_unit(result.get("unit")),
            _format_money(result.get("money")),
        ]
    )


def _format_column_summary(result: dict[str, Any]) -> str:
    columns = []
    for index, column in enumerate(result.get("columns", []), start=1):
        numbers = ",".join(_format_number(number) for number in column)
        columns.append(f"第{index}柱 {numbers}")
    parts = ["柱碰：" + "｜".join(columns)]
    parts.append(_format_stars(result.get("stars", [])))
    parts.append(_format_unit(result.get("unit")))
    parts.append(_format_money(result.get("money")))
    return "｜".join(parts)


def _format_car_summary(result: dict[str, Any]) -> str:
    return "｜".join(
        [
            f"車：{result.get('number')}",
            f"{_format_amount(result.get('car_units'))}車",
            _format_money(result.get("money")),
        ]
    )


def _format_stars(stars: list[int]) -> str:
    if not stars:
        return "未指定星數"
    return "".join(CHINESE_STARS.get(star, str(star)) for star in stars) + "星"


def _format_unit(unit: Any) -> str:
    if unit is None:
        return "未指定支數"
    return f"{_format_amount(unit)}支"


def _format_money(money: Any) -> str:
    if money is None:
        return "未指定金額"
    return f"{_format_amount(money)}元"


def _format_number(number: Any) -> str:
    return str(int(number)) if isinstance(number, float) and number.is_integer() else str(number)


def _format_amount(value: Any) -> str:
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)
