# Verified-bet lossless representation V1

This is an offline Human Truth renderer/proof contract, not an OCR reader or
value-authority change. Existing parser syntax and ordinary pasted-text behavior
are unchanged. No provider, prompt, Candidate/Queue/Claim/Prepare/Mapping or
Sandbox integration is added.

## Contract

`render_lossless_human_truth(truth, game=...)` returns canonical active text,
source semantic records, source SHA and one binding per expanded text line.
`prove_lossless_round_trip(truth, rendered, game=...)` requires the independent
original truth, checks all bindings, reparses each complete line and the entire
text through the existing production parser, and deep-compares reconstructed
semantics. Successful parsing alone is not an exact proof.

Physical identity, cancellation, shared scopes and continuation are not encoded
in a new text dialect. They are carried by a source-bound sidecar. Plain copied
text alone cannot recover these relationships. The proof explicitly identifies
which fields are parser-derived and which are source-bound. Bindings and the
original source SHA are persisted in the migration proof.

## Canonical syntax (already supported)

- Normal multi-rule: `01 07 19 2 × 5` and `01 07 19 3 × 0.5` on separate lines,
  both bound to one physical record. No orphan multiplier lines.
- Column multi-rule: repeat the complete nested column expression for each rule.
  Unequal column heights are retained. Column values are never flattened.
- Single car: `11車0.5支`.
- Each car: `11 33 各 0.5車`. Human `半車`/`各半車` renders the explicitly defined
  half unit; its original literal and each/half semantics remain in the sidecar.
- Tail: `03 × 16 × 7尾 2,3 × 0.5`. The tail digit is a special field, not an
  ordinary betting number. Parser expansion is checked against the selected game.

Bare `各`, `特尾` without an existing unambiguous parser representation, unknown
special forms, missing/zero/unresolved multipliers and conflicting rule maps fail
closed. The pre-existing parser's `3×5` single-number car shorthand remains legal;
it cannot pass this contract as an orphan multiplier because its source binding,
type and scope do not match.

## Scopes and source authority

Shared rules require nonempty, unique `applies_to_line_ids` referring to active
source records. Boundary uncertainty, unresolved values, overlapping category
scopes and missing targets are rejected. No previous/next/page scope is guessed.

Cross-record continuation requires `continuation_from`, explicit
`continuation_scope` categories, an earlier active source record, and an identical
rule map for the referenced categories. A naked `continuation=true` is insufficient.

Existing promoted Human Answer objects may prove **within-record** continuation
when the confirmation, normal scope, source human bet ID, deterministic-projection
provenance and embedded multiplier map agree. This does not infer inheritance
from a neighboring record. In both forms the active text contains complete bets.

Cancelled records never enter active text. Their entire source objects, counts
and provenance remain audit metadata and are compared against the original truth.
There is no executable cancelled syntax or automatic confirmation.

## Game and migration

The new contract requires `game="539"` or `game="六合"`; source declarations must
agree. The existing backfill uses explicit source `game`, or the declared
`539-semantic-gt-v1` source schema. It never infers 六合 from an out-of-range 539
number. Unknown/conflicting declarations fail. A caller can supply an explicit
`game_by_sample` only when it does not contradict the declared source game.

Legacy render/proof APIs remain available for historical report reproduction;
new structured backfills use V1. Old source truth, captures and historical reports
are not rewritten. Revisions remain SHA-deduplicated.

Known limitations: the current parser expands tails only through 39 even under
六合. V1 rejects that semantic mismatch rather than changing established game
rules silently. Text-only truth without explicit shared/physical scope cannot
receive a structured physical-identity proof. Historical `multiplier_conflict`
records stay rejected even if the old renderer produced parseable text.

These tests prove representation fidelity, not image recognition accuracy.
