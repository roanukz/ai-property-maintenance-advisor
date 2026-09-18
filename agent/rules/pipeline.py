"""The rules entry point validate calls (PLAN 8.5, decision 27).

`run_rules` is pure: no I/O, no model, no state. Order:

0. The model's draft is checked against `BriefDraft` and converted to the
   parity shape with the run's code built sources ("" becomes None; the
   refusal block's `searched` comes from the search trail).
   A registry source the draft does not cite and whose URL is not http or
   https is dropped here (indexes renumbered), so the parity layer's source
   URL check applies, as in v1, only to sources the brief cites; research
   never registers such a URL in the first place.
1. The v1 parity layer (`v1_parity.validate_brief_v1`, first failure wins).
   It also keeps v1's rule that an ok brief nulls a filled refusal block
   (rule 12, risk R11).
2. Citations resolve into this run's source registry (rule 1).
3. Tiers are lowered to the host ceiling (rule 2).
4. The observed code rule (rule 3).
5. Code grounding (rule 4, `agent/rules/grounding.py`): each kept
   candidate code must appear in its cited source's raw text, else its
   snippet; a v1 derived replay reports "unverifiable" instead of failing.
6. Safety steps first (rule 5).
7. Refusal and budget stop clearing (rule 6).
8. happened_before must cite a loaded record of this appliance (rule 7).
9. Registry dates: code writes the age statement (rule 8).
10. Upgrade and maintenance evidence (rule 9, `agent/rules/upgrades.py` and
    `agent/rules/maintenance.py`): an entry is dropped unless its index
    resolves and its evidence quote verifies verbatim in the cited page with
    the successor model (or, for maintenance, the interval, on a
    manufacturer tier source) inside it; code writes `due_date` from the
    registry's maintenance_log.
11. Prices (rule 10, `agent/rules/prices.py`, decision 28): an unquoted
    currency amount drops an upgrade or maintenance entry and fails
    validation in a v1 field.
12. Prune uncited sources and renumber every index (rule 11), then an ok
    brief must still cite at least one source.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any

from pydantic import ValidationError

from agent.rules import citations, clearing, grounding, observed_code as observed_rule, safety, tiers
from agent.rules import maintenance as maintenance_rule
from agent.rules import prices as prices_rule
from agent.rules import upgrades as upgrades_rule
from agent.rules.v1_parity import ParityError, validate_brief_v1
from agent.schemas import HTTP_URL, Brief, BriefDraft, draft_to_brief_dict

PASS_KINDS = ("synthesize", "upgrade")
DRAFT_SCHEMA = "draft_schema"
OK_NO_CITED_SOURCES = "ok_no_cited_sources"
BRIEF_SCHEMA = "brief_schema"

# The registry entry fields a brief source keeps (PLAN 7.3). Tiers come only
# from the draft's proposals and the host ceiling, never from the registry.
_BRIEF_SOURCE_KEYS = ("url", "title", "host", "retrieved_at", "origin")


@dataclass
class RulesResult:
    """What validate needs: the brief, or the errors that stop it."""

    brief: dict | None
    errors: list[str] = field(default_factory=list)
    refusal_origin: str | None = None
    # Rule 4's report: one entry per kept candidate code, and the run's summary.
    grounding: list[dict[str, Any]] = field(default_factory=list)
    grounding_status: str | None = None
    # Rules 9 and 10: upgrade and maintenance entries dropped, each with its reason.
    dropped: list[dict[str, Any]] = field(default_factory=list)


def _schema_errors(prefix: str, exc: ValidationError) -> list[str]:
    return [
        f"{prefix}: {'.'.join(str(p) for p in err['loc']) or '<root>'}: {err['msg']}"
        for err in exc.errors()
    ]


def _registry_source(source: dict[str, Any]) -> dict[str, Any]:
    return {k: source[k] for k in _BRIEF_SOURCE_KEYS if k in source}


def drop_uncited_non_web(shaped: dict[str, Any]) -> None:
    """Drop uncited sources whose URL is not web and renumber the in range indexes (in place).

    An index out of range stays as it is and stays out of range, since only
    uncited sources are dropped, so the parity layer still rejects it.
    """
    sources = shaped.get("sources") or []
    cited = citations.cited_indexes(shaped)
    keep = [i for i, s in enumerate(sources)
            if i in cited or (isinstance(s.get("url"), str) and HTTP_URL.match(s["url"]))]
    if len(keep) == len(sources):
        return
    new_index = {old: new for new, old in enumerate(keep)}
    for key in citations.CITING_LISTS:
        for entry in shaped.get(key) or []:
            entry["source_index"] = new_index.get(entry["source_index"], entry["source_index"])
    for caution in citations.cautions(shaped):
        index = caution.get("source_index")
        if index is not None:
            caution["source_index"] = new_index.get(index, index)
    shaped["sources"] = [sources[i] for i in keep]


def check_grounding(brief: dict[str, Any], *, sources: list[dict[str, Any]], mode: str,
                    provenance: dict | None, page_texts: dict[str, str],
                    report: list[dict[str, Any]] | None = None) -> list[str]:
    """Rule 4, code grounding (decision 25): errors for codes that fail.

    Each candidate's cited source is looked up by URL in the run's registry
    for its snippet, and in `page_texts` for its raw text. One entry per
    candidate with a code is appended to `report` when one is given.
    """
    by_url = {s.get("url"): s for s in sources}
    brief_sources = brief.get("sources") or []
    errors: list[str] = []
    for i, cand in enumerate(brief.get("candidates") or []):
        code = cand.get("code")
        if code is None:
            continue
        index = cand.get("source_index")
        cited = brief_sources[index] if isinstance(index, int) and 0 <= index < len(brief_sources) else None
        url = (cited or {}).get("url")
        result = grounding.check_grounding(
            str(code), cited, page_text=page_texts.get(url), snippet=(by_url.get(url) or {}).get("snippet"),
            mode=mode, provenance=provenance, evidence=cand.get("evidence"),
            observed_code=brief.get("observed_code"),
        )
        if report is not None:
            report.append({"candidate": i, "code": str(code), "source_url": url,
                           "status": result.status, "reason": result.reason})
        if not result.passes:
            errors.append(f"{grounding.GROUNDING_FAILED}: candidate {i} code {str(code)!r}: {result.reason}")
    return errors


def check_upgrade_and_maintenance_evidence(brief: dict[str, Any], *, page_texts: dict[str, str],
                                           maintenance_log: list[dict[str, Any]] | None = None,
                                           appliance_id: str | None = None,
                                           report: list[dict[str, Any]] | None = None) -> list[str]:
    """Rule 9: drop upgrade and maintenance entries whose evidence does not verify (in place).

    Runs after tiers (rule 2), so a maintenance entry sees its source's final
    tier. Never an error: a bad entry costs the entry, not the brief. It checks
    an answer's entries only: on a refusal or a budget stop, rule 6 clearing
    alone owns these lists, so a broken clearing rule is never hidden here.
    """
    if brief.get("status") != "ok":
        return []
    upgrades_rule.check_upgrade_options(brief, page_texts=page_texts, report=report)
    maintenance_rule.check_maintenance(brief, page_texts=page_texts, maintenance_log=maintenance_log,
                                       appliance_id=appliance_id, report=report)
    return []


def check_prices(brief: dict[str, Any], *, page_texts: dict[str, str],
                 report: list[dict[str, Any]] | None = None) -> list[str]:
    """Rule 10 (decision 28): unquoted amounts drop entries (on an answer only, as
    rule 9), and fail a v1 field on every status."""
    return prices_rule.check_prices(brief, page_texts=page_texts, report=report,
                                    entries=brief.get("status") == "ok")


def run_rules(
    draft: dict,
    *,
    sources: list[dict],
    observed_code: str | None,
    history_hits: list[dict],
    registry: dict | None,
    mode: str,
    provenance: dict | None,
    pass_kind: str,
    identity: dict | None = None,
    search_trail: list[dict] | None = None,
    page_texts: dict[str, str] | None = None,
    today: date | None = None,
    maintenance_log: list[dict] | None = None,
) -> RulesResult:
    """Run every v2 rule over one model draft.

    `draft` is the model's `BriefDraft` as a dict; `sources` is the run's
    append only source registry, which the draft's indexes point into;
    `registry` is the appliance row (`Registry.get_appliance`) or None;
    `identity` gives the maker (tier ceiling) and the manufacture date (age);
    `search_trail` gives the refusal block's `searched`; `page_texts` maps a
    source URL to its fetched raw text; `maintenance_log` is the appliance's
    registry maintenance rows (`Registry.maintenance_log`), from which rule 9
    writes due dates. `pass_kind` is "synthesize", or "upgrade" for the pass
    after upgrade_check added graph derived options to the draft: the same
    rules run on both, so a graph option is checked like a model option and
    pruning and renumbering come after it is in (decision 27).
    """
    if pass_kind not in PASS_KINDS:
        raise ValueError(f"unknown pass_kind {pass_kind!r}; expected one of {PASS_KINDS}")
    identity = identity or {}
    page_texts = page_texts or {}
    today = today or datetime.now(timezone.utc).date()

    try:
        parsed = BriefDraft.model_validate(draft)
    except ValidationError as exc:
        return RulesResult(brief=None, errors=_schema_errors(DRAFT_SCHEMA, exc))
    shaped = draft_to_brief_dict(
        parsed,
        [_registry_source(s) for s in sources],
        observed_code=observed_code,
        searched=clearing.searched_from_trail(search_trail),
    )

    drop_uncited_non_web(shaped)
    try:
        brief = validate_brief_v1(shaped, accept_budget_stopped=True)
    except ParityError as exc:
        return RulesResult(brief=None, errors=[f"{exc.reason}: {exc.message}"])

    citations.resolve_citations(brief)
    brief["sources"] = tiers.apply_tiers(brief["sources"], manufacturer=identity.get("manufacturer"),
                                         page_texts=page_texts)
    errors = observed_rule.apply_observed_code(brief, observed_code)
    grounding_report: list[dict[str, Any]] = []
    grounding_checked = not errors
    if grounding_checked:  # a rule 3 failure already rejects the draft; its candidates were not narrowed
        errors += check_grounding(brief, sources=sources, mode=mode, provenance=provenance,
                                  page_texts=page_texts, report=grounding_report)
    safety.apply_safety_order(brief)
    clearing.clear_on_refusal(brief)
    citations.check_happened_before(brief, history_hits, (registry or {}).get("id"))
    citations.apply_registry_dates(brief, registry=registry,
                                   manufacture_date=identity.get("manufacture_date"), today=today)
    dropped: list[dict[str, Any]] = []
    errors += check_upgrade_and_maintenance_evidence(brief, page_texts=page_texts,
                                                     maintenance_log=maintenance_log,
                                                     appliance_id=(registry or {}).get("id"), report=dropped)
    errors += check_prices(brief, page_texts=page_texts, report=dropped)
    grounding_status = grounding.summarize(
        [grounding.GroundingResult(e["status"], e["reason"]) for e in grounding_report],
        mode=mode, provenance=provenance) if grounding_checked else grounding.NOT_CHECKED
    if errors:
        return RulesResult(brief=None, errors=errors, grounding=grounding_report,
                           grounding_status=grounding_status, dropped=dropped)

    citations.prune_and_renumber(brief)
    if brief["status"] == "ok" and not brief["sources"]:
        return RulesResult(brief=None, errors=[
            f"{OK_NO_CITED_SOURCES}: an ok brief must cite at least one provided source"
        ])

    try:
        final = Brief.model_validate(brief).model_dump(mode="json")
    except ValidationError as exc:
        return RulesResult(brief=None, errors=_schema_errors(BRIEF_SCHEMA, exc))
    origin = "model" if final["status"] == "no_reliable_answer" else None
    return RulesResult(brief=final, errors=[], refusal_origin=origin, grounding=grounding_report,
                       grounding_status=grounding_status, dropped=dropped)
