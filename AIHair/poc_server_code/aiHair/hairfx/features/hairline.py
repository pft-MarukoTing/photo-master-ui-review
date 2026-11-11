"""hairline: HairFeatures' hairline_simple_lv1 (-o level=1, the default) and
hairline_simple_lv1.5 (-o level=2) as one feature, with the step 1 they
took from HairFeatures' hairline (_check_forehead and what it needs; the
rest of that mask pipeline is left out). Its switches, as HairFeatures set
them for both levels: colour match on, in the hair; lined up --align
regions_flow; pasted into the photo through the head mask by default
(--no-blend keeps the renders as drawn).

--- HairFeatures' features/hairline_simple.py ---

hairline_simple_lv1 and _lv2: hairline's step 1, then one render of the
photo with no mask, its prompt saying how far to lower the hairline in
words, kept as drawn — lined up with the photo, as every effect's outputs
are:

  1. As hairline (hairline._check_forehead), the photo sent at most
     -o long_side on its long side: the analyst checks how much of the
     forehead is covered and whether the brows show, and the photo is
     skipped where the hairline doesn't count (unless -o render_all=true);
     with -o asks=N it is asked up to N times, and the photo goes on where
     more than half of the answers say so.
  2. The render model lowers the hairline (result_<i>): _FILL_PROMPT with
     the level's task, how much and result (_LEVEL_TEXT): lv1 a fuller,
     slightly lower hairline, asked of a restrained retoucher; lv2 the
     hairline lower, asked first; lv0 lv1 with every word for how far
     "slightly", lv1.5 lv2 asking for about half as much, the face from the
     brows down held more firmly.

lv1's and lv1.5's results aren't kept as drawn by default but lined up
--align regions_flow and pasted into the photo through the head mask (the
hair and the face with its forehead from the render, the rest the photo's;
features' ALIGN_DEFAULTS, BLENDED and BLEND_MASKS).

Billed calls per photo: step 1, up to -o asks, and where it goes on, step
2's render (--n-images images).
"""

from dataclasses import dataclass

from pydantic import BaseModel, Field

from ..common import _ANALYSIS_LONG_SIDE, SinglePromptFeature, _analysis_image
from ..core import Skip, ask_majority, edit, majority, option


# Step 1: a hairline counts unless more than this share of the forehead is
# covered. At a third, it fell between the 30 and 35 the analyst answered
# most: asked 5 times, 7 of 37 photos went on some times and not others,
# each answering 30 or 35 (probe-detect-consistency.py,
# output/detect_consistency, 2026-10-30).
_MAX_COVERED_PERCENT = 50

# Steps 1 and 2: looping-prompt-hairline.py's analyst settings, high-effort
# reasoning with the photo seen at its full size.
_DEFAULT_REASONING = "high"

# Steps 1 and 2: looping-prompt-hairline.py's analyst settings, high-effort
# reasoning with the photo seen at its full size.
_DEFAULT_DETAIL = "original"

# Step 1, sent with the photo; HairlineReading's fields take its answers.
# Question 1 asks how much of the forehead is covered, not how much of the
# hairline shows: hair hanging at the temples hides much of the hairline
# but little of the forehead, and kept such photos from steps 2–4 when over
# half the hairline had to show. Bangs and strands over the forehead cover
# it even where skin shows between them; a few stray wisps don't.
_DETECT_PROMPT = (
    "請觀察這張照片中人物的額頭，不要修改圖片，只回答：\n"
    "1. 額頭被瀏海或其他東西蓋住的面積，佔整個額頭的百分之多少？\n"
    "2. 蓋住額頭的是什麼？\n"
    "3. 眉毛的上緣看不看得到？\n"
    "\n"
    "額頭是從前額髮際線（頭髮從頭皮長出、和額頭皮膚交界的那條線）往下到眉毛上緣、"
    "從一側太陽穴到另一側太陽穴的那一塊；被蓋住的地方，依露出來的髮際線和臉型推估它原本的範圍。"
    "第 1 題算的是面積：\n"
    "- 瀏海、垂下蓋到額頭上的髮束：蓋住的面積都算，即使皮膚從髮絲之間透出來\n"
    "- 帽子、頭巾、髮帶、髮箍、手等擋住額頭的也算\n"
    "- 零星幾根碎髮、髮際線上細短的胎毛不算\n"
    "- 往後梳、綁起來或旁分撥開、沒有蓋到額頭的頭髮不算"
)


