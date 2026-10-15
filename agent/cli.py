"""The `advisor` command line (PLAN section 8.13).

The tracing scrub below runs at import, before anything from langchain,
langgraph or langsmith is imported. langsmith caches its environment lookups
(`get_env_var` is an lru_cache), so a scrub that runs after the first langsmith
import can be too late. Keep every langchain, langgraph and langsmith import
inside a function in this module.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Mapping, MutableMapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from agent import config  # stdlib only, safe before the scrub

if TYPE_CHECKING:  # never imported at run time: agent.ledger loads langchain_core
    from agent.ledger import Ledger
    from agent.state import RunContext


def scrub_tracing_env(environ: MutableMapping[str, str] = os.environ) -> bool:
    """Unset every tracing variable unless ADVISOR_TRACING=1 (decision 31).

    Returns True when the user opted in to tracing.
    """
    if environ.get(config.ENV_TRACING) == "1":
        return True
    for name in config.TRACING_ENV_VARS:
        environ.pop(name, None)
    return False


# Runs at import on purpose: this and the strict msgpack switch are the side
# effects of importing cli.py, and both must precede any langgraph import.
TRACING_OPTED_IN: bool = scrub_tracing_env()
os.environ["LANGGRAPH_STRICT_MSGPACK"] = "true"

# State and input fields that never reach a trace, even when tracing is on:
# the photo, what was read off it, and everything that came from the registry.
HIDDEN_TRACE_FIELDS = (
    "photo",
    "extraction",
    "property_id",
    "appliance_id",
    "history_hits",
    "registry",
)
HIDDEN_PLACEHOLDER = "[hidden]"


def hide_trace_inputs(inputs: Mapping[str, object]) -> dict[str, object]:
    """Replace photo and registry fields with a placeholder.

    Phase 2 hook: when TRACING_OPTED_IN is True, the graph runner builds its
    langsmith Client with hide_inputs=hide_trace_inputs and
    hide_outputs=hide_trace_inputs, so traces carry the symptom and the brief
    but never the photo or property data.
    """
    return {
        key: (HIDDEN_PLACEHOLDER if key in HIDDEN_TRACE_FIELDS and value is not None else value)
        for key, value in inputs.items()
    }


# ---------------------------------------------------------------------------
# Errors and exit codes
# ---------------------------------------------------------------------------

EXIT_OK = 0
EXIT_REFUSED = 1  # a usage rule refused the command before any work
EXIT_NOT_YET = 2  # replay ask or resume given no cassette to replay
EXIT_SCHEMA_REJECTED = 3  # check-schema: HTTP 400, the Phase 5 stop condition (decision 22)
EXIT_LIVE_ERROR = 4  # a live command's call or run failed after the typed proceed


class CliRefusal(Exception):
    """A rule in section 8.13 refused the command."""


# ---------------------------------------------------------------------------
# Mode, cassette and output path rules
# ---------------------------------------------------------------------------


def resolve_mode(environ: Mapping[str, str], *, live: bool) -> str:
    """Read ADVISOR_MODE and check it against --live."""
    mode = environ.get(config.ENV_MODE, "replay") or "replay"
    if mode not in config.MODES:
        raise CliRefusal(f"{config.ENV_MODE}={mode!r} is not one of {', '.join(config.MODES)}.")
    if mode in config.LIVE_MODES and not live:
        raise CliRefusal(
            f"{config.ENV_MODE}={mode} spends real money and needs --live to confirm."
        )
    if mode == "replay" and live:
        raise CliRefusal(
            f"--live needs {config.ENV_MODE} set to one of {', '.join(config.LIVE_MODES)}; "
            "replay mode never calls a model."
        )
    return mode


def resolve_cassette(
    flag: str | None, environ: Mapping[str, str], *, mode: str
) -> Path | None:
    """Pick the replay script from --cassette or ADVISOR_CASSETTE (replay only)."""
    value = flag or environ.get(config.ENV_CASSETTE) or None
    if value is None:
        return None
    if mode != "replay":
        source = "--cassette" if flag else config.ENV_CASSETTE
        raise CliRefusal(f"{source} is accepted only in replay mode, not with --live.")
    return Path(value).expanduser()


def _published_roots(repo_root: Path) -> list[Path]:
    return [(repo_root / name).resolve() for name in config.PUBLISHED_PATHS]


def is_inside_published(path: Path, repo_root: Path = config.REPO_ROOT) -> bool:
    """True if path is a published file or lies inside a published folder.

    Compared case insensitively because the default macOS file system is, so
    BRIEFS/x.html would land in briefs/.
    """
    target = [part.casefold() for part in path.expanduser().resolve().parts]
    for root in _published_roots(repo_root):
        root_parts = [part.casefold() for part in root.parts]
        if target[: len(root_parts)] == root_parts:
            return True
    return False


def default_html_path(kind: str, ident: str) -> Path:
    """Where a generated page goes when --html is not given (decision 51)."""
    base = {"brief": config.BRIEFS_OUT_DIR, "property": config.PROPERTY_OUT_DIR}[kind]
    return base / f"{ident}.html"


def resolve_html_path(flag: str | None, *, kind: str, ident: str) -> Path:
    """The output path for a generated page, refusing the published pages."""
    path = Path(flag).expanduser() if flag else default_html_path(kind, ident)
    if is_inside_published(path):
        published = ", ".join(config.PUBLISHED_PATHS)
        raise CliRefusal(
            f"--html {path} is inside the published pages ({published}), "
            "which v2 never writes to. Choose a path under data/ or leave --html out."
        )
    return path


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="advisor", description="Property Maintenance Advisor v2")
    sub = parser.add_subparsers(dest="command", required=True)

    ask = sub.add_parser("ask", help="start a thread from a symptom and a photo or identity")
    ask.add_argument("--symptom", required=True)
    source = ask.add_mutually_exclusive_group()
    source.add_argument("--photo", metavar="PATH")
    source.add_argument("--identity", metavar="JSON")
    ask.add_argument("--property", dest="property_id", metavar="ID")
    ask.add_argument("--appliance", dest="appliance_id", metavar="ID")
    ask.add_argument("--code")
    _add_run_flags(ask)

    resume = sub.add_parser("resume", help="resume a paused thread with confirmed fields")
    resume.add_argument("thread_id")
    for name in ("manufacturer", "model", "serial", "date", "code"):
        resume.add_argument(f"--{name}")
    _add_run_flags(resume)

    prop = sub.add_parser("property", help="property pages")
    prop_sub = prop.add_subparsers(dest="property_command", required=True)
    show = prop_sub.add_parser("show", help="write the static property page")
    show.add_argument("property_id", metavar="ID")
    show.add_argument("--html", metavar="PATH")
    sweep = prop_sub.add_parser(
        "sweep", help="plan a replacement: show the graph's verified discontinuation facts and write the page")
    which = sweep.add_mutually_exclusive_group(required=True)
    which.add_argument("property_id", metavar="ID", nargs="?")
    which.add_argument("--all", action="store_true", help="every property in the registry")
    sweep.add_argument("--research", action="store_true",
                       help="first look up discontinuation and successors for units with no lifecycle facts")
    sweep.add_argument("--cassette", metavar="PATH", action="append",
                       help="--research in replay: a lifecycle cassette, matched to its unit (repeatable)")
    sweep.add_argument("--live", action="store_true", help="--research only: a live lookup (cheap mode)")
    sweep.add_argument("--new-ledger", action="store_true",
                       help="--research --live only: create the spend ledger if it is missing")
    recalls = prop_sub.add_parser(
        "recalls", help="product recall check against CPSC: show each appliance's latest stored check")
    which = recalls.add_mutually_exclusive_group(required=True)
    which.add_argument("property_id", metavar="ID", nargs="?")
    which.add_argument("--all", action="store_true", help="every property in the registry")
    recalls.add_argument("--live", action="store_true",
                         help="query CPSC now (free, no key), after the planned requests and a typed proceed")

    graph = sub.add_parser("graph", help="knowledge graph")
    graph_sub = graph.add_subparsers(dest="graph_command", required=True)
    graph_sub.add_parser("stats", help="node and edge counts")

    ledger = sub.add_parser("ledger", help="run and build spend, credits, remaining budget")
    ledger.add_argument("--run", dest="run_id", metavar="RUN_ID", help="show one run only")

    ev = sub.add_parser("eval", help="evaluations: LIVE with --live (cheap mode only); --score and sc12a score offline")
    ev.add_argument("which", choices=EVAL_SUITES)
    ev.add_argument("--live", action="store_true")
    add_eval_flags(ev)

    sset = sub.add_parser("safety-set",
                          help="the SC12a labeled step set: build, packet, labels, lock (no call is made)")
    sset_sub = sset.add_subparsers(dest="safety_set_command", required=True)
    build = sset_sub.add_parser("build", help="write data/eval/sc12a/items.json and agent/safety_eval/item_ids.json")
    build.add_argument("--extend", action="store_true",
                       help="the stopping rule: add the next D items to a labeled set")
    build.add_argument("--sc12b", action="store_true",
                       help="write data/eval/sc12b/items.json from the SC12b run records, for the readers")
    packet = sset_sub.add_parser("packet", help="write the items the three readers label: id, appliance, step, "
                                 "detail only, sorted by id, unlabeled items only")
    packet.add_argument("--sc12b", action="store_true", help="the SC12b steps (data/eval/sc12b/reader_items.json)")
    labels = sset_sub.add_parser("labels", help="import the readers' labels files; writes agent/safety_eval/labels.json")
    labels.add_argument("files", nargs="+", metavar="FILE")
    lock = sset_sub.add_parser("lock", help="Gate 2: write data/eval/sc12a/lock.json from the tune half")
    lock.add_argument("--reason", metavar="TEXT", help="a logged reason to replace an existing lock")

    schema = sub.add_parser(
        "check-schema", help="LIVE: one minimal synthesize call to confirm the API accepts BriefDraft (decision 22)",
    )
    schema.add_argument("--live", action="store_true")
    schema.add_argument(
        "--new-ledger", action="store_true",
        help="create the spend ledger if it is missing (it is never recreated silently)",
    )

    caps = sub.add_parser(
        "check-capabilities",
        help="LIVE: one small call per Claude Haiku 5.5 node at its exact settings, plate photo first (decision 69)",
    )
    caps.add_argument("--live", action="store_true")
    caps.add_argument(
        "--new-ledger", action="store_true",
        help="create the spend ledger if it is missing (it is never recreated silently)",
    )

    return parser


EVAL_SUITES = ("sc3b", "sc7b", "plates", "sc12a", "sc12b")


def _add_run_flags(p: argparse.ArgumentParser) -> None:
    p.add_argument("--cassette", metavar="PATH")
    p.add_argument("--html", metavar="PATH")
    p.add_argument("--live", action="store_true")
    p.add_argument(
        "--new-ledger", action="store_true",
        help="live ask only: create the spend ledger if it is missing (it is never recreated silently)",
    )


# ---------------------------------------------------------------------------
# Commands. Each checks its rules first, then hands off to its engine.
# ---------------------------------------------------------------------------


FULL_MODE_REFUSAL = (
    "full mode is not approved for a live run: decision 18 needs a separate approval for any live full run, "
    "and PLAN section 4 (the TIMEOUT_S row) wants the full synthesize timeout confirmed, or synthesize "
    "streamed, first. Typing proceed is not that approval; config.FULL_LIVE_APPROVED records it."
)


def refuse_unapproved_full(mode: str) -> None:
    """Decision 18: every live command refuses full mode until config.FULL_LIVE_APPROVED."""
    if mode == "full" and not config.FULL_LIVE_APPROVED:
        raise CliRefusal(FULL_MODE_REFUSAL)


def refuse_missing_keys(need: Sequence[str], environ: Mapping[str, str] = os.environ) -> None:
    """Refuse a live command whose keys are missing, before anything is opened or created.

    LiveSession loads and checks the keys again; this earlier check only makes
    sure a refused command leaves no ledger file behind (decision 52).
    """
    from agent.live import env as live_env

    try:
        report, _ = live_env.load_live_keys(dict(environ), config.ENV_FILE, need=need,
                                            tracing=TRACING_OPTED_IN)
    except live_env.DotenvError as exc:
        raise CliRefusal(str(exc)) from None
    if report.missing:
        raise CliRefusal(
            f"missing {', '.join(report.missing)}: set {'each' if len(report.missing) > 1 else 'it'} in the "
            "environment or in the repo's gitignored .env (see .env.example) before a live run."
        )


def open_live_ledger(mode: str, *, new_ledger: bool, need: Sequence[str], need_usd: float | None = None,
                     need_credits: int | None = None, environ: Mapping[str, str] = os.environ) -> Ledger:
    """The ledger a live run spends against, checked before the run starts.

    Refuses full mode without its decision 18 approval before anything is
    opened; then refuses missing keys, so a refused command creates no
    ledger file; then refuses when the ledger file is missing or holds no ledger,
    unless --new-ledger was passed (decision 52), and when the remaining
    build budget or credits cannot cover one run of this mode (section 8.9).
    A caller that names its worst case in dollars (need_usd) needs no credits
    unless it names them too (need_credits): plate runs make no search, and
    the sweep's lookups name theirs.
    """
    refuse_unapproved_full(mode)
    refuse_missing_keys(need, environ)
    # agent.ledger imports langchain_core, so it loads only after the scrub.
    from agent.ledger import BudgetExceeded, Ledger, LedgerMissing

    try:
        ledger = Ledger(config.LEDGER_PATH, create=new_ledger)
    except LedgerMissing as exc:
        raise CliRefusal(
            f"{exc}. It records every dollar spent and is never recreated silently; "
            "pass --new-ledger only if no live run has happened yet."
        ) from None
    try:
        credits = need_credits if need_credits is not None else (None if need_usd is None else 0)
        ledger.assert_can_start_live_run(mode, need_usd=need_usd, need_credits=credits)
    except BudgetExceeded as exc:
        raise CliRefusal(f"the build budget cannot cover a {mode} run: {exc}") from None
    return ledger


def cmd_run(args: argparse.Namespace, environ: Mapping[str, str]) -> int:
    """ask and resume share the mode, cassette, output and ledger rules."""
    mode = resolve_mode(environ, live=args.live)
    cassette_path = resolve_cassette(args.cassette, environ, mode=mode)
    ident = getattr(args, "thread_id", None) or "<run_id>"
    resolve_html_path(args.html, kind="brief", ident=ident)
    if mode in config.LIVE_MODES:
        if args.command == "resume" and args.new_ledger:
            # A paused live thread proves a live run has already spent money.
            raise CliRefusal(
                "--new-ledger is refused with a live resume: the thread's ask segment already made paid "
                "calls, and a new ledger would forget them (SC11 and the build cap, decision 52)."
            )
        if args.command == "ask" and args.photo:
            # Decision 69: once the capabilities check records the plate photo refused, a photo
            # ask is refused before the preflight and before any ledger is opened.
            from agent.live.preflight import plate_refusal

            refusal = plate_refusal(mode)
            if refusal:
                raise CliRefusal(refusal)
        ledger = open_live_ledger(mode, new_ledger=args.new_ledger, need=config.LIVE_KEY_NAMES,
                                  environ=environ)
        return run_live(args, mode, ledger)
    if cassette_path is None:
        # Replay has nothing to replay without a script. Live recordings that
        # would give every run a cassette arrive with Phase 6.
        print(
            f"advisor {args.command}: replay needs a cassette: pass --cassette PATH or set "
            f"{config.ENV_CASSETTE}."
        )
        return EXIT_NOT_YET
    if args.command == "ask":
        return cmd_ask(args, mode, cassette_path)
    return cmd_resume(args, mode, cassette_path)


# ---------------------------------------------------------------------------
# ask and resume (replay, and live through LiveSession below)
# ---------------------------------------------------------------------------

ENV_STRICT_MSGPACK = "LANGGRAPH_STRICT_MSGPACK"


def _load_cassette(path: Path):
    from agent.replay.cassettes import CassetteError, load_cassette

    try:
        return load_cassette(path)
    except (CassetteError, OSError) as exc:
        raise CliRefusal(f"cannot read the cassette: {exc}") from None


def refuse_registry_drift() -> None:
    """Refuse a live step while config.REGISTRY_PATH differs from the seed (registry.seed_drift).

    A registry file made from an older seed keeps null model numbers and
    lacks newer rows until the seed is loaded again, so a live run would
    compare, store and show what the seed no longer says. Nothing is changed.
    """
    from agent.registry import seed_drift

    problems = seed_drift(config.REGISTRY_PATH)
    if problems:
        raise CliRefusal(f"the registry at {config.REGISTRY_PATH} differs from the seed: {'; '.join(problems)}. "
                         "Reload the seed first with agent.registry.open_registry(config.REGISTRY_PATH), which "
                         "upserts every seed row and keeps every lookup, then run this again. Nothing was sent "
                         "or spent.")


def ensure_registry() -> None:
    """Load the synthetic seed into config.REGISTRY_PATH the first time a run needs it (PLAN 8.12)."""
    if config.REGISTRY_PATH.exists():
        return
    from agent.registry import open_registry

    open_registry(config.REGISTRY_PATH)


def run_context(run_id: str, mode: str, cassette, *, secrets: list[str] | tuple[str, ...] = ()) -> "RunContext":
    """The per call context for a CLI run (section 8.2); replay never touches the live ledger.

    Replay reads and grows config.REPLAY_GRAPH_PATH, never config.GRAPH_PATH
    (decision 37). A live run passes its key values as `secrets`, so the paid
    call sites redact them from every error (agent/redaction.py). The run's
    one UTC date (SC13) is pinned here from _today().
    """
    from agent.state import RunContext

    ensure_registry()
    replay = mode == "replay"
    return RunContext(
        run_id=run_id,
        mode=mode,
        ledger_path=config.REPLAY_LEDGER_PATH if replay else config.LEDGER_PATH,
        registry_path=config.REGISTRY_PATH,
        graph_path=config.REPLAY_GRAPH_PATH if replay else config.GRAPH_PATH,
        pages_dir=config.PAGES_DIR,
        cassette=cassette,
        caps=cassette.caps if cassette is not None else None,
        secrets=list(secrets),
        run_date=_today(),
    )


def new_thread_id() -> str:
    import uuid

    return f"t-{uuid.uuid4().hex[:16]}"


def _parse_identity(raw: str | None) -> dict | None:
    if raw is None:
        return None
    import json

    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise CliRefusal(f"--identity is not valid JSON: {exc}") from None
    if not isinstance(value, dict):
        raise CliRefusal("--identity must be a JSON object")
    return value


def _tracing():
    """A tracing context that hides photo and registry fields, or a no-op when not opted in."""
    import contextlib

    if not TRACING_OPTED_IN:
        return contextlib.nullcontext()
    from langsmith import Client, tracing_context

    client = Client(hide_inputs=hide_trace_inputs, hide_outputs=hide_trace_inputs)
    return tracing_context(client=client, enabled=True)


def _print_outcome(command: str, outcome) -> None:
    import json

    for name in outcome.nodes:
        print(f"  ran: {name}")
    if outcome.paused:
        print(f"advisor {command}: paused at confirm_identity; thread {outcome.thread_id}")
        print("interrupt: " + json.dumps(outcome.interrupt, sort_keys=True))
        print(
            f"resume with: advisor resume {outcome.thread_id} --model MODEL "
            "[--manufacturer ... --serial ... --date ... --code ...]"
        )
        return
    state = outcome.state
    print(f"status: {state.get('status')}")
    if state.get("stop_reason"):
        print(f"stop reason: {state.get('stop_reason')}")
    if state.get("refusal_origin"):
        print(f"refusal origin: {state.get('refusal_origin')}")
    print(f"html: {state.get('html_path')}")


def cmd_ask(args: argparse.Namespace, mode: str, cassette_path: Path | None,
            live: LiveSession | None = None) -> int:
    """Start a thread and run it to the confirmation pause or the end.

    Replay passes a cassette; a live run passes its LiveSession instead, which
    adds the preflight, the capture for the recording, and the post run SC11
    record around the same graph run.
    """
    cassette = _load_cassette(cassette_path) if cassette_path is not None else None
    thread_id = new_thread_id()
    html = resolve_html_path(args.html, kind="brief", ident=thread_id)
    identity = _parse_identity(args.identity)
    from agent.graph import build_graph, open_checkpointer, run_until_pause_or_end
    from agent.nodes.intake import intake_input

    data = intake_input(
        symptom=args.symptom, photo_path=args.photo, identity=identity,
        property_id=args.property_id, appliance_id=args.appliance_id, code=args.code,
    )
    data["html_path"] = str(html)
    if live is not None:
        live.confirm_run("ask", photo=bool(args.photo), run_id=thread_id)
    print(f"advisor ask: thread {thread_id} (process {os.getpid()}, mode {mode})")
    graph = build_graph(open_checkpointer(config.CHECKPOINT_PATH))
    ctx = run_context(thread_id, mode, cassette, secrets=live.secrets if live is not None else ())
    try:
        with _tracing(), _live_capture(live, thread_id, "ask"):
            outcome = run_until_pause_or_end(graph, data, ctx, thread_id)
    finally:
        if live is not None:
            live.scrub(ctx)  # whatever happened, no key value stays in a file this run wrote
    _print_outcome("ask", outcome)
    if live is not None:
        live.finish(outcome, ctx)
    return EXIT_OK


RESUME_FLAGS = {"manufacturer": "manufacturer", "model": "model", "serial": "serial", "date": "manufacture_date"}


def resume_value(args: argparse.Namespace, proposed: Mapping[str, object] | None) -> dict:
    """The resume value: the paused proposal, with any identity flag given replacing its field.

    --code sets the observed code ("" clears it); leaving it out keeps the candidate.
    """
    identity = {field: (proposed or {}).get(field) for field in RESUME_FLAGS.values()}
    for flag, field in RESUME_FLAGS.items():
        value = getattr(args, flag)
        if value is not None:
            identity[field] = value
    value: dict = {"identity": identity}
    if args.code is not None:
        value["observed_code"] = args.code
    return value


def cmd_resume(args: argparse.Namespace, mode: str, cassette_path: Path | None,
               live: LiveSession | None = None) -> int:
    """Resume a paused thread with the owner's confirmation; it stays paused if the model is missing."""
    cassette = _load_cassette(cassette_path) if cassette_path is not None else None
    from langgraph.types import Command

    from agent.graph import build_graph, open_checkpointer, run_until_pause_or_end, thread_config

    graph = build_graph(open_checkpointer(config.CHECKPOINT_PATH))
    snapshot = graph.get_state(thread_config(args.thread_id))
    values = dict(snapshot.values or {})
    if not values:
        raise CliRefusal(f"no thread {args.thread_id!r} in {config.CHECKPOINT_PATH}")
    if values.get("mode") != mode:
        raise CliRefusal(f"thread {args.thread_id} ran in {values.get('mode')!r} mode, not {mode!r}")
    run_id = values.get("run_id") or args.thread_id
    print(f"advisor resume: thread {args.thread_id} (process {os.getpid()}, mode {mode})")
    interrupts = list(snapshot.interrupts or ())
    if not interrupts:
        print(f"advisor resume: thread {args.thread_id} is not paused")
        print(f"status: {values.get('status')}")
        print(f"html: {values.get('html_path')}")
        return EXIT_OK
    update = None
    if args.html:
        update = {"html_path": str(resolve_html_path(args.html, kind="brief", ident=run_id))}
    command = Command(resume=resume_value(args, interrupts[0].value.get("identity")), update=update)
    if live is not None:
        live.confirm_run("resume", photo=bool(values.get("photo")), run_id=run_id)
    ctx = run_context(run_id, mode, cassette, secrets=live.secrets if live is not None else ())
    try:
        with _tracing(), _live_capture(live, run_id, "resume"):
            outcome = run_until_pause_or_end(graph, command, ctx, args.thread_id)
    finally:
        if live is not None:
            live.scrub(ctx)
    _print_outcome("resume", outcome)
    if live is not None:
        live.finish(outcome, ctx)
    return EXIT_OK


