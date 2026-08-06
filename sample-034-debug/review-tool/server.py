"""M1-A Ground Truth Review Tool - local web server (Python stdlib only).

Reads:  C:\\BetguardOCRDataset\\prelabels\\  (model output, read-only)
        C:\\BetguardOCRDataset\\raw\\          (images, read-only)
Writes: C:\\BetguardOCRDataset\\ground-truth-draft\\  ONLY (atomic + .bak)
Never writes: prelabels\\, ground-truth\\, manifest.jsonl
"""
from __future__ import annotations

import json
import os
import re
import shutil
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(os.environ["BETGUARD_DATASET"])
TOOL = Path(__file__).resolve().parent
RAW = ROOT / "raw"
PRELABELS = ROOT / "prelabels"
DRAFT = ROOT / "ground-truth-draft"
SAMPLE_IDS = [f"sample-{i:03d}" for i in range(2, 35)]
GT_SCHEMA = "539-semantic-gt-v1"
HOST, PORT = "127.0.0.1", 8765


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def draft_path(sid: str) -> Path:
    return DRAFT / f"{sid}.json"


def prelabel_path(sid: str) -> Path:
    return PRELABELS / f"{sid}.json"


def compute_progress(d: dict) -> dict:
    lines = d.get("lines", [])
    total = len(lines)
    confirmed = sum(1 for l in lines if l.get("review_action") == "confirmed")
    corrected = sum(1 for l in lines if l.get("review_action") == "corrected")
    return {
        "confirmed_lines": confirmed,
        "corrected_lines": corrected,
        "unresolved_lines": max(0, total - confirmed - corrected),
        "total_lines": total,
    }


def normalize_lines(d: dict) -> None:
    order_map = {r.get("region_id"): r.get("order", 99) for r in d.get("regions", [])}
    lines = d.get("lines", [])
    lines.sort(key=lambda l: (order_map.get(l.get("region_id"), 99), l.get("order", 0)))
    for l in lines:
        groups = l.get("number_groups")
        if isinstance(groups, list):
            cleaned = []
            for g in groups:
                if isinstance(g, list):
                    cells = [str(x).strip() for x in g if str(x).strip() != ""]
                else:
                    cells = [str(g).strip()] if str(g).strip() != "" else []
                cleaned.append(cells)
            l["number_groups"] = cleaned
    grouped: dict[str, list[str]] = {}
    for l in lines:
        grouped.setdefault(l.get("region_id") or "R-unknown", []).append(l.get("line_id"))
    for r in d.get("regions", []):
            r["line_ids"] = grouped.get(r.get("region_id"), [])


ROI_ZH = {"2": "二", "3": "三", "4": "四"}


def _roi_digits(pm: dict) -> list[str]:
    out: list[str] = []
    for key in ("roi_upper_digits", "upper_digits", "roi_lower_digits", "lower_digits", "other_visible_digits"):
        out.extend(
            str(d).strip() for d in (pm.get(key) or [])
            if str(d).strip() in ROI_ZH
        )
    if not out:
        out = [
            str(d).strip() for d in (pm.get("roi_categories") or pm.get("categories") or [])
            if str(d).strip() in ROI_ZH
        ]
    seen: set[str] = set()
    return [d for d in out if not (d in seen or seen.add(d))]


