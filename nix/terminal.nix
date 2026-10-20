# Terminal packaging stays separate from engine/runtime packaging.
{ pkgs, python, source, logicRuntime, system }:
let
  package = pkgs.stdenvNoCC.mkDerivation {
    pname = "star-kb-terminal";
    version = "0.1.0";
    src = source;
    nativeBuildInputs = [ pkgs.makeWrapper ];
    dontBuild = true;
    installPhase = ''
      mkdir -p "$out/share/star-kb" "$out/bin"
      cp -R star_kb_terminal star_kb_service prolog_star_kb "$out/share/star-kb/"
      makeWrapper ${python}/bin/python3 "$out/bin/star-kb-terminal" \
        --add-flags "-m star_kb_terminal.cli" \
        --set PYTHONPATH "$out/share/star-kb" \
        --set PYTHONNOUSERSITE 1 \
        --set STARLANG_ROOT "${logicRuntime.sourceRoot}" \
        --set STAR_KB_TERMINAL_SWIPL "${pkgs.swi-prolog}/bin/swipl"
    '';
    meta = {
      description = "Deterministic Prolog operator terminal with explicit simulation adapter";
      mainProgram = "star-kb-terminal";
      platforms = [ system ];
    };
  };
  check = pkgs.runCommand "star-kb-terminal-check" {
    nativeBuildInputs = [ python pkgs.swi-prolog package ];
  } ''
    mkdir -p tests/fixtures/terminal
    cp ${source}/tests/test_terminal.py tests/
    cp ${source}/tests/fixtures/terminal/* tests/fixtures/terminal/
    export PYTHONPATH=${package}/share/star-kb
    export STARLANG_ROOT=${logicRuntime.sourceRoot}
    export STAR_KB_TERMINAL_COMMAND=${package}/bin/star-kb-terminal
    export STAR_KB_TERMINAL_SWIPL=${pkgs.swi-prolog}/bin/swipl
    python3 -m unittest discover -s tests -p test_terminal.py -v
    swipl -q -s ${source}/tests/terminal_tests.pl \
      -g run_tests -t halt
    touch "$out"
  '';
in { inherit package check; }