# ---------------------------------------------------------------------------
# Live ask, resume and check-schema (Phase 5; the live path builder's block).
# The same graph, nodes and ledger as replay: models.make_model and
# make_tools build ChatAnthropic and the Tavily wrappers in a live mode, and
# every paid call is reserved before it is sent and charged after.
# ---------------------------------------------------------------------------

ANTHROPIC_KEY_NAME = "ANTHROPIC_API_KEY"
SC11_RULE = "ledger.run_total(run_id) <= RUN_CAP_USD[mode]"


def _today():
    from datetime import UTC, datetime

    return datetime.now(UTC).date()


def sc11_post_condition(run_total_usd: float, run_cap_usd: float) -> dict:
    """The SC11 live post condition for one run, pass or miss (never hidden)."""
    passed = round(run_total_usd, 9) <= run_cap_usd
    return {"rule": SC11_RULE, "run_total_usd": run_total_usd, "run_cap_usd": run_cap_usd,
            "result": "pass" if passed else "miss"}


class LiveSession:
    """What a live command adds around the shared graph run.

    Built only after the mode, ledger and build budget checks passed. It
    refuses full mode without its decision 18 approval, a missing key and a
    Haiku mode on or after config.HAIKU_RETIREMENT_EARLIEST and, for a
    command whose runs read the registry (`uses_registry`), a registry that
    differs from the seed (refuse_registry_drift), prints the
    preflight and reads the typed "proceed", puts the keys in the environment
    for this command only, redacts them from everything printed and gives
    them to the run context for redaction at the source, scrubs the files a
    run wrote whatever its outcome, and after a finished run writes the SC11
    post condition, the v1 case checks and the recording candidate.
    """

    def __init__(self, command: str, mode: str, ledger: Ledger, *, need: tuple[str, ...],
                 environ: MutableMapping[str, str] = os.environ, uses_registry: bool = False) -> None:
        from agent.live import env as live_env
        from agent.live.preflight import haiku_refusal

        refuse_unapproved_full(mode)
        if uses_registry:  # a command whose runs read the registry refuses a stale one
            refuse_registry_drift()
        try:
            report, additions = live_env.load_live_keys(environ, config.ENV_FILE, need=need,
                                                        tracing=TRACING_OPTED_IN)
        except live_env.DotenvError as exc:
            raise CliRefusal(str(exc)) from None
        if report.missing:
            raise CliRefusal(
                f"missing {', '.join(report.missing)}: set {'each' if len(report.missing) > 1 else 'it'} in the "
                "environment or in the repo's gitignored .env (see .env.example) before a live run."
            )
        refusal = haiku_refusal(mode, _today())
        if refusal:
            raise CliRefusal(refusal)
        self.command, self.mode, self.ledger, self.environ = command, mode, ledger, environ
        self.sources = dict(report.sources)
        self._additions = additions
        self.secrets = live_env.secret_values(environ, additions, list(report.sources))

    def active(self):
        """Keys in the environment and redacted output, for the length of the command."""
        import contextlib

        from agent.live import env as live_env

        stack = contextlib.ExitStack()
        stack.enter_context(live_env.keys_in_environ(self._additions, self.environ))
        stack.enter_context(live_env.redacted_output(self.secrets))
        return stack

    def confirm(self, plans: list, extra: Sequence[str] = ()) -> None:
        """Print the preflight and continue only on exactly "proceed" (PRD Part A rule 1).

        `extra` lines go just before the typed prompt.
        """
        from agent.live.preflight import ask_to_proceed, preflight_lines

        lines = preflight_lines(self.command, self.mode, plans, self.ledger)
        lines[-1:-1] = list(extra)
        keys = ", ".join(f"{name} from the {source}" if source == "environment" else f"{name} from .env"
                         for name, source in self.sources.items())
        lines.insert(1, f"keys: {keys} (values are never shown)")
        if not ask_to_proceed(lines, sys.stdin, sys.stdout):
            raise CliRefusal('the typed confirmation was not exactly "proceed"; nothing was spent.')

    def confirm_run(self, stage: str, *, photo: bool, run_id: str) -> None:
        from agent.live.preflight import run_plan

        spent = self.ledger.run_total(run_id)
        self.confirm([run_plan(self.mode, photo=photo, stage=stage, spent_in_run=spent)])

    def finish(self, outcome, ctx) -> None:
        """After the graph run: spend so far, or for a finished run SC11, latency, v1 checks and the recording."""
        run_id = ctx.run_id
        total = self.ledger.run_total(run_id)
        cap = config.RUN_CAP_USD[self.mode]
        if outcome.paused:
            print(f"live spend so far for run {run_id}: {_usd(total)} of the {_usd(cap)} run cap")
            return
        record = self._write_sc11(outcome, ctx, total, cap)
        sc11 = record["sc11"]
        print(f"SC11: {sc11['result']} (run total {_usd(total)}, cap {_usd(cap)}; {SC11_RULE})")
        for node, seconds in (record.get("latency") or {}).items():
            print(f"  latency {node}: {float(seconds):.2f} s")
        from agent.live.evaluators import format_v1_checks

        for line in format_v1_checks(record.get("v1_checks") or []):
            print(line)
        self._record(outcome, ctx)

    def _write_sc11(self, outcome, ctx, total: float, cap: float) -> dict:
        import json

        from agent.live.env import redact_value
        from agent.nodes.persist import run_record_path

        path = run_record_path(ctx, ctx.run_id)
        state = outcome.state
        record = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {
            "run_id": ctx.run_id, "mode": self.mode, "status": state.get("status"),
        }
        record.setdefault("latency", dict(state.get("latency") or {}))
        record["sc11"] = sc11_post_condition(total, cap)
        record["v1_checks"] = live_v1_checks(ctx.run_id, state)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(redact_value(record, self.secrets), indent=2, sort_keys=True), encoding="utf-8")
        return record

    def scrub(self, ctx) -> None:
        """Backstop behind the source redaction: mask key values in the files a run wrote.

        Runs after every live graph run, finished, paused or failed: the run
        record, the lookup log, the capture file and the checkpoint database.
        """
        from agent.live.env import scrub_files, scrub_sqlite
        from agent.live.recorder import capture_path
        from agent.nodes.persist import run_record_path
        from agent.research.tools import lookup_log_path

        paths = [run_record_path(ctx, ctx.run_id), lookup_log_path(ctx), capture_path(ctx.run_id)]
        changed = scrub_files(paths, self.secrets)
        try:
            if scrub_sqlite(config.CHECKPOINT_PATH, self.secrets):
                changed.append(config.CHECKPOINT_PATH)
        except Exception as exc:  # sqlite3.Error: report it, never hide it
            print(f"warning: could not scrub {config.CHECKPOINT_PATH}: {type(exc).__name__}; "
                  "check it for a key value and report this", file=sys.stderr)
        for path in changed:
            print(f"warning: a key value was found in {path} and redacted; report this", file=sys.stderr)
        notes = " ".join(str(r.get("note") or "") for r in self.ledger.rows(ctx.run_id))
        if any(secret in notes for secret in self.secrets):
            print("warning: a key value is in the ledger notes of this run; report this", file=sys.stderr)

    def _record(self, outcome, ctx) -> None:
        from agent.live import recorder
        from agent.research.tools import lookup_log_path

        try:
            result = recorder.write_candidate(
                ctx.run_id, outcome.state, lookup_log=lookup_log_path(ctx), pages_dir=Path(ctx.pages_dir),
                recorded_on=_today().isoformat(), secrets=self.secrets,
            )
        except Exception as exc:  # the run and its SC11 record are already written
            print(f"recording candidate: not written: {type(exc).__name__}: {exc}", file=sys.stderr)
            return
        loads = "yes" if result.loadable else f"no ({result.load_error or 'withheld'})"
        print(f"recording candidate: {result.path or 'withheld'}")
        print(f"  privacy scan: {result.verdict}; {result.hits} hit(s); loads as a cassette: {loads}")
        if result.copy_log_path is not None:
            print(f"  copy log: {result.copy_log_path}")
        print(f"  privacy report: {result.report_path}; nothing is tracked until the privacy diff is approved")


