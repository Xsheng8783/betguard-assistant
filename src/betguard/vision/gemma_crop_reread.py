"""Bounded Gemma re-read of uniquely grounded uncertain review regions.

This module creates suggestion-only evidence.  PP text is used only to locate a
unique crop; it is never copied into a betting value.  Human confirmation
remains the sole value authority.
"""

from __future__ import annotations

import base64
import copy
import hashlib
import io
import json
import math
import os
import re
import tempfile
import time
import urllib.error
import urllib.request
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from typing import Any, Callable, Mapping

from PIL import Image, ImageDraw, ImageOps

from betguard.vision.contracts import RecognitionRequest
from betguard.vision.gemma_shadow import API_KEY_ENV, ENDPOINT_TEMPLATE, MODEL_NAME


PROVIDER_ID = "gemma4-26b-crop-reread"
SCHEMA_VERSION = "betguard.vision.gemma-crop-reread-evidence.v2"
CACHE_SCHEMA_VERSION = "betguard.vision.gemma-crop-reread-cache.v2"
REQUEST_SCHEMA_VERSION = "betguard.vision.gemma-crop-reread-request.v2"
ADAPTER_VERSION = "betguard.gemma-crop-reread.v2"
ENABLED_ENV = "BETGUARD_GEMMA_CROP_REREAD_ENABLED"
TIMEOUT_ENV = "BETGUARD_GEMMA_CROP_REREAD_TIMEOUT_SECONDS"
MAX_REGIONS_ENV = "BETGUARD_GEMMA_CROP_REREAD_MAX_REGIONS"

DEFAULT_MAX_REGIONS = 8
HARD_MAX_REGIONS = 8
MAX_EXTERNAL_CALLS = 1
RETRY_COUNT = 0

_NUMBER_RE = re.compile(r"(?<!\d)(?:0[1-9]|[12]\d|3[0-9])(?!\d)")
_DIGIT_RUN_RE = re.compile(r"(?<!\d)\d{4,16}(?!\d)")
_SPECIAL_RE = re.compile(r"(?:半車|尾|車|各)")
_SPECIAL_VALUE_RE = re.compile(r"(?<!\d)(?:0?\d)\s*尾|半車|車|各")
_OPERATOR_RE = re.compile(r"[xX×]")
_MULTIPLIER_RE = re.compile(r"[xX×]\s*\d+(?:\.\d+)?", re.IGNORECASE)
_PROBABLE_SPECIAL_SHAPE_RE = re.compile(
    r"^\s*(?:0[1-9]|[12]\d|3[0-9])\s*[xX×]\s*"
    r"(?:0[1-9]|[12]\d|3[0-9])\s*[xX×]\s*\d\s*$"
)
_SAFE_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")

PROMPT = """Each numbered panel is a magnified crop intended to contain exactly one
physical betting record. Read only visible content inside that panel. For every
crop_id report literal numbers, real x or × operators, vertical column groups,
all multiplier rules, continuation, special play (尾, 車, 半車, 各), and
cancellation evidence.

number_groups must preserve the visible relationship. For example, two visible
rows `12 × 24 × 36` and `08 × 14 × 38` are one column record and must be
[["12","08"],["24","14"],["36","38"]]. A normal flat record is one group.
`2/3 × 0.5` is a multiplier rule and 0.5 must never become number "05". A
special play like `03 × 16 × 7尾` keeps only literal bet numbers in groups and
keeps `7尾` in special_text; the category digit 7 is never a number group.
number_groups must never copy a digit from multiplier_text or special_text. If
trailing `x05` might mean `x0.5`, omit that ambiguous operand from groups and
mark uncertain instead of choosing either interpretation. multiplier_text must
be the full visible literal or `unclear`, never a bare `x`. Do not invent x from
spacing, guess a number, repair unclear writing, or add betting knowledge. Use
empty groups/rules and mark uncertain=true when structure cannot be read safely.

Return only the declared JSON schema. This is machine evidence for Human Review
only. Never confirm, create a Candidate, fill, or submit a bet."""
PROMPT_SHA256 = hashlib.sha256(PROMPT.encode("utf-8")).hexdigest()

_ITEM_KEYS = frozenset(
    {
        "crop_id",
        "raw_text",
        "numbers",
        "number_groups",
        "multiplier_text",
        "multiplier_rules",
        "visible_operators",
        "layout_guess",
        "continuation",
        "special_text",
        "cancelled",
        "uncertain",
        "uncertain_reason",
    }
)

RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "version": {"type": "string", "enum": ["gemma-crop-reread-v2"]},
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "crop_id": {"type": "string", "pattern": "^C[0-9]{2}$"},
                    "raw_text": {"type": "string"},
                    "numbers": {"type": "string"},
                    "number_groups": {
                        "type": "array",
                        "items": {
                            "type": "array",
                            "items": {
                                "type": "string",
                                "pattern": "^(?:[0-9]|0[1-9]|[12][0-9]|3[0-9])$",
                            },
                            "minItems": 1,
                        },
                    },
                    "multiplier_text": {"type": "string"},
                    "multiplier_rules": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "visible_operators": {
                        "type": "array",
                        "items": {"type": "string", "enum": ["x", "X", "×"]},
                    },
                    "layout_guess": {
                        "type": "string",
                        "enum": ["normal", "column", "unclear"],
                    },
                    "continuation": {
                        "type": "string",
                        "enum": ["yes", "no", "unclear"],
                    },
                    "special_text": {"type": "string"},
                    "cancelled": {
                        "type": "string",
                        "enum": ["yes", "no", "unclear"],
                    },
                    "uncertain": {"type": "boolean"},
                    "uncertain_reason": {"type": "string"},
                },
                "required": sorted(_ITEM_KEYS),
                "additionalProperties": False,
            },
        },
    },
    "required": ["version", "items"],
    "additionalProperties": False,
}


