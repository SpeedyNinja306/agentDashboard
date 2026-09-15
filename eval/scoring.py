"""Case schema and scoring for the golden-set eval.

Two scoring tiers, chosen per case by whoever writes the case:

`checks` are deterministic assertions and are used wherever a goal has a checkable right answer.
`judge` is an explicit PASS/FAIL rubric applied by a language model, used only for open-ended goals
where no assertion can express correctness.

The rubric text always lives in the YAML. This module applies rubrics but never contains one, so
the definition of a good answer stays reviewable in a diff instead of being buried in scoring code.

A case has three possible outcomes, not two. `error` is distinct from `fail`: it means the harness
could not obtain a verdict (the worker backend was unreachable, the judge was unavailable, the
judge returned something unparseable). Reporting those as failures would understate a prompt and
hide a broken harness, so they are counted and displayed separately.
"""

from __future__ import annotations

import json
import re
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from workers.contracts import WorkerResult

Outcome = Literal["pass", "fail", "error"]
ExpectedStatus = Literal["ok", "error", "any"]
TextTarget = Literal["result", "error", "auto"]

CheckKind = Literal[
    "contains_all",
    "contains_any",
    "not_contains",
    "regex",
    "not_regex",
    "min_words",
    "max_words",
]

#: Checks taking a list of strings in `values`; the rest take a scalar in `value`.
_LIST_CHECKS = frozenset({"contains_all", "contains_any", "not_contains"})
_INT_CHECKS = frozenset({"min_words", "max_words"})
_PATTERN_CHECKS = frozenset({"regex", "not_regex"})


class Check(BaseModel):
    """One deterministic assertion against the text of a worker's envelope."""

    model_config = ConfigDict(extra="forbid")

    type: CheckKind
    values: list[str] | None = None
    value: str | int | None = None
    # `field` in YAML reads better than `target`, but `field` shadows pydantic's own import here.
    target: TextTarget = Field(default="auto", alias="field")

    @model_validator(mode="after")
    def _require_matching_operand(self) -> Check:
        if self.type in _LIST_CHECKS:
            if not self.values:
                raise ValueError(f"check '{self.type}' requires a non-empty 'values' list")
        elif self.value is None:
            raise ValueError(f"check '{self.type}' requires a 'value'")

        if self.type in _INT_CHECKS and not isinstance(self.value, int):
            raise ValueError(f"check '{self.type}' requires an integer 'value'")

        if self.type in _PATTERN_CHECKS:
            if not isinstance(self.value, str):
                raise ValueError(f"check '{self.type}' requires a string 'value'")
            try:
                re.compile(self.value)
            except re.error as exc:
                raise ValueError(f"check '{self.type}' has an invalid pattern: {exc}") from exc

        return self

    def describe(self) -> str:
        operand = self.values if self.type in _LIST_CHECKS else self.value
        return f"{self.type}={operand!r}"


class Judge(BaseModel):
    """An explicit pass/fail rubric for an open-ended case."""

    model_config = ConfigDict(extra="forbid")

    rubric: str

    @field_validator("rubric")
    @classmethod
    def _require_substantive_rubric(cls, value: str) -> str:
        text = value.strip()
        if len(text) < 40:
            raise ValueError(
                "rubric is too short to be an explicit pass/fail standard; state what PASS "
                "requires and what FAILs"
            )
        if "pass" not in text.lower() or "fail" not in text.lower():
            raise ValueError("rubric must state both what PASSes and what FAILs")
        return text


class Expectation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: ExpectedStatus
    checks: list[Check] = Field(default_factory=list)
    judge: Judge | None = None

    @model_validator(mode="after")
    def _require_some_criterion(self) -> Expectation:
        if not self.checks and self.judge is None:
            raise ValueError("expectation must carry at least one check or a judge rubric")
        return self


