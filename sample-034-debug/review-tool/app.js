"use strict";

const state = {
  samples: [],
  sid: null,
  pre: null,
  draft: null,
  edits: [],
  filters: new Set(),
  scale: 1, tx: 0, ty: 0,
};

const $ = (sel) => document.querySelector(sel);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

function track(lineId, fields) {
  state.edits.push({ at: new Date().toISOString(), line_id: lineId, fields: Array.isArray(fields) ? fields : [fields] });
}

async function api(url, opts) {
  const r = await fetch(url, opts);
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).error || r.statusText);
  return r.json();
}

// ---------------- overview ----------------
async function loadOverview() {
  state.samples = await api("/api/overview");
  const ul = $("#sample-list");
  ul.innerHTML = "";
  for (const s of state.samples) {
    const li = document.createElement("li");
    li.dataset.sid = s.sample_id;
    li.className = s.sample_id === state.sid ? "active" : "";
    const p = s.review_progress || {};
    li.innerHTML =
      `<span>${esc(s.sample_id)}</span>` +
      `<span class="prog">${s.review_status === "reviewed" ? "✅" : `${p.confirmed_lines || 0}/${p.total_lines || 0}`}</span>`;
    li.onclick = () => selectSample(s.sample_id);
    ul.appendChild(li);
  }
}

async function selectSample(sid) {
  state.sid = sid;
  state.edits = [];
  const data = await api(`/api/sample/${sid}`);
  state.pre = data.prelabel;
  state.draft = data.draft;
  $("#editor-title").textContent = `${sid} — 人工審核`;
  $("#review-img").src = `/image/${sid}`;
  $("#review-img").onload = () => fitImage();
  renderKnownIssues();
  renderEditor();
  renderProgress();
  loadOverview();
}

// ---------------- image zoom/pan ----------------
function applyTransform() {
  const img = $("#review-img");
  img.style.transform = `translate(${state.tx}px, ${state.ty}px) scale(${state.scale})`;
}
function fitImage() {
  const canvas = $("#viewer-canvas");
  const img = $("#review-img");
  const cw = canvas.clientWidth, ch = canvas.clientHeight;
  const iw = img.naturalWidth || 1, ih = img.naturalHeight || 1;
  state.scale = Math.min(cw / iw, ch / ih, 1.5);
  state.tx = (cw - iw * state.scale) / 2;
  state.ty = (ch - ih * state.scale) / 2;
  applyTransform();
}
function zoomBy(factor, cx, cy) {
  const canvas = $("#viewer-canvas");
  const img = $("#review-img");
  if (cx === undefined) { cx = canvas.clientWidth / 2; cy = canvas.clientHeight / 2; }
  const ns = Math.min(8, Math.max(0.1, state.scale * factor));
  const px = (cx - state.tx) / state.scale, py = (cy - state.ty) / state.scale;
  state.scale = ns;
  state.tx = cx - px * ns;
  state.ty = cy - py * ns;
  applyTransform();
}
function panTo(pos) {
  const img = $("#review-img");
  const cw = $("#viewer-canvas").clientWidth, ch = $("#viewer-canvas").clientHeight;
  const iw = (img.naturalWidth || 1) * state.scale, ih = (img.naturalHeight || 1) * state.scale;
  if (pos === "left") { state.tx = 0; state.ty = 0; }
  else if (pos === "center") { state.tx = -iw / 3; state.ty = 0; }
  else if (pos === "right") { state.tx = -(iw * 2) / 3; state.ty = 0; }
  else if (pos === "bottom") { state.tx = 0; state.ty = -(ih * 0.45); }
  applyTransform();
}
function initViewer() {
  const canvas = $("#viewer-canvas");
  canvas.addEventListener("wheel", (e) => {
    e.preventDefault();
    const rect = canvas.getBoundingClientRect();
    zoomBy(e.deltaY < 0 ? 1.12 : 0.89, e.clientX - rect.left, e.clientY - rect.top);
  }, { passive: false });
  let dragging = false, sx = 0, sy = 0, stx = 0, sty = 0;
  canvas.addEventListener("mousedown", (e) => {
    dragging = true; sx = e.clientX; sy = e.clientY; stx = state.tx; sty = state.ty;
    canvas.classList.add("dragging");
  });
  window.addEventListener("mousemove", (e) => {
    if (!dragging) return;
    state.tx = stx + (e.clientX - sx);
    state.ty = sty + (e.clientY - sy);
    applyTransform();
  });
  window.addEventListener("mouseup", () => { dragging = false; canvas.classList.remove("dragging"); });
  $("#zoom-in").onclick = () => zoomBy(1.2);
  $("#zoom-out").onclick = () => zoomBy(0.85);
  $("#zoom-fit").onclick = fitImage;
  document.querySelectorAll("[data-pos]").forEach((b) => (b.onclick = () => panTo(b.dataset.pos)));
  window.addEventListener("resize", fitImage);
}

