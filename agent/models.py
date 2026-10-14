"""Model and tool factories for every mode.

Replay builds ReplayChatModel and stub Tavily clients from the cassette and
must never construct a live client. Live modes build ChatAnthropic and Tavily
clients with every setting taken from config. In every mode the model sees
the same search and fetch wrappers (agent/research/tools.py); only the inner
client differs.

In replay each run builds one model per node and one set of tools, cached on
RunContext.replay, so a node that runs twice (a retry) continues the script
instead of starting it over, and tests can read the instances the graph used.

make_safety_judge does the same for Jev (agent/safety_judge.py): replay serves
the cassette's recorded "safety_check" answers and never imports typesafe_sdk;
a live mode builds the TypeSafe judge on the run's ledger.

A live model is DeclineAwareChatAnthropic: a reply with stop_reason "refusal"
raises agent.declined.ModelDeclined at this boundary, before any reader sees
its content, and nothing here retries it on another model (decision 69).
"""

from __future__ import annotations

from typing import Any, Literal

from langchain_anthropic import ChatAnthropic as LangChainChatAnthropic
from langchain_core.tools import BaseTool
from langchain_tavily import TavilyExtract, TavilySearch

from agent import config, prompts
from agent.declined import declined_from
from agent.ledger import Ledger, basis_model, price_basis
from agent.replay.replay_model import ReplayChatModel
from agent.replay.tool_stubs import make_stub_inners
from agent.research.tools import make_research_tools
from agent.safety_judge import (
    SafetyJudge,
    build_live_judge,
    build_replay_judge,
    current_capture,
    question_hash,
)
from agent.state import RunContext

NODES = ("read_plate", "classifier", "research", "synthesize")


def _check(node: str | None, mode: str) -> None:
    if mode not in config.MODES:
        raise ValueError(f"unknown mode {mode!r}; expected one of {config.MODES}")
    if node is not None and node not in NODES:
        raise ValueError(f"unknown model node {node!r}; expected one of {NODES}")


def max_tokens_for(node: str, mode: str) -> int:
    """Output budget for a node; synthesize has one per tier (replay uses cheap).

    Replay keeps the budgets the cassettes were recorded under
    (config.REPLAY_MAX_TOKENS), so its reservations stay as recorded.
    """
    budgets = config.REPLAY_MAX_TOKENS if mode == "replay" else config.MAX_TOKENS
    if node == "synthesize":
        return budgets["synthesize_full" if mode == "full" else "synthesize_cheap"]
    return budgets[node]


def live_model_kwargs(node: str, mode: str) -> dict[str, Any]:
    """The exact ChatAnthropic constructor arguments for a live call.

    Never temperature, top_p or top_k (Sonnet 5 and Claude Haiku 5.5 return 400
    on them). Claude Haiku 5.5 gets the node's effort (config.HAIKU_EFFORT),
    and never a thinking setting or a fallbacks parameter
    (config.NEVER_SEND_TO_HAIKU): adaptive thinking is its default. Sonnet 5
    synthesize in full mode keeps its effort.
    """
    model = config.MODEL_FOR[mode][node]
    max_tokens = max_tokens_for(node, mode)
    kwargs: dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "timeout": config.model_timeout_s(max_tokens),
        "max_retries": config.CLIENT_MAX_RETRIES,
    }
    if model == config.HAIKU:
        kwargs["reasoning_effort"] = config.HAIKU_EFFORT[node]
    elif node == "synthesize" and mode == "full":
        kwargs["reasoning_effort"] = config.FULL_SYNTH_EFFORT
    return kwargs


class DeclineAwareChatAnthropic(LangChainChatAnthropic):
    """ChatAnthropic that raises ModelDeclined on a reply with stop_reason "refusal".

    The check runs on the reply before any parser or agent reads it, so a
    decline never reaches a reader as an empty or partial answer. Streaming is
    off: a streamed reply carries no stop_details, so its category would be
    lost, and every call v2 makes is a single reply.
    """

    disable_streaming: bool | Literal["tool_calling"] = True

    def _generate(self, messages: Any, stop: Any = None, run_manager: Any = None, **kwargs: Any) -> Any:
        result = super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)
        return _checked(result, self.model)

    async def _agenerate(self, messages: Any, stop: Any = None, run_manager: Any = None, **kwargs: Any) -> Any:
        result = await super()._agenerate(messages, stop=stop, run_manager=run_manager, **kwargs)
        return _checked(result, self.model)


