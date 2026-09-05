"""GLM-OCR 引擎 — llama.cpp llama-server subprocess + 手寫牌單後處理。

設計原則:
  * 引擎 lazy 啟動 — 首次呼叫才 spawn llama-server，不拖慢 app 啟動。
  * 純記憶體處理 — 輸入 bytes、輸出文字行，圖片不落盤。
  * 純 CPU 推論 — -ngl 0（最低設備假設，不依賴獨顯）。
  * 零 Python 推論依賴 — 引擎是獨立 llama-server.exe（PyInstaller 當 data file），
    不打包 torch/transformers。
  * 原文保真 — 不認識的符號保留供人工檢查，不推測它們是筆跡噪音。

環境變數:
  * BETGUARD_OCR_ENGINE=glm         — 啟用本引擎（在 ocr.py 判斷）
  * BETGUARD_GLM_MODEL_DIR          — 含 GGUF 的目錄（預設: 打包 _internal/glm-ocr-model）
  * BETGUARD_LLAMA_SERVER_PATH      — llama-server.exe 路徑（預設: 打包 _internal）
"""
from __future__ import annotations

import atexit
import io
import os
import re
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from typing import Any

from .ocr import OcrError, ImageTooLargeError, MAX_IMAGE_BYTES

_CTX_SIZE = 8192
_SERVER_START_TIMEOUT = 60  # 模型載入最長等待
_INFER_TIMEOUT = 300  # 單張圖推論上限

# Detection only; never use this expression to remove or replace source marks.
_UNSUPPORTED_RE = re.compile(r"[^0-9xX×一二三四尾車半各?.,，、/／\s]")

_server: subprocess.Popen | None = None
_server_lock = threading.Lock()
_port: int = 0


def _default_model_dir() -> str:
    """模型目錄：打包後在 _internal/glm-ocr-model；dev 在專案 src 旁。"""
    base = getattr(
        sys, "_MEIPASS", os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    )
    return os.path.join(base, "glm-ocr-model")


def _default_server_path() -> str:
    """llama-server.exe 路徑：打包後在 _internal；dev 在專案根目錄。"""
    base = getattr(
        sys, "_MEIPASS", os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    )
    return os.path.join(base, "llama-server.exe")


def _find_model_files(model_dir: str) -> tuple[str, str]:
    """在目錄內找主模型與 mmproj。回傳 (model_gguf, mmproj_gguf)。"""
    if not os.path.isdir(model_dir):
        raise OcrError(f"GLM-OCR 模型目錄不存在: {model_dir}")
    main, mmproj = "", ""
    for name in sorted(os.listdir(model_dir)):
        if not name.endswith(".gguf"):
            continue
        if name.startswith("mmproj"):
            mmproj = os.path.join(model_dir, name)
        elif not main:
            main = os.path.join(model_dir, name)
    if not main or not mmproj:
        raise OcrError(f"模型目錄需含主 .gguf 與 mmproj-*.gguf: {model_dir}")
    return main, mmproj


def _pick_free_port() -> int:
    """找一個空閒 port。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _get_server() -> tuple[subprocess.Popen, int]:
    """取得（並快取）llama-server 子程序。執行緒安全。"""
    global _server, _port
    if _server is None or _server.poll() is not None:
        with _server_lock:
            if _server is None or _server.poll() is not None:
                _start_server()
    return _server, _port


def _start_server() -> None:
    """啟動 llama-server 並等待就緒。"""
    global _server, _port
    server_exe = os.environ.get("BETGUARD_LLAMA_SERVER_PATH", "") or _default_server_path()
    if not os.path.exists(server_exe):
        raise OcrError(
            f"找不到 llama-server.exe（設 BETGUARD_LLAMA_SERVER_PATH 指定）: {server_exe}"
        )
    model_dir = os.environ.get("BETGUARD_GLM_MODEL_DIR", "") or _default_model_dir()
    main_gguf, mmproj_gguf = _find_model_files(model_dir)

    port = _pick_free_port()
    cmd = [
        server_exe,
        "--model", main_gguf,
        "--mmproj", mmproj_gguf,
        "--port", str(port),
        "--ctx-size", str(_CTX_SIZE),
        "-ngl", "0",  # CPU only
    ]
    creationflags = 0
    if os.name == "nt":
        creationflags = subprocess.CREATE_NO_WINDOW  # 不要彈 console
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creationflags,
        )
    except Exception as exc:
        raise OcrError(f"llama-server 啟動失敗: {exc}") from exc

    # 等待 /v1/models 就緒
    deadline = time.monotonic() + _SERVER_START_TIMEOUT
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise OcrError(f"llama-server 啟動即退出 (exit {proc.returncode})")
        try:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/v1/models", timeout=2
            ) as r:
                if r.status == 200:
                    _server, _port = proc, port
                    atexit.register(_shutdown)
                    return
        except Exception:
            pass
        time.sleep(1)
    proc.kill()
    raise OcrError("llama-server 啟動逾時（模型載入失敗？）")


def _shutdown() -> None:
    """關閉 llama-server（atexit 與重啟時呼叫）。"""
    global _server
    proc, _server = _server, None
    if proc is not None and proc.poll() is None:
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass


def clean_glm_text(raw: str) -> str:
    """Legacy function name; return source text verbatim, including unknown marks."""
    return raw


def glm_text_issues(raw: str) -> dict[str, Any]:
    unsupported = sorted(set(_UNSUPPORTED_RE.findall(raw)))
    return {"unsupported_characters": unsupported, "uncertain": bool(unsupported or "?" in raw)}


def _infer(image_bytes: bytes) -> str:
    """送圖給 llama-server，回傳原始文字。"""
    import base64
    import json

    _, port = _get_server()
    b64 = base64.b64encode(image_bytes).decode()
    payload = {
        "model": "glm-ocr",
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                    {"type": "text", "text": "Text Recognition:"},
                ],
            }
        ],
        "max_tokens": 2048,
        "temperature": 0.1,
    }
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/v1/chat/completions",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=_INFER_TIMEOUT) as r:
            data = json.loads(r.read())
        return data["choices"][0]["message"]["content"]
    except Exception as exc:
        raise OcrError(f"GLM-OCR 辨識失敗: {exc}") from exc


def extract_text(image_bytes: bytes) -> list[dict[str, Any]]:
    """從圖片 bytes 取出文字行（GLM-OCR 引擎）。

    Args:
        image_bytes: 圖片原始 bytes（png / jpg / webp / bmp 等）。

    Returns:
        list of {text, score, box} — score/box 為 None/[]（GLM-OCR 不提供
        信心度與位置資訊）；無文字時回傳空 list。

    Raises:
        ImageTooLargeError: 圖片超過大小上限。
        OcrError: 引擎載入失敗或圖片無法解碼。
    """
    if not image_bytes:
        raise OcrError("空白圖片")
    if len(image_bytes) > MAX_IMAGE_BYTES:
        raise ImageTooLargeError(
            f"圖片超過大小上限 ({MAX_IMAGE_BYTES // (1024 * 1024)} MB)"
        )

    output_text = _infer(image_bytes)
    cleaned = clean_glm_text(output_text)
    if not cleaned:
        return []

    return [
        {"text": line, "score": None, "box": [], "source_line": index,
         "native_ocr_text": output_text, "adapter_text": cleaned,
         "transformations": [], **glm_text_issues(line)}
        for index, line in enumerate(cleaned.splitlines(), 1)
        if line.strip()
    ]
