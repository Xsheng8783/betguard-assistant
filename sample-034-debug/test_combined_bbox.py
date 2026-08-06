"""Test: single model call that outputs sections WITH per-token bboxes."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import time
import uuid
import urllib.request
from json.decoder import JSONDecoder
from pathlib import Path

KEY = os.environ.get("DASHSCOPE_API_KEY", "")
URL = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1/chat/completions"
MODEL = "qwen3-vl-plus"
PROMPT_VERSION = "combined-bbox-v1"
PLAY_MARK_PROMPT_VERSION = "play-mark-v2"
COLUMN_COMBO_PROMPT_VERSION = "column-combo-v1"


class QwenClientError(RuntimeError):
    pass


class QwenAPIKeyMissing(QwenClientError):
    pass


class QwenSchemaError(QwenClientError):
    pass


class QwenTimeoutError(QwenClientError):
    pass


def extract_json(text: str) -> dict | None:
    """JSON extraction WITHOUT greedy regex: json.loads first, then
    JSONDecoder.raw_decode at the first '{'."""
    if not text:
        return None
    try:
        obj = json.loads(text)
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        pass
    dec = JSONDecoder()
    for i, ch in enumerate(text):
        if ch == "{":
            try:
                obj, _ = dec.raw_decode(text[i:])
                return obj if isinstance(obj, dict) else None
            except json.JSONDecodeError:
                continue
    return None


def _qwen_chat(
    b64: str,
    mime: str,
    prompt: str,
    *,
    max_tokens: int,
    image_sha256: str | None = None,
    crop_box: list[int] | None = None,
    scale: int | None = None,
    image_variant: str | None = None,
    prompt_version: str = PROMPT_VERSION,
    request_id: str | None = None,
    retries: int = 2,
) -> tuple[str, dict]:
    """Shared Qwen vision client: key guard, bounded retries with backoff,
    timeout classification, response schema validation, provenance meta."""
    if not KEY:
        raise QwenAPIKeyMissing(
            "DASHSCOPE_API_KEY 未設定；拒絕以空 Bearer token 呼叫。"
        )
    rid = request_id or uuid.uuid4().hex[:12]
    payload = {
        "model": MODEL,
        "messages": [{"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
            {"type": "text", "text": prompt},
        ]}],
        "max_tokens": max_tokens,
        "temperature": 0.0,
        "response_format": {"type": "json_object"},
    }
    meta = {
        "request_id": rid,
        "model": MODEL,
        "prompt_version": prompt_version,
        "image_sha256": image_sha256,
        "crop_box": crop_box,
        "scale": scale,
        "image_variant": image_variant,
        "retries": 0,
        "latency_s": None,
    }
    last_err: Exception | None = None
    for attempt in range(retries + 1):
        t0 = time.time()
        req = urllib.request.Request(
            URL, data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {KEY}"},
        )
        try:
            with urllib.request.urlopen(req, timeout=240) as r:
                data = json.loads(r.read())
            meta["latency_s"] = round(time.time() - t0, 2)
            meta["retries"] = attempt
            try:
                content = data["choices"][0]["message"]["content"]
            except (KeyError, IndexError, TypeError) as e:
                raise QwenSchemaError(
                    f"Qwen 回應缺少 choices/message/content（request_id={rid}）"
                ) from e
            if not isinstance(content, str) or not content.strip():
                raise QwenSchemaError(
                    f"Qwen 回傳空 content（request_id={rid}）；raw={str(data)[:300]}"
                )
            return content, meta
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")[:300]
            if e.code == 429:
                last_err = e  # rate limit -> retry with backoff
            elif 400 <= e.code < 500:
                raise QwenClientError(
                    f"Qwen 4xx（request_id={rid} code={e.code}）不重試：{body}"
                ) from e
            else:
                last_err = e  # 5xx -> retry
        except urllib.error.URLError as e:
            last_err = e
        except TimeoutError as e:
            raise QwenTimeoutError(f"Qwen timeout（request_id={rid}）") from e
        if attempt < retries:
            time.sleep(1.0 * (attempt + 1))
    raise QwenClientError(f"Qwen 重試耗盡（request_id={rid}）：{last_err}")

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


def _image_sha256(image_path: Path) -> str:
    return hashlib.sha256(image_path.read_bytes()).hexdigest()


def load_normalized(image_path: Path):
    """EXIF-transposed RGB image + PNG bytes of the ACTUAL image sent to the
    model (full page), so hash/orientation always match the request."""
    from io import BytesIO
    from PIL import Image, ImageOps

    with Image.open(image_path) as im:
        norm = ImageOps.exif_transpose(im.copy()).convert("RGB")
    buf = BytesIO()
    norm.save(buf, format="PNG")
    return norm, buf.getvalue()


def _gray_enhanced(img):
    from PIL import Image, ImageEnhance, ImageOps

    gray = ImageOps.grayscale(img).convert("RGB")
    return ImageEnhance.Contrast(gray).enhance(1.3)


def call(image_path: Path, *, meta: dict | None = None) -> str:
    norm, png_bytes = load_normalized(image_path)
    content, m = _qwen_chat(
        base64.b64encode(png_bytes).decode(),
        "image/png",
        PROMPT,
        max_tokens=8000,
        image_sha256=hashlib.sha256(png_bytes).hexdigest(),
        prompt_version=PROMPT_VERSION,
    )
    if meta is not None:
        meta.update(m)
    return content


def call_with_prompt(
    image_path: Path,
    prompt: str,
    *,
    max_tokens: int = 8000,
    meta: dict | None = None,
) -> str:
    """Single full-page call with a caller-supplied prompt (for prompt A/B)."""
    norm, png_bytes = load_normalized(image_path)
    content, m = _qwen_chat(
        base64.b64encode(png_bytes).decode(),
        "image/png",
        prompt,
        max_tokens=max_tokens,
        image_sha256=hashlib.sha256(png_bytes).hexdigest(),
        prompt_version=PROMPT_VERSION,
    )
    if meta is not None:
        meta.update(m)
    return content


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


def call_column_combo_crop(
    image_path: Path,
    bbox: list[int],
    *,
    pad_x: int = 70,
    pad_y: int = 55,
    scale: int = 3,
    save_path: Path | None = None,
    box: list[int] | None = None,
    meta: dict | None = None,
    variant: str = "original_3x",
    request_id: str | None = None,
) -> str:
    """Stage-2 column-combo read: crop the FULL column grid region (numbers +
    play mark), upscale 3x, PNG, ask the column_combo prompt."""
    from io import BytesIO
    from PIL import Image

    img, png_bytes = load_normalized(image_path)
    if box is not None:
        bx1, by1, bx2, by2 = [int(v) for v in box]
        crop_box = (
            max(0, bx1),
            max(0, by1),
            min(img.width, bx2),
            min(img.height, by2),
        )
    else:
        x1, y1, x2, y2 = bbox
        cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
        half_w = max((x2 - x1) // 2 + pad_x, 200)
        half_h = max((y2 - y1) // 2 + pad_y, 180)
        crop_box = (
            max(0, cx - half_w),
            max(0, cy - half_h),
            min(img.width, cx + half_w),
            min(img.height, cy + half_h),
        )
    base = _gray_enhanced(img) if variant == "gray_enhanced_3x" else img
    crop = base.crop(crop_box)
    if scale > 1:
        crop = crop.resize((crop.width * scale, crop.height * scale), Image.LANCZOS)
    if save_path is not None:
        Path(save_path).parent.mkdir(parents=True, exist_ok=True)
        crop.save(save_path, format="PNG")
    buf = BytesIO()
    crop.save(buf, format="PNG")
    crop_bytes = buf.getvalue()
    b64 = base64.b64encode(crop_bytes).decode()
    content, m = _qwen_chat(
        b64, "image/png", COLUMN_COMBO_PROMPT,
        max_tokens=600,
        image_sha256=hashlib.sha256(crop_bytes).hexdigest(),
        crop_box=list(crop_box),
        scale=scale,
        image_variant=variant,
        prompt_version=COLUMN_COMBO_PROMPT_VERSION,
        request_id=request_id,
    )
    if meta is not None:
        meta.update(m)
    return content


def call_play_mark_crop(
    image_path: Path,
    bbox: list[int],
    *,
    pad_x: int = 60,
    pad_y: int = 45,
    scale: int = 3,
    save_path: Path | None = None,
    box: list[int] | None = None,
    meta: dict | None = None,
    variant: str = "original_3x",
    request_id: str | None = None,
) -> str:
    """Stage-2 read: crop the right-side play zone with FULL vertical extent
    (stacked digits often extend above/below the main number row), upscale
    3x and send as PNG (no lossy JPEG) with the structured play_mark prompt."""
    from io import BytesIO
    from PIL import Image

    img, png_bytes = load_normalized(image_path)
    if box is not None:
        bx1, by1, bx2, by2 = [int(v) for v in box]
        crop_box = (
            max(0, bx1),
            max(0, by1),
            min(img.width, bx2),
            min(img.height, by2),
        )
    else:
        x1, y1, x2, y2 = bbox
        cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
        half_w = max((x2 - x1) // 2 + pad_x, 170)
        half_h = max((y2 - y1) // 2 + pad_y, 170)
        crop_box = (
            max(0, cx - half_w),
            max(0, cy - half_h),
            min(img.width, cx + half_w),
            min(img.height, cy + half_h),
        )
    base = _gray_enhanced(img) if variant == "gray_enhanced_3x" else img
    crop = base.crop(crop_box)
    if scale > 1:
        crop = crop.resize((crop.width * scale, crop.height * scale), Image.LANCZOS)
    if save_path is not None:
        Path(save_path).parent.mkdir(parents=True, exist_ok=True)
        crop.save(save_path, format="PNG")
    buf = BytesIO()
    crop.save(buf, format="PNG")
    crop_bytes = buf.getvalue()
    b64 = base64.b64encode(crop_bytes).decode()
    content, m = _qwen_chat(
        b64, "image/png", PLAY_MARK_PROMPT,
        max_tokens=400,
        image_sha256=hashlib.sha256(crop_bytes).hexdigest(),
        crop_box=list(crop_box),
        scale=scale,
        image_variant=variant,
        prompt_version=PLAY_MARK_PROMPT_VERSION,
        request_id=request_id,
    )
    if meta is not None:
        meta.update(m)
    return content


def call_focused_crop(image_path: Path, bbox: list[int], pad: int = 70, min_w: int = 280, min_h: int = 190) -> str:
    """Zoomed re-read of one token region (e.g. a suspiciously tall category
    token) to recover stacked digits the full-page pass missed."""
    from io import BytesIO
    from PIL import Image

    img = Image.open(image_path)
    x1, y1, x2, y2 = bbox
    cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
    half_w = max((x2 - x1) // 2 + pad, min_w // 2)
    half_h = max((y2 - y1) // 2 + pad, min_h // 2)
    box = (
        max(0, cx - half_w),
        max(0, cy - half_h),
        min(img.width, cx + half_w),
        min(img.height, cy + half_h),
    )
    buf = BytesIO()
    img.crop(box).save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode()
    payload = {
        "model": "qwen3-vl-plus",
        "messages": [{"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
            {"type": "text", "text": FOCUSED_PROMPT},
        ]}],
        "max_tokens": 300,
        "temperature": 0.0,
        "response_format": {"type": "json_object"},
    }
    req = urllib.request.Request(
        URL, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {KEY}"},
    )
    with urllib.request.urlopen(req, timeout=240) as r:
        data = json.loads(r.read())
    return data["choices"][0]["message"]["content"]


def main() -> None:
    img = Path(os.environ["BETGUARD_DATASET"]) / "raw" / "sample-011.jpg"
    content = call(img)
    (Path(__file__).resolve().parent / "ab_results" / "prelim" / "sample-011-combined.json").write_text(
        content, encoding="utf-8")
    print(content[:1500])


if __name__ == "__main__":
    main()
