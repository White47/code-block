"""C2 domain RED contracts; no SDK gate, real transport, jobs or UI acceptance.

Proposed narrow seams where the plan leaves signatures open:
ProviderConfig.from_base_url(base_url=..., model=..., revision=...),
provider.TransportPolicy() (immutable fail-closed options),
provider.validate_transport_target(config, method=..., endpoint=...), and
provider.validate_response_status(status_code). These are local policy helpers,
not invented SDK methods. Actual SDK enforcement still needs the C1 gate.
create_provider(config, None) must reject before optional imports/client/DNS.
"""

import importlib
import importlib.util
import os
import socket
import subprocess
import sys
import unittest
from dataclasses import FrozenInstanceError
from textwrap import dedent
from unittest.mock import patch


class ProviderContractTests(unittest.TestCase):
    def setUp(self):
        # Guard once per group, before subTests can repeat a missing API failure.
        self.Config = self.module("codeviz.assistant.config", "ProviderConfig").ProviderConfig
        self.assertTrue(callable(getattr(self.Config, "from_base_url", None)),
                        "Approved C2 seam missing: ProviderConfig.from_base_url")
        self.module("codeviz.assistant.provider", "MemoryKey", "TransportPolicy",
                    "validate_transport_target", "validate_response_status", "create_provider")

    def module(self, name, *exports):
        self.assertIsNotNone(importlib.util.find_spec(name),
                             f"Missing C2 API module: {name}")
        api = importlib.import_module(name)
        for export in exports:
            self.assertTrue(hasattr(api, export), f"Missing C2 API: {name}.{export}")
        return api

    def config(self, **changes):
        arguments = dict(base_url="https://provider.invalid/v1", model="test", revision=1)
        arguments.update(changes)
        return self.Config.from_base_url(**arguments)

    def rejected(self, action, *, code=None, sensitive=None):
        from codeviz.assistant.contracts import AgentError

        with self.assertRaises(ValueError) as caught:
            action()
        self.assertIsNot(type(caught.exception), ValueError)
        self.assertNotIsInstance(caught.exception, AgentError)
        self.assertIn(getattr(caught.exception, "code", None),
                      ("AI_INVALID_INPUT", "AI_PROTOCOL_INCOMPATIBLE",
                       "AI_NOT_CONFIGURED", "AI_DEPENDENCY_MISSING"))
        if code is not None:
            self.assertEqual(getattr(caught.exception, "code", None), code)
            # A's fixed error vocabulary must remain independent of C's AI codes.
            self.assertEqual(AgentError(code).code, "invalid_input")
        if sensitive:
            self.assertNotIn(sensitive, str(caught.exception))
            self.assertNotIn(sensitive, repr(caught.exception))

    def test_https_dns_idna_ipv6_effective_ports_and_fixed_endpoints(self):
        cases = (
            ("https://Provider.INVALID", "https://provider.invalid:443", "/chat/completions"),
            ("https://provider.invalid/", "https://provider.invalid:443", "/chat/completions"),
            ("https://provider.invalid:443/v1/", "https://provider.invalid:443",
             "/v1/chat/completions"),
            ("https://bücher.invalid/v1", "https://xn--bcher-kva.invalid:443",
             "/v1/chat/completions"),
            ("https://xn--bcher-kva.invalid/v1", "https://xn--bcher-kva.invalid:443",
             "/v1/chat/completions"),
            ("https://XN--BCHER-KVA.INVALID/v1", "https://xn--bcher-kva.invalid:443",
             "/v1/chat/completions"),
            ("https://provider.invalid:8443/v1", "https://provider.invalid:8443",
             "/v1/chat/completions"),
            ("https://[2001:db8::1]/v1", "https://[2001:db8::1]:443",
             "/v1/chat/completions"),
            ("https://[2001:db8::1]:65535", "https://[2001:db8::1]:65535",
             "/chat/completions"),
        )
        with patch("socket.getaddrinfo", side_effect=AssertionError("C2 must not resolve DNS")):
            for url, origin, path in cases:
                with self.subTest(url=url):
                    config = self.config(base_url=url)
                    self.assertEqual(config.origin, origin)
                    self.assertEqual(config.endpoint, origin + path)
                    self.assertEqual((config.model, config.revision), ("test", 1))
                    self.assertFalse(hasattr(config, "__dict__"))
                    with self.assertRaises(FrozenInstanceError):
                        config.endpoint = "https://other.invalid/chat/completions"

    def test_rejects_url_credential_control_and_alias_risk_groups(self):
        groups = {
            "scheme": ("http://provider.invalid", "//provider.invalid", "provider.invalid",
                       "file:///app.py", "https:///v1"),
            "credentials": ("https://user:synthetic-secret@provider.invalid/v1",
                            "https://user@provider.invalid", "https://@provider.invalid"),
            "query_fragment": ("https://provider.invalid?", "https://provider.invalid#",
                               "https://provider.invalid/v1?q=1", "https://provider.invalid/#x"),
            "controls": (" https://provider.invalid", "https://provider.invalid ",
                         "https://pro\tvider.invalid", "https://provider.invalid/\nv1",
                         "https://provider.invalid\x00", "https://provider.invalid/\rv1"),
            "path_alias": ("https://provider.invalid/v1/../v1", "https://provider.invalid//v1",
                           "https://provider.invalid/%76%31", "https://provider.invalid/%2e/v1",
                           "https://provider.invalid/v1%2f", "https://provider.invalid/v1%5c",
                           "https://provider.invalid/%252e%252e/v1",
                           "https://provider.invalid\\v1"),
            "host_port": ("https://provider.invalid:0", "https://provider.invalid:65536",
                          "https://provider.invalid:-1", "https://provider.invalid:abc",
                          "https://provider.invalid:", "https://[2001:db8::zz]/v1",
                          "https://[2001:db8::1", "https://[fe80::1%25eth0]/v1",
                          "https://bad..invalid/v1", "https://%70rovider.invalid/v1"),
            "malformed_idna": ("https://xn--a.invalid/v1", "https://xn--abc.invalid/v1",
                               "https://a\u05d0.invalid/v1"),
        }
        for group, urls in groups.items():
            for url in urls:
                with self.subTest(group=group, url=url):
                    self.rejected(lambda url=url: self.config(base_url=url),
                                  code="AI_INVALID_INPUT" if group == "malformed_idna" else None,
                                  sensitive="synthetic-secret" if "synthetic-secret" in url else None)

    def test_unsupported_base_prefix_is_fixed_compatibility_error(self):
        for path in ("/api/v1", "/v2", "/models", "/chat/completions", "/V1"):
            with self.subTest(path=path):
                self.rejected(lambda path=path: self.config(
                    base_url="https://provider.invalid" + path),
                    code="AI_PROTOCOL_INCOMPATIBLE")
        with self.subTest(fixed_error="AI_EVIDENCE_INVALID"):
            api = self.module("codeviz.assistant.provider", "ProviderError")
            error = api.ProviderError("AI_EVIDENCE_INVALID")
            self.assertEqual(error.code, "AI_EVIDENCE_INVALID")
            self.assertEqual(str(error), "Assistant evidence validation failed.")
            self.assertEqual(error.args, ("Assistant evidence validation failed.",))
            self.assertIsNone(error.__cause__)
            self.assertIsNone(error.__context__)

    def test_config_string_bounds_types_and_revision_are_strict(self):
        for model in ("a", "模型", "m" * 128):
            with self.subTest(valid_model=model):
                self.assertEqual(self.config(model=model).model, model)
        for field, values in {
            "base_url": (None, True, 1, b"https://provider.invalid", "x" * 2049),
            "model": ("", "m" * 129, "m\n", "m\x00", "m\x7f", "\ud800", None, True, 1),
            "revision": (-1, True, False, 1.0, "1", None),
        }.items():
            for value in values:
                with self.subTest(field=field, value=value):
                    self.rejected(lambda field=field, value=value: self.config(**{field: value}))

    def test_memory_key_printable_bound_and_repr_exclusion(self):
        api = self.module("codeviz.assistant.provider", "MemoryKey")
        for value in ("synthetic-key", "k" * 8192, "测试密钥"):
            with self.subTest(valid_length=len(value)):
                key = api.MemoryKey(value)
                self.assertEqual(key.value, value)
                self.assertNotIn(value, repr(key))
                self.assertFalse(hasattr(key, "__dict__"))
        for value in ("", "k" * 8193, "key\r", "key\n", "key\x00", "key\x7f",
                      "key\t", "\ud800", None, True, 7, b"key"):
            with self.subTest(invalid_length=len(value) if isinstance(value, str) else None):
                self.rejected(lambda value=value: api.MemoryKey(value), sensitive="key")

    def test_transport_policy_is_immutable_and_disallows_proxy_tls_redirect_retry_overrides(self):
        api = self.module("codeviz.assistant.provider", "TransportPolicy")
        policy = api.TransportPolicy()
        for name, expected in (("follow_redirects", False), ("trust_env", False),
                               ("verify_tls", True), ("max_retries", 0)):
            with self.subTest(option=name):
                self.assertEqual(getattr(policy, name, None), expected)
                with self.assertRaises(FrozenInstanceError):
                    setattr(policy, name, not expected)
                self.rejected(lambda name=name, expected=expected:
                              api.TransportPolicy(**{name: not expected}))

    def test_target_pin_rejects_method_origin_port_path_and_url_alias_overrides(self):
        api = self.module("codeviz.assistant.provider", "validate_transport_target")
        config = self.config()
        api.validate_transport_target(config, method="POST", endpoint=config.endpoint)
        for method, target in (
            ("GET", config.endpoint), ("post", config.endpoint),
            ("POST", "https://other.invalid:443/v1/chat/completions"),
            ("POST", "https://provider.invalid:8443/v1/chat/completions"),
            ("POST", config.origin + "/models"), ("POST", config.endpoint + "/"),
            ("POST", config.endpoint + "?"), ("POST", config.endpoint + "#"),
            ("POST", config.origin + "/v1/../v1/chat/completions"),
        ):
            with self.subTest(method=method, target=target):
                self.rejected(lambda method=method, target=target:
                              api.validate_transport_target(config, method=method, endpoint=target))

    def test_redirect_status_is_rejected_even_without_an_alternate_origin(self):
        api = self.module("codeviz.assistant.provider", "validate_response_status")
        api.validate_response_status(200)
        for status in (301, 302, 303, 307, 308):
            with self.subTest(status=status):
                self.rejected(lambda status=status: api.validate_response_status(status),
                              code="AI_PROTOCOL_INCOMPATIBLE")

    def test_no_key_factory_rejects_before_sdk_import_dns_or_transport(self):
        api = self.module("codeviz.assistant.provider", "create_provider")
        config = self.config()
        with patch("importlib.import_module", side_effect=AssertionError("SDK import forbidden")), \
                patch.object(socket, "getaddrinfo", side_effect=AssertionError("DNS forbidden")), \
                patch.object(socket, "socket", side_effect=AssertionError("transport forbidden")), \
                patch.dict(os.environ, {"HTTPS_PROXY": "http://proxy.invalid:9",
                                        "OPENAI_API_KEY": "synthetic-env-key"}):
            self.rejected(lambda: api.create_provider(config, None), code="AI_NOT_CONFIGURED")

    def test_missing_extra_is_safe_without_network_or_exception_secret_leak(self):
        api = self.module("codeviz.assistant.provider", "create_provider", "MemoryKey")
        config = self.config()
        with patch("importlib.import_module", side_effect=ModuleNotFoundError("synthetic-secret")), \
                patch.object(socket, "getaddrinfo", side_effect=AssertionError("DNS forbidden")), \
                patch.object(socket, "socket", side_effect=AssertionError("transport forbidden")):
            self.rejected(lambda: api.create_provider(config, api.MemoryKey("synthetic-key")),
                          code="AI_DEPENDENCY_MISSING", sensitive="synthetic-secret")

    def test_cold_domain_imports_without_optional_sdk_or_network(self):
        for name in ("config", "provider"):
            self.module("codeviz.assistant." + name)
        # Fresh interpreter catches eager imports hidden by this process's module cache.
        script = dedent("""
            import importlib
            import importlib.abc
            import socket
            import sys
            from unittest.mock import patch

            class BlockSdk(importlib.abc.MetaPathFinder):
                def find_spec(self, fullname, path=None, target=None):
                    if fullname.split('.')[0] in {
                        'pydantic_ai', 'openai', 'httpx', 'httpx2', 'pydantic', 'requests'
                    }:
                        raise AssertionError('Optional SDK import: ' + fullname)

            sys.meta_path.insert(0, BlockSdk())
            with patch.object(socket, 'getaddrinfo', side_effect=AssertionError('DNS')), \\
                 patch.object(socket, 'socket', side_effect=AssertionError('socket')):
                importlib.import_module('codeviz.assistant')
                importlib.import_module('codeviz.assistant.config')
                importlib.import_module('codeviz.assistant.provider')
        """)
        result = subprocess.run([sys.executable, "-B", "-c", script], capture_output=True,
                                text=True, timeout=15, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