def live_v1_checks(run_id: str, state: Mapping[str, object]) -> list[dict]:
    """The PLAN 6.5 checks of whichever v1 live cases this run is (Phase 5: E1 and B2)."""
    import hashlib

    from agent.live import evaluators
    from agent.live.recorder import raw_reply

    plates = {"plate-clear.jpg": "E1", "plate-blurry.jpg": "E2"}
    shas = {hashlib.sha256((config.REPO_ROOT / "demo-assets" / name).read_bytes()).hexdigest(): case
            for name, case in plates.items() if (config.REPO_ROOT / "demo-assets" / name).is_file()}
    raw = raw_reply(run_id, "read_plate")
    return [evaluators.v1_case_checks(case, state, raw) for case in evaluators.v1_cases_for_run(state, shas)]


def _live_capture(live: LiveSession | None, run_id: str, segment: str):
    """Capture every model reply of a live run for its recording; nothing in replay."""
    import contextlib

    if live is None:
        return contextlib.nullcontext()
    from agent.live.recorder import capturing

    return capturing(run_id, live.mode, segment, live.secrets)


def _live_failure(command: str, exc: BaseException) -> int:
    print(f"advisor {command}: the live call failed: {type(exc).__name__}: {exc}", file=sys.stderr)
    print("Stop and report this (PRD Part A rule 7); every call made so far is in the ledger "
          "(advisor ledger).", file=sys.stderr)
    return EXIT_LIVE_ERROR


