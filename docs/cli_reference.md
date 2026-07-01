# CLI Reference

All commands listed here are local review, mock, or export commands. They do not operate the real website.

## Demo Pack

```bash
python -m betguard.webfill.cli --demo-e2e --out-dir demo_out --pretty
```

Purpose: Generate a repeatable offline demo pack.

Input: `examples/line_paste_demo.txt`

Output:

- `demo_out/queue_state.json`
- `demo_out/review.html`
- `demo_out/audit.json`
- `demo_out/summary.txt`

Safety: local only. No real website operation.

## Review Console Summary

```bash
python -m betguard.webfill.cli --review-console --queue demo_out/queue_state.json --pretty
```

Purpose: Print a local review console summary from a queue state file.

Input: queue JSON.

Output: human-readable console summary.

Safety: local only. No real website operation.

## Review HTML Report

```bash
python -m betguard.webfill.cli --review-report-html --queue demo_out/queue_state.json --out demo_out/review.html --pretty
```

Purpose: Generate a static local HTML review report.

Input: queue JSON.

Output: HTML report.

Safety: local only. No real website operation.

## Accept Valid Candidates

```bash
python -m betguard.webfill.cli --batch-review-accept-valid --queue demo_out/queue_state.json --pretty
```

Purpose: After manual review, accept only valid candidates from a `NEEDS_REVIEW` batch.

Input: queue JSON.

Output: updated queue JSON.

Safety: does not operate the real website. Invalid fragments remain in audit.

## Reject Batch

```bash
python -m betguard.webfill.cli --batch-review-reject --queue demo_out/queue_state.json --pretty
```

Purpose: Reject a batch that needs review.

Input: queue JSON.

Output: updated rejected queue JSON.

Safety: local only. No real website operation.

## Mock Next

```bash
python -m betguard.webfill.cli --batch-mock-next --queue demo_out/queue_state.json --pretty
```

Purpose: Mark the current waiting item as human-confirmed, then process the next pending mock item. Each item stops at `WAITING_FOR_HUMAN_CONFIRM`.

Input: queue JSON.

Output: updated queue JSON.

Safety: mock only. No real website operation. No submit.

## Audit Export

```bash
python -m betguard.webfill.cli --batch-audit-export --queue demo_out/queue_state.json --out demo_out/audit.json --pretty
```

Purpose: Export audit JSON from a queue state file.

Input: queue JSON.

Output: audit JSON.

Safety: local only. No real website operation.

## Safety Summary

Every command above keeps:

- `real_site_operation=false`
- `auto_submit=false`
- `danger_buttons_clicked=[]`
- No submit
- No click on danger buttons
- No real website operation
