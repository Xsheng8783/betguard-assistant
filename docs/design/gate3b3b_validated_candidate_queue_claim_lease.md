# Gate 3B-3B Validated Candidate Queue Claim / Lease Design

Status: design only. This document authorizes no production change, Candidate
value mutation, fill mapping, WebFill, submission, or workflow-completion
transition.

## Audited starting boundary

Gate 3B-1 persists authoritative HumanReview revisions and immutable Candidate
snapshots. Gate 3B-2 reloads those records and returns a read-only
`VALID_CURRENT` envelope. Gate 3B-3A stores an immutable, identity-only Queue
Entry and exposes `prepare_next`; a successful prepare deliberately leaves the
entry `QUEUED`, so two consumers may receive the same entry.

The existing Queue Entry remains normative, immutable, and `QUEUED` throughout
an active Claim. Claim/lease state is a separate authority layer. The existing
`prepare_next` envelope is diagnostic/read-only only: it is not a Claim,
ownership fence, or downstream authority. Even if diagnostic prepare observes
an actively leased entry, no work may start from that envelope. The later
`claim_next` operation is the only exclusive-ownership entrypoint. Claim data
never adds bets, numbers, multiplier, layout,
continuation, special play, cancellation details, Human Answer, or machine
evidence to a Queue or Claim record.

The repository's legacy batch queue, approved-fill queue, assist-fill,
manual-candidate registry, fill-plan, executor, and WebFill paths remain a
separate, forbidden namespace. No claim is convertible to or readable by those
paths.

## Normative records

`docs/schemas/vision_validated_candidate_queue_claim_lease_v1.schema.json`
strictly defines three record shapes:

1. an immutable `vision-validated-candidate-queue-claim-v1` snapshot;
2. an append-only `vision-validated-candidate-queue-claim-event-v1` event;
3. a monotonic `vision-validated-candidate-queue-claim-clock-v1` UTC
   high-watermark record.

The Claim snapshot contains only:

- immutable Queue Entry ID, FIFO sequence, and entry-integrity hash;
- immutable Candidate ID, revision, and canonical-content hash;
- server-owned monotonic `claim_generation` for that Queue Entry;
- claim ID and fencing token;
- authenticated consumer/principal/server-session identity;
- versioned lease-duration policy, issue time, and expiry time;
- the exact Gate 3B-2 validation contract/status/time;
- bound claim action and idempotency identity;
- record-integrity hash and non-execution safety flags.

The fencing token is a concurrency fence, not fill authority and not an API
credential. It is useful only together with the authenticated owner, exact
claim ID, generation, and server session. A later implementation must protect
its user-data directory with the same local access controls as Candidate and
Queue stores and must never log the token.

Claim and event integrity hashes use the Gate 3B canonical encoding rules:
Unicode NFC, sorted object keys, UTF-8, compact JSON, no floats or unknown
fields, and SHA-256 over the complete record except its own integrity-hash
field. Array order is preserved.

## Authority flow

```text
explicit human-enqueued immutable Queue Entry
  -> server-authenticated consumer requests a server-bound claim action
  -> global queue/claim lock
  -> reap only provably expired leases
  -> FIFO select lowest QUEUED entry with no ACTIVE claim
  -> Gate 3B-2 reload + VALID_CURRENT
  -> atomic Claim snapshot/event/idempotency/commit publication
  -> read-only claimed envelope + generation/fencing token
```

The claimed envelope contains Queue/Candidate identity, lease identity and
expiry, and the read-only Gate 3B-2 envelope. It must repeat these flags:

```json
{
  "read_only_claim": true,
  "candidate_only": true,
  "approved_for_fill": false,
  "approved_for_submit": false,
  "webfill_authorized": false,
  "auto_confirm": false,
  "auto_submit": false
}
```

A claim grants only temporary exclusive read ownership inside this isolated
queue domain. It is not permission to edit Candidate/HumanReview, produce a
fill plan, write another queue, operate a browser, or submit anything.

## Exact request authority

Claim action issuance is server-owned. It binds purpose `CLAIM`, authenticated
principal, consumer ID, server session, and a server/UI-session idempotency key.
An arbitrary client action string has no authority.

The claim request accepts only the bound action ID and idempotency key. Queue
Entry selection is server-side FIFO; the client cannot pick an entry, inspect
Candidate values before acquiring the claim, request a provider-preferred
entry, or choose the lease duration. The configured policy supplies the
duration.

Renew, release, and abandon mutation requests likewise accept only a
server-bound action ID and idempotency key. Their action-issuance endpoint may
accept a Claim ID, then the server reloads the Claim and binds its exact owner,
session, Queue/Candidate identity, generation, fencing token, and expected event
sequence into the immutable action. The mutation decoder never accepts owner,
session, expiry, duration, generation, fencing token, Candidate/Queue values,
or other client overrides. The authenticated principal, consumer ID, and
server session always come from server context.

