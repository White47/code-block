"""Pure C7 request validation, without source reads or provider construction."""

import importlib
import importlib.util
import unittest
from dataclasses import FrozenInstanceError

from codeviz.core import ViewKind


def start_payload(context=None):
    return {"projectEpoch": "epoch", "analysisGeneration": 1,
            "conversationId": "chat", "requestId": "request",
            "configRevision": 2, "consentRevision": 3, "question": "解释这个功能",
            "context": {"kind": "question"} if context is None else context}


class AssistantBridgeRequestTests(unittest.TestCase):
    def test_project_consent_and_navigation_requests_are_closed_and_detached(self):
        api = self.api()
        for name in ("parse_project", "parse_clear", "parse_connection", "parse_consent", "parse_resolve"):
            self.assertTrue(callable(getattr(api, name, None)), "Missing parser: " + name)
        project = {"projectEpoch": "epoch"}
        self.assertEqual(api.parse_project(project).project_epoch, "epoch")
        self.assertEqual(api.parse_clear(project | {"conversationId": "chat"}).conversation_id, "chat")
        self.assertEqual(api.parse_connection({"requestId": "request", "configRevision": 1}).request_id,
                         "request")
        files = ["app.py", "package/model.py"]
        grant = project | {"analysisGeneration": 1, "configRevision": 1,
                           "allowedFiles": files, "confirmed": True}
        parsed = api.parse_consent(grant)
        files.clear()
        self.assertEqual(parsed.allowed_files, ("app.py", "package/model.py"))
        resolve = project | {"analysisGeneration": 1, "conversationId": "chat",
                             "evidenceId": "evidence:local", "action": "source"}
        self.assertEqual(api.parse_resolve(resolve).action, "source")
        for parser, payload in (
            (api.parse_project, project | {"key": "forbidden"}),
            (api.parse_clear, project | {"conversationId": "chat", "draft": "forbidden"}),
            (api.parse_connection, {"requestId": "request", "configRevision": True}),
            (api.parse_consent, grant | {"allowedFiles": ["app.py", "APP.py"]}),
            (api.parse_consent, grant | {"allowedFiles": ["secrets.py"]}),
            (api.parse_consent, grant | {"confirmed": False}),
            (api.parse_resolve, resolve | {"action": "execute"}),
            (api.parse_resolve, resolve | {"target": {"filePath": "app.py"}}),
        ):
            with self.subTest(parser=parser.__name__), self.assertRaises(api.BridgeRequestError):
                parser(payload)

    def test_configuration_distinguishes_keep_clear_and_memory_only_key(self):
        api = self.api()
        parse = getattr(api, "parse_configure", None)
        self.assertTrue(callable(parse), "Missing closed configuration parser")
        payload = {"baseUrl": "https://provider.invalid/v1", "model": "model",
                   "enabled": True, "key": None}
        self.assertEqual(parse(payload).key_action, "keep")
        self.assertEqual(parse(payload | {"key": ""}).key_action, "clear")
        marker = "synthetic-memory-only-credential"
        parsed = parse(payload | {"key": marker})
        self.assertEqual(parsed.key_action, "set")
        self.assertEqual(parsed.key.value, marker)
        self.assertNotIn(marker, repr(parsed))
        for change in ({"key": 1}, {"enabled": 1}, {"sourceText": "forbidden"},
                       {"baseUrl": "https://name:credential@provider.invalid"}):
            with self.subTest(fields=list(change)), self.assertRaises(api.BridgeRequestError) as caught:
                parse(payload | change)
            self.assertIsNone(caught.exception.__context__)

    def api(self):
        name = "codeviz.assistant.bridge_requests"
        self.assertIsNotNone(importlib.util.find_spec(name), "Missing closed C7 request parser")
        return importlib.import_module(name)

    def test_start_context_is_copied_into_immutable_typed_values(self):
        api = self.api()
        joined_emoji = "解释👩‍💻这段代码\n谢谢"
        self.assertEqual(api.parse_start(start_payload() | {"question": joined_emoji}).question,
                         joined_emoji)
        for context in (
            {"kind": "question"},
            {"kind": "node", "viewKind": "Architecture", "visibleNodeId": "code:Model",
             "rootId": None, "detailSourceId": None},
            {"kind": "source", "filePath": "app.py", "mode": "function",
             "anchor": {"filePath": "app.py", "startLine": 2, "startColumn": 0,
                        "endLine": 4, "endColumn": 8}},
        ):
            with self.subTest(kind=context["kind"]):
                value = api.parse_start(start_payload(context))
                self.assertEqual(value.scope.project_epoch, "epoch")
                self.assertEqual(value.question, "解释这个功能")
                self.assertEqual(value.context.kind, context["kind"])
                if context["kind"] == "node":
                    self.assertEqual(value.context.view_kind, ViewKind.ARCHITECTURE)
                context["kind"] = "untrusted mutation"
                self.assertNotEqual(value.context.kind, context["kind"])
                with self.assertRaises(FrozenInstanceError):
                    value.question = "changed"

    def test_start_rejects_unknown_source_fields_and_invalid_coordinates(self):
        api = self.api()
        source = {"kind": "source", "filePath": "app.py", "mode": "file", "anchor": None}
        node = {"kind": "node", "viewKind": "Architecture", "visibleNodeId": "code:Model",
                "rootId": None, "detailSourceId": None}
        cases = [start_payload() | {"sourceText": "never accept editor source"},
                 start_payload({"kind": "question", "draft": "not authorized"}),
                 start_payload(source | {"filePath": "../app.py"}),
                 start_payload(source | {"anchor": {"filePath": "other.py", "startLine": 1,
                                                     "startColumn": None, "endLine": 1,
                                                     "endColumn": None}}),
                 start_payload(node | {"viewKind": "UnknownView"}),
                 start_payload() | {"analysisGeneration": True},
                 start_payload() | {"configRevision": 2**53},
                 start_payload() | {"question": "\ud800"},
                 start_payload() | {"question": "😀" * 2001}]
        self.assertEqual(len(api.parse_start(start_payload() | {"question": "😀" * 2000}).question), 2000)
        for payload in cases:
            with self.subTest(payload_keys=list(payload)):
                with self.assertRaises(api.BridgeRequestError) as caught:
                    api.parse_start(payload)
                self.assertEqual(str(caught.exception), "Invalid assistant request.")
                self.assertEqual(caught.exception.code, "AI_INVALID_INPUT")
                self.assertIsNone(caught.exception.__context__)

    def test_poll_and_cancel_have_closed_nullable_project_identity(self):
        api = self.api()
        for epoch in (None, "epoch"):
            request = {"jobId": "job", "projectEpoch": epoch}
            self.assertEqual(api.parse_cancel(request).project_epoch, epoch)
            poll = api.parse_poll(request | {"afterSeq": 2**53 - 1})
            self.assertEqual(poll.after_seq, 2**53 - 1)
            for seq in (True, -1, 2**53, 1.5):
                with self.subTest(seq=seq), self.assertRaises(api.BridgeRequestError):
                    api.parse_poll(request | {"afterSeq": seq})
            with self.assertRaises(api.BridgeRequestError):
                api.parse_cancel(request | {"afterSeq": 0})
            with self.assertRaises(api.BridgeRequestError):
                api.parse_poll(request | {"afterSeq": 0, "tool": "read_source_excerpt"})

