"""Maintenance due (PLAN section 5, PRD:111; 8.5 rule 9): only manufacturer tier intervals, dates by code.

Replays the synthetic cassette sx100_discontinued (every field synthetic; page
text only on example.com and example.org URLs, from
agent/tests/fixtures/discontinued.json) for the seeded synthetic appliance
appl-sx100, whose registry carries the synthetic maintenance_log row
maintenance:mnt-sx-0001 (done 2026-05-20). Each test names the mutation that
turns it red.
"""

from __future__ import annotations

from pathlib import Path

from agent.registry import Registry
from agent.rules.maintenance import check_maintenance, due_date, parse_interval, task_key
from agent.tests.helpers import load_case
from agent.tests.test_sc8_history import final_state, variant
from agent.tests.test_sc9_upgrades import DEALER, FORUM, NOTICE, SX100, draft_of, page, run_sx

LOG_ROW = "maintenance:mnt-sx-0001"


def _item(task: str, interval: str, index: int, evidence: str, last_done: str = "") -> dict:
    return {"task": task, "interval": interval, "source_index": index, "evidence": evidence,
            "last_done_record_id": last_done}


def _maintenance(data: dict) -> None:
    draft = draft_of(data)
    draft["upgrade_options"] = []
    draft["maintenance_due"] = [
        # Kept: manufacturer tier, interval verbatim inside a verified quote. The
        # model also supplies its own due_date, which must be ignored.
        {**_item("Clean the bucket filter", "every 3 months", 0, "Clean the bucket filter every 3 months.",
                 LOG_ROW), "due_date": "2099-01-01"},
        # Dropped: dealer page (its manufacturer proposal is lowered to dealer by the host ceiling).
        _item("Clean the bucket filter", "every 6 months", 1, "Clean the bucket filter every 6 months."),
        # Dropped: forum page.
        _item("Clean the bucket filter", "every month", 2,
              "A forum member wrote: I clean the bucket filter every month and it runs fine."),
        # Dropped: the interval is not inside the (verified) quote.
        _item("Replace the drain hose", "every 12 months", 0, "Replace the drain hose every 2 years."),
        # Kept: a record ID that is not this appliance's log row is cleared, so no due date.
        _item("Replace the drain hose", "every 2 years", 0, "Replace the drain hose every 2 years.",
              "maintenance:mnt-9999"),
    ]


def test_only_manufacturer_tier_intervals_kept(tmp_path: Path) -> None:
    """Mutations: maintenance_tier_check_off (the manufacturer tier check is
    removed, so the dealer and forum intervals stay); maintenance_interval_outside_quote
    (the span check's target is the quote itself, so an interval the quote does
    not contain stays); maintenance_due_date_not_computed (code stops writing
    due_date from the maintenance_log row); validate_maintenance_log_not_loaded
    (validate never reads the appliance's maintenance_log)."""
    cassette = variant(load_case("synthetic/sx100_discontinued"), "maintenance", _maintenance)
    ctx, outcomes = run_sx(cassette, tmp_path)
    state = final_state(outcomes)
    assert state["status"] == "ok", state.get("validation_errors")
    [row] = Registry(ctx.registry_path).maintenance_log(SX100)
    assert row["record_id"] == LOG_ROW and row["done_on"] == "2026-05-20"

    by_url = {s["url"]: s for s in state["sources"]}
    assert set(by_url) >= {NOTICE, DEALER, FORUM}
    brief = state["brief"]
    kept = [(m["task"], m["interval"], brief["sources"][m["source_index"]]["url"]) for m in brief["maintenance_due"]]
    assert kept == [("Clean the bucket filter", "every 3 months", NOTICE),
                    ("Replace the drain hose", "every 2 years", NOTICE)]
    [notice] = brief["sources"]
    assert notice["tier"] == "manufacturer"
    for item in brief["maintenance_due"]:
        assert item["interval"] in item["evidence"] and item["evidence"] in page(NOTICE)

    first, second = brief["maintenance_due"]
    assert first["last_done_record_id"] == LOG_ROW
    assert first["due_date"] == "2026-08-20" == due_date(row["done_on"], "every 3 months")
    assert second["last_done_record_id"] is None and second["due_date"] is None

    # Nothing else cites the dealer and forum pages, so they are pruned with their intervals.
    assert [s["url"] for s in brief["sources"]] == [NOTICE]

    # The interval reader takes one plain period or none.
    assert parse_interval("every 3 months") == ("month", 3)
    assert parse_interval("annually") == ("year", 1)
    assert parse_interval("rinse every 2 weeks and replace every 12 months") is None
    assert due_date("2026-01-31", "every month") == "2026-02-28"


