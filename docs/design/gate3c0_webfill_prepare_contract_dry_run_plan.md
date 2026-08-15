# Gate 3C-0 Webfill Prepare Contract and Dry-Run Plan Design

Status: design only. `DESIGN_SAFE=YES` applies only to the immutable canonical
semantic-plan boundary described here. This document authorizes no production
code, adapter compilation, DOM inspection, browser automation, Webfill, fill,
submission, automatic submission, Queue completion, or external call.

## Audited starting authority

The server-owned authority chain is:

```text
HumanReview confirmed values
  -> immutable Candidate + append-only Candidate lifecycle
  -> Gate 3B-2 VALID_CURRENT validation envelope
  -> identity-only validated Queue Entry
  -> ACTIVE Claim generation + owner/session + fencing token
  -> this Gate's immutable dry-run semantic Prepare Artifact
```

The Prepare layer must consume the existing public contracts rather than read
storage internals as a shortcut:

- Candidate values are reloaded only through the Gate 3B-2 consumption
  validator.
- Queue identity/state comes from the isolated validated Candidate Queue.
- exclusive ownership comes only from an authoritative ACTIVE Claim access,
  never from diagnostic `prepare_next`.
- Qwen, Gemma, PP, parser output, confidence, machine evidence, HumanReview
  drafts, and browser memory cannot supply Prepare values.

The Prepare Artifact is a derived cache and audit snapshot. Candidate remains
the only betting-value authority. Possession of a Prepare ID or JSON file is
not fill, browser, or submit authority.

## Exact request boundary

The normative request schema is
`docs/schemas/vision_webfill_prepare_request_v1.schema.json`. The request has
exactly these fields:

```json
{
  "schema_version": "vision-webfill-prepare-request-v1",
  "queue_entry_id": "vcq-...",
  "claim_id": "vqc-...",
  "claim_session_id": "server-session-identity",
  "claim_generation": 1,
  "fencing_token": "vqf-...",
  "expected_candidate_id": "vc-...",
  "expected_canonical_content_hash": "...",
  "expected_queue_revision": 1,
  "target_profile_id": "wtp-...",
  "target_profile_version": 1,
  "idempotency_key": "wpi-..."
}
```

`claim_session_id` and `fencing_token` are comparisons, not client-granted
authority. The handler supplies the authenticated principal, consumer ID, and
server session from trusted server context. The request session must equal that
context and the committed Claim owner. The raw fencing token must pass the
Claim store's owner-fenced authoritative read. It is never logged or copied to
the Prepare Artifact.

`expected_queue_revision` is the latest contiguous Queue lifecycle event
sequence. It is not the immutable Queue Entry's FIFO sequence. An ACTIVE Claim
normally observes revision 1 and state QUEUED. Any mismatch fails before a
Prepare transaction is written.

Unknown fields and all values such as bets, numbers, number groups,
multiplier, special play, continuation, cancelled flags, Candidate snapshots,
Human Answers, model output, selectors, URLs, cookies, or DOM data are rejected
as `PREPARE_REQUEST_INVALID` with zero Prepare writes.

## Required freshness transaction

Prepare creation holds the same Queue/Claim cross-process coordination lock
used by Claim selection. It performs, in order:

1. validate every pending Prepare transaction without writing;
2. observe the existing Claim clock high-watermark once;
3. recover only fully valid Prepare transactions;
4. load the exact Queue Entry and require derived state QUEUED and exact Queue
   revision;
5. call the Claim store's authoritative owner-fenced read with exact Claim ID,
   generation, raw fencing token, authenticated principal, consumer, and server
   session;
6. require an unexpired ACTIVE Claim and exact Queue/Candidate identities;
7. require the returned Gate 3B-2 envelope to be exact VALID_CURRENT;
8. load the immutable target profile by exact ID/version and verify its
   integrity and active-version registry entry;
9. compile the canonical semantic plan in memory using deterministic rules;
10. semantically validate the entire plan and recompute every hash;
11. write transaction journal, immutable Artifact, initial PREPARED event,
    idempotency/authority-tuple indexes, then the visibility commit marker
    last.

The Candidate store is a separate authority root, so this filesystem protocol
does not pretend to provide distributed multi-root serializability. Safety
comes from two rules: Gate 3B-2 is checked during creation, and every future
authoritative read or adapter compilation must revalidate Candidate, Queue,
Claim, clock, target profile, Artifact integrity, lifecycle, and plan hash.
The Artifact itself never authorizes execution, including during a narrow
Candidate-change race.

## Target profile contract

The target profile schema is
`docs/schemas/vision_webfill_target_profile_v1.schema.json`. A profile is an
immutable, integrity-hashed capability contract. It identifies:

