# Optional region-assisted review

The default image-to-editable-text flow is unchanged. `開啟區域檢查` opens an
inline helper, not a second page or a mandatory per-bet approval workflow.
No provider, parser grammar, lossless contract, Candidate/Queue or fill authority
is changed. This service makes no model calls and never creates a batch.

## Reuse and boundaries

The service uses `semantic_roi.SemanticRoiGroup` and its verification status
constants, the existing ROI schema's **original pixel `[x,y,width,height]`**
coordinates, existing image intake/storage, and `service.preflight_image_text`.
The standalone `roi_annotator` uses the same semantic ROI model. Its old
Closed Set verification/parser is not copied into this product path.
`region_edit` performs a legacy column-semantic pipeline edit; it is deliberately
not invoked because this helper must not infer columns or multiplier scopes.

There is no unique runtime ROI-to-text evidence on ordinary uploads, so the
initial proposal is one whole-image region. Only that whole-image region may
receive the current full editable draft. No truth files or filename/SHA answer
lookups are used. SHA is used only for storage identity/integrity.

## Edits and confirmation

- Drag to add, move or resize the lower-right corner; zoom never changes saved
  pixel coordinates. Horizontal/vertical splits cut the region at its midpoint.
- Splitting leaves both child texts blank. The complete old draft is retained
  in immutable history; no text is duplicated or guessed into child regions.
- Merge requires an exact edge-adjacent rectangular union, and matching cancelled
  states. It concatenates the two existing texts in reading order, unconfirmed.
  Non-adjacent/L-shaped unions are rejected rather than absorbing another region.
- Text, bbox, reading-order, game, split, merge and cancellation changes revoke
  confirmation. Regions whose reading positions change are also reset.
- Explicit confirmation runs the existing parser for active regions. Failed
  text remains visible and unverified. Confirmation of cancellation preserves
  its literal text as cancelled audit data, not as an active bet.
- Cancellation must itself be confirmed before exclusion. Restoring it resets
  confirmation. Deletion archives the region and never confirms other regions.
- A session revision check rejects stale requests from another tab.

## Assembly

All remaining regions, including cancellation decisions, must be confirmed.
Active texts are joined with blank lines in reading order. Both each region and
the complete assembled document are parsed; parser results must agree so a
multiplier cannot acquire a new cross-region scope. Errors carry region IDs and
line mappings; the UI renders clickable region numbers, not internal IDs.

Failure returns no partial output. Success updates only the existing editable
textarea and schedules its usual preflight. The user must still explicitly
click the existing assisted-fill button. No automatic confirmation or Submit.

## Private dataset and timing

`runs/region-assisted-review-dataset-v1/<image-sha>/<session-id>/revision-*.json`
contains append-only snapshots, including retired regions, initial text, current
text, per-region revision, confirmation, cancellation, order and crop hash.
An exact private copy of the original source image is retained under its SHA
directory because ordinary uploaded images can expire. Original pixels are
never modified. Crops need not be duplicated: they are reproducible from that
source and bbox. Crop hashes cover `RGB:width:height:` plus RGB pixel bytes.

Unconfirmed snapshots have `human_verified=false` and no verified-text value;
future training must filter for explicit human verification, not merely a saved
snapshot. Old verified revisions remain historical evidence when later edits
invalidate the current revision. No records become acceptance benchmark truth
automatically. `runs/` is ignored by Git; no private data belongs in commits.

Timing records wall-clock session start/completion and operation counters.
Completion means successful explicit assembly, not assisted fill or bet execution.
Automated browser test timing is synthetic and must not be reported as human
median/p95. Real speed/accuracy acceptance needs 5–10 user-operated images.
