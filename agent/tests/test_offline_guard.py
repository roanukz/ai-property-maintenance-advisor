"""SC1: tests cannot reach the network, see keys, trace, or collect live runs.

Each test names the change (mutation) that must turn it red.
"""

from __future__ import annotations

import ast
import json
import os
import re
import socket
import textwrap
from pathlib import Path

import pytest
from pytest_socket import SocketBlockedError

from agent import config
from agent.tests import helpers
from agent.tests.helpers import GUARD_ERROR_TEXT, GUARD_MARKER, spawn_offline_child

LIVE_DIR = config.REPO_ROOT / "agent" / "live"
TESTS_DIR = Path(__file__).resolve().parent


def test_socket_blocked_in_process() -> None:
    """Mutation: drop --disable-socket from pyproject addopts (connect is refused, not blocked)."""
    with pytest.raises(SocketBlockedError):
        socket.create_connection(("127.0.0.1", 9), timeout=2)


PYTHON_PROBES = (
    "create_connection", "socket.connect", "socket.connect_ex", "getaddrinfo",
    "gethostbyname", "gethostbyname_ex", "gethostbyaddr", "udp sendto", "udp sendmsg",
)


def test_socket_blocked_in_child_python() -> None:
    """Mutation: drop NETGUARD_DIR from the child PYTHONPATH in helpers.child_env,
    or delete any one patch in netguard/sitecustomize.py (connect_ex, a
    gethostby* lookup, sendto or sendmsg): its probe then gets through."""
    code = textwrap.dedent(
        """
        import json, socket
        def udp():
            return socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        results = {}
        for name, attempt in [
            ("create_connection", lambda: socket.create_connection(("127.0.0.1", 9), timeout=2)),
            ("socket.connect", lambda: socket.socket().connect(("127.0.0.1", 9))),
            ("socket.connect_ex", lambda: socket.socket().connect_ex(("127.0.0.1", 9))),
            ("getaddrinfo", lambda: socket.getaddrinfo("localhost", 80)),
            ("gethostbyname", lambda: socket.gethostbyname("localhost")),
            ("gethostbyname_ex", lambda: socket.gethostbyname_ex("localhost")),
            ("gethostbyaddr", lambda: socket.gethostbyaddr("127.0.0.1")),
            ("udp sendto", lambda: udp().sendto(b"x", ("127.0.0.1", 9))),
            ("udp sendmsg", lambda: udp().sendmsg([b"x"], [], 0, ("127.0.0.1", 9))),
        ]:
            try:
                attempt()
                results[name] = "no error"
            except Exception as exc:
                results[name] = str(exc)
        print(json.dumps(results))
        """
    )
    proc = spawn_offline_child(["-c", code], kind="python", timeout=60)
    assert GUARD_MARKER in proc.stderr, proc.stderr
    results = json.loads(proc.stdout.strip().splitlines()[-1])
    assert set(results) == set(PYTHON_PROBES)
    for name, message in results.items():
        assert GUARD_ERROR_TEXT in message, f"{name}: {message}"


# Probe name -> the entry point the guard must name in its error. Unpatched
# https, tls or http2 would still hit a patched lower layer (net), so only
# the named label proves each entry point is patched itself.
NODE_PROBES = {
    "fetch": "fetch", "net.connect": "net.connect",
    "net.Socket.connect": "net.Socket.prototype.connect", "tls.connect": "tls.connect",
    "http.request": "http.request", "http.get": "http.get", "https.request": "https.request",
    "https.get": "https.get", "http2.connect": "http2.connect", "dns.lookup": "dns.lookup",
    "dns.promises.lookup": "dns.promises.lookup", "dns.resolve4": "dns resolve4",
    "dns.promises.resolve4": "dns resolve4", "dns.Resolver.resolve4": "dns resolve4",
    "dns.reverse": "dns reverse", "dgram.createSocket": "dgram.createSocket",
}


