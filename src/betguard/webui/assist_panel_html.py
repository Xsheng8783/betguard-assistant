"""Updated assist panel HTML with larger fonts, Chinese headings, and card-disappear on done."""

ASSIST_PANEL_HTML = """<!doctype html>
<html lang="zh-Hant">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Betguard 牌單助手</title>
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
<h2 style="display:flex;align-items:center;gap:8px">Betguard 牌單助手
<button id="pin-btn" onclick="togglePin()" style="font-size:13px;padding:4px 10px;min-height:unset;background:#f59e0b;color:#fff">釘選視窗</button>
</h2>
<label for="assist-game">彩種</label>
<select id="assist-game"><option value="六合">六合（01–49）</option><option value="539">539（01–39）</option></select>
<textarea id="batch-text" placeholder="貼上牌單..."></textarea>
<button id="createBatchBtn" type="button" class="btn-primary">貼上並建立審核</button>
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
var TEXT_INPUT_SOURCE = "TEXT_INPUT";
var IMAGE_TRANSCRIPTION_TEXT_SOURCE = "IMAGE_TRANSCRIPTION_TEXT";
var fillBusy = false;
var inputRevision = 0;
function invalidateTextResult() {
  inputRevision++;
  panelState.stale = true;
  document.querySelectorAll('.assist-fill-btn').forEach(function(b){ b.disabled=true; });
  setStatus("文字或彩種已變更，請重新解析。");
}
document.getElementById("batch-text").addEventListener("input", invalidateTextResult);
document.getElementById("assist-game").addEventListener("change", invalidateTextResult);
function getAssistGame() { return document.getElementById("assist-game").value; }

function setStatus(msg) {
  var el = document.getElementById("status-msg");
  el.textContent = msg;
  setTimeout(function(){ el.textContent = ""; }, 4000);
}

function createBatch() {
  var ta = document.getElementById("batch-text");
  var btn = document.getElementById("createBatchBtn");
  var text = ta.value;
  var requestedSource = arguments.length ? arguments[0] : TEXT_INPUT_SOURCE;
  var inputSource = requestedSource === IMAGE_TRANSCRIPTION_TEXT_SOURCE
    ? IMAGE_TRANSCRIPTION_TEXT_SOURCE : TEXT_INPUT_SOURCE;

  if (text.trim()) {
    // Textarea has content — use it directly
    _doCreateBatch(text, ta, btn, inputSource);
  } else {
    // Try clipboard
    if (!navigator.clipboard || !navigator.clipboard.readText) {
      setStatus("無法讀取剪貼簿，請手動貼上後再按一次");
      return;
    }
    btn.disabled = true;
    btn.textContent = "讀取剪貼簿...";
    setStatus("讀取剪貼簿...");
    navigator.clipboard.readText().then(function (clipText) {
      var trimmed = (clipText || "").trim();
      if (!trimmed) {
        btn.disabled = false;
        btn.textContent = "貼上並建立審核";
        setStatus("剪貼簿沒有可用文字");
        return;
      }
      ta.value = clipText;
      _doCreateBatch(clipText, ta, btn, inputSource);
    }).catch(function () {
      btn.disabled = false;
      btn.textContent = "貼上並建立審核";
      setStatus("無法讀取剪貼簿，請手動貼上後再按一次");
    });
  }
}

function _doCreateBatch(text, ta, btn, inputSource) {
  if (fillBusy) return;
  var game = getAssistGame(), revision = inputRevision;
  btn.disabled = true;
  btn.textContent = "建立中…";
  setStatus("建立審核中...");
  fetch("/assist-panel/create-batch", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text: text, source: inputSource || TEXT_INPUT_SOURCE, game: game })
  }).then(function (r) { return r.text(); }).then(function (raw) {
    btn.disabled = false;
    btn.textContent = "貼上並建立審核";
    var data;
    try { data = JSON.parse(raw); }
    catch (e) { setStatus("回應不是有效 JSON"); return; }
    if (!data.ok) { setStatus(data.error || "建立審核失敗"); return; }
    if (revision !== inputRevision || game !== getAssistGame() || text !== ta.value) {
      setStatus("文字或彩種已變更，請重新解析。"); return;
    }
    panelState.stale = false;
    panelState.game = game;
    panelState.text = text;
    panelState.queuePath = data.queue_path || "";
    var valid = data.valid_items || data.valid_candidates || data.valid || [];
    var review = data.review_items || data.needs_review || data.invalid_items
      || data.review_candidates || data.invalid_fragments || [];
    panelState.validCandidates = valid;
    panelState.reviewCandidates = review;
    renderValid(valid);
    renderReview(review);
    updateCompletedCount();
    var supported = valid.filter(function(c){return c.fill_supported !== false;}).length;
    setStatus("已建立 " + supported + " 筆可輔助， " + review.length + " 筆需確認"
      + (supported < valid.length ? "，" + (valid.length-supported) + " 筆欄位尚未支援" : ""));
  }).catch(function (e) {
    btn.disabled = false;
    btn.textContent = "貼上並建立審核";
    setStatus("建立審核失敗");
  });
}

document.getElementById("createBatchBtn").addEventListener("click", createBatch);

function renderValid(items, appendFrom) {
  var container = document.getElementById("valid-items");
  if (!items.length) {
    container.innerHTML = '<div class="muted">目前沒有可輔助填入項目</div>';
    updateValidCount(); return;
  }
  var html = "";
  items.forEach(function (c, i) {
    if (appendFrom != null && i < appendFrom) return;
    var itemId = c.manual_candidate_id || ("item-" + i);
    var groups = c.columns || [c.numbers || []];
    var nums = groups.map(function(g){return g.map(function(n){return String(n).padStart(2,"0");}).join(" ");}).join(" × ");
    var stars = (c.stars || []).join("") + "星";
    var unit = c.unit != null ? c.unit + "支" : "";
    var money = c.money != null ? c.money + "元" : "";
    var summary = c.summary || nums;
    var betType = c.bet_type || c.type || "normal";
    var idx = c.index != null ? c.index : (c.item_index != null ? c.item_index : i);
    html += '<div class="item" id="valid-item-' + itemId + '">'
      + '<div><strong>' + escapeHtml(nums || summary) + '</strong></div>'
      + '<div class="semantic-preview">' + escapeHtml(summary) + '</div>'
      + '<div style="font-size:14px;color:#64748b">' + betType + ' | ' + stars;
    if (unit) html += ' | ' + unit;
    if (money) html += ' | ' + money;
    html += '</div>'
      + '<button class="assist-fill-btn" onclick="assistPanelFillBtn(this)"'
      + ' data-queue-path="' + panelState.queuePath + '"'
      + ' data-item-index="' + idx + '"'
      + ' data-bet-type="' + betType + '"'
      + ' data-game="' + (c.game || panelState.game || getAssistGame()) + '"';
    if (c.fill_supported === false) html += ' data-fill-unsupported="true"';
    if (c.fill_supported === false || panelState.stale) html += ' disabled';
    if (c.manual_candidate_id) html += ' data-manual-id="' + c.manual_candidate_id + '"';
    html += '>輔助填入</button>'
      + (c.fill_supported === false ? '<div class="fill-unsupported">' + escapeHtml(c.fill_unsupported_reason) + '</div>' : '')
      + '</div>';
  });
  if (appendFrom != null) {
    container.querySelectorAll(':scope > .muted').forEach(function(el){el.remove();});
    container.insertAdjacentHTML("beforeend", html);
  } else container.innerHTML = html;
  updateValidCount();
}

function updateValidCount() {
  document.getElementById("valid-count").textContent = document.querySelectorAll(
    '#valid-items .item:not(.assist-completed) .assist-fill-btn:not([data-fill-unsupported])'
  ).length;
}

function renderReview(items) {
  var container = document.getElementById("review-items");
  document.getElementById("review-count").textContent = items.length;
  if (!items.length) { container.innerHTML = '<div class="muted">目前沒有需要人工確認的項目</div>'; return; }
  var html = "";
  items.forEach(function (item, i) {
    var raw = item.raw || item.original_text || item.fragment || "";
    var reason = item.reason || item.error || item.status || "";
    var cid = reviewCandidateId(item, i);
    html += '<div class="item" id="review-item-' + cid + '">'
      + '<div style="font-size:17px;word-break:break-all">' + escapeHtml(raw) + '</div>';
    if (reason) html += '<div style="font-size:13px;color:#b91c1c">' + escapeHtml(reason) + '</div>';
    html += '<button class="btn-manual" onclick="editReviewItem(this)"'
      + ' data-id="' + cid + '"'
      + ' data-raw="' + escapeHtml(raw).replace('"', '&quot;') + '">編輯</button>'
      + '<button class="btn-manual" onclick="markManualDone(this)"'
      + ' data-id="' + cid + '"'
      + ' data-manual-candidate-id="' + cid + '">已手動下注</button>'
      + '<button class="btn-manual" onclick="markHandled(this)"'
      + ' data-id="' + cid + '">已處理</button>'
      + '</div>';
  });
  container.innerHTML = html;
}

function reviewCandidateId(item, index) {
  if (!item._review_ui_id) {
    item._review_ui_id = item.manual_candidate_id || ("rev" + index);
  }
  return item._review_ui_id;
}

function removeReviewCandidateFromState(cid) {
  panelState.reviewCandidates = (panelState.reviewCandidates || []).filter(function(item, index) {
    var itemId = reviewCandidateId(item, index);
    return String(itemId) !== String(cid);
  });
}

function escapeHtml(s) {
  return String(s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;").replace(/'/g,"&#39;");
}

var pinState = false;

function togglePin() {
  pinState = !pinState;
  var btn = document.getElementById("pin-btn");
  fetch("/api/window-pin", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ enable: pinState })
  }).then(function (r) { return r.json(); }).then(function (data) {
    if (data.ok) {
      btn.textContent = pinState ? "已釘選" : "釘選視窗";
      btn.style.background = pinState ? "#059669" : "#f59e0b";
      try { localStorage.setItem("betguard_pin", pinState ? "1" : "0"); } catch (_) {}
    } else {
      pinState = !pinState;
      setStatus(data.error || "操作失敗");
    }
  }).catch(function () {
    pinState = !pinState;
  });
}

// Restore pin state on load
(function() {
  try { var v = localStorage.getItem("betguard_pin"); if (v === "1") { pinState = false; togglePin(); } } catch (_) {}
})();

function markHandled(btn) {
  var cid = btn.getAttribute("data-id");
  var row = document.getElementById("review-item-" + cid);
  if (!row) return;
  removeReviewCandidateFromState(cid);
  row.remove();
  updateReviewCount();
}

function editReviewItem(btn) {
  var cid = btn.getAttribute("data-id");
  var raw = btn.getAttribute("data-raw") || "";
  var row = document.getElementById("review-item-" + cid);
  if (!row) return;
  // Replace card content with inline editor
  row.innerHTML = '<textarea id="edit-text-' + cid + '" style="width:100%;min-height:80px;font-size:15px;font-family:monospace;margin-bottom:6px">'
    + escapeHtml(raw) + '</textarea>'
    + '<button class="btn-manual" onclick="submitEdit(&quot;' + cid + '&quot;)">重新解析</button>'
    + '<button class="btn-manual" onclick="cancelEdit(&quot;' + cid + '&quot;)" style="background:#94a3b8">取消</button>';
}

function submitEdit(cid) {
  if (fillBusy) return;
  var ta = document.getElementById("edit-text-" + cid);
  if (!ta) return;
  var newText = ta.value;
  if (!newText.trim()) { setStatus("請輸入牌文"); return; }
  setStatus("重新解析中...");
  var game = getAssistGame(), revision = inputRevision;
  fetch("/manual-reparse", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text: newText, game: game })
  }).then(function (r) { return r.json(); }).then(function (data) {
    if (data.ok) {
      if (revision !== inputRevision || game !== getAssistGame() || ta.value !== newText) {
        setStatus("文字或彩種已變更，請重新解析。"); return;
      }
      // Remove old review card
      var row = document.getElementById("review-item-" + cid);
      if (row) row.remove();
      removeReviewCandidateFromState(cid);
      // Build valid candidate from reparse result
      var newCand = {
        columns: data.columns,
        game: data.game,
        fill_supported: data.fill_supported,
        fill_unsupported_reason: data.fill_unsupported_reason,
        numbers: data.numbers || [],
        stars: data.stars || [],
        amounts: data.amounts || {},
        summary: data.summary || newText,
        bet_type: data.bet_type || data.type || "normal",
        type: data.type || "normal",
        manual_candidate_id: data.manual_candidate_id,
        raw: newText,
        index: panelState.validCandidates ? panelState.validCandidates.length + 1 : 1
      };
      var previousCount = (panelState.validCandidates || []).length;
      var added = data.candidates || [newCand];
      panelState.validCandidates = (panelState.validCandidates || []).concat(added);
      renderValid(panelState.validCandidates, previousCount);
      updateReviewCount();
      setStatus(added.every(function(c){return c.fill_supported !== false;})
        ? "已加入可輔助填入" : "已保留解析結果；部分玩法填入欄位尚未支援。");
    } else {
      setStatus(data.error || data.reason || "解析失敗，仍需人工確認");
    }
  }).catch(function () { setStatus("解析失敗"); });
}

function cancelEdit(cid, originalRaw) {
  var row = document.getElementById("review-item-" + cid);
  if (!row) return;
  // Re-render this single card by rebuilding from panelState
  var found = null;
  if (panelState.reviewCandidates) {
    for (var i = 0; i < panelState.reviewCandidates.length; i++) {
      var item = panelState.reviewCandidates[i];
      var icid = reviewCandidateId(item, i);
      if (icid === cid) { found = item; break; }
    }
  }
  if (found) {
    renderReview(panelState.reviewCandidates);
  } else {
    // Fallback: remove row
    row.remove();
    updateReviewCount();
  }
}

function updateReviewCount() {
  var cnt = document.getElementById("review-count");
  var items = document.getElementById("review-items");
  var itemEls = items.querySelectorAll(".item");
  cnt.textContent = itemEls.length;
  if (itemEls.length === 0) {
    items.innerHTML = '<div class="muted">目前沒有需要人工確認的項目</div>';
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
      removeReviewCandidateFromState(cid);
      row.remove();
      updateReviewCount();
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
  updateValidCount();
}

function clearCompleted() {
  document.querySelectorAll(".assist-completed").forEach(function (el) { el.remove(); });
  updateCompletedCount();
}

function assistPanelFillBtn(btn, refill) {
  if (fillBusy || btn.disabled || panelState.stale) return;
  var game=btn.getAttribute("data-game") || panelState.game || getAssistGame();
  if (game!==getAssistGame() || (panelState.text!=null && panelState.text!==document.getElementById("batch-text").value)) {
    invalidateTextResult(); return;
  }
  var queuePath=btn.getAttribute("data-queue-path") || panelState.queuePath;
  var itemIndex=parseInt(btn.getAttribute("data-item-index"),10);
  var betType=btn.getAttribute("data-bet-type") || "normal";
  var manualId=btn.getAttribute("data-manual-id") || "";
  var operationId=crypto.randomUUID();
  var payload={game:game, bet_type:betType, operation_id:operationId, refill:!!refill};
  if(manualId) payload.manual_candidate_id=manualId;
  else {payload.queue_path=queuePath;payload.item_index=itemIndex;}
  fillBusy=true;btn.disabled=true;btn.textContent="處理中...";
  var aborter=new AbortController();
  var timeout=setTimeout(function(){aborter.abort();},35000);
  var locked=[];
  document.querySelectorAll('#batch-text,#assist-game,#createBatchBtn,#review-items textarea,#review-items button').forEach(function(el){
    locked.push([el,el.disabled]);el.disabled=true;
  });
  fetch("/assist-fill/start",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(payload),signal:aborter.signal})
    .then(function(r){return r.json();}).then(function(data){
      var reallyOk=data.ok===true && data.numbers_verified===true && data.amounts_verified===true && data.game_verified===true;
      btn.textContent="重新填入";
      btn.onclick=function(){assistRefillBtn(btn);};
      if(reallyOk) {
        setStatus("已輔助填入，請核對表單；尚未送出。");
        var row=btn.parentElement;
        if(!row.querySelector('.mark-done-btn')) {
          var done=document.createElement('button');
          done.className='btn-manual mark-done-btn';done.textContent='已下牌';
          ['data-queue-path','data-item-index','data-manual-id'].forEach(function(a){if(btn.hasAttribute(a))done.setAttribute(a,btn.getAttribute(a));});
          done.onclick=function(){markItemDone(done);};row.appendChild(done);
        }
      } else {
        var detail = (data.missing_targets || []).length ? " 缺號：" + data.missing_targets.join(",") : "";
        if((data.missing_amount_stars || []).length) detail += " 缺星別：" + data.missing_amount_stars.join(",");
        setStatus((data.error || "填入或讀回不完整") + detail + "；請核對表單，未自動重試。");
      }
    }).catch(function(){
      btn.textContent="重新填入";btn.onclick=function(){assistRefillBtn(btn);};
      setStatus("連線中斷，填入結果未明；請先核對表單，未自動重試。");
    }).finally(function(){
      clearTimeout(timeout);
      fillBusy=false;btn.disabled=!!panelState.stale;
      locked.forEach(function(pair){pair[0].disabled=pair[1];});
    });
}
function assistRefillBtn(btn) { assistPanelFillBtn(btn, true); }
function markItemDone(btn) {
  var queuePath = btn.getAttribute("data-queue-path") || panelState.queuePath;
  var itemIndex = parseInt(btn.getAttribute("data-item-index"), 10);
  var manualId = btn.getAttribute("data-manual-id") || "";
  var row = btn.parentElement;
  btn.disabled = true;
  btn.textContent = "處理中...";
  var body;
  if (manualId) {
    body = JSON.stringify({ manual_candidate_id: manualId });
  } else {
    body = JSON.stringify({ queue_path: queuePath, item_index: itemIndex });
  }
  fetch("/assist-fill/mark-done", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: body
  }).then(function (r) { return r.json(); }).then(function (data) {
    if (data.ok) {
      if (row) {
        row.classList.add("assist-completed");
        row.querySelectorAll("button").forEach(function(b){b.disabled=true;});
        btn.textContent="已下牌";
        updateCompletedCount();
      }
      setStatus("已標記下牌完成");
    } else {
      btn.disabled = false;
      btn.textContent = "已下牌";
      setStatus(data.error || "操作失敗");
    }
  }).catch(function () {
    btn.disabled = false;
    btn.textContent = "已下牌";
    setStatus("操作失敗");
  });
}
</script>
</body>
</html>"""
