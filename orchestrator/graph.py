"""The single-worker orchestrator loop, as a LangGraph `StateGraph`.

    START -> dispatch -> research_specialist -> finalize -> END

`dispatch` is where worker selection will live once there is more than one worker; today it
hardcodes research-specialist. See specs/orchestrator-single-worker-loop/design.md for why this
is a plain `StateGraph` rather than `create_supervisor`.
"""

from __future__ import annotations

import functools
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from orchestrator import events
from workers import research_specialist
from workers.contracts import WorkerResult, failure

ONLY_WORKER = research_specialist.WORKER_NAME


class OrchestratorState(TypedDict, total=False):
    goal: str
    worker: str
    result: WorkerResult


def _dispatch(state: OrchestratorState) -> dict[str, Any]:
    oid = events.orchestrator_id()
    events.emit("dispatch_start", oid, events.ORCHESTRATOR_NAME, {"goal": state.get("goal", "")})
    selected = ONLY_WORKER
    events.emit("dispatch_end", oid, events.ORCHESTRATOR_NAME, {"selected_worker": selected})
    return {"worker": selected}


def _run_worker(state: OrchestratorState) -> dict[str, Any]:
    worker_name = state.get("worker", ONLY_WORKER)
    wid = events.spawn_worker()
    events.emit("worker_spawned", wid, worker_name, {"goal": state.get("goal", "")})

    result = research_specialist.run(state.get("goal", ""))

    if result.status == "ok":
        preview = (result.result or "")[:120]
        events.emit("completed", wid, worker_name, {"status": "ok", "result_preview": preview})
    else:
        events.emit("error", wid, worker_name, {"error": result.error})

    return {"result": result}


def _finalize(state: OrchestratorState) -> dict[str, Any]:
    """Guarantee the graph's output carries a valid envelope even if a node skipped one."""
    result = state.get("result")
    if isinstance(result, WorkerResult):
        return {"result": result}
    return {"result": failure(f"{state.get('worker', ONLY_WORKER)} produced no result")}


@functools.lru_cache(maxsize=1)
def build_graph() -> Any:
    """Compile the loop once and reuse it; the graph is stateless between invocations."""
    builder = StateGraph(OrchestratorState)
    builder.add_node("dispatch", _dispatch)
    builder.add_node(ONLY_WORKER, _run_worker)
    builder.add_node("finalize", _finalize)

    builder.add_edge(START, "dispatch")
    builder.add_edge("dispatch", ONLY_WORKER)
    builder.add_edge(ONLY_WORKER, "finalize")
    builder.add_edge("finalize", END)

    return builder.compile()


def run_goal(goal: str) -> WorkerResult:
    """Run `goal` through the loop. Returns an envelope for every outcome, including graph faults."""
    events.start_run()
    try:
        final_state = build_graph().invoke({"goal": goal})
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
