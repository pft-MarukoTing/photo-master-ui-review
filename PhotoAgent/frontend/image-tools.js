window.PhotoAgentImageTools = (() => {
  const dataUrlCache = new WeakMap();
  const previewFileCache = new WeakMap();

  function fileToDataUrl(file) {
    if (dataUrlCache.has(file)) return dataUrlCache.get(file);

    const promise = new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(reader.result);
      reader.onerror = reject;
      reader.readAsDataURL(file);
    });
    dataUrlCache.set(file, promise);
    return promise;
  }

  function loadImageFromFile(file) {
    return new Promise((resolve, reject) => {
      const url = URL.createObjectURL(file);
      const image = new Image();
      image.onload = () => {
        URL.revokeObjectURL(url);
        resolve(image);
      };
      image.onerror = () => {
        URL.revokeObjectURL(url);
        reject(new Error(tr("無法讀取圖片，請確認檔案格式是否正確。")));
      };
      image.src = url;
    });
  }

  async function createPreviewFile(file, maxSide = 1536) {
    const cachedBySize = previewFileCache.get(file);
    if (cachedBySize?.has(maxSide)) return cachedBySize.get(maxSide);

    const promise = (async () => {
    const image = await loadImageFromFile(file);
    const longest = Math.max(image.naturalWidth, image.naturalHeight);
    if (!longest || longest <= maxSide) return file;

    const scale = maxSide / longest;
    const width = Math.round(image.naturalWidth * scale);
    const height = Math.round(image.naturalHeight * scale);
    const canvas = document.createElement("canvas");
    canvas.width = width;
    canvas.height = height;
    const ctx = canvas.getContext("2d");
    ctx.drawImage(image, 0, 0, width, height);

    const blob = await new Promise((resolve) => canvas.toBlob(resolve, "image/jpeg", 0.88));
    if (!blob) return file;
    return new File([blob], "photo-agent-preview.jpg", { type: "image/jpeg" });
    })();

    if (!cachedBySize) previewFileCache.set(file, new Map([[maxSide, promise]]));
    else cachedBySize.set(maxSide, promise);
    return promise;
  }

  function normalizeRoiBox(roiBox) {
    if (!roiBox) return null;
    const x = Number(roiBox.x);
    const y = Number(roiBox.y);
    const w = Number(roiBox.w);
    const h = Number(roiBox.h);
    if (![x, y, w, h].every(Number.isFinite) || w <= 0 || h <= 0) return null;

    const clampedX = Math.max(0, Math.min(0.98, x));
    const clampedY = Math.max(0, Math.min(0.98, y));
    return {
      x: clampedX,
      y: clampedY,
      w: Math.max(0.02, Math.min(1 - clampedX, w)),
      h: Math.max(0.02, Math.min(1 - clampedY, h))
    };
  }

  function cropDeltaReasons(before, after, stage) {
    if (!before || !after) return [];
    const changed = ["x", "y", "w", "h"].some((key) => Math.abs(Number(before[key]) - Number(after[key])) > 0.004);
    return changed ? [stage] : [];
  }

  function cleanAspectRatio(aspectRatio) {
    const value = String(aspectRatio || "").trim().toLowerCase();
    if (!value || value === "original" || value === "none") return "";
    return parseAspectRatio(value) ? value : "";
  }

  function recommendedAspectRatio(context, fallback = "") {
    return cleanAspectRatio(
      context?.recommended_aspect_ratio ||
      context?.crop_box?.aspect_ratio ||
      context?.crop_box?.aspectRatio ||
      fallback
    );
  }

  function protectedBoxes(context) {
    const boxes = [];
    const addBox = (box, kind, label) => {
      const normalized = normalizeRoiBox(box);
      if (!normalized) return;
      boxes.push({
        ...normalized,
        kind,
        label: label || box?.label || box?.reason || kind
      });
    };

    addBox(context?.roi_box, "roi", "ROI");
    addBox(context?.subject_extent_box, "subject", "subject extent");
    (Array.isArray(context?.protected_regions) ? context.protected_regions : []).forEach((region) => {
      addBox(region, "protected", region?.label);
    });

    return boxes;
  }

  // subject_extent_box remains a soft framing/display reference. It often
  // reaches an edge for a subject already cut by the captured frame, so using
  // it as a hard boundary would expand a useful composition crop back to the
  // complete image. ROI and explicitly selected protected regions stay hard.
  function hardCropProtectionBoxes(context) {
    return protectedBoxes(context).filter((box) => box.kind !== "subject");
  }

  function marginsForProtectedBox(box, context, baseMargin) {
    const safetyMargin = box.kind === "roi" ? Math.max(baseMargin, 0.05) : Math.max(baseMargin, 0.065);
    return {
      xMargin: safetyMargin,
      topMargin: safetyMargin,
      bottomMargin: safetyMargin
    };
  }

  function placeOrGrowAxis(start, size, protectedStart, protectedEnd) {
    const protectedSize = Math.max(0.02, protectedEnd - protectedStart);
    const newSize = Math.min(1, Math.max(size, protectedSize));
    let minStart = Math.max(0, protectedEnd - newSize);
    let maxStart = Math.min(protectedStart, 1 - newSize);
    if (minStart > maxStart) {
      minStart = Math.max(0, Math.min(1 - newSize, protectedStart));
      maxStart = minStart;
    }
    return [Math.min(Math.max(start, minStart), maxStart), newSize];
  }

  function protectRoi(crop, context, margin = 0.035) {
    const boxes = hardCropProtectionBoxes(context);
    if (!boxes.length) return crop;
    const padded = boxes.map((box) => {
      const { xMargin, topMargin, bottomMargin } = marginsForProtectedBox(box, context, margin);
      return {
        left: Math.max(0, box.x - xMargin),
        top: Math.max(0, box.y - topMargin),
        right: Math.min(1, box.x + box.w + xMargin),
        bottom: Math.min(1, box.y + box.h + bottomMargin)
      };
    });
    const [x, w] = placeOrGrowAxis(
      crop.x, crop.w,
      Math.min(...padded.map((box) => box.left)),
      Math.max(...padded.map((box) => box.right))
    );
    const [y, h] = placeOrGrowAxis(
      crop.y, crop.h,
      Math.min(...padded.map((box) => box.top)),
      Math.max(...padded.map((box) => box.bottom))
    );
    return { ...crop, x, y, w, h };
  }

  function normalizeCropBox(cropBox, context) {
    if (!cropBox || cropBox.enabled !== true) return null;
    const x = Number(cropBox.x);
    const y = Number(cropBox.y);
    const w = Number(cropBox.w);
    const h = Number(cropBox.h);
    if (![x, y, w, h].every(Number.isFinite) || w <= 0 || h <= 0) return null;

    let crop = {
      x: Math.max(0, Math.min(0.95, x)),
      y: Math.max(0, Math.min(0.95, y)),
      w: Math.max(0.12, Math.min(1 - Math.max(0, Math.min(0.95, x)), w)),
      h: Math.max(0.12, Math.min(1 - Math.max(0, Math.min(0.95, y)), h)),
      aspectRatio: cleanAspectRatio(cropBox.aspect_ratio || cropBox.aspectRatio || recommendedAspectRatio(context))
    };

    crop = protectRoi(crop, context);
    if (crop.w > 0.965 && crop.h > 0.965) return null;
    return crop;
  }

  function parseAspectRatio(aspectRatio) {
    const match = String(aspectRatio || "").match(/(\d+(?:\.\d+)?)\s*:\s*(\d+(?:\.\d+)?)/);
    if (!match) return null;
    const w = Number(match[1]);
    const h = Number(match[2]);
    if (!Number.isFinite(w) || !Number.isFinite(h) || w <= 0 || h <= 0) return null;
    return w / h;
  }

  function fitCropToAspect(crop, imageWidth, imageHeight, context) {
    const targetAspect = parseAspectRatio(crop.aspectRatio);
    if (!targetAspect || !imageWidth || !imageHeight) return crop;

    let { x, y, w, h } = crop;
    const currentAspect = (w * imageWidth) / (h * imageHeight);
    const anchor = String(context?.crop_intent?.subject_anchor || context?.roi_anchor || "").toLowerCase();
    if (Math.abs(currentAspect - targetAspect) < 0.01) return crop;

    if (currentAspect > targetAspect) {
      const newW = Math.max(0.12, Math.min(w, targetAspect * h * imageHeight / imageWidth));
      const center = x + w / 2;
      if (anchor.includes("right")) x = Math.max(0, Math.min(1 - newW, x + w - newW));
      else if (anchor.includes("left")) x = Math.max(0, Math.min(1 - newW, x));
      else x = Math.max(0, Math.min(1 - newW, center - newW / 2));
      w = newW;
    } else {
      const newH = Math.max(0.12, Math.min(h, (w * imageWidth) / (targetAspect * imageHeight)));
      const center = y + h / 2;
      if (anchor.includes("upper")) y = Math.max(0, Math.min(1 - newH, y));
      else y = Math.max(0, Math.min(1 - newH, center - newH / 2));
      h = newH;
    }

    return protectRoi({ ...crop, x, y, w, h }, context);
  }

  function clampRawCropBox(cropBox, context) {
    const x = Math.max(0, Math.min(0.95, Number(cropBox?.x) || 0));
    const y = Math.max(0, Math.min(0.95, Number(cropBox?.y) || 0));
    const w = Math.max(0.02, Math.min(1 - x, Number(cropBox?.w) || 1));
    const h = Math.max(0.02, Math.min(1 - y, Number(cropBox?.h) || 1));
    return { enabled: true, x, y, w, h, aspect_ratio: cleanAspectRatio(cropBox?.aspect_ratio || recommendedAspectRatio(context)) };
  }

  function cropCandidateKey(cropBox) {
    if (!cropBox?.enabled) return "original-frame";
    return [
      cropBox.aspect_ratio || "",
      Number(cropBox.x).toFixed(2),
      Number(cropBox.y).toFixed(2),
      Number(cropBox.w).toFixed(2),
      Number(cropBox.h).toFixed(2)
    ].join(":");
  }

  function addCropCandidate(candidates, seen, candidate) {
    const key = cropCandidateKey(candidate.cropBox);
    if (seen.has(key)) return;
    seen.add(key);
    candidates.push(candidate);
  }

  function cropValidationRisk(analysis) {
    const normalized = normalizeCropBox(analysis?.crop_box, analysis);
    if (!normalized) return { shouldValidate: false, level: "none", reasons: [] };

    const reasons = [];
    const area = normalized.w * normalized.h;
    const roi = normalizeRoiBox(analysis?.roi_box);
    const intent = analysis?.crop_intent || {};

    if (analysis?.crop_risk?.needs_validation === true) reasons.push("analysis-risk");
    if (area < 0.72) reasons.push("large-crop");
    if (normalized.x > 0.08 || normalized.y > 0.08 || normalized.w < 0.86 || normalized.h < 0.86) reasons.push("meaningful-reframe");
    if (intent.trim_left || intent.trim_right || intent.trim_top || intent.trim_bottom) reasons.push("edge-trim");

    protectedBoxes(analysis).forEach((box) => {
      const nearEdge = box.x < 0.08 || box.y < 0.08 || box.x + box.w > 0.92 || box.y + box.h > 0.92;
      if (nearEdge) reasons.push(`${box.kind}-near-edge`);
    });

    return {
      shouldValidate: reasons.length > 0,
      level: reasons.length >= 2 ? "high" : reasons.length === 1 ? "medium" : "low",
      reasons
    };
  }

  function buildCropCandidates(analysis, options = {}) {
    const candidates = [];
    const seen = new Set();
    const base = analysis?.crop_box?.enabled === true ? clampRawCropBox(analysis.crop_box, analysis) : null;

    (Array.isArray(analysis?.crop_candidates) ? analysis.crop_candidates : []).forEach((candidate, index) => {
      const sourceCrop = candidate?.crop_box || candidate?.cropBox;
      if (!sourceCrop) return;
      const cropBox = sourceCrop?.enabled === false
        ? { enabled: false, x: 0, y: 0, w: 1, h: 1, aspect_ratio: "" }
        : clampRawCropBox(sourceCrop, analysis);
      addCropCandidate(candidates, seen, {
        id: candidate?.id || `ai-candidate-${index + 1}`,
        label: candidate?.label || tr("AI 候選 {v1}", { v1: index + 1 }),
        cropBox,
        compositionIntent: candidate?.composition_intent || candidate?.compositionIntent || "",
        tradeoffs: candidate?.tradeoffs || ""
      });
    });

    if (base) {
      addCropCandidate(candidates, seen, {
        id: "ai-plan",
        label: tr("AI 初始裁切建議"),
        cropBox: base,
        compositionIntent: analysis?.composition_intent || "",
        tradeoffs: analysis?.crop_recommendation || ""
      });
    }

    addCropCandidate(candidates, seen, {
      id: "original-frame",
      label: tr("保留原始構圖"),
      cropBox: { enabled: false, x: 0, y: 0, w: 1, h: 1, aspect_ratio: "" },
      compositionIntent: tr("保留完整原圖與原始場景脈絡"),
      tradeoffs: tr("不做構圖裁切，只進行美化與色彩整理")
    });

    const limit = options.highRisk === true ? 6 : 4;
    if (candidates.length <= limit) return candidates;

    const original = candidates.find((candidate) => candidate.id === "original-frame");
    if (!original) return candidates.slice(0, limit);

    const selected = candidates
      .filter((candidate) => candidate.id !== "original-frame")
      .slice(0, Math.max(0, limit - 1));
    selected.push(original);
    return selected;
  }

  async function createCroppedFile(file, cropBox, context) {
    const normalized = normalizeCropBox(cropBox, context);
    if (!normalized) return file;

    const image = await loadImageFromFile(file);
    const fitted = fitCropToAspect(normalized, image.naturalWidth, image.naturalHeight, context);
    const sx = Math.round(fitted.x * image.naturalWidth);
    const sy = Math.round(fitted.y * image.naturalHeight);
    const sw = Math.round(fitted.w * image.naturalWidth);
    const sh = Math.round(fitted.h * image.naturalHeight);

    const canvas = document.createElement("canvas");
    canvas.width = sw;
    canvas.height = sh;
    const ctx = canvas.getContext("2d");
    ctx.drawImage(image, sx, sy, sw, sh, 0, 0, sw, sh);

    const blob = await new Promise((resolve) => canvas.toBlob(resolve, "image/png", 0.95));
    if (!blob) return file;
    return new File([blob], "photo-agent-cropped.png", { type: "image/png" });
  }

  function resolveCropBoxForDisplay(cropBox, context, imageWidth, imageHeight) {
    const normalized = normalizeCropBox(cropBox, context);
    if (!normalized) return null;
    return fitCropToAspect(normalized, imageWidth, imageHeight, context);
  }

  function resolveCropBoxWithReport(cropBox, context, imageWidth, imageHeight) {
    const raw = cropBox?.enabled === true ? clampRawCropBox(cropBox, context) : null;
    const normalized = normalizeCropBox(cropBox, context);
    if (!normalized) {
      return {
        cropBox: null,
        adjustmentReasons: raw ? ["local geometry disabled crop because normalized crop was invalid or effectively full-frame"] : []
      };
    }

    const fitted = fitCropToAspect(normalized, imageWidth, imageHeight, context);
    const adjustmentReasons = [
      ...cropDeltaReasons(raw, normalized, "local geometry clamped bounds or expanded crop to protect ROI/protected regions"),
      ...cropDeltaReasons(normalized, fitted, "local geometry adjusted crop to satisfy aspect ratio while protecting safety regions")
    ];

    return {
      cropBox: fitted,
      adjustmentReasons: [...new Set(adjustmentReasons)]
    };
  }

  async function createLocalEnhancedDataUrl(file, cropBox, context) {
    const imageFile = await createCroppedFile(file, cropBox, context);
    const image = await loadImageFromFile(imageFile);
    const canvas = document.createElement("canvas");
    canvas.width = image.naturalWidth;
    canvas.height = image.naturalHeight;
    const ctx = canvas.getContext("2d");

    ctx.filter = "brightness(1.04) contrast(1.08) saturate(1.06)";
    ctx.drawImage(image, 0, 0);
    ctx.filter = "none";

    const imageData = ctx.getImageData(0, 0, canvas.width, canvas.height);
    const data = imageData.data;
    for (let i = 0; i < data.length; i += 4) {
      data[i] = Math.min(255, data[i] * 1.012 + 2);
      data[i + 1] = Math.min(255, data[i + 1] * 1.006 + 1);
      data[i + 2] = Math.max(0, data[i + 2] * 0.992);
    }
    ctx.putImageData(imageData, 0, 0);

    return canvas.toDataURL("image/png", 0.95);
  }

  return {
    fileToDataUrl,
    loadImageFromFile,
    createPreviewFile,
    normalizeCropBox,
    resolveCropBoxForDisplay,
    resolveCropBoxWithReport,
    cropValidationRisk,
    buildCropCandidates,
    createCroppedFile,
    createLocalEnhancedDataUrl
  };
})();
