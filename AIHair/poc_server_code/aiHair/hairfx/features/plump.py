"""plump: HairFeatures' plumpFixHair_3.7, one images.edit call with
_PROMPT_3_7, the render kept as drawn. Its switches, as HairFeatures set
them: no colour match; lined up --align global; --blend through the hair,
off by default. The module's other prompts (plumpFixHair, _1, _2, _3) are
left out.

--- HairFeatures' features/plump_fix_hair.py ---

plumpFixHair(_1/_2): plump with the hair grown outward only;
plumpFixHair_3(_3.7): more hair at the sides and on top, as
髮量豐盈 is defined.
One images.edit call: the photo and the effect's prompt (PROMPTS) — the hair grown outward round its outer
outline by about 2_Plump's amounts, as shares of the head's width, and the
hair inside it, the face and the rest as they are — for --n-images images
(result_<i>). The render is the result, as drawn: nothing is pasted back,
so keeping everything but the outline as it is rests on the prompt. main.py
lines it up with the photo outside the hair, as every render the model drew
whole (the render as drawn kept as debug/<photo>/result_<i>_raw.png where it
moved; --no-align leaves it as drawn).

rst_pub_by_feature's 2_Plump against 0_Original (the 20 photos with one,
the in-house hair model's masks; lengths in head widths W, the widest row of
the photo's hair in its top 30%, a median 0.56 of the short side):

  - The hair grows out round its outline as a band of about even width,
    and none of it is lost: its area x1.12 (1.05–1.23); the new hair's
    distance from the old outline 0.013 W (median), 0.032 W (90th
    percentile). The crown rises 0.029 W; each side stands out 0.051 W at
    the crown and temples (the top quarter W below the crown), 0.034 W
    from there to the shoulders and 0.022 W along the lengths below 1.5 W.
  - 79% of it is outward: beside the hair's outermost pixels in its row or
    above them in its column. The rest is mostly the inner edges of locks
    hanging over the chest (subject15), and a little fringe; none grows
    over the face.
  - It also redraws the hair inside the outline (|difference| 7.0 of 255
    in the hair's body, 3.8 darker; all of it for subject3, 4 and 5), as
    plump's own renders do more (output/plump, result_0: area x1.07, the
    hair inside 13.4 off and 7.2 darker). _PROMPT locks the hair inside.

plumpFixHair's renders (output/plumpFixHair, 2026-10-28, the same 20 photos,
all 79 results, measured the same way) grew about as much as 2_Plump: area
x1.126 (the largest of each photo's four, x1.145), the new hair's distance
0.039 W (90th percentile), 86% of it outward; the hair inside 10.5 off and
4.3 darker. plumpFixHair_1 (_PROMPT_1) asks for more than 2_Plump: each
amount about doubled, and "a little" and "moderate", which held it back,
made "clearly" and "strong". Its renders (output/plumpFixHair_1, the same
way, all 28 photos, 111 results) grew to x1.20, 0.061 W; each side out by
height below the crown, against 2_Plump's: 0.048 W (0.051) at the crown and
temples, 0.065–0.069 W (0.031–0.030) from the cheeks to the shoulders, and
0.043 and 0.039 W (0.019 and 0.016) from 1.5 and 2 W down, below the
shoulders. Below them it now asks for about 2%, tapering off.

plumpFixHair_2 (_PROMPT_2) is _1 with the head held in place where the hair
has no room (output/plumpFixHair_2, 2026-10-30, subject26, 0, 12 and 3, 4
results each; the face's parts against the global fit outside the face, as
main.py --align local measures them). subject26's head stays: its parts off
by a median 0.0–0.2 px, against _1's 0.4–15.3, its hair area x1.22–1.28
(x1.22–1.25). On the other three it grows as much or more, x1.16–1.35
(x1.14–1.26), the face as far off, 0.1–3.1 px (0.1–3.6). Those renders'
outer edge came frizzier than the photo's, subject0's most; since
2026-11-02 _PROMPT_2 asks for an edge with no frizz (its comment), not
rendered yet.

plumpFixHair_3 (_PROMPT_3) is 髮量豐盈 as rst_pub_by_feature/BeautyCam-
Hair-Feature-Study.pptx defines it (slides 3 and 8–11): 兩側與頭頂的髮量明顯
增加，原有捲度與染髮效果皆能保留 — more hair, where Fluffy's 頭頂蓬度提升
lifts it. 2_Plump does that: subject0's thin crown, its scalp showing,
filled in and its hair thicker throughout, subject3's and subject9's tops
fuller, subject5's and subject15's sides thicker. plumpFixHair, the hair
inside its outline locked, can only widen it, and didn't grow enough to
look fuller. _PROMPT_3 adds hair at the sides and top, between and
beneath the photo's locks and outside the outermost, with _1's amounts
round the outline, every lock the photo has kept where it lies, and an
edge of full locks with no fine hairs beyond it (its comment: its first
version, the hairstyle locked but not the strands, grew the whole hair
outward, Fluffy's look). Its second version's renders still redrew the
hair inside, so _PROMPT_3 asks for new hair only where there is none
(output_plump3v3, 2026-11-02); since then each amount is halved (its
comment; output_plump3v4, 2026-11-02).

plumpFixHair_3.7 (_PROMPT_3_7) is _3's hair added, round the outline
about 2.5 times as far, then nearly all the fuller hair's flyaways and
frizz cut, in the same call (its comment); not rendered yet.

Billed calls per photo: 1 render.
"""

