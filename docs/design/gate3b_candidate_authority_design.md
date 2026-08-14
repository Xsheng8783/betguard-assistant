# Gate 3B Candidate Authority Design

Status: design only. This design does not authorize queue, fill, submit, or any
production Candidate write.

## Audited current boundaries

Gate 3A `_buildQwenReviewSummary()` produces a browser-local
`vision-review-candidate-preview-v1` and
`registration_state=candidate_boundary_ready_not_created`. It has no
server-owned revision, freshness hash, event log, idempotency, or persistence.
It is a display/edit surface, not authority. Client bets and client
`human_confirmed` flags must never be registered directly.

Legacy `/manual-reparse` is not a safe foundation. Its default
`register_candidate=true` writes random IDs to process-memory
`_manual_candidates` and sets `accepted_by_human=true`. It has no persistence,
revision, hash, or reload semantics. Assist handlers can also convert legacy
manual or valid queue data into accepted fill data. The new Candidate domain
must be isolated from `/manual-reparse`, `_manual_candidates`, assist-fill,
`approved_fill_queue`, and webfill.

`vision/assistive_session.py` is useful precedent only: edits clear final
confirmation/hashes, confirmation requires no blocker, freshness is recomputed,
and writes are atomic. Gate 3B needs equivalent server ownership for structured
HumanReview revisions and events.

## Authority flow

```text
Qwen / Gemma / PP machine evidence
  -> immutable evidence refs with value_authority=false
  -> browser Human Answer editor (untrusted expected state)
  -> server persists HumanReview values + append-only events
  -> explicit per-card confirmation on server
  -> persisted human_answer_revision + human_answer_hash
  -> client requests Candidate by expected revision/hash only
  -> server reloads HumanReview and constructs immutable Candidate snapshot
  -> append-only Candidate lifecycle event
  -> candidate-only result; no fill approval or queue write
```

Only the server-reloaded HumanReview supplies values. Machine evidence is
provenance only and has no field capable of supplying numbers, multiplier,
special play, continuation, cancellation, or bet type. The Candidate endpoint
must reject client-supplied bets.

## Exact Candidate contract

The normative snapshot is
`docs/schemas/vision_candidate_authority_v1.schema.json`.

Every active or cancelled bet has exactly these authority fields:

- `human_bet_id`, accepting real IDs such as `H-007`;
- `bet_type`;
- `number_groups`, always nested (normal is one group; column is two or more);
- `multiplier = {ordered_rules, scope, resolved}`;
- `special_play = {kind, raw_text, scope, resolved}`;
- `continuation = {present, resolved}`;
- `cancelled`, `active`, `executable`, and `value_authority`.

Active validated bets require `cancelled=false`, `active=true`,
`executable=true`, `value_authority=human_answer`, and all scopes resolved.
`candidate_only=true` and `approved_for_fill=false` still prevent execution at
the Candidate boundary. Cancelled bets are stored only under
`cancelled_audit`, with `cancelled=true`, `active=false`, and
`executable=false`; they never enter the execution projection.

The schema treats `game` as a non-empty string so encoding cannot corrupt a
Chinese value. The semantic validator, not mojibake-prone schema constants,
enforces supported games and number ranges.

Machine evidence refs contain provider/model/request/cache/artifact digests and
must carry `value_authority=false`. They cannot contain model values or raw
response content.

## Canonical hashes

Two hashes are mandatory and distinct:

1. `human_answer_hash` is stored by the HumanReview domain. It hashes the
   complete server-owned Human Answer value projection. It excludes machine
   evidence and volatile IDs. Its paired monotonic `human_answer_revision`
   detects edit-away/edit-back events.
2. `canonical_content_hash` is stored in the Candidate snapshot. It hashes the
   validated Candidate semantic projection: game, `source_image_hash`, ordered
   active Human Answers including every `human_bet_id`, cancelled audit
   decisions/answers including their `human_bet_id`, and safety semantics.

The Candidate content projection excludes `candidate_id`, `created_at`,
`revision`, `review_session_id`, `source_image_id`, machine evidence refs,
actors, previous revision hash, and the hash field. It includes
`source_image_hash` and `human_bet_id` because both are authority/audit identity.
The same reviewed image and Human Answer identities/values produce the same
content hash; changing an image hash or human bet ID changes it.

