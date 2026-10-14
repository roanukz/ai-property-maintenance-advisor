"""The property page's warranty coverage table (SC13).

Every registry row here is synthetic. Each test names, in its docstring, the
planted bugs in mutations.toml that turn it red.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from agent import config
from agent.registry import open_registry
from agent.render.brief_html import esc
from agent.render.property_page import render_property_page
from agent.rules import coverage as coverage_rule

HOSTILE = "<script>alert(1)</script>\"'<img src=x onerror=alert(2)>"


def _rows(page: str, title: str) -> list[str]:
    """The coverage table rows of one appliance's section, as raw HTML lines."""
    section = page[page.index(f"<h2>{title}</h2>"):]
    section = section[:section.index("</section>")]
    return [line.strip() for line in section.splitlines() if line.strip().startswith("<tr><td>")
            and "months</td>" in line]


def test_coverage_table_cites_every_row_with_status_and_synthetic_label(tmp_path: Path) -> None:
    """Optima rows ended, softener rows active, each cited to warranty:<id> and
    labeled synthetic; an appliance with no rows says "No warranty on file."
    Mutations: property_page_coverage_dropped (no coverage table),
    property_page_coverage_synthetic_dropped (rows not labeled synthetic),
    property_page_coverage_wall_clock (statuses from the wall clock, not the
    page's date)."""
    registry = open_registry(tmp_path / "registry.sqlite")
    page_a = render_property_page("prop-a", registry, generated_at="2026-10-01")
    assert "<h3>Warranty coverage as of 2026-10-01 (UTC)</h3>" in page_a
    assert _rows(page_a, "Sundance Spas Optima 880") == [
        "<tr><td>equipment</td><td>24 months</td><td>install, 2021-06-10</td><td>2023-06-10</td><td>ended</td>"
        "<td>warranty:warr-optima-equipment (synthetic)</td></tr>",
        "<tr><td>labor</td><td>12 months</td><td>install, 2021-06-10</td><td>2022-06-10</td><td>ended</td>"
        "<td>warranty:warr-optima-labor (synthetic)</td></tr>",
        "<tr><td>shell</td><td>60 months</td><td>install, 2021-06-10</td><td>2026-06-10</td><td>ended</td>"
        "<td>warranty:warr-optima-shell (synthetic)</td></tr>",
    ]
    assert "out of warranty" not in page_a.lower()
    trane = page_a[page_a.index("<h2>Trane XR16 4TTR6036</h2>"):]
    assert "<p>No warranty on file.</p>" in trane[:trane.index("</section>")]

    page_b = render_property_page("prop-b", registry, generated_at="2026-10-01")
    softener = _rows(page_b, "Synthetic Water Co. WS-400 (synthetic)")
    assert [re.findall(r"<td>(.*?)</td>", row)[3:] for row in softener] == [
        ["2027-04-20", "active", "warranty:warr-softener-labor (synthetic)"],
        ["2036-05-04", "active", "warranty:warr-softener-tank (synthetic)"],
        ["2031-05-04", "active", "warranty:warr-softener-valve (synthetic)"],
    ]
    assert "purchase, 2026-04-20" in softener[0]
    # On the day the labor term ends, that row reads ended.
    later = render_property_page("prop-b", registry, generated_at="2027-04-20")
    assert re.findall(r"<td>(.*?)</td>", _rows(later, "Synthetic Water Co. WS-400 (synthetic)")[0])[4] == "ended"


def _seed_with(tmp_path: Path, **appliance_edits) -> Path:
    seed = json.loads(config.DEFAULT_SEED_PATH.read_text(encoding="utf-8"))
    for appliance in seed["appliances"]:
        if appliance["id"] == "appl-softener":
            appliance.update(appliance_edits)
    for row in seed["warranty_coverage"]:
        if row["appliance_id"] == "appl-softener":
            row["component"] += " C" + HOSTILE
    path = tmp_path / "seed.json"
    path.write_text(json.dumps(seed), encoding="utf-8")
    return path


def test_unknown_end_dates_and_hostile_components(tmp_path: Path) -> None:
    """A start date known only to the year shows "end date unknown", never
    ended; every component is escaped. Mutations:
    property_page_coverage_status_raw (the status code printed instead of its
    label), property_page_coverage_component_raw (the component unescaped)."""
    registry = open_registry(tmp_path / "registry.sqlite",
                             seed=_seed_with(tmp_path, install_date="2026", purchase_date="2026-04"))
    page = render_property_page("prop-b", registry, generated_at="2030-01-01")
    rows = _rows(page, "Synthetic Water Co. WS-400 (synthetic)")
    assert len(rows) == 3
    for row in rows:
        cells = re.findall(r"<td>(.*?)</td>", row)
        assert cells[3:5] == ["unknown", "end date unknown"], cells
    assert HOSTILE not in page and esc(" C" + HOSTILE) in page


def test_terms_without_rows_and_a_future_start(tmp_path: Path) -> None:
    """The Optima with its terms text but no coverage rows says the terms are
    not broken into components, never "No warranty on file." under them; a
    softener installed after the page's date shows its install based rows as
    not started, with their end dates, and its purchase based row active.
    Mutations: coverage_terms_only_says_no_warranty, coverage_future_start_active."""
    seed = json.loads(config.DEFAULT_SEED_PATH.read_text(encoding="utf-8"))
    seed["warranty_coverage"] = [r for r in seed["warranty_coverage"] if r["appliance_id"] != "appl-optima880"]
    for appliance in seed["appliances"]:
        if appliance["id"] == "appl-softener":
            appliance["install_date"] = "2026-12-01"
    path = tmp_path / "seed.json"
    path.write_text(json.dumps(seed), encoding="utf-8")
    registry = open_registry(tmp_path / "registry.sqlite", seed=path)

    page_a = render_property_page("prop-a", registry, generated_at="2026-10-01")
    optima = page_a[page_a.index("<h2>Sundance Spas Optima 880</h2>"):]
    optima = optima[:optima.index("</section>")]
    assert "Warranty terms from record appliance:appl-optima880: " in optima
    assert f"<p>{coverage_rule.TERMS_ONLY}</p>" in optima and coverage_rule.NO_WARRANTY not in optima
    assert _rows(page_a, "Sundance Spas Optima 880") == []

    page_b = render_property_page("prop-b", registry, generated_at="2026-10-01")
    softener = _rows(page_b, "Synthetic Water Co. WS-400 (synthetic)")
    assert [re.findall(r"<td>(.*?)</td>", row)[2:5] for row in softener] == [
        ["purchase, 2026-04-20", "2027-04-20", "active"],
        ["install, 2026-12-01", "2036-12-01", "not started"],
        ["install, 2026-12-01", "2031-12-01", "not started"],
    ]
