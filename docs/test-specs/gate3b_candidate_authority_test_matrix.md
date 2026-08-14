# Gate 3B Candidate Authority Test Specification

This is a specification, not executable production integration.

## A. Schema and authority tests

| ID | Given / mutation | Expected |
|---|---|---|
| A01 | Valid normal Human Answer | `number_groups` has exactly one group; human authority only |
| A02 | Valid column Human Answer | nested `number_groups` deep-equal and in original column order |
| A03 | Flatten a column into one number group | `COLUMN_STRUCTURE_INVALID` |
| A04 | Add model numbers/multiplier/raw text under machine provenance | schema or `MACHINE_VALUE_AUTHORITY_FORBIDDEN` |
| A05 | Set value authority to Qwen, Gemma, PP, parser, or reconstruction | fail closed |
| A06 | Unknown root/bet/special/provenance field | schema invalid |
| A07 | Number 40 in 539 / 49 in a supported 01-49 game | 539 fails; the 01-49 game passes if otherwise valid |
| A08 | Duplicate number inside normal/column group | fail; never silently deduplicate |
| A09 | Partial or uncertain multiplier | fail; never canonicalize by guessing |
| A10 | Two independent multiplier rules `2X3`,`3X1` | retain two rules and order |
| A11 | Same-value human rule `2/3X1` | retain exactly as confirmed |
| A12 | Cancelled record moved into active list | fail closed |
| A13 | Cancelled last answer incomplete | allowed only in `cancelled_audit`, audit-only/non-executable |
| A14 | Empty active list | `CANDIDATE_NOT_READY` |
| A15 | `human_bet_id=H-007` | accepted; malformed/non-human ID rejected |
| A16 | Active valid bet | `active=true`, `cancelled=false`, `executable=true`, while Candidate remains not fill-approved |
| A17 | Machine evidence ref | must have `value_authority=false`; any value-bearing machine field rejected |
| A18 | Active multiplier has `ordered_rules=[]` | schema invalid; active bet requires at least one rule |
| A19 | Any `number_groups` member is empty | schema invalid; no empty column/group is accepted |

## B. Canonical hash tests

| ID | Change | Hash expectation |
|---|---|---|
| B01 | Change candidate ID only / change human bet ID | candidate ID: unchanged; human bet ID: changed |
| B02 | Change request/session/local image ID only | unchanged |
| B03 | Change actor or any timestamp only | unchanged |
| B04 | Change machine provider/model/request/cache/artifact ref / source image hash | machine ref: unchanged; source image hash: changed |
| B05 | Change one Human Answer number | changed |
| B06 | Move a number between columns | changed |
| B07 | Reorder columns, bets, numbers, or multiplier rules | changed |
| B08 | Change cancellation status/reason/last answer | changed |
| B09 | Same strings in NFC vs decomposed Unicode | unchanged after NFC |
| B10 | Different object insertion order/pretty whitespace | unchanged |
| B11 | Unknown field or float reaches canonicalizer | rejected before hashing |

## C. Revision, stale, duplicate, reload

| ID | Sequence | Expected |
|---|---|---|
| C01 | Confirm all, obtain HumanReview rev/hash, edit one field, create using old pair | `409 REVIEW_STALE`, zero writes |
| C02 | Confirm then adopt AI numbers/layout/multiplier | confirmation and ready hash cleared; revision increments |
| C03 | Confirm cancellation then change it | confirmation cleared; old create request stale |
| C04 | Double-click identical create | one Candidate revision; second response replayed |
| C05 | Lost response then identical retry | same candidate ID/revision/hash |
| C06 | Reuse idempotency key with another revision/hash | `409 IDEMPOTENCY_CONFLICT` |
| C07 | Reload before create | persisted review revision/hash restored |
| C08 | Reload after create | same Candidate result returned; no duplicate |
| C09 | Create changed Human Answer after fresh confirmation | new immutable snapshot; old JSON unchanged; append SUPERSEDED/CREATED events |
| C10 | Future approval bound to old revision after supersede | stale/block; no fill |
| C11 | Revoke active revision | terminal REVOKED; audit retained; fill blocked |
| C12 | Concurrent creates with same expected revision | one succeeds; equivalent one replays; conflict never creates two |
| C13 | Atomic persistence interruption | old complete state or new complete state; never partial JSON |
| C14 | Edit a HumanReview after Candidate creation | append STALE; Candidate JSON remains byte-identical |
| C15 | Detect immutable-record integrity/authority corruption | append INVALIDATED; do not rewrite snapshot |
| C16 | Fresh replacement after STALE | append SUPERSEDED for old revision and CREATED for new revision |

