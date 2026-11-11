"""grayHair: HairFeatures' grayHairTwoStep. Its switches, as HairFeatures
set them: no colour match; pasted back by HairShifted's composite (the
renders lined up region by region, the face kept the photo's but for the
hair; hairfx/common.py) by default, so not lined up by --align.

--- HairFeatures' features/gray_hair_two_step.py ---

grayHairTwoStep: grayHair with the hair checked first. GPT looks for grey
hair in the photo, and only a photo with some goes on to the render.

grayHair alone sends every photo to the render model, which redraws the
whole head whatever the prompt says: on a photo with no grey hair it took
the highlights, sheen and light or dyed strands for grey and darkened them,
and it didn't follow the prompt's clause to leave such a photo as it is
(output/grayHair, 2026-10-28: 9 photos, hair 7.9 L* darker on average, the
grey head of subject4 too). Here, as rootretouch-two-step.py does for the
roots, the analyst decides first:

  1. The analyst (-o analyst_model) is given the photo, at most
     -o long_side on its long side, with _ANALYSIS_PROMPT: is there grey
     hair mixed into darker hair, where, how much of the hair, is the
     whole head grey, and if so, is that grey grown in, or a light colour
     dyed or bleached (platinum, light blonde) or blonde by nature. It
     answers in GrayReading's fields, judging grey hair by the prompt's
     strict criteria.
  2. Wherever it finds grey hair, 明確 or 可能, or the head grey all over
     and grown in, the render model edits the photo, for --n-images images
     (result_<i>): with grayHair's prompt (single_prompt.EFFECT_PROMPTS), or,
     for a head grey all over and grown in, with _WHOLE_GRAY_PROMPT, which
     grayHair's leaves as it is. A head light all over by dye or nature
     counts as not grey all over: _PROMPT where it has grey hair 明確 or
     可能, skipped where 沒有. A photo with grey hair 沒有 is skipped and
     left as it is, unless -o render_all.

With -o asks=N, step 1 is asked up to N times (core.ask_majority): the
photo goes on where more than half of the answers say so, and takes
_WHOLE_GRAY_PROMPT where more than half of those asked find the head grey
all over and grown in.

grayHairTwoStep_1 takes _WHOLE_GRAY_PROMPT for every photo that goes on,
whatever step 1 says of the whole head. grayHair's prompt changes grey hair
to the colour of the hair beside it, and where that is grey too it left it:
in output/grayHairTwoStep (2026-10-29) subject4's salt and pepper (85% grey,
the head read as not grey all over) kept 46-62% of its hair light and
colourless (L* > 50, chroma < 12), against 1.8-2.5% given
_WHOLE_GRAY_PROMPT (output_261028), and GettyImages-1214616393's result_3
the grey band along its hairline. Step 1 read subject4 grey all over in 2
to 5 of 5 asks, by model and size (output/detect_consistency, detect_cost),
its share of grey 80-95% each time. _WHOLE_GRAY_PROMPT is taken as it is,
though its 任務 says the hair is mostly grey already.

Billed calls: one analysis per photo, up to -o asks, and one render per
photo that goes on to step 2; no renders with -o analyze_only=true.
"""

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, Field

from ..common import HairShifted, SinglePromptFeature, _analysis_image
from ..core import Skip, ask_majority, edit, majority, option


