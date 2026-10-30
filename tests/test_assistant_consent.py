"""C2 memory authorization RED contracts; no jobs/service/UI or B5 wire proof.

Bounded proposed local API (not a claimed existing interface): ConsentStore()
with grant(project_key=..., config=..., allowed_files=..., confirmed=...),
get(project_key=..., origin=...) -> ConsentGrant | None,
revoke(project_key=..., origin=...) -> int revision, and
is_current(grant, config=...) -> bool. Keys are backend-canonical identities.
Store owns revisions; replacements, including shrink, invalidate old grants.
B snapshot inventory/discovery intersection, task/history invalidation and
whole-file metadata security gates remain the main agent's integration scope.
Existing B4 SourceConsent is reused unchanged, never treated as runtime grant.
"""

import importlib
import importlib.util
import os
import unittest
from dataclasses import FrozenInstanceError, replace
from unittest.mock import patch


class ConsentContractTests(unittest.TestCase):
    def setUp(self):
        # No missing C imports at collection time; absence is an ordinary FAIL.
        for name, exports in (
            ("codeviz.assistant.config", ("ProviderConfig",)),
            ("codeviz.assistant.consent", ("ConsentGrant", "ConsentStore")),
        ):
            self.assertIsNotNone(importlib.util.find_spec(name), f"Missing C2 API module: {name}")
            module = importlib.import_module(name)
            for export in exports:
                self.assertTrue(hasattr(module, export), f"Missing C2 API: {name}.{export}")
            if name.endswith("config"):
                self.Config = module.ProviderConfig
            else:
                self.Grant, self.Store = module.ConsentGrant, module.ConsentStore
        self.assertTrue(callable(getattr(self.Config, "from_base_url", None)),
                        "Proposed C2 seam missing: ProviderConfig.from_base_url")
        self.config = self.Config.from_base_url(
            base_url="https://provider.invalid/v1", model="test", revision=1)
        self.store = self.Store()
        for method in ("grant", "get", "revoke", "is_current"):
            self.assertTrue(callable(getattr(self.store, method, None)),
                            f"Proposed C2 seam missing: ConsentStore.{method}")
        self.project = os.path.normcase(os.path.abspath("project-a"))
        self.other_project = os.path.normcase(os.path.abspath("project-b"))

    def grant(self, **changes):
        arguments = dict(project_key=self.project, config=self.config,
                         allowed_files=("src/app.py",), confirmed=True)
        arguments.update(changes)
        return self.store.grant(**arguments)

    def get(self, **changes):
        arguments = dict(project_key=self.project, origin=self.config.origin)
        arguments.update(changes)
        return self.store.get(**arguments)

    def rejected(self, action):
        from codeviz.assistant.contracts import AgentError

        with self.assertRaises(ValueError) as caught:
            action()
        self.assertIsNot(type(caught.exception), ValueError)
        self.assertNotIsInstance(caught.exception, AgentError)
        self.assertEqual(getattr(caught.exception, "code", None), "AI_INVALID_INPUT")

    def test_new_store_and_repeated_getters_never_grant_or_contact_provider(self):
        with patch("socket.getaddrinfo", side_effect=AssertionError("DNS forbidden")), \
                patch("socket.socket", side_effect=AssertionError("network forbidden")):
            for _ in range(3):
                self.assertIsNone(self.get())
            fresh = self.Store()
            self.assertIsNone(fresh.get(project_key=self.project, origin=self.config.origin))

    def test_only_explicit_boolean_confirmation_creates_a_grant(self):
        for confirmed in (False, None, 0, 1, "true", "yes"):
            with self.subTest(confirmed=confirmed):
                self.rejected(lambda confirmed=confirmed: self.grant(confirmed=confirmed))
                self.assertIsNone(self.get())
        grant = self.grant()
        self.assertIsInstance(grant, self.Grant)
        self.assertIs(grant.enabled, True)
        self.assertTrue(self.store.is_current(grant, config=self.config))

    def test_grant_is_frozen_bound_to_exact_project_origin_config_and_explicit_files(self):
        grant = self.grant()
        self.assertEqual((grant.project_key, grant.origin, grant.config_revision,
                          grant.allowed_files),
                         (self.project, self.config.origin, self.config.revision, ("src/app.py",)))
        self.assertEqual(type(grant.revision), int)
        self.assertGreaterEqual(grant.revision, 0)
        self.assertFalse(hasattr(grant, "__dict__"))
        with self.assertRaises(FrozenInstanceError):
            grant.allowed_files = ("src/private.py",)
        self.assertEqual(self.get(), grant)
        self.assertIsNone(self.get(project_key=self.other_project))
        self.assertIsNone(self.get(origin="https://other.invalid:443"))

    def test_whitelist_rejects_globs_raw_aliases_protected_paths_and_non_tuple_types(self):
        groups = {
            "glob": (("**/*.py",), ("src/*",)),
            "alias": (("src/../app.py",), ("./src/app.py",), ("src\\app.py",),
                      ("/app.py",), ("C:/app.py",), ("src//app.py",), ("src/app.py ",)),
            "protected": ((".env",), (".git/config",), ("secrets/key.py",),
                          ("cert.pem",), ("src/credentials.py",)),
            "unsorted": (("src/helpers.py", "src/app.py"),),
            "duplicate": (("src/app.py", "src/app.py"),),
            "case_alias": (("src/App.py", "src/app.py"),),
            "type": (["src/app.py"], "src/app.py", None, (True,), (7,)),
        }
        for group, whitelists in groups.items():
            for files in whitelists:
                with self.subTest(group=group, files=files):
                    self.rejected(lambda files=files: self.grant(allowed_files=files))
                    self.assertIsNone(self.get())

    def test_canonical_whitelist_interoperates_with_existing_b4_values_without_expansion(self):
        from codeviz.assistant import RequestScope
        from codeviz.assistant.snapshot import SnapshotBinding
        from codeviz.assistant.source_policy import SourceConsent

        grant = self.grant(allowed_files=("src/app.py", "src/helpers.py"))
        scope = RequestScope("epoch-a", 1, "request-a")
        binding = SnapshotBinding(self.project, (), scope.project_epoch, scope.analysis_generation)
        consent = SourceConsent(binding, "consent-a", grant.origin, grant.allowed_files)
        self.assertEqual(consent.binding.project_epoch, scope.project_epoch)
        self.assertEqual(consent.allowed_files, grant.allowed_files)
        # A new analysis binding cannot manufacture permission for a new file.
        newer = replace(binding, analysis_generation=2)
        self.assertNotEqual(newer, consent.binding)
        self.assertNotIn("src/new.py", self.get().allowed_files)
        # C rejects the same raw tuples as B4; neither silently normalizes them.
        for files in (("src/helpers.py", "src/app.py"),
                      ("src/app.py", "src/app.py"),
                      ("src/App.py", "src/app.py")):
            with self.subTest(files=files):
                with self.assertRaises(ValueError):
                    SourceConsent(binding, "consent-a", grant.origin, files)
                self.rejected(lambda files=files: self.grant(allowed_files=files))
                self.rejected(lambda files=files: replace(grant, allowed_files=files))
                self.assertEqual(self.get(), grant)
                self.assertTrue(self.store.is_current(grant, config=self.config))

    def test_scope_enlargement_requires_confirmation_and_shrink_replaces_revision(self):
        old = self.grant(allowed_files=("src/app.py", "src/helpers.py"))
        self.rejected(lambda: self.grant(
            allowed_files=("src/app.py", "src/helpers.py", "src/new.py"), confirmed=False))
        self.assertEqual(self.get(), old)
        for files in (("src/app.py",), ("src/app.py", "src/new.py")):
            with self.subTest(files=files):
                new = self.grant(allowed_files=files)
                self.assertGreater(new.revision, old.revision)
                self.assertFalse(self.store.is_current(old, config=self.config))
                self.assertTrue(self.store.is_current(new, config=self.config))
                self.assertEqual(new.allowed_files, files)
                old = new

    def test_destination_endpoint_model_or_key_revision_changes_cannot_reuse_grant(self):
        grant = self.grant()
        for changes in (
            dict(base_url="https://other.invalid/v1", revision=2),
            dict(base_url="https://provider.invalid/", revision=2),
            dict(model="other-model", revision=2),
            dict(revision=2),  # Key replacement increments config revision upstream.
        ):
            with self.subTest(changes=changes):
                arguments = dict(base_url="https://provider.invalid/v1", model="test", revision=1)
                arguments.update(changes)
                changed = self.Config.from_base_url(**arguments)
                self.assertFalse(self.store.is_current(grant, config=changed))
        self.assertTrue(self.store.is_current(grant, config=self.config))

    def test_forged_disabled_scope_or_revision_grants_are_not_current(self):
        grant = self.grant()
        for changes in (dict(project_key=self.other_project), dict(origin="https://other.invalid:443"),
                        dict(config_revision=2), dict(revision=grant.revision + 1),
                        dict(allowed_files=("src/other.py",)), dict(enabled=False)):
            with self.subTest(changes=changes):
                self.assertFalse(self.store.is_current(replace(grant, **changes), config=self.config))

    def test_grant_record_rejects_bool_integer_coercion(self):
        grant = self.grant()
        for field, values in {
            "config_revision": (True, False, -1, "1", 1.0),
            "revision": (True, False, -1, "1", 1.0),
            "enabled": (0, 1, None, "true"),
        }.items():
            for value in values:
                with self.subTest(field=field, value=value):
                    self.rejected(lambda field=field, value=value:
                                  replace(grant, **{field: value}))

    def test_revoke_increments_revision_invalidates_old_grant_and_preserves_other_partitions(self):
        grant = self.grant()
        other_project = self.grant(project_key=self.other_project)
        other_config = self.Config.from_base_url(
            base_url="https://other.invalid/v1", model="test", revision=2)
        other_origin = self.grant(config=other_config)
        revision = self.store.revoke(project_key=self.project, origin=self.config.origin)
        self.assertEqual(type(revision), int)
        self.assertGreater(revision, grant.revision)
        self.assertFalse(self.store.is_current(grant, config=self.config))
        current = self.get()
        self.assertTrue(current is None or current.enabled is False)
        self.assertTrue(self.store.is_current(other_project, config=self.config))
        self.assertTrue(self.store.is_current(other_origin, config=other_config))
        new = self.grant()
        self.assertGreater(new.revision, revision)
        self.assertFalse(self.store.is_current(grant, config=self.config))
        self.assertTrue(self.store.is_current(new, config=self.config))
        fresh = self.Store()
        self.assertIsNone(fresh.get(project_key=self.project, origin=self.config.origin))


if __name__ == "__main__":
    unittest.main()
