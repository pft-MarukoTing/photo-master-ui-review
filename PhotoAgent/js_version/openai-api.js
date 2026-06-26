window.PhotoAgentApi = (() => {
  const PROMPTS = window.PhotoAgentPrompts;
  const IMAGE_MODEL = PROMPTS.models.image;
  const ANALYSIS_MODEL = PROMPTS.models.analysis;
  const VISION_PREVIEW_MAX_SIDE = 1024;
  const {
    fileToDataUrl,
    loadImageFromFile,
    createPreviewFile,
    createCroppedFile,
    normalizeCropBox,
    buildCropCandidates,
    resolveCropBoxWithReport
  } = window.PhotoAgentImageTools;

  function extractOutputText(responseJson) {
    if (responseJson.output_text) return responseJson.output_text;
    const chunks = [];
    for (const item of responseJson.output || []) {
      for (const content of item.content || []) {
        if ((content.type === "output_text" || content.type === "text") && content.text) {
          chunks.push(content.text);
        }
      }
    }
    return chunks.join("\n").trim();
  }

  function parseJsonFromText(text) {
    try {
      return JSON.parse(text);
    } catch {
      const match = String(text || "").match(/\{[\s\S]*\}/);
      if (!match) throw new Error("AI 回應沒有包含有效 JSON。");
      return JSON.parse(match[0]);
    }
  }

  async function postJson(url, apiKey, body, failureLabel) {
    const response = await fetch(url, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${apiKey}`,
        "Content-Type": "application/json"
      },
      body: JSON.stringify(body)
    });

    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(data.error?.message || `${failureLabel}: HTTP ${response.status}`);
    }
    return data;
  }

  async function analyzePhoto({ file, apiKey }) {
    const previewFile = await createPreviewFile(file, VISION_PREVIEW_MAX_SIDE);
    const imageDataUrl = await fileToDataUrl(previewFile);
    const data = await postJson(
      "https://api.openai.com/v1/responses",
      apiKey,
      {
        model: ANALYSIS_MODEL,
        input: [
          {
            role: "user",
            content: [
              { type: "input_text", text: PROMPTS.analysisInstruction },
              { type: "input_image", image_url: imageDataUrl }
            ]
          }
        ]
      },
      "OpenAI analysis failed"
    );

    return parseJsonFromText(extractOutputText(data));
  }

  async function reviewUserInstruction({ userDirection, analysis, apiKey }) {
    const trimmed = String(userDirection || "").trim();
    if (!trimmed) {
      return {
        has_user_direction: false,
        user_intent_summary: "",
        normalized_user_direction: "",
        explicit_priorities: [],
        explicit_prohibitions: [],
        unspecified_agent_freedom: ["可依照片分析保守改善光線、色彩、清晰度、構圖與干擾。"],
        crop_or_framing_requested: false,
        crop_guidance: "使用者未指定裁切，Agent 可依分析保守判斷。",
        qa_focus: [],
        conflicts: [],
        warning: ""
      };
    }

    const data = await postJson(
      "https://api.openai.com/v1/responses",
      apiKey,
      {
        model: ANALYSIS_MODEL,
        input: [
          {
            role: "user",
            content: [
              { type: "input_text", text: PROMPTS.buildUserInstructionReviewPrompt({ userDirection: trimmed, analysis }) }
            ]
          }
        ]
      },
      "OpenAI user instruction review failed"
    );

    return {
      has_user_direction: true,
      ...parseJsonFromText(extractOutputText(data))
    };
  }

  async function validateCrop({ file, analysis, apiKey, risk, revisionFeedback, forceValidation, previousOutputImageDataUrl }) {
    const previewFile = await createPreviewFile(file, VISION_PREVIEW_MAX_SIDE);
    const previewImage = await loadImageFromFile(previewFile);
    const originalDataUrl = await fileToDataUrl(previewFile);
    const candidates = buildCropCandidates(analysis, { highRisk: risk?.level === "high" });
    if (!revisionFeedback && !forceValidation && !candidates.some((candidate) => candidate.cropBox?.enabled === true)) return analysis;
    const candidatePayloads = [];

    for (const candidate of candidates) {
      const imageFile = candidate.cropBox.enabled
        ? await createCroppedFile(previewFile, candidate.cropBox, analysis)
        : previewFile;
      candidatePayloads.push({
        ...candidate,
        localGeometry: candidate.cropBox.enabled
          ? resolveCropBoxWithReport(candidate.cropBox, analysis, previewImage.naturalWidth, previewImage.naturalHeight)
          : { cropBox: null, adjustmentReasons: [] },
        imageDataUrl: await fileToDataUrl(imageFile)
      });
    }

    if (!candidatePayloads.length) return analysis;

    const content = [
      {
        type: "input_text",
        text: PROMPTS.buildCropValidationPrompt({
          analysis,
          candidates: candidatePayloads,
          revisionFeedback,
          previousOutputAvailable: Boolean(previousOutputImageDataUrl)
        })
      },
      { type: "input_text", text: "Original photo" },
      { type: "input_image", image_url: originalDataUrl }
    ];

    if (previousOutputImageDataUrl) {
      content.push({
        type: "input_text",
        text: "Previous failed output from the last run. Use this only as negative reference for understanding the QA feedback; do not treat it as the source image."
      });
      content.push({ type: "input_image", image_url: previousOutputImageDataUrl });
    }

    for (const candidate of candidatePayloads) {
      const adjusted = candidate.localGeometry?.cropBox
        ? `Adjusted crop: ${JSON.stringify(candidate.localGeometry.cropBox)}`
        : "Adjusted crop: original frame";
      const reasons = candidate.localGeometry?.adjustmentReasons?.length
        ? `Local geometry adjustments: ${candidate.localGeometry.adjustmentReasons.join("; ")}`
        : "Local geometry adjustments: none";
      content.push({ type: "input_text", text: `Crop candidate ${candidate.id}: ${candidate.label}. ${adjusted}. ${reasons}` });
      content.push({ type: "input_image", image_url: candidate.imageDataUrl });
    }

    const data = await postJson(
      "https://api.openai.com/v1/responses",
      apiKey,
      {
        model: ANALYSIS_MODEL,
        input: [
          {
            role: "user",
            content
          }
        ]
      },
      "OpenAI crop validation failed"
    );

    const validation = parseJsonFromText(extractOutputText(data));
    const revised = validation?.revised_crop_box;
    const selectedCandidate = candidates.find((candidate) => candidate.id === validation?.selected_candidate_id);
    const shouldUseRevised = validation?.pass === false && revised && typeof revised === "object";
    const selectedCropBox = shouldUseRevised
      ? revised
      : selectedCandidate?.cropBox || analysis.crop_box;
    const selectedLabel = shouldUseRevised
      ? "AI 修正裁切"
      : selectedCandidate?.label || "原始裁切建議";

    return {
      ...analysis,
      crop_validation: {
        ...validation,
        selected_candidate_label: selectedLabel,
        risk_level: risk?.level || "unknown",
        risk_reasons: risk?.reasons || []
      },
      crop_candidates: candidates.map(({ id, label, cropBox, compositionIntent, tradeoffs }) => ({
        id,
        label,
        cropBox,
        compositionIntent,
        tradeoffs
      })),
      crop_box: selectedCropBox,
      crop_recommendation: shouldUseRevised && revised.reason
        ? `${analysis.crop_recommendation || ""} 裁切評選修正：${revised.reason}`.trim()
        : analysis.crop_recommendation
    };
  }

  function buildEditPrompt({ analysis, presetKey, preset, userDirection, revisionFeedback }) {
    return PROMPTS.buildEditPrompt({
      analysis,
      preset,
      presetPrompt: PROMPTS.presetPrompts[presetKey] || PROMPTS.presetPrompts.natural,
      userDirection,
      cropApplied: Boolean(normalizeCropBox(analysis.crop_box, analysis)),
      revisionFeedback
    });
  }

  async function editPhoto({ file, analysis, apiKey, quality, presetKey, preset, userDirection, revisionFeedback }) {
    const imageForEdit = await createCroppedFile(file, analysis.crop_box, analysis);
    const form = new FormData();
    form.append("model", IMAGE_MODEL);
    form.append("image", imageForEdit, imageForEdit.name || "photo.png");
    form.append("prompt", buildEditPrompt({ analysis, presetKey, preset, userDirection, revisionFeedback }));
    form.append("quality", quality);
    form.append("size", "auto");

    const response = await fetch("https://api.openai.com/v1/images/edits", {
      method: "POST",
      headers: { Authorization: `Bearer ${apiKey}` },
      body: form
    });

    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(data.error?.message || `OpenAI image edit failed: HTTP ${response.status}`);
    }

    const b64 = data.data?.[0]?.b64_json;
    if (!b64) throw new Error("OpenAI 回應沒有包含圖片資料。");

    return {
      image: `data:image/png;base64,${b64}`,
      model: `${ANALYSIS_MODEL} + ${IMAGE_MODEL}`,
      usage: data.usage || data.data?.[0]?.usage || null
    };
  }

  async function qualityCheckPhoto({ file, outputImageDataUrl, analysis, apiKey }) {
    const previewFile = await createPreviewFile(file, VISION_PREVIEW_MAX_SIDE);
    const originalDataUrl = await fileToDataUrl(previewFile);
    const data = await postJson(
      "https://api.openai.com/v1/responses",
      apiKey,
      {
        model: ANALYSIS_MODEL,
        input: [
          {
            role: "user",
            content: [
              { type: "input_text", text: PROMPTS.buildQualityCheckPrompt({ analysis }) },
              { type: "input_text", text: "Original photo" },
              { type: "input_image", image_url: originalDataUrl },
              { type: "input_text", text: "Edited output" },
              { type: "input_image", image_url: outputImageDataUrl }
            ]
          }
        ]
      },
      "OpenAI quality check failed"
    );

    return parseJsonFromText(extractOutputText(data));
  }

  return { analyzePhoto, reviewUserInstruction, validateCrop, editPhoto, qualityCheckPhoto };
})();
