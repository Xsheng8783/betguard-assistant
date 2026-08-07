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
    uncertain: true,
    fallback_candidate: { multiplier_candidates: ["3X1"] },
  };
  const before = JSON.stringify(line);
  const info = R.multiplierCandidatesInfo(line);
  assert.equal(info.hasCandidates, true);
  assert.deepEqual(info.candidates, ["3X1"]);
  assert.equal(info.entries[0].mode, "unknown_requires_review", "legacy same-value candidate must not auto-exclude");
  assert.equal(info.merged, null, "no slot/composable evidence -> no derived merge");
  assert.deepEqual(info.displayCandidates, ["3X1"]);
  assert.equal(info.hint, null);
  const html = R.multiplierCandidatesHtml(line);
  assert.ok(html.includes("倍率候選（需人工確認）"), "R02: block title shown");
  assert.ok(html.includes("3X1"), "R02: candidate shown");
  assert.ok(html.includes("採用此候選"), "R02: adopt buttons shown");
  assert.equal(JSON.stringify(line), before, "R02: display must not mutate line");
  assert.equal(line.review_action, "pending", "R02: review_action unchanged by display");
  ok("候選 legacy 同 value 標 unknown_requires_review、不自動 merge、顯示不改資料");
}

{
  const line = {
    line_id: "R02-L1",
    number_groups: [["15"], ["24"], ["22", "35", "28"]],
    layout_hint: "column_bet",
    multiplier_text: "2X1",
    review_action: "pending",
    uncertain: true,
    fallback_candidate: { multiplier_candidates: ["3X1"] },
  };
  const res = R.adoptMultiplierCandidate(line, "3X1");
  assert.equal(res.ok, true);
  assert.equal(line.multiplier_text, "2X1 3X1", "legacy adopt must NOT destroy existing rule");
  assert.deepEqual(line.multiplier_rules.map((r) => r.rule_text), ["2X1", "3X1"]);
  assert.equal(line.review_action, "corrected", "R02 adopt: corrected, NOT confirmed");
  assert.equal(line.uncertain, true, "R02 adopt: uncertain stays true");
  assert.equal(line.human_raw_text, "15 / 24 / 22 35 28 2X1 3X1");
  assert.deepEqual(line.fallback_candidate.multiplier_candidates, ["3X1"], "R02 adopt: original evidence kept");
  assert.equal(line.fallback_candidate.adopted_multiplier, "3X1");
  assert.ok(R.multiplierCandidatesHtml(line).includes("已採用"), "R02 adopt: adopted marker shown");
  ok("候選 legacy 採用為追加、不破壞既有規則、corrected、保留證據");
}

