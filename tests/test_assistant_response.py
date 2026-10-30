"""C6 contract risks: strict input, factory-issued authority, local semantics."""

import copy
import importlib
import importlib.util
import json
import unittest
from collections.abc import Mapping
from dataclasses import FrozenInstanceError, fields, replace
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from assistant_tool_fixtures import make_source_case, source_case_with

from codeviz.analysis.python import ParsedFile
from codeviz.assistant.contracts import AgentError
from codeviz.assistant.snapshot import AnchorRecord
from codeviz.assistant.source_policy import SourceExcerptError
from codeviz.assistant.tool_contracts import EntityRef, RelationKey
from codeviz.assistant.tools import create_tool_session
from codeviz.core import Confidence, EdgeType, SourceAnchor

KINDS = ("summary", "entry", "steps", "branches", "relations", "evidence", "unknowns")
CHECKED_AT = "2026-10-13T00:00:00Z"
SAFE_MESSAGE = "Assistant evidence validation failed."


def claim(text="Unable to confirm the function without evidence.", basis="unknown",
          ids=(), relation=None):
    return {"text": text, "basis": basis, "evidenceIds": list(ids),
            "relationEvidenceId": relation}


def envelope(value=None, section=0):
    sections = [{"kind": kind, "claims": []} for kind in KINDS]
    sections[section]["claims"] = [claim() if value is None else value]
    return {"sections": sections}


def wire(value):
    return json.dumps(value, ensure_ascii=True, separators=(",", ":")).encode()


class Associations(Mapping):
    """Expose duplicate associations before a dict can overwrite their targets."""

    def __init__(self, pairs):
        self.pairs = pairs

    def __iter__(self):
        return (key for key, _ in self.pairs)

    def __len__(self):
        return len(self.pairs)

    def __getitem__(self, key):
        return next(value for candidate, value in self.pairs if candidate == key)

    def items(self):
        return iter(self.pairs)


