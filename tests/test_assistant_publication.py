"""C3 Task 1 values/capture and Task 2 offline acceptance/publication risks."""

import builtins
import importlib
import inspect
import os
import unittest
from contextlib import ExitStack, contextmanager
from dataclasses import FrozenInstanceError, fields, is_dataclass, replace
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event, Lock, Thread
from types import MappingProxyType
from unittest.mock import patch

from codeviz.analysis.project import analyze_project
from codeviz.assistant.snapshot import (
    AnchorRecord,
    SnapshotBinding,
    SnapshotLimits,
    build_snapshot,
)
from codeviz.assistant.tool_contracts import EntityRef
from codeviz.core import ViewKind
from codeviz.persistence.fingerprint import fingerprint_project
from codeviz.persistence.store import CacheError, CacheStore
from codeviz.ui.controller import AnalysisStatus, AppController
from codeviz.ui.preferences import PreferencesStore
from codeviz.ui.web_bridge import DesktopBridge

IDENTITY_FIELDS = (
    "publication_revision", "project_epoch", "analysis_generation", "project_key",
    "root", "ignore_paths", "status", "eligible",
)
CONTEXT_FIELDS = IDENTITY_FIELDS + ("snapshot", "navigation_facts", "context_map")


class BoundedMirror:
    """Trusted constructor-only fake: one immutable slot, no service or callbacks."""

    def __init__(self):
        self.current = None

    def replace_current(self, identity):
        self.current = identity


class ThrowingMirror(BoundedMirror):
    """One trusted identity slot; inject a violation before or after its swap."""

    def __init__(self):
        super().__init__()
        self.fail_when = None
        self.fail_on_call = 1
        self.fail_also_on_next = False
        self.fallback_when = None
        self.calls_since_arm = 0
        self.first_since_arm = None
        self.eligible_attempted = None
        self.attempted = None

    def replace_current(self, identity):
        if self.fail_when is not None:
            self.calls_since_arm += 1
            if self.calls_since_arm == 1:
                self.first_since_arm = identity
            if identity.eligible:
                self.eligible_attempted = identity
            if (self.calls_since_arm == self.fail_on_call or
                    (self.fail_also_on_next and
                     self.calls_since_arm == self.fail_on_call + 1)):
                self.attempted = identity
                when = (self.fallback_when if self.calls_since_arm > self.fail_on_call
                        else self.fail_when)
                if when == "before":
                    raise RuntimeError("trusted mirror failed before swap")
                self.current = identity
                raise RuntimeError("trusted mirror failed after swap")
        self.current = identity


class DrainingMirror(ThrowingMirror):
    """Observe post-publication drain without changing the trusted mirror port."""

    def __init__(self):
        super().__init__()
        self.publication_lock = None
        self.lock = Lock()
        self.drain_calls = 0
        self.replacement_locked = False

    def replace_current(self, identity):
        with self.lock:
            self.replacement_locked = self.publication_lock.locked()
            super().replace_current(identity)

    def _drain_pending_closures(self):
        if self.publication_lock.locked() or self.lock.locked():
            raise AssertionError("drain ran under publication or service lock")
        self.drain_calls += 1


class RevokingMirror:
    """Only the reserved trusted port: two permit slots and one pending revision."""

    def __init__(self):
        self.current = None
        self.permits = (None, None)
        self.cancel_requested = (False, False)
        self.pending_revision = None
        self.lock = Lock()
        self.publication_lock = None
        self.transaction_locked = True

    def replace_current(self, identity):
        with self.lock:
            if self.publication_lock is not None:
                self.transaction_locked &= self.publication_lock.locked()
            self.cancel_requested = tuple(
                cancelled or (permit is not None and permit != identity)
                for permit, cancelled in zip(self.permits, self.cancel_requested, strict=True)
            )
            self.current = identity
            self.pending_revision = identity.publication_revision


class ExcludedPublicationValue:
    """Poison for excluded AST/metadata, never a replacement analysis service."""

    def __getattribute__(self, name):
        raise AssertionError(f"publication touched excluded value: {name}")

    def __iter__(self):
        raise AssertionError("publication iterated excluded value")

    def __bool__(self):
        raise AssertionError("publication inspected excluded value")