# HairFeatures' EFFECT_PROMPTS['grayHair'], as of 2026-11-10 its v2 of
# output/grayHairPromptTune_261109 (_work/prompt_v2.txt): the grey recoloured
# to the nearby darker hair (the original colour), not the hair right beside
# it, grey gathered in a patch at the hairline, temples, parting or crown
# counted too, and none to be left — for GettyImages-1214616393's grey band
# along its hairline, which the prompt before left.
_PROMPT = ("只把夾雜在頭髮裡的白髮改回原本較深的髮色，其他一律不動：其餘頭髮的顏色、亮部與光澤，以及整頭的髮型，都和原圖一模一樣。\n"
    "\n"
    "任務：找出與整體髮色反差明顯的白髮或灰白髮（髮絲本身褪成銀白、灰白，一根根、一小撮，或一整片集中在髮際線、兩鬢、分線或頭頂，夾雜在較深的頭髮之間），把這些髮絲改成附近較深的頭髮（原本的髮色）在同樣光線下的顏色，色相、冷暖與深淺都照它們。每一根白髮都要改到，髮際線、兩鬢、分線、頭頂、髮身與髮尾裡的都不要漏掉。如果照片裡看不到這樣的白髮（沒有白髮，或整頭幾乎都是灰白、銀白，看不出原本較深的髮色），就什麼都不改，輸出和原圖一樣。\n"
    "\n"
    "以下都不是白髮，保持原樣，不要改色：\n"
    "- 光線造成的亮部：高光、光澤、反光、逆光輪廓光、髮絲被照亮的一面\n"
    "- 天生的淺色頭髮（金髮、淺棕髮、紅髮）、染髮、挑染、漸層染、較淺或褪色的髮尾\n"
    "- 髮絲重疊、陰影、景深模糊造成的深淺\n"
    "\n"
    "限制：\n"
    "- 臉與身分：同一個人；臉型、下顎與下巴、髮際線的位置與形狀、表情、視線與頭部角度都不變\n"
    "- 五官：眼睛、眉毛、鼻子、嘴唇、牙齒、耳朵各自的位置、大小、形狀與顏色，以及彼此之間的距離都不變；不重畫、不移動、不放大縮小、不改形狀，也不調得更對稱\n"
    "- 皮膚：臉、脖子、手的膚色、亮度、紅潤度、質感、毛孔、斑點、皺紋、痣與妝容都不變；不美顏、不磨皮、不美白\n"
    "- 身體、衣物與配件：脖子、肩膀、手臂、手與手指的形狀、姿勢與位置不變；每件衣服的顏色、圖案、質感、皺褶與邊緣不變；眼鏡、耳環、首飾、髮夾、髮箍、帽子等穿戴物的形狀、顏色與位置不變，包括它們在頭髮上的位置\n"
    "- 背景：背景裡每個物件、表面、邊緣、圖案與光影都在原位、保持原樣，顏色、亮度、清晰度、模糊程度與顆粒都不變；不新增、不移除、不移動、不延伸、不清理也不重畫任何東西；照片上的文字、貼圖、浮水印、邊框、黑邊或插入的小圖保持原樣，不要移除\n"
    "- 頭髮以外的光線與色彩：光源方向、陰影、曝光、白平衡、色溫、對比、飽和度、清晰度、雜訊顆粒、壓縮質感與景深完全不變；不調色、不重新打光、不降噪、不銳化、不重繪紋理；頭髮改色不可連帶讓臉、皮膚、衣服或背景變暖、變冷、變亮或變暗\n"
    "- 構圖：不縮放、不裁切、不位移、不旋轉、不重新構圖；頭髮以外的每個像素都和原圖逐像素對齊\n"
    "- 髮色鎖定：白髮以外的頭髮顏色完全不變，色相、冷暖、明度、飽和度、光澤，以及挑染、漸層的位置都和原圖一樣；不要整頭重新上色，不要整頭加深、變暗、提亮、變暖、變冷或增加光澤，也不要把髮色調得更均勻\n"
    "- 髮型鎖定：只改白髮的顏色，不改形狀。髮長、外輪廓、蓬鬆度與髮量、分線的位置與寬度、瀏海、層次、臉旁髮絲、波浪或捲度的大小與方向、髮絲走向與成束方式、質感、碎髮與毛躁，都和原圖一模一樣；改色時不要順手梳順、整理、壓平、補髮或刪髮\n"
    "- 改色後的髮絲保留原本的亮部與陰影，跟旁邊的頭髮遵循同樣的光源：照到光的地方仍然是亮的，不要壓成一片暗色或平塗\n"
    "- 改色的髮絲與周圍頭髮自然銜接，不可出現色塊或明顯分界，也不要留下零星的白髮\n"
    "- 分線處露出的頭皮不要上色\n"
    "- 保持照片寫實，不要美顏或磨皮\n"
    "\n"
    "輸出前再確認：白髮都已改成原本較深的髮色，沒有留下零星的白髮；只有白髮的顏色改變；其餘頭髮的顏色、亮部與光澤，以及整頭的髮型和外輪廓，都和原圖一模一樣；臉、五官、皮膚、身體、衣物與配件、背景，以及頭髮以外的光線、色彩與構圖，也都和原圖逐像素一致。沒有白髮的照片，輸出和原圖一樣。")

# Luna, sent the photo at most 720 on its long side, not hairline's 2048,
# for the cost: asked so, 5 times of each of the 37 photos of
# probe-detect-cost.py (output/detect_cost_models, 2026-11-02), it cost
# 4.9% of what Sol at 720 did, its 5 answers agreed on every photo, and it
# sent on the photos Sol at full size did but subject7 and subject21, whose
# faint grey (可能 to Sol) it read 沒有 every time. Some grey Sol calls 明確
# it calls 可能 (subject1, subject4), which goes on all the same. At 720
# Sol's first ask cost nearly a third less than at full size, and its
# verdicts held but subject21's (output/detect_cost). High-effort
# reasoning, as hairline's analyst.
_DEFAULT_ANALYST_MODEL = "gpt-6-luna"

