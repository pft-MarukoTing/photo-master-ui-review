"""Feature registry: every option a request can set, in one place.

One entry per selectable thing -- image / analysis model, render option,
aspect ratio, preset, style option, portrait-fix category, advanced toggle --
holding its id, where it applies (scope), its allowed values, its default,
its zh label, whether it is debug-only or gated by a feature flag, and the
params / selections key it is read from. param_validation, selections,
pipeline, image_tools and GET /api/capabilities all read their lists from
here, so adding a model or an option is one entry, not five parallel lists.

Scopes:
    global       one value per request (params or selections.advanced_options)
    per-person   one value per detected person (selections.face_issue_overrides)
    per-item     one value per analysis item (selections.recipe_checked)

Deliberately dependency-free (config.py is pure data): param_validation, which
the backend imports, must not pull in Pillow / cv2 through image_tools.

frontend/config.js and frontend/results.js still keep their own copies of
these lists; tests/test_capabilities.py only checks the backend side.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Tuple

import config


# ── Models ────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ImageModel:
    id: str                 # the params.image_model value
    label: str
    provider: str           # "openai" | "gemini" | "mai" -- which client's editor runs it
    api_model: str          # the name sent to the provider's API
    gaze_repair: bool       # has a Step 5b eye-repair editor (pipeline.EYE_REPAIR_PASS_BY_MODEL)
    arbitrary_size: bool = False  # OpenAI only: accepts any "WxH" size, not the fixed enum


IMAGE_MODELS: Tuple[ImageModel, ...] = (
    ImageModel("gpt-image-2", "GPT Image 2", "openai", "gpt-image-2", True, arbitrary_size=True),
    ImageModel("gpt-image-1.5", "GPT Image 1.5", "openai", "gpt-image-1.5", True),
    ImageModel("gpt-image-2.5-sunburst", "GPT Image 2.5 Sunburst", "openai", "gpt-image-2.5-sunburst", True, arbitrary_size=True),
    ImageModel("gpt-image-2.5-flare", "GPT Image 2.5 Flare", "openai", "gpt-image-2.5-flare", True, arbitrary_size=True),
    # "Nano Banana 2" / "Nano Banana 2 Lite" are Google's nicknames for these.
    ImageModel("nano-banana-2", "Nano Banana 2", "gemini", "gemini-3.1-flash-image", True),
    ImageModel("nano-banana-lite", "Nano Banana 2 Lite", "gemini", "gemini-3.1-flash-lite-image", True),
    ImageModel("MAI-Image-2.6", "MAI-Image-2.6", "mai", "MAI-Image-2.6", False),
)
IMAGE_MODELS_BY_ID = {m.id: m for m in IMAGE_MODELS}

QUALITIES = ("low", "medium", "high")


@dataclass(frozen=True)
class RenderOption:
    """One entry of the upload page's model picker: a model at one quality.
    Mirrors frontend/config.js imageModelQualityOptions, `id` included (a
    model with no PhotoAgent quality tier is listed by its bare id)."""
    id: str
    image_model: str
    quality: str
    label: str
    debug_only: bool


RENDER_OPTIONS: Tuple[RenderOption, ...] = (
    RenderOption("gpt-image-2:low", "gpt-image-2", "low", "GPT Image 2 · Standard", True),
    RenderOption("gpt-image-2:medium", "gpt-image-2", "medium", "GPT Image 2 · Enhanced", True),
    RenderOption("gpt-image-2.5-sunburst:low", "gpt-image-2.5-sunburst", "low", "GPT Image 2.5 Sunburst · Standard", False),
    RenderOption("gpt-image-2.5-sunburst:medium", "gpt-image-2.5-sunburst", "medium", "GPT Image 2.5 Sunburst · Enhanced", False),
    RenderOption("gpt-image-2.5-sunburst:high", "gpt-image-2.5-sunburst", "high", "GPT Image 2.5 Sunburst · Premium", False),
    RenderOption("gpt-image-2.5-flare:low", "gpt-image-2.5-flare", "low", "GPT Image 2.5 Flare · Standard", True),
    RenderOption("gpt-image-2.5-flare:medium", "gpt-image-2.5-flare", "medium", "GPT Image 2.5 Flare · Enhanced", True),
    RenderOption("nano-banana-2", "nano-banana-2", "medium", "Nano Banana 2", True),
    RenderOption("MAI-Image-2.6", "MAI-Image-2.6", "medium", "MAI-Image-2.6", True),
)
# gpt-image-1.5 and nano-banana-lite are accepted by the API but not offered
# in the picker.


@dataclass(frozen=True)
class AnalysisModel:
    id: str
    label: str
    # reasoning.effort sent with its Responses API calls. None omits the
    # "reasoning" field entirely (a non-reasoning model rejects it); a
    # reasoning model needs an explicit level -- "none" runs reasoning-free.
    reasoning_effort: Optional[str]
    debug_only: bool


ANALYSIS_MODELS: Tuple[AnalysisModel, ...] = (
    AnalysisModel("gpt-4.1-mini", "GPT 4.1 Mini", None, True),
    AnalysisModel("gpt-5.6-luna", "GPT 5.6 Luna", "low", False),
    AnalysisModel("gpt-5.6-sol", "GPT 5.6 Sol", "none", True),
)
ANALYSIS_MODEL_REASONING_EFFORT = {m.id: m.reasoning_effort for m in ANALYSIS_MODELS}


# ── Render settings ───────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Choice:
    value: str
    label: str
    debug_only: bool = False


# "auto" follows the analysis's proposed crop, "original" keeps the source
# frame, the rest force one W:H frame.
ASPECT_RATIOS: Tuple[Choice, ...] = (
    Choice("auto", "Auto"),
    Choice("original", "維持原圖比例"),
    Choice("1:1", "1:1（方形）", True),
    Choice("5:4", "5:4（橫）", True),
    Choice("4:3", "4:3（橫）", True),
    Choice("3:2", "3:2（橫）", True),
    Choice("16:9", "16:9（橫）", True),
    Choice("4:5", "4:5（直）", True),
    Choice("3:4", "3:4（直）", True),
    Choice("2:3", "2:3（直）", True),
    Choice("9:16", "9:16（直）", True),
)

PRESET_IDS = ("natural", "editorial", "product", "portrait", "custom")

# The API accepts up to MAX_RUN_COUNT; outside debug mode the UI offers up to
# GENERAL_MAX_RUN_COUNT (frontend/app.js GENERAL_MAX_RUN_COUNT).
MAX_RUN_COUNT = 20
GENERAL_MAX_RUN_COUNT = 8

DEFAULT_IMAGE_MODEL = "gpt-image-2.5-sunburst"
DEFAULT_QUALITY = "medium"
DEFAULT_ANALYSIS_MODEL = "gpt-5.6-luna"
DEFAULT_ASPECT_RATIO = "auto"
DEFAULT_PRESET = "natural"
DEFAULT_RUN_COUNT = 1
DEFAULT_RENDER_OPTION = f"{DEFAULT_IMAGE_MODEL}:{DEFAULT_QUALITY}"


# ── Style options (selections.options) ────────────────────────────────────────

@dataclass(frozen=True)
class StyleGroup:
    id: str
    label: str
    choices: Tuple[Choice, ...]


# Values must match config.STYLE_OPTION_INSTRUCTIONS. "default" (不指定) or an
# empty value means no pick. Angle "side" is supported but not shown in the UI.
STYLE_GROUPS: Tuple[StyleGroup, ...] = (
    StyleGroup("pose", "姿勢", (
        Choice("playful", "俏皮"), Choice("candid", "自然抓拍"), Choice("strong", "強勢"),
        Choice("elegant", "優雅"), Choice("dynamic", "動感"), Choice("cool", "酷帥"),
        Choice("vsign", "勝利手勢"), Choice("cheer", "歡呼"),
    )),
    StyleGroup("angle", "拍攝角度", (
        Choice("high", "俯拍"), Choice("low", "仰拍"), Choice("eye", "平視"), Choice("side", "側拍"),
    )),
    StyleGroup("lighting", "光線", (
        Choice("gold", "金色光調"), Choice("soft", "柔光"), Choice("cool", "藍調光線"), Choice("night", "夜色"),
    )),
    StyleGroup("size", "取景範圍", (
        Choice("wide", "遠景"), Choice("wider", "廣角"), Choice("close", "近景"), Choice("closer", "特寫"),
    )),
)
STYLE_GROUPS_BY_ID = {g.id: g for g in STYLE_GROUPS}
STYLE_UNSET_VALUES = ("", "default")


# ── Portrait fixes ────────────────────────────────────────────────────────────

SMILE_LEVELS = ("auto", "neutral", "gentle", "broad", "laughing")
# Level names older analyses / history records may still carry.
SMILE_LEVEL_ALIASES = {
    "no_smile": "neutral",
    "closed_smile": "gentle",
    "toothy_smile": "broad",
    "bright_smile": "broad",
}


@dataclass(frozen=True)
class PortraitFix:
    """One 人像修正 category: a global on/off toggle (selections.advanced_options
    / params.advanced_options) plus a per-person override
    (selections.face_issue_overrides["<subject_index>:<id>"])."""
    id: str                          # the analysis subject key, e.g. "hair_issue"
    label: str
    toggle_key: Optional[str]        # the advanced_options key, None when there is none
    levels: Tuple[Choice, ...] = ()  # per-person level choices; empty = on/off only
    default_level: Optional[str] = None
    level_aliases: dict = field(default_factory=dict)
    flag: Optional[str] = None       # config attribute that must be truthy


# Labelled for what was found, not for the finding of it: a per-person fix is
# only ever shown after the analysis, where the UI already says "AI 建議". The
# pre-photo switches in ADVANCED_OPTIONS answer a different question and carry
# the longer "we will fix this" names -- the two registers differ on purpose.
PORTRAIT_FIXES: Tuple[PortraitFix, ...] = (
    PortraitFix("blink_issue", "閉眼", "eye_closed_detection"),
    PortraitFix("gaze_issue", "視線", "gaze_contact_detection"),
    # 髮絲, not 亂髮: what gets tidied is often a single strand across the face
    # rather than messy hair, and the chip should not say otherwise.
    PortraitFix("hair_issue", "髮絲", "hair_mess_detection", levels=(
        Choice("dynamic", "保留髮絲動態"), Choice("tidy", "梳整俐落"),
    ), default_level="dynamic"),
    PortraitFix("smile_issue", "笑容", "smile_adjustment", levels=(
        Choice("auto", "自動"), Choice("neutral", "不笑"), Choice("gentle", "微笑"),
        Choice("broad", "開朗笑"), Choice("laughing", "大笑"),
    ), default_level="auto", level_aliases=SMILE_LEVEL_ALIASES),
    # Its global switch is selections.body_reshape_enabled, not an advanced option.
    PortraitFix("body_reshape", "身形調整", None, flag="ENABLE_BODY_RESHAPE"),
)
PORTRAIT_FIXES_BY_ID = {f.id: f for f in PORTRAIT_FIXES}


# ── Advanced options (params / selections .advanced_options) ──────────────────

@dataclass(frozen=True)
class AdvancedOption:
    key: str
    default: object
    label: str = ""
    choices: Tuple[str, ...] = ()  # empty = a bool toggle
    # internal: still accepted and honoured, but never offered to a user or
    # the chat tools. debug_only: offered in debug mode only.
    internal: bool = False
    debug_only: bool = False


ADVANCED_OPTIONS: Tuple[AdvancedOption, ...] = (
    # Asked before there is a photo, so each label states what we will do to the
    # category rather than naming a detection the user has not seen yet.
    AdvancedOption("eye_closed_detection", True, "閉眼修正"),
    # Read by the analysis instruction AND, independently, by the Step 5b
    # gaze-repair pass.
    AdvancedOption("gaze_contact_detection", True, "視線修正"),
    AdvancedOption("hair_mess_detection", True, "髮絲整理"),
    # Legacy: a user-elected "calm the wind-blown hair" transformation, off by
    # default. No longer in the UI.
    AdvancedOption("calm_hair", False, internal=True),
    # Read at edit time only; the analysis always reads every face's smile so
    # the level can change on the results page without re-analyzing.
    AdvancedOption("smile_adjustment", True, "笑容調整"),
    # Legacy global level, superseded by the per-person smile_issue level.
    # "auto" gives a non-smiling face a natural closed-lip smile and leaves an
    # already-smiling one alone.
    AdvancedOption("smile_level", "auto", choices=SMILE_LEVELS, internal=True),
)


# ── Lookups ───────────────────────────────────────────────────────────────────

def image_model(model_id: str) -> ImageModel:
    """The registry entry, or KeyError for an unknown id."""
    return IMAGE_MODELS_BY_ID[model_id]


def supports_arbitrary_openai_size(api_model: str) -> bool:
    return any(m.arbitrary_size and m.api_model == api_model for m in IMAGE_MODELS)


def flag_enabled(flag: Optional[str]) -> bool:
    return flag is None or bool(getattr(config, flag, False))


def normalize_level(category: str, level: str) -> Optional[str]:
    """`level` as a canonical per-person level of `category`, or None when the
    category takes no level or the value is not one of its levels."""
    fix = PORTRAIT_FIXES_BY_ID.get(category)
    if fix is None or not fix.levels:
        return None
    level = fix.level_aliases.get(level, level)
    return level if any(c.value == level for c in fix.levels) else None


# ── GET /api/capabilities ─────────────────────────────────────────────────────

def _choices(choices, debug):
    return [{"value": c.value, "label": c.label} for c in choices if debug or not c.debug_only]


def describe(*, debug: bool = False) -> dict:
    """What a client may offer. Debug-only entries appear only when `debug`;
    flag-gated ones only when their flag is on; internal ones never."""
    render_options = [o for o in RENDER_OPTIONS if debug or not o.debug_only]
    render_models = {o.image_model for o in render_options}
    return {
        "render": {
            "options": [
                {"id": o.id, "image_model": o.image_model, "quality": o.quality, "label": o.label}
                for o in render_options
            ],
            "image_models": [
                {"id": m.id, "label": m.label} for m in IMAGE_MODELS if m.id in render_models
            ],
            "analysis_models": [
                {"id": m.id, "label": m.label} for m in ANALYSIS_MODELS if debug or not m.debug_only
            ],
            "aspect_ratios": _choices(ASPECT_RATIOS, debug),
            "max_run_count": MAX_RUN_COUNT if debug else GENERAL_MAX_RUN_COUNT,
            "defaults": {
                "render_option": DEFAULT_RENDER_OPTION,
                "image_model": DEFAULT_IMAGE_MODEL,
                "quality": DEFAULT_QUALITY,
                "analysis_model": DEFAULT_ANALYSIS_MODEL,
                "aspect_ratio": DEFAULT_ASPECT_RATIO,
                "preset": DEFAULT_PRESET,
                "run_count": DEFAULT_RUN_COUNT,
            },
        },
        "presets": [
            {"id": preset_id, "label": config.PRESETS[preset_id]["label"]} for preset_id in PRESET_IDS
        ],
        "style_options": [
            {
                "id": g.id, "label": g.label, "scope": "global", "selections_key": f"options.{g.id}",
                "default": "default", "choices": _choices(g.choices, debug),
            }
            for g in STYLE_GROUPS
        ],
        "portrait_fixes": [
            {
                "id": f.id, "label": f.label, "scope": "per-person",
                "selections_key": f"face_issue_overrides.<subject_index>:{f.id}",
                "toggle_key": (
                    f"advanced_options.{f.toggle_key}" if f.toggle_key else "body_reshape_enabled"
                ),
                "levels": _choices(f.levels, debug),
                "default_level": f.default_level,
            }
            for f in PORTRAIT_FIXES if flag_enabled(f.flag)
        ],
        "advanced_options": [
            {
                "key": o.key, "label": o.label, "scope": "global",
                "selections_key": f"advanced_options.{o.key}",
                "default": o.default, **({"choices": list(o.choices)} if o.choices else {}),
            }
            for o in ADVANCED_OPTIONS if not o.internal and (debug or not o.debug_only)
        ],
        "corrections": {"scope": "per-item", "selections_key": "recipe_checked"},
    }
