# OCR 引擎評估報告（2026-08-02）

> 目的：為「真實手寫 539 牌單照片 → 文字」選出下一步引擎方向，供 Owner 拍板。
> 作者：Cake（機械餅）。本報告不包含任何自動下注/送出邏輯，僅為文字辨識引擎評估。

## 1. 現況總結

- 目前架構（v0.5.39-beta）：`vision/` provider 抽象已就緒（contracts + fake provider + PaddleOCR subprocess worker + benchmark harness），service 層目前只接 fake provider，UI 為測試模式。
- 真實草寫牌單（3 張手機照片）已實測引擎：

| 引擎 | 結果 | 判定 |
|---|---|---|
| RapidOCR v4（已移除） | 大量亂碼：`1416≥58`、`h1<<16`、數字黏連 | ❌ 不可用 |
| TrOCR handwritten | 全認成英文單字（`submissions`、`to establish`） | ❌ 完全失敗 |
| PaddleOCR v6 | 模擬抖動圖數字空格正確；**真實草寫未測** | 🟡 待真實樣本 |
| gemma-4-12b（本地 VLM，LM Studio） | 結構化號碼組 + 倍數標記 + 中文 + 誠實標 `(?)` | ✅ 唯一可用（14-29s/張） |

**結論：真實草寫只有 VLM 路線可行。** VLM 的「候選 + 信心度 + (?) 標記」輸出與現有 contracts（Confidence/Alternative）設計一致。

## 2. 需求定義

- 輸入：手機拍攝的實體牌單照片（歪斜、陰影、手寫數字為主 + 少量中文欄位）
- 輸出：號碼組（539 為 5 個 01-39）+ 倍數標記 + 信心度 → 人工審核
- 打包對象：設備/技術未知的使用者 → 最低設備假設（不依賴獨顯/高 RAM/網路/自備 key；不內嵌 key）
- 隱私承諾：圖片不落盤、純本地處理

## 3. 2026 年新方法盤點（HF/GitHub 已查證）

傳統 OCR（CNN+CTC）路線已被 **VLM 型 OCR** 全面取代。2026 上半年開源三強：

| 模型 | 開發者 | 大小 | License | 特點 |
|---|---|---|---|---|
| **GLM-OCR** | 智譜 zai-org | **2.66GB（0.9B）** | MIT | OmniDocBench 94.62 #1；**官方支援 Ollama/vLLM**；版面+表格+手寫理解；3.7M 下載 |
| **DeepSeek-OCR-2** | DeepSeek | 6.79GB | Apache-2.0 | Visual Causal Flow 新視覺編碼；grounding 定位 + markdown；需 CUDA + flash-attn |
| **Unlimited-OCR** | 百度 | 6.68GB | MIT | One-shot Long-horizon Parsing；支援 ms-swift 微調；2.5M 下載 |

其他：chandra-ocr-2（datalab，qwen3.5-base）、nemotron-ocr-v2（NVIDIA）、PaddleOCR v3.7.0（2026-06，傳統路線最新）。

**⚠️ 未知項：** 上述皆為「文檔型」benchmark（印刷體表格/公式）稱霸，**真實手寫照片效果未公開實測** — 需要本地實測才知道。

## 4. 方案建議（按最低設備假設排序）

### 方案 A：本地輕量 VLM OCR — GLM-OCR（推薦首測）
- 0.9B 參數 / 2.66GB，CPU 可跑、4090 秒級；MIT 可商用
- Ollama 一鍵部署 → 完全符合現有 `paddleocr_subprocess.py` 的「EXE 外 worker + env var 指定 python」模式（改指 Ollama 或 worker python）
- 打包零負擔：EXE 不變，選配功能
- 風險：0.9B 對手寫的推理能力未知（文檔強 ≠ 手寫強），**必須實測**

### 方案 B：本地中量 VLM OCR — DeepSeek-OCR-2 / Unlimited-OCR
- 6.7GB 級，需 GPU（flash-attn）；效果可能更好但設備門檻高
- 打包/分發成本高，較不適合未知設備使用者
- 適合：若方案 A 手寫不足，作為進階選配

### 方案 C：雲端 VLM API（最高準度）
- GLM-OCR API（z.ai）、Gemini Flash、GPT-4o mini 等，1-3s/張
- 需使用者自備 key → 技術門檻高，與最低設備假設衝突
- 可作為進階用戶選配（架構 contracts 已預留 OpenAI/GCP/Azure）

### 方案 D：現有路線強化（PaddleOCR v3.7 + 前處理）
- 已證明真實草寫不足；前處理（去噪/透視校正/裁數字區放大）有邊際改善
- 建議作為 VLM 的輔助（多引擎交叉驗證），不作主引擎

## 5. 建議實測方案（待拍板）

用現有 3 張真實牌單照，測量矩陣：

| 引擎 | 號碼組完整度 | 數字正確率 | 倍數/中文辨識 | 速度 | 設備需求 |
|---|---|---|---|---|---|
| GLM-OCR（Ollama） | ? | ? | ? | ? | CPU/低配 GPU |
| DeepSeek-OCR-2 | ? | ? | ? | ? | GPU + flash-attn |
| Unlimited-OCR | ? | ? | ? | ? | GPU |
| gemma-4-12b（基線） | ✅ 已測 | 需真值 | ✅ | 14-29s | GPU |

- 需真實正確答案對照才能算精確準確率（3 張圖的真實號碼可由使用者提供）
- 建議順序：先 GLM-OCR（成本最低），不足再測 B 組

## 6. 決策請求

1. 是否授權本機實測 GLM-OCR？（安裝 Ollama + 下載 2.66GB）
2. 若 GLM-OCR 手寫不足，是否授權測 DeepSeek-OCR-2 / Unlimited-OCR？（各 ~6.7GB）
3. 方向確認：主引擎走「本地輕量 VLM worker」路線，雲端作為進階選配？

> 附註：gemma-4-12b 實測細節、TrOCR 失敗原因、VLM 提示詞模板等已在協作 agent 內部文件（vlm-handwriting.md）。