# Luna, sent the photo at most 720 on its long side, not hairline's 2048,
# for the cost: asked so, 5 times of each of the 37 photos of
# probe-detect-cost.py (output/detect_cost_models, 2026-11-02), it cost
# 4.9% of what Sol at 720 did, its 5 answers agreed on every photo, and it
# sent on the photos Sol at full size did but subject7 and subject21, whose
# faint grey (可能 to Sol) it read 沒有 every time. Some grey Sol calls 明確
# it calls 可能 (subject1, subject4), which goes on all the same. At 720
# Sol's first ask cost nearly a third less than at full size, and its
# verdicts held but subject21's (output/detect_cost). High-effort
# reasoning, as hairline's analyst.
_DEFAULT_LONG_SIDE = 720
_DEFAULT_REASONING = "high"
_DEFAULT_DETAIL = "original"

# Step 1 asked up to 5 times (ask_majority), not once, as of 2026-11-10: Luna
# at 720 read 66DD7EE7029A51725791975's head grey all over in 4 of 5 asks
# and 2175377645's in 2 of 5 (output/grayHairPromptTune_261109), one ask
# deciding its route. 3 to 5 asks, where one took 7.9 s on average
# (output/grayHairCauseCheck_261110): the first alone, the rest at once
# (core.ask_majority).
_DEFAULT_ASKS = 5

# Step 1, sent with the photo; GrayReading's fields take its answers.
# Question 1 is judged by the criteria after them, which list what grayHair
# alone took for grey hair: light that falls on the hair, hair that is light
# by nature or dye, and fine hairs the light shows through. Question 5 sets
# a head light all over by dye or nature apart from one grey all over: a
# platinum blonde (samples_261102_gray's 66DD7EE7029A51725791975) Luna at
# 720 read grey hair 沒有 but the head grey all over in 4 of 5 asks
# (output/grayHairPromptTune_261109, 2026-11-09), and _WHOLE_GRAY_PROMPT
# turned it light brown; Sol at full size read it not grey all over.
_ANALYSIS_PROMPT = (
    "請觀察這張照片中人物的頭髮，不要修改圖片，只回答：\n"
    "1. 頭髮裡有沒有夾雜白髮或灰白髮？\n"
    "2. 白髮在哪些位置（頭頂、分線、髮際線、兩鬢、後腦、髮身、髮尾）？\n"
    "3. 白髮大約佔看得到的頭髮的百分之多少？\n"
    "4. 是不是整頭幾乎都是灰白或銀白，看不出原本較深的髮色？\n"
    "5. 如果第 4 題是：整頭的淺色看起來是自然長出來的白髮，還是刻意染成或漂成的白金、淺金等淺色，或天生的金髮？\n"
    "\n"
    "第 1 題從嚴判斷。算是白髮，要同時符合：\n"
    "- 髮絲本身褪成銀白、灰白，沒有顏色，不是因為照到光才看起來比較亮\n"
    "- 一根根或一小撮夾雜在較深的頭髮之間，和周圍的髮色反差明顯\n"
    "以下都不算：\n"
    "- 光線造成的亮部：高光、光澤、反光、逆光輪廓光、髮絲被照亮的一面\n"
    "- 天生的淺色頭髮（金髮、淺棕髮、紅髮）、染髮、挑染、漸層染、較淺或褪色的髮尾\n"
    "- 細髮、碎髮或髮際線的胎毛透光看起來比較淺\n"
    "- 分線處露出的頭皮\n"
    "- 照片壓縮、模糊或雜訊造成的淺色斑點\n"
    "\n"
    "第 5 題看髮色本身的色調與髮根：\n"
    "- 長出來的白髮：沒有顏色的灰、銀白，或略帶黃的白；常常深淺不一，夾雜灰色或較深的髮絲；髮根和髮身一樣灰白\n"
    "- 染色或天生淺色：帶金、香檳、奶油、冷白或粉彩等色調，整頭顏色均勻；髮根可能露出較深的原生髮色；或是天生的金髮、淺金髮"
)

