"""Local semantic ROI annotator — loopback-only Web UI.

Usage:
  python -m betguard.vision.roi_annotator --image <path> [--output <path>]

Listens ONLY on 127.0.0.1. No external CDN / network dependencies.
All semantics parsing happens server-side via production Closed Set V2.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .roi_annotations import ROI_SCHEMA_VERSION
from .semantic_roi import (
    SEMANTIC_SCHEMA_VERSION,
    SemanticRoiAnnotationSet,
    SemanticRoiGroup,
    VERIFICATION_NEEDS_REVIEW,
    VERIFICATION_VERIFIED,
    atomic_save,
    can_verify_group,
    image_sha256,
    image_size,
    parse_semantics,
    validate_annotation_set,
)

HOST = "127.0.0.1"
PORT = 8877

PAGE = r"""<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8">
<title>語意 ROI 標註工具</title>
<style>
body{font-family:system-ui,sans-serif;margin:16px;color:#1e293b}
#canvas-wrap{position:relative;display:inline-block;border:1px solid #cbd5e1;overflow:hidden;max-width:90vw}
#canvas{display:block;background:#fff;cursor:crosshair}
.roi-rect{position:absolute;border:2px solid #2563eb;box-sizing:border-box}
.roi-rect.selected{outline:2px solid #dc2626}
.roi-label{position:absolute;background:#2563eb;color:#fff;font-size:10px;padding:0 4px;line-height:14px;white-space:nowrap}
#panel{max-width:520px;width:100%}
label{display:block;font-size:12px;color:#64748b;margin-top:8px}
input[type=text],textarea{width:100%;box-sizing:border-box;font-size:13px}
button{margin-top:8px;padding:6px 12px;cursor:pointer}
#semantics{background:#f8fafc;border:1px solid #e2e8f0;padding:8px;font-size:12px;margin-top:8px;white-space:pre-wrap}
#issues{color:#b91c1c}
.badge{font-size:11px;padding:2px 6px;border-radius:4px;margin-left:6px}
.badge.verified{background:#dcfce7;color:#166534}
.badge.review{background:#fef3c7;color:#92400e}
.ref-line{cursor:pointer;font-size:12px;padding:2px 4px}
.ref-line:hover{background:#eff6ff}
</style></head><body>
<h2>語意 ROI 標註工具 <span id="status" style="font-size:13px"></span></h2>
<div style="display:flex;gap:16px;flex-wrap:wrap">
<div id="canvas-wrap">
  <img id="canvas" alt="sample image" draggable="false">
</div>
<div id="panel">
  <div>
    <button onclick="prevRoi()">◀ 上一個</button>
    <button onclick="nextRoi()">下一個 ▶</button>
    <span id="nav" style="font-size:13px"></span>
  </div>
  <label>Group ID</label>
  <input type="text" id="gid">
  <label>raw transcription（手動輸入）</label>
  <textarea id="raw" rows="3" oninput="onRawChange()"></textarea>
  <div id="semantics"></div>
  <label>applies_to_group_ids（僅 shared multiplier 且手動選擇後填，逗號分隔）</label>
  <input type="text" id="applies" placeholder="">
  <label>notes</label>
  <input type="text" id="notes">
  <div style="margin-top:10px">
    <button onclick="saveAll()">💾 儲存</button>
    <button onclick="markVerified()" id="verifyBtn">✅ 確認此 ROI</button>
    <button onclick="deleteRoi()">🗑 刪除此 ROI</button>
    <button onclick="newRoi()">＋ 新增 ROI</button>
  </div>
  <div id="verifyMsg" style="font-size:12px;margin-top:6px"></div>
  <h4 style="margin-top:16px">舊候選清單（僅供參考，點選填入草稿）</h4>
  <div id="refs"></div>
</div>
</div>
<script>
const canvas = document.getElementById('canvas');
const wrap = document.getElementById('canvas-wrap');
let image = null;         // {w, h, dataUrl}
let rois = [];            // [{id, bbox:[x,y,w,h], raw_transcription, applies_to_group_ids, verification_status, notes}]
let refs = [];
let sel = -1;
let scale = 1;
let drawing = null;       // {sx, sy, ex, ey}
let dragging = null;      // {mode:'move'|'resize', index, offX, offY}

function esc(s){return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');}

function load() {
  fetch('/api/state').then(r=>r.json()).then(s=>{
    image = s.image; rois = s.rois; refs = s.refs;
    canvas.src = image.src;
    canvas.onload = () => { fitScale(); render(); };
    canvas.onerror = () => {
      document.getElementById('canvas').alt = '圖片載入失敗：無法從 /api/image 取得原圖';
      document.getElementById('status').textContent = '⚠ 圖片載入失敗';
    };
    renderRefs(); updateStatus();
  }).catch(err => {
    document.getElementById('status').textContent = '⚠ 無法連接工具伺服器: ' + err;
  });
}

function fitScale(){ const maxW = Math.min(window.innerWidth*0.55, 900); scale = Math.min(maxW/image.w, 700/image.h); }

function render(){
  wrap.style.width = (image.w*scale)+'px';
  wrap.style.height = (image.h*scale)+'px';
  canvas.style.width = (image.w*scale)+'px';
  canvas.style.height = (image.h*scale)+'px';
  wrap.querySelectorAll('.roi-rect,.roi-label').forEach(e=>e.remove());
  rois.forEach((r,i)=>{
    const d = document.createElement('div');
    d.className = 'roi-rect' + (i===sel?' selected':'');
    d.style.left = (r.bbox[0]*scale)+'px';
    d.style.top = (r.bbox[1]*scale)+'px';
    d.style.width = (r.bbox[2]*scale)+'px';
    d.style.height = (r.bbox[3]*scale)+'px';
    d.dataset.i = i;
    wrap.appendChild(d);
    const l = document.createElement('div');
    l.className = 'roi-label';
    l.style.left = (r.bbox[0]*scale)+'px';
    l.style.top = Math.max(0, r.bbox[1]*scale-16)+'px';
    l.textContent = (i+1)+'. '+r.id;
    l.dataset.i = i;
    wrap.appendChild(l);
  });
  if (sel>=0) showForm();
  updateStatus();
}

function updateStatus(){
  const verified = rois.filter(r=>r.verification_status==='verified').length;
  const review = rois.length - verified;
  document.getElementById('status').textContent =
    `ROI: ${rois.length}（verified ${verified} / review ${review}）`;
  document.getElementById('nav').textContent = sel>=0 ? `${sel+1}/${rois.length}` : '';
}

function toImageCoords(e){
  const rect = canvas.getBoundingClientRect();
  const x = (e.clientX - rect.left) / scale;
  const y = (e.clientY - rect.top) / scale;
  return [Math.max(0, Math.min(image.w, x)), Math.max(0, Math.min(image.h, y))];
}

wrap.addEventListener('mousedown', e=>{
  if (e.target.classList && e.target.classList.contains('roi-label')) {
    const i = +e.target.dataset.i; sel = i; render();
    dragging = {mode:'move', index:i, offX:0, offY:0}; return;
  }
  const rectEl = e.target.classList && e.target.classList.contains('roi-rect') ? e.target : null;
  if (rectEl) {
    const i = +rectEl.dataset.i; sel = i; render();
    const [mx,my] = toImageCoords(e);
    dragging = {mode:'move', index:i, offX:mx-rois[i].bbox[0], offY:my-rois[i].bbox[1]};
    return;
  }
  if (!drawing) {
    const [mx,my] = toImageCoords(e);
    drawing = {sx:mx, sy:my, ex:mx, ey:my};
  }
});
window.addEventListener('mousemove', e=>{
  if (drawing) {
    const [mx,my] = toImageCoords(e);
    drawing.ex = mx; drawing.ey = my; renderDraw();
  } else if (dragging) {
    const [mx,my] = toImageCoords(e);
    const r = rois[dragging.index];
    if (dragging.mode==='move') {
      r.bbox[0] = Math.max(0, Math.min(image.w - r.bbox[2], mx - dragging.offX));
      r.bbox[1] = Math.max(0, Math.min(image.h - r.bbox[3], my - dragging.offY));
    }
    render();
  }
});
window.addEventListener('mouseup', e=>{
  if (drawing) {
    const [mx,my] = toImageCoords(e);
    drawing.ex = mx; drawing.ey = my;
    const x = Math.min(drawing.sx, drawing.ex), y = Math.min(drawing.sy, drawing.ey);
    const w = Math.abs(drawing.ex - drawing.sx), h = Math.abs(drawing.ey - drawing.sy);
    drawing = null;
    if (w >= 3 && h >= 3) {
      const gid = nextId();
      rois.push({id:gid, bbox:[Math.round(x),Math.round(y),Math.round(w),Math.round(h)],
                 raw_transcription:'', applies_to_group_ids:[], verification_status:'needs_human_review', notes:''});
      sel = rois.length-1;
    }
    render();
  }
  dragging = null;
});
function renderDraw(){
  wrap.querySelectorAll('.roi-rect,.roi-label').forEach(e=>e.remove());
  render();
  if (drawing) {
    const d = document.createElement('div');
    d.className = 'roi-rect selected';
    d.style.left = Math.min(drawing.sx,drawing.ex)*scale+'px';
    d.style.top = Math.min(drawing.sy,drawing.ey)*scale+'px';
    d.style.width = Math.abs(drawing.ex-drawing.sx)*scale+'px';
    d.style.height = Math.abs(drawing.ey-drawing.sy)*scale+'px';
    wrap.appendChild(d);
  }
}
function nextId(){
  let n = 1;
  const ids = new Set(rois.map(r=>r.id));
  while (ids.has('G'+String(n).padStart(3,'0'))) n++;
  return 'G'+String(n).padStart(3,'0');
}
function showForm(){
  const r = rois[sel];
  document.getElementById('gid').value = r.id;
  document.getElementById('raw').value = r.raw_transcription;
  document.getElementById('applies').value = (r.applies_to_group_ids||[]).join(',');
  document.getElementById('notes').value = r.notes||'';
  onRawChange();
}
function onRawChange(){
  const r = rois[sel]; if (!r) return;
  r.raw_transcription = document.getElementById('raw').value;
  fetch('/api/parse', {method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({raw: r.raw_transcription, applies: (document.getElementById('applies').value||'').split(',').map(s=>s.trim()).filter(Boolean)})})
    .then(res=>res.json()).then(d=>{
      const box = document.getElementById('semantics');
      box.innerHTML = 'normalized: '+esc(d.normalized)+'\n'+
        'layout: '+esc(d.semantics.layout)+' | scope: '+esc(d.semantics.scope)+'\n'+
        'numbers: '+esc(JSON.stringify(d.semantics.numbers))+'  multipliers: '+esc(JSON.stringify(d.semantics.multipliers))+'\n'+
        '<span id="issues">'+(d.issues.length?('⚠ '+esc(d.issues.join(', '))):'')+'</span>';
      r._validation = d;
    });
}
function collectCurrent(){
  const r = rois[sel]; if (!r) return null;
  r.id = document.getElementById('gid').value.trim();
  r.raw_transcription = document.getElementById('raw').value;
  r.applies_to_group_ids = (document.getElementById('applies').value||'').split(',').map(s=>s.trim()).filter(Boolean);
  r.notes = document.getElementById('notes').value;
  return r;
}
function saveAll(){
  if (sel>=0) collectCurrent();
  fetch('/api/save', {method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({rois})}).then(r=>r.json()).then(d=>{
    document.getElementById('verifyMsg').textContent = d.ok ? '已儲存 '+d.path : '儲存失敗: '+d.error;
  });
}
function markVerified(){
  const r = collectCurrent(); if (!r) return;
  fetch('/api/verify', {method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({roi:r})}).then(r=>r.json()).then(d=>{
    if (d.ok) { rois[sel].verification_status='verified'; document.getElementById('verifyMsg').textContent='已確認'; render(); }
    else document.getElementById('verifyMsg').textContent = '無法確認: '+d.blockers.join('; ');
  });
}
function deleteRoi(){ if (sel<0) return; rois.splice(sel,1); sel = Math.min(sel, rois.length-1); render(); }
function newRoi(){ rois.push({id:nextId(), bbox:[0,0,50,30], raw_transcription:'', applies_to_group_ids:[], verification_status:'needs_human_review', notes:''}); sel = rois.length-1; render(); }
function prevRoi(){ if (sel>0){ sel--; render(); } }
function nextRoi(){ if (sel<rois.length-1){ sel++; render(); } }
function renderRefs(){
  const box = document.getElementById('refs');
  box.innerHTML = refs.map((r,i)=>'<div class="ref-line" onclick="useRef('+i+')">'+(i+1)+'. '+esc(r.text)+'</div>').join('');
}
function useRef(i){
  if (sel<0) newRoi();
  rois[sel].raw_transcription = refs[i].text;
  document.getElementById('raw').value = refs[i].text;
  onRawChange();
}
load();
</script></body></html>"""


class AnnotatorHandler(BaseHTTPRequestHandler):
    annotator: "RoiAnnotatorServer" = None  # type: ignore[assignment]

    def log_message(self, fmt, *args):  # silence request logging
        pass

    def _send_json(self, obj: dict, status: int = 200):
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
        if parsed.path == "/api/state":
            self._send_json(self.annotator.state())
            return
        if parsed.path == "/api/image" and not parsed.query:
            # Serve ONLY the configured image path. Query strings and any
            # other path (including traversal attempts) are 404 — no
            # arbitrary file access.
            try:
                body = self.annotator.image_bytes()
            except OSError:
                self._send_json({"error": "image unavailable"}, 404)
                return
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self._send_json({"error": "not found"}, 404)

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/api/parse":
            data = self._read_json()
            raw = str(data.get("raw", ""))
            applies = [str(x) for x in data.get("applies", [])]
            validation = self.annotator.parse(raw, applies)
            self._send_json(validation)
            return
        if parsed.path == "/api/save":
            data = self._read_json()
            ok, msg = self.annotator.save_rois(data.get("rois", []))
            self._send_json({"ok": ok, "error": msg} if not ok else {"ok": True, "path": msg})
            return
        if parsed.path == "/api/verify":
            data = self._read_json()
            roi = data.get("roi", {})
            ok, blockers = self.annotator.verify(roi)
            self._send_json({"ok": ok, "blockers": blockers})
            return
        self._send_json({"error": "not found"}, 404)


class RoiAnnotatorServer:
    """Holds image + annotation state; all parsing is server-side."""

    def __init__(self, image_path: str, output_path: str, ref_paths: list[str] | None = None):
        self.image_path = image_path
        self.output_path = output_path
        self.image_w, self.image_h = image_size(image_path)
        self.image_sha = image_sha256(image_path)
        self.refs: list[dict[str, Any]] = []
        for ref_path in ref_paths or []:
            if os.path.isfile(ref_path):
                for i, line in enumerate(
                    open(ref_path, encoding="utf-8-sig").read().splitlines(), start=1
                ):
                    if line.strip():
                        self.refs.append({"line": i, "text": line.strip()})

        self._groups: list[SemanticRoiGroup] = []
        if os.path.isfile(output_path):
            try:
                ann = SemanticRoiAnnotationSet.from_dict(
                    json.load(open(output_path, encoding="utf-8"))
                )
                self._groups = ann.groups
            except Exception:
                self._groups = []

    def state(self) -> dict[str, Any]:
        # Image is served via /api/image (loopback, fixed path only) — never
        # embedded as a base64 data URL (large images break in the browser).
        return {
            "image": {"w": self.image_w, "h": self.image_h, "src": "/api/image"},
            "rois": [g.to_dict() for g in self._groups],
            "refs": self.refs,
        }

    def image_bytes(self) -> bytes:
        with open(self.image_path, "rb") as f:
            return f.read()

    def parse(self, raw: str, applies: list[str]) -> dict[str, Any]:
        validation = parse_semantics(raw, region_bound=bool(applies))
        sem = validation.get("semantics") or {
            "layout": "", "scope": "current_group", "numbers": [], "multipliers": [],
        }
        return {
            "normalized": raw.strip(),
            "issues": validation.get("issues", []),
            "semantics": {
                "layout": sem.get("layout", ""),
                "scope": sem.get("scope", "current_group"),
                "numbers": sem.get("numbers", []),
                "multipliers": sem.get("multipliers", []),
                "needs_human_confirmation": sem.get("needs_human_confirmation", True),
            },
        }

    def save_rois(self, rois: list[dict[str, Any]]) -> tuple[bool, str]:
        try:
            groups = [SemanticRoiGroup.from_dict(r) for r in rois]
            ann = SemanticRoiAnnotationSet(
                schema_version=SEMANTIC_SCHEMA_VERSION,
                image=self.image_path,
                groups=groups,
            )
            atomic_save(ann, self.output_path)
            self._groups = groups
            return True, self.output_path
        except Exception as exc:
            return False, str(exc)

    def verify(self, roi: dict[str, Any]) -> tuple[bool, list[str]]:
        group = SemanticRoiGroup.from_dict(roi)
        all_ids = [g.id for g in self._groups] + ([group.id] if group.id not in [g.id for g in self._groups] else [])
        blockers = can_verify_group(group, all_group_ids=all_ids, img_w=self.image_w, img_h=self.image_h)
        if blockers:
            return False, blockers
        group.verification_status = VERIFICATION_VERIFIED
        # persist the verified group back into the current session
        for i, g in enumerate(self._groups):
            if g.id == group.id:
                self._groups[i] = group
                break
        else:
            self._groups.append(group)
        return True, []


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Semantic ROI annotator (loopback only)")
    parser.add_argument("--image", required=True, help="original image path")
    parser.add_argument("--output", default="", help="output annotation JSON path")
    parser.add_argument("--ref", action="append", default=[], help="reference text files (old candidates)")
    parser.add_argument("--port", type=int, default=PORT)
    args = parser.parse_args(argv)

    if not os.path.isfile(args.image):
        print(f"Error: image not found: {args.image}", file=sys.stderr)
        return 1

    output = args.output or os.path.join(
        os.path.dirname(args.image), "roi", "sample-001-semantic-annotations.json"
    )

    server = RoiAnnotatorServer(args.image, output, ref_paths=args.ref)
    AnnotatorHandler.annotator = server

    httpd = ThreadingHTTPServer((HOST, args.port), AnnotatorHandler)
    print(f"語意 ROI 標註工具：http://{HOST}:{args.port}/")
    print(f"圖片：{args.image}（{server.image_w}×{server.image_h}，SHA-256 {server.image_sha[:16]}…）")
    print(f"輸出：{output}")
    print("僅監聽本機。Ctrl+C 停止。")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
