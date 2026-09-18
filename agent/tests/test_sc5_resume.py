"""SC5: a paused run resumes in a different process (PLAN section 5).

Two `advisor` child processes, both started through
helpers.spawn_offline_child (network guard on, keys scrubbed), share only the
data folder under tmp_path (checkpoints, ledger, pages, briefs) and the
cassette. The recorded flo case skips until its privacy approval and fails in
gate mode; the synthetic FLO variant runs either way.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

import pytest

from agent import config
from agent.ledger import Ledger
from agent.tests.helpers import GUARD_MARKER, load_case, plate_for_sha256, spawn_offline_child

PROCESS = re.compile(r"\(process (\d+), mode replay\)")
THREAD = re.compile(r"^advisor ask: thread (\S+) ", re.MULTILINE)


def _advisor(args: list[str], data_dir: Path, cassette: Path):
    """One `python -m agent.cli` child with its own data folder and the cassette in the environment."""
    return spawn_offline_child(
        ["-m", "agent.cli", *args],
        kind="python",
        env_extra={"ADVISOR_DATA_DIR": str(data_dir), config.ENV_CASSETTE: str(cassette),
                   config.ENV_MODE: None, config.ENV_GATE: None},
    )


@pytest.mark.parametrize("case", ["flo", "synthetic/flo_three_candidates"])
def test_resume_in_a_new_process(case: str, tmp_path: Path) -> None:
    """Mutations: sc5_cli_in_memory_checkpointer (process 2 finds no thread);
    sc5_resume_other_checkpoint_path; cli_resume_ignores_identity_flags (the
    plate's text is confirmed instead of the owner's answer)."""
    cassette = load_case(case)
    data_dir = tmp_path / "data"
    inp = cassette.input
    photo = plate_for_sha256(inp["plate_sha256"])

    ask = _advisor(["ask", "--symptom", inp["symptom"], "--photo", str(photo)], data_dir, cassette.path)
    assert ask.returncode == 0, ask.stderr
    assert GUARD_MARKER in ask.stderr
    assert "paused at confirm_identity" in ask.stdout
    thread_id = THREAD.search(ask.stdout).group(1)
    payload = json.loads(next(line for line in ask.stdout.splitlines() if line.startswith("interrupt: "))[11:])
    assert payload["identity"]["model"]  # the clear plate was read

    answer = cassette.resume
    flags = ["--manufacturer", answer["identity"]["manufacturer"], "--model", answer["identity"]["model"],
             "--serial", answer["identity"]["serial"] or "", "--date", answer["identity"]["manufacture_date"] or ""]
    if answer["observed_code"] is not None:
        flags += ["--code", answer["observed_code"]]
    resume = _advisor(["resume", thread_id, *flags], data_dir, cassette.path)
    assert resume.returncode == 0, resume.stderr
    assert GUARD_MARKER in resume.stderr
    assert "status: ok" in resume.stdout

    pids = {int(PROCESS.search(p.stdout).group(1)) for p in (ask, resume)}
    assert len(pids) == 2 and os.getpid() not in pids

    html = Path(re.search(r"^html: (.+)$", resume.stdout, re.MULTILINE).group(1))
    assert html.is_file() and html.is_relative_to(data_dir)
    assert (data_dir / "checkpoints.sqlite").is_file()
    ledger = Ledger(data_dir / "ledger" / "replay_ledger.sqlite")
    charges = [r["node"] for r in ledger.rows(thread_id) if r["kind"] == "charge"]
    assert charges.count("read_plate") == 1
    assert "synthesize" in charges
    record = json.loads((data_dir / "runs" / f"{thread_id}.json").read_text(encoding="utf-8"))
    assert record["status"] == "ok" and record["thread_id"] == thread_id

    # The confirmed identity is the owner's answer, not the plate text, read
    # from the thread's last checkpoint (the run record has no identity).
    from agent.graph import build_graph, open_checkpointer, thread_config

    final = build_graph(open_checkpointer(data_dir / "checkpoints.sqlite")).get_state(thread_config(thread_id))
    expected = {k: (v or None) for k, v in answer["identity"].items()}
    assert final.values["identity"] == expected
    assert final.values["identity"] != {k: payload["identity"].get(k) for k in expected}
    assert final.values["observed_code"] == answer["observed_code"]
