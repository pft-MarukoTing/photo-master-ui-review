import json
import unittest

from photo_agent import chat_assistant
from photo_agent.chat_plan import apply_plan_patch, default_plan


def analysis():
    return {
        "photo_summary": "兩人在海邊合照。",
        "prompts": {"edit": "SECRET PROMPT"},
        "local_face_review": [{"detection_index": 1, "matched_subject_index": 0}],
        "_local_face_boxes": [{"box": {"x": 0.1, "y": 0.1, "w": 0.2, "h": 0.2}}],
        "_advanced_options": {},
        "retouch_recipe": ["提亮主體", "看向鏡頭", "裁成 4:5"],
        "subjects": [
            {"label": "左邊女性", "is_person": True,
             "gaze_issue": {"not_looking_at_lens_confidence": 0.6, "should_fix": False},
             "smile_issue": {"adjust_safe": True}},
            {"label": "狗", "is_person": False, "gaze_issue": {"should_fix": False}},
        ],
    }


def model_says(reply="好", action="none", patch=None, options=None, lines=False, your_pick=False):
    seen = []

    def model(context):
        seen.append(context)
        return {"reply": reply, "action": action, "patch_json": json.dumps(patch, ensure_ascii=False) if patch else "",
                "options": options or [], "lines": lines, "your_pick": your_pick}

    model.seen = seen
    return model


def ask(model, plan=None, history=None):
    history = history or [{"role": "user", "text": "幫我看一下", "card": {"type": "ask", "photo_id": "p1"}}]
    return chat_assistant.ask(analysis(), plan or default_plan(), "ready", history, model=model)


