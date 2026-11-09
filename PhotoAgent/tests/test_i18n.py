import pathlib
import re
import unittest

from photo_agent import capabilities, chat_assistant, i18n, i18n_en
from prompts import build_overall_analysis_instruction
from photo_agent import photo_router
from photo_agent.branch_skills.food import adapter as food_adapter, instructions as food_instructions

ROOT = pathlib.Path(__file__).resolve().parents[1]


def review(line, reply, verdict="keep", **extra):
    def model(context):
        model.context = context
        return {"verdict": verdict, "reply": reply, "line": "", "options": [], **extra}
    return chat_assistant.review_line(line, "", model=model), model


class SourceLanguageTest(unittest.TestCase):
    """Chinese is the source: nothing goes through a catalog."""

    def test_tr_returns_the_chinese(self):
        self.assertEqual(i18n.current(), i18n.SOURCE)
        self.assertEqual(i18n.tr("好，這個我記下來了。"), "好，這個我記下來了。")
        self.assertEqual(i18n.tr("換成 {ratio}", ratio="4:5"), "換成 4:5")
        self.assertEqual(i18n.label("設定"), "設定")

    def test_capability_labels_stay_chinese(self):
        caps = capabilities.describe()
        self.assertEqual(caps["render"]["aspect_ratios"][0]["label"], "AI 建議")
        self.assertEqual(caps["style_options"][0]["label"], "姿勢")

    def test_prompts_have_no_language_note(self):
        self.assertNotIn("LANGUAGE\n", build_overall_analysis_instruction())
        self.assertEqual(i18n.localized_prompt("Use Traditional Chinese (Taiwan)."), "Use Traditional Chinese (Taiwan).")

    def test_unknown_language_falls_back_to_chinese(self):
        for value in (None, "", "xx", "zh-TW", "xx-YY"):
            self.assertEqual(i18n.normalize(value), i18n.SOURCE)
        self.assertEqual(i18n.normalize("en-US"), "en")