def _apply_roi_to_line(line: dict, pm: dict) -> dict:
    digits = _roi_digits(pm)
    mult_raw = str(pm.get("roi_multiplier") if "roi_multiplier" in pm else (pm.get("multiplier") or "")).strip()
    missing: list[str] = []
    if not digits:
        missing.append("玩法數字")
    mult = mult_raw if re.fullmatch(r"\d+(?:\.\d+)?", mult_raw) else None
    if mult is None:
        missing.append("倍率")
    if missing:
        return {"ok": False, "missing": missing, "error": "ROI 資料不完整"}
    zh = "".join(ROI_ZH[d] for d in digits)
    full = f"{zh}X{mult}"
    applied = (line.get("play_mark") or {}).get("applied_roi")
    if applied and line.get("correction_source") == "roi" and line.get("multiplier_text") == full:
        # Sync working text for records applied before this fix; never touch
        # model_raw_text (immutable first-pass output).
        if line.get("human_raw_text") and line.get("raw_text") != line.get("human_raw_text"):
            line["raw_text"] = line["human_raw_text"]
        return {"ok": True, "idempotent": True, "full": full, "digits": digits}
    groups = line.get("number_groups") or []
    if line.get("layout_hint") == "column_bet":
        human = " / ".join(" ".join(g or []) for g in groups) + " " + full
    else:
        human = " ".join(x for g in groups for x in (g or [])) + " " + full
    first_pass_raw = line.get("model_raw_text") or line.get("raw_text") or ""
    line["human_raw_text"] = human
    line["multiplier_text"] = full
    line["correction_source"] = "roi"
    line["human_edited"] = True
    line["play_mark"] = dict(line.get("play_mark") or {})
    line["play_mark"]["first_pass_raw_text"] = first_pass_raw
    line["play_mark"]["applied_roi"] = {
        "at": now_iso(), "full_text": full, "digits": digits, "correction_source": "roi",
    }
    line["raw_text"] = human
    line["review_action"] = "corrected"
    return {"ok": True, "idempotent": False, "full": full, "digits": digits, "human": human}


def _reapply_pipeline(line: dict) -> dict:
    import sys

    src = str(Path(__file__).resolve().parents[2] / "src")
    if src not in sys.path:
        sys.path.insert(0, src)
    from betguard.vision.pipeline import reapply_after_human_edit

    original = {
        "raw_text": line.get("raw_text") or "",
        "multiplier_text": line.get("multiplier_text"),
        "layout_hint": line.get("layout_hint"),
    }
    rec = reapply_after_human_edit(
        original,
        raw_text=line.get("human_raw_text") or line.get("raw_text") or "",
        multiplier=line.get("multiplier_text"),
        layout_hint=line.get("layout_hint"),
        number_groups=line.get("number_groups") or None,
    )
    return {
        "decision": rec["decision"],
        "closed_set": rec["closed_set"],
        "semantic": rec["semantic"],
        "checks": rec["checks"],
        "expected_combination_count": rec.get("expected_combination_count"),
        "unit": rec.get("unit"),
        "money": rec.get("money"),
        "parse_error": rec.get("parse_error"),
    }


EDIT_REVALIDATE_FIELDS = {
    "human_raw_text", "multiplier_text", "number_groups", "layout_hint",
    "play_text", "play_type",
}


def _revalidate_line(line: dict) -> dict:
    """Human edit -> re-run the whole pipeline; fail-closed on structured/text
    divergence (STRUCTURED_TEXT_DIVERGENT)."""
    line["pipeline_review"] = _reapply_pipeline(line)
    cs = line["pipeline_review"].get("closed_set") or {}
    sem = line["pipeline_review"].get("semantic") or {}

    def _flatten(value):
        out = []
        if isinstance(value, (list, tuple)):
            for x in value:
                out.extend(_flatten(x))
        elif value is not None:
            out.append(str(value))
        return out

    cs_nums = sorted(set(_flatten(cs.get("semantics_numbers"))))
    sem_nums = sorted(set(_flatten(sem.get("numbers"))))
    if cs_nums != sem_nums:
        line.setdefault("warnings", [])
        if "STRUCTURED_TEXT_DIVERGENT" not in line["warnings"]:
            line["warnings"].append("STRUCTURED_TEXT_DIVERGENT")
        line["uncertain"] = True
        line["uncertain_reason"] = "STRUCTURED_TEXT_DIVERGENT"
    return line


