/* PhotoMaster 雙圖修圖品質批次評分 — frontend logic.
   Mirrors the main PhotoAgent frontend conventions: vanilla JS, an IIFE
   exporting window.Analyze, camelCase functions, fetch + polling. */
window.Analyze = (() => {
  const CONFIG = window.AnalyzeConfig || {};
  const BASE_URL =
    CONFIG.apiBaseUrl || window.location.pathname.replace(/\/[^/]*$/, "");
  const POLL_INTERVAL_MS = CONFIG.pollIntervalMs || 1500;
  const POLL_TIMEOUT_MS = 30 * 60 * 1000;

  const DIMENSION_LABELS = {
    subject_identity_preservation: "主體／身分保留",
    scene_content_preservation: "場景／內容保留",
    crop_boundary_safety: "裁切／邊界安全",
    composition_balance_improvement: "構圖／視覺平衡改善",
    geometry_perspective_integrity: "幾何／透視完整性",
    lighting_exposure_improvement: "光線／曝光改善",
    color_tone_improvement: "色彩／白平衡／色調改善",
    detail_texture_preservation: "細節／材質保留",
    retouch_naturalness: "修圖自然度",
    output_artifact_integrity: "修圖技術瑕疵／輸出完整性",
  };

  const DEFECT_LABELS = {
    subject_identity_damage: "主體／身分破壞",
    scene_rebuild_or_replacement: "場景重建／替換",
    unintended_content_change: "內容遭不當增刪",
    required_content_loss: "必要內容遺失",
    crop_boundary_damage: "裁切／邊界破壞",
    anatomy_shape_material_damage: "解剖／形狀／材質破壞",
    geometry_perspective_damage: "幾何／透視破壞",
    artificial_border_or_padding: "人工邊框／Padding",
    lighting_shadow_regression: "光線／陰影退步",
    color_tone_regression: "色彩／色調退步",
    detail_texture_damage: "細節／材質損失",
    overprocessing_synthetic_look: "過度處理／合成感",
    new_generation_artifact: "新增生成瑕疵",
    other: "其他",
  };

  const STATUS_LABELS = { scored: "已評分", invalid: "無效", error: "失敗" };
  const ORIGINAL_IMAGE_MIME_TYPES = {
    png: "image/png",
    jpg: "image/jpeg",
    jpeg: "image/jpeg",
    webp: "image/webp",
  };

  const els = {
    sections: {
      upload: document.querySelector("#upload-section"),
      progress: document.querySelector("#progress-section"),
      results: document.querySelector("#results-section"),
    },
    dropZone: document.querySelector("#drop-zone"),
    fileInput: document.querySelector("#file-input"),
    selectedFile: document.querySelector("#selected-file"),
    evalModel: document.querySelector("#eval-model"),
    startBtn: document.querySelector("#start-btn"),
    uploadError: document.querySelector("#upload-error"),

    progressLabel: document.querySelector("#progress-label"),
    progressFill: document.querySelector("#progress-fill"),
    progressCounts: document.querySelector("#progress-counts"),
    progressError: document.querySelector("#progress-error"),
    progressBack: document.querySelector("#progress-back"),

    resultsSummary: document.querySelector("#results-summary"),
    filterZip: document.querySelector("#filter-zip"),
    filterDefect: document.querySelector("#filter-defect"),
    sortBy: document.querySelector("#sort-by"),
    summaryBtn: document.querySelector("#summary-btn"),
    dlZip: document.querySelector("#dl-zip"),
    newUpload: document.querySelector("#new-upload"),
    resultsGrid: document.querySelector("#results-grid"),
    resultsEmpty: document.querySelector("#results-empty"),

    summaryModal: document.querySelector("#summary-modal"),
    summaryPanel: document.querySelector("#summary-modal .modal-panel"),
    summaryBackdrop: document.querySelector("#summary-modal .modal-backdrop"),
    summaryClose: document.querySelector("#summary-close"),
    summaryAverage: document.querySelector("#summary-average"),
    summaryHighest: document.querySelector("#summary-highest"),
    summaryLowest: document.querySelector("#summary-lowest"),
    summaryScored: document.querySelector("#summary-scored"),
    summaryCounts: document.querySelector("#summary-counts"),
    summaryDimensions: document.querySelector("#summary-dimensions"),
    summaryEmpty: document.querySelector("#summary-empty"),

    modal: document.querySelector("#detail-modal"),
    modalPanel: document.querySelector("#detail-modal .modal-panel"),
    modalBackdrop: document.querySelector("#detail-modal .modal-backdrop"),
    detailClose: document.querySelector("#detail-close"),
    detailOriginalImage: document.querySelector("#detail-original-image"),
    detailOriginalEmpty: document.querySelector("#detail-original-empty"),
    detailOriginalMeta: document.querySelector("#detail-original-meta"),
    detailResultImage: document.querySelector("#detail-result-image"),
    detailResultEmpty: document.querySelector("#detail-result-empty"),
    detailResultMeta: document.querySelector("#detail-result-meta"),
    detailTitle: document.querySelector("#detail-title"),
    detailScore: document.querySelector("#detail-score"),
    detailSummary: document.querySelector("#detail-summary"),
    detailError: document.querySelector("#detail-error"),
    detailDimensions: document.querySelector("#detail-dimensions"),
    detailDefects: document.querySelector("#detail-defects"),
    detailSuggestions: document.querySelector("#detail-suggestions"),
    detailMeta: document.querySelector("#detail-meta"),
  };

  let selectedFiles = [];
  let jobs = []; // one entry per uploaded ZIP: {jobId, filename, file, completed, failed, invalid, total_runs}
  let allResults = [];
  let apiReady = false;
  let apiError = "正在檢查評分服務設定…";
  let zipArchiveCache = new Map(); // jobId -> Promise<JSZip>
  let activeOriginalKey = null;
  let activeOriginalUrl = null;
  let detailRequestToken = 0;

  // ── Helpers ──────────────────────────────────────────────────────────────

  function sleep(ms) {
    return new Promise((resolve) => setTimeout(resolve, ms));
  }

  function showSection(name) {
    Object.entries(els.sections).forEach(([key, section]) => {
      section.classList.toggle("visible", key === name);
    });
  }

  function isNumeric(value) {
    return value !== null && value !== undefined && value !== "" && Number.isFinite(Number(value));
  }

  function fmtScore(score) {
    return isNumeric(score) ? Number(score).toFixed(1) : "–";
  }

  function clearOriginalPreviewState() {
    detailRequestToken += 1;
    zipArchiveCache = new Map();
    activeOriginalKey = null;
    if (activeOriginalUrl) URL.revokeObjectURL(activeOriginalUrl);
    activeOriginalUrl = null;
  }

  // ── Upload flow ──────────────────────────────────────────────────────────

  function updateUploadAvailability() {
    els.startBtn.disabled = !selectedFiles.length || !apiReady;
    if (!apiReady) {
      els.uploadError.textContent = apiError;
      els.uploadError.hidden = false;
    } else {
      els.uploadError.hidden = true;
    }
  }

  async function checkApiAvailability() {
    apiReady = false;
    apiError = "正在檢查評分服務設定…";
    updateUploadAvailability();
    try {
      const response = await fetch(`${BASE_URL}/api/health`);
      const data = await response.json().catch(() => ({}));
      if (!response.ok || !data.openai_key_present) {
        throw new Error(data.error || "未設定 OPENAI_API_KEY，無法執行圖片評分。");
      }
      apiReady = true;
      apiError = "";
    } catch (err) {
      apiReady = false;
      apiError = err.message || String(err);
    }
    updateUploadAvailability();
    return apiReady;
  }

  function setSelectedFiles(fileList) {
    clearOriginalPreviewState();
    const files = Array.from(fileList || []).filter((f) => f && f.name.toLowerCase().endsWith(".zip"));
    if (!files.length) {
      selectedFiles = [];
      els.startBtn.disabled = true;
      els.selectedFile.hidden = true;
      els.uploadError.textContent = "請選擇 .zip 檔案。";
      els.uploadError.hidden = false;
      return;
    }
    selectedFiles = files;
    const totalMb = files.reduce((sum, f) => sum + f.size, 0) / 1024 / 1024;
    els.selectedFile.textContent = files.length === 1
      ? `已選擇：${files[0].name}（${(files[0].size / 1024 / 1024).toFixed(1)} MB）`
      : `已選擇 ${files.length} 個 ZIP（共 ${totalMb.toFixed(1)} MB）：${files.map((f) => f.name).join("、")}`;
    els.selectedFile.hidden = false;
    updateUploadAvailability();
  }

  async function startEvaluation() {
    if (!selectedFiles.length) return;
    if (!apiReady && !(await checkApiAvailability())) return;
    showSection("progress");
    els.progressLabel.textContent = "上傳中…";
    els.progressFill.style.width = "0%";
    els.progressCounts.textContent = "";
    els.progressError.hidden = true;
    els.progressBack.hidden = true;

    const model = els.evalModel?.value.trim();
    jobs = [];
    allResults = [];

    try {
      for (let index = 0; index < selectedFiles.length; index += 1) {
        const file = selectedFiles[index];
        const position = selectedFiles.length > 1 ? `（${index + 1}/${selectedFiles.length}）` : "";
        els.progressLabel.textContent = `上傳中…${position} ${file.name}`;
        els.progressFill.style.width = "0%";

        const form = new FormData();
        form.append("file", file, file.name);
        if (model) form.append("model", model);
        const response = await fetch(`${BASE_URL}/api/evaluations`, { method: "POST", body: form });
        const data = await response.json().catch(() => ({}));
        if (!response.ok || !data.job_id) {
          throw new Error(`${file.name}：${data.error || `上傳失敗（HTTP ${response.status}）`}`);
        }

        const job = { jobId: data.job_id, filename: file.name, file, completed: 0, failed: 0, invalid: 0, total_runs: 0 };
        jobs.push(job);
        await pollJob(job, index, selectedFiles.length);
        await loadJobResults(job);
      }
      buildFilters();
      renderResults();
      showSection("results");
    } catch (err) {
      els.progressLabel.textContent = "評分失敗";
      els.progressError.textContent = err.message || String(err);
      els.progressError.hidden = false;
      els.progressBack.hidden = false;
    }
  }

  async function pollJob(job, index, total) {
    const position = total > 1 ? `（${index + 1}/${total}）${job.filename} · ` : "";
    const deadline = Date.now() + POLL_TIMEOUT_MS;
    while (Date.now() < deadline) {
      const response = await fetch(`${BASE_URL}/api/evaluations/${encodeURIComponent(job.jobId)}`);
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.error || "查詢進度失敗。");

      const done = (data.completed || 0) + (data.failed || 0) + (data.invalid || 0);
      els.progressLabel.textContent = data.status === "pending" ? "準備中…" : "評分中…";
      els.progressFill.style.width = `${data.progress || 0}%`;
      els.progressCounts.textContent = data.total_runs
        ? `${position}完成 ${done}/${data.total_runs}（成功 ${data.completed}、失敗 ${data.failed}、無效 ${data.invalid}）`
        : `${position}解析 ZIP 中…`;

      if (data.status === "completed") return;
      if (data.status === "failed") throw new Error(`${job.filename}：${data.error || "整批評分失敗。"}`);
      await sleep(POLL_INTERVAL_MS);
    }
    throw new Error(`處理逾時，請稍後再試。（Job ID: ${job.jobId}）`);
  }

  async function loadJobResults(job) {
    const response = await fetch(`${BASE_URL}/api/evaluations/${encodeURIComponent(job.jobId)}/results`);
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(`${job.filename}：${data.error || "取得結果失敗。"}`);
    job.completed = data.completed || 0;
    job.failed = data.failed || 0;
    job.invalid = data.invalid || 0;
    job.total_runs = data.total_runs || 0;
    // Tag every row with its source ZIP so results from multiple uploads can be
    // shown together and filtered by ZIP.
    (data.results || []).forEach((result) => {
      allResults.push({ ...result, _jobId: job.jobId, _zip: job.filename });
    });
  }

  // ── Filters / sorting ────────────────────────────────────────────────────

  function rebuildSelect(select, values, labeler) {
    while (select.options.length > 1) select.remove(1);
    values.forEach((value) => {
      const option = document.createElement("option");
      option.value = value;
      option.textContent = labeler ? labeler(value) : value;
      select.appendChild(option);
    });
    select.value = "";
  }

  function buildFilters() {
    // Value is the job id (unique per upload); label is the ZIP filename, so
    // two uploads sharing a filename stay distinguishable.
    while (els.filterZip.options.length > 1) els.filterZip.remove(1);
    jobs.forEach((job) => {
      const option = document.createElement("option");
      option.value = job.jobId;
      option.textContent = job.filename;
      els.filterZip.appendChild(option);
    });
    els.filterZip.value = "";
    // The ZIP filter is only useful when more than one upload is present.
    els.filterZip.hidden = jobs.length <= 1;

    const codes = [...new Set(allResults.flatMap((r) => (r.critical_defects || []).map((d) => d.code)))];
    rebuildSelect(els.filterDefect, codes, (v) => DEFECT_LABELS[v] || v);
  }

  function filteredResults() {
    const zip = els.filterZip.value;
    const defect = els.filterDefect.value;
    let rows = allResults.slice();

    if (zip) rows = rows.filter((r) => r._jobId === zip);
    if (defect) rows = rows.filter((r) => (r.critical_defects || []).some((d) => d.code === defect));

    const sort = els.sortBy.value;
    const score = (r) => (isNumeric(r.final_score) ? Number(r.final_score) : -1);
    if (sort === "score-desc") rows.sort((a, b) => score(b) - score(a));
    else if (sort === "score-asc") rows.sort((a, b) => score(a) - score(b));
    else rows.sort((a, b) => (a.task_id + a.run_id).localeCompare(b.task_id + b.run_id));
    return rows;
  }

  // ── Results rendering ────────────────────────────────────────────────────

  function renderResults() {
    const zip = els.filterZip.value;
    const scopedJobs = zip ? jobs.filter((j) => j.jobId === zip) : jobs;
    const agg = scopedJobs.reduce((acc, j) => {
      acc.total += j.total_runs;
      acc.completed += j.completed;
      acc.failed += j.failed;
      acc.invalid += j.invalid;
      return acc;
    }, { total: 0, completed: 0, failed: 0, invalid: 0 });
    const zipNote = jobs.length > 1 && !zip ? `${jobs.length} 個 ZIP · ` : "";
    els.resultsSummary.textContent =
      `${zipNote}共 ${agg.total || allResults.length} 筆：成功 ${agg.completed}、失敗 ${agg.failed}、無效 ${agg.invalid}`;

    const rows = filteredResults();
    els.resultsGrid.innerHTML = "";
    els.resultsEmpty.hidden = rows.length > 0;
    rows.forEach((result) => els.resultsGrid.appendChild(buildResultCard(result)));
  }

  function buildResultCard(result) {
    const card = document.createElement("div");
    card.className = `result-card ${result.status}`;

    const media = document.createElement("div");
    media.className = "rc-media";
    if (result.thumbnail) {
      const img = document.createElement("img");
      img.src = result.thumbnail;
      img.alt = `${result.task_id}/${result.run_id}`;
      media.appendChild(img);
    } else {
      const placeholder = document.createElement("div");
      placeholder.className = "no-thumb";
      placeholder.textContent = "🖼";
      media.appendChild(placeholder);
    }

    const badge = document.createElement("span");
    badge.className = "rc-badge" + (result.status !== "scored" ? " error-badge" : "");
    badge.textContent = STATUS_LABELS[result.status] || result.status;
    media.appendChild(badge);

    if (result.status === "scored") {
      const scoreWrap = document.createElement("div");
      scoreWrap.className = "rc-score";
      const num = document.createElement("span");
      num.className = "score-num";
      num.textContent = `${fmtScore(result.final_score)}/100`;
      scoreWrap.appendChild(num);
      media.appendChild(scoreWrap);
    }

    const body = document.createElement("div");
    body.className = "rc-body";
    // Three lines: original image filename, run id, then source ZIP.
    const original = document.createElement("p");
    original.className = "rc-name";
    original.textContent = result.original_image || "—";
    original.title = result.original_image || "";
    body.appendChild(original);

    const run = document.createElement("p");
    run.className = "rc-run";
    run.textContent = result.run_id;
    run.title = result.run_id;
    body.appendChild(run);

    const zip = document.createElement("p");
    zip.className = "rc-zip";
    zip.textContent = result._zip || "";
    zip.title = result._zip || "";
    body.appendChild(zip);

    const defects = result.critical_defects || [];
    if (defects.length) {
      const flags = document.createElement("div");
      flags.className = "rc-flags";
      defects.slice(0, 3).forEach((defect) => {
        const chip = document.createElement("span");
        chip.className = `defect-chip sev-${defect.severity}`;
        chip.textContent = DEFECT_LABELS[defect.code] || defect.code;
        flags.appendChild(chip);
      });
      if (defects.length > 3) {
        const more = document.createElement("span");
        more.className = "defect-chip";
        more.textContent = `+${defects.length - 3}`;
        flags.appendChild(more);
      }
      body.appendChild(flags);
    }

    card.appendChild(media);
    card.appendChild(body);
    card.addEventListener("click", () => openDetail(result));
    return card;
  }

  // ── Batch summary modal ──────────────────────────────────────────────────

  function fmtSummaryScore(score) {
    return isNumeric(score) ? `${fmtScore(score)}/100` : "–";
  }

  function renderSummaryDimensions(summary) {
    els.summaryDimensions.innerHTML = "";
    els.summaryDimensions.hidden = summary.scored === 0;
    els.summaryEmpty.hidden = summary.scored > 0;
    if (!summary.scored) return;

    Object.keys(DIMENSION_LABELS).forEach((key) => {
      const dimension = summary.dimensions[key];
      const row = document.createElement("div");
      row.className = "dim-row";

      const head = document.createElement("div");
      head.className = "dim-head";
      const name = document.createElement("span");
      name.className = "dim-name";
      name.textContent = DIMENSION_LABELS[key];
      const meta = document.createElement("span");
      meta.className = "dim-weight";
      const details = [];
      if (isNumeric(dimension?.weight)) details.push(`權重 ${dimension.weight}%`);
      details.push(`${dimension?.count || 0} 筆`);
      meta.textContent = details.join(" · ");
      const score = document.createElement("span");
      score.className = "dim-score" + (isNumeric(dimension?.average) ? "" : " na");
      score.textContent = fmtSummaryScore(dimension?.average);
      head.appendChild(name);
      head.appendChild(meta);
      head.appendChild(score);
      row.appendChild(head);

      if (isNumeric(dimension?.average)) {
        const bar = document.createElement("div");
        bar.className = "dim-bar";
        const fill = document.createElement("div");
        fill.className = "dim-bar-fill";
        fill.style.width = `${Math.max(0, Math.min(100, Number(dimension.average)))}%`;
        bar.appendChild(fill);
        row.appendChild(bar);
      }
      els.summaryDimensions.appendChild(row);
    });
  }

  function openSummary() {
    const summary = window.AnalyzeSummary.calculateSummary(
      allResults,
      Object.keys(DIMENSION_LABELS),
    );
    els.summaryAverage.textContent = fmtSummaryScore(summary.average);
    els.summaryHighest.textContent = fmtSummaryScore(summary.highest);
    els.summaryLowest.textContent = fmtSummaryScore(summary.lowest);
    els.summaryScored.textContent = `${summary.scored} / ${summary.total}`;
    els.summaryCounts.textContent =
      `共 ${summary.total} 筆：已評分 ${summary.scored}、失敗 ${summary.failed}、` +
      `無效 ${summary.invalid}；重大瑕疵 ${summary.criticalDefects} 項`;
    renderSummaryDimensions(summary);
    els.summaryModal.hidden = false;
    els.summaryPanel.scrollTop = 0;
  }

  function closeSummary() {
    els.summaryModal.hidden = true;
  }

  // ── Detail modal ─────────────────────────────────────────────────────────

  function setDetailImage(image, empty, source, alt, emptyMessage = "") {
    image.onerror = null;
    if (emptyMessage) empty.textContent = emptyMessage;
    if (typeof source !== "string" || !source.trim()) {
      image.removeAttribute("src");
      image.hidden = true;
      empty.hidden = false;
      return;
    }

    image.alt = alt;
    image.hidden = false;
    empty.hidden = true;
    image.onerror = () => {
      image.hidden = true;
      empty.hidden = false;
    };
    image.src = source;
  }

  function getZipArchive(result) {
    const job = jobs.find((j) => j.jobId === result._jobId);
    if (!job || !job.file) return Promise.reject(new Error("找不到原始 ZIP。"));
    if (!window.JSZip) return Promise.reject(new Error("ZIP 讀取元件未載入。"));
    let promise = zipArchiveCache.get(job.jobId);
    if (!promise) {
      promise = window.JSZip.loadAsync(job.file).catch((error) => {
        if (zipArchiveCache.get(job.jobId) === promise) zipArchiveCache.delete(job.jobId);
        throw error;
      });
      zipArchiveCache.set(job.jobId, promise);
    }
    return promise;
  }

  function zipEntryParts(entry) {
    return entry.name.replace(/\\/g, "/").split("/").filter(Boolean);
  }

  function findOriginalZipEntry(archive, result) {
    if (!result.original_image) return null;

    const entries = Object.values(archive.files)
      .filter((entry) => !entry.dir)
      .map((entry) => ({ entry, parts: zipEntryParts(entry) }));
    const taskPrefixes = new Set();

    entries.forEach(({ parts }) => {
      const length = parts.length;
      if (
        length >= 3
        && parts[length - 1] === "result.png"
        && parts[length - 3] === result.task_id
        && /^run_\d+$/.test(parts[length - 2])
      ) {
        taskPrefixes.add(parts.slice(0, -2).join("/"));
      }
    });

    if (taskPrefixes.size === 1) {
      const [taskPrefix] = taskPrefixes;
      const exactMatches = entries.filter(({ parts }) => (
        parts[parts.length - 1] === result.original_image
        && parts.slice(0, -1).join("/") === taskPrefix
      ));
      if (exactMatches.length === 1) return exactMatches[0].entry;
    }

    const fallbackMatches = entries.filter(({ parts }) => (
      parts.length >= 2
      && parts[parts.length - 2] === result.task_id
      && parts[parts.length - 1] === result.original_image
    ));
    return fallbackMatches.length === 1 ? fallbackMatches[0].entry : null;
  }

  async function prepareOriginalPreview(result) {
    const archive = await getZipArchive(result);
    const entry = findOriginalZipEntry(archive, result);
    if (!entry) return null;

    // Namespace the cache key by job id: two ZIPs can hold identically named
    // entries, so entry.name alone is not unique across uploads.
    const key = `${result._jobId}::${entry.name}`;
    if (key === activeOriginalKey && activeOriginalUrl) {
      return { key, url: activeOriginalUrl };
    }

    const extension = result.original_image.split(".").pop().toLowerCase();
    const bytes = await entry.async("uint8array");
    return {
      key,
      blob: new Blob([bytes], { type: ORIGINAL_IMAGE_MIME_TYPES[extension] || "application/octet-stream" }),
    };
  }

  async function showOriginalPreview(result, requestToken) {
    try {
      const preview = await prepareOriginalPreview(result);
      if (requestToken !== detailRequestToken) return;
      if (!preview) {
        setDetailImage(
          els.detailOriginalImage,
          els.detailOriginalEmpty,
          null,
          "原始照片",
          "無法顯示原圖",
        );
        return;
      }

      let source = preview.url;
      if (!source) {
        if (activeOriginalUrl) URL.revokeObjectURL(activeOriginalUrl);
        source = URL.createObjectURL(preview.blob);
        activeOriginalKey = preview.key;
        activeOriginalUrl = source;
      }
      setDetailImage(
        els.detailOriginalImage,
        els.detailOriginalEmpty,
        source,
        `${result.task_id} 原圖`,
        "無法顯示原圖",
      );
    } catch (_error) {
      if (requestToken !== detailRequestToken) return;
      setDetailImage(
        els.detailOriginalImage,
        els.detailOriginalEmpty,
        null,
        "原始照片",
        "無法顯示原圖",
      );
    }
  }

  function openDetail(result) {
    const requestToken = ++detailRequestToken;
    els.detailOriginalMeta.textContent = result.original_image || "";
    setDetailImage(
      els.detailOriginalImage,
      els.detailOriginalEmpty,
      null,
      `${result.task_id} 原圖`,
      result.original_image ? "載入原圖中…" : "無法顯示原圖",
    );
    setDetailImage(
      els.detailResultImage,
      els.detailResultEmpty,
      result.thumbnail,
      `${result.task_id} ${result.run_id} 修圖結果`,
    );
    const resultModel = result.source_metadata?.image_model || result.model || "";
    els.detailResultMeta.textContent = resultModel ? `${result.run_id} · ${resultModel}` : result.run_id;
    els.detailTitle.textContent = `${result.task_id} / ${result.run_id}`;
    els.detailScore.textContent = result.status === "scored" ? `${fmtScore(result.final_score)}/100` : "–";
    els.detailSummary.textContent = result.summary || "";

    els.detailError.hidden = !result.error;
    els.detailError.textContent = result.error || "";

    renderDimensions(result);
    renderDefects(result);
    renderSuggestions(result);

    const meta = [];
    if (jobs.length > 1 && result._zip) meta.push(`來源 ZIP：${result._zip}`);
    const scoringModel = result.model || CONFIG.evalModel;
    if (scoringModel) meta.push(`評分模型：${scoringModel}`);
    if (result.original_image) meta.push(`原圖：${result.original_image}`);
    if (result.source_metadata?.image_model) meta.push(`生成模型：${result.source_metadata.image_model}`);
    if (result.source_metadata?.image_quality) meta.push(`生成品質：${result.source_metadata.image_quality}`);
    if (isNumeric(result.latency_ms)) meta.push(`延遲：${Math.round(result.latency_ms)} ms`);
    if (result.retry_count) meta.push(`重試：${result.retry_count} 次`);
    if (result.usage?.total_tokens) meta.push(`tokens：${result.usage.total_tokens}`);
    els.detailMeta.textContent = meta.join("　|　");

    els.modal.hidden = false;
    els.modalPanel.scrollTop = 0;
    if (result.original_image) void showOriginalPreview(result, requestToken);
  }

  function renderDimensions(result) {
    els.detailDimensions.innerHTML = "";
    const dims = result.dimensions;
    if (!dims) {
      const note = document.createElement("p");
      note.className = "empty-note";
      note.textContent = "此筆沒有分項分數。";
      els.detailDimensions.appendChild(note);
      return;
    }
    Object.keys(DIMENSION_LABELS).forEach((key) => {
      const dim = dims[key];
      if (!dim) return;
      const row = document.createElement("div");
      row.className = "dim-row";

      const head = document.createElement("div");
      head.className = "dim-head";
      const name = document.createElement("span");
      name.className = "dim-name";
      name.textContent = DIMENSION_LABELS[key];
      const weight = document.createElement("span");
      weight.className = "dim-weight";
      weight.textContent = `權重 ${dim.weight}%`;
      const score = document.createElement("span");
      score.className = "dim-score" + (dim.status !== "scored" ? " na" : "");
      score.textContent = dim.status === "scored" ? `${dim.score}/100` : "N/A";
      head.appendChild(name);
      head.appendChild(weight);
      head.appendChild(score);
      row.appendChild(head);

      if (dim.status === "scored") {
        const bar = document.createElement("div");
        bar.className = "dim-bar";
        const fill = document.createElement("div");
        fill.className = "dim-bar-fill";
        fill.style.width = `${dim.score}%`;
        bar.appendChild(fill);
        row.appendChild(bar);
      }

      if (dim.reason) {
        const reason = document.createElement("p");
        reason.className = "dim-reason";
        reason.textContent = dim.reason;
        row.appendChild(reason);
      }
      if (dim.issues?.length) {
        const list = document.createElement("ul");
        list.className = "dim-issues";
        dim.issues.forEach((issue) => {
          const li = document.createElement("li");
          li.textContent = issue;
          list.appendChild(li);
        });
        row.appendChild(list);
      }
      els.detailDimensions.appendChild(row);
    });
  }

  function renderDefects(result) {
    els.detailDefects.innerHTML = "";
    const defects = result.critical_defects || [];
    if (!defects.length) {
      const li = document.createElement("li");
      li.className = "empty-note";
      li.textContent = "未偵測到重大瑕疵。";
      els.detailDefects.appendChild(li);
      return;
    }
    defects.forEach((defect) => {
      const li = document.createElement("li");
      const chip = document.createElement("span");
      chip.className = `defect-chip sev-${defect.severity}`;
      chip.textContent = `${defect.severity} · ${DEFECT_LABELS[defect.code] || defect.code}`;
      const text = document.createElement("span");
      text.textContent = defect.description || "";
      li.appendChild(chip);
      li.appendChild(text);
      els.detailDefects.appendChild(li);
    });
  }

  function renderSuggestions(result) {
    els.detailSuggestions.innerHTML = "";
    const suggestions = result.suggestions || [];
    if (!suggestions.length) {
      const li = document.createElement("li");
      li.className = "empty-note";
      li.textContent = "無建議。";
      els.detailSuggestions.appendChild(li);
      return;
    }
    suggestions.forEach((text) => {
      const li = document.createElement("li");
      li.textContent = text;
      els.detailSuggestions.appendChild(li);
    });
  }

  function closeDetail() {
    detailRequestToken += 1;
    els.modal.hidden = true;
  }

  // ── Downloads / reset ────────────────────────────────────────────────────

  function triggerDownload(href, filename) {
    const anchor = document.createElement("a");
    anchor.href = href;
    if (filename) anchor.download = filename;
    else anchor.download = "";
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
  }

  // Folder name for a job inside the combined ZIP: original filename without the
  // .zip suffix, path separators neutralised, and de-duplicated against siblings.
  function combinedFolderName(job, used) {
    let base = job.filename.replace(/\.zip$/i, "").replace(/[\\/]/g, "_").trim() || "results";
    let name = base;
    let suffix = 2;
    while (used.has(name)) name = `${base}_${suffix++}`;
    used.add(name);
    return name;
  }

  async function downloadResults() {
    // Always download every upload, regardless of the ZIP filter selection.
    const targets = jobs;
    if (!targets.length) return;

    // A single upload keeps the backend ZIP as-is. Multiple uploads are merged
    // into one ZIP: <原ZIP名稱>/results.json per job plus a shared prompt.json
    // (the evaluation prompt is identical across jobs).
    if (targets.length === 1 || !window.JSZip) {
      targets.forEach((job) =>
        triggerDownload(`${BASE_URL}/api/evaluations/${encodeURIComponent(job.jobId)}/download`));
      return;
    }

    els.dlZip.disabled = true;
    const originalLabel = els.dlZip.textContent;
    els.dlZip.textContent = "打包中…";
    try {
      const combined = new window.JSZip();
      const used = new Set();
      let promptCaptured = false;
      for (const job of targets) {
        const response = await fetch(`${BASE_URL}/api/evaluations/${encodeURIComponent(job.jobId)}/download`);
        if (!response.ok) throw new Error(`${job.filename}：下載失敗（HTTP ${response.status}）`);
        const inner = await window.JSZip.loadAsync(await response.blob());

        const folder = combinedFolderName(job, used);
        const resultsFile = inner.file("results.json");
        combined.file(`${folder}/results.json`, resultsFile ? await resultsFile.async("string") : "{}");

        if (!promptCaptured) {
          const promptFile = inner.file("prompt.json");
          if (promptFile) {
            combined.file("prompt.json", await promptFile.async("string"));
            promptCaptured = true;
          }
        }
      }
      const blob = await combined.generateAsync({ type: "blob" });
      const url = URL.createObjectURL(blob);
      triggerDownload(url, "evaluations-combined.zip");
      setTimeout(() => URL.revokeObjectURL(url), 10000);
    } catch (err) {
      // Fall back to separate per-job downloads so the user still gets the data.
      window.alert(`合併下載失敗，改為分別下載：\n${err.message || err}`);
      targets.forEach((job) =>
        triggerDownload(`${BASE_URL}/api/evaluations/${encodeURIComponent(job.jobId)}/download`));
    } finally {
      els.dlZip.disabled = false;
      els.dlZip.textContent = originalLabel;
    }
  }

  function resetToUpload() {
    clearOriginalPreviewState();
    selectedFiles = [];
    jobs = [];
    allResults = [];
    els.fileInput.value = "";
    els.selectedFile.hidden = true;
    els.summaryModal.hidden = true;
    els.modal.hidden = true;
    showSection("upload");
    checkApiAvailability();
  }

  // ── Event wiring ─────────────────────────────────────────────────────────

  els.fileInput.addEventListener("change", () => setSelectedFiles(els.fileInput.files));
  ["dragenter", "dragover"].forEach((type) => {
    els.dropZone.addEventListener(type, (event) => {
      event.preventDefault();
      els.dropZone.classList.add("dragover");
    });
  });
  ["dragleave", "drop"].forEach((type) => {
    els.dropZone.addEventListener(type, (event) => {
      event.preventDefault();
      els.dropZone.classList.remove("dragover");
    });
  });
  els.dropZone.addEventListener("drop", (event) => {
    const files = event.dataTransfer?.files;
    if (files && files.length) setSelectedFiles(files);
  });

  els.startBtn.addEventListener("click", startEvaluation);
  els.progressBack.addEventListener("click", resetToUpload);
  els.newUpload.addEventListener("click", resetToUpload);

  [els.filterZip, els.filterDefect, els.sortBy].forEach((select) => {
    select.addEventListener("change", renderResults);
  });

  els.summaryBtn.addEventListener("click", openSummary);
  els.dlZip.addEventListener("click", downloadResults);

  els.summaryClose.addEventListener("click", closeSummary);
  els.summaryBackdrop.addEventListener("click", closeSummary);
  els.detailClose.addEventListener("click", closeDetail);
  els.modalBackdrop.addEventListener("click", closeDetail);
  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") return;
    if (!els.summaryModal.hidden) closeSummary();
    else if (!els.modal.hidden) closeDetail();
  });

  checkApiAvailability();

  return { resetToUpload, calculateSummary: window.AnalyzeSummary.calculateSummary };
})();