class AskTest(unittest.TestCase):
    def test_the_version_note_reaches_the_model(self):
        model = model_says()
        history = [
            {"role": "assistant", "text": "", "card": {"type": "render_done", "photo_id": "p1", "note": "臉有點平，要不要補一點陰影？"}},
            {"role": "user", "text": "好", "card": {"type": "ask", "photo_id": "p1"}},
        ]
        ask(model, history=history)
        self.assertIn("assistant: [version done] 臉有點平，要不要補一點陰影？", model.seen[0])

    def test_plain_reply(self):
        result = ask(model_says("這是一張海邊合照。"))
        self.assertEqual(result, {"reply": "這是一張海邊合照。", "render": False, "proposal": None, "options": [], "close": False, "lines": False})

    def test_words_for_the_photo_are_marked_as_lines(self):
        result = ask(model_says("幫你想了幾句，想放哪一句到照片上？", options=["乾杯剛好", "一起看球"], lines=True))
        self.assertTrue(result["lines"])
        # Nothing to pick from, nothing to lay out.
        self.assertFalse(ask(model_says("好", lines=True))["lines"])

    def test_a_question_back_keeps_its_answers(self):
        result = ask(model_says("想調整哪個部分呢？", options=["笑容", "笑容", "視線", "x" * 40]))
        self.assertEqual(result["options"], ["笑容", "視線"])

    def test_the_screen_reaches_the_model(self):
        model = model_says("可以按下方的「調整」。")
        chat_assistant.ask(analysis(), default_plan(), "ready", [{"role": "user", "text": "好", "card": {"type": "ask"}}],
                           model=model, screen={"bar": ["調整", "再美化"], "topics": []})
        self.assertIn('SCREEN: {"bar":["調整","再美化"],"topics":[]}', model.seen[0])

    def test_a_render_has_no_options(self):
        self.assertEqual(ask(model_says("馬上幫你美化", action="render", options=["一鍵美化"]))["options"], [])

    def test_winding_down_closes(self):
        result = ask(model_says("好的，有需要再跟我說喔！", action="close", options=["人像"]))
        self.assertEqual((result["close"], result["options"], result["render"]), (True, [], False))

    def test_render_with_no_change(self):
        self.assertTrue(ask(model_says("馬上幫你美化", action="render"))["render"])

    def test_a_change_is_proposed_not_rendered(self):
        result = ask(model_says("可以改成柔光", action="render", patch={"style_options": {"lighting": "soft"}}))
        self.assertFalse(result["render"])
        self.assertEqual(result["proposal"]["patch"], {"style_options": {"lighting": "soft"}})
        self.assertEqual(result["proposal"]["changes"], [
            {"field": "style_options.lighting", "label": "光線", "before": "不指定", "after": "柔光"}])

    def test_proposal_patch_applies_cleanly(self):
        patch = {"face_issue_overrides": {"0:gaze_issue": {"allowed": True}}, "recipe_checked": {"2": False},
                 "preset": "portrait", "advanced_options": {"smile_adjustment": False}}
        proposal = ask(model_says(patch=patch))["proposal"]
        plan, error = apply_plan_patch(default_plan(), proposal["patch"])
        self.assertIsNone(error)
        self.assertEqual(plan["recipe_checked"], [True, True, False])
        self.assertEqual({c["field"]: (c["before"], c["after"]) for c in proposal["changes"]}, {
            "advanced_options.smile_adjustment": ("開", "關"),
            "face_issue_overrides.0:gaze_issue": ("交給 AI 判斷", "修正"),
            "recipe_checked.2": ("套用", "不套用"),
        })
        self.assertIn("左邊女性", next(c["label"] for c in proposal["changes"] if c["field"].startswith("face")))

    def test_preset_is_never_adjustable(self):
        model = model_says("改成人像精修", patch={"preset": "portrait"})
        result = ask(model)
        self.assertIsNone(result["proposal"])
        self.assertNotIn('"preset"', model.seen[0])

    def test_bad_parts_are_dropped_and_the_rest_kept(self):
        patch = {"preset": "nope", "analysis_model": "gpt-4.1-mini", "style_options": {"lighting": "soft"},
                 "face_issue_overrides": {"1:gaze_issue": False, "0:gaze_issue": False}, "recipe_checked": {"9": False}}
        proposal = ask(model_says(patch=patch))["proposal"]
        self.assertEqual(proposal["patch"], {"style_options": {"lighting": "soft"},
                                             "face_issue_overrides": {"0:gaze_issue": False}, "recipe_checked": [True, True, True]})
        self.assertEqual([c["field"] for c in proposal["changes"]],
                         ["style_options.lighting", "face_issue_overrides.0:gaze_issue"])

    def test_a_patch_that_changes_nothing_is_no_proposal(self):
        plan = default_plan()
        plan["style_options"] = {"lighting": "soft"}
        result = ask(model_says("可以改成柔光，確認後套用", patch={"style_options": {"lighting": "soft"}}), plan=plan)
        self.assertIsNone(result["proposal"])
        self.assertEqual(result["reply"], chat_assistant.ALREADY_SET_REPLY)
        self.assertIsNone(ask(model_says(patch=None))["proposal"])
        broken = lambda context: {"reply": "x", "action": "none", "patch_json": "{not json"}
        self.assertIsNone(ask(broken)["proposal"])

    def test_user_prompt_wishes_merge_per_person(self):
        result = ask(model_says(patch={"user_prompt": "左邊女性的外套改成藍色；背景換成海邊；左邊女性把眼鏡拿掉"}))
        self.assertEqual(result["proposal"]["patch"]["user_prompt"],
                         "左邊女性的外套改成藍色、把眼鏡拿掉；背景換成海邊")

    def test_user_prompt_keeps_wishes_it_cannot_place(self):
        result = ask(model_says(patch={"user_prompt": "讓整張照片更清爽；光線柔和一點"}))
        self.assertEqual(result["proposal"]["patch"]["user_prompt"], "讓整張照片更清爽；光線柔和一點")

    def test_a_change_that_maps_to_nothing_is_not_claimed(self):
        result = ask(model_says("可以不動她的眼睛，確認後套用", action="render",
                                patch={"face_issue_overrides": {"0:眼睛": False}}))
        self.assertEqual(result, {"reply": chat_assistant.UNMAPPED_REPLY, "render": False, "proposal": None, "options": [], "close": False, "lines": False})

    def test_one_plain_setting_is_applied_without_asking(self):
        result = ask(model_says("可以改成柔光，確認後套用", patch={"style_options": {"lighting": "soft"}}))
        self.assertTrue(result["proposal"]["direct"])
        self.assertEqual(result["proposal"]["undo"], {"style_options": {"lighting": "default"}})
        self.assertEqual(result["reply"], "好，光線改成「柔光」了。")

    def test_the_reply_points_at_the_render_button(self):
        model = model_says(patch={"style_options": {"lighting": "soft"}})
        result = chat_assistant.ask(analysis(), default_plan(), "ready",
                                    [{"role": "user", "text": "柔光", "card": {"type": "ask"}}],
                                    model=model, screen={"bar": ["調整", "再美化"], "topics": []})
        self.assertEqual(result["reply"], "好，光線改成「柔光」了。按下方的「美化」我就開始。")

    def test_changes_the_ai_judged_still_ask(self):
        cases = {
            "two settings": {"style_options": {"lighting": "soft"}, "aspect_ratio": "original"},
            "a portrait fix": {"face_issue_overrides": {"0:gaze_issue": {"allowed": True}}},
            "a recipe step": {"recipe_checked": {"0": False}},
        }
        for name, patch in cases.items():
            with self.subTest(name):
                proposal = ask(model_says("確認後套用", patch=patch))["proposal"]
                self.assertNotIn("direct", proposal)

    def test_a_wish_that_only_adds_is_applied(self):
        plan = default_plan()
        plan["user_prompt"] = "左邊女性的外套改成藍色"
        result = ask(model_says(patch={"user_prompt": "左邊女性的外套改成藍色；左邊女性把眼鏡拿掉"}), plan=plan)
        self.assertEqual(result["proposal"]["patch"]["user_prompt"], "左邊女性的外套改成藍色、把眼鏡拿掉")
        self.assertTrue(result["proposal"]["direct"])
        self.assertEqual(result["proposal"]["undo"], {"user_prompt": "左邊女性的外套改成藍色"})
        self.assertEqual(result["reply"], "好，記下了：左邊女性把眼鏡拿掉。")

    def test_a_long_wish_is_not_read_back(self):
        result = ask(model_says(patch={"user_prompt": "把背景換成傍晚的海邊，天空要有粉紫色的晚霞，海面反射夕陽的金光"}))
        self.assertEqual(result["reply"], "好，已加進個人化需求。")

    def test_the_ais_own_pick_waits_for_the_card(self):
        plan = default_plan()
        plan["user_prompt"] = "在照片上加上文字「盛夏」"
        reply = "白字配淡淡的深藍陰影最好，在藍色水面前也看得清楚，確認後套用。"
        result = ask(model_says(reply, patch={"user_prompt": "在照片上加上文字「盛夏」；文字用白色加淡深藍陰影"},
                                your_pick=True), plan=plan)
        self.assertNotIn("direct", result["proposal"])
        self.assertEqual(result["reply"], reply)

    def test_a_wish_for_someone_else_asks(self):
        two = analysis()
        two["_local_face_boxes"].append({"box": {"x": 0.6, "y": 0.1, "w": 0.2, "h": 0.2}})
        two["local_face_review"].append({"detection_index": 2, "matched_subject_index": 1})
        two["subjects"][1] = {"label": "右邊男性", "is_person": True}
        plan = default_plan()
        plan["user_prompt"] = "左邊女性的衣服換成白色"

        def run(text, card, wish):
            history = [{"role": "user", "text": text, "card": {"type": "ask", "photo_id": "p1", **card}}]
            return chat_assistant.ask(two, plan, "ready", history,
                                      model=model_says(patch={"user_prompt": wish}))["proposal"]

        # Pointed at her, and the wish went to her: applied.
        self.assertTrue(run("上衣改成格紋", {"subject_indices": [0]},
                            "左邊女性的衣服換成白色、上衣改成格紋").get("direct"))
        # Pointed at her, but the model gave it to him and brought back an old wish.
        self.assertNotIn("direct", run("上衣改成格紋", {"subject_indices": [0]},
                                       "左邊女性的衣服換成白色、頭髮染成金色；右邊男性的上衣改成格紋"))
        # Pointed at no one, and wishes for two people appeared.
        self.assertNotIn("direct", run("上衣改成格紋", {},
                                       "左邊女性的衣服換成白色、上衣改成格紋；右邊男性的上衣改成格紋"))
        # Pointed at both: both get it.
        self.assertTrue(run("上衣改成格紋", {"subject_indices": [0, 1]},
                            "左邊女性的衣服換成白色、上衣改成格紋；右邊男性的上衣改成格紋").get("direct"))

    def test_answering_who_keeps_the_earlier_wishes(self):
        plan = default_plan()
        plan["user_prompt"] = "左邊女性的頭髮染成金色；背景換成海邊"
        history = [
            {"role": "user", "text": "衣服換成白色", "card": {"type": "ask", "photo_id": "p1"}},
            {"role": "assistant", "text": "", "card": {"type": "llm_reply", "text": "想把哪一位的衣服換成白色？",
                                                         "options": ["左邊女性", "右邊男性"]}},
            # The answer is the 頭像, tapped.
            {"role": "user", "text": "這位", "card": {"type": "ask", "photo_id": "p1", "subject_indices": [0]}},
        ]
        # The model hands back only the new wish.
        result = ask(model_says(patch={"user_prompt": "左邊女性的衣服換成白色"}), plan=plan, history=history)
        self.assertEqual(result["proposal"]["patch"]["user_prompt"],
                         "左邊女性的頭髮染成金色、衣服換成白色；背景換成海邊")
        self.assertTrue(result["proposal"]["direct"])
        # One it kept and one it dropped: that was a choice, so it asks.
        result = ask(model_says(patch={"user_prompt": "左邊女性的頭髮染成金色、衣服換成白色"}), plan=plan, history=history)
        self.assertNotIn("direct", result["proposal"])

    def test_a_wish_that_replaces_one_asks(self):
        plan = default_plan()
        plan["user_prompt"] = "左邊女性的外套改成藍色"
        result = ask(model_says(patch={"user_prompt": "左邊女性的外套改成紅色"}), plan=plan)
        self.assertNotIn("direct", result["proposal"])

    def test_a_picked_answer_is_applied(self):
        history = [
            {"role": "assistant", "text": "", "card": {"type": "llm_reply", "text": "想怎麼調？",
                                                         "options": ["柔光、維持原比例", "維持原樣"]}},
            {"role": "user", "text": "柔光、維持原比例", "card": {"type": "ask", "photo_id": "p1"}},
        ]
        patch = {"style_options": {"lighting": "soft"}, "aspect_ratio": "original"}
        result = ask(model_says(patch=patch), history=history)
        self.assertTrue(result["proposal"]["direct"])
        self.assertEqual(result["reply"], "好，已經幫你改好了。")
        # Typed, not picked: asks.
        history[-1]["text"] = "柔光、維持原比例吧"
        self.assertNotIn("direct", ask(model_says(patch=patch), history=history)["proposal"])

    def test_undo_puts_the_plan_back(self):
        plan = default_plan()
        plan["style_options"] = {"lighting": "gold"}
        plan["face_issue_overrides"] = {"0:smile_issue": {"allowed": False}}
        patch = {"style_options": {"lighting": "soft", "size": "close"},
                 "face_issue_overrides": {"0:smile_issue": {"allowed": True}, "0:gaze_issue": {"allowed": True}},
                 "user_prompt": "背景換成海邊"}
        changed, error = apply_plan_patch(plan, patch)
        self.assertIsNone(error)
        restored, error = apply_plan_patch(changed, chat_assistant._undo_patch(patch, plan))
        self.assertIsNone(error)
        for key in ("style_options", "face_issue_overrides", "user_prompt"):
            self.assertEqual(restored[key], plan[key], key)

    def test_context_leaves_out_prompts_and_carries_the_conversation(self):
        model = model_says()
        history = [
            {"role": "assistant", "text": "", "card": {"type": "analysis_brief", "photo_id": "p1",
                                                       "summary": "海邊合照。", "issues": ["偏暗", "裁成 4:5"]}},
            {"role": "assistant", "text": "", "card": {"type": "llm_reply", "text": "可以改柔光", "proposal": {
                "changes": [{"label": "光線"}]}}},
            {"role": "user", "text": "", "card": {"type": "proposal_resolved", "choice": "apply"}},
            {"role": "user", "text": "再亮一點", "card": {"type": "ask", "photo_id": "p1"}},
        ]
        ask(model, history=history)
        context = model.seen[0]
        self.assertNotIn("SECRET PROMPT", context)
        self.assertNotIn("_advanced_options", context)
        self.assertIn("兩人在海邊合照", context)
        self.assertIn('FACES: [{"panel":"人物 1","name":"左邊女性","keys":{"0:gaze_issue":"視線","0:smile_issue":"笑容"}}]',
                      context)
        self.assertNotIn("1:gaze_issue", context)
        self.assertTrue(context.rstrip().endswith("user: 再亮一點"))
        self.assertIn("assistant: 可以改柔光 [proposed: 光線]", context)
        self.assertIn("user: [applied the proposal above]", context)
        self.assertIn("assistant: [analysis summary shown] 海邊合照。 偏暗；裁成 4:5", context)

    # The upload page's conversation comes along into the session; a line
    # pointed there has a position, not a name.
    def test_a_line_from_the_upload_page_says_where_it_pointed(self):
        model = model_says()
        history = [
            {"role": "user", "text": "頭髮換成棕色", "card": {
                "type": "ask", "photo_id": "p1", "pre": True, "where": "照片中從左邊數來第 1 位"}},
            {"role": "assistant", "text": "", "card": {"type": "llm_reply", "pre": True, "text": "好，這個我記下來了。"}},
            {"role": "user", "text": "再亮一點", "card": {"type": "ask", "photo_id": "p1", "subject_index": 0}},
            {"role": "user", "text": "衣服換白色", "card": {
                "type": "ask", "photo_id": "p1", "pre": True,
                "faces": [{"where": "照片中從左邊數來第 1 位"}, {"where": "照片中從左邊數來第 2 位"}]}},
            {"role": "user", "text": "都笑一下", "card": {"type": "ask", "photo_id": "p1", "subject_indices": [0]}},
        ]
        ask(model, history=history)
        context = model.seen[0]
        self.assertIn("user: [pointing at 照片中從左邊數來第 1 位] 頭髮換成棕色", context)
        self.assertIn("assistant: 好，這個我記下來了。", context)
        self.assertIn("user: [pointing at 左邊女性] 再亮一點", context)
        self.assertIn("user: [pointing at 照片中從左邊數來第 1 位, 照片中從左邊數來第 2 位] 衣服換白色", context)
        self.assertTrue(context.rstrip().endswith("user: [pointing at 左邊女性] 都笑一下"))

    def test_no_analysis_yet(self):
        model = model_says(action="render")
        result = chat_assistant.ask(None, default_plan(), "analyzing",
                                    [{"role": "user", "text": "幫我修好", "card": {"type": "ask"}}], model=model)
        self.assertTrue(result["render"])
        self.assertIn("ANALYSIS: (not available yet)", model.seen[0])


