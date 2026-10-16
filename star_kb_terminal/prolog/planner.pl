:- module(terminal_planner, [compatibility/3, candidates/3, stopped/1, main/0]).
:- use_module(library(http/json)).

% Catalog declarations arrive as JSON data. No assertions or user goals.
compatibility(Actor, Type, unknown) :-
    ( Type == null ; Actor.accepts == [] ), !.
compatibility(Actor, Type, compatible) :- memberchk(Type, Actor.accepts), !.
compatibility(_, _, incompatible).

candidates(Actors, Type, Results) :- maplist(describe(Type), Actors, Results).
describe(Type, Actor, Result) :-
    compatibility(Actor, Type, Status),
    Result = _{actor:Actor.actor, compatibility:Status, accepts:Actor.accepts,
               produces:Actor.produces}.

stopped(completed).
stopped(failed).
stopped(cancelled).
stopped(budget_exhausted).
stopped(blocked).

main :-
    catch((json_read_dict(current_input, Request, [value_string_as(atom)]),
           candidates(Request.actors, Request.dtype, Rows),
           json_write_dict(current_output, _{candidates:Rows}), nl),
          _, halt(2)).
