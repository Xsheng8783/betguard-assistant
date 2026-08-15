# Gate 3C-1 Webfill Adapter Authority and Read-Only Mapping Preview

Status: design only. `DESIGN_SAFE=YES` applies only to the read-only
observation and immutable Mapping Preview boundary defined here. This Gate
authorizes no browser navigation, click, typing, raw input-value content
exposure or persistence, DOM mutation,
event dispatch, fill, Webfill, submit, Queue completion, Claim completion, or
external call.

## Authority boundary

The only admissible authority flow is:

```text
Human-confirmed answer
  -> immutable Candidate
  -> validated Candidate Queue Entry
  -> exclusive ACTIVE Claim and fence
  -> Gate 3C-0 VALID_PREPARED Artifact
  -> immutable Adapter Target Profile
  -> committed read-only DOM Observation
  -> immutable Mapping Preview
  -> Human Preview
  -> a future, separately authorized Assisted Fill Gate
```

Human Review, Candidate, Queue, machine evidence, browser memory, or a DOM
Observation may not bypass `VALID_PREPARED`. A Mapping Preview is a diagnostic
mapping proposal. It is neither a fill plan nor execution authority.

## Existing Gate 3C-0 contract remains unchanged

The logical Target Profile in
`vision_webfill_target_profile_v1.schema.json` remains a semantic capability
contract with `dom_mapping_present=false`. Gate 3C-1 does not edit, reinterpret,
or add selector fields to it.

Gate 3C-1 introduces a separate immutable Adapter Target Profile. It references
one exact Gate 3C-0 Target Profile ID, version, and integrity hash. A logical
profile update or adapter-profile update is an explicit version change; no old
Prepare or Preview is silently reinterpreted.

## Exact identity-only request

The normative request schema is
`vision_webfill_mapping_preview_request_v1.schema.json`. The client supplies
only identities and comparisons:

- Prepare ID, record-integrity hash, deterministic-plan hash, and
  authority-binding hash;
- active Claim ID, generation, raw fence comparison, and server-session
  comparison;
- exact Adapter Target Profile ID, version, and integrity hash;
- committed DOM Observation ID, observation hash, page fingerprint, page
  instance ID, and navigation epoch;
- one idempotency key.

The server supplies authenticated principal and consumer identity from trusted
context. Raw fence, raw server session, and raw idempotency key are never
persisted in a Preview.

Unknown keys and every value-bearing field are rejected. In particular, a
request cannot contain bets, numbers, number groups, multiplier, special play,
continuation, logical plan, Candidate, DOM fields, selectors, current input
values, replacement values, or a client-built mapping.

## Required live revalidation

Preview creation and every authoritative Preview read must:

1. pure-preflight all pending Preview transactions before any upstream clock
   or recovery write;
2. enter the same Queue/Claim coordination used by Gate 3C-0;
3. reload the committed Prepare by identity and call its owner-fenced
   `get_prepare_state` equivalent under a narrow non-reentrant integration
   surface;
4. require exact `status=VALID_PREPARED`, state PREPARED, exact hashes, active
   Claim generation/fence/owner/session, CURRENT Candidate, QUEUED Queue Entry,
   and current Gate 3C-0 Target Profile;
5. load the exact active Adapter Target Profile and validate its reference to
   the Prepare's logical Target Profile;
6. load a committed read-only DOM Observation and verify its integrity,
   correct site/game/page/form identity, exact page fingerprint, page instance,
   and navigation epoch;
7. compile the mapping deterministically and validate every relation and hash;
8. commit journal, immutable Preview, PREVIEWED event, indexes, then visibility
   commit marker last.

Any authority failure before compilation creates no Preview. Mapping failures
such as missing or ambiguous fields may create a committed blocked Preview for
Human diagnostics, but never `VALID_MAPPING_PREVIEW`.

## Read-only DOM Observation

The normative schema is
`vision_webfill_dom_observation_v1.schema.json`. An Observation records only
sanitized structural evidence:

- observer-generated observation, page-instance, and navigation-epoch
  identity;
- hashed origin and normalized path identity, with no query or fragment;
- declared site/game page identity;
- ordered form and field identities;
- form/field structural fingerprints;
- safe selector candidates, label text, input/tag type, and structural marker
  hashes;
