# Gate 3C-3 Read-Only Browser Observation Adapter Test Matrix

Status: design-only test specification. Browser launch/control, real-site
reads, navigation, DOM mutation, click, typing, events, fill, Webfill, submit,
Queue/Claim completion, and external calls are forbidden.

## Locked 36-case adversarial matrix

| ID | Scenario | Required fail-closed result |
|---|---|---|
| B01 | Client adds selector or selector override | `CAPTURE_REQUEST_INVALID`; adapter calls 0; writes 0 |
| B02 | Client adds DOM/input values or occupancy result | `CAPTURE_REQUEST_INVALID`; raw bytes never inspected/logged; writes 0 |
| B03 | Client submits a completed Observation JSON | reject public boundary; only trusted adapter may call Observation Store |
| B04 | Capture action targets another tab | `WRONG_TARGET`; writes 0 |
| B05 | Origin/path hashes differ from active Profile policy | `WRONG_PAGE`; no raw URL in error/audit |
| B06 | Site/game differs from Profile or logical Target Profile | `WRONG_GAME`; writes 0 |
| B07 | Two forms satisfy the exact form contract | `SELECTOR_AMBIGUOUS`; no index/proximity choice |
| B08 | Required form missing | `SELECTOR_MISSING`; no fuzzy discovery |
| B09 | Required field selector matches two fields | `SELECTOR_AMBIGUOUS`; no target selected |
| B10 | Hidden replacement shadows a visible field | ambiguous or wrong structure; never prefer visible by heuristic |
| B11 | Exact required field is disabled or readonly | safe state may be observed, Mapping remains blocked; never mutate it |
| B12 | DOM mutation generation changes between reads | retry whole attempt within bound; otherwise `DOM_UNSTABLE` |
| B13 | Navigation/document changes during capture | `NAVIGATION_RACE`; partial Observation invisible |
| B14 | SPA route changes with same tab ID | new page instance + epoch; old action/Observation stale |
| B15 | Reload reuses client-supplied old epoch | `OBSERVATION_STALE`/`WRONG_TARGET`; old epoch never revived |
| B16 | BFCache restores prior document/structure | new page instance + epoch; prior Observation stale |
| B17 | Browser disconnect/reconnect | new connection identity and fresh capture required; old authority stale |
| B18 | Tab duplicated | new tab/document/page identities; action cannot retarget |
| B19 | Main frame or target iframe replaced | V1 `UNSUPPORTED_FRAME` or new main-frame identity/epoch; never reuse |
| B20 | Required form is in cross-origin iframe | `UNSUPPORTED_FRAME`; no permission bypass or frame script |
| B21 | Required field is in open/closed shadow DOM | V1 `UNSUPPORTED_FRAME`; no shadow traversal |
| B22 | Generated selector/structural ID changes between reads | `DOM_UNSTABLE`/`SELECTOR_MISSING`; never rewrite selector |
| B23 | Malicious label contains script/secret-like content | `WRONG_PAGE`; no raw label in error/audit/fixture |
| B24 | Adapter/runtime exception contains a raw value | public fixed code only; redaction assertion finds zero raw-value occurrence |
| B25 | Adapter Profile activates new version during capture | `PROFILE_MISMATCH`; no committed current Observation |
| B26 | Claim expires during capture | Observation capture may remain structural, but Mapping/Prepare authority revalidation blocks use; no value authority gained |
| B27 | Candidate/Queue/Prepare becomes invalid | capture cannot bypass chain; Mapping creation returns authority blocked |
| B28 | Crash after transaction journal or Observation record write | missing final commit keeps capture invisible; exact recovery only |
| B29 | Observation/result exists without required commit marker | not found/uncommitted; never current |
| B30 | Restart retries valid pending transaction | exact same capture/Observation IDs and hashes; no duplicate |
| B31 | Idempotency key reused with different target/profile/epoch | `CAPTURE_IDEMPOTENCY_CONFLICT`; writes 0 |
| B32 | Page never produces two equal structural reads | `DOM_UNSTABLE` or `CAPTURE_TIMEOUT`; no arbitrary sleep/fallback |
| B33 | Allowlisted target is prefilled | persist only `NONEMPTY`; raw bytes/length/hash absent; Mapping blocked |
| B34 | Required target is password/secret/payment/hidden token control | `SENSITIVE_FIELD`; control value not read; no Observation commit |
| B35 | Observation/capture/content/identity hash tampered | `HASH_MISMATCH`/store corrupt; no repair by guessing |
| B36 | Caller treats Observation/capture result as fill authority | reject boundary; all browser/fill/submit/Queue/Claim flags remain false |

## Positive capture and identity tests

1. exact purpose-bound human action, current aliases/Profile/epoch, two equal
   reads, unchanged mutation generation -> one committed Observation and one
   committed capture reference;
