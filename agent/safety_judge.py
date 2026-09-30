"""Jev, asked whether a try_first step is a safety step (decisions 53 to 57).

A SafetyJudge answers one Noul per step: `ask(step_sha256, state)` returns a
JudgeReply or raises SafetyCheckError, and nothing else, so a failed check
can never crash a run. The step is named by `step_key` (the hash of its
normalized step and detail, decision 56); `state` is what Jev reads,
{"appliance", "step", "detail"}.

The live judge (build_live_judge) calls TypeSafe through typesafe-sdk. It
builds its client on the first ask, inside the same guard as the call, so a
missing TYPESAFE_API_KEY is a "missing_key" failure (decision 55). Before
every call it reserves in the ledger with provider "typesafe"; after it, it
charges usage.input_tokens at Jev's price with output at 0, or the whole
reservation (estimated) when usage is missing or an attempt may have been
sent and billed (a timeout or a connection failure, including one the SDK
retried before the call answered), or releases the reservation when the API
answered with an error status and no attempt was lost. A reply from any
model but config.JEV_MODEL is a "model_mismatch" failure (decision 54). Every
answer and every failure is appended to the capture list, which the live
recorder writes into the cassette candidate's "safety_check" key: hashes,
the model string, the request ID and numbers, never step or page text.

The replay judge (build_replay_judge) serves those recorded entries by step
hash, in call order. An entry recorded under another question hash, or an
answer from another model, is refused with CassetteError; a step with no
entry left raises SafetyCheckError("not_recorded"), which is not a failure.

Replay never imports typesafe_sdk: the live judge imports it on its first ask.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Protocol

from agent import config
from agent.redaction import redact
from agent.replay.cassettes import (
    MODEL_NAME_RE,
    SAFETY_CHECK_ERRORS,
    SAFETY_CHECK_KEYS,
    CassetteError,
    check_safety_check_entries,
)

NODE = "safety_check"
NOUL_NAME = "safety_step"
NOT_RECORDED = "not_recorded"
FAILURE_KINDS = (*SAFETY_CHECK_ERRORS, NOT_RECORDED)
QUESTION_HASH_CHARS = 16
REQUEST_ID_HEADER = "x-typesafe-request-id"
# A failure's detail is cut to this many characters after redaction.
DETAIL_MAX_CHARS = 300
NO_USAGE_NOTE = "a Jev reply with no usage, charged at reservation"
SENT_NOTE = "a Jev call that may have been sent, charged at reservation"
LOST_NOTE = "a Jev call with a retried attempt that may have been billed, charged at reservation"
SDK_LOGGER = "typesafe_sdk"
# Recorded for a model_mismatch reply whose model string is not one token.
UNRECOGNIZED_MODEL = "unrecognized"


def question_hash(instructions: Any, criteria: Any) -> str:
    """First 16 hex characters of the sha256 of {"instructions", "criteria"} as canonical JSON."""
    canonical = json.dumps({"instructions": instructions, "criteria": criteria}, sort_keys=True,
                           separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:QUESTION_HASH_CHARS]


@dataclass(frozen=True)
class JudgeReply:
    """One answered Noul."""

    step_sha256: str
    noul: float
    model: str
    request_id: str | None
    input_tokens: int | None
    output_tokens: int | None
    question_hash: str


class SafetyCheckError(Exception):
    """A step got no Jev answer. `kind` is one of FAILURE_KINDS; `detail` never holds a key."""

    def __init__(self, kind: str, detail: str = "") -> None:
        if kind not in FAILURE_KINDS:
            raise ValueError(f"unknown safety check failure kind {kind!r}")
        self.kind = kind
        self.detail = detail
        super().__init__(f"{kind}: {detail}" if detail else kind)


class SafetyJudge(Protocol):
    """What the safety_check node asks. ask raises only SafetyCheckError."""

    question_hash: str

    def ask(self, step_sha256: str, state: dict) -> JudgeReply: ...


# ---------------------------------------------------------------------------
# Capture: where a live run's answers go for its recording
# ---------------------------------------------------------------------------

_CAPTURE: ContextVar[list | None] = ContextVar("advisor_safety_capture", default=None)


def current_capture() -> list | None:
    """The capture list of the live recording in progress, or None."""
    return _CAPTURE.get()


@contextmanager
def capture_into(sink: list) -> Iterator[list]:
    """Send every live judge built in this block to `sink` (the recorder's capture)."""
    token = _CAPTURE.set(sink)
    try:
        yield sink
    finally:
        _CAPTURE.reset(token)


def capture_entry(step_sha256: str, qhash: str, *, model: str, noul: float | None = None,
                  input_tokens: int | None = None, output_tokens: int | None = None,
                  request_id: str | None = None, error: str | None = None) -> dict[str, Any]:
    """One cassette safety_check entry, keys in SAFETY_CHECK_KEYS order."""
    values = {"step_sha256": step_sha256, "question_hash": qhash, "model": model, "noul": noul,
              "input_tokens": input_tokens, "output_tokens": output_tokens, "request_id": request_id,
              "error": error}
    return {key: values[key] for key in SAFETY_CHECK_KEYS}


# ---------------------------------------------------------------------------
# Live
# ---------------------------------------------------------------------------


def _count(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _request_id(obj: Any) -> str | None:
    """The request ID of a reply or an API error, or None; never raises (decision 55)."""
    try:
        value = obj.request_id
    except Exception:
        value = None
    if value is None:
        try:
            value = obj.raw_http_response.headers.get(REQUEST_ID_HEADER)
        except Exception:
            value = None
    return value if isinstance(value, str) and value else None


class _Failure(Exception):
    """Internal: a failed ask, with what the reply said, before it becomes SafetyCheckError."""

    def __init__(self, kind: str, detail: str, *, model: str | None = None, request_id: str | None = None,
                 input_tokens: int | None = None, output_tokens: int | None = None) -> None:
        super().__init__(kind)
        self.kind, self.detail, self.model = kind, detail, model
        self.request_id, self.input_tokens, self.output_tokens = request_id, input_tokens, output_tokens


class LiveJudge:
    """Asks Jev through typesafe-sdk; every call reserved before and charged after."""

    def __init__(self, *, ledger: Any, run_id: str, mode: str, instructions: Any, criteria: Any,
                 capture: list | None, thread_id: str | None = None, run_cap_usd: float | None = None,
                 secrets: Sequence[str] = (), client_options: Mapping[str, Any] | None = None) -> None:
        if mode not in config.LIVE_MODES:
            raise ValueError(f"a live safety judge needs a live mode, not {mode!r}")
        self.ledger, self.run_id, self.mode = ledger, run_id, mode
        self.thread_id, self.run_cap_usd = thread_id, run_cap_usd
        self.instructions, self.criteria = instructions, criteria
        self.question_hash = question_hash(instructions, criteria)
        self.capture = capture
        self._secrets = [s for s in secrets if s]
        self._client_options = dict(client_options or {})
        self._client: Any = None
        self.calls = 0

    # the client -------------------------------------------------------------

    def _client_or_build(self) -> Any:
        if self._client is None:
            from typesafe_sdk import TypeSafeClient, constants

            logger = logging.getLogger(SDK_LOGGER)
            if logger.isEnabledFor(logging.DEBUG):  # TYPESAFE_LOG_LEVEL=debug logs request bodies
                logger.setLevel(logging.INFO)
            options = {"model": config.JEV_MODEL, "base_url": constants.DEFAULT_BASE_URL,
                       "timeout": config.JEV_TIMEOUT_S, **self._client_options}
            self._client = TypeSafeClient(**options)
        return self._client

    def _noul(self) -> Any:
        from typesafe_sdk import Noul

        if self.criteria is None:
            return Noul(instructions=self.instructions)
        return Noul(instructions=self.instructions, criteria=self.criteria)

    def _detail(self, text: str) -> str:
        secrets = [*self._secrets, os.environ.get(config.TYPESAFE_KEY_NAME, ""),
                   str(self._client_options.get("api_key") or "")]
        clean = redact(text, sorted({s for s in secrets if s}, key=len, reverse=True))
        return clean[:DETAIL_MAX_CHARS]

    # one call -----------------------------------------------------------------

    def ask(self, step_sha256: str, state: dict) -> JudgeReply:
        """One Noul for one step: a JudgeReply, or SafetyCheckError of a FAILURE_KINDS kind."""
        self.calls += 1
        try:
            reply = self._ask(step_sha256, state)
        except _Failure as failure:
            self._record(capture_entry(
                step_sha256, self.question_hash, model=failure.model or config.JEV_MODEL,
                input_tokens=failure.input_tokens, output_tokens=failure.output_tokens,
                request_id=failure.request_id, error=failure.kind))
            raise SafetyCheckError(failure.kind, self._detail(failure.detail)) from None
        except Exception as exc:  # last resort (decision 55): never a crash
            self._record(capture_entry(step_sha256, self.question_hash, model=config.JEV_MODEL, error="other"))
            raise SafetyCheckError("other", self._detail(f"{type(exc).__name__}: {exc}")) from None
        self._record(capture_entry(
            step_sha256, self.question_hash, model=reply.model, noul=reply.noul, input_tokens=reply.input_tokens,
            output_tokens=reply.output_tokens, request_id=reply.request_id))
        return reply

    def _record(self, entry: dict[str, Any]) -> None:
        if self.capture is not None:
            try:
                self.capture.append(entry)
            except Exception:  # a failed capture write must not fail the run; the recording shows the gap
                pass

    def _ask(self, step_sha256: str, state: dict) -> JudgeReply:
        from agent.ledger import BudgetExceeded
        from typesafe_sdk import (
            RetryPolicy,
            TypeSafeAPIConnectionError,
            TypeSafeAPIError,
            TypeSafeAPIResponseValidationError,
            TypeSafeAPITimeoutError,
            TypeSafeError,
            TypeSafeRateLimitError,
        )

        try:
            client = self._client_or_build()
        except TypeSafeError as exc:
            kind = "missing_key" if "API key" in str(exc) else "other"
            raise _Failure(kind, f"client not built: {exc}") from None
        # Built before the reservation: a question that fails to build is never sent.
        questions = {NOUL_NAME: self._noul()}
        # The SDK retries a timed out or dropped attempt inside system_one, and
        # that attempt may have been billed. The predicate retries exactly what
        # the SDK's defaults retry (decision 57) and counts those attempts.
        lost: list[str] = []

        def lost_attempt(exc: BaseException) -> bool:
            if isinstance(exc, TypeSafeAPIConnectionError):  # a timeout is one too
                lost.append(type(exc).__name__)
                return True
            return False

        retry = RetryPolicy(max_retries=config.JEV_RETRY_MAX, timeout=config.JEV_RETRY_BUDGET_S,
                            api_timeout_error=False, api_connection_error=False, predicate=lost_attempt)
        attempts = 1 + config.JEV_RETRY_MAX
        try:
            reservation = self.ledger.reserve(
                self.run_id, mode=self.mode, node=NODE, model=config.JEV_MODEL,
                input_tokens_est=config.JEV_EST_INPUT_TOKENS * attempts, max_tokens=0,
                thread_id=self.thread_id, run_cap_usd=self.run_cap_usd, provider=config.JEV_PROVIDER,
            )
        except BudgetExceeded as exc:
            raise _Failure("reservation_refused", f"{exc.reason}: {exc}") from None
        try:
            response = client.system_one(state, questions, model=config.JEV_MODEL, retry=retry,
                                         timeout=config.JEV_TIMEOUT_S)
        except TypeSafeAPIResponseValidationError as exc:
            # A 200 whose body did not parse: billed, so charge what it reports, or the reservation.
            usage = exc.body.get("usage") if isinstance(exc.body, dict) else None
            tokens = _count(usage.get("input_tokens")) if isinstance(usage, dict) else None
            if lost:
                self.ledger.charge_estimated(reservation, LOST_NOTE)
            elif tokens is None:
                self.ledger.charge_estimated(reservation, NO_USAGE_NOTE)
            else:
                self.ledger.charge(reservation, {"input_tokens": tokens,
                                                 "output_tokens": _count(usage.get("output_tokens")) or 0})
            raise _Failure("unparseable", f"the reply did not parse: {exc}", request_id=_request_id(exc),
                           input_tokens=tokens) from None
        except TypeSafeAPIError as exc:
            if lost:  # an earlier attempt timed out or dropped and may have been billed
                self.ledger.charge_estimated(reservation, LOST_NOTE)
            else:
                self.ledger.release(reservation)
            kind = "rate_limited" if isinstance(exc, TypeSafeRateLimitError) else "api_error"
            raise _Failure(kind, str(exc), request_id=_request_id(exc)) from None
        except TypeSafeAPITimeoutError as exc:
            self.ledger.charge_estimated(reservation, SENT_NOTE)
            raise _Failure("timeout", str(exc)) from None
        except TypeSafeAPIConnectionError as exc:
            self.ledger.charge_estimated(reservation, SENT_NOTE)
            raise _Failure("api_error", f"connection failed: {exc}") from None
        except TypeSafeError as exc:  # raised before the request left, such as a body that did not encode
            self.ledger.release(reservation)
            raise _Failure("other", str(exc)) from None
        except Exception as exc:  # unknown whether it was sent: charge the reservation
            self.ledger.charge_estimated(reservation, SENT_NOTE)
            raise _Failure("other", f"{type(exc).__name__}: {exc}") from None

        input_tokens = _count(getattr(response.usage, "input_tokens", None))
        output_tokens = _count(getattr(response.usage, "output_tokens", None))
        if lost:
            self.ledger.charge_estimated(reservation, LOST_NOTE)
        elif input_tokens is None:
            self.ledger.charge_estimated(reservation, NO_USAGE_NOTE)
        else:
            self.ledger.charge(reservation, {"input_tokens": input_tokens, "output_tokens": output_tokens or 0})
        model = response.model
        request_id = _request_id(response)
        facts = {"request_id": request_id, "input_tokens": input_tokens, "output_tokens": output_tokens}
        if model != config.JEV_MODEL:
            recorded = model if isinstance(model, str) and MODEL_NAME_RE.match(model) else UNRECOGNIZED_MODEL
            raise _Failure("model_mismatch", f"the reply came from {model!r}, not {config.JEV_MODEL!r}",
                           model=recorded, **facts)
        answer = response.nouls.get(NOUL_NAME)
        noul = getattr(answer, "noul", None)
        if isinstance(noul, bool) or not isinstance(noul, (int, float)) or not math.isfinite(noul) \
                or not 0 <= noul <= 1:
            raise _Failure("unparseable", f"the reply holds no {NOUL_NAME} probability", **facts)
        return JudgeReply(step_sha256=step_sha256, noul=float(noul), model=model, request_id=request_id,
                          input_tokens=input_tokens, output_tokens=output_tokens,
                          question_hash=self.question_hash)


def build_live_judge(*, ledger: Any, run_id: str, mode: str, instructions: Any, criteria: Any,
                     capture: list | None, thread_id: str | None = None, run_cap_usd: float | None = None,
                     secrets: Sequence[str] = (), client_options: Mapping[str, Any] | None = None) -> LiveJudge:
    """The live judge for one run (the node's factory) or one candidate wording (the SC12a harness).

    `client_options` go to TypeSafeClient after the pinned ones; offline tests
    pass api_key and an httpx2.MockTransport there. `secrets` are redacted
    from every failure detail, as is the loaded TypeSafe key.
    """
    return LiveJudge(ledger=ledger, run_id=run_id, mode=mode, instructions=instructions, criteria=criteria,
                     capture=capture, thread_id=thread_id, run_cap_usd=run_cap_usd, secrets=secrets,
                     client_options=client_options)


# ---------------------------------------------------------------------------
# Replay
# ---------------------------------------------------------------------------


class ReplayJudge:
    """Serves a cassette's recorded answers by step hash, in the order they were recorded."""

    def __init__(self, entries: list[dict], *, question_hash: str) -> None:
        check_safety_check_entries(entries)
        self.question_hash = question_hash
        self._queues: dict[str, list[dict[str, Any]]] = {}
        for i, entry in enumerate(entries):
            if entry["question_hash"] != question_hash:
                raise CassetteError(
                    f"safety_check[{i}]: recorded under question hash {entry['question_hash']}, "
                    f"not {question_hash}; replay refuses an answer to another wording")
            mismatch = entry["error"] == "model_mismatch"
            if (entry["model"] == config.JEV_MODEL) == mismatch:
                raise CassetteError(
                    f"safety_check[{i}]: recorded from model {entry['model']!r}, not {config.JEV_MODEL!r}"
                    if not mismatch else f"safety_check[{i}]: a model_mismatch entry names the pinned model")
            self._queues.setdefault(entry["step_sha256"], []).append(dict(entry))
        self.calls = 0

    def ask(self, step_sha256: str, state: dict) -> JudgeReply:
        self.calls += 1
        queue = self._queues.get(step_sha256)
        if not queue:
            raise SafetyCheckError(NOT_RECORDED, f"no recorded answer for step {step_sha256[:12]}")
        entry = queue.pop(0)
        if entry["error"] is not None:
            raise SafetyCheckError(entry["error"], "a recorded failure")
        return JudgeReply(step_sha256=step_sha256, noul=float(entry["noul"]), model=entry["model"],
                          request_id=entry["request_id"], input_tokens=entry["input_tokens"],
                          output_tokens=entry["output_tokens"], question_hash=self.question_hash)


def build_replay_judge(entries: list[dict], *, question_hash: str) -> ReplayJudge:
    """A judge over a cassette's "safety_check" list; an empty list answers every step not_recorded."""
    return ReplayJudge(list(entries), question_hash=question_hash)
