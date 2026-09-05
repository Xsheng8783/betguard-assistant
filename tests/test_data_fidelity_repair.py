"""Synthetic fidelity regressions, not recognition accuracy measurements."""
import json
import pytest
from betguard.vision import service
from betguard.vision.image_text_acceptance import record_machine_transcription, validate_with_existing_parser


def test_mixed_five_and_half_must_not_rewrite():
    raw = "07 17 19\n2×5\n3×0.5"
    assert service._protect_literal_decimal(raw, "2×5;3×0.5") == (raw, False)


@pytest.mark.parametrize("raw,literal", [
    ("05 17 19\n2×5", "3×0.5"),
    ("05 17 19\n2×05", "2×0.5"),
    ("03 × 16 × ?尾 2,3×1", "7尾"),
    ("03 × 16 × 尾 2,3×1", "7尾"),
])
def test_conflicting_model_fields_cannot_supply_values(raw, literal):
    text, notices = service.gemma_evidence_to_betguard_text_with_notices({
        "status": "completed", "items": [{"raw_text": raw, "multiplier_text": literal,
        "special_text": literal, "cancelled": "no"}]})
    assert text == raw
    assert any(n["code"] == "MODEL_FIELD_CONFLICT" for n in notices)


@pytest.mark.parametrize("raw", ["03 × 16 × 7尾 二三×1", "11 33 各0.5車", "03 × 16 × ?尾 二三×1", "11 半車", "01 X 02/03 2,3×0.1"])
def test_specials_and_unknown_text_retained(raw):
    assert service.gemma_evidence_to_betguard_text({"status": "completed", "items": [{"raw_text": raw}]}) == raw


def test_cancelled_prose_cannot_erase_adjacent_bet():
    raw = "01 02 2×5\n(crossed out)\n03 04 2×0.5"
    text, notices = service.gemma_evidence_to_betguard_text_with_notices({"status": "completed", "items": [{"raw_text": raw}]})
    assert raw in text
    assert not service.preflight_image_text(text)["all_parseable"]
    assert any(n["code"] == "CANCELLATION_UNCONFIRMED" for n in notices)


def test_model_cancelled_flag_keeps_source_but_prevents_parse():
    raw = "01 02 2×5"
    text, notices = service.gemma_evidence_to_betguard_text_with_notices({"status": "completed", "items": [{"raw_text": raw, "cancelled": "yes"}]})
    assert raw in text
    assert not service.preflight_image_text(text)["all_parseable"]


@pytest.mark.parametrize("game,number,expected", [("539",39,True),("539",40,False),("539",49,False),("六合",39,True),("六合",40,True),("六合",49,True)])
def test_game_boundaries_preview_and_capture_validation(game, number, expected):
    text = f"01 {number} 2×1"
    assert service.preflight_image_text(text, game=game)["all_parseable"] is expected
    assert validate_with_existing_parser(text, game=game)["ok"] is expected


def test_capture_keeps_first_prediction_and_layers(tmp_path):
    args = dict(source_image_sha256="b"*64, reader="fake", capture_root=tmp_path, game="539",
                provider_raw_response='{"text":"01 02 2×5"}', native_ocr_text="01 02 2×5",
                adapter_text="01 02 2×5", parser_result={"all_parseable":True}, transformations=[])
    first = record_machine_transcription("a"*32, ai_original_text="01 02 2×5", **args)
    original = (tmp_path / ("a"*32+".json")).read_bytes()
    second = record_machine_transcription("a"*32, ai_original_text="03 04 2×1", **args)
    assert (tmp_path / ("a"*32+".json")).read_bytes() == original
    assert first["native_ocr_text"] == "01 02 2×5"
    assert first["human_corrected_text"] is None
    assert second["capture_id"] != first["capture_id"]
    assert len(list((tmp_path / "revisions" / ("a"*32)).glob("*.json"))) == 2


@pytest.mark.parametrize("game,n,accepted", [("539",39,True),("539",40,False),("539",49,False),("六合",39,True),("六合",40,True),("六合",49,True)])
def test_mock_fill_respects_game(game, n, accepted):
    from betguard.webfill.assisted_fill_mock import build_mock_fill_report
    report = build_mock_fill_report(f"01 {n} 2×0.5", game=game)
    assert (report["status"] == "COMPLETED_MOCK_ONLY") is accepted
    assert report["danger_buttons_clicked"] == []
    if accepted:
        assert [int(number) for number in report["selected_numbers"]] == [1, n]
        assert report["filled_amounts"] == {"二星": 50}