Canonical encoding rules:

1. validate before hashing and reject unknown fields/floats;
2. normalize strings to Unicode NFC;
3. sort object keys by Unicode code point;
4. UTF-8 JSON, `ensure_ascii=false`, `,`/`:` separators, no trailing newline;
5. preserve array order for bets, number groups, group members, and multiplier
   rules; never sort or deduplicate silently;
6. lowercase `sha256(canonical_bytes).hexdigest()`.

An optional record-integrity hash may cover the full stored snapshot, but it is
not content identity.

## Immutable snapshots and lifecycle

Candidate JSON is immutable and has `state_at_creation=CURRENT`. It never gets
edited to STALE, INVALIDATED, SUPERSEDED, or REVOKED. Those states are derived
from the append-only event ledger defined by
`vision_candidate_lifecycle_event_v1.schema.json`.

- First create stores candidate revision 1 and appends `CREATED`.
- A HumanReview edit after creation appends `STALE` for that revision.
- `INVALIDATED` is reserved for an integrity or authority invalidation, such as
  a failed immutable-record integrity check or proven authority corruption.
- A fresh confirmed replacement stores revision N+1, appends `SUPERSEDED` for
  N and `CREATED` for N+1, and keeps old JSON byte-for-byte unchanged.
- `REVOKED` is terminal in the derived view; records/events are never deleted.
- At most one revision derives as current.
- A future fill approval must bind candidate ID, revision, and content hash;
  invalidation/supersession/revocation makes that approval stale.

The HumanReview domain also uses append-only events. Every edit, AI-field
adoption, layout/cancellation change, card add/delete, confirm/unconfirm
increments `human_answer_revision` and clears ready state.

## Create, duplicate click, and reload

The future create request contains only:

```json
{
  "review_session_id": "...",
  "expected_human_answer_revision": 12,
  "expected_human_answer_hash": "...",
  "idempotency_key": "review_session_id:12:human_answer_hash"
}
```

The unique idempotency tuple is `(review_session_id,
human_answer_revision,human_answer_hash)`.

- Same tuple returns the same candidate ID/revision/hash with `replayed=true`.
- Same idempotency key with different request identity returns
  `409 IDEMPOTENCY_CONFLICT`.
- A double-click or lost-response retry never creates a duplicate.
- Reload reads the persisted HumanReview and idempotency result; browser memory
  is not authority.
- Storage uses atomic replace plus an append-only, sequence-checked event log.

## Stale and fail-closed rules

Revision/hash mismatch returns `409 REVIEW_STALE` with zero writes. Missing
confirmation, unresolved blockers/cancellation/scope, or no active bet returns
`422 CANDIDATE_NOT_READY`.

Other stable failures include `CANDIDATE_SCHEMA_INVALID`, `GAME_INVALID`,
`NUMBER_OUT_OF_RANGE`, `NUMBER_DUPLICATE`, `COLUMN_STRUCTURE_INVALID`,
`BET_TYPE_GROUP_MISMATCH`, `MULTIPLIER_INVALID`, `SCOPE_UNRESOLVED`,
`CANCELLED_IN_ACTIVE_BETS`, `MACHINE_VALUE_AUTHORITY_FORBIDDEN`,
`CONFIRMATION_STALE`, and `CANDIDATE_REVISION_CONFLICT`.

All failures are side-effect-free: no Candidate snapshot/event, legacy manual
candidate, queue/draft/ground-truth write, webfill, retry, auto-confirm, or
auto-submit.

## Minimal next implementation gate

1. Add an isolated typed `vision_candidate_authority` domain; never extend
   `_manual_candidates`.
2. Add an atomic persisted HumanReview snapshot plus append-only event store,
   revision compare-and-swap, and server-side per-card confirmation validation.
3. Add immutable Candidate snapshot storage, append-only lifecycle events, and
   the idempotency tuple index.
4. Add create/get endpoints that accept only review identity and expected
   revision/hash, then construct values server-side.
5. Wire the Gate 3A boundary only after stale/idempotency/zero-side-effect tests
   pass.
6. Do not add fill approval in that gate. It is a later explicit authority
   transition.
