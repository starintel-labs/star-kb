:- module(starintel_symbolic_investigation,
          [ expert_revision/1,
            ownership_path_candidates/3,
            contradiction_cases/3,
            frontier_question/4,
            evidence_coverage/2
          ]).

:- use_module(library(solution_sequences), [findnsols/4]).
:- use_module('../../core/star_json',
              [star_relation/9, star_source/7, star_evidence/7]).
:- use_module('../../core/star_reasoning',
              [tool_contradictions/2, tool_why_not/4]).

% An opt-in, read-only expert. star_json remains the disposable projection
% of canonical StarIntel documents. None of these predicates performs effects,
% admits rules from callers, or asserts a candidate as a canonical relation.
expert_revision(1).

max_results(Max) :-
    integer(Max),
    between(1, 64, Max).

:- meta_predicate bounded_results(+, ?, 0, -, -).

% Deliberately bound enumeration *before* sorting. Truncated means that more
% raw proofs were found; this is not an exhaustive or globally ranked search.
bounded_results(Max, Template, Goal, Items, Truncated) :-
    Limit is Max + 1,
    findnsols(Limit, Template, Goal, Found),
    length(Found, Count),
    (   Count > Max
    ->  length(Prefix, Max),
        append(Prefix, _, Found),
        Truncated = true
    ;   Prefix = Found,
        Truncated = false
    ),
    sort(Prefix, Items).

% Two explicitly directed, positive "owns" edges imply a *path candidate*,
% NOT legal control, beneficial ownership, identity equivalence, or a new fact.
% No generic "related-to" assertion is generated. Proofs identify exact
% canonical-source relation IDs for caller-side evidence review.
ownership_path_candidates(Source, Max, results(Candidates, Truncated)) :-
    atom(Source),
    max_results(Max),
    bounded_results(Max, Candidate,
                    ownership_path(Source, Candidate),
                    Candidates, Truncated).

ownership_path(Source,
               candidate(ownership_chain, Source, Target,
                         proof([FirstRelation, SecondRelation]), Status)) :-
    star_relation(FirstRelation, Source, owns, Middle,
                  true, false, _, _, _),
    star_relation(SecondRelation, Middle, owns, Target,
                  true, false, _, _, _),
    atom(Middle),
    atom(Target),
    FirstRelation \= SecondRelation,
    Source \= Middle,
    Middle \= Target,
    Source \= Target,
    proof_status([FirstRelation, SecondRelation], Status).

% "linked" means evidence references an existing source ID. It deliberately
% does not mean corroborated, authentic, independently verified, or true.
evidence_coverage(RelationId, Status) :-
    atom(RelationId),
    (   once((star_source(RelationId, SourceId, _, _, _, _, _),
              star_evidence(RelationId, _, SourceId, _, _, _, _)))
    ->  Status = linked
    ;   Status = missing
    ).

edge_disputed(RelationId) :-
    star_relation(RelationId, Subject, Predicate, Object,
                  Directed, false, _, _, _),
    star_relation(_, Subject, Predicate, Object,
                  Directed, true, _, _, _).

% A detected negation is only a *possible* conflict: callers must assess
% temporal qualifiers and provenance, not automatically invalidate an edge.
proof_status(Relations, Status) :-
    (   (member(RelationId, Relations), edge_disputed(RelationId))
    ->  Status = disputed
    ;   (member(RelationId, Relations),
         evidence_coverage(RelationId, missing))
    ->  Status = needs_evidence
    ;   Status = review_required
    ).

% Reuse star_reasoning's contradiction semantics rather than fork them.
contradiction_cases(Subject, Max, results(Cases, Truncated)) :-
    atom(Subject),
    max_results(Max),
    bounded_results(Max, Case,
                    tool_contradictions(Subject, Case),
                    Cases, Truncated).

% This question is not a collection target. It identifies absent *direct*
% support for a specific, typed assertion; absence is not negation.
frontier_question(Subject, Predicate, Object,
                  question(Subject, Predicate, Object, missing_direct_support)) :-
    atom(Subject),
    atom(Predicate),
    atom(Object),
    Predicate \= '',
    Predicate \= 'related-to',
    Predicate \= related_to,
    tool_why_not(Subject, Predicate, Object,
                 missing_direct_relation(Subject, Predicate, Object)).
