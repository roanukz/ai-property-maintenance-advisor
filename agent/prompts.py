"""Prompts, ported from v1 lib/prompts.ts close to word for word (PLAN 8.8, 6.4).

Changes from the v1 wording are the rows of the PLAN 8.8 table, the dash
replacements of PLAN 6.4, and the changes below, which have no PLAN 8.8 row
yet and are PENDING Roanuk's approval at the Phase 2 gate (decision 30):

- J1: "fetched" becomes "provided" in both places ("Warranty cautions cite
  their provided source the same way; use null only when no source was
  provided for that caution").
- J2: v1's clause that "sources" lists whatever was fetched is cut (code
  builds the sources).
- J3: '"sources" must be non-empty' becomes "at least one provided source must
  be cited" (code builds and prunes the sources).
- The research preamble sentence ("You find documentation for ...").
- The synthesis user message headings SOURCES_HEADING and
  PRIOR_ERRORS_HEADING, and the research message's prior errors block.
- The property records block (REGISTRY_HEADING, Phase 3): the registry
  dates and warranty terms with their record_id (PLAN 8.3, decision 29).
- The confirmed code line in both user messages (OBSERVED_CODE_LINE or
  NO_OBSERVED_CODE_LINE): code tells both models which code the owner
  confirmed, since validate enforces that code and not the symptom text.
  Rule 4 itself stays v1's wording.

Prompts ask; validate enforces. The v1 output format section is cut
(structured output supplies the shape) and JSON rule J5 ("do not pad") is cut
because code prunes uncited sources. agent/tests/test_prompts.py holds the
unchanged v1 sentences as the published contract.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

# ---------------------------------------------------------------------------
# read_plate (E1 to E5; E3's dash becomes a semicolon, PLAN 6.4)
# ---------------------------------------------------------------------------

EXTRACT_SYSTEM = """You read equipment data plates from photos and extract the unit's identity.

Rules:
- Extract only what is clearly legible in the photo. Transcribe exactly what is printed, including hyphens and letter/digit distinctions.
- Anything not clearly readable is null with confidence "unreadable". A field you can read but are not certain about (glare, partial blur, ambiguous characters) gets confidence "low".
- Never complete, correct, or infer a partial model number from world knowledge. If half the model number is under glare, the field is null and "unreadable"; do not guess the rest even if you recognize the product line.
- The manufacture date may appear as a date code, a "MFG" field, or be encoded in the serial number. Only report it if it is printed explicitly; do not decode serial numbers.
- The user will confirm or correct these fields before they are used, so an honest "unreadable" is more useful than a plausible guess."""

EXTRACT_USER = "Extract the equipment identity from this data plate photo."

# ---------------------------------------------------------------------------
# research (rule 1, research half of the PLAN 8.8 split)
# ---------------------------------------------------------------------------

RESEARCH_PROMPT = """You find documentation for a piece of residential equipment at a short-term rental property, so that a service brief can be written from it.

Use the search and fetch tools to find documentation matching this exact manufacturer and model, or its documented model family: owner's manual, error code tables, troubleshooting guides. Prefer official manufacturer pages, then dealer/retailer/repair-service pages, then community forums."""

# ---------------------------------------------------------------------------
# synthesize (the 9 brief rules with the PLAN 8.8 changes, plus the new quote
# rule of decision 24, and JSON rules J1 to J4)
# ---------------------------------------------------------------------------

DATE_TOKEN = "{current_date}"

