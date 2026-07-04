# betguard-assistant 本機環境啟動文件

> **版本**：v0.3.0-tiantianle-assisted-fill-success  
> **最後更新**：2026-07-05  
> **適用平台**：Windows + Git Bash / PowerShell  

---

## 快速啟動（重開機後）

```powershell
# 1. 進入專案目錄
cd "C:\Users\USER\Documents\Codex\2026-06-30\init-python-betguard-assistant-539-1\betguard-assistant"

# 2. 設定 PYTHONPATH（必須，否則找不到 betguard 模組）
$env:PYTHONPATH = (Resolve-Path .\src).Path

# 3. 確認 Python 環境
python -c "import sys; print(sys.executable)"
# 預期輸出：C:\Users\USER\AppData\Local\hermes\hermes-agent\venv\Scripts\python.exe

# 4. 確認 betguard 可匯入
python -X utf8 -c "import betguard; print(betguard.__file__)"
# 預期輸出：...\betguard-assistant\src\betguard\__init__.py

# 5. 安裝必要套件（僅首次或重裝後需要）
python -m pip install playwright streamlit
python -m playwright install chromium

# 6. 確認所有關鍵模組可用
python -X utf8 -c "import betguard; import playwright; import streamlit; print('OK')"
# 預期輸出：OK
```

### 使用 Git Bash 的替代寫法

```bash
cd "C:/Users/USER/Documents/Codex/2026-06-30/init-python-betguard-assistant-539-1/betguard-assistant"
export PYTHONPATH="$(pwd)/src"
python -X utf8 -c "import betguard; import playwright; import streamlit; print('OK')"
```

---

## 必要套件清單

| 套件 | 用途 | 安裝指令 |
|:---|---|:---|
| `playwright` | 瀏覽器自動化（輔助填入） | `python -m pip install playwright` |
| `chromium` (Playwright) | Playwright 瀏覽器執行檔 | `python -m playwright install chromium` |
| `streamlit` | 審核介面（review console） | `python -m pip install streamlit` |
| `pytest` | 執行測試（選用） | `python -m pip install pytest pytest-xdist` |

---

## 常見錯誤與修復

### ❌ `fatal: not a git repository`

```
fatal: not a git repository (or any of the parent directories): .git
```

**原因**：當前目錄不在 Git 倉庫內。

**修復**：
```powershell
cd "C:\Users\USER\Documents\Codex\2026-06-30\init-python-betguard-assistant-539-1\betguard-assistant"
```

---

### ❌ `ModuleNotFoundError: No module named 'betguard'`

```
ModuleNotFoundError: No module named 'betguard'
```

**原因**：`PYTHONPATH` 未設定或路徑錯誤。

**修復**：
```powershell
$env:PYTHONPATH = (Resolve-Path .\src).Path
```

驗證：
```powershell
python -X utf8 -c "import betguard; print(betguard.__file__)"
```

---

### ❌ `ModuleNotFoundError: No module named 'playwright'`

```
ModuleNotFoundError: No module named 'playwright'
```

**原因**：Playwright 套件未安裝（重開機後若 Python 環境切換會遺失）。

**修復**：
```powershell
python -m pip install playwright
python -m playwright install chromium
```

---

### ❌ `ModuleNotFoundError: No module named 'streamlit'`

```
ModuleNotFoundError: No module named 'streamlit'
```

**原因**：Streamlit 套件未安裝。

**修復**：
```powershell
python -m pip install streamlit
```

---

### ❌ Playwright 瀏覽器未安裝

```
playwright._impl._api_types.Error: Executable doesn't exist at ...
```

**原因**：Playwright 套件已安裝但 Chromium 執行檔未下載。

**修復**：
```powershell
python -m playwright install chromium
```

---

### ❌ `pip` 版本過舊

```
WARNING: You are using pip version 21.x; however, version 24.x is available.
```

**修復**（可選）：
```powershell
python -m pip install --upgrade pip
```

---

## 安全指令：Readiness 檢查（唯讀、不開瀏覽器）

在執行任何 live assisted fill 之前，**必須先通過 readiness 檢查**。

此指令**不會開啟瀏覽器、不會點擊、不會填寫、不會送出**：

```powershell
python -X utf8 -m betguard.webfill.cli `
  --real-site-fill-readiness `
  --queue queue_tiantianle_clean.json `
  --profile ../betguard-local-artifacts/site_profile_tiantianle_verified_attempt2.json `
  --item-index 1 `
  --concise
```

**預期輸出**：
```
Status: PASS
Item: [1] 一般：11, 22, 33｜二三星｜1支｜100元
```

### Readiness 通過條件（全部必須為 true）

| 檢查項 | 說明 |
|:---|---|
| `approved_fill_queue_present` | approved_fill_queue 存在 |
| `item_found` | 指定 index 的 item 存在 |
| `human_accepted` | 人類已確認 |
| `review_state_excluded` | 審核狀態可進入 fill 流程 |
| `profile_supplied` | site profile 已提供 |
| `profile_validation_ok` | profile 驗證通過 |
| `preflight_status_ready` | preflight 狀態為 READY |
| `execution_actions_buildable` | 可建立執行動作 |
| `action_types_allowed_only` | 僅允許的動作類型 |
| `groupset_value_excluded` | #GroupSet_Value 已被排除 |
| `amounts_position_verified` | 金額欄位位置已驗證 |
| `no_forbidden_steps` | 無禁止步驟 |
| `final_flags_safe` | 最終旗標安全 |
| `one_item_at_a_time` | 一次只處理一筆 |
| `no_auto_next_evidence` | 無 auto-next 證據 |

---

## ⚠️ 重要安全說明

| 說明 | 詳細 |
|:---|---|
| **Readiness 是安全/唯讀的** | 不開瀏覽器、不點擊、不填寫、不送出 |
| **Live assisted fill 需要人類明確決定** | 必須加上 `--i-understand-real-site-fill-risk` |
| **絕對不可自動點擊送出/確認** | `送出注單`、`確認` 對話框必須由人類手動操作 |
| **不可 auto-submit** | 系統設計上不可自動送出 |
| **不可 auto-next** | 每筆完成後 queue lock 需人類手動解鎖 |

---

## 疑難排解總覽

| 問題 | 關鍵字 | 解決方案 |
|:---|:---|:---|
| 找不到 betguard | `No module named 'betguard'` | 設定 `PYTHONPATH` |
| 找不到 playwright | `No module named 'playwright'` | `pip install playwright` + `playwright install chromium` |
| 找不到 streamlit | `No module named 'streamlit'` | `pip install streamlit` |
| 不在 Git 倉庫 | `not a git repository` | `cd` 到正確目錄 |
| 無法執行指令 | `python` 找不到 | 確認 venv Python 路徑 |
