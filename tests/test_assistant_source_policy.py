"""B4 pure policy contracts; reader authorization and disk checks live elsewhere."""

import importlib
import importlib.util
import os
import unittest
from contextlib import ExitStack
from dataclasses import FrozenInstanceError, fields, replace
from unittest.mock import patch

from codeviz.assistant.snapshot import SnapshotBinding

MESSAGES = {
    "invalid_input": "Invalid source excerpt input.",
    "scope_mismatch": "Source request scope changed.",
    "consent_required": "Source permission is unavailable.",
    "path_denied": "Source path is not permitted.",
    "source_untrusted": "Source analysis identity is unavailable.",
    "source_changed": "Source differs from the analysis snapshot.",
    "source_unavailable": "Source could not be safely read.",
    "decode_failed": "Source encoding could not be decoded.",
    "invalid_range": "Source range is invalid.",
    "secret_blocked": "Source excerpt blocked by suspected credentials.",
    "verification_failed": "Source verification could not be completed.",
}


class SourcePolicyTests(unittest.TestCase):
    def setUp(self):
        name = "codeviz.assistant.source_policy"
        self.assertIsNotNone(importlib.util.find_spec(name), f"Missing B4 API: {name}")
        self.api = importlib.import_module(name)
        for export in ("SourceExcerptError", "SourceConsent", "validate_source_path",
                       "is_protected_source_path"):
            self.assertTrue(hasattr(self.api, export), f"Missing B4 API: {export}")
        self.binding = SnapshotBinding(
            "E:/trusted/project" if os.name == "nt" else "/trusted/project",
            ("ignored",), "epoch-a", 7)

    def consent(self, **changes):
        values = dict(binding=self.binding, consent_id="consent-1",
                      provider_origin="https://provider.example", allowed_files=("app.py",))
        values.update(changes)
        return self.api.SourceConsent(**values)

    def rejected(self, action, code="invalid_input"):
        with self.assertRaises(self.api.SourceExcerptError) as caught:
            action()
        error = caught.exception
        self.assertEqual(error.code, code)
        self.assertEqual(error.message, MESSAGES[code])
        self.assertEqual(str(error), MESSAGES[code])
        self.assertIsNone(error.__cause__)
        if error.__context__ is not None:
            self.assertTrue(error.__suppress_context__)

    def test_canonical_paths_preserve_spelling(self):
        for path in ("app.py", "pkg/App.PY", "源码/示例.py", "space name/a-b_1.py",
                     "env.py", "pkg/conifer.py", "pkg/conifer .py", "pkg/null .py",
                     "pkg/COM10 .py", "pkg/LPT10 .py"):
            with self.subTest(path=path):
                self.assertEqual(self.api.validate_source_path(path), path)

    def test_escape_alias_and_windows_unsafe_paths_are_rejected(self):
        paths = ("", "/app.py", "//host/share/app.py", "\\\\host\\share\\app.py",
                 "C:/app.py", "C:app.py", "a\\b.py", "a:b.py", "a.py:stream",
                 ".", "..", "./app.py", "../app.py", "a/../b.py", "a/./b.py",
                 "a//b.py", "a/", "a./b.py", "a /b.py", "app.py.", "app.py ",
                 "a\x00.py", "a\x01.py", "a\t.py", "a\n.py", "a\x7f.py",
                 "a\x85.py", "a\ud800.py", "CON", "con.py", "PRN/x.py", "AUX.py",
                 "NUL.txt", "COM1.py", "com9/x.py", "LPT1.py", "lpt9.txt",
                 "CON .py", "pkg/CON .py", "pkg/NUL .py", "pkg/COM1 .py",
                 "pkg/LPT1 .py", "pkg/COM¹ .py", "pkg/COM² .py", "pkg/COM³ .py",
                 "pkg/LPT¹ .py", "pkg/LPT² .py", "pkg/LPT³ .py",
                 "pkg/con  .py", "pkg/nul .py", "pkg/CON .py/app.py")
        for path in paths:
            with self.subTest(path=repr(path)):
                self.rejected(lambda path=path: self.api.validate_source_path(path))

    def test_path_requires_exact_string_type(self):
        class StringSubclass(str):
            pass

        for value in (None, True, 1, b"app.py", ["app.py"], StringSubclass("app.py")):
            with self.subTest(value=repr(value)):
                self.rejected(lambda value=value: self.api.validate_source_path(value))

    def test_protected_components_are_case_insensitive(self):
        components = (".git", ".hg", ".svn", ".ssh", ".aws", ".azure", ".gnupg",
                      ".codex", "secrets", "credentials", "private_keys")
        for component in components:
            for spelling in (component, component.upper()):
                with self.subTest(component=spelling):
                    self.assertIs(self.api.is_protected_source_path(
                        f"pkg/{spelling}/safe.py"), True)

    def test_protected_basenames_and_extensions(self):
        names = (".env", ".env.py", ".env.local", "id_rsa", "id_rsa.pub", "id_dsa",
                 "id_dsa.backup", "id_ecdsa", "id_ecdsa.pub", "id_ed25519",
                 "id_ed25519.pub", "credentials", "credentials.json", "secrets.json",
                 "secrets.py", "credentials.py")
        names += tuple("asset" + extension for extension in (
            ".pem", ".key", ".p12", ".pfx", ".crt", ".cer", ".der", ".jks", ".keystore"))
        for name in names:
            with self.subTest(name=name):
                self.assertIs(self.api.is_protected_source_path(f"pkg/{name.upper()}"), True)

    def test_protection_does_not_use_substring_or_directory_extension_matches(self):
        for path in ("mysecrets.py", "pkg/secrets_utils.py", ".github/app.py",
                     "env.py", ".environment.py", "id_rsax.py", "app.key.py",
                     "pkg.key/app.py", "secrets.json.backup", "credential/app.py"):
            with self.subTest(path=path):
                self.assertIs(self.api.is_protected_source_path(path), False)

    def test_consent_is_exact_frozen_slotted_record(self):
        consent = self.consent()
        self.assertEqual([field.name for field in fields(consent)],
                         ["binding", "consent_id", "provider_origin", "allowed_files"])
        self.assertEqual(consent.binding, self.binding)
        self.assertEqual(consent.allowed_files, ("app.py",))
        self.assertFalse(hasattr(consent, "__dict__"))
        with self.assertRaises(FrozenInstanceError):
            consent.allowed_files = ("other.py",)

    def test_empty_allowlist_and_protected_grants_remain_literal(self):
        self.assertEqual(self.consent(allowed_files=()).allowed_files, ())
        paths = (".env.py", "pkg/secrets/token.py", "secrets.py")
        consent = self.consent(allowed_files=paths)
        self.assertEqual(consent.allowed_files, paths)
        for path in paths:
            with self.subTest(path=path):
                self.assertIs(self.api.is_protected_source_path(path), True)

    def test_allowlist_requires_sorted_unique_canonical_tuple(self):
        invalid = (["app.py"], "app.py", None, ("z.py", "a.py"), ("app.py", "app.py"),
                   ("App.py", "app.py"), ("pkg", "pkg/"), ("*",), ("*.py",),
                   ("a?.py",), ("[ab].py",), ("all", "*"), ("a//b.py",),
                   ("a/./b.py",), ("a\\b.py",), (True,), ("a\ud800.py",))
        for value in invalid:
            with self.subTest(value=repr(value)):
                self.rejected(lambda value=value: self.consent(allowed_files=value))
        self.assertEqual(self.consent(allowed_files=("A.py", "b.py")).allowed_files,
                         ("A.py", "b.py"))

    def test_consent_id_uses_opaque_ascii_contract(self):
        for value in ("", "-bad", "has space", "é", "a/b", "a\n", "a" * 129,
                      "a\ud800", True, b"consent-1"):
            with self.subTest(value=repr(value)):
                self.rejected(lambda value=value: self.consent(consent_id=value))
        for value in ("a", "A.b_c-1", "a" * 128):
            with self.subTest(value=value):
                self.assertEqual(self.consent(consent_id=value).consent_id, value)

    def test_https_origin_is_preserved_without_canonicalizing(self):
        for origin in ("https://provider.example", "https://provider.example/",
                       "https://provider.example:443", "https://[::1]:8443",
                       "https://PROVIDER.example"):
            with self.subTest(origin=origin):
                self.assertEqual(self.consent(provider_origin=origin).provider_origin, origin)
        self.assertNotEqual(self.consent(), self.consent(provider_origin="https://PROVIDER.example"))

    def test_origin_rejects_credentials_paths_queries_and_invalid_ports(self):
        origins = ("http://provider.example", "https://", "https:///path", "provider.example",
                   "https://user@provider.example", "https://user:pass@provider.example",
                   "https://provider.example/api", "https://provider.example?x=1",
                   "https://provider.example#fragment", "https://provider.example:bad",
                   "https://provider.example:65536", "https://provider.example:-1",
                   " https://provider.example", "https://provider.example\n",
                   "https://pro\tvider.example", "https://provider.example/ ",
                   "https://provider.example\x00", "https://[::1", "https://x\ud800",
                   None, True, b"https://provider.example")
        for origin in origins:
            with self.subTest(origin=repr(origin)):
                self.rejected(lambda origin=origin: self.consent(provider_origin=origin))

    def test_binding_is_typed_and_all_identity_fields_affect_consent(self):
        for binding in (None, {}, "binding"):
            with self.subTest(binding=binding):
                self.rejected(lambda binding=binding: self.consent(binding=binding))
        variants = (replace(self.binding, project_root=self.binding.project_root + "-other"),
                    replace(self.binding, ignore_paths=("different",)),
                    replace(self.binding, project_epoch="epoch-b"),
                    replace(self.binding, analysis_generation=8))
        for binding in variants:
            with self.subTest(binding=binding):
                consent = self.consent(binding=binding)
                self.assertEqual(consent.binding, binding)
                self.assertNotEqual(consent, self.consent())
        # Validate a corrupted DTO at the policy boundary, without invoking any reader.
        corrupted = replace(self.binding)
        object.__setattr__(corrupted, "analysis_generation", True)
        self.rejected(lambda: self.consent(binding=corrupted))

    def test_strict_consent_strings_and_tuple_subclasses(self):
        class StringSubclass(str):
            pass

        class TupleSubclass(tuple):
            pass

        for change in ({"consent_id": StringSubclass("consent-1")},
                       {"provider_origin": StringSubclass("https://provider.example")},
                       {"allowed_files": TupleSubclass(("app.py",))},
                       {"allowed_files": (StringSubclass("app.py"),)}):
            with self.subTest(change=change):
                self.rejected(lambda change=change: self.consent(**change))

    def test_fixed_error_table_and_unknown_code_do_not_echo_input(self):
        for code, message in MESSAGES.items():
            with self.subTest(code=code):
                error = self.api.SourceExcerptError(code)
                self.assertIsInstance(error, ValueError)
                self.assertEqual((error.code, error.message, str(error)), (code, message, message))
        for code in ("private/source.py", None, [], {"secret": "synthetic"}):
            with self.subTest(code=code):
                error = self.api.SourceExcerptError(code)
                self.assertEqual((error.code, str(error)), ("invalid_input", MESSAGES["invalid_input"]))

    def test_policy_operations_do_not_access_files_or_network(self):
        targets = ("builtins.open", "io.open", "os.open", "os.stat", "os.lstat",
                   "os.scandir", "codeviz.source_handles.read_source_bytes",
                   "socket.socket", "socket.getaddrinfo", "subprocess.Popen")
        with ExitStack() as stack:
            for target in targets:
                stack.enter_context(patch(target, side_effect=AssertionError("pure policy did I/O")))
            self.assertEqual(self.api.validate_source_path("pkg/app.py"), "pkg/app.py")
            self.assertIs(self.api.is_protected_source_path("pkg/app.py"), False)
            self.assertEqual(self.consent().binding, self.binding)
            self.rejected(lambda: self.api.validate_source_path("../app.py"))
        for path in ("CON .py", "pkg/CON .py", "pkg/NUL .py", "pkg/COM1 .py",
                     "pkg/LPT1 .py", "pkg/COM¹ .py", "pkg/COM² .py", "pkg/COM³ .py",
                     "pkg/LPT¹ .py", "pkg/LPT² .py", "pkg/LPT³ .py",
                     "pkg/con  .py", "pkg/nul .py", "pkg/CON .py/app.py"):
            with self.subTest(path=path):
                with ExitStack() as stack:
                    for target in targets:
                        stack.enter_context(patch(
                            target, side_effect=AssertionError("pure policy did I/O")))
                    self.rejected(lambda path=path: self.api.validate_source_path(path))


if __name__ == "__main__":
    unittest.main()