SYNTHESIS_SYSTEM = """You produce a service brief for a piece of residential equipment at a short-term rental property. The reader is a technician or a cleaner standing in front of the unit; the owner is 90 miles away. Today's date is {current_date}.

## What you must do

1. Use only the numbered sources provided below.

2. Every factual claim in the brief must carry a citation to one of the numbered sources provided below, identified by its number. Each source you cite gets an honest tier label, which you propose in "source_tiers":
   - "manufacturer": official manual, spec sheet, or support page from the maker of this unit
   - "dealer": dealer, retailer, or repair service page
   - "forum": community content (forums, Reddit, Q&A sites, blogs by individuals)
   A dealer blog is "dealer" even if it is the best source found. Never inflate a tier.

3. Map the reported symptom to documented error codes or documented causes ONLY if the provided sources support the mapping. Quote or closely track the documented meaning and the documented action. Do not paraphrase actions in ways that change their meaning (do not turn "call for service" into "replace the part").

4. OBSERVED CODE SHORT-CIRCUIT: if the symptom reports a specific code visible on the unit's display or panel, that observed code overrides symptom matching. Return the single documented candidate for that code (mark it "confirmed": true), not a list of possibilities. Only widen back out if the sources show the code has multiple documented meanings for this model family.

5. "try_first" contains ONLY steps that are (a) documented in a provided source, (b) safe for an untrained person, and (c) actually actionable by someone standing at the unit. Never include "no action required", "monitor the situation", or "call a professional" as a step. Set "safety_flag": true on every step that switches power or gas off, on or reset (a breaker, a disconnect, a GFCI, a plug or a gas valve), that has the reader open, touch or work near anything that can be electrically live, hot or carrying gas, or that runs or tests a heater, and put those steps FIRST in the list.

6. NO RELIABLE ANSWER: if sources are insufficient or contradictory, or the model cannot be confidently matched to documentation, set "status": "no_reliable_answer" and fill "no_reliable_answer" with what you found and why it is not enough; the search trail is added by code. This is a success state of the product, not a failure: a clean "we don't know" beats a plausible guess. NEVER fall back to general knowledge about the equipment category ("hot tubs in general", "most heat pumps"). If you cannot find documentation for this model or its documented family, say so. If your searches surface nothing about a model, that strongly suggests it should be no_reliable_answer; do not soften this.

7. Never state or imply a diagnosis. You list documented candidates only, each with "why_shown" explaining why it surfaced (which documented symptom or code it matches). The footer of the rendered brief tells the reader to confirm any code on the unit's own display before ordering parts.

8. Warranty: Do not compute the unit's age; it is supplied from the property records or the manufacture date. If this equipment category commonly voids warranty for commercial or short-term rental use, say so as a caution, citing a provided source if you found one, and instruct the owner to verify against their own warranty document. Never assert that anything is or is not covered.

9. If service records for this appliance are listed below, compare them to the current symptom. Fill "happened_before" with whether one matches, a one line summary, and that record's record_id. If no records are listed, set "happened_before" to null.

10. Every evidence, authorship or interval quote must be copied exactly from the source excerpts shown; do not reword, join or shorten across a gap.

Rules for the JSON:
- "source_index" is a 0-based index into the numbered sources provided below. Every try_first step and every candidate must have a valid source_index. Warranty cautions cite their provided source the same way; use null only when no source was provided for that caution.
- When status is "no_reliable_answer": "try_first" and "candidates" are empty arrays and "no_reliable_answer" is filled.
- When status is "ok": at least one provided source must be cited and "no_reliable_answer" is null.
- "who" is "anyone" only when the documented action is safe and possible for an untrained person; otherwise "technician"."""


def synthesis_system_prompt(current_date: str) -> str:
    """SYNTHESIS_SYSTEM with today's date filled in."""
    return SYNTHESIS_SYSTEM.replace(DATE_TOKEN, current_date)


# ---------------------------------------------------------------------------
# User prompt builders
# ---------------------------------------------------------------------------

IDENTITY_HEADING = "Equipment identity (confirmed by the owner from the data plate):"
NOT_READABLE = "(not readable)"
NO_RECORDS = "No service records were provided."
RECORDS_HEADING = "Service records for this appliance:"
PRIOR_ERRORS_HEADING = (
    "Your previous answer failed validation for these reasons. Fix them in this answer:"
)
SOURCES_HEADING = (
    "Numbered sources (cite each by its number as source_index; each shows its title, "
    "host and verbatim excerpts):"
)
NO_SOURCES = "No sources were retrieved."
OBSERVED_CODE_LINE = (
    "Code the owner confirmed on the unit's display: {code}. Treat it as the observed code "
    "the symptom reports."
)
NO_OBSERVED_CODE_LINE = (
    "The owner confirmed no code on the unit's display, so no code in the symptom text counts as observed."
)
SYNTHESIS_CLOSING = "Produce the service brief now. Answer using the provided schema."
RESEARCH_CLOSING = "Search first."


