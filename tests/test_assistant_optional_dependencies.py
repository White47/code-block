"""Optional installation must use exactly the already-audited SDK closure."""

import re
import tomllib
import unittest
from pathlib import Path


class AssistantOptionalDependencyTests(unittest.TestCase):
    def test_ai_extra_pins_the_audited_closure_without_becoming_a_base_dependency(self):
        root = Path(__file__).resolve().parents[1]
        project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]
        audited = re.findall(r"^([\w-]+(?:\[[\w,-]+\])?==[^\s\\]+)",
            (root / "requirements" / "assistant-probe-lock.txt").read_text(encoding="utf-8"),
            flags=re.MULTILINE)
        self.assertEqual(len(audited), 26)
        self.assertIn("pydantic-ai-slim[openai]==2.55.0", audited)
        self.assertIn("ai", project["optional-dependencies"], "SDK must be an explicit opt-in extra")
        self.assertCountEqual(project["optional-dependencies"]["ai"], audited)
        self.assertEqual(project["dependencies"], [], "Ordinary offline install must stay dependency-free")


if __name__ == "__main__":
    unittest.main()