def _complete_validation(draft: dict) -> list[dict]:
    """Fail-closed checks before marking a sample reviewed."""
    issues: list[dict] = []
    for line in draft.get("lines", []):
        lid = line.get("line_id")
        if line.get("review_action") != "confirmed":
            issues.append({
                "line_id": lid, "code": "LINE_NOT_CONFIRMED",
                "zh": "行尚未人工確認（corrected 需再次確認）",
            })
        groups = line.get("number_groups") or []
        flat = [n for g in groups for n in (g if isinstance(g, list) else [g])]
        if not flat:
            issues.append({"line_id": lid, "code": "EMPTY_NUMBER_GROUPS", "zh": "號碼組合為空"})
        if line.get("layout_hint") == "column_bet":
            for ci, col in enumerate(groups):
                if not col:
                    issues.append({"line_id": lid, "code": "EMPTY_COLUMN", "zh": f"第 {ci + 1} 欄為空"})
                for n in col:
                    if not re.fullmatch(r"\d{1,2}", str(n)) or not (1 <= int(str(n)) <= 49):
                        issues.append({"line_id": lid, "code": "INVALID_NUMBER", "zh": f"非法號碼 {n}"})
        if not line.get("pipeline_review"):
            issues.append({
                "line_id": lid, "code": "PIPELINE_REVIEW_MISSING",
                "zh": "缺少 pipeline_review（需重新驗證）",
            })
        if line.get("uncertain_reason") == "unresolved_region":
            issues.append({"line_id": lid, "code": "UNRESOLVED_REGION", "zh": "作用域未決，不得完成"})
    for rule in draft.get("shared_multiplier_rules", []):
        if not rule.get("scope"):
            issues.append({
                "line_id": rule.get("line_id") or rule.get("region_id"),
                "code": "SHARED_SCOPE_MISSING", "zh": "共用倍率缺少合法 scope",
            })
        if not rule.get("applies_to_line_ids"):
            issues.append({
                "line_id": rule.get("line_id") or rule.get("region_id"),
                "code": "SHARED_APPLIES_MISSING", "zh": "共用倍率缺少 applies_to_line_ids",
            })
    return issues


class RevisionConflict(Exception):
    pass