@dataclass(frozen=True)
class CropRereadConfig:
    enabled: bool
    endpoint: str
    timeout_seconds: float
    cache_dir: Path
    max_regions: int = DEFAULT_MAX_REGIONS
    model: str = MODEL_NAME
    temperature: float = 0.0
    max_output_tokens: int = 8192

    @property
    def configured(self) -> bool:
        return self.enabled and bool(os.environ.get(API_KEY_ENV, "").strip())


@dataclass(frozen=True)
class CropCacheIdentity:
    source_image_sha256: str
    contact_sheet_sha256: str
    proposal_sha256: str
    model: str
    prompt_sha256: str = PROMPT_SHA256
    request_schema_version: str = REQUEST_SCHEMA_VERSION
    adapter_version: str = ADAPTER_VERSION

    def to_dict(self) -> dict[str, str]:
        return {
            "source_image_sha256": self.source_image_sha256,
            "contact_sheet_sha256": self.contact_sheet_sha256,
            "proposal_sha256": self.proposal_sha256,
            "model": self.model,
            "prompt_sha256": self.prompt_sha256,
            "request_schema_version": self.request_schema_version,
            "adapter_version": self.adapter_version,
        }

    def key(self) -> str:
        return hashlib.sha256(_canonical_json(self.to_dict())).hexdigest()


class CropRereadCache:
    def __init__(self, cache_dir: Path) -> None:
        self.cache_dir = cache_dir

    def get(self, identity: CropCacheIdentity) -> dict[str, Any] | None:
        path = self.cache_dir / f"{identity.key()}.json"
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError):
            return None
        if not isinstance(record, dict) or record.get("schema_version") != CACHE_SCHEMA_VERSION:
            return None
        if record.get("identity") != identity.to_dict():
            return None
        evidence = record.get("evidence")
        if not isinstance(evidence, dict):
            return None
        if hashlib.sha256(_canonical_json(evidence)).hexdigest() != record.get("evidence_sha256"):
            return None
        try:
            return validate_crop_reread_evidence(evidence)
        except ValueError:
            return None

    def put(self, identity: CropCacheIdentity, evidence: Mapping[str, Any]) -> None:
        validated = validate_crop_reread_evidence(evidence)
        if validated["status"] != "completed":
            return
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        target = self.cache_dir / f"{identity.key()}.json"
        record = {
            "schema_version": CACHE_SCHEMA_VERSION,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "identity": identity.to_dict(),
            "evidence": validated,
            "evidence_sha256": hashlib.sha256(_canonical_json(validated)).hexdigest(),
        }
        encoded = json.dumps(record, ensure_ascii=False, indent=2).encode("utf-8")
        fd, temp_name = tempfile.mkstemp(
            prefix=f".{identity.key()}.", suffix=".tmp", dir=self.cache_dir
        )
        temp_path = Path(temp_name)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, target)
        finally:
            temp_path.unlink(missing_ok=True)


def default_crop_cache_dir() -> Path:
    root = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))
    return root / "Betguard Assistant" / "vision" / "gemma4-crop-reread-cache"


def get_crop_reread_config() -> CropRereadConfig:
    try:
        timeout = float(os.environ.get(TIMEOUT_ENV, "75"))
    except ValueError:
        timeout = 75.0
    try:
        maximum = int(os.environ.get(MAX_REGIONS_ENV, str(DEFAULT_MAX_REGIONS)))
    except ValueError:
        maximum = DEFAULT_MAX_REGIONS
    return CropRereadConfig(
        # Runtime-reader selection is the feature gate.  Operators may only
        # disable this bounded follow-up explicitly; no old research flag is
        # required to enable it.
        enabled=os.environ.get(ENABLED_ENV, "1").strip() != "0",
        endpoint=ENDPOINT_TEMPLATE.format(model=MODEL_NAME),
        timeout_seconds=min(max(timeout, 1.0), 120.0),
        cache_dir=default_crop_cache_dir(),
        max_regions=min(max(maximum, 1), HARD_MAX_REGIONS),
    )


def select_uncertain_crop_proposals(
    request: RecognitionRequest,
    review_seed: Mapping[str, Any],
    pp_evidence: Mapping[str, Any],
    *,
    max_regions: int = DEFAULT_MAX_REGIONS,
) -> list[dict[str, Any]]:
    """Select only unique review-card-to-PP geometry mappings.

    Machine literal overlap is used solely to choose a place to look.  The
    resulting proposal contains geometry/provenance but no derived bet value.
    """

    cards = review_seed.get("review_cards")
    if not isinstance(cards, list):
        return []
    regions = _valid_pp_regions(pp_evidence)
    if not regions:
        return []
    try:
        with Image.open(request.image_path) as image:
            image = ImageOps.exif_transpose(image).convert("RGB")
            image_width, image_height = image.size
            grid = _detect_red_grid(image, regions)
    except (OSError, ValueError):
        return []
    heights = [region["bbox"][3] - region["bbox"][1] for region in regions]
    median_height = float(median(heights)) if heights else 40.0
    ranked_cards: list[tuple[int, int, Mapping[str, Any]]] = []
    for index, card in enumerate(cards):
        if not isinstance(card, Mapping) or card.get("draft_classification") != "AI_UNCERTAIN":
            continue
        priority = _uncertain_priority(card)
        if priority <= 0:
            continue
        ranked_cards.append((-priority, index, card))

    tentative: list[dict[str, Any]] = []
    for _negative_priority, index, card in sorted(ranked_cards):
        proposal = _unique_crop_for_card(
            card,
            index,
            regions,
            median_height=median_height,
            image_width=image_width,
            image_height=image_height,
            grid=grid,
        )
        if proposal is not None:
            tentative.append(proposal)

    # Uniqueness is bidirectional.  A card must map to one crop, and the same
    # physical crop must not plausibly map back to multiple first-pass cards.
    # Priority is never authority for resolving that ambiguity.
    ambiguous_indexes: set[int] = set()
    for first_index, first in enumerate(tentative):
        for second_index in range(first_index + 1, len(tentative)):
            second = tentative[second_index]
            if _bbox_iou(first["bbox"], second["bbox"]) > 0.62:
                ambiguous_indexes.update((first_index, second_index))
    tentative = [
        proposal
        for index, proposal in enumerate(tentative)
        if index not in ambiguous_indexes
    ]

    selected: list[dict[str, Any]] = []
    for proposal in sorted(
        tentative,
        key=lambda value: (-int(value["priority"]), -float(value["selection_score"]), value["draft_id"]),
    ):
        if any(_bbox_iou(proposal["bbox"], item["bbox"]) > 0.62 for item in selected):
            continue
        selected.append(proposal)
        if len(selected) >= min(max(max_regions, 0), HARD_MAX_REGIONS):
            break
    for index, proposal in enumerate(selected, 1):
        proposal["crop_id"] = f"C{index:02d}"
    return selected