class AssistantPublicationTests(unittest.TestCase):
    def contract(self, *names):
        try:
            module = importlib.import_module("codeviz.ui.assistant_context")
        except ModuleNotFoundError as error:
            if error.name != "codeviz.ui.assistant_context":
                raise
            self.fail("Missing C3 Task1 value module: codeviz.ui.assistant_context")
        missing = [name for name in names if not hasattr(module, name)]
        self.assertEqual(missing, [], f"Missing C3 Task1 exports: {missing}")
        return module

    def capture_port(self, controller):
        capture = getattr(controller, "capture_assistant_context", None)
        self.assertTrue(callable(capture), "Missing C3 Task1 capture_assistant_context")
        self.assertEqual(tuple(inspect.signature(capture).parameters), ())
        return capture

    def assert_initial(self, context):
        self.assertEqual(
            tuple(getattr(context, name) for name in IDENTITY_FIELDS),
            (0, None, None, None, None, (), AnalysisStatus.NOT_ANALYZED, False),
        )
        self.assertIsNone(context.snapshot)
        for mapping in (context.navigation_facts, context.context_map):
            self.assertIsInstance(mapping, MappingProxyType)
            self.assertEqual(dict(mapping), {})
            with self.assertRaises(TypeError):
                mapping["unexpected"] = None

    def assert_deep_frozen(self, value):
        if value is None or isinstance(value, (str, int, bool, Path)):
            return
        if isinstance(value, tuple):
            for item in value:
                self.assert_deep_frozen(item)
        elif isinstance(value, MappingProxyType):
            for key, item in value.items():
                self.assert_deep_frozen(key)
                self.assert_deep_frozen(item)
        else:
            self.assertTrue(is_dataclass(value), f"Unexpected retained value: {type(value)}")
            self.assertTrue(type(value).__dataclass_params__.frozen)
            self.assertFalse(hasattr(value, "__dict__"))
            for field in fields(value):
                self.assert_deep_frozen(getattr(value, field.name))

    @contextmanager
    def capture_only(self, controller):
        """No disk, graph construction, analysis, or even a cached SDK import."""
        with ExitStack() as stack:
            for target in (
                "builtins.open", "os.open", "pathlib.Path.open",
                "pathlib.Path.read_text", "pathlib.Path.read_bytes",
                "pathlib.Path.stat", "pathlib.Path.resolve",
                "codeviz.ui.controller.analyze_project",
                "codeviz.ui.controller.fingerprint_project",
                "codeviz.ui.controller.build_visible_graph",
                "codeviz.ui.controller.build_architecture_structure",
                "codeviz.ui.controller.StructureResolver",
                "codeviz.navigation.plan_reveal",
                "codeviz.assistant.snapshot.build_snapshot",
                "importlib.import_module",
            ):
                stack.enter_context(patch(target, side_effect=AssertionError(target)))
            for name in ("view", "detail_view", "read_source"):
                stack.enter_context(patch.object(
                    controller, name, side_effect=AssertionError(name)))
            # Also covers imports through aliases bound in the future controller.
            stack.enter_context(patch.object(
                builtins, "__import__", side_effect=AssertionError("capture imported a module")))
            yield

    def capture_before_release(self, lock, capture):
        """Two real threads; timeout is deadlock protection, not a latency budget."""
        held, release, finished = Event(), Event(), Event()
        result = []

        def hold():
            with lock:
                held.set()
                release.wait(5)

        def call():
            try:
                result.append(capture())
            except Exception as error:
                result.append(error)
            finally:
                finished.set()

        holder = Thread(target=hold, daemon=True)
        caller = Thread(target=call, daemon=True)
        holder.start()
        try:
            self.assertTrue(held.wait(2), "lock holder did not start")
            caller.start()
            self.assertTrue(finished.wait(2), "capture waited for an unrelated/held lock")
        finally:
            release.set()
            holder.join(2)
            if caller.ident is not None:
                caller.join(2)
        self.assertFalse(holder.is_alive())
        self.assertFalse(caller.is_alive())
        self.assertEqual(len(result), 1)
        return result[0]

    def test_initial_identity_read_only_capture_properties_and_constructor_port(self):
        parameter = inspect.signature(AppController).parameters.get("assistant_publication_mirror")
        self.assertIsNotNone(parameter, "Missing C3 Task1 constructor mirror keyword")
        self.assertEqual(parameter.kind, inspect.Parameter.KEYWORD_ONLY)
        self.assertIsNone(parameter.default)
        for mirror in (None, BoundedMirror()):
            controller = AppController(ignore_paths=("data",), assistant_publication_mirror=mirror)
            capture = self.capture_port(controller)
            initial = capture()
            self.assert_initial(initial)
            self.assertIs(capture(), initial)
            self.assertIs(controller._assistant_context, initial)
            self.assertIs(controller._assistant_publication_mirror, mirror)
            self.assertNotIn("_project_epoch", vars(controller))
            replacement = replace(initial, project_epoch="opaque-epoch", analysis_generation=7)
            for name in ("project_epoch", "analysis_generation"):
                descriptor = getattr(type(controller), name, None)
                self.assertIsInstance(descriptor, property)
                self.assertIsNone(descriptor.fset)
                self.assertIsNone(getattr(controller, name))
                with self.assertRaises(AttributeError):
                    setattr(controller, name, "independent-identity")
                with patch.object(controller, "capture_assistant_context", return_value=replacement):
                    self.assertEqual(getattr(controller, name), getattr(replacement, name))
            self.assert_initial(initial)

    def test_exact_frozen_dto_fields_exclude_analysis_ast_metadata_and_source(self):
        module = self.contract(
            "ViewNodeKey", "NodeContext", "NavigationFact", "AssistantContext",
            "PublicationIdentity", "PublicationMirror", "PublicationError",
        )
        schemas = {
            "ViewNodeKey": ("view_kind", "root_id", "detail_source_id", "visible_node_id"),
            "NodeContext": ("targets", "source_anchors", "source_only", "truncated", "reason"),
            "NavigationFact": ("source_anchors", "visible_locations"),
            "AssistantContext": CONTEXT_FIELDS,
            "PublicationIdentity": IDENTITY_FIELDS,
        }
        for name, expected in schemas.items():
            with self.subTest(dto=name):
                value_type = getattr(module, name)
                self.assertTrue(is_dataclass(value_type))
                self.assertEqual(tuple(field.name for field in fields(value_type)), expected)
                self.assertTrue(value_type.__dataclass_params__.frozen)
                self.assertEqual(set(value_type.__slots__), set(expected))
        self.assertTrue(issubclass(module.PublicationError, ValueError))
        self.assertTrue(callable(module.PublicationMirror.replace_current))
        initial = self.capture_port(AppController())()
        self.assertIsInstance(initial, module.AssistantContext)
        self.assert_deep_frozen(initial)
        identity = module.PublicationIdentity(**{
            name: getattr(initial, name) for name in IDENTITY_FIELDS})
        self.assert_deep_frozen(identity)
        # Exact constructor schema forbids retaining analysis/AST/metadata/text/services.
        arguments = {name: getattr(initial, name) for name in CONTEXT_FIELDS}
        for forbidden in ("analysis", "parsed_file", "tree", "graph", "view_state",
                          "metadata", "source_text", "callback", "service"):
            with self.subTest(forbidden=forbidden), self.assertRaises(TypeError):
                module.AssistantContext(**arguments, **{forbidden: object()})

    def test_owned_mapping_proxies_nested_values_typed_collisions_and_retained_context(self):
        module = self.contract("ViewNodeKey", "NodeContext", "NavigationFact", "AssistantContext")
        initial = self.capture_port(AppController())()
        anchor = AnchorRecord("run.py", 1, None, 1, None)
        targets = tuple(EntityRef(kind, "same-id") for kind in ("symbol", "code", "semantic"))
        keys = (
            module.ViewNodeKey(ViewKind.SYMBOL, None, None, "same-id"),
            module.ViewNodeKey(ViewKind.SYMBOL, "root", None, "same-id"),
            module.ViewNodeKey(ViewKind.SYMBOL, "root", "detail", "same-id"),
            module.ViewNodeKey(ViewKind.EXECUTION, None, None, "same-id"),
        )
        facts = {target: module.NavigationFact((anchor,), (keys[0],)) for target in targets}
        nodes = {key: module.NodeContext(targets, (anchor,), False, False, None) for key in keys}
        for wrap in (dict, MappingProxyType):
            with self.subTest(input_mapping=wrap.__name__):
                caller_facts, caller_nodes = dict(facts), dict(nodes)
                retained = replace(initial, navigation_facts=wrap(caller_facts),
                                   context_map=wrap(caller_nodes))
                self.assert_deep_frozen(retained)
                self.assertIsInstance(retained.navigation_facts, MappingProxyType)
                self.assertIsInstance(retained.context_map, MappingProxyType)
                caller_facts.clear()
                caller_nodes.clear()
                self.assertEqual(dict(retained.navigation_facts), facts)
                self.assertEqual(dict(retained.context_map), nodes)
                self.assertEqual(len(retained.navigation_facts), 3)
                self.assertEqual(len(retained.context_map), 4)
                newer = replace(retained, publication_revision=retained.publication_revision + 1,
                                navigation_facts={}, context_map={})
                self.assertEqual(dict(newer.navigation_facts), {})
                self.assertEqual(dict(newer.context_map), {})
                self.assertEqual(dict(retained.navigation_facts), facts)
                self.assertEqual(dict(retained.context_map), nodes)
                for value, field in ((retained, "eligible"), (keys[0], "visible_node_id"),
                                     (nodes[keys[0]], "reason"), (facts[targets[0]], "source_anchors"),
                                     (targets[0], "id"), (anchor, "file_path")):
                    with self.assertRaises(FrozenInstanceError):
                        setattr(value, field, None)
                for mapping in (retained.navigation_facts, retained.context_map):
                    with self.assertRaises(TypeError):
                        mapping["new"] = None
        self.assert_initial(initial)

    def test_publication_lock_contention_returns_fixed_busy_without_waiting_or_work(self):
        controller = AppController()
        capture = self.capture_port(controller)
        module = self.contract("PublicationError")
        lock = getattr(controller, "_assistant_publication_lock", None)
        self.assertIsNotNone(lock, "Missing dedicated C3 publication lock")
        initial = capture()
        with self.capture_only(controller):
            errors = [self.capture_before_release(lock, capture) for _ in range(2)]
            self.assertIs(capture(), initial)
        for error in errors:
            self.assertIsInstance(error, module.PublicationError)
            self.assertEqual(error.code, "AI_ANALYSIS_BUSY")
            self.assertTrue(str(error))
        self.assertEqual(str(errors[0]), str(errors[1]))
        self.assert_initial(initial)

    def test_existing_bridge_operation_lock_does_not_block_capture_or_build_read_import(self):
        controller = AppController()
        capture = self.capture_port(controller)
        with TemporaryDirectory() as temporary:
            cache = CacheStore(Path(temporary))
            preferences = PreferencesStore(Path(temporary))
            # Keep the real bridge lock; settings loading is unrelated Task1 I/O.
            with patch.object(DesktopBridge, "_load_analysis_settings"):
                bridge = DesktopBridge(controller, cache, preferences)
            initial = capture()
            self.assertIsNot(controller._assistant_publication_lock, bridge._lock)
            with self.capture_only(controller):
                captured = self.capture_before_release(bridge._lock, capture)
            self.assertIs(captured, initial)
            self.assert_initial(captured)

    def task2_ports(self, controller):
        contracts = {
            "_assistant_invalidate": ("status", "root", "ignore_paths", "rotate_epoch"),
            "_assistant_commit": ("expected", "candidate", "accepted_analysis"),
            "_assistant_publish_accepted": ("expected", "accepted_analysis"),
        }
        for name, parameters in contracts.items():
            port = getattr(controller, name, None)
            self.assertTrue(callable(port), f"Missing C3 Task2 port: {name}")
            signature = inspect.signature(port)
            self.assertEqual(tuple(signature.parameters), parameters)
            if name == "_assistant_invalidate":
                for parameter in signature.parameters.values():
                    self.assertEqual(parameter.kind, inspect.Parameter.KEYWORD_ONLY)
                self.assertIs(signature.parameters["rotate_epoch"].default, False)

    @contextmanager
    def task2_project(self, *, cache=False, mirror=None):
        with TemporaryDirectory() as temporary:
            root = Path(temporary).resolve() / "project"
            root.mkdir()
            (root / "run.py").write_text(
                "def leaf(x):\n    return x + 1\n"
                "def train(x):\n    loss = leaf(x)\n    loss.backward()\n    return loss\n",
                encoding="utf-8",
            )
            store = CacheStore(Path(temporary) / "cache") if cache else None
            controller = AppController(store, assistant_publication_mirror=mirror)
            controller.open_project(root)
            yield controller, root, store

    def assert_ineligible(self, context, status, generation):
        self.assertEqual(context.status, status)
        self.assertEqual(context.analysis_generation, generation)
        self.assertFalse(context.eligible)
        self.assertIsNone(context.snapshot)
        self.assertEqual(dict(context.navigation_facts), {})
        self.assertEqual(dict(context.context_map), {})

    def snapshot_facts(self, snapshot):
        return tuple(getattr(snapshot, name) for name in (
            "file_inventory", "symbol_inventory", "code_node_inventory",
            "code_edge_inventory", "component_inventory", "relation_inventory",
            "semantic_roots",
        ))

    @contextmanager
    def snapshot_builder(self, replacement):
        # Observe the real B3 seam regardless of the controller's import spelling.
        module = importlib.import_module("codeviz.ui.controller")
        with ExitStack() as stack:
            stack.enter_context(patch("codeviz.assistant.snapshot.build_snapshot", replacement))
            if hasattr(module, "build_snapshot"):
                stack.enter_context(patch.object(module, "build_snapshot", replacement))
            yield

    def test_task2_real_fresh_cache_parity_poison_and_detached_old_graph(self):
        with self.task2_project(cache=True) as (controller, root, store):
            opened = controller.capture_assistant_context()
            result = controller.analyze()
            fresh = controller.capture_assistant_context()
            self.assertTrue(fresh.eligible, "fresh accepted analysis was not published")
            self.assert_ineligible(opened, AnalysisStatus.NOT_ANALYZED, 1)
            self.assertEqual(fresh.analysis_generation, 2)
            self.assertEqual(fresh.project_epoch, opened.project_epoch)
            self.assertEqual(fresh.project_key, os.path.normcase(os.path.normpath(str(root))))
            self.assertEqual(controller._source_editing._epoch, fresh.project_epoch)
            self.assertEqual(controller._source_editing._root, root)
            self.assertEqual(fresh.snapshot.binding, SnapshotBinding(
                str(root), (), fresh.project_epoch, 2))
            self.assertEqual(dict(fresh.context_map), {})
            inventories = self.snapshot_facts(fresh.snapshot)
            self.assertTrue(fresh.snapshot.symbol_inventory)
            for file in fresh.snapshot.file_inventory:
                self.assertIsNotNone(file.content_hash)
            expected_targets = {
                EntityRef(kind, record.id)
                for kind, records in (
                    ("symbol", fresh.snapshot.symbol_inventory),
                    ("code", fresh.snapshot.code_node_inventory),
                    ("semantic", fresh.snapshot.component_inventory))
                for record in records if len(record.id) <= 512
            }
            self.assertEqual(set(fresh.navigation_facts), expected_targets)
            self.assertTrue(all(not fact.visible_locations
                                for fact in fresh.navigation_facts.values()))
            self.assert_deep_frozen(fresh)

            # Actual CacheStore save (through analyze), actual load, actual reopen.
            cached = store.load_analysis(root)
            self.assertIsNotNone(cached)
            self.assertTrue(all(file.tree is None for file in cached.analysis.files))
            reopened = AppController(store)
            with patch("codeviz.ui.controller.analyze_project",
                       side_effect=AssertionError("cache rebuilt AST")):
                reopened.open_project(root)
            restored = reopened.capture_assistant_context()
            self.assertTrue(restored.eligible)
            self.assertEqual(restored.analysis_generation, 1)
            self.assertNotEqual(restored.project_epoch, fresh.project_epoch)
            self.assertEqual(self.snapshot_facts(restored.snapshot), inventories)
            self.assertEqual(dict(restored.navigation_facts), dict(fresh.navigation_facts))

            self.task2_ports(controller)
            poison = ExcludedPublicationValue()
            old_metadata = [node.metadata for node in result.flow.graph.nodes.values()]
            poisoned = replace(result, files=tuple(
                replace(file, tree=poison, parsed_success=True) for file in result.files))
            for key, node in tuple(poisoned.flow.graph.nodes.items()):
                poisoned.flow.graph.nodes[key] = replace(node, metadata=poison)
            poisoned = replace(poisoned, semantic=replace(
                poisoned.semantic, graph=replace(poisoned.semantic.graph, components=tuple(
                    replace(component, metadata=poison)
                    for component in poisoned.semantic.graph.components))))
            controller.analysis = poisoned
            with patch("codeviz.ui.controller.StructureResolver",
                       side_effect=AssertionError("initial acceptance scanned metadata")), \
                    patch("codeviz.navigation.plan_reveal",
                          side_effect=AssertionError("initial acceptance built routes")):
                self.assertTrue(controller._assistant_publish_accepted(fresh, poisoned))
            detached = controller.capture_assistant_context()
            self.assertEqual(self.snapshot_facts(detached.snapshot), inventories)
            facts = dict(detached.navigation_facts)
            # Mutation is AFTER publication, never during unprotected construction.
            poisoned.symbol_index.entries.clear()
            poisoned.flow.graph.nodes.clear()
            poisoned.flow.graph.edges.clear()
            for metadata in old_metadata:
                metadata.clear()
                metadata["excluded"] = poison
            self.assertEqual(self.snapshot_facts(fresh.snapshot), inventories)
            self.assertEqual(self.snapshot_facts(detached.snapshot), inventories)
            self.assertEqual(dict(detached.navigation_facts), facts)
            self.assert_deep_frozen(detached)

    def test_task2_expected_context_cas_and_bounded_atomic_mirror(self):
        mirror = RevokingMirror()
        with self.task2_project(mirror=mirror) as (controller, root, _store):
            self.task2_ports(controller)
            mirror.publication_lock = controller._assistant_publication_lock
            result = controller.analyze()
            accepted = controller.capture_assistant_context()
            self.assertTrue(accepted.eligible)
            self.assertEqual(tuple(getattr(mirror.current, field) for field in IDENTITY_FIELDS),
                             tuple(getattr(accepted, field) for field in IDENTITY_FIELDS))
            mirror.permits = (mirror.current, mirror.current)

            def guard(original):
                def checked(*args, **kwargs):
                    self.assertFalse(controller._assistant_publication_lock.locked(),
                                     "I/O or clock under publication lock")
                    self.assertFalse(mirror.lock.locked(), "I/O or clock under mirror lock")
                    return original(*args, **kwargs)
                return checked

            with ExitStack() as stack:
                for target in ("builtins.open", "os.open", "pathlib.Path.open",
                               "time.time", "time.monotonic", "time.perf_counter"):
                    module_name, name = target.rsplit(".", 1)
                    owner = Path if module_name == "pathlib.Path" else importlib.import_module(
                        module_name)
                    stack.enter_context(patch(target, guard(getattr(owner, name))))
                invalid = controller._assistant_invalidate(
                    status=AnalysisStatus.OUTDATED, root=root, ignore_paths=())
            self.assert_ineligible(invalid, AnalysisStatus.OUTDATED, 3)
            self.assertEqual(invalid.project_epoch, accepted.project_epoch)
            self.assertGreater(invalid.publication_revision, accepted.publication_revision)
            self.assertEqual(mirror.cancel_requested, (True, True))
            self.assertNotEqual(mirror.current, mirror.permits[0])
            self.assertEqual(mirror.pending_revision, invalid.publication_revision)
            self.assertFalse(controller._assistant_commit(accepted, accepted, result))
            self.assertIs(controller.capture_assistant_context(), invalid)

            # Build real B3 while a publication-only interleaving supersedes it.
            for interleaving in ("generation", "revision"):
                with self.subTest(interleaving=interleaving):
                    result = controller.analyze()
                    expected = controller.capture_assistant_context()
                    entered, release = Event(), Event()
                    answers, errors = [], []

                    def paused_builder(analysis, *, entered=entered, release=release, **kwargs):
                        self.assertFalse(controller._assistant_publication_lock.locked())
                        self.assertFalse(mirror.lock.locked())
                        snapshot = build_snapshot(analysis, **kwargs)
                        entered.set()
                        if not release.wait(5):
                            raise AssertionError("snapshot barrier was not released")
                        return snapshot

                    def publish(answers=answers, errors=errors, expected=expected, result=result):
                        try:
                            answers.append(controller._assistant_publish_accepted(expected, result))
                        except Exception as error:
                            errors.append(error)

                    with self.snapshot_builder(paused_builder):
                        worker = Thread(target=publish, daemon=True)
                        worker.start()
                        try:
                            self.assertTrue(entered.wait(2))
                            if interleaving == "generation":
                                superseding = controller._assistant_invalidate(
                                    status=AnalysisStatus.ANALYZING, root=root, ignore_paths=())
                            else:
                                self.assertTrue(controller._assistant_commit(expected, expected, result))
                                superseding = controller.capture_assistant_context()
                                self.assertEqual(superseding.analysis_generation,
                                                 expected.analysis_generation)
                            release.set()
                            worker.join(2)
                        finally:
                            release.set()
                            worker.join(2)
                    self.assertFalse(worker.is_alive())
                    self.assertEqual(errors, [])
                    self.assertEqual(answers, [False])
                    self.assertIs(controller.capture_assistant_context(), superseding)
                    self.assertEqual(mirror.pending_revision, superseding.publication_revision)

            result = controller.analyze()
            expected = controller.capture_assistant_context()
            for attribute, value in (("analysis", replace(result)),
                                     ("project_root", root.parent),
                                     ("ignore_paths", ("run.py",)),
                                     ("status", AnalysisStatus.OUTDATED)):
                with self.subTest(changed=attribute), patch.object(controller, attribute, value):
                    self.assertFalse(controller._assistant_commit(expected, expected, result))
                    self.assertFalse(controller._assistant_publish_accepted(expected, result))
                    self.assertIs(controller.capture_assistant_context(), expected)
            for field, value in (("publication_revision", expected.publication_revision + 1),
                                 ("analysis_generation", expected.analysis_generation + 1),
                                 ("project_epoch", "different-epoch"),
                                 ("ignore_paths", ("run.py",)),
                                 ("status", AnalysisStatus.PARTIAL)):
                with self.subTest(expected_field=field):
                    self.assertFalse(controller._assistant_commit(
                        replace(expected, **{field: value}), expected, result))
                    self.assertIs(controller.capture_assistant_context(), expected)
            other_root = replace(expected, root=root.parent, project_key=os.path.normcase(
                os.path.normpath(str(root.parent))))
            self.assertFalse(controller._assistant_commit(other_root, expected, result))
            wrong_binding = build_snapshot(result, binding=SnapshotBinding(
                str(root), (), "other-snapshot-epoch", expected.analysis_generation))
            for candidate in (replace(expected, snapshot=wrong_binding),
                              replace(expected, status=AnalysisStatus.ANALYZING)):
                with self.subTest(candidate_status=candidate.status,
                                  binding=candidate.snapshot.binding.project_epoch):
                    self.assertFalse(controller._assistant_commit(expected, candidate, result))
                    self.assertIs(controller.capture_assistant_context(), expected)
            self.assertTrue(mirror.transaction_locked)
            self.assertEqual(tuple(getattr(mirror.current, field) for field in IDENTITY_FIELDS),
                             tuple(getattr(expected, field) for field in IDENTITY_FIELDS))

    def test_post_lock_drain_runs_on_success_and_mirror_failure(self):
        publication_error = self.contract("PublicationError").PublicationError
        for failure in (None, "before", "after"):
            with self.subTest(failure=failure):
                mirror = DrainingMirror()
                controller = AppController(assistant_publication_mirror=mirror)
                mirror.publication_lock = controller._assistant_publication_lock
                expected = controller.capture_assistant_context()
                candidate = replace(expected, publication_revision=1)
                mirror.fail_when = failure
                if failure is None:
                    self.assertTrue(controller._assistant_replace(expected, candidate))
                    self.assertIs(controller.capture_assistant_context(), candidate)
                else:
                    with self.assertRaises(publication_error):
                        controller._assistant_replace(expected, candidate)
                    self.assertIs(controller.capture_assistant_context(), expected)
                self.assertTrue(mirror.replacement_locked)
                self.assertEqual(mirror.drain_calls, 1)

    def test_task2_mirror_exception_aborts_open_and_analyze_before_mutation(self):
        publication_error = self.contract("PublicationError").PublicationError
        for action in ("open", "analyze"):
            for when in ("before", "after"):
                mirror = ThrowingMirror()
                with self.subTest(action=action, mirror_failure=when), self.task2_project(
                        cache=True, mirror=mirror) as (controller, root, store):
                    old_result = controller.analyze()
                    old = controller.capture_assistant_context()
                    old_slot = mirror.current
                    old_source_root = controller._source_editing._root
                    old_source_epoch = controller._source_editing._epoch
                    old_ignore_paths = controller.ignore_paths
                    old_status = controller.status
                    self.assertTrue(old.eligible)
                    self.assertEqual(tuple(getattr(old_slot, field) for field in IDENTITY_FIELDS),
                                     tuple(getattr(old, field) for field in IDENTITY_FIELDS))

                    next_root = root.parent / "next-project"
                    next_root.mkdir()
                    mirror.fail_when = when
                    with patch.object(controller._source_editing, "bind_project") as binder, \
                            patch.object(store, "load_analysis") as cache_loader, \
                            patch("codeviz.ui.controller.fingerprint_project") as fingerprint, \
                            patch("codeviz.ui.controller.analyze_project") as analyzer:
                        with self.assertRaises(publication_error) as caught:
                            if action == "open":
                                controller.open_project(next_root)
                            else:
                                controller.analyze()
                    self.assertEqual(caught.exception.code, "AI_ANALYSIS_REQUIRED")
                    binder.assert_not_called()
                    cache_loader.assert_not_called()
                    fingerprint.assert_not_called()
                    analyzer.assert_not_called()
                    self.assertTrue(controller.capture_assistant_context() is old,
                                    "mirror failure changed controller identity")
                    self.assertIs(controller.analysis, old_result)
                    self.assertEqual(controller.project_root, root)
                    self.assertEqual(controller.ignore_paths, old_ignore_paths)
                    self.assertEqual(controller.status, old_status)
                    self.assertEqual(controller._source_editing._root, old_source_root)
                    self.assertEqual(controller._source_editing._epoch, old_source_epoch)
                    self.assertTrue(old.eligible)
                    self.assertIsNotNone(old.snapshot)

                    attempted = mirror.attempted
                    self.assertIsNotNone(attempted)
                    self.assertEqual(attempted.publication_revision,
                                     old.publication_revision + 1)
                    self.assertEqual(attempted.analysis_generation,
                                     old.analysis_generation + 1)
                    self.assertFalse(attempted.eligible)
                    self.assertEqual(attempted.root, next_root if action == "open" else root)
                    self.assertIs(mirror.current, old_slot if when == "before" else attempted)
                    old_fields = tuple(getattr(old, field) for field in IDENTITY_FIELDS)
                    mirrored_fields = tuple(getattr(mirror.current, field)
                                            for field in IDENTITY_FIELDS)
                    attempted_fields = tuple(getattr(attempted, field)
                                             for field in IDENTITY_FIELDS)
                    self.assertEqual(old_fields == mirrored_fields, when == "before")
                    self.assertNotEqual(old_fields, attempted_fields)

    def test_task2_completion_mirror_failure_after_successful_invalidation_stays_local_failed(self):
        publication_error = self.contract("PublicationError").PublicationError
        for when in ("before", "after"):
            mirror = ThrowingMirror()
            with self.subTest(completion_mirror_failure=when), self.task2_project(
                    mirror=mirror) as (controller, root, _store):
                old_result = controller.analyze()
                old = controller.capture_assistant_context()
                self.assertTrue(old.eligible)
                mirror.fail_when = when
                mirror.fail_on_call = 2

                def fail_analysis(*_args, old=old, mirror=mirror,
                                  controller=controller, **_kwargs):
                    invalidated = controller.capture_assistant_context()
                    self.assert_ineligible(invalidated, AnalysisStatus.ANALYZING,
                                           old.analysis_generation + 1)
                    self.assertEqual(mirror.calls_since_arm, 1)
                    self.assertIs(mirror.current, mirror.first_since_arm)
                    self.assertEqual(tuple(getattr(mirror.current, field)
                                           for field in IDENTITY_FIELDS),
                                     tuple(getattr(invalidated, field)
                                           for field in IDENTITY_FIELDS))
                    raise RuntimeError("pipeline failed")

                with patch("codeviz.ui.controller.analyze_project", side_effect=fail_analysis):
                    with self.assertRaises((RuntimeError, publication_error)) as caught:
                        controller.analyze()
                if isinstance(caught.exception, publication_error):
                    self.assertEqual(caught.exception.code, "AI_ANALYSIS_REQUIRED")
                else:
                    self.assertEqual(str(caught.exception), "pipeline failed")
                self.assertEqual(mirror.calls_since_arm, 2)
                invalidated = mirror.first_since_arm
                self.assertEqual(invalidated.status, AnalysisStatus.ANALYZING)
                self.assertFalse(invalidated.eligible)
                self.assertEqual(invalidated.analysis_generation, old.analysis_generation + 1)
                self.assertEqual(invalidated.publication_revision, old.publication_revision + 1)
                self.assertIs(controller.analysis, old_result)
                self.assertEqual(controller.status, AnalysisStatus.FAILED)

                current = controller.capture_assistant_context()
                self.assert_ineligible(current, AnalysisStatus.FAILED,
                                       invalidated.analysis_generation)
                self.assertEqual(current.publication_revision,
                                 invalidated.publication_revision + 1)
                self.assertEqual(current.project_epoch, old.project_epoch)
                self.assertEqual(current.root, root)
                self.assertEqual(current.ignore_paths, old.ignore_paths)
                self.assertIs(mirror.current,
                              invalidated if when == "before" else mirror.attempted)
                self.assertFalse(mirror.current.eligible)
                self.assertNotEqual(tuple(getattr(mirror.current, field)
                                         for field in IDENTITY_FIELDS),
                                    tuple(getattr(old, field) for field in IDENTITY_FIELDS))
                self.assertIsNotNone(controller.view(ViewKind.ARCHITECTURE))

    def test_task2_eligible_commit_mirror_failure_keeps_direct_analysis_usable(self):
        publication_error = self.contract("PublicationError").PublicationError
        for when in ("before", "after"):
            mirror = ThrowingMirror()
            with self.subTest(eligible_mirror_failure=when), self.task2_project(
                    mirror=mirror) as (controller, root, _store):
                old_result = controller.analyze()
                old = controller.capture_assistant_context()
                self.assertTrue(old.eligible)
                mirror.fail_when = when
                mirror.fail_on_call = 2
                mirror.fail_also_on_next = True
                mirror.fallback_when = "before"
                try:
                    result = controller.analyze()
                except publication_error:
                    self.fail("eligible mirror failure aborted accepted ordinary analysis")
                self.assertIs(controller.analysis, result)
                self.assertIsNot(result, old_result)
                self.assertEqual(controller.status, AppController._result_status(result))
                self.assertIn(controller.status, (AnalysisStatus.READY, AnalysisStatus.PARTIAL))
                self.assertIsNotNone(controller.view(ViewKind.ARCHITECTURE))
                self.assertEqual(mirror.calls_since_arm, 3)
                invalidated = mirror.first_since_arm
                attempted = mirror.eligible_attempted
                self.assertEqual(invalidated.status, AnalysisStatus.ANALYZING)
                self.assertFalse(invalidated.eligible)
                self.assertTrue(attempted.eligible)
                self.assertEqual(attempted.analysis_generation, invalidated.analysis_generation)
                current = controller.capture_assistant_context()
                self.assert_ineligible(current, controller.status, old.analysis_generation + 1)
                self.assertEqual(current.project_epoch, old.project_epoch)
                self.assertEqual(current.root, root)
                self.assertGreater(current.publication_revision, attempted.publication_revision)
                self.assertIs(mirror.current,
                              invalidated if when == "before" else attempted)
                self.assertNotEqual(tuple(getattr(mirror.current, field)
                                         for field in IDENTITY_FIELDS),
                                    tuple(getattr(current, field) for field in IDENTITY_FIELDS))
                self.assertNotEqual(tuple(getattr(old, field) for field in IDENTITY_FIELDS),
                                    tuple(getattr(current, field) for field in IDENTITY_FIELDS))

    def test_task2_eligible_commit_mirror_failure_keeps_direct_cached_open_usable(self):
        publication_error = self.contract("PublicationError").PublicationError
        for when in ("before", "after"):
            mirror = ThrowingMirror()
            with self.subTest(eligible_mirror_failure=when), self.task2_project(
                    cache=True, mirror=mirror) as (controller, root, store):
                old_result = controller.analyze()
                old = controller.capture_assistant_context()
                self.assertTrue(old.eligible)
                cached = store.load_analysis(root)
                self.assertIsNotNone(cached)
                mirror.fail_when = when
                mirror.fail_on_call = 2
                try:
                    controller.open_project(root)
                except publication_error:
                    self.fail("eligible mirror failure aborted accepted cached open")
                self.assertIsNotNone(controller.analysis)
                self.assertIsNot(controller.analysis, old_result)
                self.assertTrue(all(file.tree is None for file in controller.analysis.files))
                self.assertEqual(controller.status,
                                 AppController._result_status(controller.analysis))
                self.assertIn(controller.status, (AnalysisStatus.READY, AnalysisStatus.PARTIAL))
                self.assertIsNotNone(controller.view(ViewKind.ARCHITECTURE))
                self.assertEqual(mirror.calls_since_arm, 3)
                invalidated = mirror.first_since_arm
                attempted = mirror.eligible_attempted
                self.assertEqual(invalidated.status, AnalysisStatus.NOT_ANALYZED)
                self.assertFalse(invalidated.eligible)
                self.assertTrue(attempted.eligible)
                current = controller.capture_assistant_context()
                self.assert_ineligible(current, controller.status, old.analysis_generation + 1)
                self.assertNotEqual(current.project_epoch, old.project_epoch)
                self.assertEqual(current.root, root)
                self.assertGreater(current.publication_revision, attempted.publication_revision)
                self.assertEqual(controller._source_editing._root, root)
                self.assertEqual(controller._source_editing._epoch, current.project_epoch)
                self.assertFalse(mirror.current.eligible)
                self.assertEqual(tuple(getattr(mirror.current, field)
                                       for field in IDENTITY_FIELDS),
                                 tuple(getattr(current, field) for field in IDENTITY_FIELDS))

    def test_task2_controller_notification_guards_order_and_failure(self):
        parameter = inspect.signature(AppController.analyze).parameters.get("before_analysis")
        self.assertIsNotNone(parameter, "Missing C3 Task2 before_analysis keyword")
        self.assertEqual(parameter.kind, inspect.Parameter.KEYWORD_ONLY)
        self.assertIsNone(parameter.default)
        for cache in (False, True):
            mirror = RevokingMirror()
            with self.subTest(cache=cache), self.task2_project(
                    cache=cache, mirror=mirror) as (controller, root, _):
                self.task2_ports(controller)
                old_result = controller.analyze()
                old = controller.capture_assistant_context()
                events = []
                real_fingerprint = fingerprint_project

                expected_identity = [old]

                def observe(name, events=events, expected_identity=expected_identity, mirror=mirror):
                    old = expected_identity[0]
                    context = controller.capture_assistant_context()
                    self.assert_ineligible(context, AnalysisStatus.ANALYZING,
                                           old.analysis_generation + 1)
                    self.assertEqual(controller.status, AnalysisStatus.ANALYZING)
                    self.assertEqual(context.root, root)
                    self.assertEqual(context.ignore_paths, controller.ignore_paths)
                    self.assertEqual(context.project_epoch, old.project_epoch)
                    self.assertFalse(controller._assistant_publication_lock.locked())
                    self.assertFalse(mirror.lock.locked())
                    events.append(name)

                def notify():
                    observe("notification")

                def fingerprint(*args, events=events, real_fingerprint=real_fingerprint, **kwargs):
                    if "fingerprint" not in events:
                        observe("fingerprint")
                    else:
                        events.append("fingerprint")
                    return real_fingerprint(*args, **kwargs)

                def analyze(*args, **kwargs):
                    observe("analysis")
                    return analyze_project(*args, **kwargs)

                with patch.object(controller, "_assistant_invalidate",
                                  wraps=controller._assistant_invalidate) as invalidator, \
                        patch("codeviz.ui.controller.fingerprint_project", fingerprint), \
                        patch("codeviz.ui.controller.analyze_project", analyze):
                    controller.analyze(before_analysis=notify)
                self.assertEqual(invalidator.call_count, 1)
                self.assertEqual(events, ["notification", "fingerprint", "analysis", "fingerprint"]
                                 if cache else ["notification", "analysis"])
                complete = controller.capture_assistant_context()
                self.assertEqual(complete.analysis_generation, old.analysis_generation + 1)
                self.assertTrue(complete.eligible)

                # No-hook entry follows the same invalidation before the first seam.
                old = complete
                expected_identity[0] = old
                events.clear()
                with patch.object(controller, "_assistant_invalidate",
                                  wraps=controller._assistant_invalidate) as invalidator, \
                        patch("codeviz.ui.controller.fingerprint_project", fingerprint), \
                        patch("codeviz.ui.controller.analyze_project", analyze):
                    controller.analyze()
                self.assertEqual(invalidator.call_count, 1)
                self.assertEqual(events, ["fingerprint", "analysis", "fingerprint"]
                                 if cache else ["analysis"])

                old = controller.capture_assistant_context()
                expected_identity[0] = old
                old_result = controller.analysis
                events.clear()

                def failed_notification():
                    observe("notification")
                    raise RuntimeError("trusted notification failed")

                with patch("codeviz.ui.controller.fingerprint_project") as fingerprint_mock, \
                        patch("codeviz.ui.controller.analyze_project") as analyze_mock:
                    with self.assertRaisesRegex(RuntimeError, "trusted notification failed"):
                        controller.analyze(before_analysis=failed_notification)
                fingerprint_mock.assert_not_called()
                analyze_mock.assert_not_called()
                self.assertEqual(events, ["notification"])
                self.assertEqual(controller.status, AnalysisStatus.FAILED)
                self.assertIs(controller.analysis, old_result)
                self.assert_ineligible(controller.capture_assistant_context(), AnalysisStatus.FAILED,
                                       old.analysis_generation + 1)

                for attribute, value in (("project_root", None),
                                         ("status", AnalysisStatus.ANALYZING)):
                    with self.subTest(guard=attribute), patch.object(controller, attribute, value):
                        expected = controller.capture_assistant_context()
                        with patch.object(controller, "_assistant_invalidate") as invalidator, \
                                patch("codeviz.ui.controller.analyze_project") as analyze_mock, \
                                patch("codeviz.ui.controller.fingerprint_project") as fingerprint_mock:
                            with self.assertRaises(RuntimeError):
                                controller.analyze(before_analysis=notify)
                        invalidator.assert_not_called()
                        analyze_mock.assert_not_called()
                        fingerprint_mock.assert_not_called()
                        self.assertIs(controller.capture_assistant_context(), expected)
                        self.assertEqual(events, ["notification"])

                publication_error = self.contract("PublicationError").PublicationError
                with patch.object(controller, "_assistant_invalidate",
                                  side_effect=publication_error()), \
                        patch("codeviz.ui.controller.analyze_project") as analyze_mock, \
                        patch("codeviz.ui.controller.fingerprint_project") as fingerprint_mock:
                    with self.assertRaises(publication_error):
                        controller.analyze(before_analysis=notify)
                analyze_mock.assert_not_called()
                fingerprint_mock.assert_not_called()
                self.assertEqual(events, ["notification"])

    def test_task2_sanitize_failure_after_real_analysis_finishes_failed_generation(self):
        with self.task2_project(cache=True) as (controller, root, store):
            old_result = controller.analyze()
            old = controller.capture_assistant_context()
            saved_cache = store.analysis_path(root).read_bytes()
            self.assertTrue(old.eligible)

            def fail_after_analysis():
                self.assertIsNotNone(controller.analysis)
                self.assertIsNot(controller.analysis, old_result)
                raise RuntimeError("view state cleanup failed")

            with patch("codeviz.ui.controller.analyze_project", wraps=analyze_project) as analyzer, \
                    patch.object(controller, "sanitize_view_state", side_effect=fail_after_analysis):
                with self.assertRaisesRegex(RuntimeError, "view state cleanup failed"):
                    controller.analyze()
            analyzer.assert_called_once_with(root, ())
            self.assertEqual(controller.status, AnalysisStatus.FAILED)
            self.assertIs(controller.analysis, old_result)
            failed = controller.capture_assistant_context()
            self.assert_ineligible(failed, AnalysisStatus.FAILED, old.analysis_generation + 1)
            self.assertEqual(failed.project_epoch, old.project_epoch)
            self.assertEqual(failed.root, root)
            self.assertGreater(failed.publication_revision, old.publication_revision)
            self.assertEqual(store.analysis_path(root).read_bytes(), saved_cache)
            self.assertIsNotNone(controller.view(ViewKind.ARCHITECTURE))
            self.assertTrue(old.eligible)
            self.assertIsNotNone(old.snapshot)

    def test_task2_cache_open_postprocessing_failure_completes_ineligible_identity(self):
        with self.task2_project(cache=True) as (controller, root, store):
            old_result = controller.analyze()
            old = controller.capture_assistant_context()
            self.assertTrue(old.eligible)

            with patch.object(store, "load_analysis", wraps=store.load_analysis) as loader, \
                    patch.object(controller, "sanitize_view_state",
                                 side_effect=RuntimeError("cached view cleanup failed")):
                with self.assertRaisesRegex(RuntimeError, "cached view cleanup failed"):
                    controller.open_project(root)
            loader.assert_called_once_with(root)
            self.assertIsNotNone(controller.analysis)
            self.assertIsNot(controller.analysis, old_result)
            current = controller.capture_assistant_context()
            self.assertEqual(current.analysis_generation, old.analysis_generation + 1)
            self.assertNotEqual(current.project_epoch, old.project_epoch)
            self.assertEqual(current.root, root)
            self.assertEqual(current.status, controller.status)
            self.assertFalse(current.eligible)
            self.assertIsNone(current.snapshot)
            self.assertEqual(dict(current.navigation_facts), {})
            self.assertEqual(dict(current.context_map), {})
            self.assertGreater(current.publication_revision, old.publication_revision)
            self.assertIsNotNone(controller.view(ViewKind.ARCHITECTURE))
            self.assertTrue(old.eligible)
            self.assertIsNotNone(old.snapshot)

    def test_task2_pipeline_cache_failures_and_unrepairable_snapshot_provenance(self):
        with self.task2_project(cache=True) as (controller, root, store):
            self.task2_ports(controller)
            old_result = controller.analyze()
            old = controller.capture_assistant_context()
            saved = store.analysis_path(root).read_bytes()
            with patch("codeviz.ui.controller.analyze_project", side_effect=RuntimeError("pipeline")):
                with self.assertRaisesRegex(RuntimeError, "pipeline"):
                    controller.analyze()
            self.assertIs(controller.analysis, old_result)
            self.assertEqual(store.analysis_path(root).read_bytes(), saved)
            self.assert_ineligible(controller.capture_assistant_context(), AnalysisStatus.FAILED,
                                   old.analysis_generation + 1)
            controller.view(ViewKind.ARCHITECTURE)

            # Real cached graph remains ordinary usable if disk freshness cannot be verified.
            before = controller.capture_assistant_context()
            with patch("codeviz.ui.controller.fingerprint_project", side_effect=OSError("disk")):
                controller.open_project(root)
            self.assertIsNotNone(controller.analysis)
            self.assertEqual(controller.status, AnalysisStatus.OUTDATED)
            self.assert_ineligible(controller.capture_assistant_context(), AnalysisStatus.OUTDATED,
                                   before.analysis_generation + 1)
            controller.view(ViewKind.ARCHITECTURE)

            before = controller.capture_assistant_context()
            with patch.object(store, "load_analysis", side_effect=CacheError("corrupt")):
                controller.open_project(root)
            self.assertIsNone(controller.analysis)
            self.assert_ineligible(controller.capture_assistant_context(), controller.status,
                                   before.analysis_generation + 1)

        # B3 failure must not downgrade a successful ordinary analysis or backfill hashes.
        for failure in ("null_hash", "budget"):
            with self.subTest(failure=failure), self.task2_project() as (controller, root, _):
                self.task2_ports(controller)
                ordinary = analyze_project(root)
                if failure == "null_hash":
                    ordinary = replace(ordinary, files=tuple(
                        replace(file, content_hash=None) for file in ordinary.files))
                ordinary_status = AppController._result_status(ordinary)

                def bounded_builder(analysis, **kwargs):
                    kwargs["limits"] = SnapshotLimits(max_records=0)
                    return build_snapshot(analysis, **kwargs)

                before = controller.capture_assistant_context()
                with ExitStack() as stack:
                    stack.enter_context(patch("codeviz.ui.controller.analyze_project",
                                              return_value=ordinary))
                    fingerprint = stack.enter_context(patch(
                        "codeviz.ui.controller.fingerprint_project",
                        side_effect=AssertionError("snapshot hash backfill")))
                    if failure == "budget":
                        stack.enter_context(self.snapshot_builder(bounded_builder))
                    returned = controller.analyze()
                self.assertIs(returned, ordinary)
                self.assertIs(controller.analysis, ordinary)
                self.assertEqual(controller.status, ordinary_status)
                self.assert_ineligible(controller.capture_assistant_context(), ordinary_status,
                                       before.analysis_generation + 1)
                fingerprint.assert_not_called()
                controller.view(ViewKind.ARCHITECTURE)
                if failure == "null_hash":
                    self.assertTrue(all(file.content_hash is None for file in ordinary.files))

        with self.task2_project() as (controller, root, _):
            (root / "broken.py").write_text("def broken(:\n", encoding="utf-8")
            controller.analyze()
            context = controller.capture_assistant_context()
            self.assertEqual(controller.status, AnalysisStatus.PARTIAL)
            self.assertTrue(context.eligible, "valid Partial provenance should be eligible")
            self.assertEqual(context.status, AnalysisStatus.PARTIAL)
            self.assertFalse(context.snapshot.get_file("broken.py").was_parsed)


if __name__ == "__main__":
    unittest.main()
