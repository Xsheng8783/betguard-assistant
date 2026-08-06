# replay_expected.json 變更紀錄

## 2026-08-06（初始建立）

- 30 份正式 A/B raw response 固定為 fixtures，快照由 M2 完成後的 replay 流程產生。
- 基準：blocked 855、executable 165、共用倍率宣告 112。

## 2026-08-06（確定性檢查補強 + bare ×0.5 修正）

- 新增確定性檢查層（invalid／duplicate／combination count／region conflict），blocked 一律帶明確 block_reason。

## 2026-08-06（branch: debug/sample-034-ocr）

- 重生 30 份 replay 快照。
- 原因：semantic_parser / closed_set / pipeline 於本分支的變更（各二三×0.5
  不需「=」、複合倍率無空格、單位數不補 0、01–49、作用域規則、deterministic
  checks）使 replay 輸出合法更新；舊快照與現行 parser 不一致。
- 修正 semantic_parser：無類別 `×0.5`／`×05` 不再被誤判為柱碰號碼（INVALID_NUMBER_RANGE 120 → 64）。
- executable 基準演變：165（初始）→ 164（加入確定性檢查）→ 251（bare ×0.5 修正）。
- block_reason 分佈：MISSING_MULTIPLIER 448、SHARED_MULTIPLIER_DECLARATION 152、INVALID_NUMBER_RANGE 64、PARSE_ERROR 52、DUPLICATE_IN_COMBINATION 30、UNSUPPORTED_CAR_BET 23。

## 2026-08-06（管線串接：單一入口 + 決策物件 + 狀態機）

- replay 改為共用 `pipeline.process_row`（production 與 replay 單一實作）。
- 每行新增 `decision`（parse_status／supported／executable／review_status／block_reasons）與 provenance。
- 251 筆 executable 在人工批准前 review_status 皆為 needs_review、exported=0。
- 車玩法 supported=false、不可匯出（0 筆誤放行）。

## 2026-08-06（柱碰切片自動合併）

- `merge_column_slices`：同一區塊內，第一列 × 分隔 2 位數＝欄頭，後續列右對齊填入各欄，
  解決「兩列共同組成一個牌組」被模型拆成兩筆的問題。
- 實測 sample-005 已存輸出：25 行 → 21 行，4 組切片合併（`04/20 30/39 49` 等）。
- 新樣本 008–033 多為單行巢狀欄位輸出，合併影響較小；parser 直接處理。

- 以後任何 parser／closed-set／pipeline 行為變更，必須先更新本快照並在此留下原因。

## 2026-08-06???????????

- ?????X1?234X0.5?2X1 ???????????????????MULT_ANYWHERE??
- ??? ????? parser ??? ????????????? 3/4?2/3?
- ????????????????????
