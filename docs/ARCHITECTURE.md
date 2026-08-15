# Betguard Assistant Architecture

## System boundary

Betguard Assistant is a local, auditable, human-authorized workflow. Machine
output is never executable authority. The current branch ends at synthetic,
read-only browser observation and does not implement real-site capture, DOM
mutation, fill, or submit.

## Authority flow

```text
Image / AI suggestions
        |
        v
Assisted Human Review
        |
        | explicit per-bet confirmation
        v
Persisted Human Confirmed Answer       (only value authority)
        |
        v
Immutable Candidate                    (value snapshot)
        |
        v
Candidate Consumption Validator        (freshness/integrity/structure)
        |
        v
Identity-only Queue                     (no copied bet values)
        |
        v
Claim / Lease                           (single consumer, fencing token)
        |
        v
Dry-run Prepare Artifact                (deterministic logical plan)
        |
        v
Mapping Preview                         (identity/hash pointers only)
        |
        v
Synthetic Read-only Browser Observation (no real provider)
```

Each downstream read revalidates upstream identity, integrity, lifecycle, and
freshness. A downstream store cannot repair, normalize, vote on, or overwrite
upstream values.

## Layer responsibilities

### Machine evidence and Assisted Human Review

Qwen, Gemma, PP-OCRv6, parser output, and development-time Codex Vision are
separate evidence sources. The review UI supports field-level adoption and
manual editing, but every adoption or edit clears `human_confirmed` until the
user explicitly confirms the bet again. Machine evidence remains immutable.

Dense-page or provider failure must not make review impossible. The user can
create a manual bet, edit its nested groups and scopes, and confirm it without
promoting a failed machine response.

### Human Review authority

`candidate_authority.py` persists Human Review revisions and explicit
confirmation metadata. Server-side compare-and-swap operations prevent a stale
browser session from confirming or creating a Candidate from an old revision.

### Immutable Candidate

Candidate values are loaded only from persisted Human Review state. Candidate
snapshots are immutable and content-addressed. Later Human Review edits make an
older Candidate stale through append-only lifecycle events; the old Candidate
bytes are not rewritten.

Cancelled bets are retained in the audit snapshot with `active=false` and
`executable=false`.

### Candidate Consumption Validator

`candidate_consumption.py` is the read-only authority boundary used by later
Gates. It validates schema, canonical content hash, lifecycle, persisted Human
Review projection, nested structure, and provenance. It returns
`VALID_CURRENT` or a stable fail-closed error and never mutates Candidate data.

### Identity-only Queue

`validated_candidate_queue.py` stores Candidate identity, revision, content
hash, FIFO sequence, and lifecycle only. It does not store numbers,
`number_groups`, multiplier, continuation, special-play, or bet payloads.

Enqueue and remove require explicit server-bound human actions. `prepare_next`
is diagnostic and read-only; it does not claim, fill, or complete an entry.

### Claim / Lease

`validated_candidate_claims.py` provides one active claim generation per Queue
Entry. Owner/session identity, expiry, generation, and fencing token are
checked on every authoritative access. Candidate authority loss blocks both the
Claim and Queue. Release, abandon, expiry, and authority blocking are
append-only lifecycle events. There is no `COMPLETED`, fill, or submit path.

### Dry-run Prepare

`webfill_prepare.py` compiles a deterministic logical plan from a freshly
validated Candidate. It preserves bet order, nested column groups, multiplier
order and scope, continuation, special play, and cancelled audit references.
Prepare is immutable, dry-run-only, and non-executable.

### Mapping Preview

`webfill_mapping_preview.py` maps logical pointers to a committed structural
Observation and a versioned Adapter Profile. The Preview stores hashes and
pointers rather than copied bet values. Ambiguous, missing, disabled, readonly,
or occupied fields remain blocked. A fresh Human Preview joins values at read
time without persisting them into the Mapping Preview.

### Synthetic read-only Browser Observation

`webfill_browser_observation.py` defines `ReadOnlyBrowserObservationPort` and a
stable two-pass capture protocol. The production core accepts only sanitized,
allowlisted structural observations from a trusted port. It records immutable
capture/audit data, uses commit-last visibility, and detects navigation,
document, mutation-generation, restart, and TOCTOU changes.

Only a synthetic driver exists in tests. There is no Playwright, Selenium,
browser-protocol, HTTP, or real-site provider in this production module.

## Persistence model

Authority stores use immutable snapshots, append-only lifecycle events,
deterministic canonical JSON, integrity hashes, atomic writes, and final commit
markers. Read paths ignore or reject incomplete transactions. Recovery resumes
only exact, validated pending work and fails closed on conflicts.

## Legacy isolation

The validated Candidate/Queue/Claim chain is isolated from legacy
`approved_fill_queue`, manual candidate registries, old mock queues, and legacy
Webfill routes. No implicit conversion or ID aliasing is allowed.

## Current completion boundary

Implemented and tested:

- Assisted Human Review and server-side Human Review authority
- immutable Candidate and consumption validation
- identity-only Queue
- Claim / Lease with fencing and recovery
- dry-run Prepare
- Mapping Preview
- synthetic read-only Browser Observation

Not implemented:

- Real Browser Read-only Provider
- real DOM capture
- Assisted Fill execution
- Queue/Claim completion by a fill consumer
- submit or auto-submit

The next permissible activity is design review for a Real Browser Read-only
Provider. It is not authorized by the current implementation.
