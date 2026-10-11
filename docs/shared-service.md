# Shared KB service: issue #15, first executable slice

Status: implementation draft; **not production-ready or a durable/distributed KB**.
The user explicitly requested implementation on 2026-10-11. No deployment,
canonical promotion, learning-engine replacement or pending design-PR merge is
performed. This separate branch leaves PRs #5, #8, #16 and #19 untouched.

## Architecture decision and inspected ownership

HTTP `/v1/kb` and ZeroMQ REQ/REP use one authenticated dispatcher, immutable
scoped snapshot catalog, evidence envelope and real StarLang SWI adapter.
Python owns transport/authentication/admission; a private bounded JSON pipe
bridges to Common Lisp's `starlogicprotocol` methods. StarLang owns worker/MQI
safety and the fixed `relation-neighbors` query. No Prolog text, modules, paths,
raw goals, remote snapshots or rules can be supplied through either listener.
The shim does not copy private Pro Actors source or implement a second evaluator.

Inspected source pins:

| Owner | Exact source | Role and limitation |
|---|---|---|
| Star-KB main | `fa1ed67e1dae8d790b1e6cef1b06c034e5cfa1fc` | existing lossless walker, legacy semantic projector, named rules |
| StarLang merged #213 | `bfba04a15f43215cd323e1dde2d98a7343abb3ea` | reused adapter/protocol; bounded ephemeral `star_relation/4` |
| Canonical 0.10.1 | `96d26c338078676ec1dc6928755901636c9f7ef0` | `specs/starintel/0.10.1/core.star`; bytes equal at adapter pin |
| StarLang #117 | `5633854290ce8dd9acf8dd63db82bb58f6f60202` | backend-neutral catalog + Tek9/LMDB, still design-gated |
| StarLang #120 | `0d25596add24cd3b111d19a5b593bd93070f0a33` | durable index catalog and `star_relation/4` exporter; whole-corpus/raw authored-rule path is not a bounded remote API |
| Tek9 #57 | `442448380fec11566272cb5377dd634eb18d3db3` | bounded forward inference, expert manifests and provenance; not full Prolog or replicated storage |
| Star-KB #8 | `a63d888a2eb1fdaea9cb3915d7df10ca3db17477` | reuse candidate/event replay and admission; do not fork it |
| Star-KB #5 | `bafc2171f956615f446bbc8f789a3019f8f01d69` | explicit-link evidence maturity; separate open work |
| Star-KB #16 | `7aa246a6279d000332216a0fa9aec3861832dc32` | proposed policy design; not merged here |
| Star-KB #19 | `fb8d938309b90610eaaea0674df8b35bc2d2938c` | pending learning research; no implementation claim |

The service verifies the exact clean StarLang checkout, generated schema hash
`1d677d679adcf64ed2df6e9e98eff47c3f66f67f10efbe4c4b69f7fffa2def0a`
and core source hash
`e09205e71fcd0f2bf87ff7d104c5ccff298d0054764def81e00f81a6bd8639fc`.
The adapter itself verifies the executable and bootstrap hashes. Results expose
the latter two identities plus projection generation, source revision and digest.

## Explicit canonical/legacy boundary

`prolog_star_kb/projection.py` still has 0.9.x defaults, `_id`, snake_case,
nested `data`, and a nine-field semantic relation. It is **not** silently relabeled
0.10.1. This service validates flat `id`, `schemaVersion`, `source`, `destination`,
`predicate` against StarLang's generated `Relation` schema. It narrows admission
to directed, nondeleted relations with a nonempty `rev` and the configured dataset.
Endpoint schema references, sources, evidence, provenance and all extensions stay
in owned canonical bytes; the existing JSON-Pointer walker covers every field.
The four-field SWI row is only a disposable query index. The entire canonical
snapshot, scope and schema digest determine generation, including evidence-only
changes. A result reports source relations, `derived: false`; it does not invent
an inference proof or promote a generic `related-to` assertion.

Unknown top-level fields rejected by the generated schema are rejected, never
silently dropped. Schema-authorized `extensions` and arbitrary provenance remain
lossless. Broader canonical dtype projection and existing Star-KB rule-package
integration require separately tested semantic mappings.