// ---------------- known issues ----------------
function renderKnownIssues() {
  const box = $("#known-issues");
  const msgs = [];
  const d = state.draft;
  if (!d) { box.innerHTML = ""; return; }
  const sharedCount = (d.shared_multiplier_rules || []).length;
  const colCount = (d.lines || []).filter((l) => l.layout_hint === "column_bet").length;
  if (state.sid === "sample-005") {
    const weird = (d.shared_multiplier_rules || []).filter((r) => /08\s*10/.test(r.raw_text || "") || (r.warnings || []).includes("multiplier_unparsed"));
    if (weird.length) msgs.push(`sample-005 已知誤分類：${weird.length} 條 shared rule 需檢查（08 10 / 無類別 ×0.5）`);
  }
  if ((state.sid === "sample-006" || state.sid === "sample-007") && sharedCount >= 10) {
    msgs.push(`${state.sid}：${sharedCount} 條 shared rules，疑似過度判定「各／共用倍率」，請逐條確認作用範圍`);
  }
  if (colCount === 0) msgs.push("column_bet=0：請對照原圖確認是否真的沒有柱碰");
  box.innerHTML = msgs.map((m) => `<div>⚠ ${esc(m)}</div>`).join("");
}

// ---------------- editor ----------------
function allLineIds() {
  return (state.draft.lines || []).map((l) => l.line_id);
}

function lineBadges(l) {
  const b = [];
  if (l.play_type === "car_bet") b.push('<span class="badge car">車玩法 · 未支援語意 · 不進自動填單</span>');
  if (l.layout_hint === "column_bet") b.push('<span class="badge col">柱碰</span>');
  if (l.uncertain) b.push('<span class="badge unc">不確定' + (l.uncertain_reason ? ` · ${esc(reasonZh(l.uncertain_reason))}` : "") + "</span>");
  if ((l.warnings || []).includes("LEGACY_OR_HALLUCINATED_EQUALS")) b.push('<span class="badge eq">出現「=」需人工</span>');
  if (l.human_added) b.push('<span class="badge human">人工新增</span>');
  return b.join(" ");
}

function reasonZh(r) {
  return ({
    unreadable_digit: "數字看不清楚",
    unclear_multiplier: "倍率看不清楚",
    unclear_grouping: "分組看不清楚",
    unclear_boundary: "邊界看不清楚",
    unclear_reading_order: "順序看不清楚",
    ambiguous_category_or_number: "二三/23 位置歧義",
    unsupported_play_semantics: "未支援玩法（車）",
    unknown: "其他",
  })[r] || r || "";
}

function regionZh(type) {
  return ({
    normal_block: "一般區塊",
    shared_multiplier_block: "共用倍率區塊",
    column_bet: "柱碰區塊",
    bottom_special: "底部特殊區",
    unknown: "未分類",
  })[type] || type || "未分類";
}

