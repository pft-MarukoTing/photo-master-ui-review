"""Tests for the edit features editor skills declare and the analysis fields they read.

    python3 -m unittest tests.test_editor_features -v
"""

import json
import unittest
from unittest import mock

import config
from photo_agent.analysis_skills import produced_subject_fields
from photo_agent.editor_skills import describe_editor_features, editor_features
from photo_agent.param_validation import (
    ADVANCED_OPTION_CHOICES,
    ADVANCED_OPTION_DEFAULTS,
    SMILE_LEVELS,
)


class EditorFeatureTest(unittest.TestCase):
    def test_feature_ids_are_unique(self):
        ids = [feature.id for feature in editor_features()]
        self.assertEqual(len(ids), len(set(ids)))

    def test_toggles_and_photo_options_are_known_advanced_options(self):
        known = set(ADVANCED_OPTION_DEFAULTS) | set(ADVANCED_OPTION_CHOICES)
        for feature in editor_features():
            if feature.toggle:
                self.assertIn(feature.toggle, ADVANCED_OPTION_DEFAULTS, feature.id)
            for key, _ in feature.photo_options:
                self.assertIn(key, known, feature.id)

    def test_smile_levels_match_the_accepted_values(self):
        smile = next(feature for feature in editor_features() if feature.id == "smile")
        self.assertEqual([value for value, _ in smile.levels], list(SMILE_LEVELS))
        schema = smile.params_schema()
        self.assertEqual(schema["properties"]["smile_level"]["enum"], list(SMILE_LEVELS))

    def test_description_is_json_ready(self):
        described = describe_editor_features()
        self.assertEqual(json.loads(json.dumps(described, ensure_ascii=False)), described)
        for entry in described:
            schema = entry["params_schema"]
            self.assertEqual(schema["type"], "object")
            subject = schema["properties"]["subjects"]["items"]
            self.assertEqual(subject["required"], ["subject_index", "allowed"])
            self.assertEqual("level" in subject["properties"], bool(entry["levels"]))

    def test_every_feature_reads_a_field_an_analysis_skill_produces(self):
        with mock.patch.object(config, "ENABLE_BODY_RESHAPE", True):
            for feature in editor_features():
                self.assertIn(feature.issue_field, produced_subject_fields(), feature.id)

    def test_body_reshape_is_offered_only_behind_its_flag(self):
        with mock.patch.object(config, "ENABLE_BODY_RESHAPE", False):
            self.assertNotIn("body_reshape", [feature.id for feature in editor_features()])
        with mock.patch.object(config, "ENABLE_BODY_RESHAPE", True):
            self.assertIn("body_reshape", [feature.id for feature in editor_features()])


if __name__ == "__main__":
    unittest.main()
