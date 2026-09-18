"""CLI tracing policy: tracing is off unless opted in (PRD:113, decision 31).

Mutations that turn these red:
- scrub_tracing_env() calls deleted from agent/cli.py (or moved after a
  langsmith lookup, which caches the environment): the child reports True with
  tracing not opted in.
- the ADVISOR_TRACING == "1" early return deleted: the opted in child reports
  False.

The command rules of PLAN 8.13 (output paths, modes, cassettes, the ledger)
are tested in test_cli.py.
"""

from __future__ import annotations

import json

from agent import cli, config
from agent.tests.helpers import spawn_offline_child

REPO_ROOT = config.REPO_ROOT

CHILD_SCRIPT = """
import json
from agent import cli
rc = cli.main(["eval", "sc3b"])  # refused before any engine; the scrub still runs
import langsmith.utils
print(json.dumps({
    "rc": rc,
    "tracing": langsmith.utils.tracing_is_enabled(),
    "opted_in": cli.TRACING_OPTED_IN,
    "hidden": cli.hide_trace_inputs(
        {"photo": {"path": "p.jpg", "sha256": "x"}, "property_id": "prop-1",
         "appliance_id": "app-1", "history_hits": [{"a": 1}], "symptom": "no heat"}
    ),
}))
"""


def _run_child(extra_env: dict[str, str | None]) -> dict:
    # The dummy tracing variables are planted after the helper's scrub, so the
    # child sees them and only the CLI's own scrub can remove them.
    proc = spawn_offline_child(
        ["-c", CHILD_SCRIPT],
        kind="python",
        env_extra={
            "LANGCHAIN_TRACING_V2": "true",
            "LANGSMITH_API_KEY": "dummy-not-a-key",
            config.ENV_TRACING: None,  # an opt in from the shell must not leak in
            **extra_env,
        },
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


def test_cli_scrubs_tracing_unless_opted_in() -> None:
    off = _run_child({})
    assert off["rc"] == cli.EXIT_REFUSED
    assert off["tracing"] is False
    assert off["opted_in"] is False

    on = _run_child({config.ENV_TRACING: "1"})
    assert on["tracing"] is True
    assert on["opted_in"] is True
    hidden = on["hidden"]
    for field in ("photo", "property_id", "appliance_id", "history_hits"):
        assert hidden[field] == cli.HIDDEN_PLACEHOLDER, field
    assert hidden["symptom"] == "no heat"


def test_scrub_removes_every_tracing_variable() -> None:
    environ = {name: "true" for name in config.TRACING_ENV_VARS}
    environ["UNRELATED"] = "keep"
    assert cli.scrub_tracing_env(environ) is False
    assert environ == {"UNRELATED": "keep"}

    opted = {name: "true" for name in config.TRACING_ENV_VARS}
    opted[config.ENV_TRACING] = "1"
    assert cli.scrub_tracing_env(opted) is True
    assert all(opted[name] == "true" for name in config.TRACING_ENV_VARS)
