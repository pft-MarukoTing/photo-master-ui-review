"""aiHair's features, by the name a request's `feature` gives: hairfx's
EFFECTS (hairfx/features), one module each, under the same names.

hairline has two levels (request param `level`), its -o level option:
1 HairFeatures' hairline_simple_lv1, 2 its hairline_simple_lv1.5.
"""

from __future__ import annotations

FEATURE_NAMES = ("frizzCare", "plump", "fluffy", "hairline", "soft", "hairpart", "grayHair",
                 "rootRetouch", "strayHair")
# The features with levels, and the level a request without one gets.
DEFAULT_LEVEL = 1
LEVELLED = {"hairline": (1, 2)}
# A request's feature name matched ignoring case: "grayhair" is grayHair.
_BY_LOWER = {name.lower(): name for name in FEATURE_NAMES}


def canonical_feature(name: str) -> str | None:
    """`name` as FEATURE_NAMES spells it, matched ignoring case; None for no
    feature."""
    return _BY_LOWER.get(name.strip().lower())


def feature_options(feature: str, level: int | None = None) -> list[str]:
    """The hairfx -o KEY=VALUE pairs a request gives `feature`: a levelled
    feature's level (None, DEFAULT_LEVEL); none for the rest."""
    if feature not in LEVELLED:
        return []
    return [f"level={DEFAULT_LEVEL if level is None else level}"]
