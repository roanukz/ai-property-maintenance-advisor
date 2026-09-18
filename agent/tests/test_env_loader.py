"""The .env loader and key hygiene of the live commands (PRD Part A rules 1 and 8).

Driven through `advisor check-schema --live` and `advisor ask/resume --live`
on fake clients with the network blocked; the fake ChatAnthropic records the
key variables it can see when it is built, the way the real client reads
them. No test prints a key: values are compared, never shown.

Every test names the mutations in mutations.toml that turn it red.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

from agent import cli, config, models
from agent.ledger import Ledger
from agent.replay import privacy_diff
from agent.tests.test_live_path import (  # noqa: F401  (live_env is a fixture)
    FAKE_ANTHROPIC,
    FAKE_TAVILY,
    NRA_DRAFT,
    CheckedChat,
    LiveFakes,
    fake_key,
    flo_data,
    live_env,
    run_flo_live,
    type_line,
)

WATCHED = ("ANTHROPIC_API_KEY", "TAVILY_API_KEY", "LANGSMITH_API_KEY", "UNRELATED_SECRET")
FAKE_LANGSMITH = fake_key("lsv2" + "_pt_", "langsmith")
FAKE_OTHER = fake_key("other-", "unrelated")


def check_schema_env(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
                     dotenv: str) -> tuple[int, dict[str, str | None], str]:
    """Run check-schema with this .env; return the exit code, what the client saw, and stderr."""
    config.ENV_FILE.write_text(dotenv, encoding="utf-8")
    seen: dict[str, str | None] = {}

    def factory(**kwargs: Any) -> CheckedChat:
        seen.update({name: os.environ.get(name) for name in WATCHED})
        return CheckedChat(responses=[{"structured": NRA_DRAFT, "usage": {"input_tokens": 1, "output_tokens": 1}}],
                           model=kwargs["model"], max_tokens=kwargs["max_tokens"],
                           ledger_path=str(config.LEDGER_PATH))

    monkeypatch.setattr(models, "ChatAnthropic", factory)
    type_line(monkeypatch, "proceed\n")
    code = cli.main(["check-schema", "--live", "--new-ledger"])
    out, err = capsys.readouterr()
    assert FAKE_ANTHROPIC not in out + err and FAKE_TAVILY not in out + err
    return code, seen, err


DOTENVS = {
    "export_and_quotes": (
        f'# live keys\n\nexport ANTHROPIC_API_KEY="{FAKE_ANTHROPIC}"\n'
        f"TAVILY_API_KEY='{FAKE_TAVILY}'   # single quoted, then a comment\n",
        {"ANTHROPIC_API_KEY": FAKE_ANTHROPIC, "TAVILY_API_KEY": FAKE_TAVILY},
    ),
    "unquoted_inline_comment": (
        f"ANTHROPIC_API_KEY={FAKE_ANTHROPIC} # the build key\nTAVILY_API_KEY = {FAKE_TAVILY}\n",
        {"ANTHROPIC_API_KEY": FAKE_ANTHROPIC, "TAVILY_API_KEY": FAKE_TAVILY},
    ),
    "hash_inside_quotes_kept": (
        f'ANTHROPIC_API_KEY="{FAKE_ANTHROPIC} #kept"\n',
        {"ANTHROPIC_API_KEY": f"{FAKE_ANTHROPIC} #kept", "TAVILY_API_KEY": None},
    ),
    "other_names_ignored": (
        f"ANTHROPIC_API_KEY={FAKE_ANTHROPIC}\nUNRELATED_SECRET={FAKE_OTHER}\nLANGSMITH_API_KEY={FAKE_LANGSMITH}\n",
        {"ANTHROPIC_API_KEY": FAKE_ANTHROPIC, "UNRELATED_SECRET": None, "LANGSMITH_API_KEY": None},
    ),
}


@pytest.mark.parametrize("case", sorted(DOTENVS))
def test_dotenv_parses_comments_quotes_and_export(case: str, live_env: Path, monkeypatch: pytest.MonkeyPatch,
                                                  capsys: pytest.CaptureFixture[str]) -> None:
    """Mutations: env_export_not_stripped; env_quotes_kept; env_inline_comment_kept;
    env_reads_any_name (a stray .env variable reaches the process); env_tracing_names_always
    (LANGSMITH_* read without ADVISOR_TRACING=1)."""
    dotenv, expected = DOTENVS[case]
    code, seen, err = check_schema_env(monkeypatch, capsys, dotenv)
    assert code == cli.EXIT_OK, err
    for name, value in expected.items():
        assert seen.get(name) == value, name
    # The command's keys are gone from the environment once it ends.
    assert all(name not in os.environ for name in WATCHED)


def test_langsmith_keys_read_only_when_tracing_is_opted_in(live_env: Path, monkeypatch: pytest.MonkeyPatch,
                                                           capsys: pytest.CaptureFixture[str]) -> None:
    """Mutation: env_tracing_names_never (LANGSMITH_* not read even with ADVISOR_TRACING=1)."""
    import langsmith

    monkeypatch.setenv(config.ENV_TRACING, "1")
    monkeypatch.setattr(cli, "TRACING_OPTED_IN", True)
    langsmith.configure(enabled=False)  # nothing may trace from a test, opted in or not
    dotenv = f"ANTHROPIC_API_KEY={FAKE_ANTHROPIC}\nLANGSMITH_API_KEY={FAKE_LANGSMITH}\n"
    code, seen, err = check_schema_env(monkeypatch, capsys, dotenv)
    assert code == cli.EXIT_OK, err
    assert seen["LANGSMITH_API_KEY"] == FAKE_LANGSMITH
    assert "LANGSMITH_API_KEY" not in os.environ


def test_dotenv_never_overrides_a_set_variable(live_env: Path, monkeypatch: pytest.MonkeyPatch,
                                               capsys: pytest.CaptureFixture[str]) -> None:
    """Mutations: env_dotenv_overrides_environment (.env wins over a set variable);
    env_empty_variable_filled_from_dotenv (an empty variable counts as unset)."""
    from_env = fake_key("sk" + "-ant-" + "api03-", "from the environment")
    monkeypatch.setenv("ANTHROPIC_API_KEY", from_env)
    code, seen, err = check_schema_env(monkeypatch, capsys, f"ANTHROPIC_API_KEY={FAKE_ANTHROPIC}\n")
    assert code == cli.EXIT_OK, err
    assert seen["ANTHROPIC_API_KEY"] == from_env
    assert os.environ["ANTHROPIC_API_KEY"] == from_env  # restored, untouched

    # Documented: a variable set to an empty string is present, so .env does not fill it, and the
    # key counts as missing.
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    code, seen, err = check_schema_env(monkeypatch, capsys, f"ANTHROPIC_API_KEY={FAKE_ANTHROPIC}\n")
    assert code == cli.EXIT_REFUSED and "missing ANTHROPIC_API_KEY" in err
    assert seen == {}


def test_malformed_dotenv_line_refused_by_number_only(live_env: Path, monkeypatch: pytest.MonkeyPatch,
                                                      capsys: pytest.CaptureFixture[str]) -> None:
    """Mutation: env_error_echoes_line (the refusal quotes the malformed line, key and all)."""
    code, seen, err = check_schema_env(monkeypatch, capsys, f"# ok\n{FAKE_ANTHROPIC}\n")
    assert code == cli.EXIT_REFUSED and "line 2 is not NAME=value" in err
    assert seen == {}


def _files_under(root: Path) -> list[Path]:
    return [p for p in root.rglob("*") if p.is_file()]


def test_planted_key_never_written_or_printed(live_env: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                              capsys: pytest.CaptureFixture[str]) -> None:
    """Mutations: env_redaction_off (RedactingStream writes text as given); recorder_capture_not_redacted
    (the capture file keeps a reply that echoes a key); env_keys_left_in_environ (the keys stay in the
    environment after the command)."""
    data = flo_data()
    # A reply that echoes the key, as no model should but a test must assume one might.
    data["research"]["script"][0]["message"]["content"] = f"checking {FAKE_ANTHROPIC}"
    fakes = LiveFakes(data, echo_key=True).install(monkeypatch)
    thread_id, output = run_flo_live(monkeypatch, capsys, fakes)
    assert fakes.keys_seen and "debug client key [redacted]" in output
    # Redacted where each file is written, not only by the backstop scrub afterwards.
    assert "warning: a key value was found" not in output

    for secret in (FAKE_ANTHROPIC, FAKE_TAVILY):
        assert secret not in output
        assert secret not in os.environ.values()
        for path in _files_under(live_env):
            assert secret.encode() not in path.read_bytes(), path
        rows = Ledger(config.LEDGER_PATH).rows()
        assert all(secret not in str(value) for row in rows for value in row.values())
    runs = list((live_env / "runs").glob("*.json"))
    lookups = list((live_env / "lookups").glob("*.jsonl"))
    recordings = [p for p in (live_env / "recordings").glob("live_*.json") if p.name.count(".") == 1]
    assert runs and lookups and recordings  # the files the check covers exist
    for path in recordings:
        text = path.read_text(encoding="utf-8")
        assert not [m for pattern in privacy_diff.KEY_RES.values() for m in pattern.finditer(text)]


def test_tests_never_point_at_the_repo_dotenv() -> None:
    """Mutation conftest_env_file_left_real: conftest's autouse fixture no longer moves
    config.ENV_FILE, so a test that got past a refusal would read the real keys (finding M7)."""
    assert config.ENV_FILE != config.REPO_ROOT / ".env"
    assert not config.ENV_FILE.exists()
