"""Call B (portrait QC) and Call C (eye repair planning): instructions and response schemas."""

import json

from prompts import (
    _BOX_SCHEMA,
    _schema_boolean,
    _schema_enum,
    _schema_integer,
    _schema_number,
    _schema_object,
    _schema_string,
    _schema_string_array,
)


# Spliced into build_portrait_qc_instruction's Call B prompt.
_HAIR_ANALYSIS_SECTION = """## Hair analysis

### Role
- Observe each listed person's physical hair state from the full photo and matched face crop.
- Classify the state and propose its candidate plan; do not decide whether the edit should run or anticipate the user's hair level.
- Always output should_fix=false as a placeholder. Python overwrites it after this response.

### Output fields
- hair_state: choose exactly one from tidy / styled_messy / styled_bangs / styled_face_framing / motion / natural_texture / disheveled / windblown / strands_across_face / bangs_disturbed / bangs_clumped / lock_out_of_place / frizz_halo / tuft_or_flattened / occluded_or_unclear / uncertain.
- visible_evidence: name the exact strands, fringe, or region and the visible geometry that supports the state. Do not write aesthetic preference or guessed intent.
- front_hair: none / even / clumped / unclear. It is a reading, not a state: never copy its value into hair_state.
- fix_action: propose one physical operation from none / lay_back / remove / settle / redistribute. This is a candidate action, not an edit approval.
- mess_intent: always output ambiguous as a compatibility placeholder. Do not infer intent or use it as an edit verdict.
- hair_mess_confidence: confidence from 0 to 1 in the physical hair_state classification, not confidence that an edit should happen.
- correction_plan: write a candidate editor instruction for a physically actionable state; otherwise write "不適用". Writing a plan does not set should_fix.
- reason: briefly explain the physical classification and evidence. Do not argue whether the user should keep or remove it.

### Inspection order
1. Readability: if hair structure cannot be resolved because of blur, backlight, occlusion, or subject size, use occluded_or_unclear.
2. Source of movement: use motion when the person's jump, spin, flip, throw, or turn drives the hair; use windblown when environmental wind substantially lifts, opens, scatters, or pushes hair away from its settled silhouette. A coherent wind direction confirms windblown and does not make it non-actionable. Hair does not need to cover a facial feature.
3. Front-hair check: first decide whether a real fringe exists. A fringe is a shorter front layer rooted along the front hairline and visibly distinct in length or grouping from the main lengths; it may be blunt, curtain/八字, see-through, or side-swept. A side part or full-length front section that continues into the shoulder-length hair is not a fringe merely because it crosses the forehead, brow, eye, or cheek; record front_hair="none". Never use position on the face alone as evidence—use the strand's root band, endpoint, length relative to the main hair, and continuity into the longer lengths. For every confirmed fringe, inspect its left, center, and right zones at the roots and through the lengths before choosing even versus clumped. Compare adjacent bundle widths and the forehead gaps beside them; do not summarize the fringe as "light", "see-through", "roughly balanced", or "naturally separated" until this comparison is complete. If any zone contains a conspicuously thicker stuck bundle beside a much thinner bundle or an abnormally wide bare gap, use front_hair="clumped" and bangs_clumped. One such local contradiction is sufficient even when the remaining zones are balanced, the lock follows the parting direction, or oil sheen is not readable. Before choosing styled_face_framing, apply the same test to any shorter front layer: use front_hair="clumped" and bangs_clumped even when that lock follows the parting direction; a coarse separated lock with a source gap/hard edge is clumped fringe, not intentional face framing.
4. Structure: distinguish deliberate repeated styling or natural texture from disheveled hair with several unrelated disruptions, and from a single local contradiction in the surrounding hair flow.
5. Select the single clearest state. If moving or windblown hair also contains a more specific local defect such as strands_across_face or lock_out_of_place, use that local defect as hair_state and mention the movement context in visible_evidence and correction_plan.

### State and candidate-action routing
- tidy: settled hair; fix_action=none; correction_plan="不適用".
- styled_messy: deliberate tousle, cut, or product separation; fix_action=none; correction_plan="不適用".
- styled_bangs: center-parted, curtain/八字, or see-through fringe only after the left/center/right comparison finds no local thick-cord-beside-abnormal-gap contradiction; fix_action=none; correction_plan="不適用". Repetition alone does not prove styling: several separated bundles can all be clumps. A fringe is not even merely because its strands point the same way, its overall coverage looks balanced, or forehead is intentionally visible through it.
- styled_face_framing: deliberate paired temple/cheek strands integrated with an updo, ponytail, or clearly repeated face-framing cut; fix_action=none; correction_plan="不適用". Do not use this state for one isolated front lock on otherwise worn-down hair, especially when it is stuck together, has separated from the surrounding fringe/front layer, leaves a visible source gap, or falls across a brow or eye. Following the general parting direction alone does not prove intentional face framing.
- natural_texture: coherent curly, coily, fluffy, or baby-hair texture; fix_action=none; correction_plan="不適用".
- motion: body-driven flowing hair without a more specific local defect; fix_action=none; correction_plan="不適用". This state does not prevent the user's later fully-groomed transformation.
- disheveled: several visible regions have unrelated uneven directions, tangles, displaced sections, or collapsed volume that break the readable haircut, without environmental wind or body motion explaining them; fix_action=settle; describe the affected regions and use coherent hair on the same head as the target reference. Do not use this state for a consistent tousled style or natural texture.
- windblown: environmental wind has displaced a region or the whole hairstyle; fix_action=settle; provide a candidate correction_plan describing the whole disturbed region and the same head's readable haircut/flow as reference.
- strands_across_face: hair has left its surrounding flow and rests across an eye, brow, nose, mouth, lip, or cheek; use lay_back when adjacent hair can receive it, otherwise remove. Hair that belongs to an existing fringe is not this state.
- bangs_disturbed: a real fringe is flipped or pushed against its own direction; fix_action=lay_back.
- bangs_clumped: a confirmed existing fringe either forms conspicuously unequal thick bundles beside abnormal bare forehead gaps, or contains one visibly stuck-together coarse front lock separated from the otherwise coherent fringe and leaving a gap or hard edge at its source; fix_action=redistribute. This is primarily a distribution-geometry decision, not an oil-detection decision. One strong coarse pattern in any left/center/right zone is sufficient. Oil/sweat, forehead adhesion, darker or slicker grouping, and a hard continuous reflection support this reading but are not required. A clumped lock may still point in the same direction as the parting; directional agreement does not make it styled. Only front_hair="clumped" on a confirmed existing fringe may use this state and action.
- lock_out_of_place: one lock has escaped a tie, clip, hat, or surrounding flow; fix_action=lay_back.
- frizz_halo: a localized static halo contradicts an otherwise settled outline; fix_action=settle.
- tuft_or_flattened: one local tuft stands up or one area is visibly pressed against its neighbours; fix_action=settle.
- uncertain: visible hair whose state cannot be selected reliably; fix_action=none; correction_plan="不適用".

### Action meanings
- lay_back: return only the named strands to adjacent existing hair flow. A non-fringe correction must return them to the source side part or long-hair flow; never create a fringe or close forehead gaps.
- remove: use only for isolated hair that has left the hairstyle. Never remove an existing fringe, hairline, or hairstyle edge. Strands resting further down on a cheek, jaw, or lip may use remove when no adjacent flow can receive them.
- settle: describe the complete disturbed area and use undisturbed hair on the same head to define length, parting, volume, direction, and silhouette. Treat the whole disturbed area as the work, not only strands touching the face.
- redistribute: use only for a confirmed clumped fringe. Reduce its oily hard reflection to the surrounding hair's sheen and spread the same existing strands into a balanced but translucent fringe, closing abnormal gaps without adding hair, density, length, or width.

### Face and hairstyle safety
- Hair over an eye, nose, or mouth may still receive a candidate plan when the feature remains visibly recoverable around the strands. The plan must preserve that person's visible feature shape and position and repair only the strand-width footprint.
- If hair completely buries a feature and no source evidence remains around it, use occluded_or_unclear rather than inventing the feature.
- Preserve hairstyle shape, parting, length, volume, density, hairline, curl pattern, color, and real strand texture except for the candidate operation itself.
- Never 淡化, 降低對比, 柔化, or 模糊 hair. Stray hair is never a distraction item. The only tonal hair operation is reducing localized oily fringe sheen while restoring strand detail.

### Output invariants
- Hair recall check: revisit every confirmed fringe after drafting the result. Explicitly compare its left, center, and right bundle widths and adjacent forehead gaps. If the evidence names unequal/coarse/stuck bundles, a hard source edge, or an abnormal bare gap, the final pair must be front_hair="clumped" plus hair_state="bangs_clumped"; never leave the same evidence under front_hair="even" or styled_bangs. Do not require visible oil, multiple defective zones, or whole-fringe disorder.
- Do not derive or discuss should_fix. Return should_fix=false for every person; Python is the only authority that changes it.
- Return mess_intent="ambiguous"; it is retained only for response compatibility.
- fix_action and correction_plan describe the candidate implied by hair_state even while should_fix remains false.
- Never add hair work to top-level correction_plan, correction_axes, correction_inverse, or retouch_recipe.
"""


