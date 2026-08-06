"""CLI: recognize a bet slip image with the Gemini paid vision provider.

Usage:
  $env:GEMINI_API_KEY = "AIza..."
  PYTHONPATH=src .venv/Scripts/python.exe -X utf8 -m betguard.vision.gemini_cli ^
      --image "C:\BetguardOCRDataset\raw\sample-001.png" [--model gemini-2.5-flash]

Outputs the raw transcription lines + structured semantics from the
deterministic Closed Set V2 parser. All results remain
PENDING_HUMAN_CONFIRMATION (never auto-fill).
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from .contracts import RecognitionRequest
from .providers.gemini_paid import (
    DEFAULT_MODEL,
    GeminiPaidVisionProvider,
    has_api_key,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Gemini paid vision recognize")
    parser.add_argument("--image", required=True, help="bet slip image path")
    parser.add_argument("--model", default=os.environ.get("BETGUARD_GEMINI_MODEL", DEFAULT_MODEL))
    parser.add_argument("--document-mode", default="auto")
    args = parser.parse_args(argv)

    if not has_api_key():
        print("GEMINI_API_KEY 未設定（請在 PowerShell 設定環境變數，不要在聊天中貼 key）", file=sys.stderr)
        return 1

    provider = GeminiPaidVisionProvider(model=args.model)
    request = RecognitionRequest(
        request_id="gemini-cli",
        image_id=os.path.basename(args.image),
        image_path=args.image,
        metadata={"document_mode": args.document_mode},
    )
    result = provider.recognize(request)

    print(json.dumps({
        "recognition_id": result.recognition_id,
        "provider_error": result.provider_error.code if result.provider_error else None,
        "lines": [
            {
                "line_id": l.line_id,
                "raw_text": l.text,
                "alternatives": l.alternatives,
            }
            for l in result.lines
        ],
        "semantics": result.preprocessing.get("openai_lines", []) if result.preprocessing else [],
        "human_confirmation_required": bool(result.preprocessing and result.preprocessing.get("human_confirmation_required")),
        "auto_submit": bool(result.preprocessing and result.preprocessing.get("auto_submit")),
        "auto_confirm": bool(result.preprocessing and result.preprocessing.get("auto_confirm")),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