def _field(identity: Mapping[str, Any] | None, key: str) -> str:
    value = (identity or {}).get(key)
    return value if isinstance(value, str) and value else NOT_READABLE


def identity_lines(identity: Mapping[str, Any] | None, symptom: str) -> list[str]:
    """v1's identity block and symptom line (prompts.ts:74 to 81)."""
    return [
        IDENTITY_HEADING,
        f"- Manufacturer: {_field(identity, 'manufacturer')}",
        f"- Model: {_field(identity, 'model')}",
        f"- Serial: {_field(identity, 'serial')}",
        f"- Manufacture date: {_field(identity, 'manufacture_date')}",
        "",
        f"Reported symptom: {symptom}",
    ]


def observed_code_lines(observed_code: str | None) -> list[str]:
    """The confirmed code line: the code validate will enforce, or that there is none."""
    if observed_code:
        return ["", OBSERVED_CODE_LINE.format(code=observed_code)]
    return ["", NO_OBSERVED_CODE_LINE]


def records_lines(history_hits: Sequence[Mapping[str, Any]] | None) -> list[str]:
    """The service records block, each record with its record_id (rule 9)."""
    if not history_hits:
        return [NO_RECORDS]
    lines = [RECORDS_HEADING]
    for rec in history_hits:
        parts = [f"record_id: {rec.get('record_id') or ''}", f"date: {rec.get('date') or ''}",
                 f"symptom: {rec.get('symptom') or ''}"]
        if rec.get("observed_code"):
            parts.append(f"observed code: {rec['observed_code']}")
        if rec.get("work_done"):
            parts.append(f"work done: {rec['work_done']}")
        lines.append("- " + "; ".join(parts))
    return lines


REGISTRY_HEADING = (
    "Property records for this unit (from the owner's registry; code computes the unit's age from them, "
    "and warranty terms are shown to the reader exactly as recorded):"
)


def registry_lines(appliance: Mapping[str, Any] | None) -> list[str]:
    """The registry dates and warranty terms block (PLAN 8.3, decision 29), or nothing."""
    if not appliance:
        return []
    parts = [f"record_id: {appliance.get('record_id') or ''}"]
    for key, label in (("purchase_date", "purchase date"), ("install_date", "install date")):
        if appliance.get(key):
            parts.append(f"{label}: {appliance[key]}")
    if appliance.get("warranty_terms"):
        parts.append(f'warranty terms: "{appliance["warranty_terms"]}"')
    return ["", REGISTRY_HEADING, "- " + "; ".join(parts)]


def prior_errors_lines(errors: Sequence[str] | None) -> list[str]:
    """Validation errors from the previous attempt, shown on a retry (PLAN 8.4)."""
    if not errors:
        return []
    return ["", PRIOR_ERRORS_HEADING, *(f"- {e}" for e in errors)]


def build_synthesis_user_prompt(
    *,
    identity: Mapping[str, Any] | None,
    symptom: str,
    sources_block: str,
    history_hits: Sequence[Mapping[str, Any]] | None = None,
    prior_errors: Sequence[str] | None = None,
    observed_code: str | None = None,
    registry: Mapping[str, Any] | None = None,
) -> str:
    """The synthesize user message: identity, symptom, confirmed code, records, registry, sources, prior errors."""
    lines = identity_lines(identity, symptom)
    lines += observed_code_lines(observed_code)
    lines += ["", *records_lines(history_hits)]
    lines += registry_lines(registry)
    lines += ["", SOURCES_HEADING, "", sources_block or NO_SOURCES]
    lines += prior_errors_lines(prior_errors)
    lines += ["", SYNTHESIS_CLOSING]
    return "\n".join(lines)


