"""`advisor property recalls` and the property page's "Product recalls" section (SC15; decision 71).

Offline the command prints each appliance's latest stored check with its date, or says the check
has not run; with --live it prints the planned requests and waits for a typed "proceed". The live
path runs here on a fake fetch in place of product_recalls.urllib_fetch, with sockets blocked, and
every path it writes is under a temp data folder. Every recall below is the synthetic fixture
(agent/tests/fixtures/cpsc/). Each test names the mutations in mutations.toml that turn it red.
"""

from __future__ import annotations

import io
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest

from agent import cli, config
from agent import product_recalls as pr
from agent.registry import open_registry
from agent.render.brief_html import esc
from agent.render.property_page import render_property_page
from agent.tests.test_product_recalls import MATCHES, NOON
from agent.tests.test_sweep import AS_OF, sweep_data  # noqa: F401  (sweep_data is a fixture)

MATCH = MATCHES[0]  # prints "XR16 4TTR6036" in its product model field
HOSTILE = "<script>alert(1)</script>\"'<img src=x onerror=alert(2)>"


class MakerFetch:
    """The fake CPSC service: a body per maker value, "[]" for any other; records every URL."""

    def __init__(self, bodies: dict[str, list[dict[str, Any]]]) -> None:
        self.bodies = bodies
        self.urls: list[str] = []

    def __call__(self, url: str, *, timeout: float, max_bytes: int) -> bytes:
        self.urls.append(url)
        query = parse_qs(urlsplit(url).query)
        maker = next(values[0] for name, values in query.items() if name != "format")
        return json.dumps(self.bodies.get(maker, [])).encode("utf-8")


def install_fetch(monkeypatch: pytest.MonkeyPatch, bodies: dict[str, list[dict[str, Any]]]) -> MakerFetch:
    fetch = MakerFetch(bodies)
    monkeypatch.setattr(pr, "urllib_fetch", fetch)
    monkeypatch.setattr(pr, "_now", lambda: NOON)
    return fetch


def recalls_cmd(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], *args: str,
                typed: str | None = None) -> tuple[int, str, str]:
    if typed is not None:
        monkeypatch.setattr(sys, "stdin", io.StringIO(typed))
    code = cli.main(["property", "recalls", *args])
    out, err = capsys.readouterr()
    return code, out, err


def stored_files() -> list[Path]:
    return sorted(config.RECALLS_DIR.rglob("*.json")) if config.RECALLS_DIR.exists() else []


def run_records(data: Path) -> list[dict[str, Any]]:
    runs = data / "runs"
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(runs.glob("recalls-*.json"))] \
        if runs.exists() else []


def recalls_section(page: str) -> str:
    start = page.index("<h2>Product recalls</h2>")
    return page[start:page.index("</section>", start)]


def test_tests_never_touch_the_real_recall_store(tmp_path: Path) -> None:
    """Mutation conftest_recall_store_not_redirected: tests read and write data/recalls/.

    The store must sit inside this test's own temp folder, not merely outside data/: the full
    mutation run sets ADVISOR_DATA_DIR to a scratch folder, so without the redirect the store is
    already outside data/ and only this check tells the redirect is gone."""
    assert config.RECALLS_DIR.resolve().is_relative_to(tmp_path.resolve())
    assert not config.RECALLS_DIR.resolve().is_relative_to(config.DATA_DIR.resolve())
    assert not config.RECALLS_DIR.resolve().is_relative_to((config.REPO_ROOT / "data").resolve())


