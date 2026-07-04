# Safety Invariants

These rules are non-negotiable. Any change that would violate one of them is out
of scope for this package and must not be merged. They exist so that a parsing or
mapping bug can never turn into a real bet on a live site.

## No automatic actions

- **No auto submit.** Nothing submits a bet automatically.
- **No auto confirm.** No confirmation dialog is accepted automatically.
- **No auto complete.** Items do not mark themselves complete.
- **No auto accept-valid.** Approval is always an explicit human step.
- **No automatic next item.** The queue never advances to the next item on its
  own; a human moves it forward one item at a time.

## No real-site operation

- **No real-site click / fill / submit** unless a real assisted-fill path is
  explicitly implemented later behind its own gate. This package has none.
- **Danger buttons are never clicked automatically** — send (送出注單), add
  (加入注單), confirm (確認/確定), clear (清除), delete (刪除), or close.
- Read-only snapshot tools inspect DOM / text / attributes only. They must not
  fill, click, press, submit, or trigger any real website action.

## Review states never leak into fill flows

- **Needs Review / Invalid / Watchlist never enter the fill flow.** They stay in
  review and are never placed into `approved_fill_queue`.
- **Only human-confirmed `approved_fill_queue` items** may enter `fill-preview`,
  `mock-fill`, `map-dry-run`, or any future assisted fill.
- Valid candidates alone are not approved actions; accept-valid is required first.

## Audit must be complete

Every audited item must preserve:

- the **raw source** text (and original fragments),
- the **parsed result**,
- the **human confirmation** (accept / reject),
- the **approved snapshot** that entered the queue,
- the **action source** (which approved item an action came from).

Invalid fragments are not deleted when valid candidates are accepted.

## Selector safety

- **Ambiguous selectors stay BLOCKED.** If a selector cannot be proven unique to
  its target, the mapping result is BLOCKED, not SAFE.
- **Shared selectors are not forced SAFE.** A selector shared across fields — for
  example `#GroupSet_Value` shared by 二星 and 三星 — must remain BLOCKED. BLOCKED
  is a correct, expected safety outcome, not a failure to fix.
- Selector safety guards must not be weakened to make a report look "greener".

## Numbers-only assisted plan (local only, v1)

- `fill_plan.to_numbers_only_plan()` is a pure transform: `set_amount` steps are
  removed from `planned_steps` and moved into `amount_steps_removed`. They can
  never re-enter `planned_steps`.
- `amount_manual_required=true` means the human types every amount by hand.
  This flag is a plan-level fact, not a computed guess — `build_mapping_report`
  only shows amounts as "skipped by design" when the flag is set **and** no
  amount action was actually mapped. If a plan is (incorrectly) marked
  numbers-only while still carrying a real `set_amount` step, that step is
  still mapped and still subject to every existing selector guard — the flag
  can never hide or force-pass a real ambiguous amount selector.
- This v1 covers local plan/report/dry-run only. No real-site execution path
  reads or acts on `amount_manual_required` yet.

## Fixed safety flags

The following must always hold in reports and mock output:

- `real_site_operation=false`
- `auto_submit=false`
- `danger_buttons_clicked=[]`

See `ARCHITECTURE.md` for the gated pipeline and `DIAGNOSTICS_GUIDE.md` for how to
read SAFE / BLOCKED results.
