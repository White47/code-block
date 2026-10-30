"""Public foundations contracts; no provider, filesystem source, or freshness claims."""

import ast
import importlib
import importlib.util
import subprocess
import sys
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError, fields
from textwrap import dedent
from threading import Barrier
from unittest.mock import patch

EXPORTS = (
    "RequestScope", "JobStatus", "AgentError", "BudgetLimits",
    "BudgetReservation", "BudgetUsage", "BudgetLedger", "EvidenceRecord",
    "EvidenceRegistry",
)
COUNTERS = ("tool_calls", "model_requests", "input_tokens", "output_tokens")
LIMITS = ("max_files", "max_source_characters") + tuple(
    "max_" + name for name in COUNTERS
)


class Clock:
    def __init__(self, now=0.0):
        self.now = now

    def __call__(self):
        return self.now


class FoundationsTests(unittest.TestCase):
    def setUp(self):
        # Assert availability before importing: missing foundations are FAIL,
        # never a collection-time ImportError or a misleading skip.
        self.assertIsNotNone(
            importlib.util.find_spec("codeviz.assistant"),
            "Missing public feature: codeviz.assistant (foundations not implemented)",
        )
        self.api = importlib.import_module("codeviz.assistant")
        self.assertEqual(
            [name for name in EXPORTS if not hasattr(self.api, name)], [],
            "Missing public foundations exports",
        )
        core = importlib.import_module("codeviz.core")
        self.Anchor, self.Confidence = core.SourceAnchor, core.Confidence
        self.scope = self.api.RequestScope("epoch-a", 1, "request-a")

    def rejected(self, action, raw=None):
        with self.assertRaises(self.api.AgentError) as caught:
            action()
        error = caught.exception
        self.assertIsInstance(error, ValueError)
        for value in (error.code, error.message, str(error)):
            self.assertIsInstance(value, str)
            self.assertTrue(value)
            if raw:
                self.assertNotIn(raw, value)
        return error.code, error.message

    def frozen(self, value, field, replacement):
        with self.assertRaises(FrozenInstanceError):
            setattr(value, field, replacement)

    def record(self, **changes):
        args = dict(scope=self.scope, anchor=self.Anchor("src/app.py", 1, 0, 2, 3),
                    content_hash="a" * 64, confidence=self.Confidence.EXACT)
        args.update(changes)
        return self.api.EvidenceRecord(**args)

    def lookup(self, registry, evidence_id, **changes):
        args = dict(scope=self.scope, expected_file_hash="a" * 64)
        args.update(changes)
        return registry.lookup(evidence_id, **args)

    def test_scope_boundaries_frozen_slots_and_exact_statuses(self):
        for identifier in ("a", "A0._-", "x" * 128):
            for generation in (0, 2**63 - 1):
                with self.subTest(identifier=identifier, generation=generation):
                    scope = self.api.RequestScope(identifier, generation, identifier)
                    self.assertEqual((scope.project_epoch, scope.analysis_generation,
                                      scope.request_id), (identifier, generation, identifier))
        self.frozen(self.scope, "request_id", "other")
        self.assertFalse(hasattr(self.scope, "__dict__"))
        self.assertEqual({status.value for status in self.api.JobStatus},
                         {"Queued", "Running", "Completed", "Cancelled", "Failed", "Stale"})

    def test_scope_strict_validation_and_safe_fixed_errors(self):
        for name in ("project_epoch", "request_id"):
            for bad in ("", "_first", "a/b", "é", "x" * 129, "a\n", 1, None, True):
                with self.subTest(field=name, bad=bad):
                    args = dict(project_epoch="epoch", analysis_generation=0, request_id="req")
                    args[name] = bad
                    self.rejected(lambda args=args: self.api.RequestScope(**args))
        for bad in (-1, 2**63, True, False, 1.0, "1", None):
            with self.subTest(generation=bad):
                self.rejected(lambda bad=bad: self.api.RequestScope("epoch", bad, "req"))
        errors = [self.rejected(lambda raw=raw: self.api.RequestScope(raw, 0, "req"), raw)
                  for raw in ("PRIVATE/path-one", "SECRET/path-two")]
        self.assertEqual(errors[0], errors[1])

    def test_limits_defaults_validation_and_immutability(self):
        limits = self.api.BudgetLimits()
        self.assertEqual(tuple(getattr(limits, name) for name in LIMITS),
                         (8, 12000, 8, 6, 20000, 2000))
        self.assertEqual(limits.timeout_seconds, 90)
        self.frozen(limits, "max_files", 9)
        for name in LIMITS:
            self.assertEqual(getattr(self.api.BudgetLimits(**{name: 1}), name), 1)
            for bad in (0, -1, True, False, 1.0, "1", None):
                with self.subTest(field=name, bad=bad):
                    self.rejected(lambda name=name, bad=bad: self.api.BudgetLimits(**{name: bad}))
        for timeout in (0.5, 1, 90.0):
            self.assertEqual(self.api.BudgetLimits(timeout_seconds=timeout).timeout_seconds, timeout)
        for bad in (0, -1, True, "90", None, float("nan"), float("inf"), -float("inf")):
            with self.subTest(timeout=bad):
                self.rejected(lambda bad=bad: self.api.BudgetLimits(timeout_seconds=bad))

    def test_reservation_strict_counts_fragments_and_safe_paths(self):
        empty = self.api.BudgetReservation()
        self.assertEqual(empty.source_fragments, ())
        self.assertEqual(tuple(getattr(empty, name) for name in COUNTERS), (0, 0, 0, 0))
        self.frozen(empty, "tool_calls", 1)
        ledger = self.api.BudgetLedger(clock=Clock())
        for name in COUNTERS:
            for bad in (-1, True, False, 1.0, "1", None):
                with self.subTest(field=name, bad=bad):
                    before = ledger.usage
                    self.rejected(lambda name=name, bad=bad: ledger.reserve(
                        self.api.BudgetReservation(**{name: bad})))
                    self.assertEqual(ledger.usage, before)
        bad_fragments = ([], [("app.py", "x")], (["app.py", "x"],),
                         (("app.py",),), (("app.py", "x", "extra"),),
                         ((1, "x"),), (("app.py", None),), (("app.py", b"x"),))
        bad_paths = ("", ".", "../private.py", "x/../private.py", "/private.py",
                     "C:\\private.py", "C:private.py", "\\\\host\\share.py", "x\x00.py")
        for fragments in bad_fragments + tuple(((path, "x"),) for path in bad_paths):
            with self.subTest(fragments=fragments):
                before = ledger.usage
                self.rejected(lambda fragments=fragments: ledger.reserve(
                    self.api.BudgetReservation(source_fragments=fragments)))
                self.assertEqual(ledger.usage, before)
        before = ledger.usage
        for bad in (None, {}, empty.__class__):
            with self.subTest(reservation=bad):
                self.rejected(lambda bad=bad: ledger.reserve(bad))
                self.assertEqual(ledger.usage, before)

    def test_each_budget_exact_boundary_and_atomic_overflow(self):
        cases = [("max_" + name, {name: 2}, {name: 1}) for name in COUNTERS]
        cases += [("max_source_characters", {"source_fragments": (("a.py", "ab"),)},
                   {"source_fragments": (("a.py", "c"),)}),
                  ("max_files", {"source_fragments": (("a.py", ""), ("b.py", ""))},
                   {"source_fragments": (("c.py", ""),)})]
        for name, exact, extra in cases:
            with self.subTest(limit=name):
                ledger = self.api.BudgetLedger(self.api.BudgetLimits(**{name: 2}), clock=Clock())
                usage = ledger.reserve(self.api.BudgetReservation(**exact))
                self.assertEqual(ledger.usage, usage)
                candidate = dict(tool_calls=1, model_requests=1, input_tokens=1,
                                 output_tokens=1, source_fragments=(("new.py", "x"),))
                candidate.update(extra)
                self.rejected(lambda ledger=ledger, candidate=candidate: ledger.reserve(
                    self.api.BudgetReservation(**candidate)))
                self.assertEqual(ledger.usage, usage)

    def test_sources_normalize_deduplicate_files_but_accumulate_characters(self):
        ledger = self.api.BudgetLedger(clock=Clock())
        fragments = (("z.py", "é"), ("src\\app.py", "ab"), ("src/./app.py", "ab"))
        first = ledger.reserve(self.api.BudgetReservation(source_fragments=fragments))
        second = ledger.reserve(self.api.BudgetReservation(source_fragments=(("src/app.py", "ab"),)))
        self.assertEqual(first.files, ("src/app.py", "z.py"))
        self.assertEqual(first.source_characters, 5)
        self.assertEqual(second.files, first.files)
        self.assertEqual(second.source_characters, 7)
        self.assertEqual(first.source_characters, 5)  # Old snapshot stays immutable.
        self.frozen(second, "source_characters", 0)
        with self.assertRaises((AttributeError, TypeError)):
            ledger.usage = first

    def test_retries_charge_again_and_output_is_explicit_per_request(self):
        ledger = self.api.BudgetLedger(clock=Clock())
        request = self.api.BudgetReservation(model_requests=1, input_tokens=1, output_tokens=1000)
        ledger.reserve(request)
        second = ledger.reserve(request)
        self.assertEqual((second.model_requests, second.input_tokens, second.output_tokens), (2, 2, 2000))
        self.rejected(lambda: ledger.reserve(self.api.BudgetReservation(model_requests=1, output_tokens=1)))
        self.assertEqual(ledger.usage, second)
        for _ in range(4):
            ledger.reserve(self.api.BudgetReservation(model_requests=1))
        before = ledger.usage
        self.assertEqual(before.model_requests, 6)
        self.rejected(lambda: ledger.reserve(self.api.BudgetReservation(model_requests=1)))
        self.assertEqual(ledger.usage, before)

    def test_deadline_cancel_and_nonfinite_clocks_never_partially_charge(self):
        clock = Clock(10.0)
        ledger = self.api.BudgetLedger(clock=clock)
        clock.now = 99.999
        ledger.reserve(self.api.BudgetReservation(tool_calls=1))
        before = ledger.usage
        for now in (100.0, 101.0):
            with self.subTest(deadline=now):
                clock.now = now
                self.rejected(lambda: ledger.reserve(self.api.BudgetReservation(tool_calls=1)))
                self.assertEqual(ledger.usage, before)
        for bad in (float("nan"), float("inf"), -float("inf")):
            with self.subTest(clock=bad):
                self.rejected(lambda bad=bad: self.api.BudgetLedger(clock=Clock(bad)))
                live_clock = Clock()
                live = self.api.BudgetLedger(clock=live_clock)
                initial = live.usage
                live_clock.now = bad
                self.rejected(lambda live=live: live.reserve(self.api.BudgetReservation(tool_calls=1)))
                self.assertEqual(live.usage, initial)
        live = self.api.BudgetLedger(clock=Clock())
        before = live.reserve(self.api.BudgetReservation(tool_calls=1))
        live.cancel()
        live.cancel()
        for request in (self.api.BudgetReservation(), self.api.BudgetReservation(tool_calls=1)):
            self.rejected(lambda request=request: live.reserve(request))
            self.assertEqual(live.usage, before)

    def test_concurrent_last_budget_has_exactly_one_winner(self):
        ledger = self.api.BudgetLedger(self.api.BudgetLimits(max_tool_calls=1), clock=Clock())
        barrier = Barrier(8)

        def reserve():
            barrier.wait(timeout=5)
            try:
                ledger.reserve(self.api.BudgetReservation(tool_calls=1, input_tokens=1))
                return True
            except self.api.AgentError:
                return False

        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: reserve(), range(8)))
        self.assertEqual(results.count(True), 1)
        self.assertEqual((ledger.usage.tool_calls, ledger.usage.input_tokens), (1, 1))

    def test_clock_callbacks_can_read_usage_or_cancel_without_deadlock(self):
        # Isolate each callback so a lock regression is killed and reaped on RED.
        script = dedent("""
            import sys
            import unittest
            sys.path[:0] = sys.argv[2:]
            from codeviz.assistant import (
                AgentError, BudgetLedger, BudgetReservation, BudgetUsage,
            )

            check = unittest.TestCase()
            holder = {}
            observed = []

            def clock():
                if "ledger" in holder:
                    ledger = holder["ledger"]
                    if sys.argv[1] == "usage":
                        observed.append(ledger.usage)
                    else:
                        ledger.cancel()
                        observed.append(True)
                return 0.0

            ledger = BudgetLedger(clock=clock)
            holder["ledger"] = ledger
            request = BudgetReservation(
                tool_calls=1, model_requests=1, input_tokens=1, output_tokens=1,
                source_fragments=(("app.py", "x"),),
            )
            if sys.argv[1] == "usage":
                usage = ledger.reserve(request)
                check.assertEqual(usage, BudgetUsage(
                    files=("app.py",), source_characters=1, tool_calls=1,
                    model_requests=1, input_tokens=1, output_tokens=1,
                ))
                check.assertEqual(ledger.usage, usage)
                check.assertTrue(observed)
                check.assertTrue(all(value == BudgetUsage() for value in observed))
            else:
                with check.assertRaises(AgentError):
                    ledger.reserve(request)
                check.assertTrue(observed)
                check.assertEqual(ledger.usage, BudgetUsage())
        """)
        for callback in ("usage", "cancel"):
            with self.subTest(callback=callback):
                try:
                    result = subprocess.run(
                        [sys.executable, "-B", "-c", script, callback, *sys.path],
                        capture_output=True, text=True, timeout=2,
                    )
                except subprocess.TimeoutExpired:
                    self.fail(f"clock callback {callback!r} deadlocked (2s timeout)")
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_evidence_fields_are_immutable_typed_and_anchor_is_copied(self):
        anchor = self.Anchor("src\\app.py", 1, 0, 2, 3)
        record = self.record(anchor=anchor, symbol_id="s" * 512, code_edge_id="edge", semantic_id="semantic")
        self.assertEqual(record.anchor, anchor)
        self.assertIsNot(record.anchor, anchor)
        self.assertEqual(record.anchor.file_path, "src/app.py")
        self.assertFalse(record.truncated)
        self.frozen(record, "content_hash", "b" * 64)
        self.frozen(record.anchor, "start_line", 9)
        self.assertEqual({field.name for field in fields(record)},
                         {"scope", "anchor", "content_hash", "confidence", "symbol_id",
                          "code_edge_id", "semantic_id", "truncated"})
        for confidence in self.Confidence:
            self.assertIs(self.record(confidence=confidence).confidence, confidence)
        self.assertTrue(self.record(truncated=True).truncated)

    def test_evidence_strict_field_validation(self):
        cases = {"scope": (None, {}, "scope"), "anchor": (None, {}, ast.parse("x=1"), "plaintext"),
                 "content_hash": ("", "A" * 64, "g" * 64, "a" * 63, "a" * 65, 1, None),
                 "confidence": ("Exact", None, 1), "truncated": (0, 1, "true", None)}
        for name in ("symbol_id", "code_edge_id", "semantic_id"):
            cases[name] = ("", "x" * 513, 1, {}, True)
            self.assertEqual(getattr(self.record(**{name: "x" * 512}), name), "x" * 512)
        for name, values in cases.items():
            for bad in values:
                with self.subTest(field=name, bad=bad):
                    self.rejected(lambda name=name, bad=bad: self.record(**{name: bad}))

    def test_evidence_revalidates_anchor_coordinates_at_new_boundary(self):
        # Core SourceAnchor currently accepts bool/float coordinates; the new
        # evidence boundary must reject these without changing core production.
        for name in ("start_line", "start_column", "end_line", "end_column"):
            for bad in (True, 1.5):
                with self.subTest(field=name, bad=bad):
                    args = dict(file_path="app.py", start_line=1, start_column=0,
                                end_line=2, end_column=3)
                    args[name] = bad
                    anchor = self.Anchor(**args)
                    self.rejected(lambda anchor=anchor: self.record(anchor=anchor))
        for anchor in (self.Anchor("app.py", 1), self.Anchor("app.py", 1, 0, 1, 0),
                       self.Anchor("app.py", 1, 9, 2, 0)):
            with self.subTest(valid=anchor):
                self.assertEqual(self.record(anchor=anchor).anchor, anchor)

    def test_registry_issues_unique_ids_and_returns_same_record(self):
        registry = self.api.EvidenceRegistry(self.scope)
        record = self.record()
        ids = [registry.register(record) for _ in range(8)]
        self.assertEqual(len(set(ids)), 8)
        for evidence_id in ids:
            self.assertIsInstance(evidence_id, str)
            self.assertTrue(evidence_id)
            self.assertNotIn("src/app.py", evidence_id)
            self.assertIs(self.lookup(registry, evidence_id), record)
        self.assertEqual(registry.entry_count, 8)
        self.assertGreater(registry.byte_count, 0)
        for name in ("entry_count", "byte_count"):
            with self.subTest(property=name), self.assertRaises((AttributeError, TypeError)):
                setattr(registry, name, 0)
        with self.assertRaises(TypeError):
            registry.register(record, evidence_id="caller-controlled")

    def test_registry_collision_cap_and_successful_retry_are_atomic(self):
        original, pending = self.record(), self.record(symbol_id="next")
        entropy_source = "codeviz.assistant.evidence.secrets.token_urlsafe"
        for exhausted in (True, False):
            with self.subTest(exhausted=exhausted):
                registry = self.api.EvidenceRegistry(self.scope)
                issued = registry.register(original)
                before = registry.entry_count, registry.byte_count
                unique = issued[:-1] + ("A" if issued[-1] != "A" else "B")
                if exhausted:
                    with patch(entropy_source, return_value=issued) as entropy:
                        self.rejected(lambda registry=registry: registry.register(pending))
                    self.assertEqual(entropy.call_count, 8)
                    self.assertEqual((registry.entry_count, registry.byte_count), before)
                else:
                    # Measure a single real insertion via the public counters.
                    reference = self.api.EvidenceRegistry(self.scope)
                    with patch(entropy_source, return_value=unique):
                        reference.register(pending)
                    with patch(entropy_source, side_effect=(issued, unique)) as entropy:
                        retried = registry.register(pending)
                    self.assertEqual(entropy.call_count, 2)
                    self.assertEqual(retried, unique)
                    self.assertEqual((registry.entry_count, registry.byte_count),
                                     (before[0] + 1, before[1] + reference.byte_count))
                    self.assertIs(self.lookup(registry, retried), pending)
                self.assertIs(self.lookup(registry, issued), original)

    def test_registry_entropy_failure_is_safe_and_atomic(self):
        registry = self.api.EvidenceRegistry(self.scope)
        original = self.record()
        issued = registry.register(original)
        before = registry.entry_count, registry.byte_count
        marker = "PRIVATE_SECRET_MARKER"
        with patch("codeviz.assistant.evidence.secrets.token_urlsafe",
                   side_effect=OSError(marker)):
            with self.assertRaises(self.api.AgentError) as caught:
                registry.register(self.record(symbol_id="next"))
        error = caught.exception
        for value in (error.code, error.message, str(error)):
            self.assertIsInstance(value, str)
            self.assertTrue(value)
            self.assertNotIn(marker, value)
        self.assertTrue(error.__suppress_context__)
        self.assertIsNone(error.__cause__)
        self.assertEqual((registry.entry_count, registry.byte_count), before)
        self.assertIs(self.lookup(registry, issued), original)

    def test_registry_integer_serialization_failure_is_safe_and_atomic(self):
        registry = self.api.EvidenceRegistry(self.scope)
        original = self.record()
        issued = registry.register(original)
        before = registry.entry_count, registry.byte_count
        # Valid coordinates stay unbounded; failure belongs to serialization.
        pending = self.record(anchor=self.Anchor("app.py", 10**4300))
        self.assertTrue(pending.anchor.start_line == 10**4300)
        # Make the real JSON failure deterministic even if the runner disables
        # Python's integer string limit; restore its setting after registration.
        previous_limit = sys.get_int_max_str_digits()
        try:
            sys.set_int_max_str_digits(4300)
            with self.assertRaises(self.api.AgentError) as caught:
                registry.register(pending)
        finally:
            sys.set_int_max_str_digits(previous_limit)
            self.assertEqual((registry.entry_count, registry.byte_count), before)
            self.assertIs(self.lookup(registry, issued), original)
        error = caught.exception
        self.assertEqual(error.message, self.api.AgentError(error.code).message)
        self.assertEqual(str(error), error.message)
        for value in (error.code, error.message, str(error)):
            self.assertIsInstance(value, str)
            self.assertTrue(value)
            self.assertNotIn("app.py", value)
            self.assertNotIn("4300", value)
            self.assertNotIn("Exceeds the limit", value)
        self.assertTrue(error.__suppress_context__)
        self.assertIsNone(error.__cause__)

    def test_registry_rejects_unknown_scope_hash_and_invalid_records_atomically(self):
        registry = self.api.EvidenceRegistry(self.scope)
        record = self.record()
        evidence_id = registry.register(record)
        before = registry.entry_count, registry.byte_count
        scopes = (self.api.RequestScope("epoch-b", 1, "request-a"),
                  self.api.RequestScope("epoch-a", 2, "request-a"),
                  self.api.RequestScope("epoch-a", 1, "request-b"), None, {})
        for scope in scopes:
            with self.subTest(scope=scope):
                self.rejected(lambda scope=scope: self.lookup(registry, evidence_id, scope=scope))
        for bad_hash in ("b" * 64, "A" * 64, "a" * 63, None, 1):
            with self.subTest(hash=bad_hash):
                self.rejected(lambda bad_hash=bad_hash: self.lookup(
                    registry, evidence_id, expected_file_hash=bad_hash))
        errors = [self.rejected(lambda raw=raw: self.lookup(registry, raw), raw)
                  for raw in ("UNKNOWN-private-one", "UNKNOWN-secret-two")]
        self.assertEqual(errors[0], errors[1])
        for bad in (None, {}, ast.parse("x=1"), self.record(scope=scopes[0])):
            with self.subTest(record=bad):
                self.rejected(lambda bad=bad: registry.register(bad))
                self.assertEqual((registry.entry_count, registry.byte_count), before)
        self.assertIs(self.lookup(registry, evidence_id), record)

    def test_registry_limits_strict_validation_and_entry_atomicity(self):
        for name in ("max_entries", "max_bytes"):
            for bad in (0, -1, True, False, 1.0, "1", None):
                with self.subTest(field=name, bad=bad):
                    self.rejected(lambda name=name, bad=bad: self.api.EvidenceRegistry(
                        self.scope, **{name: bad}))
        for scope in (None, {}, "epoch"):
            with self.subTest(scope=scope):
                self.rejected(lambda scope=scope: self.api.EvidenceRegistry(scope))
        registry = self.api.EvidenceRegistry(self.scope, max_entries=1)
        record = self.record()
        evidence_id = registry.register(record)
        before = registry.entry_count, registry.byte_count
        self.rejected(lambda: registry.register(self.record(symbol_id="next")))
        self.assertEqual((registry.entry_count, registry.byte_count), before)
        self.assertIs(self.lookup(registry, evidence_id), record)

    def test_registry_utf8_all_fields_and_exact_byte_capacity(self):
        # Compare public measurements, without guessing JSON key spelling/order.
        def size(record):
            registry = self.api.EvidenceRegistry(self.scope)
            evidence_id = registry.register(record)
            return registry.byte_count, len(evidence_id.encode("utf-8"))

        baseline, baseline_id = size(self.record())
        for name in ("symbol_id", "code_edge_id", "semantic_id"):
            with self.subTest(field=name):
                ascii_bytes, ascii_id = size(self.record(**{name: "xxx"}))
                utf8_bytes, utf8_id = size(self.record(**{name: "证据甲"}))
                self.assertEqual((utf8_bytes - utf8_id) - (ascii_bytes - ascii_id), 6)
                long_bytes, long_id = size(self.record(**{name: "x" * 512}))
                self.assertEqual((long_bytes - long_id) - (ascii_bytes - ascii_id), 509)
        bigger, bigger_id = size(self.record(anchor=self.Anchor("src/longer.py", 1, 0, 2, 3)))
        self.assertEqual((bigger - bigger_id) - (baseline - baseline_id), 3)
        record = self.record(symbol_id="证据甲", code_edge_id="edge", semantic_id="semantic")
        measured, _ = size(record)
        exact = self.api.EvidenceRegistry(self.scope, max_bytes=measured)
        evidence_id = exact.register(record)
        self.assertEqual(exact.byte_count, measured)
        before = exact.entry_count, exact.byte_count
        self.rejected(lambda: exact.register(record))
        self.assertEqual((exact.entry_count, exact.byte_count), before)
        self.assertIs(self.lookup(exact, evidence_id), record)
        short = self.api.EvidenceRegistry(self.scope, max_bytes=measured - 1)
        self.rejected(lambda: short.register(record))
        self.assertEqual((short.entry_count, short.byte_count), (0, 0))

    def test_clear_is_permanent_idempotent_and_invalidates_issued_ids(self):
        registry = self.api.EvidenceRegistry(self.scope)
        evidence_id = registry.register(self.record())
        registry.clear()
        registry.clear()
        self.assertEqual((registry.entry_count, registry.byte_count), (0, 0))
        self.rejected(lambda: self.lookup(registry, evidence_id))
        self.rejected(lambda: registry.register(self.record()))
        self.assertEqual((registry.entry_count, registry.byte_count), (0, 0))
        fresh = self.api.EvidenceRegistry(self.scope)
        self.rejected(lambda: self.lookup(fresh, evidence_id))

    def test_concurrent_registry_capacity_and_clear_leave_no_late_entries(self):
        registry = self.api.EvidenceRegistry(self.scope, max_entries=1)
        record = self.record()
        barrier = Barrier(8)

        def register():
            barrier.wait(timeout=5)
            try:
                return registry.register(record)
            except self.api.AgentError:
                return None

        with ThreadPoolExecutor(max_workers=8) as pool:
            ids = [value for value in pool.map(lambda _: register(), range(8)) if value is not None]
        self.assertEqual(len(ids), 1)
        self.assertEqual(registry.entry_count, 1)
        self.assertIs(self.lookup(registry, ids[0]), record)
        race = self.api.EvidenceRegistry(self.scope)
        barrier = Barrier(2)

        def late_register():
            barrier.wait(timeout=5)
            try:
                return race.register(record)
            except self.api.AgentError:
                return None

        def clear():
            barrier.wait(timeout=5)
            race.clear()

        with ThreadPoolExecutor(max_workers=2) as pool:
            registration = pool.submit(late_register)
            clearing = pool.submit(clear)
            issued = registration.result(timeout=5)
            clearing.result(timeout=5)
        self.assertEqual((race.entry_count, race.byte_count), (0, 0))
        if issued is not None:
            self.rejected(lambda: self.lookup(race, issued))
        self.rejected(lambda: race.register(record))


if __name__ == "__main__":
    unittest.main()
