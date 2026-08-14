# Gate 3B-3 Validated Candidate Queue Authority Test Matrix

Status: design-only test specification. No production Queue or WebFill code is
authorized by this document.

## Required positive contract tests

| ID | Scenario | Required result |
|---|---|---|
| P01 | Explicit human enqueue of a `VALID_CURRENT` Candidate | One immutable `QUEUED` entry; identity only; `replayed=false` |
| P02 | Same exact enqueue action tuple after lost response/reload | Same entry ID/sequence/hash; `replayed=true`; no second event |
| P03 | Expected Candidate revision/hash omitted from Gate 3B-2 internal validation | Entry still saves the exact server-returned revision/hash; public enqueue API should require expected identity to bind the human click |
| P04 | Three entries committed in non-monotonic clock/filename order | FIFO remains enqueue sequence 1, 2, 3 |
| P05 | Prepare valid FIFO head | Gate 3B-2 reruns; read-only envelope returned; entry and ledger remain unchanged in `QUEUED` |
| P06 | Two concurrent prepare callers without a claim state | Both may receive the same valid head; neither changes state; the test documents that v1 is not a single-delivery claim protocol |
| P07 | Restart with committed entries/events/idempotency | Same FIFO order, derived states, hashes, and replay responses |
| P08 | Human removes `QUEUED` entry | Append-only `REMOVED`; immutable entry unchanged; entry never selected |
| P09 | Human removes `BLOCKED` entry | Append-only `REMOVED`; no restore to `QUEUED` |
| P10 | Queue Entry recursive key audit | No bet/value key appears anywhere; only Candidate identity is stored |
| P11 | Completion boundary | v1 exposes no `mark_completed`; reserved `COMPLETED` semantics are documented as not submitted/filled/approved and require a later explicit workflow Gate |
| P12 | Read-only invariant | Candidate, HumanReview, source image, and machine evidence files remain byte-identical |

Forbidden Queue Entry value keys include `bets`, `active_bets`,
`cancelled_audit`, `numbers`, `number_groups`, `multiplier`,
`multiplier_rules`, `layout`, `continuation`, `special_play`, `raw_text`,
`model_raw_text`, and `machine_evidence_refs`.

## Seventeen mandatory failure cases

| ID | Failure injection | Required fail-closed behavior |
|---|---|---|
| F01 | Enqueue request includes bets, numbers, multiplier, layout, cancellation, or any unknown field | `QUEUE_REQUEST_INVALID`; no entry/event/idempotency/sequence change |
| F02 | Missing, malformed, reused-for-another-identity, non-human, or not server-bound interactive `human_enqueue_action_id` | `EXPLICIT_HUMAN_ENQUEUE_REQUIRED` or `QUEUE_IDEMPOTENCY_CONFLICT`; an arbitrary client string has no authority; zero queue writes |
| F03 | Candidate ID does not exist or snapshot is not committed | Propagate stable `CANDIDATE_NOT_FOUND`; zero queue writes |
| F04 | Candidate snapshot/commit/schema has an unknown, missing, or wrong-type field | Propagate `CANDIDATE_SCHEMA_INVALID`; zero queue writes |
| F05 | Expected or recomputed Candidate hash differs | Propagate `CANDIDATE_HASH_MISMATCH`; zero queue writes |
| F06 | Candidate or HumanReview is stale | Propagate `CANDIDATE_STALE`; zero queue writes, or append `BLOCKED` during prepare |
| F07 | Candidate lifecycle is invalidated | Propagate `CANDIDATE_INVALIDATED`; zero enqueue writes, or append `BLOCKED` during prepare |
| F08 | Candidate revision is superseded | Propagate `CANDIDATE_SUPERSEDED`; zero enqueue writes, or append `BLOCKED` during prepare |
| F09 | Candidate lifecycle is revoked | Propagate `CANDIDATE_REVOKED`; zero enqueue writes, or append `BLOCKED` during prepare |
| F10 | Candidate values/provenance/machine refs/confirmation do not equal persisted HumanReview authority | Propagate `CANDIDATE_AUTHORITY_INVALID`; no Candidate values copied; prepare appends `BLOCKED` only |
| F11 | Normal/column groups, multiplier, special play, continuation, active/cancelled/executable flags, bet IDs, or a cancelled-only Candidate with no executable active bet are invalid | Propagate `CANDIDATE_STRUCTURE_INVALID`; no Candidate values copied; enqueue makes zero writes; prepare appends `BLOCKED` only |
| F12 | Source image ID/hash or HumanReview revision/hash no longer matches Candidate source | `CANDIDATE_STALE`; zero enqueue writes, or append `BLOCKED` during prepare |
| F13 | Duplicate human clicks, same Candidate under a second action ID, or one action ID reused for another identity | Exact replay of the existing entry or `QUEUE_IDEMPOTENCY_CONFLICT`; never duplicate the identity |
| F14 | Failure after snapshot/event/idempotency but before final commit marker | Entry remains invisible; retry resumes exact transaction; FIFO sequence is not consumed twice |
| F15 | Entry integrity mismatch, lifecycle sequence gap, unknown event, invalid state transition, or idempotency index corruption | `QUEUE_STORE_CORRUPT`; no prepare result; no repair-by-guessing |
| F16 | Candidate is valid at enqueue but not `VALID_CURRENT` at prepare | Append one `BLOCKED` event with only the stable error code; continue to the next FIFO entry; never return stale values |
| F17 | Any attempt to read/write legacy `queue_path`, `manual_candidate_id`, `approved_fill_queue`, batch queue, assist-fill, or WebFill schemas/helpers | `LEGACY_QUEUE_INTEROP_FORBIDDEN`; no conversion, import, queue mutation, fill, submit, or status alias |

