"""Local Web UI: bet slip photo → vision LLM → raw transcription →
deterministic rules → "號碼組合 玩法倍率" output (loopback only).

Mirrors the reference site's UX but with betguard safety:
  - all results stay PENDING_HUMAN_CONFIRMATION
  - auto_submit / auto_confirm / webfill always False
  - UNKNOWN tokens are surfaced, never guessed
  - loopback-only, no CDN, no external network beyond the chosen LLM API

Usage:
  $env:GEMINI_API_KEY = "AIza..."
  PYTHONPATH=src .venv/Scripts/python.exe -X utf8 -m betguard.vision.note_ui --port 8878
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .closed_set import validate_line
from .semantic_roi import parse_column_matrix, parse_semantics
from .providers.gemini_paid import (
    GeminiPaidVisionProvider,
    has_api_key as gemini_has_key,
)
from .providers.openai_paid import (
    OpenAIPaidVisionProvider,
    has_api_key as openai_has_key,
)
from .contracts import RecognitionRequest

HOST = "127.0.0.1"
PORT = 8878

# Six reference-site rules, implemented by the deterministic parser:
RULES = [
    "號碼必須是明確兩位數，範圍 01 到 39。",
    "只寫 X1 且該組有 2 個號碼時，會輸出成 二X1。",
    "倍率單獨成行時，會套用到上方連續號碼列。",
    "同一組有多個倍率時，會展開成多行。",
    "13X24X8尾 二三X1 會展開為 13/24/08 18 28 38 二三X1。",
    "矩陣格式會嘗試依直欄讀取，並用 / 分隔。",
]

PAGE = r"""<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8">
<title>下牌 LLM 辨識整理工具（本機版）</title>
<style>
body{font-family:system-ui,sans-serif;margin:16px;color:#1e293b;max-width:900px}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:16px}
@media(max-width:800px){.grid{grid-template-columns:1fr}}
button{margin-top:8px;padding:6px 14px;cursor:pointer}
textarea{width:100%;box-sizing:border-box;min-height:180px;font-size:13px;font-family:monospace}
#imgprev{max-width:100%;max-height:420px;border:1px solid #cbd5e1;display:none}
#out{white-space:pre-wrap;background:#f8fafc;border:1px solid #e2e8f0;padding:10px;font-size:13px;min-height:80px}
#status{font-size:12px;color:#64748b;margin-top:6px}
.rules{font-size:12px;color:#475569;background:#f1f5f9;padding:8px;border-radius:6px}
.badge{display:inline-block;font-size:11px;padding:2px 8px;border-radius:4px;margin-left:6px}
.badge.on{background:#dcfce7;color:#166534}
.badge.off{background:#fef3c7;color:#92400e}
</style></head><body>
<h2>下牌 LLM 辨識整理工具 <span style="font-size:13px">（本機版 · 僅 127.0.0.1）</span></h2>
<div class="grid">
  <div>
    <h3>圖片</h3>
    <input type="file" id="file" accept="image/*">
    <br><img id="imgprev" alt="preview">
    <div id="status"></div>
    <h3>LLM 供應商</h3>
    <select id="provider">
      <option value="gemini">Gemini (gemini-2.5-flash)</option>
      <option value="openai">OpenAI</option>
    </select>
    <div id="provstate"></div>
    <br><button onclick="recognize()">LLM 辨識圖片</button>
    <h3>辨識文字 / 人工校正</h3>
    <textarea id="raw" placeholder="辨識結果會出現在這裡。你可以先人工修正，再按「整理」"></textarea>
    <br><button onclick="arrange()">整理</button>
  </div>
  <div>
    <h3>整理結果（號碼組合 玩法倍率）</h3>
    <div id="out"></div>
    <button onclick="copyOut()">複製</button>
    <button onclick="downloadOut()">下載 .txt</button>
    <h3>整理規則</h3>
    <div class="rules" id="rules"></div>
  </div>
</div>
<script>
const RULES = RULES_JSON;
document.getElementById('rules').innerHTML = RULES.map(r=>'• '+r).join('<br>');
let imgData = null;

document.getElementById('file').addEventListener('change', e=>{
  const f = e.target.files[0];
  if (!f) return;
  const reader = new FileReader();
  reader.onload = ev => {
    imgData = ev.target.result;
    document.getElementById('imgprev').src = imgData;
    document.getElementById('imgprev').style.display = 'block';
    document.getElementById('status').textContent = '已載入: ' + f.name + ' (' + Math.round(f.size/1024) + ' KB)';
  };
  reader.readAsDataURL(f);
});

function provState(){
  fetch('/api/provstate').then(r=>r.json()).then(d=>{
    const b = document.getElementById('provstate');
    b.innerHTML = '';
    for (const [k,v] of Object.entries(d)) {
      const span = document.createElement('span');
      span.className = 'badge ' + (v ? 'on' : 'off');
      span.textContent = k + (v ? ' ✓' : ' ✗');
      b.appendChild(span);
    }
  });
}
provState();

async function recognize(){
  if (!imgData) { alert('請先選擇圖片'); return; }
  const prov = document.getElementById('provider').value;
  document.getElementById('status').textContent = '辨識中…';
  const res = await fetch('/api/recognize', {method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({image: imgData, provider: prov})});
  const d = await res.json();
  if (d.error) { document.getElementById('status').textContent = '❌ ' + d.error; return; }
  document.getElementById('raw').value = d.raw_text;
  document.getElementById('status').textContent = '✅ 辨識完成（' + d.provider + ' · ' + d.latency_ms + 'ms）· 請人工確認';
  arrange();
}

async function arrange(){
  const raw = document.getElementById('raw').value;
  const res = await fetch('/api/arrange', {method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({raw})});
  const d = await res.json();
  document.getElementById('out').textContent = d.output;
  document.getElementById('status').textContent = '✅ 已整理 · ' + d.notes;
}

function copyOut(){
  const t = document.getElementById('out').textContent;
  navigator.clipboard.writeText(t).then(()=>alert('已複製'));
}
function downloadOut(){
  const t = document.getElementById('out').textContent;
  const blob = new Blob([t], {type:'text/plain;charset=utf-8'});
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = 'bet-note.txt';
  a.click();
}
</script></body></html>"""

PAGE = PAGE.replace("RULES_JSON", json.dumps(RULES, ensure_ascii=False))


class NoteUIHandler(BaseHTTPRequestHandler):
    app: "NoteUIApp" = None  # type: ignore[assignment]

    def log_message(self, fmt, *args):
        pass

    def _send_json(self, obj, status=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/":
            body = PAGE.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if parsed.path == "/api/provstate":
            self._send_json(self.app.provstate())
            return
        self._send_json({"error": "not found"}, 404)

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/api/recognize":
            data = self._read_json()
            self._send_json(self.app.recognize(data))
            return
        if parsed.path == "/api/arrange":
            data = self._read_json()
            self._send_json(self.app.arrange(str(data.get("raw", ""))))
            return
        self._send_json({"error": "not found"}, 404)


class NoteUIApp:
    def __init__(self, workdir: Path):
        self.workdir = workdir
        self._gemini = GeminiPaidVisionProvider()
        self._openai = OpenAIPaidVisionProvider()

    def provstate(self) -> dict:
        return {
            "Gemini": gemini_has_key(),
            "OpenAI": openai_has_key(),
        }

    def recognize(self, data: dict) -> dict:
        provider_name = str(data.get("provider", "gemini"))
        image_data = str(data.get("image", ""))
        if not image_data.startswith("data:"):
            return {"error": "image must be a data URL"}

        header, b64 = image_data.split(",", 1)
        mime = header.split(";")[0].split(":")[1] if ":" in header else "image/png"
        ext = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}.get(mime, ".png")
        path = self.workdir / f"upload{ext}"
        path.write_bytes(base64.b64decode(b64))

        request = RecognitionRequest(
            request_id="note-ui",
            image_id=path.name,
            image_path=str(path),
            metadata={"document_mode": "auto"},
        )
        if provider_name == "openai":
            result = self._openai.recognize(request)
            provider_label = "openai-paid"
        else:
            result = self._gemini.recognize(request)
            provider_label = "gemini-paid"

        if result.provider_error:
            return {"error": f"{result.provider_error.code}: {result.provider_error.message}"}
        raw_lines = [line.text for line in result.lines]
        return {
            "provider": provider_label,
            "raw_text": "\n".join(raw_lines),
            "latency_ms": int(result.latency_ms or 0),
        }

    def arrange(self, raw: str) -> dict:
        """Deterministic safe arrange. Never invents rates or stars.

        Safety rules enforced here:
          - a number line without an explicit multiplier is MISSING_RATE
            (never padded to 二×1/四×1 by count)
          - ×1 is used only when the line itself carries ×1, or a shared
            instruction was successfully bound
          - shared instructions bind to the immediately preceding number
            block; unresolved shared blocks the result
          - matrix blocks keep their column structure (never flattened)
        """
        lines = [l.strip() for l in raw.splitlines() if l.strip()]
        if not lines:
            return self._result([], notes="空輸入", problems=["EMPTY_INPUT"])

        blocks = self._split_blocks(lines)
        out_lines: list[str] = []
        problems: list[str] = []
        pending_groups: list[str] = []   # number groups awaiting a shared bind
        matrix_groups: list[list[str]] = []  # matrix columns awaiting bind
        matrix_kept = False

        for kind, payload in blocks:
            if kind == "shared":
                shared_sem = payload
                mults = shared_sem.get("multipliers", [])
                if not mults:
                    problems.append("SHARED_EMPTY_MULTIPLIERS")
                    continue
                if not pending_groups and not matrix_groups:
                    problems.append("SHARED_WITHOUT_TARGET: 各= 指令上方沒有號碼群組")
                    continue
                # bind: every pending group gets every category×value
                for group in pending_groups:
                    for m in mults:
                        cat = m.get("category") or "?"
                        out_lines.append(f"{group} {cat}×{m['value_text']}")
                for group in matrix_groups:
                    for m in mults:
                        cat = m.get("category") or "?"
                        out_lines.append(f"{' '.join(group)} {cat}×{m['value_text']}")
                pending_groups = []
                matrix_groups = []
                continue

            # kind == "numbers": block of number lines
            if all(self._is_pure_number_line(t) for t in payload) and len(payload) > 1:
                matrix = parse_column_matrix(payload)
                if matrix is not None:
                    matrix_kept = True
                    # each column is one group (keeps column structure)
                    matrix_groups.extend(matrix)
                    continue
                if any("×" in t.replace(" ", "") for t in payload):
                    # ×-separated rows that failed matrix parse → structure lost
                    problems.append("MATRIX_STRUCTURE_UNKNOWN: 多行數字區塊無法判定欄位結構")
                    for t in payload:
                        self._parse_number_line(t, out_lines, problems, pending_groups)
                    continue
                # plain rows: each is an independent number line
                for t in payload:
                    self._parse_number_line(t, out_lines, problems, pending_groups)
                continue

            # single line or mixed block: normal rows
            for t in payload:
                self._parse_number_line(t, out_lines, problems, pending_groups)

        # post-checks: matrix groups with no shared bind keep their column
        # structure in the output but are NOT bet-ready (MISSING_RATE)
        if matrix_groups:
            out_lines.append(" / ".join(" ".join(col) for col in matrix_groups))
            for col in matrix_groups:
                problems.append(f"MISSING_RATE: {' '.join(col)}")
            matrix_groups = []
        # every pending group that never got a rate is MISSING_RATE
        # (never padded to 四×1 / 五×1 by count)
        for group in pending_groups:
            problems.append(f"MISSING_RATE: {group}")
        pending_groups = []

        # post-checks
        parse_completed = not problems and not any("?" in l for l in out_lines)
        return self._result(
            out_lines,
            notes=self._notes(problems, matrix_kept),
            problems=problems,
            parse_completed=parse_completed,
        )

    def _split_blocks(self, lines: list[str]) -> list[tuple[str, object]]:
        """Split lines into ('numbers', [...]) and ('shared', sem) blocks."""
        blocks: list[tuple[str, object]] = []
        cur: list[str] = []
        for t in lines:
            v = parse_semantics(t, region_bound=False)
            sem = v.get("semantics")
            if sem and sem.get("layout") == "shared_multiplier":
                if cur:
                    blocks.append(("numbers", cur))
                    cur = []
                blocks.append(("shared", sem))
            else:
                cur.append(t)
        if cur:
            blocks.append(("numbers", cur))
        return blocks

    def _is_pure_number_line(self, t: str) -> bool:
        """True when a line is ONLY numbers (no multiplier, no parens)."""
        v = parse_semantics(t, region_bound=False)
        sem = v.get("semantics")
        if not sem:
            return False
        return bool(sem.get("numbers")) and not sem.get("multipliers")

    def _parse_number_line(self, t: str, out_lines: list[str], problems: list[str],
                           pending_groups: list[str]) -> str:
        """Parse one number line. Lines with an explicit multiplier emit
        their canonical row; lines WITHOUT a multiplier go to pending_groups
        (they may still be bound by a later shared instruction; otherwise
        they become MISSING_RATE at the end). Never invents rates or stars."""
        v = parse_semantics(t, region_bound=False)
        sem = v.get("semantics")
        if not sem:
            problems.append(f"UNPARSEABLE: {t}")
            return ""
        numbers = sem.get("numbers", [])
        if not numbers:
            problems.append(f"UNPARSEABLE: {t}")
            return ""
        mults = sem.get("multipliers", [])
        if not mults:
            # rule 1/2: no explicit multiplier → wait for a shared bind;
            # if none comes, this becomes MISSING_RATE (never 四×1/五×1)
            pending_groups.append(" ".join(numbers))
            return ""
        line_text = ""
        for m in mults:
            cat = m.get("category")
            if cat is None:
                # rule 1: ×1 is usable ONLY when the line explicitly carries
                # ×1 (a bare multiplier with no category). Deriving the star
                # from count is allowed only then.
                if m.get("value_text") == "1":
                    cat = {2: "二", 3: "三", 4: "四", 5: "五"}.get(len(numbers), "")
                if not cat:
                    problems.append(f"UNKNOWN_STAR: {t}")
                    cat = "?"
            line_text = " ".join(numbers) + f" {cat}×{m['value_text']}"
            out_lines.append(line_text)
        return line_text

    def _notes(self, problems: list[str], matrix_kept: bool) -> str:
        parts = []
        if matrix_kept:
            parts.append("矩陣直欄讀取（欄位結構保留）")
        parts.append(f"{len(problems)} 個問題" if problems else "解析完成")
        return " · ".join(parts)

    def _result(self, out_lines: list[str], *, notes: str, problems: list[str],
                parse_completed: bool = False) -> dict:
        has_shared_marker = any("[shared]" in l for l in out_lines)
        if has_shared_marker:
            problems.append("SHARED_MARKER_LEAK")
        pending_human = bool(problems) or not parse_completed
        return {
            "output": "\n".join(out_lines) if out_lines else "（無輸出）",
            "notes": notes,
            "problems": problems,
            "parse_completed": parse_completed,
            "assist_fill_allowed": False,
            "status": "PENDING_HUMAN_CONFIRMATION" if pending_human else "OK",
        }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Local bet note LLM UI (loopback)")
    parser.add_argument("--port", type=int, default=PORT)
    parser.add_argument("--workdir", default=str(Path.home() / "AppData/Local/Temp/betnote"))
    args = parser.parse_args(argv)

    workdir = Path(args.workdir)
    workdir.mkdir(parents=True, exist_ok=True)

    app = NoteUIApp(workdir)
    NoteUIHandler.app = app
    httpd = ThreadingHTTPServer((HOST, args.port), NoteUIHandler)
    print(f"下牌 LLM 辨識整理工具（本機版）：http://{HOST}:{args.port}/")
    print(f"Gemini: {'已啟用' if gemini_has_key() else '未設定 GEMINI_API_KEY'} | "
          f"OpenAI: {'已啟用' if openai_has_key() else '未設定 OPENAI_API_KEY'}")
    print("僅監聽本機。Ctrl+C 停止。")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