## Run locally

Install Python dependencies from `service/requirements.txt`. Prefer the pinned
StarLang `nix develop` environment for SBCL, SWI/MQI and Lisp dependencies.
Debian SBCL 2.5.2/SWI 9.2.9 with bordeaux-threads, babel, ironclad, usocket and
yason was tested locally. Ubuntu stock SBCL 2.2.9/SWI 9.0.4 failed adapter startup
in the first CI attempt and is not a supported configuration for this slice.
Check out the exact adapter commit in a separate clean directory.

Set `STARLANG_ROOT`, `STAR_KB_SBCL`, `STARLANG_SWI_EXECUTABLE` to operator-owned
absolute paths. Set any required ASDF `CL_SOURCE_REGISTRY` and writable cache.
Store tokens only in environment/keyring references. Example operator config:

```json
{"scopes":[{"tenant":"tenant-a","dataset":"example","documents":[]}],
 "credentials":[{"identity":"client-a","tenant":"tenant-a","dataset":"example","tokenEnv":"KB_CLIENT_A_TOKEN"}]}
```

`KB_CLIENT_A_TOKEN` must contain at least 32 characters of independently generated
secret material. Do not put its value in this config, a command argument or Git.
Run `python3 -m star_kb_service config.json --http-port 8080`.
Both addresses are printed without credentials; SIGINT/SIGTERM closes workers.
The initial supervisor is Linux-only: it becomes a child subreaper and reaps only
its dedicated worker sessions, including adopted SWI children after SBCL
crashes. Normal EOF shutdown lets the adapter reap SWI itself.
Only loopback listeners are implemented. This is a deliberate safe default, not
proof of remote encrypted actor connectivity. Remote HTTP needs authenticated TLS;
remote ZMQ needs CURVE or an equivalently authenticated private channel before
non-loopback binding is added. No deployment was performed.

HTTP: POST JSON to `/v1/kb` with `Authorization: Bearer <environment secret>`.
ZMQ: one JSON frame `{"authorization":"<environment secret>","request":{...}}`.
Both use the same operation object and response bytes/semantics:

```json
{"protocol":"star.kb.service/1","id":"health-1","operation":"health"}
```

Use the authorized generation returned by health:

```json
{"protocol":"star.kb.service/1","id":"query-1","operation":"relation-neighbors",
 "generation":"sha256:<health generation>",
 "parameters":{"node":"entity-a","predicate":"owns","direction":"out"},
 "limits":{"answers":128,"output-bytes":65536},"timeoutMs":5000}
```

This returns `pending` and `handle`. `next`, `status`, `cancel` take `handle`
instead of query parameters. `next` executes actual SWI and publishes the bounded
complete result or a terminal failure. Missing matches yield `no-answer`.
Cancellation of pending work is terminal; late cancellation preserves completion.
**In-flight cancellation is not implemented:** concurrent calls return `busy` and
a deadline watchdog retires the entire worker process group. This differs from
pending cancellation and is tested separately with an actual SIGSTOP.

Scope comes exclusively from token identity and trusted catalog configuration.
A body cannot widen scope; foreign operation handles fail. Service admission is
serialized with nonblocking `busy` responses; HTTP and ZMQ each have one serving
thread. There is no unbounded thread or task pool. Hard limits: 16 scopes,
64 principals, 256 facts/snapshot, 512 characters/string, 131072 relation-string
bytes, 262144 wire/snapshot bytes, 128 answers, 65536 result bytes including
provenance, 32 operations and 128 request replay entries per principal/process.
No partial result is published on overflow. A worker is recycled before the
adapter's 128-operation budget is exhausted, with explicit trusted snapshot replay.

Request identity is `(principal,id)` plus a canonical request digest. An exact
replay returns the saved response; changed content conflicts. Terminal operation
results remain stable under new request IDs. Authentication is repeated even for
replays. Replay state is bounded process memory, **not durable idempotency**.
After budget exhaustion, clients must reconnect to a newly started service;
production retention/expiry and stable durable request records remain a milestone.