## D. Safety / side effects

For every success and failure in this specification assert:

- no `_manual_candidates` mutation;
- no `manual_candidate_id` or legacy `accepted_by_human` authority;
- no `approved_fill_queue`, draft, ground truth, candidate-to-fill conversion,
  webfill, paid fallback, auto-confirm, or auto-submit side effect;
- RecognitionResult, reconstruction, Qwen/Gemma/PP cache/evidence deep-equal;
- active valid bets may be `executable=true`, but Candidate-level
  `candidate_only=true` and `approved_for_fill=false` forbid queue/fill;
- cancelled bets are always inactive and non-executable.

## E. Real-sample matrix

### sample-007 -- messy review and cancellation

1. Build only from the final Human Answer snapshot, never draft/model evidence.
2. Expected reviewed set is 25 records: 24 active plus 1 human-confirmed
   cancellation. The cancelled record exists only in `cancelled_audit`.
3. Any unresolved linkage/special scope blocks Candidate creation; it is not
   converted from model or PP evidence.
4. Once explicitly resolved and reconfirmed, all structured special values
   survive exact round-trip.
5. Reload and duplicate-click cases C04-C08 use this larger document.
6. A snapshot with only 24 of 25 records confirmed fails
   `CANDIDATE_NOT_READY`; 24 active plus 1 confirmed cancellation is required.

### sample-008 -- field-separated Gemma adoption

1. S03: adopting Gemma numbers `08 / 01,04` invalidates confirmation; existing
   Qwen multiplier is unchanged until separately edited/confirmed.
2. S04: adopting `08 / 16,26` must not import Gemma `2X1` as a side effect.
3. S06: adding `09` changes only the explicitly selected number field.
4. Before fresh confirmation, create fails. After confirmation, Candidate value
   authority is `human_answer`; Gemma appears only as an evidence digest/ref.

### sample-010 -- multiple multiplier rules

1. Human Answer `32 34 35` with `2X2` and `3X5` remains one active bet.
2. Rules are neither merged nor split; their order is preserved in the hash.
3. Tail/car/half-car/each/special/scope edits invalidate confirmation and remain
   exact in Candidate special fields.

### sample-011 -- OCR substitution and columns

1. S02 uses the explicitly confirmed Human Answer (`30` if corrected by the
   human, otherwise `34`); Candidate creation never applies a 30/34 heuristic.
2. `3/4X1` remains present when the Human Answer contains it, including when
   reconstruction status had been incomplete.
3. Column examples retain `[[21,35],[23],[34],[37]]` and
   `[[34],[23],[35],[27,37]]` exactly; moving 35 or 37 changes the hash.

### sample-014 -- fragmented multipliers, independent rules, cancellations

1. S05 divergent `2/3X0.1` vs `2/3/4X0.1` cannot create until a human chooses
   and confirms one value.
2. S06 `2X1` retains the confirmed value.
3. S11/S12 retain independent `2X3` and `3X1` rules without false collision.
4. S13 retains `2X1`.
5. Strikethrough cancellation and each/half-car scope remain blockers until
   explicit resolution; cancelled structures are audit-only.
6. Changing a resolved cancellation/scope produces a new hash/revision.

## F. Endpoint contract tests for the next gate

The future endpoint accepts only review session ID, expected Human Answer
revision/hash, and idempotency key. It reloads persisted HumanReview values and
append-only events server-side; browser-local summaries and confirmation flags
are never authority. Tests must reject any request that
includes `active_bets`, `number_groups`, `multiplier`, model output,
or a client-selected Candidate hash. The response may return Candidate metadata
and the server-created document, but must never write queue/fill state.
