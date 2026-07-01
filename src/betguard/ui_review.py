from __future__ import annotations

import re
import sys
from html import escape
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import streamlit as st

from betguard.formatter import attach_summaries, format_bet_summary, ok_summaries_text, review_to_csv, review_to_json
from betguard.log import read_recent_logs, save_review_log
from betguard.review import review_text
from betguard.webfill.batch_queue import (
    BATCH_BLOCKED,
    COMPLETED,
    CURRENT,
    READY,
    WAITING_FOR_HUMAN_CONFIRM,
    build_batch_queue,
    get_current_item,
    mark_current_done_by_human,
    mark_current_waiting_for_human,
    reset_batch_queue,
)


STATUS_LABELS = {
    "ok": "OK",
    "warning": "WARNING",
    "error": "ERROR",
}
REVIEW_GAMES = ["539", "大樂", "天天樂"]
SAMPLE_INPUT = "\n".join(
    [
        "06-13-23-22/50",
        "17.20/28/34 二三×1",
        "尾2、5 二50元",
        "32車10元",
    ]
)
ERROR_SAMPLE_INPUT = "\n".join(
    [
        "06-13-23-22/50",
        "13.38.13 二三100",
    ]
)
INPUT_PLACEHOLDER = SAMPLE_INPUT
GAME_HINT_PATTERN = re.compile(r"^\s*(?:彩種|遊戲)\s*(?:[:：])?\s*(?:539|大樂|天天樂|六合)\s*$")
NO_BET_CONTENT_WARNING = "沒有可解析的下注內容。"
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


def build_next_step_state(summary: dict[str, Any] | None) -> dict[str, Any]:
    if summary is None:
        return {
            "kind": "waiting",
            "title": "等待輸入",
            "messages": ["貼上下牌內容後開始檢查。"],
            "steps": ["貼上下牌內容", "按開始檢查", "查看結果"],
            "button_text": "網站帶入功能準備中",
            "note": "目前此版本只做安全檢查，不會操作網站。",
        }
    if summary.get("can_continue"):
        return {
            "kind": "ok",
            "title": "✅ 檢查通過",
            "messages": ["可以進入網站帶入號碼與金額", "可建立逐筆填單佇列", "提醒：最後送出仍需人工確認"],
            "steps": ["下載 JSON / CSV", "複製 OK 摘要", "可進入逐筆人工確認流程"],
            "button_text": "網站帶入功能準備中",
            "note": "目前此版本只做安全檢查，不會操作網站。",
        }
    return {
        "kind": "blocked",
        "title": "⛔ 已擋下",
        "messages": ["目前不可進入網站帶入", "請先修正下牌內容"],
        "steps": ["查看錯誤筆數", "修正紅色項目", "重新檢查"],
        "button_text": "網站帶入功能準備中",
        "note": "目前此版本只做安全檢查，不會操作網站。",
    }


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


def _visible_result(result: dict[str, Any]) -> dict[str, Any]:
    data: dict[str, Any] = {"game": result.get("game"), "type": result.get("type")}
    for key in [
        "numbers",
        "columns",
        "number",
        "stars",
        "unit",
        "money",
        "car_units",
        "original_text",
        "normalized_text",
        "parse_notes",
    ]:
        if key in result:
            data[key] = result[key]
    data["warnings"] = result.get("warnings", [])
    data["errors"] = result.get("errors", [])
    return data


