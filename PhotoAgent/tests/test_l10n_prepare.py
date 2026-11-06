import json
import pathlib
import sys
import types
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import l10n_prepare  # noqa: E402
from photo_agent import i18n  # noqa: E402

FORMAT = {"list": "、", "sentence": "", "quote": ["「", "」"], "sentence_end": "。", "wish_marks": ["；", "、", "："],
          "name_mark": "：", "echo_scale": 2}
VERSION = {"doc_commit": "abc", "ref": "latest"}


class PrepareTest(unittest.TestCase):
    """Catalogs made from fixture ARB files, against the code's current English."""

    @classmethod
    def setUpClass(cls):
        cls.keys, cls.source = l10n_prepare.current_english()
        keys = cls.keys["keys"]
        cls.backend = next(k for k, v in keys.items() if v["where"] == ["backend text"] and "{" in cls.source[k])
        cls.plural = next(k for k, v in keys.items() if "plural" in cls.source[k] and "panel" in v["where"])
        cls.shared = next(k for k, v in keys.items() if {"panel", "chat"} <= set(v["where"]))

    def languages(self, **meta):
        return {"ja": {"enabled": True, "arb_locale": "ja", "name": "日本語", "prompt_name": "Japanese",
                       "format": FORMAT, "manual": {"、": "、"}, **meta}}

    def make(self, arb, languages=None):
        files = {"ja": json.dumps({"@@locale": "ja", **arb}, ensure_ascii=False)}
        texts, report = l10n_prepare.generate(files, languages or self.languages(), self.keys, self.source, VERSION)
        return {"panel": json.loads(texts["panel"].split("=", 1)[1].rstrip().rstrip(";")),
                "chat": json.loads(texts["chat"]), "backend": json.loads(texts["backend"])}, report

    def filled(self, key_id):
        """A translation of key_id's English with its placeholders kept."""
        return "訳" + "".join("{%s}" % name for name in l10n_prepare.l10n_arb.placeholders(self.source[key_id]))

    def test_ids_go_back_to_the_chinese_of_each_catalog(self):
        made, report = self.make({self.shared: "共有", self.backend: self.filled(self.backend)})
        zh = self.keys["keys"][self.shared]["zh"]
        self.assertEqual(made["panel"]["catalogs"]["ja"][zh], "共有")
        self.assertEqual(made["chat"]["catalogs"]["ja"][zh], "共有")
        backend = made["backend"]["languages"][0]
        self.assertEqual(backend["text"][self.keys["keys"][self.backend]["zh"]], self.filled(self.backend))
        self.assertEqual((backend["code"], backend["name"]), ("ja", "Japanese"))
        self.assertEqual(made["panel"]["languages"], [{"code": "ja", "name": "日本語"}])
        self.assertEqual(made["chat"]["catalogs"]["ja"]["、"], "、")
        self.assertIn("2/", report[0])

    def test_new_removed_and_empty_strings_do_not_fail(self):
        made, report = self.make({"gone_from_the_code": "古い", self.shared: ""})
        self.assertEqual(made["panel"]["catalogs"]["ja"], {"、": "、"})
        self.assertIn("1 ids no longer in the code ignored", report[0])

    def test_a_plural_may_have_only_the_other_form(self):
        name = next(iter(l10n_prepare.l10n_arb.placeholders(self.source[self.plural])))
        text ="{%s, plural, other{# 枚}}" % name + "".join(
            "{%s}" % n for n in l10n_prepare.l10n_arb.placeholders(self.source[self.plural]) if n != name)
        self.make({self.plural: text})

    def test_placeholder_and_plural_mismatches_fail_with_the_id(self):
        for text in ("訳", self.filled(self.backend) + "{extra}", self.filled(self.backend) + "}"):
            with self.assertRaises(l10n_prepare.Failed) as caught:
                self.make({self.backend: text})
            self.assertIn(self.backend, str(caught.exception))
        name = next(iter(l10n_prepare.l10n_arb.placeholders(self.source[self.plural])))
        others = "".join("{%s}" % n for n in l10n_prepare.l10n_arb.placeholders(self.source[self.plural]) if n != name)
        with self.assertRaises(l10n_prepare.Failed):
            self.make({self.plural: "{%s, plural, one{# 枚}}" % name + others})

    def test_an_enabled_language_needs_its_arb_and_metadata(self):
        with self.assertRaisesRegex(l10n_prepare.Failed, "no intl_ja.arb"):
            l10n_prepare.generate({}, self.languages(), self.keys, self.source, VERSION)
        with self.assertRaisesRegex(l10n_prepare.Failed, "format.quote"):
            self.make({}, self.languages(format={**FORMAT, "quote": "「"}))
        with self.assertRaisesRegex(l10n_prepare.Failed, "not an ARB file for ja"):
            l10n_prepare.generate({"ja": '{"@@locale": "ko"}'}, self.languages(), self.keys, self.source, VERSION)

    def test_a_disabled_language_is_left_out(self):
        made, report = self.make({}, self.languages(enabled=False))
        self.assertEqual((made["panel"]["languages"], made["backend"]["languages"], report), ([], [], []))

    def test_the_same_input_makes_the_same_files(self):
        files = {"ja": json.dumps({self.shared: "共有"})}
        first = l10n_prepare.generate(files, self.languages(), self.keys, self.source, VERSION)
        self.assertEqual(first, l10n_prepare.generate(files, self.languages(), self.keys, self.source, VERSION))


class MadeCatalogFallbackTest(unittest.TestCase):
    """A made catalog falls back to English, then Chinese."""

    def setUp(self):
        catalog = types.SimpleNamespace(CODE="xx", NAME="Test", LABELS={"設定": "設定X"}, TEXT={"好": "好X"},
                                        PATTERNS={}, FORMAT={**FORMAT, "quote": ("«", "»")})
        self.saved = dict(i18n.CATALOGS)
        i18n.CATALOGS["xx"] = catalog

    def tearDown(self):
        i18n.CATALOGS.clear()
        i18n.CATALOGS.update(self.saved)

    def test_fallback_order(self):
        with i18n.use("xx"):
            self.assertEqual(i18n.label("設定"), "設定X")
            self.assertEqual(i18n.tr("好"), "好X")
            self.assertEqual(i18n.label("分析"), "Analyze")
            self.assertEqual(i18n.tr("好，這個我記下來了。"), "OK, noted.")
            self.assertEqual(i18n.tr("沒有這句"), "沒有這句")
            self.assertEqual(i18n.quote("分析"), "«Analyze»")
            self.assertIsNone(i18n.pattern("confirm_tail", "中文"))


if __name__ == "__main__":
    unittest.main()
