"""The MCP server (agent/mcp_server.py, decision 97), offline.

Every call goes through the SDK's own in process client (mcp.Client on the
server object), on a 2026-07-28 connection unless a test pins mode="legacy",
with the network blocked by pytest-socket and every path under tmp_path. Replay
runs use the recorded cassettes; the live path runs on the fake clients of
test_live_path.py, and the product recall check on the fake CPSC fetch of
test_product_recalls_cli.py, so nothing leaves this machine. One test starts
the server over real stdio in a guarded child process.

Each test names the planted bugs in mutations.toml that turn it red.
"""

from __future__ import annotations

import ast
import io
import json
import shutil
import sqlite3
import sys
import tomllib
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import anyio
import pytest
from mcp import Client
from mcp.types import ElicitResult

from agent import build_info, cli, config, mcp_server
from agent import product_recalls as pr
from agent.ledger import Ledger
from agent.replay.cassettes import load_cassette
from agent.tests.helpers import GUARD_MARKER, load_case, plate_for_sha256, spawn_offline_child
from agent.tests.test_live_path import FAKE_ANTHROPIC, FAKE_TAVILY, FAKE_TYPESAFE, KEY_NAMES, LiveFakes, flo_data
from agent.tests.test_product_recalls import MATCHES, NOON
from agent.tests.test_product_recalls_cli import MakerFetch

CASSETTES = config.REPO_ROOT / "agent" / "tests" / "cassettes"
FLO = CASSETTES / "flo.json"
PLATE = config.REPO_ROOT / "demo-assets" / "plate-clear.jpg"
OWNER = {"manufacturer": "Sundance Spas", "model": "Optima 880", "serial": "", "manufacture_date": ""}
TOOLS = {"get_service_brief", "resume_brief", "property_status", "check_product_recalls"}


@pytest.fixture
def mcp_data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Every path a tool reads or writes, under a temp data folder; replay mode; no cassette in the environment."""
    data = tmp_path / "data"
    for name, path in {
        "DATA_DIR": data, "CHECKPOINT_PATH": data / "checkpoints.sqlite",
        "LEDGER_PATH": data / "ledger" / "ledger.sqlite", "REPLAY_LEDGER_PATH": data / "ledger" / "replay_ledger.sqlite",
        "REGISTRY_PATH": data / "registry.sqlite", "GRAPH_PATH": data / "graph.json",
        "REPLAY_GRAPH_PATH": data / "replay_graph.json", "PAGES_DIR": data / "pages", "LOOKUPS_DIR": data / "lookups",
        "BRIEFS_OUT_DIR": data / "briefs", "PROPERTY_OUT_DIR": data / "property", "EVAL_DIR": data / "eval",
        "STAGING_DIR": data / "staging", "RECORDINGS_DIR": data / "recordings",
    }.items():
        monkeypatch.setattr(config, name, path)
    for name in (*KEY_NAMES, config.ENV_MODE, config.ENV_CASSETTE, config.ENV_TRACING):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(config.REPO_ROOT)
    return data


@pytest.fixture
def mcp_live(mcp_data: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """mcp_data in cheap mode with fake keys in a temp .env and an empty ledger that already exists."""
    monkeypatch.setattr(config, "ENV_FILE", tmp_path / ".env")
    (tmp_path / ".env").write_text(f"ANTHROPIC_API_KEY={FAKE_ANTHROPIC}\nTAVILY_API_KEY={FAKE_TAVILY}\n"
                                   f"{config.TYPESAFE_KEY_NAME}={FAKE_TYPESAFE}\n", encoding="utf-8")
    monkeypatch.setenv(config.ENV_MODE, "cheap")
    Ledger(config.LEDGER_PATH, create=True)
    return mcp_data


def call(server: Any, tool: str, args: dict[str, Any], *, mode: str = "auto", elicit: Any = None) -> Any:
    """One tool call through a fresh in process client on the same server object."""
    async def go() -> Any:
        extra = {"raise_exceptions": True} if mode == "auto" else {}
        async with Client(server, mode=mode, elicitation_callback=elicit, **extra) as client:
            return await client.call_tool(tool, args)

    return anyio.run(go)


def read(server: Any, uri: str) -> str:
    async def go() -> str:
        async with Client(server, raise_exceptions=True) as client:
            return (await client.read_resource(uri)).contents[0].text

    return anyio.run(go)


def ok(result: Any) -> dict[str, Any]:
    assert not result.is_error, result.content[0].text
    return result.structured_content


def text_of(result: Any) -> str:
    return result.content[0].text


def run_record(run_id: str) -> dict[str, Any]:
    return json.loads((config.PAGES_DIR.parent / "runs" / f"{run_id}.json").read_text(encoding="utf-8"))


def thread_values(thread_id: str) -> tuple[dict[str, Any], bool]:
    from agent.graph import build_graph, open_checkpointer, thread_config

    snapshot = build_graph(open_checkpointer(config.CHECKPOINT_PATH)).get_state(thread_config(thread_id))
    return dict(snapshot.values), bool(snapshot.interrupts)


class Answers:
    """A fake person behind the client's elicitation callback: answers each question in turn and records it."""

    def __init__(self, identity: dict[str, Any] | None = None, *, proceed: str = "proceed",
                 action: str = "accept") -> None:
        self.identity = identity if identity is not None else {**OWNER, "code": "FLO"}
        self.proceed = proceed
        self.action = action
        self.asked: list[Any] = []

    async def __call__(self, context: Any, params: Any) -> ElicitResult:
        self.asked.append(params)
        if self.action != "accept":
            return ElicitResult(action=self.action)
        fields = params.requested_schema["properties"]
        if "confirmation" in fields:
            return ElicitResult(action="accept", content={"confirmation": self.proceed})
        content = {name: spec.get("default", "") for name, spec in fields.items()}
        content.update(self.identity)
        return ElicitResult(action="accept", content=content)

    def kinds(self) -> list[str]:
        return ["proceed" if "confirmation" in p.requested_schema["properties"] else "identity" for p in self.asked]


# ---------------------------------------------------------------------------
# The tool list
# ---------------------------------------------------------------------------


