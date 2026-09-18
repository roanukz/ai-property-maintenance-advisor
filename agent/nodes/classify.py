"""The route classifier (PLAN 8.4 rows 3 and 4).

One Haiku call, made only when the graph has verified edges for the model and
the owner confirmed no code, so rules cannot decide between the graph route
and the graph plus research top up. It answers match, no_match or unsure. The
call goes through the ledger's reserve and charge, like read_plate and
synthesize, with max_tokens from config.MAX_TOKENS["classifier"].

A reply that does not parse counts as unsure, and so does a call the budget
cannot afford: unsure sends the run to the top up, which is the safe side
(research then meets the same budget and stops the run if it must).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from pydantic import BaseModel, ConfigDict, Field

from agent.ledger import BudgetExceeded
from agent.nodes.synthesize import paid_structured_call
from agent.prompts import CLASSIFIER_SYSTEM, build_classifier_user_prompt
from agent.state import RunContext

NODE = "classifier"
VERDICTS = ("match", "no_match", "unsure")
UNSURE = "unsure"


class ClassifierVerdict(BaseModel):
    """The classifier's structured reply. One required field, no unions."""

    model_config = ConfigDict(extra="forbid")

    verdict: Literal["match", "no_match", "unsure"] = Field(
        description='"match", "no_match" or "unsure".'
    )


@dataclass
class Classification:
    """What route needs: the verdict, what it cost, and why when it was not the model's own."""

    verdict: str
    cost_usd: float = 0.0
    note: str | None = None


def classifier_messages(identity: Mapping[str, Any] | None, symptom: str,
                        documented: Sequence[Mapping[str, Any]]) -> list[BaseMessage]:
    return [
        SystemMessage(CLASSIFIER_SYSTEM),
        HumanMessage(build_classifier_user_prompt(identity=identity, symptom=symptom, documented=documented)),
    ]


def classify_symptom(ctx: RunContext, *, identity: Mapping[str, Any] | None, symptom: str,
                     documented: Sequence[Mapping[str, Any]]) -> Classification:
    """Ask the classifier whether the symptom matches the documented causes on file."""
    messages = classifier_messages(identity, symptom, documented)
    try:
        out, usd = paid_structured_call(ctx, NODE, ClassifierVerdict, messages)
    except BudgetExceeded as exc:
        return Classification(UNSURE, 0.0, f"the classifier call was not affordable ({exc.reason})")
    parsed = out.get("parsed")
    if out.get("parsing_error") is not None or parsed is None:
        return Classification(UNSURE, usd, "the classifier reply did not parse")
    return Classification(parsed.verdict, usd)
