# betguard-assistant

betguard-assistant is a local betting text review and mock assisted-fill tool for 539 / multi-game workflows.

It is not an auto-betting bot. It does not operate the real website, does not submit orders, and does not click send / confirm / clear / delete buttons.

## What It Does

- Cleans LINE / chat paste input.
- Splits repeated-dot pasted batches into candidate bet fragments.
- Parses supported normal, column, tail, and car formats.
- Marks unclear or risky fragments as `NEEDS_REVIEW` / invalid.
- Preserves original text and invalid fragments for audit.
- Lets a user accept valid candidates or reject the batch.
- Builds a one-item-at-a-time mock queue.
- Stops every item at `WAITING_FOR_HUMAN_CONFIRM`.
- Exports audit JSON.
- Generates a local HTML review report.

## Core Flow

```text
LINE paste
-> preprocessing
-> parser
-> review
-> accept valid / reject
-> queue
-> mock next
-> audit export
-> HTML review report
```

## Quick Start

Install in editable mode:

```bash
pip install -e ".[dev]"
```

Run tests:

```bash
py -m pytest
```

Generate the offline demo pack:

```bash
python -m betguard.webfill.cli --demo-e2e --out-dir demo_out --pretty
```

## View Demo Output

The demo creates:

- `demo_out/queue_state.json`
- `demo_out/review.html`
- `demo_out/audit.json`
- `demo_out/summary.txt`

Open `demo_out/review.html` locally to review valid candidates, invalid fragments, queue status, safety flags, and audit summary.

## Next Steps After Demo

Accept valid candidates after manually reviewing invalid fragments:

```bash
python -m betguard.webfill.cli --batch-review-accept-valid --queue demo_out/queue_state.json --pretty
```

Run the next mock item only:

```bash
python -m betguard.webfill.cli --batch-mock-next --queue demo_out/queue_state.json --pretty
```

Refresh the local HTML report:

```bash
python -m betguard.webfill.cli --review-report-html --queue demo_out/queue_state.json --out demo_out/review_after_next.html --pretty
```

Export updated audit JSON:

```bash
python -m betguard.webfill.cli --batch-audit-export --queue demo_out/queue_state.json --out demo_out/audit_after_next.json --pretty
```

## Streamlit Review UI

The Streamlit UI is a local review interface for paste, review, and report workflows:

```bash
streamlit run src/betguard/ui_review.py
```

It is still a safety review UI. It does not submit bets.

## Safety Guarantees

- `real_site_operation=false`
- `auto_submit=false`
- `danger_buttons_clicked=[]`
- No submit
- No click on send / confirm / clear / delete / danger buttons
- Every item requires human confirmation
- Invalid fragments do not disappear
- Valid items do not disappear
- The demo does not auto accept valid candidates
- The demo does not auto-run the next item

## Documentation

- [End-to-End Demo](docs/demo_e2e.md)
- [Architecture](docs/architecture.md)
- [CLI Reference](docs/cli_reference.md)