def test_unvalidated_provider_record_retains_all_visible_text():
    source = {"raw_text": "03 × 16 × ?尾 2,3×1", "unexpected": "metadata"}
    evidence = {"status": "completed", "items": [], "raw_response_text": json.dumps({"items": [source]}),
                "rejected_items": [{"item_index": 1, "reason_code": "SCHEMA"}]}
    text, notices = service.gemma_evidence_to_betguard_text_with_notices(evidence)
    assert source["raw_text"] in text
    assert notices[0]["before"] == source
    assert not service.preflight_image_text(text)["all_parseable"]


def test_failed_response_can_be_archived_without_becoming_valid_prediction(tmp_path):
    raw = {"status":"incomplete", "output_text":"01 02"}
    result = record_machine_transcription("d"*32, source_image_sha256="e"*64, ai_original_text="",
        reader="mock", provider_raw_response=raw, capture_root=tmp_path,
        parser_result={"prediction_validated":False})
    assert result["provider_raw_response"] == raw
    assert result["parser_result"]["prediction_validated"] is False
    assert result["human_corrected_text"] is None


def test_legacy_capture_is_reused_as_first_known_sha_prediction(tmp_path):
    original = {"schema_version":"betguard-image-text-machine-capture-v1", "source_image_sha256":"b"*64,
                "ai_original_text":"01 02 2×5", "prediction_timestamp":"2026-01-01T00:00:00Z"}
    legacy_path = tmp_path / ("c"*32+".json")
    blob = json.dumps(original).encode()
    legacy_path.write_bytes(blob)
    record_machine_transcription("a"*32, source_image_sha256="b"*64,
                                 ai_original_text="03 04 2×1", reader="mock", capture_root=tmp_path)
    assert legacy_path.read_bytes() == blob
    first = json.loads((tmp_path / "first-by-sha" / ("b"*64+".json")).read_text(encoding="utf-8"))
    assert first == original


@pytest.mark.parametrize("text", [
    "01 05 19 2×5 3×0.5", "01 × 02 12 × 03 13 23 2,3×0.1",
    "03 × 16 × 7尾 2,3×0.5", "11 半車", "11 33 各0.5車",
    "01 02 03\n2×0.1\n3×5", "01 02 2×1\n(crossed out)\n03 04 2×0.5",
])
def test_preflight_keeps_source_and_provenance_for_every_fragment(text):
    result = service.preflight_image_text(text, game="539")
    parsed = result["parser_normalized_result"]
    assert parsed["game"] == "539"
    assert parsed["bets"] or parsed["invalid_fragments"] or parsed["ignored_metadata_lines"]
    assert result["text_mutated"] is False


def test_http_game_reaches_preflight_batch_and_saved_truth(tmp_path, monkeypatch):
    import http.client
    from tests.test_image_text_assisted_fill import _running_app
    from betguard.webui import app
    from betguard.vision import image_text_acceptance
    monkeypatch.setattr(app, "RUNS_DIR", tmp_path)
    seen = {}
    def save(image_id, text, *, game):
        seen.update(game=game, text=text)
        return {"ok":True}
    monkeypatch.setattr(image_text_acceptance, "save_human_verified_sample", save)
    with _running_app() as port:
        def post(path, body):
            connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            try:
                connection.request("POST", path, json.dumps(body).encode(), {"Content-Type":"application/json"})
                response = connection.getresponse()
                return response.status, json.loads(response.read())
            finally:
                connection.close()
        body = {"text":"01 49 2×1", "source":"IMAGE_TRANSCRIPTION_TEXT", "game":"539"}
        _, preview = post("/api/vision/v1/transcriptions/preflight", body)
        assert preview["game"] == "539" and not preview["all_parseable"]
        status, _ = post("/assist-panel/create-batch", body)
        assert status == 409
        assert not list(tmp_path.rglob("batch_*.json"))
        body["game"] = "六合"
        _, preview = post("/api/vision/v1/transcriptions/preflight", body)
        assert preview["all_parseable"] and preview["game"] == "六合"
        status, batch = post("/assist-panel/create-batch", body)
        assert status == 200 and batch["ok"]
        queues = list(tmp_path.rglob("batch_*.json"))
        stored = json.loads(queues[0].read_text(encoding="utf-8"))
        assert stored["preprocessing"]["valid_candidates"][0]["result"]["game"] == "六合"
        post("/api/vision/v1/acceptance-dataset/samples", {"image_id":"a"*32,"human_verified_betguard_text":body["text"],"game":"六合"})
        assert seen == {"game":"六合","text":body["text"]}
