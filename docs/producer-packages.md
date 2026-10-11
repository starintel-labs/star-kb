# Producer packages: issue #24

This slice exports locked `prolog-star-kb` and `starintel-llm` packages/apps from
Star-KB. `default` selects the projection CLI. Python, SWI and KB rule files are
in the installed closure, and wrappers locate data independently of the checkout.
The harness remains opt-in: installing it makes no provider call or secret read.
Secrets and operator configurations must stay outside the Nix store.

```
nix run .#prolog-star-kb -- --help
nix run .#starintel-llm -- --help
nix build .#prolog-star-kb
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
`bfba04a15f43215cd323e1dde2d98a7343abb3ea`, including the generated canonical 0.10.1
schema. Refresh only as part of a reviewed dependency repin:

```
python3 scripts/pin-adapter-source.py /path/to/exact-clean-star-lang
python3 scripts/pin-adapter-source.py --check /path/to/exact-clean-star-lang
```

## Outstanding shared-service producer seam

At the inspected StarLang pin, the producer exports its CLI but not a supported
SBCL/SWI adapter runtime package/library. A consumer must not extract private
build inputs, reassemble the Lisp dependency list or invent a replacement runtime.
The shared-service package and installed HTTP/ZMQ integration check therefore
remain outstanding until the StarLang producer exports that seam. The immutable
identity support here prepares that integration without claiming it is complete.
No remote readiness, persistence milestone or deployment is implied.
