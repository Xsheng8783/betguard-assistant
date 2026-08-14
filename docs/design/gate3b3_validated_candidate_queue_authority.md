# Gate 3B-3 Validated Candidate Queue Authority Design

Status: design only. This document does not authorize or implement Queue,
WebFill, submission, or any production mutation.

## Audited authority contracts

Gate 3B-1 persists server-owned `HumanReview` revisions and immutable
`vision-candidate-authority-v1` snapshots in
`betguard.vision.candidate_authority`. Candidate creation accepts only the
persisted review identity, revision, hash, and idempotency key. Candidate
values come from Human Answer authority. Machine evidence is provenance with
`value_authority=false`.

Gate 3B-2 provides
`CandidateConsumptionAuthorityValidator.validate_request()`. Its public
request allowlist is only:

```json
{
  "candidate_id": "vc-...",
  "expected_candidate_revision": 1,
  "expected_content_hash": "..."
}
```

The expected revision and hash are optional, but the validator always reloads
the latest committed immutable Candidate. It validates canonical hash,
specific lifecycle state, current HumanReview identity, source image,
Candidate-to-review value/provenance equality, structures, cancelled audit,
and authority. Only `VALID_CURRENT` can pass.

The repository also contains legacy `batch_queue`, `approved_fill_queue`,
`queue_path`, `manual_candidate_id`, and `betguard.webfill` flows. They copy
parsed betting values and can lead to fill-oriented behavior. They are not an
authority source or implementation base for this design.

## New namespace and non-interoperability boundary

The future store must use a new user-data namespace:

```text
<user-data>/vision/validated-candidate-queue-v1/
  entries/
  entry-commits/
  lifecycle-events/
  enqueue-idempotency/
  prepare-idempotency/
  locks/
  sequence/
```

Production code for this namespace must not import, call, serialize to, or
read from any legacy queue or WebFill package. In particular it must not:

- create or accept `queue_path`, `manual_candidate_id`, `valid_candidates`, or
  `approved_fill_queue` records;
- call legacy batch queue, assist-fill, fill-plan, executor, or webfill helpers;
- convert a validated Candidate queue entry into a legacy queue entry;
- treat a legacy queue record as a validated Candidate queue entry;
- reuse legacy statuses such as waiting-for-human or done-by-human;
- migrate old data automatically.

There is no implicit migration. A future migration requires a separate human
review and Candidate creation flow; copying legacy values is forbidden.

## Immutable Queue Entry

The normative entry schema is
`docs/schemas/vision_validated_candidate_queue_entry_v1.schema.json`.

A Queue Entry contains Candidate identity only:

- `candidate_id`;
- immutable Candidate `revision`;
- `canonical_content_hash`.

It never contains or aliases bets, number groups, numbers, multiplier rules,
layout, continuation, special play, cancellation details, model output, or
machine evidence. Consumers reload those values through the Gate 3B-2
validator after authority has passed.

The entry also records a server-authenticated explicit human enqueue action,
the `VALID_CURRENT` validator contract/version, FIFO sequence, immutable
creation state `QUEUED`, and non-execution safety metadata. The snapshot is
never edited after publication.

`entry_integrity_hash` is SHA-256 over the complete entry except the hash field
itself, using the Candidate canonical JSON rules: Unicode NFC, sorted object
keys, UTF-8, compact separators, no floats or unknown fields. Unlike Candidate
content identity, this is a record-integrity hash and includes entry ID,
sequence, timestamps, enqueue actor/action, Candidate identity, and safety.

## Explicit enqueue authority

The future enqueue request is identity-only:

```json
{
  "candidate_id": "vc-...",
  "expected_candidate_revision": 1,
  "expected_content_hash": "...",
  "human_enqueue_action_id": "hqe-..."
}
```

The actor is supplied by the authenticated server context, not trusted from a
client JSON field. `human_enqueue_action_id` is generated only after a visible
human action and must be bound server-side to the authenticated interactive
session/action. An arbitrary client-supplied string is not enqueue authority.
API key presence, Candidate creation, page reload, or model agreement must
never enqueue automatically.

Before writing anything, enqueue must call
`CandidateConsumptionAuthorityValidator.validate_request()` with Candidate
identity only. It requires `VALID_CURRENT`. The service copies only the
validated identity into the Queue Entry, not `bets` from the validation
envelope.

The server-derived enqueue idempotency identity is:

```text
(human_enqueue_action_id, candidate_id, candidate_revision,
 canonical_content_hash)
```

The same tuple returns the same entry and `replayed=true`. Reusing the action
ID for another Candidate identity returns an idempotency conflict. One
Candidate identity may have at most one committed Queue Entry; a second human
action for the same identity returns the existing entry, never a duplicate.
A removed or completed Candidate revision cannot be re-enqueued. A fresh
Candidate revision is required.

## FIFO and prepare authority

