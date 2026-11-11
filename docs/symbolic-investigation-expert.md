# Symbolic investigation expert (candidate-only)

This module is an **opt-in local SWI-Prolog expert**, not an enabled actor, a
network service operation, a new manifest, or a replacement for the shared
Star-KB service. It is under issue [#29](https://github.com/starintel-labs/star-kb/issues/29);
the public actor roster lives in
[starintel-actors #4](https://github.com/starintel-labs/starintel-actors/issues/4).
RAGE research/design review is pending; do not activate it as a trusted public
ABI or assume the 0.10.1 end-to-end adapter has been verified by this slice.

## One reusable owner

Source: `kb/public/starintel/symbolic_investigation.pl`, module
`starintel_symbolic_investigation`. It imports the already-maintained
`star_json` projection and `star_reasoning` semantic tools; it does not
create another ontology, storage owner, client, scheduler, or Prolog listener.

- `ownership_path_candidates(+Entity,+Limit,-Results)`: two **directed,
  positive, explicitly typed** `owns` relations produce bounded candidate
  ownership-path proofs with both source Relation IDs. They do not establish
  control, beneficial ownership, or identity.
- `contradiction_cases(+Entity,+Limit,-Results)`: bounded reuse of existing
  contradiction detection. Opposite claims require temporal/source review.
- `evidence_coverage(+RelationId,-Status)`: `linked` means at least one
  evidence record references an indexed source for that relation, **not**
  independent corroboration or factual verification.
- `frontier_question(+Subject,+Predicate,+Object,-Question)`: asks about
  missing *direct* support, does not schedule a target; generic
  `related-to` predicates are rejected.
- `expert_revision(-Revision)`: local expert rule revision, currently 1.

`results(Items, Truncated)` returns at most 64 items per call. `Truncated=true`
means more raw matches exist; the capped prefix is sorted but not globally ranked
or exhaustive. Use the authenticated existing shared KB service's scope, limits,
generation pins and actor/admission policies for real deployments. This module's
direct local exports are **not** automatically exposed remotely.

## Evidence and write boundaries

Derived `candidate(ownership_chain,Source,Target,proof(RelationIds),Status)`
is *not* a canonical StarIntel document or relation. Status is one of
`needs_evidence`, `disputed`, or `review_required`, never `verified`.
No mutations or ingestion occur here. KnowledgeFoundry must eventually use
the existing candidate -> validation -> provenance -> review -> canonical
write/readback path through the server-owned 0.10.1 contract, not write to
the `star_json` projection. Neither this expert nor the public actor inventory
implements that bridge.

## Validation

```sh
python3 -m unittest tests.test_symbolic_investigation_expert -v
# With SWI-Prolog installed: synthetic source/evidence, conflict and bound tests run.
python3 scripts/sync.py --check
python3 scripts/validate-docs.py
```

Later work depends on review of [Star-KB #6](https://github.com/starintel-labs/star-kb/issues/6),
[Star-KB #15](https://github.com/starintel-labs/star-kb/issues/15),
and proactive goals [PR #27](https://github.com/starintel-labs/star-kb/pull/27).
Do not duplicate existing rule mining, hypothesis promotion, or target scheduling.