- visible, disabled, readonly, and occupancy state.

Occupancy is exactly `EMPTY`, `NONEMPTY`, or `UNKNOWN`. A trusted read-only
observer may derive only this value-free class; current input-value content is
never returned, logged, or persisted. `NONEMPTY`, `UNKNOWN`, disabled,
readonly, or not visible blocks mapping to that target. The observer must not
read cookies, local/session storage, hidden tokens, HTML source, scripts,
event handlers, query parameters, or fragments. It must not expose, log, or
persist form-value content; it may derive only the occupancy class.

An Observation has exact safety assertions that navigation, click, typing,
input/change dispatch, mutation, and submit were all false. An Observation
whose safety or integrity cannot be proved is unusable.

### Deterministic fingerprints

Canonical serialization uses the Gate 3B/3C rules: exact known keys, NFC
strings, no floats or booleans in integer slots, sorted object keys, preserved
array order, compact UTF-8 JSON, then lowercase SHA-256.

Each form fingerprint hashes its ordered structural projection:

```text
form identity + document order + method category + structural markers
+ each ordered field's identity, tag/type, label, safe selector candidates,
  structural markers, visibility/disabled/readonly, and occupancy class
```

The page fingerprint hashes:

```text
fingerprint algorithm version + site/game identity
+ origin/path identity hashes + ordered form fingerprints
+ page-level structural-marker hashes
```

It is not a hash of CSS selectors alone. `observed_at`, observation ID,
page-instance ID, and navigation epoch are excluded from the structural page
fingerprint but bound separately in the Preview. A reload/navigation must mint
a new page instance or increment the epoch, so the same structural fingerprint
cannot silently revive an old Preview.

`form_id` and `field_id` are deterministic truncated SHA-256 identities of
their canonical structural identity projections (with distinct form/field
domain separators). They are stable across observations of the same structure;
they are not random observer IDs. Collision or duplicate structural identities
are corruption/ambiguity, never resolved by document index guessing.

## Adapter Target Profile

The normative schema is
`vision_webfill_adapter_target_profile_v1.schema.json`. It is immutable and
versioned. It contains no betting values. It binds:

- one exact Gate 3C-0 logical Target Profile identity;
- expected site, game, origin/path hashes, fingerprint algorithm, and exact
  page/form structural fingerprints;
- deterministic, role-specific field rules;
- exact cardinality and lossless component policy;
- all browser/fill/submit authority flags false.

A field rule identifies a logical role and an exact selector template plus
expected form, tag/input type, label, and structural marker constraints.
Allowed placeholders are structural coordinates only:
`operation_index`, `group_index`, `number_index`, `multiplier_rule_index`, and
`component_index`. They are expanded from the immutable logical plan. Betting
values are never interpolated into selectors.

Selector candidates and templates are structural-only. XPath, JavaScript,
event-handler expressions, URLs, query/fragment material, dynamic tokens, and
any interpolation of user or betting values are forbidden.

The profile must declare rules for every required logical role. V1 requires
the browser already to be on the correct game page; it does not map or mutate
a game selector. Unknown roles, duplicate rule IDs, incomplete role coverage,
or profile/Observation mismatch are fail-closed.

## Deterministic mapping and states

The Mapping Preview schema is
`vision_webfill_mapping_preview_artifact_v1.schema.json`. One logical source
pointer becomes one or more exact target field IDs only through a declared
lossless component rule.

Each item contains:

- source Human Bet ID and operation index;
- canonical logical pointer into the live Prepare plan;
- logical action and component index;
- intended-value hash, not the raw intended value;
- candidate target field IDs and, only for unique success, selected target
  field IDs;
- one state, a non-numeric mapping-confidence class, and a stable blocking
  reason.

The intended-value hash is SHA-256 of canonical bytes for:

```json
{
  "schema_version": "vision-webfill-intended-value-binding-v1",
  "logical_pointer": "/logical_plan/operations/0/number_groups/1/0",
  "value": "value loaded only from fresh VALID_PREPARED during compilation"
}
```

The value is not copied to the Preview.

Allowed item states are:

- `MAPPED_UNIQUE`: exact page/form/rule match, required target count equals the
  declared cardinality, all fields are visible/enabled/writable/EMPTY, and no
  other item claims any selected field;