from ..common import SinglePromptFeature


# _PROMPT_3's hair added first, round the outline about 2.5 times as far,
# then nearly all the fuller hair's flyaways and frizz cut, in the same
# call. _PROMPT_3's renders (output_plump3v4, 2026-11-02) grew the photo's
# frizz into the new hair: subject0's, subject6's and subject15's halo
# filled in as the hair's body, the new band's edge fuzzy, and subject4's
# arcs of flyaways round the crown a rim of short fuzz. So STEP 1 is
# _PROMPT_3's CHANGE ONLY less what it says of flyaways — its new locks
# "over its loose flyaways", no hair beyond the contour and the photo's
# flyaways covered — at 7.5, 12.5, 9 and 2.5% of the head's width,
# "clearly" and "slightly" where _PROMPT_3 says "moderate" and "very
# slight"; STEP 2 takes away or lays back every flyaway STEP 1 drew round
# its new locks and all but the odd short wisp of the photo's, re-lays
# nearly all the surface's crossing fine hairs, and says the tidying takes
# none of the added hair back. It keeps the strands' texture, not the
# frizz: a glossy, plastic result, its strands smoothed into one mass, is
# named wrong. How much is given only as "nearly all" and "the odd short
# wisp": asked to take all the frizz, before the hair was added, renders
# came out sleek (output/plumpFixHair_3.5, 2026-11-05: subject0's and
# subject6's halo gone to the last strand), and two thirds, four fifths
# and nine tenths, asked in so many words the same day, were dropped
# unrendered. "Reduce", never "lighten", which could be read as the
# hair's colour.
_PROMPT_3_7 = (
    "Edit ONLY the hair in this photo, in two steps, and leave every other pixel exactly as it is. First add hair at the sides and on top of the head, growing its outline clearly outward round the head, so the hair looks clearly fuller, in exactly the same hairstyle. Then reduce the fuller hair's flyaways and frizz — nearly all of them gone, only the odd short wisp left. The person stays exactly where they are in the frame. The photo's own hair stays exactly as it is, pixel for pixel, but for the frizz step 2 reduces — not pushed outward, lifted or puffed up: new hair goes only where there is no hair now — round the outline, and where the scalp or background shows through between locks — never over or in place of the photo's hair. Where the hair grows out over the background, shoulders or clothing, it joins seamlessly onto the hair beside it and meets the background at an edge of full locks. This is a local retouch of the hair of an existing photo, not a new image.\n"
    "\n"
    "STEP 1 — hair added at the sides and on top:\n"
    "- More hair in the gaps: at the sides, from the temples past the cheeks to the shoulders, and on top of the head, wherever the scalp or the background shows through between the photo's locks, hair partly fills it in, along the same flow as the locks beside it, so the hair is less see-through; thin patches on top are partly covered, the parting kept as a clear line where it is. Where there is hair already, nothing is added over it.\n"
    "- More hair round the outline: new locks are laid along the outside of the outermost locks, following their flow, so the outline grows — clearly round the head, slightly below the shoulders: the top of the head rises by about 7.5% of the head's width; the sides at the crown and temples stand out by about 12.5% of it; from the cheeks to the shoulders by about 9%. Below the shoulders the lengths and ends grow only slightly thicker, by about 2.5%, and keep their own width, fall and tapered ends.\n"
    "- Added, not moved: this is more hair, not a volumizing blow-dry. The photo's hair is not pushed outward, spread apart, lifted at the roots, teased or puffed up, and its outline is not stretched or scaled up: the new locks lie outside it. Every lock of the photo's hair is left exactly as it is, as if the new hair were painted only onto the empty space round and between it.\n"
    "- The added hair is the same hair: the same colour, any dye, highlights, lowlights or balayage in the same places, the same lightness, shine and shadows, the same strand direction and the same wave or curl pattern and size; it continues each lock and strand group beside it, dense and cohesive along the hair's flow, not a thin haze of loose, frizzy or crossing fibres.\n"
    "- Full locks to the edge: the outermost new hair is full, thick locks, as dense and opaque as the locks inside, whose sides and ends form one continuous contour following the locks' natural shape — not a hard cut-out line, and not feathered into fine hairs.\n"
    "- Grow outward only, away from the head and body: never over the face, forehead or ears, and not into the gaps between the hair and the neck, body or arms.\n"
    "- Where there is little room — the hair already reaches the edge of the image, or another person's hair or body is close beside it — let the new hair run out past the image edge, or meet and tuck behind the other person's hair; never move, shift, shrink or re-centre the person's head or body to make room for it.\n"
    "\n"
    "STEP 2 — the fuller hair's flyaways and frizz reduced:\n"
    "- Flyaways and halo, nearly all: of the flyaways, fine hairs and frizzy halo standing out of the fuller hair's outline — at the crown, the sides and along the lengths — nearly all are taken away or laid back along their flow into the lock they come from: every one that step 1 drew round its new locks, and of the photo's own all but the odd short wisp close to the outline. The halo is gone; the edge is the locks' own, clean but natural, not cut out. Where one is taken away, the background, skin or clothing behind it shows, continuing as it does round it.\n"
    "- Surface fuzz, nearly all of it: of the short, broken, kinked and crossing fine hairs on the hair's surface — at the crown, the parting and between strand groups — nearly all are laid back along the flow of the strand group they lie on, and only the odd one stays, so the surface is smooth and orderly, its strands still clearly drawn — not sleek, glossy or polished. Every strand stays a crisp, clearly drawn line: re-laid, not blurred, softened or painted over into a plain mass.\n"
    "- Only the frizz, nearly all of it: only the odd short wisp along the edge stays where it is. The locks and strand groups, step 1's new locks, the waves and curls, the fuller outline, the parting, the bangs and the colour stay as they are; the hair is not re-combed, straightened, flattened or restyled. Strands that hang on their own as part of the hairstyle — face-framing strands, the bangs' tips, a few strands slipped out of a ponytail or bun — are not frizz and stay as they are. What stays is the strands' own texture: a result whose hair is glossy, plastic or painted, its strands smoothed into one mass, is wrong.\n"
    "- Still fuller: reducing the frizz takes none of step 1's added hair back. Side by side with this photo, the hair is clearly fuller — wider at the sides, higher on top, less scalp showing through — and much tidier, its strands still clearly drawn.\n"
    "\n"
    "PRESERVE EXACTLY — do not change any of these in any way:\n"
    "- The photo's hair, strand by strand, but for the frizz step 2 reduces: every strand, lock, wave and curl it has keeps its exact place, path, direction, shape, colour and texture — the hair's flow is this photo's; nothing of it is moved, pushed outward, spread, lifted, re-combed, straightened, curled or redrawn larger. The flyaways, halo and surface fuzz step 2 takes away or re-lays — nearly all of them — are the only hair changed. Added hair lies only where there was none — round the outline and in its gaps — never over or in place of it.\n"
    "- The hairstyle: the haircut and length, the parting's place and direction, the hairline, the bangs and their length and shape, the layers, the face-framing strands, and the size and direction of every wave or curl; more hair is added within this same hairstyle — it is not restyled, recut or re-parted.\n"
    "- The hair's colour: hue, saturation and lightness, and where any dye, highlights, lowlights or balayage sit; denser and tidier hair keeps this photo's colour and lightness — the hair is not darkened, lightened or made glossier overall.\n"
    "- Face and identity: the same person, with the same face shape, jaw and chin, expression, gaze and head pose; the eyes, eyebrows, nose, lips, teeth and ears each keep their exact position, size, shape and colour and the spacing between them — not redrawn, moved, resized, reshaped or made more symmetrical.\n"
    "- Where the head is: the head, face and neck keep their exact place in the frame and on the shoulders — not moved left, right, up or down, leaned, tilted or turned; the face is as far from each edge of the image, and from anyone beside the person, as in this photo.\n"
    "- Skin: tone, brightness, redness, texture, pores, freckles, wrinkles, moles and makeup; no smoothing, retouching or beautifying.\n"
    "- Body, hands, clothing and accessories, apart from where the fuller hair now lies over them: the neck, shoulders, arms, hands and fingers keep their shape, pose and position; every garment keeps its colour, pattern, texture, folds and edges; glasses, earrings, jewellery, hair clips, headbands, hats and anything else worn keep their shape, colour and position.\n"
    "- Background, apart from where the fuller hair now covers it or the flyaways taken away uncover it: every object, surface, edge, pattern, light and shadow in it stays exactly where and as it is, with the same colour, brightness, sharpness, blur and grain — nothing added, removed, moved, extended, cleaned up or re-drawn; any text, stickers, graphics, watermarks, borders, black bars or inset images stay as they are; do not remove them.\n"
    "- Other people, even only partly in the photo (at its edges, behind or beside the person): their hair, face, hands and clothing stay exactly as and where they are; only the main person's hair changes.\n"
    "- Light and colour outside the hair: light direction, shadows, exposure, white balance, contrast, sharpness, grain, compression texture and depth of field; no denoising, sharpening or re-drawn textures.\n"
    "- Framing: no zoom, crop, shift, rotation or reframing; outside the hair and the area it now reaches, the output lines up pixel-for-pixel with this photo.\n"
    "\n"
    "OUTPUT: one photorealistic photo identical to this photo except for added hair at the sides and on top, its outline grown clearly outward round the head, added only round the outline and in the gaps of the photo's own hair, which stays exactly as it is, in the same hairstyle, colour and curl, ending in full locks; and then the flyaways and frizz reduced, nearly all of them gone, only the odd short wisp still there, the strands still clearly drawn — no collage, border, text or watermark."
)


class PlumpFeature(SinglePromptFeature):
    """plump: one images.edit call, the photo and _PROMPT_3_7."""

    blend_mask = "hair"

    def __init__(self):
        super().__init__("plump", _PROMPT_3_7,
                         description="plumpFixHair_3 grown about 2.5 times as far round the "
                                     "outline, then nearly all the fuller hair's flyaways and "
                                     "frizz cut, in the same call, the render as drawn")
