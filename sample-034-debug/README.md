# sample-034 OCR + ROI review flow (debug branch)

This folder snapshots the working code for the sample-034 debugging session:
the Qwen first-pass prompt with per-token bboxes, the three-stage ROI flow
(full page -> play-mark / column-combo ROI -> merge / needs_review), and the
local M1-A review tool with the "adopt ROI reading" workflow.

## Layout

- `prelabel_geo.py` - geometry + three-stage pipeline entry (`process_parsed`)
- `test_combined_bbox.py` - combined prompt, `call_play_mark_crop`,
  `call_column_combo_crop`, `call_focused_crop`
- `test_prompt_zh.py` - Chinese full-page prompt experiment
- `fixtures/sample-034-combined.json` - stored first-pass tokens + bboxes
  (text/JSON only; no bet-slip images are committed)
- `review-tool/` - M1-A manual review tool (`server.py`, `app.js`,
  `roi_logic.js`, `index.html`, `style.css`, `test_roi_apply.mjs`)

## Required environment

- `BETGUARD_DATASET` - absolute path of the dataset root
  (e.g. `C:\BetguardOCRDataset`); the tool never hard-codes local paths.
- `DASHSCOPE_API_KEY` - only needed for live model calls; never committed.

## Run

```powershell
$env:BETGUARD_DATASET = "C:\BetguardOCRDataset"
python prelabel_geo.py sample-034            # full AI run (needs DASHSCOPE_API_KEY)
python prelabel_geo.py --reprocess sample-034  # rebuild draft from stored first pass

cd review-tool
python server.py                              # M1-A review tool on http://127.0.0.1:8765
node test_roi_apply.mjs                       # ROI adoption logic tests
```

## Security notes

- No API keys, tokens, credentials, or real bet-slip images are committed.
- The fixture contains only OCR tokens and bounding boxes.
- Runtime paths come from `BETGUARD_DATASET`; `*.bak*`, `*.png`, `*.jpg`
  under this folder are git-ignored.
