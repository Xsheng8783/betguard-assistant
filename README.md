# betguard-assistant

betguard-assistant 是一個 539 / 多彩種下注風控助理。它不是全自動下注程式，也不會連線到網站、操作網頁、執行 OCR，或送出任何下注內容。

第一階段目標：

- 解析使用者貼上的 LINE 下注文字
- 轉成標準 JSON
- 執行基本風控檢查
- 顯示紅黃綠等級對應的狀態
- 提供 CLI 與單元測試

## 安全原則

- 不確定就停
- 重複號碼禁止下注
- 號碼超出範圍禁止下注
- 未知玩法禁止下注
- 不允許自動修正下注內容
- 不允許任何自動送出下注功能
- 第一版只做文字解析與風控，不操作網站

## 安裝

建議使用 Python 3.11 以上。

~~~bash
python -m venv .venv
.venv\Scripts\activate
pip install -e ".[dev]"
~~~

## CLI 範例

~~~bash
python -m betguard.cli "06-13-23-22/50"
~~~

預期輸出：

~~~json
{
  "game": "539",
  "type": "normal",
  "numbers": [6, 13, 23, 22],
  "stars": [2, 3, 4],
  "unit": 0.5,
  "money": 50,
  "status": "ok",
  "warnings": [],
  "errors": []
}
~~~

## 測試

~~~bash
pytest
~~~

## 目前限制

- 目前只啟用 539 的基本一般號碼格式。
- 其他彩種已保留檔案位置，但規則尚未確認前不會啟用。
- 目前不支援車碰、柱碰、尾數展開等進階玩法的完整解析。


## 本機 Review UI

本機 Review UI 使用 Streamlit，提供人工貼上多行下注文字、解析、風控檢查與摘要顯示。

安裝：

~~~bash
pip install -e ".[dev]"
~~~

啟動：

~~~bash
streamlit run src/betguard/ui_review.py
~~~

安全提醒：本工具只做解析與風控，不會自動送出下注。


### Review UI v2

Review UI 會顯示每筆下注的人類可讀摘要，並提供 JSON / CSV 下載，以及「只複製 OK 摘要」文字區塊。

安全限制：如果有任何錯誤或警告，頁面會顯示「目前有錯誤或警告，不可進入網站填單流程。」本工具不會自動送出下注，也不會操作網站。


### Review 歷史紀錄

每次在本機 Review UI 按下「解析」後，系統會自動在專案根目錄的 `review_logs/` 儲存一份完整 JSON 紀錄。即使本次結果有錯誤或警告，也會保存，方便日後回查。

檔名格式：

~~~text
review_YYYYMMDD_HHMMSS.json
~~~

紀錄內容包含建立時間、來源、原始貼文、Summary、OK 摘要，以及完整逐筆解析結果。UI 也會顯示「最近 10 筆 Review 紀錄」，並可下載單筆 JSON log。

安全限制：歷史紀錄功能只會新增與讀取紀錄，不提供修改或刪除操作，也不會操作網站或自動送出下注。


## 網站填單輔助 Dry-run Inspector

目前網站填單輔助只支援 Playwright dry-run inspector。這個模式只會開啟網站、等待人工登入、掃描頁面元素並輸出檢查報告；不會填單、不會按送出、不會按確認，也不會加入注單。

安裝 Playwright：

~~~bash
python -m pip install playwright
python -m playwright install chromium
~~~

執行 dry-run inspector：

~~~bash
python -m betguard.webfill.cli --url "http://www.gts362.com" --dry-run
~~~

安全限制：這一版絕對不點擊「送出、確認、確定、下注、加入注單、送出注單、刪除、清除全部」等危險按鈕；如果找不到元素，只會回報，不會猜測或亂點。


### Selector Discovery

Selector Discovery 是網站填單輔助 v1 的前置工具，只做 DOM / frame / element attribute 掃描，輸出候選 selector。它不會點擊號碼、不會填入金額、不會按送出或確認，也不會加入注單。

使用方式：

~~~bash
python -m betguard.webfill.cli --url "http://www.gts362.com" --dry-run --discover-selectors
~~~

流程：開啟 Chromium 後，請先手動登入，並手動進到 539 / 二三四星 / 連碰頁，再回終端機按 Enter。程式會掃描所有可讀取的 page、frame、child frame、frameset `frame` / `iframe` 來源，以及元素的 `text/value/id/name/className/type/role/href`，輸出 `number_selectors`、`amount_field_selectors`、`danger_selectors` 與 diagnostics。

安全限制：Selector Discovery 只產生候選 selector，不驗證點擊，不填單，不送出。若找不到危險按鈕 selector，報告會提醒「danger buttons not found; do not proceed to assisted fill until verified」。
