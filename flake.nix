{
  description = "Producer-owned Star-KB command-line tools and shared service";

  inputs.star-lang.url = "github:lost-rob0t/star-lang/bfba04a15f43215cd323e1dde2d98a7343abb3ea";
  inputs.nixpkgs.follows = "star-lang/nixpkgs";

  outputs = { self, nixpkgs, star-lang }:
    let
      # Linux supervision uses prctl, pidfds and /proc. No portability claim.
      system = "x86_64-linux";
      pkgs = nixpkgs.legacyPackages.${system};
      python = pkgs.python3.withPackages (ps: [ ps.jsonschema ps.pyzmq ]);
      source = pkgs.lib.cleanSource ./.;
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
      };
      apps.${system} = {
        default = { type = "app"; program = "${tools}/bin/prolog-star-kb"; };
        prolog-star-kb = { type = "app"; program = "${tools}/bin/prolog-star-kb"; };
        starintel-llm = { type = "app"; program = "${tools}/bin/starintel-llm"; };
      };
      checks.${system} = { installed-cli = cliCheck; source = sourceCheck; };
      devShells.${system}.default = pkgs.mkShell {
        packages = [ python pkgs.swi-prolog pkgs.git ];
      };
    };
}