class Case(BaseModel):
    """One golden-set case."""

    model_config = ConfigDict(extra="forbid")

    id: str
    goal: str
    tags: list[str] = Field(default_factory=list)
    rationale: str = ""
    expect: Expectation

    @field_validator("goal", mode="before")
    @classmethod
    def _expand_goal(cls, value: Any) -> Any:
        """Allow `{repeat: <text>, times: <n>}` so an over-length goal need not be inlined."""
        if isinstance(value, dict):
            unexpected = sorted(set(value) - {"repeat", "times"})
            if unexpected:
                raise ValueError(f"goal mapping has unexpected key(s): {', '.join(unexpected)}")
            try:
                text = str(value["repeat"])
                times = int(value["times"])
            except KeyError as exc:
                raise ValueError(f"goal mapping is missing {exc}") from exc
            if times < 1:
                raise ValueError("goal 'times' must be at least 1")
            return text * times
        return value

    @property
    def is_judged(self) -> bool:
        return self.expect.judge is not None


class CaseOutcome(BaseModel):
    """The verdict for one case in one run, with the reason it landed there."""

    model_config = ConfigDict(extra="forbid")

    case_id: str
    outcome: Outcome
    reason: str = ""
    tags: list[str] = Field(default_factory=list)
    status: str | None = None
    judged: bool = False
    judge_reason: str = ""
    output: str = ""
    elapsed_s: float = 0.0

    @property
    def passed(self) -> bool:
        return self.outcome == "pass"


class JudgeBackend(Protocol):
    """Just enough of `workers.model.ModelBackend` for the judge to be swappable in tests."""

    name: str

    def complete(self, *, system: str, user: str) -> str: ...


JUDGE_SYSTEM_PROMPT = """\
You are grading one output produced by an automated research worker. You are given the goal the
worker received, the exact envelope it returned, and a rubric. Apply the rubric as written.

Rules:
- The rubric is the only standard. Do not add criteria of your own, and do not reward or penalise
  style, length, tone, or formatting unless the rubric mentions it.
- Where the rubric lists numbered requirements for PASS, every one must hold.
- Where the rubric lists FAIL conditions, any one of them occurring means FAIL.
- Judge only what the output actually says. Do not assume unstated intent, and do not give credit
  for something the worker "probably meant".
- If the rubric does not settle the case, return FAIL and say which part of the rubric was
  indeterminate. Do not guess.

Respond with a single JSON object and nothing else. No prose before or after, no markdown fences:

{"verdict": "PASS", "reason": "<one sentence citing the rubric clause that decided it>"}

`verdict` is exactly "PASS" or "FAIL". `reason` is one sentence, under 200 characters, naming the
rubric clause that decided the outcome.\
"""


def build_judge_request(case: Case, result: WorkerResult) -> str:
    """Render the user turn for a judge call."""
    rubric = case.expect.judge.rubric if case.expect.judge else ""
    envelope = json.dumps(result.model_dump(), indent=2, ensure_ascii=False)
    return (
        f"## Goal given to the worker\n\n{case.goal.strip()}\n\n"
        f"## Envelope the worker returned\n\n{envelope}\n\n"
        f"## Rubric\n\n{rubric.strip()}\n"
    )


def parse_judge_verdict(raw: str) -> tuple[Outcome, str]:
    """Turn a judge's raw reply into an outcome. Unparseable replies become `error`, not `fail`."""
    text = _strip_code_fence(raw or "")
    if not text:
        return "error", "judge returned an empty reply"

    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        # Salvage the first JSON object in the reply; some models prepend a sentence.
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            return "error", f"judge reply was not JSON: {_clip(text, 160)}"
        try:
            payload = json.loads(match.group(0))
        except json.JSONDecodeError as exc:
            return "error", f"judge reply was not JSON: {exc}"

    if not isinstance(payload, dict):
        return "error", f"judge reply was {type(payload).__name__}, expected an object"

    verdict = str(payload.get("verdict", "")).strip().upper()
    reason = str(payload.get("reason", "")).strip()

    if verdict == "PASS":
        return "pass", reason or "judge returned PASS with no reason"
    if verdict == "FAIL":
        return "fail", reason or "judge returned FAIL with no reason"
    return "error", f"judge returned an unrecognised verdict {verdict!r}"