_HAIR_CORRECTION_HANDOFF_SECTION = """## Per-person candidate hair correction plan
- Keep the plan inside that person's hair_issue.correction_plan.
- For a state routed to a non-none candidate action above, name the exact visible target, the operation, and the visible final state. The instruction must stand on its own for the editor.
- Write a renderable endpoint, not vague intent such as 修飾, 整理, 調整, 重組, 精修, 順服, 自然分布, or 均勻連續.
- For lay_back/remove, limit work to the named strands and their thin footprints; keep surrounding hair and visible facial geometry source-matched.
- For settle, name the disturbed region and the unaffected hair on the same head that defines its target flow and silhouette.
- For redistribute, include both operations: 去油，恢復霧面髮絲細節；梳散粗細不一的束，讓相同髮絲形成通透且較均衡的瀏海並覆蓋異常空隙。Keep fringe length, outer edge, parting, hairline, and density unchanged.
- For none, write "不適用".
"""


_PORTRAIT_QC_FACE_CROP_EVIDENCE_SECTION = """## Face-crop evidence for portrait QC
Intent: Use the supplied full photo for photographic context and each zoomed face crop for local facial evidence, without repeating subject discovery or changing the subject list finalized by Call A.

- The people to review are authoritative. Do not create, remove, reorder, merge, split, or relabel people.
- Each listed person has an immutable subject_index from Call A. Echo that exact subject_index in the corresponding output item.
- Also echo local_detection_index exactly as listed. It identifies the matching Face crop N when its value is greater than or equal to 1; -1 means no detector-backed crop is available.
- Match a Face crop N only to the listed person whose local_detection_index equals N. Do not associate crops by visual similarity, list position, prominence, or guesswork.
- Use the matched face crop as primary evidence for the physical reading of a face: physical_eye_state, blink/closure, pupil/iris detail, smile_intensity and the mouth-corner, lip, and cheek structure behind it, and strand-level hair detail -- the fringe's root band, strand thickness and spacing, forehead showing between clumps, oily sheen, and strands resting on a feature.
- The crop is one person enlarged, never the evidence for which person this is: the listed box and label identify them in the full photo, and the crop stops at the head. So the hairstyle as a whole is read from the full photo -- whether the front strands continue into the lengths (a side sweep or long front hair) or stop as a shorter band (a fringe), whether the disorder is even across the whole head (style) or a single local contradiction (accident), whether wind shows elsewhere in the frame on clothing, foliage, or other people's hair, and which part of the same head is still undisturbed enough to define what a settle returns to.
- For gaze, use the crop and full photo together: the crop resolves pupil/iris placement, while both views establish head yaw/pitch, facial geometry, and the current camera viewpoint. Never treat the crop center as the lens position.
- Use the full photo for what that reading means and for everything outside the face: expression intent, gesture, body posture, group-photo readiness, visible gaze targets, sleep/activity context, and the relationship between people.
- A crop is the same person and same shutter moment as the full photo, not a separate image.
- If local_detection_index=-1 or the matching crop is unavailable, judge from the full photo and mark a field unclear only when the relevant visual evidence is genuinely unreadable.
- Return exactly one result for every listed subject_index. Never output an issue result for a person not listed here.
"""