def run_gemma_crop_reread(
    request: RecognitionRequest,
    review_seed: Mapping[str, Any],
    pp_evidence: Mapping[str, Any],
    *,
    config: CropRereadConfig | None = None,
    cache: CropRereadCache | None = None,
    transport: Callable[[dict[str, Any], str, CropRereadConfig], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    started = time.perf_counter()
    config = config or get_crop_reread_config()
    proposals = select_uncertain_crop_proposals(
        request,
        review_seed,
        pp_evidence,
        max_regions=config.max_regions,
    )
    base = _base_evidence(request, proposals)
    if not proposals:
        return _failure(base, "CROP_REREAD_NO_UNIQUE_REGIONS", started, status="skipped")
    if not config.enabled:
        return _failure(base, "CROP_REREAD_DISABLED", started, status="disabled")
    try:
        sheet_bytes = build_contact_sheet(request.image_path, proposals)
    except (OSError, ValueError):
        return _failure(base, "CROP_REREAD_IMAGE_INVALID", started)
    identity = CropCacheIdentity(
        source_image_sha256=str(request.metadata.get("sha256") or ""),
        contact_sheet_sha256=hashlib.sha256(sheet_bytes).hexdigest(),
        proposal_sha256=hashlib.sha256(_canonical_json(proposals)).hexdigest(),
        model=config.model,
    )
    cache = cache or CropRereadCache(config.cache_dir)
    cached = cache.get(identity)
    if cached is not None:
        return {
            **cached,
            "cache_hit": True,
            "cache_identity": identity.to_dict(),
            "external_call_count": 0,
            "retry_count": RETRY_COUNT,
            "latency_ms": round((time.perf_counter() - started) * 1000.0, 3),
        }
    api_key = os.environ.get(API_KEY_ENV, "").strip()
    if not api_key:
        return {
            **_failure(base, "CROP_REREAD_NOT_CONFIGURED", started, status="unavailable"),
            "cache_identity": identity.to_dict(),
        }
    external_calls = 0
    try:
        payload = _request_payload(sheet_bytes, config)
        external_calls = 1
        envelope = (transport or _post_generate_content)(payload, api_key, config)
        evidence = _validated_response(envelope, base)
    except TimeoutError:
        return _failure(
            base,
            "CROP_REREAD_TIMEOUT",
            started,
            status="timeout",
            external_call_count=external_calls,
        )
    except urllib.error.HTTPError as exc:
        return _failure(
            base,
            "CROP_REREAD_HTTP_ERROR",
            started,
            detail=str(exc.code),
            external_call_count=external_calls,
        )
    except urllib.error.URLError as exc:
        return _failure(
            base,
            "CROP_REREAD_NETWORK_ERROR",
            started,
            detail=type(exc.reason).__name__,
            external_call_count=external_calls,
        )
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        return _failure(
            base,
            "CROP_REREAD_INVALID_RESPONSE",
            started,
            detail=type(exc).__name__,
            external_call_count=external_calls,
        )
    evidence.update(
        cache_hit=False,
        cache_identity=identity.to_dict(),
        external_call_count=external_calls,
        retry_count=RETRY_COUNT,
        latency_ms=round((time.perf_counter() - started) * 1000.0, 3),
    )
    try:
        cache.put(identity, evidence)
    except OSError:
        pass
    return validate_crop_reread_evidence(evidence)


def build_contact_sheet(
    image_path: str,
    proposals: list[Mapping[str, Any]],
) -> bytes:
    if not proposals or len(proposals) > HARD_MAX_REGIONS:
        raise ValueError("contact sheet proposal count is invalid")
    with Image.open(image_path) as source:
        source = ImageOps.exif_transpose(source).convert("RGB")
        panels: list[tuple[str, Image.Image]] = []
        for proposal in proposals:
            bbox = proposal.get("bbox")
            if not _valid_bbox(bbox):
                raise ValueError("contact sheet bbox is invalid")
            left, top, right, bottom = (int(value) for value in bbox)
            crop = source.crop((left, top, right, bottom))
            scale = min(2.5, max(1.0, 620.0 / max(crop.width, 1)))
            crop = crop.resize(
                (max(1, round(crop.width * scale)), max(1, round(crop.height * scale))),
                Image.Resampling.LANCZOS,
            )
            crop = ImageOps.contain(crop, (640, 260), Image.Resampling.LANCZOS)
            panels.append((str(proposal.get("crop_id") or ""), crop))
        columns = 2
        panel_width, panel_height, header = 660, 300, 30
        rows = math.ceil(len(panels) / columns)
        sheet = Image.new("RGB", (columns * panel_width, rows * panel_height), "white")
        draw = ImageDraw.Draw(sheet)
        for index, (crop_id, panel) in enumerate(panels):
            x = (index % columns) * panel_width
            y = (index // columns) * panel_height
            draw.rectangle((x + 2, y + 2, x + panel_width - 3, y + panel_height - 3), outline="red", width=3)
            draw.text((x + 10, y + 8), crop_id, fill="red")
            sheet.paste(panel, (x + 10, y + header))
        output = io.BytesIO()
        sheet.save(output, format="PNG", optimize=True)
        return output.getvalue()


def validate_crop_reread_evidence(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping) or value.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("crop reread evidence schema is invalid")
    if value.get("human_confirmed") is not False or value.get("value_authority") != "human_confirmed_answer":
        raise ValueError("crop reread authority is invalid")
    if value.get("auto_confirm") is not False or value.get("auto_submit") is not False:
        raise ValueError("crop reread safety flags are invalid")
    proposals = value.get("proposals")
    if not isinstance(proposals, list) or len(proposals) > HARD_MAX_REGIONS:
        raise ValueError("crop reread proposals are invalid")
    proposal_ids: set[str] = set()
    draft_ids: set[str] = set()
    for proposal in proposals:
        if not isinstance(proposal, Mapping) or set(proposal) != {
            "crop_id",
            "draft_id",
            "bbox",
            "linked_pp_evidence_ids",
            "selection_score",
            "selection_reason",
            "priority",
            "human_truth_used",
        }:
            raise ValueError("crop reread proposal is invalid")
        crop_id = proposal.get("crop_id")
        draft_id = proposal.get("draft_id")
        if (
            re.fullmatch(r"C\d{2}", str(crop_id or "")) is None
            or not _safe_id(draft_id)
            or crop_id in proposal_ids
            or draft_id in draft_ids
            or not _valid_bbox(proposal.get("bbox"))
            or proposal.get("human_truth_used") is not False
        ):
            raise ValueError("crop reread proposal identity is invalid")
        linked = proposal.get("linked_pp_evidence_ids")
        if not isinstance(linked, list) or not linked or len(linked) != len(set(linked)):
            raise ValueError("crop reread PP linkage is invalid")
        proposal_ids.add(str(crop_id))
        draft_ids.add(str(draft_id))
    items_value = value.get("items", [])
    if not isinstance(items_value, list):
        raise ValueError("crop reread items are invalid")
    items: list[dict[str, Any]] = []
    item_ids: set[str] = set()
    for item in items_value:
        validated = _validated_item(item, proposal_ids)
        if validated["crop_id"] in item_ids:
            raise ValueError("crop reread item IDs must be unique")
        item_ids.add(validated["crop_id"])
        items.append(validated)
    result = copy.deepcopy(dict(value))
    result["proposals"] = [copy.deepcopy(dict(item)) for item in proposals]
    result["items"] = items
    if int(result.get("selected_region_count", -1)) != len(proposals):
        raise ValueError("crop reread selected count is invalid")
    if int(result.get("accepted_item_count", -1)) != len(items):
        raise ValueError("crop reread accepted count is invalid")
    if int(result.get("external_call_count", 0)) not in {0, 1}:
        raise ValueError("crop reread external call count is invalid")
    if int(result.get("retry_count", 0)) != 0:
        raise ValueError("crop reread retry count is invalid")
    return result


def _valid_pp_regions(pp_evidence: Mapping[str, Any]) -> list[dict[str, Any]]:
    regions = pp_evidence.get("regions") if isinstance(pp_evidence, Mapping) else None
    if not isinstance(regions, list):
        return []
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for region in regions:
        if not isinstance(region, Mapping):
            continue
        evidence_id = str(region.get("evidence_id") or "")
        bbox = region.get("bbox")
        if not _safe_id(evidence_id) or evidence_id in seen or not _valid_bbox(bbox):
            continue
        seen.add(evidence_id)
        result.append(
            {
                "evidence_id": evidence_id,
                "text": str(region.get("text") or ""),
                "bbox": [float(value) for value in bbox],
            }
        )
    return result


def _uncertain_priority(card: Mapping[str, Any]) -> int:
    priority = 1
    if card.get("layout_suggestion") == "column":
        priority += 4
    if card.get("physical_boundary_evidence"):
        priority += 4
    raw_text = str(card.get("raw_text") or "")
    if "\n" in raw_text:
        priority += 4
    if card.get("nested_group_evidence") == "partial":
        priority += 6
    elif card.get("nested_group_evidence") is None:
        priority += 3
    if _OPERATOR_RE.search(raw_text) and card.get("layout_suggestion") != "column":
        priority += 4
    if str(card.get("continuation_suggestion") or "unclear") == "unclear":
        priority += 1
    special_known = str(card.get("special_play_raw") or "none").lower() not in {
        "",
        "none",
        "unclear",
    }
    if special_known:
        priority += 2
    if not special_known and _PROBABLE_SPECIAL_SHAPE_RE.fullmatch(raw_text):
        priority += 7
    if card.get("isolated_multiplier_number_literals"):
        priority += 2
    return priority


def _unique_crop_for_card(
    card: Mapping[str, Any],
    card_index: int,
    regions: list[dict[str, Any]],
    *,
    median_height: float,
    image_width: int,
    image_height: int,
    grid: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    groups = card.get("number_groups_suggestion")
    card_numbers = [
        str(number)
        for group in groups if isinstance(groups, list) and isinstance(group, list)
        for number in group
        if isinstance(number, str)
    ] if isinstance(groups, list) else []
    if not card_numbers:
        card_numbers = _literal_numbers(str(card.get("raw_text") or ""))
    card_counter = Counter(card_numbers)
    if not card_counter:
        return None
    raw_text = str(card.get("raw_text") or "")
    special_source = raw_text + " " + str(card.get("special_play_raw") or "")
    special_literals = _special_literals(special_source)
    special_markers = set(_SPECIAL_RE.findall(special_source))
    location_signature = (
        _location_signature(raw_text)
        if _PROBABLE_SPECIAL_SHAPE_RE.fullmatch(raw_text)
        else ""
    )
    candidates: dict[tuple[str, ...], dict[str, Any]] = {}
    for anchor in regions:
        anchor_counter = Counter(_literal_numbers(anchor["text"]))
        anchor_overlap = sum((card_counter & anchor_counter).values())
        anchor_special_literals = _special_literals(anchor["text"])
        special_match = bool(
            (special_literals & anchor_special_literals)
            if special_literals
            else (special_markers & set(_SPECIAL_RE.findall(anchor["text"])))
        )
        if anchor_overlap == 0 and not special_match:
            continue
        neighbors: list[dict[str, Any]] = []
        ax1, ay1, ax2, ay2 = anchor["bbox"]
        acx, acy = (ax1 + ax2) / 2.0, (ay1 + ay2) / 2.0
        anchor_cell = _grid_cell_index(acx, grid)
        vertical_interval = _grid_vertical_interval(
            anchor_cell,
            acy,
            grid,
            maximum_height=max(190.0, median_height * 3.4),
        )
        for region in regions:
            rx1, ry1, rx2, ry2 = region["bbox"]
            rcx, rcy = (rx1 + rx2) / 2.0, (ry1 + ry2) / 2.0
            if anchor_cell is not None and _grid_cell_index(rcx, grid) != anchor_cell:
                continue
            if vertical_interval is not None:
                vertical_ok = vertical_interval[0] < rcy < vertical_interval[1]
            else:
                vertical_ok = abs(rcy - acy) <= max(95.0, median_height * 2.0)
            horizontal_ok = (
                _axis_overlap((ax1, ax2), (rx1, rx2)) > 0
                or abs(rcx - acx) <= max(180.0, (ax2 - ax1) * 0.8)
            )
            if not (vertical_ok and horizontal_ok):
                continue
            region_counter = Counter(_literal_numbers(region["text"]))
            if sum((card_counter & region_counter).values()) or (
                special_literals & _special_literals(region["text"])
            ) or (
                not special_literals
                and special_markers & set(_SPECIAL_RE.findall(region["text"]))
            ) or (
                card.get("isolated_multiplier_number_literals")
                and _MULTIPLIER_RE.search(region["text"])
            ):
                neighbors.append(region)
        if not neighbors:
            continue
        ids = tuple(sorted(region["evidence_id"] for region in neighbors))
        combined_counter: Counter[str] = Counter()
        for region in neighbors:
            combined_counter.update(_literal_numbers(region["text"]))
        overlap = sum((card_counter & combined_counter).values())
        coverage = overlap / max(sum(card_counter.values()), 1)
        precision = overlap / max(sum(combined_counter.values()), 1)
        combined_special_literals = {
            literal for region in neighbors for literal in _special_literals(region["text"])
        }
        matched_special = bool(
            (special_literals & combined_special_literals)
            if special_literals
            else (
                special_markers
                & {
                    marker
                    for region in neighbors
                    for marker in _SPECIAL_RE.findall(region["text"])
                }
            )
        )
        combined_location_signature = _location_signature(
            " ".join(region["text"] for region in neighbors)
        )
        matched_location_signature = bool(
            location_signature
            and combined_location_signature == location_signature
        )
        score = overlap * 4.0 + coverage * 4.0 + precision * 2.0 + (6.0 if matched_special else 0.0)
        if matched_location_signature:
            score += 10.0
        if card.get("layout_suggestion") == "column" and any(_OPERATOR_RE.search(region["text"]) for region in neighbors):
            score += 1.0
        existing = candidates.get(ids)
        if existing is None or score > existing["score"]:
            candidates[ids] = {
                "score": score,
                "coverage": coverage,
                "regions": neighbors,
                "cell_index": anchor_cell,
                "vertical_interval": vertical_interval,
            }
    ranked = sorted(candidates.values(), key=lambda item: (-item["score"], -item["coverage"]))
    if not ranked:
        return None
    best = ranked[0]
    minimum_overlap = min(2, sum(card_counter.values()))
    best_counter: Counter[str] = Counter()
    for region in best["regions"]:
        best_counter.update(_literal_numbers(region["text"]))
    if sum((card_counter & best_counter).values()) < minimum_overlap and not special_markers:
        return None
    if best["coverage"] < 0.32:
        return None
    if len(ranked) > 1 and ranked[1]["score"] >= best["score"] * 0.9:
        first_box = _envelope([region["bbox"] for region in best["regions"]])
        second_box = _envelope([region["bbox"] for region in ranked[1]["regions"]])
        if _bbox_iou(first_box, second_box) < 0.5:
            return None
    envelope = _envelope([region["bbox"] for region in best["regions"]])
    if best["cell_index"] is not None and best["vertical_interval"] is not None:
        bbox = _bounded_grid_cell_bbox(
            best["cell_index"],
            best["vertical_interval"],
            grid,
            image_width=image_width,
            image_height=image_height,
        )
    else:
        bbox = _snap_to_grid_cell(
            envelope,
            grid,
            median_height=median_height,
            image_width=image_width,
            image_height=image_height,
        )
    if not _valid_bbox(bbox):
        return None
    return {
        "crop_id": "",
        "draft_id": str(card.get("draft_id") or f"review-card-{card_index + 1:04d}"),
        "bbox": bbox,
        "linked_pp_evidence_ids": [region["evidence_id"] for region in best["regions"]],
        "selection_score": round(float(best["score"]), 6),
        "selection_reason": "UNIQUE_LITERAL_TO_PP_GEOMETRY",
        "priority": _uncertain_priority(card),
        "human_truth_used": False,
    }


def _literal_numbers(value: str) -> list[str]:
    numbers = _NUMBER_RE.findall(value)
    for run in _DIGIT_RUN_RE.findall(value):
        parts = [run[index : index + 2] for index in range(0, len(run), 2)]
        if all(re.fullmatch(r"(?:0[1-9]|[12]\d|3[0-9])", part) for part in parts):
            numbers.extend(part for part in parts if part not in numbers)
    return numbers


def _special_literals(value: str) -> set[str]:
    return {
        re.sub(r"\s+", "", match.group(0))
        for match in _SPECIAL_VALUE_RE.finditer(value)
    }


def _location_signature(value: str) -> str:
    """Machine-literal signature used only to select a place to magnify."""

    return "".join(re.findall(r"\d", value))


def _detect_red_grid(
    image: Image.Image,
    regions: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Detect only strong drawn cell separators; absence is not an error."""

    if not regions:
        return None
    width, height = image.size
    pixels = image.load()
    left = max(0, math.floor(min(region["bbox"][0] for region in regions) - 20))
    right = min(width, math.ceil(max(region["bbox"][2] for region in regions) + 20))
    top = max(0, math.floor(min(region["bbox"][1] for region in regions) - 20))
    bottom = min(height, math.ceil(max(region["bbox"][3] for region in regions) + 20))

    def is_red(x: int, y: int) -> bool:
        red, green, blue = pixels[x, y]
        return (
            red > 60
            and red - green > 16
            and red - blue > 7
            and red > green * 1.17
        )

    def is_red_separator(x: int, y: int) -> bool:
        red, green, blue = pixels[x, y]
        return (
            red > 45
            and red - green > 8
            and red - blue > 3
            and red > green * 1.08
        )

    x_scores = [
        sum(int(is_red(x, y)) for y in range(top, bottom))
        for x in range(left, right)
    ]
    x_threshold = max(22, round((bottom - top) * 0.03))
    x_peaks = [
        left + index
        for index in _group_peak_indexes(x_scores, x_threshold, maximum_gap=7)
    ]
    x_peaks = [value for value in x_peaks if left + 45 < value < right - 45]
    if x_peaks:
        clustered: list[list[int]] = [[x_peaks[0]]]
        for value in x_peaks[1:]:
            if value - clustered[-1][-1] < 70:
                clustered[-1].append(value)
            else:
                clustered.append([value])
        # A close run represents the left-to-right thickness or gap of one
        # divider.  Use its first edge so the crop on the left cannot include
        # visible content from the neighboring physical bet.
        x_peaks = [min(group) for group in clustered]
    if not x_peaks:
        return None
    x_bounds = [left, *sorted(x_peaks), right]
    y_by_cell: list[list[int]] = []
    for cell_index in range(len(x_bounds) - 1):
        cell_left = x_bounds[cell_index] + 4
        cell_right = x_bounds[cell_index + 1] - 4
        if cell_right - cell_left < 40:
            y_by_cell.append([])
            continue
        y_scores = [
            sum(int(is_red_separator(x, y)) for x in range(cell_left, cell_right))
            for y in range(top, bottom)
        ]
        # A separator must span a material fraction of the physical column.
        # Sparse red handwriting is not a row boundary.
        y_threshold = max(8, round((cell_right - cell_left) * 0.14))
        y_peaks = [
            top + index
            for index in _group_peak_indexes(y_scores, y_threshold, maximum_gap=2)
        ]
        if y_peaks:
            clustered_y: list[list[int]] = [[y_peaks[0]]]
            for value in y_peaks[1:]:
                if value - clustered_y[-1][-1] < 35:
                    clustered_y[-1].append(value)
                else:
                    clustered_y.append([value])
            y_peaks = [
                max(group, key=lambda value: y_scores[value - top])
                for group in clustered_y
            ]
        y_by_cell.append(
            [value for value in y_peaks if top + 25 < value < bottom - 25]
        )
    return {
        "x_bounds": x_bounds,
        "y_by_cell": y_by_cell,
        "top": top,
        "bottom": bottom,
    }


def _group_peak_indexes(
    scores: list[int],
    threshold: int,
    *,
    maximum_gap: int,
) -> list[int]:
    candidates = [index for index, score in enumerate(scores) if score >= threshold]
    if not candidates:
        return []
    groups: list[list[int]] = [[candidates[0]]]
    for index in candidates[1:]:
        if index - groups[-1][-1] <= maximum_gap:
            groups[-1].append(index)
        else:
            groups.append([index])
    return [max(group, key=lambda index: scores[index]) for group in groups]


def _grid_cell_index(
    center_x: float,
    grid: Mapping[str, Any] | None,
) -> int | None:
    if not isinstance(grid, Mapping):
        return None
    bounds = grid.get("x_bounds")
    if not isinstance(bounds, list):
        return None
    for index in range(len(bounds) - 1):
        if bounds[index] <= center_x <= bounds[index + 1]:
            return index
    return None


def _grid_vertical_interval(
    cell_index: int | None,
    center_y: float,
    grid: Mapping[str, Any] | None,
    *,
    maximum_height: float,
) -> tuple[int, int] | None:
    """Return the single drawn row containing an anchor center.

    This is deliberately geometry-only.  A crop can contain multiple written
    lines, but it may never cross a detected physical cell separator merely
    because matching OCR literals occur in an adjacent bet.
    """

    if cell_index is None or not isinstance(grid, Mapping):
        return None
    y_by_cell = grid.get("y_by_cell")
    if not isinstance(y_by_cell, list) or cell_index >= len(y_by_cell):
        return None
    lines = y_by_cell[cell_index]
    if not isinstance(lines, list):
        return None
    boundaries = sorted(
        {
            int(grid.get("top", 0)),
            int(grid.get("bottom", 0)),
            *(int(value) for value in lines if isinstance(value, int)),
        }
    )
    for top, bottom in zip(boundaries, boundaries[1:]):
        if top <= center_y <= bottom and bottom - top >= 35:
            if bottom - top > maximum_height:
                half_height = maximum_height / 2.0
                return (
                    max(top, math.floor(center_y - half_height)),
                    min(bottom, math.ceil(center_y + half_height)),
                )
            return top, bottom
    return None


def _bounded_grid_cell_bbox(
    cell_index: int,
    vertical_interval: tuple[int, int],
    grid: Mapping[str, Any] | None,
    *,
    image_width: int,
    image_height: int,
) -> list[int]:
    if not isinstance(grid, Mapping):
        raise ValueError("grid is required")
    x_bounds = grid.get("x_bounds")
    if not isinstance(x_bounds, list) or cell_index + 1 >= len(x_bounds):
        raise ValueError("grid cell is invalid")
    top, bottom = vertical_interval
    return [
        max(0, int(x_bounds[cell_index]) + 3),
        max(0, int(top) - 6),
        min(image_width, int(x_bounds[cell_index + 1]) - 3),
        min(image_height, int(bottom) + 12),
    ]


def _snap_to_grid_cell(
    envelope: list[float],
    grid: Mapping[str, Any] | None,
    *,
    median_height: float,
    image_width: int,
    image_height: int,
) -> list[int]:
    pad_x = max(20.0, median_height * 0.45)
    pad_y = max(24.0, median_height * 0.65)
    center_x = (envelope[0] + envelope[2]) / 2.0
    cell_index = _grid_cell_index(center_x, grid)
    if cell_index is None or not isinstance(grid, Mapping):
        return [
            max(0, math.floor(envelope[0] - pad_x)),
            max(0, math.floor(envelope[1] - pad_y)),
            min(image_width, math.ceil(envelope[2] + pad_x)),
            min(image_height, math.ceil(envelope[3] + pad_y)),
        ]
    x_bounds = grid["x_bounds"]
    y_lines = list(grid["y_by_cell"][cell_index])
    top_limit = int(grid["top"])
    bottom_limit = int(grid["bottom"])
    upper = [value for value in y_lines if value <= envelope[1] + 8]
    lower = [value for value in y_lines if value >= envelope[3] - 8]
    top = max(upper) if upper else max(top_limit, math.floor(envelope[1] - pad_y))
    bottom = min(lower) if lower else min(bottom_limit, math.ceil(envelope[3] + pad_y))
    if bottom - top < max(45, median_height):
        top = max(top_limit, math.floor(envelope[1] - pad_y))
        bottom = min(bottom_limit, math.ceil(envelope[3] + pad_y))
    return [
        max(0, int(x_bounds[cell_index]) + 3),
        max(0, top + 3),
        min(image_width, int(x_bounds[cell_index + 1]) - 3),
        min(image_height, bottom - 3),
    ]


def _request_payload(image_bytes: bytes, config: CropRereadConfig) -> dict[str, Any]:
    return {
        "contents": [
            {
                "parts": [
                    {"text": PROMPT},
                    {
                        "inline_data": {
                            "mime_type": "image/png",
                            "data": base64.b64encode(image_bytes).decode("ascii"),
                        }
                    },
                ]
            }
        ],
        "generationConfig": {
            "temperature": config.temperature,
            "maxOutputTokens": config.max_output_tokens,
            "responseMimeType": "application/json",
            "responseJsonSchema": RESPONSE_SCHEMA,
            "thinkingConfig": {"thinkingLevel": "minimal"},
        },
    }


def _post_generate_content(
    payload: dict[str, Any], api_key: str, config: CropRereadConfig
) -> dict[str, Any]:
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    request = urllib.request.Request(
        config.endpoint,
        data=body,
        method="POST",
        headers={"Content-Type": "application/json", "x-goog-api-key": api_key},
    )
    with urllib.request.urlopen(request, timeout=config.timeout_seconds) as response:
        return json.loads(response.read().decode("utf-8"))


def _validated_response(
    envelope: Mapping[str, Any], base: Mapping[str, Any]
) -> dict[str, Any]:
    candidates = envelope.get("candidates") if isinstance(envelope, Mapping) else None
    if not isinstance(candidates, list) or len(candidates) != 1:
        raise ValueError("crop reread response candidate is invalid")
    candidate = candidates[0]
    if not isinstance(candidate, Mapping) or candidate.get("finishReason") != "STOP":
        raise ValueError("crop reread response did not finish")
    parts = ((candidate.get("content") or {}).get("parts") or [])
    texts = [
        part.get("text")
        for part in parts
        if isinstance(part, Mapping)
        and isinstance(part.get("text"), str)
        and not part.get("thought")
    ]
    if len(texts) != 1:
        raise ValueError("crop reread response text is invalid")
    decoded = json.loads(texts[0])
    if not isinstance(decoded, Mapping) or set(decoded) != {"version", "items"}:
        raise ValueError("crop reread response root is invalid")
    if decoded.get("version") != "gemma-crop-reread-v2" or not isinstance(decoded.get("items"), list):
        raise ValueError("crop reread response schema is invalid")
    proposal_ids = {str(item["crop_id"]) for item in base["proposals"]}
    items: list[dict[str, Any]] = []
    rejected: list[dict[str, str]] = []
    for position, item in enumerate(decoded["items"], 1):
        try:
            items.append(_validated_item(item, proposal_ids))
        except ValueError as exc:
            rejected.append({"item_index": str(position), "reason_code": str(exc)[:80]})
    return validate_crop_reread_evidence(
        {
            **copy.deepcopy(dict(base)),
            "status": "completed",
            "items": items,
            "accepted_item_count": len(items),
            "rejected_item_count": len(rejected),
            "rejected_items": rejected,
            "provider_request_id": str(envelope.get("responseId") or ""),
            "finish_reason": "STOP",
            "usage": copy.deepcopy(envelope.get("usageMetadata") or {}),
            "raw_response_sha256": hashlib.sha256(texts[0].encode("utf-8")).hexdigest(),
            "cache_hit": False,
            "external_call_count": 1,
            "retry_count": 0,
            "latency_ms": 0.0,
        }
    )


def _validated_item(item: Any, proposal_ids: set[str]) -> dict[str, Any]:
    if not isinstance(item, Mapping) or set(item) != _ITEM_KEYS:
        raise ValueError("CROP_ITEM_SCHEMA_INVALID")
    crop_id = str(item.get("crop_id") or "")
    if crop_id not in proposal_ids:
        raise ValueError("CROP_ITEM_ID_INVALID")
    for key in ("raw_text", "numbers", "multiplier_text", "special_text", "uncertain_reason"):
        if not isinstance(item.get(key), str):
            raise ValueError("CROP_ITEM_LITERAL_INVALID")
    groups = item.get("number_groups")
    if not isinstance(groups, list) or any(
        not isinstance(group, list)
        or not group
        or any(
            not isinstance(number, str)
            or (
                _NUMBER_RE.fullmatch(number) is None
                and re.fullmatch(r"\d", number) is None
            )
            for number in group
        )
        for group in groups
    ):
        raise ValueError("CROP_ITEM_NUMBER_GROUPS_INVALID")
    flattened_groups = [number for group in groups for number in group]
    raw_multiplier_context = " ".join(
        (str(item.get("raw_text") or ""), str(item.get("multiplier_text") or ""))
    )
    if "05" in flattened_groups and re.search(
        r"[xX×]\s*0?(?:\.)?5(?!\d)", raw_multiplier_context
    ):
        raise ValueError("CROP_ITEM_MULTIPLIER_NUMBER_CONTAMINATION")
    rules = item.get("multiplier_rules")
    if not isinstance(rules, list) or any(
        not isinstance(rule, str) or not rule.strip() for rule in rules
    ):
        raise ValueError("CROP_ITEM_MULTIPLIER_RULES_INVALID")
    operators = item.get("visible_operators")
    if not isinstance(operators, list) or any(
        operator not in {"x", "X", "×"} for operator in operators
    ):
        raise ValueError("CROP_ITEM_OPERATORS_INVALID")
    if item.get("layout_guess") not in {"normal", "column", "unclear"}:
        raise ValueError("CROP_ITEM_LAYOUT_INVALID")
    if len(groups) > 1 and item.get("layout_guess") != "column":
        raise ValueError("CROP_ITEM_LAYOUT_GROUP_CONFLICT")
    if item.get("layout_guess") == "column" and len(groups) < 2:
        raise ValueError("CROP_ITEM_LAYOUT_GROUP_CONFLICT")
    if item.get("continuation") not in {"yes", "no", "unclear"}:
        raise ValueError("CROP_ITEM_CONTINUATION_INVALID")
    if item.get("cancelled") not in {"yes", "no", "unclear"}:
        raise ValueError("CROP_ITEM_CANCELLED_INVALID")
    if not isinstance(item.get("uncertain"), bool):
        raise ValueError("CROP_ITEM_UNCERTAIN_INVALID")
    return copy.deepcopy(dict(item))


def _base_evidence(
    request: RecognitionRequest, proposals: list[dict[str, Any]]
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "provider_id": PROVIDER_ID,
        "model": MODEL_NAME,
        "request_schema_version": REQUEST_SCHEMA_VERSION,
        "image_sha256": str(request.metadata.get("sha256") or ""),
        "prompt_sha256": PROMPT_SHA256,
        "proposals": copy.deepcopy(proposals),
        "selected_region_count": len(proposals),
        "items": [],
        "accepted_item_count": 0,
        "rejected_item_count": 0,
        "rejected_items": [],
        "evidence_only": True,
        "machine_suggestion": True,
        "human_confirmed": False,
        "human_confirmation_required": True,
        "value_authority": "human_confirmed_answer",
        "auto_apply": False,
        "auto_confirm": False,
        "auto_submit": False,
    }


def _failure(
    base: Mapping[str, Any],
    code: str,
    started: float,
    *,
    status: str = "failed",
    detail: str | None = None,
    external_call_count: int = 0,
) -> dict[str, Any]:
    error: dict[str, str] = {"code": code}
    if detail:
        error["detail"] = detail[:120]
    return {
        **copy.deepcopy(dict(base)),
        "status": status,
        "error": error,
        "cache_hit": False,
        "external_call_count": min(max(int(external_call_count), 0), MAX_EXTERNAL_CALLS),
        "retry_count": RETRY_COUNT,
        "latency_ms": round((time.perf_counter() - started) * 1000.0, 3),
    }


def _valid_bbox(value: Any) -> bool:
    return (
        isinstance(value, (list, tuple))
        and len(value) == 4
        and all(not isinstance(item, bool) and isinstance(item, (int, float)) for item in value)
        and value[0] >= 0
        and value[1] >= 0
        and value[2] > value[0]
        and value[3] > value[1]
    )


def _envelope(values: list[list[float]]) -> list[float]:
    return [
        min(value[0] for value in values),
        min(value[1] for value in values),
        max(value[2] for value in values),
        max(value[3] for value in values),
    ]


def _axis_overlap(first: tuple[float, float], second: tuple[float, float]) -> float:
    return max(0.0, min(first[1], second[1]) - max(first[0], second[0]))


def _bbox_iou(first: list[float], second: list[float]) -> float:
    intersection = _axis_overlap((first[0], first[2]), (second[0], second[2])) * _axis_overlap(
        (first[1], first[3]), (second[1], second[3])
    )
    if intersection <= 0:
        return 0.0
    first_area = (first[2] - first[0]) * (first[3] - first[1])
    second_area = (second[2] - second[0]) * (second[3] - second[1])
    return intersection / max(first_area + second_area - intersection, 1.0)


def _safe_id(value: Any) -> bool:
    return isinstance(value, str) and _SAFE_ID_RE.fullmatch(value) is not None


def _canonical_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
