"""Updated assist panel HTML with larger fonts, Chinese headings, and card-disappear on done."""

ASSIST_PANEL_HTML = """<!doctype html>
<html lang="zh-Hant">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Betguard 輔助面板</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:system-ui,"Microsoft JhengHei",sans-serif;background:#f8fafc;color:#1e293b;font-size:16px;padding:14px}
h2{font-size:21px;margin-bottom:8px;color:#0f172a}
h3{font-size:19px;margin-bottom:6px;color:#0f172a}
textarea{width:100%;min-height:120px;font-size:17px;font-family:monospace;padding:10px;border:1px solid #cbd5e1;border-radius:6px;resize:vertical}
button{font-size:15px;padding:8px 16px;border-radius:6px;border:none;cursor:pointer;font-weight:600;min-height:36px}
.btn-primary{background:#2563eb;color:#fff}
.btn-primary:disabled{background:#94a3b8;cursor:not-allowed}
.section{background:#fff;border:1px solid #e2e8f0;border-radius:8px;padding:12px;margin-bottom:12px}
.badge{display:inline-block;font-size:14px;padding:3px 8px;border-radius:4px;font-weight:600}
.badge-valid{background:#dbeafe;color:#1e40af}
.badge-review{background:#fef3c7;color:#92400e}
.badge-fail{background:#fee2e2;color:#991b1b}
.item{padding:8px 0;border-bottom:1px solid #f1f5f9;font-size:16px}
.item:last-child{border-bottom:none}
.item > div:not(:last-child){margin-bottom:4px}
.item .label{font-size:12px;color:#64748b;margin-right:4px}
.assist-fill-btn{background:#2563eb;color:#fff;margin-top:6px;font-size:15px;padding:6px 14px}
.assist-fill-btn:disabled{background:#94a3b8;cursor:not-allowed}
.btn-manual{background:#64748b;color:#fff;margin-top:6px;margin-right:6px;font-size:14px}
.btn-manual:disabled{background:#94a3b8;cursor:not-allowed}
.muted{color:#94a3b8;font-size:14px}
.muted-note{font-size:13px;color:#94a3b8;margin-left:8px}
.item.assist-completed{opacity:0.55;background:#f1f5f9}
.completed-bar{display:flex;align-items:center;gap:8px;padding:4px 0;font-size:14px}
.completed-bar button:disabled{opacity:0.4;cursor:not-allowed}
.status{font-size:14px;color:#64748b;margin-top:6px;min-height:20px}
.footer{font-size:13px;color:#94a3b8;text-align:center;margin-top:14px}
</style>
</head>
<body>
<h2>Betguard 輔助面板</h2>
<textarea id="batch-text" placeholder="貼上牌單..."></textarea>
<button id="createBatchBtn" type="button" class="btn-primary">建立審核</button>
<div class="status" id="status-msg"></div>
<div class="section">
 <h3>可輔助填入 <span class="badge badge-valid" id="valid-count">0</span></h3>
 <div class="completed-bar">
   <span style="color:#64748b">已輔助填入：<strong id="completed-count">0</strong> 筆</span>
   <button id="clear-completed-btn" style="font-size:14px;padding:4px 10px;border:1px solid #e2e8f0;border-radius:4px;background:#fff;cursor:pointer" disabled onclick="clearCompleted()">清除已反灰</button>
 </div>
 <div id="valid-items"></div>
</div>
<div class="section">
 <h3>需要人工確認 <span class="badge badge-review" id="review-count">0</span></h3>
 <div id="review-items"></div>
</div>
<div class="footer">真站填入後仍需人工確認與送出</div>
<script>
"use strict";
var panelState = { queuePath: "", validCandidates: [], reviewCandidates: [] };

function setStatus(msg) {
  var el = document.getElementById("status-msg");
  el.textContent = msg;
  setTimeout(function(){ el.textContent = ""; }, 4000);
}

function createBatch() {
  var text = document.getElementById("batch-text").value.trim();
  if (!text) { setStatus("請先貼上牌單"); return; }
  var btn = document.getElementById("createBatchBtn");
  btn.disabled = true;
  setStatus("建立審核中...");
  fetch("/assist-panel/create-batch", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text: text })
  }).then(function (r) { return r.text(); }).then(function (raw) {
    btn.disabled = false;
    var data;
    try { data = JSON.parse(raw); }
    catch (e) { setStatus("回應不是有效 JSON"); return; }
    if (!data.ok) { setStatus(data.error || "建立審核失敗"); return; }
    panelState.queuePath = data.queue_path || "";
    var valid = data.valid_items || data.valid_candidates || data.valid || [];
    var review = data.review_items || data.needs_review || data.invalid_items
      || data.review_candidates || data.invalid_fragments || [];
    panelState.validCandidates = valid;
    panelState.reviewCandidates = review;
    renderValid(valid);
    renderReview(review);
    updateCompletedCount();
    setStatus("已建立 " + valid.length + " 筆可輔助， " + review.length + " 筆需確認");
  }).catch(function (e) {
    btn.disabled = false;
    setStatus("建立審核失敗");
  });
}

document.getElementById("createBatchBtn").addEventListener("click", createBatch);

function renderValid(items) {
  var container = document.getElementById("valid-items");
  document.getElementById("valid-count").textContent = items.length;
  if (!items.length) { container.innerHTML = '<div class="muted">目前沒有可輔助填入項目</div>'; return; }
  var html = "";
  items.forEach(function (c, i) {
    var itemId = c.manual_candidate_id || ("item-" + i);
    var nums = (c.numbers || []).join(", ");
    var stars = (c.stars || []).join("") + "星";
    var unit = c.unit != null ? c.unit + "支" : "";
    var money = c.money != null ? c.money + "元" : "";
    var summary = c.summary || nums;
    var betType = c.bet_type || c.type || "normal";
    var idx = c.index != null ? c.index : (c.item_index != null ? c.item_index : i);
    html += '<div class="item" id="valid-item-' + itemId + '">'
      + '<div><strong>' + (nums || summary) + '</strong></div>'
      + '<div style="font-size:14px;color:#64748b">' + betType + ' | ' + stars;
    if (unit) html += ' | ' + unit;
    if (money) html += ' | ' + money;
    html += '</div>'
      + '<button class="assist-fill-btn" onclick="assistPanelFillBtn(this)"'
      + ' data-queue-path="' + panelState.queuePath + '"'
      + ' data-item-index="' + idx + '"'
      + ' data-bet-type="' + betType + '"';
    if (c.manual_candidate_id) html += ' data-manual-id="' + c.manual_candidate_id + '"';
    html += '>輔助填入</button>'
      + '</div>';
  });
  container.innerHTML = html;
}

function renderReview(items) {
  var container = document.getElementById("review-items");
  document.getElementById("review-count").textContent = items.length;
  if (!items.length) { container.innerHTML = '<div class="muted">目前沒有需要人工確認的項目</div>'; return; }
  var html = "";
  items.forEach(function (item, i) {
    var raw = item.raw || item.original_text || item.fragment || "";
    var reason = item.reason || item.error || item.status || "";
    var cid = item.manual_candidate_id || ("rev" + i);
    html += '<div class="item" id="review-item-' + cid + '">'
      + '<div style="font-size:17px;word-break:break-all">' + escapeHtml(raw) + '</div>';
    if (reason) html += '<div style="font-size:13px;color:#b91c1c">' + escapeHtml(reason) + '</div>';
    html += '<button class="btn-manual" onclick="markManualDone(this)"'
      + ' data-id="' + cid + '"'
      + ' data-manual-candidate-id="' + cid + '">已手動下注</button>'
      + '<button class="btn-manual" onclick="markHandled(this)"'
      + ' data-id="' + cid + '">已處理</button>'
      + '</div>';
  });
  container.innerHTML = html;
}

function escapeHtml(s) {
  return String(s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");
}

function markHandled(btn) {
  var cid = btn.getAttribute("data-id");
  var row = document.getElementById("review-item-" + cid);
  if (!row) return;
  row.remove();
  var cnt = document.getElementById("review-count");
  var n = Math.max(0, parseInt(cnt.textContent) - 1);
  cnt.textContent = n;
  if (n === 0) {
    document.getElementById("review-items").innerHTML = '<div class="muted">目前沒有需要人工確認的項目</div>';
  }
}

function markManualDone(btn) {
  var cid = btn.getAttribute("data-manual-candidate-id") || btn.getAttribute("data-id");
  var row = document.getElementById("review-item-" + cid);
  if (!row) return;
  btn.disabled = true;
  fetch("/assist-fill/manual-done", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ manual_candidate_id: cid })
  }).then(function (r) { return r.json(); }).then(function (data) {
    if (data.ok) {
      row.remove();
      var cnt = document.getElementById("review-count");
      var n = Math.max(0, parseInt(cnt.textContent) - 1);
      cnt.textContent = n;
      if (n === 0) {
        document.getElementById("review-items").innerHTML = '<div class="muted">目前沒有需要人工確認的項目</div>';
      }
    } else {
      btn.disabled = false;
      setStatus(data.error || "操作失敗");
    }
  }).catch(function () {
    btn.disabled = false;
    setStatus("操作失敗");
  });
}

function updateCompletedCount() {
  var cnt = document.querySelectorAll(".assist-completed").length;
  document.getElementById("completed-count").textContent = cnt;
  document.getElementById("clear-completed-btn").disabled = (cnt === 0);
}

function clearCompleted() {
  document.querySelectorAll(".assist-completed").forEach(function (el) { el.remove(); });
  updateCompletedCount();
}

function assistPanelFillBtn(btn) {
  var queuePath = btn.getAttribute("data-queue-path") || panelState.queuePath;
  var itemIndex = parseInt(btn.getAttribute("data-item-index"), 10);
  var betType = btn.getAttribute("data-bet-type") || "normal";
  var manualId = btn.getAttribute("data-manual-id") || "";
  btn.disabled = true;
  btn.textContent = "處理中...";
  var body;
  if (manualId) {
    body = JSON.stringify({ manual_candidate_id: manualId, bet_type: betType });
  } else {
    body = JSON.stringify({ queue_path: queuePath, item_index: itemIndex, bet_type: betType });
  }
  fetch("/assist-fill/start", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: body
  }).then(function (r) { return r.json(); }).then(function (data) {
    var row = btn.parentElement;
    var isColumn = (betType === "column" || betType === "zhu_peng" || betType === "zhupeng");
    var reallyOk = isColumn ? (data.ok === true) : (data.ok === true && data.amounts_verified === true && !(data.missing_targets && data.missing_targets.length) && !(data.missing_amount_stars && data.missing_amount_stars.length));
    if (reallyOk && row) {
      row.classList.add("assist-completed");
      btn.textContent = "已填入";
      btn.disabled = true;
      setStatus("已輔助填入，請確認真站");
    } else {
      if (row) row.classList.add("badge-fail");
      btn.disabled = false;
      btn.textContent = "輔助填入";
      var err = data.error || "";
      if (data.missing_amount_stars && data.missing_amount_stars.length) {
        err += " 缺星別: " + data.missing_amount_stars.join(",");
      }
      if (data.missing_targets && data.missing_targets.length) {
        err += " 缺號: " + data.missing_targets.join(",");
      }
      setStatus(err || "輔助填入失敗");
    }
    updateCompletedCount();
  }).catch(function () {
    btn.disabled = false;
    btn.textContent = "輔助填入";
    setStatus("輔助填入失敗");
  });
}
</script>
</body>
</html>"""
