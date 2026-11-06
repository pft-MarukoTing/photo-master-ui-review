"""aiHair's nine hair features, by name: EFFECTS, what main.py's --effect,
batch_main.py's --feature and the server's `feature` take.

Each is a Feature subclass (hairfx/core.py) in a module of its own, which
says all of it: its prompts, its steps and its switches, as class
attributes (blend_mask, blend_default, align_default, colour_match,
colour_match_area, colour_shift; Feature's docstring says what each does),
set as HairFeatures' features/__init__.py set them for the effect it was.
The features never import one another; what more than one shares is in
hairfx/common.py.

    frizzCare    frizz_care.py    HairFeatures' frizzCare_4 (with frizzCare_3)
    plump        plump.py         plumpFixHair_3.7
    fluffy       fluffy.py        fluffy
    hairline     hairline.py      hairline_simple_lv1 (-o level=1), _lv1.5 (-o level=2)
    soft         soft.py          soft_1
    hairpart     hairpart.py      hairPart
    grayHair     gray_hair.py     grayHairTwoStep
    rootRetouch  root_retouch.py  rootRetouch_1
    strayHair    stray_hair.py    strayHairWithFace
"""

from .fluffy import FluffyFeature
from .frizz_care import FrizzCareFeature
from .gray_hair import GrayHairFeature
from .hairline import HairlineFeature
from .hairpart import HairPartFeature
from .plump import PlumpFeature
from .root_retouch import RootRetouchFeature
from .soft import SoftFeature
from .stray_hair import StrayHairFeature

EFFECTS = {feature.name: feature for feature in (
    FrizzCareFeature(),
    PlumpFeature(),
    FluffyFeature(),
    HairlineFeature(),
    SoftFeature(),
    HairPartFeature(),
    GrayHairFeature(),
    RootRetouchFeature(),
    StrayHairFeature(),
)}
