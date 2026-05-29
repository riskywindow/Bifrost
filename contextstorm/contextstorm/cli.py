"""CLI for ContextStorm."""

from __future__ import annotations

import argparse
from pathlib import Path

from .report import write_report
from .runner import ContextStormError, run_scenario
from .synthetic_kv import generate_synthetic_object, write_synthetic_object


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="contextstorm")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="run a scenario YAML file")
    run_parser.add_argument("scenario", type=Path)
    run_parser.add_argument("--runs-root", type=Path, default=None)
    run_parser.add_argument("--run-id", default=None)
    run_parser.add_argument(
        "--allow-root-faults",
        action="store_true",
        help="allow root-required local tc/netem fault profiles",
    )

    report_parser = subparsers.add_parser("report", help="write summary reports")
    report_parser.add_argument("run_dir", type=Path)

    generate_parser = subparsers.add_parser(
        "generate-synthetic", help="generate a deterministic synthetic object"
    )
    generate_parser.add_argument("--out", type=Path, required=True)
    generate_parser.add_argument("--size", type=int, required=True)
    generate_parser.add_argument(
        "--object-type",
        choices=["native_kv_page", "opaque_engine_blob"],
        default="opaque_engine_blob",
    )

    args = parser.parse_args(argv)
    try:
        if args.command == "run":
            run_dir = run_scenario(
                args.scenario,
                runs_root=args.runs_root,
                run_id=args.run_id,
                allow_root_faults=args.allow_root_faults,
            )
            print(run_dir)
            return 0
        if args.command == "report":
            summary_json, summary_md = write_report(args.run_dir)
            print(summary_json)
            print(summary_md)
            return 0
        if args.command == "generate-synthetic":
            obj = generate_synthetic_object(
                object_size_bytes=args.size, object_type=args.object_type
            )
            manifest = write_synthetic_object(obj, args.out)
            print(manifest["object_id"])
            return 0
    except (ContextStormError, OSError, ValueError) as exc:
        parser.exit(2, f"contextstorm: error: {exc}\n")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