`enqueue_sequence` is a server-owned monotonic integer assigned under a single
queue lock. Eligible entries are ordered by that integer, never by timestamps,
filenames, Candidate values, provider confidence, or browser order.

The future `prepare_next(prepare_action_id)` transaction:

1. acquires the queue lock;
2. finds the lowest-sequence entry whose derived state is `QUEUED`;
3. calls Gate 3B-2 again with the exact identity saved in that entry;
4. on a lifecycle/authority/integrity failure, appends `BLOCKED` and continues
   to the next eligible sequence without exposing Candidate values;
5. on `VALID_CURRENT`, returns the read-only Gate 3B-2 validation envelope plus
   Queue Entry identity without appending an event or changing `QUEUED`;
6. releases the lock.

Prepare never calls queue conversion, fill-plan, browser automation, WebFill,
or submit. It does not approve filling and it is not a dequeue or claim. A
successful prepare is read-only and the entry remains `QUEUED`.

`prepare_action_id` makes a failing prepare's `BLOCKED` append idempotent; a
successful prepare does not need a persistent result or lifecycle event. This
Gate intentionally has no `CLAIMED` or lease state. The queue lock serializes
selection during one call but does not reserve the entry after return, so two
consumers may receive the same valid entry. Single-delivery, claim ownership,
lease expiry, and recovery require an explicit later design Gate and must not
be inferred from this v1 contract.

## Lifecycle

Queue snapshots retain `state_at_creation=QUEUED`. Current state is derived
only from append-only events:

```text
QUEUED -> BLOCKED -> REMOVED
QUEUED -> REMOVED
```

- `QUEUED`: committed entry, eligible by FIFO sequence.
- `BLOCKED`: prepare-time Candidate validation did not return `VALID_CURRENT`.
  The event stores only a stable validator error code, never Candidate values.
- `REMOVED`: explicit human removal; terminal.
- `COMPLETED`: reserved for a future explicit downstream-workflow completion
  transition. It is not exposed by the v1 minimal API and, if later designed,
  must never mean filled, submitted, or approved for fill.

No transition restores `BLOCKED`, `REMOVED`, or `COMPLETED` to `QUEUED`.
No lifecycle event edits the immutable entry. Event fields are: schema/version,
event ID, queue entry ID, event sequence, event type, occurred-at, actor,
reason code, idempotency action hash, and entry integrity hash. Unknown fields,
sequence gaps, duplicate terminal events, or invalid transitions fail closed.

## Atomic persistence and restart

Entry publication follows a transaction journal and final immutable commit
marker. A Queue Entry is invisible until its snapshot, initial `QUEUED` event,
Candidate validator identity, enqueue idempotency record, and commit marker are
all durable. Interrupted publication is invisible and safely resumable from
the same exact idempotency tuple.

Events use write-temp, flush, fsync, and atomic publication. The queue lock
serializes sequence allocation, prepare selection/validation, `BLOCKED` event
append, and prepare-failure idempotency. A crash during a successful prepare
leaves the entry `QUEUED`; a crash around a failing prepare either leaves it
`QUEUED` or publishes one atomic `BLOCKED` event that exact-action retry can
replay. This durability does not create a post-return claim.

On restart the store:

1. loads committed entries only;
2. validates entry schema and integrity hash;
3. validates contiguous lifecycle event sequences and transitions;
4. validates idempotency indexes against immutable entries/events;
5. reconstructs states and next FIFO sequence;
6. exposes no entry if any identity or integrity relation is inconsistent.

Storage is under the user data directory, never the repository. IDs are
path-safe and never used as unchecked paths. No API key, bearer header, image
base64, model response, Candidate values, or Human Answer values are saved.

## Security invariants

- Enqueue is impossible without an explicit authenticated human action.
- Enqueue and successful prepare both require Gate 3B-2 `VALID_CURRENT`.
- Queue Entry JSON cannot express Candidate values.
- Candidate lifecycle changes block preparation even if the entry was valid at
  enqueue time.
- Machine evidence, confidence, cache presence, and model agreement have no
  queue authority.
- Queue state never grants fill, submit, WebFill, auto-confirm, or auto-submit.
- `COMPLETED` never means submitted.
- No legacy queue or WebFill import, conversion, path, schema, or status is
  permitted.
- Every failure is side-effect-free except a deliberate append-only `BLOCKED`
  diagnostic event during prepare.

## Minimal future implementation boundary

A later Gate may implement only this isolated store, event ledger, enqueue,
remove, and read-only `prepare_next`. The v1 minimal implementation must not
provide `mark_completed`; successful prepare leaves the entry `QUEUED`.
Claim/lease ownership and a future explicit downstream completion action need
their own design before `COMPLETED` can become reachable. Any fill approval,
fill mapping, Queue-to-WebFill adapter, or submit authority requires a separate
design and explicit human-controlled Gate.
