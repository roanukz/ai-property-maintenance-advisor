"""Code grounding (PLAN 8.5 rule 4, decision 25).

A non null candidate code is grounded in the source the candidate cites:

- verified: the code appears in the cited source's raw text; when the source
  has no raw text, the code must appear in its search snippet;
- unverifiable: only in replay, and only for a cassette whose
  `provenance.derived_from` names a v1 lookup (v1 recorded no page text), or
  for a live recording (`derived_from` "recorded live ...") when the cited
  page's text is not in the run's pages folder (decision 15 keeps real page
  text out of cassettes; D4, 18 September 2026);
- failed: everything else.

"Appears" means: after the section 8.11 normalization of both sides, the code
is a contiguous, case sensitive substring of the text with no letter or digit
directly before or after it, so "E1" is not found inside "E10". The forms
looked for are the code as the candidate gives it, the same with surrounding
whitespace and punctuation removed ("FLO." as "FLO"), and, when the candidate
matched the owner confirmed observed code under rule 3, the observed code as
the owner confirmed it. The v1 exemption is keyed on mode and provenance, never
on whether text was recorded. The live recording exemption also needs the page
text to be missing: with the text present, a live recording is checked like a
live run.

An evidence quote never grounds a code on its own. PLAN 8.5 rule 4 also counts
a code found in an evidence quote verified against the raw text, but such a
quote is a substring of the page, so a code standing as a token inside it is
already found in the page. The only codes the quote could add are ones cut off
at its edge ("shows E1" from "shows E10"), which the page does not support.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from agent.rules.evidence import normalize_for_span
from agent.rules.observed_code import codes_match

VERIFIED = "verified"
UNVERIFIABLE = "unverifiable"
FAILED = "failed"
# A brief whose candidates carry no code has nothing to ground; saying
# "verified" there would claim a check that never ran.
NOT_APPLICABLE = "not_applicable"
# Grounding is skipped when the observed code rule already rejected the draft.
NOT_CHECKED = "not_checked"
STATUSES = (VERIFIED, UNVERIFIABLE, FAILED, NOT_APPLICABLE, NOT_CHECKED)

GROUNDING_FAILED = "code_not_grounded"
REPLAY_MODE = "replay"
# The same prefixes Cassette.derived_from_v1 accepts (agent/replay/cassettes.py).
V1_PROVENANCE_PREFIXES = ("v1 lookup ", "v1 extract lookup ")
# The prefix agent/live/recorder.py writes into a live recording's provenance.
LIVE_PROVENANCE_PREFIX = "recorded live "

IN_RAW_TEXT = "code in the cited source's raw text"
IN_SNIPPET = "cited source has no raw text; code in its snippet"
V1_EXEMPT = "replay of a v1 derived cassette: v1 recorded no page text"
LIVE_NO_TEXT = "replay of a live recording: the cited page's text is not in the local pages folder"
NOT_FOUND_RAW = "code not in the cited source's raw text"
NOT_FOUND_SNIPPET = "cited source has no raw text and its snippet lacks the code"
NO_SOURCE = "the candidate cites no source"


@dataclass(frozen=True)
class GroundingResult:
    """The grounding status of one candidate code, with a plain reason."""

    status: str
    reason: str

    @property
    def passes(self) -> bool:
        return self.status in (VERIFIED, UNVERIFIABLE, NOT_APPLICABLE)


def derived_from_v1(provenance: dict | None) -> bool:
    """True when a cassette's provenance names a v1 lookup."""
    derived = (provenance or {}).get("derived_from")
    return isinstance(derived, str) and derived.startswith(V1_PROVENANCE_PREFIXES)


def recorded_live(provenance: dict | None) -> bool:
    """True when a cassette's provenance says the recorder built it from a live run."""
    derived = (provenance or {}).get("derived_from")
    return isinstance(derived, str) and derived.startswith(LIVE_PROVENANCE_PREFIX)


def _strip_edges(code: str) -> str:
    return re.sub(r"^[\W_]+|[\W_]+$", "", code)


def code_forms(candidate_code: str, observed_code: str | None = None) -> list[str]:
    """The printed forms of a code that count as the code appearing in a text."""
    forms = [normalize_for_span(candidate_code), normalize_for_span(_strip_edges(candidate_code))]
    if observed_code and codes_match(candidate_code, observed_code):
        forms.append(normalize_for_span(observed_code))
    out: list[str] = []
    for form in forms:
        if form and form not in out:
            out.append(form)
    return out


def code_in_text(candidate_code: str, text: str | None, observed_code: str | None = None) -> bool:
    """True when a form of the code occurs in the normalized text between non alphanumerics."""
    if not isinstance(text, str) or not text:
        return False
    norm = normalize_for_span(text)
    for form in code_forms(candidate_code, observed_code):
        pattern = r"(?<![^\W_])" + re.escape(form) + r"(?![^\W_])"
        if re.search(pattern, norm):
            return True
    return False


def check_grounding(
    candidate_code: str | None,
    cited_source: dict[str, Any] | None,
    *,
    page_text: str | None,
    snippet: str | None,
    mode: str,
    provenance: dict | None,
    evidence: str | None = None,
    observed_code: str | None = None,
    had_raw_text: bool = False,
) -> GroundingResult:
    """Ground one candidate code in its cited source (PLAN 8.5 rule 4).

    `page_text` is the cited source's raw text (None when none was recorded
    or, in replay, when it is not in the pages folder), `snippet` its search
    snippet. A null code has nothing to ground.
    `evidence` is accepted for the caller's convenience and never grounds a
    code (see the module docstring). `had_raw_text` says the recorded run
    saved the cited page's text (the recording names its text_sha256): a
    replayed live recording missing that text locally is unverifiable, never
    verified from its snippet, since live checked the page, not the snippet.
    """
    if candidate_code is None or not str(candidate_code).strip():
        return GroundingResult(NOT_APPLICABLE, "no code to ground")
    if (mode == REPLAY_MODE and cited_source is not None and not page_text and had_raw_text
            and recorded_live(provenance)):
        return GroundingResult(UNVERIFIABLE, LIVE_NO_TEXT)
    if cited_source is None:
        verdict = GroundingResult(FAILED, NO_SOURCE)
    elif page_text:
        if code_in_text(candidate_code, page_text, observed_code):
            return GroundingResult(VERIFIED, IN_RAW_TEXT)
        verdict = GroundingResult(FAILED, NOT_FOUND_RAW)
    elif code_in_text(candidate_code, snippet, observed_code):
        return GroundingResult(VERIFIED, IN_SNIPPET)
    else:
        verdict = GroundingResult(FAILED, NOT_FOUND_SNIPPET)
    if mode == REPLAY_MODE and derived_from_v1(provenance):
        return GroundingResult(UNVERIFIABLE, V1_EXEMPT)
    if mode == REPLAY_MODE and cited_source is not None and not page_text and recorded_live(provenance):
        return GroundingResult(UNVERIFIABLE, LIVE_NO_TEXT)
    return verdict


def summarize(results: list[GroundingResult], *, mode: str, provenance: dict | None) -> str:
    """One status for a brief: failed if any failed; unverifiable for a v1 derived
    replay (even with no code to check, since nothing was recorded to check
    against) or if any code was unverifiable; verified if at least one code was
    checked and found; else not_applicable (no code to ground)."""
    statuses = {r.status for r in results}
    if FAILED in statuses:
        return FAILED
    if UNVERIFIABLE in statuses or (mode == REPLAY_MODE and derived_from_v1(provenance)):
        return UNVERIFIABLE
    if VERIFIED in statuses:
        return VERIFIED
    return NOT_APPLICABLE
