"""Test ROI annotation schema validation."""

from __future__ import annotations

import json

import pytest

from betguard.vision.roi_annotations import (
    ROI_SCHEMA_VERSION,
    RoiAnnotationSet,
    RoiGroup,
    clamp_bboxes_to_image,
    load_annotation_set,
    validate_annotation_set,
)


def _ann(**kw) -> RoiAnnotationSet:
    defaults = dict(
        schema_version=ROI_SCHEMA_VERSION,
        image="C:/img/sample.png",
        groups=[RoiGroup(id="G01", bbox=[10, 20, 100, 50], ground_truth="01 20 x1")],
    )
    defaults.update(kw)
    return RoiAnnotationSet(**defaults)


class TestValidation:
    def test_valid(self, tmp_path):
        img = tmp_path / "sample.png"
        img.write_bytes(b"x")
        ann = _ann(image=str(img))
        assert validate_annotation_set(ann) == []

    def test_wrong_schema(self):
        assert any("schema_version" in e for e in validate_annotation_set(_ann(schema_version="v0")))

    def test_missing_image(self):
        assert any("image" in e for e in validate_annotation_set(_ann(image="")))

    def test_url_image_rejected(self):
        assert any("URL" in e for e in validate_annotation_set(_ann(image="https://x/y.png")))

    def test_image_not_found(self):
        assert any("not found" in e for e in validate_annotation_set(_ann(image="C:/nope/missing.png")))

    def test_no_groups(self):
        assert any("group" in e for e in validate_annotation_set(_ann(groups=[])))

    def test_duplicate_id(self):
        g = RoiGroup(id="G01", bbox=[1, 2, 3, 4], ground_truth="05")
        assert any("duplicate" in e for e in validate_annotation_set(_ann(groups=[g, g])))

    def test_bad_bbox_len(self):
        g = RoiGroup(id="G01", bbox=[1, 2, 3], ground_truth="05")
        assert any("bbox" in e for e in validate_annotation_set(_ann(groups=[g])))

    def test_negative_bbox(self):
        g = RoiGroup(id="G01", bbox=[-1, 2, 3, 4], ground_truth="05")
        assert any(">= 0" in e for e in validate_annotation_set(_ann(groups=[g])))

    def test_missing_ground_truth(self):
        g = RoiGroup(id="G01", bbox=[1, 2, 3, 4], ground_truth="")
        assert any("ground_truth" in e for e in validate_annotation_set(_ann(groups=[g])))


class TestRoundTrip:
    def test_save_load(self, tmp_path):
        p = tmp_path / "ann.json"
        ann = _ann(groups=[
            RoiGroup(id="G01", bbox=[1, 2, 30, 40], ground_truth="01 20 x1", label="二星"),
            RoiGroup(id="G02", bbox=[5, 6, 30, 40], ground_truth="18 26 x1"),
        ])
        import betguard.vision.roi_annotations as ra
        ra.save_annotation_set(ann, str(p))
        loaded = load_annotation_set(str(p))
        assert loaded.schema_version == ROI_SCHEMA_VERSION
        assert len(loaded.groups) == 2
        assert loaded.groups[0].label == "二星"
        assert loaded.groups[0].bbox == [1, 2, 30, 40]


class TestClamp:
    def test_clamps_to_image(self):
        g = RoiGroup(id="G01", bbox=[-10, -5, 500, 400], ground_truth="05")
        ann = _ann(groups=[g])
        clamp_bboxes_to_image(ann, img_w=200, img_h=100)
        assert ann.groups[0].bbox == [0, 0, 200, 100]

    def test_keeps_valid(self):
        g = RoiGroup(id="G01", bbox=[10, 20, 50, 30], ground_truth="05")
        ann = _ann(groups=[g])
        clamp_bboxes_to_image(ann, img_w=200, img_h=100)
        assert ann.groups[0].bbox == [10, 20, 50, 30]