def _log(record_id: str, task: str, done_on: str, appliance_id: str = SX100) -> dict:
    return {"record_id": record_id, "appliance_id": appliance_id, "task": task, "done_on": done_on, "synthetic": True}


def _entry(task: str, last_done: str | None) -> dict:
    return {"task": task, "interval": "every 3 months", "source_index": 0,
            "evidence": "Clean the bucket filter every 3 months.", "last_done_record_id": last_done,
            "due_date": "2099-01-01", "last_done_on": "2099-01-01"}


def _checked(entries: list[dict], log: list[dict]) -> list[dict]:
    brief = {"status": "ok", "sources": [{"url": NOTICE, "tier": "manufacturer"}], "maintenance_due": entries}
    check_maintenance(brief, page_texts={NOTICE: page(NOTICE)}, maintenance_log=log, appliance_id=SX100)
    return brief["maintenance_due"]


def test_due_date_found_without_model_record_id(tmp_path: Path) -> None:
    """The model never sees maintenance_log record IDs, so on a live run
    last_done_record_id is empty; code finds the row by its task.

    Mutations: maintenance_task_fallback_off (no lookup by task, so an empty
    ID leaves the due date empty); maintenance_fallback_takes_oldest (the
    oldest matching row is used, not the newest); maintenance_task_match_any
    (any row matches any task); maintenance_last_done_on_dropped (code stops
    copying the row's date into last_done_on); maintenance_appliance_filter_off
    (another appliance's maintenance_log row is accepted)."""
    # Through the real graph: validate loads appl-sx100's log and the model gave no ID.
    def no_record_id(data: dict) -> None:
        _maintenance(data)
        draft_of(data)["maintenance_due"][0]["last_done_record_id"] = ""

    cassette = variant(load_case("synthetic/sx100_discontinued"), "maintenance without ID", no_record_id)
    _, outcomes = run_sx(cassette, tmp_path)
    state = final_state(outcomes)
    assert state["status"] == "ok", state.get("validation_errors")
    first, second = state["brief"]["maintenance_due"]
    assert (first["task"], first["last_done_record_id"], first["last_done_on"], first["due_date"]) == (
        "Clean the bucket filter", LOG_ROW, "2026-05-20", "2026-08-20")
    # "Replace the drain hose" matches no log row, so nothing is found for it.
    assert (second["last_done_record_id"], second["last_done_on"], second["due_date"]) == (None, None, None)

    # Directly: the newest matching row wins, whatever the model's ID or dates said.
    log = [_log("maintenance:old", "Cleaned the bucket filter", "2025-01-10"),
           _log("maintenance:new", "cleaning the bucket filters", "2026-02-10"),
           _log("maintenance:hose", "Replaced the drain hose", "2026-04-01")]
    [kept] = _checked([_entry("Clean the bucket filter", None)], log)
    assert (kept["last_done_record_id"], kept["last_done_on"], kept["due_date"]) == (
        "maintenance:new", "2026-02-10", "2026-05-10")
    # A valid ID the model gave is kept as given.
    [given] = _checked([_entry("Clean the bucket filter", "maintenance:old")], log)
    assert (given["last_done_record_id"], given["last_done_on"], given["due_date"]) == (
        "maintenance:old", "2025-01-10", "2025-04-10")
    # A task no row matches gets no record, no date and no due date.
    [none] = _checked([_entry("Descale the pump", "")], log)
    assert (none["last_done_record_id"], none["last_done_on"], none["due_date"]) == (None, None, None)

    # Another appliance's row is never used, by ID or by task.
    other = [_log("maintenance:other", "Cleaned the bucket filter", "2026-03-01", appliance_id="appl-other")]
    [foreign] = _checked([_entry("Clean the bucket filter", "maintenance:other")], other)
    assert foreign["last_done_record_id"] is None and foreign["due_date"] is None

    assert task_key("Cleaned the bucket filter") == task_key("Clean the bucket filter") == ("clean", "bucket", "filter")
    assert task_key("Replace the glass") == task_key("Replaced the glasses")
    assert task_key("Replace the drain hose") != task_key("Clean the bucket filter")
