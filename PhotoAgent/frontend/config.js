window.PhotoAgentConfig = {
  // Base URL of the PhotoAgent Flask backend. Empty string = auto-detect from
  // the page's own directory (works both unprefixed locally and behind the
  // /mt-testbed-PhotoAgent reverse-proxy prefix in prod). Set explicitly only
  // to override.
  apiBaseUrl: "",
  // Mirrors config.py's MODELS["analysis"] / IMAGE_MODEL_LABELS -- shown on
  // the result page's "輸出結果" card to report which models actually ran.
  analysisModel: "gpt-4.1-mini",
  imageModels: {
    "gpt-image-2": "GPT Image 2",
    "nano-banana-2": "Nano Banana 2"
  },
  // Single source of truth for the merged 修圖模型/影像品質 selector (upload
  // page + analyze-preview) -- one option per valid model+quality
  // combination, instead of two independent selects the user could
  // previously pair up in invalid ways (and whose option lists had to be
  // hand-duplicated across both screens). Nano Banana 2 (Gemini) has no
  // quality parameter at all, so it gets a single option with quality fixed
  // to a sensible default rather than a user-facing quality choice.
  // "Standard"/"Enhanced" (not "Low"/"Medium") describe what the two tiers
  // actually trade off -- faster/lighter vs. more detail -- without reading
  // as "worse" vs. "better" the way the raw API quality names do; `quality`
  // itself stays "low"/"medium" since that's the literal API param value.
  imageModelQualityOptions: [
    { value: "gpt-image-2:low", model: "gpt-image-2", quality: "low", label: "GPT Image 2 · Standard" },
    { value: "gpt-image-2:medium", model: "gpt-image-2", quality: "medium", label: "GPT Image 2 · Enhanced" },
    { value: "nano-banana-2", model: "nano-banana-2", quality: "medium", label: "Nano Banana 2" }
  ],
  defaultImageModelQuality: "gpt-image-2:low",
  // Two user-facing aspect-ratio choices (upload page + analyze-preview,
  // above 生成張數) -- "auto" leaves recommended_aspect_ratio entirely up to
  // the analysis step (today's default behavior); "original" tells the
  // analysis model (at analyze time) and the edit-model API's own
  // size/aspectRatio parameter to keep the source photo's own proportions.
  aspectRatioOptions: [
    { value: "auto", label: "Auto" },
    { value: "original", label: "維持原圖比例" }
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
