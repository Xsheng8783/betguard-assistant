# Gate 3C-1 Webfill Adapter and Mapping Preview Test Matrix

Status: design-only test specification. No production implementation, browser
navigation, DOM mutation, click, typing, input/change dispatch, fill, Webfill,
submit, Queue/Claim completion, or external call is authorized.

## Locked 24-case acceptance matrix

| ID | Scenario | Required result |
|---|---|---|
| M01 | Correct game page, exact active profile, one visible/writable/EMPTY field per required role | Immutable `VALID_MAPPING_PREVIEW`; every required item `MAPPED_UNIQUE` + `DETERMINISTIC_EXACT`; all execution flags false |
| M02 | Instantiated structural selector has no observed field | Item `MISSING`, diagnostic `BLOCKED_MAPPING_PREVIEW`; no guessed target |
| M03 | Two observed fields satisfy one exact rule | Item `AMBIGUOUS`; no selected target and no confidence tie-break |
| M04 | Page structural fingerprint differs from active Adapter Profile | `MAPPING_PAGE_IDENTITY_MISMATCH` before mapping, or committed `OBSERVATION_STALE` for an existing Preview |
| M05 | Observation game/site/path identity is wrong | blocked; V1 does not map or mutate a game selector |
| M06 | Target is disabled, readonly, invisible, NONEMPTY, or UNKNOWN | `BLOCKED`; current value content is never exposed/logged/persisted; only occupancy may be derived |
| M07 | Normal bet | exactly one nested group retained; every number pointer maps in source order |
| M08 | Column bet | every group/number coordinate preserved; all-or-nothing column completeness; never flattened |
| M09 | Multiplier | declared exact ordered component mapping only; scope is hash-bound; incomplete/ambiguous is blocked |
| M10 | Special play | declared lossless component mapping only; kind/raw/scope source binding preserved without raw value persistence |
| M11 | Continuation | explicit continuation rule and exact source scope required; otherwise `UNSUPPORTED`/`BLOCKED` |
| M12 | Cancelled Candidate record | exclusion reference only with `mapping_item_count=0`; no target claim |
| M13 | Prepare becomes non-PREPARED or its record/plan/binding hash changes | no new Preview; existing Preview terminal `AUTHORITY_BLOCKED` |
| M14 | Claim reaches trusted expiry | no new Preview; existing Preview terminal `EXPIRED` |
| M15 | Wrong Claim generation/fence/owner/session | `MAPPING_CLAIM_INVALID`; zero Preview writes; raw fence/session absent from store |
| M16 | Candidate becomes STALE/INVALIDATED/SUPERSEDED/REVOKED through Prepare revalidation | no new Preview; existing Preview `AUTHORITY_BLOCKED` |
| M17 | Queue becomes BLOCKED/REMOVED | no new Preview; existing Preview `AUTHORITY_BLOCKED` |
| M18 | Logical Target Profile or Adapter Target Profile active version advances | old Preview `SUPERSEDED`; exact old profile cannot compile |
| M19 | Page fingerprint, page instance, navigation epoch, or form fingerprint changes after Preview | `OBSERVATION_STALE`; reload/restart cannot reuse old instance identity |
| M20 | Request includes bets/numbers/groups/multiplier/special/continuation/logical plan/DOM fields/selectors/replacement value | `MAPPING_REQUEST_INVALID`; zero writes |
| M21 | Preview item/field/hash/authority record tampered, including fully rehashed logical pointer/value binding mismatch | `MAPPING_HASH_MISMATCH` or `MAPPING_STORE_CORRUPT`; never VALID |
| M22 | Server restart and lost response | committed bytes replay identically; page-instance authority must be re-established; stale instance cannot be reused |
| M23 | Multiple active bets | bet, group, number, multiplier-rule and mapping-item order preserved; field claims disjoint |
| M24 | Website cannot express columns/scope/components losslessly | `UNSUPPORTED` or `BLOCKED_MAPPING_PREVIEW`; zero fallback conversion |

## Schema, canonicalization, and storage adversarials

All five schemas validate under JSON Schema Draft 2020-12 with format checking:

1. Mapping Preview request;
2. read-only DOM Observation;
3. Adapter Target Profile;
4. Mapping Preview Artifact;
5. Mapping Preview lifecycle event.

Domain validators additionally reject:

- unknown/missing keys at every depth, floats, non-NFC strings, bool-as-int,
  invalid IDs/hashes/timestamps, duplicate IDs, and noncontiguous indexes;
- raw input values, query/fragment, cookies/storage, HTML/script, event handlers,
  XPath, JavaScript selectors, user-value interpolation, credentials, or
  machine/Candidate/Prepare value copies in Observation/Profile/Preview stores;
- selector placeholders outside the five structural-coordinate names;
- field selector kind/text not exactly present in the committed Observation;
- selector-only fingerprinting, mismatched form/page fingerprints, and
  reused page-instance/navigation epoch after reload;
- a `MAPPED_UNIQUE` item with wrong cardinality, any non-unique field claim,
  blocked field state, confidence other than `DETERMINISTIC_EXACT`, or a
  non-null blocking reason;
