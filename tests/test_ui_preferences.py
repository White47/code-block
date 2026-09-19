"""Global language preferences never change analyzed source or project state."""

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch


class PreferencesTests(unittest.TestCase):
    def _store_class(self):
        try:
            from codeviz.ui.preferences import PreferencesStore
        except ModuleNotFoundError:
            self.fail("PreferencesStore module is missing")
        return PreferencesStore

    def test_missing_preferences_default_to_chinese_and_english_round_trips(self):
        with TemporaryDirectory() as temporary:
            store = self._store_class()(Path(temporary))
            self.assertEqual(store.load(), "zh-CN")
            store.save("en")
            self.assertEqual(store.load(), "en")
            self.assertEqual(json.loads((Path(temporary) / "preferences.json").read_text(
                encoding="utf-8"))["language"], "en")

    def test_corrupt_oversize_or_unknown_preferences_fall_back_safely(self):
        with TemporaryDirectory() as temporary:
            store = self._store_class()(Path(temporary))
            target = Path(temporary) / "preferences.json"
            for content in ("{not json", "x" * 5000,
                            '{"version": 1, "language": "xx"}'):
                with self.subTest(content=content[:12]):
                    target.write_text(content, encoding="utf-8")
                    self.assertEqual(store.load(), "zh-CN")
                    self.assertIsNotNone(store.issue)

    def test_failed_atomic_save_does_not_replace_existing_preference(self):
        with TemporaryDirectory() as temporary:
            store = self._store_class()(Path(temporary))
            store.save("en")
            with patch("codeviz.ui.preferences.os.replace", side_effect=OSError("denied")):
                with self.assertRaises(OSError):
                    store.save("zh-CN")
            self.assertEqual(store.load(), "en")

    def test_translation_has_chinese_and_english_fallback(self):
        try:
            from codeviz.ui.i18n import translate
        except ModuleNotFoundError:
            self.fail("translation catalog is missing")
        self.assertEqual(translate("zh-CN", "Open Folder"), "打开文件夹")
        self.assertEqual(translate("zh-CN", "Project Overview"), "项目概览")
        self.assertEqual(translate("zh-CN", "Open Source"), "打开源码")
        self.assertEqual(translate("zh-CN", "Training Flow"), "训练流程")
        self.assertEqual(translate("en", "Open Folder"), "Open Folder")
        self.assertEqual(translate("zh-CN", "unknown source symbol"),
                         "unknown source symbol")


if __name__ == "__main__":
    unittest.main()
