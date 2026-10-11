# Canonical persistence/readback: issue #15 next slice

Status: started after reviewed service PR #21 landed at
`d4afcac5b6bd51bb9f4d9652b790fe61faa90d9e`. This document defines the next bounded
integration; it does not claim durable writes, domain inference or actor completion.
Implementation owner for server mutations remains lost-rob0t/starintel-server#290
under #279. No competing database, outbox, event queue or scheduler is introduced.

## Verified starting boundary

The service merge retains StarLang adapter pin
`bfba04a15f43215cd323e1dde2d98a7343abb3ea` and generated canonical 0.10.1 source
`96d26c338078676ec1dc6928755901636c9f7ef0`. Post-merge service CI ran 118 tests
with SBCL 2.6.7/SWI 10.0.2. Canonical schema and core hashes remain those in
`star_kb_service/projection.py`; no generated artifacts or schema versions change.

Server source inspected at `491b89dad31e15b4172d52a03ab525b6c92fd637`.
Its schema-lock preflight verified canonical 0.10.1 at the same source commit.
Before server edits, repeat its AGENTS.md preflight against the then-current head
and inspect active branches so this integration cannot overwrite another owner.

## First bounded persistence proof

Use one synthetic, revisioned source relation and at most one candidate Claim.
The trusted caller reads source records through authorized server access, builds
an immutable scoped KB snapshot, executes the bounded named query and validates
the candidate against the pinned generated Claim schema before any write.
Persistence uses authorized PUT `/api/v1/documents/:id`; readback uses authorized
GET through the same principal. Never pass raw CouchDB credentials to Star-KB.

A snapshot-relation observation can test the serialization boundary, but is not
a newly inferred ownership relation and does not satisfy the domain-inference
acceptance gate. Select and version the actual named domain rule before claiming
the complete vertical proof. The current fixed relation-neighbors adapter cannot
be relabeled as such a rule or extended via caller-supplied Prolog source.

Candidate identity covers trusted tenant/dataset, rule version and digest,
projection generation, normalized conclusion, source document IDs/revisions and
full document digests. Store original source/evidence references and endpoint
schema identities; keep candidate/unreviewed status explicit. Query execution
cannot promote truth. Do not invent a canonical revision before persistence.
Canonical authorized extensions must survive readback unchanged.

## Existing server seams and required guard

- `source/frontends/http-contract-routes.lisp` and
  `http-authorization-routes.lisp` own the document HTTP entrypoints.
- `source/authorization/services.lisp` owns authenticated read/write decisions.
- `source/databases/document-update.lisp` owns validated merging upsert, duplicate
  detection and bounded CouchDB conflict retries.
- `source/document-access.lisp` owns canonical wire serialization and revision
  mapping; server tenant and `_id`/`_rev` envelope fields are not canonical payload.
- `source/init.lisp` and `runtime-lifecycle.lisp` own startup and rollback.

The current upsert merges into an existing document before duplicate comparison.
A deterministic candidate ID therefore does not prove immutable idempotency.
The next executable change must reuse the server update owner with a bounded
compare-or-create policy: identical derivation returns the stored candidate;
conflicting derivation at the same identity fails without silently merging it.
A client GET followed by PUT cannot enforce that policy under a race.

Source revision checks and candidate writes are not a cross-document transaction.
Pin the observed revision, reject a detected stale source before commit, and
report snapshot-based derivation without claiming atomic current-truth fencing.
After an uncertain post-commit timeout, resolve by authorized readback/replay;
do not promise rollback or exactly-once transport delivery.

## Acceptance evidence, still outstanding

1. Actual generated boundary, real named SWI query, candidate validation, real
   server/CouchDB write and authorized readback, comparing all evidence fields.
2. Server/service restart retains one candidate and its derivation identity.
3. Commit-before-ack crash and identical replay return one logical candidate.
4. Conflicting derivation is rejected even with concurrent same-ID writes.
5. Foreign tenant read/write denied; same source IDs across scopes stay isolated.
6. Stale revision/generation, exhausted conflicts and proof/byte/deadline limits
   return typed outcomes without partial candidate publication.
7. Known pre-commit cancellation writes nothing; uncertain post-commit outcomes
   resolve through readback. In-flight cancellation remains a separate gate.
8. No-answer writes nothing; canonical extension data survives persistence.
9. Failed dependency startup propagates readiness and reaps owned resources.

Mocks may test policy but cannot satisfy durable readback acceptance. No server
or canonical store has been deployed or mutated by this preparation.

Proactive changefeed experts, typed Targets/actions, feedback and bounded dedup
remain the following milestone using #8 and the existing server owners. RabbitMQ
remains queued after HTTP/ZMQ. Remote hardening, cluster storage and NLP scales
remain separate; the trusted-loopback multipart memory limitation still applies.

## Implemented source readback slice (PR #23)

The operator client `python -m star_kb_service.persistence config.json source.json`
validates a generated 0.10.1 directed Relation, checks the server create-only
capability, performs conditional creation and compares full authorized readback
against the original body (excluding only server-owned `rev`). Its closed config
contains `tenant`, `dataset`, loopback `url`, and `tokenEnv`; only the environment
holds the credential. It rejects caller-supplied revisions. Star-KB has no direct
CouchDB credentials or independent durable store.

A service scope can specify `server: {url, tokenEnv, documentIds}` instead of
`documents`. Startup reads at most 256 named documents within five seconds and
the aggregate byte budget, then constructs the existing immutable typed snapshot.
A failed later scope cleans up earlier workers. No live changefeed is claimed.

`service/readback_integration.py` owns disposable synthetic Docker resources and
uses actual server/CouchDB, generated contracts and SBCL/SWI. On 2026-10-11,
baseline server 491b89d failed one real conditional-write test (200 instead of 412).
The server #290 worktree passed all five integration tests in 28.800 seconds:
conditional creation, unchanged replay, conflict/race rejection, foreign-scope
denial, complete extension preservation, restart/readback and a fresh query
retaining evidence and stored revision. Six client boundary/cleanup tests pass; the final full regression run passed
124 tests in 47.982 seconds. A repeat durable run passed five tests in 28.669
seconds after using the named CouchDB node already configured in server CI.
Prolog smoke, sync, documentation validation and workflow lint also passed.

The local Docker proof uses the pinned image in the harness with an explicit
source overlay. VFS required a filesystem-equivalent flattened local image via
`STAR_KB_TEST_SERVER_IMAGE`; details are recorded in the server evidence document.
Native exact-head server build/CI is still required. This proves a durable source
Relation, not a newly inferred Claim. Crash after commit but before acknowledgment,
source-freshness fencing, actual domain inference, proactive experts, and RabbitMQ
actor transport remain open acceptance milestones above.
