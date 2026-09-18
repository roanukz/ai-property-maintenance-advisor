"""Custom research agent middleware: ToolBudgetDone and SpendCap (PLAN 8.7, 8.9).

Checked against the installed langchain 1.4.1 source: `AgentMiddleware.before_model(self,
state, runtime) -> dict | None` may jump only when decorated with
`hook_config(can_jump_to=[...])`, and `wrap_model_call(self, request: ModelRequest,
handler) -> ModelResponse | AIMessage | ExtendedModelResponse` cannot jump at all
(a Command goto there raises NotImplementedError), so SpendCap ends research by
raising BudgetExceeded("research_budget"), which the research node catches.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from langchain.agents.middleware import AgentMiddleware, AgentState, ModelRequest, ModelResponse, hook_config
from langchain_core.exceptions import ModelConnectionError, ModelTimeoutError
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.runtime import Runtime

from agent import config
from agent.ledger import BudgetExceeded, Ledger, estimate_input_tokens, price_usage
from agent.redaction import scrub_exception
from agent.state import RunContext

NODE = "research"
TOOLS = ("search", "fetch")

# Text ToolCallLimitMiddleware puts in a blocked call's ToolMessage (langchain
# 1.4.1 tool_call_limit.py _build_tool_message_content).
BLOCKED_MARKER = "Tool call limit exceeded"

# A call that timed out or lost its connection may still have been billed
# (PLAN 8.9), so it is charged at its reservation.
SENT_FAILURES: tuple[type[BaseException], ...] = (ModelTimeoutError, ModelConnectionError, TimeoutError)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds")


class ToolBudgetDone(AgentMiddleware[AgentState, Any]):
    """Ends the agent before another model call once both tool caps are spent.

    The final text of the research agent is unused, so a model turn after the
    last allowed tool call would only cost money. Also records calls that
    ToolCallLimitMiddleware blocked into the shared trail, so the search trail
    lists them even if the agent later raises.
    """

    def __init__(self, caps: dict[str, int], trail: list[dict[str, Any]] | None = None) -> None:
        super().__init__()
        self.caps = {tool: int(caps[tool]) for tool in TOOLS}
        self.trail = trail
        self.ended = False
        self._seen_blocked: set[str] = set()

    def _record_blocked(self, messages: list[Any]) -> None:
        if self.trail is None:
            return
        calls = {
            call["id"]: call
            for m in messages if isinstance(m, AIMessage)
            for call in m.tool_calls
        }
        for m in messages:
            if (isinstance(m, ToolMessage) and m.status == "error" and m.tool_call_id not in self._seen_blocked
                    and BLOCKED_MARKER in str(m.content)):
                self._seen_blocked.add(m.tool_call_id)
                call = calls.get(m.tool_call_id, {})
                args = call.get("args") or {}
                self.trail.append({
                    "tool": call.get("name", m.name), "query": args.get("query", args.get("url", "")),
                    "n_results": 0, "credits": 0, "at": _now(), "status": "blocked",
                    "artifact": None, "pages": {},
                })

    @staticmethod
    def calls_made(messages: list[Any]) -> dict[str, int]:
        """Tool calls the model asked for in this run, blocked ones included."""
        counts = dict.fromkeys(TOOLS, 0)
        for m in messages:
            if isinstance(m, AIMessage):
                for call in m.tool_calls:
                    if call["name"] in counts:
                        counts[call["name"]] += 1
        return counts

    @hook_config(can_jump_to=["end"])
    def before_model(self, state: AgentState, runtime: Runtime[Any]) -> dict[str, Any] | None:
        messages = list(state.get("messages", []))
        self._record_blocked(messages)
        made = self.calls_made(messages)
        if all(made[tool] >= self.caps[tool] for tool in TOOLS):
            self.ended = True
            return {"jump_to": "end"}
        return None


class SpendCap(AgentMiddleware[AgentState, Any]):
    """Reserves every research model call in the ledger before it is made, and charges it after.

    Innermost in the list, so every retry passes through it. A run or build
    dollar cap raises the ledger's BudgetExceeded (a budget stop). Reaching the
    research sub budget (decision 26) raises BudgetExceeded("research_budget"),
    which ends research without stopping the run. The sub budget is per
    research pass: each invocation of the research node gets a new SpendCap.
    """

    def __init__(self, ctx: RunContext, research_budget_usd: float) -> None:
        super().__init__()
        self.ctx = ctx
        self.research_budget_usd = research_budget_usd
        self.spent_usd = 0.0
        self.calls = 0
        self.research_budget_reached = False

    def _reservation_usd(self, model: str, input_tokens: int, max_tokens: int) -> float:
        prices = config.REPLAY_PRICES if self.ctx.mode == "replay" else config.PRICES_PER_MTOK[model]
        return price_usage(prices, {"input_tokens": input_tokens, "output_tokens": max_tokens})

    def wrap_model_call(
        self, request: ModelRequest, handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelResponse:
        ctx = self.ctx
        model = str(getattr(request.model, "model", None) or config.MODEL_FOR[ctx.mode][NODE])
        max_tokens = int(request.model_settings.get("max_tokens") or getattr(request.model, "max_tokens", 0)
                         or config.MAX_TOKENS[NODE])
        messages = ([request.system_message] if request.system_message else []) + list(request.messages)
        estimate = estimate_input_tokens(messages, model=model, tools=request.tools or None)
        ledger = Ledger(ctx.ledger_path)
        ids = {"mode": ctx.mode, "node": NODE}
        if self.spent_usd + self._reservation_usd(model, estimate, max_tokens) > self.research_budget_usd:
            self.research_budget_reached = True
            ledger.stop(ctx.run_id, reason="research_budget", **ids)
            raise BudgetExceeded(
                "research_budget",
                f"research spent {self.spent_usd:.6f}; the next call would pass {self.research_budget_usd:.6f}",
            )
        hold = ledger.reserve(ctx.run_id, model=model, input_tokens_est=estimate, max_tokens=max_tokens,
                              run_cap_usd=(ctx.caps or {}).get("run_cap_usd"), **ids)
        self.calls += 1
        try:
            response = handler(request)
        except BaseException as exc:
            if isinstance(exc, SENT_FAILURES):
                self.spent_usd += ledger.charge_timeout(hold)
            else:
                ledger.release(hold)
            # The error travels up through the agent to the graph, which keeps
            # its repr in the checkpoint: no key value may be in it.
            clean = scrub_exception(exc, ctx.secrets)
            if clean is exc:
                raise
            raise clean from None
        usage = next(
            (m.usage_metadata for m in response.result if isinstance(m, AIMessage) and m.usage_metadata),
            None,
        )
        if usage is None:
            # No usage reported: charge the reservation, marked estimated.
            self.spent_usd += ledger.charge_timeout(hold)
        else:
            self.spent_usd += ledger.charge(hold, dict(usage))
        return response
