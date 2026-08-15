# Gate 3C-3 Read-Only Browser Observation Adapter Authority

Status: design only. `DESIGN_SAFE=YES` applies only to the trusted,
read-only capture boundary defined here. This Gate authorizes no browser
launch or control, navigation, login, arbitrary evaluation, DOM mutation,
click, typing, event dispatch, fill, Webfill, submit, Queue/Claim completion,
real-site read, or external call.

## Authority flow and trust boundary

The only admissible future flow is:

```text
explicit human capture gesture
  -> server-minted, purpose-bound capture action
  -> server-owned browser/target alias registry
  -> exact active Adapter Target Profile
  -> trusted read-only adapter
  -> stable, double-read structural capture
  -> existing immutable WebfillDomObservationStore
  -> committed Observation reference
  -> existing Gate 3C-2 Mapping Preview authority checks
```

The browser page, browser content process, renderer, client, and request body
are untrusted. They may carry only opaque server aliases. They cannot supply a
DOM snapshot, selector, field, value, marker, page fingerprint, occupancy
result, mapping rule, or requested outcome.

The trusted adapter is a server-side component with a fixed implementation
and an exact allowlist. It reloads the active Adapter Target Profile and its
exact logical Target Profile reference from server stores. It resolves opaque
browser/tab/frame aliases through a server-owned registry, verifies the
purpose-bound human capture action, observes only allowed structural facts,
constructs the existing `vision-webfill-dom-observation-v1` object, and calls
`WebfillDomObservationStore.register_observation` directly. No public API may
accept a completed Observation JSON.

An Observation remains structural evidence. Browser content never becomes a
betting-value authority. Candidate remains the only value authority and the
full Candidate -> Queue -> Claim -> Prepare chain remains mandatory.

## Explicit human capture authority

A capture starts only after an authenticated human explicitly selects an
already-open target and invokes a read-only capture action. The server mints
`capture_action_id` and binds it to:

- authenticated principal and interactive session;
- browser connection alias, tab alias, and main-frame alias;
- exact Adapter Target Profile ID/version/hash;
- exact logical Target Profile ID/version/hash;
- expected site/game, origin/path policy hash, page/form contract hash, and
  navigation epoch;
- one capture idempotency context and a short expiry.

The client cannot mint, alter, retarget, or extend this authority. The action
is single-purpose and single-target. Exact replay is idempotent; reuse for a
different target, profile, epoch, principal, or idempotency context is
rejected. A capture action grants observation only. It grants no navigation,
mutation, fill, submit, Mapping Preview override, or later execution right.

## Identity-only capture request

The normative schema is
`vision_webfill_browser_observation_capture_request_v1.schema.json`. Its exact
fields are server aliases and comparisons only:

- capture action ID;
- browser connection, tab, and frame aliases;
- expected site and game;
- exact Adapter and logical Target Profile identities;
- origin/path policy and page/form contract hashes;
- expected navigation epoch;
- capture idempotency key.

The handler supplies the authenticated principal and interactive session from
trusted server context. Unknown keys and every selector, DOM, HTML, value,
marker, URL, fingerprint, script, result-state, or betting field are rejected
before adapter access.

## Browser, tab, frame, document, and page identity

The normative safe identity schema is
`vision_webfill_browser_page_identity_v1.schema.json`. All public identifiers
are opaque server-minted aliases; raw browser target IDs, protocol tokens,
connection secrets, process handles, authorization headers, and renderer
capabilities are never returned or persisted.

Identity is layered:

1. `browser_runtime_id`: one browser process/runtime lifetime;
2. `browser_session_id`: one authenticated adapter attachment lifetime;
3. `browser_connection_id`: one server connection generation;
4. `tab_instance_id`: one tab lifetime; duplication creates another ID;
5. `frame_instance_id`: one main-frame lifetime;
6. `document_instance_id`: one committed document lifetime;
7. `page_instance_id`: one observable route/view lifetime;
8. `navigation_epoch`: monotonic per tab/main-frame lineage;
9. `observation_generation`: monotonic successful capture generation;
10. `dom_mutation_generation`: trusted runtime counter for observed structural
    mutations in the target document.

The safe identity also binds site/game and normalized origin/path hashes. It
contains no raw URL, query, fragment, cookie, storage, credential, or field
value.

### Identity transition rules

