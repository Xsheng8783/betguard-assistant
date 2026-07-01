from __future__ import annotations

import sys
import re
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import streamlit as st

from betguard.formatter import attach_summaries, ok_summaries_text, review_to_csv, review_to_json
from betguard.log import read_recent_logs, save_review_log
from betguard.review import review_text
from betguard.webfill.fill_plan import build_fill_plan


STATUS_LABELS = {
    "ok": "OK",
    "warning": "WARNING",
    "error": "ERROR",
}
REVIEW_GAMES = ["539", "大樂", "天天樂"]
INPUT_PLACEHOLDER = "\n".join(
    [
        "06.13.23.22 二三50",
        "17.20/28/34 二三1",
        "尾2、5 二50元",
        "32車10元",
    ]
)
GAME_HINT_PATTERN = re.compile(r"^\s*(?:彩種|遊戲)\s*[:：]?\s*(?:539|大樂|天天樂|六合)\s*$")
NO_BET_CONTENT_WARNING = "沒有可解析的下注內容。"
OK_NEXT_STEPS = [
    "開啟網站",
    "手動登入",
    "進入正確彩種與玩法頁",
    "由系統帶入號碼與金額",
    "使用者本人確認畫面",
    "使用者本人按確認 / 送出",
]
BLOCKED_NEXT_STEPS = [
    "查看錯誤原因",
    "修正下牌文字",
    "重新開始檢查",
]
WEBFILL_PREP_BUTTON_TEXT = "網站帶入功能準備中"
WEBFILL_PREP_NOTE = "目前此版本只做安全檢查，不會操作網站。"
BOTTOM_SAFETY_REMINDER = "本工具用於加快輸入與降低錯單風險，不會自動完成下注送出。"


def build_ui_review_summary(text: str, game: str = "539") -> dict[str, Any]:
    cleaned_text = sanitize_review_input(text)
    if not cleaned_text.strip():
        return _empty_review_warning()
    return attach_summaries(review_text(cleaned_text, game=game).to_dict())


def sanitize_review_input(text: str) -> str:
    kept_lines: list[str] = []
    for line in str(text or "").splitlines():
        raw = line.strip()
        if not raw:
            kept_lines.append(line)
            continue
        if GAME_HINT_PATTERN.fullmatch(raw):
            continue
        kept_lines.append(line)
    return "\n".join(kept_lines)


def _empty_review_warning() -> dict[str, Any]:
    return {
        "total": 0,
        "ok": 0,
        "warning": 1,
        "error": 0,
        "items": [],
        "can_continue": False,
        "warnings": [NO_BET_CONTENT_WARNING],
    }


def build_next_step_state(summary: dict[str, Any]) -> dict[str, Any]:
    if summary.get("can_continue"):
        return {
            "kind": "ok",
            "title": "✅ 檢查通過",
            "messages": [
                "可以進入網站帶入號碼與金額",
                "提醒：最後送出仍需人工確認",
            ],
            "steps": list(OK_NEXT_STEPS),
            "button_text": WEBFILL_PREP_BUTTON_TEXT,
            "note": WEBFILL_PREP_NOTE,
        }
    return {
        "kind": "blocked",
        "title": "⛔ 已擋下",
        "messages": [
            "目前不可進入網站帶入",
            "請先修正下牌內容",
        ],
        "steps": list(BLOCKED_NEXT_STEPS),
        "button_text": WEBFILL_PREP_BUTTON_TEXT,
        "note": WEBFILL_PREP_NOTE,
    }


def _visible_result(result: dict[str, Any]) -> dict[str, Any]:
    data: dict[str, Any] = {"game": result.get("game"), "type": result.get("type")}
    for key in ["numbers", "columns", "number", "stars", "unit", "money", "car_units"]:
        if key in result:
            data[key] = result[key]
    data["warnings"] = result.get("warnings", [])
    data["errors"] = result.get("errors", [])
    return data


def _render_status_message(summary: dict[str, Any]) -> None:
    if summary["error"] > 0:
        st.error("有錯誤，已擋下，不可繼續")
    elif summary["warning"] > 0:
        st.warning("有警告，請人工確認後再繼續")
    else:
        st.success("檢查通過，可進入人工確認流程")


def _render_gate_message(summary: dict[str, Any]) -> None:
    if summary["can_continue"]:
        st.success("安全檢查通過：可進入網站帶入號碼與金額，但最後送出仍需人工確認。")
    else:
        st.error("目前不可進入網站帶入，請先修正下牌內容。")


def _render_next_step_section(summary: dict[str, Any]) -> None:
    state = build_next_step_state(summary)
    status_text = "\n\n".join([state["title"], *state["messages"]])
    if state["kind"] == "ok":
        st.success(status_text)
    else:
        st.error(status_text)

    st.subheader("下一步操作")
    st.markdown("\n".join(f"{index}. {step}" for index, step in enumerate(state["steps"], start=1)))
    button_col, note_col = st.columns([1, 3])
    button_col.button(state["button_text"], disabled=True)
    note_col.caption(state["note"])


def _render_summary(summary: dict[str, Any]) -> None:
    cols = st.columns(5)
    cols[0].metric("total", summary["total"])
    cols[1].metric("ok", summary["ok"])
    cols[2].metric("warning", summary["warning"])
    cols[3].metric("error", summary["error"])
    cols[4].metric("can_continue", str(summary["can_continue"]).lower())