{
  const line = {
    line_id: "R05-L1",
    number_groups: [["02"], ["17"], ["20"], ["33"]],
    layout_hint: "normal_row",
    multiplier_text: "3X1",
    review_action: "pending",
    uncertain: true,
    fallback_candidate: { multiplier_candidates: ["3/4X1"] },
  };
  const before = JSON.stringify(line);
  const info = R.multiplierCandidatesInfo(line);
  assert.deepEqual(info.displayCandidates, ["3/4X1"]);
  assert.equal(info.entries[0].mode, "unknown_requires_review");
  assert.equal(info.hint, null);
  assert.ok(R.multiplierCandidatesHtml(line).includes("3/4X1"));
  assert.equal(JSON.stringify(line), before, "R05: display must not mutate line");
  assert.equal(line.review_action, "pending", "R05: review_action unchanged by display");

  const res = R.adoptMultiplierCandidate(line, "3/4X1");
  assert.equal(res.ok, true);
  assert.equal(line.multiplier_text, "3X1 3/4X1", "legacy adopt is additive, never destroys 3X1");
  assert.deepEqual(line.multiplier_rules.map((r) => r.rule_text), ["3X1", "3/4X1"]);
  assert.equal(line.review_action, "corrected");
  assert.equal(line.uncertain, true);
  assert.equal(line.human_raw_text, "02 17 20 33 3X1 3/4X1");
  assert.deepEqual(line.fallback_candidate.multiplier_candidates, ["3/4X1"], "R05 adopt: original evidence kept");
  assert.equal(line.fallback_candidate.adopted_multiplier, "3/4X1");
  ok("候選 R05 legacy 顯示並追加 3/4X1、corrected、保留證據");
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

// ---- standardized result display (number_groups -> multiplier_text last) ----
{
  const line = {
    line_id: "R02-L1",
    layout_hint: "column_bet",
    number_groups: [["15"], ["24"], ["22", "35", "28"]],
    multiplier_text: "2X1",
    model_raw_text: "15 . 24 x 22 2 x 1 35 28",
    raw_text: "15 / 24 / 22 35 28 2X1",
  };
  const before = JSON.stringify(line);
  assert.equal(R.standardizedResultText(line), "15 / 24 / 22 35 28 2X1", "R02: multiplier last");
  assert.equal(line.model_raw_text, "15 . 24 x 22 2 x 1 35 28", "R02: model_raw_text unchanged");
  assert.equal(JSON.stringify(line), before, "R02: standardization is display-only");
  ok("標準化 R02 柱碰：number_groups → 2X1 最後、model_raw_text 不變");
}

{
  const line = {
    line_id: "R05-L1",
    layout_hint: "normal_row",
    number_groups: [["02"], ["17"], ["20"], ["33"]],
    multiplier_text: "3X1",
    model_raw_text: "02 . 17 . 20 . 33 3 x 1",
  };
  assert.equal(R.standardizedResultText(line), "02 17 20 33 3X1", "normal row: multiplier last");
  assert.equal(line.model_raw_text, "02 . 17 . 20 . 33 3 x 1");
  ok("標準化 一般行：號碼在前、倍率最後");
}

{
  const line = {
    line_id: "R03-L1",
    layout_hint: "column_bet",
    number_groups: [["35"], ["24", "34"], ["18", "28"]],
    multiplier_text: "",
    model_raw_text: "35 x 24 x 18 2 x 1 34 28",
  };
  assert.equal(R.standardizedResultText(line), "35 / 24 34 / 18 28", "no trailing space when no multiplier");
  assert.equal(line.model_raw_text, "35 x 24 x 18 2 x 1 34 28");
  ok("標準化 柱碰無倍率：不補空白、model_raw_text 不變");
}

{
  // Scrambled model_raw_text must NOT affect the standardized result.
  const line = {
    line_id: "R02-L1",
    layout_hint: "column_bet",
    number_groups: [["15"], ["24"], ["22", "35", "28"]],
    multiplier_text: "2X1",
    model_raw_text: "22 2 x 1 15 . 24 x 35 28",
  };
  assert.equal(R.standardizedResultText(line), "15 / 24 / 22 35 28 2X1");
  assert.equal(line.model_raw_text, "22 2 x 1 15 . 24 x 35 28");
  ok("標準化 不重新解析 model_raw_text（順序只來自 number_groups）");
}

{
  const line = { line_id: "R99-L1", raw_text: "只有原文", number_groups: [] };
  assert.equal(R.standardizedResultText(line), "只有原文");
  ok("標準化 無 number_groups 時退回 raw_text");
}

// ---- canonical multiplier display (spaced complete rules) ----
{
  assert.equal(R.canonicalMultiplierText("3/4 x 1"), "3/4X1");
  assert.equal(R.canonicalMultiplierText("2 x 1"), "2X1");
  assert.equal(R.canonicalMultiplierText("2 x 2 3 x 5"), "2X2 3X5");
  assert.equal(R.canonicalMultiplierText("4/3 x 1"), "3/4X1");
  const line = {
    line_id: "R03-L1",
    number_groups: [["32", "34", "35"]],
    multiplier_text: "3 x 5",
    layout_hint: "normal_row",
    model_raw_text: "32 . 34 . 35 2 x 2 3 x 5",
  };
  const before = JSON.stringify(line);
  assert.equal(R.standardizedResultText(line), "32 34 35 3X5");
  assert.equal(JSON.stringify(line), before, "canonical display must not mutate");
  ok("標準化 spaced complete 倍率顯示 3/4X1/2X1/2X2 3X5、不改資料");
}

// ---- additional_rule / alternative_reading adoption model ----
{
  assert.deepEqual(R.splitCompleteRules("2X23X5"), [], "corrupted concat is not a complete rule set");

  const line = {
    line_id: "R03-L1",
    number_groups: [["02", "30", "33"]],
    layout_hint: "normal_row",
    multiplier_text: "2X2",
    review_action: "pending",
    uncertain: false,
    fallback_candidate: {
      multiplier_candidates: [
        { rule_text: "2X2", candidate_mode: "additional_rule", candidate_group_id: "slot1" },
        { rule_text: "3X5", candidate_mode: "additional_rule", candidate_group_id: "slot2" },
      ],
    },
  };
  const res = R.adoptMultiplierCandidate(line, { rule_text: "3X5", candidate_mode: "additional_rule", candidate_group_id: "slot2" });
  assert.equal(res.ok, true);
  assert.equal(line.multiplier_text, "2X2 3X5", "additional_rule must NOT become 2X23X5");
  assert.deepEqual(line.fallback_candidate.adopted_multipliers, ["3X5"]);
  R.adoptMultiplierCandidate(line, { rule_text: "3X5", candidate_mode: "additional_rule", candidate_group_id: "slot2" });
  assert.equal(line.multiplier_text, "2X2 3X5", "repeat adopt idempotent");
  assert.equal(line.fallback_candidate.adopted_entries.length, 1, "no duplicate adoption record");
  R.removeAdoptedCandidate(line, "3X5");
  assert.equal(line.multiplier_text, "2X2", "removing adopted rule keeps pre-existing 2X2");
  ok("additional_rule 採用不串接、冪等、取消不刪原有規則");
}

{
  const line = {
    line_id: "R04-L1",
    number_groups: [["02", "05", "17"]],
    layout_hint: "normal_row",
    multiplier_text: "3X5",
    review_action: "pending",
    fallback_candidate: {
      multiplier_candidates: [{ rule_text: "2X2", candidate_mode: "additional_rule", candidate_group_id: "slot1" }],
    },
  };
  R.adoptMultiplierCandidate(line, { rule_text: "2X2", candidate_mode: "additional_rule", candidate_group_id: "slot1" });
  assert.equal(line.multiplier_text, "2X2 3X5", "deterministic canonical order (category then value)");
  ok("additional_rule 由 3X5 加入 2X2 → canonical 2X2 3X5");
}

{
  const line = {
    line_id: "R02-L1",
    number_groups: [["15"], ["24"], ["22", "35", "28"]],
    layout_hint: "column_bet",
    multiplier_text: "2X1",
    review_action: "pending",
    fallback_candidate: {
      multiplier_candidates: [
        { rule_text: "3X1", candidate_mode: "alternative_reading", candidate_group_id: "g1" },
        { rule_text: "4X1", candidate_mode: "alternative_reading", candidate_group_id: "g1" },
      ],
    },
  };
  R.adoptMultiplierCandidate(line, { rule_text: "3X1", candidate_mode: "alternative_reading", candidate_group_id: "g1" });
  assert.equal(line.multiplier_text, "2X1 3X1", "alternative keeps existing structured 2X1");
  R.adoptMultiplierCandidate(line, { rule_text: "4X1", candidate_mode: "alternative_reading", candidate_group_id: "g1" });
  assert.equal(line.multiplier_text, "2X1 4X1", "same group: 4X1 replaces only 3X1");
  ok("alternative_reading 同 group 只替換候選管理的同槽規則");
}

{
  // Same value but DIFFERENT physical slots stay separate (no auto merge).
  const line = {
    line_id: "R99-L1",
    number_groups: [["01", "02"]],
    layout_hint: "normal_row",
    multiplier_text: "2X1",
    review_action: "pending",
    fallback_candidate: {
      multiplier_candidates: [{ rule_text: "3X1", candidate_mode: "additional_rule", candidate_group_id: "slot2" }],
    },
  };
  R.adoptMultiplierCandidate(line, { rule_text: "3X1", candidate_mode: "additional_rule", candidate_group_id: "slot2" });
  assert.equal(line.multiplier_text, "2X1 3X1", "different slots, same value: NOT merged to 2/3X1");
  ok("不同 slot 同 value 不自動合併");
}

// ---- candidate group semantics regressions (follow-up deb3038) ----
{
  // A. existing 2X2 3X5 + group-C alternatives 3X1/4X1; adopt 4X1
  const line = {
    line_id: "R99-L1",
    number_groups: [["01", "02"]],
    layout_hint: "normal_row",
    multiplier_text: "2X2 3X5",
    review_action: "pending",
    fallback_candidate: {
      multiplier_candidates: [
        { rule_text: "3X1", candidate_mode: "alternative_reading", candidate_group_id: "C" },
        { rule_text: "4X1", candidate_mode: "alternative_reading", candidate_group_id: "C" },
      ],
    },
  };
  R.adoptMultiplierCandidate(line, { rule_text: "4X1", candidate_mode: "alternative_reading", candidate_group_id: "C" });
  assert.equal(line.multiplier_text, "2X2 3X5 4X1", "A: other groups + existing rules preserved");
  ok("A 採用 4X1 → 2X2 3X5 4X1");
}

{
  // B. same group: adopt 3X1 then 4X1 -> only 3X1 replaced
  const line = {
    line_id: "R99-L1",
    number_groups: [["01", "02"]],
    layout_hint: "normal_row",
    multiplier_text: "2X2 3X5",
    review_action: "pending",
    fallback_candidate: {
      multiplier_candidates: [
        { rule_text: "3X1", candidate_mode: "alternative_reading", candidate_group_id: "C" },
        { rule_text: "4X1", candidate_mode: "alternative_reading", candidate_group_id: "C" },
      ],
    },
  };
  R.adoptMultiplierCandidate(line, { rule_text: "3X1", candidate_mode: "alternative_reading", candidate_group_id: "C" });
  assert.equal(line.multiplier_text, "2X2 3X1 3X5", "B: no replace evidence -> additive, canonical order");
  R.adoptMultiplierCandidate(line, { rule_text: "4X1", candidate_mode: "alternative_reading", candidate_group_id: "C" });
  assert.equal(line.multiplier_text, "2X2 3X5 4X1", "B: 4X1 replaces only 3X1");
  ok("B 同 group 4X1 只替換 3X1");
}

{
  // C. different groups, same value -> never auto merge
  const line = {
    line_id: "R99-L1",
    number_groups: [["01", "02"]],
    layout_hint: "normal_row",
    multiplier_text: null,
    review_action: "pending",
    fallback_candidate: {
      multiplier_candidates: [
        { rule_text: "2X1", candidate_mode: "alternative_reading", candidate_group_id: "A" },
        { rule_text: "3X1", candidate_mode: "alternative_reading", candidate_group_id: "B" },
      ],
    },
  };
  const info = R.multiplierCandidatesInfo(line);
  assert.equal(info.merged, null, "C: different groups must not derive 2/3X1");
  R.adoptMultiplierCandidate(line, { rule_text: "2X1", candidate_mode: "alternative_reading", candidate_group_id: "A" });
  R.adoptMultiplierCandidate(line, { rule_text: "3X1", candidate_mode: "alternative_reading", candidate_group_id: "B" });
  assert.equal(line.multiplier_text, "2X1 3X1", "C: both adopted, never merged");
  ok("C 不同 group 同 value 不合併");
}

{
  // D. same group + composable evidence + same value -> derived 2/3X1 allowed
  const line = {
    line_id: "R99-L1",
    number_groups: [["01", "02"]],
    layout_hint: "normal_row",
    multiplier_text: null,
    review_action: "pending",
    fallback_candidate: {
      multiplier_candidates: [
        { rule_text: "2X1", candidate_mode: "alternative_reading", candidate_group_id: "A", composable: true, evidence: { composable: true } },
        { rule_text: "3X1", candidate_mode: "alternative_reading", candidate_group_id: "A", composable: true, evidence: { composable: true } },
      ],
    },
  };
  const info = R.multiplierCandidatesInfo(line);
  assert.equal(info.merged, "2/3X1", "D: same group + composable + same value");
  R.adoptMultiplierCandidate(line, "2/3X1");
  assert.equal(line.multiplier_text, "2/3X1", "D: adopting derived merged candidate");
  ok("D 同 group＋composable＋同 value 才允許 2/3X1");
}

{
  // E. legacy candidate without group: same value must not destroy other rules
  const line = {
    line_id: "R99-L1",
    number_groups: [["01", "02"]],
    layout_hint: "normal_row",
    multiplier_text: "2X1",
    review_action: "pending",
    fallback_candidate: { multiplier_candidates: ["3X1"] },
  };
  const info = R.multiplierCandidatesInfo(line);
  assert.equal(info.entries[0].mode, "unknown_requires_review");
  R.adoptMultiplierCandidate(line, "3X1");
  assert.equal(line.multiplier_text, "2X1 3X1", "E: legacy adopt is additive");
  ok("E legacy 無 group 同 value 不自動破壞既有規則");
}

// ---- baseline replacement (explicit replaces_current_rules) ----
{
  // R01 family: baseline 3X1, candidate 3/4X1 (same slot, explicit replace)
  const line = {
    line_id: "R01-L1",
    number_groups: [["05", "06", "10", "28"]],
    layout_hint: "normal_row",
    multiplier_text: "3X1",
    review_action: "pending",
    fallback_candidate: {
      multiplier_candidates: [
        { rule_text: "3/4X1", candidate_mode: "alternative_reading", candidate_group_id: "slot1", replaces_current_rules: ["3X1"] },
      ],
    },
  };
  R.adoptMultiplierCandidate(line, { rule_text: "3/4X1", candidate_mode: "alternative_reading", candidate_group_id: "slot1", replaces_current_rules: ["3X1"] });
  assert.equal(line.multiplier_text, "3/4X1", "R01: 3/4X1 replaces baseline 3X1, NOT appended");
  assert.equal(line.fallback_candidate.adopted_entries[0].removed_baseline.join(","), "3X1");
  R.removeAdoptedCandidate(line, "3/4X1");
  assert.equal(line.multiplier_text, "3X1", "R01: cancel restores baseline 3X1");
  ok("R01 family 採用替代 baseline、取消可逆恢復");
}

{
  // R11 family: baseline "2/3X1.0 2X3"; candidate 3X1 replaces 2/3X1.0 only
  const line = {
    line_id: "R11-L1",
    number_groups: [["12"], ["15"], ["34"], ["13", "20"]],
    layout_hint: "column_bet",
    multiplier_text: "2/3X1.0 2X3",
    review_action: "pending",
    fallback_candidate: {
      multiplier_candidates: [
        { rule_text: "2X3", candidate_mode: "additional_rule", candidate_group_id: "slotA" },
        { rule_text: "3X1", candidate_mode: "alternative_reading", candidate_group_id: "slotB", replaces_current_rules: ["2/3X1.0"] },
      ],
    },
  };
  R.adoptMultiplierCandidate(line, { rule_text: "3X1", candidate_mode: "alternative_reading", candidate_group_id: "slotB", replaces_current_rules: ["2/3X1.0"] });
  assert.equal(line.multiplier_text, "2X3 3X1", "R11: 2X3 kept, 2/3X1.0 replaced");
  ok("R11 family 3X1 只替換 2/3X1.0、保留 2X3");
}

{
  // R12 family: two independent additional slots, any order -> same canonical
  const mk = () => ({
    line_id: "R12-L1",
    number_groups: [["12"], ["15"], ["06", "16"]],
    layout_hint: "column_bet",
    multiplier_text: null,
    review_action: "pending",
    fallback_candidate: {
      multiplier_candidates: [
        { rule_text: "2X3", candidate_mode: "additional_rule", candidate_group_id: "slotA" },
        { rule_text: "3X1", candidate_mode: "additional_rule", candidate_group_id: "slotB" },
      ],
    },
  });
  const a = mk();
  R.adoptMultiplierCandidate(a, { rule_text: "2X3", candidate_mode: "additional_rule", candidate_group_id: "slotA" });
  R.adoptMultiplierCandidate(a, { rule_text: "3X1", candidate_mode: "additional_rule", candidate_group_id: "slotB" });
  const b = mk();
  R.adoptMultiplierCandidate(b, { rule_text: "3X1", candidate_mode: "additional_rule", candidate_group_id: "slotB" });
  R.adoptMultiplierCandidate(b, { rule_text: "2X3", candidate_mode: "additional_rule", candidate_group_id: "slotA" });
  assert.equal(a.multiplier_text, "2X3 3X1");
  assert.equal(b.multiplier_text, "2X3 3X1", "order-independent canonical result");
  R.removeAdoptedCandidate(a, "3X1");
  assert.equal(a.multiplier_text, "2X3", "cancel one slot keeps the other");
  ok("R12 family 兩筆 additional 可同時採用、順序無關、取消只移除該筆");
}

// ---- car bet canonical keeps full play text ----
{
  const line = {
    line_id: "R01-L1",
    play_type: "car_bet",
    play_text: "15 34 各半車",
    number_groups: [["15", "34"]],
    multiplier_text: null,
    layout_hint: "normal_row",
    raw_text: "15 各半車",
  };
  const before = JSON.stringify(line);
  assert.equal(R.standardizedResultText(line), "15 34 各半車", "car play text must survive canonical rendering");
  assert.equal(JSON.stringify(line), before, "car canonical is pure");
  ok("car canonical 保留 各半車、pure render");
}

console.log(`\n${passed} tests passed`);