def save_draft(
    sid: str,
    data: dict,
    edits: list | None = None,
    expected_revision: int | None = None,
) -> dict:
    if sid not in SAMPLE_IDS:
        raise ValueError("sample not editable")
    target = draft_path(sid)
    current = load_json(target) or {}
    cur_rev = int(current.get("revision") or 0)
    if expected_revision is not None and cur_rev != expected_revision:
        raise RevisionConflict(cur_rev)
    if target.exists():
        shutil.copy2(target, target.with_name(target.name + ".bak"))
    data["gt_schema_version"] = GT_SCHEMA
    data["sample_id"] = sid
    data["revision"] = cur_rev + 1
    if edits:
        data.setdefault("human_edits", []).extend(edits)
    data["last_saved_at"] = now_iso()
    data["review_progress"] = compute_progress(data)
    normalize_lines(data)
    tmp = target.with_name(f".{target.name}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, target)  # atomic
    return data


def ensure_draft(sid: str) -> dict:
    """Load draft; upgrade to the working schema on first use (draft-only writes)."""
    pre = load_json(prelabel_path(sid))
    d = load_json(draft_path(sid)) or {}
    if d.get("sample_id") != sid:
        d = {"gt_schema_version": GT_SCHEMA, "sample_id": sid}
    changed = False
    if pre and "raw_model_output" not in d:
        d["raw_model_output"] = pre.get("raw_model_output")
        d["source"] = "model_prelabel_not_ground_truth"
        changed = True
    for key, default in (
        ("review_status", "in_progress"),
        ("human_edits", []),
        ("warnings", []),
    ):
        if key not in d:
            d[key] = default
            changed = True
    if "review_progress" not in d:
        d["review_progress"] = {
            "confirmed_lines": 0,
            "corrected_lines": 0,
            "unresolved_lines": 0,
            "total_lines": 0,
        }
        changed = True
    if "last_saved_at" not in d:
        d["last_saved_at"] = None
    if "regions" not in d:
        d["regions"] = []
        changed = True
    if "lines" not in d:
        d["lines"] = []
        changed = True
    if "shared_multiplier_rules" not in d:
        d["shared_multiplier_rules"] = []
        changed = True

    pre_lines = {l.get("line_id"): l for l in (pre or {}).get("lines", [])}
    for l in d["lines"]:
        pl = pre_lines.get(l.get("line_id"))
        if "model_raw_text" not in l and pl:
            l["model_raw_text"] = pl.get("raw_text")
            changed = True
        if "human_raw_text" not in l:
            l["human_raw_text"] = None
            changed = True
        if "review_action" not in l:
            l["review_action"] = "pending"
            changed = True
        if "human_added" not in l:
            l["human_added"] = False
            changed = True

    if not d["lines"] and pre:
        d["regions"] = pre.get("regions", [])
        d["lines"] = pre.get("lines", [])
        d["shared_multiplier_rules"] = pre.get("shared_multiplier_rules", [])
        d["warnings"] = pre.get("warnings", [])
        for l in d["lines"]:
            l.setdefault("model_raw_text", l.get("raw_text"))
            l.setdefault("human_raw_text", None)
            l.setdefault("review_action", "pending")
            l.setdefault("human_added", False)
        changed = True

    d["review_progress"] = compute_progress(d)
    if changed:
        save_draft(sid, d)
    return d


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def _send(self, code: int, body: bytes, ctype: str = "application/json; charset=utf-8"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, obj):
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"))

    def _read_body(self) -> dict:
        try:
            n = int(self.headers.get("Content-Length", 0))
        except ValueError:
            n = 0
        if n > 4 * 1024 * 1024:
            raise ValueError("payload too large")
        return json.loads(self.rfile.read(n).decode("utf-8")) if n else {}

    def do_GET(self):
        url = urlparse(self.path)
        p = url.path
        if p == "/" or p == "/index.html":
            self._send(200, (TOOL / "index.html").read_bytes(), "text/html; charset=utf-8")
        elif p == "/app.js":
            self._send(200, (TOOL / "app.js").read_bytes(), "text/javascript; charset=utf-8")
        elif p == "/roi_logic.js":
            self._send(200, (TOOL / "roi_logic.js").read_bytes(), "text/javascript; charset=utf-8")
        elif p == "/style.css":
            self._send(200, (TOOL / "style.css").read_bytes(), "text/css; charset=utf-8")
        elif p == "/api/overview":
            out = []
            for sid in SAMPLE_IDS:
                d = load_json(draft_path(sid)) or {}
                out.append(
                    {
                        "sample_id": sid,
                        "review_status": d.get("review_status", "not_started"),
                        "review_progress": d.get("review_progress"),
                        "last_saved_at": d.get("last_saved_at"),
                    }
                )
            self._json(200, out)
        elif p.startswith("/api/sample/"):
            sid = p.split("/")[-1]
            if sid not in SAMPLE_IDS:
                self._json(404, {"error": "sample not found"})
                return
            pre = load_json(prelabel_path(sid)) or {}
            draft = ensure_draft(sid)
            self._json(200, {"sample_id": sid, "prelabel": pre, "draft": draft})
        elif p.startswith("/image/"):
            sid = p.split("/")[-1]
            if sid not in SAMPLE_IDS:
                self._json(404, {"error": "image not found"})
                return
            img = RAW / (f"{sid}.png" if sid == "sample-001" else f"{sid}.jpg")
            if not img.exists():
                self._json(404, {"error": "image file missing"})
                return
            data = img.read_bytes()
            ctype = "image/png" if img.suffix.lower() == ".png" else "image/jpeg"
            self._send(200, data, ctype)
        elif p.startswith("/crop/"):
            name = p.split("/")[-1]
            crop_dir = (ROOT / "audit" / "play_mark_crops").resolve()
            crop_file = (crop_dir / name).resolve()
            if not str(crop_file).startswith(str(crop_dir) + os.sep) or not crop_file.exists():
                self._json(404, {"error": "crop not found"})
                return
            self._send(200, crop_file.read_bytes(), "image/png")
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self):
        url = urlparse(self.path)
        p = url.path
        try:
            body = self._read_body()
        except Exception as e:
            self._json(400, {"error": f"bad request body: {e}"})
            return
        if p.startswith("/api/sample/") and p.endswith("/save"):
            sid = p.split("/")[-2]
            if sid not in SAMPLE_IDS:
                self._json(404, {"error": "sample not editable"})
                return
            draft = body.get("draft")
            if not isinstance(draft, dict) or draft.get("sample_id") != sid:
                self._json(400, {"error": "invalid draft payload"})
                return
            try:
                rev = body.get("expected_revision")
                # Any edited line is re-run through the full pipeline server-side.
                edited_ids = {
                    e.get("line_id") for e in (body.get("edits") or [])
                    if any(f in EDIT_REVALIDATE_FIELDS for f in (e.get("fields") or []))
                }
                for line in draft.get("lines", []):
                    if line.get("line_id") in edited_ids:
                        _revalidate_line(line)
                saved = save_draft(
                    sid, draft, edits=body.get("edits"),
                    expected_revision=rev,
                )
            except RevisionConflict as e:
                self._json(409, {"error": "revision_mismatch", "current_revision": int(str(e))})
                return
            self._json(200, {
                "ok": True, "saved_at": saved["last_saved_at"],
                "progress": saved["review_progress"], "revision": saved.get("revision"),
            })
        elif p.startswith("/api/sample/") and p.endswith("/complete"):
            sid = p.split("/")[-2]
            if sid not in SAMPLE_IDS:
                self._json(404, {"error": "sample not editable"})
                return
            sent = body.get("draft")
            draft = (
                sent
                if isinstance(sent, dict) and sent.get("sample_id") == sid
                else ensure_draft(sid)
            )
            # Ensure every line carries an up-to-date pipeline review first.
            for line in draft.get("lines", []):
                if not line.get("pipeline_review"):
                    _revalidate_line(line)
            issues = _complete_validation(draft)
            if issues:
                self._json(409, {"error": "complete_validation_failed", "issues": issues})
                return
            draft["review_status"] = "reviewed"
            draft["reviewed_by"] = str(body.get("reviewer_name") or "local-user").strip()[:64]
            draft["reviewed_at"] = now_iso()
            try:
                saved = save_draft(
                    sid, draft, edits=body.get("edits"),
                    expected_revision=body.get("expected_revision"),
                )
            except RevisionConflict as e:
                self._json(409, {"error": "revision_mismatch", "current_revision": int(str(e))})
                return
            self._json(200, {
                "ok": True, "review_status": saved["review_status"],
                "reviewed_at": saved["reviewed_at"], "revision": saved.get("revision"),
            })
        elif p.startswith("/api/sample/") and p.endswith("/apply-roi"):
            sid = p.split("/")[-2]
            if sid not in SAMPLE_IDS:
                self._json(404, {"error": "sample not editable"})
                return
            line_id = str(body.get("line_id") or "")
            try:
                draft = load_json(draft_path(sid)) or {}
            except Exception:
                draft = {}
            line = next((l for l in draft.get("lines", []) if l.get("line_id") == line_id), None)
            if line is None:
                self._json(404, {"error": "line not found"})
                return
            # Trust only the server-side draft evidence; ignore client-supplied
            # play_mark to prevent forged ROI content.
            pm = line.get("play_mark") or {}
            result = _apply_roi_to_line(line, pm)
            if not result["ok"]:
                self._json(400, {"error": result.get("error"), "missing": result.get("missing")})
                return
            if not result["idempotent"]:
                _revalidate_line(line)
                edits = [{
                    "at": now_iso(), "line_id": line_id,
                    "fields": ["multiplier_text", "raw_text", "play_mark", "correction_source"],
                    "source": "roi",
                }]
                try:
                    save_draft(
                        sid, draft, edits=edits,
                        expected_revision=body.get("expected_revision"),
                    )
                except RevisionConflict as e:
                    self._json(409, {"error": "revision_mismatch", "current_revision": int(str(e))})
                    return
            self._json(200, {
                "ok": True, "idempotent": result["idempotent"], "line": line,
                "revision": draft.get("revision"),
            })
        elif p.startswith("/api/sample/") and p.endswith("/revalidate-line"):
            sid = p.split("/")[-2]
            if sid not in SAMPLE_IDS:
                self._json(404, {"error": "sample not editable"})
                return
            line_id = str(body.get("line_id") or "")
            draft = load_json(draft_path(sid)) or {}
            line = next((l for l in draft.get("lines", []) if l.get("line_id") == line_id), None)
            if line is None:
                self._json(404, {"error": "line not found"})
                return
            _revalidate_line(line)
            try:
                saved = save_draft(
                    sid, draft,
                    edits=[{"at": now_iso(), "line_id": line_id, "fields": ["pipeline_review"], "source": "revalidate"}],
                    expected_revision=body.get("expected_revision"),
                )
            except RevisionConflict as e:
                self._json(409, {"error": "revision_mismatch", "current_revision": int(str(e))})
                return
            self._json(200, {"ok": True, "line": line, "revision": saved.get("revision")})
        else:
            self._json(404, {"error": "not found"})


if __name__ == "__main__":
    DRAFT.mkdir(parents=True, exist_ok=True)
    print(f"GT Review Tool: http://{HOST}:{PORT}")
    print(f"review files: {DRAFT}")
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
