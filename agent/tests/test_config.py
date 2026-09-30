"""No model ID or dollar figure lives outside config.py, and config is consistent.

Mutations that turn these red:
- a line such as `MODEL = "claude-haiku-4-5"` or a float equal to a cap
  (for example `cap = 0.15`) added to any agent/*.py outside config.py and
  agent/tests/: test_no_inline_model_ids_or_prices lists it.
- the claude pattern, the $ pattern or the float check removed from the
  scanner: test_scanner_catches_each_kind fails.
- the jev pattern removed or narrowed to versioned IDs: test_scanner_catches_jev_model_ids
  fails; zero kept among the money values (Jev's free output): every 0.0 in the
  package is listed and test_zero_price_is_not_a_money_value fails.
- a MODEL_FOR entry naming a model missing from PRICES_PER_MTOK, or a
  loop_guard not equal to search + fetch + 2, in config.py: the matching
  consistency test fails.
"""

from __future__ import annotations

import io
import re
import tokenize
from pathlib import Path

from agent import config

AGENT_DIR = config.REPO_ROOT / "agent"

MODEL_ID_RE = re.compile(r"claude-[a-z0-9]|\bjev-[a-z0-9]", re.IGNORECASE)
DOLLAR_RE = re.compile(r"\$\s?\d")

# A float literal that happens to equal a price but is not one can opt out
# with this marker on its line. Review every use.
NOT_A_PRICE_MARKER = "config-scan: not a price"


def _money_values() -> set[float]:
    values: set[float] = set()
    for prices in config.PRICES_PER_MTOK.values():
        values.update(prices.values())
    values.update(config.REPLAY_PRICES.values())
    values.update(config.RUN_CAP_USD.values())
    values.update(
        {
            config.REPLAY_RUN_CAP_USD,
            config.BUILD_CAP_USD,
            config.RESEARCH_BUDGET_USD,
            config.RETRY_TYPICAL_USD,
            config.TAVILY_CREDIT_USD_PAYG,
            config.ANTHROPIC_WEB_SEARCH_USD_PER_SEARCH,
        }
    )
    # A free price (Jev's output) is not a figure anyone could inline: 0.0 is
    # every float default and sum start in the package.
    return {float(v) for v in values if v}


def scan_source(text: str, money: set[float]) -> list[tuple[int, str]]:
    """Return (line, reason) for each model ID, $ figure or price float."""
    hits: list[tuple[int, str]] = []
    lines = text.splitlines()
    for number, line in enumerate(lines, start=1):
        if MODEL_ID_RE.search(line):
            hits.append((number, "model ID"))
        if DOLLAR_RE.search(line):
            hits.append((number, "dollar figure"))
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(text).readline))
    except (tokenize.TokenError, SyntaxError):
        return hits
    for tok in tokens:
        if tok.type != tokenize.NUMBER:
            continue
        literal = tok.string.replace("_", "").lower()
        if literal.startswith(("0x", "0o", "0b")):
            continue  # hex, octal and binary are ints; "0xe1" is not a float
        if not ("." in literal or "e" in literal) or literal.endswith("j"):
            continue
        line = lines[tok.start[0] - 1] if tok.start[0] - 1 < len(lines) else ""
        if float(literal) in money and NOT_A_PRICE_MARKER not in line:
            hits.append((tok.start[0], f"float {tok.string} equals a price or cap"))
    return hits


def _scanned_files() -> list[Path]:
    tests_dir = AGENT_DIR / "tests"
    config_file = AGENT_DIR / "config.py"
    return sorted(
        path
        for path in AGENT_DIR.rglob("*.py")
        if path != config_file and not path.is_relative_to(tests_dir)
    )


def test_no_inline_model_ids_or_prices() -> None:
    money = _money_values()
    found = []
    for path in _scanned_files():
        for line, reason in scan_source(path.read_text(encoding="utf-8"), money):
            found.append(f"{path.relative_to(config.REPO_ROOT)}:{line}: {reason}")
    assert not found, "import these from agent/config.py instead:\n" + "\n".join(found)


def test_scanner_catches_each_kind() -> None:
    money = _money_values()
    model = "clau" + "de-haiku-4-5"  # split so this file holds no model ID
    source = "\n".join(
        [
            "x = 1",
            f"MODEL = '{model}'",
            "note = 'costs $" + "3 a run'",
            f"cap = {config.RUN_CAP_USD['cheap']!r}",
            f"price = {config.PRICES_PER_MTOK[config.HAIKU]['output']!r}",
            "count = 5",  # an int, not a price
            "marker = 0xE1",  # hex with an "e" in it, not a float
            f"ok = {config.BUILD_CAP_USD!r}  # {NOT_A_PRICE_MARKER}",
        ]
    )
    hits = scan_source(source, money)
    assert [line for line, _ in hits] == [2, 3, 4, 5]


def test_scanner_catches_jev_model_ids() -> None:
    money = _money_values()
    ids = ["je" + "v-1.13.0", "je" + "v-latest", "je" + "v-preview", "JE" + "V-2"]  # split: no model ID here
    source = "\n".join([f"MODEL = '{model}'" for model in ids] + ["name = 'jev_threshold'", "n = 'a jev reply'"])
    assert [line for line, _ in scan_source(source, money)] == [1, 2, 3, 4]


def test_zero_price_is_not_a_money_value() -> None:
    assert config.PRICES_PER_MTOK[config.JEV_MODEL]["output"] == 0
    money = _money_values()
    assert 0.0 not in money
    assert config.PRICES_PER_MTOK[config.JEV_MODEL]["input"] in money
    source = "\n".join(["total = 0.0", f"price = {config.PRICES_PER_MTOK[config.JEV_MODEL]['input']!r}"])
    assert [line for line, _ in scan_source(source, money)] == [2]


def test_scan_covers_the_agent_package() -> None:
    files = _scanned_files()
    assert AGENT_DIR / "cli.py" in files
    assert AGENT_DIR / "config.py" not in files
    assert not any(path.is_relative_to(AGENT_DIR / "tests") for path in files)


def test_model_for_names_only_priced_models() -> None:
    assert set(config.MODEL_FOR) == set(config.MODES)
    for mode, steps in config.MODEL_FOR.items():
        for step, model in steps.items():
            assert model in config.PRICES_PER_MTOK, f"{mode}.{step} uses unpriced {model}"


def test_research_loop_guard_is_search_plus_fetch_plus_two() -> None:
    for name, limits in config.RESEARCH_LIMITS.items():
        assert limits["loop_guard"] == limits["search"] + limits["fetch"] + 2, name
