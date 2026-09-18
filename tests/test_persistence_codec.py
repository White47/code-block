"""Analysis cache round-trips facts without AST or executable deserialization."""

import json
import unittest

from tests.test_training import analyze_files

from codeviz.core import ViewKind
from codeviz.persistence.codec import decode_analysis, encode_analysis
from codeviz.search import search_project
from codeviz.views import ViewRequest, build_visible_graph


class AnalysisCodecTests(unittest.TestCase):
    def test_round_trip_keeps_search_views_health_but_not_ast_or_source(self):
        analysis = analyze_files({
            "model.py": "import torch.nn as nn\nclass Net(nn.Module):\n"
                        "    def forward(self, x): return x\n# PRIVATE_SOURCE_MARKER\n",
            "run.py": "def train(loss, optimizer):\n"
                      "    loss.backward()\n    optimizer.step()\n",
            "bad.py": "def broken(:\n",
        })
        payload = encode_analysis(analysis)
        serialized = json.dumps(payload, ensure_ascii=False)
        self.assertNotIn("PRIVATE_SOURCE_MARKER", serialized)
        self.assertNotIn("ast.Module", serialized)
        restored = decode_analysis(json.loads(serialized))
        self.assertTrue(all(file.tree is None for file in restored.files))
        self.assertEqual([file.was_parsed for file in restored.files],
                         [file.was_parsed for file in analysis.files])
        self.assertEqual(search_project(restored, "反向传播"),
                         search_project(analysis, "反向传播"))
        self.assertEqual(build_visible_graph(restored, ViewRequest(ViewKind.ARCHITECTURE)),
                         build_visible_graph(analysis, ViewRequest(ViewKind.ARCHITECTURE)))
        self.assertEqual(build_visible_graph(restored, ViewRequest(ViewKind.EXECUTION)),
                         build_visible_graph(analysis, ViewRequest(ViewKind.EXECUTION)))

    def test_unknown_type_and_malformed_payload_are_rejected(self):
        with self.assertRaises(ValueError):
            decode_analysis({"@": "object", "type": "os.system", "fields": {}})
        with self.assertRaises(ValueError):
            decode_analysis({"unexpected": "payload"})
