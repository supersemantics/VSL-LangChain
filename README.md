# vsl-langchain

A [LangChain](https://github.com/langchain-ai/langchain) adapter for
[`vsl-core`](https://github.com/supersemantics/VSL-Core): implements
`vsl_core.conformance.protocol.VSLAdapter` and gates tool calls inside a
`create_agent()` agent via `AgentMiddleware.wrap_tool_call`.

**Is:**
- A `VSLAdapter` implementation (`LangChainAdapter`) that passes `vsl_core.conformance.suite.run_conformance_suite` with an empty result.
- `VSLToolMiddleware`: a real `langchain.agents.middleware.types.AgentMiddleware` subclass that gates named tools, keyed by tool name, inside `create_agent`'s middleware stack.

**Is not:**
- A replacement for `vsl-core` itself — the `PreNode`/`Invariant`/`Fallback`/`TerminalState` constructs, the ledger, and the governance vocabulary all live in `vsl-core`. This package only compiles and wires those constructs into LangChain.
- Compatible with the deprecated `AgentExecutor`/`initialize_agent`/`create_react_agent` APIs. Those predate the middleware system this adapter depends on — this package targets `create_agent` (LangChain >= 1.0) only.

## Why `wrap_tool_call`, and why it's a real gate — not an assumption

`AgentMiddleware.wrap_tool_call`/`awrap_tool_call` is LangChain's own sanctioned interception point for tool execution. This was verified directly against the installed package's own docstring (`langchain==1.3.17`, not just documentation prose), which states outright:

> "Middleware can call the handler multiple times for retry logic, skip calling it to short-circuit, or modify the request/response."

That is a genuine F1 pre-commitment guarantee: the middleware runs strictly before the tool executes, and structurally controls whether the real call (`handler`) ever happens at all. If a gate denies, `handler` is never called — the tool genuinely never runs, proven directly in this repo's own tests (`test_integration.py` asserts the handler's call log stays empty on denial, not just that a particular return value came back).

This supersedes an earlier, weaker finding that LangChain's callback system (`on_tool_start`, etc.) had unconfirmed blocking behavior — `wrap_tool_call` is a different, confirmed mechanism, not the same uncertain one.

## Install

```bash
pip install vsl-langchain
```

This installs vsl-core (`super-semantics-vsl`, imported as `vsl_core`) and LangChain with it.

## Quick taste

```python
from langchain.agents import create_agent
from vsl_core.constructs import PreNode
from vsl_core.metrics import GammaEstimate
from vsl_langchain import LangChainAdapter, VSLToolMiddleware

adapter = LangChainAdapter()

async def monitor(candidate_input):
    amount = candidate_input["args"].get("amount", 0.0)
    return GammaEstimate(gamma_hat=2.0 if amount <= 1000.0 else 0.0)

pre_node = PreNode(
    name="refund-cap",
    description="Requires refund amount under the hard cap.",
    monitor=monitor,
)
gate = adapter.compile_pre_node(pre_node)

middleware = VSLToolMiddleware(gates_by_tool_name={"send_refund": gate})

agent = create_agent(
    model="anthropic:claude-sonnet-5",
    tools=[send_refund, lookup_order],  # lookup_order stays ungated -- no entry in gates_by_tool_name
    middleware=[middleware],
)
```

## Sync and async agent runs

`VSLToolMiddleware` implements both of LangChain's tool-call hooks, so it works however the agent is
run: `awrap_tool_call` for `ainvoke`/`astream`, `wrap_tool_call` for `invoke`/`stream`. The gates are
the same async callables either way. In a sync run they execute on one private event loop thread,
started on first use, so the sync hook also works on a thread that already has a running loop (a
sync run started from a notebook or an async web handler).

## Next to AGT's LangChain middleware

Microsoft's Agent Governance Toolkit governs LangChain with its own `create_agent` middleware,
`LangChainKernel(...).as_middleware()`. Both go in the same list, and both must allow before the tool
runs:

```python
agent = create_agent(
    model=model,
    tools=[send_refund, lookup_order],
    middleware=[kernel.as_middleware(), VSLToolMiddleware(gates_by_tool_name={"send_refund": gate})],
)
agent.invoke({"messages": [...]})
```

AGT's middleware (in `agent-governance-toolkit` 4.1 and `-core` 5.0) implements only the sync hooks,
so run the agent with `invoke`; with `ainvoke`, LangChain refuses AGT's middleware before any tool
runs. AGT's middleware also needs AGT's `agt-policies` runtime installed. This repo's tests run
`VSLToolMiddleware` in a sync `create_agent` loop next to a sync-only middleware that stands in for
AGT's, in both orders. For AGT's own interceptor chain and Agent Hooks, see
[vsl-agt](https://github.com/supersemantics/agent-governance-toolkit).

## `VSLToolMiddleware` doesn't consume `Fallback`

A `PreNode`'s `Fallback` (`on_failure`, `delta_factor`, `max_retries`, `on_max_retries`) is policy data only here — `LangChainAdapter.compile_pre_node`/`compile_invariant` never read `pre_node.fallback`. On denial, `VSLToolMiddleware` returns an error `ToolMessage` once, immediately; there is no retry loop. This is the same inertness `vsl-langgraph`'s `LangGraphAdapter` has — `vsl-core`'s own `PlainPythonReferenceAdapter` doesn't consume `Fallback` either, so no adapter currently executes these fields as behavior.

## `VSLToolMiddleware` doesn't write to the ledger either

Nothing in `VSLToolMiddleware` calls `VerbaLedger.write*` — a gate denial becomes an error `ToolMessage`, and that's it. If you want an audit trail of gate decisions, your own code (e.g. wrapping `VSLToolMiddleware` construction, or a separate middleware) has to call `ledger.write_monitor`/`ledger.write(LedgerEntryType.PRE_NODE, ...)`/`write_verification` explicitly, causally linked via `caused_by` — the same three entry types `vsl-core`'s own [building guide](https://github.com/supersemantics/VSL-Core/blob/main/docs/building-with-vsl-core.md#step-3-the-five-things-youll-actually-write) says to write around every gated call. Skip `PRE_NODE` and `VerbaLedger.audit()`'s `drift_flagged_monitor_has_pre_node` check fails on any denial — not a bug in `audit()`, just an incomplete write. An agent with no ledger calls anywhere produces real, working gating and zero audit trail — this package won't warn you, because from its point of view every gate did exactly what it was compiled to do.

To record the tool call itself, call `ledger.write_action(identity_key=..., action_name=..., decision_id=...)` inside the tool, with the same `decision_id` as the gate's `write_gate_decision`. `ledger.enforcement(decision_id)` then reads `BEFORE_ACTION` when every gate decision came before the tool ran (or a denial stopped it), and `AFTER_ACTION` when the tool ran first.

Where those writes are kept is up to you: `VerbaLedger` accepts any `LedgerStore`, and vsl-core includes `InMemoryLedgerStore`, `JsonlLedgerStore` (a local append-only file) and an optional immudb store.

## What a gate reports

Gates take no assurance claim: build a `PreNode` or `Invariant` from its name, description and monitor or rule. Whether a gate ran before its tool is computed from the ledger, not declared (see [vsl-core's "What a gate reports"](https://github.com/supersemantics/VSL-Core#what-a-gate-reports)). Code written for vsl-langchain 0.1 that still passes `assurance_basis` gets a `FutureWarning` from vsl-core; delete the argument.

## Status

Alpha. Conformance-suite green. Integration-tested against the real `ToolCallRequest`/`ToolMessage` types from the actually-installed `langchain`/`langgraph` packages (not mocks) — covering: `PreNode` allow/deny, `Invariant` allow/deny, and an ungated tool passing straight through. Also tested through a full `create_agent()` loop with a scripted chat model (no network), run both with `invoke` and `ainvoke`, and next to a sync-only middleware in both orders. Not yet tested with a live model service.

## Layout

```
src/vsl_langchain/
└── adapter.py   LangChainAdapter (VSLAdapter conformance contract), VSLToolMiddleware (create_agent wiring)
tests/
├── test_conformance.py   run_conformance_suite(LangChainAdapter()) == []
├── test_integration.py   real ToolCallRequest/ToolMessage, PreNode + Invariant, allow/deny/ungated paths
└── test_sync.py          create_agent with invoke and ainvoke; next to a sync-only middleware
```
