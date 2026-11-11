# Shared situation-rule ownership and consumer boundaries

This document defines the repository-ownership boundary for reusable StarIntel
situational-awareness, incident-assessment, and SITREP logic. It is an
architectural contract, not a claim that the engine or new document types already
exist, nor an authorization to expand the currently approved RAGE implementation
slice.

## Canonical owners

| Concern | Implementation owner |
| --- | --- |
| General-purpose symbolic domain rules and rule profiles | `starintel-labs/star-kb` |
| Temporal/spatial inference, event correlation, corroboration, contradictions, severity and prioritization, anomaly/change rules, SITREP inclusion and ranking rules | `starintel-labs/star-kb` |
| StarIntel canonical document vocabulary, schema checks, versioned semantics and conformance fixtures | `star-lang` |
| Ingest, broker queues, actor leasing/scheduling, source permissions, persistent documents, snapshot plumbing and reporting APIs | `starintel-server` |
| Source collection, protocol adapters, and actor execution | `starintel-pro-actors` and other generic StarIntel actor repositories |
| Shared operator display of canonical StarIntel outputs | Generic Quasar operator surface |
| Optional Zara product display, integration, voice, user notifications, and StarIntel API adapter | Zara-scoped repositories **only** |

## No native Zara in StarIntel

StarIntel must remain independently operable without Zara. No StarIntel core,
schema, server, actor, or shared knowledge component may require native Zara
code, import Zara modules, embed Zara-specific user interfaces/assistant flows,
or introduce Zara as an operational dependency. Zara, if integrated, consumes
ordinary authorized StarIntel contracts from within a Zara-scoped repository.
The dependency direction must never reverse.

## One implementation of reusable decision logic

Shared inference, domain heuristics, scoring, and report-selection predicates
belong in versioned and tested `star-kb` modules. That includes:

- Time-window reasoning, currency/freshness, event association, and incident
  grouping criteria.
- Geographic relationships and situation-scope membership decisions.
- Source independence, provenance, corroboration, contradiction and epistemic
  status assessment.
- Event/incident changes, evidence-based severity and prioritization, and
  threshold/reward policies used by more than one StarIntel consumer.
- SITREP statement eligibility, ordering, deduplication, confidence labeling,
  evidence requirements and change-from-last-report assessment.

Host repositories may hold source-specific parsers, transport adapters,
runtime scheduling/fencing, durable queues, schema-generated validators, API
policies, UI presentation and product-local workflows. They must **call** a
bounded, versioned `star-kb` reasoning contract for reusable decisions rather
than translating shared predicates into copied Lisp/Python/JS/Prolog rule files.
An actor may provide inputs, handle effects, and persist authorized results,
but cannot become an alternative shared rules authority.

These rules do not move canonical schema validation out of `star-lang`, move
StarIntel server reliability policy into Prolog, or require product-specific
Zara logic to become shared StarIntel knowledge.

## Truth and replay boundaries

- Canonical StarIntel JSON remains the durable truth; Prolog projections are
  rebuildable indexes and derived rule state.
- Rule outputs are hypotheses/assessments with explicit evidence, missing
  inputs, rule/profile versions, and snapshot cutoffs; they cannot silently
  become factual documents.
- SITREP composition must be reproducible from the same authorized input
  snapshot, rule version and configuration. Late evidence must produce a new
  report revision or successor, not mutate historical conclusions silently.
- False-positive controls must distinguish observed conduct and verified
  threats from discussion, rhetorical support, rumor and mere association.
- Consumer-specific rendering (including any optional LLM prose step) must not
  substitute for the shared evidence and decision rules.

## Contribution checks

1. Identify whether the change is a reusable StarIntel decision rule, canonical
   schema/validation rule, or local runtime/product-specific behavior.
2. Implement reusable rule changes in `star-kb` first with deterministic
   SWI-Prolog fixtures, provenance, bounded query behavior, and negative cases.
3. Publish/consume stable versioned interfaces; add compatibility tests that
   demonstrate two different consumers receive the same decision for identical
   inputs.
4. Put only adapters and local effects in consumer repositories. Do not copy
   generic rule logic or force a dependency on Zara.
5. If new canonical document types or fields are needed, change `star-lang`
   through an explicit version bump and migrate consumers separately.
6. Preserve the active RAGE gates and project-specific approvals; this document
   does not authorize implementing a new SITREP engine without its own design
   review.
