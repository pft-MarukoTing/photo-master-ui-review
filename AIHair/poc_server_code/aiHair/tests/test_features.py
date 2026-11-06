"""The nine features: one module each under their own names, never
importing one another, their post-processing switches HairFeatures'
defaults for the effect each was."""

import ast
import dataclasses
from pathlib import Path

import pytest

from ai_hair.features import FEATURE_NAMES, LEVELLED, feature_options
from ai_hair.pipeline import build_context
from hairfx import core
from hairfx.features import EFFECTS

FEATURES_DIR = Path(__file__).resolve().parent.parent / "hairfx" / "features"
MODULES = {"frizzCare": "frizz_care", "plump": "plump", "fluffy": "fluffy",
           "hairline": "hairline", "soft": "soft", "hairpart": "hairpart",
           "grayHair": "gray_hair", "rootRetouch": "root_retouch", "strayHair": "stray_hair"}


def test_the_nine_and_nothing_else():
    assert tuple(EFFECTS) == FEATURE_NAMES
    for name, feature in EFFECTS.items():
        assert feature.name == name
        assert type(feature).__module__ == f"hairfx.features.{MODULES[name]}"


def test_one_module_each_and_no_feature_imports_another():
    modules = {p.stem for p in FEATURES_DIR.glob("*.py")} - {"__init__"}
    assert modules == set(MODULES.values())
    for stem in modules:
        tree = ast.parse((FEATURES_DIR / f"{stem}.py").read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                # level 1: from .x (a sibling feature); level 2: from ..x (hairfx).
                assert node.level != 1, f"{stem}.py imports .{node.module}"


# feature -> (colour matched, colour match area, colour shift, blend mask,
# blended by default, lined up how, lined up at all)
EXPECTED = {
    "frizzCare": (True, "hair_inset", False, "hair", False, "global", True),
    "plump": (False, "hair", False, "hair", False, "global", True),
    "fluffy": (True, "hair", False, "crown", False, "global", True),
    "hairline": (True, "hair", False, "head", True, "regions_flow", True),
    "soft": (False, "hair", False, None, False, "global", False),
    "hairpart": (True, "hair", False, "parting", False, "global", True),
    "grayHair": (False, "hair", False, "hair_shifted", True, "global", False),
    "rootRetouch": (True, "hair", True, "hair_shifted", True, "global", False),
    "strayHair": (True, "hair", False, "face", False, "global", True),
}


@pytest.mark.parametrize("name", FEATURE_NAMES)
def test_post_processing_defaults_are_hairfeatures(name):
    feature = EFFECTS[name]
    opts = core.parse_options(feature, feature_options(name))
    ctx = build_context(client=None, n_images=1)
    colours, area, shift, mask, blended, how, aligns = EXPECTED[name]
    assert feature.colour_matches(ctx, opts) is colours
    assert feature.colour_area(ctx) == area
    assert feature.colour_shift is shift
    assert feature.blend_mask == mask
    assert bool(feature.blends(ctx, opts)) is blended
    assert feature.align_how(ctx) == how
    assert bool(feature.aligns(ctx, opts)) is aligns


def test_hairline_levels_are_its_option():
    feature = EFFECTS["hairline"]
    field = {f.name: f for f in dataclasses.fields(feature.Options)}["level"]
    assert field.metadata["choices"] == LEVELLED["hairline"] and field.default == 1
    one = feature.prompts(core.parse_options(feature, feature_options("hairline", 1)))
    two = feature.prompts(core.parse_options(feature, feature_options("hairline", 2)))
    assert one["detect"] == two["detect"] and one["edit"] != two["edit"]
    with pytest.raises(ValueError):
        core.parse_options(feature, ["level=3"])


def test_context_is_main_py_defaults():
    ctx = build_context(client=None, n_images=2)
    assert ctx.align is True and ctx.align_mode is None
    assert ctx.blend is None and ctx.colour_match is True and ctx.colour_match_area is None
    assert ctx.render.n_images == 2 and ctx.render.size == "match"
    assert ctx.render.model == core.DEFAULT_MODEL and ctx.render.quality == core.DEFAULT_QUALITY