| Event | Required identity effect | Old Observation |
|---|---|---|
| Full reload or cross-document navigation | new document and page instance; increment navigation epoch | stale permanently |
| Same-document history/hash navigation | same document; new page instance; increment navigation epoch | stale permanently |
| SPA route transition | same document only if runtime proves it; new page instance; increment navigation epoch | stale permanently |
| BFCache restore | new page instance and incremented epoch; never restore old authority | stale permanently |
| Tab duplication | new tab, frame, document, page instance; epoch starts in new lineage | not transferable |
| Main-frame replacement/navigation | new frame/document/page instance; increment epoch | stale permanently |
| Target iframe navigation/replacement | V1 unsupported; capture blocked | unusable |
| Non-target iframe changes affecting required structure | increment DOM mutation generation | stale |
| Browser process restart | new runtime/session/connection IDs | all prior runtime observations stale |
| Browser reconnect | new connection ID; V1 requires fresh document/page proof and capture | old authority not resumed |

Epochs never decrease or reuse a prior tuple. A page that returns to the same
URL or structure receives a new page instance/epoch. Structural equality does
not revive identity.

## V1 browser/frame scope

V1 supports only a user-selected, already-open, same-origin main-frame light
DOM. It does not traverse cross-origin frames, iframes, open or closed shadow
roots, portals, or browser chrome. A required form or field in any unsupported
scope is `UNSUPPORTED_FRAME` or `UNSUPPORTED_CONTROL`. It is never reached by
script injection, frame switching, fuzzy discovery, or permission expansion.

## Allowlist-based DOM reads

The active Adapter Target Profile is the sole selector and semantic-rule
authority. The adapter may read only facts needed to build the existing
Observation schema:

- exact profile-declared page/form markers;
- main-frame form membership and document order;
- tag/control type for exact allowlisted elements;
- exact structural selector identity already declared by the profile;
- safe, profile-declared label/accessibility literals;
- visibility, disabled, and readonly booleans;
- allowlisted structural attribute identities or hashes;
- occupancy class `EMPTY`, `NONEMPTY`, or `UNKNOWN`.

The adapter must not enumerate unrelated fields merely because they exist.
Profile selectors are not modified when missing or duplicated. DOM order is
evidence, never a selector fallback.

### Safe labels and hostile content

Clear label/accessibility text may be persisted only when it is NFC,
length/character-policy safe, and exactly equals a trusted Profile literal.
Otherwise the capture fails `WRONG_PAGE` or persists only a profile-defined
safe hash where the existing schema permits it. Arbitrary `textContent`,
placeholder text, error text, live-region text, and page-supplied mapping hints
are never collected. A hostile label cannot become a selector or semantic
rule.

### Occupancy containment

If occupancy requires transient value access, the raw value exists only
inside a small trusted adapter function long enough to derive:

- `EMPTY`: control-specific value has zero logical content;
- `NONEMPTY`: some content exists, without exposing its bytes or length;
- `UNKNOWN`: the adapter cannot classify without risk or the control type is
  not safely classifiable.

The function returns only the enum and clears its local reference. It may not
return, interpolate, hash, measure, log, trace, journal, fixture, cache, or
include the raw value in an exception. Hashing a raw value is also prohibited
because the hash is derived sensitive data.

Password, credential, payment, hidden-token, autofill-sensitive, or
secret-class controls are never read. If a required selector resolves to one,
the whole capture fails `SENSITIVE_FIELD`. Unrelated sensitive controls are
not enumerated. Unsupported controls are `UNKNOWN` only when an otherwise safe
Observation remains useful as blocked evidence; they can never support a
`MAPPED_UNIQUE` field.

The adapter never reads/persists cookies, local/session storage, raw HTML,
scripts, handlers, request/response bodies, screenshots, clipboard, autofill
data, query, fragment, credentials, or payment data.

## Selector and structural safety

Selectors come only from the active Adapter Target Profile and must already
pass Gate 3C-1 structural-only validation. The capture adapter additionally
rejects XPath, JavaScript/evaluation expressions, event handlers, URLs,
query/fragment material, value interpolation, generated secret tokens, and
unsupported placeholders.

For each exact selector:

- zero matches -> `SELECTOR_MISSING`;
- more than one match -> `SELECTOR_AMBIGUOUS`;
- detached/stale node -> capture generation invalid, retry from the beginning;
- hidden/disabled/readonly -> record the safe state if the structural target
  is otherwise unique; Gate 3C-2 will block mapping;
- unsupported/sensitive control -> block as above;
- changed selector/label/order/marker -> second read differs and the capture
  fails closed.

Nested or duplicate forms must match the exact form contract uniquely. The
adapter does not guess the intended form from proximity, text, or index.

## Stable capture protocol

No arbitrary sleep represents stability. A trusted runtime supplies
navigation/document events and a monotonic structural mutation counter without
executing page-provided code or injecting arbitrary JavaScript.