function regionNum(regionId) {
  const m = String(regionId || "").match(/(\d+)/);
  return m ? m[1] : "?";
}

function regionOptions(selected) {
  return (state.draft.regions || [])
    .map((r) => `<option value="${esc(r.region_id)}" ${r.region_id === selected ? "selected" : ""}>${esc(r.region_id)} · ${esc(regionZh(r.region_type))}</option>`)
    .join("");
}

function onLineEdit(line, field) {
  line.review_action = line.review_action === "confirmed" ? "corrected" : line.review_action || "corrected";
  track(line.line_id, field);
  renderProgress();
}

function renderNumberGroupsInput(line) {
  const div = document.createElement("div");
  div.className = "row";
  div.innerHTML = '<label>號碼組合</label>';
  if (line.layout_hint === "column_bet") {
    div.appendChild(renderColumnEditor(line));
  } else {
    const input = document.createElement("input");
    input.type = "text";
    input.value = (line.number_groups || []).map((g) => g.join(" ")).join(" ｜ ");
    input.title = "例：01 20；多組用｜分隔，例如 01 20 ｜ 04 11";
    input.oninput = () => {
      const groups = input.value.split("｜").map((s) => s.trim().split(/\s+/).filter(Boolean));
      line.number_groups = groups.filter((g) => g.length);
      onLineEdit(line, "number_groups");
    };
    div.appendChild(input);
  }
  return div;
}

function renderColumnEditor(line) {
  const wrap = document.createElement("div");
  const cols = line.number_groups || [];
  const rows = Math.max(1, ...cols.map((c) => c.length));
  const table = document.createElement("table");
  table.className = "column-table";
  let html = "<tr><th></th>" + cols.map((_, ci) => `<th>欄 ${ci + 1}</th>`).join("") + "</tr>";
  for (let r = 0; r < rows; r++) {
    html += "<tr><td>" + (r + 1) + "</td>";
    for (let ci = 0; ci < cols.length; ci++) {
      html += `<td><input data-col="${ci}" data-row="${r}" value="${esc(cols[ci][r] || "")}"></td>`;
    }
    html += "</tr>";
  }
  table.innerHTML = html;
  table.querySelectorAll("input").forEach((inp) => {
    inp.oninput = () => {
      const ci = +inp.dataset.col, ri = +inp.dataset.row;
      while (line.number_groups.length <= ci) line.number_groups.push([]);
      line.number_groups[ci][ri] = inp.value.trim();
      onLineEdit(line, "column_bet");
    };
  });
  wrap.appendChild(table);
  const addCol = document.createElement("button");
  addCol.textContent = "＋欄";
  addCol.onclick = () => { line.number_groups.push([]); renderEditor(); };
  const delCol = document.createElement("button");
  delCol.textContent = "－末欄";
  delCol.onclick = () => { line.number_groups.pop(); onLineEdit(line, "column_bet"); renderEditor(); };
  const addRow = document.createElement("button");
  addRow.textContent = "＋列";
  addRow.onclick = () => { line.number_groups.forEach((c) => c.push("")); onLineEdit(line, "column_bet"); renderEditor(); };
  wrap.append(addCol, delCol, addRow);
  return wrap;
}

