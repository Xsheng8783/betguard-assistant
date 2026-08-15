# Betguard Assistant

Betguard Assistant is a local-first, human-authorized review system for
Taiwan lottery bet slips and text input. Machine readers can propose evidence,
but they cannot create an executable value authority. Every value that crosses
the Candidate boundary must come from a persisted Human Confirmed Answer.

This branch contains the current authority pipeline and synthetic read-only
browser-observation core. It is no longer limited to the original mock-review
prototype.

## Current authority pipeline

```text
Image / AI suggestions
  -> Assisted Human Review
  -> Human Confirmed Answer
  -> Immutable Candidate
  -> Candidate Consumption Validator
  -> Identity-only Queue
  -> Claim / Lease
  -> Dry-run Prepare Artifact
  -> Mapping Preview
  -> Synthetic Read-only Browser Observation
```

The stages have strict responsibilities:

- AI, OCR, parser, and geometry outputs are suggestions or evidence only.
- Human Confirmed Answer is the only Candidate value authority.
- Candidate is an immutable snapshot; Queue and Claim store identity and
  provenance, not copied bet values.
- Prepare and Mapping Preview are deterministic dry-run artifacts.
- Browser Observation currently has only a trusted read-only port and a
  synthetic driver used by tests.

## Not implemented

The following capabilities are deliberately absent:

- Real Browser Read-only Provider
- real-site DOM capture
- Assisted Fill execution
- website submission
- auto-submit

No current Gate authorizes browser navigation, DOM mutation, click, typing,
fill, submit, Queue completion, or Claim completion.

## Runtime reader policy

The frozen policy is documented in
[Runtime Reader Policy](docs/RUNTIME_READER_POLICY.md):

- Codex Vision: development, annotation, and debugging only; production
  runtime calls are **0**.
- PP-OCRv6: local OCR, bounding-box, and evidence helper.
- Gemma 4 26B: planned primary raw-handwriting reader candidate; not promoted
  to production value authority by this branch.
- Qwen: current machine source and future difficult-case fallback candidate;
  it should not remain an unconditional full-page call for every image.
- Human Confirmed Answer: the only Candidate value authority.

## Safety boundary

- `auto_confirm=false`
- `approved_for_fill=false`
- `approved_for_submit=false`
- `auto_submit=false`
- machine suggestions cannot directly create Candidate, Queue, or Webfill work
- cancelled bets remain audit-only and non-executable
- ambiguous mappings fail closed
- real browser/site operation is not present

See [Architecture](docs/ARCHITECTURE.md) and
[Safety Invariants](docs/SAFETY_INVARIANTS.md).

## Development setup

Python 3.11 or newer is required.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -e ".[dev]"
```

Playwright is a development-only dependency. CI installs Chromium solely for
local synthetic UI tests; those tests do not connect to a real website.

Run the deterministic core tests:

```powershell
python -m pytest tests/test_sample034_audit.py tests/test_pipeline.py tests/test_column_geometry.py tests/test_deterministic_checks.py tests/test_semantic_parser.py tests/test_vision_closed_set.py tests/test_ab_replay_regression.py -q
```

Run the authority and read-only Gate tests using the exact command in
[`.github/workflows/ci.yml`](.github/workflows/ci.yml).

## Repository and data policy

The source repository must not contain API keys, customer images, OCR datasets,
LOCALAPPDATA caches, model weights, or generated benchmark artifacts. Local OCR
datasets and benchmark results belong outside this repository and require a
separate backup policy.

The GitHub repository is currently public. Review repository visibility before
release or before adding any non-public operational material.

## Documentation

- [Architecture](docs/ARCHITECTURE.md)
- [Runtime Reader Policy](docs/RUNTIME_READER_POLICY.md)
- [Safety Invariants](docs/SAFETY_INVARIANTS.md)
- [Draft PR Summary](docs/DRAFT_PR_SUMMARY.md)
- [CLI Reference](docs/cli_reference.md)
