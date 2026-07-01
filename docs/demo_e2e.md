# End-to-End Demo Pack

This demo shows the offline Betguard review flow:

LINE paste input -> preprocessing -> NEEDS_REVIEW -> accept valid -> queue -> mock next -> WAITING_FOR_HUMAN_CONFIRM -> review HTML -> audit JSON export.

It does not operate any real website. It does not submit. It does not click send, confirm, clear, delete, or any danger button.

## Generate Demo Files

```bash
python -m betguard.webfill.cli --demo-e2e --out-dir demo_out --pretty
```

Generated files:

- `demo_out/queue_state.json`
- `demo_out/review.html`
- `demo_out/audit.json`
- `demo_out/summary.txt`

Open `demo_out/review.html` in a browser to inspect valid candidates, invalid fragments, ignored metadata, queue state, audit summary, and safety flags.

## Accept Valid Candidates

The initial demo intentionally contains review/invalid fragments, so it starts at `NEEDS_REVIEW`.

```bash
python -m betguard.webfill.cli --batch-review-accept-valid --queue demo_out/queue_state.json --pretty
```

This keeps only valid candidates in the active queue and preserves invalid fragments in audit.

## Mock Next

```bash
python -m betguard.webfill.cli --batch-mock-next --queue demo_out/queue_state.json --pretty
```

Each mock fill stops at `WAITING_FOR_HUMAN_CONFIRM`. The system does not auto-run the next item.

## Refresh Review HTML

```bash
python -m betguard.webfill.cli --review-report-html --queue demo_out/queue_state.json --out demo_out/review_after_next.html --pretty
```

## Export Audit

```bash
python -m betguard.webfill.cli --batch-audit-export --queue demo_out/queue_state.json --out demo_out/audit_after_next.json --pretty
```

## Safety

- `real_site_operation=false`
- `auto_submit=false`
- `danger_buttons_clicked=[]`
- No real website operation
- No submit
- No danger button click