_BLINK_DETECTION_SECTION = """## Blink / eyelid physical-observation pass
Intent: maximize recall of readable half-closed, fully closed, and one-eye-closed states. Report physical evidence first. Deterministic Python, not you, decides blink_candidate, intent labels, confidence, and should_fix.

Use the matched face crop as primary eyelid evidence and the full photo only for expression and activity context. Inspect every listed person independently, including secondary people.

Physical fields:
- blink_observability: both_eyes when both eyelids can be inspected; one_eye only when one eyelid is actually hidden, cropped out, or too degraded to inspect; unreadable only when neither eyelid state is reliable. A fully_closed eye is still observable as an eyelid even though its iris is not visible. If you can assign concrete left_eye_state and right_eye_state values other than not_visible/unclear, blink_observability must be both_eyes.
- eyelid_detail_quality: readable, borderline, or unreadable based on blur, occlusion, reflection, compression, darkness, and actual pixel detail.
- left_eye_state always means the eye on the photograph-left side (viewer-left, smaller image x); right_eye_state always means the eye on the photograph-right side (viewer-right, larger image x). These are photograph-side fields, never the person's anatomical left/right. Each value is one of: open, narrowed_open, half_closed, fully_closed, not_visible, or unclear.
- half_closed means an eyelid unusually covers the iris or pupil, or the aperture looks droopy, uneven, glassy, or transitional. Do not demote it to narrowed_open because the person is smiling.
- fully_closed means the eyelids meet with no iris, pupil, or sclera visible.
- physical_eye_state summarizes the two per-eye states without interpreting intention.
- When one eye is closed and the other is half-closed or open, describe them as mismatched transitional eyelid states. Natural anatomical variation applies to stable eye contour and crease structure, not to this captured difference in eyelid openness.
- eye_closure_confidence is confidence from 0 to 1 that at least one eye is physically half_closed or fully_closed. It is not confidence that the closure was accidental.

Record the following exception evidence separately; never use it to rewrite the physical states:
- mouth_laughter_state: active_high_intensity_laughter only for unmistakable active laughter, not a normal, toothy, broad, posed, speaking, singing, shouting, or surprised mouth.
- periocular_contraction: strong_bilateral only when both cheeks strongly elevate into the lower eyelids and pronounced lower-lid/outer-eye compression is visible. Raised cheeks alone are none_or_mild.
- closure_expression_dependence: integral_to_expression only when opening the eyes would visibly contradict the same captured expression; otherwise independent_or_transitional or unclear.
- explicit_closure_context: choose a named value only when clear visible evidence supports it. A V-sign, generic hand gesture, selfie, child subject, party, stage, playful pose, ordinary smile, symmetric closure, or plausible story is none, not evidence of intention. deliberate_wink additionally requires the other eye clearly open plus a wink-specific cue such as one-sided cheek or brow action, a knowing smirk, or camera-directed play; one eye closed while the other is narrowed, half-closed, or looking away is mismatched eyelid timing, so it is none. downward_at_visible_target requires a specific visible target in this same frame.
- intent_evidence_confidence measures confidence in that explicit exception evidence, not confidence in eyelid closure.
- intent_cues must name only visible facts, and each cue must add evidence beyond the eyelid states themselves -- restating that one eye is closed, that the other is open, or that the two differ is not a cue. Do not write guesses such as possibly intentional, natural for the moment, cute, candid, or likely posing.
- overall_demeanor reads this person's whole visible body: posture, muscle tension, and engagement with the camera or the moment, independent of the eyelid state itself and independent of explicit_closure_context. This is a separate axis from whether a specific named story applies -- most photos have no specific story either way, and this field is what still tells them apart. alert_or_camera_ready: posture upright/engaged, muscles active, everything else in the frame (hands, head angle, other people) reads as poised and mid-moment -- a closed eye here specifically conflicts with the rest of the person's own readiness. calm_or_at_ease: posture relaxed, unhurried, or settled -- shoulders down, no tension, nothing else about this person suggests they were caught mid-action -- so closed eyes read as consistent with the rest of their own relaxed state, not as a mismatch with it. unclear: body language cannot be read (too small, occluded, cropped tightly to the face). Judge only this person's own body, not the scene or other people, and only from what the pose/tension actually shows -- not from the eyelid state itself, not from guessing why their eyes are closed.

Strict laughter evidence requires all three observations simultaneously: active_high_intensity_laughter, strong_bilateral periocular_contraction, and integral_to_expression. If any one is weak, obscured, or unclear, the laughter exception is not established.

Output compatibility fields in this observation pass as placeholders only: blink_candidate=false, eye_state="observation_only", severity="none", closure_intent="ambiguous", expression_coherence="unclear", blink_failure_confidence=0.0, and should_fix=false. Python overwrites them after validating the physical evidence. The Traditional-Chinese reason should concisely describe left/right eyelid morphology, readability, laughter muscle evidence, and any explicit closure context. Do not make a repair decision in reason.
"""