def test_live_check_sends_nothing_without_proceed(sweep_data: Path, monkeypatch: pytest.MonkeyPatch,
                                                  capsys: pytest.CaptureFixture[str]) -> None:
    """The preflight lists the request count, the host, the parameter names and each maker value, at a
    cost of 0 USD; anything but "proceed" sends nothing, stores nothing and logs nothing. No key, no
    ADVISOR_MODE and no ledger are needed. Mutations: cli_recalls_live_skips_confirm,
    product_recalls_plan_drops_maker_values, product_recalls_plan_counts_synthetic."""
    fetch = install_fetch(monkeypatch, {"Trane": [MATCH]})
    code, out, err = recalls_cmd(monkeypatch, capsys, "prop-a", "--live", typed="yes\n")
    assert code == cli.EXIT_REFUSED, out + err
    assert 'the typed confirmation was not exactly "proceed"; nothing was sent.' in err
    assert "planned: 6 requests to www.saferproducts.gov (CPSC's Recall Retrieval service), 3 per appliance" in out
    assert ("  parameters: one of Manufacturer, Importer, Distributor per request, set to the maker value "
            "below, plus format=json; no other parameter is sent") in out
    assert '  appl-xr16 Trane XR16 4TTR6036: maker value "Trane"' in out
    assert '  appl-optima880 Sundance Spas Optima 880: maker value "Sundance Spas"' in out
    assert "  appl-dishwasher Synthetic Dishware Co. SD-200 (synthetic): not queried (invented maker)" in out
    assert "estimated cost: 0.0000 USD" in out
    assert out.rstrip().endswith("Type proceed to send these requests; anything else cancels and sends nothing.")
    assert fetch.urls == [] and stored_files() == [] and run_records(sweep_data) == []
    assert not config.LEDGER_PATH.exists() and not config.REPLAY_LEDGER_PATH.exists()