class EnglishTest(unittest.TestCase):
    def setUp(self):
        self.lang = i18n.use("en")
        self.lang.__enter__()

    def tearDown(self):
        self.lang.__exit__(None, None, None)

    def test_capability_labels(self):
        caps = capabilities.describe()
        self.assertEqual(caps["render"]["aspect_ratios"][0]["label"], "AI Suggested")
        self.assertEqual(caps["style_options"][0]["label"], "Pose")
        self.assertEqual(caps["portrait_fixes"][0]["label"], "Closed Eyes")

    def test_prompts_ask_for_english(self):
        prompt = build_overall_analysis_instruction()
        self.assertIn("must be English", prompt)
        self.assertIn("「不適用」", prompt.split("LANGUAGE\n")[-1])
        chat = i18n.localized_prompt("Use Traditional Chinese (Taiwan), plain everyday words.")
        self.assertTrue(chat.startswith("Use English, plain everyday words."))
        self.assertIn("設定 = “Settings”", chat)

    def test_kept_reply_and_next_step(self):
        out, model = review("make the sky bluer", "")
        self.assertTrue(out["kept"])
        self.assertEqual(out["reply"], "OK, noted. Anything else you'd like to change? "
                                       "If not, pick “Analyze” or “One-Tap Retouch” below!")
        self.assertIn("ENGLISH PAGE", model.context)

    def test_a_drop_stays_dropped_in_english(self):
        # aa3caed3: a drop is not kept just because its reply sounds like "noted".
        out, _ = review("make the sky bluer", "Noted, I'll add that.", verdict="drop")
        self.assertFalse(out["kept"])

    def test_typed_start_button(self):
        self.assertEqual(review("one-tap retouch!", "")[0]["start"], "render")
        self.assertEqual(review("Analyze", "")[0]["start"], "analyze")

    def test_offer_options_and_question(self):
        out, _ = review("what would suit this photo?", "It depends on the light.", verdict="answer")
        self.assertEqual(out["options"], ["Analyze for Me", "One-Tap Retouch"])
        # Both start buttons are offered, so the question asks about both.
        self.assertTrue(out["reply"].endswith("Want me to analyze it first, or retouch it right away?"))

    def test_unknown_ratio_question_round_trip(self):
        asked, _ = review("make it 21:9 and brighten the background", "", asked_ratio="21:9")
        self.assertTrue(asked["ask"])
        self.assertTrue(asked["reply"].startswith("There's no 21:9 ratio. The closest option is “16:9 (Wallpaper)”."))
        self.assertEqual(asked["options"], ["Use 16:9", "Let the AI decide"])
        history = [{"role": "user", "text": "make it 21:9 and brighten the background"},
                   {"role": "assistant", "text": asked["reply"], "question": True, "options": asked["options"]}]
        swapped = chat_assistant.review_line("Use 16:9", "", history, model=lambda c: self.fail("no model"))
        # The ratio goes to 「設定」; only the rest stays a wish.
        self.assertEqual((swapped["aspect_ratio"], swapped["line"]), ("16:9", "brighten the background"))
        self.assertIn("set the aspect ratio to “16:9 (Wallpaper)”", swapped["reply"])
        dropped = chat_assistant.review_line("Let the AI decide", "", history, model=lambda c: self.fail("no model"))
        self.assertEqual(dropped["line"], "brighten the background")

    def test_change_reply_points_at_the_render_button(self):
        screen = {"bar": ["看美化項目", "美化"], "topics": []}
        self.assertEqual(chat_assistant._next_step(screen), "Tap “Retouch” below to start.")
        reply = chat_assistant._judged_reply("Dark brown suits the shelf, it applies once you confirm.", screen, True)
        self.assertEqual(reply, "Dark brown suits the shelf. I've added it to My Requests. You can edit it right below. "
                                "Tap “Retouch” below to start.")

    def test_options_that_name_a_button_are_dropped(self):
        screen = {"bar": ["美化"], "topics": ["設定", "風格"]}
        self.assertTrue(chat_assistant._points_at_screen("Want to try another look?", ["Open Settings"], screen))
        self.assertTrue(chat_assistant._points_at_screen("Want to open “Style”?", ["Sure", "No"], screen))
        self.assertFalse(chat_assistant._points_at_screen("Which color?", ["Red", "Blue"], screen))

    def test_wishes_are_merged_with_english_marks(self):
        analysis = {"subjects": [{"label": "Woman on the left", "is_person": True, "box": {"x": 0, "y": 0, "w": 1, "h": 1}}]}
        merged = chat_assistant._merge_prompt_wishes(
            "Woman on the left: blue jacket; Woman on the left: no glasses; Background: beach", analysis)
        self.assertEqual(merged, "Woman on the left: blue jacket, no glasses; Background: beach")


class AnalysisStepsLanguageTest(unittest.TestCase):
    """Call 0 and the food branch write user-visible text in the page's language too."""

    def test_chinese_prompts_are_untouched(self):
        self.assertEqual(i18n.localize_analysis(photo_router.ROUTER_INSTRUCTION), photo_router.ROUTER_INSTRUCTION)
        self.assertEqual(food_instructions.localized(food_instructions.ANALYSIS_INSTRUCTION),
                         food_instructions.ANALYSIS_INSTRUCTION)
        self.assertEqual(food_adapter.normalize(self._result())["review_items"][0]["recipe"], "水平／透視校正：tilted")

    def test_english_prompts_ask_for_english(self):
        with i18n.use("en"):
            router = i18n.localize_analysis(photo_router.ROUTER_INSTRUCTION)
            food = food_instructions.localized(food_instructions.ANALYSIS_INSTRUCTION)
            recipe = food_adapter.normalize(self._result())["review_items"][0]["recipe"]
        self.assertIn("photo_summary and scene_type are English", router)
        self.assertNotIn("Traditional Chinese", router + food)
        # The reframe prefix is read by the program, so it stays; the others are the user's.
        self.assertIn("重新構圖：", food)
        self.assertIn("Overall Look:", food)
        self.assertNotIn("移除干擾物", food.split("LANGUAGE\n")[0])
        self.assertEqual(recipe, "Straighten: tilted")

    @staticmethod
    def _result():
        return {"review_items": [], "alignment_strategy": {"state": "rotated", "deliberate": False, "decision": "tilted"}}


