# betguard-assistant 輔助填入完整 SOP

> **版本**：v0.4.4-zhupeng-live-test-success-with-cli-tests
> **最後更新**：2026-07-07
> **適用範圍**：539 / 天天樂 / 連碰 / 柱碰 真站輔助填入 + 本地 mock 流程

---

## 前置環境

每次新 session 開始前：

```powershell
cd C:\Users\USER\Documents\Codex\2026-06-30\init-python-betguard-assistant-539-1\betguard-assistant
python -c "import betguard; print(betguard.__file__)"
python -X utf8 -m pytest -q
git status --short
```

---

## 一、539 一般 assisted-fill SOP

適用：539 連碰，逐筆 fill，每筆人工確認。

### 1.1 文字解析 → 審核

```powershell
python -X utf8 -m betguard.webfill.cli --new-batch-from-file input_539_clean.txt --queue queue_539.json --pretty
python -X utf8 -m betguard.webfill.cli --review-package --queue queue_539.json --out-dir review_539 --pretty
start review_539\review.html
```

### 1.2 接受 valid → 建立 approved_fill_queue

```powershell
python -X utf8 -m betguard.webfill.cli --batch-review-accept-valid --queue queue_539.json --pretty
```

### 1.3 真站逐筆 fill

```powershell
python -X utf8 -m betguard.webfill.cli `
  --real-site-assisted-fill `
  --queue queue_539.json `
  --profile site_profile_539.json `
  --url https://www.gts362.com `
  --i-understand-real-site-fill-risk `
  --pretty
```

流程：
1. 瀏覽器開啟 → 手動登入
2. 導航到二三四星頁面 → 按 Enter
3. 系統自動選號 + 填金額
4. 人工檢查畫面 → 手動送出/確認
5. 執行 DONE 標記

### 1.4 DONE 標記

```powershell
python -X utf8 -m betguard.webfill.cli `
  --batch-human-confirm-current-done `
  --i-confirm-current-item-is-complete `
  --queue queue_539.json `
  --pretty
```

---

## 二、天天樂一般 assisted-fill SOP

與 539 流程相同，使用天天樂專屬 queue 和 profile。

```powershell
python -X utf8 -m betguard.webfill.cli --new-batch-from-file input_tiantianle_clean.txt --queue queue_tiantianle.json --pretty
python -X utf8 -m betguard.webfill.cli --review-package --queue queue_tiantianle.json --out-dir review_tiantianle --pretty
start review_tiantianle\review.html
python -X utf8 -m betguard.webfill.cli --batch-review-accept-valid --queue queue_tiantianle.json --pretty
```

Fill 步驟同 539，使用天天樂 profile。

---

## 三、ZhuPeng / 柱碰 session-fill SOP

適用：柱碰多筆批次，開一次瀏覽器，逐筆填，每筆 DONE gate。

### 3.1 建立 queue

```powershell
python -X utf8 -m betguard.webfill.cli --new-batch-from-file input_zhu.txt --queue queue_zhu.json --pretty
python -X utf8 -m betguard.webfill.cli --review-package --queue queue_zhu.json --out-dir review_zhu --pretty
start review_zhu\review.html
python -X utf8 -m betguard.webfill.cli --batch-review-accept-valid --queue queue_zhu.json --pretty
```

### 3.2 Preflight 檢查（local-only，不開瀏覽器）

```powershell
python -X utf8 -m betguard.webfill.cli --zhu-peng-preflight --queue queue_zhu.json --pretty
```

必須看到 `Status: READY_FOR_HUMAN_REVIEW` 才能進行下一步。

### 3.3 Session fill（開一次瀏覽器，處理多筆）

```powershell
python -X utf8 -m betguard.webfill.cli `
  --zhu-peng-session-fill `
  --queue queue_zhu.json `
  --url https://www.gts362.com `
  --i-understand-real-site-fill-risk `
  --pretty
```