def run_live(args: argparse.Namespace, mode: str, ledger: Ledger) -> int:
    """A live ask or resume: key and date checks, then the same run as replay with no cassette."""
    session = LiveSession(args.command, mode, ledger, need=config.LIVE_KEY_NAMES, uses_registry=True)
    with session.active():
        try:
            if args.command == "ask":
                return cmd_ask(args, mode, None, live=session)
            return cmd_resume(args, mode, None, live=session)
        except CliRefusal as refusal:
            print(f"advisor: refused: {refusal}", file=sys.stderr)
            return EXIT_REFUSED
        except Exception as exc:
            return _live_failure(args.command, exc)


def cmd_check_schema(args: argparse.Namespace, environ: Mapping[str, str]) -> int:
    """`advisor check-schema --live`: one minimal synthesize call against the real API (decision 22)."""
    import uuid

    mode = resolve_mode(environ, live=args.live)
    if mode not in config.LIVE_MODES:
        raise CliRefusal(
            f"check-schema makes a paid call: set {config.ENV_MODE} to one of "
            f"{', '.join(config.LIVE_MODES)} and pass --live."
        )
    ledger = open_live_ledger(mode, new_ledger=args.new_ledger, need=(ANTHROPIC_KEY_NAME,), environ=environ)
    session = LiveSession("check-schema", mode, ledger, need=(ANTHROPIC_KEY_NAME,))
    from agent.live import schema_check
    from agent.live.preflight import Plan

    with session.active():
        try:
            typical, worst = schema_check.typical_and_worst_usd(mode)
            session.confirm([Plan(
                label=f"1 minimal synthesize call on {schema_check.model_id(mode)} (the schema check)",
                typical_calls=1, max_calls=1, typical_usd=typical, worst_usd=worst,
                typical_credits=0, max_credits=0,
                notes=[f"max_tokens {config.SCHEMA_CHECK_MAX_TOKENS}, no retries; reserved and charged in the "
                       f"ledger under node {config.SCHEMA_CHECK_NODE}"],
            )])
            result = schema_check.run_schema_check(ledger, mode, f"schema-check-{uuid.uuid4().hex[:12]}")
        except CliRefusal as refusal:
            print(f"advisor: refused: {refusal}", file=sys.stderr)
            return EXIT_REFUSED
        except Exception as exc:
            return _live_failure("check-schema", exc)
        for line in schema_check.report_lines(result):
            print(line)
    if result.status == schema_check.ACCEPTED:
        return EXIT_OK
    return EXIT_SCHEMA_REJECTED if result.status == schema_check.REJECTED else EXIT_LIVE_ERROR