def score_case(
    case: Case,
    result: WorkerResult,
    *,
    judge: JudgeBackend | None = None,
) -> tuple[Outcome, str, str]:
    """Score one envelope. Returns `(outcome, reason, judge_reason)`.

    Status is checked first: when the worker took the wrong branch, the field the checks target is
    null, so running them would only produce misleading failures on top of the real one.
    """
    expected = case.expect.status
    if expected != "any" and result.status != expected:
        actual = _clip(_envelope_text(result, "auto", expected), 200)
        return "fail", f"expected status {expected!r}, got {result.status!r}: {actual}", ""

    for check in case.expect.checks:
        text = _envelope_text(result, check.target, expected)
        problem = _apply_check(check, text)
        if problem is not None:
            return "fail", problem, ""

    if case.expect.judge is None:
        return "pass", "all deterministic checks passed", ""

    if judge is None:
        return "error", "case requires a judge but none was configured", ""

    # The offline backend ignores its system prompt and returns a canned payload, so it cannot
    # apply a rubric. Letting it "judge" would manufacture verdicts that look real.
    if getattr(judge, "name", "") == "offline":
        return (
            "error",
            "judge unavailable: the offline backend cannot apply a rubric; set AGENTDASH_MODEL "
            "or --judge-backend to a hosted model",
            "",
        )

    try:
        raw = judge.complete(
            system=JUDGE_SYSTEM_PROMPT, user=build_judge_request(case, result)
        )
    except Exception as exc:
        return "error", f"judge call failed: {type(exc).__name__}: {exc}", ""

    if not isinstance(raw, str):
        return "error", f"judge returned {type(raw).__name__}, expected str", ""

    outcome, reason = parse_judge_verdict(raw)
    if outcome == "pass":
        return "pass", "judge returned PASS", reason
    if outcome == "fail":
        return "fail", f"judge returned FAIL: {reason}", reason
    return "error", reason, ""


def _apply_check(check: Check, text: str) -> str | None:
    """Return a failure reason, or `None` if the check holds."""
    haystack = text.lower()

    if check.type == "contains_all":
        missing = [v for v in (check.values or []) if v.lower() not in haystack]
        if missing:
            return f"missing required substring(s): {', '.join(repr(m) for m in missing)}"
        return None

    if check.type == "contains_any":
        if not any(v.lower() in haystack for v in (check.values or [])):
            wanted = ", ".join(repr(v) for v in (check.values or []))
            return f"contained none of: {wanted}"
        return None

    if check.type == "not_contains":
        present = [v for v in (check.values or []) if v.lower() in haystack]
        if present:
            return f"contained forbidden substring(s): {', '.join(repr(p) for p in present)}"
        return None

    if check.type == "regex":
        if not re.search(str(check.value), text, re.IGNORECASE | re.MULTILINE):
            return f"did not match pattern {check.value!r}"
        return None

    if check.type == "not_regex":
        match = re.search(str(check.value), text, re.IGNORECASE | re.MULTILINE)
        if match:
            return f"matched forbidden pattern {check.value!r} at {_clip(match.group(0), 60)!r}"
        return None

    words = len(text.split())
    if check.type == "min_words":
        if words < int(check.value or 0):
            return f"was {words} words, under the {check.value} minimum"
        return None

    if check.type == "max_words":
        if words > int(check.value or 0):
            return f"was {words} words, over the {check.value} maximum"
        return None

    return f"unknown check type {check.type!r}"


def _envelope_text(result: WorkerResult, target: TextTarget, expected: ExpectedStatus) -> str:
    """Pick the text a check runs against.

    `auto` follows the case's expected status, since that is the field the case is about. When the
    expectation is `any`, both fields are in play and are concatenated.
    """
    if target == "result":
        return result.result or ""
    if target == "error":
        return result.error or ""

    if expected == "ok":
        return result.result or ""
    if expected == "error":
        return result.error or ""
    return " ".join(part for part in (result.result, result.error) if part)


def _strip_code_fence(raw: str) -> str:
    text = raw.strip()
    if not text.startswith("```"):
        return text
    lines = text.splitlines()
    if len(lines) < 2:
        return text
    body = lines[1:-1] if lines[-1].strip().startswith("```") else lines[1:]
    return "\n".join(body).strip()


def _clip(text: str, limit: int) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else flat[: limit - 3] + "..."