For F03-F12, enqueue validation failure is entirely side-effect-free. Prepare
is allowed one append-only `BLOCKED` event because that event is the intended
diagnostic state transition; it contains no Candidate values.

## Lifecycle and concurrency adversarials

- `QUEUED -> BLOCKED`, `QUEUED -> REMOVED`, and `BLOCKED -> REMOVED` are
  accepted by the v1 API.
- Every other transition is rejected.
- Duplicate `REMOVED` or `BLOCKED` terminal actions are replayed
  only by the exact idempotency action; another action is a conflict.
- There is no v1 action that appends `COMPLETED`; source/API inspection rejects
  an accidental `mark_completed` implementation.
- A blocked sequence does not starve the next valid FIFO entry.
- Queue locks are path-safe and process restart does not delete lock-free
  committed data.
- A crash during a valid prepare keeps the entry `QUEUED`; a crash around an
  invalid prepare leaves it `QUEUED` or publishes exactly one `BLOCKED` event.
- Event order, not wall-clock time, derives state.
- Without a `CLAIMED`/lease state, two callers may prepare the same valid entry;
  this known limitation must be solved by a separate claim design, not hidden
  by the process lock.

## Five real-shape semantic preservation cases

These tests create real-shape HumanReview/Candidate fixtures through Gate 3B-1,
then validate through Gate 3B-2 before enqueue and again at prepare. Queue Entry
JSON is checked to contain no values. The prepare envelope must equal the
Gate 3B-2 envelope byte-for-byte for Candidate semantics.

| Sample | Candidate semantics checked after prepare | Queue-specific assertion |
|---|---|---|
| sample-007 | Normal/column mix, special scope, explicit cancelled audit records | Cancelled records never become executable; entry contains no cancellation details |
| sample-008 | Human-adopted corrections remain Human Answer authority; nested columns remain nested | Gemma/PP/Qwen evidence is not copied and cannot enqueue automatically |
| sample-010 | One normal bet with ordered rules `2X2`, `3X5`; column structures unchanged | Multi-rule values exist only in validator envelope, never Queue Entry |
| sample-011 | Column groups such as `[21,35] x [23] x [34] x [37]`, continuation, and human-corrected values | OCR/model disagreement is not a queue field or authority signal |
| sample-014 | Multi-line continuation, independent multiplier rules, special/scope evidence, and cancelled records | Unresolved Candidate is rejected; resolved current identity is reference-only |

Each sample test also asserts:

1. immutable Candidate and HumanReview bytes unchanged;
2. enqueue and prepare each invoke Gate 3B-2 exactly once per non-replayed
   action;
3. queue entry recursive keys exclude every value field;
4. lifecycle starts `QUEUED` and valid prepare leaves it `QUEUED` with no new
   lifecycle event;
5. v1 has no completion action; reserved `COMPLETED` never means submitted,
   filled, or approved for fill;
6. candidate/queue/webfill auto-confirm and auto-submit effects are zero.

## Static isolation tests

A future implementation test must inspect the validated-candidate queue module
imports and source graph. It must fail if the module imports from
`betguard.webfill`, legacy batch queue, web UI assist-fill, manual candidate
registry, fill mapping/plan, or executor packages. Conversely, legacy Queue and
WebFill packages must not import the new queue authority module without a later
explicit integration Gate.
