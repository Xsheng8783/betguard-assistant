"""Simple image-to-editable-text extension for the Assist Panel."""


def render_vision_ui_section() -> str:
    """Return the image typing-aid UI; parsing stays in the text workflow."""
    return r"""
<div class="section" id="vision-section" style="display:none">
  <h3>圖片轉文字</h3>
  <div style="display:flex;align-items:center;gap:6px;flex-wrap:wrap;margin:8px 0 12px;font-size:13px;color:#475569">
    <strong>上傳圖片</strong><span>↓</span><strong>AI 辨識</strong><span>↓</span>
    <strong>修改文字</strong><span>↓</span><strong>解析／輔助填入</strong>
  </div>
  <p style="font-size:13px;color:#64748b;margin-bottom:10px">
    AI 只幫忙把圖片打成文字，可能會看錯。您可以直接修改、刪除或補上內容。
  </p>

  <div id="vision-drop-zone" style="border:2px dashed #cbd5e1;border-radius:8px;padding:18px;text-align:center;cursor:pointer;background:#f8fafc;margin-bottom:10px">
    <strong>上傳圖片</strong>
    <div style="font-size:12px;color:#64748b;margin-top:4px">點擊選擇、拖曳，或 Ctrl+V 貼上圖片</div>
    <div style="font-size:12px;color:#94a3b8">PNG / JPEG / WebP，上限 10 MiB</div>
    <input type="file" id="vision-file-input" accept="image/png,image/jpeg,image/webp" style="display:none">
  </div>

  <div id="vision-preview" style="display:none;margin-bottom:10px">
    <img id="vision-preview-img" alt="上傳圖片預覽" style="display:block;max-width:100%;max-height:280px;border:1px solid #e2e8f0;border-radius:5px;margin-bottom:7px">
    <div id="vision-file-summary" style="font-size:12px;color:#64748b"></div>
  </div>

  <button id="vision-run-btn" class="btn-primary" type="button" style="display:none;margin-bottom:10px">AI 辨識圖片</button>

  <label for="vision-transcription-text" style="display:block;font-size:15px;font-weight:700;margin-bottom:5px">圖片辨識文字</label>
  <textarea id="vision-transcription-text" placeholder="AI 辨識後會放在這裡；也可以直接手動輸入或修改。" style="min-height:210px"></textarea>
  <div id="vision-transcription-notices" style="font-size:13px;color:#92400e;margin-top:7px" aria-live="polite"></div>
  <div id="vision-preflight-status" style="font-size:14px;color:#475569;margin-top:7px" aria-live="polite"></div>
  <ul id="vision-preflight-issues" style="display:none;margin:5px 0 0 20px;font-size:13px;color:#92400e"></ul>
  <div style="display:flex;gap:7px;flex-wrap:wrap;margin-top:8px">
    <button id="vision-use-text-btn" class="btn-primary" type="button">解析／輔助填入</button>
    <button id="vision-save-verified-btn" type="button" disabled style="background:#0f766e;color:#fff">保存為正確範例</button>
    <button id="vision-clear-text-btn" type="button" style="background:#64748b;color:#fff">清空文字</button>
  </div>
  <div id="vision-acceptance-dataset-status" style="font-size:13px;color:#475569;margin-top:9px">已累積正確圖片範例：讀取中…</div>
  <div id="vision-status" class="status" aria-live="polite"></div>
  <div style="font-size:12px;color:#94a3b8;margin-top:7px">不會自動送出；auto-submit=false</div>
</div>

<script>
(function() {
  "use strict";
  var uploadedImageId = "";
  var dropZone = document.getElementById("vision-drop-zone");
  var fileInput = document.getElementById("vision-file-input");
  var runButton = document.getElementById("vision-run-btn");
  var transcription = document.getElementById("vision-transcription-text");
  var status = document.getElementById("vision-status");
  var saveVerifiedButton = document.getElementById("vision-save-verified-btn");
  var datasetStatus = document.getElementById("vision-acceptance-dataset-status");
  var transcriptionNotices = document.getElementById("vision-transcription-notices");
  var preflightStatus = document.getElementById("vision-preflight-status");
  var preflightIssues = document.getElementById("vision-preflight-issues");
  var useTextButton = document.getElementById("vision-use-text-btn");
  var transcriptionCaptureAvailable = false;
  var preflightTimer = null;
  var preflightSequence = 0;

  function setVisionStatus(message, isError) {
    status.textContent = message || "";
    status.style.color = isError ? "#b91c1c" : "#475569";
  }

  function renderDatasetStatus(data) {
    var count = Number(data.unique_human_verified_images || 0);
    var target = Number(data.target || 10);
    datasetStatus.textContent = "已累積正確圖片範例：" + count + " / " + target;
    if (data.acceptance_dataset_ready) {
      datasetStatus.textContent += " · ACCEPTANCE_DATASET_READY = YES";
    }
  }

  function refreshDatasetStatus() {
    fetch("/api/vision/v1/acceptance-dataset/status")
      .then(function(response) { return response.json(); })
      .then(function(data) {
        if (!data.ok) throw new Error("status unavailable");
        renderDatasetStatus(data);
      }).catch(function() {
        datasetStatus.textContent = "已累積正確圖片範例：目前無法讀取";
      });
  }

  function parserErrorText(data) {
    var details = (data.parser_errors || []).map(function(item) {
      var fragment = String(item.fragment || "");
      var messages = (item.messages || []).join("；");
      return (fragment ? "「" + fragment + "」：" : "") + messages;
    }).filter(Boolean);
    return details.length ? " " + details.join(" / ") : "";
  }

  function renderTranscriptionNotices(notices) {
    var messages = (notices || []).map(function(item) {
      return String(item.message || "");
    }).filter(Boolean);
    transcriptionNotices.textContent = messages.join(" ");
  }

  function renderPreflight(data) {
    preflightIssues.replaceChildren();
    preflightIssues.style.display = "none";
    if (!data || !transcription.value.trim()) {
      preflightStatus.textContent = "";
      return;
    }
    if (data.all_parseable) {
      preflightStatus.textContent = "全部文字可解析";
      preflightStatus.style.color = "#166534";
      return;
    }
    var count = Number(data.unresolved_count || 0);
    preflightStatus.textContent = "目前有 " + count + " 段文字無法完整解析，請檢查。";
    preflightStatus.style.color = "#b45309";
    (data.issues || []).forEach(function(issue) {
      var item = document.createElement("li");
      var line = Number(issue.line_no || 0);
      item.textContent = (line ? "第 " + line + " 行：" : "位置：") +
        String(issue.raw || "") + " — " + String(issue.reason || "無法完整解析");
      preflightIssues.appendChild(item);
    });
    if (preflightIssues.children.length) preflightIssues.style.display = "block";
  }

  function requestParserPreflight(text, showTransportError) {
    var submittedText = String(text || "");
    var sequence = ++preflightSequence;
    return fetch("/api/vision/v1/transcriptions/preflight", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({text: submittedText})
    }).then(function(response) { return response.json(); })
    .then(function(data) {
      if (!data.ok) throw new Error("parser preview failed");
      if (sequence === preflightSequence && transcription.value === submittedText) {
        renderPreflight(data);
      }
      return data;
    }).catch(function() {
      if (showTransportError) setVisionStatus("目前無法執行 parser 預檢，請稍後再試。", true);
      return null;
    });
  }

  function scheduleParserPreflight() {
    if (preflightTimer) clearTimeout(preflightTimer);
    if (!transcription.value.trim()) {
      preflightSequence += 1;
      renderPreflight(null);
      return;
    }
    preflightStatus.textContent = "正在檢查文字…";
    preflightStatus.style.color = "#475569";
    preflightTimer = setTimeout(function() {
      requestParserPreflight(transcription.value, false);
    }, 350);
  }

  function uploadImage(file) {
    if (!file) return;
    setVisionStatus("圖片上傳中…", false);
    runButton.style.display = "none";
    transcriptionCaptureAvailable = false;
    saveVerifiedButton.disabled = true;
    renderTranscriptionNotices([]);
    renderPreflight(null);
    var reader = new FileReader();
    reader.onload = function() {
      fetch("/api/vision/v1/images", {
        method: "POST",
        headers: {
          "Content-Type": file.type || "application/octet-stream",
          "X-Filename": encodeURIComponent(file.name || "image")
        },
        body: new Uint8Array(reader.result)
      }).then(function(response) { return response.json(); })
      .then(function(data) {
        if (!data.ok) throw new Error((data.error && data.error.message) || "圖片上傳失敗");
        uploadedImageId = String(data.image.image_id || "");
        transcription.value = "";
        document.getElementById("vision-preview-img").src =
          "/api/vision/v1/images/" + encodeURIComponent(uploadedImageId);
        document.getElementById("vision-preview").style.display = "block";
        document.getElementById("vision-file-summary").textContent =
          (data.image.original_filename || "圖片") + " · " +
          data.image.width + " × " + data.image.height;
        runButton.style.display = "inline-block";
        runButton.textContent = "AI 辨識圖片";
        setVisionStatus("圖片已上傳，可以開始辨識。", false);
      }).catch(function(error) {
        uploadedImageId = "";
        setVisionStatus(error.message || "圖片上傳失敗", true);
      });
    };
    reader.onerror = function() { setVisionStatus("無法讀取圖片。", true); };
    reader.readAsArrayBuffer(file);
  }

  dropZone.addEventListener("click", function() { fileInput.click(); });
  fileInput.addEventListener("change", function() {
    if (fileInput.files && fileInput.files[0]) uploadImage(fileInput.files[0]);
  });
  dropZone.addEventListener("dragover", function(event) { event.preventDefault(); });
  dropZone.addEventListener("drop", function(event) {
    event.preventDefault();
    uploadImage(event.dataTransfer && event.dataTransfer.files[0]);
  });
  document.addEventListener("paste", function(event) {
    if (document.getElementById("vision-section").style.display === "none") return;
    var items = (event.clipboardData && event.clipboardData.items) || [];
    for (var index = 0; index < items.length; index++) {
      if (items[index].type.indexOf("image/") === 0) {
        uploadImage(items[index].getAsFile());
        break;
      }
    }
  });

  runButton.addEventListener("click", function() {
    if (!uploadedImageId) {
      setVisionStatus("請先上傳圖片。", true);
      return;
    }
    runButton.disabled = true;
    runButton.textContent = "AI 辨識中…";
    setVisionStatus("正在把圖片轉成文字…", false);
    fetch("/api/vision/v1/transcriptions", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({image_id: uploadedImageId})
    }).then(function(response) { return response.json(); })
    .then(function(data) {
      runButton.disabled = false;
      runButton.textContent = "重新辨識";
      if (!data.ok) throw new Error((data.error && data.error.message) || "AI 辨識失敗");
      transcription.value = String(data.text || "");
      renderTranscriptionNotices(data.transcription_notices || []);
      renderPreflight(data.parser_preflight || null);
      transcriptionCaptureAvailable = data.verified_sample_capture_available === true;
      saveVerifiedButton.disabled = !transcriptionCaptureAvailable;
      transcription.focus();
      setVisionStatus(
        transcriptionCaptureAvailable
          ? "辨識完成。請檢查文字，需要時直接修改；確認正確後可保存範例。"
          : "辨識完成。請檢查文字，需要時直接修改。範例保存目前不可用，但仍可正常解析。",
        !transcriptionCaptureAvailable
      );
    }).catch(function(error) {
      runButton.disabled = false;
      runButton.textContent = "重新辨識";
      transcription.style.display = "block";
      renderTranscriptionNotices([]);
      renderPreflight(null);
      setVisionStatus((error.message || "AI 辨識失敗") + " 您仍可直接輸入文字。", true);
    });
  });

  document.getElementById("vision-clear-text-btn").addEventListener("click", function() {
    transcription.value = "";
    renderTranscriptionNotices([]);
    renderPreflight(null);
    transcription.focus();
    setVisionStatus("文字已清空。", false);
  });

  saveVerifiedButton.addEventListener("click", function() {
    var text = transcription.value.trim();
    if (!uploadedImageId || !transcriptionCaptureAvailable) {
      setVisionStatus("請先上傳圖片並完成 AI 辨識。", true);
      return;
    }
    if (!text) {
      setVisionStatus("正確文字不可為空白。", true);
      return;
    }
    saveVerifiedButton.disabled = true;
    setVisionStatus("正在用現有 parser 驗證並保存…", false);
    fetch("/api/vision/v1/acceptance-dataset/samples", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({
        image_id: uploadedImageId,
        human_verified_betguard_text: text
      })
    }).then(function(response) { return response.json(); })
    .then(function(data) {
      saveVerifiedButton.disabled = false;
      if (!data.ok) {
        throw new Error(
          ((data.error && data.error.message) || "保存失敗") + parserErrorText(data)
        );
      }
      renderDatasetStatus(data.dataset_status || {});
      setVisionStatus(
        "已保存正確範例（" + String(data.sample.revision_id || "新版本") + "）。您可以繼續解析／輔助填入。",
        false
      );
    }).catch(function(error) {
      saveVerifiedButton.disabled = false;
      setVisionStatus(error.message || "保存失敗，請檢查文字。", true);
    });
  });

  transcription.addEventListener("input", scheduleParserPreflight);

  useTextButton.addEventListener("click", function() {
    var text = transcription.value;
    if (!text.trim()) {
      setVisionStatus("請先輸入或辨識牌單文字。", true);
      return;
    }
    useTextButton.disabled = true;
    requestParserPreflight(text, true).then(function(preflight) {
      useTextButton.disabled = false;
      if (!preflight) return;
      if (!preflight.all_parseable) {
        setVisionStatus("請直接修改無法解析的文字後再試。", true);
        transcription.focus();
        return;
      }
      // This is the only handoff: fully parseable image text enters the exact
      // same textarea and createBatch() function used by pasted text.  No
      // image-specific parser or queue path is introduced.
      document.getElementById("batch-text").value = text;
      switchMode("text");
      createBatch();
    });
  });

  refreshDatasetStatus();
})();
</script>
"""
