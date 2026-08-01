"""Vision image intake UI — extends assist panel with image mode.

Returns HTML/JS fragment injected into the assist panel page.
"""


def render_vision_ui_section() -> str:
    """Return HTML + JS for the vision image intake panel section."""
    return """
<div class="section" id="vision-section" style="display:none">
  <h3>圖片辨識（測試模式）</h3>
  <p class="muted" style="font-size:13px;margin-bottom:8px;color:#f59e0b">
    目前為圖片流程測試模式，顯示的是 Fake Provider 範例結果，<br>
    尚未進行真實 OCR，也不會建立審核或執行輔助填入。
  </p>

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
  <button id="vision-run-btn" class="btn-primary" style="display:none;margin-bottom:8px" onclick="visionRunJob()">執行測試辨識</button>

  <!-- Results -->
  <div id="vision-results" style="display:none;margin-top:8px">
    <h4 style="font-size:15px;margin-bottom:4px">辨識結果（Fake Provider）</h4>
    <div id="vision-results-body" style="font-size:14px;max-height:500px;overflow-y:auto;background:#f8fafc;border:1px solid #e2e8f0;border-radius:4px;padding:8px"></div>
  </div>
</div>

<script>
(function() {
  var dropZone = document.getElementById("vision-drop-zone");
  var fileInput = document.getElementById("vision-file-input");
  var uploadedImageId = null;

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
        document.getElementById("vision-run-btn").style.display = "inline-block";
        document.getElementById("vision-results").style.display = "none";
      }).catch(function() {
        status.textContent = "上傳失敗（網路錯誤）";
        status.style.color = "#ef4444";
      });
    };
    reader.readAsArrayBuffer(file);
  };

  // Delete
  window.visionDeleteImage = function() {
    if (!uploadedImageId) return;
    fetch("/api/vision/v1/images/" + uploadedImageId, { method: "DELETE" })
      .then(function(r) { return r.json(); })
      .then(function() {
        uploadedImageId = null;
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
      body: JSON.stringify({ image_id: uploadedImageId, provider_id: "fake", fixture: "bet_slip" })
    }).then(function(r) { return r.json(); })
    .then(function(data) {
      btn.disabled = false;
      btn.textContent = "執行測試辨識";
      if (!data.ok) {
        document.getElementById("vision-results-body").innerHTML = "<div style='color:#ef4444'>錯誤：" + esc((data.error && data.error.message) || "未知") + "</div>";
        document.getElementById("vision-results").style.display = "block";
        return;
      }
      _renderVisionResult(data.result);
    }).catch(function() {
      btn.disabled = false;
      btn.textContent = "執行測試辨識";
    });
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
