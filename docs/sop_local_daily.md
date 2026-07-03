# 本地日常使用 SOP（純本地，不碰真網站）

適用範圍：日常「貼單 → 審核 → mock 帶入 → 報告」流程。全程只操作本地檔案與本地 mock 頁，
不會連線真網站、不會 click、不會 submit。

> **重要：請一律在 `C:\Users\User\betguard-assistant` 操作。**
> 這台電腦上有多份 repo 副本，跑錯資料夾會出現「檔案找不到」或版本不一致的問題。
> 開始前先 `git pull` 確認是最新版本。

## 1. input.txt 放哪裡

放在 repo 根目錄 `C:\Users\User\betguard-assistant\input.txt`，UTF-8 編碼。
內容是貼上的下注訊息原文（LINE 對話可直接貼，前處理會自動忽略時間、人名、已讀等 metadata）。

## 2. 建立批次（queue_state.json）

```powershell
cd C:\Users\User\betguard-assistant
python -m betguard.webfill.cli --new-batch-from-file input.txt --queue queue_state.json --pretty
```

若 `queue_state.json` 已存在需要重建，加上 `--overwrite`。

## 3. 產生 review.html / audit.json / summary.txt

```powershell
python -m betguard.webfill.cli --review-package --queue queue_state.json --out-dir review_out --pretty
```

會在 `review_out\` 產生 `review.html`、`audit.json`、`summary.txt`。

## 4. 開 review.html

直接在檔案總管點兩下 `review_out\review.html`，或：

```powershell
start review_out\review.html
```

重點看三區：

- **Valid Candidates**：可接受的項目
- **Needs Review / Invalid**：需人工處理的項目（含「缺金額，需人工補」）
- **Ignored Metadata**：被自動忽略的聊天雜訊

## 5. 接受 valid candidates（人工看過 review.html 之後才做）

```powershell
python -m betguard.webfill.cli --batch-review-accept-valid --queue queue_state.json --pretty
```

- 只在 queue 狀態是 `NEEDS_REVIEW` 時有效。
- 會建立 `approved_fill_queue` 並自動跑第一筆 mock，停在 `WAITING_FOR_HUMAN_CONFIRM`。
- 不想接受就改跑 `--batch-review-reject`。

## 6. fill-preview（唯讀預覽，看接下來會帶入什麼）

```powershell
python -m betguard.webfill.cli --fill-preview --queue queue_state.json --pretty
```

只從 `approved_fill_queue` 讀取。沒先做第 5 步會顯示 `Run accept-valid first.`，這是正常的安全設計。

## 7. 一筆一筆 batch-mock-next

每人工確認完一筆（mock 畫面），就跑一次：

```powershell
python -m betguard.webfill.cli --batch-mock-next --queue queue_state.json --pretty
```

重複直到 `Status: COMPLETED`。每次只會前進一筆，永遠停在 `WAITING_FOR_HUMAN_CONFIRM` 等人工確認。

## 8. 完成後產生 HTML report 與 audit export

```powershell
python -m betguard.webfill.cli --review-report-html --queue queue_state.json --out review_final.html --pretty
python -m betguard.webfill.cli --batch-audit-export --queue queue_state.json --out audit_final.json --pretty
```

## 9. 安全狀態確認

每個報告結尾都要看到以下內容才算正常：

```
real_site_operation=false
auto_submit=false
danger_buttons_clicked=[]
human_required_each_item=true
```

任何一項不是這個值，立刻停手回報。

## 10. 哪些指令不要亂跑

| 指令 | 原因 |
|---|---|
| `--real-site-assisted-fill` + `--i-understand-real-site-fill-risk` | 會在真網站帶入號碼/金額，只能在明確授權的階段用 |
| `--assist-fill` + `--i-understand-human-final-confirm` | 同上（舊版真網站流程） |
| `--discover-selectors --url <真網站>` | 會開瀏覽器連真網站，只在「開盤」授權後照《開盤後只讀掃描 SOP》執行 |
| `--auto-confirm-mock` | 已被程式硬性拒絕，跑了也只會看到 REFUSED |
| 對同一個 queue 重複 `--batch-review-accept-valid` | 狀態已不是 NEEDS_REVIEW，會報錯 |

## 11. 常見錯誤與處理

| 錯誤訊息 / 現象 | 原因 | 處理 |
|---|---|---|
| `Run accept-valid first.` | 沒做第 5 步就跑 fill-preview | 先跑第 5 步 |
| `batch review accept is only allowed when status is NEEDS_REVIEW` | queue 已接受過或是 BLOCKED | 用 `--review-console --queue queue_state.json --pretty` 確認目前狀態 |
| `no item is waiting for human confirmation` | 還沒有筆停在待確認就跑 mock-next | 先確認 queue 狀態 |
| `queue is not ready for current mock fill` | queue 是 BLOCKED / NEEDS_REVIEW | 回 review 階段處理 invalid 項目 |
| 狀態 `BATCH_BLOCKED`、0 valid | 整批都是 Needs Review（如缺金額、未支援格式） | 修 input.txt 內容補金額後重建批次 |
| 終端機中文顯示亂碼 | PowerShell cp950 主控台編碼 | 只是顯示問題；檔案輸出（html/json）一律 UTF-8 正確，以檔案為準 |
| 檔案找不到 | 跑錯資料夾 | 確認在 `C:\Users\User\betguard-assistant` |
