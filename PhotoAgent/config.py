# =============================================================================
# PhotoAgent Configuration
# =============================================================================

EFFECT_NAME = 'photoAgent'

MODELS = {
    "image": "gpt-image-2",
    "analysis": "gpt-4.1-mini",
}

# UI-selectable image-edit models (Step 5). "gpt-image-2"/"gpt-image-1.5" call
# OpenAI's images/edits API (openai_client.edit_photo); "nano-banana-2"/
# "nano-banana-lite" call Gemini's generateContent API (gemini_client.edit_photo)
# with Google's gemini-3.1-flash-image / gemini-3.1-flash-lite-image.
IMAGE_MODEL_LABELS = {
    "gpt-image-2": "GPT Image 2",
    "gpt-image-1.5": "GPT Image 1.5",
    "nano-banana-2": "Nano Banana 2",
    "nano-banana-lite": "Nano Banana 2 Lite",
}

PRESETS = {
    "natural": {
        "label": "自然高級修圖",
        "intent": "保留原照內容，提升光線、色彩、構圖與整體質感。",
        "tags": ["自然", "乾淨", "高質感", "保留真實"],
    },
    "editorial": {
        "label": "雜誌 editorial",
        "intent": "讓照片更有視覺張力與雜誌感，但不改變主體與場景。",
        "tags": ["雜誌感", "層次", "氛圍", "克制"],
    },
    "product": {
        "label": "商業產品照",
        "intent": "強化產品形狀、材質、色準與背景秩序。",
        "tags": ["商品", "材質", "清楚", "商業"],
    },
    "portrait": {
        "label": "人像精修",
        "intent": "保留身分、表情、膚色與姿態，改善光線與皮膚質感。",
        "tags": ["人像", "膚色", "自然", "身分保留"],
    },
    "custom": {
        "label": "自訂方向",
        "intent": "依照補充指令調整，同時保留原始照片的真實內容。",
        "tags": ["自訂", "保留原照", "精修", "可控"],
    },
}