class AssistantResponseTests(unittest.TestCase):
    def api(self):
        name = "codeviz.assistant.response"
        self.assertIsNotNone(importlib.util.find_spec(name), "Missing C6 API: " + name)
        api = importlib.import_module(name)
        for export in ("ResponseError", "ModelClaim", "Section", "ModelAnswer",
                       "ResolvedEvidence", "EvidenceCard", "RelationFact", "Answer",
                       "ValidationIssue", "ValidationResult", "parse_model_answer",
                       "ActualEvidenceResolver", "validate_model_answer"):
            self.assertTrue(hasattr(api, export), "Missing C6 API: " + export)
        return api

    def source(self, configure=None):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        case = (make_source_case(root, b"value = 1\n") if configure is None else
                source_case_with(root, b"value = 1\n", configure=configure))
        session = create_tool_session(
            case.snapshot, scope=case.scope, consent=case.consent,
            ledger=case.ledger, registry=case.registry, current_state=case.state)
        self.addCleanup(session.close)
        return case, session

    def receipt(self, case, session, target):
        excerpt = session.dispatch("read_source_excerpt", {
            "file_path": "app.py", "start_line": 1, "end_line": 1})
        proof = session.bind_evidence(excerpt.evidence_id, target)
        self.assertEqual(session.lookup_binding(excerpt.evidence_id, target), proof)
        record = case.registry.lookup(excerpt.evidence_id, scope=case.scope,
                                      expected_file_hash=proof.content_hash)
        self.assertIs(record.confidence, Confidence.EXACT)
        return excerpt.evidence_id, proof, record

    def resolver(self, api, case, session, provided):
        return api.ActualEvidenceResolver(session, case.registry, case.snapshot,
                                          case.scope, provided)

    def validate(self, api, resolver, value=None, **kwargs):
        model = api.parse_model_answer(wire(envelope() if value is None else value))
        return api.validate_model_answer(model, resolver=resolver,
                                         checked_at=CHECKED_AT, **kwargs)

    def invalid(self, result, code=None):
        self.assertIsNone(result.answer)
        self.assertTrue(result.issues)
        self.assertLessEqual(len(result.issues), 61)
        for issue in result.issues:
            self.assertEqual(tuple(f.name for f in fields(issue)),
                             ("code", "section_index", "claim_index"))
            self.assertIn(issue.code, ("invalid_schema", "unprovided_id", "invalid_evidence",
                                      "relation_required", "invalid_relation", "static_requires_ids"))
            for index in (issue.section_index, issue.claim_index):
                self.assertTrue(index is None or type(index) is int)
        if code is not None:
            self.assertEqual(result.issues[0].code, code)

    def assert_fixed_error(self, api, error):
        self.assertIsInstance(error, api.ResponseError)
        self.assertIsInstance(error, ValueError)
        self.assertEqual(error.code, "AI_EVIDENCE_INVALID")
        self.assertEqual(str(error), SAFE_MESSAGE)
        self.assertIsNone(error.__cause__)
        self.assertIsNone(error.__context__)

    def fixed_error(self, api, action, *, private_marker=None):
        with self.assertRaises(api.ResponseError) as caught:
            action()
        if private_marker is not None:
            self.assertNotIn(private_marker, getattr(caught.exception.__context__, "doc", ""),
                             "ResponseError retained raw JSONDecodeError.doc")
        self.assert_fixed_error(api, caught.exception)

    def rejected_entry(self, api, action):
        """Invalid typed envelopes may raise fixed errors or return fixed issues."""
        try:
            result = action()
        except api.ResponseError as error:
            self.assert_fixed_error(api, error)
        else:
            self.invalid(result, "invalid_schema")

    def test_schema(self):
        api = self.api()
        valid = envelope()
        parsed = api.parse_model_answer(wire(valid))
        self.assertEqual(tuple(section.kind for section in parsed.sections), KINDS)
        self.assertEqual(parsed.sections[0].claims[0].basis, "unknown")
        self.assertFalse(hasattr(parsed, "__dict__"))
        with self.assertRaises(FrozenInstanceError):
            parsed.sections = ()
        malformed = []
        for name, sections in (("missing", valid["sections"][:-1]),
                               ("reordered", list(reversed(valid["sections"]))),
                               ("duplicate", [valid["sections"][0]] * 7)):
            malformed.append((name, wire({"sections": sections})))
        for level, keys in ((0, ("sections",)), (1, ("kind", "claims")),
                            (2, ("text", "basis", "evidenceIds", "relationEvidenceId"))):
            for key in keys:
                value = copy.deepcopy(valid)
                node = (value, value["sections"][0], value["sections"][0]["claims"][0])[level]
                del node[key]
                malformed.append((f"missing-{level}-{key}", wire(value)))
            for key in ("rootUri", "providedIds", "target", "confidence", "links", "filename",
                        "coordinates", "endpoints", "navigation", "candidateTargets"):
                value = copy.deepcopy(valid)
                node = (value, value["sections"][0], value["sections"][0]["claims"][0])[level]
                node[key] = "PRIVATE_MODEL_SENTINEL"
                malformed.append((f"extra-{level}-{key}", wire(value)))
        raw = wire(valid)
        malformed.extend((
            ("private-non-json", b"PRIVATE-RESPONSE-MARKER"),
            ("empty", b""), ("not-bytes", raw.decode()), ("bytearray", bytearray(raw)),
            ("utf8", b"\xff"), ("bom", b"\xef\xbb\xbf" + raw),
            ("trailing", raw + b" {}"), ("fence", b"```json\n" + raw + b"\n```"),
            ("comment", b"/* private */" + raw), ("deep", b"[" * 2000 + b"]" * 2000),
            ("duplicate-root", b'{"sections":[],"sections":' + wire(valid["sections"]) + b"}"),
            ("duplicate-section", raw.replace(b'"kind":"summary"',
                                               b'"kind":"summary","kind":"summary"')),
            ("duplicate-claim", raw.replace(b'"basis":"unknown"',
                                            b'"basis":"unknown","basis":"unknown"')),
        ))
        for constant in (b"NaN", b"Infinity", b"-Infinity"):
            malformed.append((constant.decode(), raw.replace(b'"unknown"', constant, 1)))
        for value in (None, [], {"sections": {}}, {"sections": [None] * 7}):
            malformed.append((f"container-{value!r}", wire(value)))
        for field, value in (("kind", "Summary"), ("kind", True), ("claims", {}), ("claims", [None])):
            item = copy.deepcopy(valid)
            item["sections"][0][field] = value
            malformed.append((f"section-type-{field}-{value!r}", wire(item)))
        for field, values in {
            "text": ("", " \n\t", "x" * 2001, "\ud800", "x\x00", "x\x1f", True, 1, []),
            "basis": ("confirmed", True, None),
            "evidenceIds": (["id"] * 2, list(map(str, range(9))), [""], ["x" * 513],
                            ["x\n"], ["\ud800"], [True], "id", None),
            "relationEvidenceId": ("not-provided", True, "x" * 513),
        }.items():
            for ordinal, value in enumerate(values):
                item = claim()
                item[field] = value
                malformed.append((f"{field}-{ordinal}", wire(envelope(item))))
        for counts, accepted in (((20, 20, 20), True), ((21,), False), ((20, 20, 20, 1), False)):
            value = envelope()
            for index, count in enumerate(counts):
                value["sections"][index]["claims"] = [claim()] * count
            if accepted:
                self.assertEqual(sum(len(s.claims) for s in api.parse_model_answer(wire(value)).sections), 60)
            else:
                malformed.append((f"counts-{counts}", wire(value)))
        for value in (claim("😀" * 2000), claim("prose\nwith\ttabs"),
                      claim(ids=tuple(map(str, range(8))), relation="0"), claim(ids=("x" * 512,))):
            self.assertEqual(api.parse_model_answer(wire(envelope(value))).sections[0].claims[0].text,
                             value["text"])
        boundary = raw + b" " * (65536 - len(raw))
        self.assertEqual(api.parse_model_answer(boundary), parsed)
        malformed.append(("65537", boundary + b" "))
        for name, payload in malformed:
            with self.subTest(schema=name):
                self.fixed_error(api, lambda payload=payload: api.parse_model_answer(payload),
                    private_marker="PRIVATE-RESPONSE-MARKER" if name == "private-non-json" else None)

        case, session = self.source()
        resolver = self.resolver(api, case, session, {})
        constructors = (
            lambda: api.ModelClaim("x", "unknown", [], None),
            lambda: api.Section("summary", []), lambda: api.ModelAnswer([]),
            lambda: api.ModelClaim("x", "static", (), "absent"),
            lambda: api.ModelClaim("\ud800", "unknown", (), None),
        )
        for index, action in enumerate(constructors):
            with self.subTest(constructor=index):
                self.fixed_error(api, action)
        class ClaimSubclass(api.ModelClaim):
            pass
        class SectionSubclass(api.Section):
            pass
        class AnswerSubclass(api.ModelAnswer):
            pass
        for level, field, replacement in (
            ("answer", "sections", list(parsed.sections)),
            ("section", "claims", list(parsed.sections[0].claims)),
            ("claim", "evidence_ids", []), ("claim", "basis", "confirmed"),
            ("claim", "text", "x" * 2001), ("claim", "evidence_ids", ("id",) * 9),
            ("claim", "relation_evidence_id", "unprovided"),
            ("answer", "sections", parsed.sections[:-1]),
            ("section", "claims", parsed.sections[0].claims * 21),
        ):
            model = copy.deepcopy(parsed)
            node = {"answer": model, "section": model.sections[0],
                    "claim": model.sections[0].claims[0]}[level]
            object.__setattr__(node, field, replacement)
            with self.subTest(typed=(level, field)):
                self.rejected_entry(api, lambda model=model: api.validate_model_answer(
                    model, resolver=resolver, checked_at=CHECKED_AT))
        for cls, original in ((ClaimSubclass, parsed.sections[0].claims[0]),
                              (SectionSubclass, parsed.sections[0]), (AnswerSubclass, parsed)):
            with self.subTest(subclass=cls.__name__):
                def attempt(cls=cls, original=original):
                    value = cls(*(getattr(original, f.name) for f in fields(original)))
                    if cls is ClaimSubclass:
                        value = api.ModelAnswer((api.Section("summary", (value,)), *parsed.sections[1:]))
                    elif cls is SectionSubclass:
                        value = api.ModelAnswer((value, *parsed.sections[1:]))
                    return api.validate_model_answer(value, resolver=resolver, checked_at=CHECKED_AT)
                self.rejected_entry(api, attempt)

    def test_authority(self):
        # This real native baseline executes even while response.py is absent.
        case, session = self.source()
        self.receipt(case, session, RelationKey("code", 0))
        api = self.api()
        targets = (None, EntityRef("symbol", "s0000"), EntityRef("code", "seed"),
                   EntityRef("semantic", "component-a"), RelationKey("code", 0),
                   RelationKey("code", 1), RelationKey("semantic", 0))
        for target in targets:
            with self.subTest(valid_target=target):
                case, session = self.source()
                eid, proof, record = self.receipt(case, session, target)
                provided = {eid: target}
                resolver = self.resolver(api, case, session, provided)
                provided.clear()
                resolved = resolver.resolve_evidence(eid)
                self.assertEqual((resolved.record, resolved.proof), (record, proof))
                relation = eid if type(target) is RelationKey else None
                model = envelope(claim("Author intent and runtime order", "static", (eid,), relation),
                                 4 if relation else 0)
                with patch.object(type(session), "dispatch", side_effect=AssertionError("new read")), \
                        patch.object(type(session), "bind_evidence", side_effect=AssertionError("new binding")):
                    result = self.validate(api, resolver, model)
                self.assertEqual(result.issues, ())
                self.assertIsNotNone(result.answer)
                card = result.answer.evidence[0]
                self.assertEqual((card.evidence_id, card.label), (eid, "app.py:1-1"))
                self.assertIs(card.confidence, proof.confidence)
                self.assertTrue(card.source_available)
                self.assertEqual(card.graph_available, target is not None)
                self.assertEqual(card.unavailable_reason,
                                 "source_only" if target is None else "navigation_check_required")
                if relation:
                    fact = result.answer.relation_facts[0]
                    self.assertEqual((fact.source_id, fact.target_id, fact.type, fact.confidence),
                                     (proof.provenance.source, proof.provenance.target,
                                      proof.provenance.type, proof.provenance.confidence))
                else:
                    self.assertEqual(result.answer.relation_facts, ())

        corruptions = (
            ("unprovided", None), ("registry-only", None), ("wrong-session", None),
            ("foreign-request", None), ("closed-registry", None), ("closed-session", None),
            ("chosen-replacement", None), ("wrong-collection", None), ("wrong-ordinal", None),
            ("missing-node", None),
            *(("proof", field) for field in ("evidence_id", "scope", "snapshot_binding", "target",
                "provenance", "status", "returned_anchor", "content_hash", "truncated", "confidence")),
            *(("record", field) for field in ("scope", "anchor", "content_hash", "truncated",
                                               "confidence", "symbol_id", "code_edge_id", "semantic_id")),
            *(("receipt", field) for field in ("scope", "requested_anchor", "returned_anchor",
                                                "content_hash", "truncated")),
            *(("proof", field) for field in ("scope_epoch", "scope_generation", "ordinal",
                                              "provenance_type", "candidate_namespace", "bool_ordinal")),
        )
        for side, field in corruptions:
            with self.subTest(authority=(side, field)):
                case, session = self.source()
                target = RelationKey("code", 0)
                eid, proof, record = self.receipt(case, session, target)
                provided = {eid: target}
                scope = case.scope
                other_scope = replace(scope, request_id="foreign-request")
                if side == "unprovided":
                    provided = {}
                elif side == "registry-only":
                    eid = case.registry.register(record)
                    provided = {eid: target}
                elif side == "wrong-session":
                    session = create_tool_session(case.snapshot, scope=scope, consent=case.consent,
                        ledger=case.ledger, registry=case.registry, current_state=case.state)
                    self.addCleanup(session.close)
                elif side == "foreign-request":
                    scope = other_scope
                elif side == "closed-registry":
                    case.registry.clear()
                elif side == "closed-session":
                    session.close()
                elif side in ("chosen-replacement", "wrong-collection", "wrong-ordinal", "missing-node"):
                    provided[eid] = {"chosen-replacement": EntityRef("code", "seed"),
                        "wrong-collection": RelationKey("semantic", 0),
                        "wrong-ordinal": RelationKey("code", 99),
                        "missing-node": EntityRef("code", "missing")}[side]
                else:
                    # Corruption is negative-only, after the genuine issued baseline.
                    original = {"proof": proof, "record": record,
                                "receipt": session._receipts[eid]}[side]
                    damaged = copy.copy(original)
                    substitutions = {
                        "evidence_id": "forged", "scope": other_scope,
                        "snapshot_binding": replace(case.snapshot.binding, analysis_generation=2),
                        "target": RelationKey("semantic", 0),
                        "provenance": case.snapshot.code_edge_inventory[1], "status": "source_only",
                        "returned_anchor": AnchorRecord("app.py", 1, 1, 1, 8),
                        "requested_anchor": AnchorRecord("app.py", 1, 1, 1, 8),
                        "anchor": SourceAnchor("app.py", 1, 1, 1, 8),
                        "content_hash": "0" * 64, "truncated": True,
                        "confidence": Confidence.UNRESOLVED,
                        "symbol_id": "s0000", "code_edge_id": "invented", "semantic_id": "component-a",
                    }
                    if field in ("scope_epoch", "scope_generation"):
                        changed_scope = replace(scope, **({"project_epoch": "foreign-epoch"}
                            if field == "scope_epoch" else {"analysis_generation": 2}))
                        object.__setattr__(damaged, "scope", changed_scope)
                    elif field in ("ordinal", "provenance_type", "candidate_namespace"):
                        provenance = copy.copy(proof.provenance)
                        attr, replacement = {"ordinal": ("original_index", 1),
                            "provenance_type": ("type", EdgeType.READS),
                            "candidate_namespace": ("possible_targets", ("component-a",))}[field]
                        object.__setattr__(provenance, attr, replacement)
                        object.__setattr__(damaged, "provenance", provenance)
                    elif field == "bool_ordinal":
                        target_copy = copy.copy(target)
                        object.__setattr__(target_copy, "original_index", True)
                        object.__setattr__(damaged, "target", target_copy)
                    else:
                        object.__setattr__(damaged, field, substitutions[field])
                    if side == "proof":
                        session._bindings[(eid, target)] = damaged
                    elif side == "record":
                        case.registry._records[eid] = damaged
                    else:
                        session._receipts[eid] = damaged
                try:
                    resolver = api.ActualEvidenceResolver(session, case.registry, case.snapshot,
                                                          scope, provided)
                except api.ResponseError as error:
                    self.assert_fixed_error(api, error)
                else:
                    with self.subTest(resolver_conversion=(side, field)):
                        self.fixed_error(api, lambda resolver=resolver, eid=eid: resolver.resolve_evidence(eid))
                    result = self.validate(api, resolver, envelope(claim("PRIVATE_MODEL_SENTINEL",
                                           "static", (eid,), eid), 4))
                    self.invalid(result, "unprovided_id" if side == "unprovided" else None)
                    self.assertNotIn(eid, repr(result.issues))
                    self.assertNotIn("PRIVATE_MODEL_SENTINEL", repr(result.issues))

        case, session = self.source()
        eid, _, _ = self.receipt(case, session, None)
        resolver = self.resolver(api, case, session, {eid: None})
        self.assertIsNotNone(self.validate(api, resolver, envelope(claim(ids=(eid,)))).answer)
        session.close()
        self.invalid(self.validate(api, resolver, envelope(claim(ids=(eid,)))), "invalid_evidence")
        self.rejected_entry(api, lambda: api.validate_model_answer(
            api.parse_model_answer(wire(envelope())), resolver=lambda _: None, checked_at=CHECKED_AT))
        case, session = self.source()
        class ResolverSubclass(api.ActualEvidenceResolver):
            pass
        self.rejected_entry(api, lambda: api.validate_model_answer(
            api.parse_model_answer(wire(envelope())), resolver=ResolverSubclass(
                session, case.registry, case.snapshot, case.scope, {}), checked_at=CHECKED_AT))

    def test_unknown_factual_claim_must_express_uncertainty(self):
        api = self.api()
        case, session = self.source()
        resolver = self.resolver(api, case, session, {})
        result = self.validate(api, resolver, envelope(claim(
            "The function definitely calls the handler.", "unknown")))
        self.invalid(result, "invalid_schema")

    def test_unknown_claim_accepts_explicit_chinese_uncertainty(self):
        api = self.api()
        case, session = self.source()
        resolver = self.resolver(api, case, session, {})
        result = self.validate(api, resolver, envelope(claim(
            "无法确认该函数是否调用处理器。", "unknown")))
        self.assertIsNotNone(result.answer)

    def test_semantic_capacity(self):
        case, session = self.source()
        self.receipt(case, session, None)
        api = self.api()
        case, session = self.source()
        targets = (None, EntityRef("code", "seed"), RelationKey("code", 0),
                   RelationKey("code", 1), RelationKey("semantic", 0))
        issued = [self.receipt(case, session, target) for target in targets]
        provided = {receipt[0]: target for receipt, target in zip(issued, targets, strict=True)}
        resolver = self.resolver(api, case, session, provided)
        eid = issued[2][0]
        # B5 can retain another binding; C must keep the one chosen association.
        session.bind_evidence(eid, RelationKey("code", 1))
        self.assertEqual(resolver.resolve_evidence(eid).proof.target, RelationKey("code", 0))
        with self.subTest(provided_associations="conflicting"):
            self.fixed_error(api, lambda: self.resolver(api, case, session, Associations(
                ((eid, RelationKey("code", 0)), (eid, RelationKey("code", 1))))))
        same = self.resolver(api, case, session, Associations(((eid, targets[2]), (eid, targets[2]))))
        self.assertEqual(same.resolve_evidence(eid).proof.target, targets[2])
        malicious = '<script>fetch("https://evil.example")</script> [open](file:///private) '
        value = envelope(claim(malicious + "author intent: guaranteed runtime order", "static", (issued[0][0],)))
        value["sections"][4]["claims"] = [claim("Guaranteed execution", "static", (r[0],), r[0])
                                                 for r in issued[2:]] * 2
        result = self.validate(api, resolver, value, incomplete=True, coverage_gaps=("source_only",))
        self.assertEqual(result.issues, ())
        answer = result.answer
        self.assertEqual((answer.checked_at, answer.incomplete, answer.coverage_gaps),
                         (CHECKED_AT, True, ("source_only",)))
        self.assertEqual(answer.sections[0].claims[0].text, value["sections"][0]["claims"][0]["text"])
        self.assertTrue(all(c.basis == "inference" for s in answer.sections for c in s.claims))
        self.assertEqual(len(answer.evidence), 4)
        self.assertEqual(tuple(f.evidence_id for f in answer.relation_facts), tuple(r[0] for r in issued[2:]))
        self.assertEqual(tuple(f.confidence for f in answer.relation_facts),
                         (Confidence.PROBABLE, Confidence.UNRESOLVED, Confidence.PROBABLE))
        self.assertEqual(tuple(f.category for f in answer.relation_facts), ("call", "data", "data"))
        self.assertEqual(tuple(f.name for f in fields(answer)),
                         ("sections", "evidence", "relation_facts", "incomplete", "coverage_gaps", "checked_at"))
        for basis in ("unknown", "inference"):
            result = self.validate(api, resolver, envelope(claim("Unable to confirm", basis), 4))
            self.assertEqual(result.answer.sections[4].claims[0].basis, basis)
            self.assertEqual(result.answer.relation_facts, ())
        for index in range(7):
            with self.subTest(static_without_ids=index):
                self.invalid(self.validate(api, resolver, envelope(claim("fact", "static"), index)))
        self.invalid(self.validate(api, resolver, envelope(claim("relation", "static", (eid,)), 4)),
                     "relation_required")
        for source_id in (issued[0][0], issued[1][0]):
            for basis in ("static", "inference", "unknown"):
                with self.subTest(non_relation=(source_id, basis)):
                    self.invalid(self.validate(api, resolver, envelope(
                        claim("relation", basis, (source_id,), source_id), 4)), "invalid_relation")
        all_bad = envelope()
        for index in range(3):
            all_bad["sections"][index]["claims"] = [claim("fact", "static")] * 20
        issues = self.validate(api, resolver, all_bad)
        self.invalid(issues)
        self.assertEqual(len(issues.issues), 60)
        self.assertEqual(tuple((i.section_index, i.claim_index) for i in issues.issues),
                         tuple((s, c) for s in range(3) for c in range(20)))

        categories = {"call": (EdgeType.CALLS,),
            "control": (EdgeType.CONTROL_FLOW, EdgeType.BRANCH_TRUE, EdgeType.BRANCH_FALSE, EdgeType.LOOP_BACK),
            "data": (EdgeType.READS, EdgeType.WRITES, EdgeType.PASSES_TO, EdgeType.RETURNS_TO,
                     EdgeType.DATA_FLOW, EdgeType.GRADIENT_FLOW),
            "structure": (EdgeType.CONTAINS, EdgeType.IMPORTS, EdgeType.EXPORTS,
                          EdgeType.INHERITS, EdgeType.IMPLEMENTS)}
        self.assertEqual({edge for values in categories.values() for edge in values}, set(EdgeType))
        for category, edges in categories.items():
            for edge_type in edges:
                with self.subTest(category=edge_type):
                    def configure(analysis, edge_type=edge_type):
                        analysis.flow.graph.edges[0] = replace(analysis.flow.graph.edges[0], type=edge_type)
                    case, session = self.source(configure)
                    eid, _, _ = self.receipt(case, session, RelationKey("code", 0))
                    result = self.validate(api, self.resolver(api, case, session, {eid: RelationKey("code", 0)}),
                                           envelope(claim("fact", "static", (eid,), eid), 4))
                    self.assertEqual(result.answer.relation_facts[0].category, category)

        def candidates(analysis, count=2, width=1, edge_count=1, denied=False):
            graph = analysis.flow.graph
            identifiers = tuple(f"candidate-{i:04d}-" + "x" * width for i in range(count))
            for identifier in identifiers:
                graph.nodes[identifier] = replace(graph.nodes["seed"], id=identifier)
            if denied:
                graph.nodes[identifiers[-1]] = replace(graph.nodes[identifiers[-1]],
                    file_path="denied.py", source_anchors=(SourceAnchor("denied.py", 1, 0, 1, 5),))
            edge = replace(graph.edges[0], possible_targets=identifiers)
            graph.edges[:] = [edge] * edge_count
            if denied:
                # Static inventory is required by B3; it grants no source receipt
                # or consent for this candidate's file.
                return replace(analysis, files=analysis.files + (
                    ParsedFile("denied.py", "denied", None, 1,
                               parsed_success=True, content_hash="a" * 64),))

        for count, denied in ((2, False), (2, True), (1000, False), (1001, False), (10000, False)):
            with self.subTest(candidates=(count, denied)):
                case, session = self.source(lambda a, count=count, denied=denied:
                                             candidates(a, count=count, denied=denied))
                excerpt = session.dispatch("read_source_excerpt", {
                    "file_path": "app.py", "start_line": 1, "end_line": 1})
                target = RelationKey("code", 0)
                if denied:
                    with self.assertRaises((AgentError, SourceExcerptError)):
                        session.bind_evidence(excerpt.evidence_id, target)
                    self.invalid(self.validate(api, self.resolver(api, case, session, {excerpt.evidence_id: target}),
                        envelope(claim("fact", "static", (excerpt.evidence_id,), excerpt.evidence_id), 4)))
                else:
                    proof = session.bind_evidence(excerpt.evidence_id, target)
                    self.assertEqual(session.lookup_binding(excerpt.evidence_id, target), proof)
                    self.assertEqual(len(proof.provenance.possible_targets), count)
                    snapshot_type = type(case.snapshot)
                    original_lookup = snapshot_type.get_code_node
                    original_inventory = snapshot_type.code_node_inventory.fget
                    candidate_ids = frozenset(proof.provenance.possible_targets)
                    work = {"node_lookups": 0, "candidate_lookups": 0, "inventory_reads": 0}

                    def counted_lookup(snapshot, identifier, original=original_lookup,
                                       work=work, candidate_ids=candidate_ids):
                        work["node_lookups"] += 1
                        work["candidate_lookups"] += identifier in candidate_ids
                        return original(snapshot, identifier)

                    def counted_inventory(snapshot, original=original_inventory, work=work):
                        work["inventory_reads"] += 1
                        return original(snapshot)

                    # Observe real methods/tuples unchanged, after factory issuance.
                    # Include resolver construction so eager indexing also counts.
                    with patch.object(snapshot_type, "get_code_node", new=counted_lookup), \
                            patch.object(snapshot_type, "code_node_inventory", new=property(counted_inventory)):
                        result = self.validate(api, self.resolver(api, case, session, {excerpt.evidence_id: target}),
                            envelope(claim("fact", "static", (excerpt.evidence_id,), excerpt.evidence_id), 4))
                    if count > 1000:
                        self.invalid(result, "invalid_evidence")
                    else:
                        self.assertEqual(result.answer.relation_facts[0].candidate_targets,
                                         proof.provenance.possible_targets)
                        self.assertIs(result.answer.relation_facts[0].confidence, Confidence.PROBABLE)
                    if count in (1000, 10000):
                        self.assertEqual(work["candidate_lookups"], 0,
                            f"{count} candidates caused {work['candidate_lookups']} repeated linear lookups; "
                            "over-cap facts must fail before candidate lookup; valid facts need one index")
                        self.assertLessEqual(work["node_lookups"], 2, "Only two relation endpoints may use lookup")
                        # One real B5 authorization inventory pass, at most one C6
                        # index pass; neither authority nor returned nodes are mocked.
                        self.assertLessEqual(work["inventory_reads"], 2, "Candidate membership needs one C6 index")
        case, session = self.source(lambda a: candidates(a))
        local = session.dispatch("get_local_relations", {"seed": {"kind": "code", "id": "seed"}})
        self.assertTrue(local.relations)
        self.assertTrue(all(not hasattr(relation, "candidate_targets") and
                            not hasattr(relation, "possible_targets") for relation in local.relations))

        def collision(analysis):
            analysis.flow.graph.nodes["component-a"] = replace(analysis.flow.graph.nodes["seed"], id="component-a")
            analysis.flow.graph.edges.append(analysis.flow.graph.edges[0])
        case, session = self.source(collision)
        targets = (EntityRef("code", "component-a"), EntityRef("semantic", "component-a"),
                   RelationKey("code", 0), RelationKey("code", 3), RelationKey("semantic", 0))
        issued = [self.receipt(case, session, target) for target in targets]
        resolver = self.resolver(api, case, session, {r[0]: t for r, t in zip(issued, targets, strict=True)})
        for receipt, target in zip(issued, targets, strict=True):
            self.assertEqual(resolver.resolve_evidence(receipt[0]).proof.target, target)
        result = self.validate(api, resolver, envelope(claim("relations", "static",
                               tuple(r[0] for r in issued[2:]), issued[3][0]), 4))
        self.assertEqual(result.answer.relation_facts[0].evidence_id, issued[3][0])

        case, session = self.source()
        resolver = self.resolver(api, case, session, {})
        for kwargs in ({"incomplete": 1}, {"coverage_gaps": []}, {"coverage_gaps": ("x",) * 21},
                       {"coverage_gaps": ("x" * 201,)}, {"checked_at": "x" * 65},
                       {"checked_at": "2026-10-13"}, {"checked_at": "2026-10-13T00:00:00+08:00"}):
            with self.subTest(envelope_options=kwargs):
                options = {"checked_at": CHECKED_AT, **kwargs}
                self.rejected_entry(api, lambda options=options: api.validate_model_answer(
                    api.parse_model_answer(wire(envelope())), resolver=resolver, **options))
        self.resolver(api, case, session, {f"id-{i}": None for i in range(1000)})
        for table in ({f"id-{i}": None for i in range(1001)},
                      {"😀" * 500 + str(i): EntityRef("code", "😀" * 500) for i in range(1000)}):
            with self.subTest(provided_capacity=len(table)):
                self.fixed_error(api, lambda table=table: self.resolver(api, case, session, table))
        for _ in range(8):
            self.receipt(case, session, None)
        before = case.registry.entry_count
        with self.assertRaises((AgentError, SourceExcerptError)):
            session.dispatch("read_source_excerpt", {"file_path": "app.py", "start_line": 1, "end_line": 1})
        self.assertEqual(case.registry.entry_count, before)

        # Four genuine bindings fit B5 retention; a <=64 KiB model pushes local
        # answer JSON over 2 MiB without manufacturing receipts or resolver proofs.
        case, session = self.source(lambda a: candidates(a, count=1000, width=497, edge_count=4))
        targets = tuple(RelationKey("code", i) for i in range(4))
        issued = [self.receipt(case, session, target) for target in targets]
        resolver = self.resolver(api, case, session, {r[0]: t for r, t in zip(issued, targets, strict=True)})
        value = envelope()
        for index in range(3):
            value["sections"][index]["claims"] = [claim("x" * 1000, "inference") for _ in range(20)]
        # Keep 60 claims total; four claims cite four independently issued facts.
        for index, receipt in enumerate(issued):
            value["sections"][2]["claims"][index] = claim(
                "x" * 1000, "inference", (receipt[0],), receipt[0])
        self.assertLessEqual(len(wire(value)), 65536)
        model = api.parse_model_answer(wire(value))
        self.invalid(api.validate_model_answer(model, resolver=resolver, checked_at=CHECKED_AT),
                     "invalid_evidence")


if __name__ == "__main__":
    unittest.main()