- any non-unique state whose confidence is not exactly `NONE`;
- a blocked item with selected target IDs, or `VALID_MAPPING_PREVIEW` with any
  non-unique item/incomplete column/multi-claim/blocking reason;
- multiplier/special/continuation components not declared by the exact active
  profile or reordered across components;
- raw fence/session/idempotency key in persisted records;
- PREVIEWED followed by more than one terminal event, sequence gaps, invalid
  cross-record hashes, or forbidden FILLED/SUBMITTED/COMPLETED events.

Persistence tests use no-overwrite immutable publication and inject crashes:

- before Preview artifact publication;
- after artifact/event but before indexes;
- before final visibility commit;
- after terminal journal but before terminal event;
- with one valid pending journal followed by one corrupt journal.

All pending journals and partial outputs are validated before any upstream
clock/recovery write. Exact retry resumes one Preview. Same tuple under another
key resolves to the same Preview. Key reuse for another tuple fails. Corrupt
preflight leaves both Mapping and upstream authority trees byte-for-byte
unchanged.

## Fingerprint and deterministic mapping assertions

Tests recompute form, page, intended-value, mapping-content, profile,
observation, event, and whole-record hashes independently. They assert:

- form field order and page form order are preserved;
- form/field IDs are recomputed deterministic structural identities and remain
  stable across same-shape observations; random IDs/collisions are rejected;
- page fingerprint includes site/game, origin/path hashes, form fingerprints,
  labels, field identities/types/states, and structural markers;
- observation ID/time/page instance/epoch do not alter the structural
  fingerprint but are separately bound in the Preview;
- selector strings alone cannot reproduce the page fingerprint;
- intended-value hash is recomputed only from a fresh Prepare pointer/value
  binding and the raw value is absent from Preview bytes;
- recompiling one exact tuple produces byte-equivalent ordered mapping content;
- client-supplied confidence, mapping override, source value, or target choice
  is rejected rather than ignored.

## Five real-shape preservation fixtures

Fixtures start from the existing committed Gate 3C-0 Prepare shapes and a
synthetic read-only DOM Observation. They do not open a betting website, read
an image/model artifact, or mutate Human Truth.

| Sample | Required mapping-design preservation |
|---|---|
| sample-007 | 24 active Human Bets remain ordered; normal/column and special scopes retained; one cancelled record excluded with zero mapping |
| sample-008 | corrected two-digit numbers and every nested group pointer retained |
| sample-010 | ordered `2X2`, `3X5` rules and special scope map through declared lossless components only |
| sample-011 | corrected `30`, `3/4X1`, column groups, and continuation retain distinct pointers/scopes |
| sample-014 | multi-line continuation, independent multipliers, special scope retained; cancelled audit excluded |

For every sample:

1. one bet summary exists for every active Prepare operation in exact order;
2. every number item retains its operation/group/number coordinate;
3. intended-value hashes independently join back to the fresh Prepare without
   raw values in Preview storage;
4. cancelled refs yield no mapping item or field claim;
5. Candidate, HumanReview, Queue Entry/ledger, Claim snapshot/ledger, Prepare,
   DOM fixture, and evidence bytes are unchanged (Claim clock high-water metadata
   may advance only as required by the existing authority read contract);
6. browser navigation, DOM mutation, click, typing, event dispatch, fill,
   Webfill, submit, Queue/Claim completion, and external calls are zero.

## Lifecycle assertions

```text
no Preview -> PREVIEWED
PREVIEWED -> AUTHORITY_BLOCKED
PREVIEWED -> EXPIRED
PREVIEWED -> OBSERVATION_STALE
PREVIEWED -> SUPERSEDED
PREVIEWED -> INVALIDATED
PREVIEWED -> REVOKED
```

Every terminal state has no outgoing transition. A blocked diagnostic Preview
still begins PREVIEWED but has `BLOCKED_MAPPING_PREVIEW`; it never becomes valid
through client override. A new valid observation/profile/Prepare tuple creates
a new immutable Preview rather than editing an old one.

## Human Preview acceptance

The read-only Human Preview DTO is produced only after fresh Prepare and
Preview revalidation. It may display fresh Prepare values alongside target
field labels/IDs and READY/BLOCKED status, but it persists no joined raw value.
It exposes no adopt/edit/fill/submit action in this Gate.

Static tests fail if the implementation imports or calls browser-control,
Selenium/Playwright execution, DOM mutation, navigation, click, typing,
event dispatch, legacy queue/manual Candidate/approved-fill, Webfill, submit,
Queue completion, Claim completion, or external model modules.

## Minimal implementation scope

The next Gate may implement only immutable read-only Observation and Adapter
Profile stores, exact request decode, deterministic mapping compiler,
Preview/lifecycle/idempotency/recovery, and read-only Human Preview DTOs with
the 24 cases and five fixtures above. A future explicit-human Assisted Fill
Gate must revalidate `VALID_PREPARED`, `VALID_MAPPING_PREVIEW`, active fence,
page instance/epoch/fingerprint, and current profiles, and still must not gain
Submit authority.