class ReviewLineTest(unittest.TestCase):
    def review(self, verdict, reply=""):
        return chat_assistant.review_line(
            "幫我分析一下", "", model=lambda context: {"verdict": verdict, "reply": reply, "line": "", "options": []})

    def test_a_typed_analyze_starts_it_and_is_not_kept(self):
        result = self.review("analyze", "好，我先幫你分析這張照片。")
        self.assertEqual(result["start"], "analyze")
        self.assertFalse(result["kept"])
        self.assertFalse(result["offer"])

    def test_a_chip_name_needs_no_model(self):
        def model(context):
            raise AssertionError("the model was asked")
        self.assertEqual(chat_assistant.review_line("一鍵美化", "", model=model)["start"], "render")
        result = chat_assistant.review_line(" 分析。", "", model=model)
        self.assertEqual(result["start"], "analyze")
        self.assertFalse(result["kept"])

    def test_other_verdicts_do_not_start_analyze(self):
        for verdict in ("keep", "drop", "render", "offer", "answer"):
            self.assertNotEqual(self.review(verdict)["start"], "analyze", verdict)

    # 收下之後不停在「記下來了」：接著問還有沒有要改的，再指向底下的開始鍵。
    def test_a_kept_wish_asks_what_comes_next(self):
        def model(context):
            return {"verdict": "keep", "reply": "好，這個我記下來了。", "line": "", "options": [], "later": False}
        result = chat_assistant.review_line("外套換成藍色", "", model=model)
        self.assertTrue(result["kept"])
        self.assertEqual(result["reply"], chat_assistant.KEPT_REPLY + chat_assistant.KEPT_NEXT)

    def test_a_kept_wish_that_already_asks_is_left_alone(self):
        def model(context):
            return {"verdict": "keep", "reply": "好，記下來了。還有別的嗎？", "line": "", "options": [], "later": False}
        self.assertEqual(chat_assistant.review_line("外套換成藍色", "", model=model)["reply"], "好，記下來了。還有別的嗎？")