def test_network_blocked_in_child_node(tmp_path: Path) -> None:
    """Mutation: drop the --import of netguard.mjs in helpers.spawn_offline_child,
    or delete any one patch in harness/netguard.mjs (https, tls, http2, a dns
    resolver or dgram): its probe then gets through or fails with Node's own error."""
    script = tmp_path / "try_network.mjs"
    script.write_text(
        textwrap.dedent(
            """
            import dgram from "node:dgram";
            import dns from "node:dns";
            import http from "node:http";
            import http2 from "node:http2";
            import https from "node:https";
            import net from "node:net";
            import tls from "node:tls";
            const results = {};
            const quiet = (x) => { if (x && x.on) { x.on("error", () => {}); } return x; };
            async function attempt(name, fn) {
              try { await fn(); results[name] = "no error"; }
              catch (err) { results[name] = String(err && err.message); }
            }
            await attempt("fetch", () => fetch("http://127.0.0.1:9/"));
            await attempt("net.connect", () => quiet(net.connect(9, "127.0.0.1")).destroy());
            await attempt("net.Socket.connect", () => {
              const s = quiet(new net.Socket()); s.connect(9, "127.0.0.1"); s.destroy();
            });
            await attempt("tls.connect", () => quiet(tls.connect(9, "127.0.0.1")).destroy());
            await attempt("http.request", () => quiet(http.request("http://127.0.0.1:9/")).destroy());
            await attempt("http.get", () => quiet(http.get("http://127.0.0.1:9/")).destroy());
            await attempt("https.request", () => quiet(https.request("https://127.0.0.1:9/")).destroy());
            await attempt("https.get", () => quiet(https.get("https://127.0.0.1:9/")).destroy());
            await attempt("http2.connect", () => quiet(http2.connect("http://127.0.0.1:9")).destroy());
            await attempt("dns.lookup", () => dns.lookup("localhost", () => {}));
            await attempt("dns.promises.lookup", () => dns.promises.lookup("localhost"));
            await attempt("dns.resolve4", () => dns.resolve4("localhost", () => {}));
            await attempt("dns.promises.resolve4", () => dns.promises.resolve4("localhost"));
            await attempt("dns.Resolver.resolve4", () => new dns.Resolver().resolve4("localhost", () => {}));
            await attempt("dns.reverse", () => dns.reverse("127.0.0.1", () => {}));
            await attempt("dgram.createSocket", () => quiet(dgram.createSocket("udp4")).close());
            console.log(JSON.stringify(results));
            """
        ),
        encoding="utf-8",
    )
    proc = spawn_offline_child([str(script.resolve())], kind="node", timeout=60)
    assert GUARD_MARKER in proc.stderr, proc.stderr
    results = json.loads(proc.stdout.strip().splitlines()[-1])
    assert set(results) == set(NODE_PROBES)
    for name, message in results.items():
        assert f"{GUARD_ERROR_TEXT} ({NODE_PROBES[name]})" in message, f"{name}: {message}"


def _scrubbed_names_present(env: dict[str, str]) -> list[str]:
    return sorted(name for name in env if helpers.should_scrub(name))


def test_no_keys_or_tracing_in_env() -> None:
    """Mutation: delete the scrub_environ(os.environ) and langsmith.configure calls in conftest,
    with a key or LANGCHAIN_TRACING_V2=true set in the shell."""
    from langsmith.utils import tracing_is_enabled

    assert _scrubbed_names_present(dict(os.environ)) == []
    assert os.environ.get("LANGGRAPH_STRICT_MSGPACK") == "true"
    assert tracing_is_enabled() is False


CONFTEST_TRACING_CHILD = """
import json
from langsmith import utils
before = utils.tracing_is_enabled()  # warms langsmith's cached env lookups, as its plugin does
import agent.tests.conftest
print(json.dumps({"before": before, "after": utils.tracing_is_enabled()}))
"""