_GAZE_DETECTION_SECTION = """## Exact lens-contact detection
Intent: For every listed person, decide only whether the visible eyes point at the exact lens that captured this image. Do not decide whether the gaze is attractive, intentional, narratively appropriate, or worth editing. Do not produce a repair strategy; Python derives should_fix after this visual classification.

### Observation order

Record these physical observations before choosing lens_contact_decision:
1. gaze_observability: both_eyes / one_eye / unreadable.
2. eye_detail_quality: readable / borderline / unreadable.
3. head_pose: frontal / slight_yaw / strong_yaw / profile / rear_or_hidden / unclear.
4. left_eye_direction and right_eye_direction: toward_exact_lens / near_lens_left / near_lens_right / above_lens / below_lens / off_left / off_right / up / down / unclear / not_visible.
5. binocular_alignment: exact_lens_convergent / near_lens_convergent / off_lens_convergent / mismatched / unverifiable.

Use the matched face crop for pupil/iris detail. Use both the crop and full photo for head yaw, pitch, facial geometry, and camera-viewpoint interpretation. The crop's geometric center is not the camera lens.

Head direction is never proof of lens contact, but it is required geometric context for interpreting iris position. A turned face may still look at the lens through compensating eye rotation. A frontal face may still look beside the lens. Do not require centered irises when head yaw makes an offset anatomically consistent with exact lens contact.

### Strict exact-lens gate

- direct_camera / looking_at_lens is a strict positive label. Use it only when exact contact with the current lens is unmistakable.
- near_camera is always not_looking_at_lens.
- Looking at another nearby camera, the photographer's face, or a point slightly above, below, left, right, beside, or past the lens is not_looking_at_lens.
- A frontal, centered, smiling, posed, camera-aware, or engaging face is not evidence of exact lens contact.
- If readable eyes look generally toward the camera but exact lock is not unmistakable, use near_camera / not_looking_at_lens.
- If uncertain between direct_camera and near_camera, use near_camera / not_looking_at_lens.
- Use unclear only when blur, reflection, occlusion, closed eyes, compression, or insufficient detail makes direction physically unreadable. Do not use unclear for readable near-camera eyes.
- One visible eye may support a lens-contact classification, but binocular_alignment must be unverifiable. Repair safety is decided later; do not erase a readable detection merely because only one eye is visible.

### Labels and confidence

gaze_state:
- direct_camera: unmistakable exact-current-lens contact.
- near_camera: close to the current lens but landing beside, above, below, or past it.
- off_camera: clearly looking left or right away from the lens.
- downward: clearly looking down.
- upward_side: clearly looking up and/or sideways.
- unfocused_camera: eye axes visibly fail to converge on one lens point; never infer attention, emotion, or mental engagement.
- cross_focused: the two eyes visibly converge inconsistently.
- ambiguous: readable evidence conflicts, but direction is not physically unreadable.
- unclear: direction cannot be resolved from the pixels.

lens_contact_confidence measures exact positive lens contact:
- 0.80-1.00: unmistakable exact-lens lock; looking_at_lens.
- 0.40-0.79: near-camera or uncertain exact contact; not_looking_at_lens when eyes are readable.
- 0.00-0.39: clearly not looking at the exact lens.

not_looking_at_lens_confidence measures evidence for a lens miss:
- Use at least 0.55 for readable near-camera or off-camera eyes.
- Use 0.75-1.00 when the miss direction is clear.
- Keep both confidence values low only when lens_contact_decision=unclear.
- Do not assign high confidence to both sides. Their sum should normally be approximately 1.0 when the eyes are readable.

gaze_target_in_frame records observation only. Set present=true only for a specific, visible, boxable object or person the eyes clearly target. A phone is the current capture device only when visible mirror geometry or another explicit capture setup proves it; a merely visible phone is not enough. This field does not change lens_contact_decision.

### Hard-negative calibration

Treat each of these as near_camera / not_looking_at_lens, not direct_camera:
- a posed person whose face points at the camera but pupils land just beside the lens;
- a press-photo subject looking toward a neighboring photographer or nearby camera;
- a foreground subject whose gaze feels engaging but the two eye axes meet slightly beside or past the current lens.

Output only the structured gaze-detection fields. reason must briefly name the visible pupil/iris direction, head-pose geometry, and why exact lens lock is present, missed, or unreadable. Do not write should_fix, gaze_correction_reasonableness, or gaze_repair_strategy; those are derived later.
"""


