"""Test a Chinese full-page combined prompt (with bbox tokens) on sample-009.

Goal: verify whether the model reads stacked category digits (3 over 4 ->
34x1) in the FIRST full-page pass, instead of needing the focused re-read.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import test_combined_bbox as tcb

PROMPT_ZH = """這是一張手寫的台灣 539（01-39）或香港六合彩（01-49）彩券。請讀取並輸出 SECTION 結構的 JSON。

重要：一個 section 就是「一張牌組」。圖上有 N 張牌組就輸出 N 個 section。絕對不要把兩張牌組放進同一個 section，也不要把一張牌組拆成兩個 section。

柱碰（column bet）是垂直對齊的欄位：一個 section 要包含這個柱碰的「所有列」（上到下），每欄上下堆疊的號碼要全部保留。
一般行（normal row）是一條水平線：一個 section 只有一列。

每一個 token（號碼、分隔 x、數字、符號）都要附像素座標 bbox [x1,y1,x2,y2]。
只輸出 JSON：
{"sections": [
  {"rows": [
     {"tokens": [{"text": "24", "bbox": [10,20,40,50]}, {"text": "x", "bbox": [45,20,60,50]}], "numbers": [["24"],["03"]], "multiplier": null, "layout_hint": "column_bet"}
  ], "shared_multiplier": null}
]}

規則：
- 柱碰的 numbers 是「欄的列表」：每個 inner list 是一欄（上到下），欄內每一個堆疊號碼都要放進去，不可以漏掉欄位下方的號碼。
- 號碼之間的 x 或 / 是欄分隔 token（text 是 "x"），不是倍率。
- 右下角的碰法（例如 4/3）和倍率（例如 x0.1）是分開的 token，不要放進任何欄位。
- **疊寫類別數字**：如果 3 的下面還有一個 4，倍率 token 必須是 "34x1" 或 "3/4x1"，絕對不可以只讀 "3x1"；如果 2 的上面或下面還有 3，必須是 "23x1" 或 "2/3x1"。上下兩個數字都要讀出來，禁止只讀一個。也不要輸出分數字元 ¾ 或 ⅔。
- 保留前導零（03 不是 3）。40-49 是合法號碼（六合彩）。
- 不要展開組合。不要發明號碼。不要漏掉欄位下方或疊寫的號碼。"""


def main() -> None:
    img = Path(os.environ["BETGUARD_DATASET"]) / "raw" / "sample-009.jpg"
    content = tcb.call_with_prompt(img, PROMPT_ZH)
    out = Path(__file__).resolve().parent / "ab_results" / "prelim" / "sample-009-zh-full.json"
    out.write_text(content, encoding="utf-8")
    m = json.JSONDecoder().raw_decode(content.lstrip())[0] if content.lstrip().startswith("{") else None
    if m is None:
        import re
        mm = re.search(r"\{.*\}", content, re.S)
        m = json.loads(mm.group(0)) if mm else None
    print("sections:", len((m or {}).get("sections") or []))
    for si, sec in enumerate((m or {}).get("sections") or [], 1):
        for row in sec.get("rows") or []:
            toks = [str(t.get("text") or "") for t in row.get("tokens") or []]
            nums = row.get("numbers")
            print(f"S{si:02d} | {row.get('layout_hint')} | toks={' '.join(toks)[:70]!r} | nums={json.dumps(nums, ensure_ascii=False)[:80]} | mult={row.get('multiplier')!r}")


if __name__ == "__main__":
    main()
