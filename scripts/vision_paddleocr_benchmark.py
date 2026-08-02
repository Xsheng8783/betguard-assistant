"""PaddleOCR benchmark CLI — runs benchmark worker via subprocess.

Usage:
  $env:BETGUARD_OCR_PYTHON = "...ocr-worker-poc\venv\Scripts\python.exe"
  .venv/Scripts/python.exe scripts/vision_paddleocr_benchmark.py --image sample.jpg --output-dir benchmark-001
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path


BENCHMARK_VERSION = "betguard.vision.benchmark.v1"
MAX_STDOUT = 5 * 1024 * 1024
MAX_STDERR = 1 * 1024 * 1024
TIMEOUT = 600

PREPROCESS_PROFILES = [
    "original", "grayscale_clahe", "upscale_2x_clahe",
    "otsu_2x", "adaptive_2x", "sharpen_clahe_2x",
]

DETECTION_PROFILES = {
    "balanced": {
        "text_det_limit_side_len": 960, "text_det_limit_type": "min",
        "text_det_thresh": 0.4, "text_det_box_thresh": 0.6,
        "text_det_unclip_ratio": 1.5, "text_rec_score_thresh": 0.0,
    },
    "high_recall": {
        "text_det_limit_side_len": 1216, "text_det_limit_type": "min",
        "text_det_thresh": 0.3, "text_det_box_thresh": 0.5,
        "text_det_unclip_ratio": 1.5, "text_rec_score_thresh": 0.0,
    },
    "experimental_low_box": {
        "text_det_limit_side_len": 1216, "text_det_limit_type": "min",
        "text_det_thresh": 0.25, "text_det_box_thresh": 0.4,
        "text_det_unclip_ratio": 1.8, "text_rec_score_thresh": 0.0,
        "_experimental": True,
    },
}


def _ocr_python() -> str:
    path = os.environ.get("BETGUARD_OCR_PYTHON", "")
    if not path:
        sys.exit("Error: BETGUARD_OCR_PYTHON not set")
    if not os.path.isfile(path):
        sys.exit(f"Error: BETGUARD_OCR_PYTHON not found: {path}")
    return path


def _worker_path() -> Path:
    candidates = [
        Path(__file__).resolve().parents[1] / "tools" / "vision" / "paddleocr_benchmark_worker.py",
        Path("tools/vision/paddleocr_benchmark_worker.py").resolve(),
    ]
    for p in candidates:
        if p.is_file():
            return p
    sys.exit("Error: benchmark worker not found")


def _run_benchmark(request: dict) -> dict:
    python_exe = _ocr_python()
    worker = str(_worker_path())

    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8:backslashreplace"

    try:
        proc = subprocess.run(
            [python_exe, worker],
            input=json.dumps(request, ensure_ascii=False).encode("utf-8"),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=TIMEOUT,
            shell=False,
            env=env,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": {"code": "BENCHMARK_TIMEOUT"}}
    except Exception:
        return {"ok": False, "error": {"code": "BENCHMARK_FAILED"}}

    stdout_bytes = proc.stdout or b""
    if len(stdout_bytes) > MAX_STDOUT:
        return {"ok": False, "error": {"code": "OUTPUT_TOO_LARGE"}}

    try:
        stdout = stdout_bytes.decode("utf-8")
    except UnicodeDecodeError:
        return {"ok": False, "error": {"code": "OUTPUT_INVALID"}}

    try:
        return json.loads(stdout)
    except json.JSONDecodeError:
        return {"ok": False, "error": {"code": "OUTPUT_INVALID"}}


def _html_escape(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def _build_html(result: dict, image_path: str | None = None) -> str:
    profiles = result.get("profiles", [])
    engine = result.get("engine", {})

    rows = ""
    for p in profiles:
        status = p.get("status", "failed")
        color = "#16a34a" if status == "completed" else "#ef4444"
        rows += f"""<tr>