_SMILE_ANALYSIS_SECTION = """## Smile analysis
Intent: Classify each visible face as neutral, gentle, broad, laughing, or unclear for optional edit-stage adjustment without treating an existing smile as a defect.

A smile is never a defect, so do not add it to correction_plan, detected_issues, or retouch_recipe.
Default to neutral. Only use gentle or above when a smile is clearly and unmistakably present.
- neutral: no clear smile; includes speaking, posing, winking, pouting, parted lips, visible teeth, or pleasant/friendly expressions without definite smile structure.
- gentle: clear but restrained smile.
- broad: strong cheerful smile.
- laughing: unmistakable laughter or intense joy.
Judge smile presence before smile intensity.
If the expression could reasonably be caused by speaking, pronunciation, posing, or another facial action, classify it as neutral.
Visible teeth, mouth openness, slight mouth-corner elevation, narrowed eyes, or raised cheeks do not establish a smile by themselves.
Require a coherent smile pattern, especially coordinated mouth-corner retraction/elevation that clearly reads as smiling rather than speaking or posing.
Prefer precision over recall: when uncertain between neutral and gentle, choose neutral.
Use unclear only when the expression cannot be seen reliably. Set adjust_safe=false only when editing could overwrite distinctive dental features or when the face is obscured, too small, or turned too far away to render a reliable expression. adjust_safe measures technical edit feasibility, not whether the captured emotion should be preserved. Speaking, laughter, strong emotion, or another visible facial action does not by itself make smile adjustment unsafe.
"""


_ANALYSIS_RESPONSIBILITIES_PORTRAIT_QC = """- Hair analysis is observation-only: classify hair_state, record evidence, and provide the routed candidate fix_action/correction_plan. Always leave hair_issue.should_fix=false for Python to derive; do not suppress a physical state because it may be intentional or because the selected hair level is unknown.
- Eye-contact detection is separate from blink repair. Analyze whether the readable eyes are locked to the exact current lens, not merely whether the face points forward. Do not derive should_fix and do not write a repair strategy in this pass.
- Gaze detection recall check: before finalizing, revisit every listed person's eyes. If any readable eyes are not unmistakably lens-locked and that subject's gaze_issue does not yet reflect it, fix it.
- Gaze consistency check: direct_camera must pair with looking_at_lens; every other readable gaze state, including near_camera, must pair with not_looking_at_lens; physically unreadable eyes must pair with unclear.
- Blink detection is observation-only in this pass. Report left/right physical eyelid states and the separate exception-evidence fields exactly as seen. Keep blink_candidate=false and should_fix=false placeholders; deterministic Python derives both after this response.
- Blink recall check: revisit every listed person's eyelids and record every readable half_closed, fully_closed, or one-eye-closed state even when the person is smiling, posing, making a gesture, or appears playful.
- Never use an ordinary or broad smile as laughter evidence. active_high_intensity_laughter, strong_bilateral periocular contraction, and integral_to_expression must each be independently visible before recording the full laughter exception evidence.
"""


_PORTRAIT_QC_PERSONA_HEADER = """You are a meticulous portrait quality-control reviewer. A separate photography-direction pass has already analyzed this photo's composition, color, and crop, and has identified the people listed below. Your only job is to inspect each of them, independently, for four physical/expression states: blink/eye closure, gaze direction, hair disruption, and smile intensity. You are not judging composition, color, or crop. For hair, author only the nested per-person candidate hair_issue.correction_plan required by the observed state; Python decides whether that candidate is enabled.

Return only valid JSON. Keep the JSON keys in English. Every user-visible string value (e.g. each issue's "reason") must be Traditional Chinese.

For every listed person, echo subject_index and local_detection_index exactly as provided. These identifiers are authoritative and are used to merge results; do not derive them from output order. Populate blink_issue, hair_issue, gaze_issue, and smile_issue for that same person. Do not output any top-level correction_plan, correction_axes, correction_inverse, or retouch_recipe.
"""


def _person_list_text(persons):
    entries = []
    for i, p in enumerate(persons):
        box = p.get("box") or {}
        subject_index = p.get("subject_index", p.get("_subject_index", i))
        local_detection_index = p.get("local_detection_index", -1)
        entries.append(
            "subject_index={} | local_detection_index={} | {} -- box x={:.3f}, y={:.3f}, w={:.3f}, h={:.3f}".format(
                subject_index, local_detection_index, p.get("label") or "person",
                box.get("x", 0), box.get("y", 0), box.get("w", 0), box.get("h", 0),
            )
        )
    return "\n".join(entries)


def portrait_qc_persons(analysis):
    """Return Call B's person list with stable indices from Call A.

    Callers should pass this result to build_portrait_qc_instruction instead
    of filtering subjects[] themselves, because a filtered-list position is
    not necessarily the subject's index in the original subjects[] array.
    """
    persons = []
    for subject_index, subject in enumerate(analysis.get("subjects") or []):
        if not isinstance(subject, dict) or not subject.get("is_person"):
            continue
        person = dict(subject)
        person["subject_index"] = subject_index
        persons.append(person)
    return persons


def build_portrait_qc_instruction(persons):
    """Call B of the analysis split: given Call A's already-
    finalized, ordered list of is_person=true subjects (label + box), judge
    each one's blink/hair/gaze/smile independently. Build persons with
    portrait_qc_persons(analysis), so every item carries its immutable
    subject_index from Call A. Results are merged by subject_index, never by
    response-array position.
    """
    return (
        _PORTRAIT_QC_PERSONA_HEADER
        + "\nPeople to review, in order:\n"
        + _person_list_text(persons)
        + "\n\n"
        + _PORTRAIT_QC_FACE_CROP_EVIDENCE_SECTION
        + "\n"
        + _BLINK_DETECTION_SECTION
        + _HAIR_ANALYSIS_SECTION
        + _HAIR_CORRECTION_HANDOFF_SECTION
        + "\n"
        + _GAZE_DETECTION_SECTION
        + "\n"
        + _SMILE_ANALYSIS_SECTION
        + "\n## Portrait-QC responsibilities\n"
        + _ANALYSIS_RESPONSIBILITIES_PORTRAIT_QC
    )


