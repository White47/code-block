"""Analysis cache round-trips facts without AST or executable deserialization."""

import json
import unittest
from copy import deepcopy
from dataclasses import replace
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import get_type_hints
from unittest.mock import patch

from tests.test_training import analyze_files

import codeviz.persistence.codec as codec
from codeviz.analysis.project import analyze_project
from codeviz.core import ViewKind
from codeviz.persistence.codec import decode_analysis, encode_analysis
from codeviz.search import search_project
from codeviz.views import ViewRequest, build_visible_graph


class AnalysisCodecTests(unittest.TestCase):
    def test_parsed_file_seven_keys_retain_actual_raw_hash_without_source(self):
        raw = "# PRIVATE_RAW_MARKER\r\nvalue = 'é'\r\n".encode("utf-8")
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "run.py").write_bytes(raw)
            (root / "bad.py").write_bytes(b"def broken(:\n")
            analysis = analyze_project(root)
        self.assertEqual(next(file for file in analysis.files if file.relative_path == "run.py").content_hash,
                         sha256(raw).hexdigest())
        payload = encode_analysis(analysis)
        records = payload["fields"]["files"]["items"]
        expected_keys = {"@", "relative_path", "module_name", "line_count",
                         "issues", "was_parsed", "content_hash"}
        for record, parsed in zip(records, analysis.files, strict=True):
            with self.subTest(path=parsed.relative_path):
                self.assertEqual(set(record), expected_keys)
                self.assertEqual(record["content_hash"], parsed.content_hash)
        self.assertTrue(all(file.content_hash is not None for file in analysis.files))
        serialized = json.dumps(payload)
        self.assertNotIn("PRIVATE_RAW_MARKER", serialized)
        self.assertNotIn("ast.Module", serialized)
        restored = decode_analysis(json.loads(serialized))
        self.assertEqual([file.content_hash for file in restored.files],
                         [file.content_hash for file in analysis.files])
        self.assertEqual([file.was_parsed for file in restored.files],
                         [file.was_parsed for file in analysis.files])
        self.assertTrue(all(file.tree is None for file in restored.files))

    def test_standalone_codec_allows_synthetic_success_with_null_hash(self):
        analysis = analyze_files({"run.py": "value = 1\n"})
        synthetic = replace(analysis, files=(replace(analysis.files[0], content_hash=None),))
        payload = encode_analysis(synthetic)
        record = payload["fields"]["files"]["items"][0]
        self.assertIn("content_hash", record)
        self.assertIsNone(record["content_hash"])
        restored = decode_analysis(payload)
        self.assertTrue(restored.files[0].was_parsed)
        self.assertIsNone(restored.files[0].content_hash)
        self.assertIsNone(restored.files[0].tree)

    def test_encoder_rejects_invalid_content_hash_risk_classes(self):
        analysis = analyze_files({"run.py": "value = 1\n"})
        for digest in (True, 64, [], "", "a" * 63, "a" * 65, "A" * 64, "g" * 64):
            with self.subTest(digest=digest):
                invalid = replace(analysis, files=(replace(analysis.files[0], content_hash=digest),))
                with self.assertRaises(ValueError):
                    encode_analysis(invalid)

    def test_decoder_accepts_only_null_or_strict_lower_hex_hash(self):
        payload = encode_analysis(analyze_files({"run.py": "value = 1\n"}))
        for digest in (None, sha256(b"actual raw bytes\r\n").hexdigest()):
            with self.subTest(digest=digest):
                candidate = deepcopy(payload)
                candidate["fields"]["files"]["items"][0]["content_hash"] = digest
                try:
                    restored = decode_analysis(candidate)
                except ValueError as error:
                    self.fail(f"valid seven-key parsed-file record rejected: {error}")
                self.assertEqual(restored.files[0].content_hash, digest)
                self.assertIsNone(restored.files[0].tree)
        for digest in (False, 64, {}, "", "a" * 63, "a" * 65, "A" * 64, "g" * 64):
            with self.subTest(digest=digest):
                candidate = deepcopy(payload)
                candidate["fields"]["files"]["items"][0]["content_hash"] = digest
                with self.assertRaises(ValueError):
                    decode_analysis(candidate)

    def test_decoder_rejects_missing_hash_and_extra_parsed_file_fields(self):
        payload = encode_analysis(analyze_files({"run.py": "value = 1\n"}))
        for change in ("missing", "extra"):
            with self.subTest(change=change):
                candidate = deepcopy(payload)
                record = candidate["fields"]["files"]["items"][0]
                if change == "missing":
                    record.pop("content_hash", None)
                else:
                    record["content_hash"] = None
                    record["raw_source"] = "PRIVATE_RAW_MARKER"
                with self.assertRaises(ValueError):
                    decode_analysis(candidate)

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

    def test_decoder_resolves_type_hints_once_per_registered_type(self):
        analysis = analyze_files({"run.py": (
            "def train(loss, optimizer):\n"
            "    loss.backward()\n    optimizer.step()\n"
            "    loss = optimizer.step()\n"
        )})
        payload = encode_analysis(analysis)
        codec._field_hints.cache_clear()
        with patch.object(codec, "get_type_hints", wraps=get_type_hints) as hints:
            decode_analysis(payload)
        self.assertLessEqual(hints.call_count, len(codec._TYPES))
