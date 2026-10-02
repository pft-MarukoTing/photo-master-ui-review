"""Which output ratios auto mode may choose for a photo of a given shape.

Dependency-free so prompts.py and image_tools.py share one rule.
"""

import math
import re


# The closed set of output ratios the edit APIs can be asked for in auto mode.
# "original" (the source photo's own ratio) is always an extra candidate alongside these.
AUTO_ASPECT_RATIO_LABELS = (
    "1:1", "3:2", "2:3", "3:4", "4:3", "4:5", "5:4",
    "9:16", "16:9" #, "21:9", "1:4", "4:1", "1:8", "8:1",
)

# Least share of the source a crop to the new ratio must be able to keep:
# allows 2:3 -> 1:1 (67%), rejects 4:3 -> 4:5 (60%) and 2:3 -> 3:2 (44%).
AUTO_RATIO_MIN_RETAINED = 0.65

_AUTO_RATIO_LABELS_NEEDING_EXTREME_SOURCE = ("16:9", "9:16")
_SOURCE_RATIO_MATCH_TOLERANCE = 0.02  # relative, so 1.74 counts as "already 16:9"


def _label_ratio(label):
    w, h = re.fullmatch(r"(\d+):(\d+)", label).groups()
    return int(w) / int(h)


def _source_at_least_as_extreme(source_ratio, label_ratio) -> bool:
    """Is the source photo as far from square as this label, on the same side of
    square? Equal (within tolerance) counts as yes."""
    if not source_ratio or not label_ratio or source_ratio <= 0 or label_ratio <= 0:
        return False
    slack = math.log(1 + _SOURCE_RATIO_MATCH_TOLERANCE)
    source, label = math.log(source_ratio), math.log(label_ratio)
    if label > 0:  # landscape label -- the photo must be at least as wide
        return source >= label - slack
    return source <= label + slack  # portrait label -- at least as tall


def _retained_by_ratio_change(source_ratio, label_ratio) -> float:
    """Largest share of the source's area a crop to label_ratio can keep."""
    return min(label_ratio / source_ratio, source_ratio / label_ratio)


def allowed_auto_ratio_labels(source_ratio) -> tuple:
    """The auto-mode candidate labels for a photo of this shape: the full set minus
    the extremes the source isn't already as elongated as, and minus any ratio a
    crop can only reach by keeping less than AUTO_RATIO_MIN_RETAINED of the photo."""
    known_source = bool(source_ratio) and source_ratio > 0
    return tuple(
        label for label in AUTO_ASPECT_RATIO_LABELS
        if (label not in _AUTO_RATIO_LABELS_NEEDING_EXTREME_SOURCE
            or _source_at_least_as_extreme(source_ratio, _label_ratio(label)))
        and (not known_source
             or _retained_by_ratio_change(source_ratio, _label_ratio(label)) >= AUTO_RATIO_MIN_RETAINED)
    )