流程：
1. 瀏覽器開啟 → 手動登入
2. 導航到柱碰頁面 → 按 Enter
3. 系統自動選柱、選號、填金額
4. 顯示「已填入目前這筆。請人工檢查網站畫面，人工送出/確認後，輸入 DONE 才會解鎖下一筆。」
5. 人工檢查畫面 → 手動送出/確認
6. 在終端機輸入 `DONE`（大小寫不拘）
7. 自動解鎖下一筆 → 重複步驟 3-7
8. 全部完成後瀏覽器自動關閉

### 3.4 重要：session fill 不需要手動執行 DONE 標記

Session runner 內建 DONE gate。輸入 `DONE` 後自動標記 current → DONE、解鎖下一筆。不需要另外執行 `--batch-human-confirm-current-done`。

---

## 四、preflight / readiness 檢查順序

真站 fill 前，必須依序通過以下檢查：

### 4.1 連碰 preflight

```powershell
python -X utf8 -m betguard.webfill.cli --real-site-fill-preflight --queue <queue> --profile <profile> --pretty
```

### 4.2 連碰 readiness

```powershell
python -X utf8 -m betguard.webfill.cli --real-site-fill-readiness --queue <queue> --profile <profile> --pretty
```

### 4.3 柱碰 preflight

```powershell
python -X utf8 -m betguard.webfill.cli --zhu-peng-preflight --queue <queue> --pretty
```

### 檢查清單

| 檢查項 | 連碰 | 柱碰 |
|--------|------|------|
| approved_fill_queue 存在 | `--real-site-fill-readiness` | `--zhu-peng-preflight` |
| accepted_by_human = true | readiness | preflight |
| Needs Review / Invalid / Watchlist 已排除 | readiness | preflight |
| 金額欄位完整 | readiness | preflight |
| #GroupSet_Value 已排除 | readiness | forbidden_selectors |
| 危險按鈕已偵測但未點擊 | readiness | danger words guard |

---

## 五、DONE gate 使用規則

### 5.1 什麼情況可以輸入 DONE

- ✅ 已在瀏覽器中人工檢查號碼和金額完全正確
- ✅ 已手動點擊送出/確認按鈕（網站端）
- ✅ 終端機顯示「輸入 DONE 才會解鎖下一筆」
- ✅ 確定目前這筆已完成，可以安全前進到下一筆

### 5.2 什麼情況不能輸入 DONE

- ❌ 尚未人工檢查畫面
- ❌ 號碼或金額與 queue 內容不符
- ❌ 尚未手動送出/確認
- ❌ 網站顯示錯誤或異常
- ❌ 任何不確定的情況

### 5.3 輸入了非 DONE 的內容會怎樣

Session runner 會：
1. 顯示「未確認（需輸入 DONE）。保持 WAITING_FOR_HUMAN_CONFIRM，session 結束。」
2. 目前這筆保持在 `WAITING_FOR_HUMAN_CONFIRM` 狀態
3. Session 終止，瀏覽器關閉
4. 可以之後用 `--batch-human-confirm-current-done` 手動完成這筆

---

## 六、出錯時如何安全停止

### 6.1 Session fill 中途退出

在 DONE 提示時輸入任何非 `DONE` 文字（例如 `q`、`stop`、直接 Enter），session 會安全終止。

### 6.2 瀏覽器異常

若瀏覽器 crash 或頁面失效：
- Session runner 會偵測 B03 frame 失效並自動 BLOCKED
- 不會自動重試或自動填入
- Queue 中未完成的 item 保持 PENDING

### 6.3 手動重置 queue 狀態

若 queue 卡在異常狀態，檢查目前狀態：

```powershell
python -X utf8 -m betguard.webfill.cli --review-console --queue <queue> --pretty
```

手動標記 DONE（僅限 WAITING_FOR_HUMAN_CONFIRM 狀態）：

```powershell
python -X utf8 -m betguard.webfill.cli `
  --batch-human-confirm-current-done `
  --i-confirm-current-item-is-complete `
  --queue <queue> `
  --pretty
```