def _install_css() -> None:
    st.markdown(
        """
        <style>
        .betguard-flow {
            color: #475569;
            font-size: 0.94rem;
            margin: -0.25rem 0 1rem 0;
        }
        .status-card {
            border-radius: 8px;
            border: 1px solid #cbd5e1;
            padding: 14px 16px;
            margin: 10px 0 12px 0;
            background: #f8fafc;
        }
        .status-card h3 {
            margin: 0 0 6px 0;
            font-size: 1.25rem;
        }
        .status-card p {
            margin: 0;
            color: #334155;
        }
        .status-ok {
            border-color: #16a34a;
            background: #ecfdf3;
        }
        .status-warning {
            border-color: #d97706;
            background: #fffbeb;
        }
        .status-error {
            border-color: #dc2626;
            background: #fef2f2;
        }
        .status-waiting {
            border-color: #94a3b8;
            background: #f8fafc;
        }
        .result-card {
            border: 1px solid #e2e8f0;
            border-left-width: 6px;
            border-radius: 8px;
            padding: 10px 12px;
            margin: 8px 0 4px 0;
            background: #ffffff;
        }
        .result-card.ok {
            border-left-color: #16a34a;
        }
        .result-card.warning {
            border-left-color: #d97706;
        }
        .result-card.error {
            border-left-color: #dc2626;
        }
        .result-title {
            font-weight: 700;
            color: #0f172a;
        }
        .result-summary {
            color: #334155;
            margin-top: 4px;
        }
        .small-muted {
            color: #64748b;
            font-size: 0.88rem;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def _status_card(summary: dict[str, Any] | None) -> None:
    if summary is None:
        kind = "waiting"
        title = "等待輸入"
        body = "貼上下牌內容後開始檢查。"
    elif summary.get("can_continue"):
        kind = "ok"
        title = "✅ 全部通過"
        body = "可建立逐筆填單佇列。"
    elif summary.get("error", 0) > 0:
        kind = "error"
        title = "⛔ 已擋下"
        body = "請先修正下牌內容。"
    else:
        kind = "warning"
        title = "⚠️ 有警告"
        body = "請人工確認後再繼續。"

    st.markdown(
        f"""
        <div class="status-card status-{kind}">
            <h3>{escape(title)}</h3>
            <p>{escape(body)}</p>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _metrics(summary: dict[str, Any] | None) -> None:
    data = summary or {"total": 0, "ok": 0, "error": 0, "warning": 0}
    cols = st.columns(4)
    cols[0].metric("總筆數", data.get("total", 0))
    cols[1].metric("OK", data.get("ok", 0))
    cols[2].metric("錯誤", data.get("error", 0))
    cols[3].metric("警告", data.get("warning", 0))


def _render_next_steps(summary: dict[str, Any] | None) -> None:
    state = build_next_step_state(summary)
    st.markdown(f"**{state['title']}**")
    for message in state["messages"]:
        st.caption(message)
    st.markdown("\n".join(f"{index}. {step}" for index, step in enumerate(state["steps"], start=1)))
    st.button(state["button_text"], disabled=True)
    st.caption(state["note"])


def _render_batch_queue_section(summary: dict[str, Any] | None) -> None:
    st.subheader("逐筆作業佇列")
    if summary is None:
        st.caption("完成 Review 後可建立逐筆佇列。")
        return
    if not summary.get("can_continue"):
        st.error("目前整批尚未通過，不能建立逐筆佇列")
        return

    if st.session_state.get("batch_queue") is None:
        if st.button("建立逐筆佇列", use_container_width=True):
            st.session_state.batch_queue = build_batch_queue(summary)
            st.rerun()
        return

    queue = st.session_state.batch_queue
    if queue.get("status") == BATCH_BLOCKED:
        st.error(queue.get("reason") or "review result is not ok")
        return
    if queue.get("status") == COMPLETED:
        st.success("整批已完成")

    current = _queue_visible_item(queue)
    if current is not None:
        display_index = int(current.get("index", 0)) + 1
        total = int(queue.get("total", 0))
        st.markdown(f"**目前第 {display_index} / {total} 筆**")
        st.write("目前項目摘要", current.get("summary", ""))
        st.write("原始文字", current.get("original_text", ""))
        st.write("狀態", current.get("status", ""))
    else:
        st.caption("目前沒有 CURRENT 或等待人工確認的項目。")

    wait_disabled = queue.get("status") != READY or current is None or current.get("status") != CURRENT
    done_disabled = queue.get("status") != WAITING_FOR_HUMAN_CONFIRM
    col1, col2 = st.columns(2)
    if col1.button("我已確認，進入等待人工送出", disabled=wait_disabled, use_container_width=True):
        st.session_state.batch_queue = mark_current_waiting_for_human(queue)
        st.rerun()
    if col2.button("這筆已人工完成，下一筆", disabled=done_disabled, use_container_width=True):
        st.session_state.batch_queue = mark_current_done_by_human(queue)
        st.rerun()
    if st.button("重設佇列", disabled=queue.get("status") == BATCH_BLOCKED, use_container_width=True):
        st.session_state.batch_queue = reset_batch_queue(queue)
        st.rerun()


def _queue_visible_item(queue: dict[str, Any]) -> dict[str, Any] | None:
    current = get_current_item(queue)
    if current is not None:
        return current
    for item in queue.get("items", []):
        if item.get("status") == WAITING_FOR_HUMAN_CONFIRM:
            return item
    return None


def _render_gate_message(summary: dict[str, Any] | None) -> None:
    if summary is None:
        st.info("等待檢查。")
    elif summary.get("can_continue"):
        st.success("安全檢查通過：可進入網站帶入號碼與金額，但最後送出仍需人工確認。")
    else:
        st.error("目前不可進入網站帶入，請先修正下牌內容。")
        for warning in summary.get("warnings", []):
            st.warning(warning)


def _render_downloads(summary: dict[str, Any]) -> None:
    left, right = st.columns(2)
    left.download_button(
        "下載 JSON",
        data=review_to_json(summary),
        file_name="betguard_review.json",
        mime="application/json",
        use_container_width=True,
    )
    right.download_button(
        "下載 CSV",
        data=review_to_csv(summary),
        file_name="betguard_review.csv",
        mime="text/csv",
        use_container_width=True,
    )


def _render_ok_summaries(summary: dict[str, Any]) -> None:
    left, right = st.columns([2, 1])
    left.text_area(
        "只複製 OK 摘要",
        value=ok_summaries_text(summary),
        height=150,
    )
    right.info("可直接貼給人工確認或下一步流程。")


def _render_item_card(item: dict[str, Any]) -> None:
    result = item.get("result", {})
    status = result.get("status", "error")
    label = STATUS_LABELS.get(status, status.upper())
    raw = str(item.get("raw", ""))
    summary = str(item.get("summary") or format_bet_summary(result))
    line = item.get("line_no", "")

    st.markdown(
        f"""
        <div class="result-card {escape(status)}">
            <div class="result-title">[{escape(label)}] 第 {escape(str(line))} 筆｜{escape(raw)}</div>
            <div class="result-summary">{escape(summary)}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    with st.expander("詳細 JSON", expanded=status in {"warning", "error"}):
        st.write("彩種", result.get("game", ""))
        st.write("原始文字", raw)
        st.write("解析結果", summary)
        if result.get("warnings"):
            st.warning("；".join(str(item) for item in result["warnings"]))
        if result.get("errors"):
            st.error("；".join(str(item) for item in result["errors"]))
        st.json(_visible_result(result), expanded=False)


def _render_items(items: list[dict[str, Any]]) -> None:
    if not items:
        st.info("沒有可顯示的下注內容。")
        return
    for item in items:
        _render_item_card(item)


def _render_history() -> None:
    st.subheader("最近 10 筆 Review 紀錄")
    logs = read_recent_logs(10)
    if not logs:
        st.caption("目前沒有 Review 紀錄。")
        return

    for index, log_entry in enumerate(logs):
        summary = log_entry.get("summary", {})
        filename = log_entry.get("filename", "review_log.json")
        can_continue = str(summary.get("can_continue")).lower()
        with st.container(border=True):
            top, download = st.columns([3, 1])
            top.markdown(f"**{log_entry.get('created_at', '')}**")
            top.caption(
                f"{filename}｜total={summary.get('total')} ok={summary.get('ok')} "
                f"warning={summary.get('warning')} error={summary.get('error')} "
                f"can_continue={can_continue}"
            )
            payload = dict(log_entry)
            payload.pop("filename", None)
            download.download_button(
                "下載",
                data=review_to_json(payload),
                file_name=filename,
                mime="application/json",
                key=f"download-review-log-{index}-{filename}",
                use_container_width=True,
            )


def _render_tabs(summary: dict[str, Any] | None) -> None:
    tab1, tab2, tab3, tab4 = st.tabs(["全部", "錯誤/警告", "OK 摘要", "下載/紀錄"])
    if summary is None:
        with tab1:
            st.info("尚未檢查。")
        with tab2:
            st.info("尚未檢查。")
        with tab3:
            st.text_area("只複製 OK 摘要", value="", height=120)
        with tab4:
            _render_history()
        return

    items = list(summary.get("items", []))
    problem_items = [
        item
        for item in items
        if item.get("result", {}).get("status") in {"warning", "error"}
    ]

    with tab1:
        _render_items(items)
    with tab2:
        if problem_items:
            _render_items(problem_items)
        else:
            st.success("目前沒有錯誤")
    with tab3:
        _render_ok_summaries(summary)
    with tab4:
        _render_downloads(summary)
        st.divider()
        _render_history()


def _ensure_session_state() -> None:
    st.session_state.setdefault("input_text", "")
    st.session_state.setdefault("review_summary", None)
    st.session_state.setdefault("last_log_name", "")
    st.session_state.setdefault("batch_queue", None)


def main() -> None:
    st.set_page_config(
        page_title="539 下牌風控助理",
        layout="wide",
    )
    _ensure_session_state()
    _install_css()

    st.title("539 下牌風控助理")
    st.caption("整批先檢查，一筆一筆人工確認送出")
    st.caption("目前此頁只做解析、風控、報告與 Dry-run 前置，不會自動送出下注。")
    st.markdown(
        '<div class="betguard-flow">1 貼上內容 → 2 開始檢查 → 3 修正錯誤 → 4 全部 OK → 5 進入逐筆人工確認</div>',
        unsafe_allow_html=True,
    )

    left, middle, right = st.columns([45, 25, 30])

    with left:
        st.subheader("輸入下牌內容")
        game = st.selectbox("彩種", REVIEW_GAMES, index=0)
        st.caption("請先選擇彩種，再貼上下牌文字。")
        st.caption("彩種請用上方選單選擇，下注框只需貼下牌內容。")
        st.text_area(
            "貼上 LINE 下注文字",
            height=300,
            placeholder=INPUT_PLACEHOLDER,
            key="input_text",
        )
        quick_a, quick_b = st.columns(2)
        if quick_a.button("載入測試資料", use_container_width=True):
            st.session_state.input_text = SAMPLE_INPUT
            st.rerun()
        if quick_b.button("載入錯誤範例", use_container_width=True):
            st.session_state.input_text = ERROR_SAMPLE_INPUT
            st.rerun()
        if st.button("開始檢查", type="primary", use_container_width=True):
            summary = build_ui_review_summary(st.session_state.input_text, game=game)
            st.session_state.review_summary = summary
            st.session_state.batch_queue = None
            log_path = save_review_log(st.session_state.input_text, summary)
            st.session_state.last_log_name = log_path.name

    summary = st.session_state.get("review_summary")
    with middle:
        st.subheader("檢查結果")
        _metrics(summary)
        _status_card(summary)
        _render_gate_message(summary)
        if st.session_state.get("last_log_name"):
            st.caption(f"已儲存紀錄：{st.session_state.last_log_name}")

    with right:
        st.subheader("下一步")
        _render_next_steps(summary)
        _render_batch_queue_section(summary)

    st.divider()
    _render_tabs(summary)
    st.caption(BOTTOM_SAFETY_REMINDER)


if __name__ == "__main__":
    main()
