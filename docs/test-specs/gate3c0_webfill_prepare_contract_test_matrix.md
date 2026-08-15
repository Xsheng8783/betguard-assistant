# Gate 3C-0 Webfill Prepare Contract Test Matrix

Status: design-only test specification. No browser, DOM, fill, Webfill,
submission, Queue completion, or external model operation is authorized.

## Positive contract cases

| ID | Scenario | Required result |
|---|---|---|
| P01 | Exact QUEUED Entry + ACTIVE Claim + VALID_CURRENT Candidate + active target profile | One committed immutable PREPARED Artifact; `VALID_DRY_RUN`; `executable=false` |
| P02 | Exact lost-response retry | Same Prepare ID, plan hash, binding hash, and record hash; `replayed=true` |
| P03 | Different idempotency key for the same authority tuple | Same Prepare Artifact through unique tuple index; no duplicate |
| P04 | Normal bet | Exactly one nested number group; source order preserved |
| P05 | Column bet | Two or more nested groups; column boundaries/order preserved |
| P06 | Multiple multiplier rules | Exact ordered rules and scope preserved |
| P07 | Special play | Kind/raw text/scope preserved and checked against target profile |
| P08 | Continuation | `present/resolved` preserved with `binding=within_human_bet` |
| P09 | Cancelled Candidate bet | Audit reference only; zero logical operations for that bet |
| P10 | Several active bets | One operation per active bet in Candidate array order; contiguous index |
| P11 | Claim renews under same ID/generation/fence | Same Artifact and plan hash; live expiry is reloaded; snapshot not edited |
| P12 | New Claim generation for unchanged Candidate/profile | New authority binding/Prepare; same deterministic plan hash |
| P13 | Restart after full commit | Byte-identical Artifact/event/idempotency visibility |
| P14 | Crash before commit marker | Artifact invisible; exact retry resumes identical transaction |
| P15 | Crash after Artifact/event before index/commit | Exact recovery publishes once; no duplicate event |
| P16 | Lifecycle read while all upstream authority is current | Derived state PREPARED; still no execution authority |
| P17 | Target profile integrity and capability contract valid | Profile accepted without any DOM or selector mapping |
| P18 | Same plan compiled twice in separate clean stores | Same deterministic plan hash |
| P19 | Machine evidence refs differ but Candidate values/hash are unchanged | Plan remains sourced only from Candidate; no machine values copied |
| P20 | Public Artifact read | Raw fencing token, raw session, raw idempotency key absent |

## Mandatory fail-closed cases