# Step 1's answers to _DETECT_PROMPT, numbered as its questions. No
# docstrings on these models: pydantic would send them to the model as the
# schema's description.
class HairlineReading(BaseModel):
    covered_percent: int = Field(description=(
        "1. 額頭（前額髮際線到眉毛上緣、太陽穴到太陽穴）被瀏海、垂下的髮束或物品蓋住的面積，"
        "佔整個額頭的百分比，0–100 的整數，照題目後的標準判斷。"
    ))
    covered_why: str = Field(description="1. 判斷依據：額頭哪幾塊被蓋住、哪幾塊露出來。")
    covered_by: list[str] = Field(description=(
        "2. 蓋住額頭的是什麼，例如「瀏海」「垂下的髮束」「帽子」；額頭整塊都露出來就留空。"
    ))
    eyebrows_visible: bool = Field(description=(
        "3. 至少一道眉毛的上緣清楚可見，沒被瀏海、頭髮或物品遮住。"
    ))


def _skip_reason(reading: HairlineReading) -> str | None:
    """Why step 1's answers keep a photo from steps 2–4, or None: they need
    no more than _MAX_COVERED_PERCENT of the forehead covered, and a brow."""
    if reading.covered_percent > _MAX_COVERED_PERCENT:
        by = "、".join(reading.covered_by)
        return (f"forehead {reading.covered_percent}% covered, over "
                f"{_MAX_COVERED_PERCENT:.0f}%" + (f" (by {by})" if by else ""))
    if not reading.eyebrows_visible:
        return "brows hidden: nothing to measure the forehead down to"
    return None


def _check_forehead(ctx, photo, opts, result, long_side: int = _ANALYSIS_LONG_SIDE) -> None:
    """Step 1 for `photo`, sent at most `long_side` on its long side, with
    `opts`' analyst, asks and render_all: the forehead checked up to `asks`
    times (ask_majority), Skip unless more than half of the answers find it
    uncovered enough and a brow showing, unless render_all."""
    result.step = "step 1 (forehead)"
    readings, skip = ask_majority(ctx, result, "detect",
                                  _analysis_image(photo.image, grid=False, long_side=long_side),
                                  _DETECT_PROMPT, HairlineReading, model=opts.analyst_model,
                                  reasoning=opts.reasoning, detail=opts.detail, n=opts.asks,
                                  skip_reason=_skip_reason)
    result.info["has_hairline"] = majority(r.covered_percent <= _MAX_COVERED_PERCENT
                                           for r in readings)
    if skip and not opts.render_all:
        raise Skip(skip)


# Step 1: Luna, not hairline's Sol (which marks the brows too), sent the
# photo at most 720 on its long side, not hairline's 2048, for the cost:
# asked so, 5 times of each of the 37 photos of probe-detect-cost.py
# (output/detect_cost_models, 2026-11-02), it cost 4.6% of what Sol at 720
# did and sent on the photos Sol at full size did but 816a32e9, as Sol at
# 720 did. It reads the forehead as less covered, by 8 points on average, so
# a photo near _MAX_COVERED_PERCENT goes on more readily (816a32e9: 20–30%
# to Sol's 60–65%). At 720 Sol's first ask cost a quarter less than at full
# size, its repeats about the same (output/detect_cost).
_DEFAULT_ANALYST_MODEL = "gpt-6-luna"

# Step 1: Luna, not hairline's Sol (which marks the brows too), sent the
# photo at most 720 on its long side, not hairline's 2048, for the cost:
# asked so, 5 times of each of the 37 photos of probe-detect-cost.py
# (output/detect_cost_models, 2026-11-02), it cost 4.6% of what Sol at 720
# did and sent on the photos Sol at full size did but 816a32e9, as Sol at
# 720 did. It reads the forehead as less covered, by 8 points on average, so
# a photo near _MAX_COVERED_PERCENT goes on more readily (816a32e9: 20–30%
# to Sol's 60–65%). At 720 Sol's first ask cost a quarter less than at full
# size, its repeats about the same (output/detect_cost).
_DEFAULT_LONG_SIDE = 720

