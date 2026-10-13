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
    if isinstance(analysis.get('_fixed_preview_person_ids'), list):
        ids = set(analysis['_fixed_preview_person_ids'])
        return [{'number': number, 'subject_index': index,
                 'name': _text(subject.get('label')) or f'人物 {number}', 'box': subject['box'],
                 'thumbnail_box': (analysis.get('_subject_thumbnail_boxes') or {}).get(str(index), subject['box'])}
                for number, (index, subject) in enumerate(
                    ((i, s) for i, s in enumerate(analysis.get('subjects') or [])
                     if s.get('preview_person_id') in ids and _box(s.get('box'))), 1)]
    subjects = analysis.get("subjects") if isinstance(analysis.get("subjects"), list) else []
    faces = analysis.get("_local_face_boxes")
    if not isinstance(faces, list):
        crops = analysis.get("_face_crop_boxes")
        faces = [{"box": b, "sent_to_model": True} for b in crops] if isinstance(crops, list) else []
    people = []
    represented = set()
    for i, face in enumerate(faces):
        box = _box(face.get("box")) if isinstance(face, dict) else None
        if not box:
            continue
        index, matched = _subject_for_detection(analysis, subjects, i + 1, box)
        if index < 0 and (matched or face.get("sent_to_model") is False):
            continue
        if index >= 0:
            if index in represented:
                continue
            represented.add(index)
        number = len(people) + 1
        label = _text(subjects[index].get("label")) if index >= 0 else ""
        people.append({"number": number, "detection_index": i + 1, "subject_index": index, "name": label or f"人物 {number}", "box": box})
    for index, subject in enumerate(subjects):
        if not isinstance(subject, dict) or subject.get("is_person") is not True or index in represented:
            continue
        detection = subject.get("local_detection_index")
        if isinstance(detection, int) and not isinstance(detection, bool) and detection > 0:
            if any(isinstance(review, dict) and review.get("detection_index") == detection
                   and (review.get("matched_subject_index") == -1 or review.get("is_real_face") is False)
                   for review in analysis.get("local_face_review") or []):
                continue
        box = _box(subject.get("box"))
        if not box or not (0 <= box["x"] and 0 <= box["y"] and box["w"] > 0 and box["h"] > 0
                           and box["x"] + box["w"] <= 1 and box["y"] + box["h"] <= 1):
            continue
        number = len(people) + 1
        people.append({"number": number, "subject_index": index,
                       "name": _text(subject.get("label")) or f"人物 {number}", "box": box})
    return people


def resolve_prompt_faces(analysis, prompt_faces):
    """Resolve v2 IDs, legacy detector IDs, or older geometry-only references.

    An explicit identity must pass reciprocal binding and photo/job checks;
    failures never fall back to names, order or geometry. Only references
    without preview identity retain the historical overlap path.
    """
    people = face_people(analysis)
    rewrites = {}
    for face in prompt_faces if isinstance(prompt_faces, list) else []:
        box = _box(face.get("box")) if isinstance(face, dict) else None
        ref = _text(face.get("ref")) if isinstance(face, dict) else ""
        if not box or not ref:
            if isinstance(face, dict) and "preview_person_id" in face:
                raise ValueError("指定人物的引用資料無效，請重新指定。")
            continue
        if "preview_person_id" in face:
            identity = analysis.get("_subject_preview_identity") or {}
            if any(face.get(k) != identity.get(k) for k in ("preview_job_id", "source_sha256")):
                raise ValueError("指定人物的預覽與此照片不符，請重新指定。")
            person_id = face["preview_person_id"]
            if (not isinstance(person_id, str) or not identity.get("preview_job_id")
                    or not identity.get("source_sha256")):
                raise ValueError("指定人物的引用資料無效，請重新指定。")
            index = (analysis.get("_preview_person_bindings") or {}).get(person_id)
            subjects = analysis.get("subjects") or []
            if (type(index) is not int or not 0 <= index < len(subjects)
                    or subjects[index].get("preview_person_id") != person_id
                    or sum(s.get("preview_person_id") == person_id for s in subjects) != 1):
                raise ValueError("指定的人物未完成身份確認，請重新指定或清除該需求。")
            label = _text(subjects[index].get("label"))
            if not label:
                raise ValueError("指定人物的名稱不可用，請重新指定。")
            where = _text(face.get("where"))
            rewrites[ref] = f"{label}（{where}）" if where else label
            continue
        if "detection_index" in face:
            # New preview references are resolved ONLY by reciprocal IDs from
            # the same photo/job, never by overlap or a provisional array index.
            identity = analysis.get("_subject_preview_identity") or {}
            if any(face.get(k) != identity.get(k) for k in ("preview_job_id", "source_sha256")):
                raise ValueError("指定人物的預覽與此照片不符，請重新指定。")
            detection = face["detection_index"]
            review = next((r for r in analysis.get("local_face_review", [])
                           if r.get("detection_index") == detection), None)
            index = review.get("matched_subject_index") if review else None
            subjects = analysis.get("subjects") or []
            if (type(index) is not int or not 0 <= index < len(subjects)
                    or review.get("is_real_face") is not True
                    or subjects[index].get("local_detection_index") != detection):
                raise ValueError("指定的人物未納入本次分析，請重新確認需求。")
            label = _text(subjects[index].get("label"))
            if not label:
                raise ValueError("指定人物的名稱不可用，請重新指定。")
            where = _text(face.get("where"))
            rewrites[ref] = f"{label}（{where}）" if where else label
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