| ID | Failure injection | Required result |
|---|---|---|
| F01 | 24/25 active Human Bets confirmed or edited-after-confirm | Gate 3B-2/Candidate rejection; zero Prepare writes |
| F02 | Human Answer revision/hash changed after Candidate | `PREPARE_CANDIDATE_INVALID`; zero Prepare writes |
| F03 | Candidate state STALE | `PREPARE_CANDIDATE_INVALID`; zero Prepare writes |
| F04 | Candidate INVALIDATED/SUPERSEDED/REVOKED | exact upstream failure; zero Prepare writes |
| F05 | Queue Entry missing, BLOCKED, REMOVED, wrong identity, or wrong revision | `PREPARE_QUEUE_STALE`; zero Prepare writes |
| F06 | Claim missing, terminal, or not ACTIVE | `PREPARE_CLAIM_INVALID`; zero Prepare writes |
| F07 | Claim lease at/past expiry | materialize Claim EXPIRED under Claim rules; no Prepare |
| F08 | wrong authenticated principal/consumer/server session | owner/session failure; zero Prepare writes |
| F09 | wrong Claim generation | stale/fence failure; zero Prepare writes |
| F10 | wrong fencing token | fence mismatch; zero Prepare writes |
| F11 | old generation/token after a new generation | reject permanently; newer Claim unchanged |
| F12 | clock below Claim high-watermark/latest event | `PREPARE_CLOCK_UNSAFE`; no expiry/recovery/Prepare writes |
| F13 | unresolved multiplier scope | `PREPARE_SCOPE_UNRESOLVED`; zero Prepare writes |
| F14 | unresolved special-play scope | `PREPARE_SCOPE_UNRESOLVED`; zero Prepare writes |
| F15 | empty number_groups or empty group | `PREPARE_STRUCTURE_INVALID`; zero Prepare writes |
| F16 | normal bet has multiple groups | reject; do not flatten |
| F17 | column bet has one group or groups were flattened | reject; do not infer boundaries |
| F18 | cancelled bet appears in operations | schema/semantic rejection |
| F19 | active=false or source executable=false bet appears in operations | schema/semantic rejection |
| F20 | continuation needs another/ambiguous Human Bet target | `PREPARE_CONTINUATION_UNSUPPORTED`; no guessed target |
| F21 | duplicate human_bet_id | `PREPARE_STRUCTURE_INVALID`; zero Prepare writes |
| F22 | active Candidate bet omitted or duplicated in operations | account/count mismatch; reject |
| F23 | request includes bets/numbers/groups/multiplier/special/continuation/Candidate JSON | `PREPARE_REQUEST_INVALID`; zero writes |
| F24 | request includes selector/DOM/URL/cookie/browser fields | `PREPARE_REQUEST_INVALID`; zero writes |
| F25 | idempotency key reused for another tuple | `PREPARE_IDEMPOTENCY_CONFLICT`; no second Artifact |
| F26 | unknown/missing target profile | `PREPARE_TARGET_PROFILE_NOT_FOUND`; zero writes |
| F27 | target profile version is not active/exact | `PREPARE_TARGET_PROFILE_VERSION_MISMATCH`; zero writes |
| F28 | target profile bytes changed under same ID/version | `PREPARE_TARGET_PROFILE_INTEGRITY_INVALID`; zero writes |
| F29 | unsupported bet type | `PREPARE_UNSUPPORTED_CAPABILITY`; zero writes |
| F30 | unsupported special-play kind | `PREPARE_UNSUPPORTED_CAPABILITY`; zero writes |
| F31 | target does not support continuation/multiple multiplier rules | `PREPARE_UNSUPPORTED_CAPABILITY`; zero writes |
| F32 | target maximum bet/group/number count exceeded | `PREPARE_UNSUPPORTED_CAPABILITY`; zero writes |
| F33 | operation order differs from Candidate active_bets | `PREPARE_NONDETERMINISTIC`; reject |
| F34 | group/number/multiplier-rule order changed or sorted | plan hash/value-projection mismatch; reject |
| F35 | deterministic plan hash mismatch | `PREPARE_HASH_MISMATCH`; Artifact withheld |
| F36 | authority binding hash mismatch | `PREPARE_HASH_MISMATCH`; Artifact withheld |
| F37 | record/profile/event integrity hash mismatch | `PREPARE_STORE_CORRUPT`; no salvage |
| F38 | transaction has unknown field or nested Candidate/machine value payload | `PREPARE_SCHEMA_INVALID`; entire store tree unchanged |
| F39 | one valid pending journal precedes one corrupt journal | preflight all first; zero recovery/clock writes |
| F40 | missing final Artifact commit marker | Artifact invisible; exact transaction recovery only |
| F41 | restart cannot authenticate old Claim session | no Prepare use/create until session authority or trusted expiry permits a new Claim |
| F42 | Candidate becomes stale after Prepare | derived `AUTHORITY_BLOCKED`; no adapter use |
| F43 | Queue becomes BLOCKED/REMOVED after Prepare | derived `AUTHORITY_BLOCKED`; no adapter use |
| F44 | Claim RELEASED or ABANDONED after Prepare | append Prepare INVALIDATED; no adapter use |
| F45 | Claim EXPIRED after Prepare | append Prepare EXPIRED; no adapter use |
| F46 | Claim AUTHORITY_BLOCKED after Prepare | append Prepare AUTHORITY_BLOCKED; no adapter use |
| F47 | target profile advances after Prepare | old Artifact SUPERSEDED; old version cannot compile |
| F48 | terminal lifecycle followed by another event | `PREPARE_STORE_CORRUPT` |
| F49 | lifecycle sequence gap/duplicate or cross-Artifact hashes | `PREPARE_STORE_CORRUPT` |
| F50 | caller asks Artifact to become executable/filled/submitted/completed | method/field absent or schema rejection; zero side effects |
| F51 | legacy manual candidate/approved-fill/queue/Webfill input or conversion | `LEGACY_WEBFILL_INTEROP_FORBIDDEN` |
| F52 | float, non-NFC ambiguity, unknown key, bool in integer slot | schema/canonicalization rejection; no normalization guess |