## Acceptance milestones that remain open

1. **Canonical durable result path:** implement an evidence-supported, specifically
   named derived relation/claim using the existing Star-KB rules, then validated
   capability-scoped persistence and readback through server #279/#290. Prove
   source revisions, conflict handling, corrections, restart and idempotency.
   Local snapshot recovery here is not durable canonical persistence.
2. **Proactive mixture of experts:** reuse #8 candidate/event admission, #19
   research, server #302 changefeed, #303 typed plans/Targets, #304 reconciliation,
   #305 budgets and existing leases/fencing. Events carry tenant/dataset,
   generation, source revision, causation and dedup identity. Experts emit typed
   Targets/actions or evidence-supported candidate relations. Independent feedback
   and admission precede promotion. Bound fanout, depth, elapsed/cost budget,
   repeated states and total actions; corrections/retractions invalidate dependent
   candidates. Test cycles, quiescence, retry dedup and denied effects. No second
   scheduler, candidate DB or learning engine is introduced by this service.
3. **Transport completion:** encrypted remote HTTP/ZMQ, actor-generated wire
   bindings, in-flight cancellation and asynchronous multiplexing. RabbitMQ stays
   queued after this HTTP/ZMQ slice, with duplicate/redelivery and deadline proofs.
4. **Distributed storage/cluster:** below; not delivered by RPC listeners.
5. **NLP classifications/scales:** separate later issue #20, no implementation.

## Distributed-capable ownership and acceptance design

Keep canonical transactional storage ownership in the existing server/Tek9 path.
Inspect and reuse Tek9 ingest/checkpoints and sync before choosing a mesh backend;
neither inspected #57 nor StarLang #117/#120 establishes replicated storage.
A new distributed database engine is out of scope.

Proposed stable node identity is an operator-provisioned UUID bound to authenticated
node credentials, never a hostname or ephemeral socket address. Placement is an
explicit versioned mapping `(tenant,dataset,partition)->owner,replicas,epoch`.
Load-balancers may route reads only to a node advertising the requested immutable
canonical checkpoint/projection generation and authorized scope. Generation and
placement epochs travel with requests; a stale replica returns a typed retryable
failure, not unmarked stale evidence. Session handles in this first slice are
node-local and require sticky routing; they are not advertised as cluster handles.

Proposed initial consistency is a single fenced canonical writer per partition
with optimistic revision checks; transactions and leases stay with their existing
owners. Read replicas consume the owning canonical changefeed/checkpoints and
rebuild projections. A mesh may distribute encrypted immutable snapshots/content
by digest, but cannot grant authorization, become an independent writer, or
silently merge contradictory canonical revisions. Replication acknowledgment,
quorum/failover policy and approved sync implementation remain design decisions.
Do not claim automatic failover until a lease/epoch mechanism prevents split
brain. During partitions, unavailable current-generation reads fail closed;
replicas do not autonomously promote themselves. Routing cannot substitute for
authorization. Global query aggregation needs explicit bounded fanout, per-shard
checkpoints and incomplete-result semantics before implementation.

Required cluster evidence: at least two real nodes, restart at each side of a
checkpoint, network partition/heal, duplicate/out-of-order event replay,
fenced-writer takeover, rejected stale epoch, tenant isolation across placement
changes, snapshot digest verification, and canonical/projection convergence
(including correction and deletion). Capture throughput/latency/memory with
workload and consistency mode. None of these is marked complete by this PR.

## Verification

Dedicated `shared-service` CI checks out the exact StarLang pin and installs real
SBCL/SWI from its existing flake lock. The integration suite must not skip in that lane. Ordinary legacy CI
may report explicit skips if its runtime is absent. `service/benchmark.py` records
a reproducible synthetic 32-relation baseline (20 direct, 5 HTTP, 5 ZMQ query/next pairs) with cold projection/worker
cost, warm latency/throughput, memory high-water marks and bounds. It measures fresh-client loopback RPC as well as direct dispatch; it is not a
remote transport or cluster benchmark. No optimization claim precedes measurement.
