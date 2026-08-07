"""Versioned prompts used by the DashScope Qwen vision integration.

Prompt text is intentionally kept byte-for-byte identical to the original
debug scripts.  Change detection is enforced by SHA-256 snapshot tests.
"""

from __future__ import annotations

import hashlib


PROMPT_VERSION = "combined-bbox-v1"
FOCUSED_PROMPT_VERSION = "focused-crop-v1"
PLAY_MARK_PROMPT_VERSION = "play-mark-v2"
COLUMN_COMBO_PROMPT_VERSION = "column-combo-v1"

TASK_TYPE_FULL_PAGE = "full_page_combined"
TASK_TYPE_FOCUSED_CROP = "focused_crop"
TASK_TYPE_PLAY_MARK = "play_mark"
TASK_TYPE_COLUMN_COMBO = "column_combo"


PROMPT = """This is a handwritten Taiwan 539 (01-39) or 六合彩 (01-49) lottery betting slip.
Read it and output SECTIONS. CRITICAL: each section is EXACTLY ONE bet. If the slip has N bets, output N sections. NEVER put two bets in one section, and NEVER split one bet across sections.
A 柱碰 (column bet) is a vertical grid of columns: its section contains ALL rows of that grid (top to bottom), and each column's stacked numbers are kept together.
A normal row bet is ONE horizontal line: its section has one row.
For EVERY token (number, separator x, digit, symbol) include its pixel bounding box [x1,y1,x2,y2].
Output ONLY JSON:
{"sections": [
  {"rows": [
     {"tokens": [{"text": "24", "bbox": [10,20,40,50]}, {"text": "x", "bbox": [45,20,60,50]}], "numbers": [["24"],["03"]], "multiplier": null, "layout_hint": "column_bet"}
  ], "shared_multiplier": null}
]}
Rules:
- A column bet's numbers are a list of lists: each inner list is ONE column (top to bottom), containing EVERY stacked number.
- A normal row may end with STACKED category digits: e.g. "02 30 33 39" then 3 written ABOVE 4 then x1 -> the multiplier token must be "34x1" (or "3/4x1"), NEVER just "3x1". If 2 is above 3 -> "23x1" (or "2/3x1"). Read the digit below/above and include BOTH.
- The handwritten "x" strokes between columns are separator tokens (text "x"), NOT multipliers.
- The bottom-right 碰法 (e.g. 4/3) and 倍率 (e.g. x0.1) are separate tokens; do not put them in any column.
- Keep leading zeros (03 not 3). 40-49 are valid.
- Do NOT expand combinations. Do NOT invent or miss numbers below the top of a column."""


FOCUSED_PROMPT = (
    "這是一張台灣 539 彩券右下角倍率區的放大圖。請只讀這個區域的文字："
    "可能是一個類別數字（2/3/4），也可能上下疊兩個數字（例如 3 上面、4 下面＝三四）加上倍率（x1）。"
    '回 JSON：{"full_text": "34x1", "category_digits": "34", "multiplier": "1"}，照實讀，不要猜。'
)


