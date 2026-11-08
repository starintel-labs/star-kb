# Continuous symbolic learning (learning/1)

## Implemented boundary

This is an experimental local, bounded learning controller over **canonical 0.10.1 JSON**.
The generated StarLang schema and `core.star` are hash-pinned using the existing
`star_kb_service.projection` contract. The old 0.9 `_id/data` projector remains
unchanged. Legacy inputs are rejected by this controller, not relabelled.

The SQLite store is a replay cache of admitted canonical bytes plus a typed
learning journal, not an independently editable world-truth database. Source
JSON and historical revisions are retained. Results export as canonical
`Document.extensions.starKbLearning` with scope and source dependencies. A
production write must use the existing authorized server persistence boundary;
the new CLI makes no network writes. No bulk production ingestion is performed.

## Modules and interfaces

- `contracts`: pinned validator, scope and hard maximum limits.
- `store`: canonical replay, indexed scalar observations, immutable typed records,
  dependencies, invalidation, atomic message claim, leases, retries, cancellation.
- `discovery`: bounded field-equivalence synthesis, externally labelled held-out
  evaluation, finite-sample association records and a restricted frozen-model
  proposal interface. No model API call is made automatically.
- `planning`: query local records first, capability matching and evidence requests.
- `runtime`: ingestion-driven event pump, reusable without another user question.
- `selectors`: closed AST compiler to `kb/core/star_learning.pl`; SWI execution,
  stack/inference/time/result bounds, cancellable subprocess, source explanations.

Responsibilities share a process. They are not fictional deployed actor services.
No actor dispatch API is guessed: `ReplayCollector` is explicitly fixture-only.
A real collector must separately consume supported requests and return canonical
provenanced documents. Registry entries state access, cost, coverage and inputs.
Planning does not authorize access or execute collection.

## Evidence model

A directly recorded JSON scalar is an observation with document ID, JSON Pointer,
value, canonical content hash and source metadata. Canonical claim documents
remain *attributed statements*, never observations of their proposition's truth.
Hypotheses, associations, definitions, conclusions, selectors, evidence requests
and experiments are typed journal records. Definitions carry operational meaning,
method/version, discovery examples, limits, and evaluation dependencies. Claims
can conflict without being overwritten. Retractions are canonical tombstones;
changed evidence invalidates dependent accepted predicates and cached conclusions.

The `starKbScope` namespaced extension binds project and operation; dataset is
canonical. Each local query, queue item and record is partitioned by all three.
Cross-scope reuse is not implemented and is denied by default.

## Selectors

A versioned immutable selector has name, purpose, subject type, parameters, scope,
expression and execution bounds. Expressions use `all`, `any`, `exclude`, `field`,
`same`, `attributed`, `during`, `count`, `reachable`, and `learned`.

`field: ["/regionCode", "OH"]` establishes recorded metadata only. Do not use it
as author residence. Paths for document geotags, mentions, event locations and
organization addresses are distinct. `during` requires a numeric recorded time
field; collection time is not publication time. `attributed` takes claimant,
predicate and object and reports explicitly positive/negative claim polarity.
A reported or quoted threat is not automatically advocacy. Use separately
attributed predicates in canonical claims. There is no text-to-extremism detector.

Conjunction/alternatives combine four-valued support/refutation. Absence remains
unresolved, not false. `exclude` swaps explicit support/refutation, not Prolog
negation-as-failure. `reachable` follows only directed canonical relation records
with evidence IDs and depth <=8. It establishes a documented path, not independent
verification of every asserted relation. `count` counts supported unconflicted
members of the bounded sample only. Budget/engine failures are separate execution
statuses with no membership decisions. No arbitrary goals or source code execute.

Example selector JSON:

```json
{"name":"recorded-language","version":1,"purpose":"Find recorded English metadata",
 "subject_type":"document","expression":{"field":["/language","en"]}}
```

## Learning and recursion

The initial synthesis grammar creates reusable binary equality concepts over
observed scalar paths, discovered from repeated values and contrasting examples.
These are deterministic **operational definitions**, not universal empirical rules.
Default continuous discovery considers `extensions.features`, language and region
fields. Explicit path lists support other domains. Generation is bounded by
candidate, document, observation and time limits. Existing shape mining is
preserved; this learner uses the same lossless scalar walker and indexed shapes.