## Claim lifecycle

Queue lifecycle remains independently derived as `QUEUED`, `BLOCKED`, or
`REMOVED`. Claim availability is derived from the claim ledger:

```text
no committed generation -> AVAILABLE
AVAILABLE -> CLAIMED(generation N) -> ACTIVE
ACTIVE -> RENEWED(generation N) -> ACTIVE
ACTIVE -> RELEASED(generation N)
ACTIVE -> ABANDONED(generation N)
ACTIVE -> EXPIRED(generation N)
ACTIVE -> AUTHORITY_BLOCKED(generation N)
RELEASED/ABANDONED/EXPIRED -> CLAIMED(generation N+1) -> ACTIVE
```

Rules:

- Exactly one generation may derive as `ACTIVE` for a Queue Entry.
- A `CLAIMED` event is sequence 1 for its immutable Claim snapshot.
- Renewal appends an event with a later expiry but does not edit the snapshot.
- Release, abandon, expiry, and authority-block are terminal for that
  generation.
- A terminal generation cannot renew, release twice under another action, or
  return to `ACTIVE`.
- The next generation is exactly prior maximum plus one; gaps, duplicates, or
  rollback fail closed.
- An old generation/fencing token never affects a newer generation.
- `ABANDONED` is an explicit, authenticated owner/session relinquishment for a
  failed local work session. Mere process disappearance does not abandon early.
- Candidate/Candidate-authority failure appends `AUTHORITY_BLOCKED` and the
  Queue's existing `BLOCKED` event through one recoverable cross-ledger
  transaction. An authority-blocked generation cannot be reclaimed even if a
  later browser still holds its token.
- A Queue Entry that becomes `BLOCKED` or `REMOVED` cannot receive a new claim.
  If this happens while a claim is active, further owner operations fail closed
  and the claim is not interpreted as Candidate authority.
- Human removal while a Claim is active is rejected with `CLAIM_ACTIVE`. The
  owner must first explicitly release/abandon, or wait for trusted expiry, then
  the human may issue the existing remove action. There is no ambiguous
  non-atomic remove-plus-claim transition.
- This Gate defines no workflow-finished state or action.

## Lease expiration and renewal

Time authority is server-owned UTC. `issued_at`, `expires_at`, and renewal
expiry come from one injected/testable clock under the queue/claim lock. Every
accepted observation atomically advances a persisted, integrity-protected
`last_observed_utc` high-watermark and observation sequence. The versioned
policy sets both one lease duration and a maximum total lifetime from the
original issue time; clients cannot extend either.

Before selection, renewal, release, or abandon, the store compares authoritative time
to the latest committed expiry:

- `now < expires_at`: the generation remains active;
- `now >= expires_at`: append exactly one idempotent `EXPIRED` event before
  considering a later generation;
- time parse failure, `now < last_observed_utc`, time behind the latest committed
  event, or an untrusted clock source: return `CLAIM_CLOCK_UNSAFE` and do not
  expire, reassign, renew, release, or abandon by guessing. The watermark never
  moves backward.

Renewal requires the current owner, generation, fencing token, unexpired lease,
remaining policy headroom, and a fresh Gate 3B-2 `VALID_CURRENT` result. The new
expiry is deterministic from policy and never exceeds original issue time plus
maximum total lifetime. A retry with the exact renew action returns the same
event/expiry; another outcome is an idempotency conflict.

If restart cannot authenticate/recover the recorded owner session, the Claim
is unusable by that session but remains exclusively reserved. It must not be
abandoned or reassigned early; only a trusted clock reaching `expires_at` may
append `EXPIRED` and make a later generation possible.

No background worker is required. Expiry can be materialized lazily under the
same global lock during claim, renew, release, or read-state operations. A
read that does not hold the mutation lock may report the last durable state but
must not silently claim that an unmaterialized lease is available.

## Concurrency and fencing

Claim selection, Gate 3B-2 validation, expiry materialization, generation
allocation, and commit publication occur under the same cross-process store
lock used to protect FIFO Queue state. This produces one winning Claim commit
when callers race for one entry. Losers rescan FIFO and may claim another
eligible entry or receive no available entry.

The lock alone is not the downstream fence. Every owner operation must compare
claim ID, generation, fencing token, authenticated principal, consumer ID,
server session, expected latest event sequence, and Queue/Candidate identity.
Once generation N is terminal and N+1 exists, every N token is stale even if a
delayed process resumes. The raw fencing token may be persisted for exact
idempotent replay, but it is not a bearer credential by itself: owner,
principal, server session, Claim ID, generation, Queue/Candidate identity, and
latest event sequence must all match.

