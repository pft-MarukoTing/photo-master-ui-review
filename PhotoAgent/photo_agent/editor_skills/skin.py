"""Skin skill: approved skin-cast and local face-tonal corrections, and each person's skin boundary."""

import re

from prompts import _has_person

from .base import EditorSkill

_SKIN_TERMS = (
    "膚色", "皮膚", "臉色", "臉部", "面部", "face tone", "skin", "complexion",
)
_SKIN_FALLBACK_AXES = {"white_balance", "hue", "saturation", "tint"}
_FACE_TONAL_AXES = {"exposure", "shadow", "highlight", "contrast"}

_QA_SKIN = 0
_CAP_SKIN = 30


def _skin_identity_lines(analysis):
    lines = []
    for subject in analysis.get("subjects") or []:
        if not isinstance(subject, dict) or not subject.get("is_person"):
            continue
        label = str(subject.get("label") or "").strip()
        skin = str(subject.get("skin") or "").strip()
        if label and skin and skin not in {"不適用", "N/A"}:
            lines.append(f"- {label}: {skin}")
    return lines


def _is_skin_correction(text, axes):
    if "skin" in axes:
        return True
    # Text only rescues a color-only item the analyzer under-tagged; any other axis means it is not skin work.
    if not axes or not axes <= _SKIN_FALLBACK_AXES:
        return False
    lowered = re.sub(
        r"非皮膚|非膚色|non[ -]skin|non[ -]complexion",
        "", text.lower(),
    )
    return any(term in lowered for term in _SKIN_TERMS)


def _is_face_tonal_correction(text, axes):
    return bool(_FACE_TONAL_AXES & set(axes)) and bool(
        re.search(r"(?:臉部|面部|臉頰|人臉|\bface\b|\bfacial\b)", text, re.IGNORECASE)
    )


def _is_face_tonal_claim(text, axes):
    return _is_face_tonal_correction(text, axes) and not (
        {"skin", "white_balance", "hue", "saturation", "tint"} & axes
    )


def _split_claimed(claimed):
    """(skin_corrections, face_tonal_corrections) from the (text, axes) items the skin skill claimed."""
    skin_corrections, face_tonal_corrections = [], []
    for text, axes in claimed:
        (face_tonal_corrections if _is_face_tonal_claim(text, axes) else skin_corrections).append(text)
    return skin_corrections, face_tonal_corrections


class SkinSkill(EditorSkill):
    name = "skin"

    def claims_correction(self, text, axes, analysis):
        # Without a person, skin and face-tonal items stay general corrections.
        return _has_person(analysis) and (
            _is_face_tonal_claim(text, axes) or _is_skin_correction(text, axes)
        )

    def color_lines(self, analysis, claimed, ctx):
        if not _has_person(analysis):
            return []
        skin_corrections, face_tonal_corrections = _split_claimed(claimed)
        lines = []
        identities = _skin_identity_lines(analysis)
        identity_text = "\n".join(identities) if identities else "- Read each person's existing depth and undertone from the supplied photograph."
        correction_text = "\n".join(
            f"- Approved skin correction: {item} Apply it as the named cast correction and stop at the recorded native complexion."
            for item in skin_corrections
        )
        face_tonal_text = "\n".join(
            f"- Approved local face tonal correction: {item} Apply it across the full named region, including clothing when specified; brighten skin through exposure while retaining facial geometry and complexion."
            for item in face_tonal_corrections
        )
        lines.extend([
            "Skin boundary:",
            identity_text,
        ])
        if correction_text:
            lines.append(correction_text)
        if face_tonal_text:
            lines.append(face_tonal_text)
        lines.extend([
            "- Keep each person's native complexion depth, undertone, and chroma while skin follows the final shared exposure and actual illumination.",
            "- Apply scene white balance, hue, and saturation to the non-skin palette independently from complexion.",
            "- Keep tone continuous across face, neck, ears, and visible hands, with natural texture and modelling.",
        ])
        return lines

    def qa_checks(self, analysis):
        if not _has_person(analysis):
            return []
        return [(_QA_SKIN, "- Skin: judge each person's skin separately from the overall grade: each person keeps their own depth, undertone, and chroma while retaining plausible scene illumination; face, neck, ears, and hands remain continuous with no patch, band, unexplained hue mismatch, or saturation increase.\n")]

    def qa_score_caps(self, analysis):
        if not _has_person(analysis):
            return []
        return [(_CAP_SKIN, "- If a person's complexion identity is lost -- lightened toward a paler complexion, flattened, made more saturated, pushed toward yellow, orange, or red without scene-light justification, or broken by a patch or band -- score must be 3 or lower. Coherent warm, cool, or colored light from the scene is not a failure.\n")]

    def color_metadata(self, claimed):
        skin_corrections, face_tonal_corrections = _split_claimed(claimed)
        return {
            "skin_corrections": skin_corrections,
            "face_tonal_corrections": face_tonal_corrections,
        }