class DetailedExplanationTest(unittest.TestCase):
    def test_the_voice_is_only_there_when_turned_on(self):
        off, on = model_says(), model_says()
        plan = default_plan()
        plan["detailed_explanation"] = False
        ask(off, plan=plan)
        ask(on)
        self.assertNotIn(chat_assistant.VOICE, off.seen[0])
        self.assertIn(chat_assistant.VOICE, on.seen[0])


class RenderNoteTest(unittest.TestCase):
    def test_the_note_reads_what_the_plan_applied(self):
        seen = []

        def model(context, images):
            seen.append(context)
            self.assertEqual(images, [])
            return {"note": " 這一版重點放在提亮主體。 "}

        plan = default_plan()
        plan["recipe_checked"] = [True, False, True]
        self.assertEqual(chat_assistant.write_render_note(analysis(), plan, model=model), "這一版重點放在提亮主體。")
        self.assertTrue(seen[0].startswith(chat_assistant.RENDER_NOTE_INSTRUCTION))
        self.assertIn('{"text":"看向鏡頭","kept":false}', seen[0])
        self.assertNotIn("SECRET PROMPT", seen[0])

    def test_a_later_version_is_told_what_this_round_changed(self):
        seen = []
        before, after = default_plan(), default_plan()
        before["user_prompt"] = "改成白天"
        after["user_prompt"] = "改成白天；加上標題「台北」"
        chat_assistant.write_render_note(analysis(), after, previous_plan=before, earlier_notes=["裁成 4:5 了。", " "],
                                         model=lambda c, i: seen.append(c) or {"note": "x"})
        # Only the wish this round added, not the whole 個人化需求 again.
        self.assertIn('THIS ROUND: [{"label":"個人化需求","added":["加上標題「台北」"]}]', seen[0])
        self.assertIn("EARLIER NOTES:\n- 裁成 4:5 了。", seen[0])

    def test_a_first_version_says_so(self):
        seen = []
        chat_assistant.write_render_note(analysis(), default_plan(), model=lambda c, i: seen.append(c) or {"note": "x"})
        self.assertIn("THIS ROUND: (first version)", seen[0])
        self.assertIn("EARLIER NOTES:\n（無）", seen[0])

    def test_an_empty_note_is_none(self):
        self.assertIsNone(chat_assistant.write_render_note(analysis(), default_plan(), model=lambda c, i: {"note": " "}))

    def test_with_both_images_the_model_compares_them(self):
        seen = []

        def model(context, images):
            seen.append((context, images))
            return {"note": "天空的偏黃收掉了。"}

        note = chat_assistant.write_render_note(analysis(), default_plan(), before="data:a", after="data:b", model=model)
        self.assertEqual(note, "天空的偏黃收掉了。")
        context, images = seen[0]
        self.assertTrue(context.startswith(chat_assistant.RENDER_REVIEW_INSTRUCTION))
        self.assertIn("三分法（井字構圖）", context)
        self.assertEqual(images, [("BEFORE (the original photo):", "data:a"), ("AFTER (this version):", "data:b")])

    def test_one_image_is_not_a_comparison(self):
        seen = []
        chat_assistant.write_render_note(analysis(), default_plan(), before="data:a",
                                         model=lambda c, i: seen.append((c, i)) or {"note": "x"})
        self.assertEqual(seen[0][1], [])
        self.assertTrue(seen[0][0].startswith(chat_assistant.RENDER_NOTE_INSTRUCTION))