<td>{p.get('rotation', '-')}°</td>
<td>{_html_escape(p.get('preprocess_profile', '-'))}</td>
<td>{_html_escape(p.get('detection_profile', '-'))}</td>
<td style="color:{color}">{status}</td>
<td>{p.get('detected_count', 0)}</td>
<td>{p.get('mean_confidence', 0):.4f}</td>
<td>{p.get('digit_count', 0)}</td>
<td>{p.get('heuristic_score', 0):.1f}</td>
<td>{p.get('elapsed_ms', 0):.0f}ms</td>
<td style="font-size:12px;max-width:300px;overflow:hidden">{_html_escape(p.get('raw_text', '')[:100])}</td>
</tr>"""

    # SVG overlay for best profile
    svg_overlay = ""
    best_idx = -1
    best_score = -1.0
    for i, p in enumerate(profiles):
        if p.get("status") == "completed" and p.get("heuristic_score", 0) > best_score:
            best_score = p["heuristic_score"]
            best_idx = i
    if best_idx >= 0:
        bp = profiles[best_idx]
        svg_polys = ""
        for item in bp.get("items", []):
            poly = item.get("original_polygon", item.get("processed_polygon", []))
            pts = " ".join(f"{pt[0]},{pt[1]}" for pt in poly if len(pt) >= 2)
            if pts:
                svg_polys += f'<polygon points="{pts}" fill="none" stroke="#2563eb" stroke-width="2"/>\n'
                cx = sum(pt[0] for pt in poly if len(pt) >= 2) / max(1, len(poly))
                cy = sum(pt[1] for pt in poly if len(pt) >= 2) / max(1, len(poly))
                text = _html_escape(item.get("text", ""))
                score = item.get("score", 0)
                svg_polys += f'<text x="{cx}" y="{cy}" font-size="11" fill="#ef4444" font-weight="bold">{text} ({score:.2f})</text>\n'
        pp = bp.get("preprocess_result", {})
        img_w, img_h = pp.get("width", 800), pp.get("height", 600)
        svg_overlay = f"""<svg width="{img_w}" height="{img_h}" style="position:absolute;top:0;left:0">
{svg_polys}
</svg>"""

    # Image data URI
    img_tag = ""
    if image_path and os.path.isfile(image_path):
        import base64
        data = Path(image_path).read_bytes()
        if len(data) < 5 * 1024 * 1024:
            b64 = base64.b64encode(data).decode()
            mime = "image/jpeg" if image_path.lower().endswith((".jpg", ".jpeg")) else "image/png"
            img_tag = f'<div style="position:relative;display:inline-block"><img src="data:{mime};base64,{b64}" style="max-width:100%;max-height:500px">{svg_overlay}</div>'

    return f"""<!doctype html>
