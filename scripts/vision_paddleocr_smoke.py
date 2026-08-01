"""PaddleOCR smoke test CLI — runs OCR on an image via subprocess worker.

Usage:
  .venv/Scripts/python.exe scripts/vision_paddleocr_smoke.py --image "C:\path\sample.jpg"
  .venv/Scripts/python.exe scripts/vision_paddleocr_smoke.py --image sample.png --json-output result.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description="PaddleOCR subprocess smoke test")
    parser.add_argument("--image", required=True, help="Path to image file")
    parser.add_argument("--json-output", help="Optional path to save JSON result")
    args = parser.parse_args()

    image_path = Path(args.image)
    if not image_path.is_file():
        print(f"Error: image not found: {args.image}", file=sys.stderr)
        sys.exit(1)

    # Import vision modules (main project, no paddleocr)
    from betguard.vision.image_intake import validate_and_store, save_metadata, delete_image
    from betguard.vision.providers.paddleocr_subprocess import recognize_with_metadata

    # Validate + store image
    data = image_path.read_bytes()
    try:
        meta = validate_and_store(data, "", image_path.name)
        save_metadata(meta)
    except ValueError as e:
        print(f"Error: invalid image: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"Image: {image_path.name}")
    print(f"  Size: {meta.width}x{meta.height}, {meta.byte_size} bytes")
    print(f"  SHA-256: {meta.sha256[:16]}...")
    print(f"  MIME: {meta.mime_type}")
    print()

    # Run OCR
    print("Running PaddleOCR via subprocess worker...")
    try:
        result = recognize_with_metadata(meta)
    except RuntimeError as e:
        print(f"Error: {e}", file=sys.stderr)
        delete_image(meta.image_id)
        sys.exit(1)

    # Display results
    print(f"Status: {result.status.value}")
    print(f"Provider: {result.provider.id}")
    if result.provider.model_version:
        print(f"  Versions: {result.provider.model_version}")
    print(f"  Models: {result.provider.model_name}")
    print(f"  Latency: {result.latency_ms:.0f} ms")
    print(f"  Lines: {len(result.lines)}")
    print()

    for line in result.lines:
        level = line.confidence.level.value if hasattr(line.confidence.level, 'value') else line.confidence.level
        print(f"  [{level:7}] {line.text:20s} score={line.confidence.value}")
        if line.bounding_box and line.bounding_box.polygon:
            poly = line.bounding_box.polygon
            print(f"           box=[{poly[0][0]:.0f},{poly[0][1]:.0f},{poly[2][0]:.0f},{poly[2][1]:.0f}]")
        if line.warnings:
            for w in line.warnings:
                print(f"           ⚠ {w}")

    if result.warnings:
        print("\nWarnings:")
        for w in result.warnings:
            print(f"  - {w}")

    # Save JSON if requested
    if args.json_output:
        out_path = Path(args.json_output)
        out_path.write_text(json.dumps(result.to_dict(), ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        print(f"\nJSON saved to: {out_path}")

    # Cleanup
    delete_image(meta.image_id)


if __name__ == "__main__":
    main()