def test_tools_their_schemas_and_resources() -> None:
    """Mutations: mcp_resume_tool_renamed (resume_brief is listed under another name);
    mcp_status_tool_not_read_only (property_status loses its read only hint);
    mcp_brief_identity_schema_open (the typed identity accepts any key)."""
    server = mcp_server.build_server()

    async def go() -> tuple[Any, Any, Any]:
        async with Client(server, raise_exceptions=True) as client:
            return (await client.list_tools(), await client.list_resource_templates(), client.instructions)

    tools, templates, instructions = anyio.run(go)
    by_name = {t.name: t for t in tools.tools}
    assert set(by_name) == TOOLS
    brief = by_name["get_service_brief"]
    assert brief.input_schema["required"] == ["symptom"]
    assert set(brief.input_schema["properties"]) == {"symptom", "photo_path", "appliance_id", "identity",
                                                     "property_id", "code", "confirm", "confirmation_id"}
    identity = brief.input_schema["$defs"]["TypedIdentity"]
    assert set(identity["properties"]) == {"manufacturer", "model", "serial", "manufacture_date"}
    assert identity["additionalProperties"] is False
    out = brief.output_schema["properties"]
    for key in ("status", "brief", "pause", "preflight", "cost", "html", "run_id", "thread_id", "build_id"):
        assert key in out, key
    assert set(out["status"]["enum"]) == {"ok", "no_reliable_answer", "budget_stopped", "paused",
                                          "confirmation_required", "not_confirmed"}
    resume = by_name["resume_brief"]
    assert resume.input_schema["required"] == ["thread_id"]
    assert set(resume.input_schema["properties"]) == {"thread_id", "manufacturer", "model", "serial",
                                                      "manufacture_date", "code", "confirm", "confirmation_id"}
    assert resume.output_schema == brief.output_schema
    status = by_name["property_status"]
    assert status.input_schema["required"] == ["property_id"]
    assert status.annotations.read_only_hint is True and status.annotations.open_world_hint is False
    assert "appliances" in status.output_schema["properties"]
    recalls = by_name["check_product_recalls"]
    assert set(recalls.input_schema["properties"]) == {"property_id", "all_properties", "confirm", "confirmation_id"}
    assert recalls.annotations.open_world_hint is True and recalls.annotations.read_only_hint is False
    assert recalls.output_schema["properties"]["status"]["enum"] == ["checked", "confirmation_required",
                                                                     "not_confirmed"]
    assert brief.annotations.read_only_hint is False and brief.annotations.open_world_hint is True
    assert {t.uri_template for t in templates.resource_templates} == {mcp_server.BRIEF_URI, mcp_server.PROPERTY_URI}
    assert 'confirm="proceed" only after the person has read that preflight' in instructions


# ---------------------------------------------------------------------------
# Replay: the pause, resume_brief, elicitation, and parity with advisor ask and resume
# ---------------------------------------------------------------------------


def test_replay_brief_pauses_then_resume_brief_finishes_it(mcp_data: Path) -> None:
    """A plate photo pauses as `advisor ask` pauses; the result names the thread, what was read and what is
    missing; resume_brief with the owner's answer finishes the brief and links the HTML it wrote. Mutations:
    mcp_paused_drops_thread_id (the paused result does not say which thread to resume);
    mcp_resume_ignores_confirmed_fields (the plate's text is confirmed instead of the owner's answer);
    mcp_brief_resource_wrong_folder (the brief resource reads outside config.BRIEFS_OUT_DIR)."""
    server = mcp_server.build_server(cassette=str(FLO))
    paused = ok(call(server, "get_service_brief", {"symptom": "panel shows FLO", "photo_path": str(PLATE)}))
    assert paused["status"] == "paused" and paused["brief"] is None and paused["html"] is None
    thread_id = paused["thread_id"]
    assert thread_id.startswith("t-") and paused["run_id"] == thread_id
    assert paused["pause"]["identity"] == {"manufacturer": "SUNDANCE® SPAS", "model": "OPTIMA 880",
                                           "serial": "100915742", "manufacture_date": "06/2014"}
    assert paused["pause"]["missing"] == [] and paused["pause"]["code_shown"] == "FLO"
    assert paused["pause"]["extraction"]["confidence"]["model"] == "high"
    assert f'resume_brief with thread_id "{thread_id}"' in paused["next_step"]
    assert paused["cost"]["ledger"] == "replay" and paused["cost"]["real_money"] is False
    assert thread_values(thread_id)[1] is True  # still paused in the checkpointer, as advisor ask leaves it

    done = call(server, "resume_brief", {"thread_id": thread_id, **OWNER, "code": "FLO"})
    final = ok(done)
    assert final["status"] == "ok" and final["thread_id"] == thread_id
    values, still_paused = thread_values(thread_id)
    assert not still_paused
    assert values["identity"] == {"manufacturer": "Sundance Spas", "model": "Optima 880", "serial": None,
                                  "manufacture_date": None}
    assert values["observed_code"] == "FLO"
    html = Path(final["html"]["path"])
    assert html.is_file() and html.resolve().is_relative_to(config.BRIEFS_OUT_DIR.resolve())
    assert final["html"]["resource_uri"] == f"advisor://briefs/{thread_id}"
    link = done.content[1]
    assert link.type == "resource_link" and link.uri == final["html"]["resource_uri"] and link.mime_type == "text/html"
    assert read(server, final["html"]["resource_uri"]) == html.read_text(encoding="utf-8")
    assert run_record(thread_id)["status"] == "ok"
    assert not config.LEDGER_PATH.exists()  # replay never opens the live ledger


def _strip(record: dict[str, Any]) -> dict[str, Any]:
    """A run record without what differs between two runs of one cassette: ids, clocks, timings and paths."""
    drop = {"run_id", "thread_id", "generated_at", "latency", "latency_s", "html_path", "graph_edges"}
    out = {k: v for k, v in record.items() if k not in drop}
    brief = json.loads(json.dumps(out["brief"]))
    for source in brief.get("sources") or []:
        source.pop("retrieved_at", None)
    out["brief"] = brief
    return out


