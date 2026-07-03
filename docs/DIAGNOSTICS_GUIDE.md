# Diagnostics Guide

How to read the map-dry-run selector diagnostics, and how to decide what (if
anything) needs a human follow-up. This is a reading guide — it does not enable
any fill action.

## Top-level result: SAFE vs BLOCKED

- **SAFE** — every required selector was found and proven unique enough to be a
  reliable target. SAFE still never means "auto fill"; it only means the mapping
  is unambiguous. Execution remains gated and manual.
- **BLOCKED** — at least one selector is missing, not specific, or shared. This is
  the correct, safe default whenever there is any doubt. **BLOCKED is a valid
  result, not an error to be forced green.**

## Per-action fields

- **`selector_found`** — whether any candidate selector was located for the step.
  `false` means nothing matched at all.
- **`confidence`** — `high` when the selector is unique/specific, `low` when it is
  generic or shared, `blocked` when nothing was found.
- **`selector_unsafe`** — `true` when a selector was found but cannot be trusted:
  either not specific to the target, or shared with another target.

## Amount-field diagnostics

When amount fields (二星 / 三星 / 四星) are ambiguous, the report adds a dedicated
block:

- **`amount_field_status`** — `BLOCKED` or `SAFE` for the amount fields as a group.
- **`ambiguous_amount_fields`** — the stars whose amount selector is unsafe, e.g.
  `["二星", "三星"]`.
- **`shared_amount_selectors`** — selectors that map to more than one star, e.g.
  `["#GroupSet_Value"]`.
- **`amount_field_diagnostics`** — one entry per problematic star, with:
  `star`, `selector`, `confidence`, `shared`, `shared_with`, `source_index`,
  `blocked_reason`, `diagnostic_source`, and bounded context excerpts (`id`,
  `name`, `className`, `parentText`, `grandparentText`, `outerHTML`). `outerHTML`
  is truncated; the report never dumps the full element.

### What "二星 / 三星 share `#GroupSet_Value`" means

Both stars resolved to the *same* input id. A single shared field cannot be a
reliable per-star target, so it cannot be proven unique. The mapping therefore
marks both stars unsafe and keeps the report BLOCKED. This is expected: the page
uses one group-value input rather than one input per star, and the diagnostics are
surfacing that fact rather than guessing.

### Why BLOCKED can be the correct answer

If the real page has no distinct, unique amount field per star, there is no safe
selector to choose. Reporting BLOCKED — and explaining *why* via the diagnostics —
is the safe outcome. Forcing it SAFE would risk filling the wrong field.

## Using diagnostics after a 539 market-open scan

1. Run the read-only site profile + `map-dry-run` for the open 539 market.
2. Read the top-level **Status**. If SAFE, numbers and amounts mapped uniquely.
3. If **BLOCKED**, open the **Amount Field Diagnostics** section:
   - Check `ambiguous_amount_fields` and `shared_amount_selectors`.
   - For each entry, read `blocked_reason` and the `shared_with` list.
   - Use `source_index`, `parentText` / `grandparentText`, and the `outerHTML`
     excerpt to understand which real element the selector hit.

## Before considering a manual amount-mapping override

Manual override is **not implemented yet** and remains gated. Before it would ever
be worth designing one, confirm from the diagnostics:

- the selector is shared (`shared: true`) rather than simply missing,
- the context excerpts show a real, distinct per-star input actually exists,
- number selectors are still `high` / unique (the number path is unaffected),
- and BLOCKED is caused only by the shared amount field, not by other errors.

If a distinct per-star field does **not** exist in the context, BLOCKED is correct
and no override should be attempted.
