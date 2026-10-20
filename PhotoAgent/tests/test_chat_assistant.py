import json
import types
import unittest
from unittest.mock import patch

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
        self.assertEqual(result, {"reply": "這是一張海邊合照。", "render": False, "proposal": None, "options": [], "close": False, "lines": False, "open": ""})

    def test_words_for_the_photo_are_marked_as_lines(self):
        result = ask(model_says("幫你想了幾句，想放哪一句到照片上？", options=["乾杯剛好", "一起看球"], lines=True))
        self.assertTrue(result["lines"])
        # Nothing to pick from, nothing to lay out.
        self.assertFalse(ask(model_says("好", lines=True))["lines"])

    def test_a_question_back_keeps_its_answers(self):
        result = ask(model_says("想調整哪個部分呢？", options=["笑容", "笑容", "視線", "x" * 40]))
        self.assertEqual(result["options"], ["笑容", "視線"])

    def test_the_screen_reaches_the_model(self):
        model = model_says("可以按下面的「看美化項目」。")
        chat_assistant.ask(analysis(), default_plan(), "ready", [{"role": "user", "text": "好", "card": {"type": "ask"}}],
                           model=model, screen={"bar": ["看美化項目", "再美化"], "topics": []})
        self.assertIn('SCREEN: {"bar":["看美化項目","再美化"],"topics":[]}', model.seen[0])

    def test_what_stays_the_same_is_sent_as_cached_blocks(self):
        sent = []
        post = types.SimpleNamespace(__wrapped__=lambda url, payload, label, timeout=None: sent.append(payload) or {
            "output_text": '{"reply":"好","action":"none","patch_json":""}'})
        with patch.object(chat_assistant, "_post_json", post):
            chat_assistant.call_model(chat_assistant.build_context(
                analysis(), default_plan(), "ready", [{"role": "user", "text": "好", "card": {"type": "ask"}}]))
            chat_assistant.call_review("x")
        payload = sent[0]
        self.assertEqual(payload["prompt_cache_options"], {"mode": "explicit"})
        developer, user = payload["input"]
        self.assertEqual(developer["role"], "developer")
        self.assertTrue(developer["content"][0]["text"].startswith(chat_assistant.INSTRUCTION))
        self.assertTrue(developer["content"][1]["text"].startswith("ANALYSIS:"))
        self.assertTrue(all(b["prompt_cache_breakpoint"] == {"mode": "explicit"} for b in developer["content"]))
        self.assertTrue(user["content"][0]["text"].startswith("PHOTO STATUS:"))
        self.assertNotIn("cache-break", json.dumps(payload, ensure_ascii=False))
        # A context with nothing marked goes as it always did, one message.
        self.assertNotIn("prompt_cache_options", sent[1])
        self.assertEqual([p["prompt_cache_key"] for p in sent],
                         ["photoagent-chat_assistant_reply", "photoagent-chat_prompt_review"])

    def test_the_review_keeps_its_instructions_ahead_of_the_line(self):
        model = model_says()
        chat_assistant.review_line("背景換成海邊", "", model=lambda context: model.seen.append(context) or {
            "verdict": "keep", "reply": "", "line": "", "options": []})
        stable, rest = model.seen[0].split(chat_assistant.CACHE_BREAK)
        self.assertIn(chat_assistant.REVIEW_SEMANTIC_CONTRACT, stable)
        self.assertTrue(rest.endswith("NEW LINE:\n背景換成海邊"))

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
        self.assertEqual(result, {"reply": chat_assistant.UNMAPPED_REPLY, "render": False, "proposal": None, "options": [], "close": False, "lines": False, "open": ""})

    def test_one_plain_setting_is_applied_without_asking(self):
        result = ask(model_says("可以改成柔光，確認後套用", patch={"style_options": {"lighting": "soft"}}))
        self.assertTrue(result["proposal"]["direct"])
        self.assertEqual(result["proposal"]["undo"], {"style_options": {"lighting": "default"}})
        self.assertEqual(result["reply"], "好，光線改成「柔光」了。")

    def test_the_reply_points_at_the_render_button(self):
        model = model_says(patch={"style_options": {"lighting": "soft"}})
        result = chat_assistant.ask(analysis(), default_plan(), "ready",
                                    [{"role": "user", "text": "柔光", "card": {"type": "ask"}}],
                                    model=model, screen={"bar": ["看美化項目", "再美化"], "topics": []})
        self.assertEqual(result["reply"], "好，光線改成「柔光」了。按下面的「美化」我就開始。")

    def test_while_adjusting_the_reply_also_points_at_the_topics(self):
        model = model_says(patch={"user_prompt": "頭巾換成紫色"})
        result = chat_assistant.ask(analysis(), default_plan(), "ready",
                                    [{"role": "user", "text": "頭巾換成紫色", "card": {"type": "ask"}}],
                                    model=model, screen={"bar": ["美化"], "topics": ["人像", "畫面"]})
        self.assertTrue(result["proposal"]["direct"])
        self.assertTrue(result["reply"].endswith("還想調整其他項目可以點下面的項目，或按「美化」我就開始。"))

    def test_a_switch_change_gets_no_card_so_the_reply_says_where_to_change_it(self):
        # 開關、選項直接套用不畫卡，要改就點底下的美化項目。
        model = model_says(patch={"style_options": {"lighting": "soft"}})
        result = chat_assistant.ask(analysis(), default_plan(), "ready",
                                    [{"role": "user", "text": "改成柔光", "card": {"type": "ask"}}],
                                    model=model, screen={"bar": ["美化"], "topics": ["人像", "畫面"]})
        self.assertEqual(result["reply"],
                         "好，光線改成「柔光」了。要修改或調整其他項目，可以點下面的美化項目，或按「美化」我就開始。")

    def test_after_a_change_the_first_round_button_is_just_render(self):
        # 第一輪動過東西，底下那顆就從「照建議直接美化」變成「美化」。
        model = model_says(patch={"user_prompt": "背景改成室外"})
        result = chat_assistant.ask(analysis(), default_plan(), "ready",
                                    [{"role": "user", "text": "背景改成室外", "card": {"type": "ask"}}],
                                    model=model, screen={"bar": ["照建議直接美化"], "topics": ["人像", "畫面"]})
        self.assertTrue(result["reply"].endswith("還想調整其他項目可以點下面的項目，或按「美化」我就開始。"))

    def test_options_that_stand_for_a_button_on_screen_are_dropped(self):
        # 選項只能把字送回來；「打開設定」點了也開不了面板，底下的按鈕才是答案。
        screen = {"bar": ["照建議直接美化"], "topics": ["人像", "風格", "設定"]}
        history = [{"role": "user", "text": "我要調整美化引擎", "card": {"type": "ask"}}]

        def said(reply, options):
            return chat_assistant.ask(analysis(), default_plan(), "ready", history, screen=screen,
                                      model=lambda c: {"reply": reply, "action": "none", "patch_json": "", "options": options})

        # 選項就是按鈕，不管問句怎麼寫。
        for options in (["打開設定", "先不調整"], ["看看「風格」", "不用"], ["照建議直接美化", "再想想"]):
            self.assertEqual(said("要先調整嗎？", options)["options"], [], options)
        # 問句在請人按畫面上的按鈕，選項換了說法也一樣。
        for reply in ("可以到「設定」裡調整。要不要打開「設定」看看可用的模型？",
                      "品質越高細節越細。要不要在「設定」裡選擇合適的引擎？",
                      "要不要先看看「風格」？",
                      "品質越高細節越細。要不要到下面的「設定」選擇引擎？",
                      "要不要按「照建議直接美化」？"):
            result = said(reply, ["調高品質", "先看看選項"])
            self.assertEqual((result["reply"], result["options"]), (reply, []), reply)
        # 真正在問答案的保留：沒指向按鈕、是要改「人像」裡的東西、前面句子提到按鈕。
        for reply, options in (("想換成什麼顏色？", ["藍色", "白色"]),
                               ("要把「人像」的視線修正關掉嗎？", ["關掉", "保留"]),
                               ("可以在「設定」裡改。你說的是哪一位？", ["左邊女性", "右邊男性"])):
            self.assertEqual(said(reply, options)["options"], options, reply)

    def test_a_reply_can_open_a_panel(self):
        history = [{"role": "user", "text": "我要調整美化引擎", "card": {"type": "ask"}}]
        first_round = {"bar": ["照建議直接美化"], "topics": ["畫面", "風格"]}
        rendered = {"bar": ["看美化項目", "再美化"], "topics": []}

        def said(label, screen, status="ready", a=None, **more):
            answer = {"reply": "美化引擎在「設定」裡，打開在下面了。", "action": "none", "patch_json": "",
                      "options": ["好"], "open": label, **more}
            return chat_assistant.ask(analysis() if a is None else a, default_plan(), status, history,
                                      model=lambda c: answer, screen=screen)

        # 設定、個人化需求每張照片都有；第一輪、美化完都打得開，打開了就不給選項。
        for screen in (first_round, rendered):
            result = said("設定", screen)
            self.assertEqual((result["open"], result["options"]), ("settings", []))
            self.assertEqual(said("個人化需求", screen)["open"], "prompt")
        # 頁面沒送 panels（舊頁面）：人像、畫面、風格要畫面上列著才開。
        self.assertEqual(said("風格", first_round)["open"], "style")
        self.assertEqual(said("人像", first_round)["open"], "")
        self.assertEqual(said("風格", rendered)["open"], "")
        # 有 panels：美化完收進「看美化項目」的也打得開；這張照片沒有的、只照需求改那版
        # 下一輪不會列的，都不開。
        folded = {**rendered, "panels": ["畫面", "風格", "設定"]}
        self.assertEqual(said("風格", folded)["open"], "style")
        self.assertEqual(said("人像", folded)["open"], "")
        prompt_only = {**rendered, "panels": ["個人化需求", "設定"]}
        self.assertEqual(said("風格", prompt_only)["open"], "")
        self.assertEqual(said("設定", prompt_only)["open"], "settings")
        # 照片忙著、還沒有分析、或同時提了改動、要開始美化，都不開。
        self.assertEqual(said("設定", {"bar": [], "topics": []}, status="rendering")["open"], "")
        self.assertEqual(said("設定", first_round, a={})["open"], "")
        self.assertEqual(said("設定", first_round, patch_json='{"style_options":{"lighting":"soft"}}')["open"], "")
        self.assertEqual(said("設定", first_round, action="render")["open"], "")
        self.assertEqual(said("沒有這個", first_round)["open"], "")

    def test_only_wishes_every_reply_also_offers_the_analysis(self):
        # 只照需求改：底下有「幫我分析」，每則都要提，模型漏講就補上。
        screen = {"bar": ["看美化項目", "幫我分析", "再美化"], "topics": []}
        history = [{"role": "user", "text": "背景再亮一點好嗎", "card": {"type": "ask"}}]
        applied = chat_assistant.ask(analysis(), default_plan(), "ready", history,
                                     model=model_says(patch={"style_options": {"lighting": "soft"}}), screen=screen)
        self.assertEqual(applied["reply"], "好，光線改成「柔光」了。按下面的「美化」我就開始。" + chat_assistant.ANALYZE_NUDGE)
        answered = chat_assistant.ask(analysis(), default_plan(), "ready", history,
                                      model=model_says("背景已經照你的需求換好了。"), screen=screen)
        self.assertEqual(answered["reply"], "背景已經照你的需求換好了。" + chat_assistant.ANALYZE_NUDGE)
        said = chat_assistant.ask(analysis(), default_plan(), "ready", history,
                                  model=model_says("也可以按「幫我分析」看看。"), screen=screen)
        self.assertEqual(said["reply"], "也可以按「幫我分析」看看。")
        asking = chat_assistant.ask(analysis(), default_plan(), "ready", history,
                                    model=lambda c: {"reply": "想換成什麼顏色？", "action": "none", "patch_json": "",
                                                     "options": ["藍色", "白色"]}, screen=screen)
        self.assertEqual(asking["reply"], "想換成什麼顏色？")
        # 問的是要不要按「幫我分析」，選項拿掉了也不再補那句。
        offering = chat_assistant.ask(analysis(), default_plan(), "ready", history,
                                      model=lambda c: {"reply": "要不要先分析整張照片？", "action": "none", "patch_json": "",
                                                       "options": ["幫我分析", "先不用"]}, screen=screen)
        self.assertEqual((offering["reply"], offering["options"]), ("要不要先分析整張照片？", []))
        plain = chat_assistant.ask(analysis(), default_plan(), "ready", history,
                                   model=model_says("背景已經照你的需求換好了。"),
                                   screen={"bar": ["看美化項目", "再美化"], "topics": []})
        self.assertEqual(plain["reply"], "背景已經照你的需求換好了。")

    def test_any_change_is_applied_without_asking(self):
        cases = {
            "two settings": ({"style_options": {"lighting": "soft"}, "aspect_ratio": "original"}, "好，已經幫你改好了。"),
            "a portrait fix": ({"face_issue_overrides": {"0:gaze_issue": {"allowed": True}}}, "好，左邊女性・視線改成「修正」了。"),
            "a recipe step": ({"recipe_checked": {"0": False}}, "好，提亮主體改成「不套用」了。"),
        }
        for name, (patch, reply) in cases.items():
            with self.subTest(name):
                result = ask(model_says("確認後套用", patch=patch))
                self.assertTrue(result["proposal"]["direct"])
                self.assertEqual(result["reply"], reply)

    def test_a_wish_that_only_adds_is_applied(self):
        plan = default_plan()
        plan["user_prompt"] = "左邊女性的外套改成藍色"
        result = ask(model_says(patch={"user_prompt": "左邊女性的外套改成藍色；左邊女性把眼鏡拿掉"}), plan=plan)
        self.assertEqual(result["proposal"]["patch"]["user_prompt"], "左邊女性的外套改成藍色、把眼鏡拿掉")
        self.assertTrue(result["proposal"]["direct"])
        self.assertEqual(result["proposal"]["undo"], {"user_prompt": "左邊女性的外套改成藍色"})
        self.assertEqual(result["reply"], "好，記下了：左邊女性把眼鏡拿掉。")

    def test_tidied_punctuation_is_not_a_rewording(self):
        cases = {
            "comma": ("外套改成藍色，眼鏡拿掉", "外套改成藍色、眼鏡拿掉、背景換成海邊"),
            "full stop": ("背景換成海邊。", "背景換成海邊、加上夕陽"),
            "space": ("外套改成藍色 背景換成海邊", "外套改成藍色、背景換成海邊、加上夕陽"),
            "colon": ("左邊女性：外套改成藍色", "左邊女性的外套改成藍色、把眼鏡拿掉"),
        }
        for name, (before, after) in cases.items():
            with self.subTest(name):
                plan = default_plan()
                plan["user_prompt"] = before
                self.assertTrue(ask(model_says(patch={"user_prompt": after}), plan=plan)["proposal"].get("direct"))

    def test_a_long_wish_is_not_read_back(self):
        result = ask(model_says(patch={"user_prompt": "把背景換成傍晚的海邊，天空要有粉紫色的晚霞，海面反射夕陽的金光"}))
        self.assertEqual(result["reply"], "好，已加進個人化需求。")

    def test_the_ais_own_pick_is_written_down_with_its_reason(self):
        plan = default_plan()
        plan["user_prompt"] = "在照片上加上文字「盛夏」"
        reply = "白字配淡淡的深藍陰影最好，在藍色水面前也看得清楚，確認後套用。"
        result = ask(model_says(reply, patch={"user_prompt": "在照片上加上文字「盛夏」；文字用白色加淡深藍陰影"},
                                your_pick=True), plan=plan)
        self.assertTrue(result["proposal"]["direct"])
        self.assertEqual(result["reply"], "白字配淡淡的深藍陰影最好，在藍色水面前也看得清楚。先幫你記進個人化需求了，下面可以直接修改。")

    def test_a_wish_for_someone_else_is_still_written_down(self):
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
        # Pointed at her, but the model gave it to him and brought back an old
        # wish: 個人化需求 still goes in, and 「修改」 is there to fix it.
        self.assertTrue(run("上衣改成格紋", {"subject_indices": [0]},
                                       "左邊女性的衣服換成白色、頭髮染成金色；右邊男性的上衣改成格紋").get("direct"))
        # Pointed at no one, and wishes for two people appeared.
        self.assertTrue(run("上衣改成格紋", {},
                            "左邊女性的衣服換成白色、上衣改成格紋；右邊男性的上衣改成格紋").get("direct"))
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
        # One it kept and one it dropped: applied too, and 「修改」 can put it back.
        result = ask(model_says(patch={"user_prompt": "左邊女性的頭髮染成金色、衣服換成白色"}), plan=plan, history=history)
        self.assertTrue(result["proposal"]["direct"])

    def test_a_wish_that_replaces_one_is_applied(self):
        plan = default_plan()
        plan["user_prompt"] = "左邊女性的外套改成藍色"
        result = ask(model_says(patch={"user_prompt": "左邊女性的外套改成紅色"}), plan=plan)
        self.assertTrue(result["proposal"]["direct"])
        self.assertEqual(result["reply"], "好，記下了：左邊女性外套改成紅色。")
        # Only reworded, nothing new: it says so, not 「已加進」.
        result = ask(model_says(patch={"user_prompt": "左邊女性的外套換成藍色"}), plan=plan)
        self.assertEqual(result["reply"], "好，記下了：左邊女性外套換成藍色。")

    def test_a_wish_taken_back_is_applied(self):
        plan = default_plan()
        plan["user_prompt"] = "左邊女性的帽子拿掉、外套改成藍色；背景換成海邊"
        history = [{"role": "user", "text": "帽子留著好了", "card": {"type": "ask", "photo_id": "p1", "subject_indices": [0]}}]
        result = ask(model_says("好，左邊女性的帽子就不拿掉了。確認後套用。",
                                patch={"user_prompt": "左邊女性的外套改成藍色；背景換成海邊"}), plan=plan, history=history)
        self.assertTrue(result["proposal"]["direct"])
        self.assertEqual(result["proposal"]["undo"], {"user_prompt": plan["user_prompt"]})
        self.assertEqual(result["reply"], "好，左邊女性帽子拿掉就不做了。")

    def test_a_reworded_wish_is_not_taken_back(self):
        plan = default_plan()
        plan["user_prompt"] = "將這枚腕錶做成抽象風格、在照片中放上「相對論」"
        # The model joins the wishes with 「，」 and rewords the first one in place.
        result = ask(model_says(patch={"user_prompt": "將這枚腕錶做成融入流逝感的抽象風格，在照片中放上「相對論」"}), plan=plan)
        self.assertTrue(result["proposal"]["direct"])
        self.assertEqual(result["reply"], "好，記下了：將這枚腕錶做成融入流逝感的抽象風格。")
        # One taken out while the other is joined with 「，」: still taken back.
        plan["user_prompt"] = "背景調暗，在照片中放上「相對論」"
        result = ask(model_says(patch={"user_prompt": "在照片中放上「相對論」"}), plan=plan)
        self.assertEqual(result["reply"], "好，背景調暗就不做了。")

    def test_a_quoted_text_keeps_its_own_commas(self):
        items = chat_assistant._wish_items("在照片中放上「時間，空間、相對論」，背景調暗", {})
        self.assertEqual(items, [(None, "在照片中放上「時間，空間、相對論」"), (None, "背景調暗")])

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
        # Typed, not picked: applied all the same.
        history[-1]["text"] = "柔光、維持原比例吧"
        self.assertTrue(ask(model_says(patch=patch), history=history)["proposal"]["direct"])

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
        self.assertIn('FACES: [{"panel":"人物 1","name":"左邊女性","keys":{"0:gaze_issue":"視線","0:smile_issue":"笑容",'
                      '"0:face_symmetry":"臉部左右平衡"}}]', context)
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

    # 總覽（好幾張）打的：AI 知道是這幾張、一張的事請人去那張；單張的上傳頁不受影響。
    BATCH = {"photos": 3, "unrendered": 3, "analyze": "全部分析", "render": "全部一鍵美化", "where": "左邊的「我的照片」"}

    def batch_review(self, answer, batch=BATCH, line="這幾張哪張最需要修？"):
        seen = []
        result = chat_assistant.review_line(line, "", model=lambda c: seen.append(c) or answer, batch=batch)
        return result, seen[0]

    def test_the_overview_tells_the_model_it_speaks_for_a_batch(self):
        _, context = self.batch_review({"verdict": "drop", "reply": "好。", "line": "", "options": []})
        self.assertIn("BATCH OVERVIEW:", context)
        self.assertIn("OVERVIEW of 3 photos", context)
        self.assertIn("「全部分析」、「全部一鍵美化」", context)
        self.assertIn("選那一張，在那裡告訴我", context)
        # 單張的上傳頁沒有總覽那段；快取那段（固定的說明）兩邊一字不差，總覽那段在變動的那邊。
        seen = []
        chat_assistant.review_line("外套換成藍色", "", model=lambda c: seen.append(c) or {
            "verdict": "drop", "reply": "好。", "line": "", "options": []})
        self.assertNotIn("BATCH OVERVIEW", seen[0])
        self.assertEqual(seen[0].split(chat_assistant.CACHE_BREAK)[0], context.split(chat_assistant.CACHE_BREAK)[0])

    def test_an_overview_with_nothing_left_to_analyze_offers_only_render(self):
        done = {**self.BATCH, "analyze": ""}
        result, context = self.batch_review({"verdict": "offer", "reply": "", "line": "", "options": ["幫我分析", "一鍵美化"]}, done)
        self.assertEqual((result["options"], result["reply"]), (["全部一鍵美化"], chat_assistant.BATCH_RENDER_OFFER_REPLY))
        self.assertIn("never offer or mention 分析", context)
        result, _ = self.batch_review({"verdict": "offer", "reply": "", "line": "", "options": []})
        self.assertEqual((result["options"], result["reply"]), (["全部分析", "全部一鍵美化"], chat_assistant.BATCH_OFFER_REPLY))

    def test_an_overview_answer_sending_them_to_one_photo_keeps_its_words_and_gets_the_starts(self):
        reply = "只想改其中一張的話，請從左邊的「我的照片」選那一張，在那裡告訴我。"
        result, _ = self.batch_review({"verdict": "answer", "reply": reply, "line": "", "options": []}, line="第二張背景換掉")
        self.assertEqual((result["kept"], result["offer"], result["reply"], result["options"]),
                         (False, True, reply, ["全部分析", "全部一鍵美化"]))

    # 總覽的回答跟單張一樣，答完沒問什麼就接上開始鍵，鍵叫總覽上的名字，問句兩顆都問到；
    # 都分析過只給美化，都在跑就不給。
    def test_an_overview_answer_that_just_stops_offers_the_batch_starts(self):
        answer = {"verdict": "answer", "reply": "1*2+3 等於 5。", "line": "", "options": []}
        result, _ = self.batch_review(answer, line="1*2+3")
        self.assertTrue(result["offer"])
        self.assertEqual(result["options"], ["全部分析", "全部一鍵美化"])
        self.assertEqual(result["reply"], "1*2+3 等於 5。這幾張要先分析看看，還是直接一鍵美化？")
        result, _ = self.batch_review(answer, {**self.BATCH, "analyze": "", "render": "一鍵美化剩餘 2"}, line="1*2+3")
        self.assertEqual((result["options"], result["reply"]), (["一鍵美化剩餘 2"], "1*2+3 等於 5。要不要直接幫你一鍵美化？"))
        # 模型照總覽的名字給選項，也認得出來是哪一顆。
        result, _ = self.batch_review({"verdict": "offer", "reply": "要不要直接全部一鍵美化？", "line": "",
                                       "options": ["全部一鍵美化", "全部分析"]}, line="直接修吧")
        self.assertEqual((result["options"], result["reply"]),
                         (["全部一鍵美化", "全部分析"], "這幾張要直接一鍵美化，還是先分析看看？"))
        result, _ = self.batch_review(answer, {**self.BATCH, "analyze": "", "render": ""}, line="1*2+3")
        self.assertEqual((result["offer"], result["options"], result["reply"]), (False, [], "1*2+3 等於 5。"))

    # 總覽收下一句：先回應，再講會套到哪幾張，最後指向總覽那兩顆鍵（不是單張的「分析」「一鍵美化」）。
    def test_an_overview_keep_says_where_it_goes_and_names_the_batch_starts(self):
        answer = {"verdict": "keep", "reply": "好，這個我記下來了。", "line": "照片清楚一點", "options": []}
        result, _ = self.batch_review(answer, {**self.BATCH, "photos": 2, "unrendered": 2}, line="照片看起來糊糊的")
        self.assertTrue(result["kept"])
        self.assertEqual(result["reply"], "好，這個我記下來了。這 2 張美化時都會照這樣修。"
                         "還有其他想改的地方嗎？沒有的話，可以按下面的「全部分析」或「全部一鍵美化」。")
        result, _ = self.batch_review(answer, {**self.BATCH, "photos": 3, "unrendered": 2, "analyze": "",
                                               "render": "一鍵美化剩餘 2"}, line="照片看起來糊糊的")
        self.assertEqual(result["reply"], "好，這個我記下來了。還沒美化的 2 張會照這樣修，已經在美化或美化好的 1 張不動。"
                         "還有其他想改的地方嗎？沒有的話，可以按下面的「一鍵美化剩餘 2」。")

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

    # 問「哪一位」要點頭像，不到兩個人就沒有頭像可點：直接收下，不留一個答不了的問題。
    def test_asking_who_with_one_face_keeps_the_line(self):
        def model(context):
            return {"verdict": "clarify", "reply": "你說的是哪一位？點下面的頭像選人就好。", "line": "", "options": [], "question_kind": "person", "semantics": [dict(
                source="把他頭髮染黑", target="他", action="頭髮染黑", complete=True,
                target_kind="person", owner_required=True, scope="edit", feature="hair_color", part="頭髮",
                intent="set", value="黑", literal_text="", constraints=[])]}
        for people in ([], ["照片中從左邊數來第 1 位"]):
            result = chat_assistant.review_line("把他頭髮染黑", "", people=people, model=model)
            self.assertTrue(result["kept"], people)
            self.assertFalse(result["ask"], people)
            self.assertEqual(result["reply"], chat_assistant.KEPT_REPLY + chat_assistant.KEPT_NEXT)

    def test_asking_who_with_two_faces_still_asks(self):
        def model(context):
            return {"verdict": "clarify", "reply": "你說的是哪一位？點下面的頭像選人就好。", "line": "", "options": [], "question_kind": "person", "semantics": [dict(
                source="把他頭髮染黑", target="他", action="頭髮染黑", complete=True,
                target_kind="person", owner_required=True, scope="edit", feature="hair_color", part="頭髮",
                intent="set", value="黑", literal_text="", constraints=[])]}
        result = chat_assistant.review_line("把他頭髮染黑", "", people=["照片中從左邊數來第 1 位", "照片中從左邊數來第 2 位"],
                                            model=model)
        self.assertTrue(result["ask"])
        self.assertFalse(result["kept"])

    def test_asking_what_with_one_face_still_asks(self):
        def model(context):
            return {"verdict": "clarify", "reply": "想換成什麼顏色？", "line": "", "options": ["白色", "黑色"], "question_kind": "content"}
        result = chat_assistant.review_line("衣服換顏色", "", people=["照片中從左邊數來第 1 位"], model=model)
        self.assertTrue(result["ask"])
        self.assertEqual(result["options"], ["白色", "黑色"])

    def test_incomplete_action_still_asks_without_face_choices_or_after_an_answer(self):
        value = dict(source="女性眼睛", target="女性", action="", complete=False,
                     target_kind="person", owner_required=False, scope="edit", feature="eyes",
                     part="eyes", intent="set", value="", constraints=[])
        for people in ([], ["唯一人物"]):
            for history in ([], [{"role": "assistant", "text": "想做什麼調整？", "question": True}]):
                for verdict in ("keep", "clarify"):
                    with self.subTest(people=people, history=history, verdict=verdict):
                        result = chat_assistant.review_line("女性眼睛", "", history=history, people=people,
                            model=lambda context: dict(verdict=verdict, reply="想怎麼調整眼睛？", options=[], semantics=[value]))
                        self.assertTrue(result["ask"])
                        self.assertFalse(result["kept"])

    def test_an_answer_is_parsed_as_the_line_written_out(self):
        value = dict(source="背景換成森林", target="背景", action="換成森林", complete=True,
                     target_kind="region", owner_required=False, scope="edit", feature="background",
                     part="背景", intent="set", value="森林", constraints=[])
        history = [{"role": "user", "text": "改背景"}, {"role": "assistant", "text": "想把背景換成什麼？", "question": True}]
        result = chat_assistant.review_line("森林", "", history=history,
            model=lambda context: dict(verdict="keep", reply="", line="背景換成森林", options=[], semantics=[value]))
        self.assertTrue(result["kept"])
        self.assertEqual(result["line"], "背景換成森林")
        self.assertEqual(result["request_semantics"][0]["source"], "背景換成森林")
        self.assertNotIn("semantic_pending", result)

    def test_ungrounded_semantics_leave_the_review_standing(self):
        value = dict(source="背景換成海邊", target="背景", action="換成海邊", complete=True,
                     target_kind="region", owner_required=False, scope="edit", feature="background",
                     part="背景", intent="set", value="海邊", constraints=[])
        result = chat_assistant.review_line("森林", "", history=[],
            model=lambda context: dict(verdict="keep", reply="", line="", options=[], semantics=[value]))
        self.assertTrue(result["kept"])
        self.assertTrue(result["semantic_pending"])
        self.assertNotIn("request_semantics", result)

    # 後講的蓋掉先講的：同一件事不能兩個都做，衝突的那幾段由模型拿掉衝突部分，
    # 這句由頁面放進自己那段，回話講一聲。
    SECTIONS = [{"who": "照片中從左邊數來第 1 位", "text": "皮膚曬黑、瘦臉"},
                {"who": "", "text": "把門後的女生去掉；把牆壁改成紅磚牆"}]

    def merge(self, verdict, merged, sections=SECTIONS):
        seen = {}

        def model(context):
            seen["context"] = context
            return {"verdict": verdict, "reply": "好，這個我記下來了。", "line": "", "options": [],
                    "later": False, "merged": merged}
        result = chat_assistant.review_line("把牆壁改成木頭牆", "", sections=sections, model=model)
        return result, seen["context"]

    def test_a_conflicting_line_rewrites_the_sections_it_takes_over(self):
        result, context = self.merge("keep", [{"section": 2, "text": "把門後的女生去掉"}])
        self.assertIn("LINES SO FAR:\n[1] 照片中從左邊數來第 1 位：皮膚曬黑、瘦臉\n[2] 未指定對象：把門後的女生去掉；把牆壁改成紅磚牆", context)
        self.assertTrue(result["kept"])
        self.assertEqual(result["merged"], [{"section": 1, "text": "把門後的女生去掉"}])
        self.assertEqual(result["reply"], chat_assistant.KEPT_REPLY + chat_assistant.MERGED_NOTE + chat_assistant.KEPT_NEXT)

    def test_a_section_can_be_emptied(self):
        result = self.merge("keep", [{"section": 1, "text": ""}])[0]
        self.assertEqual(result["merged"], [{"section": 0, "text": ""}])

    def test_a_bare_prompt_is_one_unassigned_section(self):
        def model(context):
            self.assertIn("LINES SO FAR:\n[1] 未指定對象：把牆壁改成紅磚牆", context)
            return {"verdict": "keep", "reply": "", "line": "", "options": [], "merged": [{"section": 1, "text": ""}]}
        self.assertEqual(chat_assistant.review_line("把牆壁改成木頭牆", "把牆壁改成紅磚牆", model=model)["merged"],
                         [{"section": 0, "text": ""}])

    def withdraw(self, merged, reply=""):
        def model(context):
            return {"verdict": "keep", "reply": reply, "line": "", "options": [],
                    "later": False, "merged": merged, "withdraw": True}
        sections = [{"who": "照片中從左邊數來第 1 位", "text": "帽子拿掉、瘦臉"}]
        return chat_assistant.review_line("照片中從左邊數來第 1 位：帽子留著", "", sections=sections, model=model)

    def test_a_line_that_only_takes_a_wish_back_is_withdrawn(self):
        result = self.withdraw([{"section": 1, "text": "瘦臉"}], "好，帽子就不拿掉了。")
        self.assertTrue(result["withdrawn"])
        self.assertEqual(result["merged"], [{"section": 0, "text": "瘦臉"}])
        self.assertEqual(result["reply"], "好，帽子就不拿掉了。" + chat_assistant.KEPT_NEXT)

    def test_a_withdraw_without_reply_says_the_earlier_wish_is_gone(self):
        result = self.withdraw([{"section": 1, "text": "瘦臉"}])
        self.assertEqual(result["reply"], chat_assistant.WITHDRAWN_REPLY + chat_assistant.KEPT_NEXT)

    def test_nothing_to_take_back_is_an_ordinary_wish(self):
        result = self.withdraw([])
        self.assertTrue(result["kept"])
        self.assertFalse(result["withdrawn"])

    def test_merged_unchanged_unknown_or_not_kept_is_ignored(self):
        self.assertEqual(self.merge("keep", [{"section": 1, "text": " 皮膚曬黑、瘦臉 "}])[0]["merged"], [])
        self.assertEqual(self.merge("keep", [{"section": 3, "text": ""}, {"section": 0, "text": ""}])[0]["merged"], [])
        self.assertEqual(self.merge("drop", [{"section": 2, "text": ""}])[0]["merged"], [])
        result = self.merge("keep", [])[0]
        self.assertEqual(result["merged"], [])
        self.assertEqual(result["reply"], chat_assistant.KEPT_REPLY + chat_assistant.KEPT_NEXT)

    # 上傳頁說了比例（「做成限動」），「設定」的長寬比就跟著改，不讓 AI 建議跟需求打架。
    def ratio(self, verdict, value, line="做成限動", wish="", switches=(), rest=None):
        def model(context):
            if context.startswith(chat_assistant.RATIO_REST_INSTRUCTION):
                left = context.split("LEFT: ", 1)[1]
                return {"line": left if rest is None else rest}
            return {"verdict": verdict, "reply": "", "line": wish, "options": [], "later": False,
                    "aspect_ratio": value, "advanced_options": list(switches)}
        return chat_assistant.review_line(line, "", model=model)

    def test_a_line_that_only_names_a_shape_only_sets_the_ratio(self):
        result = self.ratio("setting", "9:16")
        self.assertFalse(result["kept"])
        self.assertEqual(result["aspect_ratio"], "9:16")
        self.assertEqual(result["reply"], "好，長寬比換成「9:16（限動）」了，在「設定」裡隨時可以改。"
                         + chat_assistant.KEPT_NEXT)

    def test_a_kept_wish_that_also_names_a_shape_keeps_only_the_rest(self):
        result = self.ratio("keep", "9:16", line="做成限動，天空藍一點", wish="天空藍一點")
        self.assertTrue(result["kept"])
        self.assertEqual(result["line"], "天空藍一點")
        self.assertEqual(result["aspect_ratio"], "9:16")
        self.assertEqual(result["reply"], chat_assistant.KEPT_REPLY + "另外長寬比換成「9:16（限動）」了，在「設定」裡隨時可以改。"
                         + chat_assistant.KEPT_NEXT)

    # 模型沒拆出來：寫明的比例拿掉就沒東西了，還是只改設定；剩下的才進清單。
    def test_a_written_ratio_left_in_the_line_is_taken_out(self):
        result = self.ratio("keep", "4:5", line="裁成 4:5")
        self.assertFalse(result["kept"])
        self.assertEqual(result["aspect_ratio"], "4:5")
        result = self.ratio("keep", "4:5", line="背景調亮，裁成 4:5")
        self.assertTrue(result["kept"])
        self.assertEqual(result["line"], "背景調亮")
        # 剩下的交給模型整理：只剩問句就是只改設定。
        result = self.ratio("keep", "4:5", line="4:5可以嗎", rest="")
        self.assertFalse(result["kept"])
        self.assertEqual(result["line"], "")
        self.assertEqual(result["aspect_ratio"], "4:5")

    def test_a_line_that_only_flips_a_switch_only_sets_it(self):
        result = self.ratio("setting", "", line="閉眼不用修",
                            switches=[{"key": "eye_closed_detection", "on": False}])
        self.assertFalse(result["kept"])
        self.assertEqual(result["advanced_options"], {"eye_closed_detection": False})
        self.assertEqual(result["reply"], "好，關掉「閉眼修正」了，在「設定」裡隨時可以改。" + chat_assistant.KEPT_NEXT)

    def test_a_kept_wish_that_also_flips_a_switch_says_so(self):
        result = self.ratio("keep", "", line="大家看鏡頭，背景換白", wish="背景換白",
                            switches=[{"key": "gaze_contact_detection", "on": True}, {"key": "bogus", "on": True}])
        self.assertTrue(result["kept"])
        self.assertEqual(result["line"], "背景換白")
        self.assertEqual(result["advanced_options"], {"gaze_contact_detection": True})
        self.assertIn("另外打開「視線修正」了", result["reply"])

    def test_a_setting_with_nothing_to_set_is_kept(self):
        result = self.ratio("setting", "", line="眼睛張開")
        self.assertTrue(result["kept"])
        self.assertEqual(result["advanced_options"], {})

    def test_switches_only_come_with_a_kept_line_or_a_setting(self):
        result = self.ratio("answer", "", line="閉眼修正是什麼？",
                            switches=[{"key": "eye_closed_detection", "on": False}])
        self.assertEqual(result["advanced_options"], {})

    def test_a_ratio_only_comes_with_a_kept_wish_the_panel_offers(self):
        self.assertEqual(self.ratio("answer", "9:16")["aspect_ratio"], "")
        self.assertEqual(self.ratio("keep", "7:5")["aspect_ratio"], "")
        result = self.ratio("keep", "")
        self.assertEqual(result["aspect_ratio"], "")
        self.assertEqual(result["reply"], chat_assistant.KEPT_REPLY + chat_assistant.KEPT_NEXT)

    # 說了面板沒有的比例（21:9），先問要不要換成最接近的，不默默當成 AI 建議。
    def test_a_shape_the_panel_lacks_asks_for_the_nearest(self):
        result = chat_assistant.review_line("做成 21:9", "", model=lambda context: {
            "verdict": "keep", "reply": "好，記下來了。", "line": "", "options": [], "later": False,
            "aspect_ratio": "", "asked_ratio": "21:9"})
        self.assertFalse(result["kept"])
        self.assertTrue(result["ask"])
        self.assertEqual(result["aspect_ratio"], "")
        self.assertEqual(result["reply"], "目前沒有 21:9 這個比例，最接近的是「16:9（桌布）」，要換成這個嗎？")
        self.assertEqual(result["options"], ["換成 16:9", chat_assistant.RATIO_ASK_AI])

    def ratio_question(self, line, wish="做成 21:9，背景調亮", people=(), rest=None, asked=None):
        history = [{"role": "user", "text": wish},
                   {"role": "assistant", "question": True, "options": ["換成 16:9", chat_assistant.RATIO_ASK_AI],
                    "text": "目前沒有 21:9 這個比例，最接近的是「16:9（桌布）」，要換成這個嗎？"}]

        # Only what is left of the wish once the ratio is out goes to the model.
        def model(context):
            if not context.startswith(chat_assistant.RATIO_REST_INSTRUCTION):
                raise AssertionError("the model was asked to review")
            said, left = context.split("SAID: ", 1)[1].split("\nLEFT: ")
            if asked is not None:
                asked.append((said, left))
            return {"line": left if rest is None else rest}
        return chat_assistant.review_line(line, "", history, people, model=model)

    def test_taking_the_nearest_sets_it_and_keeps_the_rest_of_the_wish(self):
        result = self.ratio_question("換成 16:9")
        self.assertTrue(result["kept"])
        self.assertEqual(result["aspect_ratio"], "16:9")
        self.assertEqual(result["line"], "背景調亮")
        self.assertIn("16:9（桌布）", result["reply"])

    def test_taking_the_nearest_for_a_ratio_alone_keeps_nothing(self):
        who = "照片中從左邊數來第 1 位"
        for wish, people in (("做成 21:9", ()), (f"{who}：做成 21:9", [who])):
            result = self.ratio_question("換成 16:9" if not people else f"{who}：換成 16:9", wish=wish, people=people)
            self.assertFalse(result["kept"], wish)
            self.assertEqual(result["aspect_ratio"], "16:9", wish)
            self.assertEqual(result["line"], "", wish)
            self.assertEqual(result["reply"], "好，長寬比換成「16:9（桌布）」了，在「設定」裡隨時可以改。"
                             + chat_assistant.KEPT_NEXT)

    def test_leaving_it_to_the_ai_keeps_the_rest_of_the_wish(self):
        result = self.ratio_question(chat_assistant.RATIO_ASK_AI)
        self.assertTrue(result["kept"])
        self.assertEqual(result["aspect_ratio"], "")
        self.assertEqual(result["line"], "背景調亮")
        self.assertEqual(result["reply"], chat_assistant.RATIO_ASK_AI_REPLY_REST + chat_assistant.KEPT_NEXT)

    def test_leaving_only_the_ratio_to_the_ai_keeps_nothing(self):
        who = "照片中從左邊數來第 1 位"
        for wish, people in (("裁成21比9的比例", ()), (f"{who}：做成 21:9", [who])):
            result = self.ratio_question(chat_assistant.RATIO_ASK_AI, wish=wish, people=people)
            self.assertFalse(result["kept"], wish)
            self.assertEqual(result["line"], "", wish)
            self.assertEqual(result["reply"], chat_assistant.RATIO_ASK_AI_REPLY + chat_assistant.KEPT_NEXT)

    def test_the_model_tidies_what_is_left_after_the_ratio(self):
        for tap in ("換成 16:9", chat_assistant.RATIO_ASK_AI):
            asked = []
            result = self.ratio_question(tap, wish="21:9可以嗎", rest="", asked=asked)
            self.assertEqual(asked, [("21:9可以嗎", "可以嗎")], tap)
            self.assertFalse(result["kept"], tap)
            self.assertEqual(result["line"], "", tap)
        result = self.ratio_question("換成 16:9", wish="21:9可以嗎，背景調亮一點喔", rest="背景調亮一點")
        self.assertTrue(result["kept"])
        self.assertEqual(result["line"], "背景調亮一點")

    def test_nothing_left_after_the_ratio_asks_no_model(self):
        asked = []
        self.ratio_question("換成 16:9", wish="做成 21:9", asked=asked)
        self.assertEqual(asked, [])

    def test_what_is_left_is_kept_as_is_when_the_model_fails(self):
        history = [{"role": "user", "text": "21:9，背景調亮"},
                   {"role": "assistant", "question": True, "options": ["換成 16:9", chat_assistant.RATIO_ASK_AI],
                    "text": "目前沒有 21:9 這個比例，最接近的是「16:9（桌布）」，要換成這個嗎？"}]

        def model(context):
            raise RuntimeError("down")
        result = chat_assistant.review_line("換成 16:9", "", history, model=model)
        self.assertEqual(result["line"], "背景調亮")

    def test_another_way_of_writing_the_ratio_is_still_taken_out(self):
        self.assertEqual(self.ratio_question("換成 16:9", wish="背景調亮然後裁成21比9")["line"], "背景調亮")


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

    def test_a_reworded_wish_is_not_told_as_taken_out(self):
        seen = []
        before, after = default_plan(), default_plan()
        before["user_prompt"] = "將這枚腕錶做成抽象風格、在照片中放上「相對論」"
        after["user_prompt"] = "將這枚腕錶做成融入流逝感的抽象風格，在照片中放上「相對論」"
        chat_assistant.write_render_note(analysis(), after, previous_plan=before,
                                         model=lambda c, i: seen.append(c) or {"note": "x"})
        self.assertIn('THIS ROUND: [{"label":"個人化需求","revised":[{"from":"將這枚腕錶做成抽象風格",'
                      '"to":"將這枚腕錶做成融入流逝感的抽象風格"}]}]', seen[0])

    def test_a_wishes_only_version_talks_about_the_wishes_alone(self):
        seen = []
        plan = {**default_plan(), "user_prompt": "把左邊的藍傘拿掉", "overwrite_prompt": True}
        chat_assistant.write_render_note(analysis(), plan, model=lambda c, i: seen.append(c) or {"note": "x"})
        chat_assistant.write_render_note(analysis(), default_plan(), model=lambda c, i: seen.append(c) or {"note": "x"})
        self.assertIn(chat_assistant.ONLY_WISHES_RULE, seen[0])
        self.assertNotIn(chat_assistant.ONLY_WISHES_RULE, seen[1])

    # 個人化需求裡寫了 4:5，比例卻還是 AI 建議：說明只能照實際套用的設定講。
    def test_the_note_is_told_which_settings_were_applied(self):
        seen = []
        plan = {**default_plan(), "user_prompt": "做成 IG 貼文 4:5"}
        chat_assistant.write_render_note(analysis(), plan, model=lambda c, i: seen.append(c) or {"note": "x"})
        self.assertIn('APPLIED SETTINGS: {"比例":"AI 建議"', seen[0])
        self.assertIn('"閉眼修正":"開"', seen[0])
        plan["aspect_ratio"] = "4:5"
        plan["advanced_options"] = {**(plan.get("advanced_options") or {}), "eye_closed_detection": False}
        chat_assistant.write_render_note(analysis(), plan, model=lambda c, i: seen.append(c) or {"note": "x"})
        self.assertIn('"比例":"4:5（IG 貼文）"', seen[1])
        self.assertIn('"閉眼修正":"關"', seen[1])

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