def build_eye_repair_planning_instruction(targets):
    """Optional Call C. Only people who passed the deterministic blink
    and/or gaze gate are included, so this prompt plans execution without
    re-deciding whether either fix is approved.
    """
    compact_targets = []
    for target in targets:
        needs_gaze = bool(target.get("needs_gaze_repair"))
        needs_blink = bool(target.get("needs_blink_repair"))
        gaze_issue = target.get("gaze_issue") or {}
        blink_issue = target.get("blink_issue") or {}
        entry = {
            "subject_index": target.get("subject_index"),
            "local_detection_index": target.get("local_detection_index", -1),
            "label": target.get("label") or "person",
            "box": target.get("box") or {},
            "needs_gaze_repair": needs_gaze,
            "needs_blink_repair": needs_blink,
        }
        if needs_gaze:
            entry["gaze"] = {
                "gaze_observability": gaze_issue.get("gaze_observability"),
                "eye_detail_quality": gaze_issue.get("eye_detail_quality"),
                "head_pose": gaze_issue.get("head_pose"),
                "left_eye_direction": gaze_issue.get("left_eye_direction"),
                "right_eye_direction": gaze_issue.get("right_eye_direction"),
                "binocular_alignment": gaze_issue.get("binocular_alignment"),
                "gaze_state": gaze_issue.get("gaze_state"),
                "reason": gaze_issue.get("reason"),
            }
        if needs_blink:
            entry["blink"] = {
                "blink_observability": blink_issue.get("blink_observability"),
                "eyelid_detail_quality": blink_issue.get("eyelid_detail_quality"),
                "photograph_left_eye_state": blink_issue.get("left_eye_state"),
                "photograph_right_eye_state": blink_issue.get("right_eye_state"),
                "physical_eye_state": blink_issue.get("physical_eye_state"),
                "eye_state": blink_issue.get("eye_state"),
                "reason": blink_issue.get("reason"),
            }
        compact_targets.append(entry)
    return """You are planning local eye repairs for only the people listed below.

Their blink and/or gaze detection is final and was already gated by deterministic Python. Do not reclassify it, remove a target, add another person, or change should_fix. Return exactly one repair plan per listed subject_index. Each plan must include both gaze_repair_strategy and blink_repair_strategy. For whichever of the two a target's needs_gaze_repair/needs_blink_repair is false, fill that one with its "not applicable" placeholder shape (strategy_type/target_eye_state "not_safe_to_repair"/"不適用", every other field "不適用" or an empty list) instead of omitting it.

Every free-text field you write (edit_instruction, target_eye_state, target_gaze_read, success_condition, preservation rules, failure modes, etc.) will be copied verbatim into a separate prompt for a different model later -- one that sees only the full photo plus this same target's label and box, and nothing else about this analysis. So each field must stand on its own: describe the person only by their label wording or frame position (e.g. "the person on the left", the label given below), and describe the required change only in terms visible in the final photo. Never reference "Face crop N", subject_index, local_detection_index, detection_index, or any other identifier, numbering, or artifact of this analysis session -- that other model has never seen any of them and will not know what they mean.

Gaze repair (only for a target with needs_gaze_repair true):
Use the full photo and matching Face crop N to choose an anatomically plausible method that produces an unmistakable, normally visible correction to the exact current lens. The correction must be obvious at normal photo-viewing size, not only when the face is highly enlarged:
- eyes_plus_head_face_alignment is used only when the captured head yaw/pitch or face orientation itself materially prevents natural direct lens contact. Require a visible but bounded head/face alignment together with the iris/pupil correction;
- eyes_only is the correct method whenever the head and face are already naturally aligned to the current lens and both visible eyes can reach exact lens contact without side-eye, strain, crossed axes, or excessive sclera. The miss need not be tiny: a frontal face looking sideways may still need a clearly visible iris/pupil movement while its eyelids, eye shape, head pose, and facial projection remain fixed;
- not_safe_to_repair only when the pixels or geometry cannot support a reliable correction. In that case write a conservative plan and explicitly name the limitation.
For each gaze strategy, provide concrete English instructions. Name the current pupil/iris direction, exact target, required pupil/iris movement, natural sclera/catchlight rebalancing, any genuinely required bounded head/face alignment, repair strength, success condition, preservation rules, and likely failure modes. Describe this as repositioning the person's existing irises and pupils inside unchanged eye openings—never use reconstruct, rebuild, replace, regenerate, or redraw for the eyes or face. Use moderate strength by default; use subtle only for a genuinely tiny eyes_only miss whose completed correction remains obvious at normal viewing size. Preserve identity, eyelid openness, eye shape/color, canthi, lash lines, expression, glasses, skin, head pose unless the selected method explicitly requires it, clothing, scene, and every unlisted person. Do not stop at near-camera gaze.
Do not write any instruction that preserves, freezes, or restores the named person's torso, shoulders, body pose, stance, gesture, interaction points, or camera framing. Those properties are outside this local gaze plan and may be owned by a later user-selected pose or framing override.

Blink repair (only for a target with needs_blink_repair true):
The input fields photograph_left_eye_state and photograph_right_eye_state are diagnostic planner inputs only; they always mean the photograph's left and right sides. Do not repeat these source-state labels in target_eye_state, edit_instruction, eyelid_adjustment, or success_condition. Write target-forward instructions so the editor's attention stays on the desired open pair rather than the captured defect.

Treat both eyes as one coupled bilateral structure. Establish one shared final eyelid geometry before describing either eye. Only an eye detected as open may supply final geometry. When exactly one eye is fully_closed and the opposite eye is open, name the photograph side of that open eye as the finished-shape reference. When the opposite eye is narrowed_open or half_closed, neither captured aperture is a reference: require both upper-lid margins to move to one naturally-open target instead of averaging their source openings.

Use these visible match criteria: aperture-height-to-eye-width ratio; upper/lower-lid contour; iris exposure; and double-eyelid crease type, path, peak, visible length, and crease-to-lash width. Preserve fixed canthi and eye-globe identity. For a frontal face, use the nose bridge/facial midline as the bilateral comparison axis; for a turned face, compare in each eye's local axes and allow only unavoidable foreshortening. Resolve closed-lid compression into a smooth crease without adding upper-lid mass or puffiness.

When the two diagnostic source states differ and neither eye is a valid open-eye reference, make eye-fissure geometry and double-eyelid-crease geometry two independent final matches. Do not compensate for a mismatch in one by changing the other, and do not preserve the blink-induced left/right difference as natural asymmetry. Check the two matches separately, then confirm they still hold together. This stricter rule is local to that mixed-state blink pair and must not symmetrize the rest of the face.

Keep each model-facing field concise: target_eye_state and success_condition are one final-state sentence each; edit_instruction and eyelid_adjustment are at most two sentences each. Do not narrate the repair history, repeat requirements between fields, or ask the editor to reason through a checklist. Use strong repair strength when either source eye is fully closed or both eyes require opening. Preserve identity, expression, skin texture, glasses, makeup, lighting, every unlisted person, and intentional closed-eye expressions. When gaze repair is also required, describe one coordinated local eye edit.

Targets:
""" + json.dumps(compact_targets, ensure_ascii=False, indent=2)


