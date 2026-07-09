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
    "gpt-image-1.5": "GPT Image 1.5",
    "nano-banana-2": "Nano Banana 2"
  },
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
