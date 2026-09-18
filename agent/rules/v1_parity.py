"""Exact behavioral port of v1 `validateBrief` (lib/brief-validate.ts:38 to 118).

v1 mutates the brief in place and throws on the first failure. This port
deep copies the payload, applies the same writes to the copy in the same order,
and returns it, or raises `ParityError` carrying a reason code. SC1b compares
this function with v1 directly, so it works on plain JSON values (parsed
strictly, no NaN) and never goes through Pydantic.

JavaScript semantics reproduced here, in the order v1 meets them:

- Property access on `null` throws a native TypeError; on any other primitive
  or an array it yields `undefined`. So a null source, step or candidate
  element gives `v1_type_error`, while `5` or `"x"` in the same slot fails the
  first text check instead.
- `if (x)` uses JavaScript truthiness: `[]` and `{}` are truthy, `0`, `""`,
  `false` and `null` are not. A falsy object slot is skipped and left as is.
- Assigning a property on a truthy primitive throws a TypeError in strict mode
  (v1 is an ES module), which is what `happened_before: "yes"` reaches. On an
  array the assignment succeeds and JSON drops it, so the array comes out
  unchanged.
- `Number.isInteger` rejects booleans and strings and accepts integral floats.
- `warranty.cautions` is rebuilt as new `{text, source_index}` objects, so
  extra keys on a caution entry are dropped, as v1 drops them.

The one v2 difference allowed in this layer is accepting status
`budget_stopped` (decision 21, proposed). It sits behind
`accept_budget_stopped`; the SC1b harness passes False.
"""

from __future__ import annotations

import copy
import json
from typing import Any

from agent.schemas import HTTP_URL

V1_TIERS = ("manufacturer", "dealer", "forum")
V1_STATUSES = ("ok", "no_reliable_answer")
BUDGET_STOPPED = "budget_stopped"

# Native TypeErrors in v1 (null element, truthy primitive in an object slot).
V1_TYPE_ERROR = "v1_type_error"
INVALID_STATUS = "invalid_status"
INVALID_STATUS_PREFIX = "Brief has invalid status: "

# Every v1 error message, verbatim, mapped to its reason code. The invalid
# status message ends with the status value, so it is matched by prefix.
V1_MESSAGE_TO_REASON: dict[str, str] = {
    "Brief is missing its sources array.": "sources_not_array",
    "Brief has a non-text source url.": "source_url_not_text",
    "Brief has a non-text source title.": "source_title_not_text",
    "Brief cited a source with a non-web URL; refusing to render it.": "source_url_not_web",
    "Brief has a source with an invalid tier label.": "source_tier_invalid",
    "Brief is no_reliable_answer but the explanation block is missing.": "nra_block_missing",
    "Brief has a malformed no_reliable_answer.searched list.": "nra_searched_malformed",
    "Brief has a malformed no_reliable_answer.found list.": "nra_found_malformed",
    "Brief has a non-text no_reliable_answer.why_insufficient.": "nra_why_insufficient_not_text",
    "Brief claims status ok with an empty bibliography.": "ok_empty_sources",
    "Brief has a non-text try-first step.": "step_not_text",
    "Brief has a non-text try-first detail.": "step_detail_not_text",
    "A try-first step cites a source that does not exist.": "step_index_out_of_range",
    "Brief has a non-text candidate meaning.": "candidate_meaning_not_text",
    "Brief has a non-text candidate action.": "candidate_action_not_text",
    "Brief has a non-text candidate why_shown.": "candidate_why_shown_not_text",
    "A candidate cites a source that does not exist.": "candidate_index_out_of_range",
    "Brief has a non-text warranty caution.": "warranty_caution_not_text",
    "Brief has a non-text warranty age statement.": "warranty_age_statement_not_text",
    "Brief has a non-text warranty caution entry.": "warranty_caution_entry_not_text",
    "Brief has a malformed warranty verify list.": "warranty_verify_malformed",
    "Brief has a non-text happened_before summary.": "happened_before_summary_not_text",
}
_REASON_TO_MESSAGE = {code: message for message, code in V1_MESSAGE_TO_REASON.items()}
REASON_CODES: tuple[str, ...] = (INVALID_STATUS, *V1_MESSAGE_TO_REASON.values(), V1_TYPE_ERROR)


