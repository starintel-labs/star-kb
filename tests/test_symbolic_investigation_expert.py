"""Synthetic, read-only tests for the opt-in symbolic-investigation expert."""

from __future__ import annotations

import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXPERT = ROOT / "kb" / "public" / "starintel" / "symbolic_investigation.pl"
SWIPL = shutil.which("swipl")


def prolog(goal: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            SWIPL, "-q", "-s", str(EXPERT),
            "-g", f"(({goal}) -> halt(0) ; halt(1))",
            "-t", "halt(1)",
        ],
        capture_output=True,
        text=True,
        timeout=30,
        cwd=ROOT,
        check=False,
    )


def verify(test: unittest.TestCase, goal: str) -> None:
    result = prolog(goal)
    test.assertEqual(result.returncode, 0, result.stdout + "\n" + result.stderr)
    test.assertNotIn("Warning", result.stderr)
    test.assertNotIn("ERROR", result.stderr)


def relation(rid: str, subject: str, predicate: str, target: str, negated: str = "false") -> str:
    return (
        "assertz(star_json:star_relation("
        f"{rid},{subject},{predicate},{target},true,{negated},0.9,none,none))"
    )


def linked_evidence(rid: str, sid: str) -> str:
    return ",".join(
        [
            f"assertz(star_json:star_source({rid},{sid},public,locator,0.8,0.8,hash))",
            f"assertz(star_json:star_evidence({rid},e_{rid},{sid},supports,claim,0.8,observed))",
        ]
    )


class StaticContractTests(unittest.TestCase):
    def test_source_exists_and_is_not_an_ingest_or_actor_runtime(self) -> None:
        code = EXPERT.read_text(encoding="utf-8")
        self.assertIn(":- module(starintel_symbolic_investigation,", code)
        for forbidden in (
            "assertz(", "retract(", "open(", "shell(", "http_open(",
            "create_target(", "create_document(", "related-to,",
        ):
            self.assertNotIn(forbidden, code)


@unittest.skipUnless(SWIPL is not None, "SWI-Prolog is not installed")
class SymbolicInvestigationExpertTests(unittest.TestCase):
    def test_clean_load_and_invalid_bounds(self) -> None:
        verify(self, (
            "starintel_symbolic_investigation:expert_revision(1),"
            "\\+ starintel_symbolic_investigation:ownership_path_candidates(a,0,_),"
            "\\+ starintel_symbolic_investigation:ownership_path_candidates(a,65,_),"
            "\\+ starintel_symbolic_investigation:ownership_path_candidates(_,4,_)"
        ))

    def test_typed_two_edge_candidate_is_not_a_canonical_fact(self) -> None:
        verify(self, ",".join([
            relation("r1", "a", "owns", "b"),
            relation("r2", "b", "owns", "c"),
            "starintel_symbolic_investigation:ownership_path_candidates("
            "a,5,results([candidate(ownership_chain,a,c,proof([r1,r2]),needs_evidence)],false))",
            "\\+ star_json:star_relation(_,a,indirectly_owns,c,_,_,_,_,_)",
        ]))

    def test_source_linkage_never_auto_promotes_a_candidate(self) -> None:
        verify(self, ",".join([
            relation("r1", "a", "owns", "b"),
            relation("r2", "b", "owns", "c"),
            linked_evidence("r1", "src1"),
            linked_evidence("r2", "src2"),
            "starintel_symbolic_investigation:evidence_coverage(r1,linked)",
            "starintel_symbolic_investigation:ownership_path_candidates("
            "a,5,results([candidate(ownership_chain,a,c,proof([r1,r2]),review_required)],false))",
            "\\+ star_json:star_relation(_,a,indirectly_owns,c,_,_,_,_,_)",
        ]))

    def test_negation_is_flagged_for_review_without_erasing_history(self) -> None:
        verify(self, ",".join([
            relation("r1", "a", "owns", "b"),
            relation("r2", "b", "owns", "c"),
            relation("r3", "a", "owns", "b", "true"),
            "starintel_symbolic_investigation:ownership_path_candidates("
            "a,5,results([candidate(ownership_chain,a,c,proof([r1,r2]),disputed)],false))",
            "starintel_symbolic_investigation:contradiction_cases("
            "a,5,results(Cases,false))",
            "member(relation_conflict(r1,r3,a,owns,b),Cases)",
        ]))

    def test_bounded_truncation_and_cycle_exclusion(self) -> None:
        verify(self, ",".join([
            relation("r1", "a", "owns", "b"),
            relation("r2", "b", "owns", "c"),
            relation("r3", "a", "owns", "d"),
            relation("r4", "d", "owns", "e"),
            relation("r5", "c", "owns", "a"),
            "starintel_symbolic_investigation:ownership_path_candidates("
            "a,1,results(One,true))",
            "length(One,1)",
            "starintel_symbolic_investigation:ownership_path_candidates("
            "a,10,results(All,false))",
            "length(All,2)",
            "\\+ member(candidate(ownership_chain,a,a,_,_),All)",
        ]))

    def test_frontier_is_a_question_not_an_automatic_target(self) -> None:
        verify(self, ",".join([
            "starintel_symbolic_investigation:frontier_question("
            "a,works_for,org,question(a,works_for,org,missing_direct_support))",
            "\\+ starintel_symbolic_investigation:frontier_question(a,'related-to',org,_)",
            relation("r1", "a", "works_for", "org"),
            "\\+ starintel_symbolic_investigation:frontier_question(a,works_for,org,_)",
        ]))


if __name__ == "__main__":
    unittest.main()
