# Daily Workflow

This workflow is for using your own LINE / chat pasted text locally.

It does not operate the real website, does not submit, does not click, and requires human confirmation for every item.

## 1. Create `input.txt`

Paste the LINE text into `input.txt`.

## 2. Create Queue

```bash
python -m betguard.webfill.cli --new-batch-from-file input.txt --queue queue_state.json --pretty
```

If `queue_state.json` already exists, the command stops. Use `--overwrite` only when you intentionally want to replace it:

```bash
python -m betguard.webfill.cli --new-batch-from-file input.txt --queue queue_state.json --overwrite --pretty
```

## 3. Generate Review Package

```bash
python -m betguard.webfill.cli --review-package --queue queue_state.json --out-dir review_out --pretty
```

Generated files:

- `review_out/review.html`
- `review_out/audit.json`
- `review_out/summary.txt`

## 4. Open Review HTML

Open this file locally:

```text
review_out/review.html
```

Review valid candidates and invalid / warning fragments.

## 5. Accept Valid Or Reject

If the queue is `NEEDS_REVIEW` and you decide to accept only valid candidates:

```bash
python -m betguard.webfill.cli --batch-review-accept-valid --queue queue_state.json --pretty
```

If you reject the batch:

```bash
python -m betguard.webfill.cli --batch-review-reject --queue queue_state.json --pretty
```

## 6. Mock Next

```bash
python -m betguard.webfill.cli --batch-mock-next --queue queue_state.json --pretty
```

Each item stops at `WAITING_FOR_HUMAN_CONFIRM`. The tool does not auto-run the next item.

## 7. Refresh Review Package

```bash
python -m betguard.webfill.cli --review-package --queue queue_state.json --out-dir review_out --pretty
```

## 8. Export Final Audit

```bash
python -m betguard.webfill.cli --batch-audit-export --queue queue_state.json --out review_out/audit_final.json --pretty
```

## Safety

- `real_site_operation=false`
- `auto_submit=false`
- `danger_buttons_clicked=[]`
- No real website operation
- No submit
- No click
- No send / confirm / clear / delete button operation
- Every item requires human confirmation
