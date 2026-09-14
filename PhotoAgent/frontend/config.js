window.PhotoAgentConfig = {
  // Internal backend feature switch.  /api/config is authoritative after
  // startup; this default avoids a visible UI flash before that response.
  enableBodyReshape: true,
  // Manual amount control for already-approved Body Reshape endpoints.  The
  // backend /api/config response replaces this fallback at startup.
  bodyReshapeSlider: { default: 12, min: 0, max: 30, step: 1 },
  // Base URL of the PhotoAgent Flask backend. Empty string = auto-detect from
  // the page's own directory (works both unprefixed locally and behind the
  // /mt-testbed-PhotoAgent reverse-proxy prefix in prod). Set explicitly only
  // to override.
  apiBaseUrl: "",
  // Mirrors config.py's MODELS["analysis"] -- fallback label for the result
  // page's "輸出結果" card when a job predates extra-info.analysis_model.
  analysisModel: "gpt-5.6-luna",
  // Mirrors config.py's ANALYSIS_MODEL_LABELS -- the "輸出結果" card's
  // actually-ran label (keyed off extra-info.analysis_model) reads from here.
  analysisModels: {
    "gpt-4.1-mini": "GPT 4.1 Mini",
    "gpt-5.6-luna": "GPT 5.6 Luna",
    "gpt-5.6-sol": "GPT 5.6 Sol"
  },
  // Single source of truth for the 分析模型 selector (upload page +
  // analyze-preview), same array-of-options shape as imageModelQualityOptions
  // above and aspectRatioOptions below.
  analysisModelOptions: [
    { value: "gpt-4.1-mini", label: "GPT 4.1 Mini" },
    { value: "gpt-5.6-luna", label: "GPT 5.6 Luna" },
    { value: "gpt-5.6-sol", label: "GPT 5.6 Sol" }
  ],
  defaultAnalysisModel: "gpt-5.6-luna",
  // Mirrors photo_agent/param_validation.py's DEFAULT_EDITOR_PROMPT_STRATEGY --
  // the Editor prompt 架構 picker is debug-only, so this is what every normal
  // run sends and what debug mode starts on.
  defaultEditorPromptStrategy: "resolved_compact",
  imageModels: {
    "gpt-image-2": "GPT Image 2",
    "gpt-image-2.5-sunburst": "GPT Image 2.5 Sunburst",
    "gpt-image-2.5-flare": "GPT Image 2.5 Flare",
    "nano-banana-2": "Nano Banana 2",
    "MAI-Image-2.6": "MAI-Image-2.6"
  },
  // Single source of truth for the merged 修圖模型/影像品質 selector (upload
  // page + analyze-preview) -- one option per valid model+quality
  // combination, instead of two independent selects the user could
  // previously pair up in invalid ways (and whose option lists had to be
  // hand-duplicated across both screens). Nano Banana 2 (Gemini) and
  // MAI-Image-2.6 have no PhotoAgent quality tier, so each gets one option
  // with quality fixed internally rather than a user-facing quality choice.
  // "Standard"/"Enhanced" (not "Low"/"Medium") describe what the two tiers
  // actually trade off -- faster/lighter vs. more detail -- without reading
  // as "worse" vs. "better" the way the raw API quality names do; `quality`
  // itself stays "low"/"medium" since that's the literal API param value.
  imageModelQualityOptions: [
    { value: "gpt-image-2:low", model: "gpt-image-2", quality: "low", label: "GPT Image 2 · Standard", displayLabel: "Image 2 · Standard" },
    { value: "gpt-image-2:medium", model: "gpt-image-2", quality: "medium", label: "GPT Image 2 · Enhanced", displayLabel: "Image 2 · Enhanced" },
    { value: "gpt-image-2.5-sunburst:low", model: "gpt-image-2.5-sunburst", quality: "low", label: "GPT Image 2.5 Sunburst · Standard", displayLabel: "Image 2.5 Sunburst·Standard" },
    { value: "gpt-image-2.5-sunburst:medium", model: "gpt-image-2.5-sunburst", quality: "medium", label: "GPT Image 2.5 Sunburst · Enhanced", displayLabel: "Image 2.5 Sunburst·Enhanced" },
    { value: "gpt-image-2.5-flare:low", model: "gpt-image-2.5-flare", quality: "low", label: "GPT Image 2.5 Flare · Standard", displayLabel: "Image 2.5 Flare·Standard" },
    { value: "gpt-image-2.5-flare:medium", model: "gpt-image-2.5-flare", quality: "medium", label: "GPT Image 2.5 Flare · Enhanced", displayLabel: "Image 2.5 Flare·Enhanced" },
    { value: "nano-banana-2", model: "nano-banana-2", quality: "medium", label: "Nano Banana 2" },
    { value: "MAI-Image-2.6", model: "MAI-Image-2.6", quality: "medium", label: "MAI-Image-2.6" }
  ],
  defaultImageModelQuality: "gpt-image-2.5-sunburst:medium",
  // User-facing aspect-ratio choices (upload page + analyze-preview, above
  // 生成張數) -- "auto" takes the output ratio from the framing the analysis
  // proposed (measured off its crop rectangle on the backend, not picked by
  // the analysis model); "original" keeps the source photo's own proportions;
  // a W:H value forces that frame regardless of what the crop measures. All
  // are applied at edit time, so switching between them never re-runs the
  // analysis (see resolve_output_aspect_ratio). The W:H values must stay
  // within param_validation.py's VALID_ASPECT_RATIOS or the job is rejected.
  // Grouped 方 → 橫 (widening) → 直 (heightening) rather than in the backend's
  // own list order, so the picker reads as a shape choice.
  aspectRatioOptions: [
    { value: "auto", label: "Auto" },
    { value: "original", label: "維持原圖比例" },
    { value: "1:1", label: "1:1（方形）" },
    { value: "5:4", label: "5:4（橫）" },
    { value: "4:3", label: "4:3（橫）" },
    { value: "3:2", label: "3:2（橫）" },
    { value: "16:9", label: "16:9（橫）" },
    { value: "4:5", label: "4:5（直）" },
    { value: "3:4", label: "3:4（直）" },
    { value: "2:3", label: "2:3（直）" },
    { value: "9:16", label: "9:16（直）" }
  ],
  defaultAspectRatio: "auto",
  presets: {
    natural: {
      label: "自然高級修圖",
      intent: "保留原照內容，提升光線、色彩、構圖與整體質感。",
      tags: ["自然", "乾淨", "高質感", "保留真實"]
    },
    editorial: {
      label: "雜誌 editorial",
      intent: "讓照片更有視覺張力與雜誌感，但不改變主體與場景。",
      tags: ["雜誌感", "層次", "氛圍", "克制"]
    },
    product: {
      label: "商業產品照",
      intent: "強化產品形狀、材質、色準與背景秩序。",
      tags: ["商品", "材質", "清楚", "商業"]
    },
    portrait: {
      label: "人像精修",
      intent: "保留身分、表情、膚色與姿態，改善光線與皮膚質感。",
      tags: ["人像", "膚色", "自然", "身分保留"]
    },
    custom: {
      label: "自訂方向",
      intent: "依照補充指令調整，同時保留原始照片的真實內容。",
      tags: ["自訂", "保留原照", "精修", "可控"]
    }
  }
};
