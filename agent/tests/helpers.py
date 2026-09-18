"""Shared test helpers: the environment scrub and the one child process launcher.

Every child process a test starts goes through `spawn_offline_child`
(PLAN sections 5 and 8.14). pytest-socket only guards the pytest process, so
each child gets its own network guard: `netguard/sitecustomize.py` on
PYTHONPATH for Python, `harness/netguard.mjs` on `--import` for Node.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from collections.abc import Mapping, MutableMapping, Sequence
from pathlib import Path
from typing import Literal

from agent import config

TESTS_DIR = Path(__file__).resolve().parent
NETGUARD_DIR = TESTS_DIR / "netguard"
NODE_GUARD = TESTS_DIR / "harness" / "netguard.mjs"

# Variable names removed from every test process and every child.
SCRUB_PREFIXES = ("ANTHROPIC_", "LANGSMITH_", "LANGCHAIN_")
SCRUB_NAMES = ("TAVILY_API_KEY",)

# The guards print these texts. They are repeated here (not imported) because
# importing sitecustomize.py would patch sockets in the importing process.
GUARD_MARKER = "advisor offline guard: guard loaded"
GUARD_ERROR_TEXT = "advisor offline guard: network access is blocked"

DEFAULT_CHILD_TIMEOUT_S = 120


def should_scrub(name: str) -> bool:
    """True for a key, tracing or gateway variable that tests must not see."""
    return name in SCRUB_NAMES or name.startswith(SCRUB_PREFIXES)


def scrub_environ(env: MutableMapping[str, str]) -> list[str]:
    """Delete scrubbed variables from `env` in place and set strict msgpack."""
    removed = sorted(name for name in env if should_scrub(name))
    for name in removed:
        del env[name]
    env["LANGGRAPH_STRICT_MSGPACK"] = "true"
    return removed


def resolve_node_bin() -> str:
    """Find node: NODE_BIN, then config.DEFAULT_NODE_BIN, then PATH."""
    from_env = os.environ.get(config.ENV_NODE_BIN)
    if from_env:
        return from_env
    if config.DEFAULT_NODE_BIN.exists():
        return str(config.DEFAULT_NODE_BIN)
    on_path = shutil.which("node")
    if on_path:
        return on_path
    raise FileNotFoundError(
        f"node not found: set {config.ENV_NODE_BIN}, install to "
        f"{config.DEFAULT_NODE_BIN}, or put node on PATH"
    )


def child_env(
    kind: Literal["python", "node"],
    env_extra: Mapping[str, str | None] | None = None,
) -> dict[str, str]:
    """Build a scrubbed environment for a child. A None value in env_extra unsets."""
    env = dict(os.environ)
    scrub_environ(env)
    if kind == "python":
        parts = [str(NETGUARD_DIR), str(config.REPO_ROOT)]
        if env.get("PYTHONPATH"):
            parts.append(env["PYTHONPATH"])
        env["PYTHONPATH"] = os.pathsep.join(parts)
    # Applied after the scrub on purpose: a test may plant a dummy key to prove
    # that code under test removes it (decision 31).
    for name, value in (env_extra or {}).items():
        if value is None:
            env.pop(name, None)
        else:
            env[name] = value
    return env


def spawn_offline_child(
    argv: Sequence[str],
    *,
    kind: Literal["python", "node"],
    env_extra: Mapping[str, str | None] | None = None,
    cwd: str | Path | None = None,
    timeout: float = DEFAULT_CHILD_TIMEOUT_S,
) -> subprocess.CompletedProcess[str]:
    """Run a guarded child process and capture its output as text.

    `argv` is everything after the interpreter: for Python, for example
    ["-m", "agent.cli", "ask", ...]; for Node, a script path and its arguments.
    The interpreter is this venv's Python or the resolved node binary.
    """
    if kind == "python":
        command = [sys.executable, *argv]
    elif kind == "node":
        command = [resolve_node_bin(), "--import", NODE_GUARD.resolve().as_uri(), *argv]
    else:
        raise ValueError(f"unknown child kind: {kind!r}")
    return subprocess.run(
        [str(part) for part in command],
        env=child_env(kind, env_extra),
        cwd=str(cwd) if cwd is not None else str(config.REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


# ---------------------------------------------------------------------------
# SC1b payloads and the schema layer adapter (PLAN section 5, SC1b rows)
# ---------------------------------------------------------------------------

SC1B_FIXTURES = TESTS_DIR / "fixtures"
SC1B_V1_FILE = "sc1b_payloads.json"
SC1B_EDGE_FILE = "sc1b_edge_payloads.json"
SC1B_PENDING = "v1 guardrail payloads pending privacy approval"
# The code built search trail the adapter's refusal blocks use for `searched`.
SC1B_SEARCHED = ["Aquarest ZX-9000 service manual", "Aquarest ZX-9000 error codes"]
_SC1B_TIERS = ("manufacturer", "dealer", "forum")


def sc1b_v1_payload_file() -> Path | None:
    """The v1 guardrail payload file: tracked once approved, else staged, else None."""
    for path in (SC1B_FIXTURES / SC1B_V1_FILE, config.STAGING_DIR / SC1B_V1_FILE):
        if path.is_file():
            return path
    return None


def sc1b_payloads() -> list[dict]:
    """Every SC1b payload as {id, payload, origin}: v1 guardrail ones first, then edge ones.

    Parsed the way JavaScript parses JSON (NaN and Infinity are errors).
    """
    from agent.rules.v1_parity import loads_strict

    out: list[dict] = []
    v1_file = sc1b_v1_payload_file()
    files = ([(v1_file, "v1")] if v1_file else []) + [(SC1B_FIXTURES / SC1B_EDGE_FILE, "edge")]
    for path, origin in files:
        doc = loads_strict(path.read_text(encoding="utf-8"))
        for row in doc["payloads"]:
            out.append({"id": row["id"], "payload": row["payload"], "origin": origin})
    return out


def _absent_or_null(obj: dict, key: str) -> bool:
    return obj.get(key) is None


def _draft_step(step: object) -> object:
    if not isinstance(step, dict):
        return step
    out = dict(step)
    if _absent_or_null(out, "detail"):
        out["detail"] = ""
    out.setdefault("safety_flag", False)
    return out


def _draft_candidate(cand: object) -> object:
    if not isinstance(cand, dict):
        return cand
    out = dict(cand)
    out.setdefault("code", None)
    out.setdefault("who", None)
    out.setdefault("confirmed", False)
    for key in ("why_shown", "evidence"):
        if _absent_or_null(out, key):
            out[key] = ""
    return out


def split_sc1b_payload(
    payload: object, *, searched: Sequence[str] = SC1B_SEARCHED
) -> tuple[object, list[dict], object]:
    """Split a v1 shaped payload into (BriefDraft dict, source registry, v1 view).

    Only the fields `BriefDraft` shares with v1 (status, try_first, candidates,
    warranty, warranty_caution, happened_before, no_reliable_answer minus
    `searched`) keep their raw values, so the schema layer, not the adapter,
    decides them. Absence is spelled the way the draft spells it ("" for free
    text, [] for lists, False for flags, None for blocks). The registry is
    code built from the payload's sources, with a placeholder example.com URL
    where the payload's URL is unusable, so indexes keep their meaning. The v1
    view is the payload as v1 would see it after the same split: code built
    sources, code built `searched`, the warranty age statement placeholder, and
    no model supplied identity or observed code.
    """
    if not isinstance(payload, dict):
        return payload, [], payload
    raw_sources = payload.get("sources") if isinstance(payload.get("sources"), list) else []
    # Each registry snippet prints the codes of the candidates that cite it, so
    # rule 4 (code grounding, Phase 3; v1 has no counterpart) passes and the
    # schema layer and the parity layer decide the comparison.
    from agent.rules.v1_parity import is_integer

    cited_codes: dict[int, list[str]] = {}
    for cand in payload.get("candidates") if isinstance(payload.get("candidates"), list) else []:
        if isinstance(cand, dict) and isinstance(cand.get("code"), str) and is_integer(cand.get("source_index")):
            cited_codes.setdefault(int(cand["source_index"]), []).append(cand["code"])
    registry: list[dict] = []
    view_sources: list[dict] = []
    tiers: list[dict] = []
    for i, src in enumerate(raw_sources):
        src = src if isinstance(src, dict) else {}
        url = src.get("url")
        if not (isinstance(url, str) and url[:8].lower().startswith(("http://", "https://"))):
            url = f"https://sources.example.com/{i}"
        title = src.get("title") if isinstance(src.get("title"), str) else None
        tier = src.get("tier") if src.get("tier") in _SC1B_TIERS else "forum"
        registry.append({
            "source_id": f"src-{i}", "url": url, "host": url.split("/")[2].lower(), "title": title,
            "retrieved_at": "2026-09-18T00:00:00Z", "origin": "search", "text_sha256": None,
            "snippet": " ".join(f"Code {code} ." for code in cited_codes.get(i, [])),
        })
        view_sources.append({"url": url, "title": title, "tier": tier})
        tiers.append({"source_index": i, "tier": tier, "authorship_quote": ""})

    matched = payload.get("matched_identity") if isinstance(payload.get("matched_identity"), str) else None
    try_first = payload.get("try_first", [])
    candidates = payload.get("candidates", [])
    warranty = payload.get("warranty")
    hb = payload.get("happened_before")
    nra = payload.get("no_reliable_answer")

    draft: dict = {
        "status": payload.get("status"),
        "matched_identity": matched,
        "warranty_caution": payload.get("warranty_caution"),
        "happened_before": hb,
        "try_first": [_draft_step(s) for s in try_first] if isinstance(try_first, list) else try_first,
        "candidates": [_draft_candidate(c) for c in candidates] if isinstance(candidates, list) else candidates,
        "warranty": warranty,
        "no_reliable_answer": nra,
        "upgrade_options": [],
        "maintenance_due": [],
        "source_tiers": tiers,
    }
    if isinstance(hb, dict):
        hb = dict(hb)
        hb.setdefault("matches", False)
        for key in ("summary", "record_id"):
            if _absent_or_null(hb, key):
                hb[key] = ""
        draft["happened_before"] = hb
    if isinstance(warranty, dict):
        warranty = {k: v for k, v in warranty.items() if k != "age_statement"}
        for key in ("cautions", "verify"):
            if _absent_or_null(warranty, key):
                warranty[key] = []
        draft["warranty"] = warranty
    if isinstance(nra, dict):
        nra = {k: v for k, v in nra.items() if k != "searched"}
        if _absent_or_null(nra, "found"):
            nra["found"] = []
        draft["no_reliable_answer"] = nra

    view = {**payload, "sources": view_sources, "matched_identity": matched, "observed_code": None}
    if isinstance(payload.get("warranty"), dict):
        view["warranty"] = {**payload["warranty"], "age_statement": ""}
    if isinstance(payload.get("no_reliable_answer"), dict):
        view["no_reliable_answer"] = {**payload["no_reliable_answer"], "searched": list(searched)}
    return draft, registry, view


# ---------------------------------------------------------------------------
# Replay cassettes and whole graph runs (Phase 2)
# ---------------------------------------------------------------------------

PENDING_REASON = "cassette pending privacy approval"
SYNTHETIC_CASSETTE_DIR = TESTS_DIR / "cassettes" / "synthetic"
DEMO_ASSETS_DIR = config.REPO_ROOT / "demo-assets"


def gate_requires_cassettes() -> bool:
    """True under ADVISOR_GATE=2 or later, where a pending cassette fails instead of skipping."""
    raw = os.environ.get(config.ENV_GATE, "").strip()
    return raw.isdigit() and int(raw) >= 2


def resolve_cassette_path(case: str) -> Path:
    """The cassette for a case, or skip (fail in gate mode) while it awaits approval.

    "synthetic/<name>" reads a synthetic cassette tracked in the repo. Any
    other case is v1 derived: config.CASSETTE_DIR/<case>.json once approved,
    else config.STAGING_DIR/cassettes/<case>.json.
    """
    import pytest

    if case.startswith("synthetic/"):
        return SYNTHETIC_CASSETTE_DIR / f"{case.split('/', 1)[1]}.json"
    for path in (config.CASSETTE_DIR / f"{case}.json", config.STAGING_DIR / "cassettes" / f"{case}.json"):
        if path.is_file():
            return path
    if gate_requires_cassettes():
        pytest.fail(f"{case}: {PENDING_REASON}; a skipped SC2 case cannot satisfy the gate")
    pytest.skip(PENDING_REASON)


def load_case(case: str):
    """Resolve and load a cassette (agent.replay.cassettes.Cassette)."""
    from agent.replay.cassettes import load_cassette

    return load_cassette(resolve_cassette_path(case))


def plate_for_sha256(sha: str) -> Path:
    """The demo-assets photo whose bytes hash to sha (read only)."""
    import hashlib

    for path in sorted(DEMO_ASSETS_DIR.glob("*.jpg")):
        if hashlib.sha256(path.read_bytes()).hexdigest() == sha:
            return path
    raise FileNotFoundError(f"no demo-assets photo has sha256 {sha}")


def replay_ctx(tmp_path: Path, cassette, run_id: str = "run-1"):
    """A replay RunContext whose files all sit under tmp_path."""
    from agent.state import RunContext

    return RunContext(
        run_id=run_id, mode="replay", ledger_path=tmp_path / "ledger" / "replay_ledger.sqlite",
        registry_path=tmp_path / "registry.sqlite", graph_path=tmp_path / "replay_graph.json",
        pages_dir=tmp_path / "pages", cassette=cassette,
        caps=cassette.caps if hasattr(cassette, "caps") else None,
    )


def cassette_input(cassette, tmp_path: Path) -> dict:
    """The graph input for a cassette's `advisor ask`, with the page written under tmp_path."""
    from agent.nodes.intake import intake_input

    inp = cassette.input
    photo = str(plate_for_sha256(inp["plate_sha256"])) if inp["plate_sha256"] else None
    data = intake_input(symptom=inp["symptom"], photo_path=photo, identity=inp["identity"])
    data["html_path"] = str(tmp_path / "brief.html")
    return data