function renderSharedRuleEditor(rule) {
  const box = document.createElement("div");
  box.className = "sm-editor";
  const play = rule.play_type === "car_bet"
    ? '<span class="badge car">車玩法 · 未支援語意</span>'
    : "";
  const warn = rule.uncertain
    ? `<span class="badge unc">不確定${rule.uncertain_reason ? " · " + esc(reasonZh(rule.uncertain_reason)) : ""}</span>`
    : "";
  box.innerHTML = `
    <div class="model-raw"><b>AI 讀到的原文：</b>${esc(rule.raw_text)}</div>
    <div class="row"><label>人工修正原文</label><input type="text" class="sm-human" value="${esc(rule.human_raw_text || rule.raw_text)}"></div>
    <div class="row"><label>作用範圍</label>
      <select class="sm-scope">
        <option value="current_group" ${rule.scope === "current_group" ? "selected" : ""}>目前這組</option>
        <option value="all_groups_in_region" ${rule.scope === "all_groups_in_region" ? "selected" : ""}>整個區塊</option>
        <option value="unresolved_region" ${rule.scope === "unresolved_region" ? "selected" : ""}>邊界不明</option>
      </select>
    </div>
    <div class="row"><label>類別</label><span>${esc(JSON.stringify(rule.categories || []))}</span></div>
    <div class="row"><label>倍率值</label><span>${esc(rule.value_text || "")}</span></div>
    <div class="row"><label>不確定</label><input type="checkbox" class="sm-unc" ${rule.uncertain ? "checked" : ""}></div>
    <div>${play}${warn}</div>
    <div><b>套用到哪些行（務必勾選）</b></div>
    <div class="apply-list">${allLineIds().map((id) =>
      `<label><input type="checkbox" class="sm-apply" value="${esc(id)}" ${(rule.applies_to_line_ids || []).includes(id) ? "checked" : ""}> ${esc(id)}</label>`
    ).join("")}</div>`;
  box.querySelector(".sm-human").oninput = (e) => {
    rule.human_raw_text = e.target.value;
    rule.raw_text = e.target.value; // working copy; model original kept in prelabel
    onLineEdit(rule, "shared_multiplier.raw_text");
  };
  box.querySelector(".sm-scope").onchange = (e) => {
    rule.scope = e.target.value;
    onLineEdit(rule, "shared_multiplier.scope");
  };
  box.querySelector(".sm-unc").onchange = (e) => {
    rule.uncertain = e.target.checked;
    if (!rule.uncertain) rule.uncertain_reason = null;
    onLineEdit(rule, "shared_multiplier.uncertain");
  };
  box.querySelectorAll(".sm-apply").forEach((cb) => {
    cb.onchange = () => {
      rule.applies_to_line_ids = [...box.querySelectorAll(".sm-apply:checked")].map((x) => x.value);
      onLineEdit(rule, "shared_multiplier.applies_to_line_ids");
    };
  });
  const actions = document.createElement("div");
  actions.className = "line-actions";
  const toLine = document.createElement("button");
  toLine.textContent = "改為普通行";
  toLine.onclick = () => convertRuleToLine(rule);
  const del = document.createElement("button");
  del.textContent = "刪除此規則";
  del.className = "danger";
  del.onclick = () => {
    if (!confirm(`刪除規則「${rule.raw_text || ""}」？`)) return;
    state.draft.shared_multiplier_rules = state.draft.shared_multiplier_rules.filter((r) => r !== rule);
    track(rule.raw_text || "rule", "shared_multiplier.deleted");
    renderEditor();
  };
  actions.append(toLine, del);
  box.appendChild(actions);
  return box;
}

function convertRuleToLine(rule) {
  const d = state.draft;
  const firstId = (rule.applies_to_line_ids || [])[0];
  const target = d.lines.find((l) => l.line_id === firstId);
  const regionId = target ? target.region_id : ((d.regions[0] || {}).region_id || "R01");
  const id = `H${d.lines.length + 1}`;
  const rt = rule.raw_text || "";
  let groups = [];
  if (/^[\d\s.()]+$/.test(rt)) {
    const toks = rt.replace(/[.()]/g, " ").split(/\s+/).filter((t) => /^\d{1,2}$/.test(t));
    groups = toks.length ? [toks] : [];
  }
  d.lines.push({
    line_id: id, entry_id: id, region_id: regionId,
    order: d.lines.filter((l) => l.region_id === regionId).length + 1,
    raw_text: rt, model_raw_text: rt, human_raw_text: rt,
    number_groups: groups, multiplier_text: "",
    layout_hint: "normal_row", play_text: null, play_type: null,
    uncertain: groups.length ? false : true,
    uncertain_reason: groups.length ? null : "unknown",
    alternatives: [], warnings: ["converted_from_shared_rule"],
    review_action: "corrected", human_added: true,
  });
  d.shared_multiplier_rules = d.shared_multiplier_rules.filter((r) => r !== rule);
  track(id, "converted_from_shared_rule");
  renderEditor();
}