def test_conftest_turns_tracing_off_after_a_warmed_cache() -> None:
    """Mutation: delete the module level _turn_tracing_off() call in conftest;
    langsmith's cached "true" then outlives the environment scrub."""
    proc = spawn_offline_child(
        ["-c", CONFTEST_TRACING_CHILD],
        kind="python",
        # Planted after the helper's scrub, so only conftest can undo them.
        env_extra={"LANGSMITH_TRACING": "true", "LANGCHAIN_TRACING_V2": "true"},
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    result = json.loads(proc.stdout.strip().splitlines()[-1])
    assert result == {"before": True, "after": False}


# Names that start a child process without the helper's guard and scrub.
DIRECT_SPAWN_MODULES = {"subprocess", "multiprocessing", "pty"}
DIRECT_SPAWN_OS_CALLS = re.compile(r"^(system|popen|fork|forkpty|spawn\w*|exec\w*|posix_spawn\w*)$")
PYTESTER_SPAWNS = {"run", "popen", "runpytest_subprocess", "spawn", "spawn_pytest"}


def direct_spawns(path: Path) -> list[str]:
    """Every way a test module could start a child without spawn_offline_child."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found += [a.name for a in node.names if a.name.split(".")[0] in DIRECT_SPAWN_MODULES]
        elif isinstance(node, ast.ImportFrom) and node.module:
            if node.module.split(".")[0] in DIRECT_SPAWN_MODULES:
                found.append(node.module)
            if node.module == "os":
                found += [f"os.{a.name}" for a in node.names if DIRECT_SPAWN_OS_CALLS.match(a.name)]
        elif isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            owner, attr = node.value.id, node.attr
            if owner == "os" and DIRECT_SPAWN_OS_CALLS.match(attr):
                found.append(f"os.{attr}")
            elif owner == "pytester" and attr in PYTESTER_SPAWNS:
                found.append(f"pytester.{attr}")
    return [f"{path.name}: {name}" for name in found]


def test_sc5_and_sc1b_children_use_spawn_helper() -> None:
    """Mutation: add `import subprocess` (or an os.system call) to any test module.
    helpers.py and mutate.py start children on purpose and are not test modules."""
    offenders = [hit for path in sorted(TESTS_DIR.glob("test_*.py")) for hit in direct_spawns(path)]
    assert offenders == []
    # SC5 (Phase 2) must start its children, and only through the helper.
    # Mutation: sc5_spawns_directly (test_sc5_resume.py calls subprocess.run).
    sc5 = ast.parse((TESTS_DIR / "test_sc5_resume.py").read_text(encoding="utf-8"))
    calls = [n.func.id for n in ast.walk(sc5) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)]
    assert "spawn_offline_child" in calls
    # The check itself sees each form it bans.
    probe = TESTS_DIR / "helpers.py"
    assert any("subprocess" in hit for hit in direct_spawns(probe))


def test_spawn_helper_scrubs_child_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mutations: skip scrub_environ(env) in helpers.child_env; helpers_scrub_misses_typesafe_key
    (TYPESAFE_API_KEY left off the scrub list)."""
    planted = ("ANTHROPIC_API_KEY", "TAVILY_API_KEY", "LANGSMITH_API_KEY", "LANGCHAIN_TRACING_V2", "LANGSMITH_GATEWAY",
               config.TYPESAFE_KEY_NAME, "TYPESAFE_BASE_URL", "TYPESAFE_LOG_LEVEL")
    for name in planted:
        monkeypatch.setenv(name, "dummy-value-for-scrub-test")
    code = "import json, os; print(json.dumps(dict(os.environ)))"
    proc = spawn_offline_child(["-c", code], kind="python", timeout=60)
    child_env = json.loads(proc.stdout.strip().splitlines()[-1])
    assert _scrubbed_names_present(child_env) == []
    assert [name for name in planted if name in child_env] == []
    assert child_env.get("LANGGRAPH_STRICT_MSGPACK") == "true"


def _imports_agent_live(path: Path) -> bool:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    target = "agent" + ".live"
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            names = [node.module] + [f"{node.module}.{alias.name}" for alias in node.names]
        else:
            continue
        if any(name == target or name.startswith(target + ".") for name in names):
            return True
    return False


def test_default_collection_contains_no_live_tests() -> None:
    """Mutation: add agent/live to testpaths, or write a test marked live or importing agent.live."""
    proc = spawn_offline_child(
        ["-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider"],
        kind="python",
        env_extra={config.ENV_GATE: None},
        cwd=config.REPO_ROOT,
    )
    from agent.tests.conftest import LIVE_MARKER_ERROR

    assert LIVE_MARKER_ERROR not in proc.stdout + proc.stderr
    node_ids = [line for line in proc.stdout.splitlines() if "::" in line]
    assert node_ids, proc.stdout + proc.stderr
    assert any("test_offline_guard.py::" in node_id for node_id in node_ids)
    assert [n for n in node_ids if n.startswith("agent/live/")] == []
    importing = [p.name for p in TESTS_DIR.rglob("test_*.py") if _imports_agent_live(p)]
    assert importing == []


def _run_synthetic_session(pytester: pytest.Pytester, gate: str | None, inventory: Path):
    return spawn_offline_child(
        ["-m", "pytest", "-p", "agent.tests.conftest", "-p", "no:cacheprovider", "-rA"],
        kind="python",
        env_extra={config.ENV_GATE: gate, config.ENV_GATE_INVENTORY: str(inventory)},
        cwd=pytester.path,
    )


def test_gate_mode_fails_on_skipped_required_test(pytester: pytest.Pytester) -> None:
    """Mutation: cut GatePlugin registration in pytest_configure, or drop its
    wasxfail branch, or count only setup skips (`report.when != "call"`, which
    misses pytest.skip() in a test body), or drop the gate summary line."""
    pytester.makepyfile(
        test_synthetic="""
        import pytest

        @pytest.fixture
        def needs_node():
            pytest.skip("synthetic fixture skip")

        @pytest.mark.skip(reason="synthetic skip")
        def test_skipped():
            pass

        def test_body_skip():
            pytest.skip("synthetic body skip")

        def test_fixture_skip(needs_node):
            pass

        @pytest.mark.xfail(reason="synthetic xfail")
        def test_xfailed():
            assert False

        def test_passes():
            pass
        """
    )
    inventory = pytester.path / "inventory.json"
    inventory.write_text(
        json.dumps(
            [
                {"v1_test": 1, "name": "synthetic", "verdict": "port", "phase": 1,
                 "node_ids": ["test_synthetic.py::test_skipped", "test_synthetic.py::test_body_skip",
                              "test_synthetic.py::test_fixture_skip", "test_synthetic.py::test_xfailed",
                              "test_synthetic.py::test_passes"],
                 "approved_obsolete_on": None},
                {"v1_test": 2, "name": "later phase", "verdict": "port", "phase": 2,
                 "node_ids": ["test_absent.py::test_not_written_yet"], "approved_obsolete_on": None},
            ]
        ),
        encoding="utf-8",
    )

    control = _run_synthetic_session(pytester, None, inventory)
    assert control.returncode == 0, control.stdout + control.stderr

    gated = _run_synthetic_session(pytester, "1", inventory)
    output = gated.stdout + gated.stderr
    assert gated.returncode == pytest.ExitCode.TESTS_FAILED, output
    assert "ADVISOR_GATE failed" in output
    assert "skipped: test_synthetic.py::test_skipped" in output
    assert "skipped: test_synthetic.py::test_body_skip" in output
    assert "skipped: test_synthetic.py::test_fixture_skip" in output
    assert "xfailed: test_synthetic.py::test_xfailed" in output
    assert "test_passes [" not in output
    assert "test_not_written_yet" not in output
    assert "gate phase 1: 5 required node IDs, 0 sc1b" in output

    # A gate below every row enforces nothing, and says so instead of looking green.
    empty = _run_synthetic_session(pytester, "0", inventory)
    assert empty.returncode == 0, empty.stdout + empty.stderr
    assert "gate phase 0: 0 required node IDs, 0 sc1b" in empty.stdout + empty.stderr


def test_gate_mode_fails_on_missing_or_deselected_required_test(pytester: pytest.Pytester) -> None:
    """Mutation: cut the not collected and deselected checks in GatePlugin.pytest_sessionfinish,
    or count only setup skips (an sc1b test that skips in its body then passes the gate)."""
    pytester.makepyfile(
        test_synthetic="""
        import pytest

        def test_kept():
            pass

        def test_dropped():
            pass

        @pytest.mark.sc1b
        @pytest.mark.skip(reason="no node")
        def test_sc1b_case():
            pass

        @pytest.mark.sc1b
        def test_sc1b_body_skip():
            pytest.skip("node not found")
        """
    )
    inventory = pytester.path / "inventory.json"
    inventory.write_text(
        json.dumps(
            [
                {"v1_test": 1, "name": "synthetic", "verdict": "port", "phase": 1,
                 "node_ids": ["test_synthetic.py::test_dropped", "test_gone.py::test_never_written"],
                 "approved_obsolete_on": None},
            ]
        ),
        encoding="utf-8",
    )
    proc = spawn_offline_child(
        ["-m", "pytest", "-p", "agent.tests.conftest", "-p", "no:cacheprovider", "-k", "not dropped"],
        kind="python",
        env_extra={config.ENV_GATE: "1", "ADVISOR_GATE_INVENTORY": str(inventory)},
        cwd=pytester.path,
    )
    output = proc.stdout + proc.stderr
    assert proc.returncode == pytest.ExitCode.TESTS_FAILED, output
    assert "deselected: test_synthetic.py::test_dropped" in output
    assert "not collected: v1 test 1 (phase 1): test_gone.py::test_never_written" in output
    assert "skipped: test_synthetic.py::test_sc1b_case [sc1b case" in output
    assert "skipped: test_synthetic.py::test_sc1b_body_skip [sc1b case" in output
    assert "gate phase 1: 2 required node IDs, 2 sc1b" in output


def test_gate_mode_requires_its_phase_tests_and_sc1b(pytester: pytest.Pytester) -> None:
    """A gate run on a subset of files, or with -k, fails when a phase's own tests
    or its SC1b cases are missing (PLAN section 10: a skipped SC1b does not meet
    the Phase 2 gate).

    Mutations: gate_required_list_emptied (the requirements' node IDs are never
    added); gate_min_sc1b_ignored (a session with no sc1b case passes);
    gate_node_phases_ignored (the Phase 2 half of a phase 4 inventory row is not
    enforced at the Phase 2 gate)."""
    pytester.makepyfile(
        test_synthetic="""
        def test_required_here():
            pass

        def test_other():
            pass
        """
    )
    inventory = pytester.path / "inventory.json"
    inventory.write_text(json.dumps([
        {"v1_test": 1, "name": "split row", "verdict": "port", "phase": 4,
         "node_ids": ["test_synthetic.py::test_other", "test_later.py::test_not_due"],
         "node_phases": {"test_synthetic.py::test_other": 2}, "approved_obsolete_on": None},
    ]), encoding="utf-8")
    requirements = pytester.path / "requirements.json"
    requirements.write_text(
        json.dumps({"phases": {"2": {"min_sc1b": 1, "node_ids": [
            "test_synthetic.py::test_required_here", "test_absent.py::test_left_out"]}}}),
        encoding="utf-8",
    )
    env = {config.ENV_GATE_INVENTORY: str(inventory), config.ENV_GATE_REQUIREMENTS: str(requirements)}

    def session(gate: str, *extra: str):
        return spawn_offline_child(
            ["-m", "pytest", "-p", "agent.tests.conftest", "-p", "no:cacheprovider", *extra],
            kind="python", env_extra={config.ENV_GATE: gate, **env}, cwd=pytester.path,
        )

    below = session("1")
    assert below.returncode == 0, below.stdout + below.stderr  # phase 2 requirements not due yet

    gated = session("2", "-k", "not required_here and not other")
    output = gated.stdout + gated.stderr
    assert gated.returncode == pytest.ExitCode.TESTS_FAILED, output
    assert "deselected: test_synthetic.py::test_required_here [phase 2 gate requirement" in output
    assert "not collected: phase 2 gate requirement: test_absent.py::test_left_out" in output
    assert "deselected: test_synthetic.py::test_other [v1 test 1 (phase 2)" in output
    assert "test_not_due" not in output
    assert "sc1b: 0 cases collected; this gate needs at least 1" in output
    assert "gate phase 2: 3 required node IDs, 0 sc1b" in output


def test_phase_2_gate_requires_sc1b_and_the_inventory_check() -> None:
    """The real requirements file names the Phase 2 gate's own tests.

    Mutation gate_requirements_phase_2_emptied: the file's phase 2 list is emptied."""
    from agent.tests.conftest import REQUIREMENTS_PATH, gate_requirements

    required, min_sc1b = gate_requirements(REQUIREMENTS_PATH, 2)
    names = {key.split("/")[-1] for key in required}
    assert min_sc1b >= 1
    for node_id in ("test_v1_inventory.py::test_every_v1_guardrail_is_ported_or_approved_obsolete",
                    "test_sc1b_differential.py::test_v1_and_v2_decisions_match",
                    "test_sc2_replay.py::test_flo_gives_one_confirmed_candidate",
                    "test_sc5_resume.py::test_resume_in_a_new_process",
                    "test_graph_budget.py::test_budget_stop_is_never_no_reliable_answer"):
        assert node_id in names, node_id
    assert gate_requirements(REQUIREMENTS_PATH, 1) == ({}, 0)


def test_live_marker_fails_collection(pytester: pytest.Pytester) -> None:
    """Mutation: cut the live check in conftest pytest_collection_modifyitems."""
    pytester.makepyfile(
        test_synthetic="""
        import pytest

        @pytest.mark.live
        def test_paid_call():
            pass
        """
    )
    proc = spawn_offline_child(
        ["-m", "pytest", "-p", "agent.tests.conftest", "-p", "no:cacheprovider"],
        kind="python",
        env_extra={config.ENV_GATE: None},
        cwd=pytester.path,
    )
    from agent.tests.conftest import LIVE_MARKER_ERROR

    assert proc.returncode != 0
    assert LIVE_MARKER_ERROR in proc.stdout + proc.stderr
    assert "test_paid_call" in proc.stdout + proc.stderr
