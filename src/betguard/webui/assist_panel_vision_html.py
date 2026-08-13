"""Vision image intake UI — extends assist panel with image mode.

Returns HTML/JS fragment injected into the assist panel page.
"""


def render_vision_ui_section() -> str:
    """Return HTML + JS for the vision image intake panel section."""
    return """
<div class="section" id="vision-section" style="display:none">
  <h3>手寫牌單 AI 辨識</h3>
  <p class="muted" style="font-size:13px;margin-bottom:8px;color:#475569">
    專門辨識數字、x 與二／三／四。AI 結果必須逐行人工確認，<br>
    確認後只會帶回文字 Review，不會自動送出或操作網站。<br>
    「Qwen 看圖」只在人工按下按鈕後呼叫 DashScope，結果只作視覺輔助證據。<br>
    付費 Vision 啟用時，圖片會傳送到設定的 OpenAI API。
  </p>
  <div id="vision-provider-status" style="font-size:12px;margin-bottom:8px;color:#64748b">正在檢查辨識環境...</div>
  <label style="display:block;font-size:13px;color:#334155;margin-bottom:8px">
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
    Qwen 遊戲類型（同時用於規則重建與重新解析預覽）：
    <select id="vision-qwen-game" style="margin-left:6px;padding:5px 8px;border:1px solid #cbd5e1;border-radius:4px">
      <option value="539">539</option>
      <option value="六合">六合彩</option>
    </select>
  </label>
  <button id="vision-run-btn" class="btn-primary" style="display:none;margin-bottom:8px" onclick="visionRunJob()">開始 AI 辨識</button>
  <button id="vision-qwen-run-btn" class="btn-primary" style="display:none;margin-bottom:8px;background:#7c3aed" onclick="visionRunQwenJob()">Qwen 看圖</button>

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
  var qwenReviewSession = null;
  var qwenRequestedGame = "";
  var uploadedImageMetadata = null;
  var visionUploadGeneration = 0;

  fetch("/api/vision/v1/providers").then(function(r) { return r.json(); }).then(function(data) {
    var providers = (data && data.providers) || [];
    var paid = providers.filter(function(p) { return p.id === "openai-vision-paid"; })[0];
    var qwen = providers.filter(function(p) { return p.id === "qwen-dashscope"; })[0];
    var status = document.getElementById("vision-provider-status");
    qwenConfigured = !!(qwen && qwen.configured);
    document.getElementById("vision-qwen-run-btn").disabled = !qwenConfigured;
    var qwenStatus = qwenConfigured
      ? "Qwen 看圖已設定，只會在按下按鈕後呼叫。"
      : "Qwen 看圖尚未設定。";
    var paidStatus = paid && paid.configured
      ? "付費 Vision 已設定；辨識後仍需逐行人工確認。"
      : "付費 Vision 尚未設定。";
    status.textContent = qwenStatus + "｜" + paidStatus;
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

  // Run job
  window.visionRunJob = function() {
    if (!uploadedImageId) return;
    var btn = document.getElementById("vision-run-btn");
    btn.disabled = true;
    btn.textContent = "辨識中...";
    fetch("/api/vision/v1/jobs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        image_id: uploadedImageId,
        aided_image_id: uploadedAidImageId || "",
        provider_id: "openai-vision-paid",
        document_mode: document.getElementById("vision-document-mode").value || "auto"
      })
    }).then(function(r) { return r.json(); })
    .then(function(data) {
      btn.disabled = false;
      btn.textContent = "開始 AI 辨識";
      if (!data.ok) {
        document.getElementById("vision-results-body").innerHTML = "<div style='color:#ef4444'>錯誤：" + esc((data.error && data.error.message) || "未知") + "</div>";
        document.getElementById("vision-results").style.display = "block";
        return;
      }
      if (data.pending_confirmation) {
        _renderPendingConfirmation(data);
      } else {
        _renderVisionResult(data.result || {});
      }
    }).catch(function() {
      btn.disabled = false;
      btn.textContent = "開始 AI 辨識";
      document.getElementById("vision-results-body").innerHTML = "<div style='color:#ef4444'>辨識失敗，請稍後重試。</div>";
      document.getElementById("vision-results").style.display = "block";
    });
  };

  // Explicit Gate 1B Qwen evidence request.  Uploading an image or detecting
  // an API key never calls this function; only the "Qwen 看圖" button does.
  window.visionRunQwenJob = function() {
    if (!uploadedImageId) return;
    var btn = document.getElementById("vision-qwen-run-btn");
    var gameSelect = document.getElementById("vision-qwen-game");
    var selectedGame = gameSelect ? gameSelect.value : "";
    if (selectedGame !== "539" && selectedGame !== "六合") {
      _renderQwenFailure("請先明確選擇 539 或六合彩");
      return;
    }
    _resetQwenReviewSession();
    qwenRequestedGame = selectedGame;
    btn.disabled = true;
    btn.textContent = "Qwen 辨識中...";
    fetch("/api/vision/v1/jobs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        image_id: uploadedImageId,
        provider_id: "qwen-dashscope",
        game: selectedGame
      })
    }).then(function(r) { return r.json(); })
    .then(function(data) {
      btn.disabled = !qwenConfigured;
      btn.textContent = "Qwen 看圖";
      if (!data.ok || !data.result) {
        _renderQwenFailure((data.error && data.error.message) || "Qwen 回應無效");
        return;
      }
      qwenStructureEvidence = Array.isArray(data.structure_evidence)
        ? data.structure_evidence : [];
      ppocrShadowEvidence = data.shadow_evidence || null;
      gemmaShadowEvidence = data.gemma_shadow_evidence || null;
      multiModelComparison = data.multi_model_comparison || null;
      visionShadowLatency = data.vision_latency || null;
      _renderQwenEvidence(data.result);
    }).catch(function() {
      btn.disabled = !qwenConfigured;
      btn.textContent = "Qwen 看圖";
      _renderQwenFailure("Qwen 辨識失敗，請稍後重試。");
    });
  };

  function _renderQwenFailure(message) {
    _resetQwenReviewSession();
    qwenEvidenceResult = null;
    qwenStructureEvidence = [];
    ppocrShadowEvidence = null;
    gemmaShadowEvidence = null;
    multiModelComparison = null;
    visionShadowLatency = null;
    var userMessage = arguments.length > 1 ? arguments[1] : "";
    var safeUserMessage = userMessage ||
      "AI 未能完整讀取這張圖片。\\n可以重新辨識或改用手動輸入。";
    document.getElementById("vision-results-body").innerHTML =
      '<div id="qwen-evidence-status" style="color:#b91c1c;font-weight:700">Qwen 辨識失敗</div>' +
      '<div id="qwen-failure-message" style="color:#991b1b;margin-top:6px">' +
        esc(safeUserMessage).replace(/\\n/g, '<br>') + '</div>' +
      '<details class="qwen-failure-details" style="font-size:11px;color:#64748b;margin-top:6px"><summary>進階資訊</summary><pre style="white-space:pre-wrap">' +
        esc(message || "invalid response") + '</pre></details>' +
      '<div style="font-size:11px;color:#64748b;margin-top:6px">needs_review｜auto_confirm=false｜auto_submit=false</div>';
    document.getElementById("vision-results").style.display = "block";
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
        human_confirmed: false
      }
    };
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
        multiplier: {source: "Qwen reconstruction"}
      };
      cards.push({
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
        evidence_sources: evidenceSources,
        field_sources: {
          numbers: {source: "Qwen reconstruction"},
          multiplier: {source: "Qwen reconstruction"}
        },
        suggestion_adoptions: [],
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
      active_structure_id: null,
      show_pending_only: false,
      candidate_preview: null,
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

  function _qwenGemmaEvidenceHtml(card, index) {
    var sources = card.evidence_sources || {};
    var qwen = sources.qwen || {};
    var gemma = sources.gemma;
    var slots = ["Qwen", "Gemma", "PP", "Codex", "Human Answer"];
    var html = '<section class="review-evidence-sources" style="margin-top:8px;padding:7px;background:#f8fafc;border:1px solid #cbd5e1;border-radius:5px">' +
      '<div class="review-evidence-source-slots" style="font-size:11px;color:#64748b">sources=' + esc(slots.join(' / ')) + '</div>' +
      '<div class="qwen-card-source" style="margin-top:5px"><strong>Qwen evidence（唯讀）</strong>' +
      '<div>model=' + esc(JSON.stringify(qwen.model_candidate || {})) + '</div>' +
      '<div>reconstruction=' + esc(JSON.stringify(qwen.reconstructed_candidate || {})) + '</div></div>' +
      '<div class="human-answer-source" style="margin-top:5px"><strong>Human Answer（browser-local）</strong>' +
      '<div>staged=' + esc(JSON.stringify(card.staged_structure || {})) + '</div>' +
      '<div>human_confirmed=' + esc(String(card.human_confirmed === true)) + '</div></div>';
    if (!gemma) {
      var unlinked = qwenReviewSession && Array.isArray(qwenReviewSession.unlinked_gemma_items)
        ? qwenReviewSession.unlinked_gemma_items : [];
      html += '<div class="gemma-card-source-unavailable" style="margin-top:5px;color:#64748b">Gemma evidence 未以精確 structure_id 掛接；不使用位置、文字或索引補位。請由人工明確選擇候選。</div>';
      if (unlinked.length) {
        html += '<div class="gemma-unlinked-candidates" style="margin-top:6px">';
        for (var candidateIndex = 0; candidateIndex < unlinked.length; candidateIndex++) {
          var candidate = unlinked[candidateIndex] || {};
          var candidateGroups = _gemmaNumberGroups(candidate, card);
          var candidateRules = _gemmaMultiplierRules(candidate);
          html += '<div class="gemma-unlinked-candidate" data-evidence-id="' + esc(candidate.evidence_id || '') + '" style="padding:5px 0;border-top:1px dashed #cbd5e1">' +
            '<div><strong>Gemma 候選 ' + esc(candidate.evidence_id || String(candidateIndex + 1)) + '</strong>：' + esc(candidate.raw_text || '') + '</div>';
          if (candidateGroups.length) {
            html += '<button type="button" class="adopt-gemma-candidate-numbers" onclick="qwenReviewAdoptGemmaCandidateNumbers(' + index + ',' + candidateIndex + ')">將此候選號碼帶入本卡</button> ';
          }
          if (candidateRules.length) {
            html += '<button type="button" class="adopt-gemma-candidate-multiplier" onclick="qwenReviewAdoptGemmaCandidateMultiplier(' + index + ',' + candidateIndex + ')">將此候選倍率帶入本卡</button>';
          }
          html += '</div>';
        }
        html += '</div>';
      }
      return html + '</section>';
    }
    var staged = card.staged_structure || {};
    var gemmaGroups = _gemmaNumberGroups(gemma, card);
    var gemmaRules = _gemmaMultiplierRules(gemma);
    var numbersDiffer = gemmaGroups.length && !_sameReviewValue(_qwenNormalizeGroups(staged.number_groups || []), gemmaGroups);
    var multiplierDiffer = gemmaRules.length && !_sameReviewValue(staged.multiplier_rules || [], gemmaRules);
    html += '<div class="gemma-card-source" data-evidence-id="' + esc(gemma.evidence_id || '') + '" style="margin-top:7px;padding-top:6px;border-top:1px solid #e2e8f0">' +
      '<strong>Gemma suggestion（唯讀）</strong>' +
      '<div>raw_text=' + esc(gemma.raw_text || '') + '</div>' +
      '<div>numbers=' + esc(gemma.numbers || 'unclear') + '; multiplier_text=' + esc(gemma.multiplier_text || 'none') + '</div>' +
      '<div>layout_guess=' + esc(gemma.layout_guess || 'unclear') + '; uncertain=' + esc(String(gemma.uncertain === true)) + '</div>';
    if (numbersDiffer || multiplierDiffer) {
      html += '<div class="gemma-card-disagreement" style="color:#b91c1c;font-weight:700">AI 來源不同，請分欄人工檢查；採用任一建議都不會自動確認。</div>';
    }
    if (gemmaGroups.length && numbersDiffer) {
      html += '<button type="button" class="adopt-gemma-numbers" onclick="qwenReviewAdoptGemmaNumbers(' + index + ')">採用 Gemma 號碼</button> ';
    }
    if (gemmaRules.length && multiplierDiffer) {
      html += '<button type="button" class="adopt-gemma-multiplier" onclick="qwenReviewAdoptGemmaMultiplier(' + index + ')">採用 Gemma 倍率</button>';
    }
    return html + '</div></section>';
  }

  function _qwenCardStructureHtml(card) {
    var structure = card.staged_structure || {};
    var groups = _qwenNormalizeGroups(structure.number_groups || []);
    var layout = String(structure.layout || "unknown");
    var html = '<div class="qwen-review-layout" style="font-size:12px;color:#64748b;margin-bottom:6px">版面：' +
      esc(_qwenLayoutLabel(layout)) + '</div>';
    if (layout === "column_bet" || layout === "column") {
      html += '<div class="qwen-review-columns" style="display:grid;gap:4px;margin-bottom:7px">';
      for (var i = 0; i < groups.length; i++) {
        html += '<div class="qwen-review-column" data-column-index="' + i + '" style="font-family:monospace;font-size:16px">' +
          esc(groups[i].join(" ")) + '</div>';
      }
      html += '</div>';
    } else {
      html += '<div class="qwen-review-numbers" style="font-family:monospace;font-size:17px;margin-bottom:7px">' +
        esc(groups.map(function(group) { return group.join(" "); }).join(" / ") || "—") + '</div>';
    }
    var rules = Array.isArray(structure.multiplier_rules) ? structure.multiplier_rules : [];
    html += '<div class="qwen-review-play"><strong>玩法：</strong>' +
      esc(rules.length ? rules.join("、") : "未辨識／需要人工處理") + '</div>';
    if (structure.collision != null) {
      html += '<div class="qwen-review-collision"><strong>碰法：</strong>' + esc(structure.collision) + '</div>';
    }
    if (structure.play_text != null) {
      html += '<div class="qwen-review-play-text"><strong>玩法文字：</strong>' + esc(structure.play_text) + '</div>';
    }
    return html;
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
    html += '<div class="qwen-review-human-status" style="margin:6px 0;color:#9a3412;font-weight:700">' +
      esc(_qwenHumanStatus(card.source_status)) + '</div>';
    html += _qwenCardStructureHtml(card);
    html += _qwenGemmaEvidenceHtml(card, index);
    if (card.manual_edits.length) {
      html += '<div class="qwen-manual-edit-marker" style="font-size:12px;color:#0f766e;margin-top:5px">已採用人工修改</div>';
    }
    if (card.review_state === "editing") {
      html += '<div class="qwen-card-editor" style="margin-top:8px;padding-top:8px;border-top:1px solid #e2e8f0" onclick="event.stopPropagation()">' +
        '<label style="display:block;font-weight:700;margin-bottom:4px">可編輯表示</label>' +
        '<textarea class="qwen-card-editable" data-card-index="' + index + '" style="width:100%;min-height:68px;font-family:monospace;padding:6px">' + esc(card.edit_text) + '</textarea>' +
        '<div style="margin-top:6px"><button type="button" class="qwen-card-reparse" onclick="qwenReviewReparse(' + index + ')">重新解析</button> ' +
        '<button type="button" class="qwen-card-cancel" onclick="qwenReviewCancelEdit(' + index + ')">取消</button></div>' +
        _qwenPreviewHtml(card, index) + '</div>';
    } else {
      html += '<div style="margin-top:8px"><button type="button" class="qwen-edit-structure" onclick="event.stopPropagation();qwenReviewStartEdit(' + index + ')">修改</button> ' +
        '<button type="button" class="qwen-confirm-structure" onclick="event.stopPropagation();qwenReviewConfirm(' + index + ')">' +
        (card.review_state === "confirmed" ? "取消確認" : "確認此筆") + '</button></div>';
    }
    html += '<details class="qwen-card-advanced" style="font-size:11px;color:#64748b;margin-top:7px" onclick="event.stopPropagation()"><summary>進階資訊</summary>' +
      '<div>structure_id=' + esc(card.structure_id) + '｜primary_line_id=' + esc(card.primary_line_id) + '</div>' +
      '<div>source_line_ids=' + esc(JSON.stringify(card.source_line_ids)) + '</div>' +
      '<div>technical_status=' + esc(card.source_status) + '</div>' +
      '<div>model_candidate=' + esc(JSON.stringify(card.model_candidate || {})) + '</div>' +
      '<div>reconstructed_candidate=' + esc(JSON.stringify(card.original_structure || {})) + '</div>' +
      '<div>staged_structure=' + esc(JSON.stringify(card.staged_structure || {})) + '</div>' +
      '<div>field_sources=' + esc(JSON.stringify(card.field_sources || {})) + '</div>' +
      '<div>suggestion_adoptions=' + esc(JSON.stringify(card.suggestion_adoptions || [])) + '</div>' +
      '<div>warnings=' + esc(JSON.stringify(card.warnings)) + '</div></details>';
    return html + '</article>';
  }

  function _renderQwenReviewSession() {
    var target = document.getElementById("qwen-review-session");
    if (!target || !qwenReviewSession) return;
    var cards = qwenReviewSession.structures;
    var confirmed = cards.filter(function(card) { return card.review_state === "confirmed"; }).length;
    var editing = cards.filter(function(card) { return card.review_state === "editing"; }).length;
    var pending = cards.length - confirmed - editing;
    var html = '<section class="qwen-review-session-panel">' +
      '<div style="display:flex;flex-wrap:wrap;justify-content:space-between;gap:8px;align-items:center;margin:8px 0">' +
      '<div><strong id="qwen-review-progress">已確認 ' + confirmed + ' / 總共 ' + cards.length + '</strong>' +
      '<div id="qwen-review-counts" style="font-size:12px;color:#64748b">待確認 ' + pending + '｜已確認 ' + confirmed + '｜需要修改 ' + editing + '</div></div>' +
      '<label style="font-size:12px"><input id="qwen-pending-only" type="checkbox" ' +
        (qwenReviewSession.show_pending_only ? 'checked ' : '') + 'onchange="qwenReviewTogglePending(this.checked)"> 只看待確認</label></div>';
    if (!cards.length) {
      html += '<div class="qwen-no-primary-structures" style="padding:9px;background:#fef2f2;color:#991b1b">資料不完整，沒有可供逐筆審核的 primary structure。</div>';
    }
    html += '<div id="qwen-review-cards" style="display:grid;gap:9px">';
    for (var i = 0; i < cards.length; i++) {
      if (qwenReviewSession.show_pending_only && cards[i].review_state === "confirmed") continue;
      html += _qwenReviewCardHtml(cards[i], i);
    }
    html += '</div><button type="button" id="qwen-complete-review" class="btn-primary" style="margin-top:10px" ' +
      (cards.length && confirmed === cards.length ? '' : 'disabled ') + 'onclick="qwenCompleteReview()">完成整張審核</button>' +
      '<div id="qwen-review-completion" style="margin-top:8px"></div>' +
      '<details id="qwen-known-limitations" style="font-size:11px;color:#64748b;margin-top:9px"><summary>已知限制</summary>' +
      '劃除投注仍是未解決的 cancellation evidence；半車等跨列 scope 仍需人工判斷；模糊、密集或遮擋字跡可能造成辨識不完整。本階段未修改 OCR。</details>' +
      '</section>';
    target.innerHTML = html;
    if (qwenReviewSession.candidate_preview) _renderQwenCompletion(qwenReviewSession.candidate_preview);
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
    card.review_state = card.review_state === "confirmed" ? "pending" : "confirmed";
    card.human_confirmed = card.review_state === "confirmed";
    if (card.evidence_sources && card.evidence_sources.human_answer) {
      card.evidence_sources.human_answer.human_confirmed = card.human_confirmed;
    }
    qwenReviewSession.candidate_preview = null;
    _renderQwenReviewSession();
  };

  window.qwenReviewStartEdit = function(index) {
    if (!qwenReviewSession || !qwenReviewSession.structures[index]) return;
    var card = qwenReviewSession.structures[index];
    card.review_state = "editing";
    card.human_confirmed = false;
    if (card.evidence_sources && card.evidence_sources.human_answer) {
      card.evidence_sources.human_answer.human_confirmed = false;
    }
    card.edit_text = _qwenCanonicalEditText(card.staged_structure);
    card.reparse_preview = null;
    card.preview_error = "";
    qwenReviewSession.candidate_preview = null;
    _renderQwenReviewSession();
  };

  window.qwenReviewCancelEdit = function(index) {
    if (!qwenReviewSession || !qwenReviewSession.structures[index]) return;
    var card = qwenReviewSession.structures[index];
    card.review_state = "pending";
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
    var previous = card.staged_structure || {};
    card.staged_structure = {
      number_groups: columns.length ? columns : (numbers.length ? [numbers] : []),
      multiplier_rules: rules,
      layout: preview.type === "column" ? "column_bet" : "normal_row",
      collision: _qwenCollisionFromRules(rules, previous.collision),
      shared_multiplier: previous.shared_multiplier == null ? null : previous.shared_multiplier,
      parser_preview: _cloneJson(preview)
    };
    card.manual_edits.push({
      canonical_text: card.edit_text,
      parser_preview: _cloneJson(preview),
      adopted_at: new Date().toISOString()
    });
    card.field_sources.numbers = {source: "Human Answer", human_confirmed: false};
    card.field_sources.multiplier = {source: "Human Answer", human_confirmed: false};
    if (card.evidence_sources && card.evidence_sources.human_answer) {
      card.evidence_sources.human_answer.staged_structure = _cloneJson(card.staged_structure);
      card.evidence_sources.human_answer.field_sources = _cloneJson(card.field_sources);
    }
    card.review_state = "pending";
    card.human_confirmed = false;
    if (card.evidence_sources && card.evidence_sources.human_answer) {
      card.evidence_sources.human_answer.human_confirmed = false;
    }
    card.reparse_preview = null;
    card.preview_error = "";
    qwenReviewSession.candidate_preview = null;
    _renderQwenReviewSession();
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
    card.review_state = "pending";
    card.human_confirmed = false;
    card.reparse_preview = null;
    card.preview_error = "";
    if (card.evidence_sources && card.evidence_sources.human_answer) {
      card.evidence_sources.human_answer.human_confirmed = false;
      card.evidence_sources.human_answer.staged_structure = _cloneJson(card.staged_structure);
      card.evidence_sources.human_answer.field_sources = _cloneJson(card.field_sources);
    }
    qwenReviewSession.candidate_preview = null;
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

  window.qwenReviewAdoptGemmaNumbers = function(index) {
    if (!qwenReviewSession || !qwenReviewSession.structures[index]) return;
    var card = qwenReviewSession.structures[index];
    var gemma = card.evidence_sources && card.evidence_sources.gemma;
    if (!gemma) return;
    var groups = _gemmaNumberGroups(gemma, card);
    if (!groups.length) return;
    var previous = _cloneJson(card.staged_structure || {});
    previous.number_groups = groups;
    card.staged_structure = previous;
    card.field_sources.numbers = {
      source: "Gemma suggestion",
      provider: "gemma4-26b-shadow",
      evidence_id: String(gemma.evidence_id || ""),
      human_confirmed: false
    };
    _recordGemmaAdoption(card, "numbers", gemma);
    _renderQwenReviewSession();
  };

  window.qwenReviewAdoptGemmaMultiplier = function(index) {
    if (!qwenReviewSession || !qwenReviewSession.structures[index]) return;
    var card = qwenReviewSession.structures[index];
    var gemma = card.evidence_sources && card.evidence_sources.gemma;
    if (!gemma) return;
    var rules = _gemmaMultiplierRules(gemma);
    if (!rules.length) return;
    var previous = _cloneJson(card.staged_structure || {});
    previous.multiplier_rules = rules;
    previous.collision = _qwenCollisionFromRules(rules, previous.collision);
    card.staged_structure = previous;
    card.field_sources.multiplier = {
      source: "Gemma suggestion",
      provider: "gemma4-26b-shadow",
      evidence_id: String(gemma.evidence_id || ""),
      human_confirmed: false
    };
    _recordGemmaAdoption(card, "multiplier", gemma);
    _renderQwenReviewSession();
  };

  function _buildQwenReviewSummary() {
    if (!qwenReviewSession || !qwenReviewSession.structures.length || qwenReviewSession.structures.some(function(card) { return card.review_state !== "confirmed"; })) return null;
    return {
      schema_version: "vision-review-candidate-preview-v1",
      review_session_id: qwenReviewSession.review_session_id,
      game: qwenReviewSession.game,
      source_image_id: qwenReviewSession.source_image_id,
      image_sha256: qwenReviewSession.image_sha256,
      confirmed_structures: qwenReviewSession.structures.map(function(card) {
        var structure = card.staged_structure || {};
        return {
          structure_id: card.structure_id,
          primary_line_id: card.primary_line_id,
          source_line_ids: _cloneJson(card.source_line_ids),
          number_groups: _cloneJson(structure.number_groups || []),
          multiplier_rules: _cloneJson(structure.multiplier_rules || []),
          layout: structure.layout || "unknown",
          collision: structure.collision == null ? null : structure.collision,
          manual_edits: _cloneJson(card.manual_edits),
          field_sources: _cloneJson(card.field_sources || {}),
          suggestion_adoptions: _cloneJson(card.suggestion_adoptions || []),
          human_confirmed: card.human_confirmed === true
        };
      }),
      review_timestamp: new Date().toISOString(),
      human_confirmation_required: true,
      auto_apply: false,
      auto_confirm: false,
      auto_submit: false,
      registration_state: "browser_local_preview_only"
    };
  }

  function _renderQwenCompletion(summary) {
    var output = document.getElementById("qwen-review-completion");
    if (!output) return;
    output.innerHTML = '<div id="qwen-review-complete-status" style="padding:8px;background:#ecfdf5;border-left:4px solid #10b981;color:#065f46;font-weight:700">人工審核完成，尚未加入待選牌清單</div>' +
      '<details id="qwen-review-summary" style="font-size:11px;color:#475569;margin-top:6px"><summary>審核摘要／候選預覽</summary><pre style="white-space:pre-wrap">' +
      esc(JSON.stringify(summary, null, 2)) + '</pre></details>';
  }

  window.qwenCompleteReview = function() {
    var summary = _buildQwenReviewSummary();
    if (!summary) return;
    qwenReviewSession.candidate_preview = summary;
    _renderQwenCompletion(summary);
  };

  window.qwenGetReviewSession = function() { return _cloneJson(qwenReviewSession); };
  window.qwenGetReviewSummary = function() {
    return qwenReviewSession ? _cloneJson(qwenReviewSession.candidate_preview) : null;
  };
  window.qwenGetRecognitionResult = function() { return _cloneJson(qwenEvidenceResult); };
  window.qwenGetPpocrShadowEvidence = function() { return _cloneJson(ppocrShadowEvidence); };
  window.qwenGetGemmaShadowEvidence = function() { return _cloneJson(gemmaShadowEvidence); };

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
    html += '<section id="qwen-line-correction-tools" style="margin-top:10px;padding:8px;background:#fff;border:1px solid #e2e8f0;border-radius:6px">' +
      '<strong>逐行文字修正（輔助）</strong><div style="font-size:11px;color:#64748b;margin:3px 0 6px">主要審核請使用上方投注卡；此區只將文字帶入唯讀解析預覽。</div>';
    for (var toolIndex = 0; toolIndex < lines.length; toolIndex++) {
      var toolLine = lines[toolIndex] || {};
      html += '<div class="qwen-line-correction-tool" style="display:flex;gap:5px;margin-top:5px">' +
        '<input class="qwen-line-edit" value="' + esc(toolLine.text || '') + '" aria-label="第 ' + (toolIndex + 1) + ' 行手動修改" style="flex:1;min-width:0;font-family:monospace;padding:4px;border:1px solid #cbd5e1;border-radius:4px">' +
        '<button type="button" class="qwen-copy-line" onclick="qwenCopyLine(' + toolIndex + ')">複製</button>' +
        '<button type="button" class="qwen-stage-line" onclick="qwenStageLine(' + toolIndex + ')">帶入修正欄</button></div>';
    }
    html += '</section>';
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

    html += '<div id="qwen-correction-panel" style="margin-top:10px;padding:8px;background:#f8fafc;border:1px solid #cbd5e1;border-radius:4px">' +
      '<label for="qwen-review-editable" style="display:block;font-weight:700;margin-bottom:4px">前端修正暫存（可手動修改）</label>' +
      '<textarea id="qwen-review-editable" style="width:100%;min-height:70px;font-family:monospace;padding:6px" placeholder="先按某行的「帶入修正欄」，再人工修改"></textarea>' +
      '<div style="font-size:11px;color:#64748b;margin-top:3px">重新解析預覽沿用上方同一個遊戲類型；不使用 auto，document_mode 不代表遊戲類型。</div>' +
      '<button type="button" id="qwen-manual-reparse-btn" class="btn-primary" style="margin-top:6px" onclick="qwenPreviewReparse()">重新解析預覽</button>' +
      '<div id="qwen-manual-reparse-result" style="font-size:12px;margin-top:6px;color:#64748b">尚未要求解析預覽；未呼叫 /manual-reparse。</div>' +
      '</div>';
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
})();
</script>
"""
