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
  assert.equal(line.model_raw_text, "02 . 30 . 33 . 39 3x1", "A: model_raw_text IMMUTABLE");
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

// ---- multiplier candidates display (display-only, no mutation) ----
{
  const line = {
    line_id: "R02-L1",
    number_groups: [["15"], ["24"], ["22", "35", "28"]],
    layout_hint: "column_bet",
    multiplier_text: "2X1",
    review_action: "pending",
    fallback_candidate: { multiplier_candidates: ["3X1"] },
  };
  const before = JSON.stringify(line);
  const info = R.multiplierCandidatesInfo(line);
  assert.equal(info.hasCandidates, true);
  assert.deepEqual(info.candidates, ["3X1"]);
  assert.equal(info.merged, "2/3X1");
  assert.deepEqual(info.displayCandidates, ["3X1", "2/3X1"], "R02: all candidates listed, merged included");
  assert.equal(info.hint, "可能為 2/3X1，請依圖片確認");
  const html = R.multiplierCandidatesHtml(line);
  assert.ok(html.includes("倍率候選（需人工確認）"), "R02: block title shown");
  assert.ok(html.includes("3X1"), "R02: candidate shown");
  assert.ok(html.includes("2/3X1"), "R02: merged candidate shown");
  assert.ok(html.includes("採用此候選"), "R02: adopt buttons shown");
  assert.equal(JSON.stringify(line), before, "R02: display must not mutate line");
  assert.equal(line.review_action, "pending", "R02: review_action unchanged by display");
  ok("候選 R02 顯示 3X1＋合併 2/3X1、顯示不改資料");
}

{
  const line = {
    line_id: "R02-L1",
    number_groups: [["15"], ["24"], ["22", "35", "28"]],
    layout_hint: "column_bet",
    multiplier_text: "2X1",
    review_action: "pending",
    fallback_candidate: { multiplier_candidates: ["3X1"] },
  };
  const res = R.adoptMultiplierCandidate(line, "2/3X1");
  assert.equal(res.ok, true);
  assert.equal(line.multiplier_text, "2/3X1", "R02 adopt: merged form, NOT 2X1 3X1");
  assert.deepEqual(line.multiplier_rules, [{ rule_text: "2/3X1", categories: ["2", "3"], value: "1" }]);
  assert.equal(line.review_action, "corrected", "R02 adopt: corrected, NOT confirmed");
  assert.equal(line.uncertain, true, "R02 adopt: uncertain stays true");
  assert.equal(line.human_raw_text, "15 / 24 / 22 35 28 2/3X1");
  assert.deepEqual(line.fallback_candidate.multiplier_candidates, ["3X1"], "R02 adopt: original evidence kept");
  assert.equal(line.fallback_candidate.adopted_multiplier, "2/3X1");
  assert.ok(R.multiplierCandidatesHtml(line).includes("已採用"), "R02 adopt: adopted marker shown");
  ok("候選 R02 採用 2/3X1（不寫 2X1 3X1）、corrected、保留證據");
}

{
  const line = {
    line_id: "R05-L1",
    number_groups: [["02"], ["17"], ["20"], ["33"]],
    layout_hint: "normal_row",
    multiplier_text: "3X1",
    review_action: "pending",
    fallback_candidate: { multiplier_candidates: ["3/4X1"] },
  };
  const before = JSON.stringify(line);
  const info = R.multiplierCandidatesInfo(line);
  assert.deepEqual(info.displayCandidates, ["3/4X1"]);
  assert.equal(info.hint, "可能為 3/4X1，請依圖片確認");
  assert.ok(R.multiplierCandidatesHtml(line).includes("3/4X1"));
  assert.equal(JSON.stringify(line), before, "R05: display must not mutate line");
  assert.equal(line.review_action, "pending", "R05: review_action unchanged by display");

  const res = R.adoptMultiplierCandidate(line, "3/4X1");
  assert.equal(res.ok, true);
  assert.equal(line.multiplier_text, "3/4X1");
  assert.deepEqual(line.multiplier_rules, [{ rule_text: "3/4X1", categories: ["3", "4"], value: "1" }]);
  assert.equal(line.review_action, "corrected");
  assert.equal(line.uncertain, true);
  assert.equal(line.human_raw_text, "02 17 20 33 3/4X1");
  assert.deepEqual(line.fallback_candidate.multiplier_candidates, ["3/4X1"], "R05 adopt: original evidence kept");
  assert.equal(line.fallback_candidate.adopted_multiplier, "3/4X1");
  ok("候選 R05 顯示並採用 3/4X1、corrected、保留證據");
}

{
  const noFb = { line_id: "R03-L1", multiplier_text: "2X1", review_action: "pending" };
  assert.equal(R.multiplierCandidatesInfo(noFb).hasCandidates, false);
  assert.equal(R.multiplierCandidatesHtml(noFb), "");
  const emptyFb = { line_id: "R03-L1", multiplier_text: "2X1", fallback_candidate: { multiplier_candidates: [] } };
  assert.equal(R.multiplierCandidatesInfo(emptyFb).hasCandidates, false);
  assert.equal(R.multiplierCandidatesHtml(emptyFb), "");
  ok("候選 無 fallback_candidate／空陣列→不顯示區塊");
}

{
  const line = {
    line_id: "R02-L1",
    number_groups: [["15"], ["24"], ["22", "35", "28"]],
    layout_hint: "column_bet",
    multiplier_text: "2X1",
    review_action: "pending",
    fallback_candidate: { multiplier_candidates: ["3X1", "3x1", "3 × 1", "3X1"] },
  };
  const before = JSON.stringify(line);
  const info = R.multiplierCandidatesInfo(line);
  assert.deepEqual(info.candidates, ["3X1"], "normalize + dedupe for display");
  assert.deepEqual(line.fallback_candidate.multiplier_candidates, ["3X1", "3x1", "3 × 1", "3X1"], "raw evidence untouched");
  R.multiplierCandidatesHtml(line);
  assert.equal(JSON.stringify(line), before);
  ok("候選 正規化去重顯示、原始證據不刪");
}

{
  const line = {
    line_id: "R02-L1",
    number_groups: [["15"], ["24"], ["22", "35", "28"]],
    layout_hint: "column_bet",
    multiplier_text: "2X1",
    review_action: "pending",
    fallback_candidate: { multiplier_candidates: ["3X1"] },
  };
  const before = JSON.stringify(line);
  const res = R.adoptMultiplierCandidate(line, "4X1"); // not offered
  assert.equal(res.ok, false);
  assert.equal(JSON.stringify(line), before, "invalid adoption must not mutate");
  ok("候選 非候選值不可採用、資料不變");
}

console.log(`\n${passed} tests passed`);
