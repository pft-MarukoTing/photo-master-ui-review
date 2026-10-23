"""The food branch: a food photographer's analysis in place of Call A, its own editor prompt, and food reframing."""

import json

from aspect_ratio_rules import allowed_auto_ratio_labels
from image_tools import create_preview_bytes, get_image_dimensions, image_to_data_url
from photo_agent.capabilities import ANALYSIS_MODEL_REASONING_EFFORT
from prompts import photo_facts_section

from ..base import BranchSkill
from .adapter import BRANCH, merge_reframe, to_analysis
from .edit_prompt import build_food_edit_prompt
from .instructions import (
    ANALYSIS_INSTRUCTION, analysis_schema, localized, ratio_labels, ratio_policy, reframe_instruction, reframe_schema,
)


def _labels(pref, width, height):
    return ratio_labels(pref, width, height, allowed_auto_ratio_labels(width / height))


def _request(image_bytes, analysis_model, instruction, schema, label):
    import openai_client

    data_url = image_to_data_url(create_preview_bytes(image_bytes), "image/jpeg")
    envelope = openai_client._analysis_request(analysis_model, data_url, [], instruction, schema, label,
                                               with_face_crops=False)
    usage = {"model": analysis_model, "usage": envelope.get("usage"),
             "reasoning_effort": ANALYSIS_MODEL_REASONING_EFFORT.get(analysis_model)}
    return openai_client._parse_response_json(envelope), usage


class FoodBranchSkill(BranchSkill):
    name = BRANCH
    categories = ("food",)

    def analyze(self, image_bytes, aspect_ratio, photo_facts, analysis_model, user_requests=None):
        from photo_agent import chat_analysis

        width, height = get_image_dimensions(image_bytes)
        pref = str(aspect_ratio or "auto").strip().lower()
        instruction = (localized(ANALYSIS_INSTRUCTION + ratio_policy(pref, width, height)) + photo_facts_section(photo_facts)
                       + chat_analysis.context(user_requests))
        result, usage = _request(image_bytes, analysis_model, instruction, analysis_schema(_labels(pref, width, height)),
                                 "Photo analysis failed (food branch)")
        analysis = to_analysis(result, photo_facts, (width, height))
        if user_requests:
            analysis["_analysis_user_requests"] = user_requests
        return analysis, usage, instruction

    def reframe(self, image_bytes, analysis, aspect_ratio, analysis_model):
        width, height = get_image_dimensions(image_bytes)
        pref = str(aspect_ratio or "auto").strip().lower()
        food = analysis["food_branch"]
        settled = {
            "dishes_and_objects": food["subjects"],
            "finished_look": food["look_recipe"],
            "other_review_items": [item["recipe"] for item in food["review_items"] if item["kind"] != "reframe"],
            "previous_framing": food["framing"],
        }
        instruction = localized(reframe_instruction(ratio_policy(pref, width, height),
                                                    json.dumps(settled, ensure_ascii=False)))
        reframe, usage = _request(image_bytes, analysis_model, instruction, reframe_schema(_labels(pref, width, height)),
                                  "Photo reframing failed (food branch)")
        return merge_reframe(analysis, reframe, pref, (width, height)), usage, instruction

    def build_edit_prompt(self, framed_analysis, crop_plan, source_size, revision_feedback=None):
        return build_food_edit_prompt(framed_analysis, crop_plan, source_size, revision_feedback)