# blink_issue/hair_issue/gaze_issue/smile_issue below deliberately carry no
# subject_label or box of their own -- they are nested inside one
# subjects[] entry, so they always inherit that same subject's label and
# box by construction. There is no cross-array label-matching step left for
# the model to get wrong.

# A Call-C-authored (or deterministic-fallback) repair strategy for an
# approved blink fix, the same role _GAZE_REPAIR_STRATEGY_SCHEMA plays for gaze.
_BLINK_REPAIR_STRATEGY_SCHEMA = _schema_object({
    "target_eye_state": _schema_string(),
    "eyelid_adjustment": _schema_string(),
    "repair_strength": _schema_string(),
    "edit_instruction": _schema_string(),
    "success_condition": _schema_string(),
    "preserve_during_repair": _schema_string_array(),
    "failure_to_avoid": _schema_string_array(),
})


# Call B reports recall-first physical evidence only.  The legacy fields
# remain in the shape for downstream compatibility, but their final values are
# overwritten by derive_blink_decisions; the model is not authoritative
# for blink_candidate or should_fix.
_BLINK_DETECTION_SCHEMA = _schema_object({
    "blink_observability": _schema_enum(("both_eyes", "one_eye", "unreadable")),
    "eyelid_detail_quality": _schema_enum(("readable", "borderline", "unreadable")),
    "left_eye_state": _schema_enum((
        "open", "narrowed_open", "half_closed", "fully_closed", "not_visible", "unclear",
    )),
    "right_eye_state": _schema_enum((
        "open", "narrowed_open", "half_closed", "fully_closed", "not_visible", "unclear",
    )),
    "physical_eye_state": _schema_enum((
        "open", "narrowed_open", "half_closed", "fully_closed", "one_eye_closed", "unclear",
    )),
    "eye_closure_confidence": _schema_number(),
    "mouth_laughter_state": _schema_enum((
        "not_laughing", "smile_or_broad_smile", "active_high_intensity_laughter", "unclear",
    )),
    "periocular_contraction": _schema_enum((
        "none_or_mild", "strong_bilateral", "unclear",
    )),
    "closure_expression_dependence": _schema_enum((
        "integral_to_expression", "independent_or_transitional", "unclear",
    )),
    "explicit_closure_context": _schema_enum((
        "none", "deliberate_wink", "sleep", "prayer_or_meditation", "savoring",
        "crying_or_pain_or_strong_emotion", "downward_at_visible_target",
        "other_clear_activity", "unclear",
    )),
    "intent_evidence_confidence": _schema_number(),
    "overall_demeanor": _schema_enum((
        "alert_or_camera_ready", "calm_or_at_ease", "unclear",
    )),
    "intent_cues": _schema_string_array(),
    "blink_candidate": _schema_boolean(),
    "eye_state": _schema_string(),
    "severity": _schema_string(),
    "closure_intent": _schema_string(),
    "expression_coherence": _schema_string(),
    "blink_failure_confidence": _schema_number(),
    "should_fix": _schema_boolean(),
    "reason": _schema_string(),
    "blink_repair_strategy": _BLINK_REPAIR_STRATEGY_SCHEMA,
})


