:- module(star_learning, [run/5]).
:- use_module(library(lists)).
:- use_module(library(apply)).
:- use_module(library(time)).
:- use_module(library(http/json)).
:- dynamic value/3, claim/5, edge/4, definition/3, subject/2, passage_match/4.

% Values are canonical JSON scalars, not executable terms. No caller goal is
% invoked through call/1, and no generated directive/rule is consulted.
proof(field(Path,Value), D, T,F,E) :-
    (value(D,Path,Value) -> T=1,E=[D] ; T=0,E=[]), F=0.
proof(same(Path,Anchor), D,T,F,E) :-
    (value(D,Path,V), V \== null, value(Anchor,Path,V) -> T=1,E=[D,Anchor] ; T=0,E=[]), F=0.
proof(passage(Path,Minimum,Anchor),D,T,F,E) :-
    (passage_match(D,Anchor,Path,Minimum) -> T=1,E=[D,Anchor] ; T=0,E=[]), F=0.
proof(attributed(Who,P,O), D,T,F,E) :-
    (D=Who;claim(Who,P,O,_,D)),!,
    findall(R,claim(Who,P,O,positive,R),Ps),
    findall(R,claim(Who,P,O,negative,R),Ns),
    bit(Ps,T),bit(Ns,F),append(Ps,Ns,E).
proof(attributed(_,_,_),_,0,0,[]).
proof(reachable(P,To,Depth), D,T,F,E) :-
    (walk(D,P,To,Depth,[],E0) -> T=1,E=E0 ; T=0,E=[]), F=0.
proof(during(Path,Start,End),D,T,F,E) :-
    (value(D,Path,V), atom_number(V,N), N>=Start,N=<End -> T=1,E=[D] ; T=0,E=[]),F=0.
proof(learned(Id,Version,Anchor),D,T,F,E) :-
    definition(Id,Version,Body), substitute(Body,Anchor,Bound), proof(Bound,D,T,F,E).
proof(all(Items),D,T,F,E) :- proofs(Items,D,Ts,Fs,E), all_bit(Ts,T),any_bit(Fs,F).
proof(any(Items),D,T,F,E) :- proofs(Items,D,Ts,Fs,E), any_bit(Ts,T),all_bit(Fs,F).
proof(exclude(Item),D,T,F,E) :- proof(Item,D,F,T,E).
proof(count(Item,Minimum),_,T,F,E) :-
    findall(S-Es,(subject(S,_),proof(Item,S,1,0,Es)),Pairs),
    length(Pairs,N),(N>=Minimum -> T=1 ; T=0), F=0,
    findall(X,(member(_-Xs,Pairs),member(X,Xs)),E).

substitute(same(P,parameter),A,same(P,A)) :- !.
substitute(field(P,V),_,field(P,V)).
substitute(passage(P,N,parameter),A,passage(P,N,A)) :- !.
bit([],0) :- !.
bit(_,1).
all_bit(Bs,1) :- maplist(=(1),Bs),!.
all_bit(_,0).
any_bit(Bs,1) :- memberchk(1,Bs),!.
any_bit(_,0).
proofs([],_,[],[],[]).
proofs([X|Xs],D,[T|Ts],[F|Fs],E) :- proof(X,D,T,F,E1),proofs(Xs,D,Ts,Fs,E2),append(E1,E2,E).
walk(From,P,To,N,Seen,E) :- N>0,\+memberchk(From,Seen),edge(From,P,Next,R),
    (Next=To -> E=[R] ; M is N-1,walk(Next,P,To,M,[From|Seen],Rest),E=[R|Rest]).
status(1,0,supported).
status(0,1,excluded).
status(0,0,unresolved).
status(1,1,conflicting).
row(Query,Type,_{subject:D,membership:S,evidence:Evidence}) :-
    subject(D,Actual),(Type=any;Type=Actual),
    proof(Query,D,T,F,E),status(T,F,S),sort(E,Evidence).
run(Query,Type,MaxResults,Inferences,Seconds) :-
    catch(call_with_time_limit(Seconds,
      call_with_inference_limit(findall(R,row(Query,Type,R),Rows),Inferences,Outcome)),
      time_limit_exceeded,Outcome=timeout),
    (Outcome==inference_limit_exceeded -> Result=_{execution:budget_exhausted,rows:[]}
    ;Outcome==timeout -> Result=_{execution:timeout,rows:[]}
    ;length(Rows,Count),Count>MaxResults -> Result=_{execution:result_limit,rows:[]}
    ;Result=_{execution:complete,rows:Rows}),
    json_write_dict(current_output,Result),nl.