def resume_command(cassette, **override):
    """Command(resume=...) carrying the cassette's recorded confirmation."""
    from langgraph.types import Command

    value = cassette.resume
    value.update(override)
    return Command(resume=value)


def run_case(cassette, tmp_path: Path, *, resume: bool = True, thread_id: str = "thread-1",
             checkpoint: Path | None = None):
    """Run a cassette through the real graph: ask, then resume with its recorded answer.

    Returns (graph, ctx, [RunOutcome of ask, RunOutcome of resume if any]).
    """
    from agent.graph import build_graph, open_checkpointer, run_until_pause_or_end

    ctx = replay_ctx(tmp_path, cassette, run_id=thread_id)
    graph = build_graph(open_checkpointer(checkpoint or tmp_path / "checkpoints.sqlite"))
    outcomes = [run_until_pause_or_end(graph, cassette_input(cassette, tmp_path), ctx, thread_id)]
    if resume and outcomes[0].paused and cassette.resume is not None:
        outcomes.append(run_until_pause_or_end(graph, resume_command(cassette), ctx, thread_id))
    return graph, ctx, outcomes


def assert_run_record_matches(ctx, state: dict) -> dict:
    """The persisted run record carries the final state's outcome and the ledger's spend."""
    import json

    import pytest

    from agent.ledger import Ledger
    from agent.nodes.persist import run_record_path

    record = json.loads(run_record_path(ctx, ctx.run_id).read_text(encoding="utf-8"))
    for key in ("status", "refusal_origin", "stop_reason"):
        assert record[key] == state.get(key), key
    assert record["cost_usd"] == pytest.approx(Ledger(ctx.ledger_path).run_total(ctx.run_id))
    assert record["latency_s"] == pytest.approx(sum(state["latency"].values()))
    return record
