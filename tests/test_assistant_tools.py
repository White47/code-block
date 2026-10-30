"""B5 R1/R8/R9/R13: admission, native receipts and local accounting."""

import json
import time
import unittest
from contextlib import ExitStack
from dataclasses import fields, replace
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from assistant_tool_fixtures import (
    index_case_with,
    make_index_case,
    make_session,
    make_source_case,
    require_api,
)

from codeviz.assistant._explanation_context import explanation_draft
from codeviz.assistant.contracts import AgentError
from codeviz.assistant.source_excerpts import (
    ExcerptRequest,
    SourceExcerptReader,
    SourceReadState,
)
from codeviz.assistant.source_policy import SourceExcerptError
from codeviz.core import CodeEdge, CodeNode, Confidence, EdgeType
from codeviz.source_handles import SourceReadError, read_source_bytes


class ToolSessionTests(unittest.TestCase):
    def setUp(self):
        self.dto = require_api(self, "tool_contracts", "SearchRequest", "RelationsRequest", "EntityRef",
                               "ExcerptToolResult")
        self.api = require_api(self, "tools", "ToolSession", "create_tool_session")
        for method in ("dispatch", "search_symbols", "get_local_relations", "read_source_excerpt",
                       "bind_evidence", "lookup_binding", "close"):
            self.assertTrue(hasattr(self.api.ToolSession, method), "Missing B5 API: ToolSession." + method)

    def source(self, raw=b"x = 1\r\n"):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        return make_source_case(Path(temporary.name), raw)

    def rejected(self, action, code="invalid_input"):
        with self.assertRaises((AgentError, SourceExcerptError)) as caught:
            action()
        error = caught.exception
        self.assertEqual(error.code, code)
        self.assertEqual(str(error), str(type(error)(code)))
        self.assertNotIn("private-sentinel", str(error))

    def test_r1_r8_strict_schema_attempts_and_raw_path_before_anchor(self):
        invalid = (("execute", {"command": "private-sentinel"}), (True, {}),
                   ("search_symbols", []), ("search_symbols", {"query": True}),
                   ("search_symbols", {"query": "", "scope": "private-sentinel"}),
                   ("search_symbols", {"query": "", "limit": 1.0}),
                   ("get_local_relations", {"seed": {"kind": "code", "id": "seed", "root": "x"}}),
                   ("read_source_excerpt", {"file_path": "app.py", "start_line": True, "end_line": 1}),
                   ("read_source_excerpt", {"file_path": "app.py", "start_line": 1, "end_line": 1,
                                            "limits": {}}),
                   ("search_symbols", {"query": "x" * 2001}),
                   ("search_symbols", {"query": "private-sentinel\n"}),
                   ("get_local_relations", {"seed": {"kind": "code", "id": "seed"}, "hops": 3}),
                   ("read_source_excerpt", {"file_path": "app.py", "start_line": 1, "end_line": 1,
                                            "start_column": 1.0}))
        for name, payload in invalid:
            with self.subTest(name=name, payload=payload):
                case = make_index_case()
                session = make_session(case)
                with patch.object(SourceExcerptReader, "read_excerpt", side_effect=AssertionError("reader")):
                    self.rejected(lambda session=session, name=name, payload=payload: session.dispatch(name, payload))
                self.assertEqual(case.ledger.usage.tool_calls, 1)
                self.assertEqual(case.registry.entry_count, 0)
        for path in ("./app.py", "app.py/", "pkg//app.py", "pkg\\app.py", "../app.py"):
            with self.subTest(path=path):
                case = make_index_case()
                session = make_session(case)
                with ExitStack() as stack:
                    for target in ("codeviz.assistant.tools.AnchorRecord",
                                   "codeviz.assistant.source_excerpts.read_source_bytes", "os.stat",
                                   "os.lstat", "os.scandir", "builtins.open"):
                        stack.enter_context(patch(target, side_effect=AssertionError("prevalidation boundary")))
                    read = stack.enter_context(patch.object(SourceExcerptReader, "read_excerpt",
                                                           side_effect=AssertionError("reader")))
                    self.rejected(lambda session=session, path=path: session.dispatch("read_source_excerpt", {
                        "file_path": path, "start_line": 1, "end_line": 1}))
                read.assert_not_called()
                self.assertEqual(case.ledger.usage.tool_calls, 1)
                self.assertEqual((case.registry.entry_count, case.registry.byte_count), (0, 0))

    def test_symbol_search_result_normalizes_only_an_identical_definition(self):
        def matching(analysis):
            symbol = analysis.symbol_index.entries["s0000"]
            analysis.flow.graph.add_node(CodeNode(
                symbol.id, symbol.kind, symbol.name, symbol.qualified_name, symbol.scope,
                "python", (symbol.source_anchor,)))
            analysis.flow.graph.edges.append(CodeEdge(
                symbol.id, "neighbor", EdgeType.CALLS, Confidence.EXACT,
                symbol.source_anchor))

        case = index_case_with(matching)
        session = make_session(case)
        search = session.dispatch("search_symbols", {"query": "s0000", "limit": 1})
        ref = search.results[0].ref
        result = session.dispatch("get_local_relations", {
            "seed": {"kind": ref.kind, "id": ref.id}, "hops": 1,
        })
        self.assertEqual(result.status, "ok")
        self.assertIn("s0000", {node.ref.id for node in result.nodes})

        unmatched = make_session(make_index_case()).dispatch("get_local_relations", {
            "seed": {"kind": "symbol", "id": "s0000"},
        })
        self.assertEqual((unmatched.status, unmatched.nodes, unmatched.relations),
                         ("unavailable", (), ()))

    def test_explanation_schema_allows_the_search_symbol_relation_chain(self):
        draft = explanation_draft("question", (), time.monotonic() + 30)
        logical = json.loads(draft.body)
        descriptions = {tool["name"]: tool["description"] for tool in logical["tools"]}
        self.assertTrue(all(text.strip() for text in descriptions.values()))
        self.assertIn("empty query", descriptions["search_symbols"])
        self.assertIn("relative file paths", descriptions["read_source_excerpt"])
        self.assertIn("ID", descriptions["get_local_relations"])
        relations = next(tool for tool in logical["tools"]
                         if tool["name"] == "get_local_relations")
        self.assertEqual(relations["parameters"]["properties"]["seed"]
                         ["properties"]["kind"]["enum"],
                         ["symbol", "code", "semantic"])

    def test_r8_direct_zero_dispatch_once_and_ninth_no_query(self):
        case = make_index_case()
        session = make_session(case)
        session.search_symbols(self.dto.SearchRequest(""))
        session.get_local_relations(self.dto.RelationsRequest(self.dto.EntityRef("code", "seed")))
        self.assertEqual(case.ledger.usage.tool_calls, 0)
        for _ in range(8):
            session.dispatch("search_symbols", {"query": ""})
        self.assertEqual(case.ledger.usage.tool_calls, 8)
        closed = make_index_case()
        closed_session = make_session(closed)
        closed_session.close()
        with self.assertRaises((AgentError, SourceExcerptError)):
            closed_session.dispatch("search_symbols", {"query": ""})
        self.assertEqual(closed.ledger.usage.tool_calls, 0)
        with patch.object(session, "search_symbols", side_effect=AssertionError("ninth query")):
            self.rejected(lambda: session.dispatch("search_symbols", {"query": ""}), "budget_exceeded")
        self.assertEqual(case.ledger.usage.tool_calls, 8)
        for mode, code in (("cancel", "cancelled"), ("timeout", "timeout"),
                           ("revoke", "consent_required"), ("scope", "scope_mismatch")):
            with self.subTest(mode=mode):
                case = make_index_case()
                session = make_session(case)
                if mode == "cancel":
                    case.ledger.cancel()
                elif mode == "timeout":
                    case.now[0] = 90
                elif mode == "revoke":
                    case.state.value = SourceReadState(case.scope, None, False)
                else:
                    case.state.value = replace(case.state.value, scope=replace(case.scope, request_id="other"))
                self.rejected(lambda session=session: session.dispatch("search_symbols", {"query": ""}), code)
                self.assertEqual(case.ledger.usage.tool_calls, 0)

    def test_r9_r13_native_adapter_receipt_projection_no_execution_and_no_wire_charge(self):
        raw = b"x = 1\r\nraise RuntimeError('must never execute')\r\n"
        case = self.source(raw)
        session = self.api.create_tool_session(case.snapshot, scope=case.scope, consent=case.consent,
                                              ledger=case.ledger, registry=case.registry,
                                              current_state=case.state)
        # Preload the stdlib codec before exec guards block its lazy import in a fresh process.
        "".encode("utf-16-le")
        with ExitStack() as stack:
            for target in ("socket.socket", "subprocess.Popen", "builtins.exec", "builtins.eval"):
                stack.enter_context(patch(target, side_effect=AssertionError("execution boundary")))
            native = stack.enter_context(patch("codeviz.assistant.source_excerpts.read_source_bytes",
                                               wraps=read_source_bytes))
            excerpt = session.read_source_excerpt(ExcerptRequest(case.anchor))
            self.assertEqual(case.ledger.usage.tool_calls, 0)
            projected = session.dispatch("read_source_excerpt", {"file_path": "app.py",
                                                                  "start_line": 1, "end_line": 1})
        self.assertEqual(native.call_count, 4)
        self.assertEqual(excerpt.content_hash, sha256(raw).hexdigest())
        self.assertEqual(excerpt.text, "x = 1")
        record = case.registry.lookup(excerpt.evidence_id, scope=case.scope,
                                      expected_file_hash=excerpt.content_hash)
        self.assertEqual((record.anchor.start_column, record.anchor.end_column), (0, 5))
        self.assertEqual(record.truncated, excerpt.truncated)
        self.assertIsInstance(projected, self.dto.ExcerptToolResult)
        self.assertEqual({f.name for f in fields(projected)},
                         {"evidence_id", "relative_path", "text", "returned_anchor", "truncated"})
        self.assertEqual((case.ledger.usage.tool_calls, case.ledger.usage.files,
                          case.ledger.usage.source_characters, case.ledger.usage.model_requests), (1, (), 0, 0))
        for start, end in ((1, None), (None, 2)):
            with self.subTest(start_column=start, end_column=end):
                raw = b"x = 1\n"
                case = self.source(raw)
                request = ExcerptRequest(replace(case.anchor, start_column=start, end_column=end))
                expected_anchor = replace(case.anchor, start_column=0, end_column=5)
                # Confirm the real B4 reader expands either missing column to the whole line.
                native_excerpt = case.reader.read_excerpt(request)
                self.assertEqual(native_excerpt.text, "x = 1")
                self.assertEqual(native_excerpt.returned_anchor, expected_anchor)
                retained = case.registry.entry_count
                session = self.api.create_tool_session(
                    case.snapshot, scope=case.scope, consent=case.consent, ledger=case.ledger,
                    registry=case.registry, current_state=case.state)
                with ExitStack() as stack:
                    for target in ("socket.socket", "subprocess.Popen", "builtins.exec", "builtins.eval"):
                        stack.enter_context(patch(target, side_effect=AssertionError("execution boundary")))
                    try:
                        excerpt = session.read_source_excerpt(request)
                    except (AgentError, SourceExcerptError) as error:
                        self.fail("B5 rejected a valid B4 mixed-None column request: " + error.code)
                    self.assertEqual(excerpt.text, "x = 1")
                    self.assertEqual(excerpt.requested_anchor, request.anchor)
                    self.assertEqual(excerpt.returned_anchor, expected_anchor)
                    self.assertEqual(excerpt.content_hash, sha256(raw).hexdigest())
                    self.assertEqual(excerpt.scope, case.scope)
                    self.assertFalse(excerpt.truncated)
                    record = case.registry.lookup(excerpt.evidence_id, scope=case.scope,
                                                  expected_file_hash=excerpt.content_hash)
                    self.assertEqual(record.anchor.start_column, 0)
                    self.assertEqual(record.anchor.end_column, 5)
                    self.assertEqual(record.scope, case.scope)
                    self.assertFalse(record.truncated)
                    self.assertEqual(case.registry.entry_count, retained + 1)
                    session.search_symbols(self.dto.SearchRequest(""))
                    subsequent = session.read_source_excerpt(ExcerptRequest(case.anchor))
                    self.assertEqual(subsequent.text, "x = 1")
                    self.assertEqual(case.registry.lookup(excerpt.evidence_id, scope=case.scope,
                                                         expected_file_hash=excerpt.content_hash), record)
                    self.assertEqual(case.registry.entry_count, retained + 2)
                    self.assertEqual(case.ledger.usage.tool_calls, 0)

    def test_r9_secret_changed_and_corrupt_receipt_fail_closed(self):
        for mode, code in (("secret", "secret_blocked"), ("changed", "source_changed"),
                           ("receipt", "evidence_unavailable"), ("unsupported", "verification_failed")):
            with self.subTest(mode=mode):
                raw = (b"x = 1\n# api_key = sk-" + b"a" * 32 + b"\n"
                       if mode == "secret" else b"x = 1\r\n")
                case = self.source(raw)
                session = make_session(case)
                if mode == "changed":
                    Path(case.snapshot.binding.project_root, "app.py").write_bytes(b"x = 2\r\n")
                with ExitStack() as stack:
                    if mode == "unsupported":
                        stack.enter_context(patch("codeviz.assistant.source_excerpts.read_source_bytes",
                                                  side_effect=SourceReadError("UNSUPPORTED_PLATFORM")))
                        for target in ("builtins.open", "io.open"):
                            stack.enter_context(patch(target, side_effect=AssertionError("ordinary read fallback")))
                    if mode == "receipt":
                        actual = case.reader.read_excerpt
                        def corrupted(request, actual=actual, case=case):
                            return replace(actual(request), returned_anchor=replace(case.anchor, start_column=0,
                                                                                    end_column=1))
                        stack.enter_context(patch.object(case.reader, "read_excerpt", side_effect=corrupted))
                    self.rejected(lambda session=session: session.dispatch("read_source_excerpt", {
                        "file_path": "app.py", "start_line": 1, "end_line": 1}), code)
                self.assertEqual(case.registry.entry_count, 0)
                self.assertEqual(case.ledger.usage.tool_calls, 1)


if __name__ == "__main__":
    unittest.main()
