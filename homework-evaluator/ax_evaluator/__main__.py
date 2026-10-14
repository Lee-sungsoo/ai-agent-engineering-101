"""Command-line entrypoint. Only the explicit judge command calls a model."""

import argparse
import sys
from pathlib import Path

from .providers import judge
from .review import ROOT, finalize, prepare, validate_review
from .snapshot import collect_local, collect_pr, verify_snapshot
from .storage import canonical, read_json, write_new


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="AX week 5 evidence-based review harness"
    )
    commands = parser.add_subparsers(dest="command", required=True)
    local = commands.add_parser(
        "collect-local", help="Snapshot local files without executing them"
    )
    local.add_argument("source", type=Path)
    local.add_argument("--spec", type=Path, required=True)
    local.add_argument("--out", type=Path, required=True)
    remote = commands.add_parser("collect-pr", help="Read a week 5 PR from GitHub")
    remote.add_argument("number", type=int)
    remote.add_argument("--repo", default="Q00/ai-agent-engineering-101")
    remote.add_argument("--spec-ref", default="main")
    remote.add_argument("--out", type=Path, required=True)
    prep = commands.add_parser(
        "prepare",
        help="Run deterministic checks and create review packet; no model call",
    )
    prep.add_argument("snapshot", type=Path)
    prep.add_argument("--rubric", type=Path, default=ROOT / "rubrics/week05.json")
    prep.add_argument("--out", type=Path, required=True)
    prep.add_argument("--max-chars", type=int, default=180_000)
    prep.add_argument("--per-file-chars", type=int, default=12_000)
    call = commands.add_parser(
        "judge",
        help="Call a model explicitly; may send evidence externally and incur cost",
    )
    call.add_argument("run", type=Path)
    call.add_argument("--config", type=Path, required=True)
    call.add_argument("--out", type=Path, required=True)
    human = commands.add_parser(
        "import-review", help="Validate a manually completed response template"
    )
    human.add_argument("run", type=Path)
    human.add_argument("review", type=Path)
    human.add_argument("--reviewer", required=True)
    human.add_argument("--out", type=Path, required=True)
    final = commands.add_parser(
        "finalize", help="Validate saved review and calculate draft points offline"
    )
    final.add_argument("run", type=Path)
    final.add_argument("response", type=Path)
    final.add_argument("--out", type=Path, required=True)
    evidence = commands.add_parser(
        "evidence", help="Read exact source lines from a snapshot"
    )
    evidence.add_argument("snapshot", type=Path)
    evidence.add_argument("path")
    evidence.add_argument("--start", type=int, default=1)
    evidence.add_argument("--end", type=int)
    args = parser.parse_args(argv)
    try:
        if args.command == "collect-local":
            result = collect_local(args.source, args.spec, args.out)
            print(f"snapshot {result['id']}")
        elif args.command == "collect-pr":
            if args.number <= 0:
                raise ValueError("PR number must be positive")
            result = collect_pr(args.repo, args.number, args.out, args.spec_ref)
            print(f"snapshot {result['id']} ({len(result['files'])} files)")
        elif args.command == "prepare":
            if args.max_chars <= 0 or args.per_file_chars <= 0:
                raise ValueError("Evidence limits must be positive")
            bundle = prepare(
                read_json(args.snapshot),
                read_json(args.rubric),
                args.out,
                args.max_chars,
                args.per_file_chars,
            )
            print(f"prepared {bundle['id']}; no model called")
        elif args.command == "judge":
            judge(
                read_json(args.run / "bundle.json"),
                read_json(args.run / "snapshot.json"),
                read_json(args.config),
                args.out,
            )
            print(f"validated response: {args.out / 'response.json'}")
        elif args.command == "import-review":
            bundle, snapshot, review = (
                read_json(args.run / "bundle.json"),
                read_json(args.run / "snapshot.json"),
                read_json(args.review),
            )
            validate_review(review, bundle, snapshot)
            write_new(
                args.out,
                {
                    "bundle_id": bundle["id"],
                    "review": review,
                    "provenance": {"kind": "human", "reviewer": args.reviewer},
                },
            )
            print(f"validated human review: {args.out}")
        elif args.command == "finalize":
            report = finalize(args.run, read_json(args.response), args.out)
            print(
                canonical(
                    {
                        key: report[key]
                        for key in (
                            "base_earned",
                            "base_max",
                            "base_pending",
                            "bonus_earned",
                        )
                    }
                )
            )
        elif args.command == "evidence":
            snapshot = read_json(args.snapshot)
            verify_snapshot(snapshot)
            text = snapshot["files"][args.path]["text"]
            if text is None:
                raise ValueError("File is not UTF-8 text")
            lines = text.splitlines()
            end = args.end if args.end is not None else len(lines)
            if not 1 <= args.start <= end <= len(lines):
                raise ValueError("Invalid line range")
            for number in range(args.start, end + 1):
                print(f"{number}: {lines[number - 1]}")
    except (ValueError, OSError, KeyError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
