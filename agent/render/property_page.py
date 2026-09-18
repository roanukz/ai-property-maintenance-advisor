"""Static property page (PLAN 8.13 `advisor property show`, PRD:109 and PRD:112).

`render_property_page(property_id, registry, graph=None, generated_at=...)`
returns one self contained HTML page for a property: its appliances, each
appliance's service history, its maintenance log and what is known about
maintenance due, and, when a knowledge graph is given, the successor models
that verified SUPERSEDED_BY edges document for it.

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
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from agent.render.brief_html import CSP, STYLE, _day, esc, safe_url

SYNTHETIC_NOTICE = (
    "Every record on this page is synthetic test data from the seed registry: the property, the "
    "serial numbers, the dates, the warranty terms, the service records and the maintenance log "
    "are invented. Manufacturer and model names are real only where an appliance note says so."
)
FOOTER = (
    "Generated on {date} from the property registry. Every registry record shown is synthetic. "
    "Successor models come only from verified evidence in validated briefs. This page is not a diagnosis."
)
NO_INTERVAL = "No manufacturer interval from a validated brief is on file, so no due date is computed."


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
            f"        <td>{_cell(a.get('serial'))}</td>",
            f"        <td>{_cell(a.get('install_date'))}</td>",
            f"        <td>{_cell(a.get('purchase_date'))}</td>",
            f"        <td>{esc(a.get('status'))}</td>",
            "      </tr>",
        ]))
    head = ("<thead><tr><th>Appliance</th><th>Category</th><th>Maker and model</th><th>Serial</th>"
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


def _successors(graph: Any, appliance: Mapping[str, Any]) -> list[str]:
    if graph is None:
        return []
    try:
        edges = graph.superseded_by(appliance.get("manufacturer") or "", appliance.get("model") or "")
    except (KeyError, ValueError, TypeError):
        edges = []
    if not edges:
        return []
    items = []
    for edge in edges:
        source = edge.get("source") or {}
        url = safe_url(edge.get("source_url"))
        label = esc(source.get("title") or edge.get("source_url"))
        link = f'<a href="{esc(url)}">{label}</a>' if url else label
        name = " ".join(esc(v) for v in (edge.get("successor_manufacturer"), edge.get("successor_model")) if v)
        items.append("\n".join([
            "    <li>",
            f"      <strong>{name}</strong>",
            f'      <p class="quote">Quoted from the source: {esc(edge.get("evidence"))}</p>',
            f'      <p>{link} <span class="retrieved">retrieved {esc(_day(edge.get("retrieved_at")))}</span></p>',
            "    </li>",
        ]))
    return ["  <h3>Successor models on record</h3>", '  <ul class="items">', *items, "  </ul>"]


def render_property_page(
    property_id: str,
    registry: Any,
    *,
    graph: Any = None,
    generated_at: str,
    maintenance_due: Mapping[str, Sequence[Any]] | None = None,
) -> str:
    """The whole property page. Raises KeyError for a property the registry does not hold."""
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
        lines += _service_history(reg.service_history(a["id"]))
        lines += _maintenance(reg.maintenance_log(a["id"]), (maintenance_due or {}).get(a["id"]) or [])
        lines += _successors(graph, a)
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
