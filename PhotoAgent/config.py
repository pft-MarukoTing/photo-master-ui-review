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
            "Take this exact photograph and re-angle it into a clearly high-angle shot looking down on the subject, as if the camera were raised up and tilted down toward it, revealing more of the top-facing surfaces and upper contours. "
            "Make the high angle distinct and obvious — it should clearly read as looking down, not just a tiny downward nudge. "
            "Crucially, this is a high angle shot from the SAME distance as the original, not a distant overhead: the camera is raised and tilted down while keeping the exact same shooting distance. Only the downward viewing angle changes — the distance does not change in either direction, neither farther nor closer. "
            "The subject spans the same fraction of the frame and stays the same size and crop as in the original, as if only the camera's tilt changed. "
            "Keep the surrounding scene in place: the background stays in the same position and on the same side of the frame as it already is, just viewed from the higher angle, while the subject remains in roughly the same part of the frame. "
            "Keep shapes, proportions, and perspective believable, and avoid warped geometry or invented background content. "
            "Do not zoom or move the camera closer or farther to achieve the high angle — keep the subject at exactly the same size as in the original."
        ),
        "low": (
            "Take this exact photograph and tilt the camera's vertical angle upward, pivoting in place, so it reads as looking up at the subject from a lower camera, making them feel more prominent and elevated. "
            "The framing scale is unchanged: the subject spans the same fraction of the frame and sits at the same size and crop as in the original, as if only the camera's tilt changed. "
            "Keep the surrounding scene in place: the background stays in the same position and on the same side of the frame as it already is, just seen from below, while the subject remains in roughly the same part of the frame. "
            "Keep proportions and structural geometry believable, and avoid warped architecture or unrealistic enlargement of foreground elements. "
            "Do not zoom out or pull the camera back."
        ),
        "eye": (
            "Take this exact photograph and adjust the camera's vertical angle toward the subject's eye level, pivoting in place, so the perspective feels balanced, direct, and neutral rather than dramatically high or low. "
            "The framing scale is unchanged: the subject spans the same fraction of the frame and sits at the same size and crop as in the original, as if only the camera's tilt changed. "
            "Keep the surrounding scene in place: the background stays in the same position and on the same side of the frame as it already is, while the subject remains in roughly the same part of the frame. "
            "Maintain believable proportions, depth, and spatial layout. "
            "Do not zoom out or pull the camera back."
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
            "Reframe this as the same photograph captured from further back -- a moderate camera zoom-out or slightly wider lens. "
            "Take the entire original image, scale it down to about 80% of its current size while keeping all of its content, proportions, and detail exactly the same, and place it within a larger frame of the same scene; then paint the surrounding area with the parts of the same real location that a wider shot would naturally reveal, continuing the existing perspective, depth, lighting, and materials outward on all sides including the foreground. "
            "Keep the same place, moment, subject, and scene identity. "
            "The result must be one continuous photograph, not a smaller photo with borders, blank margins, or an invented different location around it."
        ),
        "wider": (
            "Reframe this as the same photograph captured from much further back -- a strong camera zoom-out or much wider lens. "
            "Take the entire original image, scale it down to about 60% of its current size while keeping all of its content, proportions, and detail exactly the same, and place it within a larger frame of the same scene; then paint the surrounding area with the parts of the same real location that a much wider shot would naturally reveal, continuing the existing perspective, depth, lighting, and materials outward on all sides including the foreground. "
            "Keep the same place, moment, subject, and scene identity. "
            "The result must be one continuous photograph, not a smaller photo with borders, blank margins, or an invented different location around it."
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
        "Prioritize believable light, faithful color that keeps the original palette and its warm/cool "
        "character, controlled contrast, and small refinements that a good human retoucher would make. "
        "Cleaner does not mean warmer or more saturated."
    ),
    "editorial": (
        "A refined editorial finish: add taste, depth, and visual confidence while keeping the exact photograph. "
        "Think restrained magazine color work, graceful highlight rolloff, midtone separation earned through "
        "tonal contrast rather than added saturation or warmth, better subject separation, and no gimmicks."
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
