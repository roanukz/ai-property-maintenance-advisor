"""A scripted chat model that stands in for ChatAnthropic in replay (PLAN 8.10).

None of the fake models in langchain_core override bind_tools, so none can drive
create_agent with tools, and FakeMessagesListChatModel silently wraps around
when its list runs out. This one fails loudly instead.
"""

from __future__ import annotations

import json
import uuid
import warnings
from operator import itemgetter
from typing import Any, Literal

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.output_parsers import JsonOutputParser, PydanticOutputParser
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables import RunnableMap, RunnablePassthrough
from langchain_core.utils.function_calling import convert_to_openai_tool
from langchain_core.utils.pydantic import is_basemodel_subclass
from pydantic import Field

# A pure module level function (it builds no client). It is private, but using
# it means replay binds exactly the schema a live call sends, which is the
# anthropic transform_schema output that decision 22's limits are counted on.
from langchain_anthropic.chat_models import _convert_to_anthropic_output_config_format


class ReplayExhausted(RuntimeError):
    """The model was called more times than the script has responses."""


def _usage_metadata(usage: dict[str, Any] | None) -> dict[str, Any]:
    """Normalize scripted usage to the UsageMetadata shape, filling total_tokens."""
    usage = dict(usage or {})
    usage.setdefault("input_tokens", 0)
    usage.setdefault("output_tokens", 0)
    usage.setdefault("total_tokens", usage["input_tokens"] + usage["output_tokens"])
    return usage


def _tool_calls(raw: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    calls = []
    for call in raw or []:
        calls.append(
            {
                "name": call["name"],
                "args": dict(call.get("args", {})),
                "id": call.get("id") or f"replay-call-{uuid.uuid4().hex[:12]}",
                "type": "tool_call",
            }
        )
    return calls


class ReplayChatModel(BaseChatModel):
    """Serves scripted responses in order and records every request."""

    responses: list[dict]
    model: str
    max_tokens: int
    received: list[list[BaseMessage]] = Field(default_factory=list, exclude=True)
    received_kwargs: list[dict[str, Any]] = Field(default_factory=list, exclude=True)
    cursor: int = Field(default=0, exclude=True)

    @property
    def _llm_type(self) -> str:
        return "replay-chat-model"

    @property
    def _identifying_params(self) -> dict[str, Any]:
        return {"model": self.model, "max_tokens": self.max_tokens}

    def _next_message(self) -> AIMessage:
        if self.cursor >= len(self.responses):
            raise ReplayExhausted(
                f"replay script for {self.model} exhausted after {self.cursor} calls"
            )
        response = self.responses[self.cursor]
        self.cursor += 1
        if "structured" in response:
            content = json.dumps(response["structured"])
            tool_calls: list[dict[str, Any]] = []
            usage = response.get("usage")
        elif "message" in response:
            scripted = response["message"]
            content = scripted.get("content", "")
            tool_calls = _tool_calls(scripted.get("tool_calls"))
            usage = scripted.get("usage", response.get("usage"))
        else:
            raise ValueError(f"replay response {self.cursor - 1} has neither message nor structured")
        # A fresh id every time: add_messages silently overwrites a repeated id.
        return AIMessage(
            content=content,
            tool_calls=tool_calls,
            id=f"replay-{uuid.uuid4()}",
            usage_metadata=_usage_metadata(usage),
            response_metadata={"model_name": self.model, "model_provider": "replay"},
        )

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        self.received.append(list(messages))
        self.received_kwargs.append(dict(kwargs))
        return ChatResult(generations=[ChatGeneration(message=self._next_message())])

    def bind_tools(self, tools: Any, *, tool_choice: Any = None, **kwargs: Any) -> Any:
        """Accept any tools (including an empty list); the script decides the calls."""
        kwargs["tools"] = [t if isinstance(t, dict) else convert_to_openai_tool(t) for t in tools]
        if tool_choice is not None:
            kwargs["tool_choice"] = tool_choice
        return self.bind(**kwargs)

    def with_structured_output(
        self,
        schema: Any,
        *,
        include_raw: bool = False,
        method: Literal["function_calling", "json_mode", "json_schema"] = "json_schema",
        **kwargs: Any,
    ) -> Any:
        """Mirror ChatAnthropic's json_schema branch, including include_raw wiring."""
        if method == "json_mode":
            warnings.warn(
                "Unrecognized structured output method 'json_mode'. Defaulting to 'json_schema' method.",
                stacklevel=2,
            )
            method = "json_schema"
        if method == "function_calling":
            return super().with_structured_output(schema, include_raw=include_raw, **kwargs)
        if method != "json_schema":
            raise ValueError(
                f"Unrecognized structured output method '{method}'. "
                "Expected 'function_calling' or 'json_schema'."
            )
        llm = self.bind(
            output_config={"format": _convert_to_anthropic_output_config_format(schema)},
            ls_structured_output_format={
                "kwargs": {"method": "json_schema"},
                "schema": convert_to_openai_tool(schema),
            },
        )
        if isinstance(schema, type) and is_basemodel_subclass(schema):
            parser: Any = PydanticOutputParser(pydantic_object=schema)
        else:
            parser = JsonOutputParser()
        if include_raw:
            parser_assign = RunnablePassthrough.assign(
                parsed=itemgetter("raw") | parser, parsing_error=lambda _: None
            )
            parser_none = RunnablePassthrough.assign(parsed=lambda _: None)
            parser_with_fallback = parser_assign.with_fallbacks(
                [parser_none], exception_key="parsing_error"
            )
            return RunnableMap(raw=llm) | parser_with_fallback
        return llm | parser
