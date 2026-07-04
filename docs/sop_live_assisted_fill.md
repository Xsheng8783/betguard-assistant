# 真站首次人工輔助填入 SOP（Live Assisted Fill Operator Runbook v1）

> **本 SOP 是「開始考慮」第一次真站人工輔助填入之前必讀的檢查清單，不是執行手冊。**
> 走完本 SOP 的所有檢查點只代表資料面「看起來安全」；是否要真的按下
> `--i-understand-real-site-fill-risk` 執行、什麼時候執行，永遠是人的決定。
>
> 本文件本身不會、也不應該讓你去點擊/填寫/送出真網站。閱讀與操作這份 SOP
> 全程只需要本機 JSON 檔案與既有唯讀 CLI 指令。

## 0. 這份 SOP 解決什麼問題

目前為止已經完成、且各自有測試驗證的安全層：

- `--real-site-fill-readiness` — PASS/BLOCKED 總表（15 項檢查）
- `--real-site-fill-preflight` — v1 preflight，`READY_FOR_HUMAN_REVIEW` 才算過
- `--map-dry-run --concise` — 號碼/金額 selector 對照，`SAFE` 才算過
- `real_site_assisted_fill.py` 已強制要求 v1 preflight READY 才能建立 execution actions
- `#GroupSet_Value` 在 mapping 層、preflight 層、execution 層三重排除
- 用 FakePage/FakeLocator 驗證過：不會 submit、不會 confirm、不會自動跳下一筆、不會自動送出

這些機制個別都已經是安全的，但**沒有人規定操作者要照什麼順序、看到什麼結果才可以繼續**。
這份 SOP 就是把上面這些機制串成一份「操作者在第一次真站測試前，必須照順序做完的檢查清單」。

## 1. 開始前的必要條件（Required preconditions）

**下列每一項都必須成立，缺一都不要往下走：**

1. **git 狀態乾淨**——`git status --short` 只能看到本機測試產物（例如
   `input_539_clean.txt` / `queue_539_clean.json` / `review_539_clean/` 這類已知的
   未追蹤本機檔案），不能有未預期的原始碼變更、也不能有已 staged 但未確認的內容。
2. **queue 檔案正確**——確認 `--queue` 指向的是今天、這個遊戲、這筆下注的
   queue 檔案，不是舊檔或別的遊戲的檔案。
3. **`approved_fill_queue` 存在**——這筆 queue 已經跑過人工審核流程
   （accept-valid），`queue["approved_fill_queue"]` 不是空的。
4. **這筆項目 `accepted_by_human=true`**——目標 item 已經被人明確接受過，
   不是自動產生、未經確認的候選。
5. **profile 檔案正確對應遊戲**——539 用 539 的 site_profile，天天樂用天天樂的
   site_profile，**不要混用**（見第 5 節天天樂注意事項）。
6. **`--real-site-fill-readiness` 回傳 `PASS`**。
7. **`--real-site-fill-preflight` 回傳 `READY_FOR_HUMAN_REVIEW`**。
8. **`--map-dry-run --concise` 回傳 `SAFE`**。

以上 6–8 三項理論上是一致的（readiness 內部就是呼叫 preflight，preflight 內部
就是呼叫 map-dry-run 的同一套邏輯），但仍建議三個指令都跑一次、都親眼看過
輸出，而不是只信任其中一個——這是人為疏忽的最後防線，不是重複勞動。

## 2. 必須執行的指令（Required commands）

依序執行，每一步都先看結果再決定要不要繼續：

```powershell
# 1) 總表：PASS 才能往下看
python -m betguard.webfill.cli --real-site-fill-readiness `
  --queue queue.json --profile site_profile.json --item-index 0 --concise

# 2) 個別確認 preflight：必須是 READY_FOR_HUMAN_REVIEW
python -m betguard.webfill.cli --real-site-fill-preflight `
  --queue queue.json --profile site_profile.json --item-index 0 --concise

# 3) 個別確認 map-dry-run：必須是 SAFE
python -m betguard.webfill.cli --map-dry-run `
  --fill-plan fill_plan.json --profile site_profile.json --concise
