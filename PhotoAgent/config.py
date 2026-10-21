# =============================================================================
# PhotoAgent Configuration
# =============================================================================

EFFECT_NAME = 'photoAgent'

# Internal feature switch for the optional holistic Body Reshape analysis pass.
# Keep this enabled by default so existing deployments retain their current
# behaviour; set it to False to disable body-reshape analysis, editing, and
# the read-only preview.  The Flask /api/config endpoint exposes the value to
# the frontend without making it a user-editable setting.
ENABLE_BODY_RESHAPE = False

# Enables Chat previews and ensures Stage 2.0 precedes every analyze_stage call.
# Ready snapshots are reused; disabling retains the legacy Chat/analyze paths.
ENABLE_CHAT_SUBJECT_PREVIEW = False
# Enable the Preview Conversation flow (introduction and request matching).
# Off keeps Stage 2.0/avatars, and uses chat_review plus the legacy geometry
# handoff for requests; no chat_preview_conversation call is made by this flow.
ENABLE_CHAT_PREVIEW_INTRODUCTION = False
# Chat only: require confirmation of subject-relative versus image-relative body sides.
# Off preserves left/right wording as an ordinary edit; person selection still applies.
ENABLE_CHAT_BODY_SIDE_CLARIFICATION = False
# Continue dialogue only after chat_ask contract validation and repair fail.
# Off preserves the existing terminal failure/retry behavior.
ENABLE_CHAT_PATCH_CONTINUE = True
# Optional extra visual pass for Chat requests. Enable during rollout to
# clarify absent/uncertain edit targets; no extra call when disabled or empty.
ENABLE_CHAT_REQUEST_FEASIBILITY = False
# 「僅依個人化需求」(overwrite_prompt) edits also get a light whole-photo retouch
# from the picked preset after the request. Off sends the request (and styles) alone.
ENABLE_OVERWRITE_BASELINE_RETOUCH = False
# Call 0 (photo_agent/photo_router.py) before analysis: picks a branch skill by category; off = Call A for every photo, writing photo_summary/scene_* itself.
ENABLE_PHOTO_ROUTER = True
SUBJECT_PREVIEW_TIMEOUT_SECONDS = 8.0
SUBJECT_PREVIEW_MAX_DETECTIONS = 24
SUBJECT_PREVIEW_MAX_ADDITIONAL_PEOPLE = 8
SUBJECT_PREVIEW_MAX_OUTPUT_TOKENS = 4000
SUBJECT_PREVIEW_REASONING_EFFORT = "none"
ENABLE_PREVIEW_LOW_CONFIDENCE_CANDIDATES = True
SUBJECT_PREVIEW_LOW_CONFIDENCE_THRESHOLD = 0.5
SUBJECT_PREVIEW_MAX_LOW_CONFIDENCE_CANDIDATES = 8
SUBJECT_PREVIEW_MAX_LOW_CONFIDENCE_ZOOMS = 2
SUBJECT_PREVIEW_AB_MAX_OUTPUT_TOKENS = 6000

MODELS = {
    "image": "gpt-image-2",
    "analysis": "gpt-5.6-luna",
    # Step 5b (pipeline._run_eye_repair_pass) -- which model runs the
    # second edit pass when it repairs gaze only (a blink repair uses
    # pipeline.BLINK_REPAIR_IMAGE_MODEL), independent of "image" above.
    "gaze_repair": "nano-banana-2",
}

