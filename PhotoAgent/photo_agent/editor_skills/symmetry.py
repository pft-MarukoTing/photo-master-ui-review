"""Face-symmetry skill: a user's request that one person's face read balanced left to right."""

from .base import MODE_USER_FACE_GEOMETRY, EditorSkill
from .face import FACE_SYMMETRY, _BASE_PRESERVE, _box, has_user_face_state

# Analysis field the chat route writes: [{"subject_index": int, "kind": "face_symmetry", "text": str}].
USER_FACE_REQUESTS = "_user_face_requests"

FACE_GEOMETRY_TASK_INTRO = (
    "You are editing the supplied photograph with photographic realism, anatomically coherent facial geometry, "
    "and faithful texture. Apply the user-directed facial change visibly while preserving the same people, scene, and identity."
)

# The balanced end state as one line per feature, so each is a target the editor renders towards. Every
# feature is adjusted, never redrawn, so identity carries through; only the head turn a photographer would ask for.
_FACE_SYMMETRY_LINES = (
    "- User-directed final state: the face reads balanced left to right in each item below and is still "
    "unmistakably this person. Each item is an adjustment of the feature already there, keeping its own "
    "texture and marks — this face corrected, not a redrawn, averaged or idealized one.",
    "- Face contour: the cheek and jaw of the fuller side are slimmed to match the other side.",
    "- Chin: the chin sits centered under the nose and mouth, with both sides of the chin and jawline matched.",
    "- Nose: the bridge runs straight down the facial midline from between the brows to the tip, a bent or crooked "
    "bridge straightened, with the tip and nostrils centered and level.",
    "- Eyes: both eyes sit at the same height and match in size, width and opening, with eyelid creases of matching "
    "height and width; the smaller eye is brought to the other, keeping this person's iris and eye character.",
    "- Brows: both brows sit at the same height with a matching arch.",
    "- Mouth: the mouth corners and lip line sit level and the lips are centered under the nose.",
    "- Head: a small, natural correction toward the camera and toward level is allowed, the kind a photographer gets "
    "by asking the subject to face the lens a little more, but not a full turn to frontal. Gaze, identity and "
    "skin stay the same.",
)

_QA_FACE_SYMMETRY = 45


def face_symmetry_requests(analysis):
    """(subject_index, subject) for every valid face-symmetry request, one per person, in subject order."""
    subjects = analysis.get("subjects") or []
    indices = set()
    for request in analysis.get(USER_FACE_REQUESTS) or []:
        if not isinstance(request, dict) or request.get("kind") != FACE_SYMMETRY:
            continue
        index = request.get("subject_index")
        if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < len(subjects):
            continue
        if isinstance(subjects[index], dict) and subjects[index].get("is_person"):
            indices.add(index)
    return [(index, subjects[index]) for index in sorted(indices)]


def face_geometry_guard_line(subject):
    """The completion line for one person with a balanced-face final state."""
    return (
        f"- The result is incomplete if any of {subject}'s face contour, chin, nose, eyes, brows or mouth "
        "still looks clearly uneven or crooked. Turning the head all the way to face the camera, or redrawing a "
        "feature into a different one, overshoots it."
    )


def face_geometry_guard(resolved):
    """Completion lines for every person with a balanced-face final state."""
    return "".join(
        "\n" + face_geometry_guard_line(item["subject"]) for item in resolved if item["final_state"].get(FACE_SYMMETRY)
    )


class FaceSymmetrySkill(EditorSkill):
    name = "face_symmetry"
    facial_features = frozenset({FACE_SYMMETRY})

    def contracts(self, analysis, ctx):
        return [{
            "feature": FACE_SYMMETRY,
            "source": "user",
            "subject_index": index,
            "subject": subject.get("label") or "畫面中的人物",
            "box": _box(subject),
            "endpoint": {FACE_SYMMETRY: "balanced"},
            "direct_changes": ["face_contour", "facial_feature_balance"],
            "dependent_changes": [
                "eye_landmark_geometry", "eyelid_aperture", "facial_expression", "head_pose", "mouth_expression",
                "underlying_facial_anatomy",
            ],
            "preserve": sorted(_BASE_PRESERVE),
        } for index, subject in face_symmetry_requests(analysis)]

    def subject_lines(self, item, ctx):
        return list(_FACE_SYMMETRY_LINES) if item["final_state"].get(FACE_SYMMETRY) else []

    def prompt_modes(self, analysis, resolved, ctx):
        return {MODE_USER_FACE_GEOMETRY} if has_user_face_state(resolved) else set()

    def final_state_terms(self, analysis):
        return ("face balance",) if face_symmetry_requests(analysis) else ()

    def qa_checks(self, analysis):
        names = "、".join(subject.get("label") or "畫面中的人物" for _, subject in face_symmetry_requests(analysis))
        if not names:
            return []
        return [(
            _QA_FACE_SYMMETRY,
            f"- User-requested face balance: the user asked for {names}'s face to look balanced left to right, so a "
            "reshaped cheek, jaw and chin and slightly adjusted eyes, brows, nose and mouth on that face are requested, "
            "not an identity change. Check each against the original: the fuller side slimmed toward the other; the "
            "chin centered under nose and mouth; the nose bridge straight on the midline with the tip centered; both "
            "eyes at the same height and matching in size, opening and eyelid crease; brows level; mouth corners level; "
            "at most a small, natural head correction. Fail it if that face looks essentially unchanged, if the head is "
            "turned all the way to frontal, if a feature was redrawn into a different one so the person no longer looks "
            "like themselves, or if gaze or skin changed. A reshaped face on anyone not named also fails "
            "this check.\n",
        )]