2. request body has exactly the capture schema fields and contains no DOM,
   selector, marker, fingerprint, or value;
3. raw browser target/protocol IDs are replaced by opaque server aliases;
4. exact same request/idempotency key replays byte-identical committed records;
5. same authority tuple under a second permitted retry resolves to the same
   capture; a different tuple cannot alias it;
6. page/form/field IDs and fingerprints recompute with the existing Gate 3C-1
   projections and `sha256-canonical-dom-structure-v1`;
7. current Observation registration occurs only after capture visibility
   commit and final stable check;
8. a later mutation/navigation signal makes the capture authority stale even
   if the immutable Observation bytes remain intact;
9. a Mapping Preview preflight and precommit freshness check see the same
   document/page/epoch/mutation generation or create zero Preview writes;
10. process restart without runtime continuity proof makes the old capture
    stale and requires a new explicit capture action.

## Stable capture tests

Use a deterministic synthetic adapter with scripted identity/mutation states;
do not instantiate a browser or DOM library.

- equal A/B reads at one generation commit;
- A/B content mismatch without generation signal still fails `DOM_UNSTABLE`;
- detached-node signal restarts one complete attempt;
- mutation between A and B restarts, never combines reads;
- mutation before final precommit check creates no current Observation;
- mutation immediately after record publication prevents capture commit/current
  activation and emits value-free `OBSERVATION_STALE`;
- maximum two attempts; third attempt never runs;
- timeout uses monotonic server policy, not page time or arbitrary sleep;
- partially loaded/late-hydrated forms remain unstable until a later human
  retry, never auto-wait indefinitely.

## Navigation/page identity tests

Assert the transition table in the design for full reload, cross-document and
same-document navigation, SPA route changes, BFCache restore, tab duplication,
main-frame replacement, iframe navigation, runtime restart, and reconnect.
For each transition:

- navigation epoch is monotonic and never reused;
- page instance never revives;
- browser/tab/frame/document identities change exactly as specified;
- same structural fingerprint cannot preserve stale authority;
- old capture action and idempotency tuple cannot retarget the new document.

## Raw-value containment tests

Synthetic controls use sentinel secrets that must be absent recursively from:

- adapter return objects;
- Observation, capture result, audit events, journals, commits, indexes, and
  current pointers;
- logs, fixed exceptions, traces, test fixtures, hashes, and telemetry.

Instrument the occupancy classifier so its only public result is the enum.
Assert no length, prefix, digest, encoded form, or derived raw-value metadata is
persisted. Password/credential/payment controls must not invoke raw-value read
at all.

## Schema and fixture validation

Validate with JSON Schema Draft 2020-12 and `FormatChecker`:

1. capture request schema;
2. safe browser/page identity schema;
3. committed capture result/reference schema;
4. safe audit event schema;
5. the existing Gate 3C-1 DOM Observation schema.

Validate all synthetic fixtures. Independently recompute identity, existing
form/field/page/Observation, capture-content, result-record, and audit-event
hashes using NFC, sorted object keys, preserved arrays, compact UTF-8 JSON, no
floats, and lowercase SHA-256.

Adversarial schema tests add every prohibited root field: bets, numbers,
multiplier, logical plan, selector, selector override, DOM, HTML, raw value,
occupancy result, page marker, claimed fingerprint, requested state, script,
URL, cookie, storage, screenshot, credential, browser token, fill, submit.
Every instance must fail rather than ignore the key.

## Persistence/recovery tests

Inject crashes:

- before/after transaction journal;
- after Observation record but before its commit;
- after Observation commit but before capture reference;
- after capture reference/audit/index but before capture visibility commit;
- after visibility commit but before current pointer;
- after current pointer but before final postcommit check detects mutation;
- with one valid pending transaction followed by one corrupt transaction.

All pending journals and partial outputs are pure-preflight validated before
recovery writes. A corrupt journal causes zero mutation. Exact valid recovery
resumes once. No uncommitted record is returned by an authoritative read.

## Existing authority regression

The implementation Gate must rerun Gate 3C-2's 44 focused tests and the five
sample-007/008/010/011/014 preservation cases. Capture cannot change Candidate,
HumanReview, Queue, Claim, Prepare, Adapter Profile, Mapping Preview, or
machine-evidence values. Existing Observation schema and fingerprints remain
byte-contract compatible.

Static tests reject imports/calls for browser launch/control, Playwright,
Selenium, navigation, click, typing, event dispatch, DOM mutation, fill,
Webfill, submit, Queue/Claim completion, external models, or network clients.

## Minimal implementation scope

Implement only server action/identity registries, a synthetic read-only adapter
interface, allowlist capture, stable-window protocol, immutable capture/audit
store, trusted ingestion into the existing Observation Store, freshness
coordination, and the tests above. Real browser integration remains a separate
explicitly authorized Gate.
