# Safety Invariants

These rules are non-negotiable for the current branch.

## Value authority

- Human Confirmed Answer is the only Candidate value authority.
- AI/OCR/parser/geometry output is suggestion or evidence only.
- A machine suggestion cannot directly create Candidate, Queue, Claim,
  Prepare, Mapping, Webfill, or submit work.
- Any human edit or suggestion adoption clears confirmation until the user
  explicitly reconfirms that bet.
- Cancelled bets remain audit-only with `active=false` and
  `executable=false`.

## Immutable authority chain

- Candidate snapshots are immutable and content-addressed.
- Queue Entries contain Candidate identity only, never copied bet values.
- Claims grant temporary single-consumer ownership, not value authority.
- Prepare and Mapping Preview are immutable dry-run artifacts.
- Every downstream access revalidates upstream lifecycle, hash, revision,
  owner/session identity, generation, fencing token, and expiry as applicable.
- Stale, revoked, superseded, invalidated, ambiguous, or corrupt authority
  fails closed.

## Browser and execution boundary

- No Real Browser Read-only Provider exists in the current branch.
- No production component launches or controls a browser for Gate 3C-4.
- No real-site navigation, DOM mutation, click, typing, event dispatch, fill,
  submit, or auto-submit is authorized.
- Browser Observation accepts sanitized allowlisted structure only.
- Raw input values, HTML, URL/query/fragment, cookies, storage, credentials,
  screenshots, and browser tokens must not be persisted or hashed.
- The synthetic driver is test-only and cannot become runtime authority.

## Mapping safety

- Ambiguous or missing selectors remain blocked.
- Column groups are atomic and must never be flattened.
- Multiplier, continuation, and special-play scopes must be mapped losslessly
  or remain blocked.
- A Mapping Preview cannot override Human Review or Candidate values.

## Lifecycle restrictions

- No automatic confirmation or automatic queue insertion.
- No claim or prepare operation marks work `COMPLETED`.
- No current lifecycle may use `FILLED` or `SUBMITTED`.
- Queue removal, claim release/abandon, and authority invalidation are explicit
  append-only events.

## Repository boundary

- API keys, credentials, customer images, OCR datasets, LOCALAPPDATA caches,
  model weights, and generated benchmark artifacts must remain outside Git.
- External model calls are forbidden in deterministic CI groups.
- Public-repository visibility must be reviewed before release or before any
  non-public operational material is introduced.
