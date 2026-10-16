"""Face-symmetry skill: a user's request that one person's face read balanced left to right."""

from .base import MODE_USER_FACE_GEOMETRY, EditorSkill
from .face import FACE_SYMMETRY, _BASE_PRESERVE, _box, has_user_face_state

# Analysis field the chat route writes: [{"subject_index": int, "kind": "face_symmetry", "text": str}].
USER_FACE_REQUESTS = "_user_face_requests"

FACE_GEOMETRY_TASK_INTRO = (
    "You are editing the supplied photograph with photographic realism, anatomically coherent local facial geometry, "
    "and faithful texture. Apply the user-directed facial change visibly while preserving the same people, scene, and identity."
)

# The balanced end state, reached the way the edit model does it when asked on its own: several small
# corrections together, with only the head turn a photographer would ask for.
_FACE_SYMMETRY_STATE = (
    "- User-directed final state: the face reads balanced left to right — the cheek and jaw contour of the fuller "
    "side is slimmed to match the other side, and the chin and mouth sit centered under the nose. A small, natural "
    "correction of the head toward the camera and toward level is allowed, the kind a photographer gets by asking "
    "the subject to face the lens a little more, but not a full turn to frontal. Gaze, expression, identity and skin "
    "stay the same."
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


def face_geometry_guard(resolved):
    """Completion lines for every person with a balanced-face final state."""
    return "".join(
        f"\n- The result is incomplete if {item['subject']}'s face still looks clearly uneven left to right. "
        "Turning the head all the way to face the camera overshoots it."
        for item in resolved if item["final_state"].get(FACE_SYMMETRY)
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
            "direct_changes": ["face_contour"],
            "dependent_changes": ["head_pose", "underlying_facial_anatomy"],
            "preserve": sorted(_BASE_PRESERVE),
        } for index, subject in face_symmetry_requests(analysis)]

    def subject_lines(self, item, ctx):
        return [_FACE_SYMMETRY_STATE] if item["final_state"].get(FACE_SYMMETRY) else []

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
            "reshaped cheek or jaw contour on that face is requested, not an identity change. Check that the face reads "
            "more balanced than in the original: the fuller side slimmed toward the other, chin and mouth centered under "
            "the nose, with at most a small, natural head correction. Fail it if that face looks essentially unchanged, "
            "if the head is turned all the way to frontal, or if identity, expression, gaze or skin changed. A reshaped "
            "face on anyone not named also fails this check.\n",
        )]
