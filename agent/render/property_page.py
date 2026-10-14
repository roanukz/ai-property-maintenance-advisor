"""Static property page (PLAN 8.13 `advisor property show`, PRD:109 and PRD:112).

`render_property_page(property_id, registry, graph=None, generated_at=...)`
returns one self contained HTML page for a property: its appliances (with
each one's exact model number, escaped, or "not recorded"), the
"Plan a replacement" section, the "Product recalls" section, and each
appliance's service history, its maintenance log and what is known about
maintenance due.

Plan a replacement (SC14): one entry per appliance with the unit, its age
from the install date as of `generated_at`, and each replacement fact the
knowledge graph verifies for it (agent/sweep.py): the reason (discontinued or
parts unavailable), the successor only when the fact's quote names it, the
verbatim evidence, and its source link, retrieved date and evidence hash.
With no fact, or no graph, the entry says exactly sweep.NONE_DOCUMENTED. This
section replaces the page's earlier per appliance successor list, so a
successor is never shown twice.

Product recalls (SC15): one entry per appliance from its latest stored CPSC
check under `recalls_dir` (agent/product_recalls.py; the page makes no
request). The words are the module's: a product recall that prints the exact
model, with its number, title, hazard, remedy, link and the serial number sentence;
"No CPSC product recall names this exact model" with the date checked; a
check that did not run; an invented maker, never checked; or, with no stored
check, product_recalls.NOT_CHECKED_SENTENCE. Every value is escaped.

The page is labeled synthetic: the seed registry is synthetic test data
(PLAN 8.12), and the page says so near the top and in the footer. It follows
the brief renderer's safety rules (SC10): every value from the registry, the
graph or a source goes through `esc` (html.escape with quote=True), a URL
becomes a link only when it matches ^https?://, and the head carries the same
CSP meta and data: favicon, inline CSS and no external load.

Maintenance due: a due date is computed by validate from a manufacturer
interval quoted in a validated brief (PLAN 8.5 rule 9), never from the
registry alone. Pass those validated items as `maintenance_due`
({appliance_id: [MaintenanceItem dicts]}); without them the page states that
no documented interval is on file.

Warranty coverage (SC13): per appliance, a table of its `warranty_coverage`
rows: component, term, start event and date, end date and status (not
started, active, ended, end date unknown), each cited to its `warranty:<id>`
and labeled synthetic. Code computes the end dates and statuses with the
brief's own functions (agent/rules/coverage.py), as of `generated_at`, the
page's one UTC date, pinned once by the caller. An appliance with no rows
shows "No warranty on file.", or, when its record holds warranty terms text
(quoted just above), that the terms are not broken into components, so
nothing is computed from them.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date
from pathlib import Path
from typing import Any

from agent import product_recalls, sweep
from agent.render.brief_html import CSP, STYLE, _day, esc, safe_url
from agent.rules import coverage as coverage_rule

SYNTHETIC_NOTICE = (
    "Every record on this page is synthetic test data from the seed registry: the property, the "
    "serial numbers, the dates, the warranty terms, the service records and the maintenance log "
    "are invented. Manufacturer and model names are real only where an appliance note says so."
)
FOOTER = (
    "Generated on {date} (UTC) from the property registry. Every registry record shown is synthetic. "
    "Successor models come only from verified evidence in validated lookups. This page is not a diagnosis."
)
REPLACEMENT_INTRO = (
    "Discontinued models, discontinued parts and successors come only from verified quotes in the "
    "knowledge graph. Each age is computed from the install date as of {date} (UTC)."
)
NO_INTERVAL = "No manufacturer interval from a validated brief is on file, so no due date is computed."
RECALLS_INTRO = (
    "Product recalls come from the U.S. Consumer Product Safety Commission's Recall Retrieval service, "
    "searched by maker. Code shows a product recall only when it prints the exact model, and it does not check "
    "serial numbers. Each entry is the latest stored check."
)


def _registry(registry: Any) -> Any:
    """A Registry, from a Registry or a path to its SQLite file."""
    if hasattr(registry, "list_appliances"):
        return registry
    from agent.registry import Registry

    if not Path(registry).is_file():
        raise FileNotFoundError(f"no registry at {registry}")
    return Registry(registry)


def _cell(value: Any) -> str:
    return esc(value) if value not in (None, "") else "not recorded"


def _appliance_table(appliances: Sequence[Mapping[str, Any]]) -> str:
    rows = []
    for a in appliances:
        rows.append("\n".join([
            "      <tr>",
            f"        <td>{esc(a.get('id'))}</td>",
            f"        <td>{esc(a.get('category'))}</td>",
            f"        <td>{esc(a.get('manufacturer'))} {esc(a.get('model'))}</td>",
            f"        <td>{_cell(a.get('model_number'))}</td>",
            f"        <td>{_cell(a.get('serial'))}</td>",
            f"        <td>{_cell(a.get('install_date'))}</td>",
            f"        <td>{_cell(a.get('purchase_date'))}</td>",
            f"        <td>{esc(a.get('status'))}</td>",
            "      </tr>",
        ]))
    head = ("<thead><tr><th>Appliance</th><th>Category</th><th>Maker and model</th><th>Model number</th>"
            "<th>Serial</th>"
            "<th>Installed</th><th>Purchased</th><th>Status</th></tr></thead>")
    return "\n".join([
        '<section class="tablewrap">',
        "  <h2>Appliances</h2>",
        "  <table>",
        f"    {head}",
        "    <tbody>",
        *rows,
        "    </tbody>",
        "  </table>",
        "</section>",
    ])


def _service_history(records: Sequence[Mapping[str, Any]]) -> list[str]:
    if not records:
        return ["  <h3>Service history</h3>", "  <p>No service records.</p>"]
    rows = []
    for r in records:
        rows.append(
            f"      <tr><td>{_cell(r.get('date'))}</td><td>{esc(r.get('symptom'))}</td>"
            f"<td>{_cell(r.get('observed_code'))}</td><td>{_cell(r.get('work_done'))}</td>"
            f"<td>{_cell(r.get('performed_by'))}</td><td>{esc(r.get('record_id'))}</td></tr>"
        )
    return [
        "  <h3>Service history</h3>",
        "  <table>",
        "    <thead><tr><th>Date</th><th>Symptom</th><th>Code</th><th>Work done</th><th>By</th>"
        "<th>Record</th></tr></thead>",
        "    <tbody>",
        *rows,
        "    </tbody>",
        "  </table>",
    ]


def _maintenance(log: Sequence[Mapping[str, Any]], due: Sequence[Any]) -> list[str]:
    out = ["  <h3>Maintenance log</h3>"]
    if log:
        out.append("  <ul>" + "".join(
            f"<li>{esc(m.get('task'))}: done on {_cell(m.get('done_on'))} (record {esc(m.get('record_id'))})</li>"
            for m in log
        ) + "</ul>")
    else:
        out.append("  <p>No maintenance recorded.</p>")
    out.append("  <h3>Maintenance due</h3>")
    items = [m for m in due if isinstance(m, Mapping)]
    if not items:
        out.append(f"  <p>{NO_INTERVAL}</p>")
        return out
    lines = []
    for m in items:
        due_date = esc(m.get("due_date")) if m.get("due_date") else "not computed"
        lines.append(f"<li>{esc(m.get('task'))}: interval {esc(m.get('interval'))}; due {due_date}</li>")
    out.append("  <ul>" + "".join(lines) + "</ul>")
    return out


def _coverage(rows: Sequence[Mapping[str, Any]], appliance: Mapping[str, Any], as_of: date) -> list[str]:
    out = [f"  <h3>Warranty coverage as of {esc(as_of.isoformat())} (UTC)</h3>"]
    computed = coverage_rule.coverage_rows(rows, appliance, as_of)
    if not computed:
        # Terms text above with no rows: "No warranty on file." would contradict it.
        out.append(f"  <p>{coverage_rule.no_rows_statement(bool(appliance.get('warranty_terms')))}</p>")
        return out
    lines = []
    for c in computed:
        start = f"{esc(c.get('start_event'))}, {_cell(c.get('start_date'))}"
        record = esc(c.get("record_id")) + (" (synthetic)" if c.get("synthetic") else "")
        lines.append(
            f"      <tr><td>{esc(c.get('component'))}</td><td>{esc(c.get('term_months'))} months</td>"
            f"<td>{start}</td><td>{esc(c.get('end_date')) if c.get('end_date') else 'unknown'}</td>"
            f"<td>{esc(coverage_rule.STATUS_LABELS.get(c.get('status'), c.get('status')))}</td>"
            f"<td>{record}</td></tr>"
        )
    return out + [
        "  <table>",
        "    <thead><tr><th>Component</th><th>Term</th><th>Starts from</th><th>End date</th><th>Status</th>"
        "<th>Record</th></tr></thead>",
        "    <tbody>",
        *lines,
        "    </tbody>",
        "  </table>",
    ]


def _fact_lines(fact: Mapping[str, Any]) -> list[str]:
    reason = esc(sweep.REASON_LABELS.get(fact.get("reason"), fact.get("reason")))
    if fact.get("part") is not None:
        reason += f" (part {esc(fact.get('part'))})"
    name = " ".join(esc(v) for v in (fact.get("successor_manufacturer"), fact.get("successor_model")) if v)
    successor = f"Successor named in the evidence: <strong>{name}</strong>" if fact.get("successor_model") \
        else esc(sweep.NO_SUCCESSOR_NAMED)
    url = safe_url(fact.get("source_url"))
    label = esc(fact.get("source_title") or fact.get("source_url"))
    link = f'<a href="{esc(url)}">{label}</a>' if url else label
    return [
        f"      <p>Reason: {reason}. {successor}</p>",
        f'      <p class="quote">Quoted from the source: {esc(fact.get("evidence"))}</p>',
        f'      <p>{link} <span class="retrieved">retrieved {esc(_day(fact.get("retrieved_at")))}</span>'
        f' <span class="note">evidence sha256 {esc(fact.get("evidence_sha256"))}</span></p>',
    ]


def _replacement_section(graph: Any, appliances: Sequence[Mapping[str, Any]], as_of: date) -> str:
    """The "Plan a replacement" section: one entry per appliance (SC14)."""
    items = []
    for row in sweep.sweep_rows(graph, appliances, as_of):
        lines = [
            "    <li>",
            f"      <strong>{esc(row.get('manufacturer'))} {esc(row.get('model'))}</strong>",
            f'      <p class="meta">{esc(row.get("appliance_id"))} &middot; record {esc(row.get("record_id"))}'
            f"{' &middot; synthetic record' if row.get('synthetic') else ''}</p>",
            f"      <p>Age: {esc(row['age']['statement'])}</p>",
        ]
        if row.get("statement"):
            lines.append(f"      <p>{esc(row['statement'])}</p>")
        for fact in row.get("facts") or []:
            lines += _fact_lines(fact)
        lines.append("    </li>")
        items.append("\n".join(lines))
    return "\n".join([
        "<section>",
        "  <h2>Plan a replacement</h2>",
        f'  <p class="meta">{REPLACEMENT_INTRO.format(date=esc(as_of.isoformat()))}</p>',
        '  <ul class="items">',
        *items,
        "  </ul>",
        "</section>",
    ])


def _recalls_section(appliances: Sequence[Mapping[str, Any]], recalls_dir: Path | str | None) -> str:
    """The "Product recalls" section: one entry per appliance from its latest stored check (SC15)."""
    items = []
    for a in appliances:
        result = product_recalls.stored_result(recalls_dir, a)
        lines = [
            "    <li>",
            f"      <strong>{esc(a.get('manufacturer'))} {esc(a.get('model'))}</strong>",
            f'      <p class="meta">{esc(a.get("id"))} &middot; record {esc(a.get("record_id"))}</p>',
            *[f"    {line}" for line in product_recalls.html_body(result)],
            "    </li>",
        ]
        items.append("\n".join(lines))
    return "\n".join([
        "<section>",
        f"  <h2>{product_recalls.SECTION_TITLE}</h2>",
        f'  <p class="meta">{RECALLS_INTRO}</p>',
        '  <ul class="items">',
        *items,
        "  </ul>",
        "</section>",
    ])


def render_property_page(
    property_id: str,
    registry: Any,
    *,
    graph: Any = None,
    generated_at: str,
    maintenance_due: Mapping[str, Sequence[Any]] | None = None,
    recalls_dir: Path | str | None = None,
) -> str:
    """The whole property page. Raises KeyError for a property the registry does not hold.

    `generated_at` is the page's one UTC date (ISO), shown in the footer and
    used for every warranty status on the page. `recalls_dir` is where the
    product recall checks are stored (config.RECALLS_DIR); None shows that
    the check has not run.
    """
    as_of = date.fromisoformat(generated_at)
    reg = _registry(registry)
    prop = next((p for p in reg.list_properties() if p.get("id") == property_id), None)
    if prop is None:
        raise KeyError(f"unknown property {property_id!r}")
    appliances = reg.list_appliances(property_id)
    label = prop.get("label") or property_id
    synthetic = " (synthetic)" if prop.get("synthetic") else ""

    parts = [
        "\n".join([
            "<section>",
            "  <h1>Property record</h1>",
            f'  <p class="unit">{esc(label)}</p>',
            f'  <p class="meta">Property {esc(property_id)}{synthetic} &middot; {len(appliances)} appliances</p>',
            "</section>",
        ]),
        "\n".join([
            '<section class="caution">',
            "  <h2>Synthetic data</h2>",
            f"  <p>{SYNTHETIC_NOTICE}</p>",
            "</section>",
        ]),
        _appliance_table(appliances),
        _replacement_section(graph, appliances, as_of),
        _recalls_section(appliances, recalls_dir),
    ]
    for a in appliances:
        lines = [
            '<section class="tablewrap">',
            f"  <h2>{esc(a.get('manufacturer'))} {esc(a.get('model'))}</h2>",
            f'  <p class="meta">{esc(a.get("category"))} &middot; record {esc(a.get("record_id"))}'
            f"{' &middot; synthetic record' if a.get('synthetic') else ''}</p>",
        ]
        if a.get("notes"):
            lines.append(f'  <p class="meta">Note: {esc(a.get("notes"))}</p>')
        if a.get("warranty_terms"):
            lines.append(f"  <p>Warranty terms from record {esc(a.get('record_id'))}: {esc(a.get('warranty_terms'))}</p>")
        lines += _coverage(reg.warranty_coverage(a["id"]), a, as_of)
        lines += _service_history(reg.service_history(a["id"]))
        lines += _maintenance(reg.maintenance_log(a["id"]), (maintenance_due or {}).get(a["id"]) or [])
        lines.append("</section>")
        parts.append("\n".join(lines))
    parts.append(f"<footer>\n  <p>{FOOTER.format(date=esc(generated_at))}</p>\n</footer>")

    head = [
        "<!doctype html>",
        '<html lang="en">',
        "<head>",
        '<meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        '<meta name="robots" content="noindex">',
        f'<meta http-equiv="Content-Security-Policy" content="{esc(CSP)}">',
        '<link rel="icon" href="data:,">',
        f"<title>Property record: {esc(label)}</title>",
        f"<style>{STYLE}</style>",
        "</head>",
        "<body>",
    ]
    return "\n".join([*head, *parts, "</body>", "</html>", ""])
