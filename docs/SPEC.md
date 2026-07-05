# betguard-assistant 系統規格

> v0.4.0 | 2026-07-05

## 概述

betguard-assistant 是安全人工輔助投注系統。解析下注文字 → 人工審核 → 真站輔助填入 → 人工送出。**永遠不自動送出、不自動確認。**

## 架構

```
輸入文字 → parser → review.html → accept-valid
  → approved_fill_queue → readiness → preflight
  → real-site fill → 人工送出 → human-confirm-done
```

## 核心安全規則

1. 不自動送出 (auto_submit = false)
2. 不自動確認 (auto_confirm = false)
3. 不自動下一筆 (auto_next = false)
4. #GroupSet_Value 永不當做金額欄
5. danger button 只偵測不點擊
6. Needs Review / Invalid / Watchlist 不進 fill flow
7. 只能用 approved_fill_queue

## 模組

| 模組 | 功能 |
|:---|:---|
| `parser` | 文字解析 |
| `review` | 人工審核 web UI |
| `batch_queue` | Queue 管理 (READY → WAITING → DONE → COMPLETED) |
| `real_site_fill_preflight` | 填入前安全檢查 |
| `real_site_fill_plan` | 產生執行計劃 (selector + action) |
| `real_site_assisted_fill` | Playwright 真站操作 |
| `fill_mapping` | Selector 對應 (B03 frame, PengBet.Value) |
| `safety` | Danger word 檢查 |
| `selector_discovery` | 頁面 DOM 掃描產生 profile |

## 關鍵演算法

### Frame Resolution v5
1. `page.main_frame` → 遞迴 `child_frames`
2. 名稱匹配 (`mainFrame`)
3. URL 匹配 (`/Front/B/B03`)
4. Shared/Index 診斷 → 自動發現 child_frames → 重試

### Batch Execution v1
- 安全檢查: 1 次 JS evaluate (XPath `text=` 轉換)
- 批量執行: 1 次 JS evaluate (所有 click/fill)
- N+1 round-trip → 2 round-trip

## 支援彩種/平台

| 平台 | 彩種 | Profile | Frame |
|:---|:---|:---|:---|
| gts362 | 天天樂 | `site_profile_tiantianle_verified` | Shared/Index → mainFrame/B03 |
| gts362 | 539 | `site_profile_539_enriched` | 共用 template |

## 部署

- Python 3.11 + Playwright + Chromium
- Windows PowerShell CLI
- 無需伺服器，本地執行
- Queue/profile 檔案本機儲存 (.gitignore)

## 測試覆蓋

- `test_real_site_assisted_fill.py`: 109 tests
- `test_batch_queue.py`: 23 tests
- `test_batch_mock_queue.py`: 30 tests
- 完整 pytest: 978 passed
