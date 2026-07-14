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
            "Transform the photo into a clearly high-angle shot, as if the camera has moved above the subject "
            "and is looking downward. The subject should visually look up toward the camera when appropriate, "
            "with more top-facing facial, hair, shoulder, tabletop, floor, or surrounding environment cues visible. "
            "Make the viewpoint change noticeable rather than merely correcting perspective, while preserving the same "
            "identity, outfit, location, props, and believable body proportions. Avoid unnatural tiny-body distortion, "
            "warped heads, stretched limbs, or fake newly invented background."
        ),
        "low": (
            "Transform the photo into a clearly low-angle shot, as if the camera has moved below the subject's normal eye line "
            "and is looking upward. The subject should feel taller, more present, and more visually assertive, with stronger "
            "upward perspective cues such as visible ceiling, upper background structures, or upward-leading vertical lines when the scene allows. "
            "Make the viewpoint change visible, not just a small tilt. Preserve the same subject identity, outfit, location, and props. "
            "Avoid extreme wide-angle distortion, enlarged legs or chin, warped architecture, or a superhero-like exaggeration unless the original scene supports it."
        ),
        "eye": (
            "Transform the photo toward a natural eye-level shot, as if the camera is held around the subject's face or normal viewing height. "
            "Keep the vertical perspective balanced and direct: faces should feel more level with the camera, body proportions should look natural, "
            "and the scene should feel like a straightforward everyday portrait rather than a dramatic high or low shot. "
            "This can re-center the viewer-subject relationship, but must preserve the same identity, expression style, location, props, and realistic spatial layout."
        ),
        "side": (
            "Transform the photo into a clear side or three-quarter side shot. Rotate the camera viewpoint and subject orientation enough that the body, "
            "face contour, shoulder line, and silhouette read more from the side, similar to a portrait taken from an oblique angle rather than straight-on. "
            "The side angle should reveal more profile, body depth, and dimensional contour while keeping the subject recognizable and still located in the same scene. "
            "Avoid turning the person into a different pose, changing clothing, flipping identity features, or inventing a new background."
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
