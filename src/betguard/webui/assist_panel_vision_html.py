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
      <img id="vision-preview-img" style="max-width:100%;max-height:300px;border:1px solid #e2e8f0;border-radius:4px">
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
  <button id="vision-run-btn" class="btn-primary" style="display:none;margin-bottom:8px" onclick="visionRunJob()">開始 AI 辨識</button>

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

  fetch("/api/vision/v1/providers").then(function(r) { return r.json(); }).then(function(data) {
    var providers = (data && data.providers) || [];
    var paid = providers.filter(function(p) { return p.id === "openai-vision-paid"; })[0];
    var status = document.getElementById("vision-provider-status");
    if (paid && paid.configured) {
      status.textContent = "付費 Vision 已設定；辨識後仍需逐行人工確認。";
      status.style.color = "#15803d";
    } else {
      status.textContent = "付費 Vision 尚未設定，會安全退回本機 OCR。";
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
        if (!data.ok) {
          status.textContent = "上傳失敗: " + (data.error && data.error.message || "未知錯誤");
          status.style.color = "#ef4444";
          return;
        }
        uploadedImageId = data.image.image_id;
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
        _createVisionAidImage(file, data.image).then(function(aidId) {
          uploadedAidImageId = aidId;
          if (aidId) {
            status.textContent = "已上傳；已建立低解析多欄放大辨識圖";
          }
          document.getElementById("vision-run-btn").style.display = "inline-block";
        }).catch(function() {
          uploadedAidImageId = null;
          status.textContent = "已上傳；放大辨識圖建立失敗，將使用原圖";
          status.style.color = "#b45309";
          document.getElementById("vision-run-btn").style.display = "inline-block";
        });
        document.getElementById("vision-results").style.display = "none";
      }).catch(function() {
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
    var deleteIds = [uploadedImageId, uploadedAidImageId].filter(function(id) { return !!id; });
    Promise.all(deleteIds.map(function(id) {
      return fetch("/api/vision/v1/images/" + id, { method: "DELETE" });
    }))
      .then(function() {
        uploadedImageId = null;
        uploadedAidImageId = null;
        document.getElementById("vision-preview").style.display = "none";
        document.getElementById("vision-run-btn").style.display = "none";
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
