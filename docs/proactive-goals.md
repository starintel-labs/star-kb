# Bounded proactive goals (interface 1)

This is a minimal goal engine, not the full proactive roadmap or operator
terminal. It composes the existing scoped StarLang SWI relation query and the
StarIntel 0.10.1 server bridges. An authenticated caller explicitly submits a
bounded set of newly available canonical relation IDs and advances the goal.
There is no automatic changefeed, background investigation, model promotion,
unrestricted Prolog evaluation, or deployment.

## Contract

The library is `star_kb_service.proactive.ProactiveEngine(store, engine_factory,
actors)`. The store is `ServerDocuments` configured for `relation`, `task`, `claim`
and `target`; `engine_factory(snapshot)` returns the existing `Engine`.

- `capabilities()` lists the interface, rules, actor allowlist and bounds.
- `submit(goal_id, source_ids, capability, actor=None)` persists an immutable
  canonical Task plan, pinning the source generation and versioned rule digest.
- `status(goal_id)` reads canonical plan, attempt records and results.
- `advance(goal_id)` executes one bounded attempt. A fresh process resumes with
  only the same scoped credentials and goal ID. Repeated completed calls do no
  work. Different content at an existing goal identity is a replay conflict.

Both existing HTTP and ZMQ listeners expose this library when the operator adds
`"proactive": {"actors": {"fixture-corroborator": ["corroborate-subsidiary/1"]}}`
to a server-backed scope. File-only scopes cannot enable durable goals. Existing
credential authentication supplies tenant/dataset; requests cannot select them.
The actor map is operator configuration, not a discovered live registry.

The existing `star.kb.service/1` request envelope supports:

```json
{"protocol":"star.kb.service/1","id":"submit-1","operation":"goal-submit","parameters":{"goal":"fixture-chain","sources":["relation-ab","relation-bc"],"capability":"subsidiary-chain/1"}}
```

`goal-status` and `goal-advance` take `parameters: {"goal":"fixture-chain"}`.
`actor-capabilities` takes no parameters. Use a fresh request `id` for subsequent
status/advance calls: the existing per-session replay cache returns the original
reply for repeated request IDs. Responses contain `state`, `goal`, `plan`,
`attempts`, `results` (canonical IDs), `unresolvedEffects` (logical IDs with an
unknown/no acknowledged effect), and nullable `error`. Fetch result documents
through the existing authorized canonical server read interface.

## Named rule and trust boundary

`subsidiary-chain/1` joins two directed `subsidiary-of` relations whose source,
intermediate and destination references all have schema `organization`. The
middle identity must match. Cycles ending at the starting organization produce
nothing. Each distinct pair of supporting relations yields one candidate Claim
with predicate `indirect-subsidiary-of`, explicit unknown truth status, exact
source IDs/revisions/digests, endpoint schema identities, original evidence,
provenance, projection generation, rule digest and adapter execution identity.
This is a new two-edge conclusion, not a relabeled neighboring source row.
SWI supplies the bounded named relation query; Python composes the typed join.

`corroborate-subsidiary/1` uses the same proof to request corroboration by an
explicitly allowlisted actor. It submits through `POST /api/v1/targets`, reusing
the server's durable idempotency/acceptance/scheduling owner. The server owns the
canonical Target ID and execution state. Complete derivation provenance and
source references travel in canonical `options.starKbDerivation` and survive
authorized readback. Plain document creation is not used to fake queueing.
Actor execution and completion are separate from goal effect acceptance.

Inputs are directed, undeleted, revisioned 0.10.1 Relations from the authorized
scope. Missing verification status or `unverified`, `verified`, `confirmed` is
accepted as source evidence; this does not make the conclusion verified.
Other verification statuses, derived/generated provenance, or provenance trust
outside absent/source/verified/trusted are rejected. Thus rejected or candidate
knowledge cannot silently recursively enter the source rule. The existing
lossless JSON Pointer projection preserves additive fields.

## Bounds, persistence and uncertain outcomes

At most 16 source records, 16 proof results, depth 2, 16 named SWI queries and
three durable attempts are allowed per goal. Work shares a five-second deadline;
owned worker cleanup uses existing bounded supervision. Oversized conclusions
are rejected before effect publication. Every source is reread before effects;
revision changes fail `stale_generation`. This is optimistic snapshot checking,
not an atomic cross-document transaction.

Plans, attempt reservations, planned effects, receipts and completion are
canonical Task records persisted through the server's conditional-create and
readback protocol. There is no local truth database, scheduler or replacement
Target queue. A crash consumes its reserved attempt. Retrying repeats stable
conditional writes and Target idempotency keys. Concurrent callers can repeat
computation but cannot create different content at the same derivation identity.
Deduplication identity intentionally includes the full scoped source generation,
proof, rule and adapter identity; changed inputs/builds are new derivations.

A batch is not atomic: some effects may be committed before a later failure.
Status reports observed result IDs and unresolved logical effects even without a
completion record. Claim readback resolves lost acknowledgment; Target receipts
are retained after acknowledged acceptance. The final attempt can fail after
commit, leaving an unresolved Target acceptance. `retry_exhausted` does not imply
rollback. An explicit new goal with identical sources/capability/actor safely
replays the same effect keys to reconcile, subject to server policy and unchanged
source generation. No automatic unbounded retry occurs. This limitation is part
of the interface, not an exactly-once execution claim.

## Verification

`tests/test_proactive.py` covers policy with generated schemas, then actual SWI
inference against an isolated HTTP protocol fixture. Such fixture tests do not
prove real server storage/scheduling. `service/readback_integration.py` adds a
real server/CouchDB/SWI claim test with commit-before-ack fault injection and
server/store restart. Run evidence and any infrastructure blockers belong in the
PR; neither mocks nor skipped integration are reported as real durability proof.
