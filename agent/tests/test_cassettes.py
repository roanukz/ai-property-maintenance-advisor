"""Cassette loader, cassette builder and privacy diff (PLAN section 8.10).

Every input here is synthetic and lives in fixtures/cassette_samples/ or is
built inside the test; nothing reads the private v1 folder. Each test names
the code change (mutation) that turns it red.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from agent import config
from agent.replay import build_cassettes as bc
from agent.replay import privacy_diff as pd
from agent.replay.cassettes import CassetteError, cassette_from_dict, load_cassette

SAMPLES = Path(__file__).resolve().parent / "fixtures" / "cassette_samples"
CASSETTE = SAMPLES / "synthetic_cassette.json"


def sample() -> dict:
    return json.loads(CASSETTE.read_text(encoding="utf-8"))


def write(tmp_path: Path, data: dict, name: str = "c.json") -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def lookup() -> dict:
    return json.loads((SAMPLES / "synthetic_lookup.json").read_text(encoding="utf-8"))


MINI_SPEC = bc.CaseSpec(
    "mini", "synthetic0001", None,
    ("synthetic query one", "synthetic query two", "synthetic query three",
     "synthetic query four", "synthetic query five"),
    "synthetic sample",
)


def build_mini() -> tuple[dict, dict]:
    return bc.build_case(MINI_SPEC, {"synthetic0001": lookup()})


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------


def test_loader_rejects_cassette_missing_provenance(tmp_path):
    # Mutation: drop "provenance" from REQUIRED_KEYS (the loader then raises a
    # bare KeyError, not a CassetteError naming the key).
    data = sample()
    del data["provenance"]
    with pytest.raises(CassetteError, match="missing required key.*provenance"):
        load_cassette(write(tmp_path, data))


@pytest.mark.parametrize("key", ["derived_from", "copied_fields", "synthetic_fields", "built_by", "reviewed"])
def test_loader_rejects_incomplete_provenance(tmp_path, key):
    # Mutation: remove the key from PROVENANCE_REQUIRED.
    data = sample()
    del data["provenance"][key]
    with pytest.raises(CassetteError, match=f"provenance: missing required key.*{key}"):
        load_cassette(write(tmp_path, data))


def test_loader_rejects_copied_fields_without_a_source(tmp_path):
    # Mutation: drop the "no derived_from cannot list copied fields" check.
    data = sample()
    data["provenance"]["copied_fields"] = ["input.symptom"]
    with pytest.raises(CassetteError, match="cannot list copied fields"):
        load_cassette(write(tmp_path, data))


def test_loader_rejects_wrong_version_and_unknown_keys(tmp_path):
    # Mutation: drop the version check, or the unknown key check in _Checker.keys.
    data = sample()
    data["cassette_version"] = 2
    with pytest.raises(CassetteError, match="cassette_version"):
        load_cassette(write(tmp_path, data))
    data = sample()
    data["transcript"] = []
    with pytest.raises(CassetteError, match="unknown key.*transcript"):
        load_cassette(write(tmp_path, data))


def test_responses_and_tool_results_return_what_was_recorded():
    # Mutations: responses_for("synthesize") drops usage or wraps the draft
    # wrongly; read_plate or classifier served from the wrong key;
    # tool_results_for stops filtering by tool, reorders, or keeps "tool".
    raw = sample()
    c = load_cassette(CASSETTE)
    assert c.case == "synthetic_sample"
    assert c.responses_for("read_plate") == [
        {"structured": raw["read_plate"]["extraction"], "usage": raw["read_plate"]["usage"]}
    ]
    assert c.responses_for("classifier") == raw["classifier"]
    assert c.responses_for("research") == raw["research"]["script"]
    assert c.responses_for("synthesize") == [
        {"structured": raw["synthesize"][0]["draft"], "usage": raw["synthesize"][0]["usage"]}
    ]
    searches = [r for r in raw["research"]["tool_results"] if r["tool"] == "search"]
    assert c.tool_results_for("search") == [{k: v for k, v in r.items() if k != "tool"} for r in searches]
    assert c.tool_results_for("fetch") == [
        {k: v for k, v in raw["research"]["tool_results"][1].items() if k != "tool"}
    ]
    assert c.tool_results_for("search")[1] == {"query": "sx-100 e9 reset", "error": "Error 500: synthetic upstream failure"}
    assert (c.provenance, c.input, c.resume, c.caps, c.expect, c.page_texts) == (
        raw["provenance"], raw["input"], raw["resume"], raw["caps"], raw["expect"], raw["page_texts"]
    )
    with pytest.raises(ValueError, match="unknown node"):
        c.responses_for("validate")


def test_accessors_return_copies():
    # Mutation: return the stored list instead of a deep copy; the second
    # read then sees the first caller's edit.
    c = load_cassette(CASSETTE)
    c.responses_for("research")[0]["message"]["content"] = "edited"
    c.tool_results_for("search")[0]["results"].clear()
    assert c.responses_for("research")[0]["message"]["content"] == ""
    assert len(c.tool_results_for("search")[0]["results"]) == 2


def test_script_and_tool_results_must_agree(tmp_path):
    # Mutation: remove _check_script_matches_results.
    data = sample()
    data["research"]["tool_results"].pop()
    with pytest.raises(CassetteError, match="2 search call.*holds 1"):
        load_cassette(write(tmp_path, data))
    data = sample()
    data["research"]["tool_results"][0]["query"] = "a different query"
    with pytest.raises(CassetteError, match="recorded result is for 'a different query'"):
        load_cassette(write(tmp_path, data))


def test_text_only_on_example_hosts(tmp_path):
    # Mutation: make is_example_url return True, or drop either check.
    data = sample()
    data["page_texts"] = {"https://www.sundancespas.com/manual": "invented text"}
    with pytest.raises(CassetteError, match="example host"):
        load_cassette(write(tmp_path, data))
    data = sample()
    data["research"]["tool_results"][0]["results"][0]["url"] = "https://www.trane.com/x"
    with pytest.raises(CassetteError, match="real URL"):
        load_cassette(write(tmp_path, data))


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------


def test_copier_refuses_non_allowlisted_fields():
    # Mutations: bypass the allowlist check in take(), or drop the refusal of
    # dict and list values (a whole object would then be copied).
    cp = bc.AllowlistCopier(lookup(), "synthetic lookup")
    with pytest.raises(bc.NotAllowlisted, match="prior_notes is not on the copy allowlist"):
        cp.take("$.input.prior_notes", "input.notes")
    with pytest.raises(bc.NotAllowlisted, match="share_path"):
        cp.take("$.share_path", "x")
    with pytest.raises(bc.NotAllowlisted, match="not on the copy allowlist"):
        cp.take("$.input.identity", "input.identity")
    with pytest.raises(bc.NotAllowlisted, match="only scalars are copied"):
        cp.take("$.brief.warranty", "draft.warranty")
    with pytest.raises(bc.NotAllowlisted, match="may not be read"):
        cp.sha256_of_base64("$.input.prior_notes")
    assert cp.copies == []
    assert cp.take("$.input.symptom", "input.symptom") == "synthetic symptom: no heat"
    assert cp.copies == [{"dest": "input.symptom", "source": "synthetic lookup $.input.symptom",
                          "value": "synthetic symptom: no heat"}]


def test_index_remap_maps_by_url_and_a_shift_lands_on_a_decoy():
    # Mutations: remap_index off by one (either way), remap by position
    # instead of URL, or cited_positions placing cited results next to each
    # other (the neighbor is then a real source, not a decoy).
    cassette, _ = build_mini()
    c = cassette_from_dict(cassette)
    registry = c.registry_urls()
    v1 = lookup()["brief"]
    v1_url = [pd.strip_tracking(s["url"])[0] for s in v1["sources"]]
    draft = c.responses_for("synthesize")[0]["structured"]
    pairs = [(draft["try_first"][i], v1["try_first"][i]) for i in range(2)]
    pairs += [(draft["candidates"][i], v1["candidates"][i]) for i in range(2)]
    pairs += [(draft["warranty"]["cautions"][0], v1["warranty"]["cautions"][0]), (draft["warranty_caution"], v1["warranty_caution"])]
    for new, old in pairs:
        i = new["source_index"]
        assert registry[i] == v1_url[old["source_index"]]
        for shifted in (i - 1, i + 1):
            assert registry[shifted] != registry[i]
            assert registry[shifted].startswith(tuple(f"https://{h}/" for h in bc.DECOY_HOSTS))
    assert draft["warranty"]["cautions"][1]["source_index"] is None
    # Not a fixed offset: the cited order is reversed in the registry.
    assert [registry.index(u) for u in v1_url] != sorted(registry.index(u) for u in v1_url)
    assert cassette["expect"]["cited_urls"]["try_first"] == [v1_url[0], v1_url[1]]


def test_builder_strips_tracking_and_leaves_uncopied_fields_behind():
    # Mutations: drop strip_tracking from _v1_sources; copy sources_found or
    # prior_notes; log a copy without writing it (or the reverse).
    cassette, log = build_mini()
    text = json.dumps(cassette)
    assert "utm_source" not in text and "https://maker.example/manual?page=4" in text
    assert "never be copied" not in text and "Uncited synthetic result" not in text
    assert "never-copied" not in text
    assert {e["dest"] for e in log["copies"]} >= {"input.symptom", "input.identity.model"}
    for entry in log["copies"]:
        assert pd.get_path(cassette, entry["dest"]) == entry["value"], entry["dest"]


def test_built_case_replays_through_create_agent_and_draft_validates(tmp_path):
    # Mutations: script and tool results out of step (ReplayExhausted), a
    # final message missing (the agent keeps calling), or a draft that fails
    # BriefDraft (the parsing error is reported), or queries equal to the
    # v1 recorded ones (the builder refuses).
    from langchain.agents import create_agent
    from langchain_core.messages import HumanMessage

    from agent.ledger import Ledger
    from agent.replay.replay_model import ReplayChatModel
    from agent.replay.tool_stubs import make_stub_tools
    from agent.schemas import BriefDraft
    from agent.state import RunContext

    cassette, _ = build_mini()
    c = cassette_from_dict(cassette)
    Ledger(tmp_path / "l", create=True)
    ctx = RunContext(run_id="t", mode="replay", ledger_path=tmp_path / "l", registry_path=tmp_path / "r",
                     graph_path=tmp_path / "g", pages_dir=tmp_path, cassette=c)
    model_id = config.MODEL_FOR["replay"]["research"]
    research = ReplayChatModel(responses=c.responses_for("research"), model=model_id, max_tokens=1)
    tools = make_stub_tools(c, ctx)
    out = create_agent(research, tools).invoke({"messages": [HumanMessage("go")]})
    assert research.cursor == len(c.responses_for("research")) == 6
    assert tools[0].calls == 5 and out["messages"][-1].content.startswith("Research finished")
    assert [a["query"] for a in ctx.collector] == list(MINI_SPEC.queries)
    synth = ReplayChatModel(responses=c.responses_for("synthesize"), model=model_id, max_tokens=1)
    result = synth.with_structured_output(BriefDraft, include_raw=True).invoke([HumanMessage("write")])
    assert result["parsing_error"] is None and isinstance(result["parsed"], BriefDraft)
    assert bc.pass_cost_usd(cassette) <= config.REPLAY_RUN_CAP_USD


def test_builder_refuses_queries_that_repeat_v1_searched():
    # Mutation: remove the clash check in build_case.
    data = lookup()
    data["brief"]["no_reliable_answer"] = {"searched": ["Synthetic query ONE"], "found": [], "why_insufficient": "x"}
    with pytest.raises(ValueError, match="repeat v1's searched strings"):
        bc.build_case(MINI_SPEC, {"synthetic0001": data})


def test_research_usage_matches_section_9_typical_pass():
    # Mutation: change the split (base, per search or output tokens), so the
    # research pass no longer totals 44,250 input and 1,500 output tokens.
    cassette, _ = build_mini()
    usages = [r["message"]["usage"] for r in cassette["research"]["script"]]
    assert sum(u["input_tokens"] for u in usages) == 44_250
    assert sum(u["output_tokens"] for u in usages) == 1_500
    assert cassette["synthesize"][0]["usage"]["input_tokens"] == 13_000


SYNTHETIC_GUARDRAILS = """
const okSources = [
  { title: 'Synthetic', url: "https://maker.example/m", tier: "manufacturer" },
];
check("synthetic accept", () => {
  const brief = {
    status: "ok", // a comment
    sources: okSources, candidates: [], /* block comment */ note: 'it\\'s "quoted"',
  };
  validateBrief(brief);
});
check("synthetic reject", () => {
  expectThrow(
    () => validateBrief({ status: "ok", sources: [], n: 2 }),
    /empty bibliography/i,
    "message"
  );
});
check("synthetic parse", () => {
  const brief = parseBriefJson('Prose.\\n```json\\n{"status":"ok","sources":[]}\\n```');
});
check("synthetic prose", () => {
  expectThrow(() => parseBriefJson("no object here"), /no JSON/i, "m");
});
"""


def test_sc1b_extractor_parses_literals_without_evaluating():
    # Mutations: the decision taken from the wrong branch (expectThrow not
    # read), comment skipping removed, symbol lookup removed, or the
    # prose only parse test kept as an empty payload.
    doc, log = bc.extract_sc1b(SYNTHETIC_GUARDRAILS, "synthetic.mjs")
    got = {p["id"]: p for p in doc["payloads"]}
    assert list(got) == ["v1_test_01", "v1_test_02", "v1_test_03_parsed"]
    assert got["v1_test_01"]["payload"] == {
        "status": "ok", "candidates": [], "note": "it's \"quoted\"",
        "sources": [{"title": "Synthetic", "url": "https://maker.example/m", "tier": "manufacturer"}],
    }
    assert got["v1_test_01"]["v1_decision"] == "accept"
    assert got["v1_test_02"]["v1_decision"] == "reject"
    assert got["v1_test_02"]["v1_error_matcher"] == "/empty bibliography/i"
    assert got["v1_test_02"]["payload"] == {"status": "ok", "sources": [], "n": 2}
    assert got["v1_test_03_parsed"]["payload"] == {"status": "ok", "sources": []}
    assert all(p["provenance"] == "synthetic.mjs" for p in doc["payloads"])
    assert any(c["dest"] == "payloads[0].payload.note" for c in log["copies"])


# ---------------------------------------------------------------------------
# Privacy diff
# ---------------------------------------------------------------------------


def kinds(text: str, denylist=()) -> set[str]:
    return {k for k, _ in pd.scan_string(text, list(denylist))}


def test_scanner_flags_planted_personal_data():
    # Mutation: remove any one pattern (email, phone, the bare phone, street,
    # the any case street, 5 digit or the denylist loop), or stop scanning keys
    # and integers; its planted sample then comes back clean.
    data = json.loads((SAMPLES / "privacy_sample.json").read_text(encoding="utf-8"))
    deny = ["synthetic owner name"]
    for kind, text in data["planted"]:
        assert kind in kinds(text, deny), text
    for text in data["clean"]:
        assert kinds(text, deny) == set(), text
    for planted in data["planted_values"]:
        hits = pd.scan_keys_and_numbers(planted["value"], deny)
        assert {"path": planted["path"], "kind": planted["kind"]} in [
            {"path": h["path"], "kind": h["kind"]} for h in hits], planted
    for value in data["clean_values"]:  # token counts under usage are not addresses
        assert pd.scan_keys_and_numbers(value, deny) == [], value


def test_scanner_flags_key_patterns():
    # Mutations: drop a key prefix, the header name pattern, the entropy
    # check, or the query value candidates; or check every URL path segment
    # (the readable PDF name in the clean sample then trips the entropy check).
    tail = "Q7vK2mX9pL4s" + "T8wR3nB6yH1cF5"
    samples = {
        "Anthropic key prefix": "key " + "sk" + "-ant-" + "api03-" + tail,
        "LangSmith key prefix": "lsv2" + "_pt_" + tail,
        "Tavily key prefix": "tvly" + "-dev-" + tail,
        "auth header name": "Author" + "ization: Bearer something",
        "high entropy token": "token " + tail + "a1B2c3D4e5F6g7",
    }
    for kind, text in samples.items():
        assert kind in kinds(text), kind
    assert "auth header name" in kinds("X-Api" + "-Key header")
    assert "high entropy token" in kinds("https://example.com/p?k=" + tail + "Zz9Yy8Xx7")
    # A readable path made of hyphenated words scores above the entropy
    # threshold but is not a key. Split so the repo key scanner never sees it.
    pdf = ("https://example.com/" + "wp-content/uploads/2019/02/" + "AR-OWNERS-MANUAL-"
           + "DOMESTIC-ENGLISH-" + "HU2-378100-2-REV-S-.pdf")
    assert kinds(pdf) == set()


def test_five_digit_numbers_count_only_outside_urls():
    # Mutation: scan the whole string instead of the text with URLs removed.
    assert "5 digit number" not in kinds("see https://example.com/product/56105 now")
    assert "5 digit number" in kinds("ZIP 56105, see https://example.com/")
    assert "5 digit number" not in kinds("647490 tokens")
    assert "5 digit number" in kinds("64749 tokens")


def test_tracking_parameters_are_stripped():
    # Mutation: remove a name from TRACKING_EXACT or the utm_ prefix; or strip
    # every parameter (page=80 is content and must stay).
    url = ("https://example.com/manual.html?page=80&utm_source=x&utm_campaign=y"
           "&gclid=1&fbclid=2&sessionid=3&PHPSESSID=4")
    clean, removed = pd.strip_tracking(url)
    assert clean == "https://example.com/manual.html?page=80"
    assert set(removed) == {"utm_source", "utm_campaign", "gclid", "fbclid", "sessionid", "PHPSESSID"}
    assert pd.strip_tracking("https://example.com/a;jsessionid=abc?x=1") == ("https://example.com/a?x=1", ["jsessionid"])
    assert pd.strip_tracking("https://example.com/a?page=2") == ("https://example.com/a?page=2", [])


def jpeg(with_app1: bool) -> bytes:
    app0 = b"\xff\xe0" + (16).to_bytes(2, "big") + b"JFIF\x00" + b"\x01\x01\x00\x00\x01\x00\x01\x00\x00"
    app1 = b"\xff\xe1" + (14).to_bytes(2, "big") + b"Exif\x00\x00" + b"\x00" * 6
    dqt = b"\xff\xdb" + (4).to_bytes(2, "big") + b"\x00\x00"
    scan = b"\xff\xda" + (4).to_bytes(2, "big") + b"\x00\x00" + b"\xff\xe1\x00" + b"\xff\xd9"
    return b"\xff\xd8" + app0 + (app1 if with_app1 else b"") + dqt + scan


def test_exif_segment_detected_and_referenced_image_rejected(tmp_path):
    # Mutations: jpeg_has_app1 always False, or it checks the wrong marker,
    # or it keeps reading past start of scan (the clean image's scan data
    # holds APP1 bytes); diff_file skipping the plate check.
    assert pd.jpeg_has_app1(jpeg(True)) is True
    assert pd.jpeg_has_app1(jpeg(False)) is False
    demo = tmp_path / "demo"
    demo.mkdir()
    (demo / "plate.jpg").write_bytes(jpeg(True))
    import hashlib

    data = sample()
    data["input"]["plate_sha256"] = hashlib.sha256(jpeg(True)).hexdigest()
    staging = tmp_path / "staging"
    (staging / "cassettes").mkdir(parents=True)
    (staging / "copy_logs").mkdir()
    write(staging / "cassettes", data, "s.json")
    (staging / "copy_logs" / "s.copies.json").write_text(json.dumps({"copies": [], "hashed": []}))
    results, report = pd.run(staging, demo, status=lambda: "(stub working tree)")
    assert {"path": "input.plate_sha256", "kind": "EXIF or APP1 segment", "match": "plate.jpg"} in results[0].hits
    assert "BLOCKED" in report


def stub_status() -> str:
    return "(stub working tree)"


def stage_mini(tmp_path: Path, denylist: str | None = "synthetic owner name\n") -> tuple[Path, dict, dict]:
    staging = tmp_path / "staging"
    cassette, log = build_mini()
    (staging / "cassettes").mkdir(parents=True)
    (staging / "copy_logs").mkdir()
    (staging / "cassettes" / "mini.json").write_text(json.dumps(cassette))
    (staging / "copy_logs" / "mini.copies.json").write_text(json.dumps(log))
    if denylist is not None:
        (staging / pd.DENYLIST_FILE).write_text(denylist)
    return staging, cassette, log


def test_privacy_diff_lists_copies_blocks_on_hits_and_stays_in_staging(tmp_path):
    # Mutations: the report omits copied fields or their source path; the
    # result line ignores hits; the copy log check is dropped; run() accepts
    # a folder inside agent/tests; diff_file stops scanning keys and integers;
    # run() calls git_status itself instead of the status it was given.
    staging, cassette, _ = stage_mini(tmp_path)
    results, report = pd.run(staging, tmp_path, status=stub_status)
    assert results[0].hits == [] and "Result: CLEAN" in report
    assert "`input.symptom` from v1 lookup synthetic0001 $.input.symptom: synthetic symptom: no heat" in report
    assert "(stub working tree)" in report

    tampered = copy.deepcopy(cassette)
    tampered["input"]["symptom"] = "email owner.synthetic@example.com"
    tampered["expect"]["zip"] = 90210
    (staging / "cassettes" / "mini.json").write_text(json.dumps(tampered))
    results, report = pd.run(staging, tmp_path, status=stub_status)
    got = {(h["path"], h["kind"]) for h in results[0].hits}
    assert ("input.symptom", "email") in got and ("input.symptom", "copy log mismatch") in got
    assert ("expect.zip", "number of 5 or more digits") in got
    assert "Result: BLOCKED" in report

    with pytest.raises(ValueError, match="never inside agent/tests"):
        pd.run(config.REPO_ROOT / "agent" / "tests" / "cassettes", tmp_path, status=stub_status)


def test_empty_denylist_is_incomplete_never_clean(tmp_path):
    # Mutation: have verdict() ignore the denylist size, so a scan that never
    # looked for a private name reports CLEAN.
    staging, _, _ = stage_mini(tmp_path, denylist=None)
    results, report = pd.run(staging, tmp_path, status=stub_status)
    assert results[0].hits == []
    assert "Result: INCOMPLETE: denylist empty, private names not checked" in report
    assert "Result: CLEAN" not in report
    # The file it creates is a commented template that still counts as empty.
    template = (staging / pd.DENYLIST_FILE).read_text(encoding="utf-8")
    assert template.startswith("#") and pd.load_denylist(staging / pd.DENYLIST_FILE) == []
    assert pd.verdict(0, 1) == "CLEAN" and pd.verdict(1, 0) == "BLOCKED"


def test_loader_accepts_each_failure_shape_and_nothing_else(tmp_path):
    # Mutations: drop the raise kind check (an unknown raise loads), or the
    # one shape rule (an entry holding two shapes loads).
    for shape in ({"string": "No results."}, {"raise": "timeout"}, {"error": "Error 432: plan limit"}):
        data = sample()
        data["research"]["tool_results"][2] = {"tool": "search", "query": "sx-100 e9 reset", **shape}
        c = load_cassette(write(tmp_path, data))
        assert c.tool_results_for("search")[1] == {"query": "sx-100 e9 reset", **shape}
    for bad, message in (({"raise": "crash"}, "must be one of timeout"),
                         ({"error": "x", "string": "y"}, "more than one failure shape")):
        data = sample()
        data["research"]["tool_results"][2] = {"tool": "search", **bad}
        with pytest.raises(CassetteError, match=message):
            load_cassette(write(tmp_path, data))


def fake_extract(photo: bytes, model: str) -> dict:
    import base64

    ident = {"manufacturer": "Synthetic Spas", "model": model, "serial": "", "manufacture_date": ""}
    return {
        "id": "privateid" + model.replace(" ", "").lower(),
        "input": {"photo_base64": base64.b64encode(photo).decode()},
        "extracted_identity": {**ident, "confidence": {k: "high" for k in ident}},
        "usage": {"input_tokens": 1500, "output_tokens": 80},
    }


def test_extract_lookup_found_by_plate_hash_and_labeled_by_public_file(tmp_path):
    # Mutations: take the first extract lookup whatever its photo (the wrong
    # extraction, or two matches); label provenance or copies with the lookup
    # id, which names a private file.
    demo = tmp_path / "demo"
    demo.mkdir()
    (demo / "plate.jpg").write_bytes(jpeg(False))
    other = fake_extract(jpeg(False) + b"x", "Other 1")
    wanted = fake_extract(jpeg(False), "Wanted 2")
    lookups = {other["id"]: other, wanted["id"]: wanted}
    spec = bc.CaseSpec("plate", None, "plate.jpg", (), "synthetic", symptom="not heating")
    cassette, log = bc.build_case(spec, lookups, demo)
    assert cassette["read_plate"]["extraction"]["model"] == "Wanted 2"
    assert cassette["provenance"]["derived_from"] == "v1 extract lookup for demo-assets/plate.jpg"
    text = json.dumps(cassette) + json.dumps(log)
    assert "privateid" not in text
    assert all(c["source"].startswith("v1 extract lookup for demo-assets/plate.jpg ") for c in log["copies"])
    assert cassette_from_dict(cassette).derived_from_v1

    twin = fake_extract(jpeg(False), "Twin 3")
    with pytest.raises(ValueError, match="found 2"):
        bc.build_case(spec, {**lookups, twin["id"]: twin}, demo)


def test_five_digit_rule_ignores_digits_inside_identifiers() -> None:
    """Mutation privacy_five_digit_inside_words: the ZIP rule matches digits inside
    a hex run ID, so a live recording is blocked at random (about one run ID in
    three holds five digits in a row)."""
    from agent.replay import privacy_diff

    def hits(text: str) -> list[str]:
        return [m.group() for m in privacy_diff.FIVE_DIGIT_RE.finditer(text)]

    assert hits("case t-3be90749debc4812 run a69879b") == []
    assert hits("recorded live 2026-09-18 run t-0fa69879c1d2") == []
    assert hits("Springfield IL 62704") == ["62704"]
    assert hits("zip 62704-1234.") == ["62704-1234"]


def test_blocked_calls_account_for_scripted_calls_without_results() -> None:
    """Mutation cassettes_blocked_calls_ignored: a recording whose last search the
    run's cap blocked (scripted, never sent) cannot load, which is what the
    Phase 5 live run hit."""
    base = json.loads((SAMPLES / "synthetic_cassette.json").read_text(encoding="utf-8"))
    extra = {"message": {"content": "", "tool_calls": [{"name": "search", "args": {"query": "one more"}}],
                         "usage": base["research"]["script"][0]["message"]["usage"]}}
    data = copy.deepcopy(base)
    data["research"]["script"].insert(-1, extra)  # a third search, scripted but blocked
    with pytest.raises(CassetteError, match="3 search call"):
        cassette_from_dict(data)
    data["research"]["blocked_calls"] = {"search": 1}
    loaded = cassette_from_dict(data).tool_results_for("search")
    assert [r["query"] for r in loaded] == \
        [r["query"] for r in base["research"]["tool_results"] if r["tool"] == "search"]
    for wrong in ({"search": 2}, {"search": 0}, {"fetch": 1}):
        data["research"]["blocked_calls"] = wrong
        with pytest.raises(CassetteError):
            cassette_from_dict(data)
    data["research"]["blocked_calls"] = {"browse": 1}
    with pytest.raises(CassetteError, match="blocked_calls"):
        cassette_from_dict(data)
