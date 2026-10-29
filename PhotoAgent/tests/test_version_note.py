import json
import unittest
from unittest.mock import patch

from tests.test_chat_routes import account


class FakeRedis:
    def __init__(self):
        self.hashes = {}

    def hgetall(self, key):
        return dict(self.hashes.get(key, {}))

    def hset(self, key, field, value):
        self.hashes.setdefault(key, {})[field] = value


class VersionNoteTest(unittest.TestCase):
    def setUp(self):
        self.redis = FakeRedis()
        patcher = patch.object(account, "pf_redis_client", self.redis)
        patcher.start()
        self.addCleanup(patcher.stop)
        key = account._versions_key("amy", "case-1")
        for version_id, job_id, run in (("v1", "j1", 1), ("v2", "j2", 1), ("v2", "j2", 2)):
            self.redis.hset(key, account._version_field(version_id, job_id, run),
                            json.dumps({"kind": "edit", "created_at": 1, "params": {"user_prompt": "x"}}))

    def test_a_note_is_kept_on_every_image_of_its_version_only(self):
        self.assertTrue(account.set_version_note("amy", "case-1", "v2", "杯子拿掉了。"))
        versions = account._group_versions(account._load_version_records("amy", "case-1"))
        self.assertEqual([(v["version_id"], v["note"]) for v in versions], [("v1", None), ("v2", "杯子拿掉了。")])
        # The rest of the record stays as it was.
        self.assertEqual(versions[1]["params"], {"user_prompt": "x"})

    def test_an_unknown_version_is_left_alone(self):
        self.assertFalse(account.set_version_note("amy", "case-1", "gone", "note"))


if __name__ == "__main__":
    unittest.main()