Evaluation requires distinct held-out document pairs, both label classes,
independently supplied label sources, disjoint discovery/evaluation source groups,
zero observed errors and improvement over a declared constant baseline.
Source-group labels must be supplied externally; distinct account names are not
proof of independence. The controller records confusion counts and sample
accuracy, never an invented confidence value. The acceptance threshold is an
initial conservative policy, not a general theorem about intelligence.

An accepted definition is usable through `learned:[id,version,anchorDocument]`.
The demonstration compares source-diversified later retrieval with chronological
retrieval at the same document budget; it measures unique independently labelled
evidence groups and redundancy. The learned concept changes the actual chosen
records. A second language-convention example demonstrates the same synthesis
mechanism, without inferring nationality. Experiments include rejected candidates.

## Run

Install `service/requirements.txt` and SWI-Prolog. Obtain the pinned StarLang
checkout as in `.github/workflows/shared-service.yml`.

```sh
export STARLANG_ROOT=/path/to/pinned/star-lang
python3 -m star_kb_learning --starlang "$STARLANG_ROOT" --store /tmp/learn.sqlite \
  --project demo --operation replay --dataset demo replay documents.ndjson
python3 -m star_kb_learning --starlang "$STARLANG_ROOT" --store /tmp/learn.sqlite \
  --project demo --operation replay --dataset demo select selector.json
python3 scripts/learning-demo.py --starlang "$STARLANG_ROOT" --output /tmp/learning-results.json
python3 scripts/verify-learning.py --starlang "$STARLANG_ROOT"
```

The mandatory learning gate fails when schema or SWI is missing. Optional existing
service tests still report their real dependency skips. The standalone learning
gate is not a substitute for full HTTP/ZMQ/SBCL integration tests.

## Known limits and follow-up

No claim of ASI, general autonomous science, model-weight updates, calibrated
population inference, live collector connectivity or proven intelligence gain.
Initial invention is a small synthesis grammar, not unrestricted ILP. Cadence is
sample interval estimation, not an automation classifier. Full phrase clustering,
regional causal inference, geographic boundary acquisition and general hypothesis
program synthesis remain extension work. The frozen-model adapter accepts bounded
proposals but has no configured provider. Records are bounded per scope; long-term
journal retention and production change-feed integration need operator policy.
Review source-independence labels and annotation procedures before real research.

## Continuous replay supervisor

`follow INPUT.ndjson` tails complete newline-committed records, checkpoints byte
offsets durably, discovers on incoming changes and continues until stopped.
`--polls N` makes a bounded replay test. Replacement or truncation restarts the
stream with idempotent canonical admission. Unchanged generations skip synthesis.
The producer must emit supported 0.10.1 records, not legacy collector envelopes.

The existing `collectors` registry currently emits the older candidate format,
so it is not silently wired into this controller. The existing LLM harness owns
provider budgets and execution/review, whereas this slice only accepts restricted
proposal JSON from it; a configured adapter can call `Discovery.propose`. This
distinction prevents an optional provider or collector from gaining new access
merely because learning is installed.

The synthetic report also exercises recorded-region/creation-time selectors,
publication-style interval records (using explicitly named `createdAt`), and
self-examination of unresolved hypotheses and rejected concepts. A source label
is a canonical annotation document, not an untraceable numeric score.

## One-day public Reddit export test

The execution environment could not retrieve `ingest.starintel.actor` (403 on
health, capabilities and public search); no live-data result is claimed. If the
operator supplies a **public-only canonical 0.10.1 Reddit NDJSON export** for a
UTC day, the following local command validates schema, preserves original content
digests, applies an explicit local replay-scope adapter, filters `/createdAt`,
discovers candidates and executes a real bounded SWI day selector:

```sh
python3 scripts/test-public-day.py public-reddit-day.ndjson \
  --starlang "$STARLANG_ROOT" --dataset reddit --day 2026-11-07 \
  --output /tmp/public-reddit-day-results.json
```

Maximum admitted sample: 256 documents, 64 KiB/document, 2048 scanned export
records. No credentials or private records are needed. This is an export replay,
not direct live collection. It does not accept learned predicates without
independent held-out evaluation labels, and does not label accounts as bots or
extremists from metadata. `/createdAt` is the explicitly chosen recorded creation
field, not collection time or a promise about every source's publication semantics.
