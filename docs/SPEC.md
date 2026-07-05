# betguard-assistant 系統規格

> v0.5.0 | 2026-07-06

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
| `batch_fill_all` | 連續多筆填入 (瀏覽器不關) |
| `fill_mapping` | Selector 對應 (B03 frame, PengBet.Value) |
| `safety` | Danger word 檢查 |
| `selector_discovery` | 頁面 DOM 掃描產生 profile |

## 關鍵演算法

### Frame Resolution (window.frames)
Playwright 對 HTML `<frameset>` 支援有限 (`child_frames` 為空)。
→ 繞過：`window.frames` 原生 JS 走訪，URL 含 `/Front/B/B03` 的 frame。

### Knockout ViewModel Injection (v0.5.0)
不點 DOM 按鈕，直接操作 knockout.js ViewModel：
1. `ko.contextFor(td)` 取得號碼的 knockout context
2. `Mo.OnSwitchSel($data)` 選取號碼 (0.05s)
3. `PengBet.Value` 設定金額 (Playwright fill, ~3s)
總時間：0.1s/筆 (相較原生 90s 改善 99.9%)

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