def test_live_check_after_proceed_stores_each_response_and_logs_the_run(
        sweep_data: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """After "proceed", each real maker's three requests go to the endpoint only, with whitelisted
    parameters; each check is stored with its retrieved date, the run record logs it at cost 0, and the
    offline command and the property page then show the stored check. Mutations:
    cli_recalls_queries_synthetic_makers, cli_recalls_no_run_record, cli_recalls_offline_reads_no_store,
    cli_property_show_no_recalls_dir, cli_sweep_pages_no_recalls_dir."""
    fetch = install_fetch(monkeypatch, {"Trane": [MATCH]})
    code, out, err = recalls_cmd(monkeypatch, capsys, "prop-a", "--live", typed="proceed\n")
    assert code == cli.EXIT_OK, out + err
    assert len(fetch.urls) == 6
    for url in fetch.urls:
        parts = urlsplit(url)
        assert f"{parts.scheme}://{parts.netloc}{parts.path}" == pr.ENDPOINT
        assert set(parse_qs(parts.query)) <= pr.V14_PARAMETERS
    makers = {v[0] for url in fetch.urls for k, v in parse_qs(urlsplit(url).query).items() if k != "format"}
    assert makers == {"Trane", "Sundance Spas"}

    files = stored_files()
    assert [p.parent.name for p in files] == ["appl-optima880", "appl-xr16"]
    for path in files:
        check = json.loads(path.read_text(encoding="utf-8"))
        assert path.name.startswith("20261013T120000Z-")
        assert [r["retrieved_at"] for r in check["requests"]] == ["2026-10-13T12:00:00+00:00"] * 3
    assert "CPSC product recall SYN001 names the model XR16 4TTR6036" in out
    assert "No CPSC product recall names this exact model: Optima 880." in out

    [record] = run_records(sweep_data)
    assert record["kind"] == pr.RUN_RECORD_KIND and record["cost_usd"] == 0.0
    assert record["run_id"].startswith(cli.RECALLS_RUN_PREFIX) and record["build_id"]
    assert sorted(c["appliance_id"] for c in record["checks"]) == ["appl-optima880", "appl-xr16"]
    assert {c["appliance_id"]: c["status"] for c in record["checks"]} == {"appl-xr16": pr.MATCHED,
                                                                         "appl-optima880": pr.NO_MATCH}
    assert sorted(record["stored"]) == [str(p) for p in files]
    assert f"run record: {sweep_data / 'runs' / (record['run_id'] + '.json')}" in out

    code, out, err = recalls_cmd(monkeypatch, capsys, "prop-a")
    assert code == cli.EXIT_OK, out + err
    assert "appl-xr16: latest stored check 2026-10-13" in out
    assert "CPSC product recall SYN001 names the model XR16 4TTR6036" in out
    assert "appl-dishwasher: latest stored check never" in out and len(fetch.urls) == 6

    assert cli.main(["property", "show", "prop-a"]) == cli.EXIT_OK
    capsys.readouterr()
    section = recalls_section((config.PROPERTY_OUT_DIR / "prop-a.html").read_text(encoding="utf-8"))
    assert "CPSC product recall SYN001 names the model XR16 4TTR6036" in section
    assert f'<a href="{MATCH["URL"]}">' in section
    assert "No CPSC product recall names this exact model: Optima 880." in section
    assert pr.NOT_CHECKED_SENTENCE not in section

    # The sweep writes the same page, so it shows the same stored checks.
    assert cli.main(["property", "sweep", "prop-a"]) == cli.EXIT_OK
    capsys.readouterr()
    swept = recalls_section((config.PROPERTY_OUT_DIR / "prop-a.html").read_text(encoding="utf-8"))
    assert swept == section


def test_offline_command_says_the_check_has_not_run_and_sends_nothing(
        sweep_data: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """With nothing stored, every real unit says the check has not run and an invented maker says it
    does not apply; nothing is fetched, and --all covers both properties. Mutations:
    cli_recalls_all_first_only (the shared target rule), product_recalls_not_checked_reworded."""
    fetch = install_fetch(monkeypatch, {})
    code, out, err = recalls_cmd(monkeypatch, capsys, "--all")
    assert code == cli.EXIT_OK, out + err
    registry = open_registry(config.REGISTRY_PATH, seed=False)
    for prop in registry.list_properties():
        for a in registry.list_appliances(prop["id"]):
            assert f"{a['id']}: latest stored check never" in out
    assert out.count("    The product recall check has not run.") == 3  # Sundance Spas, Trane, Rheem
    assert "The product recall check does not apply to a synthetic maker" in out
    assert fetch.urls == [] and stored_files() == []
    code, out, err = recalls_cmd(monkeypatch, capsys, "prop-missing")
    assert code == cli.EXIT_REFUSED and "unknown property" in err


def test_page_section_follows_plan_a_replacement_and_escapes_every_value(tmp_path: Path) -> None:
    """The section sits after "Plan a replacement" and before the appliance sections; registry and
    recall values are escaped; with no store every real unit says the check has not run. Mutations:
    property_page_recalls_section_dropped, property_page_recalls_unit_raw, property_page_recalls_body_raw,
    product_recalls_none_store_not_handled."""
    from agent.tests.test_render import _hostile_seed, assert_self_contained

    registry = open_registry(tmp_path / "hostile.sqlite", seed=_hostile_seed(tmp_path))
    page = render_property_page("prop-a", registry, generated_at=AS_OF.isoformat())
    assert page.index("<h2>Plan a replacement</h2>") < page.index("<h2>Product recalls</h2>")
    xr = registry.get_appliance("appl-xr16")
    assert page.index("<h2>Product recalls</h2>") < page.index(f"<h2>{esc(xr['manufacturer'])}")
    section = recalls_section(page)
    for a in registry.list_appliances("prop-a"):
        assert f"<strong>{esc(a['manufacturer'])} {esc(a['model'])}</strong>" in section
    assert HOSTILE not in page and "<script>" not in page
    assert section.count(f"<p>{pr.NOT_CHECKED_SENTENCE}</p>") == 2  # Optima and XR16; the dishwasher is invented
    assert assert_self_contained(page).problems == []

    # A stored check whose recall carries markup reaches the page escaped.
    plain = open_registry(tmp_path / "plain.sqlite")
    trane = plain.get_appliance("appl-xr16")
    hostile_match = {**MATCH, "Title": "Synthetic recall" + HOSTILE, "Hazards": [{"Name": "Hazard" + HOSTILE}]}
    store = tmp_path / "recalls"
    pr.store_check(store, pr.check_appliance(trane, fetch=MakerFetch({"Trane": [hostile_match]}), now=lambda: NOON))
    page = render_property_page("prop-a", plain, generated_at=AS_OF.isoformat(), recalls_dir=store)
    section = recalls_section(page)
    assert esc("Synthetic recall" + HOSTILE) in section and esc("Hazard" + HOSTILE) in section
    assert HOSTILE not in page and "<script>" not in page
    assert pr.SERIAL_SENTENCE in section and section.count(f"<p>{pr.NOT_CHECKED_SENTENCE}</p>") == 1
    assert datetime(2026, 10, 13, tzinfo=UTC).date().isoformat() in section
