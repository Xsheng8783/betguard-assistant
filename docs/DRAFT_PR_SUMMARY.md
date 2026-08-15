# Draft PR Summary: Repository Hardening and Runtime Authority

## Current authority pipeline

This checkpoint documents and tests the pipeline from machine evidence through
Assisted Human Review, Human Confirmed Answer, immutable Candidate, Candidate
validation, identity-only Queue, Claim/Lease, dry-run Prepare, Mapping Preview,
and synthetic read-only Browser Observation.

## Safety boundaries

- Human Confirmed Answer is the only Candidate value authority.
- Queue, Claim, Mapping, and Browser Observation do not copy or overwrite bet
  values.
- Browser Observation is synthetic/read-only; no real provider is present.
- No DOM mutation, Webfill execution, submit, or auto-submit is authorized.
- External model calls are disabled in deterministic CI.

## Tests

CI retains the existing parser/vision core group and adds an authority and
read-only synthetic group covering Gate 3B-1, 3B-2, 3B-3, 3B-3B, 3C-0, 3C-2,
3C-4, and Assisted Human Review Productization.

## Not completed

- Real Browser Read-only Provider
- real-site DOM capture
- Assisted Fill adapter/execution
- submit and auto-submit

## Migration and release risks

- The GitHub repository is public and requires a visibility review before
  non-public operational material is added.
- Legacy queues and fill paths coexist in the repository but must remain
  isolated from the validated Candidate authority chain.
- Model routing is intentionally unchanged; Gemma is not promoted by this
  checkpoint.
- A future real-browser provider must preserve sanitized observation,
  continuity, TOCTOU, and raw-value-containment contracts.

This file is a summary only. It does not create or merge a pull request.