class ParityError(ValueError):
    """v1 would throw here. `reason` is the code; `message` is v1's text."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(f"{reason}: {message}")
        self.reason = reason
        self.message = message


def reason_for_v1_error(message: str, *, error_name: str = "Error") -> str:
    """The reason code for an error v1 threw (used by the SC1b harness)."""
    if error_name == "TypeError":
        return V1_TYPE_ERROR
    if message.startswith(INVALID_STATUS_PREFIX):
        return INVALID_STATUS
    try:
        return V1_MESSAGE_TO_REASON[message]
    except KeyError:
        raise KeyError(f"no reason code for v1 message {message!r} ({error_name})") from None


def _reject_constant(name: str) -> Any:
    raise ValueError(f"non JSON constant {name}; JavaScript's JSON.parse rejects it")


def loads_strict(text: str) -> Any:
    """Parse JSON the way JavaScript does: NaN and Infinity are errors, not floats."""
    return json.loads(text, parse_constant=_reject_constant)


def _fail(reason: str) -> ParityError:
    return ParityError(reason, _REASON_TO_MESSAGE[reason])


def _type_error(what: str) -> ParityError:
    return ParityError(V1_TYPE_ERROR, f"TypeError: {what}")


class _Undefined:
    def __repr__(self) -> str:
        return "undefined"


UNDEFINED = _Undefined()


def _get(obj: Any, key: str, where: str) -> Any:
    """JavaScript `obj.key` on a JSON value: null throws, other non objects give undefined."""
    if obj is None:
        raise _type_error(f"cannot read properties of null (reading '{key}') in {where}")
    if isinstance(obj, dict):
        return obj.get(key, UNDEFINED)
    return UNDEFINED


def _nullish(value: Any) -> bool:
    return value is None or value is UNDEFINED


def _or_default(value: Any, default: Any) -> Any:
    """JavaScript `value ?? default`."""
    return default if _nullish(value) else value


def js_truthy(value: Any) -> bool:
    """JavaScript truthiness for JSON values: empty lists and objects are truthy."""
    if _nullish(value) or value is False:
        return False
    if isinstance(value, bool):
        return True
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value != ""
    return True


def js_string(value: Any) -> str:
    """JavaScript String(value) for the scalars a status can hold."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _is_string(value: Any) -> bool:
    return isinstance(value, str)


def _expect_string(value: Any, reason: str) -> Any:
    if not _is_string(value):
        raise _fail(reason)
    return value


def _expect_string_array(value: Any, reason: str) -> list:
    if not isinstance(value, list) or any(not _is_string(v) for v in value):
        raise _fail(reason)
    return value


def is_integer(value: Any) -> bool:
    """JavaScript Number.isInteger on a JSON value: no bools, integral floats pass."""
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return True
    return isinstance(value, float) and value.is_integer()


def _set(obj: Any, key: str, value: Any, where: str) -> None:
    """JavaScript `obj.key = value` in strict mode."""
    if isinstance(obj, dict):
        obj[key] = value
    elif isinstance(obj, list):
        pass  # a property on an array is legal and JSON.stringify drops it
    else:
        raise _type_error(f"cannot create property '{key}' on {type(obj).__name__} in {where}")


def _check_status(brief: Any, accept_budget_stopped: bool) -> Any:
    status = _get(brief, "status", "brief")
    allowed = V1_STATUSES + ((BUDGET_STOPPED,) if accept_budget_stopped else ())
    if not (_is_string(status) and status in allowed):
        raise ParityError(INVALID_STATUS, f"{INVALID_STATUS_PREFIX}{js_string(status)}")
    return status


def _check_sources(brief: dict) -> list:
    sources = brief.get("sources", UNDEFINED)
    if not isinstance(sources, list):
        raise _fail("sources_not_array")
    for i, source in enumerate(sources):
        where = f"sources[{i}]"
        url = _expect_string(_get(source, "url", where), "source_url_not_text")
        _expect_string(_or_default(_get(source, "title", where), ""), "source_title_not_text")
        if not HTTP_URL.match(url):
            raise _fail("source_url_not_web")
        tier = _get(source, "tier", where)
        if not (_is_string(tier) and tier in V1_TIERS):
            raise _fail("source_tier_invalid")
    return sources


def _check_refusal_block(brief: dict) -> None:
    block = brief.get("no_reliable_answer", UNDEFINED)
    if not js_truthy(block):
        raise _fail("nra_block_missing")
    where = "no_reliable_answer"
    searched = _expect_string_array(_get(block, "searched", where), "nra_searched_malformed")
    _set(block, "searched", searched, where)
    found = _expect_string_array(_or_default(_get(block, "found", where), []), "nra_found_malformed")
    _set(block, "found", found, where)
    why = _expect_string(_get(block, "why_insufficient", where), "nra_why_insufficient_not_text")
    _set(block, "why_insufficient", why, where)


