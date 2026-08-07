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
    if (value && typeof value === "object") return normRuleText(value.rule_text || "");
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

  // Complete rules from a multiplier text. Returns [] when ANY token is
  // partial/invalid (fragments never become official rules).
  function splitCompleteRules(text) {
    if (!text) return [];
    const compact = canonicalMultiplierText(text);
    const out = [];
    for (const t of compact.split(/\s+/).filter(Boolean)) {
      if (!ruleParts(t)) return [];
      out.push(t);
    }
    return out;
  }

  function candidateMeta(c) {
    return c && typeof c === "object" ? c : {};
  }

  // Legacy (no metadata) mode inference. Without physical-slot evidence we
  // must NEVER auto-declare mutual exclusion: same value -> unknown (needs
  // review), different value -> additional rule.
  function inferMode(rule, currentRules) {
    const p = ruleParts(rule);
    if (!p) return "unknown_requires_review";
    for (const r of currentRules) {
      const q = ruleParts(r);
      if (q && q.value === p.value) return "unknown_requires_review";
    }
    return "additional_rule";
  }

  // Returns {hasCandidates, entries, display, adopted(Set), merged, hint}.
  // PURE: never mutates line. Candidates may be strings (legacy) or dicts
  // {rule_text, candidate_mode, candidate_group_id, source, evidence}.
  function multiplierCandidatesInfo(line) {
    const fb = line && line.fallback_candidate;
    const raw = Array.isArray(fb && fb.multiplier_candidates) ? fb.multiplier_candidates : [];
    const currentRules = splitCompleteRules(line && line.multiplier_text);
    const seen = new Set();
    const entries = [];
    for (const c of raw) {
      const rule = normalizeCandidate(c);
      const meta = candidateMeta(c);
      if (!rule || seen.has(rule)) continue;
      seen.add(rule);
      const evidence = meta.evidence || {};
      entries.push({
        rule,
        mode: meta.candidate_mode || inferMode(rule, currentRules),
        group: meta.candidate_group_id || null,
        source: meta.source || null,
        composable: meta.composable === true || evidence.composable === true,
        merged: false,
      });
    }
    const adoptedList = Array.isArray(fb && fb.adopted_multipliers)
      ? fb.adopted_multipliers.map(normalizeCandidate).filter(Boolean)
      : [];
    const adoptedSingle = fb && fb.adopted_multiplier ? normalizeCandidate(fb.adopted_multiplier) : null;
    if (adoptedSingle && !adoptedList.includes(adoptedSingle)) adoptedList.push(adoptedSingle);
    const adopted = new Set(adoptedList);

    // Derived merged candidate: ONLY same candidate_group_id + composable
    // evidence + same value. Different groups never merge, even on same value.
    let merged = null;
    let mergedGroup = null;
    for (const e of entries) {
      if (!(e.mode === "alternative_reading" && e.group && e.composable)) continue;
      const cp = ruleParts(e.rule);
      if (!cp) continue;
      const peers = entries.filter((x) =>
        x.group === e.group &&
        x.mode === "alternative_reading" &&
        x.composable &&
        (() => { const q = ruleParts(x.rule); return q && q.value === cp.value; })()
      );
      const cats = new Set();
      peers.forEach((x) => { const q = ruleParts(x.rule); (q ? q.cats : []).forEach((c) => cats.add(c)); });
      if (cats.size >= 2) {
        merged = Array.from(cats).sort().join("/") + "X" + cp.value;
        mergedGroup = e.group;
        break;
      }
    }
    const display = entries.slice();
    if (merged && !entries.some((e) => e.rule === merged)) {
      display.push({ rule: merged, mode: "alternative_reading", group: mergedGroup, source: "derived", merged: true, composable: true });
    }
    const hint = merged ? `可能為 ${merged}，請依圖片確認` : null;
    return {
      hasCandidates: entries.length > 0,
      entries,
      candidates: entries.map((e) => e.rule),
      display,
      displayCandidates: display.map((e) => e.rule),
      adopted,
      merged,
      hint,
    };
  }

  // Display-only HTML; returns "" when there is nothing to show. Adopting /
  // removing is an explicit button click; rendering never mutates the line.
  function multiplierCandidatesHtml(line) {
    const info = multiplierCandidatesInfo(line);
    if (!info.hasCandidates) return "";
    const lineId = escHtml(line.line_id);
    const item = (e) => {
      const adopted = info.adopted.has(e.rule);
      const modeTag = e.mode === "alternative_reading"
        ? '<span class="mult-mode">替代讀法</span>'
        : e.mode === "unknown_requires_review"
          ? '<span class="mult-mode">未標記（需人工確認）</span>'
          : '<span class="mult-mode">追加規則</span>';
      const action = adopted
        ? `<button type="button" class="mult-remove" onclick="removeCandidateClick('${lineId}','${escHtml(e.rule)}')">取消採用</button>`
        : `<button type="button" class="mult-adopt" onclick="adoptCandidateClick('${lineId}','${escHtml(e.rule)}')">採用此候選</button>`;
      return (
        `<li class="${e.merged ? "mult-merged" : ""}"><span class="mult-cand">${escHtml(e.rule)}</span>${modeTag}` +
        (adopted ? '<span class="mult-adopted">已採用</span>' : "") +
        action +
        "</li>"
      );
    };
    const items = info.display.map(item).join("");
    return (
      '<div class="mult-candidates"><b>倍率候選（需人工確認）</b><ul>' +
      items +
      "</ul>" +
      (info.hint ? `<div class="mult-hint">${escHtml(info.hint)}</div>` : "") +
      "</div>"
    );
  }

  function _applyAdoption(line, newText, value, entry) {
    line.multiplier_text = newText || null;
    line.multiplier_rules = newText
      ? newText.split(" ").map((r) => {
          const p = ruleParts(r) || { cats: [], value: null };
          return { rule_text: r, categories: p.cats, value: p.value };
        })
      : [];
    const groups = line.number_groups || [];
    const humanRaw =
      (line.layout_hint === "column_bet"
        ? groups.map((g) => (g || []).join(" ")).join(" / ")
        : groups.flat().join(" ")) + (newText ? " " + newText : "");
    line.human_raw_text = humanRaw;
    line.raw_text = humanRaw;
    line.review_action = "corrected"; // edited but NOT confirmed
    line.correction_source = "multiplier_candidate";
    line.human_edited = true;
    line.fallback_candidate = Object.assign({}, line.fallback_candidate || {});
    const adoptedList = Array.isArray(line.fallback_candidate.adopted_multipliers)
      ? line.fallback_candidate.adopted_multipliers.map(normalizeCandidate).filter(Boolean)
      : [];
    if (!adoptedList.includes(value)) adoptedList.push(value);
    line.fallback_candidate.adopted_multipliers = adoptedList;
    line.fallback_candidate.adopted_multiplier = value; // backward compatible
    const entries = Array.isArray(line.fallback_candidate.adopted_entries)
      ? line.fallback_candidate.adopted_entries
      : [];
    const exists = entries.find((e) => normalizeCandidate(e.rule_text || "") === value && !e.removed_at);
    if (!exists) {
      entries.push(Object.assign({ rule_text: value, at: new Date().toISOString() }, entry));
    }
    line.fallback_candidate.adopted_entries = entries;
  }

  // Explicit human adoption of one candidate. additional_rule candidates are
  // ADDED to the current rules (never string-concatenated, never merged by
  // value unless evidence says composable); alternative_reading candidates
  // REPLACE the current reading (same physical slot -> mutually exclusive).
  function adoptMultiplierCandidate(line, candidate) {
    if (!line) return { ok: false, error: "line missing" };
    const info = multiplierCandidatesInfo(line);
    const value = normalizeCandidate(candidate);
    const found = info.entries.find((e) => e.rule === value) || info.display.find((e) => e.rule === value);
    if (!value || !found) return { ok: false, error: "candidate not available" };
    const parts = ruleParts(value);
    if (!parts || parts.cats.length === 0) return { ok: false, error: "candidate unparseable" };
    const meta = candidateMeta(candidate);
    const mode = found.mode || meta.candidate_mode || "additional_rule";
    const group = found.group || meta.candidate_group_id || null;
    const current = splitCompleteRules(line.multiplier_text);
    const before = current.slice();
    let after;
    let replacedRules = [];
    if (mode === "alternative_reading") {
      // Remove ONLY the candidate-system-managed alternatives of the SAME
      // physical slot (candidate_group_id); keep other groups' adopted rules
      // and the original structured/human rules.
      const entries = Array.isArray((line.fallback_candidate || {}).adopted_entries)
        ? line.fallback_candidate.adopted_entries
        : [];
      replacedRules = entries
        .filter((e) => e.group && group && e.group === group && !e.removed_at && normalizeCandidate(e.rule_text || "") !== value)
        .map((e) => normalizeCandidate(e.rule_text || ""));
      after = current.filter((r) => !replacedRules.includes(r));
      if (!after.includes(value)) after.push(value);
      const nowIso = new Date().toISOString();
      entries.forEach((e) => {
        if (replacedRules.includes(normalizeCandidate(e.rule_text || "")) && !e.removed_at) e.removed_at = nowIso;
      });
      line.fallback_candidate = Object.assign({}, line.fallback_candidate || {}, { adopted_entries: entries });
      const am = Array.isArray(line.fallback_candidate.adopted_multipliers)
        ? line.fallback_candidate.adopted_multipliers.map(normalizeCandidate).filter(Boolean)
        : [];
      line.fallback_candidate.adopted_multipliers = am.filter((r) => !replacedRules.includes(r));
    } else {
      after = current.includes(value) ? current.slice() : current.concat([value]);
    }
    const addedRules = after.filter((r) => !before.includes(r));
    const removedRules = before.filter((r) => !after.includes(r));
    _applyAdoption(line, after.join(" "), value, { mode, group, added_rules: addedRules, removed_rules: removedRules, replaced_rules: replacedRules });
    return { ok: true, value, mode, line };
  }

  // Cancel one adopted candidate. additional_rule: remove ONLY when this
  // candidate introduced the rule (a pre-existing rule is never deleted).
  function removeAdoptedCandidate(line, rule) {
    if (!line) return { ok: false, error: "line missing" };
    const value = normalizeCandidate(rule);
    const fb = line.fallback_candidate || {};
    const entries = Array.isArray(fb.adopted_entries) ? fb.adopted_entries : [];
    const idx = entries.findIndex((e) => normalizeCandidate(e.rule_text || "") === value);
    if (idx < 0) return { ok: false, error: "not adopted" };
    const entry = entries[idx];
    let current = splitCompleteRules(line.multiplier_text);
    if (entry.mode === "alternative_reading") {
      current = current.filter((r) => r !== value);
    } else {
      const added = (entry.added_rules || []).map(normalizeCandidate);
      if (added.includes(value)) current = current.filter((r) => r !== value);
      // if the rule pre-existed, keep it
    }
    const newText = current.join(" ");
    line.multiplier_text = newText || null;
    line.multiplier_rules = newText
      ? newText.split(" ").map((r) => {
          const p = ruleParts(r) || { cats: [], value: null };
          return { rule_text: r, categories: p.cats, value: p.value };
        })
      : [];
    const groups = line.number_groups || [];
    const humanRaw =
      (line.layout_hint === "column_bet"
        ? groups.map((g) => (g || []).join(" ")).join(" / ")
        : groups.flat().join(" ")) + (newText ? " " + newText : "");
    line.human_raw_text = humanRaw;
    line.raw_text = humanRaw;
    line.review_action = "corrected";
    const remaining = (fb.adopted_multipliers || []).map(normalizeCandidate).filter((r) => r !== value);
    fb.adopted_multipliers = remaining;
    fb.adopted_multiplier = remaining.length ? remaining[remaining.length - 1] : null;
    entries[idx] = Object.assign({}, entry, { removed_at: new Date().toISOString() });
    fb.adopted_entries = entries;
    line.fallback_candidate = fb;
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
    removeAdoptedCandidate,
    normalizeCandidate,
    standardizedResultText,
    canonicalMultiplierText,
    splitCompleteRules,
  };
});
