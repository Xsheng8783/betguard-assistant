# Betguard Assistant Windows 安裝包建置說明

## 建置環境

- Windows 10/11
- Python 3.11
- Git Bash
- Inno Setup 6（選用，用於產生 Setup.exe）

必要 Python 套件：
```
pip install pyinstaller playwright
python -m playwright install chromium
```

## 如何產生安裝包

```powershell
powershell -ExecutionPolicy Bypass -File scripts/build_windows_installer.ps1
```

### 輸出

| 路徑 | 說明 |
|:---|:---|
| `dist/BetguardAssistant/` | PyInstaller one-folder 打包 |
| `release/Betguard-Assistant-v0.5.34-beta-Setup.exe` | Inno Setup 安裝程式 |

若未安裝 Inno Setup，`dist/` 資料夾可直接複製使用。

## 安裝包內容

- BetguardAssistant.exe（啟動器，無 CMD 視窗）
- Python 3.11 runtime
- Playwright Chromium browser
- 所有必要 source code

## 如何在乾淨電腦測試

1. 準備一台沒有 Python / Git / Playwright 的 Windows 電腦
2. 將 Setup.exe 複製到該電腦
3. 雙擊 Setup.exe，按照安裝精靈完成安裝
4. 桌面出現「Betguard Assistant」捷徑
5. 雙擊捷徑 → 瀏覽器自動開啟 Dashboard
6. 可使用 Dashboard 的「開啟下牌網站」與輔助面板

## 使用者資料保存位置

```
%USERPROFILE%\Documents\Betguard Assistant Data\
  runs/         # 審核批次
  logs/         # 日誌（如有）
  exports/      # 匯出資料（如有）
  settings/     # 設定（如有）
```

## 如何解除安裝

- 開始功能表 → Betguard Assistant → 解除安裝
- 或：設定 → 應用程式 → Betguard Assistant → 解除安裝
- 解除安裝時可選擇保留使用者資料（預設保留）
- 使用者資料位於 `Documents\Betguard Assistant Data\`，可手動刪除

## 常見錯誤

### 啟動時找不到 Chromium
確認 `dist/BetguardAssistant/ms-playwright/` 目錄存在且包含 chromium 子目錄。

### 8765 port 被占用
關閉其他佔用 8765 的程式，或修改 `src/betguard/webui/app.py` 中的 port。

### 殺毒軟體攔截
PyInstaller 打包的 exe 可能被 Windows Defender 誤判。將安裝目錄加入白名單。

## 如何更新版本號

1. 修改 `pyproject.toml` 中的 `version`
2. 修改 `installer/betguard-assistant.iss` 中的 `#define MyAppVersion`
3. 修改 `scripts/build_windows_installer.ps1` 中的 `$Version`
4. 重新執行建置指令
