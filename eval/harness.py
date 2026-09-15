"""Golden-set eval runner for worker prompts.

    python -m eval.harness --worker research-specialist --prompt-version v1
    python -m eval.harness --worker research-specialist --repeat 3   # flakiness check

Loads `eval/<worker>/cases.yaml`, runs each goal through the worker at a pinned prompt version,
scores the envelope, and prints a report: overall pass rate, then every non-passing case with the
reason it did not pass.

`--repeat N` runs the whole suite N times and reports per-case stability. Prompt changes are judged
on the pass rate, so a case whose verdict moves between identical runs makes the number
meaningless; the harness surfaces that rather than leaving it to be discovered later.

Exit code is 0 when the pass rate meets `--threshold` and nothing errored, 1 otherwise.
"""

from __future__ import annotations

import argparse
import contextlib
import inspect
import io
import json
import sys
import time
from pathlib import Path
from typing import Any, Callable

import yaml
from pydantic import ValidationError

from eval.scoring import Case, CaseOutcome, JudgeBackend, score_case
from workers.contracts import WorkerResult, failure
from workers.model import ENV_VAR, ModelUnavailable, resolve_backend

_REPO_ROOT = Path(__file__).resolve().parents[1]

DEFAULT_THRESHOLD = 0.90
CASES_FILENAME = "cases.yaml"

#: `run(goal, *, backend, prompt_version) -> WorkerResult`
WorkerRunner = Callable[..., WorkerResult]

_REPORT_WIDTH = 88


class SuiteError(RuntimeError):
    """The suite could not be set up: bad cases file, unknown worker, unavailable backend."""


# --------------------------------------------------------------------------------------------
# Loading
# --------------------------------------------------------------------------------------------


def cases_path(worker: str) -> Path:
    return _REPO_ROOT / "eval" / worker / CASES_FILENAME


