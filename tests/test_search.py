"""Search uses only indexed definitions and source-backed semantic facts."""

import unittest

from tests.test_training import analyze_files

from codeviz.search import search_project


class SearchTests(unittest.TestCase):
    def test_same_named_variables_remain_scope_disambiguated(self):
        analysis = analyze_files({"run.py": (
            "def train_epoch():\n    loss = 1\n    return loss\n"
            "def validate():\n    loss = 2\n    return loss\n")})
        matches = search_project(analysis, "loss").results
        variables = [item for item in matches if item.source_kind == "symbol"
                     and item.name == "loss"]
        self.assertEqual({item.qualified_name for item in variables},
                         {"run.train_epoch.loss", "run.validate.loss"})
        self.assertEqual({item.source_anchor.start_line for item in variables}, {2, 5})

    def test_chinese_and_english_semantics_resolve_to_same_event(self):
        analysis = analyze_files({"run.py": (
            "import torch\ndef train(loss, optimizer):\n"
            "    criterion = torch.nn.MSELoss()\n"
            "    loss.backward()\n    optimizer.step()\n")})
        chinese = search_project(analysis, "反向传播").results
        english = search_project(analysis, "backward").results
        self.assertEqual({item.id for item in chinese}, {item.id for item in english
                          if item.source_kind == "semantic"})
        self.assertTrue(chinese)
        self.assertEqual(chinese[0].source_anchor.start_line, 4)
        self.assertTrue(any(item.name == "ParameterUpdate"
                            for item in search_project(analysis, "参数更新").results))
        self.assertEqual(
            {item.id for item in search_project(analysis, "parameter update").results},
            {item.id for item in search_project(analysis, "参数更新").results})

    def test_browse_limit_and_empty_match_are_explicit(self):
        analysis = analyze_files({"run.py": "a = 1\nb = 2\nc = 3\n"})
        response = search_project(analysis, "", max_results=2)
        self.assertEqual(len(response.results), 2)
        self.assertTrue(response.truncated)
        self.assertGreater(response.total_count, 2)
        self.assertFalse(search_project(analysis, "does-not-exist").results)
        with self.assertRaises(ValueError):
            search_project(analysis, "", max_results=0)
