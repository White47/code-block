"""Local cache content, invalidation and corruption boundaries."""

import json
import unittest
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from codeviz.analysis.human_tasks import human_task_fact_id
from codeviz.analysis.human_understanding import HumanOperationKind, _fact_id
from codeviz.analysis.model_parameter_sharing import parameter_sharing_fact_id
from codeviz.analysis.project import analyze_project
from codeviz.core import ViewKind
from codeviz.persistence.codec import decode_analysis, encode_analysis
from codeviz.persistence.fingerprint import (
    ANALYSIS_VERSION,
    ProjectFingerprint,
    fingerprint_project,
    outdated_reasons,
)
from codeviz.persistence.store import CacheError, CacheStore, CacheVersionMismatch
from codeviz.views import DetailIslandState, ViewportState, ViewState


class CacheStoreTests(unittest.TestCase):
    def test_store_rejects_human_task_with_unrelated_real_control_node(self):
        with TemporaryDirectory() as project_dir, TemporaryDirectory() as data_dir:
            project = Path(project_dir)
            (project / "train.py").write_text(
                "def step(loss, accumulation_steps):\n"
                "    for _ in range(accumulation_steps):\n"
                "        scaled = loss / accumulation_steps\n"
                "        scaled.backward()\n"
                "    done = True\n",
                encoding="utf-8",
            )
            analysis = analyze_project(project)
            fingerprint = fingerprint_project(project)
            store = CacheStore(Path(data_dir))
            store.save_analysis(project, analysis, fingerprint)
            task = analysis.human_tasks.tasks[0]
            unrelated = next(node for node in analysis.flow.graph.nodes.values()
                             if node.metadata.get("statement_type") == "Assign"
                             and node.source_anchors[0].start_line == 5)
            forged = replace(
                task,
                id=human_task_fact_id(
                    "task", task.owner_symbol_id, task.kind.value, task.source_anchor,
                    unrelated.id, task.callsite_ids, task.target_symbol_ids,
                ),
                control_node_id=unrelated.id,
            )
            tampered = replace(
                analysis,
                human_tasks=replace(analysis.human_tasks, tasks=(forged,)),
            )

            with self.assertRaises(CacheError):
                store.save_analysis(project, tampered, fingerprint)

    def test_store_rejects_parameter_sharing_rebound_to_constructor_assignment(self):
        with TemporaryDirectory() as project_dir, TemporaryDirectory() as data_dir:
            project = Path(project_dir)
            (project / "model.py").write_text(
                "import torch.nn as nn\n"
                "class Net(nn.Module):\n"
                "    def __init__(self):\n"
                "        self.embedding = nn.Embedding(8, 4)\n"
                "        self.head = nn.Linear(4, 8, bias=False)\n"
                "        self.embedding.weight = self.head.weight\n",
                encoding="utf-8",
            )
            analysis = analyze_project(project)
            fingerprint = fingerprint_project(project)
            store = CacheStore(Path(data_dir))
            store.save_analysis(project, analysis, fingerprint)
            fact = analysis.model.parameter_sharings[0]
            wrong_anchor = next(
                node.source_anchors[0] for node in analysis.flow.graph.nodes.values()
                if node.metadata.get("statement_type") == "Assign"
                and node.source_anchors[0].start_line == 4
            )
            forged = replace(
                fact,
                id=parameter_sharing_fact_id(
                    fact.model_definition_id, fact.owner_symbol_id,
                    fact.assigned_instance_id, fact.assigned_attribute,
                    fact.source_instance_id, fact.source_attribute, wrong_anchor,
                ),
                source_anchor=wrong_anchor,
            )
            tampered = replace(
                analysis,
                model=replace(analysis.model, parameter_sharings=(forged,)),
            )

            with self.assertRaises(CacheError):
                store.save_analysis(project, tampered, fingerprint)

    def test_store_rejects_forged_parameter_sharing_left_attribute(self):
        with TemporaryDirectory() as project_dir, TemporaryDirectory() as data_dir:
            project = Path(project_dir)
            (project / "model.py").write_text(
                "import torch.nn as nn\n"
                "class Net(nn.Module):\n"
                "    def __init__(self):\n"
                "        self.left = nn.Linear(2, 2)\n"
                "        self.right = nn.Linear(2, 2)\n"
                "        self.left.weight = self.right.weight\n",
                encoding="utf-8",
            )
            analysis = analyze_project(project)
            fingerprint = fingerprint_project(project)
            fact = analysis.model.parameter_sharings[0]
            forged = replace(
                fact,
                id=parameter_sharing_fact_id(
                    fact.model_definition_id, fact.owner_symbol_id,
                    fact.assigned_instance_id, "bias",
                    fact.source_instance_id, fact.source_attribute,
                    fact.source_anchor,
                ),
                assigned_attribute="bias",
            )
            tampered = replace(
                analysis,
                model=replace(analysis.model, parameter_sharings=(forged,)),
            )

            with self.assertRaises(CacheError):
                CacheStore(Path(data_dir)).save_analysis(
                    project, tampered, fingerprint,
                )

    def test_v6_analysis_is_rejected_before_decode_without_changing_user_state(self):
        self.assertEqual(ANALYSIS_VERSION, "7")
        with self.provenance_fixture() as (project, store, _analysis, _fingerprint):
            path = store.analysis_path(project)
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["fingerprint"]["analysis_version"] = "6"
            payload["analysis"] = {"v6": "must not decode"}
            path.write_text(json.dumps(payload), encoding="utf-8")
            before = {item: item.read_bytes() for item in path.parent.glob("*.json")}

            with patch("codeviz.persistence.store.decode_analysis") as decode:
                with self.assertRaises(CacheVersionMismatch):
                    store.load_analysis(project)
                decode.assert_not_called()
            self.assertEqual({item: item.read_bytes() for item in before}, before)

    def test_store_rejects_human_fact_owner_outside_the_same_analysis(self):
        with TemporaryDirectory() as project_dir, TemporaryDirectory() as data_dir:
            project = Path(project_dir)
            (project / "run.py").write_text(
                "def task():\n    \"\"\"执行任务。\"\"\"\n",
                encoding="utf-8",
            )
            store = CacheStore(Path(data_dir))
            analysis = analyze_project(project)
            fingerprint = fingerprint_project(project)
            store.save_analysis(project, analysis, fingerprint)
            statement = analysis.human_understanding.author_statements[0]
            missing_owner = "node_missing"
            tampered = replace(
                analysis,
                human_understanding=replace(
                    analysis.human_understanding,
                    author_statements=(replace(
                        statement,
                        id=_fact_id(missing_owner, "author_statement", statement.source_anchor),
                        owner_symbol_id=missing_owner,
                    ),),
                ),
            )

            with patch("codeviz.persistence.store.encode_analysis", wraps=encode_analysis) as encode:
                with self.assertRaises(CacheError):
                    store.save_analysis(project, tampered, fingerprint)
                encode.assert_not_called()

            path = store.analysis_path(project)
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["analysis"] = encode_analysis(tampered)
            path.write_text(json.dumps(payload), encoding="utf-8")
            with patch("codeviz.persistence.store.decode_analysis", wraps=decode_analysis) as decode:
                with self.assertRaises(CacheError):
                    store.load_analysis(project)
                decode.assert_called_once()

    def test_store_rejects_author_anchor_rebound_to_another_same_file_owner(self):
        with TemporaryDirectory() as project_dir, TemporaryDirectory() as data_dir:
            project = Path(project_dir)
            (project / "run.py").write_text(
                "def first():\n    \"\"\"第一个。\"\"\"\n\n"
                "def second():\n    \"\"\"第二个。\"\"\"\n",
                encoding="utf-8",
            )
            store = CacheStore(Path(data_dir))
            analysis = analyze_project(project)
            fingerprint = fingerprint_project(project)
            first, second = analysis.human_understanding.author_statements
            forged = replace(
                first,
                id=_fact_id(second.owner_symbol_id, "author_statement", first.source_anchor),
                owner_symbol_id=second.owner_symbol_id,
            )
            tampered = replace(
                analysis,
                human_understanding=replace(
                    analysis.human_understanding,
                    author_statements=(forged, second),
                ),
            )

            with self.assertRaises(CacheError):
                store.save_analysis(project, tampered, fingerprint)

    def test_store_rejects_return_fact_rebound_to_module_owner(self):
        with TemporaryDirectory() as project_dir, TemporaryDirectory() as data_dir:
            project = Path(project_dir)
            (project / "run.py").write_text(
                "def task():\n    return 1\n",
                encoding="utf-8",
            )
            store = CacheStore(Path(data_dir))
            analysis = analyze_project(project)
            fingerprint = fingerprint_project(project)
            operation = analysis.human_understanding.operations[0]
            module_id = next(entry.id for entry in analysis.symbol_index.entries.values()
                             if entry.qualified_name == "run")
            forged = replace(
                operation,
                id=_fact_id(module_id, "operation", operation.source_anchor,
                            operation.kind.value, None),
                owner_symbol_id=module_id,
            )
            tampered = replace(
                analysis,
                human_understanding=replace(
                    analysis.human_understanding,
                    operations=(forged,),
                ),
            )

            with self.assertRaises(CacheError):
                store.save_analysis(project, tampered, fingerprint)

    def test_store_rejects_return_fact_anchored_to_non_return_statement(self):
        with TemporaryDirectory() as project_dir, TemporaryDirectory() as data_dir:
            project = Path(project_dir)
            (project / "run.py").write_text(
                "def task():\n    value = 1\n    return value\n",
                encoding="utf-8",
            )
            store = CacheStore(Path(data_dir))
            analysis = analyze_project(project)
            fingerprint = fingerprint_project(project)
            operation = analysis.human_understanding.operations[0]
            assignment = next(
                node for node in analysis.flow.graph.nodes.values()
                if node.metadata.get("statement_type") == "Assign"
            )
            wrong_anchor = assignment.source_anchors[0]
            forged = replace(
                operation,
                id=_fact_id(operation.owner_symbol_id, "operation", wrong_anchor,
                            operation.kind.value, None),
                source_anchor=wrong_anchor,
            )
            tampered = replace(
                analysis,
                human_understanding=replace(
                    analysis.human_understanding,
                    operations=(forged,),
                ),
            )

            with self.assertRaises(CacheError):
                store.save_analysis(project, tampered, fingerprint)

    def test_store_rejects_nested_return_rebound_to_enclosing_function(self):
        with TemporaryDirectory() as project_dir, TemporaryDirectory() as data_dir:
            project = Path(project_dir)
            (project / "run.py").write_text(
                "def outer():\n    def inner():\n        return 1\n    return inner\n",
                encoding="utf-8",
            )
            store = CacheStore(Path(data_dir))
            analysis = analyze_project(project)
            fingerprint = fingerprint_project(project)
            entries = analysis.symbol_index.entries
            outer_id = next(entry.id for entry in entries.values()
                            if entry.qualified_name == "run.outer")
            inner_id = next(entry.id for entry in entries.values()
                            if entry.qualified_name == "run.outer.inner")
            operation = next(fact for fact in analysis.human_understanding.operations
                             if fact.owner_symbol_id == inner_id)
            forged = replace(
                operation,
                id=_fact_id(outer_id, "operation", operation.source_anchor,
                            operation.kind.value, None),
                owner_symbol_id=outer_id,
            )
            tampered = replace(
                analysis,
                human_understanding=replace(
                    analysis.human_understanding,
                    operations=tuple(forged if fact is operation else fact
                                     for fact in analysis.human_understanding.operations),
                ),
            )

            with self.assertRaises(CacheError):
                store.save_analysis(project, tampered, fingerprint)

    def test_store_rejects_return_kind_inconsistent_with_source_witness(self):
        with TemporaryDirectory() as project_dir, TemporaryDirectory() as data_dir:
            project = Path(project_dir)
            (project / "run.py").write_text(
                "def empty():\n    return\n\ndef valued(value):\n    return value\n",
                encoding="utf-8",
            )
            store = CacheStore(Path(data_dir))
            analysis = analyze_project(project)
            fingerprint = fingerprint_project(project)
            for operation in analysis.human_understanding.operations:
                wrong_kind = (
                    HumanOperationKind.RETURN_VALUE
                    if operation.kind is HumanOperationKind.RETURN_EMPTY
                    else HumanOperationKind.RETURN_EMPTY
                )
                forged = replace(
                    operation,
                    id=_fact_id(operation.owner_symbol_id, "operation",
                                operation.source_anchor, wrong_kind.value, None),
                    kind=wrong_kind,
                )
                tampered = replace(
                    analysis,
                    human_understanding=replace(
                        analysis.human_understanding,
                        operations=tuple(forged if fact is operation else fact
                                         for fact in analysis.human_understanding.operations),
                    ),
                )
                with self.subTest(source_kind=operation.kind), self.assertRaises(CacheError):
                    store.save_analysis(project, tampered, fingerprint)

    def test_store_rejects_author_statement_bound_to_lexical_scope(self):
        with TemporaryDirectory() as project_dir, TemporaryDirectory() as data_dir:
            project = Path(project_dir)
            (project / "run.py").write_text(
                "def task():\n    \"\"\"任务。\"\"\"\n    callback = lambda: 1\n",
                encoding="utf-8",
            )
            store = CacheStore(Path(data_dir))
            analysis = analyze_project(project)
            fingerprint = fingerprint_project(project)
            statement = analysis.human_understanding.author_statements[0]
            lexical = next(entry for entry in analysis.symbol_index.entries.values()
                           if entry.metadata.get("scope_type") == "Lambda")
            forged = replace(
                statement,
                id=_fact_id(lexical.id, "author_statement", lexical.source_anchor),
                owner_symbol_id=lexical.id,
                source_anchor=lexical.source_anchor,
            )
            tampered = replace(
                analysis,
                human_understanding=replace(
                    analysis.human_understanding,
                    author_statements=(forged,),
                ),
            )

            with self.assertRaises(CacheError):
                store.save_analysis(project, tampered, fingerprint)

    def test_store_rejects_operation_with_another_real_callsite(self):
        with TemporaryDirectory() as project_dir, TemporaryDirectory() as data_dir:
            project = Path(project_dir)
            (project / "run.py").write_text(
                "def task(values):\n    print(len(values))\n",
                encoding="utf-8",
            )
            store = CacheStore(Path(data_dir))
            analysis = analyze_project(project)
            fingerprint = fingerprint_project(project)
            operations = analysis.human_understanding.operations
            self.assertEqual(len(operations), 2)
            tampered = replace(
                analysis,
                human_understanding=replace(
                    analysis.human_understanding,
                    operations=(
                        replace(operations[0], callsite_id=operations[1].callsite_id),
                        operations[1],
                    ),
                ),
            )

            with self.assertRaises(CacheError):
                store.save_analysis(project, tampered, fingerprint)

    def test_store_rejects_operation_with_wrong_real_external_target(self):
        with TemporaryDirectory() as project_dir, TemporaryDirectory() as data_dir:
            project = Path(project_dir)
            (project / "run.py").write_text(
                "def task(values):\n    print(len(values))\n",
                encoding="utf-8",
            )
            store = CacheStore(Path(data_dir))
            analysis = analyze_project(project)
            fingerprint = fingerprint_project(project)
            operations = analysis.human_understanding.operations
            stdout = next(fact for fact in operations if fact.kind.value == "standard_output")
            count = next(fact for fact in operations if fact.kind.value == "count")
            wrong_target = count.target_symbol_id
            self.assertIsNotNone(wrong_target)
            forged = replace(
                stdout,
                id=_fact_id(stdout.owner_symbol_id, "operation", stdout.source_anchor,
                            stdout.kind.value, wrong_target),
                target_symbol_id=wrong_target,
            )
            tampered = replace(
                analysis,
                human_understanding=replace(
                    analysis.human_understanding,
                    operations=tuple(forged if fact is stdout else fact for fact in operations),
                ),
            )

            with self.assertRaises(CacheError):
                store.save_analysis(project, tampered, fingerprint)

    def test_store_rejects_call_confidence_upgraded_above_resolution(self):
        with TemporaryDirectory() as project_dir, TemporaryDirectory() as data_dir:
            project = Path(project_dir)
            (project / "run.py").write_text(
                "def task():\n    print('result')\n",
                encoding="utf-8",
            )
            store = CacheStore(Path(data_dir))
            analysis = analyze_project(project)
            fingerprint = fingerprint_project(project)
            operation = analysis.human_understanding.operations[0]
            forged = replace(operation, confidence=operation.confidence.EXACT)
            tampered = replace(
                analysis,
                human_understanding=replace(
                    analysis.human_understanding,
                    operations=(forged,),
                ),
            )

            with self.assertRaises(CacheError):
                store.save_analysis(project, tampered, fingerprint)

    def test_store_requires_exact_truncation_count_and_unique_diagnostic(self):
        source = "\n".join(
            f"def task_{index}():\n    \"\"\"任务 {index}。\"\"\"\n"
            for index in range(513)
        )
        with TemporaryDirectory() as project_dir, TemporaryDirectory() as data_dir:
            project = Path(project_dir)
            (project / "many.py").write_text(source, encoding="utf-8")
            store = CacheStore(Path(data_dir))
            analysis = analyze_project(project)
            fingerprint = fingerprint_project(project)
            store.save_analysis(project, analysis, fingerprint)
            diagnostic = next(issue for issue in analysis.issues
                              if issue.code == "UNDERSTANDING_FACTS_TRUNCATED")
            cases = {
                "count_mismatch": replace(
                    analysis,
                    human_understanding=replace(
                        analysis.human_understanding, omitted_fact_count=999,
                    ),
                ),
                "zero_with_diagnostic": replace(
                    analysis,
                    human_understanding=replace(
                        analysis.human_understanding, omitted_fact_count=0,
                    ),
                ),
                "duplicate_diagnostic": replace(
                    analysis,
                    issues=(*analysis.issues, diagnostic),
                ),
            }

            for risk, tampered in cases.items():
                with self.subTest(risk=risk), self.assertRaises(CacheError):
                    store.save_analysis(project, tampered, fingerprint)

    @contextmanager
    def provenance_fixture(self):
        with TemporaryDirectory() as project_dir, TemporaryDirectory() as data_dir:
            project = Path(project_dir)
            (project / "run.py").write_bytes(b"value = 1\n# PRIVATE_SOURCE_MARKER\n")
            store = CacheStore(Path(data_dir))
            analysis = analyze_project(project)
            fingerprint = fingerprint_project(project)
            store.save_analysis(project, analysis, fingerprint)
            node_id = next(iter(analysis.symbol_index.entries))
            store.save_view_state(project, ViewState(
                active_view=ViewKind.SYMBOL,
                positions_by_view={"Symbol": {node_id: (12., 18.)}}))
            store.save_annotations(project, {node_id: "keep review"})
            yield project, store, analysis, fingerprint

    def assert_source_rejected(self, project, store, analysis, fingerprint, message):
        path = store.analysis_path(project)
        before = {item: item.read_bytes() for item in path.parent.glob("*.json")}
        with patch("codeviz.persistence.store.encode_analysis", wraps=encode_analysis) as encode:
            with self.assertRaises(CacheError) as caught:
                store.save_analysis(project, analysis, fingerprint)
            self.assertEqual(str(caught.exception), message)
            encode.assert_not_called()
        self.assertEqual({item: item.read_bytes() for item in before}, before)
        # Exercise the real decoder and then the load boundary using a tampered cache.
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["analysis"] = encode_analysis(analysis)
        payload["fingerprint"] = fingerprint.to_payload()
        path.write_text(json.dumps(payload), encoding="utf-8")
        tampered = path.read_bytes()
        with patch("codeviz.persistence.store.decode_analysis", wraps=decode_analysis) as decode:
            with self.assertRaises(CacheError) as caught:
                store.load_analysis(project)
            self.assertEqual(str(caught.exception), message)
            decode.assert_called_once()
        self.assertEqual(path.read_bytes(), tampered)
        for item, content in before.items():
            if item != path:
                self.assertEqual(item.read_bytes(), content)
        path.write_bytes(before[path])

    def test_matching_inventory_accepts_reordered_analysis_and_casefold_py_suffix(self):
        with self.provenance_fixture() as (project, store, analysis, fingerprint):
            (project / "other.py").write_bytes(b"other = 2\n")
            analysis = analyze_project(project)
            fingerprint = fingerprint_project(project)
            reversed_analysis = replace(analysis, files=tuple(reversed(analysis.files)))
            store.save_analysis(project, reversed_analysis, fingerprint)
            saved = store.load_analysis(project)
            self.assertIsNotNone(saved)
            self.assertEqual([file.content_hash for file in saved.analysis.files],
                             [file.content_hash for file in reversed_analysis.files])
            renamed = replace(analysis.files[0], relative_path="other.PY")
            upper = replace(analysis, files=(renamed, analysis.files[1]))
            upper_fingerprint = replace(fingerprint, files=tuple(sorted(
                ("other.PY" if path == "other.py" else path, digest)
                for path, digest in fingerprint.files)))
            store.save_analysis(project, upper, upper_fingerprint)
            self.assertEqual(store.load_analysis(project).analysis.files[0].relative_path,
                             "other.PY")

    def test_empty_analysis_and_fingerprint_inventories_are_accepted(self):
        with TemporaryDirectory() as project_dir, TemporaryDirectory() as data_dir:
            project = Path(project_dir)
            analysis = analyze_project(project)
            fingerprint = fingerprint_project(project)
            store = CacheStore(Path(data_dir))
            store.save_analysis(project, analysis, fingerprint)
            saved = store.load_analysis(project)
            self.assertEqual(saved.analysis.files, ())
            self.assertEqual(saved.fingerprint.files, ())

    def test_store_rejects_duplicate_missing_and_extra_inventory_on_save_and_load(self):
        with self.provenance_fixture() as (project, store, analysis, fingerprint):
            file = analysis.files[0]
            cases = (
                (replace(analysis, files=(file, file)), fingerprint),
                (replace(analysis, files=()), fingerprint),
                (replace(analysis, files=(file, replace(file, relative_path="extra.py"))), fingerprint),
                (analysis, replace(fingerprint, files=())),
                (analysis, replace(fingerprint, files=tuple(sorted(
                    (*fingerprint.files, ("extra.py", file.content_hash)))))),
            )
            for index, (candidate, manifest) in enumerate(cases):
                with self.subTest(risk=index):
                    self.assert_source_rejected(project, store, candidate, manifest,
                                                "analysis cache source inventory mismatch")

    def test_store_rejects_noncanonical_or_non_python_paths_on_both_boundaries(self):
        with self.provenance_fixture() as (project, store, analysis, fingerprint):
            cases = (("", "analysis"), ("./run.py", "both"),
                     ("pkg/../run.py", "analysis"), ("pkg//run.py", "fingerprint"),
                     ("pkg\\run.py", "both"), ("/run.py", "analysis"),
                     ("C:/run.py", "fingerprint"), ("../run.py", "both"),
                     ("run.txt", "both"))
            for path, side in cases:
                with self.subTest(path=path, side=side):
                    candidate = (replace(analysis, files=(replace(
                        analysis.files[0], relative_path=path),))
                        if side != "fingerprint" else analysis)
                    manifest = (replace(fingerprint, files=((path, analysis.files[0].content_hash),))
                                if side != "analysis" else fingerprint)
                    self.assert_source_rejected(project, store, candidate, manifest,
                                                "analysis cache source inventory mismatch")

    def test_store_rejects_success_null_and_any_nonnull_hash_mismatch(self):
        with self.provenance_fixture() as (project, store, analysis, fingerprint):
            (project / "bad.py").write_bytes(b"def broken(:\n")
            failed = analyze_project(project).files[0]
            self.assertFalse(failed.was_parsed)
            for parsed in (replace(analysis.files[0], content_hash=None),
                           replace(analysis.files[0], content_hash="0" * 64),
                           replace(failed, relative_path="run.py")):
                with self.subTest(success=parsed.was_parsed, digest=parsed.content_hash):
                    self.assert_source_rejected(project, store,
                                                replace(analysis, files=(parsed,)), fingerprint,
                                                "analysis cache source hash mismatch")

    def test_failed_parse_allows_null_or_matching_hash_without_backfill(self):
        with self.provenance_fixture() as (project, store, analysis, fingerprint):
            (project / "run.py").write_bytes(b"def broken(:\n")
            failed = analyze_project(project)
            fingerprint = fingerprint_project(project)
            self.assertFalse(failed.files[0].was_parsed)
            for digest in (None, failed.files[0].content_hash):
                with self.subTest(digest=digest):
                    candidate = replace(failed, files=(replace(failed.files[0], content_hash=digest),))
                    store.save_analysis(project, candidate, fingerprint)
                    saved = store.load_analysis(project)
                    self.assertFalse(saved.analysis.files[0].was_parsed)
                    self.assertIsNone(saved.analysis.files[0].tree)
                    self.assertEqual(saved.analysis.files[0].content_hash, digest)

    def test_manual_fingerprint_validation_precedes_encoding_and_preserves_old_bytes(self):
        with self.provenance_fixture() as (project, store, analysis, fingerprint):
            file = fingerprint.files[0]
            cases = (
                replace(fingerprint, files=(file, file)),
                replace(fingerprint, files=((file[0], "PRIVATE_HASH_MARKER"),)),
                replace(fingerprint, files=((file[0], "A" * 64),)),
                replace(fingerprint, files=(("z.py", file[1]), file)),
                replace(fingerprint, ignore_paths=("../PRIVATE_PATH_MARKER",)),
                replace(fingerprint, parser_version="old-parser"),
                replace(fingerprint, semantic_version="2"),
                replace(fingerprint, analysis_version="3"),
                replace(fingerprint, analysis_version=4),
            )
            path = store.analysis_path(project)
            before = {item: item.read_bytes() for item in path.parent.glob("*.json")}
            for index, manifest in enumerate(cases):
                with self.subTest(risk=index):
                    with patch("codeviz.persistence.store.encode_analysis", wraps=encode_analysis) as encode:
                        with self.assertRaises(CacheError) as caught:
                            store.save_analysis(project, analysis, manifest)
                        encode.assert_not_called()
                    message = str(caught.exception)
                    self.assertNotIn(str(project), message)
                    for secret in (file[0], file[1], "PRIVATE_HASH_MARKER", "PRIVATE_PATH_MARKER",
                                   "PRIVATE_SOURCE_MARKER", "old-parser"):
                        self.assertNotIn(secret, message)
                    self.assertEqual({item: item.read_bytes() for item in before}, before)
            with patch.object(ProjectFingerprint, "from_payload", wraps=ProjectFingerprint.from_payload) as validate:
                store.save_analysis(project, analysis, fingerprint)
                validate.assert_called_once_with(fingerprint.to_payload())

    def test_load_validates_saved_sources_without_reading_changed_disk_source(self):
        with self.provenance_fixture() as (project, store, analysis, fingerprint):
            (project / "run.py").write_bytes(b"value = 999\n")
            with patch("codeviz.source_handles.read_source_bytes", side_effect=AssertionError("source reread")), \
                    patch("codeviz.persistence.fingerprint.read_source_bytes", side_effect=AssertionError("source reread")), \
                    patch("codeviz.analysis.python.read_source_bytes", side_effect=AssertionError("source reread")):
                saved = store.load_analysis(project)
            self.assertEqual(saved.fingerprint, fingerprint)
            self.assertEqual(saved.analysis.files[0].content_hash, analysis.files[0].content_hash)
            payload = json.loads(store.analysis_path(project).read_text(encoding="utf-8"))
            payload["fingerprint"]["files"][0][1] = "0" * 64
            store.analysis_path(project).write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(CacheError, "^analysis cache source hash mismatch$"):
                store.load_analysis(project)

    def test_load_rejects_decoded_inventory_and_hash_risk_classes(self):
        with self.provenance_fixture() as (project, store, analysis, fingerprint):
            path = store.analysis_path(project)
            original = json.loads(path.read_text(encoding="utf-8"))
            file = analysis.files[0]
            cases = (
                (replace(analysis, files=(file, file)), fingerprint,
                 "analysis cache source inventory mismatch"),
                (replace(analysis, files=()), fingerprint,
                 "analysis cache source inventory mismatch"),
                (analysis, replace(fingerprint, files=()),
                 "analysis cache source inventory mismatch"),
                (replace(analysis, files=(replace(file, relative_path="./run.py"),)),
                 replace(fingerprint, files=(("./run.py", file.content_hash),)),
                 "analysis cache source inventory mismatch"),
                (analysis, replace(fingerprint, files=(("run.py", "0" * 64),)),
                 "analysis cache source hash mismatch"),
                (replace(analysis, files=(replace(file, content_hash=None),)), fingerprint,
                 "analysis cache source hash mismatch"),
            )
            for index, (candidate, manifest, message) in enumerate(cases):
                with self.subTest(risk=index):
                    payload = {**original, "analysis": encode_analysis(candidate),
                               "fingerprint": manifest.to_payload()}
                    path.write_text(json.dumps(payload), encoding="utf-8")
                    before = {item: item.read_bytes() for item in path.parent.glob("*.json")}
                    with patch("codeviz.persistence.store.decode_analysis", wraps=decode_analysis) as decode:
                        with self.assertRaises(CacheError) as caught:
                            store.load_analysis(project)
                        self.assertEqual(str(caught.exception), message)
                        decode.assert_called_once()
                    self.assertEqual({item: item.read_bytes() for item in before}, before)

    def test_save_load_and_source_change_do_not_write_into_project(self):
        with TemporaryDirectory() as project_dir, TemporaryDirectory() as data_dir:
            project, data = Path(project_dir), Path(data_dir)
            (project / "run.py").write_text(
                "def train(loss):\n    loss.backward()\n# PRIVATE_SOURCE_MARKER\n",
                encoding="utf-8")
            before = set(project.iterdir())
            store = CacheStore(data)
            analysis = analyze_project(project)
            fingerprint = fingerprint_project(project)
            store.save_analysis(project, analysis, fingerprint)
            saved = store.load_analysis(project)
            self.assertIsNotNone(saved)
            self.assertEqual(saved.fingerprint, fingerprint)
            self.assertEqual(len(saved.analysis.symbol_index.entries),
                             len(analysis.symbol_index.entries))
            self.assertNotIn("PRIVATE_SOURCE_MARKER",
                             store.analysis_path(project).read_text(encoding="utf-8"))
            store.save_view_state(project, ViewState(
                selected_id="run.train", positions={"run.train": (10.0, 20.0)}))
            store.save_annotations(project, {"run.train": "reviewed"})
            cache_files = tuple(store.analysis_path(project).parent.glob("*.json"))
            self.assertEqual(len(cache_files), 3)
            self.assertTrue(all("PRIVATE_SOURCE_MARKER" not in path.read_text(
                encoding="utf-8") for path in cache_files))
            self.assertEqual(set(project.iterdir()), before)
            self.assertEqual(outdated_reasons(fingerprint, fingerprint_project(project)), ())
            (project / "run.py").write_text("def train(): return 2\n", encoding="utf-8")
            self.assertIn("source changed",
                          outdated_reasons(fingerprint, fingerprint_project(project)))

    def test_added_deleted_config_and_version_changes_are_detected(self):
        with TemporaryDirectory() as project_dir:
            project = Path(project_dir)
            (project / "run.py").write_text("value = 1\n", encoding="utf-8")
            original = fingerprint_project(project)
            (project / "new.py").write_text("other = 2\n", encoding="utf-8")
            self.assertIn("source changed", outdated_reasons(original, fingerprint_project(project)))
            with_new_file = fingerprint_project(project)
            (project / "new.py").unlink()
            self.assertIn("source changed", outdated_reasons(
                with_new_file, fingerprint_project(project)))
            self.assertIn("analysis config changed", outdated_reasons(
                original, fingerprint_project(project, ignore_paths=("ignored",))))
            self.assertIn("parser version changed", outdated_reasons(
                original, fingerprint_project(project, parser_version="future")))
            self.assertIn("semantic rules version changed", outdated_reasons(
                original, fingerprint_project(project, semantic_version="future")))

    def test_corrupt_cache_is_rejected_without_touching_other_files(self):
        with TemporaryDirectory() as project_dir, TemporaryDirectory() as data_dir:
            project, data = Path(project_dir), Path(data_dir)
            (project / "run.py").write_text("value = 1\n", encoding="utf-8")
            store = CacheStore(data)
            store.save_analysis(project, analyze_project(project), fingerprint_project(project))
            unrelated = store.analysis_path(project).parent / "annotations.json"
            unrelated.write_text('{"note":"keep me"}', encoding="utf-8")
            store.analysis_path(project).write_text("{broken", encoding="utf-8")
            with self.assertRaises(CacheError):
                store.load_analysis(project)
            self.assertEqual(unrelated.read_text(encoding="utf-8"), '{"note":"keep me"}')
            store.analysis_path(project).write_text("null", encoding="utf-8")
            with self.assertRaises(CacheError):
                store.load_analysis(project)

    def test_atomic_write_failure_preserves_previous_cache_and_cleans_temp_file(self):
        with TemporaryDirectory() as project_dir, TemporaryDirectory() as data_dir:
            project = Path(project_dir)
            (project / "run.py").write_text("value = 1\n", encoding="utf-8")
            store = CacheStore(Path(data_dir))
            analysis = analyze_project(project)
            fingerprint = fingerprint_project(project)
            store.save_analysis(project, analysis, fingerprint)
            target = store.analysis_path(project)
            previous = target.read_bytes()
            with patch("codeviz.persistence.store.os.fsync", side_effect=OSError("disk error")):
                with self.assertRaises(CacheError):
                    store.save_analysis(project, analysis, fingerprint)
            self.assertEqual(target.read_bytes(), previous)
            self.assertEqual(list(target.parent.glob(".analysis-*.tmp")), [])
            with patch("codeviz.persistence.store.os.replace", side_effect=OSError("disk error")):
                with self.assertRaises(CacheError):
                    store.save_analysis(project, analysis, fingerprint)
            self.assertEqual(target.read_bytes(), previous)
            self.assertEqual(list(target.parent.glob(".analysis-*.tmp")), [])

    def test_cache_directory_inside_project_is_rejected(self):
        with TemporaryDirectory() as project_dir:
            project = Path(project_dir)
            (project / "run.py").write_text("value = 1\n", encoding="utf-8")
            store = CacheStore(project / ".codeviz")
            with self.assertRaises(CacheError):
                store.save_analysis(project, analyze_project(project),
                                    fingerprint_project(project))
            self.assertFalse((project / ".codeviz").exists())

    def test_view_layout_round_trip_is_independent_of_analysis(self):
        with TemporaryDirectory() as project_dir, TemporaryDirectory() as data_dir:
            project = Path(project_dir)
            store = CacheStore(Path(data_dir))
            state = ViewState(active_view=ViewKind.ARCHITECTURE,
                              selected_id="model-a",
                              open_tabs=("graph:Architecture", "source:0:model.py"),
                              graph_roots={"Architecture": "model-a"},
                              positions_by_view={
                                  "Architecture": {"shared": (10.0, 20.0)},
                                  "Execution": {"shared": (30.0, 40.0)},
                              },
                              viewport_by_view={
                                  "Architecture": ViewportState(1.4, (25.0, -10.0)),
                              },
                              detail_islands=(DetailIslandState(
                                  "Architecture:model-a", ViewKind.ARCHITECTURE,
                                  "model-a", (620.0, 180.0)),))
            store.save_view_state(project, state)
            self.assertEqual(store.load_view_state(project), state)
            self.assertFalse(store.analysis_path(project).exists())

    def test_legacy_view_state_defaults_new_canvas_fields(self):
        legacy = {"expanded_ids": [], "positions": {}, "zoom": 1.2,
                  "pan": [4.0, -2.0]}
        restored = ViewState.from_dict(legacy)
        self.assertEqual(restored.viewport_by_view, {})
        self.assertEqual(restored.detail_islands, ())
        self.assertEqual(restored.positions_by_view, {})
        self.assertEqual(restored.zoom, 1.2)
        self.assertEqual(restored.pan, (4.0, -2.0))

    def test_legacy_flat_positions_are_tolerated_by_every_view(self):
        restored = ViewState.from_dict({
            "expanded_ids": [], "positions": {"shared": [12.0, 18.0]}})

        self.assertEqual(restored.positions_for(ViewKind.SYMBOL)["shared"],
                         (12.0, 18.0))
        self.assertEqual(restored.positions_for(ViewKind.DATA_FLOW)["shared"],
                         (12.0, 18.0))

    def test_viewport_serialization_does_not_depend_on_insertion_order(self):
        architecture = ViewportState(1.4, (25.0, -10.0))
        execution = ViewportState(0.8, (-8.0, 12.0))
        first = ViewState(viewport_by_view={
            ViewKind.ARCHITECTURE.value: architecture,
            ViewKind.EXECUTION.value: execution,
        })
        second = ViewState(viewport_by_view={
            ViewKind.EXECUTION.value: execution,
            ViewKind.ARCHITECTURE.value: architecture,
        })

        self.assertEqual(first.to_dict(), second.to_dict())
        self.assertEqual(
            json.dumps(first.to_dict(), ensure_ascii=False, separators=(",", ":")),
            json.dumps(second.to_dict(), ensure_ascii=False, separators=(",", ":")),
        )

    def test_canvas_state_rejects_invalid_numeric_values(self):
        for zoom in (0.24, 2.51, float("nan"), float("inf"), True):
            with self.subTest(zoom=zoom), self.assertRaises(ValueError):
                ViewportState(zoom=zoom)  # type: ignore[arg-type]
        for pan in ((float("nan"), 0.0), (True, 0.0), (0.0, float("inf"))):
            with self.subTest(pan=pan), self.assertRaises(ValueError):
                ViewportState(pan=pan)  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            DetailIslandState("Architecture:model", ViewKind.ARCHITECTURE,
                              "model", (float("nan"), 0.0))

    def test_canvas_state_loader_strictly_rejects_malformed_records(self):
        base: dict[str, object] = {"expanded_ids": [], "positions": {}}
        invalid_payloads = (
            {**base, "viewport_by_view": {"Unknown": {"zoom": 1.0,
                                                         "pan": [0.0, 0.0]}}},
            {**base, "viewport_by_view": {"Architecture": {
                "zoom": 1.0, "pan": [0.0, 0.0], "extra": True}}},
            {**base, "detail_islands": [{
                "id": "x", "view_kind": "Unknown", "source_node_id": "x",
                "position": None}]},
            {**base, "detail_islands": [{
                "id": "x", "view_kind": "Architecture", "source_node_id": "x",
                "position": None}] * 2},
            {**base, "detail_islands": [{
                "id": f"Architecture:{index}", "view_kind": "Architecture",
                "source_node_id": str(index), "position": None}
                for index in range(4)]},
            {**base, "detail_islands": [{
                "id": "x" * 4001, "view_kind": "Architecture",
                "source_node_id": "x", "position": None}]},
            {**base, "detail_islands": [{
                "id": "x", "view_kind": "Architecture", "source_node_id": "x",
                "position": [0.0, float("inf")]}]},
        )
        for payload in invalid_payloads:
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                ViewState.from_dict(payload)