class ReframeTextTest(unittest.TestCase):
    """A reframing item rebuilt for a new ratio says its start once."""

    def test_english_start_is_taken_off(self):
        import image_tools
        self.assertEqual(image_tools._reframing_detail("重新構圖：change to 3:4. Trim the coats."), "Trim the coats.")
        self.assertEqual(image_tools._reframing_detail("重新構圖：keep the original ratio. Trim the coats."), "Trim the coats.")

    def test_chinese_is_unchanged(self):
        import image_tools
        self.assertEqual(image_tools._reframing_detail("重新構圖：調整為4:5，收窄左右邊界"), "收窄左右邊界")
        self.assertEqual(image_tools._aspect_ratio_reason("auto", "4:5", None, None, True), "AI 未提出裁切建議，維持原圖比例。")

    def test_a_chinese_reframe_item_falls_back_to_the_english_plan(self):
        import image_tools
        ctx = {"correction_plan": ["Trim the extreme-left balcony area while keeping her whole."],
               "correction_axes": [["crop"]], "correction_inverse": ["keep the frame"],
               "retouch_recipe": ["重新構圖：收窄左側多餘陽台空間，讓女性更集中。"]}
        box = {"x": 0.1, "y": 0, "w": 0.9, "h": 1, "enabled": True}
        with i18n.use("en"):
            out = image_tools._sync_reframing_review_contract(ctx, "4:3", box)
        self.assertEqual(out["retouch_recipe"][0],
                         "重新構圖：change to 4:3. Trim the extreme-left balcony area while keeping her whole.")
        # Chinese keeps the model's own Chinese item.
        zh = image_tools._sync_reframing_review_contract(ctx, "4:3", box)
        self.assertEqual(zh["retouch_recipe"][0], "重新構圖：調整為4:3，收窄左側多餘陽台空間，讓女性更集中。")

    def test_geometry_notes_in_english(self):
        import image_tools
        with i18n.use("en"):
            reason = image_tools._aspect_ratio_reason("4:5", "4:5", 0.8, None, False)
            plan = i18n.tr("以{ratio}輸出比例重新構圖；{detail}", ratio="4:5", detail="Trim the coats.")
            self.assertEqual(image_tools._reframing_detail(plan), "Trim the coats.")
        self.assertTrue(reason.startswith("The output ratio was changed to “4:5”"))
        self.assertNotRegex(reason, r"[一-鿿]")


class CatalogTest(unittest.TestCase):
    """Every fixed sentence the Python code passes to i18n.tr has English."""

    def test_every_tr_literal_has_a_translation(self):
        literal = re.compile(r"""i18n\.tr\(\s*(['"])(.*?)(?<!\\)\1""")
        missing = []
        for path in [*ROOT.glob("*.py"), *ROOT.glob("photo_agent/**/*.py"), *ROOT.glob("backend/**/*.py")]:
            for match in literal.finditer(path.read_text(encoding="utf-8")):
                if match.group(2) and match.group(2) not in i18n_en.TEXT and match.group(2) not in i18n_en.LABELS:
                    missing.append(f"{path.name}: {match.group(2)}")
        self.assertEqual(missing, [])

    def test_constants_passed_to_tr_have_a_translation(self):
        for text in (chat_assistant.KEPT_REPLY, chat_assistant.KEPT_NEXT, chat_assistant.OFFER_REPLY,
                     chat_assistant.CLARIFY_REPLY, chat_assistant.MERGED_NOTE, chat_assistant.WITHDRAWN_REPLY,
                     chat_assistant.UNASSIGNED, chat_assistant.ANALYZE_NUDGE, chat_assistant.UNMAPPED_REPLY,
                     chat_assistant.ALREADY_SET_REPLY, chat_assistant.ALREADY_SET_REDO, chat_assistant.CONTINUE_REPLY, chat_assistant.CONTINUE_ASK_REPLY,
                     chat_assistant.CONTINUATION_BUTTON, chat_assistant.CONTINUATION_NEXT, chat_assistant.RATIO_ASK_AI,
                     chat_assistant.RATIO_ASK_AI_REPLY, chat_assistant.RATIO_ASK_AI_REPLY_REST,
                     *chat_assistant.REVIEW_RATIOS.values()):
            self.assertIn(text, i18n_en.TEXT)

    def test_placeholders_match(self):
        for source, english in i18n_en.TEXT.items():
            self.assertEqual(set(re.findall(r"{(\w+)}", source)), set(re.findall(r"{(\w+)}", english)), source)


if __name__ == "__main__":
    unittest.main()
