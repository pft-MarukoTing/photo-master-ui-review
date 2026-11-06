import pytest

from ai_hair.features import FEATURE_NAMES, feature_options
from ai_hair.param_validation import MAX_RESULTS, validate_ai_hair_request
from engine_result_code import RESULT_ERROR_EMPTY_SRC_KEYS, RESULT_INVALID_PARAMETER


def task(params=None, **overrides):
    info = {
        "effect": "aiHair",
        "srcKeys": ["demo/aiHair/src/job/photo.jpg"],
        "dstKeys": ["demo/aiHair/result/job/result_0.jpg"],
        "acts": [{"id": 0, "params": params if params is not None else {"feature": "frizzCare"}}],
    }
    info.update(overrides)
    return info


@pytest.mark.parametrize("feature", [f for f in FEATURE_NAMES if f != "hairline"])
def test_each_feature_is_taken_by_its_name(feature):
    request = validate_ai_hair_request(task({"feature": feature}))
    assert request["params"]["feature"] == feature
    assert request["params"]["level"] is None
    assert feature_options(feature, None) == []


@pytest.mark.parametrize("level,expected", [(None, 1), (1, 1), (2, 2), ("2", 2)])
def test_hairline_levels(level, expected):
    params = {"feature": "hairline"}
    if level is not None:
        params["level"] = level
    request = validate_ai_hair_request(task(params))
    assert request["params"]["level"] == expected
    assert feature_options("hairline", request["params"]["level"]) == [f"level={expected}"]


@pytest.mark.parametrize("level", [0, 3, 1.5, True, "lv1"])
def test_hairline_rejects_other_levels(level):
    with pytest.raises(RuntimeError) as e:
        validate_ai_hair_request(task({"feature": "hairline", "level": level}))
    assert e.value.args[1] == RESULT_INVALID_PARAMETER


def test_feature_name_is_case_insensitive():
    request = validate_ai_hair_request(task({"feature": " GRAYHAIR "}))
    assert request["params"]["feature"] == "grayHair"


@pytest.mark.parametrize("params", [{}, {"feature": ""}, {"feature": "curly"}, {"feature": 3}])
def test_bad_feature(params):
    with pytest.raises(RuntimeError) as e:
        validate_ai_hair_request(task(params))
    assert e.value.args[1] == RESULT_INVALID_PARAMETER


def test_effect_must_be_ai_hair():
    with pytest.raises(RuntimeError) as e:
        validate_ai_hair_request(task(effect="aiSong"))
    assert e.value.args[1] == RESULT_INVALID_PARAMETER


def test_src_keys_needs_exactly_one_photo():
    with pytest.raises(RuntimeError) as e:
        validate_ai_hair_request(task(srcKeys=[]))
    assert e.value.args[1] == RESULT_ERROR_EMPTY_SRC_KEYS
    with pytest.raises(RuntimeError) as e:
        validate_ai_hair_request(task(srcKeys=["a.jpg", "b.jpg"]))
    assert e.value.args[1] == RESULT_INVALID_PARAMETER


@pytest.mark.parametrize("dst_keys", [
    [],
    ["result.mp3"],
    ["a.jpg", "a.jpg"],
    [f"r{i}.jpg" for i in range(MAX_RESULTS + 1)],
])
def test_bad_dst_keys(dst_keys):
    with pytest.raises(RuntimeError) as e:
        validate_ai_hair_request(task(dstKeys=dst_keys))
    assert e.value.args[1] == RESULT_INVALID_PARAMETER


def test_dst_keys_set_the_results_wanted():
    keys = ["r0.jpg", "r1.PNG", "r2.webp"]
    assert validate_ai_hair_request(task(dstKeys=keys))["dst_keys"] == keys


def test_inference_style_overrides_feature_and_level():
    params = {
        "feature": "soft",
        "inference_style_list": [{"style_id": "s1", "items": [{"feature": "hairline", "level": 2,
                                                                "ignored": 1}]}],
    }
    request = validate_ai_hair_request(task(params))
    assert request["style_id"] == "s1"
    assert request["has_style_values"] is True
    assert request["applied_style_keys"] == ["feature", "level"]
    assert request["params"]["feature"] == "hairline" and request["params"]["level"] == 2
    assert "inference_style_list" not in request["params"]


def test_every_feature_is_a_hairfx_feature():
    from hairfx.features import EFFECTS

    assert tuple(EFFECTS) == FEATURE_NAMES
