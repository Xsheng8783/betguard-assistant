# betguard-assistant

## 專案定位

betguard-assistant 是一個**本地安全審核 / mock assisted fill 工具**，用於 539 / 多遊戲下注文字的解析與人工審核。

- **不是自動下注工具**
- **不操作真網站**
- **不 submit**
- **不 click**（不點送出 / 確認 / 清除 / 刪除等任何危險按鈕）

所有解析、審核、mock 填單流程都在本地執行，每一筆都需要人工確認。

## 安全限制（Safety Guarantees）

- `real_site_operation=false`
- `auto_submit=false`
- `danger_buttons_clicked=[]`
- No submit（不送出注單）
- No click（不點擊送出 / 確認 / 清除 / 刪除 / 危險按鈕）
- No live-site operation（完全不接觸真實網站）
- No auto accept valid（valid 項目不會自動接受，需人工執行 accept）
- No auto mock next（不會自動處理下一筆，需人工逐筆執行）
- Every item requires human confirmation（每一筆都停在 `WAITING_FOR_HUMAN_CONFIRM`）
- Invalid fragments 不會消失，全部保留在 audit 供人工檢查
- Valid items 不會消失

## 基本流程

1. 把 LINE / 文字內容貼到 `input.txt`
2. 產生 `queue_state.json`（新批次）
3. 產生 `review_out/review.html`（審核報告）
4. 人工打開 review.html，查看 **Valid Candidates** 與 **Needs Review / Invalid** 清單
5. 確認無誤後，人工執行 accept valid
6. 人工逐筆執行 batch-mock-next（一次只處理一筆）
7. 匯出 `audit_final.json` 留存稽核紀錄

## 指令區（PowerShell）

先跑測試確認環境正常：

```powershell
python -m pytest
```

清除上一批的輸出：

```powershell
Remove-Item queue_state.json -ErrorAction SilentlyContinue
Remove-Item review_out -Recurse -Force -ErrorAction SilentlyContinue
```

建立新批次並產生審核報告：

```powershell
python -m betguard.webfill.cli --new-batch-from-file input.txt --queue queue_state.json --pretty --overwrite
python -m betguard.webfill.cli --review-package --queue queue_state.json --out-dir review_out --pretty
start .\review_out\review.html
```

人工審核後，接受 valid 項目並逐筆執行 mock（每執行一次只處理一筆）：

```powershell
python -m betguard.webfill.cli --batch-review-accept-valid --queue queue_state.json --pretty
python -m betguard.webfill.cli --batch-mock-next --queue queue_state.json --pretty
```

匯出最終 audit：

```powershell
python -m betguard.webfill.cli --batch-audit-export --queue queue_state.json --out review_out/audit_final.json --pretty
```

## Needs Review 說明

以下格式**不會自動處理**，一律標記為 Needs Review / Invalid，保留給人工判斷：

- 缺金額（例如只有號碼沒有金額）
- Customer-specific 簡碼：`1000` / `600` / `400` 這類客戶專屬尾碼
- 含「改」的項目
- 含「臂」的項目
- 含「各10」的項目
- 含「寫」的項目
- 含「港」前綴的項目
- 號碼超出範圍（539 有效範圍 1–39）
- 小數 hyphen amount（例如 `02-03-05-16-20 -0.25`）
- 重複號碼 / 混合錯誤格式

這些項目會完整保留在 review 報告與 audit 的 invalid fragments 中，不會被丟棄。

## 版本資訊

- Current stable tag: `v0.1.0-safe-local-review`
- Commit: `9c3c627 Accept confirmed integer hyphen amounts`
- Release Check: 567 passed
- Safety flags: `real_site_operation=false`, `auto_submit=false`, `danger_buttons_clicked=[]`

## 安裝與其他工具

安裝（editable mode）：

```powershell
pip install -e ".[dev]"
```

離線 demo（不需 input.txt，產生範例輸出到 demo_out/）：

```powershell
python -m betguard.webfill.cli --demo-e2e --out-dir demo_out --pretty
```

Streamlit 本地審核 UI（僅供審核，不會 submit）：

```powershell
streamlit run src/betguard/ui_review.py
```

## 手寫牌單 AI 辨識

圖片工作台支援上傳 PNG / JPEG / WebP，辨識範圍刻意限制為數字、`x` 與
`二／三／四` 等下注文字。付費 Vision 會先讀整張圖片，結果一律停在
`PENDING_HUMAN_CONFIRMATION`；每行經人工修正並勾選後，才可帶回文字 Review。

PowerShell 可用隱藏輸入設定當次工作階段的 API Key，不會寫入專案：

```powershell
$secret = Read-Host "OPENAI_API_KEY" -AsSecureString
$env:OPENAI_API_KEY = [System.Net.NetworkCredential]::new('', $secret).Password
Remove-Variable secret
$env:BETGUARD_VISION_MODEL = "gpt-5"
$env:PYTHONPATH = (Resolve-Path .\src).Path
python -m betguard.webui.app
```

開啟 `http://127.0.0.1:8765/assist-panel`，切換到「手寫圖片辨識」。如果
API Key 或模型未設定，畫面會明確顯示本機 OCR fallback；沒有可用的本機 OCR
也不會假裝成功。圖片品質 Gate 未通過時，不能把 OCR 文字帶回 Review。

安全邊界固定為 `auto_submit=false`、`auto_confirm=false`，OCR 不會直接進入
webfill，也不會操作任何下注網站。

## Documentation

- [End-to-End Demo](docs/demo_e2e.md)
- [Architecture](docs/architecture.md)
- [CLI Reference](docs/cli_reference.md)
