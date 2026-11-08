window.PhotoAgentConfig = {
  // Internal backend feature switch.  /api/config is authoritative after
  // startup; this default avoids a visible UI flash before that response.
  enableBodyReshape: true,
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
    { value: "gpt-5.6-luna", label: "GPT 5.6 Luna" },
  ],
  defaultAnalysisModel: "gpt-5.6-luna",
  // Mirrors photo_agent/param_validation.py's ADVANCED_OPTION_DEFAULTS, for the
  // subset 人像設定 actually shows as toggles (calm_hair/smile_level are picked
  // per person in the review panel, not here). What a fresh upload starts from,
  // what resetSettingsToDefaults restores on both pages' copies of the fold,
  // and what a case falls back to when its own values were never recorded.
  defaultAdvancedOptions: {
    eye_closed_detection: true,
    gaze_contact_detection: true,
    hair_mess_detection: true,
    smile_adjustment: true
  },
  imageModels: {
    "gpt-image-2": "GPT Image 2",
    "gpt-image-2.5-sunburst": "GPT Image 2.5 Sunburst",
    "gpt-image-2.5-flare": "GPT Image 2.5 Flare",
    "nano-banana-2": "Nano Banana 2",
    "MAI-Image-2.6": "MAI-Image-2.6"
  },
  // Single source of truth for the merged 修圖模型/影像品質 selector (upload
  // page + analyze-preview). All three Sunburst tiers are approved for the
  // release picker; Nano Banana 2 and MAI-Image-2.6 are debug-only
  // candidates -- app.js's DEBUG_ONLY_IMAGE_MODELS hides them outside debug
  // mode, so they stay listed here purely for debug-mode testing. Nano
  // Banana 2 and MAI-Image-2.6 have no PhotoAgent quality tier, so each
  // uses one option with quality fixed internally.
  // "Standard"/"Enhanced"/"Premium" (not "Low"/"Medium"/"High") describe
  // what the tiers actually trade off -- faster/lighter vs. more detail --
  // without reading as "worse" vs. "better" the way the raw API quality
  // names do; `quality` itself stays "low"/"medium"/"high" since that's the
  // literal API param value. Sunburst's own displayLabel (the selected-value
  // badge, not the dropdown row) uses "Efficient"/"Enhanced"/"Premium"
  // instead, since it's the release-facing set and reads better standalone
  // without a "Standard" that sounds like a fallback/default.
  imageModelQualityOptions: [
    { value: "gpt-image-2.5-sunburst:low", model: "gpt-image-2.5-sunburst", quality: "low", label: "GPT Image 2.5 Sunburst · Standard", displayLabel: "Sunburst · Standard" },
    { value: "gpt-image-2.5-sunburst:medium", model: "gpt-image-2.5-sunburst", quality: "medium", label: "GPT Image 2.5 Sunburst · Enhanced", displayLabel: "Sunburst · Enhanced" },
    { value: "gpt-image-2.5-sunburst:high", model: "gpt-image-2.5-sunburst", quality: "high", label: "GPT Image 2.5 Sunburst · Premium", displayLabel: "Sunburst · Premium" },
    { value: "nano-banana-2", model: "nano-banana-2", quality: "medium", label: "Nano Banana 2", displayLabel: "Nano Banana 2", scalable: false },
    { value: "MAI-Image-2.6", model: "MAI-Image-2.6", quality: "medium", label: "MAI-Image-2.6", displayLabel: "MAI-Image-2.6", scalable: false }
  ],
  defaultImageModelQuality: "gpt-image-2.5-sunburst:medium",
  // 輸出尺寸 tiers (backend capabilities.OUTPUT_SCALES), named by long edge.
  // Only engines that take an arbitrary size honour it -- the ones marked
  // `scalable: false` above render at their own fixed size, and the picker
  // is disabled for them. The per-ratio pixel sizes shown next to each tier
  // come from /api/capabilities at startup rather than being copied here.
  outputScaleOptions: [
    { value: "1K", label: "1K" },
    { value: "2K", label: "2K" }
  ],
  defaultOutputScale: "2K",
  // User-facing aspect-ratio choices (upload page + analyze-preview, above
  // 生成張數) -- "auto" takes the output ratio from the framing the analysis
  // proposed (measured off its crop rectangle on the backend, not picked by
  // the analysis model); "original" keeps the source photo's own proportions;
  // a W:H value forces that frame regardless of what the crop measures. All
  // are applied at edit time, so switching between them never re-runs the
  // analysis (see resolve_output_aspect_ratio). The W:H values must stay
  // within param_validation.py's VALID_ASPECT_RATIOS or the job is rejected.
  // The W:H values run tallest → widest, so the picker's shape row reads as
  // one continuous scale with 1:1 in the middle.
  aspectRatioOptions: [
    { value: "auto", label: tr("AI 建議"), description: tr("依照片內容自動決定") },
    { value: "original", label: tr("原圖比例"), description: tr("不改變照片長寬") },
    { value: "9:16", label: "9:16", hint: tr("限動") },
    { value: "2:3", label: "2:3", hint: tr("人像") },
    { value: "3:4", label: "3:4", hint: tr("手機") },
    { value: "4:5", label: "4:5", hint: tr("IG 貼文") },
    { value: "1:1", label: "1:1", hint: tr("方形") },
    { value: "5:4", label: "5:4", hint: tr("橫幅") },
    { value: "4:3", label: "4:3", hint: tr("平板") },
    { value: "3:2", label: "3:2", hint: tr("相機") },
    { value: "16:9", label: "16:9", hint: tr("桌布") }
  ],
  defaultAspectRatio: "auto",
  presets: {
    natural: {
      label: tr("自然高級美化"),
      intent: tr("保留原照內容，提升光線、色彩、構圖與整體質感。"),
      tags: [tr("自然"), tr("乾淨"), tr("高質感"), tr("保留真實")]
    },
    editorial: {
      label: tr("雜誌 editorial"),
      intent: tr("讓照片更有視覺張力與雜誌感，但不改變主體與場景。"),
      tags: [tr("雜誌感"), tr("層次"), tr("氛圍"), tr("克制")]
    },
    product: {
      label: tr("商業產品照"),
      intent: tr("強化產品形狀、材質、色準與背景秩序。"),
      tags: [tr("商品"), tr("材質"), tr("清楚"), tr("商業")]
    },
    portrait: {
      label: tr("人像精修"),
      intent: tr("保留身分、表情、膚色與姿態，改善光線與皮膚質感。"),
      tags: [tr("人像"), tr("膚色"), tr("自然"), tr("身分保留")]
    },
    custom: {
      label: tr("自訂方向"),
      intent: tr("依照補充指令調整，同時保留原始照片的真實內容。"),
      tags: [tr("自訂"), tr("保留原照"), tr("精修"), tr("可控")]
    }
  }
};