def cmd_check_capabilities(args: argparse.Namespace, environ: Mapping[str, str]) -> int:
    """`advisor check-capabilities --live`: the first live step on Claude Haiku 5.5 (decision 69).

    agent/live/capabilities.py prints the planned calls and their cost and
    waits for a typed "proceed"; nothing is sent without it. Its record turns
    plate reading off when the photo is refused.
    """
    import uuid

    mode = resolve_mode(environ, live=args.live)
    if mode not in config.LIVE_MODES:
        raise CliRefusal(
            f"check-capabilities makes paid calls: set {config.ENV_MODE} to one of "
            f"{', '.join(config.LIVE_MODES)} and pass --live."
        )
    ledger = open_live_ledger(mode, new_ledger=args.new_ledger, need=(ANTHROPIC_KEY_NAME,), environ=environ)
    session = LiveSession("check-capabilities", mode, ledger, need=(ANTHROPIC_KEY_NAME,))
    from agent.live import capabilities

    with session.active():
        try:
            result = capabilities.check(ledger, mode, stdin=sys.stdin, stdout=sys.stdout,
                                        run_id=f"capabilities-{uuid.uuid4().hex[:12]}")
        except Exception as exc:
            return _live_failure("check-capabilities", exc)
    if result is None:
        return EXIT_REFUSED
    return EXIT_OK if result.passed else EXIT_LIVE_ERROR


def property_graph_path(environ: Mapping[str, str]) -> Path:
    """The graph a property page reads: the replay graph in replay, the runtime graph otherwise.

    `property show` spends nothing, so it needs no --live; ADVISOR_MODE only
    picks which graph's verified successors it shows (decision 37 keeps the
    two apart). It reads the graph and never writes it.
    """
    mode = environ.get(config.ENV_MODE, "replay") or "replay"
    if mode == "replay":
        return config.REPLAY_GRAPH_PATH
    if mode in config.LIVE_MODES:
        return config.GRAPH_PATH
    raise CliRefusal(f"{config.ENV_MODE}={mode!r} is not one of {', '.join(config.MODES)}.")


def maintenance_due_by_appliance(registry, appliance_ids: list[str], *, replay: bool) -> dict[str, list]:
    """Validated maintenance items per appliance, from the newest ok run that carries any.

    The due dates were computed by validate from the registry maintenance_log
    and a manufacturer interval quoted in a verified source (PLAN 8.5 rule 9);
    this only finds them. A run is found through the registry lookups table
    (run_id and appliance_id) and read from its run record under data/runs/.
    Replay records feed only a replay page and live records only a live page.
    """
    import json

    from agent.nodes.persist import RUNS_DIRNAME

    runs = config.PAGES_DIR.parent / RUNS_DIRNAME
    out: dict[str, list] = {}
    for appliance_id in appliance_ids:
        for lookup in reversed(registry.lookups(appliance_id=appliance_id)):
            path = runs / f"{lookup['run_id']}.json"
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if record.get("status") != "ok" or (record.get("mode") == "replay") != replay:
                continue
            items = [m for m in ((record.get("brief") or {}).get("maintenance_due") or []) if isinstance(m, dict)]
            if items:
                out[appliance_id] = items
                break
    return out


def cmd_property_show(args: argparse.Namespace, environ: Mapping[str, str]) -> int:
    """Write the static property page, labeled synthetic (PLAN 8.13, decision 51).

    The page's one date, which every warranty status on it uses, is pinned
    once here from _today() (UTC).
    """
    html = resolve_html_path(args.html, kind="property", ident=args.property_id)
    graph_path = property_graph_path(environ)
    ensure_registry()
    from agent.kg import KnowledgeGraph
    from agent.registry import Registry
    from agent.render.property_page import render_property_page

    registry = Registry(config.REGISTRY_PATH)
    known = [p["id"] for p in registry.list_properties()]
    if args.property_id not in known:
        raise CliRefusal(f"unknown property {args.property_id!r}; the registry holds {', '.join(known) or 'none'}.")
    appliance_ids = [a["id"] for a in registry.list_appliances(args.property_id)]
    graph = KnowledgeGraph.load(graph_path, registry=config.REGISTRY_PATH)
    due = maintenance_due_by_appliance(registry, appliance_ids, replay=graph_path == config.REPLAY_GRAPH_PATH)
    page = render_property_page(
        args.property_id, registry, graph=graph, maintenance_due=due,
        generated_at=_today().isoformat(), recalls_dir=config.RECALLS_DIR,
    )
    html.parent.mkdir(parents=True, exist_ok=True)
    html.write_text(page, encoding="utf-8")
    print(f"advisor property show: {args.property_id}, {len(appliance_ids)} appliances, graph {graph_path}")
    print(f"html: {html}")
    return EXIT_OK


# ---------------------------------------------------------------------------
# advisor property sweep (SC14, decision 72): the offline "Plan a replacement"
# sweep in agent/sweep.py, and with --research the lifecycle lookups in
# agent/lifecycle.py, which load only when asked for.
# ---------------------------------------------------------------------------

SWEEP_OFFLINE_NOTE = ("the sweep spent nothing: it read the graph and the registry only (no model or search "
                      "call, no ledger)")
SWEEP_PAGES_NOTE = "writing the pages spent nothing; the lookups' spend is listed above"
SWEEP_FLAGS_REFUSAL = ("--live, --cassette and --new-ledger go with --research only; the sweep itself is "
                       "offline and spends nothing.")


def sweep_targets(registry, args: argparse.Namespace) -> list[str]:
    """The property IDs a sweep covers: every one with --all, else the one named (refused when unknown)."""
    known = [p["id"] for p in registry.list_properties()]
    if args.all:
        return known
    if args.property_id in known:
        return [args.property_id]
    raise CliRefusal(f"unknown property {args.property_id!r}; the registry holds {', '.join(known) or 'none'}.")


def cmd_property_sweep(args: argparse.Namespace, environ: Mapping[str, str]) -> int:
    """Print each unit's verified replacement facts and write or refresh its property page.

    Offline unless --research: it reads the graph property show reads and
    the registry, and opens no ledger. The pages' one date is pinned once
    here from _today() (UTC), before any lookup.
    """
    if not args.research and (args.live or args.cassette or args.new_ledger):
        raise CliRefusal(SWEEP_FLAGS_REFUSAL)
    graph_path = property_graph_path(environ)
    ensure_registry()
    from agent.registry import Registry

    registry = Registry(config.REGISTRY_PATH)
    targets = sweep_targets(registry, args)
    as_of = _today()
    if args.research:
        code = sweep_research(args, environ, registry, targets, graph_path)
        if code != EXIT_OK:
            return code
    write_sweep_pages(registry, targets, graph_path, as_of, researched=args.research)
    return EXIT_OK


def write_sweep_pages(registry, targets: Sequence[str], graph_path: Path, as_of, *, researched: bool) -> None:
    """The offline sweep: print every unit's facts and write each property's page.

    After --research the lookups may have spent, so the closing line says
    only that writing the pages spent nothing.
    """
    from agent import sweep
    from agent.kg import KnowledgeGraph
    from agent.render.property_page import render_property_page

    graph = KnowledgeGraph.load(graph_path, registry=config.REGISTRY_PATH)
    for property_id in targets:
        appliances = registry.list_appliances(property_id)
        html = resolve_html_path(None, kind="property", ident=property_id)
        due = maintenance_due_by_appliance(registry, [a["id"] for a in appliances],
                                           replay=graph_path == config.REPLAY_GRAPH_PATH)
        page = render_property_page(property_id, registry, graph=graph, maintenance_due=due,
                                    generated_at=as_of.isoformat(), recalls_dir=config.RECALLS_DIR)
        print(f"advisor property sweep: {property_id}, {len(appliances)} appliances, graph {graph_path}, "
              f"as of {as_of.isoformat()} (UTC)")
        for row in sweep.sweep_rows(graph, appliances, as_of):
            for line in sweep.row_lines(row):
                print(line)
        html.parent.mkdir(parents=True, exist_ok=True)
        html.write_text(page, encoding="utf-8")
        print(f"html: {html}")
    print(SWEEP_PAGES_NOTE if researched else SWEEP_OFFLINE_NOTE)