---

## 七、哪些本地 artifacts 不准 commit

以下檔案/目錄為本地工作產物，**絕不 commit 到 Git**：

| 類別 | 範例 | 原因 |
|------|------|------|
| Queue 狀態 | `queue_*.json` | 含真實下注資料 |
| Review 輸出 | `review_*/` | 含真實下注資料 |
| 輸入文字 | `input_*.txt` | 含真實下注資料 |
| 探索腳本 | `discover_*.py`, `find_*.py` | 臨時腳本，非正式程式 |
| 真站測試 | `fast_fill.py`, `open_site.py`, `zhu_flow.py`, `zhu_back.py`, `zhupeng_typist.py` | 一次性真站測試 |
| Dump 檔案 | `*_dump.json`, `viewmodel_dump.json` | DOM 探查結果 |
| 圖片 | `*.png` | 非程式資產 |
| venv | `.venv/`, `venv/` | Python 環境 |

**只 commit**：`src/`、`tests/`、`docs/`、`pyproject.toml`、`AGENTS.md`、`README.md`。

---

## 八、Git tag / 穩定點紀錄

### 現有 tags

```
v0.4.4-zhupeng-live-test-success-with-cli-tests  ← 目前最新
```

### Tag 命名規則

- 功能完成 → `v<version>-<feature>-<milestone>`
- 例：`v0.4.4-zhupeng-live-test-success-with-cli-tests`

### 打 tag 前檢查

```powershell
python -X utf8 -m pytest -q          # 必須全過
git status --short                    # 無意外修改
git diff --name-only                  # 確認只改預期檔案
```

### 打 tag

```powershell
git tag -a v<version>-<feature>-<milestone> -m "<描述>"
git push origin v<version>-<feature>-<milestone>
```

---

## 九、安全不變量

每一筆操作都必須滿足：

| 不變量 | 連碰 | 柱碰 |
|--------|------|------|
| `auto_submit = false` | ✅ | ✅ |
| `auto_confirm = false` | ✅ | ✅ |
| `auto_next = false` | ✅ | ✅ |
| 不點擊送出按鈕 | ✅ | ✅ |
| 不點擊確認按鈕 | ✅ | ✅ |
| Needs Review / Invalid / Watchlist 不進 fill | ✅ | ✅ |
| #GroupSet_Value 不當金額欄 | ✅ | ✅ |
| Danger words 集中在 safety.py | ✅ | ✅ |
| 每筆等待人工 DONE | — | ✅ session runner |
| Queue 記錄 audit trail | ✅ | ✅ |

---

## 十、常用指令速查

| 操作 | 指令 |
|------|------|
| 建立批次 | `--new-batch-from-file input.txt --queue q.json --pretty` |
| 產生 review | `--review-package --queue q.json --out-dir review --pretty` |
| 接受 valid | `--batch-review-accept-valid --queue q.json --pretty` |
| 柱碰 preflight | `--zhu-peng-preflight --queue q.json --pretty` |
| 連碰 preflight | `--real-site-fill-preflight --queue q.json --profile p.json --pretty` |
| 連碰 readiness | `--real-site-fill-readiness --queue q.json --profile p.json --pretty` |
| 連碰逐筆 fill | `--real-site-assisted-fill --queue q.json --profile p.json --url <url> --i-understand-real-site-fill-risk` |
| 柱碰 session fill | `--zhu-peng-session-fill --queue q.json --url <url> --i-understand-real-site-fill-risk` |
| DONE 標記 | `--batch-human-confirm-current-done --i-confirm-current-item-is-complete --queue q.json` |
| 查看狀態 | `--review-console --queue q.json --pretty` |
| Mock 流程 | `--batch-mock-next --queue q.json --pretty` |
| 匯出 audit | `--batch-audit-export --queue q.json --out audit.json --pretty` |

---

> **⚠️ betguard-assistant 是安全輔助稽核工具，不是自動下注系統。所有送出、確認操作必須由人類執行。**
