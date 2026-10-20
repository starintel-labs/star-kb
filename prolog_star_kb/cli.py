from __future__ import annotations

import argparse
from contextlib import contextmanager
import itertools
import json
import sys
from pathlib import Path
from typing import Iterable, Iterator

from .ingest.autodig import ingest_autodig, ndjson_bytes
from .projection import ProjectionManifest, load_documents, render_projection, stream_projection_lines
from .shapes import DEFAULT_MAX_EXEMPLARS, mine_documents, render_shapes, report_json
from .tools import compile_tool_call, tool_catalog


@contextmanager
def _projection_output(filename: str):
    """Create configured output parents without removing existing contents."""
    try:
        path = Path(filename)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as handle:
            yield handle
    except OSError as exc:
        raise SystemExit(f"cannot write projection {filename!r}: {exc}") from exc


def _ndjson_first_line(line: str) -> bool:
    stripped = line.lstrip()
    if not stripped.startswith("{"):
        return False
    try:
        return isinstance(json.loads(stripped), dict)
    except json.JSONDecodeError:
        return False


def _load_input(input_path: str) -> tuple[str | None, Iterable[str] | None, str]:
    """Lazily classify input as NDJSON or whole-text JSON.

    Returns (first_line, line_iter, whole_text). When first_line is not None the
    input is NDJSON and the stream is first_line followed by line_iter (the
    still-open file iterator); otherwise whole_text holds the entire input.
    """
    handle = None
    if input_path == "-":
        lines: Iterator[str] = iter(sys.stdin)
    else:
        handle = open(input_path, encoding="utf-8")
        lines = iter(handle)
    first = next((line for line in lines if line.strip()), None)
    if first is None:
        if handle is not None:
            handle.close()
        return None, None, ""
    if _ndjson_first_line(first):
        return first, lines, ""
    whole = first + "".join(lines)
    if handle is not None:
        handle.close()
    return None, None, whole


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="prolog-star-kb")
    sub = parser.add_subparsers(dest="command", required=True)

    project = sub.add_parser("project", help="project canonical StarIntel JSON/NDJSON into Prolog facts")
    project.add_argument("input", help="JSON/NDJSON file or - for stdin")
    project.add_argument("-o", "--output", help="output .pl file; defaults to stdout")
    project.add_argument("--manifest", help="StarIntel schema release manifest JSON; defaults to the built-in current core manifest")
    project.add_argument(
        "--stream",
        action="store_true",
        help="force NDJSON line-streaming (constant memory; output follows input order)",
    )

    mine = sub.add_parser("mine-shapes", help="mine deterministic fact-shape/ontology facts from StarIntel JSON/NDJSON")
    mine.add_argument("input", help="JSON/NDJSON file or - for stdin")
    mine.add_argument("-o", "--output", help="output .pl file; defaults to stdout")
    mine.add_argument("--json", help="optional versioned candidate-ontology JSON sidecar path")
    mine.add_argument("--max-exemplars", type=int, default=DEFAULT_MAX_EXEMPLARS,
                      help=f"maximum exemplar _ids per shape (default {DEFAULT_MAX_EXEMPLARS})")
    mine.add_argument(
        "--stream",
        action="store_true",
        help="force NDJSON line-streaming (constant memory; requires NDJSON input)",
    )

    sub.add_parser("tools", help="print AI tool definitions as JSON")

    goal = sub.add_parser("goal", help="compile a safe AI tool call into a bounded Prolog goal")
    goal.add_argument("name")
    goal.add_argument("arguments", help="JSON object containing tool arguments")

    ingest = sub.add_parser(
        "ingest-autodig",
        help="convert AutoDig claim bundles into candidate StarIntel NDJSON documents",
    )
    ingest.add_argument("input", help="JSON/NDJSON claim bundle, Markdown report, or - for stdin")
    ingest.add_argument("-o", "--output", help="output NDJSON file; defaults to stdout")
    ingest.add_argument(
        "--format",
        choices=("auto", "json", "markdown"),
        default="auto",
        help="input format; auto detects Markdown by file suffix",
    )
    ingest.add_argument(
        "--source",
        action="append",
        default=[],
        help="source locator to attach (repeatable; used by archival Markdown ingestion)",
    )
    ingest.add_argument("--dataset", default="autodig-import", help="dataset for generated candidates")

    args = parser.parse_args(argv)
    if args.command == "project":
        manifest = ProjectionManifest()
        if args.manifest:
            raw_manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
            if not isinstance(raw_manifest, dict):
                raise SystemExit("manifest must be a JSON object")
            manifest = ProjectionManifest.from_mapping(raw_manifest)
        first, line_iter, text = _load_input(args.input)
        if args.stream or first is not None:
            if first is not None:
                stream = stream_projection_lines(itertools.chain([first], line_iter), manifest)
            else:
                stream = stream_projection_lines(text.splitlines(), manifest)
            if args.output:
                with _projection_output(args.output) as out:
                    for line in stream:
                        out.write(line + "\n")
            else:
                for line in stream:
                    sys.stdout.write(line + "\n")
            return 0
        rendered = render_projection(load_documents(text), manifest)
        if args.output:
            with _projection_output(args.output) as out:
                out.write(rendered)
        else:
            sys.stdout.write(rendered)
        return 0
    if args.command == "mine-shapes":
        first, line_iter, text = _load_input(args.input)
        if args.stream or first is not None:
            if first is not None:
                documents: Iterable[dict] = _iter_ndjson_documents(itertools.chain([first], line_iter))
            else:
                documents = _iter_ndjson_documents(text.splitlines())
        else:
            documents = load_documents(text)
        report = mine_documents(documents, max_exemplars=max(0, args.max_exemplars))
        rendered = render_shapes(report, max_exemplars=max(0, args.max_exemplars))
        if args.json:
            Path(args.json).write_text(
                json.dumps(report_json(report, max_exemplars=max(0, args.max_exemplars)),
                           ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        if args.output:
            Path(args.output).write_text(rendered, encoding="utf-8")
        else:
            sys.stdout.write(rendered)
        return 0
    if args.command == "tools":
        json.dump(tool_catalog(), sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
        return 0
    if args.command == "goal":
        arguments = json.loads(args.arguments)
        if not isinstance(arguments, dict):
            raise SystemExit("arguments must be a JSON object")
        print(compile_tool_call(args.name, arguments))
        return 0
    if args.command == "ingest-autodig":
        text = sys.stdin.read() if args.input == "-" else Path(args.input).read_text(encoding="utf-8")
        report_format = None if args.format == "auto" else args.format
        documents = ingest_autodig(
            text,
            name=args.input,
            dataset=args.dataset,
            report_format=report_format,
            extra_sources=args.source,
        )
        payload = ndjson_bytes(documents)
        if args.output:
            Path(args.output).write_bytes(payload)
        else:
            sys.stdout.buffer.write(payload)
        return 0
    return 2


def _iter_ndjson_documents(lines: Iterable[str]) -> Iterator[dict]:
    for lineno, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            document = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid NDJSON at line {lineno}: {exc}") from exc
        if not isinstance(document, dict):
            raise ValueError(f"NDJSON line {lineno} is not an object")
        yield document


if __name__ == "__main__":
    raise SystemExit(main())