_HAIR_ISSUE_SCHEMA = _schema_object({
    "hair_state": _schema_enum((
        "tidy", "styled_messy", "styled_bangs", "styled_face_framing",
        "motion", "natural_texture", "disheveled", "windblown", "strands_across_face",
        "bangs_disturbed", "bangs_clumped", "lock_out_of_place",
        "frizz_halo", "tuft_or_flattened", "occluded_or_unclear",
        "uncertain", "不適用",
    )),
    "visible_evidence": _schema_string_array(),
    "front_hair": _schema_enum(("none", "even", "clumped", "unclear", "不適用")),
    "fix_action": _schema_enum((
        "none", "lay_back", "remove", "settle", "redistribute", "不適用",
    )),
    "mess_intent": _schema_enum((
        "intentional_style", "accidental_capture", "ambiguous", "不適用",
    )),
    "hair_mess_confidence": _schema_number(),
    "should_fix": _schema_boolean(),
    "correction_plan": _schema_string(),
    "reason": _schema_string(),
})


_GAZE_TARGET_IN_FRAME_SCHEMA = _schema_object({
    "present": _schema_boolean(),
    "description": _schema_string(),
    "box": _BOX_SCHEMA,
})


_GAZE_REPAIR_STRATEGY_SCHEMA = _schema_object({
    "strategy_type": _schema_string(),
    "original_gaze_read": _schema_string(),
    "target_gaze_read": _schema_string(),
    "eye_adjustment": _schema_string(),
    "sclera_catchlight_adjustment": _schema_string(),
    "head_face_adjustment": _schema_string(),
    "repair_strength": _schema_string(),
    "edit_instruction": _schema_string(),
    "success_condition": _schema_string(),
    "preserve_during_repair": _schema_string_array(),
    "failure_to_avoid": _schema_string_array(),
})


# Call B deliberately stops at physical observation and exact-lens
# classification.  should_fix and the full repair strategy are added by
# deterministic Python and an optional repair-planning Call C respectively.
_GAZE_DETECTION_SCHEMA = _schema_object({
    "gaze_observability": _schema_enum(("both_eyes", "one_eye", "unreadable")),
    "eye_detail_quality": _schema_enum(("readable", "borderline", "unreadable")),
    "head_pose": _schema_enum(("frontal", "slight_yaw", "strong_yaw", "profile", "rear_or_hidden", "unclear")),
    "left_eye_direction": _schema_enum((
        "toward_exact_lens", "near_lens_left", "near_lens_right", "above_lens", "below_lens",
        "off_left", "off_right", "up", "down", "unclear", "not_visible",
    )),
    "right_eye_direction": _schema_enum((
        "toward_exact_lens", "near_lens_left", "near_lens_right", "above_lens", "below_lens",
        "off_left", "off_right", "up", "down", "unclear", "not_visible",
    )),
    "binocular_alignment": _schema_enum((
        "exact_lens_convergent", "near_lens_convergent", "off_lens_convergent",
        "mismatched", "unverifiable",
    )),
    "gaze_state": _schema_enum((
        "direct_camera", "near_camera", "off_camera", "downward", "upward_side",
        "unfocused_camera", "cross_focused", "ambiguous", "unclear",
    )),
    "gaze_target_in_frame": _GAZE_TARGET_IN_FRAME_SCHEMA,
    "lens_contact_decision": _schema_enum(("looking_at_lens", "not_looking_at_lens", "unclear")),
    "lens_contact_confidence": _schema_number(),
    "not_looking_at_lens_confidence": _schema_number(),
    "reason": _schema_string(),
})


_SMILE_STATE_SCHEMA = _schema_object({
    # Keep this constrained at the API boundary as well as in the prose
    # instruction.  A plain string allowed structured-output models to
    # translate values (for example gentle -> "溫和微笑"), which made
    # the downstream resolver discard an otherwise readable face.
    "smile_intensity": _schema_enum(("neutral", "gentle", "broad", "laughing", "unclear")),
    "adjust_safe": _schema_boolean(),
    "reason": _schema_string(),
})


# Call B's per-person output shape (build_portrait_qc_instruction) -- one
# entry per person handed to Call B. subject_index is the stable merge key;
# local_detection_index validates the Face crop N association.
_PERSON_ISSUES_SCHEMA = _schema_object({
    "subject_index": _schema_integer(),
    "local_detection_index": _schema_integer(),
    "blink_issue": _BLINK_DETECTION_SCHEMA,
    "hair_issue": _HAIR_ISSUE_SCHEMA,
    "gaze_issue": _GAZE_DETECTION_SCHEMA,
    "smile_issue": _SMILE_STATE_SCHEMA,
})


def build_portrait_qc_response_schema(person_count):
    """person_count pins person_issues[] to exactly that many entries via
    minItems/maxItems, so a length mismatch against the `persons` list
    build_portrait_qc_instruction was given is an API-level schema violation.
    Identity and merge correctness are validated separately by subject_index
    and local_detection_index; array order is not used as identity."""
    return {
        "type": "json_schema",
        "name": "portrait_qc",
        "strict": True,
        "schema": _schema_object({
            "person_issues": {
                "type": "array",
                "items": _PERSON_ISSUES_SCHEMA,
                "minItems": person_count,
                "maxItems": person_count,
            },
        }),
    }


_EYE_REPAIR_PLAN_ITEM_SCHEMA = _schema_object({
    "subject_index": _schema_integer(),
    "local_detection_index": _schema_integer(),
    "gaze_repair_strategy": _GAZE_REPAIR_STRATEGY_SCHEMA,
    "blink_repair_strategy": _BLINK_REPAIR_STRATEGY_SCHEMA,
})


def build_eye_repair_planning_response_schema(person_count):
    return {
        "type": "json_schema",
        "name": "eye_repair_planning",
        "strict": True,
        "schema": _schema_object({
            "repair_plans": {
                "type": "array",
                "items": _EYE_REPAIR_PLAN_ITEM_SCHEMA,
                "minItems": person_count,
                "maxItems": person_count,
            },
        }),
    }
