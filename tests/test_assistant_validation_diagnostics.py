"""Private diagnostics: closed values, isolation and no raw response retention."""

import importlib
import json
import unittest
from contextlib import nullcontext
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch


class ValidationDiagnosticsTests(unittest.TestCase):
    def api(self):
        return importlib.import_module("codeviz.assistant._validation_diagnostics")

    def test_capture_accepts_only_closed_primitives_and_two_phases(self):
        api = self.api()
        capture = api.Capture()
        attempt = capture.begin("final")
        with api.observation(attempt):
            api.fact("rootObject", True)
            api.fact("SECRET-PATH", "SECRET-TEXT")
            api.fact("sectionCount", True)
            api.location(0, 1)
            api.fact("claimKeysExact", False)
        attempt.finish("parse", (("invalid_schema", 0, 1),))
        capture.begin("repair").finish("evidence", ())
        self.assertIsNone(capture.begin("repair"))
        raw = capture.encode()
        self.assertLessEqual(len(raw), 4096)
        self.assertNotIn(b"SECRET", raw)
        records = json.loads(raw)
        self.assertEqual(records[0]["shape"]["rootObject"], True)
        self.assertIsNone(records[0]["shape"]["sectionCount"])
        self.assertEqual(records[0]["shapeFailureLocation"], [0, 1])
        self.assertEqual(records[1]["issues"], [])
        api.fact("rootObject", False)
        self.assertEqual(capture.encode(), raw)

    def test_serializer_rejects_unexpected_fields_and_preserves_issue_counts(self):
        api = self.api()
        capture = api.Capture()
        attempt = capture.begin("final")
        attempt.finish("evidence", (("unprovided_id", 0, 0),) * 20)
        record = json.loads(capture.encode())[0]
        self.assertEqual(record["issueCounts"]["unprovided_id"], 20)
        self.assertEqual(len(record["issues"]), 8)
        self.assertTrue(record["issuesTruncated"])
        record["modelText"] = "SECRET"
        self.assertIsNone(api.encode_records([record]))

    def test_serializer_rejects_nonexact_string_keys_without_stringifying_them(self):
        api = self.api()

        class SpoofedKey(str):
            def __hash__(self):
                return hash("phase")

            def __eq__(self, other):
                return other == "phase"

        capture = api.Capture()
        capture.begin("final")
        record = json.loads(capture.encode())[0]
        record.pop("phase")
        record[SpoofedKey("SECRET")] = "final"
        self.assertIsNone(api.encode_records([record]))

    def test_parse_shape_diagnoses_existing_checks_without_recording_values(self):
        from codeviz.assistant.response import ResponseError, parse_model_answer

        api = self.api()
        cases = (
            (b"[]", "rootObject"),
            (b'{"SECRET":1}', "rootKeysExact"),
            (b'{"sections":[]}', "sectionOrderValid"),
            (b'{"sections":null}', "sectionsArray"),
            (b'{"sections":[],"sections":[]}', "duplicateKeysAbsent"),
            (b'{"sections":NaN}', "finiteNumbersOnly"),
            (b'{"sections":', "jsonValid"),
            (b"\xff", "utf8Valid"),
            (b"\xef\xbb\xbf{}", "bomAbsent"),
        )
        for raw, failed in cases:
            with self.subTest(check=failed):
                capture = api.Capture()
                attempt = capture.begin("final")
                with api.observation(attempt), self.assertRaises(ResponseError):
                    parse_model_answer(raw)
                attempt.finish("parse", (("invalid_schema", None, None),))
                record = json.loads(capture.encode())[0]
                self.assertIs(record["shape"][failed], False)
                self.assertNotIn(b"SECRET", capture.encode())

    def test_unknown_marker_is_reported_from_the_existing_evidence_branch(self):
        from tests.assistant_tool_fixtures import make_source_case

        from codeviz.assistant.response import (
            ActualEvidenceResolver,
            parse_model_answer,
            validate_model_answer,
        )
        from codeviz.assistant.tools import create_tool_session

        api = self.api()
        kinds = ("summary", "entry", "steps", "branches", "relations", "evidence", "unknowns")
        value = {"sections": [{"kind": k, "claims": []} for k in kinds]}
        value["sections"][6]["claims"] = [{
            "text": "SECRET unsupported conclusion", "basis": "unknown",
            "evidenceIds": [], "relationEvidenceId": None}]
        with TemporaryDirectory() as temporary:
            case = make_source_case(Path(temporary), b"value = 1\n")
            session = create_tool_session(case.snapshot, scope=case.scope, consent=case.consent,
                ledger=case.ledger, registry=case.registry, current_state=case.state)
            self.addCleanup(session.close)
            resolver = ActualEvidenceResolver(session, case.registry, case.snapshot, case.scope, {})
            capture = api.Capture()
            attempt = capture.begin("final")
            with api.observation(attempt):
                model = parse_model_answer(json.dumps(value).encode())
                result = validate_model_answer(model, resolver=resolver,
                                               checked_at="2026-10-27T00:00:00Z")
            attempt.finish("evidence", tuple((i.code, i.section_index, i.claim_index)
                                             for i in result.issues))
            self.assertIsNone(result.answer)
            record = json.loads(capture.encode())[0]
            self.assertIs(record["shape"]["unknownMarkerPresent"], False)
            self.assertEqual(record["issues"], [
                {"code": "invalid_schema", "section": 6, "claim": 0}])
            self.assertNotIn(b"SECRET", capture.encode())

    def test_claim_and_section_failures_have_bounded_locations(self):
        from codeviz.assistant.response import ResponseError, parse_model_answer

        api = self.api()
        kinds = ("summary", "entry", "steps", "branches", "relations", "evidence", "unknowns")
        cases = (
            ("section", "kind", "SECRET", "sectionOrderValid"),
            ("section", "claims", None, "claimsArray"),
            ("claim", "text", "", "textValid"),
            ("claim", "basis", "SECRET", "basisEnumValid"),
            ("claim", "evidenceIds", ["x", "x"], "evidenceIdsValid"),
            ("claim", "relationEvidenceId", "SECRET", "relationIdValid"),
            ("claim", "extra", "SECRET", "claimKeysExact"),
        )
        for area, key, invalid, failed in cases:
            with self.subTest(check=failed):
                claim = {"text": "无法确认", "basis": "unknown",
                         "evidenceIds": [], "relationEvidenceId": None}
                value = {"sections": [{"kind": kind, "claims": []} for kind in kinds]}
                value["sections"][0]["claims"] = [claim]
                (value["sections"][0] if area == "section" else claim)[key] = invalid
                capture = api.Capture()
                attempt = capture.begin("final")
                with api.observation(attempt), self.assertRaises(ResponseError):
                    parse_model_answer(json.dumps(value).encode())
                self.assertIs(json.loads(capture.encode())[0]["shape"][failed], False)
                if failed == "textValid":
                    self.assertIsNone(json.loads(capture.encode())[0]["shape"]["evidenceIdsValid"])
                self.assertNotIn(b"SECRET", capture.encode())

    def test_evidence_codes_are_observed_without_changing_results_or_ledger(self):
        from tests.assistant_tool_fixtures import make_source_case

        from codeviz.assistant.response import (
            ActualEvidenceResolver,
            parse_model_answer,
            validate_model_answer,
        )
        from codeviz.assistant.tool_contracts import EntityRef
        from codeviz.assistant.tools import create_tool_session

        api = self.api()
        kinds = ("summary", "entry", "steps", "branches", "relations", "evidence", "unknowns")
        with TemporaryDirectory() as temporary:
            case = make_source_case(Path(temporary), b"value = 1\n")
            session = create_tool_session(case.snapshot, scope=case.scope, consent=case.consent,
                ledger=case.ledger, registry=case.registry, current_state=case.state)
            self.addCleanup(session.close)
            excerpt = session.dispatch("read_source_excerpt", {
                "file_path": "app.py", "start_line": 1, "end_line": 1})
            target = EntityRef("code", "seed")
            session.bind_evidence(excerpt.evidence_id, target)
            identifier = excerpt.evidence_id
            cases = (
                ("unprovided_id", 0, "inference", ["SECRET"], None, {}),
                ("invalid_evidence", 0, "inference", [identifier], None,
                 {identifier: EntityRef("code", "missing")}),
                ("static_requires_ids", 0, "static", [], None, {}),
                ("relation_required", 4, "static", [identifier], None, {identifier: target}),
                ("invalid_relation", 4, "inference", [identifier], identifier, {identifier: target}),
                ("invalid_schema", 6, "unknown", [], None, {}),
            )
            for code, section, basis, ids, relation, provided in cases:
                with self.subTest(code=code):
                    value = {"sections": [{"kind": k, "claims": []} for k in kinds]}
                    value["sections"][section]["claims"] = [{
                        "text": "SECRET", "basis": basis, "evidenceIds": ids,
                        "relationEvidenceId": relation}]
                    model = parse_model_answer(json.dumps(value).encode())
                    resolver = ActualEvidenceResolver(
                        session, case.registry, case.snapshot, case.scope, provided)
                    before = case.ledger.usage
                    plain = validate_model_answer(
                        model, resolver=resolver, checked_at="2026-10-27T00:00:00Z")
                    capture = api.Capture()
                    attempt = capture.begin("repair")
                    with api.observation(attempt):
                        checked = validate_model_answer(
                            model, resolver=resolver, checked_at="2026-10-27T00:00:00Z")
                    attempt.finish("evidence", tuple(
                        (i.code, i.section_index, i.claim_index) for i in checked.issues))
                    self.assertEqual(checked, plain)
                    self.assertEqual(case.ledger.usage, before)
                    self.assertEqual(json.loads(capture.encode())[0]["issueCounts"][code], 1)
                    self.assertNotIn(b"SECRET", capture.encode())

    def test_sink_is_opt_in_first_capture_only_and_writes_closed_snapshot(self):
        api = self.api()
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            with patch.object(api, "_write", side_effect=AssertionError("must not access disk")):
                self.assertIsNone(api.startup(root, None))
                self.assertIsNone(api.startup(root, "true"))
            sink = api.startup(root, "1")
            self.assertEqual(json.loads((root / api.BASENAME).read_bytes()), [])
            capture = sink.claim()
            self.assertIsNone(sink.claim())
            capture.begin("final").finish("parse", (("invalid_schema", None, None),))
            sink.publish(capture)
            self.assertTrue(sink._written.wait(2))
            self.assertEqual(json.loads((root / api.BASENAME).read_bytes())[0]["phase"], "final")
            self.assertEqual(list(root.iterdir()), [root / api.BASENAME])

    def test_sink_refuses_links(self):
        api = self.api()
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "keep.txt"
            target.write_text("SECRET")
            link = root / api.BASENAME
            try:
                link.symlink_to(target)
            except OSError:
                self.skipTest("Symlink creation unavailable")
            self.assertIsNone(api.startup(root, "1"))
            self.assertEqual(target.read_text(), "SECRET")

    def test_io_failure_and_nonregular_destination_never_escape(self):
        api = self.api()
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / api.BASENAME).mkdir()
            self.assertIsNone(api.startup(root, "1"))
            owner, operation = ((api, "_rename_windows") if api.os.name == "nt"
                                else (api.os, "replace"))
            with patch.object(owner, operation, side_effect=OSError("SECRET")):
                self.assertIsNone(api.startup(root / "other", "1"))
            self.assertEqual(list((root / "other").iterdir()), [])

    def test_link_reparse_modes_and_startup_worker_failure_are_safe(self):
        import stat
        from types import SimpleNamespace

        api = self.api()
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            for mode, attributes in ((stat.S_IFLNK, 0), (stat.S_IFDIR, 0x400)):
                with self.subTest(mode=mode), patch.object(
                        Path, "lstat", return_value=SimpleNamespace(
                            st_mode=mode, st_file_attributes=attributes)):
                    self.assertIsNone(api.startup(root, "1"))
            with patch.object(api.Thread, "start", side_effect=RuntimeError("SECRET")):
                self.assertIsNone(api.startup(root, "1"))

    def test_concurrent_claims_never_mix_captures(self):
        from concurrent.futures import ThreadPoolExecutor

        api = self.api()
        with TemporaryDirectory() as temporary:
            sink = api.startup(Path(temporary), "1")
            with ThreadPoolExecutor(max_workers=4) as pool:
                captures = list(pool.map(lambda _i: sink.claim(), range(8)))
            claimed = [capture for capture in captures if capture is not None]
            self.assertEqual(len(claimed), 1)
            sink.publish(api.Capture())
            self.assertFalse(sink._written.is_set())
            sink.publish(claimed[0])
            self.assertTrue(sink._written.wait(2))

    def test_parse_exceptions_reset_context_and_concurrent_observers_are_isolated(self):
        from concurrent.futures import ThreadPoolExecutor

        from codeviz.assistant.response import ResponseError, parse_model_answer

        api = self.api()

        def run(raw):
            capture = api.Capture()
            with api.observation(capture.begin("final")):
                try:
                    parse_model_answer(raw)
                except ResponseError:
                    pass
            api.fact("sectionCount", 100)
            return json.loads(capture.encode())[0]["shape"]

        with ThreadPoolExecutor(max_workers=2) as pool:
            root_error, encoding_error = list(pool.map(run, (b"{}", b"\xff")))
        self.assertIs(root_error["rootKeysExact"], False)
        self.assertIs(encoding_error["utf8Valid"], False)
        self.assertIsNone(encoding_error["rootKeysExact"])
        self.assertIsNone(root_error["sectionCount"])

    def test_failed_background_write_does_not_change_the_service_result(self):
        from tests.test_assistant_retrieval_budget import (
            RetrievalBudgetServiceTests,
            _RepairHeadroomProvider,
        )

        from codeviz.assistant.service import AssistantService

        api = self.api()
        helper = RetrievalBudgetServiceTests("test_deepseek_invalid_final_has_one_reserved_repair")
        self.addCleanup(helper.doCleanups)
        plain, baseline, _ = helper._run(b"value = 1\n", _RepairHeadroomProvider())
        with TemporaryDirectory() as temporary:
            sink = api.startup(Path(temporary), "1")
            original = AssistantService.__init__

            def injecting(engine, *args, **kwargs):
                kwargs["_validation_diagnostics"] = sink
                original(engine, *args, **kwargs)

            with patch.object(api, "_write", side_effect=OSError("SECRET")), patch.object(
                    AssistantService, "__init__", new=injecting):
                checked, provider, _ = helper._run(b"value = 1\n", _RepairHeadroomProvider())
                self.assertTrue(sink._written.wait(2))
            self.assertEqual(checked.status, plain.status)
            self.assertTrue(checked.resources_released)
            self.assertEqual(provider.dispatches, baseline.dispatches)
            self.assertEqual(
                [p.max_output_tokens for p, _ in provider.prepared],
                [p.max_output_tokens for p, _ in baseline.prepared])
            self.assertEqual(sink._capture.attempts, [])
            self.assertEqual(json.loads((Path(temporary) / api.BASENAME).read_bytes()), [])

    def test_capture_allocation_failure_does_not_break_admission(self):
        from tests.test_assistant_retrieval_budget import (
            RetrievalBudgetServiceTests,
            _RepairHeadroomProvider,
        )

        from codeviz.assistant.service import AssistantService

        api = self.api()
        helper = RetrievalBudgetServiceTests("test_deepseek_invalid_final_has_one_reserved_repair")
        self.addCleanup(helper.doCleanups)
        with TemporaryDirectory() as temporary:
            sink = api.startup(Path(temporary), "1")
            original = AssistantService.__init__

            def injecting(engine, *args, **kwargs):
                kwargs["_validation_diagnostics"] = sink
                original(engine, *args, **kwargs)

            with patch.object(api.DiagnosticsSink, "claim", side_effect=OSError("SECRET")), patch.object(
                    AssistantService, "__init__", new=injecting):
                snapshot, _provider, _service = helper._run(b"value = 1\n", _RepairHeadroomProvider())
            self.assertEqual(snapshot.status.value, "Completed")
            self.assertTrue(snapshot.resources_released)
            self.assertEqual(json.loads((Path(temporary) / api.BASENAME).read_bytes()), [])

    def test_directory_replacement_cannot_redirect_the_atomic_write(self):
        api = self.api()
        with TemporaryDirectory() as temporary:
            root = Path(temporary) / "cache"
            root.mkdir()
            moved = Path(temporary) / "old-cache"
            owner, operation = ((api, "_rename_windows") if api.os.name == "nt"
                                else (api.os, "replace"))
            original = getattr(owner, operation)
            attempted = []

            def swapping(*args, **kwargs):
                attempted.append(True)
                if api.os.name == "nt":
                    import ctypes
                    from ctypes import wintypes

                    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
                    create = kernel.CreateFileW
                    create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                       wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD,
                                       wintypes.HANDLE]
                    create.restype = wintypes.HANDLE
                    writer = create(str(root), 0x40000000, 7, None, 3, 0x02200000, None)
                    failure = ctypes.get_last_error()
                    invalid = writer is None or writer == ctypes.c_void_p(-1).value
                    if not invalid:
                        close = kernel.CloseHandle
                        close.argtypes = [wintypes.HANDLE]
                        close(writer)
                    self.assertTrue(invalid)
                    self.assertEqual(failure, 32)
                try:
                    root.rename(moved)
                except OSError:
                    pass  # Windows must pin the directory against replacement.
                else:
                    root.mkdir()
                    (root / api.BASENAME).write_text("CANARY")
                return original(*args, **kwargs)

            with patch.object(owner, operation, side_effect=swapping):
                self.assertTrue(api._write(root, b"[]"))
            self.assertTrue(attempted)
            if api.os.name == "nt":
                self.assertFalse(moved.exists())
            if moved.exists():
                self.assertEqual((root / api.BASENAME).read_text(), "CANARY")
            else:
                self.assertEqual((root / api.BASENAME).read_bytes(), b"[]")

    def test_real_service_phases_persist_only_after_terminal_release(self):
        from tests.test_assistant_retrieval_budget import (
            RetrievalBudgetServiceTests,
            _RepairHeadroomProvider,
        )

        from codeviz.assistant.service import AssistantService

        api = self.api()
        helper = RetrievalBudgetServiceTests("test_deepseek_invalid_final_has_one_reserved_repair")
        self.addCleanup(helper.doCleanups)
        with TemporaryDirectory() as temporary:
            sink = api.startup(Path(temporary), "1")
            original = AssistantService.__init__

            def injecting(engine, *args, **kwargs):
                kwargs["_validation_diagnostics"] = sink
                original(engine, *args, **kwargs)

            with patch.object(AssistantService, "__init__", new=injecting):
                snapshot, provider, _service = helper._run(b"value = 1\n", _RepairHeadroomProvider())
            self.assertEqual(snapshot.status.value, "Completed")
            self.assertTrue(snapshot.resources_released)
            self.assertEqual(provider.final_dispatches, 2)
            self.assertTrue(sink._written.wait(2))
            records = json.loads((Path(temporary) / api.BASENAME).read_bytes())
            self.assertEqual([r["phase"] for r in records], ["final", "repair"])
            self.assertEqual(records[0]["stage"], "parse")
            self.assertEqual(records[1]["stage"], "evidence")
            self.assertEqual(records[1]["issues"], [])

    def test_transport_record_uses_owned_phase_after_close_without_changing_public_failure(self):
        from tests.test_assistant_retrieval_budget import (
            RetrievalBudgetServiceTests,
            _RepairHeadroomProvider,
            _SchedulingProvider,
        )

        from codeviz.assistant._delta_reasons import DeltaRejection
        from codeviz.assistant.issues import ProtocolDiagnostic
        from codeviz.assistant.sdk_provider import SDKAdapterError
        from codeviz.assistant.service import AssistantService

        api = self.api()
        enabled_results = []
        original = AssistantService.__init__

        def make_injector(selected_sink, observed_ledgers):
            def injecting(engine, *args, **kwargs):
                kwargs["_validation_diagnostics"] = selected_sink
                original(engine, *args, **kwargs)

                async def before_dispatch(_prepared):
                    with engine._lock:
                        observed_ledgers.append(next(iter(engine._jobs.values())).ledger.usage)

                engine._before_dispatch = before_dispatch

            return injecting

        for enabled, capture_fault in ((False, False), (True, False), (True, True)):
            with TemporaryDirectory() as temporary:
                directory = Path(temporary)
                sink = api.startup(directory, "1" if enabled else "0")
                before_close = []
                ledgers = []

                class FailingOrdinary(_SchedulingProvider):
                    def __init__(self, observations=before_close, root=directory, active=enabled):
                        super().__init__(tool_turns=4, search_limit=1)
                        self.observations, self.root, self.active = observations, root, active

                    async def dispatch(self, prepared):
                        if self.dispatches == 2:
                            self.dispatches += 1
                            raise SDKAdapterError("AI_PROTOCOL_INCOMPATIBLE", ProtocolDiagnostic(
                                "dispatch", check_site="delta_shape"), rejection=DeltaRejection("arguments_json"))
                        async for event in super().dispatch(prepared):
                            yield event

                    async def aclose(self):
                        self.observations.append(json.loads((self.root / api.BASENAME).read_bytes())
                                                 if self.active else (self.root / api.BASENAME).exists())
                        await super().aclose()

                helper = RetrievalBudgetServiceTests("test_deepseek_invalid_final_has_one_reserved_repair")
                self.addCleanup(helper.doCleanups)
                fault = (patch.object(api.Capture, "transport", side_effect=MemoryError("SYNTHETIC"))
                         if capture_fault else nullcontext())
                with fault, patch.object(AssistantService, "__init__", new=make_injector(sink, ledgers)):
                    snapshot, provider, service = helper._run(b"value = 1\n", FailingOrdinary())
                self.assertEqual(snapshot.status.value, "Failed")
                self.assertTrue(snapshot.resources_released)
                self.assertTrue(provider.closed)
                self.assertEqual(provider.dispatches, 3)
                self.assertTrue(before_close)
                self.assertTrue(all(value == ([] if enabled else False) for value in before_close))
                enabled_results.append((snapshot.issue.to_public(), tuple(ledgers),
                                        service._completed[snapshot.job_id].input_accounting))
                if enabled:
                    self.assertTrue(sink._written.wait(2))
                    self.assertEqual(json.loads((directory / api.BASENAME).read_bytes()), [] if capture_fault else [
                        {"phase": "ordinary", "stage": "transport", "reason": "arguments_json"}])
                else:
                    self.assertFalse((directory / api.BASENAME).exists())
        self.assertEqual(enabled_results[0], enabled_results[1])
        self.assertEqual(enabled_results[0], enabled_results[2])

        class FailingRepair(_RepairHeadroomProvider):
            async def dispatch(self, prepared):
                logical = next(value for handle, value in self.prepared if handle is prepared)
                if logical["mode"] == "final" and self.final_dispatches == 1:
                    self.final_dispatches += 1
                    raise SDKAdapterError("AI_PROTOCOL_INCOMPATIBLE", ProtocolDiagnostic(
                        "dispatch", check_site="delta_shape"), rejection=DeltaRejection("delta_role"))
                async for event in super().dispatch(prepared):
                    yield event

        with TemporaryDirectory() as temporary:
            sink = api.startup(Path(temporary), "1")
            helper = RetrievalBudgetServiceTests("test_deepseek_invalid_final_has_one_reserved_repair")
            self.addCleanup(helper.doCleanups)
            with patch.object(AssistantService, "__init__", new=make_injector(sink, [])):
                snapshot, provider, _service = helper._run(b"value = 1\n", FailingRepair())
            self.assertTrue(snapshot.resources_released)
            self.assertEqual(provider.final_dispatches, 2)
            self.assertTrue(sink._written.wait(2))
            records = json.loads((Path(temporary) / api.BASENAME).read_bytes())
            self.assertEqual(len(records), 2)
            self.assertEqual(records[0]["phase"], "final")
            self.assertEqual(records[0]["stage"], "parse")
            self.assertEqual(records[1], {"phase": "repair", "stage": "transport", "reason": "delta_role"})

    def test_desktop_opt_in_uses_the_actual_cache_root_without_configuring_ai(self):
        import os

        from codeviz.ui.desktop import _create_desktop_bridge

        api = self.api()
        with TemporaryDirectory() as temporary, patch.dict(
                os.environ, {"CODEVIZ_CLOSED_VALIDATION_DIAGNOSTICS": "1"}), patch(
                "codeviz.persistence.store.default_data_dir", return_value=Path(temporary)):
            bridge = _create_desktop_bridge()
            self.addCleanup(bridge._finish_assistant_close)
            self.assertEqual(json.loads((Path(temporary) / api.BASENAME).read_bytes()), [])
            self.assertIsNotNone(bridge._assistant_runtime._validation_diagnostics)