# Traditional-Chinese instruction text for the "optional recipe list" style
# overrides (Pose/Angle/Lighting/Size) on the review screen. Keys must match
# the `value` fields of frontend/results.js's POSE_OPTIONS/ANGLE_OPTIONS/
# LIGHTING_OPTIONS/SIZE_OPTIONS.
STYLE_OPTION_INSTRUCTIONS = {
    "pose": {
        "playful": (
            "Adjust the subject's pose and expression to feel more lively, relaxed, and playful. "
            "Add a natural sense of movement or lighthearted energy while preserving the same person, "
            "identity, body shape, clothing, and scene context. Avoid exaggerated cartoon-like gestures."
        ),
        "candid": (
            "Make the pose feel candid and unposed, as if captured in a natural in-between moment. "
            "Soften overly stiff posture, gaze, hands, or shoulders while keeping the subject recognizable "
            "and the moment believable within the same scene."
        ),
        "strong": (
            "Adjust the subject's pose to feel more confident, composed, and visually strong. "
            "Improve posture, shoulder line, stance, and facial presence while preserving identity, "
            "body proportions, clothing, and the original environment. Avoid overly dramatic fashion-pose changes."
        ),
    },
    "angle": {
        "high": (
            "Transform the photo into a clearly high-angle shot, as if the camera has moved above the main subject "
            "and is looking downward. Reveal more top-facing surfaces, upper contours, or surrounding context when appropriate. "
            "Make the viewpoint change noticeable rather than just slightly correcting perspective. Preserve the same main subject, "
            "same scene, same location, and same props, while keeping shapes, proportions, and perspective believable. "
            "Avoid unnatural distortion, warped geometry, or invented background content."
        ),
        "low": (
            "Transform the photo into a clearly low-angle shot, as if the camera has moved below the subject and is looking upward. "
            "Make the subject feel more prominent, elevated, or visually powerful, with stronger upward perspective cues when the scene allows. "
            "The viewpoint change should be clearly visible, not just a subtle tilt. Preserve the same main subject, same scene, "
            "same location, and same props, while keeping proportions and structural geometry believable. "
            "Avoid extreme distortion, warped architecture, or unrealistic enlargement of foreground elements."
        ),
        "eye": (
            "Transform the photo toward a natural eye-level shot, as if the camera is positioned at the subject's normal viewing height. "
            "Keep the perspective balanced, direct, and realistic, so the result feels more neutral and straightforward rather than dramatically high or low. "
            "Preserve the same main subject, same scene, same location, and same props, while maintaining believable proportions, depth, and spatial layout."
        ),
        "side": (
            "Transform the photo into a clear side-angle or three-quarter side-angle shot, targeting an oblique viewpoint around 45 to 90 degrees from a straight-on front view. "
            "If the original photo is already somewhat side-facing, increase the side-angle readability rather than leaving it unchanged: make the subject's lateral contour, depth, side plane, silhouette, or profile structure more visually dominant. "
            "For people or animals, this may reveal more profile, shoulder line, body depth, or side-facing gaze; for products, food, objects, architecture, or scenes, this may reveal more side surfaces, object depth, edge thickness, receding lines, or side-plane geometry. "
            "The output should clearly read as a side or three-quarter-side photograph, not merely a front view with a tiny rotation. "
            "Preserve the same main subject, same scene, same location, same props, and recognizable subject form. "
            "Avoid turning it into a different subject, inventing a new background, breaking structural geometry, or over-rotating until important front-facing details disappear unless a true profile view is natural for the subject."
        ),
    },
    "lighting": {
        "gold": (
            "Apply warm golden-hour style lighting with a tasteful yellow-gold glow, richer highlights, and warmer atmosphere. "
            "Keep skin tones, whites, product colors, and material colors believable. "
            "Avoid heavy orange casts, fake sunset backgrounds, or changing the original location."
        ),
        "soft": (
            "Apply soft diffused lighting with gentle shadows, smoother highlight transitions, and more even illumination. "
            "Improve facial, product, or scene readability while preserving natural texture and the original lighting logic. "
            "Avoid plastic skin, flat lighting, or a studio-like rebuild unless already present in the original photo."
        ),
    },
    "size": {
        "wide": (
            "Use a wider framing where the subject appears slightly smaller and more of the original environment is visible. "
            "Preserve the same location and real scene context. Do not invent new background areas, artificial borders, "
            "or expanded canvas details."
        ),
        "wider": (
            "Use an even wider environmental framing where background and spatial context have a stronger role. "
            "Keep the subject clearly readable and preserve the same location, props, and perspective. "
            "Do not fabricate new scenery, side areas, or empty canvas just to make the frame wider."
        ),
        "close": (
            "Use a closer framing where the subject appears larger and more visually prominent. "
            "Keep important body parts, props, and role cues intact, with natural breathing room around the subject. "
            "Avoid cramped edges, awkward cuts, or excessive background blur."
        ),
        "closer": (
            "Use a much closer close-up style framing focused on the most important subject area. "
            "Preserve identity, expression, key details, and any essential interaction cues. "
            "Avoid cutting through faces, hands, joints, products, or important props in a visually awkward way."
        ),
    },
}

STYLE_OPTION_LABELS = {
    "pose": "Pose",
    "angle": "Angle",
    "lighting": "Lighting",
    "size": "Framing size",
}

# Short per-value display labels, used for the recipe_adherence.style_items
# shown in the UI so it reads "Pose: Playful" instead of the long instruction
# text above. Must match frontend/results.js's POSE_OPTIONS/ANGLE_OPTIONS/
# LIGHTING_OPTIONS/SIZE_OPTIONS labels.
STYLE_OPTION_VALUE_LABELS = {
    "pose": {"playful": "Playful", "candid": "Candid", "strong": "Strong"},
    "angle": {"high": "High Shot", "low": "Low Shot", "eye": "Eye Level", "side": "Side Shot"},
    "lighting": {"gold": "Gold Tone", "soft": "Soft Glow"},
    "size": {"wide": "Wide", "wider": "Wider", "close": "Close", "closer": "Closer"},
}

PRESET_PROMPTS = {
    "natural": (
        "A quiet premium retouch: make the existing photograph feel cleaner, calmer, and more intentional. "
        "Prioritize believable light, neutral but pleasing color, controlled contrast, and small refinements "
        "that a good human retoucher would make."
    ),
    "editorial": (
        "A refined editorial finish: add taste, depth, and visual confidence while keeping the exact photograph. "
        "Think restrained magazine color work, graceful highlight rolloff, richer midtones, better subject "
        "separation, and no gimmicks."
    ),
    "product": (
        "A commercial presentation finish: preserve the exact product or subject, then improve legibility, "
        "material texture, light quality, color truth, and background discipline. The result should feel "
        "photographed well, not invented."
    ),
    "portrait": (
        "A humane portrait retouch: preserve identity, expression, facial geometry, skin tone, hair, clothing, "
        "and body shape. Improve light, complexion, catchlights, texture, and overall presence without "
        "beauty-filter smoothing."
    ),
    "custom": "",
}
