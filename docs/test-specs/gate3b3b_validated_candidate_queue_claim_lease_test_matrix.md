# Gate 3B-3B Claim / Lease Test Matrix

Status: design-only test specification. No production Claim, fill, WebFill,
submission, or workflow-finished behavior is authorized here.

## Positive contract cases

| ID | Scenario | Required result |
|---|---|---|
| P01 | One authenticated consumer claims the only eligible Queue Entry | One immutable committed generation 1; `ACTIVE`; identity-only; Gate 3B-2 called once |
| P02 | Exact claim retry after a lost response | Same claim ID, generation, fencing token, expiry, and event; `replayed=true` |
| P03 | Two different eligible entries | Lowest `enqueue_sequence` is claimed first regardless of clock, filename, Candidate ID, or provider evidence |
| P04 | Valid active owner renews before expiry | One `RENEWED` event; deterministic later expiry within policy maximum; snapshot unchanged |
| P05 | Exact renew retry | Same event and expiry; no duplicate event |
| P06 | Valid owner explicitly releases | One terminal `RELEASED` event; Candidate, HumanReview, Queue Entry unchanged |
| P07 | Exact release retry | Same terminal event; no duplicate |
| P08 | Lease reaches exact expiry | One deterministic `EXPIRED` event; generation becomes terminal |
| P09 | New claim after expiry | Generation N+1 with a different fencing token; old token permanently stale |
| P10 | Restart with active lease | Same derived owner, generation, token, expiry, events, and FIFO exclusion |
| P11 | Restart after expiry but before materialization | First locked operation appends one `EXPIRED`; safe later generation can be allocated |
| P12 | Claim publication interrupted before commit | Claim invisible; exact retry resumes identical immutable records |
| P13 | Event publication interrupted | Exact operation retry resumes one event/result; no sequence gap |
| P14 | Simultaneous callers for one entry | Exactly one committed active generation; loser rescan/no-entry behavior is deterministic |
| P15 | Simultaneous callers for several entries | Each entry has at most one active generation; FIFO claim order is preserved under the global lock |
| P16 | Prepared claimed envelope audit | Top-level Queue/Candidate/Claim identity plus exact Gate 3B-2 envelope; every execution safety flag false |
| P17 | Recursive record-key audit | Claim/event/idempotency/transaction files contain no Candidate/Human Answer values |
| P18 | Read-only bytes audit | Candidate, HumanReview, Queue Entry, Qwen/Gemma/PP evidence remain byte-identical |
| P19 | Authenticated owner explicitly abandons a failed local work session | One terminal `ABANDONED`; Queue Entry stays `QUEUED`; no downstream action |
| P20 | Candidate becomes stale during an active lease | Claim is unusable immediately; one recoverable cross-ledger transaction publishes `AUTHORITY_BLOCKED` plus Queue `BLOCKED` |
| P21 | Restart cannot recover/authenticate the prior owner session | Claim cannot be used and cannot be reassigned early; trusted expiry is the only automatic terminal path |
| P22 | Diagnostic `prepare_next` observes an actively leased Queue Entry | Prepare envelope has no claim/fence/downstream authority; work is rejected unless obtained through `claim_next` |

## Mandatory failure cases

