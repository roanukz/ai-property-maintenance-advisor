"""The ported prompts keep v1's wording except the approved changes (PLAN 8.8, 6.4).

The sentences below are the published contract: v1 lib/prompts.ts text that v2
must carry unchanged, and the exact v2 text of every PLAN 8.8 and 6.4 row.
Each test names the mutation that turns it red.
"""

from __future__ import annotations

import pytest

from agent import prompts

DASHES = (chr(0x2014), chr(0x2013))

# v1 prompts.ts sentences v2 keeps verbatim (E1, E2, E4, E5, the preamble,
# rule 2's tier lines, rules 4 and 7, the unchanged parts of rules 3, 5, 6, 8,
# and JSON rule J4).
V1_EXTRACT_UNCHANGED = (
    "You read equipment data plates from photos and extract the unit's identity.",
    "- Extract only what is clearly legible in the photo. Transcribe exactly what is printed, "
    "including hyphens and letter/digit distinctions.",
    '- Anything not clearly readable is null with confidence "unreadable". A field you can read '
    'but are not certain about (glare, partial blur, ambiguous characters) gets confidence "low".',
    '- The manufacture date may appear as a date code, a "MFG" field, or be encoded in the serial '
    "number. Only report it if it is printed explicitly; do not decode serial numbers.",
    "- The user will confirm or correct these fields before they are used, so an honest "
    '"unreadable" is more useful than a plausible guess.',
)
# v1 lib/anthropic.ts:112, the extraction user message.
V1_EXTRACT_USER = "Extract the equipment identity from this data plate photo."

V1_SYNTHESIS_UNCHANGED = (
    "You produce a service brief for a piece of residential equipment at a short-term rental "
    "property. The reader is a technician or a cleaner standing in front of the unit; the owner "
    "is 90 miles away. Today's date is ",
    "## What you must do",
    '   - "manufacturer": official manual, spec sheet, or support page from the maker of this unit',
    '   - "dealer": dealer, retailer, or repair service page',
    '   - "forum": community content (forums, Reddit, Q&A sites, blogs by individuals)',
    '   A dealer blog is "dealer" even if it is the best source found. Never inflate a tier.',
    "Quote or closely track the documented meaning and the documented action. Do not paraphrase "
    'actions in ways that change their meaning (do not turn "call for service" into "replace the part").',
    "4. OBSERVED CODE SHORT-CIRCUIT: if the symptom reports a specific code visible on the unit's "
    "display or panel, that observed code overrides symptom matching. Return the single documented "
    'candidate for that code (mark it "confirmed": true), not a list of possibilities. Only widen '
    "back out if the sources show the code has multiple documented meanings for this model family.",
    "(b) safe for an untrained person, and (c) actually actionable by someone standing at the unit. "
    'Never include "no action required", "monitor the situation", or "call a professional" as a '
    'step. Safety-relevant steps (overheat, electrical, gas) go FIRST in the list with "safety_flag": true.',
    '6. NO RELIABLE ANSWER: if sources are insufficient or contradictory, or the model cannot be '
    'confidently matched to documentation, set "status": "no_reliable_answer" and fill '
    '"no_reliable_answer" with ',
    'NEVER fall back to general knowledge about the equipment category ("hot tubs in general", '
    '"most heat pumps"). If you cannot find documentation for this model or its documented family, '
    "say so.",
    "7. Never state or imply a diagnosis. You list documented candidates only, each with "
    '"why_shown" explaining why it surfaced (which documented symptom or code it matches). The '
    "footer of the rendered brief tells the reader to confirm any code on the unit's own display "
    "before ordering parts.",
    "If this equipment category commonly voids warranty for commercial or short-term rental use, "
    "say so as a caution, citing a ",
    " source if you found one, and instruct the owner to verify against their own warranty "
    "document. Never assert that anything is or is not covered.",
    "Rules for the JSON:",
    'Every try_first step and every candidate must have a valid source_index.',
    '- "who" is "anyone" only when the documented action is safe and possible for an untrained '
    'person; otherwise "technician".',
)
V1_RESEARCH_UNCHANGED = (
    "or its documented model family: owner's manual, error code tables, troubleshooting guides. "
    "Prefer official manufacturer pages, then dealer/retailer/repair-service pages, then community forums.",
)

