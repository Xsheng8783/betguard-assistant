# Architecture

betguard-assistant is organized around a local, auditable review pipeline. The current release package is for local review, mock assisted fill, demo, and audit export. There is no live site operation in this package.

## Modules

### `input_preprocessor.py`

Cleans LINE / chat paste text, strips obvious metadata, splits repeated-dot groups, preserves `original_text`, and produces candidate fragments with preprocessing notes.

### `parser.py`

Parses supported bet fragments into structured results. It handles normal, column, tail-expanded column, car, per-star amounts, and game-specific number ranges. Unclear or unsupported text remains invalid or warning instead of being guessed.

### `formatter.py`

Builds human-readable summaries for parsed bets and review results. These summaries are used by CLI output, queue reports, audit, and local HTML review pages.

### `batch_mock_queue.py`

Builds the batch queue from preprocessing and review results. It enforces the review states and the one-item-at-a-time mock flow.

Important statuses:

- `READY_FOR_QUEUE`
- `NEEDS_REVIEW`
- `WAITING_FOR_HUMAN_CONFIRM`
- `MOCK_FILL_FAILED`
- `REJECTED`
- `COMPLETED`
- `BATCH_BLOCKED`

### `assisted_fill_mock.py`

Runs mock-only assisted fill for supported normal, column, and car bets. It records selected numbers, selected columns, car number, and filled amounts. It never clicks danger buttons.

### `batch_audit.py`

Creates and updates JSON-serializable audit data. Audit preserves original text, candidate fragments, invalid reasons, queue item state, mock result summaries, and safety flags.

### `review_console.py`

Builds a local review console model and static HTML report from queue state. The report displays preprocessing, valid candidates, invalid fragments, queue view, actions, audit summary, and safety flags. It does not execute actions.

### `demo_e2e.py`

Generates the offline end-to-end demo pack: queue state, review HTML, audit JSON, and summary text.

### `cli.py`

Provides command-line entry points for demo generation, review HTML, accept/reject, mock next, audit export, and read-only selector inspection tools.

## Data Flow

```text
original_text
-> input_preprocessor candidate fragments
-> parser parsed result
-> review result
-> queue item
-> mock result
-> audit export
-> local review report
```

The original text and every original fragment are preserved for audit. Invalid fragments are not removed when valid candidates are accepted.

## State Flow

```text
READY
-> READY_FOR_QUEUE
-> WAITING_FOR_HUMAN_CONFIRM
-> COMPLETED
```

If the batch contains invalid or warning fragments:

```text
NEEDS_REVIEW
-> accept valid -> READY_FOR_QUEUE / WAITING_FOR_HUMAN_CONFIRM
-> reject -> REJECTED
```

If no valid candidate exists:

```text
BATCH_BLOCKED
```

## Safety Boundary

- No live site operation.
- No submit.
- No click on send / confirm / clear / delete / danger buttons.
- Mock fill only operates local mock data.
- Read-only snapshot tools may inspect DOM/text/attributes only.
- `real_site_operation=false`
- `auto_submit=false`
- `danger_buttons_clicked=[]`

Readonly snapshot code must remain read-only. It must not fill, click, press, submit, or trigger any real website action.