# Step 2 for a head grey all over, where grayHair's prompt leaves the photo
# as it is: the grey goes to the colour of the darker strands still in it
# (subject4's salt and pepper), with grayHair's locks on the rest — as of
# 2026-10-29, the face, the features, the skin, the body, clothing and
# accessories, the background, the light and colour outside the hair and the
# framing each a 限制 line of its own (single_prompt's note).
_WHOLE_GRAY_PROMPT = (
    "把整頭的白髮與灰白髮改回原本較深的髮色，其他一律不動：整頭的髮型、亮部與光澤的位置，都和原圖一模一樣。\n"
    "\n"
    "任務：這張照片的頭髮大多已經是銀白、灰白，只夾雜少數較深的髮絲，那就是原本的髮色。把所有銀白、灰白的髮絲改成這些較深髮絲在同樣光線下的顏色，色相、冷暖與深淺都照它們，讓整頭回到原本的髮色；看不到較深的髮絲時，改成與眉毛相配的自然深色。\n"
    "\n"
    "限制：\n"
    "- 臉與身分：同一個人；臉型、下顎與下巴、髮際線的位置與形狀、表情、視線與頭部角度都不變\n"
    "- 五官：眼睛、眉毛、鼻子、嘴唇、牙齒、耳朵各自的位置、大小、形狀與顏色，以及彼此之間的距離都不變；不重畫、不移動、不放大縮小、不改形狀，也不調得更對稱\n"
    "- 皮膚：臉、脖子、手的膚色、亮度、紅潤度、質感、毛孔、斑點、皺紋、痣與妝容都不變；不美顏、不磨皮、不美白\n"
    "- 身體、衣物與配件：脖子、肩膀、手臂、手與手指的形狀、姿勢與位置不變；每件衣服的顏色、圖案、質感、皺褶與邊緣不變；眼鏡、耳環、首飾、髮夾、髮箍、帽子等穿戴物的形狀、顏色與位置不變，包括它們在頭髮上的位置\n"
    "- 背景：背景裡每個物件、表面、邊緣、圖案與光影都在原位、保持原樣，顏色、亮度、清晰度、模糊程度與顆粒都不變；不新增、不移除、不移動、不延伸、不清理也不重畫任何東西；照片上的文字、貼圖、浮水印、邊框、黑邊或插入的小圖保持原樣，不要移除\n"
    "- 頭髮以外的光線與色彩：光源方向、陰影、曝光、白平衡、色溫、對比、飽和度、清晰度、雜訊顆粒、壓縮質感與景深完全不變；不調色、不重新打光、不降噪、不銳化、不重繪紋理；頭髮改色不可連帶讓臉、皮膚、衣服或背景變暖、變冷、變亮或變暗\n"
    "- 構圖：不縮放、不裁切、不位移、不旋轉、不重新構圖；頭髮以外的每個像素都和原圖逐像素對齊\n"
    "- 髮色：只照原本較深的髮絲改色，不要換成別的髮色，不要加挑染、漸層或較淺的髮尾\n"
    "- 髮型鎖定：只改顏色，不改形狀。髮長、外輪廓、蓬鬆度與髮量、分線的位置與寬度、瀏海、層次、臉旁髮絲、波浪或捲度的大小與方向、髮絲走向與成束方式、質感、碎髮與毛躁，都和原圖一模一樣；改色時不要順手梳順、整理、壓平、補髮或刪髮\n"
    "- 改色後的頭髮保留原本的亮部、陰影與光澤，跟照片的光源一致：照到光的地方仍然是亮的，不要壓成一片暗色或平塗\n"
    "- 從髮根到髮尾顏色自然銜接，不可出現色塊或明顯分界，也不要留下零星的白髮\n"
    "- 分線處露出的頭皮不要上色\n"
    "- 保持照片寫實，不要美顏或磨皮\n"
    "\n"
    "輸出前再確認：白髮與灰白髮都改成了原本較深的髮色；整頭的髮型和外輪廓、亮部與光澤的位置，都和原圖一模一樣；臉、五官、皮膚、身體、衣物與配件、背景，以及頭髮以外的光線、色彩與構圖，也都和原圖逐像素一致。"
)


# Step 1's answers to _ANALYSIS_PROMPT, numbered as its questions. No
# docstring: pydantic would send it to the model as the schema's description.
class GrayReading(BaseModel):
    gray_hair: Literal["明確", "可能", "沒有"] = Field(description=(
        "1. 頭髮裡有沒有夾雜白髮，照題目後的標準從嚴判斷：「明確」= 兩條都符合，而且看得清楚；"
        "「可能」= 有淺色髮絲，但不完全符合或看不清楚；「沒有」= 沒有白髮，或只有不算的那幾種淺色。"
    ))
    gray_why: str = Field(description="1. 判斷依據：符合或不符合哪幾條標準。")
    gray_where: list[str] = Field(description=(
        "2. 白髮所在的位置：頭頂、分線、髮際線、兩鬢、後腦、髮身、髮尾中的哪些；"
        "第 1 題「沒有」就留空。"
    ))
    gray_percent: int = Field(description=(
        "3. 白髮佔看得到的頭髮的百分比，0–100 的整數；第 1 題「沒有」就填 0。"
    ))
    whole_head_gray: bool = Field(description=(
        "4. 整頭幾乎都是灰白或銀白：白色就是整體的髮色，看不出原本較深的髮色。"
    ))
    whole_head_cause: Literal["長出來的白髮", "染色或天生淺色", "不適用"] = Field(description=(
        "5. 第 4 題「是」時，整頭淺色的來源，照題目後的第 5 題標準判斷："
        "「長出來的白髮」= 自然長出的白髮、灰白髮；「染色或天生淺色」= 刻意染成或漂成的白金、"
        "淺金等淺色，或天生的金髮；第 4 題「否」就填「不適用」。"
    ))