function convertLineToRule(line) {
  const d = state.draft;
  const rt = line.human_raw_text || line.raw_text || "";
  d.shared_multiplier_rules.push({
    raw_text: rt,
    human_raw_text: rt,
    scope: rt.includes("各") ? "all_groups_in_region" : "current_group",
    applies_to_line_ids: [],
    boundary_uncertain: true,
    uncertain: line.uncertain,
    uncertain_reason: line.uncertain_reason,
    categories: [],
    value_text: null,
    warnings: ["converted_from_line"],
    play_type: line.play_type,
    play_text: line.play_text,
  });
  d.lines = d.lines.filter((l) => l !== line);
  track(line.line_id, "converted_to_shared_rule");
  renderEditor();
}

function renderLineCard(line, region) {
  const card = document.createElement("div");
  card.className = "line-card " + (line.review_action || "pending");
  if (line.uncertain) card.classList.add("uncertain");
  card.dataset.lineId = line.line_id;
  const flags = [];
  if (line.uncertain) flags.push("uncertain");
  if (line.play_type === "car_bet") flags.push("car_bet");
  if (line.layout_hint === "column_bet") flags.push("column_bet");
  if ((line.warnings || []).includes("LEGACY_OR_HALLUCINATED_EQUALS")) flags.push("legacy");
  if (line.uncertain_reason === "ambiguous_category_or_number") flags.push("ambiguous");
  if (line.uncertain_reason === "unsupported_play_semantics") flags.push("unsupported");
  card.dataset.flags = flags.join(" ");
  const pm = line.play_mark || {};
  const cropPath = pm.crop_path || "";
  const cropImg = cropPath
    ? `<div class="play-crop"><b>ROI 裁切證據：</b><img src="/crop/${esc(cropPath.split(/[\\/]/).pop())}" alt="play mark crop"></div>`
    : "";
  const roiApp = ROILogic.canApplyRoi(pm);
  const roiEvidence =
    pm.roi_categories || pm.roi_upper_digits || pm.roi_lower_digits
      ? `<div class="roi-evidence"><b>ROI 讀取：</b>${esc((pm.roi_categories || []).join(" + "))} × ${esc(pm.roi_multiplier || "?")}` +
        (pm.roi_uncertain ? `<div class="roi-warn">ROI 結果不確定，請核對裁切圖後再採用</div>` : "") +
        (!roiApp.ok ? `<div class="roi-warn">缺少：${esc(roiApp.missing.join("、"))}</div>` : "") +
        `</div>`
      : "";

  card.innerHTML = `
    <div class="meta">
      <span><b>第${regionNum(line.region_id)}區 第${esc(line.order)}行</b>（${esc(line.line_id)}）</span>
      <span>區塊：${esc(line.region_id)}</span>
      ${lineBadges(line)}
    </div>
    ${line.human_raw_text ? `<div class="human-raw"><b>修正後原文：</b>${esc(line.human_raw_text)}</div>` : ""}
    <div class="model-raw"><b>AI 讀到的原文：</b>${esc(line.model_raw_text ?? line.raw_text ?? "")}</div>
    ${roiEvidence}
    ${cropImg}
    <div class="row"><label>人工修正原文</label><input type="text" class="f-human" value="${esc(line.human_raw_text ?? "")}"></div>
    <div class="row"><label>倍率</label><input type="text" class="f-mult" value="${esc(line.multiplier_text || "")}"></div>
    <div class="row"><label>版面類型</label>
      <select class="f-layout">
        ${[["normal_row", "一般行"], ["number_set", "括號號碼組"], ["shared_multiplier", "共用倍率"], ["column_bet", "柱碰"]]
          .map(([v, zh]) => `<option value="${v}" ${line.layout_hint === v ? "selected" : ""}>${zh}</option>`).join("")}
      </select>
    </div>
    <div class="row"><label>玩法文字</label><input type="text" class="f-play" value="${esc(line.play_text || "")}"></div>
    <div class="row"><label>玩法類型</label>
      <select class="f-playtype">
        ${[["", "（無）"], ["car_bet", "車玩法"]].map(([v, zh]) => `<option value="${v}" ${line.play_type === v ? "selected" : ""}>${zh}</option>`).join("")}
      </select>
    </div>
    <div class="row"><label>不確定</label><input type="checkbox" class="f-unc" ${line.uncertain ? "checked" : ""}></div>
    <div class="row"><label>不確定原因</label>
      <select class="f-uncreason">
        ${[["", "（無）"], ["unreadable_digit", "數字看不清楚"], ["unclear_multiplier", "倍率看不清楚"], ["unclear_grouping", "分組看不清楚"], ["unclear_boundary", "邊界看不清楚"], ["unclear_reading_order", "順序看不清楚"], ["ambiguous_category_or_number", "二三/23 位置歧義"], ["unsupported_play_semantics", "未支援玩法（車）"], ["unknown", "其他"]]
          .map(([v, zh]) => `<option value="${v}" ${line.uncertain_reason === v ? "selected" : ""}>${zh}</option>`).join("")}
      </select>
    </div>
    <div class="row"><label>移動到區塊</label>
      <select class="f-region">${regionOptions(region.region_id)}</select>
    </div>`;

  const numWrap = document.createElement("div");
  numWrap.appendChild(renderNumberGroupsInput(line));
  card.appendChild(numWrap);

  if ((line.warnings || []).length) {
    const warns = document.createElement("div");
    warns.className = "warnings";
    warns.textContent = "注意事項：" + (line.warnings || []).join("；");
    card.appendChild(warns);
  }

  const actions = document.createElement("div");
  actions.className = "line-actions";
  const mkBtn = (text, cls, fn) => { const b = document.createElement("button"); b.textContent = text; if (cls) b.className = cls; b.onclick = fn; actions.appendChild(b); };
  mkBtn(line.review_action === "confirmed" ? "✅ 已確認" : "確認本行正確", line.review_action === "confirmed" ? "ok" : "", () => {
    line.review_action = line.review_action === "confirmed" ? "pending" : "confirmed";
    track(line.line_id, "review_action");
    renderEditor();
  });
  mkBtn("標記已修正", "", () => {
    line.review_action = "corrected";
    track(line.line_id, "review_action");
    renderEditor();
  });
  if (pm.roi_categories || pm.roi_upper_digits || pm.roi_lower_digits) {
    if (roiApp.ok) {
      mkBtn(pm.roi_uncertain ? "採用 ROI 讀取（需人工確認）" : "採用 ROI 讀取", "ok", () => applyRoiClick(line));
    } else {
      const b = document.createElement("button");
      b.textContent = "採用 ROI 讀取";
      b.disabled = true;
      b.title = "缺少：" + (roiApp.missing.join("、") || "玩法資料");
      actions.appendChild(b);
    }
  }
  mkBtn("↑", "", () => { swapOrder(line, -1); });
  mkBtn("↓", "", () => { swapOrder(line, 1); });
  mkBtn("改為共用規則", "", () => convertLineToRule(line));
  mkBtn("刪除本行", "danger", () => {
    if (!confirm(`刪除 ${line.line_id}？`)) return;
    state.draft.lines = state.draft.lines.filter((l) => l !== line);
    track(line.line_id, "deleted");
    renderEditor();
  });
  card.appendChild(actions);

  card.querySelector(".f-human").oninput = (e) => { line.human_raw_text = e.target.value; onLineEdit(line, "raw_text"); };
  card.querySelector(".f-mult").oninput = (e) => { line.multiplier_text = e.target.value; onLineEdit(line, "multiplier_text"); };
  card.querySelector(".f-layout").onchange = (e) => { line.layout_hint = e.target.value; onLineEdit(line, "layout_hint"); renderEditor(); };
  card.querySelector(".f-play").oninput = (e) => { line.play_text = e.target.value; onLineEdit(line, "play_text"); };
  card.querySelector(".f-playtype").onchange = (e) => {
    line.play_type = e.target.value || null;
    if (line.play_type === "car_bet") { line.uncertain = true; line.uncertain_reason = "unsupported_play_semantics"; }
    onLineEdit(line, "play_type");
    renderEditor();
  };
  card.querySelector(".f-unc").onchange = (e) => { line.uncertain = e.target.checked; if (!line.uncertain) line.uncertain_reason = null; onLineEdit(line, "uncertain"); };
  card.querySelector(".f-uncreason").onchange = (e) => { line.uncertain_reason = e.target.value || null; onLineEdit(line, "uncertain_reason"); };
  card.querySelector(".f-region").onchange = (e) => { line.region_id = e.target.value; onLineEdit(line, "region_id"); renderEditor(); };
  return card;
}

