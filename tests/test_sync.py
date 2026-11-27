"""VSLToolMiddleware in synchronous agent runs (invoke), and next to a
sync-only middleware such as AGT's LangChain GovernanceMiddleware. A real
create_agent with a scripted chat model: no network, no API key."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from langchain.agents import create_agent
from langchain.agents.middleware.types import AgentMiddleware
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import tool
from langgraph.prebuilt.tool_node import ToolCallRequest
from vsl_core.constructs import Invariant, PreNode
from vsl_core.metrics import GammaEstimate

from vsl_langchain import LangChainAdapter, VSLToolMiddleware

HARD_CAP = 1000.0
ran: list[float] = []


@tool
def issue_refund(amount: float) -> str:
    """Refund a customer."""
    ran.append(amount)
    return f"refunded {amount}"


class ScriptedModel(GenericFakeChatModel):
    """Asks for one issue_refund call, then answers."""

    def bind_tools(self, tools: Any, **kwargs: Any) -> "ScriptedModel":
        return self


def scripted(amount: float) -> ScriptedModel:
    return ScriptedModel(messages=iter([
        AIMessage(content="", tool_calls=[{"name": "issue_refund", "args": {"amount": amount}, "id": "c1", "type": "tool_call"}]),
        AIMessage(content="done"),
    ]))


async def within_cap(candidate: Any) -> bool:
    return 0 < candidate["args"]["amount"] <= HARD_CAP


async def budget_headroom(candidate: Any) -> GammaEstimate:
    amount = candidate["args"]["amount"]
    return GammaEstimate(gamma_hat=500.0 / amount if amount > 0 else 0.0)


def vsl_middleware() -> VSLToolMiddleware:
    return VSLToolMiddleware(gates_by_tool_name={
        "issue_refund": LangChainAdapter().compile_invariant(Invariant(name="refund-cap", description="d", rule=within_cap)),
    })


class SyncOnlyPolicy(AgentMiddleware):
    """Stands in for AGT's LangChain GovernanceMiddleware, which implements
    only the sync wrap_tool_call and blocks by raising."""

    def __init__(self, blocked: set[str] | None = None) -> None:
        self.blocked = blocked or set()
        self.seen: list[str] = []

    def wrap_tool_call(self, request: Any, handler: Any) -> Any:
        name = request.tool_call["name"]
        self.seen.append(name)
        if name in self.blocked:
            raise PermissionError(f"policy blocks {name}")
        return handler(request)


def tool_messages(result: dict) -> list[ToolMessage]:
    return [m for m in result["messages"] if isinstance(m, ToolMessage)]


@pytest.fixture(autouse=True)
def clear_ran():
    ran.clear()


# ---- a sync run (invoke) ---------------------------------------------------------------------

def test_sync_run_allowed_call_runs_the_tool():
    agent = create_agent(model=scripted(120.0), tools=[issue_refund], middleware=[vsl_middleware()])
    result = agent.invoke({"messages": [("user", "refund 120")]})
    assert ran == [120.0]
    assert tool_messages(result)[-1].status == "success"


def test_sync_run_denied_call_never_runs_the_tool():
    agent = create_agent(model=scripted(5000.0), tools=[issue_refund], middleware=[vsl_middleware()])
    result = agent.invoke({"messages": [("user", "refund 5000")]})
    assert ran == []
    message = tool_messages(result)[-1]
    assert message.status == "error" and "refund-cap" in message.content


def test_async_run_still_works_the_same():
    agent = create_agent(model=scripted(5000.0), tools=[issue_refund], middleware=[vsl_middleware()])
    result = asyncio.run(agent.ainvoke({"messages": [("user", "refund 5000")]}))
    assert ran == [] and tool_messages(result)[-1].status == "error"


def test_sync_run_with_a_pre_node():
    middleware = VSLToolMiddleware(gates_by_tool_name={
        "issue_refund": LangChainAdapter().compile_pre_node(
            PreNode(name="budget", description="d", monitor=budget_headroom, gamma_threshold=1.1)),
    })
    agent = create_agent(model=scripted(480.0), tools=[issue_refund], middleware=[middleware])  # 500/480 = 1.04
    agent.invoke({"messages": [("user", "refund 480")]})
    assert ran == []


# ---- next to a sync-only middleware --------------------------------------------------------------

@pytest.mark.parametrize("vsl_first", [True, False])
def test_beside_a_sync_only_policy_both_must_allow(vsl_first):
    policy = SyncOnlyPolicy()
    stack = [vsl_middleware(), policy] if vsl_first else [policy, vsl_middleware()]
    agent = create_agent(model=scripted(120.0), tools=[issue_refund], middleware=stack)
    agent.invoke({"messages": [("user", "refund 120")]})
    assert ran == [120.0] and policy.seen == ["issue_refund"]


@pytest.mark.parametrize("vsl_first", [True, False])
def test_beside_a_sync_only_policy_a_vsl_denial_stops_the_tool(vsl_first):
    policy = SyncOnlyPolicy()
    stack = [vsl_middleware(), policy] if vsl_first else [policy, vsl_middleware()]
    agent = create_agent(model=scripted(5000.0), tools=[issue_refund], middleware=stack)
    result = agent.invoke({"messages": [("user", "refund 5000")]})
    assert ran == []
    assert "Denied by VSL governance" in tool_messages(result)[-1].content


def test_beside_a_sync_only_policy_its_denial_stops_the_tool_too():
    policy = SyncOnlyPolicy(blocked={"issue_refund"})
    agent = create_agent(model=scripted(120.0), tools=[issue_refund], middleware=[policy, vsl_middleware()])
    with pytest.raises(PermissionError):
        agent.invoke({"messages": [("user", "refund 120")]})
    assert ran == []


# ---- the sync hook directly -----------------------------------------------------------------------

def request(name: str, args: dict) -> ToolCallRequest:
    return ToolCallRequest(tool_call={"name": name, "args": args, "id": "c1", "type": "tool_call"}, tool=None, state={}, runtime=None)


def test_the_sync_hook_passes_ungated_tools_straight_through():
    calls: list[str] = []
    result = vsl_middleware().wrap_tool_call(request("lookup_order", {}), lambda r: calls.append(r.tool_call["name"]) or "ok")
    assert result == "ok" and calls == ["lookup_order"]


async def test_the_sync_hook_works_from_a_thread_with_a_running_event_loop():
    # A sync agent run started from async code (a notebook, a web handler) calls
    # the sync hook on a thread whose loop is already running.
    calls: list[str] = []
    middleware = vsl_middleware()
    allowed = middleware.wrap_tool_call(request("issue_refund", {"amount": 120.0}), lambda r: calls.append("ran") or "ok")
    denied = middleware.wrap_tool_call(request("issue_refund", {"amount": 5000.0}), lambda r: calls.append("ran") or "ok")
    assert allowed == "ok" and calls == ["ran"]
    assert isinstance(denied, ToolMessage) and denied.status == "error"


def test_a_gate_that_fails_for_another_reason_raises_in_a_sync_run_too():
    async def broken(_candidate: Any) -> bool:
        raise ConnectionError("judge down")

    middleware = VSLToolMiddleware(gates_by_tool_name={
        "issue_refund": LangChainAdapter().compile_invariant(Invariant(name="judge", description="d", rule=broken)),
    })
    with pytest.raises(ConnectionError):
        middleware.wrap_tool_call(request("issue_refund", {"amount": 1.0}), lambda r: "ok")
