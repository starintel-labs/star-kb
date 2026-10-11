"""Canonical client policy tests; these do not prove durable persistence."""
import os
import unittest

ROOT = os.environ.get("STARLANG_ROOT")
if ROOT:
    from star_kb_service.contracts import Fault
    from star_kb_service.persistence import ServerDocuments


@unittest.skipUnless(ROOT, "pinned generated schema required")
class PersistenceBoundaryTests(unittest.TestCase):
    def client(self, url="http://127.0.0.1:1"):
        return ServerDocuments(ROOT, "tenant-a", "d", url, "x" * 32)

    def document(self):
        return {"id": "fixture", "dtype": "relation", "dataset": "d", "schemaVersion": "0.10.1",
                "source": {"schema": "entity", "id": "a"},
                "destination": {"schema": "entity", "id": "b"}, "predicate": "owns"}

    def test_remote_redirect_and_embedded_credentials_not_configuration(self):
        for url in ("http://example.org", "https://127.0.0.1", "http://user:secret@127.0.0.1",
                    "http://127.0.0.1/private", "http://127.0.0.1?token=secret"):
            with self.assertRaisesRegex(Fault, "invalid_store_configuration"):
                self.client(url)

    def test_generated_unknown_fields_and_scope_fail_before_network(self):
        for change in ({"unknown": 1}, {"dataset": "foreign"}, {"schemaVersion": "0.9.0"}):
            with self.assertRaises(Fault):
                self.client().create({**self.document(), **change})

    def test_revision_is_server_owned(self):
        with self.assertRaisesRegex(Fault, "server_owned_revision"):
            self.client().create({**self.document(), "rev": "forged"})

    def test_schema_validation_owns_extension_values(self):
        source = {**self.document(), "extensions": {"future": ["雪", False, None]}}
        result = self.client().validate(source)
        result["extensions"]["future"].append("new")
        self.assertEqual(["雪", False, None], source["extensions"]["future"])

    def test_snapshot_admission_is_bounded_before_reads(self):
        for identifiers in ([], ["a", "a"], [str(i) for i in range(257)]):
            with self.assertRaisesRegex(Fault, "fact_limit"):
                self.client().snapshot(identifiers)

    def test_later_store_failure_releases_prior_worker(self):
        from unittest.mock import Mock, patch
        from star_kb_service.__main__ import configured_service
        worker = Mock()
        config = {"scopes": [
            {"tenant": "tenant-a", "dataset": "d", "documents": [{**self.document(), "rev": "1-fixture"}]},
            {"tenant": "tenant-b", "dataset": "d", "server": {
                "url": "http://127.0.0.1:1", "tokenEnv": "KB_TEST_STORE_TOKEN", "documentIds": ["x"]}}],
            "credentials": []}
        with patch.dict(os.environ, {"KB_TEST_STORE_TOKEN": "x" * 32}), \
                patch("star_kb_service.__main__.Engine", return_value=worker), \
                patch("star_kb_service.__main__.ServerDocuments") as store:
            store.return_value.snapshot.side_effect = Fault("store_unavailable")
            with self.assertRaisesRegex(Fault, "store_unavailable"):
                configured_service(config, ROOT)
        worker.close.assert_called_once_with()