# Step 1 asked up to 5 times (ask_majority), not once, as of 2026-11-10, as
# grayHair's: a photo near _MAX_COVERED_PERCENT read one way one time and the
# other the next. 3 to 5 asks per photo: the first alone, the rest at once
# (core.ask_majority).
_DEFAULT_ASKS = 5

# Step 2's prompt. Neither the mask nor the drop a prompt asked for set how
# far the hairline moved: with hairline's steps 2–3's mask reaching 10% and
# 30% of the way to the brows, hairline-mask-fill-seg.py's prompt (asking
# for 50%) lowered it by about 26% and hairline-mask-fill-seg-enhance.py's
# (asking for no number) by about 46%, lower in each of 15 photos
# (output/hairline_simple_lv1, _lv2, median over the forehead's columns).
# What set it was the prompt's weight on the change: seg's is one line
# under some thirty of keeping things as they are ("a restrained hair
# retoucher"), enhance's comes first and says not to hold back. So this is
# enhance's, with no mask: how far said in words alone, and nothing about
# an area. Its renders flattened the hairline into a straight band
# (subject4), grew it nearly to the brows (subject24) and re-combed the hair
# above, so the new hairline is the old one's outline moved down, the hair
# keeps its combing, and no fine hairs are added along it (hairline's
# repair found them in too many renders).
#
# With only how much differing by level, lv1 asking for a little and lv2
# for a clear change, the levels all but met: lv1 about 26–37%, lv2 33–45%,
# 2–9 points lower in each of subject1, 4, 11 and 24
# (output/hairline_simple_nomask), against about 20 between seg's and
# enhance's — the task coming first pushed lv1 down further than seg's had,
# whatever how much said. So lv1's task is seg's restraint, said for the
# hairline (_LEVEL_TEXT): a restrained retoucher filling in a hairline and
# bringing it down slightly, the face keeping its look. With it lv1 lowered
# the hairline by about 13–26%, 15–25 points short of lv2 in each of the
# four (output/hairline_simple_nomask_v2). lv2 is now that first round's
# lv1, the task first and a small change asked for (its 26–37%), and no
# longer its lv2, a clear change asked for, the brows as its floor. On
# 2026-10-29 the face's features, the body, clothing and accessories, and
# the background spelled out, each with what of it stays (single_prompt's
# note).
_FILL_PROMPT = (
    "{task}\n"
    "\n"
    "HOW MUCH:\n"
    "{how_much}\n"
    "\n"
    "THE NEW HAIRLINE:\n"
    "- It is the person's own hairline moved down: the same outline — its curve across the "
    "forehead, its shape at the centre and its corners at the temples — lower by about the same "
    "amount all along it, not flattened into a straight, level or blunt line.\n"
    "- Everything between the old hairline and the new one becomes hair: cover it with dense "
    "hair rooted in the scalp, with no forehead skin showing through, the corners at the temples "
    "too.\n"
    "- The hair ends at the new hairline: below it, no bangs, fringe, wisps or strands hang or "
    "fall over the forehead or the temples, and the skin below it stays bare forehead skin, as "
    "it is now.\n"
    "- Its edge is as soft and slightly irregular as the person's hairline is now: not a "
    "straight, hard or wig-like edge, and no fuzz, haze or row of fine baby hairs added along "
    "it.\n"
    "\n"
    "THE NEW HAIR — the person's own hair, as if it had always grown there:\n"
    "- Growing in the same direction as the hair at the current hairline, as dense as the hair "
    "just behind it, with the same strand thickness, texture and wave or curl; the hair above it "
    "keeps its parting and the way it is combed.\n"
    "- The same colour as the roots next to it: hue, lightness, saturation and warmth, with "
    "their light and dark strands, highlights and grey or white hairs carried on; no darker, "
    "lighter, greyer, warmer or more even than the hair it grows from, so no line shows where "
    "the new hair meets the old.\n"
    "- Highlights and shadows that follow the photo's light, as the hair next to it does.\n"
    "\n"
    "THE EYEBROWS — the fixed reference, kept exactly as they are in this photo:\n"
    "- Where they are: each brow at the same height, the same distance above its eye and from "
    "the other brow, from its inner end to its tail; not raised, lowered or tilted to fit the "
    "new hairline.\n"
    "- Their shape: the same length, start and end, the same arch peaking at the same point, the "
    "same thickness and taper toward the tail, and the same colour, density and direction of "
    "their hairs; not redrawn, filled in, thinned, sharpened or straightened.\n"
    "- Fully in view: no hair, new or existing, falls over any part of either brow. At the "
    "temples the new hair stays above and beside the brows' outer ends, and their tails stay "
    "uncovered, as they are now.\n"
    "\n"
    "PRESERVE EXACTLY — everything but the new hair, unchanged:\n"
    "- Face and identity: the same person, with the same face shape below the hairline, jaw and "
    "chin, expression, gaze and head pose; the eyebrows (as above), eyes, nose, mouth and ears "
    "each keep their exact position, size, shape and colour and the spacing between them — not "
    "redrawn, moved, resized, reshaped or made more symmetrical.\n"
    "- Skin: the forehead below the new hairline and all other skin keep their tone, "
    "brightness, redness, texture, pores, freckles and wrinkles; no smoothing or beautifying.\n"
    "- The rest of the hair: its shape, length, parting, volume, outline, colour and light.\n"
    "- Body, hands, clothing and accessories: the neck, shoulders, arms, hands and fingers keep "
    "their shape, pose and position; every garment keeps its colour, pattern, texture, folds and "
    "edges; glasses, earrings, jewellery, hair clips, headbands, hats and anything else worn "
    "keep their shape, colour and position, including where they sit in or on the hair.\n"
    "- Background: every object, surface, edge, pattern, light and shadow in it stays exactly "
    "where and as it is, with the same colour, brightness, sharpness, blur and grain — nothing "
    "added, removed, moved, extended, cleaned up or re-drawn; any text, stickers, graphics, "
    "watermarks, borders, black bars or inset images stay as they are; do not remove them.\n"
    "- The photo's colour and light: white balance, exposure, contrast, sharpness and grain; no "
    "colour grading or relighting.\n"
    "- Framing: no zoom, crop, shift, rotation or reframing; apart from the new hair, the output "
    "lines up pixel-for-pixel with this photo.\n"
    "\n"
    "OUTPUT: one photorealistic photo identical to this photo except that {result}, with the "
    "eyebrows where and as they were — no collage, border, text or watermark."
)