Creation failures are side-effect-free. Later freshness discovery may append
only one value-free terminal Prepare lifecycle event; it never mutates the
Artifact or upstream stores.

## Schema and semantic adversarials

All four schemas validate under Draft 2020-12 with format checking:

- Prepare request;
- target profile;
- Prepare Artifact;
- Prepare lifecycle event.

Tests must reject unknown/missing fields at every depth, invalid IDs/hashes,
wrong date-time formats, booleans/floats in integer slots, empty operations,
noncontiguous operation indexes, duplicate Human Bet IDs, duplicate numbers
within/across one bet's groups, out-of-game-range numbers, invalid multiplier
syntax, unresolved scopes, mismatched source/plan Candidate identity, mismatched
profile identities, and any forbidden selector/browser/credential field.

JSON Schema does not prove the following; domain tests must:

- semantic number ranges for the selected game;
- operation count and deep-equal projection against Gate 3B-2 bets;
- cancelled-reference projection against `cancelled_audit`;
- target capability limits;
- canonical plan, authority, record, profile, and event hashes;
- exact idempotency/tuple relations;
- live Queue/Claim/Candidate/profile freshness;
- lifecycle transitions and crash-recovery relations.

## Lifecycle transition assertions

```text
no Artifact -> PREPARED
PREPARED -> AUTHORITY_BLOCKED
PREPARED -> EXPIRED
PREPARED -> INVALIDATED
PREPARED -> SUPERSEDED
PREPARED -> REVOKED
```

Every terminal state has no outgoing transition. Claim renewal is not a
Prepare event. FILLED, SUBMITTED, COMPLETED, APPROVED, CLAIMED, EXECUTING, and
DONE are forbidden event names.

## Five real-shape preservation fixtures

Each fixture is built through persisted HumanReview, immutable Candidate,
Gate 3B-2, validated Queue, and ACTIVE Claim. It never rereads an image or
model artifact.

| Sample | Required semantic preservation |
|---|---|
| sample-007 | 24 active operations in Candidate order; one cancelled audit ref; normal/column and special scopes unchanged |
| sample-008 | confirmed corrected two-digit numbers and nested groups unchanged |
| sample-010 | ordered multiplier rules `2X2`, `3X5`, column boundaries, and special scope unchanged |
| sample-011 | corrected `30`, `3/4X1`, column groups, and continuation unchanged |
| sample-014 | multi-line continuation, independent multiplier scopes, special play, and cancelled audit unchanged |

For every sample:

1. logical operation projection is deep-equal to Candidate active values plus
   only the explicit `continuation.binding` and operation metadata;
2. `cancelled_audit_refs` contain IDs only;
3. deterministic recompile produces the same plan hash;
4. Candidate, HumanReview, Queue, Claim, and evidence bytes remain unchanged;
5. Artifact lifecycle access reruns all authority checks;
6. adapter compilation, browser, Webfill, submit, Queue completion, and
   external calls are zero.

## Minimal implementation test boundary

The next Gate may test only target-profile reads, request decoding,
deterministic logical plan compilation, hashes, immutable Artifact/event store,
idempotency, recovery, freshness, and read APIs. Static source tests must fail
if the implementation imports or calls legacy queue, approved-fill,
manual-candidate, fill-plan, executor, browser automation, Webfill, or submit
modules.

