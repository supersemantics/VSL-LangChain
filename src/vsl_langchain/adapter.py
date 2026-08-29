"""LangChainAdapter + VSLToolMiddleware: a VSLAdapter conformant with
vsl-core's conformance contract, plus the LangChain-specific wiring to gate
tool calls inside a create_agent() agent via AgentMiddleware.wrap_tool_call.

wrap_tool_call is a confirmed F1 pre-commitment interception point --
verified directly against langchain's actual source
(langchain/agents/middleware/types.py) and langgraph's ToolCallRequest
(langgraph/prebuilt/tool_node.py), not assumed from docs prose. The
middleware receives the ToolCallRequest strictly before the tool executes,
and structurally controls whether the real call (`handler`) ever runs --
the framework's own docstring says outright you can "skip calling it to
short-circuit". This is the same F1 property vsl-langgraph's LangGraphAdapter
relies on, just reached through create_agent's own sanctioned middleware
hook instead of a hand-wrapped node function.

The compiled gate itself is the same framework-agnostic async callable
vsl-core's own PlainPythonReferenceAdapter produces -- LangChainAdapter's
compile_pre_node/compile_invariant are intentionally near-identical to
vsl-langgraph's LangGraphAdapter for that reason. What this package adds on
top is VSLToolMiddleware: the tool-name-keyed wiring for create_agent's
middleware list, and the ToolMessage-shaped denial response create_agent
actually expects back from wrap_tool_call.

Requires langchain>=1.0's create_agent API (AgentMiddleware.wrap_tool_call).
Does not support the deprecated AgentExecutor/create_react_agent path --
that API predates the middleware system this adapter depends on.
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable, Mapping

from langchain.agents.middleware.types import AgentMiddleware
from langchain_core.messages import ToolMessage
from vsl_core.constructs import Invariant, PreNode
from vsl_core.conformance.protocol import CompiledGate
from vsl_core.exceptions import AutomationDeniedException, InvariantViolation

DEFAULT_DENIAL_KEY = "vsl_denial"


class LangChainAdapter:
    """VSLAdapter implementation. Gate-compilation logic is intentionally
    identical in shape to vsl-langgraph's LangGraphAdapter -- the compiled
    gate is framework-agnostic (vsl_core.conformance.protocol.CompiledGate);
    only the wiring around it (VSLToolMiddleware, below) is LangChain-specific.
    """

    def compile_pre_node(self, pre_node: PreNode) -> CompiledGate:
        async def gate(candidate_input: Any) -> None:
            estimate = await pre_node.monitor(candidate_input)
            if not estimate.sufficient(pre_node.gamma_threshold):
                raise AutomationDeniedException(
                    reason=f"Pre-node '{pre_node.name}' insufficient Gamma",
                    identity_key="vsl-langchain",
                )

        return gate

    def compile_invariant(self, invariant: Invariant) -> CompiledGate:
        async def gate(candidate_input: Any) -> None:
            holds = await invariant.rule(candidate_input)
            if not holds:
                reason = f"Invariant '{invariant.name}' violated"
                terminal_state_name = invariant.on_violation.name if invariant.on_violation is not None else None
                if terminal_state_name is not None:
                    reason += f" -- entering terminal state '{terminal_state_name}'"
                raise InvariantViolation(
                    reason=reason,
                    identity_key="vsl-langchain",
                    invariant_name=invariant.name,
                    terminal_state_name=terminal_state_name,
                )

        return gate


class VSLToolMiddleware(AgentMiddleware):
    """Gates specific tools inside a create_agent() agent, via
    AgentMiddleware.awrap_tool_call.

    `gates_by_tool_name` maps a tool's name (matched against
    request.tool_call["name"]) to a CompiledGate produced by
    LangChainAdapter.compile_pre_node/compile_invariant. Tools with no
    entry in this mapping are ungated -- they pass straight to `handler`,
    matching vsl-core's own "not every action needs a gate" posture (a
    read-only lookup tool, for example, shouldn't be gated at all).

    `candidate_input_fn` extracts whatever the gate should evaluate from the
    ToolCallRequest; defaults to passing the request's tool_call dict
    (name/args/id) through unchanged, matching vsl-langgraph's gated_node
    default of passing the whole state through.

    On denial, returns a ToolMessage (status="error") carrying the raised
    exception's reason instead of letting it propagate -- the tool call
    itself never fires, since `handler` is never called. This mirrors
    gated_node's convention (denial becomes a state update, not a crash)
    adapted to what create_agent's tool-call loop actually expects back:
    a ToolMessage, not a raised exception.
    """

    def __init__(
        self,
        gates_by_tool_name: Mapping[str, CompiledGate],
        *,
        candidate_input_fn: Callable[[Any], Any] | None = None,
        denial_key: str = DEFAULT_DENIAL_KEY,
    ) -> None:
        self._gates = dict(gates_by_tool_name)
        self._extract = candidate_input_fn or (lambda request: request.tool_call)
        self._denial_key = denial_key

    async def awrap_tool_call(
        self,
        request: Any,
        handler: Callable[[Any], Awaitable[ToolMessage | Any]],
    ) -> ToolMessage | Any:
        tool_name = request.tool_call["name"]
        gate = self._gates.get(tool_name)
        if gate is None:
            return await handler(request)

        try:
            await gate(self._extract(request))
        except AutomationDeniedException as exc:
            return ToolMessage(
                content=f"Denied by VSL governance: {exc}",
                tool_call_id=request.tool_call["id"],
                status="error",
            )

        return await handler(request)