# Level -> _FILL_PROMPT's {task}, {how_much} and {result}: lv1 seg's
# restraint (a retoucher's small change, filling in before lowering, the
# face keeping its look), lv2 enhance's task first with a small change
# asked for, which lowers it further. lv0 is lv1 with each word for how far
# brought down to "slightly" (a slight change, a thin line of new hair,
# nearly all of the forehead showing), and lv1.5 lv2 with each brought down
# to about half (slightly, visible rather than clearly so, nearly all of the
# forehead showing) and nothing from the brows down to change said in its
# task and result too, the brows not pushed toward the eyes: lv2's renders
# drew them nearer, by a median of 1.0 per 1000 px of the long side to lv1's
# 0.5 (output/hairline_simple_lv1, _lv2, FAN's landmarks).
# aiHair: only lv1 and lv1.5 are kept, as -o level 1 and 2.
_LEVEL_TEXT = {
    1: dict(
        task="Act as a restrained hair retoucher refining this person's front hairline: fill in "
             "the thin and sparse parts along it and bring it down slightly, so the hairline "
             "looks fuller and a little lower while the face keeps its look. Only the hairline "
             "changes: the eyebrows, and everything below them, stay exactly where they are. "
             "This is a local edit of an existing photo, not a new image.",
        how_much="A small, natural-looking change:\n"
                 "- Fill in first: where the hairline is thin, sparse or patchy, grow hair until "
                 "it is as full as the rest of the line.\n"
                 "- Then lower it only slightly: the new hairline sits just below the old one, a "
                 "narrow strip of new hair along it. Most of the forehead still shows, and its "
                 "shape and height read the same at a glance.\n"
                 "- Not a new look: someone who knows this person sees the same forehead and "
                 "face, with a fuller, slightly lower hairline. Do not leave the hairline exactly "
                 "where it is.",
        result="its hairline is fuller and slightly lower, as HOW MUCH asks"),
    2: dict(
        task="Lower this person's front hairline slightly: grow new hair down onto the top of "
             "the forehead, from one temple to the other, so the hairline sits slightly lower. "
             "The forehead gets shorter from the top only, and nothing from the eyebrows down "
             "changes: each brow stays exactly where and as it is, the same distance above its "
             "eye — not pushed down toward it, thickened or redrawn — and the eyes, nose, "
             "mouth, cheeks, jaw and skin below them are this photo's, not redrawn, smoothed, "
             "moved or reshaped. This is a local edit of an existing photo, not a new image.",
        how_much="A small, natural-looking change: bring the hairline down slightly — visible "
                 "next to the original photo, but nearly all of the forehead still shows and the "
                 "face keeps its look. Do not leave the hairline where it is.",
        result="its hairline is slightly lower, as much as HOW MUCH asks, and nothing from the "
               "eyebrows down has changed"),
}
_LEVEL_PROMPTS = {k: _FILL_PROMPT.format(**text) for k, text in _LEVEL_TEXT.items()}


