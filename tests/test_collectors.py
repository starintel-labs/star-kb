from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from collectors import (  # noqa: E402
    HttpJsonCollector,
    RssAtomCollector,
    build_raw_envelope,
    enabled_source_ids,
    load_registry,
    ndjson_bytes,
    parse_registry,
    run_source,
)
from prolog_star_kb.projection import render_projection  # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures" / "collectors"
REGISTRY = ROOT / "sources" / "registry.json"
RETRIEVED_AT = "2026-09-19T12:00:00Z"

SWIPL = shutil.which("swipl")


def swipl_succeeds(goal: str) -> bool:
    result = subprocess.run(
        ["swipl", "-q", "-g", goal, "-t", "halt(1)"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


class RawEnvelopeTests(unittest.TestCase):
    def test_envelope_has_exact_contract_fields(self) -> None:
        envelope = build_raw_envelope(
            collector_id="http-json",
            source_id="fixture-http-json",
            retrieved_at=RETRIEVED_AT,
            request_url="file://tests/fixtures/collectors/example-api.json",
            media_type="application/json",
            content=b'{"a": 1}',
        )
        self.assertEqual(
            {"collector_id", "source_id", "retrieved_at", "request_url", "media_type", "sha256", "content"},
            set(envelope),
        )
        self.assertEqual(hashlib.sha256(b'{"a": 1}').hexdigest(), envelope["sha256"])

    def test_raw_hash_changes_when_content_changes(self) -> None:
        kwargs = dict(
            collector_id="http-json",
            source_id="s",
            retrieved_at=RETRIEVED_AT,
            request_url="file://x",
            media_type="application/json",
        )
        a = build_raw_envelope(content=b'{"v": 1}', **kwargs)
        b = build_raw_envelope(content=b'{"v": 2}', **kwargs)
        self.assertNotEqual(a["sha256"], b["sha256"])


class RegistryTests(unittest.TestCase):
    def test_repository_registry_loads_and_fixtures_are_offline(self) -> None:
        specs = load_registry(REGISTRY)
        self.assertTrue(specs)
        enabled = [spec for spec in specs if spec.enabled]
        self.assertTrue(enabled)
        for spec in enabled:
            self.assertTrue(spec.url.startswith("file://"), f"enabled source {spec.source_id} must stay offline")
        for spec in specs:
            self.assertLessEqual(spec.timeout_seconds, 120)
            self.assertLessEqual(spec.max_retries, 5)
            self.assertLessEqual(spec.max_items, 1000)

    def test_registry_rejects_duplicate_ids(self) -> None:
        registry = {
            "sources": [
                {"id": "dup", "type": "http_json", "url": "https://a.test/1", "enabled": False},
                {"id": "dup", "type": "http_json", "url": "https://a.test/2", "enabled": False},
            ]
        }
        with self.assertRaisesRegex(ValueError, "duplicate source id"):
            parse_registry(registry)

    def test_registry_rejects_unbounded_timeout_and_budget(self) -> None:
        base = {"id": "s", "type": "http_json", "url": "https://a.test/x", "enabled": False}
        for field, bad in [
            ("timeout_seconds", [0, -5, 3600, "30"]),
            ("max_retries", [-1, 100, 1.5]),
            ("max_items", [0, 5000, "many"]),
        ]:
            for value in bad:
                with self.assertRaises(ValueError, msg=f"{field}={value!r} must be rejected"):
                    parse_registry({"sources": [{**base, field: value}]})

    def test_registry_rejects_unknown_type_and_unsafe_scheme(self) -> None:
        with self.assertRaisesRegex(ValueError, "unknown collector type"):
            parse_registry({"sources": [{"id": "s", "type": "scraper", "url": "https://a.test/x"}]})
        with self.assertRaisesRegex(ValueError, "url scheme"):
            parse_registry({"sources": [{"id": "s", "type": "http_json", "url": "ftp://a.test/x"}]})


class CollectorContractTests(unittest.TestCase):
    def test_http_json_collect_is_deterministic(self) -> None:
        spec = [s for s in load_registry(REGISTRY) if s.source_id == "fixture-http-json"][0]
        collector = HttpJsonCollector()
        envelope_a, docs_a = collector.collect(spec, retrieved_at=RETRIEVED_AT)
        envelope_b, docs_b = collector.collect(spec, retrieved_at=RETRIEVED_AT)
        self.assertEqual(ndjson_bytes(docs_a), ndjson_bytes(docs_b))
        self.assertEqual(canonical(envelope_a), canonical(envelope_b))
        self.assertEqual("application/json", envelope_a["media_type"])
        self.assertEqual(2, len(docs_a))
        raw_bytes = FIXTURES.joinpath("example-api.json").read_bytes()
        self.assertEqual(hashlib.sha256(raw_bytes).hexdigest(), envelope_a["sha256"])

    def test_normalized_output_provenance_carries_raw_hash_and_collector(self) -> None:
        spec = [s for s in load_registry(REGISTRY) if s.source_id == "fixture-http-json"][0]
        envelope, documents = HttpJsonCollector().collect(spec, retrieved_at=RETRIEVED_AT)
        for document in documents:
            self.assertTrue(document["candidate"])
            self.assertEqual("http-json", document["provenance"]["collector"])
            self.assertEqual(f"raw-envelope:{envelope['sha256']}", document["provenance"]["transform"])
            self.assertEqual(envelope["sha256"], document["sources"][0]["content_hash"])
            self.assertEqual(f"raw:{spec.source_id}", document["sources"][0]["source_id"])

    def test_rss_and_atom_fixtures_normalize_deterministically(self) -> None:
        for source_id, media, expected_count in [
            ("fixture-rss", "application/rss+xml", 2),
            ("fixture-atom", "application/atom+xml", 1),
        ]:
            spec = [s for s in load_registry(REGISTRY) if s.source_id == source_id][0]
            collector = RssAtomCollector()
            envelope_a, docs_a = collector.collect(spec, retrieved_at=RETRIEVED_AT)
            envelope_b, docs_b = collector.collect(spec, retrieved_at=RETRIEVED_AT)
            self.assertEqual(media, envelope_a["media_type"])
            self.assertEqual(expected_count, len(docs_a))
            self.assertEqual(ndjson_bytes(docs_a), ndjson_bytes(docs_b))
            titles = {doc["data"]["title"] for doc in docs_a}
            self.assertTrue(titles)

    def test_run_source_writes_raw_and_normalized_under_artifact_dir(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            result_a = run_source(REGISTRY, "fixture-rss", td, retrieved_at=RETRIEVED_AT)
            result_b = run_source(REGISTRY, "fixture-rss", td + "/again", retrieved_at=RETRIEVED_AT)
            self.assertEqual(result_a["raw_sha256"], result_b["raw_sha256"])
            raw = Path(result_a["raw_path"]).read_bytes()
            self.assertTrue(raw.endswith(b"\n"))
            envelope = json.loads(raw.decode("utf-8"))
            self.assertEqual("rss-atom", envelope["collector_id"])
            normalized_a = Path(result_a["normalized_path"]).read_bytes()
            normalized_b = Path(result_b["normalized_path"]).read_bytes()
            self.assertEqual(normalized_a, normalized_b)

    def test_enabled_source_ids_reports_fixtures(self) -> None:
        self.assertIn("fixture-rss", enabled_source_ids(REGISTRY))
        self.assertEqual(["fixture-http-json"], enabled_source_ids(REGISTRY, "fixture-http-json"))
        self.assertEqual([], enabled_source_ids(REGISTRY, "example-remote-feed"))


class ProjectionContractTests(unittest.TestCase):
    """Always-on Python-level Prolog fact assertions plus guarded SWI-Prolog checks."""

    def _projection(self) -> tuple[str, str]:
        spec = [s for s in load_registry(REGISTRY) if s.source_id == "fixture-rss"][0]
        envelope, documents = RssAtomCollector().collect(spec, retrieved_at=RETRIEVED_AT)
        rendered = render_projection(documents)
        return rendered, envelope["sha256"]

    def test_projection_contains_candidate_doc_source_and_provenance(self) -> None:
        rendered, raw_hash = self._projection()
        self.assertIn("star_doc('starintel:candidate:fixture-rss:", rendered)
        self.assertIn("star_source('starintel:candidate:fixture-rss:", rendered)
        self.assertIn(f"'raw:fixture-rss', 'collector:rss-atom', 'file://tests/fixtures/collectors/feed.xml', null, null, '{raw_hash}')", rendered)
        self.assertIn("star_provenance('starintel:candidate:fixture-rss:", rendered)
        self.assertIn("'rss-atom'", rendered)
        self.assertIn("star_json_value('starintel:candidate:fixture-rss:", rendered)

    def test_candidate_marker_survives_projection(self) -> None:
        rendered, _ = self._projection()
        self.assertIn("'/candidate', 'boolean', true", rendered)

    @unittest.skipUnless(SWIPL is not None, "swipl not available")
    def test_swipl_loads_projection_and_resolves_raw_hash(self) -> None:
        rendered, raw_hash = self._projection()
        with tempfile.TemporaryDirectory() as td:
            projection = Path(td) / "candidates.pl"
            projection.write_text(rendered, encoding="utf-8")
            goal = (
                "use_module('kb/core/star_json.pl'), "
                f"star_json:load_projection('{projection}'), "
                "(star_json:star_doc(D, _, _, _, _, _), atom_concat('starintel:candidate:fixture-rss:', _, D) -> true; halt(1)), "
                f"(star_json:star_source(_, 'raw:fixture-rss', _, _, _, _, '{raw_hash}') -> halt(0); halt(1))"
            )
            self.assertTrue(swipl_succeeds(goal), f"SWI-Prolog query failed: {goal}")


class CollectorProjectionCliTests(unittest.TestCase):
    def project(self, source, output, *flags):
        return subprocess.run(
            [sys.executable, str(ROOT / "tools/prolog-star-kb"), "project",
             str(source), "-o", str(output), *flags],
            cwd=ROOT, capture_output=True, text=True,
        )

    def test_offline_collectors_create_nested_projection_and_repeat(self):
        for source_id in enabled_source_ids(REGISTRY):
            with self.subTest(source_id=source_id), tempfile.TemporaryDirectory() as td:
                result = run_source(REGISTRY, source_id, td, retrieved_at=RETRIEVED_AT)
                source = Path(result["normalized_path"])
                raw_before = Path(result["raw_path"]).read_bytes()
                normalized_before = source.read_bytes()
                output = Path(td) / source_id / "projection" / "nested" / "facts.pl"
                self.assertFalse(output.parent.exists())
                stdout = subprocess.run(
                    [sys.executable, str(ROOT / "tools/prolog-star-kb"), "project", str(source)],
                    cwd=ROOT, capture_output=True, text=True, check=True,
                ).stdout
                first = self.project(source, output)
                self.assertEqual(0, first.returncode, first.stderr)
                self.assertEqual(stdout, output.read_text())
                self.assertIn("star_doc(", stdout)
                self.assertIn("star_source(", stdout)
                self.assertIn(result["raw_sha256"], stdout)
                sibling = output.parent / "keep.txt"
                sibling.write_bytes(b"preserve existing content")
                second = self.project(source, output)
                self.assertEqual(0, second.returncode, second.stderr)
                self.assertEqual(stdout, output.read_text())
                self.assertEqual(b"preserve existing content", sibling.read_bytes())
                self.assertEqual(raw_before, Path(result["raw_path"]).read_bytes())
                self.assertEqual(normalized_before, source.read_bytes())

    def test_whole_json_and_forced_stream_create_parents_without_changing_facts(self):
        spec = next(s for s in load_registry(REGISTRY) if s.source_id == "fixture-atom")
        _, documents = RssAtomCollector().collect(spec, retrieved_at=RETRIEVED_AT)
        with tempfile.TemporaryDirectory() as td:
            source = Path(td) / "input.json"
            for streaming in (False, True):
                source.write_text(ndjson_bytes(documents).decode() if streaming else json.dumps(documents, indent=2))
                output = Path(td) / str(streaming) / "nested" / "facts.pl"
                result = self.project(source, output, *(["--stream"] if streaming else []))
                self.assertEqual(0, result.returncode, result.stderr)
                self.assertEqual(render_projection(documents), output.read_text())

    def test_invalid_parent_and_directory_output_fail_without_removing_files(self):
        with tempfile.TemporaryDirectory() as td:
            result = run_source(REGISTRY, "fixture-atom", td, retrieved_at=RETRIEVED_AT)
            blocker = Path(td) / "blocker"
            blocker.write_bytes(b"keep")
            for output in (blocker / "nested" / "facts.pl", Path(td)):
                run = self.project(result["normalized_path"], output)
                self.assertNotEqual(0, run.returncode)
                self.assertIn("cannot write projection", run.stderr)
                self.assertIn(str(output), run.stderr)
                self.assertNotIn("Traceback", run.stderr)
                self.assertEqual(b"keep", blocker.read_bytes())

    def test_permission_failure_is_not_swallowed(self):
        # Inject OS errors so this regression also runs under privileged CI users.
        from prolog_star_kb.cli import main
        with tempfile.TemporaryDirectory() as td:
            result = run_source(REGISTRY, "fixture-atom", td, retrieved_at=RETRIEVED_AT)
            output = Path(td) / "facts.pl"
            source = Path(result["normalized_path"])
            source.write_text(json.dumps(json.loads(source.read_text()), indent=2))
            output.write_bytes(b"keep")
            for operation in ("mkdir", "open"):
                with patch.object(Path, operation, side_effect=PermissionError(13, "Permission denied", str(output))):
                    with self.assertRaisesRegex(SystemExit, "cannot write projection.*Permission denied"):
                        main(["project", result["normalized_path"], "-o", str(output)])
                self.assertEqual(b"keep", output.read_bytes())


class WorkflowStaticTests(unittest.TestCase):
    def test_collect_workflow_is_statically_valid(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "collect.yml").read_text(encoding="utf-8")
        try:
            import yaml
        except ImportError:
            for marker in ("workflow_dispatch:", "schedule:", "fromJSON(needs.prepare.outputs.ids)", "upload-artifact"):
                self.assertIn(marker, workflow)
            return
        data = yaml.safe_load(workflow)
        triggers = data[True] if True in data else data.get("on")
        self.assertIn("workflow_dispatch", triggers)
        self.assertIn("schedule", triggers)
        jobs = data["jobs"]
        self.assertIn("prepare", jobs)
        collect = jobs["collect"]
        self.assertEqual("prepare", collect["needs"])
        self.assertIn("source_id", collect["strategy"]["matrix"])
        actions = [step.get("uses", "") for step in collect["steps"] if "uses" in step]
        self.assertTrue(any("upload-artifact" in use for use in actions))

    def test_workflow_never_commits_scraped_data(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "collect.yml").read_text(encoding="utf-8")
        self.assertNotIn("git commit", workflow)
        self.assertNotIn("git push", workflow)
        self.assertIn("runner.temp", workflow)


def canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


if __name__ == "__main__":
    unittest.main()
