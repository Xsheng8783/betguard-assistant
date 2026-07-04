# Tiantianle Real-Site Assisted Fill — 首次成功里程碑

> **日期**：2026-07-05  
> **性質**：安全里程碑文件，非操作手冊  
> **狀態**：首次成功 — 天天樂 二三四星 連碰 輔助填入  

---

## 前置條件

| 條件 | 狀態 |
|:---|---|
| 文字已解析（parser → review → accepted） | ✅ |
| `approved_fill_queue` 已建立且人類已確認 | ✅ |
| `queue_tiantianle_clean.json` 存在且 status=READY | ✅ |
| `site_profile_tiantianle_verified_attempt2.json` 已驗證 | ✅ |
| Python 環境：`betguard` + `playwright` + Chromium | ✅ |
| `PYTHONPATH` 指向 `src/` | ✅ |
| Readiness checklist 全部 PASS | ✅ |
| Preflight → `READY_FOR_HUMAN_REVIEW` | ✅ |

---

## 成功案例：天天樂 11, 22, 33 二三星

### 輸入
```
11 22 33 二三X1
```

### 解析結果
- 號碼：`11, 22, 33`
- 星別：`二星` + `三星`（不填四星）
- 二星金額：`100`
- 三星金額：`100`

### 執行結果

| 動作 | 結果 |
|:---|---|
| 選擇 11 | ✅ 成功點擊 |
| 選擇 22 | ✅ 成功點擊 |
| 選擇 33 | ✅ 成功點擊 |
| 填入二星金額 100 | ✅ 成功填入 |
| 填入三星金額 100 | ✅ 成功填入 |
| 填入四星金額 | ⬚ 未執行（正確跳過） |

---

## 安全保證（已觀察）

| 保證 | 狀態 |
|:---|---|
| 送出按鈕 (`送出注單`) 被偵測但**未點擊** | ✅ |
| 確認對話框被偵測但**未點擊** | ✅ |
| `real_site_auto_submit` = `false` | ✅ |
| `human_required` = `true` | ✅ |
| `auto-submit` 不可能 | ✅ |
| `auto-next` 不可能 | ✅ |
| `#GroupSet_Value` 仍被排除 | ✅ |
| Danger buttons detected but clicked none | ✅ |
| Queue 停在 `WAITING_FOR_HUMAN_CONFIRM` | ✅ |
| Next item locked until human confirms | ✅ |

---

## 技術里程碑

### Frame Resolver 修復歷史

天天樂網站的投注頁面採用 `<frameset>` 結構：

```
www.gts362.com (頂層)
  └─ /Front/Shared/Index (frameset)
       ├─ gmenu (/Front/Shared/Menu)
       ├─ gprint (/Front/Shared/BetList)
       └─ mainFrame (/Front/B/B03)  ← 真正投注頁
```

**問題**：Playwright 的 `page.frames` 只回傳頂層頁面，`mainFrame` 僅能透過 `frame.child_frames` 存取，但 `getattr(frame, "child_frames")` 在部分 Playwright 版本中非同步載入。

**修復**（commit 未推送）：
1. `_collect_all_frames` 回傳 `(frames, diagnostics)` tuple
2. `_resolve_frame` 在 Shared/Index 診斷發現 child_frames 後自動合併到候選池並重試名稱/URL 匹配
3. 加入 collector diagnostics 以利於未來 debug

### Queue 狀態

Queue 必須處於 `READY` 狀態且 items 為 `CURRENT`，才能被 live fill 正確處理。若上一次操作停留在 `WAITING_FOR_HUMAN_CONFIRM`，需手動重置。

---

## 完成後**不可**做的事

| 禁止 | 原因 |
|:---|---|
| ❌ 點擊送出 (`送出注單`) | 必須由人類確認後手動操作 |
| ❌ 點擊確認對話框 | 同上 |
| ❌ 執行 `auto-submit` | 設計上不可能，但不可嘗試繞過 |
| ❌ 執行 `auto-next` | Queue lock 防止，但不可手動解鎖 |
| ❌ 修改 parser/preprocessor | 第一階段範圍已完成 |
| ❌ 放寬 selector guard | 安全約束不可降級 |
| ❌ 將 `#GroupSet_Value` 當作金額欄 | 該元素為群組設定值，非投注金額 |

---

## 建議後續工作流程

1. **人類檢查**：在瀏覽器中確認號碼和金額無誤
2. **人類手動送出**：點擊 `送出注單` → 確認對話框
3. **標記完成**：執行 `mark_item_done_by_human` 或手動更新 queue
4. **下一筆**：重置 queue 狀態，處理 next item
5. **重複**：Readiness → Preflight → Live fill → 人類檢查 → 手動送出

---

## 相關檔案

| 檔案 | 用途 |
|:---|---|
| `queue_tiantianle_clean.json` | 天天樂測試 queue |
| `../betguard-local-artifacts/site_profile_tiantianle_verified_attempt2.json` | 天天樂網站 profile |
| `src/betguard/webfill/real_site_assisted_fill.py` | 輔助填入核心邏輯 |
| `tests/test_real_site_assisted_fill.py` | 970 個測試（含 frame resolver 測試） |

---

> **⚠️ 本文件記錄的是一次安全輔助填入的成功案例。betguard-assistant 不是自動下注系統。所有送出、確認操作必須由人類執行。**
