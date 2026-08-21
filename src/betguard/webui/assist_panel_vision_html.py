"""Vision image intake UI — extends assist panel with image mode.

Returns HTML/JS fragment injected into the assist panel page.
"""


def render_vision_ui_section() -> str:
    """Return HTML + JS for the vision image intake panel section."""
    return """
<div class="section" id="vision-section" style="display:none">
  <h3>Betguard 本機輔助流程</h3>
  <div id="mvp-stepper" style="display:grid;grid-template-columns:repeat(3,1fr);gap:6px;margin:8px 0 12px">
    <div id="mvp-step-1" style="padding:8px;border:2px solid #2563eb;border-radius:7px;background:#eff6ff"><strong>1 上傳圖片</strong></div>
    <div id="mvp-step-2" style="padding:8px;border:1px solid #cbd5e1;border-radius:7px"><strong>2 檢查並確認</strong></div>
    <div id="mvp-step-3" style="padding:8px;border:1px solid #cbd5e1;border-radius:7px"><strong>3 輔助填入</strong></div>
  </div>
  <p class="muted" style="font-size:13px;margin-bottom:8px;color:#475569">
    Gemma 先提供手寫建議，PP-OCRv6 只作本機證據；Qwen 只在失敗或您要求第二意見時使用。<br>
    所有 AI 結果都必須由您逐筆確認。輔助填入只會操作 127.0.0.1 本機測試頁，永不送出。
  </p>
  <div id="vision-provider-status" style="font-size:12px;margin-bottom:8px;color:#64748b">正在檢查辨識環境...</div>
  <label style="display:none;font-size:13px;color:#334155;margin-bottom:8px">
    這張牌單的版面：
    <select id="vision-document-mode" style="margin-left:6px;padding:5px 8px;border:1px solid #cbd5e1;border-radius:4px">
      <option value="auto">不確定，保守辨識</option>
      <option value="normal">主要是一般牌</option>
      <option value="column">整張是柱碰</option>
      <option value="mixed">一般／柱碰混合</option>
    </select>
  </label>

  <!-- Upload area -->
  <div id="vision-drop-zone" style="border:2px dashed #cbd5e1;border-radius:8px;padding:20px;text-align:center;cursor:pointer;margin-bottom:8px;background:#f8fafc">
    <div style="font-size:14px;color:#64748b">拖曳圖片至此，或點擊選擇檔案</div>
    <div style="font-size:12px;color:#94a3b8">支援 PNG / JPEG / WebP，上限 10 MiB</div>
    <div style="font-size:12px;color:#94a3b8">也可以 Ctrl+V 貼上剪貼簿圖片</div>
    <input type="file" id="vision-file-input" accept="image/png,image/jpeg,image/webp" style="display:none">
  </div>

  <!-- Preview -->
  <div id="vision-preview" style="display:none;margin-bottom:8px">
    <div style="display:flex;gap:8px;align-items:start">
      <div id="vision-image-stage" style="position:relative;display:inline-block;max-width:100%">
        <img id="vision-preview-img" style="display:block;max-width:100%;max-height:300px;border:1px solid #e2e8f0;border-radius:4px">
        <div id="vision-structure-highlight" aria-hidden="true" style="display:none;position:absolute;pointer-events:none;border:3px solid #f97316;background:rgba(249,115,22,.16);border-radius:4px;box-sizing:border-box"></div>
      </div>
      <div style="font-size:13px;color:#64748b;min-width:160px">
        <div>檔名：<span id="vision-filename">-</span></div>
        <div>類型：<span id="vision-mime">-</span></div>
        <div>尺寸：<span id="vision-dims">-</span></div>
        <div>大小：<span id="vision-size">-</span></div>
        <div>SHA-256：<span id="vision-sha" style="font-size:11px">-</span></div>
        <div style="margin-top:6px">
          <span id="vision-upload-status" style="font-size:12px"></span>
        </div>
        <button id="vision-delete-btn" style="font-size:13px;padding:4px 10px;background:#ef4444;color:#fff;margin-top:6px;display:none" onclick="visionDeleteImage()">刪除圖片</button>
      </div>
    </div>
  </div>

  <!-- Run job button -->
  <label id="vision-qwen-game-label" style="display:block;font-size:13px;color:#334155;margin-bottom:8px">
    遊戲類型：
    <select id="vision-qwen-game" style="margin-left:6px;padding:5px 8px;border:1px solid #cbd5e1;border-radius:4px">
      <option value="539">539</option>
      <option value="六合">六合彩</option>
    </select>
  </label>
  <button id="vision-run-btn" class="btn-primary" style="display:none;margin-bottom:8px" onclick="visionRunJob()">開始 AI 辨識</button>
  <button id="vision-qwen-run-btn" class="btn-primary" style="display:none;margin-bottom:8px;background:#7c3aed" onclick="visionRunQwenJob()">取得 Qwen 第二意見</button>

  <!-- Results -->
  <div id="vision-results" style="display:none;margin-top:8px">
    <h4 style="font-size:15px;margin-bottom:4px">辨識結果（待人工確認）</h4>
    <div id="vision-results-body" style="font-size:14px;max-height:500px;overflow-y:auto;background:#f8fafc;border:1px solid #e2e8f0;border-radius:4px;padding:8px"></div>
  </div>
</div>

<script>
(function() {
  var dropZone = document.getElementById("vision-drop-zone");
  var fileInput = document.getElementById("vision-file-input");
  var uploadedImageId = null;
  var uploadedAidImageId = null;
  var visionQualityPassed = false;
  var qwenConfigured = false;
  var qwenEvidenceResult = null;
  var qwenStructureEvidence = [];
  var ppocrShadowEvidence = null;
  var gemmaShadowEvidence = null;
  var multiModelComparison = null;
  var visionShadowLatency = null;
  var runtimeRoutingResult = null;
  var qwenReviewSession = null;
  var qwenRequestedGame = "";
  var uploadedImageMetadata = null;
  var visionUploadGeneration = 0;
  var qwenAuthorityMutationChain = Promise.resolve();
  var QWEN_REVIEW_STORAGE_KEY = "betguard.vision.review_session_id.v1";

  fetch("/api/vision/v1/providers").then(function(r) { return r.json(); }).then(function(data) {
    var providers = (data && data.providers) || [];
    var qwen = providers.filter(function(p) { return p.id === "qwen-dashscope"; })[0];
    var status = document.getElementById("vision-provider-status");
    qwenConfigured = !!(qwen && qwen.configured);
    document.getElementById("vision-qwen-run-btn").disabled = !qwenConfigured;
    var qwenStatus = qwenConfigured ? "Qwen fallback／第二意見可用" : "Qwen 未設定（仍可用 Gemma／PP 或完全人工輸入）";
    status.textContent = "Gemma primary｜PP 本機證據｜" + qwenStatus + "｜Codex runtime=0";
    if (qwenConfigured) {
      status.style.color = "#15803d";
    } else {
      status.style.color = "#b45309";
    }
  }).catch(function() {
    document.getElementById("vision-provider-status").textContent = "無法讀取辨識環境狀態。";
  });

  // Click to select file
  dropZone.addEventListener("click", function() { fileInput.click(); });
  fileInput.addEventListener("change", function() {
    if (fileInput.files.length) visionUpload(fileInput.files[0]);
  });

  // Drag/drop
  dropZone.addEventListener("dragover", function(e) { e.preventDefault(); });
  dropZone.addEventListener("drop", function(e) {
    e.preventDefault();
    var f = e.dataTransfer.files[0];
    if (f) visionUpload(f);
  });

  // Clipboard paste (Ctrl+V)
  document.addEventListener("paste", function(e) {
    if (document.getElementById("vision-section").style.display === "none") return;
    var items = e.clipboardData.items;
    for (var i = 0; i < items.length; i++) {
      if (items[i].type.indexOf("image/") === 0) {
        visionUpload(items[i].getAsFile());
        break;
      }
    }
  });

  // Escape helper
  function esc(s) {
    return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;")
      .replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }

  // Upload
  window.visionUpload = function(file) {
    var uploadGeneration = ++visionUploadGeneration;
    _resetQwenReviewSession();
    qwenEvidenceResult = null;
    qwenStructureEvidence = [];
    ppocrShadowEvidence = null;
    gemmaShadowEvidence = null;
    multiModelComparison = null;
    visionShadowLatency = null;
    runtimeRoutingResult = null;
    uploadedImageMetadata = null;
    uploadedImageId = null;
    uploadedAidImageId = null;
    document.getElementById("vision-results").style.display = "none";
    document.getElementById("vision-run-btn").style.display = "none";
    document.getElementById("vision-qwen-run-btn").style.display = "none";
    var status = document.getElementById("vision-upload-status");
    status.textContent = "上傳中...";
    status.style.color = "#64748b";

    var reader = new FileReader();
    reader.onload = function() {
      var bytes = new Uint8Array(reader.result);
      fetch("/api/vision/v1/images", {
        method: "POST",
        headers: {
          "Content-Type": file.type || "application/octet-stream",
          "X-Filename": encodeURIComponent(file.name || "image")
        },
        body: bytes
      }).then(function(r) { return r.json(); })
      .then(function(data) {
        if (uploadGeneration !== visionUploadGeneration) return;
        if (!data.ok) {
          status.textContent = "上傳失敗: " + (data.error && data.error.message || "未知錯誤");
          status.style.color = "#ef4444";
          return;
        }
        uploadedImageId = data.image.image_id;
        uploadedImageMetadata = _cloneJson(data.image);
        status.textContent = "已上傳";
        status.style.color = "#16a34a";

        document.getElementById("vision-filename").textContent = esc(data.image.original_filename || "-");
        document.getElementById("vision-mime").textContent = data.image.mime_type;
        document.getElementById("vision-dims").textContent = data.image.width + " × " + data.image.height;
        document.getElementById("vision-size").textContent = _formatBytes(data.image.byte_size);
        document.getElementById("vision-sha").textContent = (data.image.sha256 || "").substring(0, 12) + "...";
        document.getElementById("vision-preview-img").src = "/api/vision/v1/images/" + data.image.image_id;
        document.getElementById("vision-preview").style.display = "block";
        _mvpSetStep(1);
        document.getElementById("vision-delete-btn").style.display = "inline-block";
        document.getElementById("vision-qwen-run-btn").style.display = "inline-block";
        document.getElementById("vision-qwen-run-btn").disabled = !qwenConfigured;
        _createVisionAidImage(file, data.image).then(function(aidId) {
          if (uploadGeneration !== visionUploadGeneration) return;
          uploadedAidImageId = aidId;
          if (aidId) {
            status.textContent = "已上傳；已建立低解析多欄放大辨識圖";
          }
          document.getElementById("vision-run-btn").style.display = "inline-block";
        }).catch(function() {
          if (uploadGeneration !== visionUploadGeneration) return;
          uploadedAidImageId = null;
          status.textContent = "已上傳；放大辨識圖建立失敗，將使用原圖";
          status.style.color = "#b45309";
          document.getElementById("vision-run-btn").style.display = "inline-block";
        });
        document.getElementById("vision-results").style.display = "none";
      }).catch(function() {
        if (uploadGeneration !== visionUploadGeneration) return;
        status.textContent = "上傳失敗（網路錯誤）";
        status.style.color = "#ef4444";
      });
    };
    reader.readAsArrayBuffer(file);
  };

  function _uploadVisionBytes(bytes, mimeType, filename) {
    return fetch("/api/vision/v1/images", {
      method: "POST",
      headers: {
        "Content-Type": mimeType,
        "X-Filename": encodeURIComponent(filename)
      },
      body: bytes
    }).then(function(r) { return r.json(); }).then(function(data) {
      if (!data.ok) throw new Error("aid upload failed");
      return data.image.image_id;
    });
  }

  function _createVisionAidImage(file, metadata) {
    var longSide = Math.max(metadata.width || 0, metadata.height || 0);
    var portrait = (metadata.height || 0) > (metadata.width || 0);
    if (!portrait || longSide >= 900) return Promise.resolve(null);

    return new Promise(function(resolve, reject) {
      var image = new Image();
      var objectUrl = URL.createObjectURL(file);
      image.onload = function() {
        try {
          var canvas = document.createElement("canvas");
          canvas.width = 1800;
          canvas.height = 1420;
          var ctx = canvas.getContext("2d");
          ctx.fillStyle = "#ffffff";
          ctx.fillRect(0, 0, canvas.width, canvas.height);
          ctx.fillStyle = "#111827";
          ctx.font = "24px sans-serif";
          ctx.fillText("FULL", 20, 30);

          var fullWidth = 390;
          var fullHeight = Math.min(1320, Math.round(fullWidth * image.height / image.width));
          ctx.drawImage(image, 20, 45, fullWidth, fullHeight);

          var starts = [0, 0.29, 0.58];
          var labels = ["LEFT DETAIL", "CENTER DETAIL", "RIGHT DETAIL"];
          for (var i = 0; i < starts.length; i++) {
            var sourceX = Math.round(image.width * starts[i]);
            var sourceWidth = Math.min(Math.round(image.width * 0.42), image.width - sourceX);
            var targetX = 430 + i * 455;
            var targetWidth = 425;
            var targetHeight = Math.min(1350, Math.round(targetWidth * image.height / sourceWidth));
            ctx.fillText(labels[i], targetX, 30);
            ctx.drawImage(
              image,
              sourceX, 0, sourceWidth, image.height,
              targetX, 45, targetWidth, targetHeight
            );
          }

          canvas.toBlob(function(blob) {
            URL.revokeObjectURL(objectUrl);
            if (!blob) { reject(new Error("aid image encode failed")); return; }
            blob.arrayBuffer().then(function(buffer) {
              return _uploadVisionBytes(
                new Uint8Array(buffer),
                "image/png",
                "vision-aid-" + (file.name || "image") + ".png"
              );
            }).then(resolve).catch(reject);
          }, "image/png");
        } catch (error) {
          URL.revokeObjectURL(objectUrl);
          reject(error);
        }
      };
      image.onerror = function() {
        URL.revokeObjectURL(objectUrl);
        reject(new Error("aid image decode failed"));
      };
      image.src = objectUrl;
    });
  }

  // Delete
  window.visionDeleteImage = function() {
    if (!uploadedImageId) return;
    _resetQwenReviewSession();
    qwenEvidenceResult = null;
    qwenStructureEvidence = [];
    ppocrShadowEvidence = null;
    gemmaShadowEvidence = null;
    multiModelComparison = null;
    visionShadowLatency = null;
    uploadedImageMetadata = null;
    var deleteIds = [uploadedImageId, uploadedAidImageId].filter(function(id) { return !!id; });
    Promise.all(deleteIds.map(function(id) {
      return fetch("/api/vision/v1/images/" + id, { method: "DELETE" });
    }))
      .then(function() {
        uploadedImageId = null;
        uploadedAidImageId = null;
        document.getElementById("vision-preview").style.display = "none";
        document.getElementById("vision-run-btn").style.display = "none";
        document.getElementById("vision-qwen-run-btn").style.display = "none";
        document.getElementById("vision-results").style.display = "none";
        document.getElementById("vision-upload-status").textContent = "";
        fileInput.value = "";
      });
  };

  function _runtimeRoutingFromResponse(data) {
    if (!data || typeof data !== "object") return null;
    return data.routing_result ||
      (data.result && data.result.routing_result) ||
      null;
  }

  var RUNTIME_SECOND_OPINION_TIMEOUT_MS = 75000;

  function _runtimeFetchJsonWithTimeout(url, options, timeoutMs) {
    var controller = typeof AbortController === "function" ? new AbortController() : null;
    var requestOptions = Object.assign({}, options || {});
    if (controller) requestOptions.signal = controller.signal;
    var timer = null;
    var timeout = new Promise(function(_resolve, reject) {
      timer = setTimeout(function() {
        if (controller) controller.abort();
        var error = new Error("runtime reader request timed out");
        error.name = "AbortError";
        reject(error);
      }, timeoutMs);
    });
    var request = fetch(url, requestOptions).then(function(response) {
      return response.json();
    });
    return Promise.race([request, timeout]).then(function(value) {
      if (timer) clearTimeout(timer);
      return value;
    }, function(error) {
      if (timer) clearTimeout(timer);
      throw error;
    });
  }

  // Run job
  window.visionRunJob = function() {
    if (!uploadedImageId) return;
    var btn = document.getElementById("vision-run-btn");
    btn.disabled = true;
    btn.textContent = "AI 辨識中；正在檢查不確定區域...";
    fetch("/api/vision/v1/jobs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        image_id: uploadedImageId,
        aided_image_id: uploadedAidImageId || "",
        provider_id: "runtime-reader-router",
        game: document.getElementById("vision-qwen-game").value || "539",
        second_opinion_requested: false,
        document_mode: document.getElementById("vision-document-mode").value || "auto"
      })
    }).then(function(r) { return r.json(); })
    .then(function(data) {
      btn.disabled = false;
      btn.textContent = "開始 AI 辨識";
      if (!data.ok) {
        _renderRuntimeReaderFailure();
        return;
      }
      var routing = _runtimeRoutingFromResponse(data);
      if (!routing) { _renderRuntimeReaderFailure(); return; }
      _renderRuntimeReaderRouting(routing);
    }).catch(function() {
      btn.disabled = false;
      btn.textContent = "開始 AI 辨識";
      _renderRuntimeReaderFailure();
    });
  };

  // Explicit second opinion: the same router remains in control and allows at
  // most one Qwen request.  It never overwrites the Human Answer automatically.
  window.visionRunQwenJob = function() {
    if (!uploadedImageId) return;
    var btn = document.getElementById("vision-qwen-run-btn");
    var gameSelect = document.getElementById("vision-qwen-game");
    var selectedGame = gameSelect ? gameSelect.value : "";
    if (selectedGame !== "539" && selectedGame !== "六合") {
      _renderQwenFailure("請先明確選擇 539 或六合彩");
      return;
    }
    qwenRequestedGame = selectedGame;
    btn.disabled = true;
    btn.textContent = "取得第二意見中...";
    _runtimeFetchJsonWithTimeout("/api/vision/v1/jobs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        image_id: uploadedImageId,
        provider_id: "runtime-reader-router",
        game: selectedGame,
        second_opinion_requested: true
      })
    }, RUNTIME_SECOND_OPINION_TIMEOUT_MS).then(function(data) {
      btn.disabled = !qwenConfigured;
      btn.textContent = "取得 Qwen 第二意見";
      var routing = _runtimeRoutingFromResponse(data);
      // Backward-compatible rendering is retained for deterministic Gate 3A
      // fixtures. Production sends the runtime-router envelope above.
      if (data.ok && !routing && data.result && data.result.status === "completed") {
        qwenStructureEvidence = Array.isArray(data.structure_evidence) ? data.structure_evidence : [];
        gemmaShadowEvidence = data.gemma_shadow_evidence || null;
        ppocrShadowEvidence = data.shadow_evidence || null;
        if (qwenReviewSession && Array.isArray(qwenReviewSession.structures) && qwenReviewSession.structures.length) {
          qwenEvidenceResult = _cloneJson(data.result);
          qwenReviewSession.second_opinion_evidence = _cloneJson(data.result);
          _renderQwenReviewSession();
          _renderRuntimeSecondOpinionBanner(true);
        } else {
          _renderQwenEvidence(data.result);
        }
        return;
      }
      if (data.ok && !routing && data.result && data.result.status === "failed") {
        gemmaShadowEvidence = data.gemma_shadow_evidence || null;
        ppocrShadowEvidence = data.shadow_evidence || null;
        _renderQwenSecondOpinionFailure("Qwen response unavailable", data);
        return;
      }
      if (!data.ok || !routing) {
        _renderQwenSecondOpinionFailure("Qwen response unavailable", data);
        return;
      }
      _renderRuntimeReaderSecondOpinion(routing);
    }).catch(function(error) {
      btn.disabled = !qwenConfigured;
      btn.textContent = "取得 Qwen 第二意見";
      _renderQwenSecondOpinionFailure(
        error && error.name === "AbortError" ? "Qwen request timed out" : "Qwen request failed",
        {}
      );
    });
  };

  function _mvpSetStep(step) {
    for (var i = 1; i <= 3; i++) {
      var node = document.getElementById("mvp-step-" + i);
      if (!node) continue;
      node.style.border = i === step ? "2px solid #2563eb" : "1px solid #cbd5e1";
      node.style.background = i === step ? "#eff6ff" : "#fff";
    }
  }

  function _renderRuntimeReaderFailure() {
    runtimeRoutingResult = null;
    gemmaShadowEvidence = null;
    ppocrShadowEvidence = null;
    _renderQwenFailure(
      "runtime readers unavailable",
      "AI建議不可用，仍可手動輸入。",
      {},
      "AI 辨識失敗"
    );
    _mvpSetStep(2);
  }

  function _renderRuntimeSecondOpinionBanner(success) {
    var host = document.getElementById("qwen-review-session");
    if (!host || !host.parentNode) return;
    var previous = document.getElementById("runtime-second-opinion-status");
    if (previous && previous.parentNode) previous.parentNode.removeChild(previous);
    var banner = document.createElement("div");
    banner.id = "runtime-second-opinion-status";
    banner.style.cssText = success
      ? "padding:7px;background:#eff6ff;color:#1e40af;font-weight:700"
      : "padding:7px;background:#fef2f2;color:#991b1b;font-weight:700";
    banner.textContent = success
      ? "Qwen 第二意見已加入進階資訊；Human Answer 未變更。"
      : "Qwen 第二意見失敗；既有 Human Answer 未變更。";
    host.parentNode.insertBefore(banner, host);
  }

  function _renderQwenSecondOpinionFailure(message, payload) {
    if (qwenReviewSession && Array.isArray(qwenReviewSession.structures) && qwenReviewSession.structures.length) {
      qwenReviewSession.second_opinion_error = String(message || "Qwen response unavailable");
      _renderQwenReviewSession();
      _renderRuntimeSecondOpinionBanner(false);
      return;
    }
    _renderQwenFailure(
      message || "Qwen response unavailable",
      "AI建議不可用，仍可手動輸入。",
      payload || {},
      "Qwen 第二意見失敗"
    );
  }

  function _runtimeCard(draft, item, index, source, qwenEvidence) {
    var draftClassification = String(draft.draft_classification ||
      (draft.provisional ? "AI_UNCERTAIN" : "SAFE_DRAFT"));
    var provisional = draftClassification === "AI_UNCERTAIN";
    var layout = String(draft.layout_suggestion || (item && item.layout_guess) || "unclear");
    var suggestedGroups = Array.isArray(draft.number_groups_suggestion)
      ? _cloneJson(draft.number_groups_suggestion) : null;
    var suggestedRules = Array.isArray(draft.multiplier_rules_suggestion)
      ? _cloneJson(draft.multiplier_rules_suggestion) : null;
    var staged = {
      number_groups: source === "gemma4-26b-shadow" ? (suggestedGroups || _gemmaNumberGroups(item || {}, {staged_structure:{layout:layout}})) : [],
      multiplier_rules: source === "gemma4-26b-shadow" ? (suggestedRules || _gemmaMultiplierRules(item || {})) : [],
      layout: layout === "column" ? "column_bet" : (layout === "normal" ? "normal_row" : "unknown"),
      continuation: String(draft.continuation_suggestion || "").toLowerCase() === "yes",
      special_text: draft.special_play_raw === "none" ? "" : String(draft.special_play_raw || ""),
      cancelled: String(draft.cancelled_suggestion || "").toLowerCase() === "yes"
    };
    var sourceStatus = provisional ? "incomplete" :
      (staged.number_groups.length && staged.multiplier_rules.length && !draft.uncertain ? "consistent" : "incomplete");
    var cropSuggestion = draft.crop_reread_suggestion
      ? _cloneJson(draft.crop_reread_suggestion) : null;
    var cropStatus = String(draft.crop_reread_status || "");
    return {
      human_bet_id: _qwenHumanBetId(index),
      structure_id: "RUNTIME-" + String(index + 1).padStart(3, "0"),
      primary_line_id: "", source_line_ids: [], source_status: sourceStatus,
      review_state: "pending", model_candidate: {}, original_structure: _cloneJson(staged),
      staged_structure: _cloneJson(staged),
      draft_classification: draftClassification, provisional_review: provisional,
      needs_review: provisional || draft.needs_review === true,
      warnings: (draft.uncertain || provisional) ? ["machine_suggestion_uncertain"] : [],
      crop_reread_status:cropStatus,
      crop_reread_geometry:_cloneJson(draft.crop_reread_geometry || null),
      crop_reread_suggestion:cropSuggestion,
      crop_reread_conflicts:_cloneJson(draft.crop_reread_conflicts || []),
      ai_recheck_conflict:draft.ai_recheck_conflict === true,
      evidence: {raw_text: draft.raw_text || ""}, edit_text: draft.raw_text || "", reparse_preview: null,
      preview_error: "", manual_edits: [], field_corrections: [],
      evidence_sources: {
        qwen: qwenEvidence ? {source:"Qwen second opinion", raw:_cloneJson(qwenEvidence)} : null,
        gemma: item ? Object.assign({source:"Gemma suggestion"}, _cloneJson(item)) : null,
        crop_reread: cropSuggestion ? {
          source:"Gemma local crop re-read", status:cropStatus,
          suggestion:_cloneJson(cropSuggestion),
          geometry:_cloneJson(draft.crop_reread_geometry || null),
          conflicts:_cloneJson(draft.crop_reread_conflicts || [])
        } : null,
        ppocr: ppocrShadowEvidence ? {source:"PP evidence"} : null,
        codex: null,
        human_answer: {source:"Human Answer", human_confirmed:false, staged_structure:_cloneJson(staged)}
      },
      field_sources: {
        numbers:{source:source === "gemma4-26b-shadow" ? "Gemma suggestion" : "Human Answer",human_confirmed:false},
        multiplier:{source:source === "gemma4-26b-shadow" ? "Gemma suggestion" : "Human Answer",human_confirmed:false},
        layout:{source:source === "gemma4-26b-shadow" ? "Gemma suggestion" : "Human Answer",human_confirmed:false}
      },
      suggestion_adoptions: [], blocking_resolved_by_human: sourceStatus === "consistent", human_confirmed:false
    };
  }

  function _renderRuntimeReaderRouting(routing) {
    runtimeRoutingResult = _cloneJson(routing);
    gemmaShadowEvidence = routing.gemma_evidence || null;
    ppocrShadowEvidence = routing.pp_evidence || null;
    qwenEvidenceResult = routing.qwen_evidence && routing.qwen_evidence.recognition_result || null;
    var seed = routing.review_seed || {};
    var drafts = Array.isArray(seed.review_cards) ? seed.review_cards :
      (Array.isArray(seed.bet_drafts) ? seed.bet_drafts :
      (Array.isArray(seed.draft_items) ? seed.draft_items : []));
    var gemmaItems = gemmaShadowEvidence && Array.isArray(gemmaShadowEvidence.items) ? gemmaShadowEvidence.items : [];
    var gemmaByEvidenceId = Object.create(null);
    gemmaItems.forEach(function(item) {
      if (item && item.evidence_id) gemmaByEvidenceId[String(item.evidence_id)] = item;
    });
    var cards = drafts.map(function(draft, index) {
      var item = draft && draft.source_evidence_id
        ? gemmaByEvidenceId[String(draft.source_evidence_id)] || null : null;
      return _runtimeCard(draft || {}, item, index, routing.selected_prefill_source, routing.qwen_evidence || null);
    });
    qwenReviewSession = {
      schema_version:"vision-review-session-v1",
      review_session_id:"vision-review-" + String(uploadedImageId || "unknown") + "-" + String(Date.now()),
      game:(document.getElementById("vision-qwen-game") || {}).value || "539",
      source_image_id:String(uploadedImageId || ""),
      image_sha256:String((uploadedImageMetadata && uploadedImageMetadata.sha256) || routing.image_sha256 || ""),
      structures:cards, unlinked_gemma_items:_cloneJson(seed.unresolved_machine_fragments || []), active_structure_id:cards.length ? cards[0].structure_id : null,
      show_pending_only:false, candidate_preview:null, boundary_signal:null,
      human_answer_revision:null, human_answer_hash:"", server_state:"saving", server_error:null,
      server_candidate:null, runtime_routing:_cloneJson(routing), created_at:new Date().toISOString(),
      safety:{human_confirmation_required:true,auto_apply:false,auto_confirm:false,auto_submit:false}
    };
    var status = routing.selected_prefill_source
      ? (seed.partial_machine_read
          ? "AI 已讀到部分投注，仍有內容需要人工補充。"
          : "AI 建議已預填；每一筆仍需人工確認。")
      : "AI建議不可用，仍可手動輸入。";
    var cropDiagnostics = seed.machine_read_diagnostics || {};
    var cropSelectedCount = Number(cropDiagnostics.crop_reread_selected_count || 0);
    var cropAcceptedCount = Number(cropDiagnostics.crop_reread_accepted_count || 0);
    var cropNotice = cropSelectedCount > 0
      ? '<div id="runtime-crop-reread-notice" style="padding:7px;background:#f0fdf4;color:#166534;font-weight:700">AI 已二次檢查 ' +
        esc(String(cropSelectedCount)) + ' 個不確定區域；取得 ' +
        esc(String(cropAcceptedCount)) + ' 筆放大影像建議。</div>' : '';
    var unresolvedCount = Array.isArray(seed.unresolved_machine_fragments)
      ? seed.unresolved_machine_fragments.length : 0;
    var fragmentNotice = unresolvedCount > 0
      ? '<div id="runtime-unresolved-fragment-notice" style="padding:7px;background:#fff7ed;color:#9a3412;font-weight:700">AI 另外讀到 ' +
        esc(String(unresolvedCount)) + ' 個未能安全組成投注的片段，請檢查。</div>' : '';
    document.getElementById("vision-results-body").innerHTML =
      '<div id="runtime-reader-status" style="padding:7px;background:#eff6ff;color:#1e40af;font-weight:700">' + esc(status) + '</div>' +
      cropNotice +
      fragmentNotice +
      '<div id="qwen-review-session"></div><details id="runtime-reader-advanced" style="margin-top:8px"><summary>進階資訊</summary><pre style="white-space:pre-wrap">' +
      esc(JSON.stringify({routing_decision:routing.routing_decision, fallback_reason:routing.fallback_reason, counters:routing.model_call_counters, cache:routing.cache_status, machine_read_diagnostics:seed.machine_read_diagnostics || {}, unresolved_machine_fragments:seed.unresolved_machine_fragments || []}, null, 2)) +
      '</pre>' + _renderPpocrShadowEvidence() + _renderGemmaShadowEvidence() + '</details>';
    document.getElementById("vision-results").style.display = "block";
    _mvpSetStep(2);
    _qwenCreateAuthorityReview();
    _renderQwenReviewSession();
  }

  function _renderRuntimeReaderSecondOpinion(routing) {
    var hasExistingHumanAnswer = qwenReviewSession &&
      Array.isArray(qwenReviewSession.structures) &&
      qwenReviewSession.structures.length > 0;
    if (!hasExistingHumanAnswer) {
      _renderRuntimeReaderRouting(routing);
    } else {
      runtimeRoutingResult = _cloneJson(routing);
      gemmaShadowEvidence = routing.gemma_evidence || gemmaShadowEvidence || null;
      ppocrShadowEvidence = routing.pp_evidence || ppocrShadowEvidence || null;
      qwenEvidenceResult = routing.qwen_evidence && routing.qwen_evidence.recognition_result || null;
      qwenReviewSession.runtime_routing = _cloneJson(routing);
      qwenReviewSession.second_opinion_evidence = _cloneJson(routing.qwen_evidence || null);
      qwenReviewSession.field_conflicts = _cloneJson(routing.field_conflicts || []);
      _renderQwenReviewSession();
    }
    var qwenEvidence = routing.qwen_evidence || null;
    _renderRuntimeSecondOpinionBanner(!!qwenEvidence && qwenEvidence.status === "completed");
  }

  function _renderQwenFailure(message) {
    var payload = arguments.length > 2 && arguments[2] ? arguments[2] : {};
    var preservedPp = payload.shadow_evidence || ppocrShadowEvidence || null;
    var preservedGemma = payload.gemma_shadow_evidence || gemmaShadowEvidence || null;
    var preservedComparison = payload.multi_model_comparison || multiModelComparison || null;
    var preservedLatency = payload.vision_latency || visionShadowLatency || null;
    var gameSelect = document.getElementById("vision-qwen-game");
    var failureGame = qwenRequestedGame || (gameSelect && gameSelect.value) || "539";
    _resetQwenReviewSession();
    qwenEvidenceResult = null;
    qwenStructureEvidence = [];
    ppocrShadowEvidence = preservedPp;
    gemmaShadowEvidence = preservedGemma;
    multiModelComparison = preservedComparison;
    visionShadowLatency = preservedLatency;
    var userMessage = arguments.length > 1 ? arguments[1] : "";
    var safeUserMessage = userMessage ||
      "AI 未能完整讀取這張圖片。\\n可以重新辨識或改用手動輸入。";
    var failureTitle = arguments.length > 3 && arguments[3]
      ? String(arguments[3]) : "Qwen 第二意見失敗";
    document.getElementById("vision-results-body").innerHTML =
      '<div id="qwen-evidence-status" style="color:#b91c1c;font-weight:700">' + esc(failureTitle) + '</div>' +
      '<div id="qwen-failure-message" style="color:#991b1b;margin-top:6px">' +
        esc(safeUserMessage).replace(/\\n/g, '<br>') + '</div>' +
      '<details class="qwen-failure-details" style="font-size:11px;color:#64748b;margin-top:6px"><summary>進階資訊</summary><pre style="white-space:pre-wrap">' +
        esc(message || "invalid response") + '</pre></details>' +
      '<div style="font-size:11px;color:#64748b;margin-top:6px">needs_review｜auto_confirm=false｜auto_submit=false</div>' +
      '<div id="qwen-review-session"></div>' +
      '<details id="qwen-advanced-evidence" style="font-size:12px;color:#475569;margin-top:10px"><summary>進階證據</summary>' +
      _renderPpocrShadowEvidence() + _renderGemmaShadowEvidence() + '</details>';
    _createQwenFailureReviewSession(failureGame, {message: String(message || "invalid response")});
    document.getElementById("vision-results").style.display = "block";
    _renderQwenReviewSession();
  }

  function _qwenEvidenceByLineId(qwenResponse) {
    var evidenceByLineId = Object.create(null);
    var sections = qwenResponse && Array.isArray(qwenResponse.sections)
      ? qwenResponse.sections : [];
    for (var s = 0; s < sections.length; s++) {
      var section = sections[s] || {};
      var rows = Array.isArray(section.rows) ? section.rows : [];
      for (var r = 0; r < rows.length; r++) {
        var lineId = "S" + String(s + 1).padStart(2, "0") +
          "-L" + String(r + 1).padStart(2, "0");
        evidenceByLineId[lineId] = {
          row: rows[r] || {},
          shared_multiplier: section.shared_multiplier
        };
      }
    }
    return evidenceByLineId;
  }

  function _qwenReconstructionByLineId(structureEvidence) {
    var reconstructionByLineId = Object.create(null);
    var items = Array.isArray(structureEvidence) ? structureEvidence : [];
    for (var i = 0; i < items.length; i++) {
      var item = items[i] || {};
      var lineId = String(item.line_id || "");
      if (lineId && !Object.prototype.hasOwnProperty.call(reconstructionByLineId, lineId)) {
        reconstructionByLineId[lineId] = item;
      }
    }
    return reconstructionByLineId;
  }

  function _qwenComparisonLabel(status) {
    var labels = {
      consistent: "結構一致",
      divergent: "結構分歧",
      incomplete: "證據不足",
      unsupported: "不支援"
    };
    return labels[status] || "證據不足";
  }

  function _cloneJson(value) {
    if (value == null) return value;
    return JSON.parse(JSON.stringify(value));
  }

  function _resetQwenReviewSession() {
    qwenReviewSession = null;
    qwenRequestedGame = "";
    var gameSelect = document.getElementById("vision-qwen-game");
    if (gameSelect) gameSelect.disabled = false;
    var highlight = document.getElementById("vision-structure-highlight");
    if (highlight) highlight.style.display = "none";
  }

  function _qwenHumanStatus(status) {
    var labels = {
      consistent: "AI 結構一致，仍請確認",
      divergent: "AI 與規則結果不同，請檢查",
      incomplete: "資料需要人工檢查",
      unsupported: "此玩法目前需要人工處理"
    };
    return labels[status] || labels.incomplete;
  }

  function _qwenLayoutLabel(layout) {
    var labels = {
      normal: "一般牌",
      normal_row: "一般牌",
      column: "柱碰",
      column_bet: "柱碰",
      mixed: "混合版面"
    };
    return labels[layout] || "需要人工判斷";
  }

  function _qwenPrimaryStructures(structureEvidence) {
    var items = Array.isArray(structureEvidence) ? structureEvidence : [];
    var primaries = [];
    var seen = Object.create(null);
    for (var i = 0; i < items.length; i++) {
      var item = items[i] || {};
      if (item.line_role === "continuation" || item.line_role === "unlinked") continue;
      if (item.primary_line_id && item.line_id && item.primary_line_id !== item.line_id) continue;
      var structureId = String(item.structure_id || item.primary_line_id || item.line_id || "");
      if (!structureId || seen[structureId]) continue;
      seen[structureId] = true;
      primaries.push(item);
    }
    return primaries;
  }

  function _qwenInitialReviewStructure(item) {
    // Review cards always start from deterministic reconstructed evidence.
    // A technical incomplete/divergent status changes only the human-facing
    // warning; it must never erase usable number groups or multiplier rules.
    var candidate = item && item.reconstructed_candidate;
    var structure = candidate && typeof candidate === "object" && !Array.isArray(candidate)
      ? _cloneJson(candidate) : {};
    structure.number_groups = Array.isArray(candidate && candidate.number_groups)
      ? _cloneJson(candidate.number_groups) : [];
    structure.multiplier_rules = Array.isArray(candidate && candidate.multiplier_rules)
      ? _cloneJson(candidate.multiplier_rules) : [];
    return structure;
  }

  function _gemmaEvidenceByStructureId(evidence) {
    var items = evidence && evidence.status === "completed" && Array.isArray(evidence.items)
      ? evidence.items : [];
    var grouped = Object.create(null);
    for (var i = 0; i < items.length; i++) {
      var item = items[i] || {};
      var structureId = String(item.structure_id || item.target_structure_id || "");
      if (!/^S\d+$/.test(structureId)) continue;
      if (!grouped[structureId]) grouped[structureId] = [];
      grouped[structureId].push(item);
    }
    var exact = Object.create(null);
    Object.keys(grouped).forEach(function(structureId) {
      // Multiple claims for one structure are ambiguous and intentionally
      // remain unlinked.  There is no positional or text fallback.
      if (grouped[structureId].length === 1) exact[structureId] = _cloneJson(grouped[structureId][0]);
    });
    return exact;
  }

  function _gemmaUnlinkedEvidence(evidence) {
    var items = evidence && evidence.status === "completed" && Array.isArray(evidence.items)
      ? evidence.items : [];
    return items.filter(function(item) {
      var structureId = String((item || {}).structure_id || (item || {}).target_structure_id || "");
      return !/^S\d+$/.test(structureId);
    }).map(_cloneJson);
  }

  function _reviewEvidenceSources(item, gemmaItem) {
    return {
      qwen: {
        source: "Qwen evidence",
        model_candidate: _cloneJson(item.model_candidate || {}),
        reconstructed_candidate: _cloneJson(item.reconstructed_candidate || {})
      },
      gemma: gemmaItem ? Object.assign({source: "Gemma suggestion"}, _cloneJson(gemmaItem)) : null,
      ppocr: null,
      codex: null,
      human_answer: {
        source: "Human Answer",
        human_confirmed: false,
        field_corrections: []
      }
    };
  }

  function _qwenIsCancelledStructure(structure) {
    structure = structure || {};
    return structure.cancelled === true || structure.cancelled === "yes";
  }

  function _qwenHumanBetId(index) {
    return "H-" + String(index + 1).padStart(3, "0");
  }

  function _qwenSpecialPlay(structure, resolved) {
    structure = structure || {};
    var values = {
      tail: structure.tail == null ? null : structure.tail,
      car: structure.car == null ? null : structure.car,
      half_car: structure.half_car == null ? null : structure.half_car,
      each: structure.each == null ? null : structure.each,
      special_text: structure.special_text == null ? null : structure.special_text
    };
    var hasValue = Object.keys(values).some(function(key) {
      return values[key] !== null && values[key] !== "" && values[key] !== false;
    });
    return {
      kind: hasValue ? "structured_human_play" : "none",
      raw_text: hasValue ? JSON.stringify(values) : null,
      scope: structure.scope == null || structure.scope === "" ? null : String(structure.scope),
      resolved: resolved === true
    };
  }

  function _qwenAuthorityBet(card, index) {
    var structure = (card || {}).staged_structure || {};
    var cancelled = _qwenIsCancelledStructure(structure);
    var resolved = card.blocking_resolved_by_human === true || card.source_status === "consistent";
    return {
      human_bet_id: String(card.human_bet_id || _qwenHumanBetId(index)),
      bet_type: _qwenCanonicalLayout(structure.layout) === "column_bet" ? "column" : "normal",
      number_groups: _qwenNormalizeGroups(structure.number_groups || []),
      multiplier: {
        ordered_rules: Array.isArray(structure.multiplier_rules) ? structure.multiplier_rules.map(String) : [],
        scope: structure.multiplier_scope == null ? null : String(structure.multiplier_scope),
        resolved: resolved && Array.isArray(structure.multiplier_rules) && structure.multiplier_rules.length > 0
      },
      special_play: _qwenSpecialPlay(structure, resolved),
      continuation: {
        present: !!structure.continuation,
        resolved: resolved
      },
      cancelled: cancelled,
      active: !cancelled,
      human_confirmed: card.human_confirmed === true
    };
  }

  function _qwenAuthorityBets() {
    if (!qwenReviewSession) return [];
    return qwenReviewSession.structures.map(_qwenAuthorityBet);
  }

  function _qwenAuthorityErrorCode(data) {
    if (!data) return "CANDIDATE_AUTHORITY_ERROR";
    if (data.code) return String(data.code);
    if (data.error && data.error.code) return String(data.error.code);
    return "CANDIDATE_AUTHORITY_ERROR";
  }

  function _qwenAuthorityErrorMessage(data) {
    if (!data) return "server authority unavailable";
    if (data.error && data.error.message) return String(data.error.message);
    if (typeof data.error === "string") return data.error;
    return String(data.message || _qwenAuthorityErrorCode(data));
  }

  function _qwenMachineEvidenceRefs() {
    var refs = [];
    var preprocessing = qwenEvidenceResult && qwenEvidenceResult.preprocessing || {};
    var request = preprocessing.qwen_request || {};
    var evidenceHash = String(preprocessing.qwen_response_sha256 || request.response_sha256 || "");
    if (/^[a-f0-9]{64}$/.test(evidenceHash)) {
      refs.push({
        provider_id: "qwen-dashscope",
        model: String((qwenEvidenceResult.provider || {}).model_name || request.model || "") || null,
        request_id: String(request.request_id || qwenEvidenceResult.request_id || "") || null,
        cache_hit: typeof request.cache_hit === "boolean" ? request.cache_hit : null,
        evidence_hash: evidenceHash,
        artifact_ref: null,
        value_authority: false
      });
    }
    [gemmaShadowEvidence, ppocrShadowEvidence].forEach(function(evidence) {
      if (!evidence || typeof evidence !== "object") return;
      var hash = String(evidence.evidence_hash || evidence.artifact_sha256 || "");
      if (!/^[a-f0-9]{64}$/.test(hash)) return;
      var provider = evidence.provider || {};
      refs.push({
        provider_id: String(provider.id || evidence.provider_id || "shadow-evidence"),
        model: String(provider.model_name || evidence.model || "") || null,
        request_id: String(evidence.request_id || "") || null,
        cache_hit: typeof evidence.cache_hit === "boolean" ? evidence.cache_hit : null,
        evidence_hash: hash,
        artifact_ref: evidence.artifact_ref == null ? null : String(evidence.artifact_ref),
        value_authority: false
      });
    });
    return refs;
  }

  function _qwenRememberReviewSession(reviewSessionId) {
    try {
      if (reviewSessionId) localStorage.setItem(QWEN_REVIEW_STORAGE_KEY, reviewSessionId);
      else localStorage.removeItem(QWEN_REVIEW_STORAGE_KEY);
    } catch (_) {}
  }

  function _qwenApplyAuthorityReview(review) {
    if (!qwenReviewSession || !review || typeof review !== "object") return;
    qwenReviewSession.human_answer_revision = Number(review.human_answer_revision);
    qwenReviewSession.human_answer_hash = String(review.human_answer_hash || "");
    qwenReviewSession.server_state = "ready";
    qwenReviewSession.server_error = null;
    var byId = Object.create(null);
    (Array.isArray(review.bets) ? review.bets : []).forEach(function(bet) {
      byId[String((bet || {}).human_bet_id || "")] = bet;
    });
    qwenReviewSession.structures.forEach(function(card, index) {
      card.human_bet_id = String(card.human_bet_id || _qwenHumanBetId(index));
      var bet = byId[card.human_bet_id];
      if (!bet) return;
      var structure = _cloneJson(card.staged_structure || {});
      structure.number_groups = _cloneJson(bet.number_groups || []);
      structure.multiplier_rules = _cloneJson((bet.multiplier || {}).ordered_rules || []);
      if ((bet.multiplier || {}).scope != null || Object.prototype.hasOwnProperty.call(structure, "multiplier_scope")) {
        structure.multiplier_scope = (bet.multiplier || {}).scope == null ? null : (bet.multiplier || {}).scope;
      }
      var preserveProvisionalUnknown = card.provisional_review === true &&
        bet.human_confirmed !== true && _qwenCanonicalLayout(structure.layout) === "unknown";
      structure.layout = preserveProvisionalUnknown ? "unknown" :
        (bet.bet_type === "column" ? "column_bet" : "normal_row");
      if ((bet.continuation || {}).present) {
        if (!structure.continuation) structure.continuation = true;
      } else if (Object.prototype.hasOwnProperty.call(structure, "continuation")) {
        structure.continuation = false;
      }
      if (bet.cancelled === true || Object.prototype.hasOwnProperty.call(structure, "cancelled")) {
        structure.cancelled = bet.cancelled === true;
      }
      var special = bet.special_play || {};
      if (special.raw_text) {
        try {
          var decoded = JSON.parse(special.raw_text);
          Object.keys(decoded).forEach(function(key) { structure[key] = decoded[key]; });
        } catch (_) {
          structure.special_text = String(special.raw_text);
        }
      }
      if (special.scope != null || Object.prototype.hasOwnProperty.call(structure, "scope")) {
        structure.scope = special.scope == null ? null : special.scope;
      }
      card.staged_structure = structure;
      card.human_confirmed = bet.human_confirmed === true;
      card.review_state = card.human_confirmed
        ? "confirmed"
        : (card.review_state === "editing" ? "editing" : "pending");
      card.blocking_resolved_by_human = card.human_confirmed || card.source_status === "consistent";
    });
    _qwenRememberReviewSession(qwenReviewSession.review_session_id);
  }

  function _qwenSetAuthorityFailure(data) {
    if (!qwenReviewSession) return;
    qwenReviewSession.server_state = _qwenAuthorityErrorCode(data) === "REVIEW_STALE" ? "stale" : "error";
    qwenReviewSession.server_error = {
      code: _qwenAuthorityErrorCode(data),
      message: _qwenAuthorityErrorMessage(data)
    };
  }

  function _qwenCreateAuthorityReview() {
    if (!qwenReviewSession) return Promise.resolve(null);
    qwenReviewSession.server_state = "saving";
    return fetch("/api/vision/v1/review-sessions", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({
        review_session_id: qwenReviewSession.review_session_id,
        source_image_id: qwenReviewSession.source_image_id,
        source_image_hash: qwenReviewSession.image_sha256,
        game: qwenReviewSession.game,
        bets: _qwenAuthorityBets(),
        machine_evidence_refs: _qwenMachineEvidenceRefs(),
        blocking_unresolved_count: _qwenReviewReadiness().blocking_unresolved
      })
    }).then(function(response) { return response.json(); }).then(function(data) {
      if (!data.ok) throw data;
      _qwenApplyAuthorityReview(data.review);
      _renderQwenReviewSession();
      return data.review;
    }).catch(function(data) {
      _qwenSetAuthorityFailure(data);
      _renderQwenReviewSession();
      return null;
    });
  }

  function _qwenPersistAuthorityReview() {
    if (!qwenReviewSession || !Number.isInteger(qwenReviewSession.human_answer_revision) ||
        !/^[a-f0-9]{64}$/.test(String(qwenReviewSession.human_answer_hash || ""))) {
      _qwenSetAuthorityFailure({code: "REVIEW_NOT_PERSISTED", error: {message: "Human Review 尚未由 server 建立"}});
      _renderQwenReviewSession();
      return Promise.resolve(null);
    }
    var reviewSessionId = qwenReviewSession.review_session_id;
    var payload = {
      expected_human_answer_revision: qwenReviewSession.human_answer_revision,
      expected_human_answer_hash: qwenReviewSession.human_answer_hash,
      bets: _qwenAuthorityBets(),
      machine_evidence_refs: _qwenMachineEvidenceRefs(),
      blocking_unresolved_count: _qwenReviewReadiness().blocking_unresolved
    };
    qwenReviewSession.server_state = "saving";
    _renderQwenReviewSession();
    return fetch("/api/vision/v1/review-sessions/" + encodeURIComponent(reviewSessionId), {
      method: "PATCH",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify(payload)
    }).then(function(response) { return response.json(); }).then(function(data) {
      if (!data.ok) throw data;
      _qwenApplyAuthorityReview(data.review);
      qwenReviewSession.server_candidate = data.candidate || qwenReviewSession.server_candidate || null;
      _renderQwenReviewSession();
      return data.review;
    }).catch(function(data) {
      _qwenSetAuthorityFailure(data);
      _renderQwenReviewSession();
      return null;
    });
  }

  function _qwenQueueAuthorityMutation() {
    qwenAuthorityMutationChain = qwenAuthorityMutationChain.then(_qwenPersistAuthorityReview);
    return qwenAuthorityMutationChain;
  }

  function _qwenPersistAuthorityConfirmation(card, confirmed) {
    if (!qwenReviewSession || !card || qwenReviewSession.server_state !== "ready") return Promise.resolve(null);
    var endpoint = confirmed ? "confirmations" : "unconfirmations";
    var payload = {
      expected_human_answer_revision: qwenReviewSession.human_answer_revision,
      expected_human_answer_hash: qwenReviewSession.human_answer_hash,
      human_bet_ids: [String(card.human_bet_id || "")]
    };
    qwenReviewSession.server_state = "saving";
    _renderQwenReviewSession();
    return fetch("/api/vision/v1/review-sessions/" + encodeURIComponent(qwenReviewSession.review_session_id) + "/" + endpoint, {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify(payload)
    }).then(function(response) { return response.json(); }).then(function(data) {
      if (!data.ok) throw data;
      _qwenApplyAuthorityReview(data.review);
      qwenReviewSession.server_candidate = data.candidate || qwenReviewSession.server_candidate || null;
      if (confirmed) _qwenSelectNextUnconfirmed(qwenReviewSession.structures.indexOf(card));
      _renderQwenReviewSession();
      return data.review;
    }).catch(function(data) {
      _qwenSetAuthorityFailure(data);
      _renderQwenReviewSession();
      return null;
    });
  }

  function _qwenCardFromAuthorityBet(bet, index) {
    bet = bet || {};
    var special = bet.special_play || {};
    var structure = {
      number_groups: _cloneJson(bet.number_groups || []),
      multiplier_rules: _cloneJson((bet.multiplier || {}).ordered_rules || []),
      multiplier_scope: (bet.multiplier || {}).scope == null ? null : (bet.multiplier || {}).scope,
      layout: bet.bet_type === "column" ? "column_bet" : "normal_row",
      continuation: !!((bet.continuation || {}).present),
      scope: special.scope == null ? null : special.scope,
      special_text: "",
      cancelled: bet.cancelled === true
    };
    if (special.raw_text) {
      try {
        var fields = JSON.parse(special.raw_text);
        Object.keys(fields).forEach(function(key) { structure[key] = fields[key]; });
      } catch (_) {
        structure.special_text = String(special.raw_text);
      }
    }
    var confirmed = bet.human_confirmed === true;
    return {
      human_bet_id: String(bet.human_bet_id || _qwenHumanBetId(index)),
      structure_id: "SERVER-" + String(bet.human_bet_id || _qwenHumanBetId(index)),
      primary_line_id: "",
      source_line_ids: [],
      source_status: "incomplete",
      review_state: confirmed ? "confirmed" : "pending",
      model_candidate: {},
      original_structure: _cloneJson(structure),
      staged_structure: structure,
      warnings: ["reloaded_from_server_human_review"],
      evidence: {},
      edit_text: "",
      reparse_preview: null,
      preview_error: "",
      manual_edits: [],
      field_corrections: [],
      evidence_sources: {
        qwen: null,
        gemma: null,
        ppocr: null,
        codex: null,
        human_answer: {source: "Persisted Human Answer", human_confirmed: confirmed}
      },
      field_sources: {
        numbers: {source: "Persisted Human Answer", human_confirmed: confirmed},
        multiplier: {source: "Persisted Human Answer", human_confirmed: confirmed},
        layout: {source: "Persisted Human Answer", human_confirmed: confirmed}
      },
      suggestion_adoptions: [],
      blocking_resolved_by_human: confirmed,
      human_confirmed: confirmed
    };
  }

  function _qwenLoadAuthorityReview(review, candidate) {
    var bets = Array.isArray(review && review.bets) ? review.bets : [];
    qwenReviewSession = {
      schema_version: "vision-review-session-v1",
      review_session_id: String(review.review_session_id || ""),
      game: String(review.game || "539"),
      source_image_id: String(review.source_image_id || ""),
      image_sha256: String(review.source_image_hash || ""),
      structures: bets.map(_qwenCardFromAuthorityBet),
      unlinked_gemma_items: [],
      active_structure_id: bets.length ? "SERVER-" + String(bets[0].human_bet_id || _qwenHumanBetId(0)) : null,
      show_pending_only: false,
      candidate_preview: null,
      boundary_signal: null,
      human_answer_revision: Number(review.human_answer_revision),
      human_answer_hash: String(review.human_answer_hash || ""),
      server_state: "ready",
      server_error: null,
      server_candidate: candidate || null,
      reloaded_from_server: true,
      created_at: String(review.created_at || new Date().toISOString()),
      safety: {
        human_confirmation_required: true,
        auto_apply: false,
        auto_confirm: false,
        auto_submit: false
      }
    };
    uploadedImageId = qwenReviewSession.source_image_id || null;
    uploadedImageMetadata = {
      image_id: qwenReviewSession.source_image_id,
      sha256: qwenReviewSession.image_sha256
    };
    qwenRequestedGame = qwenReviewSession.game;
    var section = document.getElementById("vision-section");
    if (section) section.style.display = "block";
    var image = document.getElementById("vision-preview-img");
    if (image && uploadedImageId) image.src = "/api/vision/v1/images/" + encodeURIComponent(uploadedImageId);
    var preview = document.getElementById("vision-preview");
    if (preview && uploadedImageId) preview.style.display = "block";
    var body = document.getElementById("vision-results-body");
    if (body) body.innerHTML = '<div id="qwen-evidence-status" style="padding:7px 9px;background:#fff7ed;color:#9a3412;font-weight:700">已重新載入 Server Human Review；server authority 優先</div><div id="qwen-review-session"></div>';
    var results = document.getElementById("vision-results");
    if (results) results.style.display = "block";
    _qwenRememberReviewSession(qwenReviewSession.review_session_id);
    if (candidate) {
      qwenReviewSession.server_queue_entry = null;
      qwenReviewSession.server_queue_error = null;
    }
    _renderQwenReviewSession();
    if (candidate) _qwenRefreshCandidateQueue(candidate);
  }

  function _qwenReloadPersistedReview() {
    var reviewSessionId = "";
    try { reviewSessionId = localStorage.getItem(QWEN_REVIEW_STORAGE_KEY) || ""; } catch (_) {}
    if (!reviewSessionId) return;
    fetch("/api/vision/v1/review-sessions/" + encodeURIComponent(reviewSessionId))
      .then(function(response) { return response.json(); })
      .then(function(data) {
        if (!data.ok || !data.review) throw data;
        _qwenLoadAuthorityReview(data.review, data.candidate || null);
      }).catch(function(data) {
        if (_qwenAuthorityErrorCode(data) === "REVIEW_NOT_FOUND") _qwenRememberReviewSession("");
      });
  }

  function _qwenNewManualCard(index) {
    var structureId = "MANUAL-" + String(index + 1).padStart(2, "0");
    var staged = {
      number_groups: [],
      multiplier_rules: [],
      layout: "unknown",
      collision: null,
      continuation: false,
      tail: null,
      car: null,
      half_car: null,
      each: null,
      special_text: "",
      cancelled: false
    };
    return {
      human_bet_id: _qwenHumanBetId(index),
      structure_id: structureId,
      primary_line_id: "",
      source_line_ids: [],
      source_status: "unsupported",
      review_state: "editing",
      model_candidate: {},
      original_structure: {},
      staged_structure: _cloneJson(staged),
      warnings: ["manual_entry_after_provider_failure"],
      evidence: {},
      edit_text: "",
      reparse_preview: null,
      preview_error: "",
      manual_edits: [],
      field_corrections: [],
      evidence_sources: {
        qwen: {source: "Qwen unavailable", model_candidate: {}, reconstructed_candidate: {}},
        gemma: null,
        ppocr: null,
        codex: null,
        human_answer: {
          source: "Human Answer",
          human_confirmed: false,
          staged_structure: _cloneJson(staged),
          field_sources: {
            numbers: {source: "Human Answer"},
            multiplier: {source: "Human Answer"},
            layout: {source: "Human Answer"}
          },
          field_corrections: []
        }
      },
      field_sources: {
        numbers: {source: "Human Answer", human_confirmed: false},
        multiplier: {source: "Human Answer", human_confirmed: false},
        layout: {source: "Human Answer", human_confirmed: false}
      },
      suggestion_adoptions: [],
      blocking_resolved_by_human: false,
      human_confirmed: false
    };
  }

  function _createQwenFailureReviewSession(game, failure) {
    qwenReviewSession = {
      schema_version: "vision-review-session-v1",
      review_session_id: "vision-review-" + String(uploadedImageId || "unknown") + "-" + String(Date.now()),
      game: game === "六合" ? "六合" : "539",
      source_image_id: String(uploadedImageId || ""),
      image_sha256: String((uploadedImageMetadata && uploadedImageMetadata.sha256) || ""),
      structures: [],
      unlinked_gemma_items: _gemmaUnlinkedEvidence(gemmaShadowEvidence),
      active_structure_id: null,
      show_pending_only: false,
      candidate_preview: null,
      boundary_signal: null,
      human_answer_revision: null,
      human_answer_hash: "",
      server_state: "saving",
      server_error: null,
      server_candidate: null,
      provider_failure: _cloneJson(failure || {}),
      created_at: new Date().toISOString(),
      safety: {
        human_confirmation_required: true,
        auto_apply: false,
        auto_confirm: false,
        auto_submit: false
      }
    };
    var gameSelect = document.getElementById("vision-qwen-game");
    if (gameSelect) gameSelect.disabled = true;
    _qwenCreateAuthorityReview();
  }

  function _createQwenReviewSession(result, structureEvidence, game) {
    var source = result.source_image || {};
    var primaries = _qwenPrimaryStructures(structureEvidence);
    var gemmaByStructureId = _gemmaEvidenceByStructureId(gemmaShadowEvidence);
    var cards = [];
    for (var i = 0; i < primaries.length; i++) {
      var item = primaries[i] || {};
      var primaryLineId = String(item.primary_line_id || item.line_id || "");
      var sourceLineIds = Array.isArray(item.member_line_ids)
        ? item.member_line_ids.map(String) : (primaryLineId ? [primaryLineId] : []);
      var reconstructed = _qwenInitialReviewStructure(item);
      var structureId = String(item.structure_id || primaryLineId);
      var evidenceSources = _reviewEvidenceSources(item, gemmaByStructureId[structureId] || null);
      evidenceSources.human_answer.staged_structure = _cloneJson(reconstructed);
      evidenceSources.human_answer.field_sources = {
        numbers: {source: "Qwen reconstruction"},
        multiplier: {source: "Qwen reconstruction"},
        layout: {source: "Qwen reconstruction"}
      };
      cards.push({
        human_bet_id: _qwenHumanBetId(i),
        structure_id: structureId,
        primary_line_id: primaryLineId,
        source_line_ids: sourceLineIds,
        source_status: ["consistent", "divergent", "incomplete", "unsupported"].indexOf(item.status) >= 0
          ? item.status : "incomplete",
        review_state: "pending",
        model_candidate: _cloneJson(item.model_candidate || {}),
        original_structure: _cloneJson(reconstructed),
        staged_structure: _cloneJson(reconstructed),
        warnings: _cloneJson(Array.isArray(item.warnings) ? item.warnings : []),
        evidence: _cloneJson(item.evidence || {}),
        edit_text: "",
        reparse_preview: null,
        preview_error: "",
        manual_edits: [],
        field_corrections: [],
        evidence_sources: evidenceSources,
        field_sources: {
          numbers: {source: "Qwen reconstruction"},
          multiplier: {source: "Qwen reconstruction"},
          layout: {source: "Qwen reconstruction"}
        },
        suggestion_adoptions: [],
        blocking_resolved_by_human: item.status === "consistent",
        human_confirmed: false
      });
    }
    qwenReviewSession = {
      schema_version: "vision-review-session-v1",
      review_session_id: "vision-review-" + String(uploadedImageId || source.image_id || "unknown") + "-" + String(Date.now()),
      game: game,
      source_image_id: String(uploadedImageId || source.image_id || ""),
      image_sha256: String((uploadedImageMetadata && uploadedImageMetadata.sha256) || source.sha256 || ""),
      structures: cards,
      // Gemma currently has no physical bbox/structure identity.  Keep those
      // suggestions browser-local and unlinked until an explicit user click
      // associates one evidence item with one review card.
      unlinked_gemma_items: _gemmaUnlinkedEvidence(gemmaShadowEvidence),
      active_structure_id: cards.length ? cards[0].structure_id : null,
      show_pending_only: false,
      candidate_preview: null,
      boundary_signal: null,
      human_answer_revision: null,
      human_answer_hash: "",
      server_state: "saving",
      server_error: null,
      server_candidate: null,
      created_at: new Date().toISOString(),
      safety: {
        human_confirmation_required: true,
        auto_apply: false,
        auto_confirm: false,
        auto_submit: false
      }
    };
    var gameSelect = document.getElementById("vision-qwen-game");
    if (gameSelect) gameSelect.disabled = true;
    _qwenCreateAuthorityReview();
  }

  function _qwenNumber(value) {
    var text = String(value == null ? "" : value).trim();
    return /^\\d$/.test(text) ? "0" + text : text;
  }

  function _qwenNormalizeGroups(groups) {
    if (!Array.isArray(groups)) return [];
    return groups.map(function(group) {
      return (Array.isArray(group) ? group : [group]).map(_qwenNumber).filter(function(value) { return !!value; });
    }).filter(function(group) { return group.length > 0; });
  }

  function _qwenCanonicalEditText(structure) {
    structure = structure || {};
    var groups = _qwenNormalizeGroups(structure.number_groups || []);
    var layout = String(structure.layout || "");
    var numberText = layout === "column_bet" || layout === "column"
      ? groups.map(function(group) { return group.join(" "); }).join(" / ")
      : groups.map(function(group) { return group.join(" "); }).join(" ");
    var rules = Array.isArray(structure.multiplier_rules) ? structure.multiplier_rules : [];
    return [numberText, rules.map(_qwenRuleToParserText).join(" ")].filter(function(part) { return !!part; }).join(" ");
  }

  function _qwenValidHumanNumber(value) {
    return /^(0[1-9]|[12]\\d|3[0-9])$/.test(String(value == null ? "" : value).trim());
  }

  function _qwenFlattenGroups(groups) {
    return _qwenNormalizeGroups(groups || []).reduce(function(all, group) {
      return all.concat(group);
    }, []);
  }

  function _qwenTransposeRows(rows) {
    if (!Array.isArray(rows) || rows.length !== 2) return [];
    var first = (rows[0] || []).map(_qwenNumber);
    var second = (rows[1] || []).map(_qwenNumber);
    if (!first.length || first.length !== second.length) return [];
    if (first.concat(second).some(function(value) { return !_qwenValidHumanNumber(value); })) return [];
    return first.map(function(value, index) { return [value, second[index]]; });
  }

  function _qwenLiteralRows(text) {
    var rows = [];
    String(text || "").split(/\\r?\\n/).forEach(function(line) {
      // Decimal multiplier literals such as 0.5 are deliberately excluded.
      var values = [];
      var expression = /(^|[^\\d.])(0[1-9]|[12]\\d|3[0-9])(?=$|[^\\d.])/g;
      var match;
      while ((match = expression.exec(line)) !== null) values.push(match[2]);
      if (values.length) rows.push(values);
    });
    return rows;
  }

  function _qwenVisibleRows(card) {
    card = card || {};
    var candidates = [
      card.ai_visible_rows,
      card.evidence && card.evidence.visible_rows,
      card.evidence_sources && card.evidence_sources.gemma && card.evidence_sources.gemma.visible_rows,
      card.crop_reread_suggestion && card.crop_reread_suggestion.visible_rows,
      card.evidence_sources && card.evidence_sources.crop_reread &&
        card.evidence_sources.crop_reread.suggestion &&
        card.evidence_sources.crop_reread.suggestion.visible_rows
    ];
    for (var i = 0; i < candidates.length; i++) {
      if (!Array.isArray(candidates[i])) continue;
      var normalized = candidates[i].map(function(row) {
        return (Array.isArray(row) ? row : []).map(_qwenNumber).filter(_qwenValidHumanNumber);
      }).filter(function(row) { return row.length; });
      if (normalized.length) return normalized;
    }
    var literal = _qwenLiteralRows(card.evidence && card.evidence.raw_text);
    if (literal.length) return literal;
    var structure = card.staged_structure || {};
    var groups = _qwenNormalizeGroups(structure.number_groups || []);
    if (_qwenCanonicalLayout(structure.layout) === "column_bet" && groups.length > 1) {
      var rowCount = groups[0].length;
      if (rowCount > 0 && groups.every(function(group) { return group.length === rowCount; })) {
        var rows = [];
        for (var rowIndex = 0; rowIndex < rowCount; rowIndex++) {
          rows.push(groups.map(function(group) { return group[rowIndex]; }));
        }
        return rows;
      }
    }
    return groups.length ? [_qwenFlattenGroups(groups)] : [];
  }

  function _qwenNextHumanBetId() {
    var used = Object.create(null);
    (qwenReviewSession && qwenReviewSession.structures || []).forEach(function(card) {
      used[String(card.human_bet_id || "")] = true;
    });
    for (var i = 1; i < 1000; i++) {
      var value = _qwenHumanBetId(i - 1);
      if (!used[value]) return value;
    }
    return "H-999";
  }

  function _qwenQuickCommit(index, component, updated, fields, note) {
    if (!qwenReviewSession || !qwenReviewSession.structures[index]) return Promise.resolve(null);
    var card = qwenReviewSession.structures[index];
    var previous = _cloneJson(card.staged_structure || {});
    card.staged_structure = _cloneJson(updated || {});
    (fields || []).forEach(function(field) {
      card.field_sources[field] = {source: "Human Answer", human_confirmed: false};
      _recordReviewFieldCorrection(card, field, "Human Answer", previous[field], card.staged_structure[field]);
    });
    card.manual_edits.push({
      component: component,
      note: note || null,
      adopted_at: new Date().toISOString(),
      human_confirmed: false
    });
    delete card.quick_preview;
    delete card.quick_group_draft;
    delete card.quick_multiplier_draft;
    delete card.quick_special_draft;
    _invalidateReviewConfirmation(card);
    card.blocking_resolved_by_human = false;
    _renderQwenReviewSession();
    return _qwenQueueAuthorityMutation();
  }

  function _qwenAiLiteralHtml(card) {
    var raw = String(card && card.evidence && card.evidence.raw_text || "").trim();
    var rows = _qwenVisibleRows(card);
    return '<section class="qwen-ai-literal" style="margin-top:7px;padding:7px;background:#f8fafc;border:1px solid #e2e8f0;border-radius:5px">' +
      '<strong>AI 原始讀取</strong>' +
      '<div class="qwen-ai-raw-text" style="white-space:pre-wrap;font-family:monospace">' + esc(raw || "未提供") + '</div>' +
      '<div class="qwen-ai-visible-rows" style="font-size:12px;color:#475569">可見兩排／行：' + esc(JSON.stringify(rows)) + '</div></section>';
  }

  function _qwenQuickPreviewHtml(card, index) {
    var preview = card.quick_preview;
    if (!preview) return '';
    var apply = '';
    if (preview.kind === "normal") {
      apply = '<button type="button" class="qwen-apply-normal-preview" onclick="qwenQuickApplyNormal(' + index + ')">套用一般投注</button>';
    } else if (preview.kind === "two_row_column") {
      apply = '<button type="button" class="qwen-apply-column-preview" ' + (preview.valid ? '' : 'disabled ') +
        'onclick="qwenQuickApplyColumn(' + index + ')">套用柱碰分組</button>';
    } else if (preview.kind === "merge_next") {
      apply = '<button type="button" class="qwen-apply-merge-preview" ' + (preview.valid ? '' : 'disabled ') +
        'onclick="qwenQuickApplyMerge(' + index + ')">確認合併並套用</button>';
    }
    return '<div class="qwen-quick-preview" data-preview-kind="' + esc(preview.kind || '') + '" style="margin-top:7px;padding:8px;background:#fff7ed;border:1px solid #fdba74;border-radius:5px">' +
      '<strong>人工操作預覽（尚未套用）</strong>' +
      '<div class="qwen-preview-rows">兩排：' + esc(JSON.stringify(preview.rows || [])) + '</div>' +
      '<div class="qwen-preview-groups">柱群：' + esc(JSON.stringify(preview.groups || [])) + '</div>' +
      (preview.warning ? '<div class="qwen-preview-warning" style="color:#b91c1c;font-weight:700">' + esc(preview.warning) + '</div>' : '') +
      apply + ' <button type="button" class="qwen-cancel-quick-preview" onclick="qwenQuickCancelPreview(' + index + ')">取消預覽</button></div>';
  }

  function _qwenGroupEditorHtml(card, index) {
    var groups = card.quick_group_draft;
    if (!Array.isArray(groups)) return '';
    var html = '<div class="qwen-group-editor" style="margin-top:7px;padding:8px;border:1px solid #93c5fd;border-radius:5px">' +
      '<strong>柱群編輯器（套用前不會修改 Human Answer）</strong>';
    for (var g = 0; g < groups.length; g++) {
      html += '<div class="qwen-group-row" data-group-index="' + g + '" style="display:flex;flex-wrap:wrap;gap:4px;align-items:center;margin-top:6px">' +
        '<strong>第' + (g + 1) + '柱：</strong>';
      for (var n = 0; n < groups[g].length; n++) {
        html += '<span class="qwen-group-number" data-number-index="' + n + '" style="display:inline-flex;gap:2px;align-items:center">' +
          '<input value="' + esc(groups[g][n]) + '" maxlength="2" size="2" onchange="qwenGroupSetNumber(' + index + ',' + g + ',' + n + ',this.value)">' +
          '<button type="button" title="移到上一柱" onclick="qwenGroupMoveNumber(' + index + ',' + g + ',' + n + ',-1)">←</button>' +
          '<button type="button" title="移到下一柱" onclick="qwenGroupMoveNumber(' + index + ',' + g + ',' + n + ',1)">→</button>' +
          '<button type="button" title="刪除號碼" onclick="qwenGroupDeleteNumber(' + index + ',' + g + ',' + n + ')">×</button></span>';
      }
      html += '<button type="button" onclick="qwenGroupAddNumber(' + index + ',' + g + ')">＋號碼</button>' +
        '<button type="button" title="上移柱" onclick="qwenGroupMoveColumn(' + index + ',' + g + ',-1)">↑柱</button>' +
        '<button type="button" title="下移柱" onclick="qwenGroupMoveColumn(' + index + ',' + g + ',1)">↓柱</button>' +
        '<button type="button" onclick="qwenGroupDeleteColumn(' + index + ',' + g + ')">刪除柱</button></div>';
    }
    return html + '<div class="qwen-group-editor-error" style="color:#b91c1c">' + esc(card.quick_group_error || '') + '</div>' +
      '<button type="button" onclick="qwenGroupAddColumn(' + index + ')">＋新增柱</button> ' +
      '<button type="button" class="qwen-apply-group-editor" onclick="qwenGroupApply(' + index + ')">套用柱群</button> ' +
      '<button type="button" onclick="qwenGroupCancel(' + index + ')">取消</button></div>';
  }

  function _qwenMultiplierEditorHtml(card, index) {
    var draft = card.quick_multiplier_draft;
    if (!Array.isArray(draft)) return '';
    var html = '<div class="qwen-multiplier-editor" style="margin-top:7px;padding:8px;border:1px solid #a7f3d0;border-radius:5px">' +
      '<strong>Ordered multiplier rules</strong>';
    for (var i = 0; i < draft.length; i++) {
      var rule = draft[i] || {};
      html += '<div class="qwen-multiplier-rule" data-rule-index="' + i + '" style="display:flex;flex-wrap:wrap;gap:4px;margin-top:5px">' +
        '<label>類別 <input class="qwen-multiplier-category" value="' + esc(rule.category || '') + '" size="7" onchange="qwenMultiplierSet(' + index + ',' + i + ',\\'category\\',this.value)"></label>' +
        '<label>倍率 <input class="qwen-multiplier-value" value="' + esc(rule.value || '') + '" size="5" onchange="qwenMultiplierSet(' + index + ',' + i + ',\\'value\\',this.value)"></label>' +
        '<label>範圍 <input class="qwen-multiplier-scope" value="' + esc(rule.scope || '') + '" size="12" onchange="qwenMultiplierSet(' + index + ',' + i + ',\\'scope\\',this.value)"></label>' +
        '<button type="button" onclick="qwenMultiplierMove(' + index + ',' + i + ',-1)">↑</button>' +
        '<button type="button" onclick="qwenMultiplierMove(' + index + ',' + i + ',1)">↓</button>' +
        '<button type="button" onclick="qwenMultiplierDelete(' + index + ',' + i + ')">刪除</button></div>';
    }
    return html + '<div class="qwen-multiplier-error" style="color:#b91c1c">' + esc(card.quick_multiplier_error || '') + '</div>' +
      '<button type="button" onclick="qwenMultiplierAdd(' + index + ')">＋倍率規則</button> ' +
      '<button type="button" class="qwen-apply-multiplier-editor" onclick="qwenMultiplierApply(' + index + ')">套用倍率</button> ' +
      '<button type="button" onclick="qwenMultiplierCancel(' + index + ')">取消</button></div>';
  }

  function _qwenSpecialQuickEditorHtml(card, index) {
    var draft = card.quick_special_draft;
    if (!draft) return '';
    var kinds = [["none","無"],["tail","尾"],["car","車"],["half_car","半車"],["each","各"],["custom","自訂原文"]];
    var options = kinds.map(function(item) {
      return '<option value="' + item[0] + '" ' + (draft.kind === item[0] ? 'selected ' : '') + '>' + item[1] + '</option>';
    }).join('');
    return '<div class="qwen-special-quick-editor" style="margin-top:7px;padding:8px;border:1px solid #fbcfe8;border-radius:5px">' +
      '<strong>特殊玩法</strong> <select class="qwen-special-kind" onchange="qwenSpecialSet(' + index + ',\\'kind\\',this.value)">' + options + '</select> ' +
      '<label>原文 <input class="qwen-special-literal" value="' + esc(draft.literal || '') + '" onchange="qwenSpecialSet(' + index + ',\\'literal\\',this.value)"></label> ' +
      '<label>範圍 <input class="qwen-special-quick-scope" value="' + esc(draft.scope || '') + '" onchange="qwenSpecialSet(' + index + ',\\'scope\\',this.value)"></label> ' +
      '<label><input type="checkbox" class="qwen-special-quick-continuation" ' + (draft.continuation ? 'checked ' : '') + 'onchange="qwenSpecialSet(' + index + ',\\'continuation\\',this.checked)"> continuation</label>' +
      '<div class="qwen-special-quick-error" style="color:#b91c1c">' + esc(card.quick_special_error || '') + '</div>' +
      '<button type="button" class="qwen-apply-special-editor" onclick="qwenSpecialApply(' + index + ')">套用特殊玩法</button> ' +
      '<button type="button" onclick="qwenSpecialCancel(' + index + ')">取消</button></div>';
  }

  function _qwenSplitEditorHtml(card, index) {
    if (card.quick_split_text == null) return '';
    return '<div class="qwen-split-editor" style="margin-top:7px;padding:8px;border:1px solid #c4b5fd;border-radius:5px">' +
      '<strong>拆成兩筆（請明確輸入兩行）</strong>' +
      '<textarea class="qwen-split-rows" style="display:block;width:100%;min-height:54px" onchange="qwenSplitSetText(' + index + ',this.value)">' + esc(card.quick_split_text) + '</textarea>' +
      '<div class="qwen-split-warning" style="font-size:12px;color:#9a3412">倍率與特殊玩法保留在第一筆；第二筆由人工另行補充並確認。</div>' +
      '<div class="qwen-split-error" style="color:#b91c1c">' + esc(card.quick_split_error || '') + '</div>' +
      '<button type="button" class="qwen-apply-split" onclick="qwenSplitApply(' + index + ')">確認拆分</button> ' +
      '<button type="button" onclick="qwenSplitCancel(' + index + ')">取消</button></div>';
  }

  function _qwenQuickCorrectionHtml(card, index) {
    var nextAvailable = !!(qwenReviewSession && qwenReviewSession.structures[index + 1]);
    return '<section class="qwen-fast-correction" style="margin-top:8px;padding:8px;background:#f8fafc;border:1px solid #94a3b8;border-radius:6px" onclick="event.stopPropagation()">' +
      '<strong>快速人工修正</strong><div style="display:flex;flex-wrap:wrap;gap:5px;margin-top:6px">' +
      '<button type="button" class="qwen-quick-normal" onclick="qwenQuickSetNormal(' + index + ')">設為一般投注</button>' +
      '<button type="button" class="qwen-quick-two-row" onclick="qwenQuickPreviewTwoRow(' + index + ')">兩排轉柱碰</button>' +
      '<button type="button" class="qwen-quick-merge" ' + (nextAvailable ? '' : 'disabled ') + 'onclick="qwenQuickPreviewMerge(' + index + ')">與下一筆合併為柱碰</button>' +
      '<button type="button" class="qwen-quick-split" onclick="qwenSplitOpen(' + index + ')">拆成兩筆</button>' +
      '<button type="button" class="qwen-open-group-editor" onclick="qwenGroupOpen(' + index + ')">編輯柱群</button>' +
      '<button type="button" class="qwen-open-multiplier-editor" onclick="qwenMultiplierOpen(' + index + ')">編輯倍率</button>' +
      '<button type="button" class="qwen-open-special-editor" onclick="qwenSpecialOpen(' + index + ')">編輯特殊玩法</button></div>' +
      _qwenQuickPreviewHtml(card, index) + _qwenGroupEditorHtml(card, index) +
      _qwenMultiplierEditorHtml(card, index) + _qwenSpecialQuickEditorHtml(card, index) +
      _qwenSplitEditorHtml(card, index) + '</section>';
  }

  function _qwenRuleToParserText(rule) {
    var parts = String(rule || "").toUpperCase().split("X");
    if (parts.length !== 2) return String(rule || "");
    var digits = {"2": "二", "3": "三", "4": "四"};
    var categories = parts[0].split("/");
    if (!categories.length || categories.some(function(value) { return !digits[value]; })) return String(rule || "");
    return categories.map(function(value) { return digits[value]; }).join("") + "X" + parts[1];
  }

  function _qwenExtractMultiplierRules(text) {
    var rules = [];
    var expression = /(二三四|二三|三四|二|三|四|[234](?:\\s*\\/\\s*[234])*)\\s*[xX×]\\s*(\\d+(?:\\.\\d+)?)/g;
    var categories = {"二": "2", "三": "3", "四": "4", "二三": "2/3", "三四": "3/4", "二三四": "2/3/4"};
    var match;
    while ((match = expression.exec(String(text || ""))) !== null) {
      var category = categories[match[1]] || match[1].replace(/\\s+/g, "");
      rules.push(category + "X" + match[2]);
    }
    return rules.filter(function(rule, index) { return rules.indexOf(rule) === index; });
  }

  function _qwenCollisionFromRules(rules, previous) {
    var collisions = (rules || []).map(function(rule) {
      var category = String(rule).split("X", 1)[0];
      return category.indexOf("/") >= 0 ? category : null;
    }).filter(function(value) { return !!value; });
    return collisions.length === 1 ? collisions[0] : (collisions.length ? null : (previous == null ? null : previous));
  }

  function _gemmaNumbersFromFragment(text) {
    // Gate 3A currently supports 539 only for these literal suggestions.
    // One-digit play fragments and 40-49 must not leak into number groups.
    var matches = String(text || "").match(/(?:^|\D)(0[1-9]|[12]\d|3[0-9])(?=\D|$)/g) || [];
    return matches.map(function(value) {
      var match = String(value).match(/(0[1-9]|[12]\d|3[0-9])/);
      return match ? match[1] : "";
    }).filter(function(value) { return !!value; });
  }

  function _gemmaTextWithoutMultiplier(item) {
    var raw = String(item && item.raw_text || "");
    var multiplier = String(item && item.multiplier_text || "").trim();
    if (!multiplier || multiplier.toLowerCase() === "none") return raw;
    var offset = raw.toLowerCase().lastIndexOf(multiplier.toLowerCase());
    return offset < 0 ? raw : raw.slice(0, offset) + " " + raw.slice(offset + multiplier.length);
  }

  function _gemmaNumberGroups(item, card) {
    var text = _gemmaTextWithoutMultiplier(item);
    var layout = String(item && item.layout_guess || (card.staged_structure || {}).layout || "unclear");
    if (layout === "column" || layout === "column_bet") {
      var lines = text.split(/\\r?\\n/).map(function(line) { return line.trim(); }).filter(function(line) { return !!line; });
      if (!lines.length) return [];
      var parts = lines[0].split(/\s*[xX×]\s*/);
      if (parts.length < 2) return [];
      var groups = parts.map(_gemmaNumbersFromFragment);
      if (!groups.every(function(group) { return group.length > 0; })) return [];
      // A continuation line without another column separator can only extend
      // the last visible column.  Any later line containing x is ambiguous and
      // remains unavailable rather than being guessed into a structure.
      for (var i = 1; i < lines.length; i++) {
        if (/[xX×]/.test(lines[i])) return [];
        var continuation = _gemmaNumbersFromFragment(lines[i]);
        if (!continuation.length) continue;
        groups[groups.length - 1] = groups[groups.length - 1].concat(continuation);
      }
      return groups;
    }
    var numbers = _gemmaNumbersFromFragment(text);
    return numbers.length ? [numbers] : [];
  }

  function _gemmaMultiplierRules(item) {
    var text = String(item && item.multiplier_text || "").toUpperCase();
    var expression = /(?:^|[^0-9])((?:[234](?:\s*\/\s*[234])*)\s*X\s*\d+(?:\.\d+)?)(?=$|[^0-9.])/g;
    var rules = [];
    var match;
    while ((match = expression.exec(text)) !== null) {
      rules.push(match[1].replace(/\s+/g, ""));
    }
    return rules.filter(function(rule, index) { return rules.indexOf(rule) === index; });
  }

  function _sameReviewValue(left, right) {
    return JSON.stringify(left || []) === JSON.stringify(right || []);
  }

  function _qwenCanonicalLayout(layout) {
    layout = String(layout || "").toLowerCase();
    if (layout === "normal" || layout === "normal_row") return "normal_row";
    if (layout === "column" || layout === "column_bet") return "column_bet";
    return "unknown";
  }

  function _qwenModelRules(model) {
    var value = model && model.multiplier;
    if (Array.isArray(value)) return value.map(String).filter(function(item) { return !!item; });
    return value == null || value === "" ? [] : [String(value).toUpperCase()];
  }

  function _qwenReviewFieldConflicts(card) {
    var staged = card.staged_structure || {};
    var model = card.model_candidate || {};
    var qwenNumbers = _qwenNormalizeGroups(model.numbers || []);
    var qwenRules = _qwenModelRules(model);
    var qwenLayout = _qwenCanonicalLayout(model.layout_hint);
    var stagedNumbers = _qwenNormalizeGroups(staged.number_groups || []);
    var stagedRules = Array.isArray(staged.multiplier_rules) ? staged.multiplier_rules.map(String) : [];
    var stagedLayout = _qwenCanonicalLayout(staged.layout);
    var conflicts = {
      numbers: qwenNumbers.length > 0 && !_sameReviewValue(qwenNumbers, stagedNumbers),
      multiplier: qwenRules.length > 0 && !_sameReviewValue(qwenRules, stagedRules),
      layout: qwenLayout !== "unknown" && stagedLayout !== "unknown" && qwenLayout !== stagedLayout
    };
    var gemma = card.evidence_sources && card.evidence_sources.gemma;
    if (gemma) {
      var gemmaGroups = _gemmaNumberGroups(gemma, card);
      var gemmaRules = _gemmaMultiplierRules(gemma);
      var gemmaLayout = _qwenCanonicalLayout(gemma.layout_guess);
      conflicts.numbers = conflicts.numbers || (gemmaGroups.length > 0 && !_sameReviewValue(gemmaGroups, stagedNumbers));
      conflicts.multiplier = conflicts.multiplier || (gemmaRules.length > 0 && !_sameReviewValue(gemmaRules, stagedRules));
      conflicts.layout = conflicts.layout || (gemmaLayout !== "unknown" && stagedLayout !== "unknown" && gemmaLayout !== stagedLayout);
    }
    var cropConflicts = Array.isArray(card.crop_reread_conflicts)
      ? card.crop_reread_conflicts : [];
    conflicts.numbers = conflicts.numbers || cropConflicts.indexOf("number_groups") >= 0;
    conflicts.multiplier = conflicts.multiplier ||
      cropConflicts.indexOf("multiplier_rules") >= 0 ||
      cropConflicts.indexOf("multiplier_raw") >= 0;
    conflicts.layout = conflicts.layout || cropConflicts.indexOf("layout") >= 0;
    return conflicts;
  }

  // Product review cards intentionally show concise suggestions only. Raw
  // model payloads and unlinked candidates remain in the folded evidence area.
  function _qwenGemmaEvidenceHtml(card, index) {
    var sources = card.evidence_sources || {};
    var qwen = sources.qwen || {};
    var gemma = sources.gemma;
    var model = qwen.model_candidate || {};
    var reconstructed = qwen.reconstructed_candidate || {};
    var html = '<section class="review-ai-suggestions" style="margin-top:8px;padding:7px;background:#f8fafc;border:1px solid #cbd5e1;border-radius:5px">' +
      '<strong>AI 建議</strong>' +
      '<div class="qwen-suggestion-summary" style="font-size:12px;margin-top:4px">Qwen：號碼 ' +
      esc(JSON.stringify(model.numbers || reconstructed.number_groups || [])) +
      '；倍率 ' + esc(JSON.stringify(model.multiplier == null ? (reconstructed.multiplier_rules || []) : model.multiplier)) +
      '；版型 ' + esc(_qwenLayoutLabel(model.layout_hint || reconstructed.layout || "unknown")) + '</div>';
    if (!gemma) {
      html += '<div class="gemma-card-source-unavailable" style="margin-top:5px;color:#64748b">Gemma 尚未由人工掛到本卡；不使用位置、文字或索引補位。候選與原始證據收在進階證據。</div>';
      return html + '</section>';
    }
    var staged = card.staged_structure || {};
    var gemmaGroups = _gemmaNumberGroups(gemma, card);
    var gemmaRules = _gemmaMultiplierRules(gemma);
    var gemmaLayout = _qwenCanonicalLayout(gemma.layout_guess);
    var numbersDiffer = gemmaGroups.length && !_sameReviewValue(_qwenNormalizeGroups(staged.number_groups || []), gemmaGroups);
    var multiplierDiffer = gemmaRules.length && !_sameReviewValue(staged.multiplier_rules || [], gemmaRules);
    var layoutDiffer = gemmaLayout !== "unknown" && gemmaLayout !== _qwenCanonicalLayout(staged.layout);
    html += '<div class="gemma-card-source" data-evidence-id="' + esc(gemma.evidence_id || '') + '" style="margin-top:7px;padding-top:6px;border-top:1px solid #e2e8f0">' +
      '<strong>Gemma 建議</strong>' +
      '<div class="gemma-suggestion-summary">號碼 ' + esc(JSON.stringify(gemmaGroups)) +
      '；倍率 ' + esc(JSON.stringify(gemmaRules)) + '；版型 ' + esc(_qwenLayoutLabel(gemmaLayout)) + '</div>';
    if (numbersDiffer || multiplierDiffer || layoutDiffer) {
      html += '<div class="gemma-card-disagreement" style="color:#9a3412;font-weight:700">AI 來源有欄位差異；請只採用要變更的欄位。</div>';
    }
    if (gemmaGroups.length && numbersDiffer) {
      html += '<button type="button" class="adopt-gemma-numbers" onclick="qwenReviewAdoptGemmaNumbers(' + index + ')">採用 Gemma 號碼</button> ';
    }
    if (gemmaRules.length && multiplierDiffer) {
      html += '<button type="button" class="adopt-gemma-multiplier" onclick="qwenReviewAdoptGemmaMultiplier(' + index + ')">採用 Gemma 倍率</button> ';
    }
    if (gemmaLayout !== "unknown" && layoutDiffer) {
      html += '<button type="button" class="adopt-gemma-layout" onclick="qwenReviewAdoptGemmaLayout(' + index + ')">採用 Gemma 版型</button>';
    }
    html += '</div>';
    var crop = sources.crop_reread || null;
    if (crop && crop.suggestion) {
      var cropSuggestion = crop.suggestion || {};
      html += '<div class="gemma-crop-reread-source" style="margin-top:7px;padding-top:6px;border-top:1px solid #e2e8f0">' +
        '<strong>Gemma 放大區域二次檢查</strong>' +
        '<div>號碼 ' + esc(JSON.stringify(cropSuggestion.number_groups_suggestion || [])) +
        '；倍率 ' + esc(JSON.stringify(cropSuggestion.multiplier_rules_suggestion || cropSuggestion.multiplier_raw || [])) +
        '；版型 ' + esc(_qwenLayoutLabel(cropSuggestion.layout_suggestion || "unknown")) +
        '；特殊 ' + esc(String(cropSuggestion.special_play_raw || "none")) + '</div>';
      if (String(crop.status || "") === "AI_RECHECK_CONFLICT") {
        html += '<div class="gemma-crop-reread-conflict" style="color:#9a3412;font-weight:700">AI_RECHECK_CONFLICT｜整頁與放大影像不同，請由人工判斷；未自動覆蓋 Human Answer。</div>';
      }
      html += '</div>';
    }
    return html + '</section>';
  }

  function _qwenAdvancedGemmaCandidatesHtml(card, index) {
    var unlinked = qwenReviewSession && Array.isArray(qwenReviewSession.unlinked_gemma_items)
      ? qwenReviewSession.unlinked_gemma_items : [];
    var html = '<div class="review-evidence-source-slots">sources=Qwen / Gemma / PP / Codex / Human Answer</div>';
    var selected = card.evidence_sources && card.evidence_sources.gemma;
    if (selected) html += '<div class="gemma-selected-raw">Gemma selected raw=' + esc(JSON.stringify(selected)) + '</div>';
    if (!unlinked.length) return html;
    html += '<div class="gemma-unlinked-candidates" style="margin-top:6px"><strong>未掛接 Gemma 候選（必須由人工點選）</strong>';
    for (var candidateIndex = 0; candidateIndex < unlinked.length; candidateIndex++) {
      var candidate = unlinked[candidateIndex] || {};
      var candidateGroups = _gemmaNumberGroups(candidate, card);
      var candidateRules = _gemmaMultiplierRules(candidate);
      var candidateLayout = _qwenCanonicalLayout(candidate.layout_guess);
      html += '<div class="gemma-unlinked-candidate" data-evidence-id="' + esc(candidate.evidence_id || '') + '" style="padding:5px 0;border-top:1px dashed #cbd5e1">' +
        '<div><strong>' + esc(candidate.evidence_id || String(candidateIndex + 1)) + '</strong> raw=' + esc(candidate.raw_text || '') + '</div>';
      if (candidateGroups.length) html += '<button type="button" class="adopt-gemma-candidate-numbers" onclick="qwenReviewAdoptGemmaCandidateNumbers(' + index + ',' + candidateIndex + ')">採用此候選號碼</button> ';
      if (candidateRules.length) html += '<button type="button" class="adopt-gemma-candidate-multiplier" onclick="qwenReviewAdoptGemmaCandidateMultiplier(' + index + ',' + candidateIndex + ')">採用此候選倍率</button> ';
      if (candidateLayout !== "unknown") html += '<button type="button" class="adopt-gemma-candidate-layout" onclick="qwenReviewAdoptGemmaCandidateLayout(' + index + ',' + candidateIndex + ')">採用此候選版型</button>';
      html += '</div>';
    }
    return html + '</div>';
  }

  function _qwenCardStructureHtml(card) {
    var structure = card.staged_structure || {};
    var groups = _qwenNormalizeGroups(structure.number_groups || []);
    var layout = _qwenCanonicalLayout(structure.layout);
    var conflicts = _qwenReviewFieldConflicts(card);
    function fieldStyle(conflict) {
      return conflict ? 'border:2px solid #dc2626;background:#fff7f7;' : 'border:1px solid #e2e8f0;background:#fff;';
    }
    function conflictText(conflict) {
      return conflict ? '<div class="review-field-conflict" style="font-size:11px;color:#b91c1c;font-weight:700">AI 來源不同，請檢查此欄位</div>' : '';
    }
    var html = '<div class="human-answer-fields" style="display:grid;gap:6px">';
    html += '<div class="review-field" data-field="layout" data-conflict="' + String(conflicts.layout) + '" style="padding:6px;' + fieldStyle(conflicts.layout) + '">' +
      '<strong>Human Answer・版型：</strong>' + esc(_qwenLayoutLabel(layout)) + conflictText(conflicts.layout) + '</div>';
    html += '<div class="review-field" data-field="numbers" data-conflict="' + String(conflicts.numbers) + '" style="padding:6px;' + fieldStyle(conflicts.numbers) + '">' +
      '<strong>Human Answer・號碼</strong>' + conflictText(conflicts.numbers);
    if (layout === "column_bet") {
      html += '<div class="qwen-review-columns" style="display:grid;gap:4px;margin-top:5px">';
      for (var i = 0; i < groups.length; i++) {
        html += '<div class="qwen-review-column" data-column-index="' + i + '" style="font-family:monospace;font-size:16px">' + esc(groups[i].join(" ")) + '</div>';
      }
      html += '</div>';
    } else {
      html += '<div class="qwen-review-numbers" style="font-family:monospace;font-size:17px;margin-top:5px">' +
        esc(groups.map(function(group) { return group.join(" "); }).join(" / ") || "無") + '</div>';
    }
    html += '</div>';
    var rules = Array.isArray(structure.multiplier_rules) ? structure.multiplier_rules : [];
    html += '<div class="review-field" data-field="multiplier" data-conflict="' + String(conflicts.multiplier) + '" style="padding:6px;' + fieldStyle(conflicts.multiplier) + '">' +
      '<div class="qwen-review-play"><strong>Human Answer・倍率：</strong>' + esc(rules.length ? rules.join("、") : "未辨識／需要人工處理") +
      (structure.multiplier_scope == null || structure.multiplier_scope === "" ? '' : '；範圍=' + esc(structure.multiplier_scope)) + '</div>' +
      conflictText(conflicts.multiplier);
    if (structure.collision != null) html += '<div class="qwen-review-collision"><strong>疊寫類別：</strong>' + esc(structure.collision) + '</div>';
    html += '</div>';
    var specialKeys = ["continuation", "tail", "尾", "car", "車", "half_car", "半車", "each", "各", "special_text", "play_text", "scope"];
    var special = [];
    for (var s = 0; s < specialKeys.length; s++) {
      var key = specialKeys[s];
      if (Object.prototype.hasOwnProperty.call(structure, key) && structure[key] != null && structure[key] !== "") {
        special.push(key + '=' + JSON.stringify(structure[key]));
      }
    }
    if (special.length) html += '<div class="review-field review-special-fields" data-field="special" style="padding:6px;border:1px solid #e2e8f0;background:#fff"><strong>特殊／範圍：</strong>' + esc(special.join('；')) + '</div>';
    html += '<label class="review-cancelled-field" style="display:flex;gap:6px;align-items:center;font-size:12px" onclick="event.stopPropagation()">' +
      '<input type="checkbox" class="qwen-card-cancelled" ' + (_qwenIsCancelledStructure(structure) ? 'checked ' : '') +
      'onchange="qwenReviewSetCancelled(' + qwenReviewSession.structures.indexOf(card) + ',this.checked)">此筆取消／塗改，不列為 active 投注</label>';
    return html + '</div>';
  }

  function _qwenPreviewHtml(card, index) {
    if (card.preview_error) {
      return '<div class="qwen-card-preview-error" style="color:#b91c1c;margin-top:6px">' + esc(card.preview_error) + '</div>';
    }
    var data = card.reparse_preview;
    if (!data) return '<div class="qwen-card-preview-empty" style="color:#64748b;margin-top:6px">尚未重新解析。</div>';
    return '<div class="qwen-card-preview" style="margin-top:8px;padding:7px;background:#f8fafc;border:1px solid #cbd5e1;border-radius:4px">' +
      '<strong>修改後預覽</strong><br>' +
      '號碼：' + esc(JSON.stringify(data.numbers)) + '<br>' +
      '星別：' + esc(JSON.stringify(data.stars)) + '<br>' +
      '金額：' + esc(JSON.stringify(data.amounts)) + '<br>' +
      '類型：' + esc(data.type || '-') + '<br>' +
      '欄位：' + esc(JSON.stringify(data.columns)) + '<br>' +
      '摘要：' + esc(data.summary || '') + '<br>' +
      'auto_confirm=false｜auto_submit=false<br>' +
      '<button type="button" class="qwen-adopt-edit" style="margin-top:6px;background:#0f766e;color:#fff" onclick="event.stopPropagation();qwenReviewAdoptEdit(' + index + ')">採用修改</button>' +
      '</div>';
  }

  function _qwenSpecialInputValue(value) {
    if (value == null || value === false) return "";
    if (typeof value === "string") return value;
    return JSON.stringify(value);
  }

  function _qwenSpecialFieldValue(structure, field) {
    var aliases = {tail: "尾", car: "車", half_car: "半車", each: "各"};
    if (structure[field] != null && structure[field] !== "") return structure[field];
    var alias = aliases[field];
    return alias && structure[alias] != null ? structure[alias] : null;
  }

  function _qwenSpecialEditorHtml(card, index) {
    var structure = card.staged_structure || {};
    return '<fieldset class="qwen-special-editor" style="margin-top:8px;padding:7px;border:1px solid #cbd5e1"><legend>特殊／範圍（Human Answer）</legend>' +
      '<label style="display:block"><input type="checkbox" class="qwen-special-continuation" ' +
      (structure.continuation ? 'checked ' : '') + '> continuation</label>' +
      '<label style="display:block">尾 <input class="qwen-special-tail" value="' + esc(_qwenSpecialInputValue(_qwenSpecialFieldValue(structure, "tail"))) + '"></label>' +
      '<label style="display:block">車 <input class="qwen-special-car" value="' + esc(_qwenSpecialInputValue(_qwenSpecialFieldValue(structure, "car"))) + '"></label>' +
      '<label style="display:block">半車 <input class="qwen-special-half-car" value="' + esc(_qwenSpecialInputValue(_qwenSpecialFieldValue(structure, "half_car"))) + '"></label>' +
      '<label style="display:block">各 <input class="qwen-special-each" value="' + esc(_qwenSpecialInputValue(_qwenSpecialFieldValue(structure, "each"))) + '"></label>' +
      '<label style="display:block">特殊玩法 <input class="qwen-special-text" value="' + esc(_qwenSpecialInputValue(structure.special_text)) + '"></label>' +
      '<label style="display:block">範圍 <input class="qwen-special-scope" value="' + esc(_qwenSpecialInputValue(structure.scope)) + '"></label>' +
      '<button type="button" class="qwen-adopt-special-fields" style="margin-top:6px" onclick="qwenReviewAdoptSpecialFields(' + index + ')">採用特殊／範圍修改</button>' +
      '</fieldset>';
  }

  function _qwenReviewCardHtml(card, index) {
    var stateLabel = card.review_state === "confirmed" ? "已確認" :
      (card.review_state === "editing" ? "需要修改" : "待確認");
    var stateColor = card.review_state === "confirmed" ? "#15803d" :
      (card.review_state === "editing" ? "#b45309" : "#475569");
    var active = qwenReviewSession.active_structure_id === card.structure_id;
    var html = '<article class="qwen-review-card" data-structure-id="' + esc(card.structure_id) +
      '" data-review-state="' + esc(card.review_state) + '" onclick="qwenReviewSelect(' + index + ')" style="padding:11px;border:2px solid ' +
      (active ? '#f97316' : '#e2e8f0') + ';border-radius:8px;background:#fff;cursor:pointer">';
    html += '<div style="display:flex;justify-content:space-between;gap:8px;align-items:start">' +
      '<strong>投注 ' + (index + 1) + '</strong>' +
      '<span class="qwen-review-state" style="font-size:12px;color:' + stateColor + ';font-weight:700">' + esc(stateLabel) + '</span></div>';
    if (card.draft_classification === "AI_UNCERTAIN") {
      html += '<div class="runtime-draft-classification runtime-draft-provisional" style="margin-top:6px;padding:6px;background:#fff7ed;color:#9a3412;font-weight:700">' +
        'AI_UNCERTAIN｜AI 對這筆分組不確定，請檢查。</div>';
    } else if (card.draft_classification === "SAFE_DRAFT") {
      html += '<div class="runtime-draft-classification runtime-draft-safe" style="margin-top:6px;color:#166534;font-size:12px;font-weight:700">SAFE_DRAFT</div>';
    }
    if (card.crop_reread_status) {
      var rereadLabel = card.crop_reread_status === "AI_REREAD_CONFIRMED"
        ? "AI 已二次檢查" : "AI 仍不確定";
      html += '<div class="runtime-crop-reread-badge" data-crop-reread-status="' +
        esc(card.crop_reread_status) + '" style="margin-top:5px;font-size:12px;font-weight:700;color:#1d4ed8">' +
        esc(rereadLabel) + '</div>';
    }
    html += '<div class="review-image-context" style="font-size:11px;color:#64748b;margin-top:4px">原圖位置：有可信 region provenance 才顯示高亮；否則保留完整原圖，不猜 bbox。</div>';
    html += '<div class="qwen-review-human-status" style="margin:6px 0;color:#9a3412;font-weight:700">' +
      esc(_qwenHumanStatus(card.source_status)) + '</div>';
    html += _qwenAiLiteralHtml(card);
    html += _qwenCardStructureHtml(card);
    html += _qwenQuickCorrectionHtml(card, index);
    html += _qwenGemmaEvidenceHtml(card, index);
    if (card.manual_edits.length) {
      html += '<div class="qwen-manual-edit-marker" style="font-size:12px;color:#0f766e;margin-top:5px">已採用人工修改</div>';
    }
    if (card.review_state === "editing") {
      var editLayout = _qwenCanonicalLayout((card.staged_structure || {}).layout);
      html += '<div class="qwen-card-editor" style="margin-top:8px;padding-top:8px;border-top:1px solid #e2e8f0" onclick="event.stopPropagation()">' +
        '<label style="display:block;font-weight:700;margin-bottom:4px">可編輯表示</label>' +
        '<textarea class="qwen-card-editable" data-card-index="' + index + '" style="width:100%;min-height:68px;font-family:monospace;padding:6px">' + esc(card.edit_text) + '</textarea>' +
        '<div style="margin-top:6px"><button type="button" class="qwen-card-reparse" onclick="qwenReviewReparse(' + index + ')">重新解析</button> ' +
        '<button type="button" class="qwen-card-cancel" onclick="qwenReviewCancelEdit(' + index + ')">取消</button></div>' +
        '<label style="display:block;margin-top:6px">版型 <select class="qwen-card-layout-edit" data-card-index="' + index + '">' +
        '<option value="unknown" ' + (editLayout === 'unknown' ? 'selected ' : '') + '>待人工判斷</option>' +
        '<option value="normal_row" ' + (editLayout === 'normal_row' ? 'selected ' : '') + '>一般</option>' +
        '<option value="column_bet" ' + (editLayout === 'column_bet' ? 'selected ' : '') + '>柱碰</option></select></label>' +
        _qwenSpecialEditorHtml(card, index) + _qwenPreviewHtml(card, index) + '</div>';
    } else {
      html += '<div style="margin-top:8px"><button type="button" class="qwen-edit-structure" onclick="event.stopPropagation();qwenReviewStartEdit(' + index + ')">修改</button> ' +
        '<button type="button" class="qwen-confirm-structure" onclick="event.stopPropagation();qwenReviewConfirm(' + index + ')">' +
        (card.review_state === "confirmed" ? "取消確認" : "確認此筆") + '</button></div>';
    }
    html += '<details class="qwen-card-advanced" style="font-size:11px;color:#64748b;margin-top:7px" onclick="event.stopPropagation()"><summary>進階資訊</summary>' +
      '<div>structure_id=' + esc(card.structure_id) + '｜primary_line_id=' + esc(card.primary_line_id) + '</div>' +
      '<div>source_line_ids=' + esc(JSON.stringify(card.source_line_ids)) + '</div>' +
      '<div>technical_status=' + esc(card.source_status) + '</div>' +
      '<div>draft_classification=' + esc(card.draft_classification || "") + '</div>' +
      '<div>model_candidate=' + esc(JSON.stringify(card.model_candidate || {})) + '</div>' +
      '<div>reconstructed_candidate=' + esc(JSON.stringify(card.original_structure || {})) + '</div>' +
      '<div>staged_structure=' + esc(JSON.stringify(card.staged_structure || {})) + '</div>' +
      '<div>field_sources=' + esc(JSON.stringify(card.field_sources || {})) + '</div>' +
      '<div>field_corrections=' + esc(JSON.stringify(card.field_corrections || [])) + '</div>' +
      '<div>suggestion_adoptions=' + esc(JSON.stringify(card.suggestion_adoptions || [])) + '</div>' +
      '<div>warnings=' + esc(JSON.stringify(card.warnings)) + '</div>' +
      '<div>crop_reread_status=' + esc(card.crop_reread_status || "") + '</div>' +
      '<div>crop_reread_geometry=' + esc(JSON.stringify(card.crop_reread_geometry || null)) + '</div>' +
      '<div>crop_reread_suggestion=' + esc(JSON.stringify(card.crop_reread_suggestion || null)) + '</div>' +
      '<div>crop_reread_conflicts=' + esc(JSON.stringify(card.crop_reread_conflicts || [])) + '</div>' +
      _qwenAdvancedGemmaCandidatesHtml(card, index) + '</details>';
    return html + '</article>';
  }

  function _qwenReviewIsActive(card) {
    return !_qwenIsCancelledStructure((card || {}).staged_structure || {});
  }

  function _qwenReviewReadiness() {
    if (!qwenReviewSession || !qwenReviewSession.structures.length) {
      return {ready: false, active_total: 0, active_confirmed: 0, cancelled_total: 0, blocking_unresolved: 0};
    }
    var cards = qwenReviewSession.structures;
    var active = cards.filter(_qwenReviewIsActive);
    var cancelled = cards.filter(function(card) { return !_qwenReviewIsActive(card); });
    var activeConfirmed = active.filter(function(card) { return card.review_state === "confirmed" && card.human_confirmed === true; }).length;
    var cancelledHandled = cancelled.filter(function(card) { return card.review_state === "confirmed" && card.human_confirmed === true; }).length;
    var blocking = active.filter(function(card) {
      return card.source_status !== "consistent" && card.blocking_resolved_by_human !== true;
    }).length;
    return {
      ready: active.length > 0 && activeConfirmed === active.length && cancelledHandled === cancelled.length && blocking === 0,
      active_total: active.length,
      active_confirmed: activeConfirmed,
      cancelled_total: cancelled.length,
      cancelled_handled: cancelledHandled,
      blocking_unresolved: blocking
    };
  }

  function _qwenActiveReviewIndex() {
    if (!qwenReviewSession) return -1;
    for (var i = 0; i < qwenReviewSession.structures.length; i++) {
      if (qwenReviewSession.structures[i].structure_id === qwenReviewSession.active_structure_id) return i;
    }
    return qwenReviewSession.structures.length ? 0 : -1;
  }

  function _qwenVisibleReviewIndices() {
    if (!qwenReviewSession) return [];
    var result = [];
    for (var i = 0; i < qwenReviewSession.structures.length; i++) {
      if (qwenReviewSession.show_pending_only && qwenReviewSession.structures[i].review_state === "confirmed") continue;
      result.push(i);
    }
    return result;
  }

  function _qwenCompactCardHtml(card, index, active) {
    var groups = _qwenNormalizeGroups((card.staged_structure || {}).number_groups || []);
    var summary = groups.map(function(group) { return group.join(" "); }).join(" × ") || "未辨識號碼";
    var state = card.review_state === "confirmed" ? "已確認" : (card.review_state === "editing" ? "編輯中" : "待確認");
    return '<button type="button" class="qwen-review-compact-item" data-structure-id="' + esc(card.structure_id) +
      '" data-active="' + String(active) + '" onclick="qwenReviewSelect(' + index + ')" style="width:100%;text-align:left;padding:6px;border:' +
      (active ? '2px solid #f97316' : '1px solid #cbd5e1') + ';background:#fff;border-radius:5px">' +
      '<strong>第 ' + (index + 1) + ' 筆</strong> · ' + esc(state) + ' · <span class="qwen-compact-number-summary">' + esc(summary) + '</span></button>';
  }

  function _renderQwenReviewSession() {
    var target = document.getElementById("qwen-review-session");
    if (!target || !qwenReviewSession) return;
    var cards = qwenReviewSession.structures;
    var readiness = _qwenReviewReadiness();
    var confirmed = cards.filter(function(card) { return card.review_state === "confirmed"; }).length;
    var pending = cards.length - confirmed;
    var activeIndex = _qwenActiveReviewIndex();
    var visibleIndices = _qwenVisibleReviewIndices();
    if (visibleIndices.length && visibleIndices.indexOf(activeIndex) < 0) {
      activeIndex = visibleIndices[0];
      qwenReviewSession.active_structure_id = cards[activeIndex].structure_id;
    }
    var visiblePosition = visibleIndices.indexOf(activeIndex);
    var html = '<section class="qwen-review-session-panel">' +
      '<div class="qwen-review-toolbar" style="display:flex;flex-wrap:wrap;justify-content:space-between;gap:8px;align-items:center;margin:8px 0">' +
      '<div><strong id="qwen-review-progress">已確認 ' + readiness.active_confirmed + ' / 總共 ' + readiness.active_total + ' active</strong>' +
      '<div id="qwen-review-counts" style="font-size:12px;color:#64748b">confirmed=' + confirmed + '；pending=' + pending +
      '；unresolved=' + readiness.blocking_unresolved + '；cancelled=' + readiness.cancelled_total + '</div></div>' +
      '<div><span id="qwen-review-position">第 ' + (visiblePosition < 0 ? 0 : visiblePosition + 1) + ' / ' + visibleIndices.length + ' 筆</span> ' +
      '<button type="button" id="qwen-review-previous" onclick="qwenReviewPrevious()" ' + (visiblePosition <= 0 ? 'disabled ' : '') + '>上一筆</button> ' +
      '<button type="button" id="qwen-review-next" onclick="qwenReviewNext()" ' + (visiblePosition < 0 || visiblePosition >= visibleIndices.length - 1 ? 'disabled ' : '') + '>下一筆</button></div>' +
      '<label style="font-size:12px"><input id="qwen-pending-only" type="checkbox" ' +
      (qwenReviewSession.show_pending_only ? 'checked ' : '') + 'onchange="qwenReviewTogglePending(this.checked)"> 只看待確認</label></div>';
    var authorityState = String(qwenReviewSession.server_state || "unpersisted");
    var authorityColor = authorityState === "ready" ? "#166534" : (authorityState === "saving" ? "#475569" : "#b91c1c");
    var authorityText = authorityState === "ready"
      ? "Human Answer 已安全保存"
      : (authorityState === "saving" ? "正在保存 Server Human Review…" :
        (authorityState === "stale" ? "Human Answer 已變更，請重新載入並確認。" : "Server Human Review 尚未可用。"));
    html += '<div id="qwen-authority-status" data-authority-state="' + esc(authorityState) + '" style="padding:6px 8px;margin-bottom:7px;background:#f8fafc;color:' + authorityColor + '">' + esc(authorityText) +
      (authorityState === "stale" || authorityState === "error" ? ' <button type="button" id="qwen-reload-authority" onclick="qwenReloadReviewAuthority()">重新載入</button>' : '') +
      '<details class="qwen-authority-advanced" style="font-size:11px;color:#64748b"><summary>進階資訊</summary>revision=' +
      esc(qwenReviewSession.human_answer_revision == null ? '-' : qwenReviewSession.human_answer_revision) +
      '｜hash=' + esc(String(qwenReviewSession.human_answer_hash || "").slice(0, 12)) + '</details></div>';
    if (qwenReviewSession.provider_failure) {
      html += '<div class="qwen-provider-failure-review-note" style="padding:7px;background:#fff7ed;color:#9a3412">Qwen 未完成；可使用既有 Gemma／PP 證據或人工新增，不會自動建立候選。</div>';
    }
    if (!cards.length) html += '<div class="qwen-no-primary-structures" style="padding:9px;background:#fef2f2;color:#991b1b">沒有可用的 AI 投注卡；請人工新增。</div>';
    html += '<button type="button" id="qwen-add-manual-structure" onclick="qwenReviewAddManualStructure()">＋ 新增人工投注</button>' +
      '<div id="qwen-review-compact-list" style="display:grid;gap:4px;margin-top:8px">';
    for (var compact = 0; compact < visibleIndices.length; compact++) {
      var compactIndex = visibleIndices[compact];
      html += _qwenCompactCardHtml(cards[compactIndex], compactIndex, compactIndex === activeIndex);
    }
    html += '</div><div id="qwen-review-cards" style="display:grid;gap:9px;margin-top:8px">';
    if (activeIndex >= 0 && visibleIndices.indexOf(activeIndex) >= 0) html += _qwenReviewCardHtml(cards[activeIndex], activeIndex);
    html += '</div>';
    if (readiness.ready && authorityState === "ready") {
      html += '<button type="button" id="mvp-local-sandbox-fill" class="btn-primary" style="margin-top:10px" onclick="mvpFillLocalSandbox()">輔助填入本機測試頁</button>' +
        '<button type="button" id="qwen-complete-review" style="display:none" onclick="qwenCompleteReview()">建立 Candidate</button>' +
        '<div id="qwen-candidate-boundary-status" style="font-size:11px;color:#64748b">後台會重新驗證 Human Review，建立 immutable authority chain；只填 127.0.0.1，不送出。</div>';
    } else {
      var missing = [];
      if (readiness.active_confirmed !== readiness.active_total) missing.push("尚有未確認 active 投注");
      if (readiness.cancelled_handled !== readiness.cancelled_total) missing.push("尚有未確認取消項目");
      if (readiness.blocking_unresolved) missing.push("blocking unresolved=" + readiness.blocking_unresolved);
      if (authorityState !== "ready") missing.push("Server Human Review 尚未同步");
      html += '<div id="qwen-candidate-boundary-status" style="margin-top:10px;color:#9a3412">' + esc(missing.join("；") || "尚未符合 Candidate 建立條件") + '</div>';
    }
    html += '<div id="qwen-review-completion" style="margin-top:8px"></div></section>';
    target.innerHTML = html;
    if (qwenReviewSession.server_candidate) _renderQwenCompletion(qwenReviewSession.server_candidate);
    _updateQwenStructureHighlight();
  }

  function _qwenBoundingExtent(box) {
    if (!box || (box.coordinate_space !== "pixel" && box.coordinate_space !== "normalized") || !Array.isArray(box.polygon)) return null;
    var xs = [];
    var ys = [];
    for (var i = 0; i < box.polygon.length; i++) {
      var point = box.polygon[i];
      if (!Array.isArray(point) || point.length < 2 || !Number.isFinite(Number(point[0])) || !Number.isFinite(Number(point[1]))) return null;
      xs.push(Number(point[0]));
      ys.push(Number(point[1]));
    }
    if (!xs.length) return null;
    return {space: box.coordinate_space, x1: Math.min.apply(null, xs), y1: Math.min.apply(null, ys), x2: Math.max.apply(null, xs), y2: Math.max.apply(null, ys)};
  }

  function _qwenReliableStructureBox(card) {
    var cropGeometry = card && card.crop_reread_geometry;
    var cropBox = cropGeometry && cropGeometry.bbox;
    var cropWidth = Number(uploadedImageMetadata && uploadedImageMetadata.width || 0);
    var cropHeight = Number(uploadedImageMetadata && uploadedImageMetadata.height || 0);
    if (Array.isArray(cropBox) && cropBox.length === 4 && cropWidth > 0 && cropHeight > 0) {
      var cropValues = cropBox.map(Number);
      if (cropValues.every(Number.isFinite) && cropValues[0] >= 0 && cropValues[1] >= 0 &&
          cropValues[2] <= cropWidth && cropValues[3] <= cropHeight &&
          cropValues[2] > cropValues[0] && cropValues[3] > cropValues[1]) {
        return {x1:cropValues[0], y1:cropValues[1], x2:cropValues[2], y2:cropValues[3], width:cropWidth, height:cropHeight};
      }
    }
    if (!qwenEvidenceResult) return null;
    var lineIds = Object.create(null);
    card.source_line_ids.forEach(function(lineId) { lineIds[String(lineId)] = true; });
    var lines = Array.isArray(qwenEvidenceResult.lines) ? qwenEvidenceResult.lines : [];
    var extents = [];
    for (var i = 0; i < lines.length; i++) {
      var line = lines[i] || {};
      if (!lineIds[String(line.line_id || "")]) continue;
      var extent = _qwenBoundingExtent(line.bounding_box);
      if (extent) {
        extents.push(extent);
        continue;
      }
      var tokens = Array.isArray(line.tokens) ? line.tokens : [];
      for (var t = 0; t < tokens.length; t++) {
        var tokenExtent = _qwenBoundingExtent(tokens[t] && tokens[t].bounding_box);
        if (tokenExtent) extents.push(tokenExtent);
      }
    }
    if (!extents.length) return null;
    var space = extents[0].space;
    if (extents.some(function(item) { return item.space !== space; })) return null;
    var source = qwenEvidenceResult.source_image || {};
    var width = Number(source.width || (uploadedImageMetadata && uploadedImageMetadata.width) || 0);
    var height = Number(source.height || (uploadedImageMetadata && uploadedImageMetadata.height) || 0);
    if (!(width > 0 && height > 0)) return null;
    var union = {
      x1: Math.min.apply(null, extents.map(function(item) { return item.x1; })),
      y1: Math.min.apply(null, extents.map(function(item) { return item.y1; })),
      x2: Math.max.apply(null, extents.map(function(item) { return item.x2; })),
      y2: Math.max.apply(null, extents.map(function(item) { return item.y2; }))
    };
    if (space === "normalized") {
      if (union.x1 < 0 || union.y1 < 0 || union.x2 > 1 || union.y2 > 1) return null;
      union.x1 *= width; union.x2 *= width; union.y1 *= height; union.y2 *= height;
    } else if (union.x1 < 0 || union.y1 < 0 || union.x2 > width || union.y2 > height) {
      return null;
    }
    if (!(union.x2 > union.x1 && union.y2 > union.y1)) return null;
    union.width = width;
    union.height = height;
    return union;
  }

  function _updateQwenStructureHighlight() {
    var highlight = document.getElementById("vision-structure-highlight");
    var image = document.getElementById("vision-preview-img");
    if (!highlight || !image || !qwenReviewSession || !qwenReviewSession.active_structure_id) {
      if (highlight) highlight.style.display = "none";
      return;
    }
    var card = qwenReviewSession.structures.filter(function(item) {
      return item.structure_id === qwenReviewSession.active_structure_id;
    })[0];
    var box = card ? _qwenReliableStructureBox(card) : null;
    if (!box || !(image.clientWidth > 0 && image.clientHeight > 0)) {
      highlight.style.display = "none";
      return;
    }
    highlight.style.left = (box.x1 / box.width * image.clientWidth) + "px";
    highlight.style.top = (box.y1 / box.height * image.clientHeight) + "px";
    highlight.style.width = ((box.x2 - box.x1) / box.width * image.clientWidth) + "px";
    highlight.style.height = ((box.y2 - box.y1) / box.height * image.clientHeight) + "px";
    highlight.style.display = "block";
  }

  function _recordReviewFieldCorrection(card, field, source, before, after) {
    if (!Array.isArray(card.field_corrections)) card.field_corrections = [];
    card.field_corrections.push({
      field: field,
      source: source,
      before: _cloneJson(before),
      after: _cloneJson(after),
      changed_at: new Date().toISOString(),
      human_confirmed: false
    });
    if (card.evidence_sources && card.evidence_sources.human_answer) {
      card.evidence_sources.human_answer.field_corrections = _cloneJson(card.field_corrections);
    }
  }

  function _invalidateReviewConfirmation(card) {
    card.review_state = "pending";
    card.human_confirmed = false;
    card.blocking_resolved_by_human = card.source_status === "consistent";
    card.reparse_preview = null;
    card.preview_error = "";
    if (card.evidence_sources && card.evidence_sources.human_answer) {
      card.evidence_sources.human_answer.human_confirmed = false;
      card.evidence_sources.human_answer.staged_structure = _cloneJson(card.staged_structure);
      card.evidence_sources.human_answer.field_sources = _cloneJson(card.field_sources || {});
    }
    if (qwenReviewSession) {
      qwenReviewSession.candidate_preview = null;
      qwenReviewSession.boundary_signal = null;
    }
  }

  function _qwenSelectNextUnconfirmed(fromIndex) {
    if (!qwenReviewSession) return;
    var cards = qwenReviewSession.structures;
    for (var offset = 1; offset <= cards.length; offset++) {
      var index = (fromIndex + offset) % cards.length;
      if (cards[index].review_state !== "confirmed") {
        qwenReviewSession.active_structure_id = cards[index].structure_id;
        return;
      }
    }
  }

  window.qwenQuickCancelPreview = function(index) {
    if (!qwenReviewSession || !qwenReviewSession.structures[index]) return;
    delete qwenReviewSession.structures[index].quick_preview;
    _renderQwenReviewSession();
  };

  window.qwenQuickPreviewNormal = function(index) {
    if (!qwenReviewSession || !qwenReviewSession.structures[index]) return;
    var card = qwenReviewSession.structures[index];
    var numbers = _qwenFlattenGroups((card.staged_structure || {}).number_groups || []);
    card.quick_preview = {
      kind: "normal",
      rows: numbers.length ? [numbers] : [],
      groups: numbers.length ? [numbers] : [],
      valid: numbers.length > 0,
      warning: numbers.length ? "" : "目前沒有合法號碼可套用。"
    };
    _renderQwenReviewSession();
  };

  window.qwenQuickSetNormal = function(index) {
    window.qwenQuickPreviewNormal(index);
    window.qwenQuickApplyNormal(index);
  };

  window.qwenQuickApplyNormal = function(index) {
    if (!qwenReviewSession || !qwenReviewSession.structures[index]) return;
    var card = qwenReviewSession.structures[index];
    var preview = card.quick_preview;
    if (!preview || preview.kind !== "normal" || !preview.valid) return;
    var updated = _cloneJson(card.staged_structure || {});
    updated.layout = "normal_row";
    updated.number_groups = [_cloneJson(preview.groups[0])];
    _qwenQuickCommit(index, "set_normal", updated, ["layout", "number_groups"], "explicit_user_action");
  };

  window.qwenQuickPreviewTwoRow = function(index) {
    if (!qwenReviewSession || !qwenReviewSession.structures[index]) return;
    var card = qwenReviewSession.structures[index];
    var rows = _qwenVisibleRows(card);
    var groups = _qwenTransposeRows(rows);
    var warning = "";
    if (rows.length !== 2) warning = "需要剛好兩排可見號碼；請改用柱群編輯器人工整理。";
    else if (rows[0].length !== rows[1].length) warning = "兩排長度不一致，未猜測缺字位置，不能套用。";
    else if (!groups.length) warning = "兩排包含不合法號碼，不能套用。";
    card.quick_preview = {kind:"two_row_column", rows:_cloneJson(rows), groups:groups, valid:groups.length > 0, warning:warning};
    _renderQwenReviewSession();
  };

  window.qwenQuickApplyColumn = function(index) {
    if (!qwenReviewSession || !qwenReviewSession.structures[index]) return;
    var card = qwenReviewSession.structures[index];
    var preview = card.quick_preview;
    if (!preview || preview.kind !== "two_row_column" || !preview.valid) return;
    var updated = _cloneJson(card.staged_structure || {});
    updated.layout = "column_bet";
    updated.number_groups = _cloneJson(preview.groups);
    _qwenQuickCommit(index, "two_rows_to_column", updated, ["layout", "number_groups"], "explicit_preview_apply");
  };

  window.qwenQuickPreviewMerge = function(index) {
    if (!qwenReviewSession || !qwenReviewSession.structures[index] || !qwenReviewSession.structures[index + 1]) return;
    var current = qwenReviewSession.structures[index];
    var next = qwenReviewSession.structures[index + 1];
    var rows = [
      _qwenFlattenGroups((current.staged_structure || {}).number_groups || []),
      _qwenFlattenGroups((next.staged_structure || {}).number_groups || [])
    ];
    var groups = _qwenTransposeRows(rows);
    var warning = rows[0].length !== rows[1].length
      ? "兩筆號碼數量不同，未猜測缺字位置，不能合併。"
      : (!groups.length ? "目前號碼不足或不合法，不能合併。" : "下一筆只會在您確認後移除；兩筆都不會自動確認。");
    current.quick_preview = {kind:"merge_next", rows:rows, groups:groups, valid:groups.length > 0, warning:warning};
    _renderQwenReviewSession();
  };

  window.qwenQuickApplyMerge = function(index) {
    if (!qwenReviewSession || !qwenReviewSession.structures[index] || !qwenReviewSession.structures[index + 1]) return;
    var card = qwenReviewSession.structures[index];
    var removed = qwenReviewSession.structures[index + 1];
    var preview = card.quick_preview;
    if (!preview || preview.kind !== "merge_next" || !preview.valid) return;
    var previous = _cloneJson(card.staged_structure || {});
    var updated = _cloneJson(previous);
    updated.layout = "column_bet";
    updated.number_groups = _cloneJson(preview.groups);
    card.staged_structure = updated;
    card.field_sources.layout = {source:"Human Answer",human_confirmed:false};
    card.field_sources.number_groups = {source:"Human Answer",human_confirmed:false};
    _recordReviewFieldCorrection(card, "layout", "Human Answer", previous.layout, updated.layout);
    _recordReviewFieldCorrection(card, "number_groups", "Human Answer", previous.number_groups || [], updated.number_groups);
    card.manual_edits.push({component:"merge_next_as_column", merged_human_bet_id:removed.human_bet_id, adopted_at:new Date().toISOString(), human_confirmed:false});
    delete card.quick_preview;
    _invalidateReviewConfirmation(card);
    card.blocking_resolved_by_human = false;
    qwenReviewSession.structures.splice(index + 1, 1);
    _renderQwenReviewSession();
    _qwenQueueAuthorityMutation();
  };

  window.qwenGroupOpen = function(index) {
    if (!qwenReviewSession || !qwenReviewSession.structures[index]) return;
    var card = qwenReviewSession.structures[index];
    card.quick_group_draft = _qwenNormalizeGroups((card.staged_structure || {}).number_groups || []);
    if (!card.quick_group_draft.length) card.quick_group_draft = [[""]];
    card.quick_group_error = "";
    _renderQwenReviewSession();
  };

  window.qwenGroupCancel = function(index) {
    if (!qwenReviewSession || !qwenReviewSession.structures[index]) return;
    delete qwenReviewSession.structures[index].quick_group_draft;
    delete qwenReviewSession.structures[index].quick_group_error;
    _renderQwenReviewSession();
  };

  window.qwenGroupSetNumber = function(index, groupIndex, numberIndex, value) {
    var card = qwenReviewSession && qwenReviewSession.structures[index];
    if (!card || !Array.isArray(card.quick_group_draft) || !card.quick_group_draft[groupIndex]) return;
    card.quick_group_draft[groupIndex][numberIndex] = _qwenNumber(value);
  };

  window.qwenGroupAddNumber = function(index, groupIndex) {
    var card = qwenReviewSession && qwenReviewSession.structures[index];
    if (!card || !card.quick_group_draft || !card.quick_group_draft[groupIndex]) return;
    card.quick_group_draft[groupIndex].push("");
    _renderQwenReviewSession();
  };

  window.qwenGroupDeleteNumber = function(index, groupIndex, numberIndex) {
    var card = qwenReviewSession && qwenReviewSession.structures[index];
    if (!card || !card.quick_group_draft || !card.quick_group_draft[groupIndex]) return;
    card.quick_group_draft[groupIndex].splice(numberIndex, 1);
    _renderQwenReviewSession();
  };

  window.qwenGroupMoveNumber = function(index, groupIndex, numberIndex, direction) {
    var card = qwenReviewSession && qwenReviewSession.structures[index];
    var target = groupIndex + direction;
    if (!card || !card.quick_group_draft || !card.quick_group_draft[groupIndex] || !card.quick_group_draft[target]) return;
    var value = card.quick_group_draft[groupIndex].splice(numberIndex, 1)[0];
    card.quick_group_draft[target].push(value);
    _renderQwenReviewSession();
  };

  window.qwenGroupAddColumn = function(index) {
    var card = qwenReviewSession && qwenReviewSession.structures[index];
    if (!card || !Array.isArray(card.quick_group_draft)) return;
    card.quick_group_draft.push([""]);
    _renderQwenReviewSession();
  };

  window.qwenGroupDeleteColumn = function(index, groupIndex) {
    var card = qwenReviewSession && qwenReviewSession.structures[index];
    if (!card || !Array.isArray(card.quick_group_draft)) return;
    card.quick_group_draft.splice(groupIndex, 1);
    _renderQwenReviewSession();
  };

  window.qwenGroupMoveColumn = function(index, groupIndex, direction) {
    var card = qwenReviewSession && qwenReviewSession.structures[index];
    var target = groupIndex + direction;
    if (!card || !card.quick_group_draft || target < 0 || target >= card.quick_group_draft.length) return;
    var value = card.quick_group_draft.splice(groupIndex, 1)[0];
    card.quick_group_draft.splice(target, 0, value);
    _renderQwenReviewSession();
  };

  window.qwenGroupApply = function(index) {
    var card = qwenReviewSession && qwenReviewSession.structures[index];
    if (!card || !Array.isArray(card.quick_group_draft)) return;
    var groups = card.quick_group_draft.map(function(group) { return group.map(_qwenNumber); });
    if (groups.length < 2 || groups.some(function(group) { return !group.length || group.some(function(value) { return !_qwenValidHumanNumber(value); }); })) {
      card.quick_group_error = "柱碰至少需要兩個非空柱，且每個號碼必須是 01–39。";
      _renderQwenReviewSession();
      return;
    }
    var updated = _cloneJson(card.staged_structure || {});
    updated.number_groups = groups;
    updated.layout = "column_bet";
    _qwenQuickCommit(index, "group_editor", updated, ["number_groups", "layout"], "explicit_group_apply");
  };

  function _qwenMultiplierDraft(structure) {
    structure = structure || {};
    var scope = structure.multiplier_scope == null ? "" : String(structure.multiplier_scope);
    return (Array.isArray(structure.multiplier_rules) ? structure.multiplier_rules : []).map(function(rule) {
      var match = String(rule || "").toUpperCase().match(/^([234](?:\\/[234])*)X(\\d+(?:\\.\\d+)?)$/);
      return {category:match ? match[1] : "", value:match ? match[2] : "", scope:scope};
    });
  }

  window.qwenMultiplierOpen = function(index) {
    var card = qwenReviewSession && qwenReviewSession.structures[index];
    if (!card) return;
    card.quick_multiplier_draft = _qwenMultiplierDraft(card.staged_structure);
    if (!card.quick_multiplier_draft.length) card.quick_multiplier_draft = [{category:"2",value:"1",scope:""}];
    card.quick_multiplier_error = "";
    _renderQwenReviewSession();
  };

  window.qwenMultiplierCancel = function(index) {
    var card = qwenReviewSession && qwenReviewSession.structures[index];
    if (!card) return;
    delete card.quick_multiplier_draft;
    delete card.quick_multiplier_error;
    _renderQwenReviewSession();
  };

  window.qwenMultiplierSet = function(index, ruleIndex, field, value) {
    var card = qwenReviewSession && qwenReviewSession.structures[index];
    if (!card || !card.quick_multiplier_draft || !card.quick_multiplier_draft[ruleIndex] || ["category","value","scope"].indexOf(field) < 0) return;
    card.quick_multiplier_draft[ruleIndex][field] = String(value || "").trim();
  };

  window.qwenMultiplierAdd = function(index) {
    var card = qwenReviewSession && qwenReviewSession.structures[index];
    if (!card || !card.quick_multiplier_draft) return;
    card.quick_multiplier_draft.push({category:"2",value:"1",scope:card.quick_multiplier_draft[0] && card.quick_multiplier_draft[0].scope || ""});
    _renderQwenReviewSession();
  };

  window.qwenMultiplierDelete = function(index, ruleIndex) {
    var card = qwenReviewSession && qwenReviewSession.structures[index];
    if (!card || !card.quick_multiplier_draft) return;
    card.quick_multiplier_draft.splice(ruleIndex, 1);
    _renderQwenReviewSession();
  };

  window.qwenMultiplierMove = function(index, ruleIndex, direction) {
    var card = qwenReviewSession && qwenReviewSession.structures[index];
    var target = ruleIndex + direction;
    if (!card || !card.quick_multiplier_draft || target < 0 || target >= card.quick_multiplier_draft.length) return;
    var value = card.quick_multiplier_draft.splice(ruleIndex, 1)[0];
    card.quick_multiplier_draft.splice(target, 0, value);
    _renderQwenReviewSession();
  };

  window.qwenMultiplierApply = function(index) {
    var card = qwenReviewSession && qwenReviewSession.structures[index];
    if (!card || !Array.isArray(card.quick_multiplier_draft)) return;
    var rules = [];
    var scopes = [];
    for (var i = 0; i < card.quick_multiplier_draft.length; i++) {
      var item = card.quick_multiplier_draft[i];
      var category = String(item.category || "").replace(/\\s+/g, "");
      var value = String(item.value || "").trim();
      if (!/^[234](?:\\/[234]){0,2}$/.test(category) ||
          category.split("/").some(function(part, partIndex, parts) { return parts.indexOf(part) !== partIndex; }) ||
          !/^\\d+(?:\\.\\d+)?$/.test(value)) {
        card.quick_multiplier_error = "倍率類別僅支援 2、3、4 的單一或斜線組合（例如 2/3、3/4、2/3/4）；倍率值必須是非負數字。";
        _renderQwenReviewSession();
        return;
      }
      rules.push(category + "X" + value);
      scopes.push(String(item.scope || "").trim());
    }
    if (!rules.length) {
      card.quick_multiplier_error = "至少保留一條倍率規則；不確定時請保留原值並人工檢查。";
      _renderQwenReviewSession();
      return;
    }
    if (scopes.some(function(scope) { return scope !== scopes[0]; })) {
      card.quick_multiplier_error = "目前 authority contract 只有單一倍率範圍；不同 scope 請拆成投注後再設定。";
      _renderQwenReviewSession();
      return;
    }
    var updated = _cloneJson(card.staged_structure || {});
    updated.multiplier_rules = rules;
    updated.multiplier_scope = scopes[0] || null;
    updated.collision = _qwenCollisionFromRules(rules, updated.collision);
    _qwenQuickCommit(index, "multiplier_editor", updated, ["multiplier_rules", "multiplier_scope"], "explicit_multiplier_apply");
  };

  function _qwenCurrentSpecialDraft(structure) {
    structure = structure || {};
    var entries = [
      ["tail", _qwenSpecialFieldValue(structure, "tail")],
      ["car", _qwenSpecialFieldValue(structure, "car")],
      ["half_car", _qwenSpecialFieldValue(structure, "half_car")],
      ["each", _qwenSpecialFieldValue(structure, "each")]
    ];
    for (var i = 0; i < entries.length; i++) {
      if (entries[i][1] != null && entries[i][1] !== "") return {kind:entries[i][0],literal:String(entries[i][1]),scope:String(structure.scope || ""),continuation:!!structure.continuation};
    }
    if (structure.special_text) return {kind:"custom",literal:String(structure.special_text),scope:String(structure.scope || ""),continuation:!!structure.continuation};
    return {kind:"none",literal:"",scope:String(structure.scope || ""),continuation:!!structure.continuation};
  }

  window.qwenSpecialOpen = function(index) {
    var card = qwenReviewSession && qwenReviewSession.structures[index];
    if (!card) return;
    card.quick_special_draft = _qwenCurrentSpecialDraft(card.staged_structure);
    card.quick_special_error = "";
    _renderQwenReviewSession();
  };

  window.qwenSpecialCancel = function(index) {
    var card = qwenReviewSession && qwenReviewSession.structures[index];
    if (!card) return;
    delete card.quick_special_draft;
    delete card.quick_special_error;
    _renderQwenReviewSession();
  };

  window.qwenSpecialSet = function(index, field, value) {
    var card = qwenReviewSession && qwenReviewSession.structures[index];
    if (!card || !card.quick_special_draft || ["kind","literal","scope","continuation"].indexOf(field) < 0) return;
    card.quick_special_draft[field] = field === "continuation" ? value === true : String(value || "");
  };

  window.qwenSpecialApply = function(index) {
    var card = qwenReviewSession && qwenReviewSession.structures[index];
    if (!card || !card.quick_special_draft) return;
    var draft = card.quick_special_draft;
    if (["none","tail","car","half_car","each","custom"].indexOf(draft.kind) < 0 || (draft.kind !== "none" && !String(draft.literal || "").trim())) {
      card.quick_special_error = "請選擇特殊玩法並保留圖片可見原文；看不清楚時不要猜。";
      _renderQwenReviewSession();
      return;
    }
    var updated = _cloneJson(card.staged_structure || {});
    ["tail","尾","car","車","half_car","半車","each","各"].forEach(function(key) { updated[key] = null; });
    updated.special_text = "";
    if (draft.kind === "custom") updated.special_text = String(draft.literal).trim();
    else if (draft.kind !== "none") updated[draft.kind] = String(draft.literal).trim();
    updated.scope = String(draft.scope || "").trim() || null;
    updated.continuation = draft.continuation === true;
    _qwenQuickCommit(index, "special_play_editor", updated,
      ["tail","car","half_car","each","special_text","scope","continuation"], "explicit_special_apply");
  };

  window.qwenSplitOpen = function(index) {
    var card = qwenReviewSession && qwenReviewSession.structures[index];
    if (!card) return;
    var rows = _qwenVisibleRows(card);
    card.quick_split_text = rows.slice(0, 2).map(function(row) { return row.join(" "); }).join("\\n");
    card.quick_split_error = "";
    _renderQwenReviewSession();
  };

  window.qwenSplitSetText = function(index, value) {
    var card = qwenReviewSession && qwenReviewSession.structures[index];
    if (card) card.quick_split_text = String(value || "");
  };

  window.qwenSplitCancel = function(index) {
    var card = qwenReviewSession && qwenReviewSession.structures[index];
    if (!card) return;
    delete card.quick_split_text;
    delete card.quick_split_error;
    _renderQwenReviewSession();
  };

  window.qwenSplitApply = function(index) {
    var card = qwenReviewSession && qwenReviewSession.structures[index];
    if (!card) return;
    var rows = _qwenLiteralRows(card.quick_split_text || "");
    if (rows.length !== 2 || rows.some(function(row) { return !row.length || row.some(function(value) { return !_qwenValidHumanNumber(value); }); })) {
      card.quick_split_error = "請明確輸入兩行合法 01–39 號碼；系統不會自動猜拆分點。";
      _renderQwenReviewSession();
      return;
    }
    var previous = _cloneJson(card.staged_structure || {});
    var first = _cloneJson(previous);
    first.layout = "normal_row";
    first.number_groups = [_cloneJson(rows[0])];
    card.staged_structure = first;
    card.field_sources.number_groups = {source:"Human Answer",human_confirmed:false};
    card.field_sources.layout = {source:"Human Answer",human_confirmed:false};
    _recordReviewFieldCorrection(card, "number_groups", "Human Answer", previous.number_groups || [], first.number_groups);
    _recordReviewFieldCorrection(card, "layout", "Human Answer", previous.layout, first.layout);
    card.manual_edits.push({component:"split_card", part:1, adopted_at:new Date().toISOString(), human_confirmed:false});
    delete card.quick_split_text;
    _invalidateReviewConfirmation(card);
    card.blocking_resolved_by_human = false;
    var second = _qwenNewManualCard(qwenReviewSession.structures.length);
    second.human_bet_id = _qwenNextHumanBetId();
    second.structure_id = "MANUAL-SPLIT-" + String(Date.now()) + "-" + String(index + 2);
    second.review_state = "pending";
    second.staged_structure.number_groups = [_cloneJson(rows[1])];
    second.staged_structure.layout = "normal_row";
    second.original_structure = _cloneJson(second.staged_structure);
    second.edit_text = rows[1].join(" ");
    second.manual_edits.push({component:"split_card", part:2, source_human_bet_id:card.human_bet_id, adopted_at:new Date().toISOString(), human_confirmed:false});
    qwenReviewSession.structures.splice(index + 1, 0, second);
    _renderQwenReviewSession();
    _qwenQueueAuthorityMutation();
  };

  window.qwenReviewSelect = function(index) {
    if (!qwenReviewSession || !qwenReviewSession.structures[index]) return;
    qwenReviewSession.active_structure_id = qwenReviewSession.structures[index].structure_id;
    _renderQwenReviewSession();
  };

  window.qwenReviewTogglePending = function(enabled) {
    if (!qwenReviewSession) return;
    qwenReviewSession.show_pending_only = enabled === true;
    _renderQwenReviewSession();
  };

  window.qwenReviewConfirm = function(index) {
    if (!qwenReviewSession || !qwenReviewSession.structures[index]) return;
    var card = qwenReviewSession.structures[index];
    qwenReviewSession.candidate_preview = null;
    qwenReviewSession.boundary_signal = null;
    var confirmed = card.human_confirmed !== true;
    qwenAuthorityMutationChain = qwenAuthorityMutationChain.then(function() {
      return _qwenPersistAuthorityConfirmation(card, confirmed);
    });
  };

  window.qwenReviewStartEdit = function(index) {
    if (!qwenReviewSession || !qwenReviewSession.structures[index]) return;
    var card = qwenReviewSession.structures[index];
    card.review_state = "editing";
    card.edit_text = _qwenCanonicalEditText(card.staged_structure);
    card.reparse_preview = null;
    card.preview_error = "";
    qwenReviewSession.candidate_preview = null;
    _renderQwenReviewSession();
  };

  window.qwenReviewCancelEdit = function(index) {
    if (!qwenReviewSession || !qwenReviewSession.structures[index]) return;
    var card = qwenReviewSession.structures[index];
    card.review_state = card.human_confirmed === true ? "confirmed" : "pending";
    card.reparse_preview = null;
    card.preview_error = "";
    _renderQwenReviewSession();
  };

  window.qwenReviewReparse = function(index) {
    if (!qwenReviewSession || !qwenReviewSession.structures[index]) return;
    var card = qwenReviewSession.structures[index];
    var input = document.querySelector('.qwen-card-editable[data-card-index="' + index + '"]');
    var text = input ? input.value.trim() : "";
    card.edit_text = text;
    card.reparse_preview = null;
    card.preview_error = "";
    if (!text) {
      card.preview_error = "請先輸入修正內容。";
      _renderQwenReviewSession();
      return;
    }
    window["fetch"]("/manual-reparse", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({text: text, game: qwenReviewSession.game, register_candidate: false})
    }).then(function(response) { return response.json(); }).then(function(data) {
      if (!data.ok) {
        card.preview_error = data.error || data.reason || "parser／validator 未通過";
        _renderQwenReviewSession();
        return;
      }
      if (data.manual_candidate_id || data.accepted_by_human === true || data.auto_confirm !== false || data.auto_submit !== false) {
        card.preview_error = "解析預覽安全狀態不符。";
        _renderQwenReviewSession();
        return;
      }
      card.reparse_preview = {
        numbers: _cloneJson(data.numbers == null ? [] : data.numbers),
        stars: _cloneJson(data.stars == null ? [] : data.stars),
        amounts: _cloneJson(data.amounts == null ? {} : data.amounts),
        type: data.type == null ? "" : String(data.type),
        columns: _cloneJson(data.columns == null ? [] : data.columns),
        summary: data.summary == null ? "" : String(data.summary),
        auto_confirm: false,
        auto_submit: false
      };
      _renderQwenReviewSession();
    }).catch(function() {
      card.preview_error = "解析預覽請求失敗。";
      _renderQwenReviewSession();
    });
  };

  window.qwenReviewAdoptEdit = function(index) {
    if (!qwenReviewSession || !qwenReviewSession.structures[index]) return;
    var card = qwenReviewSession.structures[index];
    var preview = card.reparse_preview;
    if (!preview) return;
    var columns = _qwenNormalizeGroups(preview.columns || []);
    var numbers = (preview.numbers || []).map(_qwenNumber).filter(function(value) { return !!value; });
    var rules = _qwenExtractMultiplierRules(card.edit_text);
    var previous = _cloneJson(card.staged_structure || {});
    var layoutInput = document.querySelector('.qwen-card-layout-edit[data-card-index="' + index + '"]');
    var selectedLayout = _qwenCanonicalLayout(layoutInput ? layoutInput.value : preview.type);
    var updated = _cloneJson(previous);
    updated.number_groups = columns.length ? columns : (numbers.length ? [numbers] : []);
    updated.multiplier_rules = rules;
    updated.layout = selectedLayout === "unknown" ? (preview.type === "column" ? "column_bet" : "normal_row") : selectedLayout;
    updated.collision = _qwenCollisionFromRules(rules, previous.collision);
    updated.parser_preview = _cloneJson(preview);
    card.staged_structure = updated;
    card.manual_edits.push({
      canonical_text: card.edit_text,
      parser_preview: _cloneJson(preview),
      adopted_at: new Date().toISOString()
    });
    card.field_sources.numbers = {source: "Human Answer", human_confirmed: false};
    card.field_sources.multiplier = {source: "Human Answer", human_confirmed: false};
    card.field_sources.layout = {source: "Human Answer", human_confirmed: false};
    _recordReviewFieldCorrection(card, "numbers", "Human Answer", previous.number_groups || [], updated.number_groups || []);
    _recordReviewFieldCorrection(card, "multiplier", "Human Answer", previous.multiplier_rules || [], updated.multiplier_rules || []);
    _recordReviewFieldCorrection(card, "layout", "Human Answer", previous.layout || "unknown", updated.layout || "unknown");
    if (card.evidence_sources && card.evidence_sources.human_answer) {
      card.evidence_sources.human_answer.staged_structure = _cloneJson(card.staged_structure);
      card.evidence_sources.human_answer.field_sources = _cloneJson(card.field_sources);
    }
    _invalidateReviewConfirmation(card);
    _renderQwenReviewSession();
    _qwenQueueAuthorityMutation();
  };

  window.qwenReviewAdoptSpecialFields = function(index) {
    if (!qwenReviewSession || !qwenReviewSession.structures[index]) return;
    var card = qwenReviewSession.structures[index];
    var root = document.querySelector('.qwen-card-editable[data-card-index="' + index + '"]');
    var editor = root && root.closest('.qwen-card-editor');
    if (!editor) return;
    var previous = _cloneJson(card.staged_structure || {});
    var updated = _cloneJson(previous);
    var continuationInput = editor.querySelector('.qwen-special-continuation');
    var continuationEnabled = !!(continuationInput && continuationInput.checked);
    updated.continuation = continuationEnabled ? (previous.continuation || true) : false;
    var fields = [
      ["tail", ".qwen-special-tail"],
      ["car", ".qwen-special-car"],
      ["half_car", ".qwen-special-half-car"],
      ["each", ".qwen-special-each"],
      ["special_text", ".qwen-special-text"],
      ["scope", ".qwen-special-scope"]
    ];
    var aliases = {tail: "尾", car: "車", half_car: "半車", each: "各"};
    for (var i = 0; i < fields.length; i++) {
      var field = fields[i][0];
      var input = editor.querySelector(fields[i][1]);
      var nextText = input ? input.value.trim() : "";
      var previousValue = _qwenSpecialFieldValue(previous, field);
      var previousText = _qwenSpecialInputValue(previousValue);
      updated[field] = nextText === previousText ? _cloneJson(previousValue) : (nextText || null);
      if (aliases[field] && Object.prototype.hasOwnProperty.call(previous, aliases[field])) {
        updated[aliases[field]] = _cloneJson(updated[field]);
      }
    }
    var changedFields = [];
    var allFields = ["continuation", "tail", "car", "half_car", "each", "special_text", "scope"];
    for (var f = 0; f < allFields.length; f++) {
      var name = allFields[f];
      var previousComparable = name === "continuation" ? previous[name] : _qwenSpecialFieldValue(previous, name);
      if (JSON.stringify(previousComparable == null ? null : previousComparable) === JSON.stringify(updated[name] == null ? null : updated[name])) continue;
      changedFields.push(name);
      card.field_sources[name] = {source: "Human Answer", human_confirmed: false};
      _recordReviewFieldCorrection(card, name, "Human Answer", previousComparable, updated[name]);
    }
    if (!changedFields.length) return;
    card.staged_structure = updated;
    card.manual_edits.push({
      component: "special_scope",
      fields: changedFields,
      adopted_at: new Date().toISOString()
    });
    if (card.evidence_sources && card.evidence_sources.human_answer) {
      card.evidence_sources.human_answer.staged_structure = _cloneJson(updated);
      card.evidence_sources.human_answer.field_sources = _cloneJson(card.field_sources);
    }
    _invalidateReviewConfirmation(card);
    _renderQwenReviewSession();
    _qwenQueueAuthorityMutation();
  };

  function _recordGemmaAdoption(card, component, gemma) {
    card.suggestion_adoptions.push({
      component: component,
      source: "Gemma suggestion",
      provider: "gemma4-26b-shadow",
      evidence_id: String(gemma.evidence_id || ""),
      adopted_at: new Date().toISOString(),
      human_confirmed: false
    });
    _invalidateReviewConfirmation(card);
  }

  function _selectUnlinkedGemmaEvidence(cardIndex, candidateIndex) {
    if (!qwenReviewSession || !qwenReviewSession.structures[cardIndex]) return null;
    if (!Number.isInteger(candidateIndex) || candidateIndex < 0 || candidateIndex >= (qwenReviewSession.unlinked_gemma_items || []).length) return null;
    var candidate = qwenReviewSession.unlinked_gemma_items[candidateIndex] || {};
    var evidenceId = String(candidate.evidence_id || "");
    if (!evidenceId) return null;
    var matches = (qwenReviewSession.unlinked_gemma_items || []).filter(function(item) {
      return String((item || {}).evidence_id || "") === evidenceId;
    });
    if (matches.length !== 1) return null;
    var alreadyUsed = qwenReviewSession.structures.some(function(card, index) {
      var selected = card.evidence_sources && card.evidence_sources.gemma;
      return index !== cardIndex && selected && String(selected.evidence_id || "") === String(evidenceId || "");
    });
    if (alreadyUsed) return null;
    var selected = Object.assign({
      source: "Gemma suggestion",
      browser_local_association: true,
      association_method: "explicit_user_click",
      human_confirmed: false
    }, _cloneJson(matches[0]));
    qwenReviewSession.structures[cardIndex].evidence_sources.gemma = selected;
    return selected;
  }

  window.qwenReviewAdoptGemmaCandidateNumbers = function(index, candidateIndex) {
    var selected = _selectUnlinkedGemmaEvidence(index, candidateIndex);
    if (!selected) return;
    window.qwenReviewAdoptGemmaNumbers(index);
  };

  window.qwenReviewAdoptGemmaCandidateMultiplier = function(index, candidateIndex) {
    var selected = _selectUnlinkedGemmaEvidence(index, candidateIndex);
    if (!selected) return;
    window.qwenReviewAdoptGemmaMultiplier(index);
  };

  window.qwenReviewAdoptGemmaCandidateLayout = function(index, candidateIndex) {
    var selected = _selectUnlinkedGemmaEvidence(index, candidateIndex);
    if (!selected) return;
    window.qwenReviewAdoptGemmaLayout(index);
  };

  window.qwenReviewAdoptGemmaNumbers = function(index) {
    if (!qwenReviewSession || !qwenReviewSession.structures[index]) return;
    var card = qwenReviewSession.structures[index];
    var gemma = card.evidence_sources && card.evidence_sources.gemma;
    if (!gemma) return;
    var groups = _gemmaNumberGroups(gemma, card);
    if (!groups.length) return;
    var previous = _cloneJson(card.staged_structure || {});
    var before = _cloneJson(previous.number_groups || []);
    previous.number_groups = groups;
    card.staged_structure = previous;
    card.field_sources.numbers = {
      source: "Gemma suggestion",
      provider: "gemma4-26b-shadow",
      evidence_id: String(gemma.evidence_id || ""),
      human_confirmed: false
    };
    _recordReviewFieldCorrection(card, "numbers", "Gemma suggestion", before, groups);
    _recordGemmaAdoption(card, "numbers", gemma);
    _renderQwenReviewSession();
    _qwenQueueAuthorityMutation();
  };

  window.qwenReviewAdoptGemmaMultiplier = function(index) {
    if (!qwenReviewSession || !qwenReviewSession.structures[index]) return;
    var card = qwenReviewSession.structures[index];
    var gemma = card.evidence_sources && card.evidence_sources.gemma;
    if (!gemma) return;
    var rules = _gemmaMultiplierRules(gemma);
    if (!rules.length) return;
    var previous = _cloneJson(card.staged_structure || {});
    var before = _cloneJson(previous.multiplier_rules || []);
    previous.multiplier_rules = rules;
    previous.collision = _qwenCollisionFromRules(rules, previous.collision);
    card.staged_structure = previous;
    card.field_sources.multiplier = {
      source: "Gemma suggestion",
      provider: "gemma4-26b-shadow",
      evidence_id: String(gemma.evidence_id || ""),
      human_confirmed: false
    };
    _recordReviewFieldCorrection(card, "multiplier", "Gemma suggestion", before, rules);
    _recordGemmaAdoption(card, "multiplier", gemma);
    _renderQwenReviewSession();
    _qwenQueueAuthorityMutation();
  };

  window.qwenReviewAdoptGemmaLayout = function(index) {
    if (!qwenReviewSession || !qwenReviewSession.structures[index]) return;
    var card = qwenReviewSession.structures[index];
    var gemma = card.evidence_sources && card.evidence_sources.gemma;
    if (!gemma) return;
    var layout = _qwenCanonicalLayout(gemma.layout_guess);
    if (layout === "unknown") return;
    var previous = _cloneJson(card.staged_structure || {});
    var before = previous.layout || "unknown";
    previous.layout = layout;
    card.staged_structure = previous;
    card.field_sources.layout = {
      source: "Gemma suggestion",
      provider: "gemma4-26b-shadow",
      evidence_id: String(gemma.evidence_id || ""),
      human_confirmed: false
    };
    _recordReviewFieldCorrection(card, "layout", "Gemma suggestion", before, layout);
    _recordGemmaAdoption(card, "layout", gemma);
    _renderQwenReviewSession();
    _qwenQueueAuthorityMutation();
  };

  window.qwenReviewSetCancelled = function(index, enabled) {
    if (!qwenReviewSession || !qwenReviewSession.structures[index]) return;
    var card = qwenReviewSession.structures[index];
    var previous = _cloneJson(card.staged_structure || {});
    var updated = _cloneJson(previous);
    updated.cancelled = enabled === true;
    card.staged_structure = updated;
    card.field_sources.cancelled = {source: "Human Answer", human_confirmed: false};
    _recordReviewFieldCorrection(card, "cancelled", "Human Answer", _qwenIsCancelledStructure(previous), enabled === true);
    _invalidateReviewConfirmation(card);
    _renderQwenReviewSession();
    _qwenQueueAuthorityMutation();
  };

  window.qwenReviewAddManualStructure = function() {
    if (!qwenReviewSession) return;
    var card = _qwenNewManualCard(qwenReviewSession.structures.length);
    qwenReviewSession.structures.push(card);
    qwenReviewSession.active_structure_id = card.structure_id;
    qwenReviewSession.candidate_preview = null;
    qwenReviewSession.boundary_signal = null;
    _renderQwenReviewSession();
    _qwenQueueAuthorityMutation();
  };

  window.qwenReviewPrevious = function() {
    var index = _qwenActiveReviewIndex();
    var visible = _qwenVisibleReviewIndices();
    var position = visible.indexOf(index);
    if (position <= 0) return;
    qwenReviewSession.active_structure_id = qwenReviewSession.structures[visible[position - 1]].structure_id;
    _renderQwenReviewSession();
  };

  window.qwenReviewNext = function() {
    var index = _qwenActiveReviewIndex();
    var visible = _qwenVisibleReviewIndices();
    var position = visible.indexOf(index);
    if (!qwenReviewSession || position < 0 || position >= visible.length - 1) return;
    qwenReviewSession.active_structure_id = qwenReviewSession.structures[visible[position + 1]].structure_id;
    _renderQwenReviewSession();
  };

  document.addEventListener("keydown", function(event) {
    if (!qwenReviewSession) return;
    var index = _qwenActiveReviewIndex();
    if (index < 0) return;
    if (event.altKey && !event.ctrlKey && !event.metaKey) {
      var key = String(event.key || "").toLowerCase();
      if (["n","c","m"].indexOf(key) < 0) return;
      event.preventDefault();
      // Shortcuts only open a preview.  They never apply or confirm structure.
      if (key === "n") qwenQuickPreviewNormal(index);
      if (key === "c") qwenQuickPreviewTwoRow(index);
      if (key === "m") qwenQuickPreviewMerge(index);
      return;
    }
    if (!(event.ctrlKey || event.metaKey) || event.key !== "Enter" ||
        qwenReviewSession.structures[index].review_state === "editing") return;
    event.preventDefault();
    qwenReviewConfirm(index);
  });

  function _buildQwenReviewSummary() {
    var readiness = _qwenReviewReadiness();
    if (!readiness.ready) return null;
    function serializeCard(card) {
      var structure = card.staged_structure || {};
      return {
        structure_id: card.structure_id,
        primary_line_id: card.primary_line_id,
        source_line_ids: _cloneJson(card.source_line_ids),
        number_groups: _cloneJson(structure.number_groups || []),
        multiplier_rules: _cloneJson(structure.multiplier_rules || []),
        layout: structure.layout || "unknown",
        collision: structure.collision == null ? null : structure.collision,
        continuation: _cloneJson(structure.continuation == null ? false : structure.continuation),
        tail: _cloneJson(structure.tail == null ? null : structure.tail),
        car: _cloneJson(structure.car == null ? null : structure.car),
        half_car: _cloneJson(structure.half_car == null ? null : structure.half_car),
        each: _cloneJson(structure.each == null ? null : structure.each),
        special_text: _cloneJson(structure.special_text == null ? "" : structure.special_text),
        cancelled: _qwenIsCancelledStructure(structure),
        human_answer: _cloneJson(structure),
        manual_edits: _cloneJson(card.manual_edits),
        field_corrections: _cloneJson(card.field_corrections || []),
        field_sources: _cloneJson(card.field_sources || {}),
        suggestion_adoptions: _cloneJson(card.suggestion_adoptions || []),
        human_confirmed: card.human_confirmed === true
      };
    }
    return {
      schema_version: "vision-review-candidate-preview-v1",
      review_session_id: qwenReviewSession.review_session_id,
      game: qwenReviewSession.game,
      source_image_id: qwenReviewSession.source_image_id,
      image_sha256: qwenReviewSession.image_sha256,
      confirmed_structures: qwenReviewSession.structures.filter(_qwenReviewIsActive).map(serializeCard),
      cancelled_structures: qwenReviewSession.structures.filter(function(card) { return !_qwenReviewIsActive(card); }).map(serializeCard),
      review_timestamp: new Date().toISOString(),
      human_confirmation_required: true,
      auto_apply: false,
      auto_confirm: false,
      auto_submit: false,
      registration_state: "candidate_boundary_ready_not_created"
    };
  }

  function _renderQwenCompletion(candidateValue) {
    var output = document.getElementById("qwen-review-completion");
    if (!output) return;
    var derivedState = candidateValue && candidateValue.state ? String(candidateValue.state) : "CURRENT";
    var candidate = candidateValue && candidateValue.candidate ? candidateValue.candidate : candidateValue;
    candidate = candidate || {};
    if (derivedState !== "CURRENT") {
      output.innerHTML = '<div id="qwen-existing-candidate-stale" data-candidate-state="' + esc(derivedState) + '" style="padding:8px;background:#fff7ed;color:#9a3412">舊 Candidate 狀態：' + esc(derivedState) + '；不會改寫舊 snapshot。重新確認 Human Answer 後才能建立新版 Candidate。</div>';
      return;
    }
    output.innerHTML = '<div id="qwen-review-complete-status" style="padding:8px;background:#ecfdf5;border-left:4px solid #10b981;color:#065f46;font-weight:700">已建立安全快照</div>' +
      '<details id="qwen-created-candidate-metadata" style="font-size:11px;color:#64748b;margin-top:5px"><summary>進階資訊</summary>Candidate ID=' + esc(candidate.candidate_id || "-") +
      '｜revision=' + esc(candidate.revision || "-") +
      '｜content hash=' + esc(String(candidate.canonical_content_hash || "").slice(0, 12)) +
      '｜CURRENT<div class="candidate-boundary-safety">candidate_only=true；approved_for_fill=false；external fill=0；auto_confirm=false；auto_submit=false</div></details>' +
      _qwenCandidateQueueHtml();
  }

  function _qwenCandidateQueueHtml() {
    var record = qwenReviewSession && qwenReviewSession.server_queue_entry;
    var queueError = qwenReviewSession && qwenReviewSession.server_queue_error;
    if (queueError) {
      return '<div id="qwen-candidate-queue-error" data-error-code="' + esc(queueError.code || "QUEUE_ERROR") + '" style="margin-top:7px;color:#991b1b">待處理操作失敗：' + esc(queueError.message || queueError.code || "unknown") + '</div>' +
        '<button type="button" id="qwen-candidate-enqueue" onclick="qwenEnqueueCandidate()">加入待處理</button>';
    }
    if (!record) {
      return '<div id="qwen-candidate-queue-status" data-queue-state="NOT_QUEUED" style="margin-top:7px;color:#475569">尚未加入待處理；建立 Candidate 不會自動加入。</div>' +
        '<button type="button" id="qwen-candidate-enqueue" onclick="qwenEnqueueCandidate()">加入待處理</button>';
    }
    var entry = record.queue_entry || record;
    var state = String(record.state || entry.state_at_creation || "QUEUED");
    var html = '<div id="qwen-candidate-queue-status" data-queue-state="' + esc(state) + '" style="margin-top:7px;color:#065f46">' +
      (state === "QUEUED" ? '已加入待處理' : '待處理狀態=' + esc(state)) + '</div>' +
      '<details class="qwen-queue-advanced" style="font-size:11px;color:#64748b"><summary>進階資訊</summary>entry ID=' + esc(entry.queue_entry_id || "-") +
      '｜sequence=' + esc(entry.enqueue_sequence || "-") + '</details>';
    if (state === "QUEUED" || state === "BLOCKED") {
      html += '<button type="button" id="qwen-candidate-remove" onclick="qwenRemoveCandidateQueueEntry()">移除待處理</button>';
    }
    return html + '<div class="qwen-candidate-queue-safety" style="font-size:11px;color:#475569">只保存 Candidate identity；不核准填入、不送出、不自動執行。</div>';
  }

  function _qwenCurrentCandidate() {
    if (!qwenReviewSession || !qwenReviewSession.server_candidate) return null;
    var wrapper = qwenReviewSession.server_candidate;
    var state = wrapper && wrapper.state ? String(wrapper.state) : "CURRENT";
    if (state !== "CURRENT") return null;
    return wrapper && wrapper.candidate ? wrapper.candidate : wrapper;
  }

  function _qwenQueueError(data) {
    if (!qwenReviewSession) return;
    qwenReviewSession.server_queue_error = {
      code: _qwenAuthorityErrorCode(data),
      message: _qwenAuthorityErrorMessage(data)
    };
    _renderQwenCompletion(qwenReviewSession.server_candidate);
  }

  function _qwenRefreshCandidateQueue(candidateValue) {
    if (!qwenReviewSession) return Promise.resolve(null);
    var state = candidateValue && candidateValue.state ? String(candidateValue.state) : "CURRENT";
    var candidate = candidateValue && candidateValue.candidate ? candidateValue.candidate : candidateValue;
    if (state !== "CURRENT" || !candidate || !candidate.candidate_id) {
      qwenReviewSession.server_queue_entry = null;
      return Promise.resolve(null);
    }
    var reviewSessionId = qwenReviewSession.review_session_id;
    return fetch("/api/vision/v1/candidate-queue").then(function(response) { return response.json(); }).then(function(data) {
      if (!data.ok) throw data;
      if (!qwenReviewSession || qwenReviewSession.review_session_id !== reviewSessionId) return null;
      var matches = (Array.isArray(data.entries) ? data.entries : []).filter(function(record) {
        var entry = record && record.queue_entry ? record.queue_entry : record;
        var identity = entry && entry.candidate_identity || {};
        return identity.candidate_id === candidate.candidate_id &&
          Number(identity.candidate_revision) === Number(candidate.revision) &&
          identity.canonical_content_hash === candidate.canonical_content_hash;
      });
      qwenReviewSession.server_queue_entry = matches.length ? matches[0] : null;
      qwenReviewSession.server_queue_error = null;
      _renderQwenCompletion(qwenReviewSession.server_candidate);
      return qwenReviewSession.server_queue_entry;
    }).catch(function(data) {
      _qwenQueueError(data);
      return null;
    });
  }

  window.qwenEnqueueCandidate = function() {
    var candidate = _qwenCurrentCandidate();
    if (!candidate) return Promise.resolve(null);
    var button = document.getElementById("qwen-candidate-enqueue");
    if (button) button.disabled = true;
    var identity = {
      candidate_id: candidate.candidate_id,
      expected_candidate_revision: candidate.revision,
      expected_content_hash: candidate.canonical_content_hash
    };
    return fetch("/api/vision/v1/candidate-queue/enqueue-actions", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify(identity)
    }).then(function(response) { return response.json(); }).then(function(action) {
      if (!action.ok) throw action;
      return fetch("/api/vision/v1/candidate-queue/enqueue", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({
          candidate_id: identity.candidate_id,
          expected_candidate_revision: identity.expected_candidate_revision,
          expected_content_hash: identity.expected_content_hash,
          human_enqueue_action_id: action.human_enqueue_action_id,
          idempotency_key: action.idempotency_key
        })
      });
    }).then(function(response) { return response.json(); }).then(function(data) {
      if (!data.ok) throw data;
      qwenReviewSession.server_queue_entry = {queue_entry: data.queue_entry, state: data.state || "QUEUED"};
      qwenReviewSession.server_queue_error = null;
      _renderQwenCompletion(qwenReviewSession.server_candidate);
      return data;
    }).catch(function(data) {
      _qwenQueueError(data);
      return null;
    });
  };

  window.qwenRemoveCandidateQueueEntry = function() {
    if (!qwenReviewSession || !qwenReviewSession.server_queue_entry) return Promise.resolve(null);
    var record = qwenReviewSession.server_queue_entry;
    var entry = record.queue_entry || record;
    if (!entry.queue_entry_id || ["QUEUED", "BLOCKED"].indexOf(String(record.state || "QUEUED")) < 0) return Promise.resolve(null);
    var actionUrl = "/api/vision/v1/candidate-queue/entries/" + encodeURIComponent(entry.queue_entry_id) + "/remove-actions";
    return fetch(actionUrl, {
      method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({})
    }).then(function(response) { return response.json(); }).then(function(action) {
      if (!action.ok) throw action;
      return fetch("/api/vision/v1/candidate-queue/entries/" + encodeURIComponent(entry.queue_entry_id) + "/remove", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({
          human_remove_action_id: action.human_remove_action_id,
          idempotency_key: action.idempotency_key
        })
      });
    }).then(function(response) { return response.json(); }).then(function(data) {
      if (!data.ok) throw data;
      qwenReviewSession.server_queue_entry = {queue_entry: entry, state: data.state || "REMOVED"};
      qwenReviewSession.server_queue_error = null;
      _renderQwenCompletion(qwenReviewSession.server_candidate);
      return data;
    }).catch(function(data) {
      _qwenQueueError(data);
      return null;
    });
  };

  window.qwenCompleteReview = function() {
    if (!qwenReviewSession || qwenReviewSession.server_state !== "ready") return;
    var readiness = _qwenReviewReadiness();
    if (!readiness.ready) return;
    var button = document.getElementById("qwen-complete-review");
    if (button) button.disabled = true;
    qwenReviewSession.server_state = "creating_candidate";
    var idempotencyKey = qwenReviewSession.review_session_id + ":" +
      qwenReviewSession.human_answer_revision + ":" + qwenReviewSession.human_answer_hash;
    fetch("/api/vision/v1/candidates", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({
        review_session_id: qwenReviewSession.review_session_id,
        expected_human_answer_revision: qwenReviewSession.human_answer_revision,
        expected_human_answer_hash: qwenReviewSession.human_answer_hash,
        idempotency_key: idempotencyKey
      })
    }).then(function(response) { return response.json(); }).then(function(data) {
      if (!data.ok) throw data;
      qwenReviewSession.server_state = "ready";
      qwenReviewSession.server_candidate = {
        candidate: data.candidate,
        state: "CURRENT"
      };
      qwenReviewSession.server_queue_entry = null;
      qwenReviewSession.server_queue_error = null;
      qwenReviewSession.candidate_preview = null;
      qwenReviewSession.boundary_signal = {
        status: "candidate_created",
        candidate_id: data.candidate && data.candidate.candidate_id,
        candidate_revision: data.candidate && data.candidate.revision,
        replayed: data.replayed === true,
        queue_written: false,
        external_fill_called: false
      };
      _renderQwenReviewSession();
    }).catch(function(data) {
      _qwenSetAuthorityFailure(data);
      _renderQwenReviewSession();
      var output = document.getElementById("qwen-review-completion");
      if (!output) return;
      var code = _qwenAuthorityErrorCode(data);
      var message = code === "REVIEW_STALE"
        ? "Human Answer 已變更，請重新確認後再建立 Candidate。"
        : (code === "CANDIDATE_NOT_READY" ? "尚未符合 Candidate 建立條件：" + _qwenAuthorityErrorMessage(data) : "Candidate 建立失敗：" + _qwenAuthorityErrorMessage(data));
      output.innerHTML = '<div id="qwen-candidate-create-error" data-error-code="' + esc(code) + '" style="padding:8px;background:#fef2f2;color:#991b1b">' + esc(message) + '</div>';
    });
  };

  window.mvpFillLocalSandbox = function() {
    if (!qwenReviewSession || qwenReviewSession.server_state !== "ready") return;
    var readiness = _qwenReviewReadiness();
    if (!readiness.ready) return;
    var button = document.getElementById("mvp-local-sandbox-fill");
    var output = document.getElementById("qwen-review-completion");
    if (button) { button.disabled = true; button.textContent = "正在準備本機測試頁…"; }
    _mvpSetStep(3);
    fetch("/api/vision/v1/mvp/sandbox/actions", {
      method:"POST", headers:{"Content-Type":"application/json"},
      body:JSON.stringify({review_session_id:qwenReviewSession.review_session_id})
    }).then(function(response) { return response.json(); }).then(function(action) {
      if (!action.ok) throw action;
      return fetch("/api/vision/v1/mvp/sandbox/execute", {
        method:"POST", headers:{"Content-Type":"application/json"},
        body:JSON.stringify({
          schema_version:action.schema_version,
          action_id:action.action_id,
          idempotency_key:action.idempotency_key
        })
      }).then(function(response) { return response.json(); }).then(function(result) {
        if (!result.ok) throw result;
        if (result.status === "FILLED_VERIFIED") {
          if (output) output.innerHTML = '<div id="mvp-fill-success" style="padding:10px;background:#ecfdf5;color:#065f46;font-weight:700">已填入本機測試表單，請人工檢查。<br><a href="' + esc(action.sandbox_url || '#') + '" target="_blank" rel="noopener">開啟本機測試表單</a></div>';
        } else {
          if (output) output.innerHTML = '<div id="mvp-fill-partial" style="padding:10px;background:#fff7ed;color:#9a3412;font-weight:700">部分欄位未能確認，已停止，請人工檢查。</div>';
        }
        if (button) { button.disabled = false; button.textContent = "輔助填入本機測試頁"; }
        return result;
      });
    }).catch(function(data) {
      var message = data && data.message || "輔助填入目前無法完成，請人工檢查。";
      if (output) output.innerHTML = '<div id="mvp-fill-error" style="padding:10px;background:#fef2f2;color:#991b1b"><strong>' + esc(message) + '</strong><details><summary>進階資訊</summary><code>' + esc(data && (data.code || (data.advanced || {}).code) || "MVP_ERROR") + '</code></details></div>';
      if (button) { button.disabled = false; button.textContent = "輔助填入本機測試頁"; }
    });
  };

  window.qwenGetReviewSession = function() { return _cloneJson(qwenReviewSession); };
  window.qwenReloadReviewAuthority = function() {
    if (!qwenReviewSession || !qwenReviewSession.review_session_id) return;
    fetch("/api/vision/v1/review-sessions/" + encodeURIComponent(qwenReviewSession.review_session_id))
      .then(function(response) { return response.json(); })
      .then(function(data) {
        if (!data.ok || !data.review) throw data;
        _qwenLoadAuthorityReview(data.review, data.candidate || null);
      }).catch(function(data) {
        _qwenSetAuthorityFailure(data);
        _renderQwenReviewSession();
      });
  };
  window.qwenGetReviewSummary = function() {
    if (!qwenReviewSession || !qwenReviewSession.server_candidate) return null;
    var value = qwenReviewSession.server_candidate;
    return _cloneJson(value && value.candidate ? value.candidate : value);
  };
  window.qwenGetRecognitionResult = function() { return _cloneJson(qwenEvidenceResult); };
  window.qwenGetPpocrShadowEvidence = function() { return _cloneJson(ppocrShadowEvidence); };
  window.qwenGetGemmaShadowEvidence = function() { return _cloneJson(gemmaShadowEvidence); };
  window.qwenGetRuntimeRoutingResult = function() { return _cloneJson(runtimeRoutingResult); };

  function _renderPpocrShadowEvidence() {
    if (!ppocrShadowEvidence) return '';
    var status = ppocrShadowEvidence.status || 'unavailable';
    var provider = ppocrShadowEvidence.provider || {};
    var comparison = ppocrShadowEvidence.comparison || {};
    var records = Array.isArray(comparison.records) ? comparison.records : [];
    var regions = Array.isArray(ppocrShadowEvidence.regions) ? ppocrShadowEvidence.regions : [];
    var html = '<section id="ppocr-shadow-evidence" style="margin-top:10px;padding:7px;background:#f8fafc;border:1px dashed #94a3b8;border-radius:4px">' +
      '<strong>PP-OCRv6 supplementary evidence (Advanced only)</strong>' +
      '<div class="ppocr-shadow-safety" style="font-size:11px;color:#9a3412;margin-top:3px">' +
      '僅供比對；Qwen 仍是主要來源。human_confirmation_required=true, auto_apply=false, auto_confirm=false, auto_submit=false</div>' +
      '<div class="ppocr-shadow-metadata" style="font-size:11px;margin-top:4px">status=' + esc(status) +
      ', provider=' + esc(provider.id || 'ppocrv6-shadow') +
      ', model=' + esc(provider.model_name || '-') +
      ', cache_hit=' + esc(String(ppocrShadowEvidence.cache_hit === true)) + '</div>';
    if (ppocrShadowEvidence.error) {
      html += '<div class="ppocr-shadow-error" style="font-size:11px;color:#b91c1c">optional shadow unavailable: ' +
        esc(JSON.stringify(ppocrShadowEvidence.error)) + '</div>';
    }
    for (var i = 0; i < records.length; i++) {
      var record = records[i] || {};
      var qwen = record.qwen || {};
      var ppocr = record.ppocr || {};
      var isDisagreement = record.classification === 'DISAGREE';
      html += '<div class="ppocr-shadow-comparison" data-comparison="' + esc(record.classification || '') +
        '" style="font-size:11px;margin-top:5px;padding:4px;border-top:1px solid #e2e8f0">' +
        'Qwen=' + esc(qwen.text == null ? '-' : qwen.text) +
        '; PP-OCR=' + esc(ppocr.text == null ? '-' : ppocr.text) +
        '; confidence=' + esc(ppocr.confidence == null ? '-' : String(ppocr.confidence)) +
        '; comparison=<strong>' + esc(record.classification || 'UNMATCHED_GEOMETRY') + '</strong>' +
        (isDisagreement ? '<div class="ppocr-shadow-disagreement" style="color:#b91c1c">另一辨識來源判定不同，請人工確認</div>' : '') +
        '</div>';
    }
    if (!records.length && regions.length) {
      html += '<pre class="ppocr-shadow-regions" style="font-size:10px;white-space:pre-wrap">' +
        esc(JSON.stringify(regions, null, 2)) + '</pre>';
    }
    if (visionShadowLatency) {
      html += '<div class="ppocr-shadow-latency" style="font-size:11px;margin-top:5px">' +
        'pp_latency_ms=' + esc(String(visionShadowLatency.pp_latency_ms)) +
        ', qwen_latency_ms=' + esc(String(visionShadowLatency.qwen_latency_ms)) +
        ', comparison_latency_ms=' + esc(String(visionShadowLatency.comparison_latency_ms)) +
        ', total_vision_latency_ms=' + esc(String(visionShadowLatency.total_vision_latency_ms)) +
        ', parallel=' + esc(String(visionShadowLatency.parallel_execution === true)) + '</div>';
    }
    return html + '</section>';
  }

  function _renderGemmaShadowEvidence() {
    if (!gemmaShadowEvidence) return '';
    var status = gemmaShadowEvidence.status || 'unavailable';
    var provider = gemmaShadowEvidence.provider || {};
    var items = Array.isArray(gemmaShadowEvidence.items) ? gemmaShadowEvidence.items : [];
    var comparison = multiModelComparison || {};
    var comparisonClass = comparison.classification || 'MULTI_MODEL_COMPARISON_UNAVAILABLE';
    var html = '<section id="gemma-shadow-evidence" style="margin-top:10px;padding:7px;background:#fff7ed;border:1px dashed #fb923c;border-radius:4px">' +
      '<strong>Gemma 4 26B raw-reader evidence (Advanced only)</strong>' +
      '<div class="gemma-shadow-safety" style="font-size:11px;color:#9a3412;margin-top:3px">' +
      'Machine suggestion only; Qwen remains authoritative. human_confirmation_required=true, auto_apply=false, auto_confirm=false, auto_submit=false</div>' +
      '<div class="gemma-shadow-metadata" style="font-size:11px;margin-top:4px">status=' + esc(status) +
      ', provider=' + esc(provider.id || 'gemma4-26b-shadow') +
      ', model=' + esc(provider.model_name || '-') +
      ', cache_hit=' + esc(String(gemmaShadowEvidence.cache_hit === true)) +
      ', provider_request_id=' + esc(gemmaShadowEvidence.provider_request_id || '-') + '</div>';
    html += '<div class="gemma-multi-model-comparison" data-classification="' + esc(comparisonClass) +
      '" style="font-size:11px;margin-top:4px">comparison=<strong>' + esc(comparisonClass) + '</strong>' +
      (comparisonClass === 'MULTI_MODEL_DISAGREEMENT'
        ? '<div style="color:#b91c1c">另一辨識來源不同，請人工確認；不得自動選邊。</div>' : '') + '</div>';
    if (gemmaShadowEvidence.error) {
      html += '<div class="gemma-shadow-error" style="font-size:11px;color:#b91c1c">optional shadow unavailable: ' +
        esc(JSON.stringify(gemmaShadowEvidence.error)) + '</div>';
    }
    for (var i = 0; i < items.length; i++) {
      var item = items[i] || {};
      html += '<div class="gemma-shadow-item" style="font-size:11px;margin-top:5px;padding:4px;border-top:1px solid #fed7aa">' +
        '<strong>' + esc(item.evidence_id || ('GEMMA-' + String(i + 1))) + '</strong>' +
        '<div>raw_text=' + esc(item.raw_text || '') + '</div>' +
        '<div>numbers=' + esc(item.numbers || 'unclear') +
        '; multiplier_text=' + esc(item.multiplier_text || 'none') +
        '; layout_guess=' + esc(item.layout_guess || 'unclear') + '</div>' +
        '<div>special_text=' + esc(item.special_text || 'none') +
        '; cancelled=' + esc(item.cancelled || 'unclear') +
        '; uncertain=' + esc(String(item.uncertain === true)) +
        '; reason=' + esc(item.uncertain_reason || 'none') + '</div></div>';
    }
    if (visionShadowLatency) {
      html += '<div class="gemma-shadow-latency" style="font-size:11px;margin-top:5px">gemma_latency_ms=' +
        esc(String(visionShadowLatency.gemma_latency_ms == null ? '-' : visionShadowLatency.gemma_latency_ms)) + '</div>';
    }
    return html + '</section>';
  }

  // RecognitionStatus.COMPLETED means only that the provider job finished.
  // Every Qwen result remains review-only evidence until the human explicitly
  // presses the manual reparse button below.
  function _renderQwenEvidence(result) {
    var provider = result.provider || {};
    var preprocessing = result.preprocessing || {};
    var requestMeta = preprocessing.qwen_request || {};
    var qwenResponse = preprocessing.qwen_response || {};
    var lines = Array.isArray(result.lines) ? result.lines : [];
    var evidenceByLineId = _qwenEvidenceByLineId(qwenResponse);
    var reconstructionByLineId = _qwenReconstructionByLineId(qwenStructureEvidence);
    var sections = Array.isArray(qwenResponse.sections) ? qwenResponse.sections : [];

    if (result.status !== "completed" || provider.id !== "qwen-dashscope" ||
        !sections.length || !lines.length) {
      var providerError = result.provider_error || {};
      var failureDiagnostic = preprocessing.failure_diagnostic || {};
      var failureCode = providerError.code || failureDiagnostic.classification || "";
      if (failureCode === "QWEN_OUTPUT_TRUNCATED") {
        var truncatedDetails = ["QWEN_OUTPUT_TRUNCATED"];
        var truncatedRequestId = failureDiagnostic.request_id || result.request_id || "";
        if (truncatedRequestId) {
          truncatedDetails.push("request_id=" + truncatedRequestId);
        }
        if (failureDiagnostic.finish_reason) {
          truncatedDetails.push("finish_reason=" + failureDiagnostic.finish_reason);
        }
        _renderQwenFailure(
          truncatedDetails.join("\\n"),
          "這張牌單內容較多，AI 回傳未完成。\\n目前無法完整辨識，請勿直接確認結果。"
        );
        return;
      }
      var diagnosticNumber = failureDiagnostic.diagnostic_id || result.request_id || "";
      var failureDetails = providerError.message || "invalid Qwen response schema";
      if (diagnosticNumber) {
        failureDetails = "診斷編號：" + diagnosticNumber + "\\n" + failureDetails;
      }
      _renderQwenFailure(failureDetails);
      return;
    }

    qwenEvidenceResult = result;
    _createQwenReviewSession(result, qwenStructureEvidence, qwenRequestedGame);
    var html = '<div id="qwen-evidence-status" style="padding:7px 9px;background:#fff7ed;border-left:4px solid #f59e0b;color:#9a3412;font-weight:700">' +
      'AI 辨識完成，待人工核對</div>';
    html += '<div style="font-size:11px;color:#64748b;margin:6px 0">' +
      'needs_review｜provider job status=completed（僅代表 provider job 完成，不代表人工確認）' +
      '｜auto_confirm=false｜auto_submit=false</div>';
    html += '<div id="qwen-review-session"></div>';
    html += '<details class="qwen-research-controls" style="margin-top:10px"><summary>進階：逐行文字工具</summary><section id="qwen-line-correction-tools" style="padding:8px;background:#fff;border:1px solid #e2e8f0;border-radius:6px">' +
      '<strong>逐行文字修正（輔助）</strong><div style="font-size:11px;color:#64748b;margin:3px 0 6px">主要審核請使用上方投注卡；此區只將文字帶入唯讀解析預覽。</div>';
    for (var toolIndex = 0; toolIndex < lines.length; toolIndex++) {
      var toolLine = lines[toolIndex] || {};
      html += '<div class="qwen-line-correction-tool" style="display:flex;gap:5px;margin-top:5px">' +
        '<input class="qwen-line-edit" value="' + esc(toolLine.text || '') + '" aria-label="第 ' + (toolIndex + 1) + ' 行手動修改" style="flex:1;min-width:0;font-family:monospace;padding:4px;border:1px solid #cbd5e1;border-radius:4px">' +
        '<button type="button" class="qwen-copy-line" onclick="qwenCopyLine(' + toolIndex + ')">複製</button>' +
        '<button type="button" class="qwen-stage-line" onclick="qwenStageLine(' + toolIndex + ')">帶入修正欄</button></div>';
    }
    html += '</section></details>';
    html += '<details id="qwen-advanced-evidence" style="font-size:12px;color:#475569;margin-top:10px"><summary>進階資訊（原始 Qwen 與重建證據）</summary>';
    html += '<div class="qwen-evidence-metadata" style="font-size:11px;color:#475569;padding:6px;background:#f1f5f9;border-radius:4px">' +
      'provider=' + esc(provider.id || '-') +
      '｜model=' + esc(provider.model_name || requestMeta.model || '-') +
      '｜prompt_version=' + esc(preprocessing.prompt_version || '-') +
      '｜prompt_sha256=' + esc(preprocessing.prompt_sha256 || '-') +
      '｜image_sha256=' + esc(requestMeta.image_sha256 || (result.source_image && result.source_image.sha256) || '-') +
      '｜cache_hit=' + esc(String(requestMeta.cache_hit === true)) +
      '｜request_id=' + esc(requestMeta.request_id || result.request_id || '-') +
      '</div>';
    html += '<div style="margin-top:8px"><strong>RecognitionResult.raw_text</strong>' +
      '<pre id="qwen-raw-text" style="white-space:pre-wrap;background:#fff;border:1px solid #e2e8f0;padding:6px">' +
      esc(result.raw_text || '') + '</pre></div>';

    for (var i = 0; i < lines.length; i++) {
      var line = lines[i] || {};
      var lineId = String(line.line_id || "");
      var hasStructureEvidence = !!lineId &&
        Object.prototype.hasOwnProperty.call(evidenceByLineId, lineId);
      var rowEvidence = hasStructureEvidence ? evidenceByLineId[lineId] : null;
      var row = rowEvidence ? rowEvidence.row : null;
      var hasReconstruction = !!lineId &&
        Object.prototype.hasOwnProperty.call(reconstructionByLineId, lineId);
      var reconstruction = hasReconstruction ? reconstructionByLineId[lineId] : null;
      var isContinuation = !!(reconstruction && reconstruction.line_role === "continuation");
      var modelCandidate = reconstruction && reconstruction.model_candidate
        ? reconstruction.model_candidate : {};
      var reconstructed = reconstruction && reconstruction.reconstructed_candidate
        ? reconstruction.reconstructed_candidate : {};
      var tokens = Array.isArray(line.tokens) ? line.tokens : [];
      html += '<div class="qwen-evidence-line" data-line-index="' + i + '" data-line-id="' + esc(lineId) + '" style="padding:9px 0;border-bottom:1px solid #cbd5e1">';
      html += '<div><strong>Line.text：</strong><span class="qwen-model-line-text">' + esc(line.text || '') + '</span></div>';
      if (hasStructureEvidence) {
        html += '<div class="qwen-row-structure" style="font-size:11px;color:#475569;margin-top:5px"><strong>原始該列 Qwen 證據：</strong>' +
          'numbers=' + esc(JSON.stringify(row.numbers == null ? null : row.numbers)) +
          '｜multiplier=' + esc(JSON.stringify(row.multiplier == null ? null : row.multiplier)) +
          '｜layout_hint=' + esc(row.layout_hint == null ? 'null' : row.layout_hint) +
          '｜shared_multiplier=' + esc(JSON.stringify(rowEvidence.shared_multiplier == null ? null : rowEvidence.shared_multiplier)) +
          '</div>';
      } else {
        html += '<div class="qwen-row-structure qwen-structure-unavailable" style="font-size:11px;color:#b91c1c;margin-top:5px">' +
          'structure evidence unavailable｜needs_review</div>';
      }
      if (hasReconstruction && !isContinuation) {
        html += '<div class="qwen-structure-comparison-pair">';
        html += '<div class="qwen-model-comparison-structure" style="font-size:11px;color:#475569;margin-top:5px"><strong>AI 結構（比較基準）：</strong>' +
          'numbers=' + esc(JSON.stringify(modelCandidate.numbers == null ? [] : modelCandidate.numbers)) +
          '｜multiplier=' + esc(JSON.stringify(modelCandidate.multiplier == null ? null : modelCandidate.multiplier)) +
          '｜layout_hint=' + esc(modelCandidate.layout_hint == null ? 'unknown' : modelCandidate.layout_hint) +
          '｜shared_multiplier=' + esc(JSON.stringify(modelCandidate.shared_multiplier == null ? null : modelCandidate.shared_multiplier)) +
          '</div>';
        html += '<div class="qwen-reconstructed-structure" style="font-size:11px;color:#475569;margin-top:5px"><strong>規則重建：</strong>' +
          'structure_id=' + esc(reconstruction.structure_id || '-') +
          '｜primary_line_id=' + esc(reconstruction.primary_line_id || lineId) +
          '｜member_line_ids=' + esc(JSON.stringify(reconstruction.member_line_ids || [lineId])) +
          '｜game=' + esc(reconstruction.game || '-') +
          '｜' +
          'number_groups=' + esc(JSON.stringify(reconstructed.number_groups == null ? [] : reconstructed.number_groups)) +
          '｜multiplier_rules=' + esc(JSON.stringify(reconstructed.multiplier_rules == null ? [] : reconstructed.multiplier_rules)) +
          '｜layout=' + esc(reconstructed.layout == null ? 'unknown' : reconstructed.layout) +
          '｜collision=' + esc(JSON.stringify(reconstructed.collision == null ? null : reconstructed.collision)) +
          '｜shared_multiplier=' + esc(JSON.stringify(reconstructed.shared_multiplier == null ? null : reconstructed.shared_multiplier)) +
          '</div>';
        if (reconstruction.status === "consistent" || reconstruction.status === "divergent") {
          html += '<div class="qwen-structure-comparison" data-structure-status="' + esc(reconstruction.status) + '" style="font-size:11px;color:#9a3412;margin-top:4px"><strong>比較狀態：</strong>' +
            esc(_qwenComparisonLabel(reconstruction.status)) + '（' + esc(reconstruction.status) + '）｜needs_review' +
            '｜human_confirmation_required=true｜auto_apply=false｜auto_confirm=false｜auto_submit=false</div>';
        } else {
          html += '<div class="qwen-reconstruction-status" data-structure-status="' + esc(reconstruction.status || 'incomplete') + '" style="font-size:11px;color:#9a3412;margin-top:4px"><strong>證據狀態：</strong>' +
            esc(_qwenComparisonLabel(reconstruction.status)) + '（' + esc(reconstruction.status || 'incomplete') + '）｜needs_review' +
            '｜human_confirmation_required=true｜auto_apply=false｜auto_confirm=false｜auto_submit=false</div>';
        }
        html += '</div>';
        html += '<div class="qwen-reconstruction-warnings" style="font-size:11px;color:#64748b;margin-top:4px"><strong>warnings：</strong>' +
          esc(JSON.stringify(Array.isArray(reconstruction.warnings) ? reconstruction.warnings : [])) + '</div>';
        html += '<details class="qwen-reconstruction-evidence" style="font-size:11px;color:#64748b;margin-top:4px"><summary>evidence</summary><pre style="white-space:pre-wrap">' +
          esc(JSON.stringify(reconstruction.evidence || {}, null, 2)) + '</pre></details>';
      } else if (isContinuation) {
        html += '<div class="qwen-reconstruction-continuation" style="font-size:11px;color:#64748b;margin-top:5px">' +
          '此列是 structure ' + esc(reconstruction.structure_id || '-') + ' 的 continuation；' +
          'primary_line_id=' + esc(reconstruction.primary_line_id || '-') +
          '｜不建立獨立投注結構｜needs_review</div>';
      } else {
        html += '<div class="qwen-reconstructed-structure qwen-reconstruction-unavailable" style="font-size:11px;color:#b91c1c;margin-top:5px">' +
          '<strong>規則重建：</strong>structure evidence unavailable｜證據不足（incomplete）｜needs_review' +
          '｜human_confirmation_required=true｜auto_apply=false｜auto_confirm=false｜auto_submit=false</div>';
      }
      html += '<div class="qwen-token-bboxes" style="font-size:11px;color:#64748b;margin-top:4px"><strong>token bbox：</strong>';
      if (!tokens.length) {
        html += '無';
      }
      for (var t = 0; t < tokens.length; t++) {
        html += '<div>' + esc(tokens[t].text || '') + ' bbox=' +
          esc(JSON.stringify(tokens[t].bounding_box || null)) + '</div>';
      }
      html += '</div></div>';
    }
    html += _renderPpocrShadowEvidence();
    html += _renderGemmaShadowEvidence();
    html += '</details>';

    html += '<details class="qwen-research-controls" style="margin-top:10px"><summary>進階：整頁文字解析預覽</summary><div id="qwen-correction-panel" style="padding:8px;background:#f8fafc;border:1px solid #cbd5e1;border-radius:4px">' +
      '<label for="qwen-review-editable" style="display:block;font-weight:700;margin-bottom:4px">前端修正暫存（可手動修改）</label>' +
      '<textarea id="qwen-review-editable" style="width:100%;min-height:70px;font-family:monospace;padding:6px" placeholder="先按某行的「帶入修正欄」，再人工修改"></textarea>' +
      '<div style="font-size:11px;color:#64748b;margin-top:3px">重新解析預覽沿用上方同一個遊戲類型；不使用 auto，document_mode 不代表遊戲類型。</div>' +
      '<button type="button" id="qwen-manual-reparse-btn" class="btn-primary" style="margin-top:6px" onclick="qwenPreviewReparse()">重新解析預覽</button>' +
      '<div id="qwen-manual-reparse-result" style="font-size:12px;margin-top:6px;color:#64748b">尚未要求解析預覽；未呼叫 /manual-reparse。</div>' +
      '</div></details>';
    document.getElementById("vision-results-body").innerHTML = html;
    document.getElementById("vision-results").style.display = "block";
    _renderQwenReviewSession();
  }

  window.qwenCopyLine = function(index) {
    var inputs = document.querySelectorAll(".qwen-line-edit");
    var value = inputs[index] ? inputs[index].value : "";
    if (!value || !navigator.clipboard) return;
    navigator.clipboard.writeText(value);
  };

  // This action changes only the browser-local editable textarea.  It does
  // not mutate qwenEvidenceResult, model_raw_text, a draft, or any queue.
  window.qwenStageLine = function(index) {
    var inputs = document.querySelectorAll(".qwen-line-edit");
    var target = document.getElementById("qwen-review-editable");
    if (!target || !inputs[index]) return;
    target.value = inputs[index].value;
    document.getElementById("qwen-manual-reparse-result").textContent =
      "已帶入前端修正暫存；尚未要求解析預覽。";
  };

  window.qwenPreviewReparse = function() {
    var target = document.getElementById("qwen-review-editable");
    var gameSelect = document.getElementById("vision-qwen-game");
    var output = document.getElementById("qwen-manual-reparse-result");
    var btn = document.getElementById("qwen-manual-reparse-btn");
    var text = target ? target.value.trim() : "";
    var selectedGame = gameSelect ? gameSelect.value : "";
    if (!text) {
      output.textContent = "needs_review：請先帶入並人工修改文字。";
      output.style.color = "#b91c1c";
      return;
    }
    if (selectedGame !== "539" && selectedGame !== "六合") {
      output.textContent = "needs_review：請明確選擇 539 或六合彩。";
      output.style.color = "#b91c1c";
      return;
    }
    btn.disabled = true;
    output.textContent = "依既有 parser／validator 產生唯讀預覽中...";
    fetch("/manual-reparse", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        text: text,
        game: selectedGame,
        register_candidate: false
      })
    }).then(function(r) { return r.json(); }).then(function(data) {
      btn.disabled = false;
      if (!data.ok) {
        output.textContent = "needs_review：" + (data.error || data.reason || "parser／validator 未通過");
        output.style.color = "#b91c1c";
        return;
      }
      if (data.manual_candidate_id || data.accepted_by_human === true ||
          data.auto_confirm !== false || data.auto_submit !== false) {
        output.textContent = "needs_review：解析預覽安全狀態不符，未顯示為可填入候選。";
        output.style.color = "#b91c1c";
        return;
      }
      output.innerHTML =
        '<div class="qwen-parser-preview-status" style="font-weight:700;color:#9a3412">解析預覽完成，尚未加入可填入候選</div>' +
        '<div style="margin-top:5px;color:#475569">' +
          'numbers=' + esc(JSON.stringify(data.numbers == null ? null : data.numbers)) + '<br>' +
          'stars=' + esc(JSON.stringify(data.stars == null ? null : data.stars)) + '<br>' +
          'amounts=' + esc(JSON.stringify(data.amounts == null ? null : data.amounts)) + '<br>' +
          'type=' + esc(data.type == null ? null : data.type) + '<br>' +
          'columns=' + esc(JSON.stringify(data.columns == null ? null : data.columns)) + '<br>' +
          'summary=' + esc(data.summary == null ? '' : data.summary) + '<br>' +
          'auto_confirm=false<br>auto_submit=false' +
        '</div>';
      output.style.color = "#475569";
    }).catch(function() {
      btn.disabled = false;
      output.textContent = "needs_review：解析預覽請求失敗。";
      output.style.color = "#b91c1c";
    });
  };

  function _renderPendingConfirmation(report) {
    var pending = report.pending_confirmation || {};
    var lines = pending.lines || [];
    var quality = report.quality || {};
    var source = report.selected_source === "paid_vision" ? "付費 Vision" : "本機 OCR fallback";
    var documentMode = report.document_mode || "auto";
    visionQualityPassed = quality.pass === true;
    var html = '<div style="font-size:12px;color:#64748b;margin-bottom:8px">' +
      '來源：' + esc(source) + '｜版面提示：' + esc(documentMode) +
      '｜狀態：PENDING_HUMAN_CONFIRMATION｜牌組數：' + lines.length +
      '</div>';

    if (report.paid_skipped_reason) {
      html += '<div style="padding:6px 8px;margin-bottom:8px;background:#fff7ed;border-left:3px solid #f59e0b;color:#9a3412">' +
        esc(report.paid_skipped_reason) + '</div>';
    }
    if (quality.blocked) {
      html += '<div style="padding:6px 8px;margin-bottom:8px;background:#fef2f2;border-left:3px solid #ef4444;color:#991b1b">' +
        '圖片品質檢查未通過：' + esc((quality.reasons || []).join('; ')) + '</div>';
    }
    if (!lines.length) {
      html += '<div style="color:#ef4444">沒有可確認的辨識行。請檢查 API Key、模型設定或本機 OCR 環境。</div>';
    }
    if (report.selected_source === "paid_vision") {
      html += '<div style="padding:6px 8px;margin-bottom:8px;background:#eff6ff;border-left:3px solid #2563eb;color:#1e3a8a">' +
        '每個項目應代表一筆完整牌組；相同星別＋倍率應展開到同區塊牌組。版面提示只供人工核對；請逐筆選擇一般／柱碰／車／不確定，模型不會決定最終牌型。</div>';
    }
    if (report.paid_input_aided) {
      html += '<div style="padding:6px 8px;margin-bottom:8px;background:#f0fdf4;border-left:3px solid #16a34a;color:#166534">' +
        '本次付費 Vision 同時參考原圖與左／中／右放大區域；原始圖片品質 Gate 仍然有效。</div>';
    }

    for (var i = 0; i < lines.length; i++) {
      var line = lines[i] || {};
      var uncertain = !!line.uncertain;
      var groups = line.number_groups || [];
      var groupText = groups.map(function(group) { return (group || []).join(", "); }).join(" / ");
      html += '<div class="vision-confirm-line" style="padding:8px 0;border-bottom:1px solid #e2e8f0">';
      html += '<div style="display:flex;gap:6px;align-items:center">';
      html += '<span style="font-size:11px;color:' + (uncertain ? '#b45309' : '#64748b') + '">' +
        esc(line.entry_id || '-') + ' / ' + esc(line.line_id || ('L' + (i + 1))) + '</span>';
      html += '<input class="vision-line-text" value="' + esc(_visionReviewDraft(line)) + '" style="flex:1;min-width:0;font-family:monospace;font-size:15px;padding:5px;border:1px solid #cbd5e1;border-radius:4px" title="送進文字 Review 前的可編輯草稿">';
      html += '<select class="vision-layout-choice" style="font-size:12px;padding:5px;border:1px solid #cbd5e1;border-radius:4px">' +
        '<option value="">人工選擇牌型</option><option value="normal">一般</option>' +
        '<option value="column">柱碰</option><option value="car">車</option>' +
        '<option value="unknown">不確定</option></select>';
      html += '<label style="font-size:12px;white-space:nowrap"><input class="vision-line-confirmed" type="checkbox"> 已核對</label>';
      html += '</div>';
      html += '<div style="font-size:11px;color:#475569;margin-top:3px">模型版面提示：' +
        esc(line.layout_hint || 'unknown') + '｜號碼分組：' + esc(groupText || '未辨識') +
        '｜倍率：' + esc(line.multiplier_text || '未辨識') + '</div>';
      html += '<div style="font-size:11px;color:#64748b;margin-top:2px">模型原文：' +
        esc(line.raw_text || '未辨識') + '｜上方欄位是可編輯的 Review 格式草稿</div>';
      if (uncertain) {
        html += '<div style="font-size:11px;color:#b45309;margin-top:3px">不確定：' + esc(line.uncertain_reason || '請人工核對') + '</div>';
      }
      var alternatives = line.alternatives || [];
      if (alternatives.length) {
        html += '<div style="font-size:11px;color:#64748b;margin-top:2px">可能：' + esc(alternatives.join(' / ')) + '</div>';
      }
      html += '</div>';
    }

    if (lines.length) {
      html += '<button type="button" class="btn-primary" style="margin-top:8px" onclick="visionApplyConfirmedText()"' +
        (visionQualityPassed ? '' : ' disabled') + '>全部核對完成，帶回文字 Review</button>';
      html += '<div id="vision-confirm-message" style="font-size:12px;margin-top:5px;color:#64748b">每一行都需要人工勾選確認。</div>';
    }
    html += '<div style="font-size:11px;color:#64748b;margin-top:8px">安全狀態：auto_submit=false｜auto_confirm=false</div>';

    document.getElementById("vision-results-body").innerHTML = html;
    document.getElementById("vision-results").style.display = "block";
  }

  function _visionReviewDraft(line) {
    var groups = Array.isArray(line.number_groups) ? line.number_groups : [];
    var cleanGroups = groups.map(function(group) {
      return (Array.isArray(group) ? group : []).filter(function(token) { return !!token; });
    }).filter(function(group) { return group.length > 0; });
    var multiplier = (line.multiplier_text || "").trim();
    var numbers = "";

    if (line.layout_hint === "column_like" && cleanGroups.length > 1) {
      numbers = cleanGroups.map(function(group) { return group.join("."); }).join("/");
    } else {
      numbers = cleanGroups.reduce(function(all, group) { return all.concat(group); }, []).join(".");
    }

    if (line.layout_hint === "car_like" && numbers) {
      return numbers.split(".")[0] + "車" + multiplier;
    }
    if (numbers) {
      return numbers + (multiplier ? " " + multiplier : "");
    }
    return line.raw_text || "";
  }

  window.visionApplyConfirmedText = function() {
    var rows = document.querySelectorAll(".vision-confirm-line");
    var texts = [];
    var allConfirmed = rows.length > 0;
    for (var i = 0; i < rows.length; i++) {
      var checked = rows[i].querySelector(".vision-line-confirmed").checked;
      var value = rows[i].querySelector(".vision-line-text").value.trim();
      var layoutChoice = rows[i].querySelector(".vision-layout-choice").value;
      if (!checked || !value || !layoutChoice || layoutChoice === "unknown") allConfirmed = false;
      if (value) texts.push(value);
    }
    var message = document.getElementById("vision-confirm-message");
    if (!visionQualityPassed) {
      message.textContent = "圖片品質檢查未通過，不能進入 Review；請重新拍攝或上傳較清楚的圖片。";
      message.style.color = "#ef4444";
      return;
    }
    if (!allConfirmed) {
      message.textContent = "尚有未核對、空白或牌型未明確確認的辨識行，不能進入 Review。";
      message.style.color = "#ef4444";
      return;
    }
    var textArea = document.getElementById("batch-text");
    textArea.value = texts.join("\\n");
    textArea.dispatchEvent(new Event("input", { bubbles: true }));
    switchMode("text");
  };

  // Render RecognitionResult
  function _renderVisionResult(result) {
    var lines = result.lines || [];
    var html = '<div style="font-size:12px;color:#94a3b8;margin-bottom:6px">' +
      'Provider: ' + esc((result.provider && result.provider.id) || "-") +
      ' | Status: ' + esc(result.status) +
      ' | Lines: ' + lines.length +
      '</div>';

    html += '<div style="font-family:monospace;font-size:16px;line-height:1.6">';
    for (var i = 0; i < lines.length; i++) {
      var line = lines[i];
      var conf = line.confidence || {};
      var level = conf.level || "unknown";
      var confColor = level === "high" ? "#16a34a" : level === "medium" ? "#f59e0b" : level === "low" ? "#ef4444" : "#94a3b8";
      html += '<div style="padding:4px 0;border-bottom:1px solid #f1f5f9">';
      html += '<span style="color:' + confColor + ';font-size:10px;margin-right:6px">[' + esc(level) + ']</span>';
      html += '<span style="font-weight:600">' + esc(line.text) + '</span>';

      // Alternatives
      var alts = line.alternatives || [];
      var tokens = line.tokens || [];
      for (var t = 0; t < tokens.length; t++) {
        var tokAlts = tokens[t].alternatives || [];
        if (tokAlts.length > 0) {
          html += ' <span style="font-size:11px;color:#f59e0b">(';
          html += esc(tokens[t].text) + ': ';
          for (var a = 0; a < tokAlts.length; a++) {
            if (a > 0) html += ', ';
            html += esc(tokAlts[a].text);
            if (tokAlts[a].score != null) html += ' ' + tokAlts[a].score.toFixed(2);
          }
          html += ')</span>';
        }
      }

      // Warnings
      var warns = line.warnings || [];
      if (warns.length > 0) {
        html += ' <span style="font-size:10px;color:#ef4444">⚠ ' + esc(warns.join("; ")) + '</span>';
      }
      html += '</div>';
    }
    html += '</div>';

    // Provider metadata
    html += '<div style="font-size:11px;color:#94a3b8;margin-top:6px;padding-top:4px;border-top:1px solid #e2e8f0">' +
      'Model: ' + esc((result.provider && result.provider.model_name) || "-") +
      ' | Version: ' + esc((result.provider && result.provider.model_version) || "-") +
      ' | Latency: ' + (result.latency_ms != null ? result.latency_ms + "ms" : "-") +
      '</div>';

    document.getElementById("vision-results-body").innerHTML = html;
    document.getElementById("vision-results").style.display = "block";
  }

  function _formatBytes(b) {
    if (b < 1024) return b + " B";
    if (b < 1048576) return (b / 1024).toFixed(1) + " KB";
    return (b / 1048576).toFixed(1) + " MB";
  }

  _qwenReloadPersistedReview();
})();
</script>
"""
