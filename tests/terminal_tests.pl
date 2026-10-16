:- use_module('../star_kb_terminal/prolog/planner').
:- begin_tests(terminal_planner).
test(compatible) :- compatibility(_{accepts:[domain]}, domain, compatible).
test(incompatible) :- compatibility(_{accepts:[url]}, domain, incompatible).
test(missing_declaration) :- compatibility(_{accepts:[]}, domain, unknown).
test(untyped_input) :- compatibility(_{accepts:[host]}, null, unknown).
test(acceptance_not_completion, [fail]) :- stopped(accepted).
test(running_not_completion, [fail]) :- stopped(running).
test(blocked_stops) :- stopped(blocked).
test(completed_stops) :- stopped(completed).
test(no_goal_execution) :-
    candidates([_{actor:evil, accepts:['halt(9)'], produces:[]}], domain,
               [R]), R.compatibility == incompatible.
:- end_tests(terminal_planner).
