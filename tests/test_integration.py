"""Exercises VSLToolMiddleware against the real langchain/langgraph types
(ToolCallRequest, ToolMessage), not mocks -- same discipline as
vsl-langgraph's test_integration.py.
"""

from typing import Any

from langgraph.prebuilt.tool_node import ToolCallRequest
from vsl_core.constructs import Invariant, PreNode
from vsl_core.exceptions import InvariantViolation
from vsl_core.metrics import GammaEstimate

from vsl_langchain import LangChainAdapter, VSLToolMiddleware

_HARD_CAP = 1000.0


def _make_request(name: str, args: dict[str, Any], call_id: str = "call_1") -> ToolCallRequest:
    return ToolCallRequest(
        tool_call={"name": name, "args": args, "id": call_id, "type": "tool_call"},
        tool=None,
        state={},
        runtime=None,
    )


async def _under_hard_cap(candidate_input: Any) -> GammaEstimate:
    amount = candidate_input["args"].get("amount", 0.0)
    return GammaEstimate(gamma_hat=2.0 if amount <= _HARD_CAP else 0.0)


async def _refund_amount_ok(candidate_input: Any) -> bool:
    amount = candidate_input["args"].get("amount", 0.0)
    return amount <= _HARD_CAP


async def _handler_records_call(calls: list[str]):
    async def handler(request: ToolCallRequest):
        calls.append(request.tool_call["name"])
        return f"executed {request.tool_call['name']}"

    return handler


def _build_pre_node_middleware() -> VSLToolMiddleware:
    adapter = LangChainAdapter()
    pre_node = PreNode(
        name="refund-amount-pre-node",
        description="Requires refund amount under the hard cap before send_refund runs.",
        monitor=_under_hard_cap,
    )
    gate = adapter.compile_pre_node(pre_node)
    return VSLToolMiddleware(gates_by_tool_name={"send_refund": gate})


def _build_invariant_middleware() -> VSLToolMiddleware:
    adapter = LangChainAdapter()
    invariant = Invariant(
        name="refund-amount-invariant",
        description="Refund amount must never exceed the hard cap -- non-bypassable.",
        rule=_refund_amount_ok,
    )
    gate = adapter.compile_invariant(invariant)
    return VSLToolMiddleware(gates_by_tool_name={"send_refund": gate})


async def test_pre_node_allows_call_under_cap_and_handler_actually_runs():
    calls: list[str] = []
    middleware = _build_pre_node_middleware()
    handler = await _handler_records_call(calls)

    request = _make_request("send_refund", {"amount": 250.0})
    result = await middleware.awrap_tool_call(request, handler)

    assert calls == ["send_refund"]
    assert result == "executed send_refund"


async def test_pre_node_denies_call_over_cap_and_handler_never_runs():
    calls: list[str] = []
    middleware = _build_pre_node_middleware()
    handler = await _handler_records_call(calls)

    request = _make_request("send_refund", {"amount": 5000.0}, call_id="call_2")
    result = await middleware.awrap_tool_call(request, handler)

    # The real proof of F1 pre-commitment: handler was never called at all.
    assert calls == []
    assert result.tool_call_id == "call_2"
    assert result.status == "error"
    assert "Denied by VSL governance" in result.content


async def test_invariant_denies_call_over_cap_and_handler_never_runs():
    calls: list[str] = []
    middleware = _build_invariant_middleware()
    handler = await _handler_records_call(calls)

    request = _make_request("send_refund", {"amount": 5000.0}, call_id="call_3")
    result = await middleware.awrap_tool_call(request, handler)

    assert calls == []
    assert result.status == "error"


async def test_ungated_tool_passes_straight_through():
    calls: list[str] = []
    middleware = _build_pre_node_middleware()  # only gates "send_refund"
    handler = await _handler_records_call(calls)

    request = _make_request("lookup_order", {"order_id": "ORD-1"})
    result = await middleware.awrap_tool_call(request, handler)

    assert calls == ["lookup_order"]
    assert result == "executed lookup_order"


import pytest  # noqa: E402

from vsl_core.ledger import Enforcement, VerbaLedger  # noqa: E402


@pytest.mark.parametrize("amount", [250.0, 5000.0])
async def test_ledger_shows_the_gate_held_before_the_tool(amount):
    ledger = VerbaLedger()

    async def rule(candidate_input: Any) -> bool:
        allowed = candidate_input["args"]["amount"] <= _HARD_CAP
        ledger.write_gate_decision(identity_key="refund-agent", primitive_name="refund-cap",
                                   allowed=allowed, decision_id="d1")
        return allowed

    calls: list[str] = []

    async def handler(request: ToolCallRequest):
        ledger.write_action(identity_key="refund-agent", action_name="send_refund", decision_id="d1")
        calls.append(request.tool_call["name"])
        return "executed send_refund"

    gate = LangChainAdapter().compile_invariant(Invariant(name="refund-cap", description="d", rule=rule))
    middleware = VSLToolMiddleware(gates_by_tool_name={"send_refund": gate})
    await middleware.awrap_tool_call(_make_request("send_refund", {"amount": amount}), handler)

    assert (calls == ["send_refund"]) is (amount <= _HARD_CAP)
    assert ledger.enforcement("d1") == Enforcement.BEFORE_ACTION


def test_compiling_gates_without_a_basis_warns_about_nothing():
    import warnings

    with warnings.catch_warnings():
        warnings.simplefilter("error")
        LangChainAdapter().compile_invariant(Invariant(name="i", description="d", rule=_refund_amount_ok))
        LangChainAdapter().compile_pre_node(PreNode(name="p", description="d", monitor=_under_hard_cap))