# PLAN 6.4: dashes replaced by a semicolon or colon.
DASH_ROWS = (
    (prompts.EXTRACT_SYSTEM,
     'If half the model number is under glare, the field is null and "unreadable"; do not guess '
     "the rest even if you recognize the product line."),
    (prompts.SYNTHESIS_SYSTEM,
     'This is a success state of the product, not a failure: a clean "we don\'t know" beats a '
     "plausible guess."),
    (prompts.SYNTHESIS_SYSTEM,
     "If your searches surface nothing about a model, that strongly suggests it should be "
     "no_reliable_answer; do not soften this."),
)

# PLAN 8.8 rows, exact v2 text.
TABLE_ROWS = (
    (prompts.RESEARCH_PROMPT,
     "Use the search and fetch tools to find documentation matching this exact manufacturer and model, "),
    (prompts.SYNTHESIS_SYSTEM, "1. Use only the numbered sources provided below.\n"),
    (prompts.SYNTHESIS_SYSTEM,
     "2. Every factual claim in the brief must carry a citation to one of the numbered sources "
     "provided below, identified by its number."),
    (prompts.SYNTHESIS_SYSTEM, 'propose in "source_tiers"'),
    (prompts.SYNTHESIS_SYSTEM,
     "3. Map the reported symptom to documented error codes or documented causes ONLY if the "
     "provided sources support the mapping."),
    (prompts.SYNTHESIS_SYSTEM, '5. "try_first" contains ONLY steps that are (a) documented in a provided source,'),
    (prompts.SYNTHESIS_SYSTEM,
     'fill "no_reliable_answer" with what you found and why it is not enough; the search trail is '
     "added by code."),
    (prompts.SYNTHESIS_SYSTEM,
     "8. Warranty: Do not compute the unit's age; it is supplied from the property records or the "
     "manufacture date."),
    (prompts.SYNTHESIS_SYSTEM, "citing a provided source if you found one"),
    (prompts.SYNTHESIS_SYSTEM,
     "9. If service records for this appliance are listed below, compare them to the current "
     'symptom. Fill "happened_before" with whether one matches, a one line summary, and that '
     'record\'s record_id. If no records are listed, set "happened_before" to null.'),
    (prompts.SYNTHESIS_SYSTEM,
     "Every evidence, authorship or interval quote must be copied exactly from the source excerpts "
     "shown; do not reword, join or shorten across a gap."),
)

# Text v2 cuts: the search tool, the JSON fence and output format section, J5,
# "Do not invent sources", pasted notes, and "fetched" (code builds sources).
CUT = (
    "Search the live web",
    "```",
    "## Output format",
    "fenced",
    "Do not pad the bibliography",
    "Do not invent sources",
    "fetched",
    "exactly what you searched for",
    "install-date math",
    "Prior service notes",
    "Search first, then answer in the required JSON format.",
)


def _all_prompt_text() -> list[str]:
    identity = {"manufacturer": "M", "model": "X", "serial": "", "manufacture_date": None}
    return [
        prompts.EXTRACT_SYSTEM,
        prompts.EXTRACT_USER,
        prompts.RESEARCH_PROMPT,
        prompts.SYNTHESIS_SYSTEM,
        prompts.synthesis_system_prompt("2026-09-18"),
        prompts.build_synthesis_user_prompt(
            identity=identity, symptom="s", sources_block="[0] t",
            history_hits=[{"record_id": "service:r1", "date": "2026-01-02", "symptom": "x"}],
            prior_errors=["e"],
        ),
        prompts.build_research_user_prompt(identity=identity, symptom="s", prior_errors=["e"]),
    ]


def test_unchanged_v1_rules_are_verbatim() -> None:
    """Mutation prompts_rule4_reworded: drop "single" from rule 4; the rule no longer matches v1."""
    for sentence in V1_EXTRACT_UNCHANGED:
        assert sentence in prompts.EXTRACT_SYSTEM, sentence
    assert prompts.EXTRACT_USER == V1_EXTRACT_USER
    for sentence in V1_SYNTHESIS_UNCHANGED:
        assert sentence in prompts.SYNTHESIS_SYSTEM, sentence
    for sentence in V1_RESEARCH_UNCHANGED:
        assert sentence in prompts.RESEARCH_PROMPT, sentence


