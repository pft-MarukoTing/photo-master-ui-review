"""The chat page's note on a freshly analyzed photo: what the analysis says,
in the analyzer's own words, plus the people in it as the face panel names
them.

Nothing here calls a model. The summary fields are already written for a
reader (photo_summary / detected_issues). aesthetic_diagnosis is
deliberately left out: in the chat it read as a second, competing opening
line before the issue list that says the same thing more concretely.
"""

import math

MAX_ISSUES = 6

def _text(value):
    return value.strip() if isinstance(value, str) else ""


def _box(value):
    if not isinstance(value, dict):
        return None
    numbers = [value.get(k) for k in ("x", "y", "w", "h")]
    if any(isinstance(n, bool) or not isinstance(n, (int, float)) or not math.isfinite(n) for n in numbers):
        return None
    return dict(zip(("x", "y", "w", "h"), numbers))


def _overlap(a, b):
    iw = min(a["x"] + a["w"], b["x"] + b["w"]) - max(a["x"], b["x"])
    ih = min(a["y"] + a["h"], b["y"] + b["h"]) - max(a["y"], b["y"])
    if iw <= 0 or ih <= 0:
        return 0
    smaller = min(a["w"] * a["h"], b["w"] * b["h"])
    return iw * ih / smaller if smaller > 0 else 0


def _subject_for_detection(analysis, subjects, detection_index, face_box):
    for review in analysis.get("local_face_review") or []:
        if isinstance(review, dict) and review.get("detection_index") == detection_index:
            index = review.get("matched_subject_index")
            ok = isinstance(index, int) and not isinstance(index, bool) and 0 <= index < len(subjects)
            return (index if ok and isinstance(subjects[index], dict) else -1), True
    best, found = 0.5, -1
    for index, subject in enumerate(subjects):
        box = _box(subject.get("box")) if isinstance(subject, dict) else None
        if box and _overlap(face_box, box) > best:
            best, found = _overlap(face_box, box), index
    return found, False


def face_people(analysis):
    """The faces the detail panel shows, in its order (frontend-chat face.ts
    faceEntries): [{number, subject_index, name, box}]. `number` is the
    panel's "人物 N"; subject_index is -1 for a face with no subject."""
    subjects = analysis.get("subjects") if isinstance(analysis.get("subjects"), list) else []
    faces = analysis.get("_local_face_boxes")
    if not isinstance(faces, list):
        crops = analysis.get("_face_crop_boxes")
        faces = [{"box": b, "sent_to_model": True} for b in crops] if isinstance(crops, list) else []
    people = []
    for i, face in enumerate(faces):
        box = _box(face.get("box")) if isinstance(face, dict) else None
        if not box:
            continue
        index, matched = _subject_for_detection(analysis, subjects, i + 1, box)
        if index < 0 and (matched or face.get("sent_to_model") is False):
            continue
        number = len(people) + 1
        label = _text(subjects[index].get("label")) if index >= 0 else ""
        people.append({"number": number, "subject_index": index, "name": label or f"人物 {number}", "box": box})
    return people


def resolve_prompt_faces(analysis, prompt_faces):
    """{the text a pre-session face pick wrote into user_prompt: the text it
    should read now}, for every pick whose box is still a face in the panel.

    The upload page lets the user point at a face before the photo has been
    analyzed, so the pick can only survive as words -- a name from the local
    detection order ("人物 2") and where that face stands in the frame. By the
    time there is an analysis the name can be stale: face_people drops a face
    the analyzer saw nothing in and renumbers what is left, and it prefers the
    analyzer's own label for a person, which is also what the model reading
    the prompt can actually resolve. Matching the box -- both sides carry the
    same padded detection box, so the overlap is 1.0 -- is what says which
    person the pick meant.
    """
    people = face_people(analysis)
    rewrites = {}
    for face in prompt_faces if isinstance(prompt_faces, list) else []:
        box = _box(face.get("box")) if isinstance(face, dict) else None
        ref = _text(face.get("ref")) if isinstance(face, dict) else ""
        if not box or not ref:
            continue
        best, found = 0.5, None
        for person in people:
            score = _overlap(box, person["box"])
            if score > best:
                best, found = score, person
        if found is None:
            continue
        where = _text(face.get("where"))
        rewrites[ref] = f"{found['name']}（{where}）" if where else found["name"]
    return rewrites


def person_names(analysis):
    """{subject_index: the name the panel shows for that person}."""
    names = {}
    for person in face_people(analysis):
        if person["subject_index"] >= 0:
            names.setdefault(person["subject_index"], person["name"])
    return names


def shown_subjects(analysis):
    """Indexes of the subjects the detail panel lists a face for, in its order."""
    return list(person_names(analysis))


def build_brief(analysis):
    """The analysis_brief card body, or None when the analysis has nothing
    to say."""
    issues = [_text(i) for i in analysis.get("detected_issues") or [] if _text(i)][:MAX_ISSUES]
    brief = {
        "summary": _text(analysis.get("photo_summary")),
        "issues": issues,
    }
    if not any(brief.values()):
        return None
    return brief