One attempt is:

1. under the adapter authority lock, reload the human action, target aliases,
   active profiles, and expected policy hashes;
2. require the already-open target, same main frame, exact document/page
   instance, and expected navigation epoch;
3. record capture start time, document identity, epoch, and mutation generation
   `g0`;
4. perform structural read A from the Profile allowlist;
5. reread trusted runtime identity/generation; any navigation, document,
   frame, page-instance, or generation change aborts the attempt;
6. perform independent structural read B using the same Profile authority;
7. canonicalize both reads using Gate 3C-1 projections and require byte-equal
   content and equal fingerprints;
8. require final document/page identity, epoch, and mutation generation equal
   the start values;
9. build the existing Observation, compute its form/page/record hashes, and
   validate it using the existing Observation validator;
10. immediately before publication, recheck identity/generation and active
    Profile; publish record then commit marker last through the immutable
    Observation Store;
11. immediately after publication, recheck once. If it changed, do not make
    the capture-authority record current; append safe `OBSERVATION_STALE` audit
    and leave the immutable Observation unusable for Mapping Preview.

Retries restart from step 1 with no partial structural reuse. V1 permits at
most two attempts inside a server policy timeout. Retry is allowed only for a
navigation/mutation/detached-node race, never for wrong target, profile,
sensitive field, missing/ambiguous selector, or unsupported scope. A page that
does not produce two identical reads is `DOM_UNSTABLE`; timeout is
`CAPTURE_TIMEOUT`.

Late hydration is simply a mutation. There is no fixed wait for it. The human
may retry later after the page stabilizes.

## Fingerprint and canonicalization strategy

This Gate reuses Gate 3C-1 exactly:

- Unicode NFC;
- exact known keys and no floats/bool-as-int;
- sorted object keys and preserved array order;
- compact UTF-8 JSON;
- lowercase SHA-256;
- `sha256-canonical-dom-structure-v1` form/page projections;
- deterministic form/field IDs from their existing domain-separated
  structural projections.

The stable structural page fingerprint includes site/game, origin/path
identity hashes, page markers, ordered form fingerprints, ordered field
identity/type/label/selector/state evidence. It excludes observation ID/time,
page instance, navigation epoch, mutation generation, raw URL, query,
fragment, HTML, and values.

The per-document page instance and navigation epoch are bound separately in
both capture result and existing Mapping Preview authority. The Observation
content hash covers the complete existing Observation except its own hash.
The capture record integrity hash additionally binds browser/page identity,
capture action/profile provenance, stable-window generations, and Observation
reference. These hashes have distinct purposes and are never substituted for
one another.

The new hashes use these exact projections:

- `identity_integrity_hash`: the complete
  `vision-webfill-browser-page-identity-v1` object except that hash;
- `capture_request_hash`: the exact capture request with
  `capture_idempotency_key` removed;
- `capture_content_hash`: `{schema_version:
  vision-webfill-browser-observation-capture-content-v1, authority}` with
  `idempotency_key_hash` removed, plus complete `browser_page_identity`,
  `stable_capture`, and `observation_reference`;
- `record_integrity_hash`: the complete capture result except that hash;
- `authority_binding_hash`: `{schema_version:
  vision-webfill-browser-observation-authority-binding-v1, authority,
  browser_page_identity_hash}`;
- `event_integrity_hash`: the complete audit event except that hash.

Hashing raw DOM values, their length, encoding, or digest is prohibited.

## Capture-to-store transaction and recovery

The future adapter store is isolated under a new user-data namespace. A
transaction contains only safe identities, hashes, sanitized structures, and
the exact proposed Observation/capture-reference/audit records. It never
contains raw values, browser tokens, HTML, selectors supplied by a client, or
credentials.

Publication order is:

1. immutable capture transaction journal;
2. existing immutable Observation record through
   `register_observation(..., activate=false)`;
3. Observation commit marker;
4. immutable capture result/reference;
5. safe audit events and idempotency/authority indexes;
6. final capture visibility commit marker;
7. current-observation authority pointer only after the final runtime
   identity/generation check, using an exact idempotent
   `register_observation(..., activate=true)` replay of the same bytes.

Before any recovery write, every pending journal and partial output is
schema/hash/cross-record validated. One corrupt journal blocks all mutation as
`TRANSACTION_RECOVERY_REQUIRED`. An uncommitted Observation/capture result is
invisible to authoritative reads. Exact retry resumes the same capture ID and
Observation ID. Idempotency-key reuse with another authority tuple fails.

The immutable Observation Store remains unchanged. Only the trusted adapter
may call its registration method in the production composition root; public
routes never expose that method or accept Observation bodies.

