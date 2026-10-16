# Prolog operator terminal

This client implements an original workspace/module workflow inspired by sn0int:
inspect inputs, discover actors, explicitly select an actor, interpret a goal,
inspect a plan, submit, then inspect observed status and evidence. It contains no
sn0int source, actor implementation, collector, scheduler or canonical database.

Current boundary: **simulation only for submission**. The default adapter fails
closed. The engine belongs to PR #27 / task
`01a14593-c522-76c8-9ac8-7efa043950f2`; its collector/proactive files are not edited
here. A real engine adapter and isolated end-to-end execution remain an integration
gate after that interface lands. Simulated completion is never reported as actual
actor completion. Polling does not advance simulated state.

## Run

```sh
nix run .#star-kb-terminal -- --help
nix build .#star-kb-terminal
nix build .#checks.x86_64-linux.terminal
```

From source, install Python jsonschema and SWI-Prolog; point `STARLANG_ROOT` at the
pinned StarLang producer source (`4c8d1e19249a5030b7d97f953f694c09849b9131`).
The installed Nix wrapper supplies both the canonical bundle and SWI. The exact
schema hash is reused from the existing service projection contract. It is not a
new schema copy or a relabeled 0.9 projection. `STAR_KB_TERMINAL_SWIPL` can select
an explicit local SWI executable for source tests.

Use an isolated temporary directory for the following synthetic session:

```sh
work=$(mktemp -d)
export STARLANG_ROOT=/path/to/pinned/star-lang
python3 -m star_kb_terminal.cli \
  --workspace "$work/workspace.json" \
  --registry tests/fixtures/terminal/registry.json \
  --adapter simulated --simulation-state "$work/test-engine.json" shell
```

```text
actors --type domain
input example.invalid
input 192.0.2.1
select fixture-domain
investigate example.invalid
plan
submit
status REQUEST_ID_FROM_SUBMIT
results REQUEST_ID_FROM_SUBMIT
simulate REQUEST_ID_FROM_SUBMIT running
simulate REQUEST_ID_FROM_SUBMIT completed
results REQUEST_ID_FROM_SUBMIT
quit
```

Every command also works noninteractively with the same global flags, and emits
JSON. Quoted shell inputs use ordinary shell quoting. `help`, EOF and `quit` work
inside the REPL. No command evaluates input as Prolog or invokes an LLM.

`workspace` prints the imported document snapshots, selected actor, goal, inspected
plan and last observed submissions. Use separate `--workspace` paths to switch
investigations. `load FILE` imports a bounded JSON object/array or NDJSON file for
read-only inspection; `inputs` lists interpreted inputs and imported IDs. Canonical
unknown/additive data inside `extensions` is preserved. Conflicting IDs, deleted
records, legacy `_id`/`data` envelopes and invalid documents are rejected.

## Input and registry semantics

URL (HTTP/S, without credentials), DNS domain, IPv4/IPv6 and prefixed/loaded
canonical IDs have deterministic lexical interpretations. Nothing resolves DNS or
fetches a URL. A loaded document whose ID is also a domain or URL produces multiple
candidates; choose `--type domain`, `url`, `ip`, or `document-id`. Unknown text stays
unresolved. Generic canonical IDs can be selected explicitly. A supplied document
type uses `--type document:person` (or any document type in the pinned schema) and
requires a schema-shaped document ID; it is an operator declaration, not validation
of an unloaded document. IPs have no guessed canonical dtype: load a canonical
record or supply its type. The engine must resolve and validate references before
execution.

`--registry` consumes a canonical 0.10.1 ActorManifest JSON/NDJSON export from the
existing registry. `actor`, `accepts`, `produces`, capabilities and target options
come from those documents. There is no built-in actor list. The fixture actors are
synthetic and explicitly test-only. Registry identity and every manifest content
hash are bound into a plan; a changed registry requires a new inspection before
submission. Missing `accepts` means unknown, and no implicit wildcard exists.
Declared compatibility does not prove credentials, availability or authorization.

