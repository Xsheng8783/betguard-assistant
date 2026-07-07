# 本地網站工作台 (v1)

> **範圍**：v1 是建立審核批次的便利工具。**它不會接觸真網站、不會自動送出、也不會自動把 Needs Review 升級到 approved_fill_queue。**

---

## 啟動

```powershell
python -X utf8 -m betguard.webui.app --port 8765
```

啟動後打開瀏覽器：

```
http://127.0.0.1:8765/
```

預設綁 `127.0.0.1`（loopback only）。若要對 LAN 開放（**不建議**）：

```powershell
python -X utf8 -m betguard.webui.app --host 0.0.0.0 --port 8765
# WARNING: 啟動時會印 warning, 因為這條路徑對外暴露了工作台
```

按 `Ctrl-C` 停止。

---

## 路由

| 路由 | 方法 | 說明 |
|------|------|------|
| `/` | GET | 儀表板（顯示 version / git commit / 安全提醒 / 功能入口） |
| `/workbench` | GET | 牌單輸入表單（textarea + 遊戲類型下拉 + 按鈕） |
| `/workbench` | POST | 收 input → 建 batch → 顯示結果頁 |
| `/latest-review` | GET | 開 `runs/` 下最新的 `review_*.html`，若無則顯示提示 |
| `/sop` | GET | 顯示 `docs/sop_assisted_fill_complete.md` |
| `/cli` | GET | 顯示 `docs/cli_reference.md` |
| `/runs/...` | GET | 讀 `runs/` 下的 input / queue / review.html |

---

## 完整使用流程

1. 啟動 server: `python -X utf8 -m betguard.webui.app`
2. 開 `http://127.0.0.1:8765/`
3. 點「貼上牌單建立審核」
4. 把 LINE / 聊天室的牌單文字貼到 textarea
5. 選遊戲類型（**自動判斷** / 539 / 天天樂 / ZhuPeng）
6. 點「建立審核批次」
7. 頁面顯示 valid / needs review / invalid 數量 + 檔案位置
8. 點「開啟 review.html」看 review console

---

## 產物位置

每次建批次會在 `runs/YYYY-MM-DD/` 下產出 3 個檔：

```
runs/
└── 2026-07-07/
    ├── input_143012.txt         ← 原始牌單文字
    ├── queue_143012.json        ← build_batch_mock_queue 結果 (status=NEEDS_REVIEW)
    └── review_143012.html       ← review console HTML
```

**這 3 個檔不會被 commit**（`.gitignore` 已加 `runs/`）。

---

## 安全保證（已驗證，14 個 test 涵蓋）

| 保證 | 怎麼保證 |
|------|---------|
| 空白輸入會被拒絕 | 回 400 + 顯示錯誤頁，不建任何檔 |
| 只呼叫 `--new-batch-from-file` 跟 `--review-report-html` | `_run_cli` 的 `FORBIDDEN_FLAGS` 集檢查 |
| 不會建立 `approved_fill_queue` | queue.json 永遠保持 `NEEDS_REVIEW` |
| 不會升 `WAITING_FOR_HUMAN_CONFIRM` | 測試 6 驗證 |
| 不會呼叫 `real-site-assisted-fill` | FORBIDDEN_FLAGS 包含該 flag |
| 不會跑 browser | 不 import playwright / selenium |
| 不會 commit runs/ 產物 | `.gitignore` 含 `runs/` |

---

## 常見問題

### Q: 啟動時出現 `OSError: [Errno 98] Address already in use`?
A: 換 port：`--port 8766`

### Q: 啟動時出現 `ModuleNotFoundError: No module named 'betguard'`?
A: 確認有設 `PYTHONPATH=src` 或用 venv 內的 python 直跑。

### Q: 想看 queue.json 內容?
A: 直接開 `runs/YYYY-MM-DD/queue_HHMMSS.json`，或從首頁點「開啟最新 review.html」用 review console 看。

### Q: 怎麼刪舊的 runs/ 產物?
A: 直接 `Remove-Item -Recurse runs/2026-07-07` (PowerShell) 或 `rm -rf runs/2026-07-07` (bash)。v1 沒做自動清理。

### Q: 為什麼 queue 還是 NEEDS_REVIEW, 不能直接 accept?
A: 設計如此。Needs Review / Invalid 必須由人工 review 後手動決定是否 accept-valid。Web 工作台**只**幫你建批次，不幫你決定 accept。

### Q: 怎麼手動 accept valid (不在工作台範圍)?
A: 關掉 webui，用 CLI：
```powershell
python -X utf8 -m betguard.webfill.cli `
  --batch-review-accept-valid `
  --queue runs/2026-07-07/queue_143012.json `
  --pretty
```

---

## 已知限制 (v1)

- HTML 字串寫死在 Python code，沒 template engine（之後 v2 再用 jinja2）
- 沒 CSRF / auth（純 local-only，綁 127.0.0.1）
- 沒自動清理舊 runs/（磁碟會累積）
- 沒 `runs/_cleanup.py`（v2 再做）
- 不支援「從這次建的批次直接進 mock」（要走 CLI）

---

## 跟既有功能的對應

| 功能 | 對應 |
|------|------|
| 貼牌單 → 建批次 | `betguard.webfill.cli --new-batch-from-file` |
| 產 review.html | `betguard.webfill.cli --review-report-html` |
| 看 review.html | 既有 `review_console.py` 渲染的 HTML |
| 人工 accept valid | `betguard.webfill.cli --batch-review-accept-valid`（**不在工作台範圍**） |
| 進真站 fill | `betguard.webfill.cli --real-site-assisted-fill`（**絕對不會被工作台呼叫**） |