- game;
- supported normal/column bet types;
- supported special-play kinds;
- continuation and multiple-multiplier support;
- maximum active bets, groups, and numbers per group;
- exact preservation of bet, group, number, and multiplier-rule order.

V1 target profiles explicitly contain no selector, XPath, URL, form field,
browser session, cookie, navigation step, click, submit, or site-specific
mapping. `logical_contract_only=true`, while DOM mapping, browser execution,
and submit authorization are all false.

The server maintains an immutable profile registry plus one active version per
profile ID. A request must name the active version. Reusing an ID/version with
different bytes is `TARGET_PROFILE_INTEGRITY_INVALID`; requesting an old,
missing, or unknown version is `TARGET_PROFILE_VERSION_MISMATCH` or
`TARGET_PROFILE_NOT_FOUND`. Profile changes never silently reinterpret an old
Artifact.

## Immutable Prepare Artifact

The normative schema is
`docs/schemas/vision_webfill_prepare_artifact_v1.schema.json`. A committed V1
Artifact contains:

- immutable Prepare ID and creation time;
- exact Queue Entry identity, FIFO sequence, Queue revision, and integrity
  hash;
- exact Candidate ID, revision, and canonical content hash;
- Claim ID/generation, consumer/principal, observed lease expiry, and SHA-256
  bindings for the fencing token and server session;
- immutable target-profile ID/version/integrity hash;
- idempotency-key hash;
- one canonical semantic logical plan;
- deterministic plan and authority-binding hashes;
- VALID_DRY_RUN validation counts and provenance;
- empty `blocking_reasons`, `executable=false`, and all execution flags false;
- full-record integrity hash.

Raw fencing token, raw server session, raw idempotency key, API credentials,
machine evidence values, HumanReview JSON, Candidate JSON, cookies, base64
images, selectors, and browser state are not persisted.

Only a semantically valid dry-run plan is committed. A rejected request does
not create a "blocked plan" containing guessed or partial operations. Failure
details are returned separately and contain stable codes without values.

## Canonical semantic logical plan

The logical plan is stage 1 of a deliberately separated pipeline:

```text
1. canonical semantic logical plan       <- Gate 3C-0 only
2. future target-adapter compilation     <- not authorized
3. future browser execution              <- not authorized
4. future submit authorization           <- not authorized
```

One active Candidate bet produces exactly one `LOGICAL_BET_ENTRY`. Operations
are ordered by the Candidate `active_bets` array, with a contiguous one-based
`operation_index`. The projection preserves, byte-semantically:

- `human_bet_id`;
- `bet_type`;
- nested `number_groups` and their column boundaries;
- group order and number order;
- ordered multiplier rules and exact scope;
- special-play kind, raw text, and exact scope;
- continuation state, resolved within the same Human Bet;
- active/source-executable status.

Normal bets retain exactly one group. Column bets retain two or more groups.
No operation may flatten, sort, deduplicate, expand combinations, infer a
missing group, move a multiplier/special scope, split a fused value, create a
Human Bet, omit an active Human Bet, or use machine evidence as a value.

Candidate `cancelled_audit` entries never become operations. The plan stores
only ordered audit references containing `human_bet_id` and
`cancelled_non_executable`; it does not copy cancelled bet values into an
executable-looking structure. Active=false or executable=false records cannot
enter operations.

`continuation.present=true` means the already-confirmed continuation belongs
inside that same Candidate Human Bet and its existing groups. V1 never links a
continuation to another Human Bet. If a source needs a distinct or ambiguous
target not expressible by the Candidate contract, preparation fails
`PREPARE_CONTINUATION_UNSUPPORTED` rather than guessing.

Target-profile capability checks occur after Gate 3B-2 validation and before
hashing. Unsupported bet types/special plays, excessive sizes, continuation
without support, or multiple multiplier rules without support fail with zero
Artifact writes.

## Canonical serialization and hashes

All hashes use the Gate 3B canonical encoding:

1. reject unknown fields, non-string object keys, and floating-point values;
2. normalize all strings and keys to Unicode NFC;
3. sort object keys by Unicode code point;
4. preserve every array order;
5. encode UTF-8 JSON with `ensure_ascii=false`, compact `,`/`:` separators,
   and no trailing newline;
6. lowercase `sha256(canonical_bytes).hexdigest()`.

Three hashes have different purposes:

### `deterministic_plan_hash`

Hash the complete `logical_plan` object. It includes game, Candidate identity,
target-profile identity, ordering policy, ordered operations, and cancelled
audit references. It excludes Prepare ID/time, Queue/Claim/session,
idempotency, validation timestamps, provenance, safety, and the hash field.

