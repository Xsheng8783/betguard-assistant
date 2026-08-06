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

  function escHtml(s) {
    return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }

  function normRuleText(r) {
    return String(r || "").replace(/\s+/g, "").replace(/[x×]/g, "X");
  }

  function ruleParts(rule) {
    const m = /^([234/]+)X(\d+(?:\.\d+)?)$/.exec(normRuleText(rule));
    if (!m) return null;
    return { cats: m[1].match(/[234]/g) || [], value: m[2] };
  }

  function normalizeCandidate(value) {
    return normRuleText(value);
  }

  // Display-only canonical result: number_groups in order, multiplier_text
  // LAST. NEVER parses model_raw_text; if there are no groups, raw_text is
  // shown as-is. PURE: never mutates line.
  function standardizedResultText(line) {
    if (!line) return "";
    const groups = line.number_groups || [];
    const mult = canonicalMultiplierText(line.multiplier_text);
    if (groups.length) {
      const body =
        line.layout_hint === "column_bet"
          ? groups.map((g) => (g || []).join(" ")).join(" / ")
          : groups.flat().join(" ");
      return mult ? `${body} ${mult}` : body;
    }
    return String(line.raw_text || "");
  }

  // Display-only canonical multiplier: spaces around x/× and / collapsed,
  // x/× -> X, category order 2/3/4. PURE: never mutates anything.
  function canonicalMultiplierText(text) {
    if (!text) return "";
    const compact = String(text)
      .trim()
      .replace(/\s*([xX×])\s*/g, "X")
      .replace(/\s*\/\s*/g, "/");
    return compact
      .split(/\s+/)
      .filter(Boolean)
      .map((rule) => {
        const m = /^([234/]+)X([\d.]+)$/.exec(rule);
        if (!m) return rule;
        const cats = Array.from(new Set(m[1].match(/[234]/g) || [])).sort().join("/");
        return cats + "X" + m[2];
      })
      .join(" ");
  }

  // Returns {hasCandidates, candidates, merged, displayCandidates, adopted,
  // hint}. PURE: never mutates line. Candidates are normalized + deduped for
  // DISPLAY ONLY; the original fallback_candidate.multiplier_candidates array
  // is never modified here.
  function multiplierCandidatesInfo(line) {
    const fb = line && line.fallback_candidate;
    const raw = Array.isArray(fb && fb.multiplier_candidates) ? fb.multiplier_candidates : [];
    const seen = new Set();
    const candidates = [];
    for (const c of raw) {
      const n = normalizeCandidate(c);
      if (!n || seen.has(n)) continue;
      seen.add(n);
      candidates.push(n);
    }
    let merged = null;
    if (line && line.multiplier_text) {
      const currentRules = String(line.multiplier_text).split(/\s+/).map(normalizeCandidate).filter(Boolean);
      for (const cand of candidates) {
        const cp = ruleParts(cand);
        if (!cp) continue;
        const cats = new Set(cp.cats);
        for (const r of currentRules) {
          const p = ruleParts(r);
          if (p && p.value === cp.value) p.cats.forEach((c) => cats.add(c));
        }
        if (cats.size >= 2) {
          merged = Array.from(cats).sort().join("/") + "X" + cp.value;
          break;
        }
      }
    }
    const displayCandidates = merged && !candidates.includes(merged)
      ? candidates.concat([merged])
      : candidates.slice();
    const adopted = fb && fb.adopted_multiplier ? normalizeCandidate(fb.adopted_multiplier) : null;
    const hint = merged ? `可能為 ${merged}，請依圖片確認` : null;
    return {
      hasCandidates: candidates.length > 0,
      candidates,
      merged,
      displayCandidates,
      adopted,
      hint,
    };
  }

  // Display-only HTML; returns "" when there is nothing to show. Adopting is
  // an explicit button click; rendering itself never mutates the line.
  function multiplierCandidatesHtml(line) {
    const info = multiplierCandidatesInfo(line);
    if (!info.hasCandidates) return "";
    const lineId = escHtml(line.line_id);
    const item = (value) => {
      const isMerged = info.merged === value;
      const adopted = info.adopted === value;
      const action = adopted
        ? '<span class="mult-adopted">已採用</span>'
        : `<button type="button" class="mult-adopt" onclick="adoptCandidateClick('${lineId}','${escHtml(value)}')">採用此候選</button>`;
      return `<li class="${isMerged ? "mult-merged" : ""}"><span class="mult-cand">${escHtml(value)}</span>${action}</li>`;
    };
    const items = info.displayCandidates.map(item).join("");
    return (
      '<div class="mult-candidates"><b>倍率候選（需人工確認）</b><ul>' +
      items +
      "</ul>" +
      (info.hint ? `<div class="mult-hint">${escHtml(info.hint)}</div>` : "") +
      "</div>"
    );
  }

  // Explicit human adoption of one candidate. Mutates line ONLY on adoption:
  // multiplier_text, multiplier_rules, human_raw_text/raw_text, review_action
  // -> corrected, uncertain stays true, and fallback_candidate keeps ALL
  // original candidates plus an adopted_multiplier marker. Never confirms.
  function adoptMultiplierCandidate(line, candidate) {
    if (!line) return { ok: false, error: "line missing" };
    const info = multiplierCandidatesInfo(line);
    const value = normalizeCandidate(candidate);
    if (!value || !(info.candidates.includes(value) || info.merged === value)) {
      return { ok: false, error: "candidate not available" };
    }
    const parts = ruleParts(value);
    if (!parts || parts.cats.length === 0) {
      return { ok: false, error: "candidate unparseable" };
    }
    const groups = line.number_groups || [];
    const humanRaw =
      (line.layout_hint === "column_bet"
        ? groups.map((g) => (g || []).join(" ")).join(" / ")
        : groups.flat().join(" ")) + " " + value;
    line.multiplier_text = value;
    line.multiplier_rules = [{ rule_text: value, categories: parts.cats, value: parts.value }];
    line.human_raw_text = humanRaw;
    line.raw_text = humanRaw;
    line.review_action = "corrected"; // edited but NOT confirmed
    line.uncertain = true; // stays until human unchecks or confirms
    line.correction_source = "multiplier_candidate";
    line.human_edited = true;
    line.fallback_candidate = Object.assign({}, line.fallback_candidate || {});
    line.fallback_candidate.adopted_multiplier = value; // original candidates untouched
    return { ok: true, value, line };
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

  return {
    roiDigits,
    playText,
    canApplyRoi,
    applyRoiToLine,
    ZH,
    multiplierCandidatesInfo,
    multiplierCandidatesHtml,
    adoptMultiplierCandidate,
    normalizeCandidate,
    standardizedResultText,
    canonicalMultiplierText,
  };
});