<html lang="zh-Hant">
<head><meta charset="utf-8"><title>PaddleOCR Benchmark</title>
<style>
body{{font-family:system-ui,sans-serif;margin:20px;color:#1e293b}}
table{{border-collapse:collapse;width:100%;font-size:14px}}
th,td{{border:1px solid #e2e8f0;padding:6px 8px;text-align:left}}
th{{background:#f8fafc;font-weight:600}}
.warn{{background:#fef3c7;padding:8px;border-left:3px solid #f59e0b;margin:12px 0}}
</style></head>
<body>
<h1>PaddleOCR Benchmark Report</h1>
<div class="warn">⚠ heuristic_score is NOT accuracy. It ranks profiles by detection count, confidence, and digit presence.</div>
<p>Engine: {_html_escape(engine.get('name', '-'))} | Paddle: {_html_escape(engine.get('paddle_version', '-'))} | PaddleOCR: {_html_escape(engine.get('paddleocr_version', '-'))}</p>
<p>Best rotation: {result.get('provisional_best_rotation', '-')}° | Best preprocess: {_html_escape(result.get('provisional_best_preprocess', '-'))} | Best detection: {_html_escape(result.get('provisional_best_detection', '-'))}</p>
<p>Total time: {result.get('total_elapsed_ms', 0):.0f}ms | Inferences: {result.get('inference_count', 0)}</p>
{img_tag}
<h2>Profile Results</h2>
<table>
<tr><th>Rot</th><th>Preprocess</th><th>Detection</th><th>Status</th><th>Items</th><th>Mean Conf</th><th>Digits</th><th>Score</th><th>Time</th><th>Raw Text</th></tr>
{rows}
</table>
</body></html>"""


def _build_markdown(result: dict) -> str:
    profiles = result.get("profiles", [])
    engine = result.get("engine", {})
    lines = [
        "# PaddleOCR Benchmark Summary",
        "",
        f"**Engine:** {engine.get('name', '-')}",
        f"**Paddle:** {engine.get('paddle_version', '-')}",
        f"**PaddleOCR:** {engine.get('paddleocr_version', '-')}",
        f"**Model:** {engine.get('detection_model', '-')} / {engine.get('recognition_model', '-')}",
        "",
        f"**Provisional best rotation:** {result.get('provisional_best_rotation', '-')}°",
        f"**Provisional best preprocess:** {result.get('provisional_best_preprocess', '-')}",
        f"**Provisional best detection:** {result.get('provisional_best_detection', '-')}",
        f"**Total time:** {result.get('total_elapsed_ms', 0):.0f}ms",
        f"**Inferences:** {result.get('inference_count', 0)}",
        "",
        "> ⚠ heuristic_score is NOT accuracy.",
        "",
        "| Rot | Preprocess | Detection | Status | Items | Mean Conf | Digits | Score | Time |",
        "|-----|-----------|-----------|--------|-------|-----------|--------|-------|------|",
    ]
    for p in profiles:
        lines.append(
            f"| {p.get('rotation', '-')}° | {p.get('preprocess_profile', '-')} | {p.get('detection_profile', '-')} | "
            f"{p.get('status', '-')} | {p.get('detected_count', 0)} | {p.get('mean_confidence', 0):.4f} | "
            f"{p.get('digit_count', 0)} | {p.get('heuristic_score', 0):.1f} | {p.get('elapsed_ms', 0):.0f}ms |"
        )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="PaddleOCR benchmark")
    parser.add_argument("--image", required=True, help="Path to sample image")
    parser.add_argument("--output-dir", required=True, help="Output directory (must be outside repo)")
    parser.add_argument("--ground-truth", help="Optional ground truth text file")
    args = parser.parse_args()

    image_path = Path(args.image).resolve()
    if not image_path.is_file():
        sys.exit(f"Error: image not found: {args.image}")

    output_dir = Path(args.output_dir).resolve()
    # Reject if inside repository
    repo_root = Path(__file__).resolve().parents[1]
    try:
        output_dir.relative_to(repo_root)
        sys.exit(f"Error: output-dir must be outside repository: {args.output_dir}")
    except ValueError:
        pass  # OK, outside repo

    if output_dir.is_symlink():
        sys.exit("Error: output-dir must not be a symlink")

    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"Image: {image_path}")
    print(f"Output: {output_dir}")
    print("Running benchmark...")

    request_id = uuid.uuid4().hex[:12]
    request = {
        "protocol_version": BENCHMARK_VERSION,
        "request_id": request_id,
        "image_path": str(image_path),
        "rotations": [0, 90, 180, 270],
        "preprocess_profiles": PREPROCESS_PROFILES,
        "detection_profiles": DETECTION_PROFILES,
    }

    result = _run_benchmark(request)

    if not result.get("ok"):
        print(f"Benchmark failed: {result.get('error', {}).get('code', 'UNKNOWN')}")
        return

    # Save JSON
    json_path = output_dir / "benchmark-result.json"
    json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"JSON: {json_path}")

    # Save Markdown
    md_path = output_dir / "benchmark-summary.md"
    md_path.write_text(_build_markdown(result), encoding="utf-8")
    print(f"Markdown: {md_path}")

    # Save HTML
    html_path = output_dir / "benchmark-report.html"
    html_path.write_text(_build_html(result, str(image_path)), encoding="utf-8")
    print(f"HTML: {html_path}")

    # Summary
    print(f"\nProvisional best rotation: {result.get('provisional_best_rotation', '-')}°")
    print(f"Provisional best preprocess: {result.get('provisional_best_preprocess', '-')}")
    print(f"Provisional best detection: {result.get('provisional_best_detection', '-')}")
    print(f"Total inferences: {result.get('inference_count', 0)}")
    print(f"Total time: {result.get('total_elapsed_ms', 0):.0f}ms")


if __name__ == "__main__":
    main()