def test_replay_runs_match_advisor_ask_and_resume(mcp_data: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """The same cassette through `advisor ask` and `advisor resume` and through the tools writes the same run
    record: the same input reaches the same graph. Mutations: mcp_ask_drops_photo (the photo never reaches
    intake), mcp_ask_drops_typed_identity (a typed identity never reaches intake)."""
    flo_args = ["--manufacturer", "Sundance Spas", "--model", "Optima 880", "--serial", "", "--date", "",
                "--code", "FLO"]
    assert cli.main(["ask", "--symptom", "panel shows FLO", "--photo", str(PLATE), "--cassette", str(FLO)]) == 0
    cli_flo = capsys.readouterr().out.split("advisor ask: thread ", 1)[1].split(" ", 1)[0]
    assert cli.main(["resume", cli_flo, *flo_args, "--cassette", str(FLO)]) == 0
    unknown = CASSETTES / "unknown.json"
    identity = load_cassette(unknown).input["identity"]
    assert cli.main(["ask", "--symptom", "not heating", "--identity", json.dumps(identity),
                     "--cassette", str(unknown)]) == 0
    cli_unknown = capsys.readouterr().out.split("advisor ask: thread ", 1)[1].split(" ", 1)[0]

    flo_server = mcp_server.build_server(cassette=str(FLO))
    paused = ok(call(flo_server, "get_service_brief", {"symptom": "panel shows FLO", "photo_path": str(PLATE)}))
    mcp_flo = ok(call(flo_server, "resume_brief", {"thread_id": paused["thread_id"], **OWNER, "code": "FLO"}))
    mcp_unknown = ok(call(mcp_server.build_server(cassette=str(unknown)), "get_service_brief",
                          {"symptom": "not heating", "identity": identity}))
    assert mcp_unknown["status"] == "no_reliable_answer"
    assert _strip(run_record(mcp_flo["run_id"])) == _strip(run_record(cli_flo))
    assert _strip(run_record(mcp_unknown["run_id"])) == _strip(run_record(cli_unknown))


@pytest.mark.parametrize("mode", ["auto", "legacy"])
def test_elicitation_asks_the_identity_question_and_finishes_in_one_call(mcp_data: Path, mode: str) -> None:
    """A client that declared form elicitation is asked the pause's question, prefilled with what the plate
    read, and the one call ends with the brief: by a returned question on 2026-07-28 ("auto" here) and by a
    request in the middle of the call on a handshake era connection ("legacy"). Mutations:
    mcp_modern_client_asked_mid_call (a 2026-07-28 client is sent a request it cannot take);
    mcp_returned_identity_question_dropped (a 2026-07-28 client is handed the pause instead of the question);
    mcp_identity_form_not_prefilled (the form drops what the plate read)."""
    server = mcp_server.build_server(cassette=str(FLO))
    person = Answers()
    result = ok(call(server, "get_service_brief", {"symptom": "panel shows FLO", "photo_path": str(PLATE)},
                     mode=mode, elicit=person))
    assert result["status"] == "ok"
    assert person.kinds() == ["identity"]
    question = person.asked[0]
    assert f"paused as thread {result['thread_id']}" in question.message
    assert "Read from the plate photo: manufacturer SUNDANCE® SPAS (high); model OPTIMA 880 (high)" in question.message
    fields = question.requested_schema["properties"]
    assert fields["model"]["default"] == "OPTIMA 880" and fields["code"]["default"] == "FLO"
    assert set(fields) == {"manufacturer", "model", "serial", "manufacture_date", "code"}
    values, still_paused = thread_values(result["thread_id"])
    assert not still_paused and values["identity"]["model"] == "Optima 880" and values["observed_code"] == "FLO"
    assert len(server.advisor.pending) == 0


@pytest.mark.parametrize("mode", ["auto", "legacy"])
def test_a_declined_identity_question_leaves_the_thread_paused(mcp_data: Path, mode: str) -> None:
    """Declining the form spends nothing more and hands back the paused thread, which resume_brief then
    finishes, as `advisor resume` would. Mutations: mcp_returned_decline_resumes (a declined returned question
    resumes with the plate's text); mcp_pushed_decline_resumes (a declined request mid call does the same)."""
    server = mcp_server.build_server(cassette=str(FLO))
    person = Answers(action="decline")
    paused = ok(call(server, "get_service_brief", {"symptom": "panel shows FLO", "photo_path": str(PLATE)},
                     mode=mode, elicit=person))
    assert paused["status"] == "paused" and "The identity question was declined." in paused["message"]
    assert person.kinds() == ["identity"]
    assert thread_values(paused["thread_id"])[1] is True
    final = ok(call(server, "resume_brief", {"thread_id": paused["thread_id"], **OWNER, "code": "FLO"}))
    assert final["status"] == "ok"


# ---------------------------------------------------------------------------
# Citations and safety flags, verbatim
# ---------------------------------------------------------------------------

CASES = [
    ("flo", {"photo": True}),
    ("notheating", {"photo": True}),
    ("hvac", {}),
    ("unknown", {}),
    ("synthetic/flo_three_candidates", {"photo": True}),
    ("synthetic/optima_history", {"appliance_id": "appl-optima880"}),
    ("synthetic/hvac_invented_code", {}),
    ("synthetic/at_caps_first_pass", {}),
    ("synthetic/sx100_discontinued", {}),
]


def _run_case(case: str, how: dict[str, Any]) -> tuple[dict[str, Any], Any]:
    """Run one cassette through the tools to its end, answering a pause as the cassette recorded it."""
    cassette = load_case(case)
    server = mcp_server.build_server(cassette=str(cassette.path))
    inp = cassette.input
    args: dict[str, Any] = {"symptom": inp["symptom"]}
    if how.get("photo"):
        args["photo_path"] = str(plate_for_sha256(inp["plate_sha256"]))
    elif how.get("appliance_id"):
        args["appliance_id"] = how["appliance_id"]
    else:
        args["identity"] = inp["identity"]
    result = call(server, "get_service_brief", args)
    response = ok(result)
    if response["status"] == "paused":
        answer = cassette.resume
        resume = {"thread_id": response["thread_id"], **answer["identity"]}
        if answer.get("observed_code") is not None:
            resume["code"] = answer["observed_code"]
        result = call(server, "resume_brief", resume)
        response = ok(result)
    return response, result


@pytest.mark.parametrize("case,how", CASES, ids=[c for c, _ in CASES])
def test_every_citation_and_safety_flag_the_brief_holds_reaches_the_response(
        mcp_data: Path, case: str, how: dict[str, Any]) -> None:
    """For every recorded case, the structured content and the text carry each step to try first with its
    flag and its cited source, each safety flagged step word for word, each candidate's and caution's source,
    and the coverage line, exactly as the run record holds them; the full brief is carried unchanged.
    Mutations: mcp_writer_flags_dropped (steps the writer flagged leave safety_flags);
    mcp_citation_number_off_by_one (a cited source gets its neighbor's number); mcp_text_drops_safety_label
    (the text no longer marks a safety step); mcp_text_drops_step_citation (the text drops a step's source);
    mcp_candidate_source_dropped (candidates lose their source); mcp_coverage_line_dropped (the warranty
    coverage line is left out)."""
    response, result = _run_case(case, how)
    record = run_record(response["run_id"])
    brief = record["brief"]
    view = response["brief"]
    text = text_of(result)
    sources = brief.get("sources") or []
    assert view["full"] == brief and view["status"] == brief["status"] == response["status"]
    assert len(view["try_first"]) == len(brief.get("try_first") or [])
    flagged = [s for s in brief.get("try_first") or [] if s.get("safety_flag") is True]
    assert [f["step"] for f in view["safety_flags"]] == [s["step"] for s in flagged]
    for n, (step, shown) in enumerate(zip(brief.get("try_first") or [], view["try_first"], strict=True), start=1):
        assert (shown["step"], shown["detail"], shown["safety_flag"]) == \
            (step["step"], step.get("detail"), step.get("safety_flag") is True)
        cited = sources[step["source_index"]]
        assert shown["source"]["url"] == cited["url"] and shown["source"]["title"] == cited.get("title")
        assert shown["source"]["number"] == step["source_index"] + 1
        label = "SAFETY: " if step.get("safety_flag") is True else ""
        assert f"  {n}. {label}{step['step']} [{step['source_index'] + 1}]" in text
        assert cited["url"] in text
        if step.get("safety_flag") is True:
            entry = next(f for f in view["safety_flags"] if f["position"] == n)
            assert (entry["step"], entry["detail"], entry["source"]["url"]) == (step["step"], step.get("detail"),
                                                                                cited["url"])
            assert entry["raised_by"] == record["safety"]["steps"][n - 1]["raised_by"]
    for cand, shown in zip(brief.get("candidates") or [], view["candidates"], strict=True):
        assert shown["documented_meaning"] == cand["documented_meaning"]
        assert shown["source"]["url"] == sources[cand["source_index"]]["url"]
        assert f"[{cand['source_index'] + 1}]" in text
    caution = brief.get("warranty_caution")
    if caution:
        expected = sources[caution["source_index"]]["url"] if caution.get("source_index") is not None else None
        assert view["warranty_caution"]["text"] == caution["text"] and caution["text"] in text
        assert (view["warranty_caution"]["source"] or {}).get("url") == expected
    warranty = brief.get("warranty")
    if warranty:
        assert view["warranty"]["coverage_statement"] == warranty["coverage_statement"]
        assert f"Coverage: {warranty['coverage_statement']}" in text
    else:
        assert view["warranty"] is None
    if brief.get("no_reliable_answer"):
        assert view["no_reliable_answer"]["searched"] == brief["no_reliable_answer"]["searched"]
    assert [s["url"] for s in view["sources"]] == [s["url"] for s in sources]
    assert response["cost"]["run_total_usd"] == pytest.approx(record["cost_usd"])
    assert f"run {response['run_id']}" in text and "Cost: " in text


# ---------------------------------------------------------------------------
# Live: nothing is sent and no ledger row is written without the confirmation
# ---------------------------------------------------------------------------

LIVE_ASK = {"symptom": "panel shows FLO", "photo_path": str(PLATE)}


def _rows() -> list[dict[str, Any]]:
    return Ledger(config.LEDGER_PATH).rows()


def _nothing_sent(fakes: LiveFakes) -> None:
    assert fakes.constructed == [] and fakes.stubs == {} and fakes.jev_requests == []
    assert _rows() == []


def test_a_live_brief_sends_nothing_and_writes_no_ledger_row_until_confirmed(
        mcp_live: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """The first call returns the preflight `advisor ask --live` prints, word for word; no client is built and
    no ledger row is written by a missing, wrong, reused or foreign confirmation, or by an elicitation answer
    that is not exactly proceed; then the right one runs. Mutations: mcp_gate_accepts_any_confirm (a typed
    line other than proceed spends); mcp_gate_confirm_without_preflight (confirm="proceed" spends on the first
    call); mcp_pending_reusable (a confirmation can be used twice); mcp_pending_ignores_arguments (a
    confirmation for other arguments spends); mcp_elicited_proceed_not_checked (any accepted elicitation
    spends); mcp_preflight_drops_keys_line (the preflight differs from the command's)."""
    fakes = LiveFakes(flo_data()).install(monkeypatch)
    monkeypatch.setattr(sys, "stdin", io.StringIO("no\n"))
    assert cli.main(["ask", "--symptom", LIVE_ASK["symptom"], "--photo", str(PLATE), "--live"]) == cli.EXIT_REFUSED
    printed = capsys.readouterr().out.splitlines()
    _nothing_sent(fakes)

    server = mcp_server.build_server(live=True)
    first = ok(call(server, "get_service_brief", LIVE_ASK))
    assert first["status"] == "confirmation_required" and first["mode"] == "cheap"
    lines = first["preflight"]["lines"]
    assert lines == printed
    assert lines[-1] == "Type proceed to spend this; anything else cancels and spends nothing."
    assert first["preflight"]["sends"] == [f"This run sends the plate photo at {PLATE.resolve()} to the model."]
    _nothing_sent(fakes)

    assert ok(call(server, "get_service_brief", {**LIVE_ASK, "confirm": "proceed"}))["status"] == \
        "confirmation_required"
    second = ok(call(server, "get_service_brief", LIVE_ASK))["preflight"]["confirmation_id"]
    refused = ok(call(server, "get_service_brief", {**LIVE_ASK, "confirm": "yes", "confirmation_id": second}))
    assert refused["status"] == "not_confirmed" and refused["message"] == mcp_server.NOT_CONFIRMED
    reused = ok(call(server, "get_service_brief", {**LIVE_ASK, "confirm": "proceed", "confirmation_id": second}))
    assert reused["status"] == "confirmation_required"
    other = ok(call(server, "get_service_brief", {**LIVE_ASK, "symptom": "no heat"}))["preflight"]["confirmation_id"]
    foreign = ok(call(server, "get_service_brief", {**LIVE_ASK, "confirm": "proceed", "confirmation_id": other}))
    assert foreign["status"] == "confirmation_required"
    for mode in ("auto", "legacy"):
        answer = ok(call(server, "get_service_brief", LIVE_ASK, mode=mode, elicit=Answers(proceed="proceed now")))
        assert answer["status"] == "not_confirmed"
    _nothing_sent(fakes)

    fresh = ok(call(server, "get_service_brief", LIVE_ASK))["preflight"]["confirmation_id"]
    paused = ok(call(server, "get_service_brief", {**LIVE_ASK, "confirm": "proceed", "confirmation_id": fresh}))
    assert paused["status"] == "paused"
    assert set(fakes.chats) == {"read_plate"} and fakes.stubs == {}
    assert {r["node"] for r in _rows() if r["kind"] == "charge"} == {"read_plate"}


def test_a_live_brief_by_elicitation_confirms_each_paid_segment(
        mcp_live: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """On a 2026-07-28 client with elicitation, one call asks for the ask preflight's proceed, the identity,
    then the resume preflight's proceed, and only then runs the rest: every live call is reserved in the
    ledger, the run record carries SC11, and no key reaches the response. Mutations:
    mcp_resume_preflight_skipped (the rest of the run spends without its own preflight);
    mcp_live_session_confirm_never_refuses (a run whose preflight was never confirmed still spends)."""
    fakes = LiveFakes(flo_data()).install(monkeypatch)
    server = mcp_server.build_server(live=True)
    person = Answers()
    result = call(server, "get_service_brief", LIVE_ASK, elicit=person)
    final = ok(result)
    assert final["status"] == "ok"
    assert person.kinds() == ["proceed", "identity", "proceed"]
    assert person.asked[0].message.splitlines()[1].startswith("advisor ask: preflight, mode cheap, LIVE")
    assert person.asked[2].message.splitlines()[0].startswith("advisor resume: preflight, mode cheap, LIVE")
    assert all(fakes.all_checks()) and set(fakes.chats) == {"read_plate", "research", "synthesize"}
    charges = {r["node"] for r in _rows() if r["kind"] == "charge"}
    assert {"read_plate", "research", "synthesize", "safety_check"} <= charges
    record = run_record(final["run_id"])
    assert record["sc11"]["result"] == "pass" and final["cost"]["sc11"] == record["sc11"]
    assert final["cost"]["real_money"] is True and final["cost"]["run_cap_usd"] == config.RUN_CAP_USD["cheap"]
    assert any(note.startswith("SC11: pass") for note in final["notes"])
    blob = json.dumps(final) + text_of(result)
    assert FAKE_ANTHROPIC not in blob and FAKE_TAVILY not in blob and FAKE_TYPESAFE not in blob

    session = server.advisor.live_session("ask", "cheap")
    with pytest.raises(mcp_server.PreflightChanged):
        session.confirm_run("ask", photo=False, run_id="t-0000000000000000")
    session.approved = session.run_preflight("ask", photo=False, run_id=None)
    session.confirm_run("ask", photo=False, run_id="t-0000000000000000")


def test_a_live_run_never_starts_without_a_confirmed_session(mcp_live: Path,
                                                             monkeypatch: pytest.MonkeyPatch) -> None:
    """Behind every confirmation path: a live ask or resume segment refuses to start with no session, or with
    one whose preflight nobody confirmed, whichever code calls it. Mutation: mcp_live_run_without_session."""
    fakes = LiveFakes(flo_data()).install(monkeypatch)
    server = mcp_server.build_server(live=True)
    advisor = server.advisor
    req = mcp_server.BriefArgs(symptom=LIVE_ASK["symptom"], photo_path=LIVE_ASK["photo_path"])
    for session in (None, advisor.live_session("ask", "cheap")):
        with pytest.raises(cli.CliRefusal, match="only after its preflight is confirmed"):
            advisor.run_ask(req, "cheap", None, session=session)
    _nothing_sent(fakes)
    shown = ok(call(server, "get_service_brief", LIVE_ASK))["preflight"]["confirmation_id"]
    paused = ok(call(server, "get_service_brief", {**LIVE_ASK, "confirm": "proceed", "confirmation_id": shown}))
    value = mcp_server.resume_value_from({**OWNER, "code": "FLO"}, paused["pause"]["identity"])
    for session in (None, advisor.live_session("resume", "cheap")):
        with pytest.raises(cli.CliRefusal, match="only after its preflight is confirmed"):
            advisor.run_resume(paused["thread_id"], value, "cheap", None, session=session)
    assert set(fakes.chats) == {"read_plate"} and fakes.stubs == {}
    assert {r["node"] for r in _rows() if r["kind"] == "charge"} == {"read_plate"}
    assert thread_values(paused["thread_id"])[1] is True


def test_a_preflight_that_moved_after_it_was_shown_is_shown_again(mcp_live: Path,
                                                                  monkeypatch: pytest.MonkeyPatch) -> None:
    """A confirmation answers the lines it was shown: when the build spend moves before it is used, the
    preflight is shown again and nothing is sent. Mutation: mcp_gate_ignores_changed_lines (a stale
    confirmation spends against a preflight nobody saw)."""
    fakes = LiveFakes(flo_data()).install(monkeypatch)
    server = mcp_server.build_server(live=True)
    shown = ok(call(server, "get_service_brief", LIVE_ASK))
    with sqlite3.connect(config.LEDGER_PATH) as conn:
        conn.execute("INSERT INTO entries (ts, run_id, mode, node, provider, kind, usd) VALUES "
                     "('2026-10-01T00:00:00+00:00', 'earlier', 'cheap', 'synthesize', 'anthropic', 'charge', ?)",
                     (config.RUN_CAP_USD["cheap"] / 3,))
    again = ok(call(server, "get_service_brief", {**LIVE_ASK, "confirm": "proceed",
                                                  "confirmation_id": shown["preflight"]["confirmation_id"]}))
    assert again["status"] == "confirmation_required" and mcp_server.PREFLIGHT_CHANGED in again["message"]
    assert again["preflight"]["lines"] != shown["preflight"]["lines"]
    assert fakes.constructed == [] and fakes.stubs == {} and [r["run_id"] for r in _rows()] == ["earlier"]


def test_a_live_failure_is_reported_without_a_key(mcp_live: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A live run that fails with a key in its error text, from anywhere in the run, reaches the model with the
    key redacted, as the command redacts what it prints. The paid call sites already redact their own errors
    (agent/redaction.py); this is the backstop behind them, so the failure here comes from the runner itself.
    Mutation: mcp_live_failure_not_redacted."""
    from agent import graph

    LiveFakes(flo_data()).install(monkeypatch)

    def fails(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError(f"upstream said {FAKE_ANTHROPIC} is invalid")

    monkeypatch.setattr(graph, "run_until_pause_or_end", fails)
    server = mcp_server.build_server(live=True)
    shown = ok(call(server, "get_service_brief", LIVE_ASK))
    failed = call(server, "get_service_brief", {**LIVE_ASK, "confirm": "proceed",
                                                "confirmation_id": shown["preflight"]["confirmation_id"]})
    assert failed.is_error
    message = text_of(failed)
    assert "the live call failed: RuntimeError: upstream said [redacted] is invalid" in message
    assert "every call made so far is in the ledger" in message and FAKE_ANTHROPIC not in message


# ---------------------------------------------------------------------------
# resume_brief refusals, property_status, and the pending answers
# ---------------------------------------------------------------------------


def test_resume_brief_refuses_what_advisor_resume_refuses_and_never_reruns_a_finished_thread(
        mcp_data: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An unknown thread, an id that is not one, and a replay thread resumed in a live mode are refused before
    anything runs; a finished thread is shown, not run again. Mutations: mcp_thread_mode_check_off (a replay
    thread is resumed as a live one); mcp_finished_thread_resumed (a finished thread is handled as paused)."""
    server = mcp_server.build_server(cassette=str(FLO))
    for thread_id, words in (("t-0123456789abcdef", "no thread 't-0123456789abcdef'"), ("../x", "is not an id")):
        refused = call(server, "resume_brief", {"thread_id": thread_id, "model": "Optima 880"})
        assert refused.is_error and words in text_of(refused)
    paused = ok(call(server, "get_service_brief", {"symptom": "panel shows FLO", "photo_path": str(PLATE)}))
    done = ok(call(server, "resume_brief", {"thread_id": paused["thread_id"], **OWNER, "code": "FLO"}))
    rows = Ledger(config.REPLAY_LEDGER_PATH).rows()
    again = ok(call(server, "resume_brief", {"thread_id": paused["thread_id"], **OWNER}))
    assert again["status"] == "ok" and again["run_id"] == done["run_id"]
    assert again["message"].startswith("This thread is not paused, so nothing was run")
    assert Ledger(config.REPLAY_LEDGER_PATH).rows() == rows

    second = ok(call(server, "get_service_brief", {"symptom": "panel shows FLO", "photo_path": str(PLATE)}))
    monkeypatch.setenv(config.ENV_MODE, "cheap")
    live = mcp_server.build_server(live=True)
    refused = call(live, "resume_brief", {"thread_id": second["thread_id"], "model": "Optima 880"})
    assert refused.is_error and "ran in 'replay' mode, not 'cheap'" in text_of(refused)
    assert not config.LEDGER_PATH.exists()


def test_property_status_is_offline_and_shows_what_the_property_page_shows(
        mcp_data: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """property_status computes coverage, the replacement rows and the recall lines with the property page's
    own code for the pinned UTC date, writes no file and opens no ledger; the property resource is the page
    `advisor property show` writes, byte for byte. Mutations: mcp_status_wall_clock (statuses use the real
    clock, not the run date); mcp_status_coverage_rows_from_other_appliance (an appliance shows another's
    coverage); mcp_property_page_wrong_date (the page resource is dated differently from the command's)."""
    from agent.rules import coverage as coverage_rule

    server = mcp_server.build_server()
    result = call(server, "property_status", {"property_id": "prop-b"})
    status = ok(result)
    today = cli._today()
    assert status["as_of"] == today.isoformat() and status["graph"] == str(config.REPLAY_GRAPH_PATH)
    softener = next(a for a in status["appliances"] if a["appliance_id"] == "appl-softener")
    assert sorted((r["component"], r["end_date"], r["status"]) for r in softener["warranty"]["rows"]) == [
        ("control valve", "2031-05-04", "active"), ("labor", "2027-04-20", "active"),
        ("resin tank", "2036-05-04", "active")]
    assert softener["warranty"]["statement"].startswith(
        f"Warranty coverage on file that is active as of {today.isoformat()} (UTC):")
    from agent.registry import Registry

    registry = Registry(config.REGISTRY_PATH)
    assert [a["appliance_id"] for a in status["appliances"]] == [a["id"] for a in registry.list_appliances("prop-b")]
    for a in status["appliances"]:
        row = registry.get_appliance(a["appliance_id"])
        rows = coverage_rule.coverage_rows(registry.warranty_coverage(row["id"]), row, today)
        assert a["warranty"]["rows"] == rows
        assert a["warranty"]["statement"] == coverage_rule.coverage_statement(
            rows, today, terms_on_file=bool(row.get("warranty_terms")))
        if pr.is_synthetic_maker(row):
            assert a["recalls"]["status"] == pr.NOT_APPLICABLE
            assert a["recalls"]["lines"][1] == "  " + pr.NOT_APPLICABLE_SENTENCE.format(maker=row["manufacturer"])
        else:
            assert a["recalls"]["status"] == pr.NOT_CHECKED
            assert a["recalls"]["lines"][1] == "  " + pr.NOT_CHECKED_SENTENCE
        assert a["replacement"]["statement"] == "No discontinuation documented in what this tool has verified."
        assert a["replacement"]["age"]["statement"].endswith(f"as of {today.isoformat()} (UTC).") or \
            "so the age is not computed" in a["replacement"]["age"]["statement"]
    assert "Warranty: Warranty coverage on file that is active" in text_of(result)
    assert not config.PROPERTY_OUT_DIR.exists() and not config.LEDGER_PATH.exists()
    assert not (config.PAGES_DIR.parent / "runs").exists()

    page = read(server, status["page"]["resource_uri"])
    assert cli.main(["property", "show", "prop-b"]) == cli.EXIT_OK
    capsys.readouterr()
    assert page == (config.PROPERTY_OUT_DIR / "prop-b.html").read_text(encoding="utf-8")
    unknown = call(server, "property_status", {"property_id": "prop-zz"})
    assert unknown.is_error and "unknown property 'prop-zz'; the registry holds prop-a, prop-b." in text_of(unknown)


def test_pending_answers_are_used_once_bound_to_their_call_and_expire() -> None:
    """Mutations: mcp_pending_never_expires (a confirmation outlives CONFIRMATION_TTL_S);
    mcp_pending_ignores_tool (a confirmation shown for one tool answers another)."""
    now = [1000.0]
    store = mcp_server.PendingAnswers(clock=lambda: now[0])
    digest = mcp_server.args_digest("get_service_brief", {"symptom": "x"})

    def add() -> str:
        return store.add(mcp_server.Pending(kind="ask", tool="get_service_brief", digest=digest, lines=("a",)))

    first = add()
    assert store.take(first, tool="get_service_brief", digest=digest, kinds=("ask",)) is not None
    assert store.take(first, tool="get_service_brief", digest=digest, kinds=("ask",)) is None
    assert store.take(add(), tool="resume_brief", digest=digest, kinds=("ask",)) is None
    assert store.take(add(), tool="get_service_brief", digest="other", kinds=("ask",)) is None
    assert store.take(add(), tool="get_service_brief", digest=digest, kinds=("resume",)) is None
    late = add()
    now[0] += mcp_server.CONFIRMATION_TTL_S
    assert store.take(late, tool="get_service_brief", digest=digest, kinds=("ask",)) is None
    assert mcp_server.args_digest("get_service_brief", {"symptom": "x"}) != \
        mcp_server.args_digest("resume_brief", {"symptom": "x"})


# ---------------------------------------------------------------------------
# check_product_recalls never sends without confirmation
# ---------------------------------------------------------------------------


def _install_fetch(monkeypatch: pytest.MonkeyPatch) -> MakerFetch:
    fetch = MakerFetch({"Trane": [MATCHES[0]]})
    monkeypatch.setattr(pr, "urllib_fetch", fetch)
    monkeypatch.setattr(pr, "_now", lambda: NOON)
    return fetch


def _stored() -> list[Path]:
    return sorted(config.RECALLS_DIR.rglob("*.json")) if config.RECALLS_DIR.exists() else []


def _recall_runs() -> list[Path]:
    runs = config.PAGES_DIR.parent / "runs"
    return sorted(runs.glob("recalls-*.json")) if runs.exists() else []


def test_check_product_recalls_sends_nothing_until_proceed(mcp_data: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The first call returns product_recalls.plan_lines word for word and sends nothing; a wrong, missing or
    reused confirmation, or a declined or wrong elicitation, sends nothing; then the right one sends each real
    maker's three requests, stores each check and logs the run, never querying an invented maker. Mutations:
    mcp_recalls_send_before_gate (the requests go out before the confirmation); mcp_recalls_proceed_bypass
    (confirm="proceed" sends on the first call); mcp_recalls_query_synthetic_makers (an invented maker is
    queried); mcp_recalls_elicited_answer_not_checked (any accepted elicitation sends)."""
    fetch = _install_fetch(monkeypatch)
    server = mcp_server.build_server()
    first = ok(call(server, "check_product_recalls", {"property_id": "prop-a"}))
    assert first["status"] == "confirmation_required" and first["properties"] == ["prop-a"]
    cli.ensure_registry()
    from agent.registry import Registry

    appliances = Registry(config.REGISTRY_PATH).list_appliances("prop-a")
    assert first["preflight"]["lines"] == pr.plan_lines(appliances, config.RECALLS_DIR)
    assert first["preflight"]["confirmation_id"]

    def nothing_sent() -> None:
        assert fetch.urls == [] and _stored() == [] and _recall_runs() == []
        assert not config.LEDGER_PATH.exists() and not config.REPLAY_LEDGER_PATH.exists()

    nothing_sent()
    assert ok(call(server, "check_product_recalls", {"property_id": "prop-a", "confirm": "proceed"}))["status"] == \
        "confirmation_required"
    shown = ok(call(server, "check_product_recalls", {"property_id": "prop-a"}))["preflight"]["confirmation_id"]
    wrong = ok(call(server, "check_product_recalls", {"property_id": "prop-a", "confirm": "Proceed",
                                                      "confirmation_id": shown}))
    assert wrong["status"] == "not_confirmed"
    assert ok(call(server, "check_product_recalls", {"property_id": "prop-a", "confirm": "proceed",
                                                     "confirmation_id": shown}))["status"] == "confirmation_required"
    for mode in ("auto", "legacy"):
        for person in (Answers(action="decline"), Answers(proceed="yes")):
            assert ok(call(server, "check_product_recalls", {"property_id": "prop-a"}, mode=mode,
                           elicit=person))["status"] == "not_confirmed"
            assert person.kinds() == ["proceed"]
            assert person.asked[0].message.splitlines()[0] == \
                "advisor property recalls: preflight, LIVE (requests to an outside service)"
    nothing_sent()

    fresh = ok(call(server, "check_product_recalls", {"property_id": "prop-a"}))["preflight"]["confirmation_id"]
    checked = ok(call(server, "check_product_recalls", {"property_id": "prop-a", "confirm": "proceed",
                                                        "confirmation_id": fresh}))
    assert checked["status"] == "checked" and checked["complete"] is True
    assert len(fetch.urls) == 6
    makers = {v[0] for url in fetch.urls for k, v in parse_qs(urlsplit(url).query).items() if k != "format"}
    assert makers == {"Trane", "Sundance Spas"}
    assert [p.parent.name for p in _stored()] == ["appl-optima880", "appl-xr16"]
    assert [Path(r).name for r in [checked["run_record"]]] == [p.name for p in _recall_runs()]
    by_id = {r["appliance_id"]: r for r in checked["results"]}
    assert by_id["appl-dishwasher"]["status"] == pr.NOT_APPLICABLE and by_id["appl-dishwasher"]["stored"] is None
    assert "CPSC product recall SYN001 names the model XR16 4TTR6036" in " ".join(by_id["appl-xr16"]["lines"])


def test_the_recall_check_stores_what_advisor_property_recalls_live_stores(
        mcp_data: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """The command after a typed proceed and the tool after confirm="proceed" store the same checks and log the
    same entries. Mutation: mcp_recalls_drop_model_number (the tool checks without the model number)."""
    _install_fetch(monkeypatch)
    monkeypatch.setattr(sys, "stdin", io.StringIO("proceed\n"))
    assert cli.main(["property", "recalls", "prop-a", "--live"]) == cli.EXIT_OK
    capsys.readouterr()
    by_command = {p.parent.name: json.loads(p.read_text(encoding="utf-8")) for p in _stored()}
    [command_run] = [json.loads(p.read_text(encoding="utf-8")) for p in _recall_runs()]
    shutil.rmtree(config.RECALLS_DIR)
    for p in _recall_runs():
        p.unlink()

    server = mcp_server.build_server()
    shown = ok(call(server, "check_product_recalls", {"property_id": "prop-a"}))["preflight"]["confirmation_id"]
    ok(call(server, "check_product_recalls", {"property_id": "prop-a", "confirm": "proceed",
                                              "confirmation_id": shown}))
    by_tool = {p.parent.name: json.loads(p.read_text(encoding="utf-8")) for p in _stored()}
    [tool_run] = [json.loads(p.read_text(encoding="utf-8")) for p in _recall_runs()]
    assert by_tool == by_command
    assert tool_run["checks"] == command_run["checks"] and tool_run["cost_usd"] == command_run["cost_usd"] == 0


# ---------------------------------------------------------------------------
# The build fingerprint, the entry point and the stdio wire
# ---------------------------------------------------------------------------


def test_the_server_is_in_the_build_fingerprint_and_imports_nothing_it_leaves_out(tmp_path: Path) -> None:
    """The server decides whether a live run may spend, so an edit to it is a new build (decision 98); and it
    imports no module build_info leaves out. Mutation: build_info_excludes_mcp_server."""
    root = tmp_path / "agent"
    for rel in ("config.py", "mcp_server.py"):
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(config.REPO_ROOT / "agent" / rel, root / rel)
    before = build_info.fingerprint(root)
    with (root / "mcp_server.py").open("a", encoding="utf-8") as handle:
        handle.write("\n# changed\n")
    assert build_info.fingerprint(root) != before
    assert not build_info.is_excluded("mcp_server.py")
    tree = ast.parse((config.REPO_ROOT / "agent" / "mcp_server.py").read_text(encoding="utf-8"))
    imported = {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module}
    imported |= {alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
    left_out = sorted(name for name in imported if name.startswith("agent.") and any(
        build_info.is_excluded(rel) for rel in (name.removeprefix("agent.").replace(".", "/") + ".py",
                                                name.removeprefix("agent.").replace(".", "/") + "/__init__.py")))
    assert left_out == []


STDIO_CLIENT = """
import json, os, sys
import anyio
from mcp import Client, StdioServerParameters

stray = []

async def watch(message):
    if isinstance(message, Exception):  # a line on stdout that is not the protocol (the SDK logs and goes on)
        stray.append(type(message).__name__)

async def main():
    env = {name: os.environ[name] for name in ("PYTHONPATH", "ADVISOR_DATA_DIR", "LANGGRAPH_STRICT_MSGPACK")}
    params = StdioServerParameters(command=sys.executable, args=["-m", "agent.mcp_server"], env=env, cwd=os.getcwd())
    async with Client(params, message_handler=watch) as client:
        tools = await client.list_tools()
        result = await client.call_tool("property_status", {"property_id": "prop-a"})
        print(json.dumps({"protocol": client.protocol_version, "tools": sorted(t.name for t in tools.tools),
                          "property": result.structured_content["property_id"],
                          "appliances": len(result.structured_content["appliances"]), "stray": stray}))

anyio.run(main)
"""


def test_the_server_starts_over_stdio_from_its_entry_point(tmp_path: Path) -> None:
    """`python -m agent.mcp_server` serves over real stdio: a guarded child process launches it the way a host
    does, with a host's bare environment, and lists the tools and calls property_status over the wire, which
    only works when nothing but the protocol reaches stdout. The `advisor-mcp` script names the same main.
    Mutation: mcp_main_prints_to_stdout (a line printed before serving corrupts the wire)."""
    scripts = tomllib.loads((config.REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["scripts"]
    assert scripts["advisor-mcp"] == "agent.mcp_server:main" and callable(mcp_server.main)
    data = tmp_path / "data"
    proc = spawn_offline_child(["-c", STDIO_CLIENT], kind="python", timeout=120,
                               env_extra={"ADVISOR_DATA_DIR": str(data), config.ENV_MODE: None,
                                          config.ENV_CASSETTE: None})
    assert proc.returncode == 0, proc.stderr[-3000:]
    assert proc.stderr.count(GUARD_MARKER) >= 2, proc.stderr  # the client and the server it launched
    result = json.loads(proc.stdout.strip().splitlines()[-1])
    assert result == {"protocol": "2026-07-28", "tools": sorted(TOOLS), "property": "prop-a", "appliances": 3,
                      "stray": []}
    assert (data / "registry.sqlite").is_file() and not (data / "ledger").exists()


def test_the_entry_point_refuses_a_live_mode_without_live(monkeypatch: pytest.MonkeyPatch,
                                                          capsys: pytest.CaptureFixture[str]) -> None:
    """main() refuses before serving, as `advisor` refuses: a live ADVISOR_MODE needs --live, and --live needs
    a live mode. Mutation: mcp_main_skips_mode_check (the server starts in a live mode without --live)."""
    monkeypatch.setenv(config.ENV_MODE, "cheap")
    assert mcp_server.main([]) == cli.EXIT_REFUSED
    assert "advisor-mcp: refused: ADVISOR_MODE=cheap spends real money and needs --live" in capsys.readouterr().err
    monkeypatch.delenv(config.ENV_MODE)
    assert mcp_server.main(["--live"]) == cli.EXIT_REFUSED
    assert "--live needs ADVISOR_MODE set to one of" in capsys.readouterr().err


def test_counts_in_the_result_messages_are_singular_at_one() -> None:
    """A result message says "1 step", "1 flagged as a safety step", "1 documented candidate" and "1 appliance",
    and the plural otherwise (the live log of 16 October printed "1 documented candidates"; decision 105).
    Mutations: mcp_counts_always_plural (a count's noun stays plural at 1); mcp_brief_flags_always_plural (one
    flagged step reads "1 flagged as safety steps")."""
    assert mcp_server.brief_counts(1, 1, 1) == "1 step to try first, 1 flagged as a safety step; 1 documented candidate"
    assert mcp_server.brief_counts(2, 0, 3) == ("2 steps to try first, 0 flagged as safety steps; "
                                                "3 documented candidates")
    assert mcp_server.counted(1, "appliance", "appliances") == "1 appliance"
    assert mcp_server.counted(0, "appliance", "appliances") == "0 appliances"


def test_a_paused_brief_tells_the_assistant_to_leave_the_unit_to_the_owner(mcp_data: Path) -> None:
    """The pause's next step, the plain text the model reads and the server's standing instructions all tell the
    assistant to show the owner what was read and never fill in a field itself, because the server cannot tell
    who supplied the fields resume_brief receives (decision 105); the finished brief's message then counts its one
    flagged step and one candidate in the singular. Mutations: mcp_pause_drops_ask_owner (the pause no longer
    asks for the owner's answer); mcp_instructions_drop_ask_owner (the standing instructions no longer say it)."""
    server = mcp_server.build_server(cassette=str(FLO))
    result = call(server, "get_service_brief", {"symptom": "panel shows FLO", "photo_path": str(PLATE)})
    paused = ok(result)
    assert paused["status"] == "paused"
    assert paused["next_step"].startswith(mcp_server.ASK_OWNER)
    assert mcp_server.ASK_OWNER in result.content[0].text
    assert "When a brief pauses for the owner to confirm the unit: " + mcp_server.ASK_OWNER in mcp_server.INSTRUCTIONS
    final = ok(call(server, "resume_brief", {"thread_id": paused["thread_id"], **OWNER, "code": "FLO"}))
    assert final["status"] == "ok"
    assert "2 steps to try first, 1 flagged as a safety step; 1 documented candidate." in final["message"]
