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
            "Transform the subject into a clearly playful pose, not just a slightly nicer version of the original. "
            "The result should feel lively, friendly, lighthearted, and approachable, with a visible change in gesture or body language. "
            "For people or animals, introduce a natural playful cue such as a brighter expression, head tilt, leaning-in posture, relaxed shoulders, "
            "hands brought together, arms opened slightly, or a small energetic weight shift. "
            "For products, objects, food, or scenes, create a more playful presentation through a livelier arrangement, more expressive orientation, "
            "or a lighter visual rhythm while keeping the same main subject and scene. "
            "If the original already feels playful, push it to a different clearly playful moment instead of leaving it unchanged. "
            "Preserve the same identity or object form, same outfit/materials, same location, same props, and believable anatomy/geometry. "
            "Avoid cartoonish gestures, childish exaggeration, or changes that make the subject look like a different person/object."
        ),
        "candid": (
            "Transform the subject into a clearly candid, unposed moment, as if captured naturally between posed shots. "
            "The result should feel spontaneous, relaxed, and documentary-like rather than deliberately arranged. "
            "For people or animals, use a visible natural shift such as an in-between gaze, slight lean, softened shoulders, casual hand placement, "
            "subtle motion, natural smile, or imperfect but charming posture. "
            "For products, objects, food, or scenes, make the composition feel casually observed in real use rather than formally staged, "
            "while preserving the same subject, scene, props, and material truth. "
            "If the original already looks candid, change it to a different natural in-between moment instead of keeping the pose essentially the same. "
            "Preserve recognizability, identity or object form, realistic proportions, and scene continuity. "
            "Avoid making the image messy, blurry, awkward, or accidentally distorted."
        ),
        "strong": (
            "Transform the subject into a clearly strong, confident, and composed pose. "
            "The result should have a more assertive visual presence, not merely a subtle posture cleanup. "
            "Create strength through the overall body language: upright posture, grounded stance, stable balance, cleaner body direction, "
            "stronger shoulder line, controlled head angle, focused gaze, and deliberate use of arms and hands. "
            "Prefer a varied confident pose that suits the input image, such as hands relaxed by the side, hands in pockets, arms loosely crossed, "
            "one arm resting on a nearby surface, a subtle forward lean, a wider stance, a cleaner side-facing stance, or a composed neutral arm position. "
            "For products, objects, architecture, or scenes, strengthen the presentation through a more deliberate orientation, clearer silhouette, "
            "more stable structure, and stronger visual hierarchy while preserving the same subject and scene. "
            "If the original already looks strong, move it into a different strong pose with a visibly changed stance, gesture, torso direction, "
            "head direction, arm position, or weight distribution rather than leaving it unchanged. "
            "Preserve identity or object form, body/object proportions, clothing/materials, location, props, and photographic realism. "
            "Avoid exaggerated fashion posing, aggressive body distortion, unrealistic anatomy, or changing the subject into a different person/object."
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
            "Transform the image with a clearly visible warm gold-tone lighting style, not just a slight white-balance shift. "
            "Create a warmer golden atmosphere with amber highlights, richer warm midtones, gentle contrast, and a sunlit or warm practical-light feeling that suits the original scene. "
            "For outdoor scenes, this can feel like late-afternoon or golden-hour sunlight with softly glowing highlights and warm background bokeh. "
            "For indoor or night scenes, this can feel like warm lamp, candle, restaurant, or practical-light ambience with richer skin/object warmth and deeper surrounding shadows. "
            "Keep the same subject, location, props, and scene structure, and preserve believable material colors and skin tones. "
            "Avoid fake sunset backgrounds, heavy orange casts, overexposed yellow haze, or changing the original location."
        ),
        "soft": (
            "Transform the image with a natural soft-glow lighting style inspired by softly lit street portraits and gentle ambient backlight. "
            "The result should feel luminous, flattering, and cohesive across the whole image, not like a subject pasted onto a glowing outline. "
            "Soften the overall light with smoother shadow transitions, slightly cleaner highlights, gentle catchlights, and a mild ambient glow that also affects the background. "
            "Adjust the background together with the subject: make surrounding lights, foliage, street details, bokeh, or distant highlights feel softly diffused and tonally balanced so the subject and environment share the same lighting atmosphere. "
            "Use subject-background separation through natural depth, soft contrast, background diffusion, and very subtle edge light only where it fits the scene. "
            "The edge separation should be delicate and integrated into the ambient light, never a visible halo ring, cutout glow, or bright outline around the subject. "
            "For people or animals, keep skin, hair, and fur texture natural while adding soft dimensionality around hair and shoulders; for products, food, objects, or scenes, emphasize shape and depth with soft highlights and balanced surrounding ambience. "
            "Make the lighting change noticeable through the whole image's softness, depth, and glow, not by drawing a bright rim around the subject. "
            "Preserve the same subject, location, props, and scene truth. Avoid plastic skin, flat studio lighting, artificial fog, excessive bloom, blown highlights, rebuilt background, or obvious halo/cutout effects."
        ),
    },
    "size": {
        "wide": (
            "Transform the image into a wider framing where the main subject appears clearly but moderately smaller and more of the surrounding environment is visible. "
            "Target subject scale: the subject's visible frame ratio should decrease by about 20% compared with the original image. "
            "In practical terms, the subject's bounding-box height or dominant dimension should become roughly 80% of its original frame-relative size, and its approximate area share may become about 65% of the original. "
            "This should feel like the camera pulled back, zoomed out, or slightly widened the field of view while keeping the same subject, location, perspective logic, and scene identity. "
            "If the original frame does not contain enough surrounding context, extend the canvas by plausibly continuing the same real scene beyond the original edges. "
            "Any newly visible area must look like a natural continuation of the existing background, lighting, depth, materials, and spatial layout, not a blank border or unrelated invented scenery. "
            "Keep the subject clearly readable and preserve important identity, object shape, clothing/materials, props, and role cues. "
            "Avoid tiny crops that do not visibly change subject scale, artificial side bands, stretched backgrounds, duplicated patterns, warped perspective, or unrelated new objects."
        ),
        "wider": (
            "Transform the image into a much wider environmental framing where the main subject becomes substantially smaller and the surrounding scene plays a stronger compositional role. "
            "Target subject scale: the subject's visible frame ratio should decrease by about 40% compared with the original image. "
            "In practical terms, the subject's bounding-box height or dominant dimension should become roughly 60% of its original frame-relative size, and its approximate area share may become about 35% to 40% of the original. "
            "This should read like a same-scene pull-back shot or wider lens view, with more environmental context around the subject on multiple sides when compositionally appropriate. "
            "If needed, extend the canvas and outpaint plausible same-scene background beyond the original frame, continuing existing perspective lines, lighting direction, bokeh, surfaces, architecture, landscape, or interior context. "
            "The expanded scene should support scale, breathing room, story, and environmental atmosphere while preserving the same location and photographic realism. "
            "Keep the subject recognizable and avoid changing identity, product/object form, outfit/materials, props, or the core scene. "
            "Avoid merely applying a tiny zoom-out, adding blank margins, stretching or cloning edges, creating artificial empty bands, or inventing a different location."
        ),
        "close": (
            "Use a closer framing where the subject appears larger and more visually prominent. "
            "Keep important body parts, props, and role cues intact, with natural breathing room around the subject. "
            "Avoid cramped edges, awkward cuts, or excessive background blur."
        ),
        "closer": (
            "Use an extreme close-up that features a single subject. The mandatory crop region already isolates "
            "that one subject; fill the frame with it. Exclude out-of-frame elements by cropping, not by erasing "
            "what remains in frame; keep the subject's own context and remove only genuine distractions. "
            "Preserve the chosen subject's identity, expression, and key details. "
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