Inspection sources: `starintel-pro-actors` at `6095fac`,
`python/src/starintel_pro_actors/manifest_core.py` and `manifests.py`, plus
`starintel-server/source/actor-registry.lisp`. Upstream currently emits mixed
legacy and canonical manifests; export canonical records or use upstream migration.
The terminal does not silently convert them. Some target consumers declare only
`target`, not domain/URL; this client does not invent an actor-specific target
converter. The engine owns that conversion and any canonical Target creation.

The Prolog module takes bounded JSON data and derives compatibility. Planning is
one explicitly selected actor step, with declared expected output types; outputs
are **not promised evidence**. It neither automatically selects actors nor chains
unobserved outputs into new work. Recursive/proactive planning belongs to the
engine. A blocked plan gives its reason and is not submittable.

## Structured goal and engine port

`investigate X` creates `star.kb.goal/1` with `intent=investigate`, interpreted
`subject`, `limits={maxSteps:1}` and `requestedText`. `goal FILE` accepts the same
closed structure and reinterprets its subject before use. This is the optional
natural-language/model boundary: a future model may propose this object, but may
not supply Prolog, actor code, budget increases or hidden effects. CI and all
current commands use deterministic parsing only.

`star_kb_terminal.adapter.Engine` is the **proposed local Python client port**, not
a new public wire protocol or a competing engine. The eventual adapter translates
into the engine owner's supported operations:

- `submit(plan, request_id)`: idempotently accept or reject the inspected goal/plan.
  Same ID + different plan must conflict. A lost reply must be recoverable by ID.
- `status(request_id)`: return observed progress and stopping state, including
  `accepted`, `running`, `completed`, `failed`, `cancelled`, `blocked`, or
  `budget_exhausted`. Unknown or unreachable is an error, never completion.
- `results(request_id)`: return the same state envelope plus actual canonical
  0.10.1 documents and evidence references. Empty results stay empty.

Replies identify `requestId`, stable `engine`, `simulated` (boolean), `state`,
`stopped` (boolean), `reason` (required for stopped states),
`progress={completed,total}`, `evidence` (array), and optional `documents` (array).
`completed` means the engine's explicit terminal state, not a submitted target,
queue ACK or the existence of an operation ID. The real adapter must expose engine
provenance and canonical result revisions; this client validates documents again.
Authentication, canonical writes, policy, budgets, cancellation and dispatch stay
with the engine and its transport. No new remote listener is introduced.

The workspace writes its request ID atomically **before** adapter I/O, and retains
`submission_unknown` on lost response. Retrying the same inspected plan uses the
same ID. Restart/reconnect uses explicit `status`/`results`; the last observed state
is labeled as cached workspace data. Workspace path is part of client identity;
keep the path stable across restarts. Moving a workspace is not a supported replay
operation. Local files use atomic replace/fsync and an advisory lock, with new
files mode 0600. Concurrent clients serialize per workspace.

## Verification and integration gate

```sh
python3 -m unittest discover -s tests -p test_terminal.py -v
swipl -q -s tests/terminal_tests.pl -g run_tests -t halt
```

Tests use the actual Prolog planner, synthetic canonical inputs, CLI subprocesses,
and an explicitly simulated adapter. They cover ambiguous inputs, unknown
compatibility, stale plans, missing engine, acceptance versus completion, lost
reply/retry, restart, engine state loss, immutable terminal states, canonical
validation and evidence emptiness. The Nix check runs the installed command from
outside the checkout. These tests are **not real actor end-to-end evidence**.

Before claiming real integration: review the landed engine contract, add its
adapter without changing ownership, run isolated synthetic execution through that
engine, verify canonical evidence readback, lost-response idempotency and restart,
and record the exact engine and terminal heads. No live investigations, production
secrets or deployment are part of this change.