async function applyRoiClick(line) {
  const res = await api(`/api/sample/${state.sid}/apply-roi`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ line_id: line.line_id, expected_revision: state.draft.revision }),
  });
  if (res && res.ok) {
    const idx = state.draft.lines.findIndex((l) => l.line_id === line.line_id);
    if (idx >= 0) state.draft.lines[idx] = res.line;
    if (res.revision) state.draft.revision = res.revision;
    $("#status").textContent = res.idempotent
      ? "已採用過（未重複建立紀錄）"
      : "已採用 ROI 候選（尚未確認，請再按「確認本行正確」）";
    renderEditor();
  } else {
    $("#status").textContent = "採用 ROI 失敗：" + (res?.error || "未知錯誤");
  }
}

function swapOrder(line, delta) {
  const lines = state.draft.lines;
  const same = lines.filter((l) => l.region_id === line.region_id).sort((a, b) => (a.order || 0) - (b.order || 0));
  const i = same.indexOf(line);
  const j = i + delta;
  if (j < 0 || j >= same.length) return;
  [same[i].order, same[j].order] = [same[j].order, same[i].order];
  track(line.line_id, "order");
  renderEditor();
}

function renderEditor() {
  const body = $("#editor-body");
  const d = state.draft;
  if (!d) { body.innerHTML = '<div class="empty">請先選取樣本</div>'; return; }
  const active = state.filters;
  const lines = d.lines || [];
  const regions = d.regions || [];
  body.innerHTML = "";

  // shared multiplier rules (top, with dedicated editor)
  const rules = d.shared_multiplier_rules || [];
  if (rules.length) {
    const block = document.createElement("div");
    block.className = "region-block";
    const head = document.createElement("div");
    head.className = "region-head";
    head.innerHTML = `<span>共用倍率規則（${rules.length}）— 逐條確認是否真的是共用、套用到哪些行</span>`;
    block.appendChild(head);
    rules.forEach((r) => block.appendChild(renderSharedRuleEditor(r)));
    body.appendChild(block);
  }

  for (const region of [...regions].sort((a, b) => (a.order || 0) - (b.order || 0))) {
    const block = document.createElement("div");
    block.className = "region-block";
    const head = document.createElement("div");
    head.className = "region-head";
    head.innerHTML = `<span>${esc(region.region_id)} · ${esc(regionZh(region.region_type))}${region.column === "unknown" ? " · 位置未知（請對圖確認）" : ""}</span>`;
    const addBtn = document.createElement("button");
    addBtn.textContent = "＋新增行";
    addBtn.onclick = () => addLine(region.region_id);
    head.appendChild(addBtn);
    block.appendChild(head);
    const regionLines = lines
      .filter((l) => l.region_id === region.region_id)
      .sort((a, b) => (a.order || 0) - (b.order || 0));
    for (const line of regionLines) {
      const lineFlags = [];
      if (line.uncertain) lineFlags.push("uncertain");
      if (line.play_type === "car_bet") lineFlags.push("car_bet");
      if (line.layout_hint === "column_bet") lineFlags.push("column_bet");
      if ((line.warnings || []).includes("LEGACY_OR_HALLUCINATED_EQUALS")) lineFlags.push("legacy");
      if (line.uncertain_reason === "ambiguous_category_or_number") lineFlags.push("ambiguous");
      if (line.uncertain_reason === "unsupported_play_semantics") lineFlags.push("unsupported");
      const hidden = active.size && !lineFlags.some((x) => active.has(x));
      const card = renderLineCard(line, region);
      card.style.display = hidden ? "none" : "";
      block.appendChild(card);
    }
    body.appendChild(block);
  }
  renderProgress();
}