- `AMBIGUOUS`: more than the exact permitted candidate count or a multi-claim;
- `MISSING`: no matching field;
- `UNSUPPORTED`: profile/site cannot losslessly express the logical action;
- `BLOCKED`: field state, page/form identity, or another safety constraint
  prevents use.

`mapping_confidence` is `DETERMINISTIC_EXACT` only for `MAPPED_UNIQUE` and
`NONE` for every other state. It is not a probability or a model score.

There is no confidence score, voting, fuzzy text match, index fallback, or
manual client override that can convert an item to `MAPPED_UNIQUE`.

## Columns, multipliers, special play, and continuation

Nested `number_groups` are never flattened. Every number item retains its
operation, group, and number indexes. A column-bet summary is complete only
when every group and every required number component is `MAPPED_UNIQUE`, in
source order, with disjoint fields. One missing or ambiguous component makes
the entire bet `BLOCKED` or `UNSUPPORTED`; it is never converted to a normal
bet.

A multiplier rule, special play, or continuation may map to one field or an
exact ordered array of component field/option IDs only when the Adapter Profile
declares a deterministic lossless component mapping. Scope remains part of the
hashed source binding. A similar-looking field, incomplete components, or
undeclared scope representation is `BLOCKED`/`UNSUPPORTED`; scope is never
guessed.

Gate 3C-0 `cancelled_audit_refs` produce exclusion records only. They produce
zero mapping items and zero target-field claims.

## Immutable Mapping Preview

A Preview persists pointers, hashes, field identities, states, and audit
counts. It does not duplicate raw betting values. Human display joins the
Preview with a freshly revalidated `VALID_PREPARED` Artifact at read time.

`VALID_MAPPING_PREVIEW` requires every required item to be `MAPPED_UNIQUE`,
every active operation to be fully accounted for, every column mapping to be
complete, zero field multi-claims, all cancelled bets excluded, and zero
blocking reasons. Otherwise the committed diagnostic status is
`BLOCKED_MAPPING_PREVIEW`.

Both statuses remain immutable, non-executable, and unauthorized for fill or
submit. The whole-record integrity hash covers all persisted content. The
mapping-content hash excludes Preview ID/time/idempotency and hashes the exact
authority identities, ordered items, summaries, exclusions, and observation
binding.

## Idempotency and crash safety

The semantic tuple is:

```text
Prepare ID + Prepare record/plan/authority hashes
+ Claim ID/generation/fence hash/owner/session hashes
+ Adapter Target Profile ID/version/integrity hash
+ DOM Observation ID/hash/page fingerprint/page instance/navigation epoch
```

Exact key and tuple replay returns the same Preview. A different key for the
same tuple resolves through a unique tuple index to the same Preview. Key reuse
for another tuple is rejected. A deterministic recompile that differs from an
existing mapping-content hash is rejected as nondeterministic.

The isolated store uses immutable journals and records, append-only lifecycle,
no-overwrite publication, and a final visibility commit marker. Before any
clock/recovery write it validates every pending transaction, every partial
output, all hashes, indexes, schemas, and cross-record relations. One corrupt
journal prevents all mutation. No failure repairs or salvages a partial
mapping by guessing.

## Freshness and lifecycle

The immutable snapshot has `state_at_creation=PREVIEWED`. Current state is
derived from append-only events defined by
`vision_webfill_mapping_preview_lifecycle_event_v1.schema.json`:

| Event | Meaning |
|---|---|
| `PREVIEWED` | Initial immutable Preview; still no fill authority |
| `AUTHORITY_BLOCKED` | Prepare/Candidate/Queue/Claim authority failed |
| `EXPIRED` | Bound Claim reached trusted expiry |
| `OBSERVATION_STALE` | Page instance, navigation epoch, or fingerprint changed |
| `SUPERSEDED` | Logical or Adapter Target Profile active version changed |
| `INVALIDATED` | Integrity/policy relation no longer permits mapping |
| `REVOKED` | Explicit trusted administrative revocation |

All events after PREVIEWED are terminal. A terminal event never edits the
Preview. Derived authority is fail-closed even if terminal-event persistence
fails; the event is recovered idempotently later.

