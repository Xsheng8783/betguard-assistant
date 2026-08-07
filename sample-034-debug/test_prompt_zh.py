"""Test a Chinese full-page combined prompt (with bbox tokens) on sample-009.

Goal: verify whether the model reads stacked category digits (3 over 4 ->
34x1) in the FIRST full-page pass, instead of needing the focused re-read.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import test_combined_bbox as tcb
from betguard.vision.qwen_prompts import PROMPT_ZH


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