@dataclass
class HairlineOptions:
    level: int = option(1, "how far: 1 HairFeatures' hairline_simple_lv1 (the hairline filled "
                           "in, fuller and slightly lower), 2 its hairline_simple_lv1.5 (lowered "
                           "slightly, nothing from the brows down to change)", choices=(1, 2))
    render_all: bool = option(False, "go on past step 1 even where the forehead is covered "
                                     "or the brows hidden")
    asks: int = option(_DEFAULT_ASKS, "times step 1 is asked at most: the photo goes on where more than "
                          "half of the answers say so, a tie not; the first alone, the rest "
                          "at once from the prompt cache, stopping once that is settled")
    analyst_model: str = option(_DEFAULT_ANALYST_MODEL, "step 1's model")
    reasoning: str = option(_DEFAULT_REASONING, "step 1's reasoning effort",
                            choices=("none", "low", "medium", "high", "xhigh", "max"))
    detail: str = option(_DEFAULT_DETAIL, "the image detail step 1 sees the photo at",
                         choices=("low", "high", "auto", "original"))
    long_side: int = option(_DEFAULT_LONG_SIDE, "the long side step 1 sees the photo at, at "
                                                "most (a smaller photo as it is)")

    def __post_init__(self):
        if self.asks < 1:
            raise ValueError(f"option asks: must be at least 1; got {self.asks}")
        if self.long_side < 1:
            raise ValueError(f"option long_side: must be at least 1; got {self.long_side}")


class HairlineFeature(SinglePromptFeature):
    """hairline: step 1 (_check_forehead), then one render with -o level's
    prompt; lined up region by region and pasted into the photo through
    the head mask, by default."""

    Options = HairlineOptions
    default_n_images = 4
    colour_match = True
    blend_mask = "head"
    blend_default = True
    align_default = "regions_flow"

    def __init__(self):
        super().__init__("hairline", _LEVEL_PROMPTS[1])
        self.description = (
            "hairline's check of the forehead, then one render, no mask, -o level 1 filling "
            "in the hairline and lowering it slightly, 2 lowering it about half as far as "
            "hairline_simple_lv2, nothing from the brows down to change; lined up region by "
            "region, its face part by part and by optical flow, and pasted into the photo "
            "through the hair and the face; per photo up to -o asks analyst calls (5 by "
            "default, from 3 once the majority is settled), and 1 render where it goes on")

    def prompts(self, opts) -> dict[str, str]:
        return {"detect": _DETECT_PROMPT, "edit": _LEVEL_PROMPTS[opts.level]}

    def process(self, ctx, photo, opts, result) -> None:
        _check_forehead(ctx, photo, opts, result, long_side=opts.long_side)
        result.step = "edit"
        renders = edit(ctx, result, "edit", [photo.file], _LEVEL_PROMPTS[opts.level],
                       photo.size, n=self.n_images(ctx.render),
                       keep_size=ctx.render.keep_rendered_size)
        for i, image in enumerate(renders):
            result.outputs[f"result_{i}"] = image
