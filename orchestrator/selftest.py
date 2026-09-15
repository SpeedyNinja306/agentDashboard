"""Self-test for the multi-worker orchestrator: `python -m orchestrator.selftest`.

Ticket 5 (second-worker-generalization) makes two claims that the single-worker tests cannot
cover, and this script is the evidence for both. It needs no model and no credentials.

1. **Routing.** An explicit worker selection is honoured; an unrouted goal is routed by keyword;
   an unknown selection falls back rather than crashing.

2. **Concurrent isolation.** Two workers running at the same time do not contaminate each other's
   events or state. This is the claim the ticket's acceptance criterion names directly. The test
   forces genuine overlap with a two-party barrier inside the model backend: both runs must be
   inside their model call simultaneously for either to proceed, so a pass is proof the lanes ran
   concurrently, and a serialized queue would deadlock the barrier and fail loudly instead of
   passing by accident.

The dashboard's own event-to-state reduction is re-implemented here (`_reduce_worker_status`) and
asserted against the captured stream, so "the dashboard shows independent live status" is checked
against the same events the browser would receive rather than by eyeballing the UI.
"""

from __future__ import annotations

import asyncio
import json
import sys
import threading
from typing import Any

from orchestrator import events, router
from orchestrator.graph import build_graph, run_goal
from orchestrator.tasks import TaskQueue
from workers import registry
from workers import runner as runner_mod

_failures: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  ok    {label}")
    else:
        print(f"  FAIL  {label}  {detail}")
        _failures.append(label)


# ---------------------------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------------------------


def test_routing() -> None:
    print("routing")
    check(
        "explicit selection is honoured over the goal text",
        router.select_worker("what port does postgres use", requested="coding-agent")
        == "coding-agent",
    )
    check(
        "a coding goal routes to coding-agent",
        router.route("Write a Python function to parse a CSV file") == "coding-agent",
    )
    check(
        "a research goal routes to research-specialist",
        router.route("Compare the tradeoffs of REST and gRPC") == "research-specialist",
    )
    check(
        "an unrouted goal falls to the default worker",
        router.route("tell me about the history of Rome") == registry.DEFAULT_WORKER,
    )
    check(
        "an unknown explicit selection falls back instead of raising",
        router.select_worker("write a python script", requested="ghost") == "coding-agent",
    )


# ---------------------------------------------------------------------------------------------
# A backend that makes two concurrent runs provably overlap
# ---------------------------------------------------------------------------------------------


class RendezvousBackend:
    """Returns a valid envelope, but only once every party has entered `complete`.

    The barrier is the whole point: with N=2, neither run can leave its model call until both are
    inside it, so the two runs are guaranteed to be in flight at the same instant. A queue that
    ran them one after another would leave the first run waiting on a party that never comes, the
    barrier would break on timeout, and the run would surface as an error — turning "was it really
    concurrent?" from a judgement call into a pass/fail.
    """

    name = "rendezvous"

    def __init__(self, barrier: threading.Barrier) -> None:
        self._barrier = barrier

    def complete(self, *, system: str, user: str) -> str:
        try:
            self._barrier.wait(timeout=5)
        except threading.BrokenBarrierError:
            return json.dumps(
                {"status": "error", "result": None, "error": "rendezvous timed out (serialized)"}
            )
        # Echo the goal back so the captured events prove which goal this worker actually handled.
        return json.dumps(
            {"status": "ok", "result": f"handled goal: {user.strip()[:60]}", "error": None}
        )


# ---------------------------------------------------------------------------------------------
# Concurrent isolation
# ---------------------------------------------------------------------------------------------

#: Mirrors dashboard/src/hooks/useWorkerEvents.ts applyEvent, so the state the browser would
#: compute from these events is what gets asserted.
def _reduce_worker_status(events_for_worker: list[dict[str, Any]]) -> str:
    status = "idle"
    for event in events_for_worker:
        et = event["event_type"]
        if et == "worker_spawned":
            status = "running"
        elif et == "completed":
            status = "idle"
        elif et == "error":
            status = "error"
    return status


