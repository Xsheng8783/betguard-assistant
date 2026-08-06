import { createRequire } from "module";
import assert from "node:assert/strict";

const require = createRequire(import.meta.url);
const R = require("./roi_logic.js");

function r02Line() {
  return {
    line_id: "R02-L1",
    raw_text: "02 . 30 . 33 . 39 3x1",
    model_raw_text: "02 . 30 . 33 . 39 3x1",
    number_groups: [["02", "30", "33", "39"]],
    layout_hint: "normal_row",
    multiplier_text: "3x1",
    uncertain: true,
    uncertain_reason: "play_mark_unclear",
    review_action: "pending",
    warnings: ["play_mark_roi_unclear"],
    play_mark: {
      first_pass_categories: ["3"],
      roi_categories: ["3", "4"],
      roi_upper_digits: ["3"],
      roi_lower_digits: ["4"],
      roi_multiplier: "1",
      roi_uncertain: true,
      crop_path: "sample-009-R02-L1.png",
    },
  };
}

let passed = 0;
function ok(name) {
  passed++;
  console.log("PASS", name);
}

// ---- A. R02 type: uncertain=true, button data must be usable ----
{
  const line = r02Line();
  const can = R.canApplyRoi(line.play_mark);
  assert.equal(can.ok, true, "A: canApplyRoi must be true even when uncertain");
  assert.deepEqual(can.digits, ["3", "4"], "A: positional digits upper->lower");
  assert.equal(can.playText, "三四", "A: play text 三四, NOT 34");
  assert.equal(can.fullText, "三四X1", "A: full text 三四X1");
  assert.equal(can.missing.length, 0, "A: no missing fields");
  ok("A1 採用按鈕資料可用（uncertain=true 仍可採用）");

  const res = R.applyRoiToLine(line, line.play_mark);
  assert.equal(res.ok, true, "A: apply ok");
  assert.equal(res.idempotent, false);
  assert.equal(line.multiplier_text, "三四X1");
  assert.equal(line.human_raw_text, "02 30 33 39 三四X1");
  assert.equal(line.raw_text, "02 30 33 39 三四X1", "A: display raw updated");
  assert.equal(line.model_raw_text, "02 30 33 39 三四X1", "A: display model_raw updated");
  assert.equal(line.correction_source, "roi");
  assert.equal(line.human_edited, true);
  assert.equal(line.uncertain, true, "A: uncertain history preserved");
  assert.equal(line.review_action, "corrected", "A: edited but NOT confirmed");
  assert.equal(line.play_mark.first_pass_categories.join(""), "3", "A: divergence evidence kept");
  assert.equal(line.play_mark.roi_categories.join(""), "34", "A: ROI evidence kept");
  assert.equal(line.play_mark.first_pass_raw_text, "02 . 30 . 33 . 39 3x1", "A: first-pass raw preserved");
  assert.equal(line.play_mark.applied_roi.full_text, "三四X1");
  assert.equal(line.warnings[0], "play_mark_roi_unclear", "A: warnings untouched");
  ok("A2 點擊後產生 三四X1、維持 needs_review 證據、未確認");
}

// ---- B. uncertain=false also adoptable ----
{
  const line = r02Line();
  line.play_mark.roi_uncertain = false;
  const can = R.canApplyRoi(line.play_mark);
  assert.equal(can.ok, true, "B: can apply when certain");
  const res = R.applyRoiToLine(line, line.play_mark);
  assert.equal(res.ok, true);
  assert.equal(line.multiplier_text, "三四X1");
  assert.equal(line.uncertain, true, "B: original uncertain flag untouched");
  ok("B uncertain=false 可正常採用");
}

// ---- C. missing multiplier -> cannot apply, no mutation ----
{
  const line = r02Line();
  delete line.play_mark.roi_multiplier;
  const can = R.canApplyRoi(line.play_mark);
  assert.equal(can.ok, false);
  assert.ok(can.missing.includes("倍率"), "C: missing multiplier reported");
  const before = JSON.stringify(line);
  const res = R.applyRoiToLine(line, line.play_mark);
  assert.equal(res.ok, false);
  assert.equal(JSON.stringify(line), before, "C: line unchanged");
  ok("C 缺少倍率→不可套用、資料未變");
}

// ---- D. divergence evidence preserved after adoption ----
{
  const line = r02Line();
  line.play_mark.first_pass_categories = ["3"];
  line.play_mark.roi_categories = ["3", "4"];
  line.warnings = ["play_mark_divergent"];
  R.applyRoiToLine(line, line.play_mark);
  assert.deepEqual(line.play_mark.first_pass_categories, ["3"], "D: first-pass kept");
  assert.deepEqual(line.play_mark.roi_categories, ["3", "4"], "D: roi kept");
  assert.deepEqual(line.warnings, ["play_mark_divergent"], "D: divergence warning kept");
  ok("D 採用後保留 play_mark_divergent 差異紀錄");
}

// ---- E. idempotency ----
{
  const line = r02Line();
  const edits = [];
  const first = R.applyRoiToLine(line, line.play_mark);
  if (first.edit) edits.push(first.edit);
  const second = R.applyRoiToLine(line, line.play_mark);
  assert.equal(second.idempotent, true, "E: second click idempotent");
  assert.equal(second.edit, null, "E: no duplicate edit record");
  assert.equal(edits.length, 1, "E: only one edit record");
  assert.equal(line.multiplier_text, "三四X1");
  ok("E 重複按兩次不建立重複紀錄");
}

// ---- extra: positional order (upper 4 / lower 3 -> 四三, not sorted 三四) ----
{
  const line = r02Line();
  line.play_mark.roi_upper_digits = ["4"];
  line.play_mark.roi_lower_digits = ["3"];
  line.play_mark.roi_categories = ["4", "3"];
  const can = R.canApplyRoi(line.play_mark);
  assert.equal(can.playText, "四三", "positional order upper->lower");
  ok("額外 位置順序 upper→lower（四三不是三四）");
}

// ---- extra: column bet human raw ----
{
  const line = r02Line();
  line.layout_hint = "column_bet";
  line.number_groups = [["03"], ["11"], ["18", "28"]];
  R.applyRoiToLine(line, line.play_mark);
  assert.equal(line.human_raw_text, "03 / 11 / 18 28 三四X1");
  ok("額外 柱碰 human_raw_text 用 / 分欄");
}

console.log(`\n${passed} tests passed`);