```

**可選：金額 numbers-only fallback。**
如果這筆項目的金額欄位讓你不放心（即使 readiness/preflight 都過），可以改用
numbers-only 模式，讓工具只協助選號碼、金額永遠由人工手動輸入：

```powershell
python -m betguard.webfill.cli --numbers-only-fill-plan --fill-plan fill_plan.json --out fill_plan_numbers_only.json
```

之後用 `fill_plan_numbers_only.json` 重跑第 3 步的 `--map-dry-run --concise`，
確認 `amount_manual_required: true` 且不出現 `Amounts:` 區塊。

## 3. 硬性停止條件（Hard stop conditions）

**只要出現以下任何一種情況，立刻停止，不要往下、不要重跑到它變綠為止：**

- `--real-site-fill-readiness` 回傳 `BLOCKED`
- `--real-site-fill-preflight` 回傳 `BLOCKED`
- `--map-dry-run` 回傳 `BLOCKED`
- 遊戲或 profile 對不上（例如你以為在測天天樂，但 profile/queue 其實是 539 的）
- 輸出裡任何地方出現 `GroupSet_Value` 被當成安全的金額欄位
- 任何地方出現 `submit`/`confirm`/送出/確認相關的字樣被列進可執行動作
- 同一時間有超過一筆項目處於 `CURRENT`/`WAITING_FOR_HUMAN_CONFIRM`
- 金額欄位的 `position_verified` 不是 `true`

**BLOCKED 是正確、預期的安全結果，不是要修的錯誤。** 出現以上任一情況時，
回去看該指令的 `errors`/`missing` 欄位找原因，而不是嘗試繞過、手改 JSON、
或加 override 檔案讓它變綠。

## 4. 第一次真站測試的規則（First live test rules）

一旦真的要進到「人工輔助點擊/填寫」階段（也就是加上
`--i-understand-real-site-fill-risk` 執行 `--real-site-assisted-fill`），
必須遵守：

- **一次只處理一筆項目。** 不要一次排一整批。
- **人全程盯著畫面**，逐一確認工具準備做的每個動作，而不是背景執行後才回來看結果。
- **不自動跳下一筆。** 這筆做完、人工確認完，才手動開始下一筆。
- **不 submit**（不按送出注單）。
- **不 confirm**（不按確認/確定）。
- **不做任何「完成/結案」動作。** 這筆的最終送出與確認永遠由人親手操作，
  工具不做，也不應該被要求做。
- **做完這一筆立刻停下來**，不管結果看起來多順利，都不要連續跑下一筆。
- **人工記錄結果**（成功/失敗/畫面截圖/任何異常），這份記錄不由工具自動產生。

## 5. 539 與天天樂的重要差異（caveat）

- 天天樂與 539 共用同一套底層頁面樣板（同一組 `PengBet.Value` 三欄金額邏輯），
  所以 mapping/preflight 的判斷邏輯不需要為天天樂另外修改，這點已經驗證過。
- **但天天樂的 `current_game`/`game_id` 欄位可能是過期資料。** 站台在同一個
  分頁內用 AJAX 切換遊戲時，`$Global` 設定（反映在
  `global_config.game_id` / `market_state.current_game_name` 上）只在第一次
  載入頁面時設定一次，切換遊戲後**不會**跟著更新。這代表即使畫面上真的是
  天天樂，選出來的 selector_report/profile 裡這兩個欄位仍可能顯示 `"539"`。
- **判斷依據請以渲染後的 `mainFrame` 文字為準**（例如
  `diagnostics.live_frames[].sample_text` 裡是否出現「天天樂 - 下注資訊」
  等天天樂專屬字樣，以及與 539 不同的單碰上限數字），而不是
  `current_game`/`game_id` 這兩個過期欄位。
- 因此在第一次真站測試前，務必**親眼**確認畫面上顯示的遊戲，而不是只看
  JSON 裡的 `current_game` 欄位就下結論——這正是本 SOP 第 1 節「profile 正確
  對應遊戲」這一條存在的原因。

## 相關文件

- `docs/SAFETY_INVARIANTS.md` — 各項安全機制背後的不可變規則與理由。
- `docs/DIAGNOSTICS_GUIDE.md` — 如何讀懂 map-dry-run / 診斷欄位的細節。
- `docs/sop_market_open_scan.md` — 開盤後掃描、建立 selector_report/profile
  的完整流程（含天天樂章節）。