def _render_downloads(summary: dict[str, Any]) -> None:
    left, right = st.columns(2)
    left.download_button(
        "下載 JSON",
        data=review_to_json(summary),
        file_name="betguard_review.json",
        mime="application/json",
    )
    right.download_button(
        "下載 CSV",
        data=review_to_csv(summary),
        file_name="betguard_review.csv",
        mime="text/csv",
    )


def _render_ok_summaries(summary: dict[str, Any]) -> None:
    st.subheader("只複製 OK 摘要")
    st.text_area(
        "OK 摘要",
        value=ok_summaries_text(summary),
        height=160,
        label_visibility="collapsed",
    )


def _render_fill_plan_preview(summary: dict[str, Any]) -> None:
    plan = build_fill_plan(summary)
    st.subheader("Assisted Fill Plan 預覽")
    st.info("此階段不會操作網站、不會送出注單。")

    if plan.get("errors"):
        for error in plan["errors"]:
            st.warning(error)
        return

    for item_plan in plan.get("plans", []):
        with st.container(border=True):
            st.write("line_no", item_plan.get("line_no"))
            st.write("raw", item_plan.get("raw"))
            st.write("numbers", item_plan.get("numbers", []))
            st.write("stars", item_plan.get("stars", []))
            st.write("amounts", item_plan.get("amounts", {}))
            st.json(
                {
                    "planned_steps": item_plan.get("planned_steps", []),
                    "forbidden_steps": item_plan.get("forbidden_steps", []),
                },
                expanded=False,
            )

    for skipped in plan.get("skipped", []):
        st.caption(f"skipped line {skipped.get('line_no')}: {skipped.get('reason')}")


def _render_item(item: dict[str, Any]) -> None:
    result = item["result"]
    status = result.get("status", "error")
    label = STATUS_LABELS.get(status, status.upper())
    title = f"[{item['line_no']}] {label} {result.get('type', 'unknown')}"

    if status == "ok":
        st.success(title)
    elif status == "warning":
        st.warning(title)
    else:
        st.error(title)

    st.write("彩種", result.get("game", ""))
    st.write("原始文字", item["raw"])
    st.write("解析結果", item.get("summary", ""))
    st.write("狀態", label)
    st.json(_visible_result(result), expanded=False)

    warnings = result.get("warnings", [])
    errors = result.get("errors", [])
    if warnings:
        st.write("警告原因", warnings)
        for warning in warnings:
            st.warning(warning)
    if errors:
        st.write("錯誤原因", errors)
        for error in errors:
            st.error(error)


def _render_webfill_flow() -> None:
    st.subheader("網站帶入流程")
    st.markdown(
        "\n".join(
            [
                "1. 系統會自動帶入對應號碼",
                "2. 系統會自動填入二星 / 三星 / 四星金額",
                "3. 系統不會自動送出",
                "4. 最後由使用者本人確認 / 送出",
            ]
        )
    )


def _history_download_json(log_entry: dict[str, Any]) -> str:
    payload = dict(log_entry)
    payload.pop("filename", None)
    return review_to_json(payload)


def _render_history() -> None:
    st.subheader("最近 10 筆 Review 紀錄")
    logs = read_recent_logs(10)
    if not logs:
        st.caption("尚無 Review 紀錄。")
        return

    for index, log_entry in enumerate(logs):
        summary = log_entry.get("summary", {})
        filename = log_entry.get("filename", "review_log.json")
        can_continue = str(summary.get("can_continue")).lower()
        with st.container(border=True):
            st.markdown(f"**{log_entry.get('created_at', '')}**")
            st.write("檔名", filename)
            st.write(
                "Summary",
                f"total={summary.get('total')} ok={summary.get('ok')} "
                f"warning={summary.get('warning')} error={summary.get('error')} "
                f"can_continue={can_continue}",
            )
            st.download_button(
                "下載該筆 JSON log",
                data=_history_download_json(log_entry),
                file_name=filename,
                mime="application/json",
                key=f"download-review-log-{index}-{filename}",
            )


def main() -> None:
    st.set_page_config(page_title="539 / 大樂 下牌風控助理")
    st.title("539 / 大樂 下牌風控助理")
    st.markdown(
        "\n".join(
            [
                "1. 選擇彩種",
                "2. 貼上下牌文字",
                "3. 按開始檢查",
                "4. 檢查安全結果",
                "5. 通過後才進入網站帶入，送出仍需人工確認",
            ]
        )
    )
    st.info("請先選擇彩種，再貼上下牌文字。")
    st.caption("彩種請用上方選單選擇，下注框只需貼下牌內容。")

    game = st.selectbox("彩種", REVIEW_GAMES, index=0)
    text = st.text_area(
        "貼上下牌文字",
        height=240,
        placeholder=INPUT_PLACEHOLDER,
    )
    if st.button("開始檢查", type="primary"):
        summary = build_ui_review_summary(text, game=game)
        log_path = save_review_log(text, summary)
        st.success(f"已儲存紀錄：{log_path.name}")
        for warning in summary.get("warnings", []):
            st.warning(warning)
        _render_status_message(summary)
        _render_next_step_section(summary)
        _render_summary(summary)
        if summary["total"] == 0:
            _render_history()
            st.caption(BOTTOM_SAFETY_REMINDER)
            return
        _render_downloads(summary)
        _render_ok_summaries(summary)
        _render_fill_plan_preview(summary)
        _render_webfill_flow()

        for item in summary["items"]:
            with st.container(border=True):
                _render_item(item)

    _render_history()
    st.caption(BOTTOM_SAFETY_REMINDER)


if __name__ == "__main__":
    main()