The same immutable Candidate plus the same target-profile bytes yields the
same plan hash across exact retries, Claim renewals, or later Claim generations.
A number/group/order/scope/Human Bet ID/Candidate hash/profile change changes
the plan hash.

### `authority_binding_hash`

Hash this exact projection:

```json
{
  "schema_version": "vision-webfill-prepare-authority-binding-v1",
  "queue": "complete Artifact authority.queue",
  "candidate": "complete Artifact authority.candidate",
  "claim": {
    "claim_id": "...",
    "claim_generation": 1,
    "fencing_token_hash": "...",
    "consumer_id": "...",
    "authenticated_principal": "...",
    "claim_session_id_hash": "..."
  },
  "target_profile": "complete Artifact authority.target_profile",
  "deterministic_plan_hash": "..."
}
```

It deliberately excludes observed Claim expiry so a valid renewal of the same
generation/fence does not create a new plan. It excludes the raw token/session
and idempotency key. A new generation, fence, Candidate, Queue revision, target
profile, owner/session binding, or plan produces a different binding hash.

### `record_integrity_hash`

Hash the entire stored Artifact except `record_integrity_hash`. It includes
Prepare ID/time, observed expiry, idempotency-key hash, validation/provenance,
plan/binding hashes, and safety. It proves record integrity, not betting-value
authority.

The target profile and lifecycle event use the same full-record-except-own-hash
rule for `profile_integrity_hash` and `event_integrity_hash`.

## Idempotency

The semantic authority tuple is:

```text
(queue_entry_id, queue_revision, entry_integrity_hash,
 candidate_id, candidate_revision, canonical_content_hash,
 claim_id, claim_generation, fencing_token_hash,
 consumer_id, authenticated_principal, claim_session_id_hash,
 target_profile_id, target_profile_version, profile_integrity_hash)
```

The server stores only the idempotency-key hash, exact request hash, tuple
hash, Prepare ID, and final record-integrity hash.

- Exact key + exact tuple returns the same Artifact with `replayed=true`.
- The same tuple under a different key resolves to the same Artifact through a
  unique tuple index; it never creates a duplicate.
- Reusing a key for another tuple is `PREPARE_IDEMPOTENCY_CONFLICT`.
- A lost-response/crash retry resumes the same transaction/Prepare ID.
- Claim renewal leaves the tuple unchanged and replays the same Artifact.
- A new Claim generation/fence, Candidate revision/hash, Queue revision, or
  target-profile version is a new tuple and requires a new Artifact.
- Deterministic recompilation of one tuple must reproduce the stored plan
  hash; any mismatch is `PREPARE_NONDETERMINISTIC` and exposes no Artifact.

## Prepare lifecycle and freshness

The immutable snapshot keeps `state_at_creation=PREPARED`. Current state is
derived from append-only events defined by
`docs/schemas/vision_webfill_prepare_lifecycle_event_v1.schema.json`:

| Event | Meaning | Usable for future adapter compilation? |
|---|---|---|
| `PREPARED` | Initial validated dry-run snapshot | Only after fresh live revalidation; still no fill authority |
| `AUTHORITY_BLOCKED` | Candidate/Queue/Claim authority failed | No |
| `EXPIRED` | Bound Claim generation reached trusted expiry | No |
| `INVALIDATED` | Valid snapshot later failed a non-upstream semantic/profile policy check | No |
| `SUPERSEDED` | A newer explicit Prepare/profile version replaces this Artifact | No |
| `REVOKED` | Explicit trusted security/administrative revocation | No |

All events after PREPARED are terminal. There is no FILLED, SUBMITTED,
COMPLETED, APPROVED, CLAIMED, or executable lifecycle state. The Artifact JSON
is never edited.

Freshness effects are exact:

- Claim renewal with the same ID/generation/fence does not invalidate or
  rewrite the Artifact; authoritative access uses the renewed live expiry.
- Claim RELEASED or ABANDONED appends Prepare INVALIDATED with the exact
  upstream reason.
- Claim EXPIRED appends Prepare EXPIRED.
- Claim AUTHORITY_BLOCKED, Candidate STALE/INVALIDATED/SUPERSEDED/REVOKED, or
  Queue BLOCKED/REMOVED appends Prepare AUTHORITY_BLOCKED.
- target-profile active-version replacement appends SUPERSEDED; old version
  requests are rejected.
- a newer Claim generation never revives the old Artifact; it requires a new
  Prepare authority tuple.
- corruption of the Artifact/event ledger itself returns
  `PREPARE_STORE_CORRUPT`; corrupted bytes are never trusted enough to append a
  repairing event by guesswork.

