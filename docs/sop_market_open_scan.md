# 開盤後只讀真網站掃描 SOP

> **本 SOP 只在使用者明確說「開盤」之後才能執行。**
> 在那之前，任何連線真網站的指令都不要跑。
>
> 掃描全程唯讀：工具只讀 DOM，不 click、不 fill、不 submit
> （selector_discovery.py 已逐行確認無任何點擊/填寫 API）。

開始前請先在 `C:\Users\User\betguard-assistant` 執行 `git pull`，
確認版本含 `--discover-selectors --out` 功能（commit `f9e8dbd` 之後）。

## 1. 什麼時候可以掃

**四個條件全部同時在畫面上看到**才能掃：

1. 已進入「二三四星」下注頁（不是首頁、不是選單）
2. 01–39 號碼盤完整可見
3. 二星 / 三星 / 四星金額欄位可見
4. 「送出注單」等危險按鈕可見
   （可見即可，絕不去點——掃描器需要**偵測到**它們，profile 驗證才會通過）

## 2. 什麼時候不能掃

以下任一情況都不掃：

- 還在**首頁**或**遊戲選單**
- 畫面上**沒有號碼盤**
- **未登入**，或被跳回登入頁
- 顯示**已關盤 / 停止收單**

這些情況掃了也只會得到 BLOCKED profile，浪費一次登入 session。

## 3. 掃描指令（唯讀 + 直接寫檔）

```powershell
cd C:\Users\User\betguard-assistant
python -m betguard.webfill.cli --dry-run --discover-selectors --url https://www.gts362.com --out selector_report.json
```

- `--out` 由 Python 直接寫 UTF-8 JSON。
- **不要再用 PowerShell `>` 重導向**（會變 UTF-16 壞檔，後續步驟讀不了）。
- 成功時 stdout 只印一行 `Selector report written: selector_report.json`。

## 4. 人工登入與人工進頁提醒

指令會開啟一個**看得見的** Chromium 視窗，然後終端機停住等你：

1. 在瀏覽器裡**自己**登入（工具不碰帳號密碼）
2. **自己**點進 539 → 二三四星頁，確認第 1 節四條件都在畫面上
3. 回到終端機按 Enter，工具開始唯讀掃描，結束後自動關閉瀏覽器

## 5. selector_report.json 檢查方式

打開檔案（或用 `python -m json.tool selector_report.json | more`）確認：

- `warnings` 裡**沒有** `login page`、`market closed`、`expected 39 number selectors` 等訊息
- `number_candidates` 有 01–39 共 39 組
- `amount_field_candidates` 的 二星 / 三星 / 四星 都非空
- `danger_candidates` 非空（有偵測到「送出注單」等）
- `market_state.can_probe_bet_page` 為 `true`

任何一項不符 → 不要往下走，回第 1 節確認頁面狀態後重掃。

## 6. 轉成 site_profile

```powershell
python -m betguard.webfill.cli --save-site-profile --selector-report selector_report.json --out site_profile.json --site-name gts362 --page-name 539 --captured-at <當天日期> --pretty
```

`--captured-at` 填當天日期（例如 `2026-07-03`），之後判斷 profile 新舊用。

## 7. 驗證 profile

```powershell
python -m betguard.webfill.cli --site-profile-report --profile site_profile.json --pretty
```

必須看到 `Status: OK` 才能進第 8 節。

## 8. 跑 map-dry-run

```powershell
python -m betguard.webfill.cli --map-dry-run --fill-plan manual_mapping_test/fill_plan.json --profile site_profile.json --pretty
```

fill_plan 可先用 manual_mapping_test 的測試計畫，之後再換成當天真實批次產生的 plan。

## 9. SAFE / BLOCKED 判斷

- **SAFE**：所有號碼與金額欄位都找到 selector、danger candidates 已驗證存在。
- **BLOCKED**：看 `Missing:` 段落——
  - `number XX selector missing`：該號碼沒對到
  - `amount field X星 missing`：金額欄沒對到
  - `danger buttons not verified`：沒偵測到危險按鈕，**這條永遠不能繞過**
- BLOCKED 就回第 5 節檢查或重掃，**不要試圖手改 JSON 讓它變 SAFE**。

## 10. executable=false 的意義

**SAFE ≠ 會執行。** `Final Decision` 永遠是 `executable: false`。

SAFE 只代表「selector 對照成功，資料可留作下一階段人工評估」，
不代表工具會（或可以）在真網站執行任何動作。

## 11. 絕對不能做的事

- 不 **click** 任何按鈕（包含號碼、頁籤）——掃描期間所有點擊都由人工在瀏覽器做，工具本身零點擊
- 不 **submit**、不按「送出注單」「確認」「加入注單」
- 不 **fill** 任何金額欄位
- 不 **confirm** 任何注單
- 不跑 `--real-site-assisted-fill`、不加 `--i-understand-real-site-fill-risk`
- 不跑 `--assist-fill`、不加 `--i-understand-human-final-confirm`

## 12. 結束點

**map-dry-run 跑完 = 本階段結束。**

得到 SAFE 之後停下、回報結果，等下一次明確授權才進入任何後續階段。
