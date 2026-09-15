"""CLI entrypoint: `python -m orchestrator.run "goal text"`.

stdout is always a single `{status, result, error}` JSON object — including for bad CLI usage —
so callers can parse stdout unconditionally. Exit code is 0 for `ok`, 1 for `error`, matching the
envelope rather than duplicating information not in it.
"""

from __future__ import annotations

import argparse
import sys

from orchestrator.graph import run_goal
from workers import registry
from workers.contracts import WorkerResult, failure
from workers.model import ENV_VAR

EXIT_OK = 0
EXIT_ERROR = 1


class _StructuredArgumentParser(argparse.ArgumentParser):
    """Emit the result envelope on usage errors instead of argparse's plain-text exit."""

    def error(self, message: str) -> "None":  # type: ignore[override]
        _emit(failure(f"invalid command line: {message}"))
        raise SystemExit(EXIT_ERROR)


def _build_parser() -> argparse.ArgumentParser:
    parser = _StructuredArgumentParser(
        prog="python -m orchestrator.run",
        description="Dispatch one goal to a worker and print the result envelope.",
    )
    parser.add_argument(
        "goal",
        nargs="*",
        help="the goal; quote it, or pass it as several words",
    )
    parser.add_argument(
        "--worker",
        default=None,
        metavar="NAME",
        help="dispatch to a specific worker; omit to let the router choose from the goal",
    )
    parser.add_argument(
        "--backend",
        default=None,
        metavar="SPEC",
        help=f"model backend override, e.g. offline or anthropic:claude-sonnet-4-5 (env: {ENV_VAR})",
    )
    parser.add_argument(
        "--compact",
        action="store_true",
        help="print the envelope on one line instead of indented",
    )
    return parser


def _emit(result: WorkerResult, *, compact: bool = False) -> None:
    print(result.to_json(indent=None if compact else 2))


def main(argv: list[str] | None = None) -> int:
    _force_utf8_stdout()
    args = _build_parser().parse_args(argv)

    if args.backend:
        import os

        os.environ[ENV_VAR] = args.backend

    goal = " ".join(args.goal).strip()
    if not goal:
        _emit(failure("no goal given; usage: python -m orchestrator.run \"goal text\""),
              compact=args.compact)
        return EXIT_ERROR

    if args.worker is not None and not registry.is_known(args.worker):
        _emit(
            failure(
                f"unknown worker '{args.worker}'; known workers: {list(registry.worker_names())}"
            ),
            compact=args.compact,
        )
        return EXIT_ERROR

    try:
        result = run_goal(goal, worker=args.worker)
    except KeyboardInterrupt:
        _emit(failure("interrupted by user"), compact=args.compact)
        return EXIT_ERROR
    except Exception as exc:
        result = failure(f"orchestrator entrypoint failed with {type(exc).__name__}: {exc}")

    _emit(result, compact=args.compact)
    return EXIT_OK if result.status == "ok" else EXIT_ERROR


def _force_utf8_stdout() -> None:
    """Keep non-ASCII findings printable on a cp1252 Windows console."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
        except (AttributeError, ValueError):
            pass


if __name__ == "__main__":
    raise SystemExit(main())