Before returning any Claim envelope and before renew, release, or abandon, the
server reloads Queue state and reruns Gate 3B-2. Claim usability is the
conjunction of a valid `ACTIVE` ledger, Queue state `QUEUED`, exact owner fence,
trusted clock, and current `VALID_CURRENT` Candidate. A failed Candidate check
immediately makes the Claim unusable in memory, even if diagnostic event I/O
then fails. No caller receives values or authority from that Claim.

This design does not claim distributed consensus across unrelated storage
roots. All cooperating processes must use one user-data root and the same
cross-process lock/atomic filesystem contract. Network-distributed claims need
a separate design.

## Atomic persistence and recovery

The claim store uses a separate namespace below the validated-candidate queue:

```text
validated-candidate-queue-v1/
  claims/
  claim-commits/
  claim-events/
  claim-actions/
  claim-action-idempotency/
  claim-operation-idempotency/
  claim-transactions/
  claim-queue-block-transactions/
  claim-clock/
```

Claim publication writes an immutable transaction journal, Claim snapshot,
initial `CLAIMED` event, action/idempotency result, and Queue Entry-to-generation
index. The immutable commit marker is last. Reads expose committed Claims only.

On interruption:

- before commit, the Claim is invisible; exact retry resumes the same claim ID,
  generation, token, and expiry from the journal;
- after commit, exact retry replays the committed result;
- another action encountering the same in-progress Queue Entry resumes or
  waits for that transaction, never allocates a competing generation;
- event append plus operation-idempotency result is one recoverable transaction;
- a partial/corrupt relation is not repaired by guessing and exposes no claim.

Restart validates schemas, integrity hashes, commit relations, contiguous event
sequences, one active generation per Queue Entry, monotonic generations,
idempotency indexes, Queue Entry identity, and Candidate identity. Any conflict
returns `CLAIM_STORE_CORRUPT` and withholds that entry.

Candidate invalidation during an active lease uses a separate recoverable
cross-ledger transaction containing the Claim `AUTHORITY_BLOCKED` event, Queue
`BLOCKED` event, both idempotency identities, and a final coordination commit
marker. The transaction marker is written before either event and the final
commit marker is last. Exact retry/restart completes the same two events once.
Even before that final marker exists, every authoritative access reruns Gate
3B-2, so the Claim is already unusable; partial event publication can never
revive it. Unknown partial relations fail closed and expose neither a usable
Claim nor Candidate values.

## Idempotency

Distinct server-bound identities are used for claim, renew, release, and
abandon.
Idempotency records bind purpose, authenticated owner/session, Queue/Claim
identity, generation, fencing token, and exact request hash.

- Exact replay returns byte-identical identity and expiry with `replayed=true`.
- A key reused for another purpose, owner, target, generation, token, or result
  returns `CLAIM_IDEMPOTENCY_CONFLICT`.
- A second action cannot create another active generation.
- Expiry uses a deterministic server expiry action identity derived from Claim
  ID/generation/committed expiry, so concurrent expiry checks append once.
- Idempotency never stores Candidate values.

## Fail-closed safety and legacy isolation

All Candidate validation failures remain the stable Gate 3B-2 codes. At claim
selection an invalid Queue head receives only the existing Queue `BLOCKED`
diagnostic and FIFO scanning continues; no Claim is published. A Claim-specific
failure appends no Candidate values and does not change Candidate/HumanReview.

Claim modules must not import, call, serialize to, or be imported as an
authority shortcut by legacy batch queue, approved-fill queue, assist-fill,
manual candidate, fill-plan, executor, browser automation, or WebFill modules.
There is no migration or conversion contract.

No API key, bearer header, image/base64, model response, Human Answer, bet
value, or machine-evidence value is stored. All operations preserve
`approved_for_fill=false`, `approved_for_submit=false`, `auto_confirm=false`,
and `auto_submit=false`.

## Minimal next implementation scope

A later implementation Gate may add only:

1. the isolated Claim snapshot/event/idempotency/transaction store;
2. server-bound claim/renew/release/abandon action issuance;
3. `claim_next`, `renew_claim`, `release_claim`, `abandon_claim`, and read-state
   methods;
4. lazy expiry materialization under the global lock;
5. read-only claimed envelopes and the specified tests.

It must not modify Queue Entry or Candidate schemas, add Candidate values to a
Claim, add a workflow-finished action, create a fill mapping, connect legacy
queues, call WebFill, or submit. Any downstream consumption after a claim needs
a separate authority design.