def load_cases(path: Path) -> list[Case]:
    """Parse and validate a cases file. Raises `SuiteError` naming the offending case."""
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise SuiteError(f"could not read cases file {path}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise SuiteError(f"cases file {path.name} is not valid YAML: {exc}") from exc

    if not isinstance(raw, dict):
        raise SuiteError(f"cases file {path.name} must be a mapping at the top level")

    entries = raw.get("cases")
    if not isinstance(entries, list) or not entries:
        raise SuiteError(f"cases file {path.name} has no 'cases' list")

    cases: list[Case] = []
    seen: set[str] = set()
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise SuiteError(f"case at index {index} is not a mapping")
        label = entry.get("id", f"<index {index}>")
        try:
            case = Case.model_validate(entry)
        except ValidationError as exc:
            raise SuiteError(f"case {label!r} is invalid: {_terse(exc)}") from exc
        if case.id in seen:
            raise SuiteError(f"duplicate case id {case.id!r}")
        seen.add(case.id)
        cases.append(case)

    return cases


def load_worker(worker: str) -> WorkerRunner:
    """Resolve a worker name like `research-specialist` to its `run` callable."""
    module_name = f"workers.{worker.replace('-', '_')}"
    try:
        module = __import__(module_name, fromlist=["run"])
    except ImportError as exc:
        raise SuiteError(f"no worker package for {worker!r} (tried {module_name}): {exc}") from exc

    run = getattr(module, "run", None)
    if not callable(run):
        raise SuiteError(f"{module_name} has no callable run()")
    return run


# --------------------------------------------------------------------------------------------
# Running
# --------------------------------------------------------------------------------------------


def run_case(
    case: Case,
    runner: WorkerRunner,
    *,
    prompt_version: str | None,
    worker_backend: Any | None,
    judge: JudgeBackend | None,
) -> CaseOutcome:
    """Run and score one case. Never raises; a crashing worker becomes an `error` outcome."""
    started = time.monotonic()
    kwargs: dict[str, Any] = {"backend": worker_backend}
    if prompt_version is not None and _accepts(runner, "prompt_version"):
        kwargs["prompt_version"] = prompt_version

    # Workers emit lifecycle events as JSON lines on stdout. That is right for a real run and
    # noise here, so it is captured and dropped rather than interleaved with the report.
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            result = runner(case.goal, **kwargs)
    except Exception as exc:
        result = failure(f"worker raised {type(exc).__name__}: {exc}")

    if not isinstance(result, WorkerResult):
        return CaseOutcome(
            case_id=case.id,
            outcome="error",
            reason=f"worker returned {type(result).__name__}, expected WorkerResult",
            tags=case.tags,
            judged=case.is_judged,
            elapsed_s=time.monotonic() - started,
        )

    outcome, reason, judge_reason = score_case(case, result, judge=judge)

    return CaseOutcome(
        case_id=case.id,
        outcome=outcome,
        reason=reason,
        tags=case.tags,
        status=result.status,
        judged=case.is_judged,
        judge_reason=judge_reason,
        output=(result.result or result.error or ""),
        elapsed_s=time.monotonic() - started,
    )


def _accepts(func: Callable[..., Any], parameter: str) -> bool:
    """True when `func` takes `parameter`, so a worker without the seam still evaluates."""
    try:
        signature = inspect.signature(func)
    except (TypeError, ValueError):
        return False
    if parameter in signature.parameters:
        return True
    return any(p.kind is inspect.Parameter.VAR_KEYWORD for p in signature.parameters.values())


def run_suite(
    cases: list[Case],
    runner: WorkerRunner,
    *,
    prompt_version: str | None = None,
    worker_backend: Any | None = None,
    judge: JudgeBackend | None = None,
    on_case: Callable[[CaseOutcome], None] | None = None,
) -> list[CaseOutcome]:
    outcomes = []
    for case in cases:
        outcome = run_case(
            case,
            runner,
            prompt_version=prompt_version,
            worker_backend=worker_backend,
            judge=judge,
        )
        outcomes.append(outcome)
        if on_case is not None:
            on_case(outcome)
    return outcomes


def pass_rate(outcomes: list[CaseOutcome]) -> float:
    if not outcomes:
        return 0.0
    return sum(1 for o in outcomes if o.passed) / len(outcomes)


# --------------------------------------------------------------------------------------------
# Reporting
# --------------------------------------------------------------------------------------------

_MARK = {"pass": "PASS", "fail": "FAIL", "error": "ERR "}


def _rule(char: str = "-") -> str:
    return char * _REPORT_WIDTH


def print_run_report(outcomes: list[CaseOutcome], *, header: str) -> None:
    print(_rule("="))
    print(header)
    print(_rule("="))

    width = max((len(o.case_id) for o in outcomes), default=10)
    for outcome in outcomes:
        tier = "judge" if outcome.judged else "check"
        print(f"{_MARK[outcome.outcome]}  {outcome.case_id:<{width}}  {tier:<5}  {outcome.reason}")

    counts = {
        key: sum(1 for o in outcomes if o.outcome == key) for key in ("pass", "fail", "error")
    }
    rate = pass_rate(outcomes)
    print(_rule())
    print(
        f"pass rate: {counts['pass']}/{len(outcomes)} = {rate:.2f}   "
        f"(failed {counts['fail']}, harness errors {counts['error']})"
    )


def print_failure_detail(outcomes: list[CaseOutcome], cases: dict[str, Case]) -> None:
    problems = [o for o in outcomes if not o.passed]
    if not problems:
        return

    print()
    print(_rule("="))
    print("NON-PASSING CASES")
    print(_rule("="))
    for outcome in problems:
        case = cases.get(outcome.case_id)
        print()
        print(f"[{outcome.outcome.upper()}] {outcome.case_id}   tags: {', '.join(outcome.tags)}")
        if case is not None:
            print(f"  goal:     {_clip(case.goal, 300)}")
            print(f"  expected: status {case.expect.status}")
        print(f"  got:      status {outcome.status}")
        print(f"  why:      {outcome.reason}")
        if outcome.output:
            print(f"  output:   {_clip(outcome.output, 600)}")


def print_stability_report(runs: list[list[CaseOutcome]]) -> bool:
    """Report per-case verdict stability across runs. Returns True when every case was stable."""
    print()
    print(_rule("="))
    print(f"STABILITY ACROSS {len(runs)} RUNS")
    print(_rule("="))

    by_case: dict[str, list[str]] = {}
    for run in runs:
        for outcome in run:
            by_case.setdefault(outcome.case_id, []).append(outcome.outcome)

    flaky = {cid: verdicts for cid, verdicts in by_case.items() if len(set(verdicts)) > 1}
    rates = [pass_rate(run) for run in runs]

    print("pass rate per run: " + ", ".join(f"{r:.2f}" for r in rates))
    if flaky:
        print(f"UNSTABLE: {len(flaky)} case(s) changed verdict between identical runs")
        width = max(len(cid) for cid in flaky)
        for cid, verdicts in sorted(flaky.items()):
            print(f"  {cid:<{width}}  {' -> '.join(verdicts)}")
        print()
        print("The pass rate is not reproducible while these cases move. Treat the score as")
        print("provisional until they are stable.")
        return False

    spread = max(rates) - min(rates) if rates else 0.0
    print(f"STABLE: every case returned the same verdict in all {len(runs)} runs (spread {spread:.2f})")
    return True


# --------------------------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m eval.harness",
        description="Score a worker prompt against its golden set.",
    )
    parser.add_argument(
        "--worker", default="research-specialist", help="worker name (default: %(default)s)"
    )
    parser.add_argument(
        "--prompt-version",
        default=None,
        metavar="vN",
        help="prompt version to score; defaults to the worker's shipped version",
    )
    parser.add_argument(
        "--backend",
        default=None,
        metavar="SPEC",
        help=f"worker model backend, e.g. anthropic:claude-sonnet-4-5 (env: {ENV_VAR})",
    )
    parser.add_argument(
        "--judge-backend",
        default=None,
        metavar="SPEC",
        help="model backend for the LLM judge; defaults to --backend",
    )
    parser.add_argument(
        "--repeat",
        type=int,
        default=1,
        metavar="N",
        help="run the suite N times and report stability (default: 1)",
    )
    parser.add_argument(
        "--only",
        default=None,
        metavar="SUBSTR",
        help="run only cases whose id or tags contain SUBSTR",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=DEFAULT_THRESHOLD,
        help="pass rate required for exit code 0 (default: %(default)s)",
    )
    parser.add_argument(
        "--json",
        dest="as_json",
        action="store_true",
        help="emit machine-readable results instead of the text report",
    )
    return parser