function addLine(regionId) {
  const lines = state.draft.lines;
  const n = lines.filter((l) => l.region_id === regionId).length + 1;
  const id = `H${lines.length + 1}`;
  lines.push({
    line_id: id, entry_id: id, region_id: regionId, order: n,
    raw_text: "", model_raw_text: null, human_raw_text: "",
    number_groups: [], multiplier_text: "", layout_hint: "normal_row",
    play_text: null, play_type: null, uncertain: true,
    uncertain_reason: "unknown", alternatives: [], warnings: [],
    review_action: "corrected", human_added: true,
  });
  track(id, "added");
  renderEditor();
}

function renderProgress() {
  const p = state.draft?.review_progress;
  $("#progress-label").textContent = p
    ? `已確認 ${p.confirmed_lines} ／ 已修正 ${p.corrected_lines} ／ 未處理 ${p.unresolved_lines} ／ 總 ${p.total_lines}`
    : "";
}

async function save(complete = false) {
  const body = { draft: state.draft, edits: state.edits };
  if (complete) body.reviewer_name = $("#reviewer-name").value;
  body.expected_revision = state.draft.revision;
  let res;
  try {
    res = await api(`/api/sample/${state.sid}${complete ? "/complete" : "/save"}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  } catch (e) {
    const msg = String(e?.message || e);
    if (msg.includes("revision_mismatch") || msg.includes("complete_validation_failed")) {
      $("#status").textContent = msg.includes("revision_mismatch")
        ? "版本衝突：另一個分頁已儲存，請重新整理（409）"
        : "完成失敗（409）：" + msg;
      await selectSample(state.sid);
      return;
    }
    throw e;
  }
  if (complete) {
    state.draft.review_status = "reviewed";
    state.draft.reviewed_at = res.reviewed_at;
    state.draft.reviewed_by = $("#reviewer-name").value || "local-user";
  }
  state.edits = [];
  if (res.revision) state.draft.revision = res.revision;
  $("#status").textContent = complete ? `已標記完成審核（${res.reviewed_at || ""}）` : `已儲存（${res.saved_at}）`;
  await loadOverview();
  renderProgress();
}

// ---------------- filters ----------------
function initFilters() {
  document.querySelectorAll("[data-filter]").forEach((cb) => {
    cb.onchange = () => {
      if (cb.checked) state.filters.add(cb.dataset.filter);
      else state.filters.delete(cb.dataset.filter);
      renderEditor();
    };
  });
}

// ---------------- boot ----------------
document.addEventListener("DOMContentLoaded", () => {
  initViewer();
  initFilters();
  $("#btn-save").onclick = () => save(false);
  $("#btn-complete").onclick = () => {
    if (!confirm("確定完成本張人工審核？將寫入 reviewed_by / reviewed_at，之後才可另行 freeze。")) return;
    save(true);
  };
  loadOverview().then(() => selectSample("sample-005"));
});