def _check_steps(brief: dict, in_range) -> None:
    for i, step in enumerate(brief["try_first"]):
        where = f"try_first[{i}]"
        _expect_string(_get(step, "step", where), "step_not_text")
        _expect_string(_or_default(_get(step, "detail", where), ""), "step_detail_not_text")
        _set(step, "safety_flag", _get(step, "safety_flag", where) is True, where)
        if not in_range(_get(step, "source_index", where)):
            raise _fail("step_index_out_of_range")


def _check_candidates(brief: dict, in_range) -> None:
    for i, cand in enumerate(brief["candidates"]):
        where = f"candidates[{i}]"
        _expect_string(_get(cand, "documented_meaning", where), "candidate_meaning_not_text")
        _expect_string(_get(cand, "documented_action", where), "candidate_action_not_text")
        _expect_string(_or_default(_get(cand, "why_shown", where), ""), "candidate_why_shown_not_text")
        who = _get(cand, "who", where)
        if not (_is_string(who) and who in ("anyone", "technician")):
            _set(cand, "who", "technician", where)
        _set(cand, "confirmed", _get(cand, "confirmed", where) is True, where)
        if not in_range(_get(cand, "source_index", where)):
            raise _fail("candidate_index_out_of_range")


def _check_top_caution(brief: dict, in_range) -> None:
    caution = brief.get("warranty_caution", UNDEFINED)
    if not js_truthy(caution):
        return
    where = "warranty_caution"
    _expect_string(_get(caution, "text", where), "warranty_caution_not_text")
    if not in_range(_get(caution, "source_index", where)):
        _set(caution, "source_index", None, where)


def _check_warranty(brief: dict, in_range) -> None:
    warranty = brief.get("warranty", UNDEFINED)
    if not js_truthy(warranty):
        return
    where = "warranty"
    _expect_string(_get(warranty, "age_statement", where), "warranty_age_statement_not_text")
    cautions = _get(warranty, "cautions", where)
    cautions = cautions if isinstance(cautions, list) else []
    rebuilt = []
    for entry in cautions:
        if _is_string(entry):
            rebuilt.append({"text": entry, "source_index": None})
            continue
        # v1 reads `entry?.text`: optional chaining, so a null entry fails the
        # text check rather than throwing a TypeError.
        text = UNDEFINED if entry is None else _get(entry, "text", "warranty.cautions")
        _expect_string(text, "warranty_caution_entry_not_text")
        index = _get(entry, "source_index", "warranty.cautions")
        rebuilt.append({"text": text, "source_index": index if in_range(index) else None})
    _set(warranty, "cautions", rebuilt, where)
    verify = _expect_string_array(_or_default(_get(warranty, "verify", where), []), "warranty_verify_malformed")
    _set(warranty, "verify", verify, where)


def _check_happened_before(brief: dict) -> None:
    hb = brief.get("happened_before", UNDEFINED)
    if not js_truthy(hb):
        return
    where = "happened_before"
    _set(hb, "matches", _get(hb, "matches", where) is True, where)
    _expect_string(_or_default(_get(hb, "summary", where), ""), "happened_before_summary_not_text")


def validate_brief_v1(payload: Any, *, accept_budget_stopped: bool = True) -> Any:
    """Run v1 validateBrief on a copy of `payload` and return the normalized copy.

    Raises ParityError on the first check v1 would throw on. With
    `accept_budget_stopped` (the v2 default), status `budget_stopped` passes the
    status check and follows the refusal path without needing its block:
    sources are checked, try_first and candidates are cleared.
    """
    brief = copy.deepcopy(payload)
    status = _check_status(brief, accept_budget_stopped)
    # Past the status check `brief` is an object: any other value has no status.
    sources = _check_sources(brief)

    def in_range(i: Any) -> bool:
        return is_integer(i) and 0 <= i < len(sources)

    brief["try_first"] = brief["try_first"] if isinstance(brief.get("try_first"), list) else []
    brief["candidates"] = brief["candidates"] if isinstance(brief.get("candidates"), list) else []

    if status == BUDGET_STOPPED:
        brief["try_first"] = []
        brief["candidates"] = []
        return brief
    if status == "no_reliable_answer":
        _check_refusal_block(brief)
        brief["try_first"] = []
        brief["candidates"] = []
        return brief

    if len(sources) == 0:
        raise _fail("ok_empty_sources")
    brief["no_reliable_answer"] = None
    _check_steps(brief, in_range)
    _check_candidates(brief, in_range)
    _check_top_caution(brief, in_range)
    _check_warranty(brief, in_range)
    _check_happened_before(brief)
    return brief
