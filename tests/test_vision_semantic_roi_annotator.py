"""Tests for semantic ROI annotator: model, verification rules, safe save."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

from betguard.vision.semantic_roi import (
    SEMANTIC_SCHEMA_VERSION,
    SemanticRoiAnnotationSet,
    SemanticRoiGroup,
    VERIFICATION_NEEDS_REVIEW,
    VERIFICATION_VERIFIED,
    atomic_save,
    can_verify_group,
    image_sha256,
    parse_semantics,
    validate_annotation_set,
)


def _png(tmp_path: Path, w: int = 346, h: int = 477) -> Path:
    """Minimal valid-ish PNG header for size parsing (bytes only)."""
    import struct
    import zlib

    def chunk(tag: bytes, data: bytes) -> bytes:
        c = struct.pack(">I", len(data)) + tag + data
        return c + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    p = tmp_path / "sample.png"
    p.write_bytes(
        b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(b"\x00" * (w * h * 3))) + chunk(b"IEND", b"")
    )
    return p


def _group(gid: str, bbox: list[int], raw: str = "", status: str = VERIFICATION_NEEDS_REVIEW,
           applies: list[str] | None = None, scope: str = "current_group") -> SemanticRoiGroup:
    return SemanticRoiGroup(
        id=gid, bbox=bbox, raw_transcription=raw, verification_status=status,
        applies_to_group_ids=applies or [], scope=scope,
    )


class TestParseSemantics:
    def test_normal_row(self):
        v = parse_semantics("01 20 ×1")
        assert v["semantics"]["layout"] == "normal_row"
        assert v["semantics"]["numbers"] == ["01", "20"]
        assert v["semantics"]["scope"] == "current_group"
        assert v["needs_human_confirmation"] is True

    def test_plain_numbers_no_multiplier(self):
        v = parse_semantics("14 16 22 28")
        assert v["semantics"]["layout"] == "normal_row"
        assert v["semantics"]["numbers"] == ["14", "16", "22", "28"]

    def test_number_set(self):
        v = parse_semantics("(12.18.20.23) 三×0.5 四×3")
        s = v["semantics"]
        assert s["layout"] == "number_set"
        assert s["numbers"] == ["12", "18", "20", "23"]
        assert s["multipliers"] == [
            {"category": "三", "value_text": "0.5"},
            {"category": "四", "value_text": "3"},
        ]

    def test_shared_multiplier_unbound(self):
        v = parse_semantics("各=三×0.3", region_bound=False)
        assert v["semantics"]["layout"] == "shared_multiplier"
        assert v["semantics"]["scope"] == "unresolved_region"

    def test_shared_multiplier_bound(self):
        v = parse_semantics("各=三×0.3", region_bound=True)
        assert v["semantics"]["layout"] == "shared_multiplier"
        assert v["semantics"]["scope"] == "all_groups_in_region"

    def test_invalid_syntax_flagged(self):
        v = parse_semantics("12 三×0.5.2")
        assert v["issues"]  # ambiguous_symbol present

    def test_question_mark_flagged(self):
        v = parse_semantics("01 2? ×1")
        assert v["issues"]


class TestCanVerify:
    def test_valid_group_verifiable(self, tmp_path):
        img = _png(tmp_path)
        g = _group("G001", [10, 20, 100, 50], "01 20 ×1")
        assert can_verify_group(g, all_group_ids=["G001"], img_w=346, img_h=477) == []

    def test_empty_bbox_blocked(self, tmp_path):
        img = _png(tmp_path)
        g = _group("G001", [0, 0, 0, 0], "01 20 ×1")
        assert "bbox_invalid_or_empty" in can_verify_group(g, all_group_ids=["G001"], img_w=346, img_h=477)

    def test_out_of_image_blocked(self, tmp_path):
        img = _png(tmp_path)
        g = _group("G001", [300, 400, 100, 100], "01 20 ×1")
        assert "bbox_out_of_image" in can_verify_group(g, all_group_ids=["G001"], img_w=346, img_h=477)

    def test_empty_raw_blocked(self, tmp_path):
        img = _png(tmp_path)
        g = _group("G001", [10, 20, 100, 50], "")
        assert "raw_transcription_empty" in can_verify_group(g, all_group_ids=["G001"], img_w=346, img_h=477)

    def test_question_mark_blocked(self, tmp_path):
        img = _png(tmp_path)
        g = _group("G001", [10, 20, 100, 50], "01 2? ×1")
        assert "unresolved_question_mark" in can_verify_group(g, all_group_ids=["G001"], img_w=346, img_h=477)

    def test_invalid_syntax_blocked(self, tmp_path):
        img = _png(tmp_path)
        g = _group("G001", [10, 20, 100, 50], "12 三×0.5.2")
        blockers = can_verify_group(g, all_group_ids=["G001"], img_w=346, img_h=477)
        assert any("validation_issues" in b for b in blockers)

    def test_shared_unbound_blocked(self, tmp_path):
        img = _png(tmp_path)
        g = _group("G001", [10, 20, 100, 50], "各=三×0.3")
        blockers = can_verify_group(g, all_group_ids=["G001", "G002"], img_w=346, img_h=477)
        assert "shared_multiplier_unbound" in blockers
        assert "scope_unresolved_region" in blockers

    def test_shared_bound_passes(self, tmp_path):
        img = _png(tmp_path)
        g = _group("G001", [10, 20, 100, 50], "各=三×0.3", applies=["G002", "G003"])
        blockers = can_verify_group(g, all_group_ids=["G001", "G002", "G003"], img_w=346, img_h=477)
        assert "shared_multiplier_unbound" not in blockers
        assert "scope_unresolved_region" not in blockers

    def test_applies_to_missing_group_blocked(self, tmp_path):
        img = _png(tmp_path)
        g = _group("G001", [10, 20, 100, 50], "各=三×0.3", applies=["G999"])
        assert "applies_to_missing_group" in can_verify_group(g, all_group_ids=["G001"], img_w=346, img_h=477)

    def test_applies_to_self_blocked(self, tmp_path):
        img = _png(tmp_path)
        g = _group("G001", [10, 20, 100, 50], "各=三×0.3", applies=["G001"])
        assert "applies_to_self" in can_verify_group(g, all_group_ids=["G001"], img_w=346, img_h=477)

    def test_applies_to_duplicate_blocked(self, tmp_path):
        img = _png(tmp_path)
        g = _group("G001", [10, 20, 100, 50], "各=三×0.3", applies=["G002", "G002"])
        assert "applies_to_duplicate" in can_verify_group(g, all_group_ids=["G001", "G002"], img_w=346, img_h=477)

    def test_applies_to_blank_blocked(self, tmp_path):
        img = _png(tmp_path)
        g = _group("G001", [10, 20, 100, 50], "各=三×0.3", applies=[""])
        assert "applies_to_blank_id" in can_verify_group(g, all_group_ids=["G001", "G002"], img_w=346, img_h=477)

    def test_applies_to_invalid_format_blocked(self, tmp_path):
        img = _png(tmp_path)
        g = _group("G001", [10, 20, 100, 50], "各=三×0.3", applies=["壞 id!"])
        assert "applies_to_invalid_format" in can_verify_group(g, all_group_ids=["G001"], img_w=346, img_h=477)

    def test_multi_category_shared_parse(self, tmp_path):
        img = _png(tmp_path)
        g = _group("G003", [10, 20, 100, 50], "二三x0.3")
        v = parse_semantics("二三x0.3", region_bound=False)
        assert v["semantics"]["layout"] == "shared_multiplier"
        assert v["semantics"]["multipliers"] == [
            {"category": "二", "value_text": "0.3"},
            {"category": "三", "value_text": "0.3"},
        ]
        assert v["semantics"]["scope"] == "unresolved_region"
        blockers = can_verify_group(g, all_group_ids=["G003", "G004"], img_w=346, img_h=477)
        assert "shared_multiplier_unbound" in blockers

    def test_multi_category_shared_bound_verifiable(self, tmp_path):
        img = _png(tmp_path)
        g = _group("G003", [10, 20, 100, 50], "各二三x0.3", applies=["G004"])
        v = parse_semantics("各二三x0.3", region_bound=True)
        assert v["semantics"]["scope"] == "all_groups_in_region"
        blockers = can_verify_group(g, all_group_ids=["G003", "G004"], img_w=346, img_h=477)
        assert blockers == []


class TestDatasetValidation:
    def test_duplicate_ids_rejected(self, tmp_path):
        img = _png(tmp_path)
        g1 = _group("G001", [10, 20, 100, 50], "01 20 ×1", status=VERIFICATION_VERIFIED)
        g2 = _group("G001", [30, 40, 60, 40], "05 15 ×1", status=VERIFICATION_VERIFIED)
        ann = SemanticRoiAnnotationSet(image=str(img), groups=[g1, g2])
        errors = validate_annotation_set(ann, img_w=346, img_h=477)
        assert any("duplicate" in e for e in errors)

    def test_verified_with_blockers_rejected(self, tmp_path):
        img = _png(tmp_path)
        g = _group("G001", [10, 20, 100, 50], "01 2? ×1", status=VERIFICATION_VERIFIED)
        ann = SemanticRoiAnnotationSet(image=str(img), groups=[g])
        errors = validate_annotation_set(ann, img_w=346, img_h=477)
        assert any("verified but blockers" in e for e in errors)

    def test_missing_image(self, tmp_path):
        ann = SemanticRoiAnnotationSet(image=str(tmp_path / "nope.png"), groups=[
            _group("G001", [10, 20, 100, 50], "01 20 ×1")])
        assert any("image not found" in e for e in validate_annotation_set(ann))

    def test_applies_to_missing_group_in_dataset(self, tmp_path):
        img = _png(tmp_path)
        g = _group("G001", [10, 20, 100, 50], "各=三×0.3", applies=["G404"])
        ann = SemanticRoiAnnotationSet(image=str(img), groups=[g])
        errors = validate_annotation_set(ann, img_w=346, img_h=477)
        assert any("missing group" in e for e in errors)

    def test_invalid_status_rejected(self, tmp_path):
        img = _png(tmp_path)
        g = _group("G001", [10, 20, 100, 50], "01 20 ×1", status="auto_approved")
        ann = SemanticRoiAnnotationSet(image=str(img), groups=[g])
        assert any("verification_status" in e for e in validate_annotation_set(ann))


class TestAtomicSave:
    def test_save_roundtrip(self, tmp_path):
        img = _png(tmp_path)
        g = _group("G001", [10, 20, 100, 50], "01 20 ×1", status=VERIFICATION_VERIFIED)
        ann = SemanticRoiAnnotationSet(image=str(img), groups=[g])
        out = tmp_path / "out.json"
        atomic_save(ann, str(out))
        loaded = SemanticRoiAnnotationSet.from_dict(json.loads(out.read_text(encoding="utf-8")))
        assert loaded.groups[0].id == "G001"
        assert loaded.groups[0].verification_status == VERIFICATION_VERIFIED

    def test_atomic_save_keeps_backup(self, tmp_path):
        img = _png(tmp_path)
        g = _group("G001", [10, 20, 100, 50], "01 20 ×1")
        ann = SemanticRoiAnnotationSet(image=str(img), groups=[g])
        out = tmp_path / "out.json"
        atomic_save(ann, str(out))
        atomic_save(ann, str(out))
        assert (tmp_path / "out.json.bak").is_file()

    def test_atomic_save_refuses_invalid(self, tmp_path):
        img = _png(tmp_path)
        g = _group("G001", [10, 20, 100, 50], "01 2? ×1", status=VERIFICATION_VERIFIED)
        ann = SemanticRoiAnnotationSet(image=str(img), groups=[g])
        out = tmp_path / "out.json"
        with pytest.raises(ValueError):
            atomic_save(ann, str(out))
        assert not out.exists()  # nothing written

    def test_no_temp_leftover_on_failure(self, tmp_path):
        img = _png(tmp_path)
        # verified + empty raw is invalid → save must refuse and leave no temp
        g = _group("G001", [10, 20, 100, 50], "", status=VERIFICATION_VERIFIED)
        ann = SemanticRoiAnnotationSet(image=str(img), groups=[g])
        out = tmp_path / "out.json"
        with pytest.raises(ValueError):
            atomic_save(ann, str(out))
        leftovers = list(tmp_path.glob(".roi-annot-*"))
        assert leftovers == []

    def test_needs_review_incomplete_can_save(self, tmp_path):
        """Work-in-progress groups (needs_human_review, empty raw) CAN save."""
        img = _png(tmp_path)
        g = _group("G001", [10, 20, 100, 50], "")
        ann = SemanticRoiAnnotationSet(image=str(img), groups=[g])
        out = tmp_path / "out.json"
        atomic_save(ann, str(out))  # must not raise
        assert out.is_file()


class TestImageMeta:
    def test_sha256(self, tmp_path):
        img = _png(tmp_path)
        assert len(image_sha256(str(img))) == 64

    def test_size_png(self, tmp_path):
        from betguard.vision.semantic_roi import image_size
        img = _png(tmp_path, 346, 477)
        assert image_size(str(img)) == (346, 477)


class TestNoExternal:
    def test_annotator_binds_loopback_only(self):
        from betguard.vision import roi_annotator
        assert roi_annotator.HOST == "127.0.0.1"

    def test_annotator_no_openai_import(self):
        from betguard.vision import roi_annotator as mod
        with open(mod.__file__, encoding="utf-8") as f:
            content = f.read()
        assert "openai" not in content.lower()
        assert "urllib.request" not in content

    def test_output_defaults_outside_repo(self, tmp_path):
        from betguard.vision import roi_annotator
        img = _png(tmp_path)
        server = roi_annotator.RoiAnnotatorServer(
            str(img), str(tmp_path / "out.json"), ref_paths=[]
        )
        assert server.image_w == 346


class TestImageEndpoint:
    """/api/image must serve the configured image only, never arbitrary paths."""

    @pytest.fixture()
    def server(self, tmp_path):
        from betguard.vision import roi_annotator
        img = _png(tmp_path, 346, 477)
        srv = roi_annotator.RoiAnnotatorServer(str(img), str(tmp_path / "out.json"), ref_paths=[])
        roi_annotator.AnnotatorHandler.annotator = srv
        yield srv, img

    def _get(self, handler_cls, path, server_attr="annotator"):
        from http.server import BaseHTTPRequestHandler
        import io

        class FakeConn:
            def __init__(self):
                self.wfile = io.BytesIO()
                self.rfile = io.BytesIO(b"")

        conn = FakeConn()
        handler = handler_cls.__new__(handler_cls)
        handler.rfile = conn.rfile
        handler.wfile = conn.wfile
        handler.headers = {"Content-Length": "0"}
        handler.path = path
        handler.command = "GET"
        handler.server = type("S", (), {})()
        handler.send_response = lambda code, msg=None: setattr(handler, "_code", code)
        handler.send_header = lambda k, v: setattr(handler, "_headers", {**getattr(handler, "_headers", {}), k: v})
        handler.end_headers = lambda: None
        handler.handle_one_request = None
        # call do_GET directly
        handler.do_GET()
        return handler

    def test_image_endpoint_200_png(self, tmp_path):
        from betguard.vision import roi_annotator
        img = _png(tmp_path, 346, 477)
        srv = roi_annotator.RoiAnnotatorServer(str(img), str(tmp_path / "out.json"), ref_paths=[])
        roi_annotator.AnnotatorHandler.annotator = srv
        h = self._get(roi_annotator.AnnotatorHandler, "/api/image")
        assert h._code == 200
        assert h._headers.get("Content-Type") == "image/png"
        body = h.wfile.getvalue()
        assert body == img.read_bytes()
        assert len(body) > 0

    def test_image_sha256_matches_original(self, tmp_path):
        import hashlib
        from betguard.vision import roi_annotator
        img = _png(tmp_path, 346, 477)
        srv = roi_annotator.RoiAnnotatorServer(str(img), str(tmp_path / "out.json"), ref_paths=[])
        roi_annotator.AnnotatorHandler.annotator = srv
        h = self._get(roi_annotator.AnnotatorHandler, "/api/image")
        assert hashlib.sha256(h.wfile.getvalue()).hexdigest() == hashlib.sha256(img.read_bytes()).hexdigest()

    def test_arbitrary_path_rejected(self, tmp_path):
        from betguard.vision import roi_annotator
        img = _png(tmp_path, 346, 477)
        srv = roi_annotator.RoiAnnotatorServer(str(img), str(tmp_path / "out.json"), ref_paths=[])
        roi_annotator.AnnotatorHandler.annotator = srv
        for path in ["/api/image?path=C:/Windows/win.ini", "/api/image/../etc/passwd",
                     "/api/image?f=/etc/passwd", "/static/anything.png"]:
            h = self._get(roi_annotator.AnnotatorHandler, path)
            assert h._code == 404, f"path {path} must be 404"

    def test_state_uses_loopback_endpoint_not_abs_path(self, tmp_path):
        from betguard.vision import roi_annotator
        img = _png(tmp_path, 346, 477)
        srv = roi_annotator.RoiAnnotatorServer(str(img), str(tmp_path / "out.json"), ref_paths=[])
        state = srv.state()
        assert state["image"]["src"] == "/api/image"
        assert "dataUrl" not in state["image"]
        assert "C:" not in state["image"].get("src", "")

    def test_page_html_no_absolute_image_path(self, tmp_path):
        from betguard.vision import roi_annotator
        assert "C:\\" not in roi_annotator.PAGE
        assert "/api/image" in roi_annotator.PAGE or "image.src" in roi_annotator.PAGE

    def test_new_roi_defaults_empty(self, tmp_path):
        from betguard.vision import roi_annotator
        img = _png(tmp_path, 346, 477)
        srv = roi_annotator.RoiAnnotatorServer(str(img), str(tmp_path / "out.json"), ref_paths=[])
        srv._groups = [SemanticRoiGroup(id="G001", bbox=[1, 1, 50, 30])]
        state = srv.state()
        assert state["rois"][0]["applies_to_group_ids"] == []
        assert state["rois"][0]["raw_transcription"] == ""
        assert state["rois"][0]["verification_status"] == "needs_human_review"
        assert "G001,G002" not in json.dumps(roi_annotator.PAGE)

    def test_missing_image_404(self, tmp_path):
        from betguard.vision import roi_annotator
        img = _png(tmp_path, 346, 477)
        srv = roi_annotator.RoiAnnotatorServer(str(img), str(tmp_path / "out.json"), ref_paths=[])
        srv.image_path = str(tmp_path / "missing.png")
        roi_annotator.AnnotatorHandler.annotator = srv
        h = self._get(roi_annotator.AnnotatorHandler, "/api/image")
        assert h._code == 404


class TestColumnMatrix:
    """Matrix (zhu-peng) columns read top-to-bottom, slash-separated."""

    def test_basic_matrix(self):
        from betguard.vision.semantic_roi import parse_column_matrix, flatten_matrix_for_ui
        cols = parse_column_matrix([
            "01 X 10 X 17",
            "02   11   18",
            "03   12   27",
        ])
        assert cols == [["01", "02", "03"], ["10", "11", "12"], ["17", "18", "27"]]
        assert flatten_matrix_for_ui(cols) == "01 02 03 / 10 11 12 / 17 18 27"

    def test_lowercase_x_matrix(self):
        from betguard.vision.semantic_roi import parse_column_matrix
        cols = parse_column_matrix(["01 x 10", "02 11"])
        assert cols == [["01", "02"], ["10", "11"]]

    def test_single_row_matrix(self):
        from betguard.vision.semantic_roi import parse_column_matrix
        assert parse_column_matrix(["01 X 10 X 17"]) == [["01"], ["10"], ["17"]]

    def test_ragged_matrix_rejected(self):
        from betguard.vision.semantic_roi import parse_column_matrix
        assert parse_column_matrix(["01 X 10 X 17", "02 11"]) is None

    def test_non_number_rejected(self):
        from betguard.vision.semantic_roi import parse_column_matrix
        assert parse_column_matrix(["01 X 10", "02 XX"]) is None

    def test_empty_rejected(self):
        from betguard.vision.semantic_roi import parse_column_matrix
        assert parse_column_matrix([]) is None

    def test_question_mark_allowed(self):
        from betguard.vision.semantic_roi import parse_column_matrix
        cols = parse_column_matrix(["01 X 1?", "02 11"])
        assert cols == [["01", "02"], ["1?", "11"]]


class TestTailExpansionROI:
    def test_tail_in_semantics(self):
        from betguard.vision.semantic_roi import parse_semantics
        v = parse_semantics("13X24X8尾 二三X1")
        assert v["semantics"]["layout"] == "normal_row"
        assert "13" in v["semantics"]["numbers"]
        assert "08" in v["semantics"]["numbers"]
        assert "38" in v["semantics"]["numbers"]