def test_rules_are_numbered_one_to_ten_in_order() -> None:
    """Mutation prompts_rule7_dropped: cut rule 7; the numbering check fails."""
    text = prompts.SYNTHESIS_SYSTEM
    positions = [text.index(f"\n{n}. ") for n in range(1, 11)]
    assert positions == sorted(positions)


@pytest.mark.parametrize("prompt,text", DASH_ROWS, ids=[f"dash{i}" for i in range(len(DASH_ROWS))])
def test_dash_replacements_follow_plan_6_4(prompt: str, text: str) -> None:
    """Mutation prompts_e3_dash_back: restore E3's em dash; the semicolon text is gone."""
    assert text in prompt


@pytest.mark.parametrize("prompt,text", TABLE_ROWS, ids=[f"row{i}" for i in range(len(TABLE_ROWS))])
def test_plan_8_8_rows_use_the_approved_text(prompt: str, text: str) -> None:
    """Mutation prompts_rule9_notes_back: restore v1 rule 9 ("prior service notes"); its row fails."""
    assert text in prompt


def test_cut_text_is_gone() -> None:
    """Mutation prompts_do_not_invent_back: re-add "Do not invent sources." to rule 6."""
    for prompt in _all_prompt_text():
        for text in CUT:
            assert text not in prompt, text


def test_no_em_or_en_dash_in_any_prompt() -> None:
    """Mutation prompts_e3_dash_back also turns this red."""
    for prompt in _all_prompt_text():
        for dash in DASHES:
            assert dash not in prompt


def test_synthesis_prompt_fills_the_date() -> None:
    """Mutation prompts_date_not_filled: synthesis_system_prompt returns the template unchanged."""
    filled = prompts.synthesis_system_prompt("2026-09-18")
    assert "Today's date is 2026-09-18." in filled
    assert prompts.DATE_TOKEN not in filled


def test_synthesis_user_prompt_blocks() -> None:
    """Mutation prompts_records_without_id: drop record_id from each record line."""
    text = prompts.build_synthesis_user_prompt(
        identity={"manufacturer": "Sundance Spas", "model": "Optima 880", "serial": "", "manufacture_date": None},
        symptom="panel shows FLO",
        sources_block="[0] Title\nHost: h.example\nExcerpts:\nquote",
        history_hits=[{"record_id": "service:svc-1", "date": "2025-07-01", "symptom": "not heating",
                       "observed_code": "FLO", "work_done": "cleaned filter"}],
        prior_errors=None,
    )
    lines = text.splitlines()
    assert lines[:7] == [
        "Equipment identity (confirmed by the owner from the data plate):",
        "- Manufacturer: Sundance Spas",
        "- Model: Optima 880",
        "- Serial: (not readable)",
        "- Manufacture date: (not readable)",
        "",
        "Reported symptom: panel shows FLO",
    ]
    assert "- record_id: service:svc-1; date: 2025-07-01; symptom: not heating; observed code: FLO; " \
           "work done: cleaned filter" in lines
    assert "[0] Title" in lines
    assert prompts.PRIOR_ERRORS_HEADING not in text
    assert lines[-1] == "Produce the service brief now. Answer using the provided schema."
    empty = prompts.build_synthesis_user_prompt(identity=None, symptom="s", sources_block="")
    assert "No service records were provided." in empty
    assert prompts.NO_SOURCES in empty


def test_prior_errors_shown_only_on_a_retry() -> None:
    """Mutation prompts_prior_errors_dropped: prior_errors_lines returns []; the retry loses its errors."""
    text = prompts.build_synthesis_user_prompt(
        identity=None, symptom="s", sources_block="[0] t", prior_errors=["candidate 0 cites source 9"])
    assert prompts.PRIOR_ERRORS_HEADING in text
    assert "- candidate 0 cites source 9" in text
    research = prompts.build_research_user_prompt(identity=None, symptom="s", prior_errors=["x failed"])
    assert "- x failed" in research
    assert research.splitlines()[-1] == "Search first."