def sweep_research(args: argparse.Namespace, environ: Mapping[str, str], registry, targets: Sequence[str],
                   graph_path: Path) -> int:
    """--research: refuse invented units, plan, then one bounded lookup per eligible unit.

    Replay reads one cassette per unit (--cassette, matched by maker and
    model); a unit with none is skipped. A live sweep opens the ledger, prints
    the preflight for the planned lookups and continues only on a typed
    "proceed" (LiveSession), so nothing is called or saved without it. Each
    live lookup's replies are captured, and its recording candidate is written
    after it, as a live ask's is.

    All the lookups share one run cap for the whole sweep (lifecycle.SweepBudget):
    each runs under what is left of it, and one whose worst case it cannot
    cover is not started and saves nothing. A replay sweep is priced and capped
    as its cassettes were recorded (lifecycle.sweep_price_mode).
    """
    mode = resolve_mode(environ, live=args.live)
    if mode != "replay" and args.cassette:
        raise CliRefusal("--cassette is accepted only in replay mode, not with --live.")
    from types import SimpleNamespace

    from agent import lifecycle
    from agent.kg import KnowledgeGraph
    from agent.replay.cassettes import lookup_recording_state

    kg = KnowledgeGraph.load(graph_path, registry=config.REGISTRY_PATH)
    appliances = [a for property_id in targets for a in registry.list_appliances(property_id)]
    decisions = lifecycle.eligibility(kg, appliances)
    print(f"advisor property sweep --research: lifecycle lookups (discontinuation and successors only), mode {mode}")
    for decision in decisions:
        print(lifecycle.decision_line(decision))
    eligible = [d["appliance"] for d in decisions if d["decision"] == lifecycle.ELIGIBLE]
    if not eligible:
        print("no unit needs a lifecycle lookup; nothing was called")
        return EXIT_OK
    if mode == "replay":
        cassettes = [_load_cassette(Path(p).expanduser()) for p in args.cassette or []]
        try:  # priced as the cassettes were recorded (ledger.price_basis)
            price_mode = lifecycle.sweep_price_mode(mode, [c for c in (lifecycle.cassette_for(a, cassettes)
                                                                        for a in eligible) if c is not None])
        except ValueError as exc:
            raise CliRefusal(str(exc)) from None
        plan = lifecycle.sweep_plan(price_mode, eligible)
        budget = lifecycle.SweepBudget(mode, price_mode)  # one run cap for the whole sweep
        for line in lifecycle.plan_lines(plan, len(eligible), price_mode):
            print(line)
        for appliance in eligible:
            cassette = lifecycle.cassette_for(appliance, cassettes)
            if cassette is None:
                print(f"  {lifecycle.unit_label(appliance)}: no cassette for this unit in replay; nothing looked up")
                continue
            if not budget.covers_next():  # replay: not started, nothing saved
                for line in lifecycle.result_lines(lifecycle.cap_reached_result(appliance, budget)):
                    print(line)
                continue
            ctx = run_context(lifecycle.new_run_id(), mode, cassette)
            ctx.caps = budget.caps_for(ctx.caps)
            result = lifecycle.lookup(appliance, ctx)
            budget.record(result)
            for line in lifecycle.result_lines(result):
                print(line)
        return EXIT_OK
    plan = lifecycle.sweep_plan(mode, eligible)
    budget = lifecycle.SweepBudget(mode)  # one run cap for the whole sweep
    ledger = open_live_ledger(mode, new_ledger=args.new_ledger, need=lifecycle.KEY_NAMES,
                              need_usd=plan.worst_usd, need_credits=plan.max_credits, environ=environ)
    session = LiveSession("property sweep", mode, ledger, need=lifecycle.KEY_NAMES, uses_registry=True)
    with session.active():
        try:
            session.confirm([plan], extra=[lifecycle.later_routes_line(graph_path),
                                           lifecycle.cap_scope_line(plan, len(eligible), mode)])
            for appliance in eligible:
                if not budget.covers_next():  # live: not started, nothing saved
                    for line in lifecycle.result_lines(lifecycle.cap_reached_result(appliance, budget)):
                        print(line)
                    continue
                ctx = run_context(lifecycle.new_run_id(), mode, None, secrets=session.secrets)
                ctx.caps = budget.caps_for(ctx.caps)
                try:
                    with _live_capture(session, ctx.run_id, "lookup"):
                        result = lifecycle.lookup(appliance, ctx)
                finally:
                    session.scrub(ctx)
                budget.record(result)
                for line in lifecycle.result_lines(result):
                    print(line)
                # Its recording candidate: a lifecycle cassette a replay sweep matches to this unit.
                session._record(SimpleNamespace(state=lookup_recording_state(
                    lifecycle.identity_of(appliance), result, research_limits=lifecycle.LIMITS_ROW)), ctx)
        except CliRefusal as refusal:
            print(f"advisor: refused: {refusal}", file=sys.stderr)
            return EXIT_REFUSED
        except Exception as exc:
            return _live_failure("property sweep", exc)
    return EXIT_OK


# ---------------------------------------------------------------------------
# advisor property recalls (SC15, decision 71): the product recall check in
# agent/product_recalls.py. Offline it prints each appliance's latest stored
# check; --live queries CPSC, which is free and needs no key, so it opens no
# ledger and reads no ADVISOR_MODE, but still waits for a typed "proceed".
# ---------------------------------------------------------------------------

RECALLS_RUN_PREFIX = "recalls-"


def cmd_property_recalls(args: argparse.Namespace, environ: Mapping[str, str]) -> int:
    """Print every appliance's latest stored product recall check, or with --live run the check first."""
    ensure_registry()
    from agent import product_recalls
    from agent.registry import Registry

    registry = Registry(config.REGISTRY_PATH)
    targets = sweep_targets(registry, args)
    appliances = [a for property_id in targets for a in registry.list_appliances(property_id)]
    if args.live:
        return recalls_live(appliances)
    for a in appliances:
        result = product_recalls.stored_result(config.RECALLS_DIR, a)
        checked = str(result.get("checked_at") or "")[:10] or "never"
        print(f"{a.get('id')}: latest stored check {checked}")
        for line in product_recalls.text_lines(result):
            print(f"  {line}")
    print("the product recall check spent nothing and sent nothing: it read the stored checks only")
    return EXIT_OK


