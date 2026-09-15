"""The orchestrator loop, as a LangGraph `StateGraph`.

    START -> dispatch -> <selected worker> -> finalize -> END

`dispatch` chooses the worker (see `orchestrator.router`) and a conditional edge sends the run to
that worker's node. Each registered worker is its own node, so a run touches exactly one worker
and the others stay untouched — the graph-level half of the "no cross-contamination" guarantee the
concurrency test checks at the event level.

This is still LangGraph and still the orchestrator-worker pattern. It is not `create_supervisor`;
see `specs/second-worker-generalization/design.md` for why routing is deterministic here and what
would move it to a supervisor.
"""

from __future__ import annotations

import functools
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from orchestrator import events, router
from workers import registry
from workers.contracts import WorkerResult, failure


class OrchestratorState(TypedDict, total=False):
    goal: str
    #: An explicit worker the caller asked for, if any. Honoured over keyword routing.
    requested_worker: str
    #: The worker dispatch actually selected; set by the dispatch node, read by the router edge.
    worker: str
    result: WorkerResult


def _dispatch(state: OrchestratorState) -> dict[str, Any]:
    oid = events.orchestrator_id()
    goal = state.get("goal", "")
    requested = state.get("requested_worker")
    events.emit(
        "dispatch_start",
        oid,
        events.ORCHESTRATOR_NAME,
        {"goal": goal, "requested_worker": requested},
    )
    selected = router.select_worker(goal, requested=requested)
    events.emit("dispatch_end", oid, events.ORCHESTRATOR_NAME, {"selected_worker": selected})
    return {"worker": selected}


def _make_worker_node(worker_name: str) -> Any:
    """Build the node that runs one specific worker.

    Binding the name per node (rather than reading it from state inside one shared node) is what
    makes each worker a distinct vertex in the graph, so the run's path names the worker it used.
    """

    def _run(state: OrchestratorState) -> dict[str, Any]:
        wid = events.spawn_worker()
        goal = state.get("goal", "")
        events.emit("worker_spawned", wid, worker_name, {"goal": goal})

        result = registry.run_worker(worker_name, goal)

        if result.status == "ok":
            preview = (result.result or "")[:120]
            events.emit("completed", wid, worker_name, {"status": "ok", "result_preview": preview})
        else:
            events.emit("error", wid, worker_name, {"error": result.error})

        return {"result": result}

    return _run


def _route_to_worker(state: OrchestratorState) -> str:
    """Conditional-edge selector: the node name equals the worker name dispatch chose."""
    selected = state.get("worker", registry.DEFAULT_WORKER)
    return selected if registry.is_known(selected) else registry.DEFAULT_WORKER


def _finalize(state: OrchestratorState) -> dict[str, Any]:
    """Guarantee the graph's output carries a valid envelope even if a node skipped one."""
    result = state.get("result")
    if isinstance(result, WorkerResult):
        return {"result": result}
    return {"result": failure(f"{state.get('worker', '?')} produced no result")}


@functools.lru_cache(maxsize=1)
def build_graph() -> Any:
    """Compile the loop once and reuse it; the graph is stateless between invocations."""
    builder = StateGraph(OrchestratorState)
    builder.add_node("dispatch", _dispatch)
    builder.add_node("finalize", _finalize)

    names = registry.worker_names()
    for name in names:
        builder.add_node(name, _make_worker_node(name))

    builder.add_edge(START, "dispatch")
    builder.add_conditional_edges("dispatch", _route_to_worker, {name: name for name in names})
    for name in names:
        builder.add_edge(name, "finalize")
    builder.add_edge("finalize", END)

    return builder.compile()


def run_goal(
    goal: str, *, run_id: str | None = None, worker: str | None = None
) -> WorkerResult:
    """Run `goal` through the loop. Returns an envelope for every outcome, including graph faults.

    `run_id` becomes the orchestrator's agent_id for this run; the server passes its task_id so
    clients can tie a stream of events back to the task that produced them. `worker` names an
    explicit worker to honour over keyword routing.
    """
    events.start_run(run_id)
    initial: dict[str, Any] = {"goal": goal}
    if worker:
        initial["requested_worker"] = worker
    try:
        final_state = build_graph().invoke(initial)
    except Exception as exc:
        oid = events.orchestrator_id()
        events.emit(
            "error",
            oid,
            events.ORCHESTRATOR_NAME,
            {"error": f"orchestrator graph failed with {type(exc).__name__}: {exc}"},
        )
        return failure(f"orchestrator graph failed with {type(exc).__name__}: {exc}")

    result = final_state.get("result") if isinstance(final_state, dict) else None
    if isinstance(result, WorkerResult):
        return result
    oid = events.orchestrator_id()
    events.emit(
        "error",
        oid,
        events.ORCHESTRATOR_NAME,
        {"error": "orchestrator graph returned no result envelope"},
    )
    return failure("orchestrator graph returned no result envelope")