def test_concurrent_isolation() -> None:
    print("concurrent isolation")

    goals = {
        "research-specialist": "What TCP port does PostgreSQL listen on by default?",
        "coding-agent": "Write a Python function that reverses a string.",
    }

    captured: list[dict[str, Any]] = []
    cap_lock = threading.Lock()

    def sink(event: dict[str, Any]) -> None:
        with cap_lock:
            captured.append(dict(event))

    barrier = threading.Barrier(len(goals))
    backend = RendezvousBackend(barrier)

    # Both runs resolve to the rendezvous backend regardless of AGENTDASH_MODEL. Patched on the
    # runner, which is where the name is bound.
    original_resolve = runner_mod.resolve_backend
    runner_mod.resolve_backend = lambda spec=None: backend  # type: ignore[assignment]
    previous_sink = events.set_sink(sink)

    # The compiled graph is a shared singleton; clear it so this run also exercises building it,
    # then it is invoked concurrently from two threads below.
    build_graph.cache_clear()

    try:
        results = asyncio.run(_run_two_concurrently(goals))
    finally:
        events.set_sink(previous_sink)
        runner_mod.resolve_backend = original_resolve  # type: ignore[assignment]

    # -- both actually ran, and the barrier proves they overlapped --------------------------
    for worker, record in results.items():
        ok = record is not None and record.status == "done"
        detail = "" if ok else f"record={record.to_dict() if record else None}"
        check(f"{worker} task completed (status done)", ok, detail)

    # -- events partition cleanly by worker; none carry another worker's identity -----------
    by_worker: dict[str, list[dict[str, Any]]] = {w: [] for w in goals}
    orchestrator_events: list[dict[str, Any]] = []
    for event in captured:
        name = event["agent_name"]
        if name in by_worker:
            by_worker[name].append(event)
        elif name == events.ORCHESTRATOR_NAME:
            orchestrator_events.append(event)

    for worker in goals:
        evs = by_worker[worker]
        check(f"{worker} emitted its own lifecycle events", len(evs) >= 3)

        worker_ids = {e["agent_id"] for e in evs}
        check(
            f"{worker}'s events all share one worker agent_id",
            len(worker_ids) == 1,
            f"ids={worker_ids}",
        )

        # The goal each worker actually processed, read back from its tool_call_start preview.
        previews = [
            e["payload"].get("goal_preview", "")
            for e in evs
            if e["event_type"] == "tool_call_start"
        ]
        check(
            f"{worker} processed only its own goal (no goal bleed across threads)",
            all(p and p in goals[worker] for p in previews),
            f"previews={previews}",
        )

    # The two workers must have distinct agent_ids: a shared id would mean the contextvar leaked.
    research_ids = {e["agent_id"] for e in by_worker["research-specialist"]}
    coding_ids = {e["agent_id"] for e in by_worker["coding-agent"]}
    check(
        "the two workers have disjoint agent_ids",
        research_ids.isdisjoint(coding_ids),
        f"research={research_ids} coding={coding_ids}",
    )

    # -- dispatch decisions are correctly attributed to each run's task_id -------------------
    dispatch_ends = [e for e in orchestrator_events if e["event_type"] == "dispatch_end"]
    selected_by_task = {
        e["agent_id"]: e["payload"].get("selected_worker") for e in dispatch_ends
    }
    for worker, record in results.items():
        tid = record.task_id if record else None
        check(
            f"dispatch for the {worker} task selected {worker}",
            tid is not None and selected_by_task.get(tid) == worker,
            f"selected_by_task={selected_by_task}",
        )

    # -- the dashboard would show each node idle again, independently ------------------------
    for worker in goals:
        final = _reduce_worker_status(by_worker[worker])
        check(f"dashboard state for {worker} settles back to idle", final == "idle", final)


async def _run_two_concurrently(goals: dict[str, str]) -> dict[str, Any]:
    queue = TaskQueue(run_goal, workers=registry.worker_names())
    queue.start()
    records = {worker: queue.submit(goal, worker) for worker, goal in goals.items()}

    async def _await_done() -> None:
        while any(r.status not in ("done", "error") for r in records.values()):
            await asyncio.sleep(0.01)

    try:
        await asyncio.wait_for(_await_done(), timeout=15)
    finally:
        await queue.stop()

    return {worker: queue.get(record.task_id) for worker, record in records.items()}


def main() -> int:
    for test in (test_routing, test_concurrent_isolation):
        test()
        print()

    if _failures:
        print(f"SELFTEST FAILED: {len(_failures)} check(s) failed")
        for label in _failures:
            print(f"  - {label}")
        return 1

    print("SELFTEST PASSED: routing and concurrent isolation behave as specified")
    return 0


if __name__ == "__main__":
    sys.exit(main())