def recalls_live(appliances: Sequence[Mapping[str, object]]) -> int:
    """Plan, read the typed "proceed", then query CPSC per appliance, store each check and log the run."""
    import json
    import uuid

    from agent import product_recalls
    from agent.build_info import build_id
    from agent.live.preflight import ask_to_proceed
    from agent.nodes.persist import RUNS_DIRNAME

    refuse_registry_drift()  # the stored checks keep the model numbers compared, so the seed must be loaded
    if not ask_to_proceed(product_recalls.plan_lines(appliances, config.RECALLS_DIR), sys.stdin, sys.stdout):
        raise CliRefusal('the typed confirmation was not exactly "proceed"; nothing was sent.')
    run_id = f"{RECALLS_RUN_PREFIX}{uuid.uuid4().hex[:16]}"
    checks, stored, complete = [], [], True
    try:
        for a in appliances:
            if product_recalls.is_synthetic_maker(a):
                continue
            try:
                check = product_recalls.check_appliance(a)
            except ValueError as exc:  # a model that normalizes to nothing: refused before any request
                print(f"{a.get('id')}: not checked: {exc}")
                complete = False
                continue
            stored.append(product_recalls.store_check(config.RECALLS_DIR, check))
            checks.append(check)
            result = product_recalls.evaluate(check)
            complete = complete and not result["errors"]
            for line in product_recalls.text_lines(result):
                print(line)
            print(f"  stored: {stored[-1]}")
    except Exception as exc:  # requests went out: stop, but still log what ran (below)
        print(f"advisor property recalls: the check failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        print("Stop and report this; the checks stored so far are in the run record below.", file=sys.stderr)
        complete = False
    finally:
        # Written whatever happens, so every check stored so far is logged with the run.
        record = product_recalls.run_record(run_id, checks, stored, build_id())
        path = config.PAGES_DIR.parent / RUNS_DIRNAME / f"{run_id}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(record, indent=2, sort_keys=True), encoding="utf-8")
        print(f"run record: {path} (cost 0.0000 USD)")
    return EXIT_OK if complete else EXIT_LIVE_ERROR


def graph_stats_lines(environ: Mapping[str, str]) -> list[str]:
    """The lines `advisor graph stats` prints for the mode's graph: nodes by type, edges by kind, drops.

    ADVISOR_MODE picks the graph by the rule `property show` uses
    (property_graph_path): config.GRAPH_PATH in a live mode,
    config.REPLAY_GRAPH_PATH in replay. Registry derived nodes and edges
    (decision 45) are counted apart, because they are built at load time and
    never saved.
    """
    import json

    from agent.kg import DOCUMENTED_CAUSE, KnowledgeGraph, drops_path

    registry = config.REGISTRY_PATH if config.REGISTRY_PATH.exists() else None
    path = property_graph_path(environ)
    label = "replay graph (replay runs)" if path == config.REPLAY_GRAPH_PATH else "runtime graph (live runs)"
    lines = [f"{label}: {path}"]
    if not path.exists():
        lines.append("  no graph file yet")
    stats = KnowledgeGraph.load(path, registry=registry).stats()
    if stats["load_drops"]:
        lines.append(f"  saved edges dropped at load (failed a check; see the drop log): {stats['load_drops']}")
    lines.append("  nodes: " + _counts(stats["nodes"]))
    lines.append("  edges: " + _counts(stats["edges"]))
    # Symptom level knowledge (decision D2): route row 3 needs config.GRAPH_ONLY_MIN_CAUSES of them.
    lines.append(f"  documented causes: {stats['edges'].get(DOCUMENTED_CAUSE, 0)} edges, "
                 f"{stats['nodes'].get('cause', 0)} causes")
    lines.append("  registry nodes (not saved): " + _counts(stats["registry_nodes"]))
    lines.append("  registry edges (not saved): " + _counts(stats["registry_edges"]))
    drops = drops_path(path)
    dropped = [ln for ln in drops.read_text(encoding="utf-8").splitlines() if ln.strip()] if drops.exists() else []
    lines.append(f"  edges dropped for failed evidence: {len(dropped)}")
    reasons: dict[str, int] = {}
    for line in dropped:
        reason = json.loads(line).get("reason") or "unknown"
        reasons[reason] = reasons.get(reason, 0) + 1
    if reasons:
        lines.append("    by reason: " + _counts(dict(sorted(reasons.items()))))
    return lines


def _counts(counts: Mapping[str, int]) -> str:
    return ", ".join(f"{k} {v}" for k, v in counts.items()) if counts else "none"


def cmd_graph_stats(args: argparse.Namespace, environ: Mapping[str, str]) -> int:
    """Print node and edge counts by type and the edges dropped for failed evidence (PLAN 8.13)."""
    for line in graph_stats_lines(environ):
        print(line)
    return EXIT_OK


def cmd_ledger(args: argparse.Namespace, environ: Mapping[str, str]) -> int:
    """Print run and build spend, credits, remaining budget and estimated rows.

    Reads config.LEDGER_PATH and never creates it (decision 52): with no ledger
    file it says so and exits 0, since nothing has been spent.
    """
    path = config.LEDGER_PATH
    if not path.exists():
        print(f"advisor ledger: no ledger file at {path}; nothing has been recorded yet.")
        return EXIT_OK
    # agent.ledger imports langchain_core, so it loads only after the scrub.
    from agent.ledger import Ledger

    from agent.ledger import LedgerMissing

    try:
        ledger = Ledger(path)
    except LedgerMissing as exc:
        raise CliRefusal(f"{exc}; nothing is read from it.") from None
    for line in ledger_report(ledger, run_id=args.run_id):
        print(line)
    if args.run_id:
        # Tokens by node, actual against the estimate, and R4 (Phase 5 report; decision 24).
        from agent.live.report import run_report_lines

        for line in run_report_lines(ledger, args.run_id):
            print(line)
    return EXIT_OK


def ledger_report(ledger: Ledger, *, run_id: str | None = None) -> list[str]:
    """The lines `advisor ledger` prints, built from one Ledger."""
    rows = ledger.rows(run_id)
    build_spent = ledger.build_total()
    build_credits = ledger.build_credits()
    lines = [
        f"ledger: {ledger.path}",
        f"build spend (live runs only): {_usd(build_spent)} of {_usd(config.BUILD_CAP_USD)}, "
        f"remaining {_usd(config.BUILD_CAP_USD - build_spent)}",
        f"build Tavily credits: {build_credits} of {config.BUILD_CREDIT_CAP}, "
        f"remaining {config.BUILD_CREDIT_CAP - build_credits}",
    ]
    runs: dict[str, str] = {}
    for row in rows:
        runs.setdefault(row["run_id"], row["mode"])
    lines.append(f"runs: {len(runs)}")
    for rid, mode in runs.items():
        stops = [r["note"] for r in rows if r["run_id"] == rid and r["kind"] == "stop"]
        stop_text = f", stopped: {stops[-1]}" if stops else ""
        lines.append(
            f"  {rid} [{mode}]: {_usd(ledger.run_total(rid))}, "
            f"{ledger.run_credits(rid)} credits{stop_text}"
        )
    estimated = [r for r in rows if r["kind"] == "charge" and r["estimated"]]
    lines.append(f"estimated rows (reconcile against the console): {len(estimated)}")
    for row in estimated:
        lines.append(
            f"  #{row['id']} {row['run_id']} {row['node'] or ''} {_usd(row['usd'])}: {row['note']}"
        )
    return lines


def _usd(amount: float) -> str:
    return f"{amount:.4f} USD"


# ---------------------------------------------------------------------------
# eval (the LIVE evaluations, decision 49): agent/live/eval_sc3b.py,
# agent/live/eval_sc7b.py and agent/live/eval_plates.py. Full mode is refused in
# eval_sc3b.check_mode and, for every live command, in refuse_unapproved_full.
# ---------------------------------------------------------------------------


def add_eval_flags(p: argparse.ArgumentParser) -> None:
    """Flags `advisor eval` adds beyond `which` and --live. There is no flag that skips the typed proceed."""
    p.add_argument(
        "--new-ledger", action="store_true",
        help="create the spend ledger if it is missing (it is never recreated silently)",
    )
    p.add_argument(
        "--fix", metavar="DEFECT",
        help="a rerun after a logged code fix: name the defect (decision 35)",
    )
    p.add_argument(
        "--score", action="store_true",
        help="score the run records already in data/eval and print the result; spends nothing",
    )
    p.add_argument("--wording", type=int, metavar="N", help="sc12a: the Jev question wording to ask or score")
    p.add_argument("--heldout", action="store_true",
                   help="sc12a: the held out half (after the lock; scored once)")
    p.add_argument("--rescore-reason", metavar="TEXT",
                   help="sc12a: the recorded reason for scoring the held out half again")


SC12A_FLAGS = ("wording", "heldout", "rescore_reason")


def eval_score(which: str, records: list[dict], first_lookups: list[dict] | None = None, *,
               planned: bool = False) -> tuple[dict, list[str]]:
    """Score run records with the pure evaluators (decisions 33, 34, 35): (score, printed lines).

    With planned=True, SC7b also requires a finished run for every repeat
    eval_sc7b plans (the commands always pass it).
    """
    from agent.live import evaluators

    if which == "sc3b":
        score = evaluators.score_sc3b(records)
        return score, evaluators.format_sc3b(score)
    if which == "plates":
        score = evaluators.score_plates(records)
        return score, evaluators.format_plates(score)
    if which != "sc7b":
        raise CliRefusal(f"no evaluator scores suite {which!r} from run records.")
    repeats = None
    if planned:
        from agent.live.eval_sc7b import planned_repeats

        repeats = planned_repeats()
    score = evaluators.score_sc7b(records, first_lookups or [], repeats)
    return score, evaluators.format_sc7b(score)


def recorder_blocked_calls(script: Sequence[Mapping[str, object]], results: Sequence[Mapping[str, object]],
                           limits_key: str | None) -> dict[str, int]:
    """Offline tests reach the recorder through cli (agent.live stays out of test imports)."""
    from agent.live.recorder import blocked_calls

    return blocked_calls(script, results, limits_key)


def recorder_in_call_order(lookups: Sequence[Mapping[str, object]],
                           script: Sequence[Mapping[str, object]]) -> list[tuple[int, Mapping[str, object]]]:
    """Offline tests reach the recorder through cli (agent.live stays out of test imports)."""
    from agent.live.recorder import in_call_order

    return in_call_order(lookups, script)


def v1_case_checks(case: str, state: Mapping[str, object], raw_extraction: Mapping[str, object] | None = None
                   ) -> tuple[dict, list[str]]:
    """The PLAN 6.5 checks of one v1 case on a run's final state: (result, printed lines)."""
    from agent.live import evaluators

    result = evaluators.v1_case_checks(case, state, raw_extraction)
    return result, evaluators.format_v1_checks([result])


def cmd_eval_score(args: argparse.Namespace) -> int:
    """`advisor eval sc3b|sc7b --score`: print the pooled result of the records in data/eval; no call is made."""
    if args.live or args.fix or args.new_ledger:
        raise CliRefusal("--score only reads run records; leave out --live, --fix and --new-ledger.")
    from agent.live.eval_sc3b import load_records

    records = load_records(args.which)
    firsts = None
    if args.which == "sc7b":
        from agent.live.eval_sc7b import external_first_lookups
        from agent.live.evaluators import reported_records

        # Outside first lookups of the build whose repeats are reported (decision 35).
        firsts = external_first_lookups(reported_records(records)["build_id"])
    _, lines = eval_score(args.which, records, firsts, planned=True)
    for line in lines:
        print(line)
    return EXIT_OK


def cmd_eval_live(args: argparse.Namespace, environ: Mapping[str, str]) -> int:
    """`advisor eval SUITE --live` (PLAN 8.13), or --score; every suite has its own branch."""
    if args.which != "sc12a" and any(getattr(args, flag, None) for flag in SC12A_FLAGS):
        raise CliRefusal("--wording, --heldout and --rescore-reason apply to sc12a only.")
    if args.which == "sc12a":
        return cmd_eval_sc12a(args, environ)
    if args.which == "sc12b":
        if args.score:
            return cmd_eval_sc12b_score(args)
        from agent.live.sc12b import cmd_eval_sc12b

        return cmd_eval_sc12b(args, environ)
    if args.score:
        return cmd_eval_score(args)
    if args.which == "sc3b":
        from agent.live.eval_sc3b import cmd_eval_sc3b

        return cmd_eval_sc3b(args, environ)
    if args.which == "plates":
        from agent.live.eval_plates import cmd_eval_plates

        return cmd_eval_plates(args, environ)
    if args.which == "sc7b":
        from agent.live.eval_sc7b import cmd_eval_sc7b

        return cmd_eval_sc7b(args, environ)
    raise CliRefusal(f"unknown eval suite {args.which!r}; the suites are {', '.join(EVAL_SUITES)}.")


# ---------------------------------------------------------------------------
# SC12a and SC12b (safety step flagging): the labeled set, its lock and the
# scorer are offline (agent/safety_eval); the paid halves are
# agent/live/sc12a.py and agent/live/sc12b.py.
# ---------------------------------------------------------------------------


def cmd_eval_sc12a(args: argparse.Namespace, environ: Mapping[str, str]) -> int:
    """`advisor eval sc12a [--live] [--wording N] [--heldout] [--rescore-reason TEXT]`.

    Without --live it scores recorded replies only: the tune half, or with
    --heldout the held out half, once, against a lock that matches the
    current config and prompt.
    """
    from agent.safety_eval import harness

    if args.live:
        if args.rescore_reason:
            raise CliRefusal("--rescore-reason is for scoring; --live only records replies.")
        from agent.live.sc12a import cmd_eval_sc12a as live_sc12a

        return live_sc12a(args, environ)
    resolve_mode(environ, live=False)
    if args.fix or args.new_ledger:
        raise CliRefusal("advisor eval sc12a without --live only scores recorded replies; leave out --fix and "
                         "--new-ledger.")
    if args.rescore_reason and not args.heldout:
        raise CliRefusal("--rescore-reason applies to --heldout only.")
    if args.heldout and args.wording is not None:
        raise CliRefusal("the held out half is scored with the locked wording only; leave out --wording.")
    try:
        result = harness.score_heldout(args.rescore_reason) if args.heldout else harness.score_tune(args.wording)
    except harness.EvalRefusal as exc:
        raise CliRefusal(str(exc)) from None
    for line in harness.format_result(result):
        print(line)
    return EXIT_OK


def cmd_eval_sc12b_score(args: argparse.Namespace) -> int:
    """`advisor eval sc12b --score`: the SC12b steps against their labels; no call is made."""
    if any((args.live, args.fix, args.new_ledger)):
        raise CliRefusal("--score only reads run records; leave out --live, --fix and --new-ledger.")
    from agent.safety_eval import harness, sc12b

    labels = harness.load_labels()
    baseline = None
    if harness.items_path().is_file():
        baseline = sc12b.writer_baseline(harness.load_items(), labels)
    for line in sc12b.format_sc12b(sc12b.score_sc12b(sc12b.load_records(), labels), baseline):
        print(line)
    return EXIT_OK


def cmd_safety_set(args: argparse.Namespace, environ: Mapping[str, str]) -> int:
    """`advisor safety-set build|packet|labels|lock`: offline, and never a paid call."""
    import json

    from agent.safety_eval import harness, pools, sc12b

    try:
        if args.safety_set_command == "build":
            if args.sc12b:
                items = sc12b.sc12b_items(sc12b.load_records())
                path, reused = harness.write_sc12b_items(items)
                print(f"advisor safety-set build --sc12b: {len(items)} steps in {path}; {reused} already have an "
                      f"SC12a label and are not labeled again; {len(items) - reused} go to the readers "
                      "(advisor safety-set packet --sc12b)")
                return EXIT_OK
            roots, documents = pools.Roots.from_config(), pools.load_document_list()
            print(f"maker document list {pools.MAKER_DOCUMENTS_PATH.name}: sha256 {documents.sha256}, "
                  f"status {documents.status!r}")
            if documents.status != "final":
                print("  note: the list is not marked final; a change to it after this build blocks --extend and "
                      "the lock until the set is built again")
            result = (harness.extend_set if args.extend else harness.build_set)(roots, documents)
            print(f"advisor safety-set build: {len(result.items)} items in {harness.items_path()}; IDs in "
                  f"{harness.COMMITTED_DIR / harness.ITEM_IDS}")
            lines = harness.counts_lines(harness.load_items(), harness.load_labels())
        elif args.safety_set_command == "packet":
            labels = harness.load_labels()
            if args.sc12b:
                if not harness.sc12b_items_path().is_file():
                    raise CliRefusal("no SC12b steps yet: run advisor safety-set build --sc12b first.")
                items = harness.read_json(harness.sc12b_items_path())["items"]
            else:
                items = harness.load_items()["items"]
            path, count = harness.write_reader_packet(items, labels, sc12b=args.sc12b)
            print(f"advisor safety-set packet: {count} unlabeled items for the readers in {path}; give each reader "
                  "PROTOCOL.md and this file, and nothing else")
            return EXIT_OK
        elif args.safety_set_command == "labels":
            harness.import_labels([Path(f) for f in args.files])
            print(f"advisor safety-set labels: labels by id in {harness.COMMITTED_DIR / harness.LABELS}")
            lines = harness.counts_lines(harness.load_items(), harness.load_labels())
        elif args.safety_set_command == "lock":
            lock = harness.write_lock(args.reason)
            print(f"advisor safety-set lock: Gate 2 written to {harness.lock_path()}; copy it into DECISION-LOG.md:")
            lines = json.dumps(lock, indent=2, sort_keys=True, ensure_ascii=False).splitlines()
        else:
            raise CliRefusal(f"unknown safety-set command {args.safety_set_command!r}")
    except harness.EvalRefusal as exc:
        raise CliRefusal(str(exc)) from None
    for line in lines:
        print(line)
    return EXIT_OK


def _dispatch(args: argparse.Namespace, environ: Mapping[str, str]) -> int:
    if args.command in ("ask", "resume"):
        return cmd_run(args, environ)
    if args.command == "property":
        if args.property_command == "sweep":
            return cmd_property_sweep(args, environ)
        if args.property_command == "recalls":
            return cmd_property_recalls(args, environ)
        return cmd_property_show(args, environ)
    if args.command == "graph":
        return cmd_graph_stats(args, environ)
    if args.command == "ledger":
        return cmd_ledger(args, environ)
    if args.command == "check-schema":
        return cmd_check_schema(args, environ)
    if args.command == "check-capabilities":
        return cmd_check_capabilities(args, environ)
    if args.command == "safety-set":
        return cmd_safety_set(args, environ)
    if args.command == "eval":
        return cmd_eval_live(args, environ)
    raise CliRefusal(f"unknown command {args.command!r}")


def main(argv: list[str] | None = None) -> int:
    """Entry point for the `advisor` console script."""
    # Repeat the scrub in case the environment changed after import; it is
    # idempotent and still precedes any langchain import in this process.
    global TRACING_OPTED_IN
    TRACING_OPTED_IN = scrub_tracing_env()
    # Section 8.2: set before any langgraph import (also set at import, below the scrub).
    os.environ[ENV_STRICT_MSGPACK] = "true"
    args = build_parser().parse_args(argv)
    try:
        return _dispatch(args, os.environ)
    except CliRefusal as refusal:
        print(f"advisor: refused: {refusal}", file=sys.stderr)
        return EXIT_REFUSED


if __name__ == "__main__":
    # Run as `python -m agent.cli`, this file is __main__ while the live modules
    # import agent.cli, so each has its own CliRefusal class. Delegating to the
    # imported module keeps one class, as the `advisor` entry point does.
    from agent.cli import main as _main

    sys.exit(_main())