class OpeningTest(unittest.TestCase):
    def test_the_opening_speaks_from_the_recipe(self):
        seen = []

        def model(context):
            seen.append(context)
            return {"lead": " 海邊的光很好。 ", "points": ["把主體提亮。", "", "看向鏡頭。", "裁成 4:5。", "多一句。"]}

        brief = chat_assistant.write_opening(analysis(), model=model)
        self.assertEqual(brief, {"summary": "海邊的光很好。", "issues": ["把主體提亮。", "看向鏡頭。", "裁成 4:5。"]})
        self.assertIn("RECIPE:\n- 提亮主體\n- 看向鏡頭\n- 裁成 4:5", seen[0])
        self.assertNotIn("SECRET PROMPT", seen[0])
        self.assertIn("WISHES: （無）", seen[0])

    def test_the_opening_hears_the_users_wishes(self):
        seen = []
        chat_assistant.write_opening(analysis(), " 天空改成夕陽 ",
                                     model=lambda c: seen.append(c) or {"lead": "x", "points": ["y"]})
        self.assertIn("WISHES: 天空改成夕陽", seen[0])

    def test_no_recipe_no_model(self):
        def model(context):
            raise AssertionError("the model was asked")
        self.assertIsNone(chat_assistant.write_opening({"photo_summary": "海邊"}, model=model))

    def test_an_empty_answer_keeps_the_plain_brief(self):
        self.assertIsNone(chat_assistant.write_opening(analysis(), model=lambda c: {"lead": "", "points": ["x"]}))


if __name__ == "__main__":
    unittest.main()