@dataclass
class GrayHairOptions:
    render_all: bool = option(False, "go on to step 2 for every photo, not only where step 1 "
                                     "finds grey hair (明確 or 可能) or the head grey all over "
                                     "and grown in")
    analyze_only: bool = option(False, "step 1 only: read the photo, render nothing")
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


def _whole_gray(reading: GrayReading) -> bool:
    """Step 1 finds the head grey all over and that grey grown in, for
    _WHOLE_GRAY_PROMPT. A head light all over by dye or nature (question 5)
    counts as not grey all over; one read grey all over with question 5
    不適用 counts as grown in, as before question 5 was asked."""
    return reading.whole_head_gray and reading.whole_head_cause != "染色或天生淺色"


def _skip_reason(reading: GrayReading) -> str | None:
    """Why step 1's answers keep a photo from step 2, or None: step 2 takes
    any grey hair, 明確 or 可能, and a head grey all over and grown in."""
    if reading.gray_hair == "沒有" and not _whole_gray(reading):
        return "grey hair 沒有" + ("（整頭淺色是染色或天生淺色）" if reading.whole_head_gray else "")
    return None


class GrayHairFeature(HairShifted, SinglePromptFeature):
    """grayHair: HairFeatures' grayHairTwoStep (whole_gray_always off, as
    it ran): GPT reads the photo for grey hair (step 1); a photo with grey
    hair 明確 or 可能 gets _PROMPT's render, one grey all over and grown in
    _WHOLE_GRAY_PROMPT's, one with none (a head light all over by dye or
    nature included) is skipped; then pasted back by HairShifted's composite
    (hairfx/common.py) by default."""

    Options = GrayHairOptions
    blend_mask = "hair_shifted"
    blend_default = True

    def __init__(self):
        super().__init__("grayHair", _PROMPT)
        self.description = (
            "grayHair with GPT checking the hair first: a photo with grey hair 明確 or 可能 "
            "gets grayHair's render, one grey all over and grown in its own (one light all "
            "over by dye or nature as not grey all over); per photo up to -o asks analyst "
            "calls (5 by default, from 3 once the majority is settled), and 1 render call "
            "where it goes on")

    def prompts(self, opts) -> dict[str, str]:
        out = {"detect": _ANALYSIS_PROMPT}
        if not opts.analyze_only:
            out.update(super().prompts(opts))
            out["edit_whole_gray"] = _WHOLE_GRAY_PROMPT
        return out

    def process(self, ctx, photo, opts, result) -> None:
        result.step = "step 1 (grey hair)"
        readings, skip = ask_majority(ctx, result, "detect",
                                      _analysis_image(photo.image, grid=False,
                                                      long_side=opts.long_side),
                                      _ANALYSIS_PROMPT, GrayReading, model=opts.analyst_model,
                                      reasoning=opts.reasoning, detail=opts.detail, n=opts.asks,
                                      skip_reason=_skip_reason)
        result.info["has_gray_hair"] = skip is None
        whole_gray = majority(_whole_gray(r) for r in readings)
        if opts.analyze_only:
            found = "、".join(r.gray_hair for r in readings)
            raise Skip("analyze_only: " + (skip or f"grey hair {found}, would go on to step 2 ("
                                           + ("edit_whole_gray" if whole_gray else "edit") + ")"))
        if skip and not opts.render_all:
            raise Skip(skip)

        step, prompt = (("edit_whole_gray", _WHOLE_GRAY_PROMPT) if whole_gray
                        else ("edit", self.prompt))
        result.step = step
        renders = edit(ctx, result, step, [photo.file], prompt, photo.size,
                       n=self.n_images(ctx.render), keep_size=ctx.render.keep_rendered_size)
        for i, image in enumerate(renders):
            result.outputs[f"result_{i}"] = image
