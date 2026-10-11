{
  description = "Producer-owned Star-KB command-line tools and shared service";

  inputs.star-lang.url = "github:lost-rob0t/star-lang/4c8d1e19249a5030b7d97f953f694c09849b9131";
  inputs.nixpkgs.follows = "star-lang/nixpkgs";

  outputs = { self, nixpkgs, star-lang }:
    let
      # Linux supervision uses prctl, pidfds and /proc. No portability claim.
      system = "x86_64-linux";
      pkgs = nixpkgs.legacyPackages.${system};
      python = pkgs.python3.withPackages (ps: [ ps.jsonschema ps.pyzmq ]);
      source = pkgs.lib.cleanSource ./.;
      logicRuntime = star-lang.packages.${system}.star-lang-logic-runtime;
      tools = pkgs.stdenvNoCC.mkDerivation {
        pname = "star-kb-tools";
        version = "0.1.0";
        src = source;
        nativeBuildInputs = [ pkgs.makeWrapper ];
        dontBuild = true;
        installPhase = ''
          mkdir -p "$out/share/star-kb" "$out/bin"
          cp -R prolog_star_kb kb tools "$out/share/star-kb/"
          makeWrapper ${python}/bin/python3 "$out/bin/prolog-star-kb" \
            --add-flags "$out/share/star-kb/tools/prolog-star-kb" \
            --prefix PYTHONPATH : "$out/share/star-kb" \
            --prefix PATH : ${pkgs.lib.makeBinPath [ pkgs.swi-prolog ]}
          makeWrapper ${python}/bin/python3 "$out/bin/starintel-llm" \
            --add-flags "$out/share/star-kb/tools/starintel-llm" \
            --prefix PYTHONPATH : "$out/share/star-kb" \
            --prefix PATH : ${pkgs.lib.makeBinPath [ pkgs.swi-prolog ]}
        '';
        meta = {
          description = "Canonical JSON projection and optional LLM harness";
          mainProgram = "prolog-star-kb";
          platforms = [ system ];
        };
      };
      service = pkgs.stdenvNoCC.mkDerivation {
        pname = "star-kb-service";
        version = "0.1.0";
        src = source;
        nativeBuildInputs = [ pkgs.makeWrapper ];
        dontBuild = true;
        installPhase = ''
          mkdir -p "$out/share/star-kb" "$out/bin"
          cp -R star_kb_service prolog_star_kb service "$out/share/star-kb/"
          makeWrapper ${python}/bin/python3 "$out/bin/star-kb-service" \
            --add-flags "-m star_kb_service" \
            --set PYTHONPATH "$out/share/star-kb" \
            --set PYTHONNOUSERSITE 1 \
            --set STARLANG_ROOT "${logicRuntime.sourceRoot}" \
            --set STAR_KB_SBCL "${logicRuntime}/bin/starlang-sbcl" \
            --set STARLANG_SWI_EXECUTABLE "${logicRuntime}/bin/starlang-swipl"
        '';
        meta = {
          description = "Authenticated loopback HTTP/ZMQ knowledge service";
          mainProgram = "star-kb-service";
          platforms = [ system ];
        };
      };
      installedServiceCheck = pkgs.runCommand "star-kb-installed-service-check" {
        nativeBuildInputs = [ python service ];
      } ''
        export HOME="$TMPDIR/home"
        mkdir -p "$HOME" "$TMPDIR/outside-checkout/tests"
        cp ${source}/tests/test_shared_service.py ${source}/tests/test_transport_security.py \
          ${source}/tests/test_adapter_identity.py ${source}/tests/test_installed_service.py \
          "$TMPDIR/outside-checkout/tests/"
        cd "$TMPDIR/outside-checkout"
        export PYTHONPATH=${service}/share/star-kb
        export PYTHONNOUSERSITE=1
        export STARLANG_ROOT=${logicRuntime.sourceRoot}
        export STAR_KB_SBCL=${logicRuntime}/bin/starlang-sbcl
        export STARLANG_SWI_EXECUTABLE=${logicRuntime}/bin/starlang-swipl
        export STAR_KB_INSTALLED_COMMAND=${service}/bin/star-kb-service
        export STAR_KB_INSTALLED_ROOT=${service}/share/star-kb
        python3 -m unittest discover -s tests -p 'test_*.py' -v
        touch "$out"
      '';
      cliCheck = pkgs.runCommand "star-kb-installed-cli-check" {
        nativeBuildInputs = [ tools python pkgs.swi-prolog ];
      } ''
        export HOME="$TMPDIR/home"
        mkdir -p "$HOME"
        cd "$TMPDIR"
        prolog-star-kb --help
        starintel-llm --help
        printf '%s\n' '{"_id":"packaged:relation","dtype":"relation","dataset":"fixture","data":{"subject":"a","predicate":"owns","object":"b"}}' > fixture.json
        prolog-star-kb project fixture.json -o fixture.pl
        swipl -q -g "use_module('${tools}/share/star-kb/kb/core/star_json.pl'),use_module('${tools}/share/star-kb/kb/core/star_reasoning.pl'),star_json:load_projection('fixture.pl'),star_reasoning:tool_explain_relation('packaged:relation',_),halt" -t 'halt(1)'
        touch "$out"
      '';
      sourceCheck = pkgs.runCommand "star-kb-source-check" {
        nativeBuildInputs = [ python pkgs.swi-prolog ];
      } ''
        cp -R ${source} source
        chmod -R u+w source
        cd source
        export HOME="$TMPDIR/home"
        mkdir -p "$HOME"
        python3 scripts/sync.py
        python3 scripts/sync.py --check
        python3 scripts/validate-docs.py
        export STARLANG_IDENTITY_ROOT=${star-lang}
        python3 -m unittest discover -s tests -p 'test_*.py' -v
        touch "$out"
      '';
    in {
      packages.${system} = {
        default = tools;
        prolog-star-kb = tools;
        starintel-llm = tools;
        star-kb-service = service;
      };
      apps.${system} = {
        default = { type = "app"; program = "${tools}/bin/prolog-star-kb"; };
        prolog-star-kb = { type = "app"; program = "${tools}/bin/prolog-star-kb"; };
        starintel-llm = { type = "app"; program = "${tools}/bin/starintel-llm"; };
        star-kb-service = { type = "app"; program = "${service}/bin/star-kb-service"; };
      };
      checks.${system} = { installed-cli = cliCheck; installed-service = installedServiceCheck; source = sourceCheck; };
      devShells.${system}.default = pkgs.mkShell {
        packages = [ python pkgs.swi-prolog pkgs.git ];
      };
    };
}