def _select(cases: list[Case], needle: str | None) -> list[Case]:
    if not needle:
        return cases
    lowered = needle.lower()
    kept = [c for c in cases if lowered in c.id.lower() or any(lowered in t.lower() for t in c.tags)]
    if not kept:
        raise SuiteError(f"--only {needle!r} matched no cases")
    return kept


def _resolve(spec: str | None, role: str) -> Any:
    try:
        return resolve_backend(spec)
    except ModelUnavailable as exc:
        raise SuiteError(f"{role} backend unavailable: {exc}") from exc


def main(argv: list[str] | None = None) -> int:
    _force_utf8_stdout()
    args = _build_parser().parse_args(argv)

    try:
        if args.repeat < 1:
            raise SuiteError("--repeat must be at least 1")

        cases = _select(load_cases(cases_path(args.worker)), args.only)
        runner = load_worker(args.worker)
        worker_backend = _resolve(args.backend, "worker")
        judge = _resolve(args.judge_backend or args.backend, "judge")
    except SuiteError as exc:
        print(f"eval setup failed: {exc}", file=sys.stderr)
        return 1

    judged = sum(1 for c in cases if c.is_judged)
    worker_name = getattr(worker_backend, "name", "?")
    judge_name = getattr(judge, "name", "?")

    if not args.as_json:
        _warn_if_unusable(worker_name, judge_name, judged)

    runs: list[list[CaseOutcome]] = []
    for index in range(args.repeat):
        header = (
            f"eval: {args.worker}   prompt={args.prompt_version or 'default'}   "
            f"worker={worker_name}   judge={judge_name}\n"
            f"cases: {len(cases)} ({judged} judged, {len(cases) - judged} deterministic)   "
            f"run {index + 1} of {args.repeat}"
        )
        outcomes = run_suite(
            cases,
            runner,
            prompt_version=args.prompt_version,
            worker_backend=worker_backend,
            judge=judge,
        )
        runs.append(outcomes)
        if not args.as_json:
            if index:
                print()
            print_run_report(outcomes, header=header)

    final = runs[-1]
    stable = True

    if args.as_json:
        print(
            json.dumps(
                {
                    "worker": args.worker,
                    "prompt_version": args.prompt_version,
                    "worker_backend": worker_name,
                    "judge_backend": judge_name,
                    "repeat": args.repeat,
                    "pass_rate": pass_rate(final),
                    "pass_rate_per_run": [pass_rate(r) for r in runs],
                    "runs": [[o.model_dump() for o in r] for r in runs],
                },
                indent=2,
                ensure_ascii=False,
            )
        )
    else:
        print_failure_detail(final, {c.id: c for c in cases})
        if args.repeat > 1:
            stable = print_stability_report(runs)

    rate = pass_rate(final)
    errored = any(o.outcome == "error" for o in final)
    return 0 if (rate >= args.threshold and not errored and stable) else 1


def _warn_if_unusable(worker_name: str, judge_name: str, judged: int) -> None:
    """Say up front when the configured backends cannot produce a meaningful score."""
    if worker_name == "offline":
        print(
            "WARNING: the worker backend is 'offline'. It returns a fixed canned payload and\n"
            f"         ignores the prompt entirely, so this run does not measure the prompt.\n"
            f"         Set {ENV_VAR} or --backend to a hosted model for a real score.\n",
            file=sys.stderr,
        )
    if judge_name == "offline" and judged:
        print(
            f"WARNING: the judge backend is 'offline' and cannot apply a rubric, so the {judged}\n"
            "         judged case(s) will be reported as harness errors rather than scored.\n",
            file=sys.stderr,
        )


def _clip(text: str, limit: int) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else flat[: limit - 3] + "..."


def _terse(exc: ValidationError) -> str:
    parts = []
    for err in exc.errors():
        location = ".".join(str(item) for item in err["loc"]) or "<root>"
        parts.append(f"{location}: {err['msg']}")
    return "; ".join(parts)


def _force_utf8_stdout() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
        except (AttributeError, ValueError):
            pass


if __name__ == "__main__":
    raise SystemExit(main())