def build_research_user_prompt(
    *,
    identity: Mapping[str, Any] | None,
    symptom: str,
    prior_errors: Sequence[str] | None = None,
    observed_code: str | None = None,
) -> str:
    """The research agent's task message: identity, symptom, confirmed code, prior errors."""
    lines = identity_lines(identity, symptom)
    lines += observed_code_lines(observed_code)
    lines += prior_errors_lines(prior_errors)
    lines += ["", RESEARCH_CLOSING]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# route classifier (PLAN 8.4 rows 3 and 4; new in v2, no v1 counterpart)
# ---------------------------------------------------------------------------

CLASSIFIER_SYSTEM = """You decide whether a reported symptom matches the documented causes already on file for one piece of residential equipment.

Answer with exactly one verdict:
- "match": the symptom plainly describes one of the documented causes or codes listed below.
- "no_match": the symptom describes none of them.
- "unsure": you cannot tell from the listed causes alone.

Use only the documented causes listed. Never use general knowledge about the equipment category. When in doubt, answer "unsure"."""

CLASSIFIER_CAUSES_HEADING = "Documented causes on file (each quoted from its source):"
CLASSIFIER_NO_CAUSES = "No documented causes are on file."
CLASSIFIER_CLOSING = "Give your verdict now. Answer using the provided schema."


def classifier_cause_lines(documented: Sequence[Mapping[str, Any]] | None) -> list[str]:
    """One line per documented cause: its code, when it has one, and the verbatim evidence."""
    if not documented:
        return [CLASSIFIER_NO_CAUSES]
    lines = [CLASSIFIER_CAUSES_HEADING]
    for cause in documented:
        code = cause.get("code")
        evidence = cause.get("evidence") or ""
        lines.append(f'- code {code}: "{evidence}"' if code else f'- "{evidence}"')
    return lines


def build_classifier_user_prompt(
    *,
    identity: Mapping[str, Any] | None,
    symptom: str,
    documented: Sequence[Mapping[str, Any]] | None,
) -> str:
    """The classifier's user message: identity, symptom and the documented causes on file."""
    lines = identity_lines(identity, symptom)
    lines += ["", *classifier_cause_lines(documented)]
    lines += ["", CLASSIFIER_CLOSING]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# safety_check: Jev's one Noul per try_first step: SC12a wording 3, chosen on the
# tune half (the brief's starting wording plus criteria from the label protocol).
# Its question_hash is recorded with every answer, and replay refuses an answer
# recorded under another wording.
# ---------------------------------------------------------------------------

SAFETY_STEP_NOUL = {
    "instructions": (
        "Doing this step, or doing it wrong, involves electricity, gas or overheating: it switches power "
        "or gas off, on or reset; it has the reader open, touch or work near something that can be "
        "electrically live, hot or carrying gas; it checks such a hazard before contact; or it runs or "
        "tests a heater as a diagnostic."
    ),
    "criteria": {
        "true": (
            "The step, or doing it wrong, involves electricity, gas or overheating: it switches power or gas "
            "off, on or reset (a circuit breaker, a disconnect, a GFCI, unplugging the unit, a power switch "
            "used to cut power for service, or a gas valve); it has the reader open, touch or work near "
            "something that can be electrically live, hot or carrying gas (an equipment panel or cabinet, "
            "wiring, a heater or heating element, a burner, hot water or a hot surface); it checks one of "
            "those hazards before contact, such as reading an indicator light before touching anything; or "
            "it runs or tests a heater or burner as a diagnostic. Removing or opening an access door, panel, "
            "cover or control box on the equipment counts as working near something that can be live."
        ),
        "false": (
            "Ordinary use of the controls (pressing a button, changing a mode or setpoint for normal use), "
            "checking water level or water chemistry, cleaning or swapping a filter, or a step whose only "
            "hazard is mechanical, chemical, pressure, water or a fall, unless the step or its detail brings "
            "in one of the yes cases. Refrigerant, nitrogen, vacuum or other pressure work, and running the "
            "equipment with the filter out, are no unless the step or its detail brings in one of the yes "
            "cases."
        ),
    },
}
