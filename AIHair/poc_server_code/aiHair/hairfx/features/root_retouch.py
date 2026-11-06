"""rootRetouch: HairFeatures' rootRetouch_1, one images.edit call with
_PROMPT (its EFFECT_PROMPTS["rootRetouch_1"]): the roots recoloured over the
photo's strands, which stay where they are.

Its switches, as HairFeatures set them:
  colour match on, in the hair, with colour_shift: the hair the colour
      match's fit leaves out (the recoloured roots, mostly) is shifted as
      the hair round it instead of taken through the matrix, whose gain made
      the roots darker where dark and lighter where light;
  pasted back by HairShifted's composite by default (hairfx/common.py's
      shifted_composite: the renders lined up region by region, the face,
      the hair and the rest each by one translation, OpenCV in
      --face-python; pasted through the hair, the photo's and the render's;
      the face kept the photo's but for the hair), so not lined up by
      --align; --no-blend keeps the renders as drawn.
"""

from ..common import HairShifted, SinglePromptFeature


# HairFeatures' EFFECT_PROMPTS['rootRetouch_1'].
_PROMPT = ("只修改髮根那一段的顏色，而且只改顏色：這是在原圖的髮絲上直接補色，不是重畫頭髮，每一根髮絲都留在原圖的位置，粗細、形狀與走向都不變。其他一律不動：已染的髮身與髮尾的顏色，以及整頭的髮型與髮流，都和原圖一模一樣。\n"
    "\n"
    "任務：把靠近頭皮新長出的髮根（分線與髮際線附近顏色不同的那一段）染成與現有染髮完全一致的顏色：照片中已染髮身靠近髮根處的實際顏色。如果看不出染髮後新長出的髮根色差（沒有染髮，或髮根本來就和髮身同色），就什麼都不改，輸出和原圖一樣。\n"
    "\n"
    "做法：像後製時在原圖上疊一層只蓋住髮根的染色圖層。髮根處原圖的明暗紋理整個留下：亮的髮絲仍然亮，暗的髮絲與髮絲間的縫隙仍然暗，細髮仍然細，髮絲之間露出的頭皮或皮膚仍然露出；只把這一段的色相、飽和度與深淺調向髮身的顏色。不要先把髮根抹掉再畫一片新的頭髮。\n"
    "\n"
    "限制：\n"
    "- 臉與身分：同一個人；臉型、下顎與下巴、髮際線的位置與形狀、表情、視線與頭部角度都不變\n"
    "- 五官：眼睛、眉毛、鼻子、嘴唇、牙齒、耳朵各自的位置、大小、形狀與顏色，以及彼此之間的距離都不變；不重畫、不移動、不放大縮小、不改形狀，也不調得更對稱\n"
    "- 皮膚：臉、脖子、手的膚色、亮度、紅潤度、質感、毛孔、斑點、皺紋、痣與妝容都不變；不美顏、不磨皮、不美白\n"
    "- 身體、衣物與配件：脖子、肩膀、手臂、手與手指的形狀、姿勢與位置不變；每件衣服的顏色、圖案、質感、皺褶與邊緣不變；眼鏡、耳環、首飾、髮夾、髮箍、帽子等穿戴物的形狀、顏色與位置不變，包括它們在頭髮上的位置\n"
    "- 背景：背景裡每個物件、表面、邊緣、圖案與光影都在原位、保持原樣，顏色、亮度、清晰度、模糊程度與顆粒都不變；不新增、不移除、不移動、不延伸、不清理也不重畫任何東西；照片上的文字、貼圖、浮水印、邊框、黑邊或插入的小圖保持原樣，不要移除\n"
    "- 頭髮以外的光線與色彩：光源方向、陰影、曝光、白平衡、色溫、對比、飽和度、清晰度、雜訊顆粒、壓縮質感與景深完全不變；不調色、不重新打光、不降噪、不銳化、不重繪紋理；頭髮改色不可連帶讓臉、皮膚、衣服或背景變暖、變冷、變亮或變暗\n"
    "- 構圖：不縮放、不裁切、不位移、不旋轉、不重新構圖；頭髮以外的每個像素都和原圖逐像素對齊\n"
    "- 髮型鎖定：只改顏色，不改形狀。髮長、外輪廓、蓬鬆度與髮量、分線的位置與寬度、瀏海、層次、臉旁髮絲、波浪或捲度的大小與方向、髮絲走向與成束方式、質感、碎髮與毛躁，都和原圖一模一樣；補染髮根時不要順手梳順、整理、壓平、補髮或刪髮\n"
    "- 髮流鎖定（髮根一帶最容易被改動，特別注意）：頭頂、分線、髮旋與髮際線附近，每根髮絲從頭皮長出的位置、方向與彎曲，髮束的分組、粗細與前後交疊，都和原圖一模一樣；分線與髮旋露出的頭皮縫隙保持原本的位置、寬度與曲折，不要補滿、移動、拉直或變窄；不要把髮根梳成同一個方向，也不要讓頭頂變得更蓬、更平或更整齊\n"
    "- 額頭與臉旁的髮絲：落在額頭、眉毛、臉頰上的每一根髮絲與碎髮，位置、走向、長短與根數都不變，髮絲之間露出的皮膚也不變；濕髮或捲髮結成的髮束保持原本的形狀，不要梳開、梳順、合併或拿掉\n"
    "- 頭頂被畫面切掉時：髮根在畫面外看不到，就只處理畫面裡看得到的那一段；畫面上緣的頭髮在原本的位置被切掉，不要補畫、延伸或重新想像頭頂、分線或髮根，也不要移動或縮小人物來露出頭頂；畫面裡看不出髮根色差，就什麼都不改\n"
    "- 只處理上面那一段從頭皮開始的髮根。頭髮長短不一造成的深淺（瀏海、短層次、碎髮、髮際線的細髮或胎毛），以及光線、陰影、髮絲重疊造成的深淺，都是原本就有的，保持原樣，不要為了跟髮身一致而改色\n"
    "- 髮色鎖定：髮根以外的頭髮（已染的髮身與髮尾）顏色完全不變，色相、冷暖、明度、飽和度、光澤，以及挑染、漸層的位置都和原圖一樣；不要整頭重新上色，不要整頭加深、提亮、變暖、變冷或增加光澤，也不要把髮色調得更均勻\n"
    "- 髮根補染的顏色以照片中髮身靠近髮根處的實際顏色為準，只用來補髮根，不是要把整頭改成的新顏色\n"
    "- 髮根的明暗沿用原圖：原圖哪裡亮、哪裡暗，改色後仍是同樣的位置亮、同樣的位置暗；不要重新打光，也不要壓成一片平塗\n"
    "- 髮根與髮身之間只靠顏色漸層銜接：沿著同一根髮絲讓顏色逐漸接上髮身，不可出現明顯分界線，也不靠重畫、梳順或模糊髮絲來遮掉分界\n"
    "- 分線處露出的頭皮不要上色\n"
    "- 保持照片寫實，不要美顏或磨皮\n"
    "\n"
    "輸出前再確認：只有髮根那一段的顏色改變；每一根髮絲的位置、粗細與走向，分線與髮旋露出的頭皮縫隙，以及額頭與臉旁的每一根髮絲，都和原圖逐根一致；髮身與髮尾的顏色，以及整頭的髮型和外輪廓，都和原圖一模一樣；臉、五官、皮膚、身體、衣物與配件、背景，以及頭髮以外的光線、色彩與構圖，也都和原圖逐像素一致。")


class RootRetouchFeature(HairShifted, SinglePromptFeature):
    """rootRetouch: a SinglePromptFeature whose --blend is HairShifted's."""

    colour_match = True
    colour_shift = True
    blend_mask = "hair_shifted"
    blend_default = True

    def __init__(self):
        super().__init__("rootRetouch", _PROMPT)