def _checked(result: Any, model: str) -> Any:
    """The ChatResult unchanged, or ModelDeclined raised with its billed message."""
    declined = declined_from(result.generations[0].message, model, result.llm_output)
    if declined is not None:
        raise declined
    return result


# The class every live chat model is built from, looked up when it is built:
# the decline aware class, under the name the live path tests replace with
# their fakes.
ChatAnthropic = DeclineAwareChatAnthropic


def make_model(node: str, ctx: RunContext) -> Any:
    """The chat model for one node in this run's mode."""
    _check(node, ctx.mode)
    if ctx.mode == "replay":
        if ctx.cassette is None:
            raise ValueError("replay mode needs a cassette on the RunContext")
        cache = ctx.replay.setdefault("models", {})
        if node not in cache:
            # A recording that names its live mode replays under that mode's
            # model names and output budgets (ledger.price_basis).
            basis = price_basis(ctx.mode, ctx.cassette)
            cache[node] = ReplayChatModel(
                responses=ctx.cassette.responses_for(node),
                model=basis_model(basis, node, ctx.cassette),
                max_tokens=max_tokens_for(node, basis),
            )
        return cache[node]
    return ChatAnthropic(**live_model_kwargs(node, ctx.mode))


def make_tools(ctx: RunContext) -> list[BaseTool]:
    """Search and fetch tools for the research agent, wrapped the same way in every mode."""
    _check(None, ctx.mode)
    if ctx.mode == "replay":
        if ctx.cassette is None:
            raise ValueError("replay mode needs a cassette on the RunContext")
        if "tools" not in ctx.replay:
            stubs = make_stub_inners(ctx.cassette)
            ctx.replay["stubs"] = stubs
            ctx.replay["tools"] = make_research_tools(
                ctx, search_inner=stubs["search"], fetch_inner=stubs["fetch"]
            )
        return list(ctx.replay["tools"])
    return make_research_tools(
        ctx,
        search_inner=TavilySearch(**config.TAVILY_SEARCH_SETTINGS),
        fetch_inner=TavilyExtract(**config.TAVILY_EXTRACT_SETTINGS),
    )


def _thread_id() -> str | None:
    try:
        from langgraph.config import get_config

        return (get_config().get("configurable") or {}).get("thread_id")
    except RuntimeError:  # called outside a graph run, as in unit tests
        return None


def make_safety_judge(ctx: RunContext) -> SafetyJudge:
    """The Jev judge for this run: the cassette's recorded answers in replay, TypeSafe live.

    Replay builds one judge per run, cached on RunContext.replay, from the
    cassette's optional "safety_check" list (none recorded: every step is
    not_recorded). A live judge reserves on the run's ledger and appends its
    answers to the live recording's capture, when one is running.
    """
    _check(None, ctx.mode)
    noul = prompts.SAFETY_STEP_NOUL
    if ctx.mode == "replay":
        if ctx.cassette is None:
            raise ValueError("replay mode needs a cassette on the RunContext")
        if "safety_judge" not in ctx.replay:
            entries = getattr(ctx.cassette, "safety_check", None) or []
            ctx.replay["safety_judge"] = build_replay_judge(
                entries, question_hash=question_hash(noul["instructions"], noul.get("criteria")))
        return ctx.replay["safety_judge"]
    return build_live_judge(
        ledger=Ledger(ctx.ledger_path),
        run_id=ctx.run_id,
        mode=ctx.mode,
        instructions=noul["instructions"],
        criteria=noul.get("criteria"),
        capture=current_capture(),
        thread_id=_thread_id(),
        run_cap_usd=(ctx.caps or {}).get("run_cap_usd"),
        secrets=ctx.secrets,
    )
