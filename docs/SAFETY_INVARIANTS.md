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

## Amount field position verification (v1)

- The site renders exactly three visible amount inputs
  (`input[data-bind*="PengBet.Value"]`) sharing one row (`top` within
  `AMOUNT_ROW_TOP_TOLERANCE`). No id/name distinguishes them — only ascending
  `left` position does. Sorted left-to-right they are 二星 / 三星 / 四星, in
  that fixed order.
- Mapping only accepts this shortcut when there are **exactly three** eligible
  candidates in one row. Any other count (missing, duplicated, wrong row)
  falls back to the old ranking, which stays BLOCKED on ambiguity as above.
- `#GroupSet_Value` is a 分組序號 (grouping/sequence number) field, not an
  amount field. It is explicitly excluded from the eligible-candidate pool —
  it must never be selected as an amount target even if it happens to look
  unique.
- Hidden `#ta_*` / `#tb_*` fields and quick-bet-preset inputs are excluded from
  the eligible-candidate pool for the same reason: they are not the visible
  per-star amount inputs a human would actually see and fill.
- This position-based rule has been validated against real captured data for
  both 539 and 天天樂 (Tiantianle) — both share the same underlying page
  template, so the same three-input, same-row, left-to-right logic resolves
  SAFE on both games without any game-specific code.

## Stale `current_game` / `game_id` after in-page game switch

- The site's top-level `$Global` config (reflected as
  `global_config.game_id` / `market_state.current_game_id` /
  `current_game_name`) is set once at initial page load and is **not**
  refreshed when a user switches games via the in-page AJAX menu. A selector
  report captured after switching from 539 to 天天樂 can still show these
  fields as `"539"` even though the rendered page is genuinely 天天樂.
- Do not trust these fields alone to identify which game a capture belongs
  to. The reliable signal is the **rendered page text**, e.g.
  `diagnostics.live_frames[].sample_text` for `mainFrame` (look for literal
  game-name text such as "天天樂 - 下注資訊" and game-specific limit numbers
  that differ from 539's).
- This means a `map-dry-run --concise` report's `Market: current_game: 539`
  line can be stale and must not be read as proof the dry-run ran against 539
  data — check which selector report / site profile was actually fed in.

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