## TOCTOU and freshness

Stable capture proves only one bounded observation window. It does not prove a
page stays unchanged forever.

The future capture-authority store retains the document/page/epoch/mutation
generation bound to each Observation. It subscribes to trusted runtime
navigation/disconnect/mutation signals and derives an Observation stale in
memory immediately. Persistence of the safe stale audit is idempotent; an I/O
failure cannot make the Observation usable.

Mapping Preview creation/read must add a narrow adapter freshness check around
the existing Gate 3C-2 Observation lookup:

1. verify the capture commit, exact Observation reference, browser runtime
   continuity, document/page instance, navigation epoch, mutation generation,
   and active Profile before compilation;
2. compile against the immutable Observation;
3. repeat the same adapter freshness check immediately before Mapping Preview
   commit.

If either check changes, creation returns `MAPPING_OBSERVATION_STALE` with zero
Preview writes. A mutation after commit makes the existing Preview terminal
`OBSERVATION_STALE`. Browser disconnect/reconnect, server restart without a
provable same runtime/document channel, profile advance, reload, navigation,
SPA transition, or BFCache restore all require a new capture.

Future fill must still revalidate or recapture. No Observation, capture result,
page fingerprint, or Mapping Preview grants fill authority.

## Safe error and audit model

The normative audit schema is
`vision_webfill_browser_observation_audit_event_v1.schema.json`. Public errors
use stable codes only:

- `AUTHORITY_BLOCKED`
- `WRONG_TARGET`
- `WRONG_PAGE`
- `WRONG_GAME`
- `PROFILE_MISMATCH`
- `NAVIGATION_RACE`
- `DOM_UNSTABLE`
- `SELECTOR_MISSING`
- `SELECTOR_AMBIGUOUS`
- `UNSUPPORTED_FRAME`
- `UNSUPPORTED_CONTROL`
- `SENSITIVE_FIELD`
- `OBSERVATION_STALE`
- `CAPTURE_TIMEOUT`
- `HASH_MISMATCH`
- `TRANSACTION_RECOVERY_REQUIRED`

Messages are fixed, value-free text. Audit context is limited to typed hashes
of safe server identities and contract records. It never contains raw values,
HTML, URL, query/fragment, selectors from the client, cookie/storage,
credentials, browser tokens, screenshots, or exception representations from
the page.

## Authority and state transitions

| Current | Event | Next | Authoritative effect |
|---|---|---|---|
| none | valid explicit action | AUTHORIZED | no Observation |
| AUTHORIZED | trusted adapter starts | CAPTURING | no Observation |
| CAPTURING | stable capture + commit | COMMITTED | Observation reference may be current |
| AUTHORIZED/CAPTURING | any fail-closed error | FAILED | zero authoritative Observation |
| COMMITTED | mutation/navigation/disconnect/profile change | STALE | Observation and dependent Preview unusable |
| COMMITTED/STALE/FAILED | any client retry | no mutation | requires exact replay or new action |

`FAILED` and `STALE` never transition back to COMMITTED. A new action creates
a new capture generation/reference. There are no FILLED, SUBMITTED, COMPLETED,
APPROVED, EXECUTING, or browser-action states.

## Trust-boundary data flow

```text
untrusted UI opaque aliases
  -> exact request decoder
  -> server human-action + target registry
  -> active Profile stores
  -> trusted read-only adapter
       [raw value may exist only transiently inside occupancy classifier]
  -> sanitized structure A/B
  -> canonical Gate 3C-1 Observation
  -> immutable Observation + capture store
  -> reference/hash-only result
```

No reverse path writes values or selectors into the page. No page-originated
rule crosses into the Profile. No browser observation reaches Candidate,
Queue, Claim, Prepare values, Human Answer, or machine evidence.

## Minimal next implementation Gate

The next Gate may implement only:

1. server-owned browser target/page identity registry interfaces with a
   synthetic read-only adapter protocol;
2. explicit capture-action binding and exact request decoder;
3. allowlist structural reader abstraction and value-contained occupancy
   classifier;
4. generation-based double-read stable capture;
5. capture/reference/audit/idempotency/transaction store;
6. direct trusted ingestion into existing `WebfillDomObservationStore`;
7. adapter freshness coordination surface for Gate 3C-2;
8. synthetic-fixture and adversarial tests, including all B01-B36 cases.

Implementation tests must use a deterministic in-memory/synthetic document
adapter. They must not launch/control a browser, read a real site, navigate,
execute page JavaScript, mutate DOM, click, type, fill, submit, complete Queue
or Claim, or make external calls.