| ID | Failure injection | Stable fail-closed result |
|---|---|---|
| F01 | Claim/renew/release/abandon mutation request contains bets, numbers, multiplier, layout, cancellation, owner/session, expiry/duration, generation, fencing token, Candidate/Queue identity, or any unknown field | `CLAIM_REQUEST_INVALID`; exact action+idempotency allowlist only; zero writes |
| F02 | Missing, malformed, client-invented, wrong-purpose, or wrong-session claim action | `CLAIM_ACTION_REQUIRED` or `CLAIM_IDEMPOTENCY_CONFLICT`; zero Claim writes |
| F03 | Claim idempotency key reused for another consumer, purpose, target, or result | `CLAIM_IDEMPOTENCY_CONFLICT`; no second claim |
| F04 | Queue Entry is missing, uncommitted, schema-invalid, or integrity-invalid | `QUEUE_ENTRY_NOT_FOUND` or `QUEUE_STORE_CORRUPT`; no Claim |
| F05 | Queue Entry state is `BLOCKED` | Not eligible; no Claim and no state restoration |
| F06 | Queue Entry state is `REMOVED` | Not eligible; no Claim and no state restoration |
| F07 | Candidate is not found at claim-time Gate 3B-2 validation | Queue head gets one value-free `BLOCKED` reason `CANDIDATE_NOT_FOUND`; scanning continues |
| F08 | Candidate schema/hash is invalid | Queue head gets one value-free `BLOCKED` with exact Gate 3B-2 code; no Claim |
| F09 | Candidate/HumanReview is stale | `CANDIDATE_STALE` diagnostic only; no Claim |
| F10 | Candidate is invalidated, superseded, or revoked | Exact lifecycle code; no Claim |
| F11 | Candidate authority/provenance projection is invalid | `CANDIDATE_AUTHORITY_INVALID`; no Claim |
| F12 | Candidate structure or cancelled-only/no-executable-active scope is invalid | `CANDIDATE_STRUCTURE_INVALID`; no Claim |
| F13 | One active generation already exists | `CLAIM_ALREADY_ACTIVE`; no second active Claim |
| F14 | Claim ID, generation, fencing token, consumer, principal, or server session mismatches renew/release/abandon | `CLAIM_FENCE_MISMATCH` or `CLAIM_OWNER_MISMATCH`; zero events |
| F15 | Old generation/token arrives after a later generation was committed | `CLAIM_STALE`; later generation unchanged |
| F16 | Renew, release, or abandon arrives at/after expiry | Materialize `EXPIRED` once; operation fails `CLAIM_EXPIRED` |
| F17 | Renewal would exceed maximum total lease lifetime | `CLAIM_RENEWAL_LIMIT`; no event/expiry change |
| F18 | Gate 3B-2 fails during renewal/release/abandon/authoritative access | Claim becomes derived-unusable immediately; recoverable `AUTHORITY_BLOCKED` + Queue `BLOCKED`; no values returned |
| F19 | Server clock parses incorrectly, is below persisted `last_observed_utc`/latest event, or lacks trusted source | `CLAIM_CLOCK_UNSAFE`; high-watermark never rolls back; no expire/reassign/renew/release/abandon guess |
| F20 | Duplicate/gapped generation, event sequence gap, multiple active generations, token reuse, or integrity mismatch | `CLAIM_STORE_CORRUPT`; entry withheld |
| F21 | Failure after snapshot/event/idempotency but before Claim commit marker | Claim remains invisible; exact journal recovery only |
| F22 | Different action encounters an in-progress Claim transaction | Resume/wait for exact transaction; never allocate a competing generation |
| F23 | Release/renew action is replayed with a different expected event sequence | `CLAIM_IDEMPOTENCY_CONFLICT` or `CLAIM_STALE`; no extra event |
| F24 | Attempt to renew/release/abandon a terminal generation twice under another action | `CLAIM_TERMINAL`; no event |
| F25 | Candidate becomes stale/invalid while Claim is active, including failure during event publication | Derived access rejects immediately; exact recoverable transaction appends Claim `AUTHORITY_BLOCKED` + Queue `BLOCKED`; partial append never leaves a usable Claim |
| F26 | Claim record contains Candidate values or machine evidence values | Schema rejection / `CLAIM_STORE_CORRUPT`; no exposure |
| F27 | Claim envelope marks fill/submit/WebFill/auto-confirm/auto-submit true | `CLAIM_AUTHORITY_INVALID`; no Claim/result publication |
| F28 | Any import/conversion involving legacy queue, approved-fill, assist-fill, manual candidate, fill-plan, executor, or WebFill | `LEGACY_QUEUE_INTEROP_FORBIDDEN`; zero external side effects |
| F29 | Caller requests workflow-finished transition or downstream execution | Endpoint/method absent; no event, queue mutation, fill, or submit |
| F30 | Unknown Claim/event schema version, field, event type, or transition | `CLAIM_SCHEMA_INVALID` or `CLAIM_STORE_CORRUPT`; no salvage-by-guessing |
| F31 | Human remove is requested while Claim is active | `CLAIM_ACTIVE`; zero Queue/Claim change; first explicit owner release/abandon or trusted expiry, then a new remove action |

For F07-F12, the only permitted Queue mutation is the existing append-only
`BLOCKED` diagnostic produced during locked FIFO selection. It contains only a
stable Gate 3B-2 error code. Claim-specific failures are otherwise
side-effect-free.

## Schema adversarials

All three valid root shapes in
`vision_validated_candidate_queue_claim_lease_v1.schema.json` must validate
under JSON Schema Draft 2020-12 with format checking. Tests must reject:

- unknown/missing/wrong-type root and nested fields;
- invalid IDs, uppercase or wrong-length hashes, booleans in integer slots,
  floats, zero/negative sequence or generation;
- `expires_at <= issued_at` and policy duration greater than maximum (semantic
  validator checks cross-field relations);
- wrong Queue/Candidate identity relation;
- a `CLAIMED` or `RENEWED` event with null expiry;
- a `RELEASED`, `ABANDONED`, `EXPIRED`, or `AUTHORITY_BLOCKED` event with a
  non-null expiry;
- event/record integrity recomputation mismatch;
- any forbidden bet/value key at any depth.

Schema validation alone is insufficient for time ordering, hash recomputation,
event transition, ownership, fencing, or cross-record identity; domain tests
must cover those semantic invariants explicitly.

## Lifecycle, expiry, and concurrency assertions

- `CLAIMED` is event sequence 1 and starts one `ACTIVE` generation.
- `RENEWED` is allowed only for the same unexpired active generation and may
  repeat with distinct exact actions while policy headroom remains.
- `RELEASED`, `ABANDONED`, `EXPIRED`, and `AUTHORITY_BLOCKED` are terminal for
  the generation.
- No event type represents workflow completion, fill, or submission.
- Generations increase exactly by one only after the prior generation is
  terminal.
- An active generation excludes that Queue Entry from claim selection but does
  not mutate its Queue state away from `QUEUED`.
- A process/global lock serializes expiration, FIFO selection, Gate 3B-2
  validation, generation allocation, and commit publication.
- Fencing remains effective after the lock is released: a delayed old owner is
  rejected by generation/token comparison.
- A backward/untrusted clock never makes a lease appear expired.
- Restart with an unrecoverable owner session never permits use or early
  reassignment; only trusted time reaching expiry can free the entry.
- Concurrent expiry checks append exactly one deterministic event.
- Claim commit visibility is last; incomplete snapshots/events/indexes are not
  readable authority.
- Candidate failure is checked before every authoritative access; Claim
  `AUTHORITY_BLOCKED` and Queue `BLOCKED` are one recoverable cross-ledger
  transaction, but failure to append either event still cannot make the Claim
  usable.
- Active-lease human removal is rejected until explicit release/abandon or
  trusted expiry.
- A `prepare_next` result can never satisfy claim/fencing checks or authorize
  work, including when it names the same Queue Entry.

## Five real-shape preservation cases

Each fixture must be built through Gate 3B-1, validated by Gate 3B-2, enqueued
through Gate 3B-3A, then claimed. The claimed validation envelope must be
deep-equal to direct Gate 3B-2 output. Claim records contain no values.

| Sample | Semantic evidence visible only in validator envelope | Claim assertions |
|---|---|---|
| sample-007 | Normal/column mix, special scope, 24 active plus cancelled audit | Cancelled audit remains non-executable; no cancellation/value data in Claim |
| sample-008 | Human-adopted corrections and nested groups | Qwen/Gemma/PP evidence has no claim authority |
| sample-010 | Ordered rules `2X2`, `3X5`; column structure | Multi-rule values remain only in read-only validator envelope |
| sample-011 | Human correction plus multi-column continuation shapes | OCR disagreement is not a claim-selection signal |
| sample-014 | Multi-line continuation, independent multipliers, special/cancelled scope | Unresolved Candidate cannot claim; resolved identity remains lossless |

For every sample assert:

1. Candidate, HumanReview, Queue Entry, and evidence bytes are unchanged;
2. Queue and Claim files recursively contain no semantic value fields;
3. Gate 3B-2 is invoked at claim and renewal, never bypassed;
4. exact retry does not duplicate Claim or event;
5. expiry/new generation invalidates the old fence;
6. all fill/submit/WebFill/automatic side effects are zero.

## Static isolation and minimal implementation boundary

Static import/source-graph tests must reject imports between the Claim module
and legacy batch queue, approved-fill queue, assist-fill/manual registry,
fill-plan, executor, browser automation, or WebFill. Those legacy modules must
also not consume Claim IDs, fencing tokens, or the new user-data paths.

A next implementation Gate may add only an isolated Claim store, action issue,
claim/renew/release/abandon/read methods, lazy expiry, and focused routes/tests. It must
not add a workflow-finished method, Candidate value projection, queue
conversion, fill approval, WebFill, or submit behavior.