No lifecycle event grants adapter, browser, fill, submit, or Queue-completion
authority.

## Atomic persistence and crash consistency

The future implementation uses a new user-data namespace, not the repository
and not any legacy fill queue:

```text
<user-data>/vision/webfill-prepare-v1/
  target-profiles/
  target-profile-active-version/
  artifacts/
  artifact-commits/
  lifecycle-events/
  request-actions/
  idempotency/
  authority-tuple-index/
  prepare-transactions/
  lifecycle-transactions/
```

Prepare creation runs under Queue/Claim coordination. An immutable transaction
journal records identities, hashes, the complete proposed Artifact and PREPARED
event, but never the raw fencing token/session/idempotency key. Publication
order is Artifact, event, indexes, then commit marker last. Uncommitted
Artifacts are invisible.

Before changing clock or resuming any journal, recovery validates all pending
transactions, schemas, recursive forbidden fields, hashes, cross-record
relations, Claim/Queue/Candidate/profile identities, and idempotency bindings.
If any pending journal is corrupt, recovery writes nothing. Valid exact retry
resumes immutable records; another tuple cannot overwrite or salvage them.

Lifecycle append uses its own immutable transaction and contiguous sequence.
Upstream invalidation can be discovered on every read/compile and therefore
makes the Artifact unusable in memory even if event I/O fails. A recoverable
transaction later appends the same terminal event once. No partial Prepare
publication can change Candidate, Queue, Claim, or HumanReview.

## Fail-closed error classes

At minimum the implementation test contract uses:

- `PREPARE_REQUEST_INVALID`
- `PREPARE_NOT_FOUND`
- `PREPARE_IDEMPOTENCY_CONFLICT`
- `PREPARE_QUEUE_STALE`
- `PREPARE_CLAIM_INVALID`
- `PREPARE_CLAIM_EXPIRED`
- `PREPARE_CLOCK_UNSAFE`
- `PREPARE_CANDIDATE_INVALID`
- `PREPARE_TARGET_PROFILE_NOT_FOUND`
- `PREPARE_TARGET_PROFILE_VERSION_MISMATCH`
- `PREPARE_TARGET_PROFILE_INTEGRITY_INVALID`
- `PREPARE_STRUCTURE_INVALID`
- `PREPARE_SCOPE_UNRESOLVED`
- `PREPARE_UNSUPPORTED_CAPABILITY`
- `PREPARE_CONTINUATION_UNSUPPORTED`
- `PREPARE_NONDETERMINISTIC`
- `PREPARE_HASH_MISMATCH`
- `PREPARE_SCHEMA_INVALID`
- `PREPARE_STORE_CORRUPT`
- `LEGACY_WEBFILL_INTEROP_FORBIDDEN`

Every creation failure has zero Prepare writes. The only later write permitted
by upstream failure is one append-only terminal lifecycle event. No failure
normalizes values, flattens columns, retries an external model, changes Claim,
completes Queue, or calls a browser.

## Five-sample fixture strategy

Tests construct persisted HumanReview -> Candidate -> Gate 3B-2 -> Queue ->
Claim fixtures using existing real-shape cases, without rereading images or
machine evidence:

- sample-007: normal/column mix, special scope, 24 active and one cancelled;
- sample-008: confirmed corrected numbers and nested groups;
- sample-010: ordered `2X2`, `3X5` multiplier rules and special scope;
- sample-011: corrected `30`, `3/4X1`, columns, and continuation;
- sample-014: multi-line continuation, independent multipliers, special scope,
  and cancelled audit.

For each sample assert one operation per active Candidate bet, exact deep-equal
value projection, ordered cancelled audit references, stable plan hash, no
Candidate/HumanReview/Queue/Claim mutations, and all execution side effects
zero. Fixture values are test inputs only; Human Truth and dataset files are
never modified.

## Minimal implementation Gate

The next Gate may implement only:

1. immutable target-profile registry/read validation;
2. exact Prepare request decoder and server-context binding;
3. deterministic Candidate-to-logical-plan compiler;
4. semantic/profile validation and canonical hashes;
5. isolated Artifact/event/idempotency/transaction store;
6. create/get/derived-state APIs returning dry-run artifacts;
7. restart, crash, freshness, adversarial, and five-sample tests.

It must not implement CSS/XPath/DOM mapping, browser adapters, browser
automation, cookies, navigation, fill, Webfill, submit, auto-submit, Queue
completion, model calls, or legacy queue conversion. A later target-adapter
design must repeat live Candidate/Queue/Claim/profile validation and cannot use
Prepare JSON alone as authority.

