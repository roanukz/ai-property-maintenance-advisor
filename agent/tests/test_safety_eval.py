"""SC12a and SC12b, offline: the labeled set, labels, statistics, lock, held out rule and CLI.

Every test builds its own synthetic tree under tmp_path; nothing reads
data/, and nothing here labels real items or calls Jev. Replies are written
as the live harness would record them. The live halves (agent/live/sc12a.py
and sc12b.py) are reached only through agent.cli, since test_offline_guard.py
forbids a test that imports agent.live. Each test names the mutation that
turns it red.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import pytest

from hypothesis import given, settings
from hypothesis import strategies as st

from agent import cli, config
from agent.rules.step_text import step_key
from agent.safety_eval import harness, labels, pools, sc12b, stats

SAFETY_EVAL_DIR = config.REPO_ROOT / "agent" / "safety_eval"


# ---------------------------------------------------------------------------
# Wilson interval
# ---------------------------------------------------------------------------


def test_wilson_interval_matches_published_values() -> None:
    """Newcombe RG. Two-sided confidence intervals for the single proportion:
    comparison of seven methods. Statistics in Medicine 1998;17:857-872,
    Table II, method 3 (the Wilson score interval without continuity
    correction), four decimal places.

    Mutations: stats_wilson_drops_center_shift (the z squared over 2n term
    left out of the center), stats_wilson_z_90 (the 90% quantile)."""
    published = {(81, 263): (0.2553, 0.3662), (15, 148): (0.0624, 0.1605), (0, 20): (0, 0.1611),
                 (1, 29): (0.0061, 0.1718), (29, 29): (0.8830, 1)}
    for (k, n), (lo, hi) in published.items():
        got = stats.wilson(k, n)
        assert got is not None
        assert round(got[0], 4) == pytest.approx(lo) and round(got[1], 4) == pytest.approx(hi), (k, n, got)
    assert stats.wilson(0, 0) is None


# ---------------------------------------------------------------------------
# Pools on a synthetic tree
# ---------------------------------------------------------------------------


def _li(step: str, detail: str, *, safety: bool = False) -> str:
    cls = ' class="safety"' if safety else ""
    title = f"SAFETY: {step}" if safety else step
    return (f"<li{cls}>\n<strong>{title}</strong>\n<p>{detail} "
            f'<a class="ref" href="https://example.com/x">[1]</a></p>\n</li>')


def _brief(path: Path, unit: str, steps: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f'<html><head><title>Service brief: {unit}</title></head><body>'
                    f'<p class="unit">{unit}</p><ol class="steps">{"".join(steps)}</ol>'
                    '<ol><li><strong>Not a step</strong></li></ol></body></html>', encoding="utf-8")


def _page(data: Path, text: str) -> str:
    sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
    (data / "pages").mkdir(parents=True, exist_ok=True)
    (data / "pages" / f"{sha}.txt").write_text(text, encoding="utf-8")
    return sha


def _log(path: Path, lines: list[tuple[str, str, list[tuple[str, str]]]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    out = []
    for at, query, results in lines:
        out.append(json.dumps({"at": at, "tool": "search", "query": query, "status": "ok",
                               "results": [{"url": u, "title": "t", "content": "c", "text_sha256": s}
                                           for u, s in results]}))
    path.write_text("\n".join(out) + "\n", encoding="utf-8")


LONG_KEPT = "Check " + "y" * 394  # 400 characters: kept
LONG_DROPPED = "Check " + "x" * 395  # 401 characters: dropped


def _tree(tmp_path: Path) -> tuple[pools.Roots, list[dict]]:
    repo, data, cassettes = tmp_path / "repo", tmp_path / "data", tmp_path / "cassettes"
    _brief(repo / "briefs" / "aaa111aaa111.html", "Trane XR16", [
        _li("Kill power before touching filters", "Turn the breaker off.", safety=True),
        _li("Check the water level", "Keep it at the fill line."),
    ])
    _brief(repo / "briefs" / "v2" / "t-1111.html", "Sundance Spas Optima 880", [
        _li("Check the water level", "Keep it at the fill line.", safety=True),
        _li("Remove the filter", "Rinse it."),
    ])
    _brief(data / "briefs" / "t-1111.html", "Sundance Spas Optima 880", [_li("Remove the filter", "Rinse it.")])
    _brief(data / "superseded" / "b1" / "briefs" / "t-2222.html", "Trane XR16", [
        _li("Reset the GFCI", "Press reset."),
        _li("Clean or replace the air filter", "With power off at the breaker, clean it."),
    ])
    cassettes.mkdir(parents=True)
    (cassettes / "hvac.json").write_text(json.dumps({
        "provenance": {"derived_from": "v1 lookup aaa111aaa111"},
        "input": {"identity": {"manufacturer": "Trane", "model": "XR16"}, "symptom": "AC not cooling"},
        "synthesize": [{"draft": {"try_first": [
            {"step": "Kill power before touching filters", "detail": "Turn the breaker off.", "safety_flag": False},
            {"step": "Replace thermostat batteries", "detail": "Use AA cells.", "safety_flag": False},
        ]}}],
    }), encoding="utf-8")
    maker = _page(data, "Turn off the breaker before service.\nThe unit is quiet. Check the filter monthly.\n"
                        "• Kill the circuit first.")
    listed = _page(data, "1. Drain the spa. Remove the filter\n")
    dealer_b = _page(data, f"Test the GFCI.\n{LONG_DROPPED}\n{LONG_KEPT}\n")
    dealer_a = _page(data, "Turn off the breaker before service.\nOpen the panel.")
    forum = _page(data, "Turn the heater on.")
    _page(data, "Check the snippet only.")  # a snippet file: no lookup names it as text_sha256
    _log(data / "lookups" / "t-9.jsonl", [
        ("2026-09-18T01:00:00", "Trane XR16 manual", [("https://shop.dealer.com/a", dealer_a),
                                                     ("https://www.trane.com/x", maker)]),
        ("2026-09-18T00:00:00", "Sundance 880 manual", [("https://www.reddit.com/r/x", forum),
                                                       ("https://cdn.example.net/manual.pdf", listed)]),
    ])
    _log(data / "superseded" / "s1" / "lookups" / "t-8.jsonl",
         [("2026-09-17T00:00:00", "Trane XR16 fan", [("https://shop.example.com/b", dealer_b)])])
    return pools.Roots(repo, data, cassettes), [{"url": "https://cdn.example.net/manual.pdf"}]


SUNDANCE = {"manufacturer": "Sundance Spas", "model": "Optima 880"}
TRANE = {"manufacturer": "Trane", "model": "XR16"}
FLO = "question:sundance-spas:optima880:code:FLO"


def _write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def _family_tree(tmp_path: Path) -> tuple[pools.Roots, list[dict]]:
    """_tree plus recorded inputs, two more runs and a second listed manual in the first one's family.

    t-1111 (a recording) and t-3333 (an eval record, other symptom words, the same code) answer
    FLO on the Optima 880; the v1 lookup aaa111aaa111 (its cassette) and t-2222 (a superseded
    build's recording) answer "AC not cooling" on the XR16; t-4444 has no recorded input."""
    roots, _listed = _tree(tmp_path)
    data = roots.data_dir
    _write(data / "recordings" / "live_t_1111.json", {
        "input": {"identity": None, "symptom": "panel shows FLO"},
        "resume": {"identity": {**SUNDANCE, "serial": "1"}, "observed_code": "FLO"}})
    _write(data / "recordings" / "live_t_2222.privacy.json", {"input": {"identity": SUNDANCE, "symptom": "x"}})
    _write(data / "superseded" / "b1" / "recordings" / "live_t_2222.json", {
        "input": {"identity": TRANE, "symptom": "AC not cooling."}, "resume": {}})
    _write(data / "eval" / "sc7b-t-3333.json", {
        "run_id": "t-3333", "input": {"identity": None, "symptom": "FLO on the panel", "photo": None},
        "answers": [{"identity": SUNDANCE, "observed_code": "flo"}]})
    _brief(data / "briefs" / "t-3333.html", "Sundance Spas Optima 880", [_li("Unplug the spa", "At the plug.")])
    _brief(data / "briefs" / "t-4444.html", "Trane XR16", [_li("Look for ice on the lines", "Both lines.")])
    second = _page(data, "Replace the ozonator.\nRefill the spa.")
    _log(data / "lookups" / "t-10.jsonl", [
        ("2026-09-18T02:00:00", "Sundance 880 manual 2019", [("https://cdn.example.net/manual-2019.pdf", second)])])
    listed = [{"url": "https://cdn.example.net/manual.pdf", "family": "sundance-manual"},
              {"url": "https://cdn.example.net/manual-2019.pdf/", "family": "sundance-manual"}]
    return roots, listed


def test_d_pages_give_at_most_the_cap(tmp_path: Path) -> None:
    """A page with 30 qualifying sentences (between 30 that do not qualify) gives its first 25,
    then D moves to the next page; the stopping rule's extension continues the same capped walk.

    Mutations: pools_doc_cap_removed, pools_doc_cap_off_by_one (26 per page),
    pools_doc_cap_counts_every_sentence (the cap counts sentences that do not qualify)."""
    assert config.SC12A_D_DOC_CAP == 25
    roots, listed = _tree(tmp_path)
    big = _page(roots.data_dir, "\n".join(f"Check part {n}. The part is fine." for n in range(1, 31)))
    _log(roots.data_dir / "lookups" / "t-0.jsonl",
         [("2026-09-16T00:00:00", "Trane XR16 parts", [("https://www.trane.com/big", big)])])
    result = pools.build_items(roots, listed, limit=100)
    d_steps = [i["step"] for i in result.items if i["pool"] == "D"]
    assert d_steps[:26] == [f"Check part {n}." for n in range(1, 26)] + ["Turn off the breaker before service."]
    assert sum(1 for i in result.items if i["sources"] == ["https://www.trane.com/big"]) == 25
    g_count = sum(1 for i in result.items if i["pool"] == "G")
    short = pools.build_items(roots, listed, limit=g_count + 20)
    grown = pools.extend_items(short.items, roots, listed, by=6)
    assert [i["step"] for i in grown.items[g_count + 20:]] == [
        *(f"Check part {n}." for n in range(21, 26)), "Turn off the breaker before service."]


def test_families_group_listed_manuals_and_runs_of_one_question(tmp_path: Path) -> None:
    """Listed manuals that share a family land in one half; so do runs and v1 lookups that answer
    one question on one unit, read from recordings (this build's or a superseded one's), eval
    records and v1 cassettes; a run with no recorded input is its own family.

    Mutations: pools_listed_family_ignored (every D page its own family),
    pools_question_ignores_code (the question is the symptom even when a code was given),
    pools_family_skips_eval_records, pools_family_skips_superseded_recordings."""
    roots, listed = _family_tree(tmp_path)
    result = pools.build_items(roots, listed, limit=100)
    family = {(i["pool"], i["step"]): i["family"] for i in result.items}
    for step in ("Drain the spa.", "Remove the filter", "Replace the ozonator.", "Refill the spa."):
        assert family[("D", step)] == "sundance-manual", step
    assert family[("D", "Open the panel.")] == "url:https://shop.dealer.com/a"
    family = {step: name for (pool, step), name in family.items() if pool == "G"}
    assert family["Remove the filter"] == family["Unplug the spa"] == FLO  # t-1111 and t-3333
    trane = "question:trane:xr16:symptom:ac not cooling"
    assert family["Replace thermostat batteries"] == family["Reset the GFCI"] == trane  # v1 cassette and t-2222
    assert family["Look for ice on the lines"] == "t-4444"
    by_family: dict[str, set[str]] = {}
    for item in result.items:
        by_family.setdefault(item["family"], set()).add(item["half"])
        assert item["family_sha256"] == pools.family_sha256(item["family"])
    assert all(len(halves) == 1 for halves in by_family.values()), by_family
    assert {i["document"] for i in result.items if i["family"] == "sundance-manual"} == {
        "url:https://cdn.example.net/manual.pdf", "url:https://cdn.example.net/manual-2019.pdf"}
    # Balance on the tree: the halves differ by at most the largest non forced family.
    sizes = {name: f["items"] for name, f in result.families.items()}
    tune = sum(n for name, n in sizes.items() if result.families[name]["half"] == pools.TUNE)
    assert abs(tune - (sum(sizes.values()) - tune)) <= max(sizes.values())


def test_pools_keep_order_dedupe_and_groups(tmp_path: Path) -> None:
    """Mutations: pools_dealer_before_listed (the D groups out of decision 53's order),
    pools_writer_flag_last_wins (an item's writer flag is its last occurrence's, not any),
    pools_sentence_limit_off_by_one (a 400 character sentence is dropped)."""
    roots, listed = _tree(tmp_path)
    result = pools.build_items(roots, listed, limit=100)
    steps = [(i["pool"], i["step"]) for i in result.items]
    assert steps == [
        ("G", "Kill power before touching filters"), ("G", "Check the water level"), ("G", "Remove the filter"),
        ("G", "Reset the GFCI"), ("G", "Clean or replace the air filter"), ("G", "Replace thermostat batteries"),
        ("D", "Turn off the breaker before service."), ("D", "Check the filter monthly."),
        ("D", "Drain the spa."), ("D", "Remove the filter"),
        ("D", "Test the GFCI."), ("D", LONG_KEPT), ("D", "Open the panel."),
    ]
    by_step = {i["step"]: i for i in result.items if i["pool"] == "G"}
    # A flag seen in any occurrence counts; the v1 "SAFETY: " prefix is gone.
    assert by_step["Kill power before touching filters"]["writer_flag"] is True
    assert by_step["Kill power before touching filters"]["occurrences"] == 2
    assert by_step["Check the water level"]["writer_flag"] is True
    assert by_step["Remove the filter"]["writer_flag"] is False
    # An item belongs to the first document it was found in; a cassette is its v1 lookup's document.
    assert by_step["Check the water level"]["document"] == "aaa111aaa111"
    assert by_step["Replace thermostat batteries"]["document"] == "aaa111aaa111"
    assert by_step["Remove the filter"]["document"] == "t-1111"
    assert by_step["Remove the filter"]["detail"] == "Rinse it."
    assert by_step["Kill power before touching filters"]["appliance"] == "air conditioner"
    d_items = [i for i in result.items if i["pool"] == "D"]
    assert [i["group"] for i in d_items] == ["maker", "maker", "listed", "listed", "dealer", "dealer", "dealer"]
    assert d_items[0]["document"] == "url:https://www.trane.com/x"
    assert d_items[0]["appliance"] == "air conditioner" and d_items[2]["appliance"] == "hot tub"
    assert result.exhausted is True
    # A worked example's step sends its document to the tune half.
    assert "t-2222" in result.forced_tune
    assert all(i["half"] == "tune" for i in result.items if i["document"] == "t-2222")

    capped = pools.build_items(roots, listed, limit=8)
    assert len(capped.items) == 8 and capped.exhausted is False
    extended = pools.extend_items(capped.items, roots, listed, by=3)
    assert [i["step"] for i in extended.items[8:]] == ["Drain the spa.", "Remove the filter", "Test the GFCI."]
    assert [i["id"] for i in extended.items[:8]] == [i["id"] for i in capped.items]
    assert extended.exhausted is False


FAMILY_SIZES = {"alpha": 3, "bravo": 1, "charlie": 1, "delta": 2, "echo": 1}


def test_split_is_stable_by_family(tmp_path: Path) -> None:
    """Families in the order of the sha256 of their keys (echo, delta, alpha, charlie, bravo), each
    to the half with fewer items so far, ties to tune; the same whatever order they arrive in.

    Mutations: pools_split_salt_changed (the family hash is salted, so the order moves),
    pools_split_ties_to_heldout, pools_split_in_arrival_order (families placed in the order given,
    not by hash)."""
    golden = {"echo": "tune", "delta": "heldout", "alpha": "tune", "charlie": "heldout", "bravo": "heldout"}
    assert pools.assign_halves(FAMILY_SIZES) == golden
    assert pools.assign_halves(dict(reversed(list(FAMILY_SIZES.items())))) == golden
    assert pools.family_sha256("echo") == hashlib.sha256(b"echo").hexdigest()
    # Building twice from the same tree gives the same families and halves.
    roots, listed = _family_tree(tmp_path)
    first, second = (pools.build_items(roots, listed, limit=100) for _ in range(2))
    assert [(i["id"], i["family"], i["half"]) for i in first.items] == [
        (i["id"], i["family"], i["half"]) for i in second.items]
    assert first.families == second.families


def test_forced_tune_families_go_to_tune_whole(tmp_path: Path) -> None:
    """A worked example's brief sends its whole family to tune, not just its own document: here
    t-2222 holds a worked example's step, and the v1 lookup aaa111aaa111 asked the same question.

    Mutations: pools_forced_tune_ignored (forced families are placed like any other),
    pools_forced_family_is_the_document (a forced brief's document, not its family, is forced)."""
    forced = pools.assign_halves(FAMILY_SIZES, forced_tune=["delta"])
    assert forced == {"echo": "heldout", "delta": "tune", "alpha": "heldout", "charlie": "tune", "bravo": "tune"}
    assert pools.assign_halves({"big": 10, "small": 1}, forced_tune=["big"]) == {"big": "tune", "small": "heldout"}
    roots, listed = _family_tree(tmp_path)
    result = pools.build_items(roots, listed, limit=100)
    trane = "question:trane:xr16:symptom:ac not cooling"
    assert "t-2222" in result.forced_tune and trane in result.forced_families
    assert result.families[trane] == {"half": "tune", "items": 5, "forced": True, "stratum": "air conditioner"}
    assert {i["document"] for i in result.items if i["family"] == trane} == {"aaa111aaa111", "t-2222"}
    assert all(i["half"] == "tune" for i in result.items if i["family"] == trane)
    # A forced brief with no item in the set still forces the family its recorded input gives.
    assert pools.forced_families_of([], ["t-3333"], pools.recorded_inputs(roots)) == [FLO]


@settings(max_examples=200, deadline=None)
@given(st.dictionaries(st.text(min_size=1, max_size=6), st.integers(min_value=1, max_value=40), max_size=30))
def test_halves_balance_within_the_largest_family(sizes: dict[str, int]) -> None:
    """With nothing forced, the halves differ by at most the largest family's size.

    Mutations: pools_split_to_larger_half, pools_split_counts_families (the balance counts
    families, not items)."""
    halves = pools.assign_halves(sizes)
    assert set(halves) == set(sizes)
    tune = sum(n for name, n in sizes.items() if halves[name] == pools.TUNE)
    held = sum(n for name, n in sizes.items() if halves[name] == pools.HELDOUT)
    assert abs(tune - held) <= max(sizes.values(), default=0)


STRATA = {"echo": "x", "delta": "y", "alpha": "x", "charlie": "y", "bravo": "x"}


def test_split_balances_each_appliance(tmp_path: Path) -> None:
    """Decision 59: each family goes to the half with fewer items of its stratum (its majority
    appliance) so far, then fewer items in all, ties to tune. On FAMILY_SIZES with two strata the
    split differs from the unstratified one; on the family tree each appliance is split.

    Mutations: pools_split_ignores_strata (assign_halves balances totals only),
    pools_finish_drops_strata (the builder passes no strata)."""
    assert pools.assign_halves(FAMILY_SIZES, strata=STRATA) == {
        "echo": "tune", "delta": "heldout", "alpha": "heldout", "charlie": "tune", "bravo": "tune"}
    tied = [{"family": "f", "appliance": "hot tub"}, {"family": "f", "appliance": "air conditioner"},
            {"family": "g", "appliance": "hot tub"}]
    assert pools.family_strata(tied) == {"f": "air conditioner", "g": "hot tub"}  # a tie goes to the first name
    roots, listed = _family_tree(tmp_path)
    result = pools.build_items(roots, listed, limit=100)
    assert {name: (f["stratum"], f["half"]) for name, f in result.families.items()} == {
        FLO: ("hot tub", "heldout"),
        "url:https://www.trane.com/x": ("air conditioner", "heldout"),
        "url:https://shop.example.com/b": ("air conditioner", "heldout"),
        "url:https://shop.dealer.com/a": ("air conditioner", "heldout"),
        "question:trane:xr16:symptom:ac not cooling": ("air conditioner", "tune"),
        "t-4444": ("air conditioner", "tune"),
        "sundance-manual": ("hot tub", "tune"),
    }


@settings(max_examples=200, deadline=None)
@given(st.dictionaries(st.text(min_size=1, max_size=6),
                       st.tuples(st.integers(min_value=1, max_value=40), st.sampled_from(["a", "b", "c"])),
                       max_size=30))
def test_halves_balance_within_each_stratum(families: dict[str, tuple[int, str]]) -> None:
    """With nothing forced, within each stratum the halves differ by at most that stratum's largest family.

    Mutation: pools_split_ignores_strata."""
    sizes = {name: size for name, (size, _s) in families.items()}
    strata = {name: stratum for name, (_n, stratum) in families.items()}
    halves = pools.assign_halves(sizes, strata=strata)
    assert set(halves) == set(sizes)
    for stratum in set(strata.values()):
        members = [name for name in sizes if strata[name] == stratum]
        tune = sum(sizes[n] for n in members if halves[n] == pools.TUNE)
        held = sum(sizes[n] for n in members if halves[n] == pools.HELDOUT)
        assert abs(tune - held) <= max(sizes[n] for n in members), (stratum, tune, held)


def test_extension_keeps_every_family_half(tmp_path: Path) -> None:
    """The stopping rule's extension keeps each existing family's stored half, even one the rule
    would place otherwise (a forced family included), while the walk continues inside a family.

    Mutation: pools_extend_ignores_fixed_halves."""
    roots, listed = _family_tree(tmp_path)
    g_count = sum(1 for i in pools.build_items(roots, listed, limit=100).items if i["pool"] == "G")
    short = pools.build_items(roots, listed, limit=g_count + 3)  # stops after "Drain the spa."
    assert short.items[-1]["step"] == "Drain the spa." and short.items[-1]["family"] == "sundance-manual"
    flipped = [dict(i, half=pools.HELDOUT if i["half"] == pools.TUNE else pools.TUNE) for i in short.items]
    stored = {i["family"]: i["half"] for i in flipped}
    grown = pools.extend_items(flipped, roots, listed, by=20, forced_families=short.forced_families)
    assert grown.items[len(flipped)]["family"] == "sundance-manual"  # the walk resumes inside a family
    for item in grown.items:
        if item["family"] in stored:
            assert item["half"] == stored[item["family"]], item["family"]
    for name, half in stored.items():
        assert grown.families[name]["half"] == half


def test_extension_forces_a_new_worked_example_document(tmp_path: Path) -> None:
    """A page the extension reaches that holds a worked example's step sends its family to tune.

    Mutation: pools_extend_skips_worked_examples."""
    roots, listed = _tree(tmp_path)
    late = _page(roots.data_dir, "Clean or replace the air filter.\nOpen the cabinet door.")
    _log(roots.data_dir / "lookups" / "t-11.jsonl",
         [("2026-09-19T00:00:00", "Trane XR16 filter", [("https://late.example.com/filter", late)])])
    full = pools.build_items(roots, listed, limit=100)
    short = pools.build_items(roots, listed, limit=len(full.items) - 2)
    late_doc = "url:https://late.example.com/filter"
    assert late_doc not in short.forced_tune
    grown = pools.extend_items(short.items, roots, listed, by=10, forced_tune=short.forced_tune,
                               forced_families=short.forced_families)
    assert late_doc in grown.forced_tune and late_doc in grown.forced_families
    assert grown.families[late_doc]["forced"] is True and grown.families[late_doc]["half"] == pools.TUNE


def test_d_cap_counts_a_url_across_its_stored_versions(tmp_path: Path) -> None:
    """A URL stored in two versions (15 qualifying sentences each) gives 25 items in all, not 30.

    Mutation: pools_doc_cap_per_version (the cap restarts at each stored version)."""
    roots, listed = _tree(tmp_path)
    first = _page(roots.data_dir, "\n".join(f"Check part {n}." for n in range(1, 16)))
    second = _page(roots.data_dir, "\n".join(f"Check valve {n}." for n in range(1, 16)))
    _log(roots.data_dir / "lookups" / "t-12.jsonl", [
        ("2026-09-16T00:00:00", "Trane XR16 parts", [("https://www.trane.com/two", first)]),
        ("2026-09-16T01:00:00", "Trane XR16 parts", [("https://www.trane.com/two/", second)])])
    result = pools.build_items(roots, listed, limit=200)
    from_url = [i["step"] for i in result.items if i["document"] == "url:https://www.trane.com/two"]
    assert len(from_url) == 25
    assert from_url == [f"Check part {n}." for n in range(1, 16)] + [f"Check valve {n}." for n in range(1, 11)]


def test_a_revision_of_a_listed_manual_joins_its_family(tmp_path: Path, sc12a_dirs: Path) -> None:
    """maker_documents.json's also_in_family gives a page another revision's family (decision 59):
    the maker site's revision of the listed Trane manual lands in the listed manual's half, and
    keeps its group.

    Mutations: pools_also_in_family_ignored (d_candidates reads only the listed families),
    pools_document_list_drops_also_in_family, harness_build_drops_also_in_family,
    maker_documents_also_in_family_other_family."""
    roots, listed = _family_tree(tmp_path / "tree")
    also = [{"url": "https://www.trane.com/x/", "family": "sundance-manual"}]
    result = pools.build_items(roots, listed, limit=100, also_in_family=also)
    moved = [i for i in result.items if i["document"] == "url:https://www.trane.com/x"]
    assert moved and all(i["family"] == "sundance-manual" and i["group"] == "maker" for i in moved)
    assert len({i["half"] for i in result.items if i["family"] == "sundance-manual"}) == 1
    documents = pools.DocumentList(documents=listed, not_documentation=[], status="final", sha256="a" * 64,
                                   also_in_family=also)
    harness.build_set(roots, documents)
    built = {i["document"]: i["family"] for i in harness.load_items()["items"]}
    assert built["url:https://www.trane.com/x"] == "sundance-manual"
    # The committed list: the maker site's revision 5 of Trane 18-AC98D1 joins the listed revision 7E.
    real = pools.load_document_list()
    listed_families = {d["family"] for d in real.documents}
    assert real.also_in_family, "also_in_family is empty"
    for entry in real.also_in_family:
        assert entry["family"] in listed_families and entry["reason"]
        assert pools.norm_url(entry["url"]) not in {pools.norm_url(d["url"]) for d in real.documents}
    trane = [e for e in real.also_in_family if "18-AC98D1-5-EN" in e["url"]]
    assert [e["family"] for e in trane] == ["trane-condensing-units-18-ac98d1-7e-en"]
    assert pools.page_group(trane[0]["url"], set()) == "maker"


def test_near_duplicates_are_counted_across_the_halves(tmp_path: Path) -> None:
    """Pairs in different halves are counted with digits and punctuation stripped: identical,
    at or above each ratio, and sharing a long span; identical groups within one half are counted
    apart. Counts only, no text.

    Mutations: pools_near_dup_counts_within_a_half (pairs in one half are counted as crossing),
    pools_near_dup_keeps_digits (digits are not stripped)."""
    long_a = "Remove the access panel screws and lift the panel away from the condensing unit cabinet slowly"
    rows = [
        ("tune", "Check part 1 now.", ""),
        ("heldout", "Check part 2 now!", ""),
        ("tune", "Check part 3 now", ""),
        ("tune", "Turn the breaker off at the main panel before service", ""),
        ("heldout", "Turn the breaker off at the main panel before servicing", ""),
        ("tune", long_a, "then set it aside"),
        ("heldout", "First, wait. " + long_a, "and note which wires connect where on the terminal strip"),
        ("heldout", "Drain the spa", "Use a hose."),
    ]
    items = [{"half": h, "step": st_, "detail": d} for h, st_, d in rows]
    assert pools.stripped_text(items[0]) == "check part now"
    counts = pools.near_duplicates(items)
    assert counts["across_halves"] == {"identical_stripped": 2, "ratio_at_least_0.9": 3, "ratio_at_least_0.8": 3,
                                       "shared_span_at_least_60": 1}
    assert counts["within_a_half"] == {"identical_stripped_groups": 1, "items": 2}
    assert "check" not in json.dumps(counts)


def test_heldout_writer_arm_scored_on_all_of_pool_g_when_the_half_has_no_generated_step(
        sc12a_dirs: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """When no held out item is a generated step, the writer's own flags are scored on all of pool
    G with the reason printed, not shown as n/a; with no writer flag held out, a note says each
    candidate equals its layer alone.

    Mutation: harness_writer_arm_heldout_only (the held out writer line scores held out G only)."""
    items = _write_set()
    doc = harness.load_items()
    for item in doc["items"]:
        if item["half"] == pools.HELDOUT:
            item["pool"] = "D"
    doc["items"][0]["writer_flag"] = True  # a tune G step, labeled yes
    harness.write_json(harness.items_path(), doc)
    assert len(items) == len(doc["items"])
    assert cli.main(["safety-set", "lock"]) == cli.EXIT_OK
    capsys.readouterr()
    assert cli.main(["eval", "sc12a", "--heldout"]) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "  writer (generated steps only, all of pool G): recall 0.500" in out and "(1 of 2)" in out
    assert "writer (generated steps only): n/a" not in out
    assert f"note: {harness.WRITER_ALL_G_NOTE}" in out and f"note: {harness.NO_WRITER_FLAG_NOTE}" in out
    attempt = json.loads(harness.heldout_path().read_text(encoding="utf-8"))["attempts"][0]
    assert attempt["result"]["arms"]["notes"] == [harness.WRITER_ALL_G_NOTE, harness.NO_WRITER_FLAG_NOTE]


# ---------------------------------------------------------------------------
# Labels
# ---------------------------------------------------------------------------


def test_labels_take_the_majority_and_refuse_a_changed_vote() -> None:
    """Mutations: labels_other_hazard_counts_yes (a yes with hazard "other" counts as a
    safety step), labels_changed_vote_accepted."""
    raw = labels.merge_votes({}, {"readers": [
        {"reader": "r1", "labels": [{"id": "a", "safety": "yes", "hazard": "electrical"},
                                    {"id": "b", "safety": "yes", "hazard": "other"}]},
        {"reader": "r2", "labels": [{"id": "a", "safety": "yes", "hazard": "heat"},
                                    {"id": "b", "safety": "yes", "hazard": "other"}]},
        {"reader": "r3", "labels": [{"id": "a", "safety": "no", "hazard": "none"},
                                    {"id": "b", "safety": "no", "hazard": "other"}]},
    ]}, ["a", "b", "c"])
    by_id, incomplete = labels.labels_by_id(raw)
    assert incomplete == []
    assert by_id["a"]["positive"] is True and by_id["a"]["hazard"] == "split" and by_id["a"]["unanimous"] is False
    # A step whose only hazard is "other" is a no, even when readers said yes.
    assert by_id["b"]["positive"] is False and by_id["b"]["hazard"] == "other"
    assert by_id["b"]["unanimous"] is True and by_id["b"]["contradictory_votes"] == 2
    assert labels.unanimity(by_id)["unanimous"] == 1
    with pytest.raises(labels.LabelError):
        labels.merge_votes(raw, {"reader": "r1", "labels": [{"id": "a", "safety": "no", "hazard": "none"}]}, ["a"])
    with pytest.raises(labels.LabelError):
        labels.merge_votes(raw, {"reader": "r4", "labels": [{"id": "zz", "safety": "no", "hazard": "none"}]}, ["a"])


# ---------------------------------------------------------------------------
# Thresholds and the choice rule
# ---------------------------------------------------------------------------


def _scored(*rows: tuple[bool, float | None, str]) -> list[dict]:
    return [{"positive": p, "noul": n, "hazard": h, "writer": False, "word_tuned": False} for p, n, h in rows]


def test_threshold_selection_follows_the_brief() -> None:
    """Mutations: stats_threshold_lowest_full_recall (the lowest full recall threshold,
    not the highest), stats_threshold_floor_ignored (the fallback ignores the precision floor)."""
    jev = harness.production(harness.CONFIG_LAYERS[stats.JEV])
    full = _scored((True, 0.9, "electrical"), (True, 0.7, "heat"), (False, 0.8, "none"), (False, 0.1, "none"))
    choice = stats.select_threshold(full, jev)
    assert (choice.threshold, choice.rule) == (0.7, stats.FULL_RECALL)
    # A positive with no reply: no threshold flags every positive, so the fallback picks the best
    # recall with precision at least the floor, preferring the higher threshold on a tie.
    partial = _scored((True, 0.9, "electrical"), (True, 0.8, "heat"), (True, None, "gas"), (False, 0.5, "none"),
                      (False, 0.4, "none"), (False, 0.3, "none"))
    fallback = stats.select_threshold(partial, jev)
    assert (fallback.threshold, fallback.rule) == (0.8, stats.BEST_AT_FLOOR)
    assert fallback.counts.tp == 2 and fallback.counts.flagged == 2
    noisy = _scored((True, None, "heat"), (True, 0.2, "heat"), (False, 0.9, "none"), (False, 0.8, "none"),
                    (False, 0.7, "none"))
    assert stats.select_threshold(noisy, jev).rule == stats.NONE_QUALIFIED
    # The writer and the word rule already catch every positive: Jev need raise nothing.
    covered = [dict(i, word_tuned=i["positive"]) for i in full]
    word_jev = harness.production(harness.CONFIG_LAYERS[stats.WORD_JEV])
    assert stats.select_threshold(covered, word_jev).threshold is None


def test_choice_rule_and_its_ties() -> None:
    """Mutations: stats_choice_ignores_floor (a candidate below the precision floor is kept),
    stats_choice_tie_prefers_model_call (on a full tie the model calling candidate wins),
    stats_choice_precision_tie_break_reversed."""
    C = stats.Counts
    # Highest recall wins among those at or above the floor.
    pick = stats.choose({"word": C(5, 10, 10), "jev": C(8, 12, 10), "word+jev": C(9, 18, 10)})
    assert pick.picked == "jev" and "word+jev" in pick.dropped and pick.ranked == ["jev"]
    # Recall tie: the higher precision.
    assert stats.choose({"word": C(8, 13, 10), "jev": C(8, 10, 10)}).picked == "jev"
    # Recall and precision tie: the one with no model call.
    tie = stats.choose({"word": C(8, 10, 10), "jev": C(8, 10, 10), "word+jev": C(8, 10, 10)})
    assert tie.picked == "word" and tie.ranked == ["word", "jev", "word+jev"]
    # None kept: nothing picked; an unusable threshold is dropped with its reason.
    none = stats.choose({"word": C(1, 10, 10), "jev": None})
    assert none.picked is None and none.dropped["jev"] == "no usable threshold on the tune half"


def test_reliability_table_bins_and_counts() -> None:
    """Mutation: stats_reliability_drops_one (a probability of exactly 1 falls out of the last bin)."""
    rows = stats.reliability(_scored((True, 1, "heat"), (True, 0.95, "heat"), (False, 0.9, "none"),
                                     (False, 0.05, "none"), (True, None, "gas")))
    assert [r["count"] for r in rows] == [1, 0, 0, 0, 0, 0, 0, 0, 0, 3]
    assert rows[9]["positives"] == 2 and rows[9]["observed_rate"] == pytest.approx(2 / 3)
    assert rows[0]["observed_rate"] == 0 and rows[1]["mean_noul"] is None


# ---------------------------------------------------------------------------
# The stopping rule, the lock and the held out rule, through the CLI
# ---------------------------------------------------------------------------

# (half, step, writer flag, majority safety, hazard, Jev probability)
SET = [
    ("tune", "Turn off power at the breaker, wait 20 minutes, then restore power", False, "yes", "electrical", 0.95),
    ("tune", "Let the element cool before touching it", False, "yes", "heat", 0.72),
    ("tune", "Set the heat mode to Standard", False, "no", "none", 0.4),
    ("tune", "Check the water level is at the correct fill line", False, "no", "none", 0.1),
    ("heldout", "Turn off power at the breaker, then restore power", False, "yes", "electrical", 0.9),
    ("heldout", "Kill the circuit that feeds the spa", False, "yes", "electrical", 0.85),
    ("heldout", "Close the gas valve before lifting the cover", False, "yes", "gas", 0.7),
    ("heldout", "Press the Power button on the topside control", False, "no", "none", 0.2),
    ("heldout", "Visually inspect the fan blades for damage", False, "no", "other", 0.75),
    ("heldout", "Do not open the equipment bay while the pump is running", False, "no", "none", 0.05),
]


@pytest.fixture
def sc12a_dirs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(config, "EVAL_DIR", tmp_path / "eval")
    committed = tmp_path / "committed"
    committed.mkdir()
    monkeypatch.setattr(harness, "COMMITTED_DIR", committed)
    monkeypatch.delenv(config.ENV_MODE, raising=False)
    # The synthetic sets get their own lock and choice, so they start from the
    # config as it stood before the real lock, not from what SC12a shipped.
    monkeypatch.setattr(config, "SAFETY_LAYERS", ("word",))
    monkeypatch.setattr(config, "JEV_THRESHOLD", None)
    return tmp_path


def _write_set(rows=SET, *, exhausted: bool = True, replies: bool = True) -> list[dict]:
    items = [{"id": step_key(step, ""), "pool": "G", "appliance": "hot tub", "step": step, "detail": "",
              "document": f"doc-{n}", "family": f"doc-{n}", "writer_flag": writer, "occurrences": 1,
              "sources": [], "order": n,
              "document_sha256": pools.document_sha256(f"doc-{n}"), "half": half}
             for n, (half, step, writer, *_rest) in enumerate(rows)]
    harness.write_items(pools.BuildResult(items=items, exhausted=exhausted, forced_tune=[]),
                        maker_documents_sha256=pools.load_document_list().sha256)
    votes = [{"id": i["id"], "safety": r[3], "hazard": r[4]} for i, r in zip(items, rows, strict=True)]
    path = config.EVAL_DIR / "labels-in.json"
    path.write_text(json.dumps({"readers": [{"reader": f"r{k}", "labels": votes} for k in (1, 2, 3)]}),
                    encoding="utf-8")
    harness.import_labels([path])
    if replies:
        qhash = harness.question_hash_of(harness.pick_wording(None))
        for item, row in zip(items, rows, strict=True):
            harness.append_reply(qhash, {"item_id": item["id"], "step_sha256": item["id"], "question_hash": qhash,
                                         "model": config.JEV_MODEL, "noul": row[5], "error": None})
    return items


def test_stopping_rule_blocks_scoring_until_met(sc12a_dirs: Path, capsys: pytest.CaptureFixture[str],
                                                monkeypatch: pytest.MonkeyPatch) -> None:
    """Mutation: harness_stopping_rule_ignored (scoring runs with too few held out positives)."""
    _write_set(exhausted=False)
    status = harness.stopping_status(harness.load_items(), harness.load_labels())
    assert status == {"heldout_positives": 3, "exhausted": False, "unlabeled": [], "satisfied": False}
    assert cli.main(["eval", "sc12a"]) == cli.EXIT_REFUSED
    assert "fewer than 30" in capsys.readouterr().err
    monkeypatch.setattr(config, "SC12A_MIN_HELDOUT_POSITIVES", 3)
    assert harness.stopping_status(harness.load_items(), harness.load_labels())["satisfied"] is True
    assert cli.main(["eval", "sc12a"]) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "SC12a, tune half: 4 items" in out and "threshold for jev (tune half, full_recall): 0.7200" in out


def test_heldout_scoring_refused_without_a_lock(sc12a_dirs: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Mutation: harness_heldout_without_lock (the lock check is cut)."""
    _write_set()
    assert cli.main(["eval", "sc12a", "--heldout"]) == cli.EXIT_REFUSED
    assert "no held out number before Gate 2" in capsys.readouterr().err
    assert not harness.heldout_path().exists()


def test_heldout_scoring_refused_when_the_lock_does_not_match(sc12a_dirs: Path, monkeypatch: pytest.MonkeyPatch,
                                                              capsys: pytest.CaptureFixture[str]) -> None:
    """Mutations: harness_lock_mismatch_ignored (the hash comparison is cut),
    harness_lock_skips_word_list (the tuned word list is not hashed into the lock)."""
    _write_set()
    assert cli.main(["safety-set", "lock"]) == cli.EXIT_OK
    lock = json.loads(harness.lock_path().read_text(encoding="utf-8"))
    assert lock["model"] == config.JEV_MODEL and lock["word_list_tuned"] == list(config.SAFETY_WORDS)
    assert lock["thresholds"]["jev"] == {"threshold": 0.72, "rule": "full_recall"}
    for name in harness.LOCK_FIELDS:
        assert len(lock["sha256"][name]) == 64
    assert lock["sha256"]["wording"] == harness.sha256_of(lock["wording"])
    capsys.readouterr()
    assert cli.main(["safety-set", "lock"]) == cli.EXIT_REFUSED  # a second lock needs a logged reason
    monkeypatch.setattr(config, "SAFETY_WORDS", (*config.SAFETY_WORDS, "panel"))
    assert cli.main(["eval", "sc12a", "--heldout"]) == cli.EXIT_REFUSED
    err = capsys.readouterr().err
    assert "does not match" in err and "word_list_tuned" in err
    assert not harness.heldout_path().exists()


def test_lock_refuses_a_changed_split(sc12a_dirs: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """The lock hashes each item's family and half (decision 58): an item moved to another family,
    its half unchanged, is a changed split, and the held out half is not scored under the old lock.

    Mutations: harness_lock_skips_split (the split is not among the lock's fields),
    harness_lock_split_omits_family (the split hashes halves only)."""
    _write_set()
    assert cli.main(["safety-set", "lock"]) == cli.EXIT_OK
    lock = json.loads(harness.lock_path().read_text(encoding="utf-8"))
    split = [[i["id"], i["family"], i["half"]] for i in harness.load_items()["items"]]
    assert lock["sha256"]["split"] == harness.sha256_of(split) and "split" not in {k for k in lock if k != "sha256"}
    doc = harness.load_items()
    doc["items"][0]["family"] = "doc-1"  # the same half, another family
    harness.write_json(harness.items_path(), doc)
    capsys.readouterr()
    assert cli.main(["eval", "sc12a", "--heldout"]) == cli.EXIT_REFUSED
    err = capsys.readouterr().err
    assert "does not match" in err and "(split)" in err
    assert not harness.heldout_path().exists()


def test_heldout_scored_once_then_only_with_a_reason(sc12a_dirs: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Mutations: harness_rescore_without_reason (a second scoring needs no reason),
    harness_rescore_reason_not_recorded."""
    _write_set()
    assert cli.main(["safety-set", "lock"]) == cli.EXIT_OK
    capsys.readouterr()
    assert cli.main(["eval", "sc12a", "--heldout"]) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "SC12a, held out half: 6 items" in out
    assert "choice rule picks: jev" in out and "dropped word: precision below 0.6" in out
    assert "  Jev: recall 0.667" in out
    assert "flags on steps whose only hazard is other (counted as negatives):" in out and "Jev 1" in out
    assert cli.main(["eval", "sc12a", "--heldout"]) == cli.EXIT_REFUSED
    assert "--rescore-reason" in capsys.readouterr().err
    assert cli.main(["eval", "sc12a", "--heldout", "--rescore-reason", "relabeled item 4 after review"]) == cli.EXIT_OK
    attempts = json.loads(harness.heldout_path().read_text(encoding="utf-8"))["attempts"]
    assert [a["reason"] for a in attempts] == [None, "relabeled item 4 after review"]
    assert attempts[0]["result"]["choice"]["picked"] == "jev"


def test_unknown_eval_suite_is_an_error() -> None:
    """Mutations: cli_unknown_suite_falls_through (an unknown suite runs sc7b again),
    cli_eval_score_unknown_is_sc7b."""
    args = argparse.Namespace(which="sc99", score=False, live=False, fix=None, new_ledger=False,
                              wording=None, heldout=False, rescore_reason=None)
    with pytest.raises(cli.CliRefusal, match="unknown eval suite 'sc99'"):
        cli.cmd_eval_live(args, {})
    with pytest.raises(cli.CliRefusal, match="no evaluator scores suite 'sc99'"):
        cli.eval_score("sc99", [])
    with pytest.raises(cli.CliRefusal, match="apply to sc12a only"):
        cli.cmd_eval_live(argparse.Namespace(**{**vars(args), "which": "sc3b", "heldout": True}), {})
    assert set(cli.EVAL_SUITES) == {"sc3b", "sc7b", "plates", "sc12a", "sc12b"}


# ---------------------------------------------------------------------------
# SC12b's offline pieces
# ---------------------------------------------------------------------------


def test_sc12b_first_lookup_graph_is_restored_by_hash(tmp_path: Path) -> None:
    """Mutations: sc12b_graph_not_restored (the original bytes are not written back),
    sc12b_strip_keeps_scoped_codes (a code scoped to the model survives)."""
    graph = {"graph_version": 1, "meta": {}, "nodes": [
        {"key": "model:sundance-spas:optima880", "type": "model"},
        {"key": "code:sundance-spas:optima880:FLO", "type": "code"},
        {"key": "model:trane:xr164ttr6036", "type": "model"},
        {"key": "cause:trane:xr164ttr6036:abc", "type": "cause"},
        {"key": "source:1", "type": "source"},
    ], "edges": [
        {"kind": "HAS_CODE", "src": "model:sundance-spas:optima880", "dst": "code:sundance-spas:optima880:FLO"},
        {"kind": "DOCUMENTED_CAUSE", "src": "model:trane:xr164ttr6036", "dst": "cause:trane:xr164ttr6036:abc"},
    ]}
    path = tmp_path / "graph.json"
    path.write_text(json.dumps(graph, indent=1), encoding="utf-8")  # not the saved form: restore is by bytes
    before = sc12b.file_sha256(path)
    scope = sc12b.model_scope({"manufacturer": "Sundance Spas", "model": "Optima 880"})
    assert scope == "sundance-spas:optima880"
    with sc12b.first_lookup_graph(path, scope) as info:
        during = json.loads(path.read_text(encoding="utf-8"))
        assert [n["key"] for n in during["nodes"]] == ["model:trane:xr164ttr6036", "cause:trane:xr164ttr6036:abc",
                                                        "source:1"]
        assert [e["kind"] for e in during["edges"]] == ["DOCUMENTED_CAUSE"]
        assert (info["nodes_removed"], info["edges_removed"]) == (2, 1)
        path.write_text("{}", encoding="utf-8")  # the run grows the graph
    assert sc12b.file_sha256(path) == before and info["restored"] is True


def test_sc12b_inputs_photo_and_score(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Mutations: sc12b_photo_first_file (the photo is not matched by hash),
    sc12b_miss_not_listed (a positive left unflagged is not reported), sc12b_stop_line_at_reach."""
    recordings, assets = tmp_path / "recordings", tmp_path / "assets"
    recordings.mkdir()
    assets.mkdir()
    (assets / "a-other.jpg").write_bytes(b"other")
    (assets / "b-plate.jpg").write_bytes(b"plate")
    plate = hashlib.sha256(b"plate").hexdigest()
    (recordings / "live_t_1234.json").write_text(json.dumps({
        "input": {"identity": None, "symptom": "panel shows FLO", "plate_sha256": plate},
        "resume": {"identity": {"manufacturer": "Sundance Spas", "model": "Optima 880", "serial": "1"},
                   "observed_code": "FLO"}}), encoding="utf-8")
    given = sc12b.read_input("t-1234", recordings)
    assert given["answer_identity"] == {"manufacturer": "Sundance Spas", "model": "Optima 880"}
    assert given["answer_code"] == "FLO" and given["symptom"] == "panel shows FLO"
    assert sc12b.resolve_photo(given["plate_sha256"], assets) == assets / "b-plate.jpg"
    assert sc12b.resolve_photo(None, assets) is None

    breaker = {"step": "Turn off power at the breaker", "detail": "", "safety_flag": True}
    gas = {"step": "Close the gas valve", "detail": "", "safety_flag": False}
    record = {"run_id": "t-x", "status": "ok", "cost_usd": 0.05, "credits": 6, "steps": [breaker, gas],
              "safety_provenance": [{"step_sha256": step_key(breaker["step"], ""), "writer_flag": False,
                                     "jev_noul": 0.9}]}
    marks = {step_key(breaker["step"], ""): {"positive": True, "hazard": "electrical"},
             step_key(gas["step"], ""): {"positive": True, "hazard": "gas"}}
    score = sc12b.score_sc12b([record], marks)
    assert score["layers"]["final"].tp == 1 and score["layers"]["word"].tp == 1
    assert score["misses"] == [{"run_id": "t-x", "step": "Close the gas valve", "detail": ""}]
    lines = "\n".join(sc12b.format_sc12b(score, None))
    assert "MISSED" in lines and "Close the gas valve" in lines
    assert sc12b.arm_a_in("x " + sc12b.ARM_A_MARKER + " y") and not sc12b.arm_a_in("item 5 as published")
    assert sc12b.past_stop_line(config.SC12_STOP_USD) is False
    assert sc12b.past_stop_line(config.SC12_STOP_USD + 0.01) is True


def test_sc12b_score_reads_like_sc12a(sc12a_dirs: Path, monkeypatch: pytest.MonkeyPatch,
                                     capsys: pytest.CaptureFixture[str]) -> None:
    """`advisor eval sc12b --score` prints each layer as the SC12a scorer does: recall and precision,
    each with its Wilson 95% interval as [low, high], under plain layer names, and marks the word rule
    and Jev shipped or not shipped by config.SAFETY_LAYERS.

    Mutations: sc12b_score_raw_interval (the interval printed as a raw tuple, precision without one),
    sc12b_score_shipped_always (every code layer marked shipped), sc12b_score_word_tag_from_jev (the
    word rule takes Jev's tag), sc12b_score_baseline_no_interval (the baseline's recall without its interval)."""
    monkeypatch.setattr(config, "JEV_THRESHOLD", 0.5)
    rows = [  # step, final flag, writer flag, Jev probability, positive, hazard
        ("Turn off power at the breaker", True, True, 0.9, True, "electrical"),
        ("Open the panel door", True, False, 0.2, True, "electrical"),
        ("Close the cabinet door", False, False, 0.1, False, "other"),
        ("Rinse the filter", False, False, None, False, "none"),
    ]
    record = {"kind": "run", "suite": sc12b.SUITE, "run_id": "t-score", "status": "ok", "started_at": "1",
              "cost_usd": 0.05, "credits": 6,
              "steps": [{"step": r[0], "detail": "", "safety_flag": r[1]} for r in rows],
              "safety_provenance": [{"step_sha256": step_key(r[0], ""), "writer_flag": r[2], "jev_noul": r[3]}
                                    for r in rows]}
    config.EVAL_DIR.mkdir(parents=True)
    (config.EVAL_DIR / "sc12b-t-score.json").write_text(json.dumps(record), encoding="utf-8")
    (harness.COMMITTED_DIR / harness.LABELS).write_text(json.dumps({"labels": {
        step_key(r[0], ""): {"positive": r[4], "hazard": r[5]} for r in rows}}), encoding="utf-8")

    def printed() -> str:
        capsys.readouterr()
        assert cli.main(["eval", "sc12b", "--score"]) == cli.EXIT_OK
        return capsys.readouterr().out

    out = printed()  # the fixture's config: SAFETY_LAYERS = ("word",)
    assert "each layer, recall and precision with Wilson 95% intervals:" in out
    assert ("  final flags (as the briefs show them): recall 1.000 [0.342, 1.000] (2 of 2), "
            "precision 1.000 [0.342, 1.000] (2 of 2)\n") in out
    assert ("  writer (revised instruction, arm A): recall 0.500 [0.095, 0.905] (1 of 2), "
            "precision 1.000 [0.207, 1.000] (1 of 1)\n") in out
    assert ("  word rule, tuned (shipped): recall 1.000 [0.342, 1.000] (2 of 2), "
            "precision 0.667 [0.208, 0.939] (2 of 3)\n") in out
    assert ("  Jev at 0.50 (not shipped): recall 0.500 [0.095, 0.905] (1 of 2), "
            "precision 1.000 [0.207, 1.000] (1 of 1)\n") in out
    assert "wilson recall" not in out and "(0." not in out
    assert "target: every in scope positive flagged: met" in out

    monkeypatch.setattr(config, "SAFETY_LAYERS", ("jev",))
    out = printed()
    assert "  word rule, tuned (not shipped): " in out and "  Jev at 0.50 (shipped): " in out
    monkeypatch.setattr(config, "SAFETY_LAYERS", ("word", "jev"))
    out = printed()
    assert "  word rule, tuned (shipped): " in out and "  Jev at 0.50 (shipped): " in out
    assert "not shipped" not in out

    score = sc12b.score_sc12b(sc12b.load_records(), harness.load_labels())
    lines = sc12b.format_sc12b(score, stats.Counts(tp=3, flagged=3, positives=5))
    assert ("  writer baseline, 18 September v2 briefs on the fixed build (pool G): recall 0.600 [0.231, 0.882] "
            "(3 of 5); search results drift, so compare with care") in lines

    monkeypatch.setattr(config, "JEV_THRESHOLD", None)
    assert sc12b.layer_names()["jev"] == "Jev, no threshold set (shipped)"


def test_sc12b_writer_baseline_leaves_out_steps_only_a_replaced_build_wrote() -> None:
    """The 18 September writer baseline counts v2 steps (documents t-...) from the fixed build only: a
    step whose every source is a replaced build's brief under data/superseded/ is left out, since the
    replaced build is not a result; a step the fixed build also wrote stays in, and so does a step read
    from a data folder outside the repo whose path has no superseded/ part.

    Mutations: sc12b_baseline_keeps_superseded_steps (the filter is cut), sc12b_baseline_superseded_any
    (one superseded source is enough to drop a step the fixed build also wrote)."""

    def item(key: str, document: str, sources: list[str], flag: bool) -> dict[str, object]:
        return {"id": key, "pool": pools.POOL_G, "document": document, "sources": sources, "writer_flag": flag}

    doc = {"items": [
        item("fixed", "t-1111", ["briefs/v2/t-1111.html", "data/briefs/t-1111.html"], False),
        item("both", "t-2222", ["data/briefs/t-2222.html", "data/superseded/b/briefs/t-2222.html"], True),
        item("outside", "t-4444", ["/tmp/x/data/briefs/t-4444.html"], True),
        item("replaced", "t-3333", ["data/superseded/b/briefs/t-3333.html"], True),
        item("replaced_abs", "t-5555", ["/tmp/x/data/superseded/b/briefs/t-5555.html"], True),
        item("v1", "549892815cb6", ["briefs/549892815cb6.html"], True),
    ]}
    labels = {key: {"positive": True, "hazard": "electrical"}
              for key in ("fixed", "both", "outside", "replaced", "replaced_abs", "v1")}
    counts = sc12b.writer_baseline(doc, labels)
    assert (counts.tp, counts.flagged, counts.positives) == (2, 2, 3)
    assert sc12b.superseded_only({"sources": ["data/superseded/b/briefs/t-3333.html"]})
    assert not sc12b.superseded_only({"sources": []})


def test_maker_documents_list_is_final_and_reviewed() -> None:
    """Decision 53 fixes the list before any labeling; the review is recorded in the file.

    Mutations: maker_documents_back_to_draft (the list goes back to an unreviewed draft),
    maker_documents_family_split (one Sundance 880 manual gets a family of its own)."""
    data = json.loads((SAFETY_EVAL_DIR / "maker_documents.json").read_text(encoding="utf-8"))
    assert data["status"] == "final"
    assert "before any labeling" in data["reviewed"]
    assert "ending review" not in json.dumps(data)
    assert "decision 58" in data["reviewed"] and "before any labeling" in data["reviewed"].split("decision 58")[1]
    docs = pools.load_maker_documents()
    assert len(docs) >= 3
    # Decision 58's families: the Sundance 880 manuals share one, the AquaRest manuals another, and
    # every other listed document is its own.
    families = [doc["family"] for doc in docs]
    sundance = [d["family"] for d in docs if d["maker"] == "Sundance Spas"]
    aquarest = [d["family"] for d in docs if d["maker"] == "AquaRest Spas"]
    assert sundance == ["sundance-880-owners-manual"] * 3 and aquarest == ["aquarest-owners-manual"] * 3
    others = [f for f in families if f not in ("sundance-880-owners-manual", "aquarest-owners-manual")]
    assert len(others) == len(set(others)) == len(docs) - 6
    for doc in docs:
        assert set(doc) >= {"url", "host", "title", "reason", "family"}
        assert doc["host"] == pools.tiers.host_of(doc["url"])
        # Listed documents sit on hosts the host rule does not already call the maker's.
        assert pools.page_group(doc["url"], {pools.norm_url(doc["url"])}) == "listed"


def test_live_sc12_commands_refuse_before_any_key_or_ledger(sc12a_dirs: Path, monkeypatch: pytest.MonkeyPatch,
                                                            capsys: pytest.CaptureFixture[str]) -> None:
    """The paid halves refuse, with nothing read or created, when their protocol step is missing:
    the held out half is asked only after the lock, and SC12b runs only after the lock.

    Mutations: sc12a_live_heldout_before_lock (the live held out batch skips the lock check),
    sc12b_live_before_lock (SC12b skips the lock check)."""
    ledger = sc12a_dirs / "ledger" / "ledger.sqlite"
    monkeypatch.setattr(config, "LEDGER_PATH", ledger)
    monkeypatch.setenv(config.ENV_MODE, "cheap")
    _write_set()
    capsys.readouterr()
    assert cli.main(["eval", "sc12a", "--live", "--heldout"]) == cli.EXIT_REFUSED
    assert "only after the lock" in capsys.readouterr().err
    assert cli.main(["eval", "sc12b", "--live"]) == cli.EXIT_REFUSED
    assert "SC12b runs after the lock (Gate 2)" in capsys.readouterr().err
    assert not ledger.exists()
    # Gate 1's printout comes with every labels import.
    monkeypatch.delenv(config.ENV_MODE)
    assert cli.main(["safety-set", "labels", str(config.EVAL_DIR / "labels-in.json")]) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "pool G: tune 4, held out 6" in out and "unanimity: 10 of 10" in out
    assert "held out in scope positives: 3 (electrical 2, gas 1)" in out


# ---------------------------------------------------------------------------
# The maker document list is pinned (decision 53) and filters pool D
# ---------------------------------------------------------------------------


def test_maker_list_hash_pins_the_set_extension_and_lock(sc12a_dirs: Path, tmp_path: Path,
                                                         monkeypatch: pytest.MonkeyPatch,
                                                         capsys: pytest.CaptureFixture[str]) -> None:
    """The set records the list's sha256; --extend and the lock refuse a changed list, and the lock pins it.

    Mutations: harness_extend_ignores_maker_list (--extend draws D under an edited list);
    harness_lock_ignores_maker_list (the lock is written under an edited list);
    harness_lock_skips_maker_list (the lock does not hash the list)."""
    roots, listed = _tree(tmp_path / "tree")
    built = pools.DocumentList(documents=listed, not_documentation=[], status="draft", sha256="a" * 64)
    harness.build_set(roots, built)
    assert harness.load_items()["maker_documents_sha256"] == "a" * 64
    assert harness.read_json(harness.COMMITTED_DIR / harness.ITEM_IDS)["maker_documents_sha256"] == "a" * 64
    edited = pools.DocumentList(documents=listed[:0], not_documentation=[], status="draft", sha256="b" * 64)
    with pytest.raises(harness.EvalRefusal, match="changed since the set was built"):
        harness.extend_set(roots, edited)
    with pytest.raises(harness.EvalRefusal) as caught:  # the same list gets past the pin
        harness.extend_set(roots, built)
    assert "changed since" not in str(caught.value)

    harness.items_path().unlink()
    _write_set()  # records the committed list's hash
    changed = tmp_path / "maker_documents.json"
    changed.write_bytes(pools.MAKER_DOCUMENTS_PATH.read_bytes() + b"\n")
    real = pools.MAKER_DOCUMENTS_PATH
    monkeypatch.setattr(pools, "MAKER_DOCUMENTS_PATH", changed)
    capsys.readouterr()
    assert cli.main(["safety-set", "lock"]) == cli.EXIT_REFUSED
    assert "changed since the set was built" in capsys.readouterr().err
    monkeypatch.setattr(pools, "MAKER_DOCUMENTS_PATH", real)
    assert cli.main(["safety-set", "lock"]) == cli.EXIT_OK
    lock = json.loads(harness.lock_path().read_text(encoding="utf-8"))
    assert lock["sha256"]["maker_documents"] == harness.sha256_of(pools.load_document_list().sha256)


def test_a_set_is_rebuilt_only_under_a_new_sampling_before_anyone_had_it(sc12a_dirs: Path, tmp_path: Path) -> None:
    """Decisions 58 and 59 replaced the split before any labeling: a set built under an earlier sampling is
    replaced while no reader or scorer has had it, and the replacement is recorded. A set built
    under the current sampling is built once, one a reader has had is never rebuilt, and --extend
    refuses a set of another sampling.

    Mutations: harness_rebuild_same_sampling (a current set is rebuilt),
    harness_rebuild_ignores_readers (a set whose reader packet exists is rebuilt),
    harness_extend_other_sampling (an older set is extended under the new families),
    harness_counts_skip_appliances (the build printout leaves out each half by appliance)."""
    roots, listed = _tree(tmp_path / "tree")
    documents = pools.DocumentList(documents=listed, not_documentation=[], status="final", sha256="a" * 64)
    old = {"built_at": "2026-09-29T14:35:00+00:00", "exhausted": False, "maker_documents_sha256": "a" * 64,
           "items": [{"id": "0" * 64, "pool": "D", "appliance": "hot tub", "step": "Drain the spa.", "detail": "",
                      "document": "url:x", "half": "tune"}]}
    harness.write_json(harness.items_path(), old)
    with pytest.raises(harness.EvalRefusal, match="cannot be extended"):
        harness.extend_set(roots, documents)
    result = harness.build_set(roots, documents)
    built = harness.load_items()
    assert built["sampling"] == pools.SAMPLING == "decision 59" and len(built["items"]) == len(result.items)
    assert built["replaced"] == {"sampling": "decision 53", "built_at": old["built_at"], "items": 1}
    ids = harness.read_json(harness.COMMITTED_DIR / harness.ITEM_IDS)
    assert ids["sampling"] == "decision 59" and all(i["family"] for i in ids["items"])
    assert ids["families"] == built["families"] and sum(f["items"] for f in ids["families"].values()) == len(ids["items"])
    lines = harness.counts_lines(built, {})
    assert any(line.startswith("replaced an unlabeled set of 1 items") for line in lines)
    assert any(line.startswith("families: tune ") for line in lines)
    # Decision 59: the printout gives each half by appliance and the near duplicates across the halves.
    assert any(line.startswith("  held out by appliance: ") for line in lines)
    assert any(line.startswith("near duplicate pairs across the halves (reported, not removed): identical stripped ")
               for line in lines)
    assert built["near_duplicates"] == ids["near_duplicates"] == pools.near_duplicates(built["items"])
    with pytest.raises(harness.EvalRefusal, match="built once"):
        harness.build_set(roots, documents)
    harness.write_json(harness.items_path(), old)
    harness.write_reader_packet(old["items"], {})
    with pytest.raises(harness.EvalRefusal, match="never rebuilt"):
        harness.build_set(roots, documents)
    assert harness.load_items() == old


def test_not_documentation_pages_are_left_out_and_urls_name_the_appliance(tmp_path: Path) -> None:
    """Mutations: pools_not_documentation_kept (a listed off topic page still gives D items);
    pools_appliance_from_query_only (a page whose query names no appliance is "appliance" though
    its URL says hot tub)."""
    roots, listed = _tree(tmp_path)
    kept = pools.build_items(roots, listed, limit=100)
    assert "Open the panel." in [i["step"] for i in kept.items]
    cut = pools.build_items(roots, listed, limit=100, excluded=["https://shop.dealer.com/a/"])
    assert "Open the panel." not in [i["step"] for i in cut.items]
    assert all(i.get("sources") != ["https://shop.dealer.com/a"] for i in cut.items)
    assert pools.page_appliance("\"Optima 880\" FLO troubleshooting",
                                "https://www.example.co.uk/blogs/maintenance/how-to-fix-hot-tub-flow-errors") == "hot tub"
    assert pools.page_appliance("Trane XR16 manual", "https://example.com/spa") == "air conditioner"
    assert pools.page_appliance("\"880 SERIES\" filetype:pdf", "https://example.com/rules.pdf") == "appliance"
    committed = pools.load_document_list()
    urls = {pools.norm_url(u) for u in committed.excluded}
    assert {"https://hypebeast.com/tags/adidas-zx-9000", "https://stockx.com/adidas-zx-9000-30-years-of-torsion"} <= urls
    assert all(d.get("reason") for d in committed.not_documentation)
    assert not urls & {pools.norm_url(d["url"]) for d in committed.documents}


# ---------------------------------------------------------------------------
# What the readers get (blind), and SC12b steps that SC12a already labeled
# ---------------------------------------------------------------------------


def test_reader_packet_holds_only_id_appliance_step_and_detail(sc12a_dirs: Path,
                                                               capsys: pytest.CaptureFixture[str]) -> None:
    """Mutation: reader_packet_carries_writer_flag (a layer's output reaches the readers)."""
    items = _write_set()
    item = {**items[0], "writer_flag": True, "pool": "G", "sources": ["briefs/x.html"], "group": "maker",
            "page_sha256": "c" * 64, "id": "0" * 64}
    path, count = harness.write_reader_packet([*items, item], harness.load_labels())
    packet = harness.read_json(path)["items"]
    assert count == 1 and path == harness.reader_packet_path()
    assert packet == [{"id": "0" * 64, "appliance": "hot tub", "step": items[0]["step"], "detail": ""}]
    path, count = harness.write_reader_packet(items, {})
    packet = harness.read_json(path)["items"]
    assert count == len(items) and [p["id"] for p in packet] == sorted(i["id"] for i in items)
    assert all(set(p) == {"id", "appliance", "step", "detail"} for p in packet)
    assert cli.main(["safety-set", "packet"]) == cli.EXIT_OK
    assert "0 unlabeled items" in capsys.readouterr().out  # every SC12a item is labeled


def test_sc12b_steps_already_labeled_are_not_labeled_again(sc12a_dirs: Path,
                                                           capsys: pytest.CaptureFixture[str]) -> None:
    """Mutation: sc12b_items_relabel_known (a step SC12a labeled goes to the readers again, and a
    different vote on it would refuse the whole SC12b import)."""
    items = _write_set()
    known = items[0]
    fresh = {"id": step_key("Unplug the spa", ""), "pool": sc12b.POOL_B, "appliance": "hot tub",
             "step": "Unplug the spa", "detail": "", "half": None, "runs": ["t-1"]}
    path, reused = harness.write_sc12b_items([
        {"id": known["id"], "pool": sc12b.POOL_B, "appliance": "hot tub", "step": known["step"], "detail": "",
         "half": None, "runs": ["t-1"]}, fresh])
    assert reused == 1
    stored = {i["id"]: i["reused_label"] for i in harness.read_json(path)["items"]}
    assert stored == {known["id"]: True, fresh["id"]: False}
    assert cli.main(["safety-set", "packet", "--sc12b"]) == cli.EXIT_OK
    assert "1 unlabeled items" in capsys.readouterr().out
    packet = harness.read_json(harness.reader_packet_path(sc12b=True))["items"]
    assert [p["id"] for p in packet] == [fresh["id"]]


# ---------------------------------------------------------------------------
# The choice rule keeps nothing
# ---------------------------------------------------------------------------


def test_no_candidate_kept_prints_the_null_config() -> None:
    """Mutation: format_null_choice_silent (no SAFETY_LAYERS value and no JEV_SAFETY_ENABLED line
    when the choice rule keeps no candidate)."""
    low = stats.Counts(tp=1, flagged=4, positives=2, other_flagged=0)
    choice = stats.choose({stats.WORD: low, stats.JEV: None, stats.WORD_JEV: low})
    assert choice.picked is None
    result = {"half": pools.HELDOUT, "items": 4, "question_hash": "0" * 16, "thresholds": {},
              "arms": {"alone": {}, "candidates": {}}, "choice": choice, "reliability": []}
    lines = harness.format_result(result)
    assert "  config: SAFETY_LAYERS = ()" in lines
    assert any("set JEV_SAFETY_ENABLED = False" in line for line in lines)
    assert any("published miss (success criterion 2) stays unfixed" in line for line in lines)


# ---------------------------------------------------------------------------
# SC12b's live command, on the live path's fakes (reached through agent.cli only)
# ---------------------------------------------------------------------------

from agent.tests.test_live_eval import Fakes, live_env  # noqa: E402,F401  (live_env is a fixture)


def _sc12b_ready(monkeypatch: pytest.MonkeyPatch, *, runs_per_input: int = 1) -> None:
    """A locked, held out scored set, arm A in the prompt, config as the choice rule picked (jev at
    0.72), and one recorded input: the fabricated Aquarest of case B4, typed identity, no photo."""
    from agent import prompts

    committed = config.EVAL_DIR.parent / "committed"
    committed.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(harness, "COMMITTED_DIR", committed)
    # The synthetic set is locked and scored from the pre-lock config, as the real one was.
    monkeypatch.setattr(config, "SAFETY_LAYERS", ("word",))
    monkeypatch.setattr(config, "JEV_THRESHOLD", None)
    _write_set()
    harness.write_lock()
    assert harness.score_heldout()["choice"].picked == stats.JEV
    monkeypatch.setattr(prompts, "SYNTHESIS_SYSTEM", prompts.SYNTHESIS_SYSTEM + "\n" + sc12b.ARM_A_MARKER + ".")
    monkeypatch.setattr(config, "SAFETY_LAYERS", ("jev",))
    monkeypatch.setattr(config, "JEV_THRESHOLD", 0.72)
    identity = {"manufacturer": "Aquarest", "model": "ZX-9000 Pro"}
    config.RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)
    (config.RECORDINGS_DIR / "live_t_5678.json").write_text(json.dumps({
        "input": {"identity": identity, "symptom": "not heating", "plate_sha256": None},
        "resume": {"identity": identity, "observed_code": None}}), encoding="utf-8")
    monkeypatch.setattr(config, "SC12B_INPUT_RUNS", ("t-5678",))
    monkeypatch.setattr(config, "SC12B_RUNS_PER_INPUT", runs_per_input)


def _sc12b(monkeypatch: pytest.MonkeyPatch, typed: str) -> int:
    import io

    monkeypatch.setattr("sys.stdin", io.StringIO(typed))
    return cli.main(["eval", "sc12b", "--live"])


def _used_credits(credits: int) -> None:
    import sqlite3

    with sqlite3.connect(config.LEDGER_PATH) as conn:
        conn.execute("INSERT INTO entries (ts, run_id, mode, node, provider, kind, usd, tavily_credits) VALUES "
                     "('2026-09-18T00:00:00+00:00', 'earlier', 'cheap', 'research', 'tavily', 'charge', 0, ?)",
                     (credits,))


def test_sc12b_refuses_a_config_the_choice_rule_did_not_pick(live_env: Fakes, monkeypatch: pytest.MonkeyPatch,
                                                              capsys: pytest.CaptureFixture[str]) -> None:
    """Default config (the word layer, Jev enabled, no threshold) is not what the held out choice
    picked (jev at 0.72), so the paid batch refuses before any key or ledger is read.

    Mutation: sc12b_live_config_unchecked (SC12b runs whatever config ships)."""
    _sc12b_ready(monkeypatch)
    monkeypatch.setattr(config, "SAFETY_LAYERS", ("word",))
    monkeypatch.setattr(config, "JEV_THRESHOLD", None)
    config.LEDGER_PATH.unlink()
    assert _sc12b(monkeypatch, "proceed\n") == cli.EXIT_REFUSED
    err = capsys.readouterr().err
    assert "does not ship it" in err and "config.SAFETY_LAYERS is ('word',)" in err
    assert "config.JEV_THRESHOLD is None" in err
    assert not config.LEDGER_PATH.exists() and live_env.models == []


def test_sc12b_gate3_prints_the_credit_plan_before_any_refusal(live_env: Fakes, monkeypatch: pytest.MonkeyPatch,
                                                                capsys: pytest.CaptureFixture[str]) -> None:
    """At 63 credits used (87 left) ten first lookups plan 80 on first passes against a ceiling of
    100: Gate 3 prints the plan and a note and asks for proceed. Below the plan it prints the plan
    and then stops and asks (decision 1).

    Mutation: sc12b_batch_credit_refusal_first (the batch ledger check refuses at the ceiling
    before Gate 3 prints anything)."""
    _sc12b_ready(monkeypatch, runs_per_input=10)
    monkeypatch.setattr(config, "BUILD_CREDIT_CAP", 150)  # the cap this scenario was written for
    _used_credits(63)
    assert _sc12b(monkeypatch, "no\n") == cli.EXIT_REFUSED
    out = capsys.readouterr().out
    assert "Gate 3: 10 first lookups; Tavily credits planned on first passes 80, ceiling 100" in out
    assert "the build has 87 left" in out and "cover the planned 80 but not the ceiling of 100" in out
    assert out.index("Gate 3: 10 first lookups") < out.index("Type proceed")

    _used_credits(40)  # 103 used, 47 left
    assert _sc12b(monkeypatch, "proceed\n") == cli.EXIT_REFUSED
    captured = capsys.readouterr()
    assert "Gate 3: 10 first lookups; Tavily credits planned on first passes 80" in captured.out
    assert "the build has 47 left" in captured.out and "stop and ask" in captured.out
    assert "Type proceed" not in captured.out and "decision 1" in captured.err
    assert live_env.models == [] and live_env.tavily == []


def test_sc12b_reports_the_stop_line_after_the_last_run(live_env: Fakes, monkeypatch: pytest.MonkeyPatch,
                                                        capsys: pytest.CaptureFixture[str]) -> None:
    """A run that carries this work past the stop line, the last run included, ends the batch with
    "stop and report", a refused exit code and the flag in the summary.

    Mutation: sc12b_stop_line_not_checked_after_run (nothing is checked after a run, so the last
    run passes the line silently)."""
    _sc12b_ready(monkeypatch)
    monkeypatch.setattr(config, "SC12_STOP_USD", 0.000001)  # the one fake run passes it
    assert _sc12b(monkeypatch, "proceed\n") == cli.EXIT_REFUSED
    err = capsys.readouterr().err
    assert "passed the 0.00 USD stop line after run 1" in err and "stop and report" in err
    summary = json.loads(next(config.EVAL_DIR.glob("summary-sc12b-*.json")).read_text(encoding="utf-8"))
    assert summary["stop_line_passed"] is True and summary["work_spend_usd"] > config.SC12_STOP_USD
    assert len(summary["this_batch"]) == 1 and live_env.models
