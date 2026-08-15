# Runtime Reader Policy

Status: frozen for the current checkpoint branch.

This document defines reader roles only. It does not change provider routing,
promote a model to value authority, or authorize any external call.

## Authority rule

Human Confirmed Answer is the only Candidate value authority. Every machine
reader is evidence-only until a user explicitly adopts, edits, and confirms the
result through Assisted Human Review.

## Codex Vision

- Role: development, annotation, debugging, and benchmark analysis only.
- Production runtime calls: **0**.
- It must not become a required customer-side dependency.
- Its suggestions may be displayed as machine evidence but cannot establish
  Human Truth or Candidate values.

## PP-OCRv6

- Role: local OCR, bounding-box, polygon, and evidence helper.
- Geometry may support human review and deterministic analysis.
- PP regions must not automatically create bets, merge entities, or become
  Human Truth.

## Gemma 4 26B

- Role: planned primary raw-handwriting reader candidate.
- Strength under evaluation: handwritten number, multiplier, stacked-category,
  and dense-page raw reading.
- Current status: evidence-only shadow/review source.
- This policy freeze does not switch production authority or routing to Gemma.

## Qwen

- Role: current machine source.
- Planned long-term role: on-demand difficult-case fallback candidate.
- It should not remain an unconditional full-page call for every image.
- Truncation, timeout, schema failure, or invalid output must not prevent manual
  review and must never be treated as a complete prediction.

## Human Confirmed Answer

- Role: sole Candidate value authority.
- Confirmation is explicit and revision-bound.
- Any subsequent edit invalidates the prior confirmation until reconfirmed.
- Machine agreement can reduce review effort but cannot auto-confirm a field or
  bet.

## Enforcement boundary

The current implementation enforces authority at Human Review, Candidate,
Candidate Consumption, Queue, Claim, Prepare, and Mapping boundaries. Provider
routing changes require a separate Gate, tests, and explicit authorization.