Exact effects:

- Prepare non-PREPARED, Candidate non-CURRENT, Queue BLOCKED/REMOVED, or Claim
  AUTHORITY_BLOCKED/RELEASED/ABANDONED -> `AUTHORITY_BLOCKED`;
- trusted Claim expiry -> `EXPIRED`;
- DOM page-instance/epoch/fingerprint/form change -> `OBSERVATION_STALE`;
- logical Target Profile or Adapter Target Profile active-version change ->
  `SUPERSEDED`;
- integrity or deterministic relation failure -> unusable immediately; only a
  trusted relation may append `INVALIDATED` rather than masking corruption.

There are no FILLED, SUBMITTED, COMPLETED, EXECUTING, or APPROVED events.

## Human Preview

Human Preview is read-only. It reloads and revalidates the Prepare and Preview,
then joins logical pointers with current Prepare values for display. It may
show, per Human Bet, the confirmed value, target field labels/IDs, group
boundaries, mapping state, and blocking reason. Raw values remain sourced from
the Prepare response and are not written into the Preview.

The UI must clearly distinguish `VALID_MAPPING_PREVIEW` from a blocked
diagnostic Preview. It cannot edit field mapping, override ambiguity, confirm
fill, dispatch a DOM action, or treat a Preview as submit authority.

## Future Assisted Fill boundary

A later Gate may design a separate explicit-human Assisted Fill action only if
all of these are revalidated at action time:

- `VALID_PREPARED`;
- `VALID_MAPPING_PREVIEW`;
- ACTIVE owner/session/fenced Claim;
- exact current page instance/navigation epoch/fingerprint;
- current logical and Adapter Target Profiles;
- a server-minted, purpose-bound, single-use explicit human action.

Even then, Assisted Fill and Submit authority remain separate. This design
does not authorize either. `prepare_next`, an Observation, a Preview ID, a
field ID, or a Human Preview click is not an execution capability.

## Stable failure classes

The implementation Gate should expose stable, value-free errors including:

- `MAPPING_REQUEST_INVALID`
- `MAPPING_PREPARE_INVALID`
- `MAPPING_CLAIM_INVALID`
- `MAPPING_TARGET_PROFILE_NOT_FOUND`
- `MAPPING_TARGET_PROFILE_SUPERSEDED`
- `MAPPING_TARGET_PROFILE_INTEGRITY_INVALID`
- `MAPPING_OBSERVATION_NOT_FOUND`
- `MAPPING_OBSERVATION_STALE`
- `MAPPING_OBSERVATION_INTEGRITY_INVALID`
- `MAPPING_PAGE_IDENTITY_MISMATCH`
- `MAPPING_FORM_IDENTITY_MISMATCH`
- `MAPPING_AMBIGUOUS`
- `MAPPING_MISSING`
- `MAPPING_UNSUPPORTED`
- `MAPPING_FIELD_BLOCKED`
- `MAPPING_IDEMPOTENCY_CONFLICT`
- `MAPPING_NONDETERMINISTIC`
- `MAPPING_HASH_MISMATCH`
- `MAPPING_SCHEMA_INVALID`
- `MAPPING_STORE_CORRUPT`
- `LEGACY_WEBFILL_INTEROP_FORBIDDEN`

## Isolated persistence namespace

The future implementation uses a new user-data namespace and never imports or
converts legacy fill queues or manual Candidate registries:

```text
<user-data>/vision/webfill-mapping-preview-v1/
  dom-observations/
  dom-observation-commits/
  adapter-target-profiles/
  adapter-target-profile-active-version/
  mapping-previews/
  mapping-preview-commits/
  lifecycle-events/
  idempotency/
  authority-tuple-index/
  preview-transactions/
  lifecycle-transactions/
```

## Minimal next implementation Gate

The next Gate may implement only immutable Adapter Target Profile and DOM
Observation stores, exact request decoding, deterministic read-only mapping,
immutable Preview/lifecycle/idempotency/recovery, Human Preview DTO generation,
and the locked 24-case/five-sample tests. A test DOM fixture may be inspected
read-only; production browser control, navigation, DOM mutation, click, typing,
events, fill, Webfill, submit, Queue completion, Claim completion, and external
calls remain forbidden.
