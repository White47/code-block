"""Desktop golden paths through the real bridge, parser, and isolated cache."""

import json
import shutil
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from codeviz.persistence.store import CacheStore
from codeviz.ui.web_bridge import DesktopBridge


class DesktopAcceptanceTests(unittest.TestCase):
    fixture_root = Path(__file__).parent / "fixtures" / "mvp_project"

    def setUp(self) -> None:
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.data_dir = Path(temporary.name) / "cache"
        self.project_root = Path(temporary.name) / "mvp_project"
        shutil.copytree(self.fixture_root, self.project_root)
        self.bridge = self.new_bridge()

    def new_bridge(self) -> DesktopBridge:
        return DesktopBridge(cache_store=CacheStore(self.data_dir))

    def success(self, response):
        self.assertIs(response["ok"], True, response)
        self.assertEqual(response["schemaVersion"], 1)
        self.assertEqual(json.loads(json.dumps(response, allow_nan=False)), response)
        return response["data"]

    def analyze_project(self):
        opened = self.success(self.bridge.open_project(str(self.project_root)))
        self.assertEqual(opened["status"], "NotAnalyzed")
        self.assertEqual(opened["projectPath"], str(self.project_root.resolve()))
        analyzed = self.success(self.bridge.analyze())
        # The fixture intentionally contains broken.py; valid files still work.
        self.assertEqual(analyzed["status"], "Partial")
        self.assertEqual(analyzed["outdatedReasons"], [])
        self.assertIsNone(analyzed["cacheIssue"])
        overview = self.success(self.bridge.get_overview())
        self.assertEqual(overview["projectName"], "mvp_project")
        self.assertEqual(overview["framework"], "PyTorch")
        self.assertLess(overview["health"]["filesParsed"],
                        overview["health"]["filesTotal"])
        return overview

    def item(self, items, field, value):
        matches = [item for item in items if item[field] == value]
        self.assertEqual(len(matches), 1, (field, value, items))
        return matches[0]

    def graph(self, kind, root_id, selected_id=None, bridge=None):
        graph = self.success((bridge or self.bridge).get_view(
            kind, root_id, selected_id))
        self.assertEqual(graph["viewKind"], kind)
        self.assertIn(root_id, graph["rootIds"])
        ids = {node["id"] for node in graph["nodes"]}
        self.assertTrue(ids)
        self.assertEqual(len(ids), len(graph["nodes"]))
        for edge in graph["edges"]:
            self.assertIn(edge["source"], ids)
            self.assertIn(edge["target"], ids)
        if selected_id is not None:
            self.assertIn(selected_id, ids)
        return graph

    def inspector_source(self, kind, root_id, node, expected_text,
                         detail_source_id=None, bridge=None):
        bridge = bridge or self.bridge
        inspector = self.success(bridge.get_inspector(
            kind, root_id, node["id"], detail_source_id=detail_source_id))
        self.assertEqual(inspector["id"], node["id"])
        self.assertEqual(inspector["role"], node["role"])
        self.assertEqual(inspector["sourceAnchors"], node["sourceAnchors"])
        self.assertTrue(inspector["canOpenSource"])
        self.assertTrue(inspector["sourceAnchors"])
        excerpts = []
        for anchor in inspector["sourceAnchors"]:
            source = self.success(bridge.read_source(anchor))
            self.assertEqual(source["filePath"], anchor["filePath"])
            self.assertEqual(source["startLine"], anchor["startLine"])
            self.assertEqual(source["endLine"], anchor["endLine"])
            original = (self.project_root / anchor["filePath"]).read_bytes().decode(
                "utf-8")
            self.assertEqual(source["text"], original)
            self.assertLessEqual(anchor["endLine"], len(original.splitlines()))
            excerpts.append("\n".join(source["text"].splitlines()[
                anchor["startLine"] - 1:anchor["endLine"]]))
        self.assertIn(expected_text, "\n".join(excerpts))
        return inspector

    def test_project_analysis_models_architecture_detail_inspector_source(self):
        overview = self.analyze_project()
        self.assertEqual({model["name"] for model in overview["models"]}, {
            "model.Block", "model.Encoder", "model.Classifier", "model.Baseline",
        })
        model = self.item(overview["models"], "name", "model.Classifier")
        architecture = self.graph("Architecture", model["id"])
        self.assertEqual({node["displayName"] for node in architecture["nodes"]},
                         {"Classifier", "embedding", "encoder", "head"})
        encoder = self.item(architecture["nodes"], "displayName", "encoder")
        self.inspector_source("Architecture", model["id"], encoder,
                              "self.encoder = Encoder()")
        detail = self.success(self.bridge.get_detail_view(
            "Architecture", model["id"], encoder["id"]))
        self.assertEqual(detail["viewKind"], "Architecture")
        definition = self.item(detail["nodes"], "codeName", "model.Encoder")
        self.assertIn(definition["id"], detail["rootIds"])
        self.assertNotIn(definition["id"],
                         {node["id"] for node in architecture["nodes"]})
        self.inspector_source("Architecture", model["id"], definition,
                              "class Encoder(nn.Module):", encoder["id"])
        layers = self.item(detail["nodes"], "codeName", "model.Encoder.layers")
        self.assertEqual(layers["metadata"]["repeatCount"], 2)
        self.inspector_source("Architecture", model["id"], layers,
                              "nn.ModuleList([Block() for _ in range(2)])",
                              encoder["id"])

    def test_train_epoch_execution_roles_inspector_source(self):
        overview = self.analyze_project()
        entry = self.item(overview["entries"], "name", "train_epoch")
        execution = self.graph("Execution", entry["id"])
        epoch = self.item(execution["nodes"], "role", "EpochLoop")
        self.success(self.bridge.expand(epoch["id"]))
        execution = self.graph("Execution", entry["id"])
        batch = self.item(execution["nodes"], "role", "BatchLoop")
        self.success(self.bridge.expand(batch["id"]))
        execution = self.graph("Execution", entry["id"])
        operations = {
            "ForwardPass": "prediction = model(batch)",
            "LossCalculation": "loss = criterion(prediction, target)",
            "BackwardPass": "loss.backward()",
            "ParameterUpdate": "optimizer.step()",
        }
        operation_ids = []
        for role, text in operations.items():
            with self.subTest(role=role):
                node = self.item(execution["nodes"], "role", role)
                self.inspector_source("Execution", entry["id"], node, text)
                operation_ids.append(node["id"])
        control_edges = {(edge["source"], edge["target"])
                         for edge in execution["edges"]
                         if edge["type"] == "CONTROL_FLOW"}
        for before, after in zip(operation_ids, operation_ids[1:], strict=False):
            self.assertIn((before, after), control_edges)

    def test_search_loss_and_backward_reveal_graph_inspector_source(self):
        self.analyze_project()
        for query, kind, expected_role, text in (
            ("loss", "Variable", "Data", "loss = criterion(prediction, target)"),
            ("反向传播", "BackwardPass", "BackwardPass", "loss.backward()"),
        ):
            with self.subTest(query=query):
                response = self.success(self.bridge.search(query))
                self.assertGreater(response["totalCount"], 0)
                self.assertFalse(response["truncated"])
                matches = [result for result in response["results"]
                           if result["kind"] == kind and
                           (kind != "Variable" or
                            result["qualifiedName"] == "run.train_epoch.loss")]
                self.assertEqual(len(matches), 1, response)
                result = matches[0]
                target = result["revealTarget"]
                self.assertIsNone(target["reason"])
                self.assertEqual(target["viewKind"],
                                 "DataFlow" if query == "loss" else "Execution")
                graph = self.graph(target["viewKind"], target["rootId"],
                                   target["selectedId"])
                node = self.item(graph["nodes"], "id", target["selectedId"])
                self.assertEqual(node["role"], expected_role)
                self.assertIn(result["sourceAnchor"], node["sourceAnchors"])
                self.inspector_source(target["viewKind"], target["rootId"],
                                      node, text)

    def test_reopen_restores_analysis_cache_and_view_state(self):
        overview = self.analyze_project()
        model = self.item(overview["models"], "name", "model.Classifier")
        graph = self.graph("Architecture", model["id"])
        encoder = self.item(graph["nodes"], "displayName", "encoder")
        self.success(self.bridge.save_layout({
            "activeView": "Architecture", "selectedId": encoder["id"],
            "openTabs": ["Overview", "Architecture", "Execution"],
            "graphRoots": {"Architecture": model["id"]},
        }))
        self.success(self.bridge.move_node(
            encoder["id"], {"x": 321, "y": -45}, "Architecture"))
        self.success(self.bridge.save_viewport(
            "Architecture", 1.75, {"x": 12, "y": -34}))
        self.success(self.bridge.expand(model["id"]))
        self.success(self.bridge.open_detail_island(
            "Architecture", encoder["id"], {"x": 500, "y": 600}))
        saved = self.success(self.bridge.get_view_state())
        self.assertEqual(saved["selectedId"], encoder["id"])
        self.assertEqual(saved["positionsByView"]["Architecture"][encoder["id"]],
                         [321, -45])
        self.assertEqual(saved["viewportByView"]["Architecture"],
                         {"zoom": 1.75, "pan": [12, -34]})
        self.assertIn(model["id"], saved["expandedIds"])
        self.assertEqual(saved["detailIslands"][0]["sourceNodeId"], encoder["id"])
        reopened = self.new_bridge()
        session = self.success(reopened.open_project(str(self.project_root)))
        # No analyze call: every following response must come from persisted data.
        self.assertEqual(session["status"], "Partial")
        self.assertEqual(session["outdatedReasons"], [])
        self.assertIsNone(session["cacheIssue"])
        self.assertTrue(self.success(reopened.get_cache_info())["exists"])
        self.assertEqual(self.success(reopened.get_overview()), overview)
        self.assertEqual(self.success(reopened.get_view_state()), saved)
        restored = self.graph("Architecture", model["id"], encoder["id"], reopened)
        self.assertEqual(restored["layoutHints"][encoder["id"]], [321, -45])
        self.inspector_source("Architecture", model["id"], encoder,
                              "self.encoder = Encoder()", bridge=reopened)

    def test_source_changed_marks_cached_analysis_outdated_on_reopen(self):
        overview = self.analyze_project()
        source_path = self.project_root / "run.py"
        original = source_path.read_text(encoding="utf-8")
        source_path.write_text(original + "\n# acceptance: source changed\n",
                               encoding="utf-8")
        reopened = self.new_bridge()
        session = self.success(reopened.open_project(str(self.project_root)))
        self.assertEqual(session["status"], "Outdated")
        self.assertEqual(session["outdatedReasons"], ["source changed"])
        self.assertIsNone(session["cacheIssue"])
        self.assertEqual(self.success(reopened.get_session())["status"], "Outdated")
        cache = self.success(reopened.get_cache_info())
        self.assertTrue(cache["exists"])
        self.assertEqual(cache["status"], "Outdated")
        self.assertEqual(cache["outdatedReasons"], ["source changed"])
        self.assertEqual(self.success(reopened.get_overview()), overview)
        refreshed = self.success(reopened.analyze())
        self.assertEqual(refreshed["status"], "Partial")
        self.assertEqual(refreshed["outdatedReasons"], [])
        self.assertEqual(self.success(self.new_bridge().open_project(
            str(self.project_root)))["status"], "Partial")


if __name__ == "__main__":
    unittest.main()
