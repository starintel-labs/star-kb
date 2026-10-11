# Producer packages: issue #24

This slice exports locked `prolog-star-kb`, `starintel-llm` and `star-kb-service` packages/apps from
Star-KB. `default` selects the projection CLI. Python, SWI and KB rule files are
in the installed closure, and wrappers locate data independently of the checkout.
The harness remains opt-in: installing it makes no provider call or secret read.
Secrets and operator configurations must stay outside the Nix store.

```
nix run .#prolog-star-kb -- --help
nix run .#starintel-llm -- --help
nix build .#prolog-star-kb
nix run .#star-kb-service -- /path/to/operator-config.json --http-port 8080
nix flake check --no-update-lock-file -L
```

Only `x86_64-linux` is currently declared. Other systems are neither declared nor
validated; the service uses Linux prctl, pidfds and /proc and cannot be described
as portable. The lock follows the pinned StarLang producer's nixpkgs revision.

The installed CLI check runs from a temporary directory, projects a synthetic
relation and queries it with actual SWI. Source checks run the existing RAGE,
Python and pinned-source identity gates. They explicitly skip real service tests
when a StarLang worker runtime is absent; this is not installed-service coverage.
The existing dedicated shared-service CI still runs actual SBCL/SWI integration.

## Installed adapter provenance

The prior engine required a clean Git checkout. Packaged immutable source trees
omit `.git`, so the engine now verifies a trusted, checked-in SHA-256 inventory
of every tracked file at the exact StarLang commit. Missing, added, changed and
symlink-substituted files fail closed. Git checkouts still require the exact clean
HEAD as well. Removing `.git` does not disable the byte check. No environment
variable or request field can turn verification off.

The inventory covers 421 files at
`4c8d1e19249a5030b7d97f953f694c09849b9131`, including the generated canonical 0.10.1
schema. Refresh only as part of a reviewed dependency repin:

```
python3 scripts/pin-adapter-source.py /path/to/exact-clean-star-lang
python3 scripts/pin-adapter-source.py --check /path/to/exact-clean-star-lang
```

## Installed shared service

The package consumes StarLang's merged `star-lang-logic-runtime` output and its
public `sourceRoot`, `starlang-sbcl`, and `starlang-swipl` entrypoints. It copies
only Star-KB's own Python/worker files. Lisp dependencies, SWI and StarLang source
remain producer-owned; no private build inputs or duplicate runtime are used.
Canonical schema/core hashes are unchanged by this runtime/source repin.

`checks.installed-service` copies only test files into an empty temporary working
directory, imports the installed KB modules, and executes existing real
HTTP/ZMQ/SWI auth, scope, replay, cancellation, deadline, crash and cleanup tests.
It also starts the actual installed CLI with runtime paths removed from the
caller environment, checks HTTP/ZMQ parity and provenance, then proves owned
Lisp/SWI processes are gone after termination. All data and credentials are
synthetic and created at test execution, outside the store. No durable server
or live provider is required by this installed-package check.

Actual execution evidence must come from exact-head Nix CI; source-only tests do
not substitute for installed-package proof. Remote readiness, proactive experts,
RabbitMQ actor transport and deployment remain separate milestones.
