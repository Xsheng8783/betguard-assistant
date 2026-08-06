/* ROI adoption logic shared by the review UI and Node tests.
 *
 * play_mark evidence shape (produced by prelabel_geo._merge_play_mark):
 *   first_pass_categories, roi_categories, roi_upper_digits,
 *   roi_lower_digits, roi_multiplier, roi_layout, roi_raw_text,
 *   roi_uncertain, roi_uncertain_candidates, crop_path
 */
(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.ROILogic = factory();
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  const ZH = { "2": "二", "3": "三", "4": "四" };
  const DIGIT_RE = /^[234]$/;
  const MULT_RE = /^\d+(?:\.\d+)?$/;

  function digitsFrom(value) {
    return (value || [])
      .map((d) => String(d).trim())
      .filter((d) => DIGIT_RE.test(d));
  }

  // Positional order: upper -> lower -> other_visible -> union fallback.
  function roiDigits(pm) {
    const upper = digitsFrom(pm.roi_upper_digits || pm.upper_digits);
    const lower = digitsFrom(pm.roi_lower_digits || pm.lower_digits);
    const other = digitsFrom(pm.other_visible_digits);
    let ordered = upper.concat(lower, other);
    if (ordered.length === 0) ordered = digitsFrom(pm.roi_categories || pm.categories);
    const seen = new Set();
    return ordered.filter((d) => !seen.has(d) && seen.add(d));
  }

  function playText(digits) {
    return digits.map((d) => ZH[d] || d).join("");
  }

  function roiMultiplier(pm) {
    const raw = String(pm.roi_multiplier ?? pm.multiplier ?? "").trim();
    return MULT_RE.test(raw) ? raw : null;
  }

  // Returns {ok, missing[], digits, multiplier, playText, fullText}.
  function canApplyRoi(pm) {
    const missing = [];
    const digits = roiDigits(pm);
    if (digits.length === 0) missing.push("玩法數字");
    const multiplier = roiMultiplier(pm);
    if (multiplier === null) missing.push("倍率");
    const text = playText(digits);
    if (digits.length > 0 && !text) missing.push("玩法文字");
    const ok = missing.length === 0;
    return {
      ok,
      missing,
      digits,
      multiplier,
      playText: text,
      fullText: ok ? `${text}X${multiplier}` : null,
    };
  }

  // Mutates line. Returns {ok, idempotent, edit|null, error|null, missing}.
  function applyRoiToLine(line, pm, now) {
    const prep = canApplyRoi(pm);
    if (!prep.ok) return { ok: false, idempotent: false, edit: null, missing: prep.missing, error: "ROI 資料不完整" };

    const applied = (line.play_mark && line.play_mark.applied_roi) || null;
    if (
      applied &&
      line.correction_source === "roi" &&
      line.multiplier_text === prep.fullText
    ) {
      return { ok: true, idempotent: true, edit: null, missing: [], error: null };
    }

    const groups = line.number_groups || [];
    const humanRaw =
      (line.layout_hint === "column_bet"
        ? groups.map((g) => (g || []).join(" ")).join(" / ")
        : groups.flat().join(" ")) + " " + prep.fullText;

    const at = (now || new Date()).toISOString();
    const firstPassRaw = line.model_raw_text || line.raw_text || "";
    line.human_raw_text = humanRaw;
    line.multiplier_text = prep.fullText;
    line.correction_source = "roi";
    line.human_edited = true;
    // human_raw_text / raw_text hold the adopted text; model_raw_text is
    // IMMUTABLE (always the first-pass model output). ROI evidence and
    // uncertain history stay untouched.
    line.play_mark = Object.assign({}, line.play_mark || {}, {
      first_pass_raw_text: firstPassRaw,
      applied_roi: { at, full_text: prep.fullText, digits: prep.digits, correction_source: "roi" },
    });
    line.raw_text = humanRaw;
    line.review_action = "corrected"; // edited but NOT confirmed
    // Do NOT touch line.uncertain / uncertain_reason (needs_review stays).

    const edit = { at, line_id: line.line_id, fields: ["multiplier_text", "raw_text", "play_mark", "correction_source"], source: "roi" };
    return { ok: true, idempotent: false, edit, missing: [], error: null };
  }

  return { roiDigits, playText, canApplyRoi, applyRoiToLine, ZH };
});