# The selectable image / analysis models (labels, providers, reasoning effort)
# live in the feature registry, photo_agent/capabilities.py.

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
            "Prefer a varied confident pose that suits the input image, such as hands relaxed by the side, arms folded squarely across the chest, "
            "one hand on the hip, one arm resting on a nearby surface, a subtle forward lean, a wider grounded stance, a cleaner side-facing stance, or a composed neutral arm position. "
            "For products, objects, architecture, or scenes, strengthen the presentation through a more deliberate orientation, clearer silhouette, "
            "more stable structure, and stronger visual hierarchy while preserving the same subject and scene. "
            "If the original already looks strong, move it into a different strong pose with a visibly changed stance, gesture, torso direction, "
            "head direction, arm position, or weight distribution rather than leaving it unchanged. "
            "Preserve identity or object form, body/object proportions, clothing/materials, location, props, and photographic realism. "
            "Avoid exaggerated fashion posing, aggressive body distortion, unrealistic anatomy, or changing the subject into a different person/object."
        ),
        "elegant": (
            "Transform the subject into a clearly elegant, poised pose, not just a slightly tidier version of the original. "
            "The result should read as refined and graceful, with a visible change in how the body is carried. "
            "Build the elegance from line rather than effort: a lengthened neck, shoulders lowered and drawn open, a slight chin tuck, "
            "a gentle curve through the torso, weight settled onto one leg with the other foot lightly placed, and hands placed deliberately -- "
            "resting at the waist, grazing a collar or sleeve, holding the opposite wrist, or falling in a soft unclenched line at the side. "
            "Keep the expression composed and quiet rather than broadly smiling. "
            "If the main subject is not a person, express the same restraint through a more deliberate orientation, cleaner silhouette, and calmer arrangement. "
            "If the original already looks elegant, move it into a different elegant pose with a visibly changed weight distribution, arm line, torso rotation, "
            "or head angle rather than leaving it unchanged. "
            "Preserve identity, body proportions, clothing/materials, location, props, and photographic realism. "
            "Avoid runway contortion, stiff formality, an over-arched back, unnaturally lengthened neck or limbs, or changing the subject into a different person."
        ),
        "dynamic": (
            "Transform the subject into a clearly dynamic pose caught mid-motion, not a static pose with a hint of movement. "
            "The result should read as one frozen frame out of a moving moment, the body committed to an action rather than holding still. "
            "Use a visible motion cue that suits the scene: a mid-stride step with the weight arriving on the front foot, a torso turning toward or away from the camera, "
            "hair and clothing carried by the movement, an arm swinging through, a hand pushing hair back, or a look thrown back over the shoulder. "
            "Keep the motion within one believable step or turn -- do not invent a jump, spin, or sprint unless the original scene already supports it. "
            "If the main subject is not a person, convey the motion through a more active orientation or arrangement rather than by animating the object. "
            "If the original already shows motion, move it to a different point in that movement instead of leaving it essentially unchanged. "
            "Preserve identity, body proportions, limb count and joint direction, clothing/materials, location, props, and photographic realism. "
            "Render it as a sharp, well-exposed frame: no motion blur, ghosting, streaked or duplicated limbs, distorted hands, floating feet, or lost ground contact."
        ),
        "cool": (
            "Transform the subject into a clearly cool, effortless, self-possessed pose. "
            "The result should read as unbothered rather than eager -- presence without performance -- and its body language should differ visibly from the original. "
            "Build it from a relaxed, slightly asymmetric stance: weight dropped onto one hip, shoulders loose and angled a little off the camera axis, chin level or fractionally lifted, "
            "and hands doing something casual. When the visible clothing, held items, and scene support it, place one or both hands in existing pockets; otherwise hook a "
            "thumb into a waistband or strap, let an arm hang loose, rest a hand low at the hip, or fold the arms low and easy. Let the gaze go level into the "
            "lens or off past the camera, with the expression neutral to faintly amused rather than openly smiling. "
            "If the main subject is not a person, express the same ease through a looser, less centered, less formally staged presentation. "
            "If the original already looks cool, move it into a different cool pose with a visibly changed stance, hand placement, torso direction, or gaze rather than leaving it unchanged. "
            "Preserve identity, body proportions, clothing/materials, location, props, and photographic realism. "
            "Avoid a slouch that collapses the spine, a sulky or aggressive expression, exaggerated street-style posturing, or changing the subject into a different person."
        ),
        "vsign": (
            "Transform the subject into a clearly casual V-sign pose -- the everyday peace-sign gesture, thrown off without ceremony. "
            "The result should read as offhand and unforced rather than staged: the gesture is a throwaway, not a presentation. "
            "Raise one hand into a relaxed V near the face -- beside the cheek, at the temple, or level with the shoulder -- with a loose wrist and the elbow "
            "bent naturally rather than held out stiffly, and let the rest of the body stay easy: weight on one leg, a small head tilt toward the raised hand, "
            "and the free hand hanging loose, in a pocket, or resting at the hip. A lightly kicked-back foot, a small step, or a half-turn of the torso may be "
            "added when the scene supports it. Let the expression brighten to match the gesture. "
            "Render the hand with exactly two extended fingers, index and middle, parted, with the remaining fingers folded down and the thumb tucked across them; "
            "keep five fingers in total, correct joint direction, and a hand size normal relative to the face. "
            "Never let the raised hand cover an eye, an eyebrow, or the mouth. "
            "If the main subject is not a person, this gesture does not apply -- leave the subject's arrangement unchanged rather than inventing an equivalent. "
            "If the original already shows a V-sign, change which hand is raised, its height, or the surrounding body language rather than leaving it unchanged. "
            "Preserve identity, body proportions, clothing/materials, location, props, and photographic realism. "
            "Avoid a stiff arm shot straight at the camera, a mangled or extra-fingered hand, a palm pressed flat against the cheek, a cutesy childish rendition, "
            "or changing the subject into a different person."
        ),
        "cheer": (
            "Transform the subject into a clearly joyful celebratory pose, lifting each person's available arms. "
            "The result should read as one unguarded happy moment, with the whole body opening up rather than the arms alone moving. "
            "When both hands are free, lift both arms up and out -- overhead in a wide V, or spread open at shoulder height. When one hand holds an object or supports "
            "another person, retain that interaction and lift only the free arm. When both hands hold or support a child, another person, or an object, retain the "
            "two-handed interaction and express celebration through the open chest, head, stance, and bright expression. Each variation fully satisfies this pose. "
            "Keep the chest open, shoulders back and down, hands relaxed, and the weight light on the feet, optionally with a small rise onto the toes, a step, or a "
            "slight backward tilt of the head. "
            "Let the expression brighten to match the gesture. "
            "Fit the gesture to the existing frame: where there is not enough room above the head, open the arms outward and lower instead of raising them until "
            "the hands leave the frame or crowd its top edge. Do not extend the canvas, zoom out, or paint new scene to make room for them. "
            "Build the gesture with each person's existing limbs and finish with exactly two arms and two hands per person, each hand connected to its owner's arm. "
            "Keep the two arms matched in length and believable at the shoulder joint, with five fingers on each hand and correct joint direction. "
            "If the main subject is not a person, this gesture does not apply -- leave the subject's arrangement unchanged rather than inventing an equivalent. "
            "If the original already shows open arms, move it to a different open-armed moment with a visibly changed arm height, spread, or stance rather than "
            "leaving it unchanged. "
            "Preserve identity, body proportions, clothing/materials, location, props, and photographic realism. "
            "Avoid dislocated or overlong arms, hands cut off at the wrist, a strained or forced grin, an invented mid-air jump the scene does not support, "
            "or changing the subject into a different person."
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
            "For indoor or night scenes, this can feel like warm lamp, candle, restaurant, or practical-light ambience with deeper surrounding shadows. "
            "The warmth belongs to the light rather than to the surfaces: it arrives as a warm source falling across the scene, so each surface shows it the way that surface would -- strongest where the light lands, unchanged where it does not reach. Skin takes this light like any other surface, warmed by what falls on it and never tinted on its own, so a face under warm light still reads as that person's own skin. "
            "Keep the same subject, location, props, and scene structure, and preserve believable material colors. "
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
        "cool": (
            "Transform the photo with a clearly visible cool blue-hour atmosphere, as daylight fades after sunset while the sky remains luminous. "
            "Let soft blue ambient light from the scene shape subjects and background together, with cooler midtones and gently deeper shadows. "
            "Keep faces in that ambient light, with exposure and shadow direction consistent with nearby surfaces; skin shares the cool illumination while retaining its complexion, and existing lamps keep their own color. "
            "Preserve the same scene, material identity, and photographic realism."
        ),
        "night": (
            "Transform the whole photo into a clearly recognizable night scene with dark blue ambient light and deeper, natural shadows. "
            "Let existing light sources create gentle highlights on the subject and surroundings so both share the same nighttime atmosphere. "
            "Keep the subject readable, skin complexion recognizable, and the original location and materials believable."
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
        # new 7
        "closer": (
            "Use an extreme close-up that features a single subject. Pick that one subject out of the photograph "
            "and fill the frame with it. Exclude out-of-frame elements by cropping, not by erasing "
            "what remains in frame; keep the subject's own context and remove only genuine distractions. "
            "Preserve the chosen subject's identity, expression, and key details. "
            "Avoid cutting through faces, hands, joints, products, or important props in a visually awkward way."
        ),
        # "closer": (
        #     "Use an extreme close-up that features a single subject. The mandatory crop region already isolates "
        #     "that one subject; fill the frame with it and do not reintroduce other people, objects, or background "
        #     "that fall outside that region -- cropping them out entirely is expected and correct. "
        #     "Preserve the chosen subject's identity, expression, and key details. "
        #     "Avoid cutting through faces, hands, joints, products, or important props in a visually awkward way."
        # ), # new 6
        # "closer": (
        #     "Use an extreme close-up framing: pick a single subject to feature -- the one that is largest, most in focus, "
        #     "or most central in the frame -- and crop tightly onto just their important detail, tighter than the mandatory crop. "
        #     "This overrides the general instruction to preserve every recognizable person, object, "
        #     "or protected region elsewhere in the frame: cropping other people or elements out entirely is expected and correct. "
        #     "Preserve identity, expression, key details, and any essential interaction cues of the chosen subject. "
        #     "Avoid cutting through faces, hands, joints, products, or important props in a visually awkward way."
        # ),
        # "closer": (
        #     "Use an extreme close-up framing: pick a single subject to feature -- the one that is largest, most "
        #     "in focus, or most central in the frame -- and crop tightly onto just their face and shoulders (or the "
        #     "single most important detail for a non-person subject), tighter than the mandatory crop. This "
        #     "overrides the general instruction to preserve every recognizable person, object, or protected region "
        #     "elsewhere in the frame: cropping other people or elements out entirely is expected and correct. "
        #     "Preserve identity, expression, key details, and any essential interaction cues of the chosen subject. "
        #     "Avoid cutting through faces, hands, joints, products, or important props in a visually awkward way."
        # ),
        # "closer": (
        #     "Use an extreme close-up framing, focusing on a single most prominent subject in a tight crop on "
        #     "their face and shoulders (or the single most important detail for a non-person subject), tighter "
        #     "than the mandatory crop above and without revealing more background than it already shows. "
        #     "Preserve identity, expression, key details, and any essential interaction cues. "
        #     "Avoid cutting through faces, hands, joints, products, or important props in a visually awkward way."
        # ),
        # "closer": (
        #     "Use a much closer close-up style framing focused on the most important subject area. "
        #     "Preserve identity, expression, key details, and any essential interaction cues. "
        #     "Avoid cutting through faces, hands, joints, products, or important props in a visually awkward way."
        # ),
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
    "pose": {
        "playful": "Playful", "candid": "Candid", "strong": "Strong",
        "elegant": "Elegant", "dynamic": "Dynamic", "cool": "Cool",
        "vsign": "V-Sign", "cheer": "Cheer",
    },
    "angle": {"high": "High Shot", "low": "Low Shot", "eye": "Eye Level", "side": "Side Shot"},
    "lighting": {"gold": "Gold Tone", "soft": "Soft Glow", "cool": "Blue Hour", "night": "Night"},
    "size": {"wide": "Wide", "wider": "Wider", "close": "Close", "closer": "Closer"},
}

PRESET_PROMPTS = {
    "natural": (
        "A premium photographic retouch: create an appealing, cohesive scene palette with well-shaped light, "
        "tonal depth, and natural material texture. Preserve scene structure. "
        "Grade skin independently, preserving each person's complexion, undertone, and chroma."
    ),
    "editorial": (
        "A refined editorial finish: add taste, depth, and visual confidence while keeping the exact photograph. "
        "Use cohesive scene color, graceful highlight rolloff, midtone depth, and clear subject separation. "
        "Preserve scene structure and material identity; protect skin color independently."
    ),
    "product": (
        "A commercial presentation finish: preserve the exact product or subject, then improve legibility, "
        "material texture, light quality, color truth, and background discipline. The result should feel "
        "photographed well, not invented."
    ),
    "portrait": (
        "A humane portrait retouch: preserve identity, expression, facial geometry, skin tone, hair, clothing, "
        "and body shape. Improve light, catchlights, texture, and the evenness and clarity of the skin "
        "without beauty-filter smoothing. Presence comes from the light on the face rather than from "
        "added color."
    ),
    "custom": "",
}