PLAY_MARK_PROMPT = """你是一個台灣 539／六合彩手寫牌單的忠實視覺轉錄器。

你的任務是辨識圖片中的所有手寫內容，尤其要正確辨識右側「玩法標記」內上下堆疊的數字。

請嚴格遵守以下規則：

1. 只根據圖片中實際可見的內容辨識，不可依常見投注格式自行猜測、補字或省略。
2. 主要號碼通常由左到右排列，請依照原圖順序完整讀取。
3. 右側玩法標記可能不是水平書寫，而是上下堆疊書寫。
4. 看到「3×1」、「2×1」或其他看似完整的水平內容時，不可立即停止辨識。
5. 必須仔細檢查「×」左側所有可見數字，包括：
   - 寫在上方的數字
   - 寫在下方的數字
   - 偏離主要文字基線的數字
   - 與其他筆畫靠得很近或部分相接的數字
6. 特別檢查每個玩法數字的正下方是否還有另一個數字。例如圖片若呈現：

   3
   4 ×1

   則必須辨識為上方數字 3、下方數字 4、倍率 1，不可只輸出 3×1。
7. 上下堆疊的玩法數字必須分開輸出，不可直接攤平成單一水平字串後遺漏下方數字。
8. 若看見疑似數字，但無法確認，必須保留在 uncertain_candidates，並將 uncertain 設為 true，不可直接刪除。
9. 若某個數字部分被遮住、與其他筆畫相接或位置異常，請在 uncertain_reason 說明。
10. 不可把倍率的數字誤認為主號碼，也不可把玩法數字混入主號碼。
11. 不可把「×」左側的上下數字合併成一個兩位數。例如上方 3、下方 4 是兩個玩法類別，不是數字 34。
12. 即使下方數字位於兩行文字之間，也必須視為可能的玩法標記內容並仔細檢查。
13. 請先辨識主號碼，再獨立檢查右側玩法區，最後才組合結果。
14. 請完整檢查圖片後再回答，不可看到第一個合理結果就提前結束。

請只輸出以下 JSON，不要加入解釋、Markdown 或其他文字：

{
  "raw_text": "",
  "main_numbers": [],
  "play_mark": {
    "upper_digits": [],
    "lower_digits": [],
    "other_visible_digits": [],
    "multiplier": null,
    "layout": "horizontal",
    "raw_play_text": "",
    "uncertain": false,
    "uncertain_candidates": [],
    "uncertain_reason": null
  },
  "overall_uncertain": false,
  "overall_uncertain_reason": null
}

欄位說明：

- raw_text：忠實保留整個區域中可見的手寫內容。
- main_numbers：只放左側主要號碼，依原圖由左到右排列。
- upper_digits：玩法區中位於上方的數字。
- lower_digits：玩法區中位於上方數字正下方的數字。
- other_visible_digits：玩法區內其他位置可見、但無法歸入上下位置的數字。
- multiplier：× 或 x 後面的倍率，只輸出倍率數值。
- layout：水平排列輸出 "horizontal"；上下堆疊輸出 "vertical_stack"；同時存在水平與上下排列輸出 "mixed"；無法判定輸出 "uncertain"。
- raw_play_text：忠實描述玩法區所有可見內容，必須保留下方數字。
- uncertain_candidates：放入疑似但無法完全確認的字元。
- 不確定時不得自行補字，但也不得把疑似存在的下方數字直接省略。

輸出前請再做一次強制檢查：

- 是否已檢查每個玩法數字的正下方？
- 是否因為看到「3×1」而漏掉 3 下方的 4？
- 是否完整保留所有上下堆疊數字？
- 是否把不確定內容標記出來，而不是直接省略？"""


COLUMN_COMBO_PROMPT = """你是一個台灣 539／六合彩手寫牌單的「柱碰（column bet）」忠實視覺轉錄器。

柱碰是垂直對齊的欄位：每一欄可能有多個號碼上下堆疊。你必須依空間位置重建欄位結構，不可展平成單行。

規則：
1. 號碼之間的 x、× 或 / 是欄分隔，不是倍率。
2. columns 是「欄的列表」：每個 inner list 是一欄（上到下），欄內所有堆疊號碼都要讀取，不可以漏掉下方號碼。
3. 右下角是碰法（collision，例如 2/3、4/3）和倍率（multiplier，例如 0.1、1），分開輸出。
4. 保留前導零（03 不是 3）。40-49 是合法號碼（六合彩）。
5. 不要發明號碼、不要展開組合。
6. 無法確認任何欄位時，uncertain 設為 true，並在 uncertain_reason 說明。

只輸出 JSON，不要加入其他文字：
{"columns": [], "collision": null, "multiplier": null, "uncertain": true, "uncertain_columns": [], "uncertain_reason": null}

若要以範例說明，請使用與任何真實牌單不同的合成號碼，例如：
{"columns": [["05"], ["12", "17"], ["23"], ["31"]], "collision": "2/3", "multiplier": "0.5", "uncertain": false, "uncertain_reason": null}
（此範例為合成資料：每欄一個號碼、第二欄有上下堆疊、右下角為二三碰 × 0.5。）

看不清楚、無法確認的欄位放 uncertain_columns，uncertain 設為 true，不得自行補字。"""


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


def prompt_sha256(prompt: str) -> str:
    """Return the SHA-256 of the exact UTF-8 prompt bytes."""
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()
