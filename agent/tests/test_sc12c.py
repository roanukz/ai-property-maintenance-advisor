"""SC12c, Phase 0: the blind labeling page, its local server, the seal, the score and the lock (decision 70).

Synthetic tests build their own SC12a tree under tmp_path. Tests that need
the real held out half copy data/eval/sc12a and the committed labels into
tmp_path first, so nothing here can write a real SC12a file; they skip, with
the reason, when data/ is absent. The mutation gate leaves data/ out of its
copies and names the repo's own data folder in ADVISOR_PHASE5_DATA_DIR,
which these tests read, never write. The server runs on a Unix socket: the
test process may open no TCP socket (pytest-socket). No call of any kind is
made. Each test names the mutation that turns it red.
"""

from __future__ import annotations

import hashlib
import html
import json
import os
import re
import shutil
import socket
import threading
from fractions import Fraction
from pathlib import Path
from typing import Any

import pytest

from agent import build_info, config
from agent.prompts import SAFETY_STEP_NOUL
from agent.safety_eval import harness, sc12c, stats

REAL_DATA = Path(os.environ.get("ADVISOR_PHASE5_DATA_DIR") or config.REPO_ROOT / "data")
REAL_SC12A = REAL_DATA / "eval" / "sc12a"
NO_DATA = "needs the SC12a held out half under data/eval/sc12a (gitignored, absent here)"
SAFETY_EVAL_DIR = config.REPO_ROOT / "agent" / "safety_eval"
PROTOCOL = SAFETY_EVAL_DIR / "PROTOCOL.md"
HOST = "127.0.0.1:8770"

# Keys that would carry an AI label, a score, a word rule result or the set's
# structure; none may appear in the page's data.
FORBIDDEN_KEYS = {"label", "hazard", "safety", "positive", "noul", "votes", "writer_flag", "writer", "group",
                  "half", "family", "family_sha256", "pool", "sources", "document", "document_sha256", "sha",
                  "sha256", "page", "page_sha256", "order", "occurrences", "word_published", "word_tuned"}
# Words and values of the AI labels and the layers, in the page's own text (outside the protocol and items).
FORBIDDEN_CHROME = ("positive", "noul", "0.51", "word rule", "word_rule", "jev", "majority", "writer", "reader",
                    "threshold", '"label"', '"half"', '"family"', '"group"', '"sha256"', "page_sha", "tune",
                    "heldout", "held out", "pool", "votes")
# Never anywhere in the page, item text included.
FORBIDDEN_ANYWHERE = ("noul", "jev", "0.51", "word rule", "majority")


# ---------------------------------------------------------------------------
# Cohen's kappa
# ---------------------------------------------------------------------------


def test_cohen_kappa_matches_hand_computed_tables() -> None:
    """Hand computed: kappa = (po - pe) / (1 - pe), pe from each rater's own totals.

    [[45, 15], [25, 15]]: po 60/100, pe 0.6*0.7 + 0.4*0.3 = 0.54, kappa 0.06/0.46 = 3/23.
    [[20, 5], [10, 15]]: po 35/50, pe 0.5*0.6 + 0.5*0.4 = 0.5, kappa 2/5.
    An empty cell, [[10, 0], [5, 5]]: po 15/20, pe 0.5*0.75 + 0.5*0.25 = 0.5, kappa 1/2.
    Three categories, [[3, 1, 0], [0, 2, 1], [1, 0, 2]]: po 7/10, pe (16 + 9 + 9)/100,
    kappa (0.70 - 0.34)/0.66 = 6/11. Perfect agreement is 1, agreement at chance is 0, and one category for
    every item (pe 1) or no items has no kappa.

    Mutations: stats_kappa_chance_from_one_rater (chance from the first
    rater's totals squared), stats_kappa_undefined_divides (pe 1 divides by zero)."""
    assert stats.cohen_kappa([[45, 15], [25, 15]]) == Fraction(3, 23)
    assert stats.cohen_kappa([[20, 5], [10, 15]]) == Fraction(2, 5)
    assert stats.cohen_kappa([[10, 0], [5, 5]]) == Fraction(1, 2)
    assert stats.cohen_kappa([[10, 0], [0, 5]]) == 1
    assert stats.cohen_kappa([[25, 25], [25, 25]]) == 0
    assert stats.cohen_kappa([[3, 1, 0], [0, 2, 1], [1, 0, 2]]) == Fraction(6, 11)
    assert stats.cohen_kappa([[7, 0], [0, 0]]) is None
    assert stats.cohen_kappa([[0, 0], [0, 0]]) is None
    assert stats.agreement_table([("yes", "no"), ("yes", "yes"), ("no", "no")], ("yes", "no")) == [[1, 1], [0, 1]]
    with pytest.raises(ValueError):
        stats.cohen_kappa([[1, 2]])


# ---------------------------------------------------------------------------
# A synthetic SC12a tree
# ---------------------------------------------------------------------------

QHASH = "q" * 16


def _id(name: str) -> str:
    return hashlib.sha256(name.encode("utf-8")).hexdigest()


# name: (step, AI majority (label, hazard), Jev's answer, Roanuk's answer (safety, hazard))
SYNTHETIC = {
    "A": ("Turn off power at the breaker before opening the panel", ("yes", "electrical"), 0.95, ("yes", "electrical")),
    "B": ("Open the equipment door to reach the heater", ("yes", "heat"), 0.70, ("yes", "heat")),
    "C": ("Check the water level in the spa", ("no", "other"), 0.10, ("no", "other")),
    "D": ("Replace the filter cartridge", ("no", "none"), 0.60, ("yes", "electrical")),
    "E": ("Remove the access box cover", ("yes", "electrical"), 0.40, ("no", "none")),
    "F": ("Let the heat settle before checking", ("no", "other"), 0.20, ("yes", "other")),
}
# Against the AI majority (positives A, B, E): Jev flags A, B, D; the published list A, B, F; the tuned list A, B, E.
SYNTHETIC_AI_ARMS = {sc12c.JEV_ARM: (2, 3, 3), sc12c.PUBLISHED_ARM: (2, 3, 3), sc12c.TUNED_ARM: (3, 3, 3)}


def _item(name: str, step: str, half: str = "heldout") -> dict[str, Any]:
    return {"id": _id(name), "appliance": "hot tub", "step": step, "detail": "", "half": half, "pool": "D",
            "family": f"family-{name}", "family_sha256": _id("f" + name), "group": f"group-{name}",
            "writer_flag": None, "page_sha256": _id("p" + name), "document_sha256": _id("d" + name),
            "document": f"https://example.com/{name}", "sources": [f"https://example.com/{name}"],
            "occurrences": 1, "order": 0}


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


@pytest.fixture
def synthetic(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Six held out items and two tune items, their AI majority labels, Jev replies and a lock, under tmp_path."""
    eval_dir = tmp_path / "eval"
    items = [_item(name, step) for name, (step, *_rest) in SYNTHETIC.items()]
    items += [_item("T1", "Tune half step one", "tune"), _item("T2", "Tune half step two", "tune")]
    _write_json(eval_dir / "sc12a" / "items.json", {"items": items})
    labels = {_id(name): {"label": label, "positive": label == "yes", "hazard": hazard,
                          "votes": {f"reader-{n}": {"safety": label, "hazard": hazard} for n in (1, 2, 3)}}
              for name, (_, (label, hazard), _, _) in SYNTHETIC.items()}
    committed_sc12a = tmp_path / "committed_sc12a"
    _write_json(committed_sc12a / "labels.json", {"labels": labels})
    replies = eval_dir / "sc12a" / "replies" / f"{QHASH}.jsonl"
    replies.parent.mkdir(parents=True)
    replies.write_text("".join(json.dumps({"item_id": _id(name), "noul": noul, "question_hash": QHASH,
                                           "model": config.JEV_MODEL, "error": None}) + "\n"
                               for name, (_, _, noul, _) in SYNTHETIC.items()), encoding="utf-8")
    lock = eval_dir / "sc12a" / "lock.json"
    _write_json(lock, {"question_hash": QHASH, "thresholds": {"jev": {"rule": "full_recall", "threshold": 0.51}}})
    monkeypatch.setattr(config, "EVAL_DIR", eval_dir)
    monkeypatch.setattr(harness, "COMMITTED_DIR", committed_sc12a)
    monkeypatch.setattr(sc12c, "COMMITTED_DIR", tmp_path / "committed")
    monkeypatch.setattr(sc12c, "LOCK_FILE_SHA256", hashlib.sha256(lock.read_bytes()).hexdigest())
    monkeypatch.setattr(sc12c, "PUBLISHED_AI_ARMS", SYNTHETIC_AI_ARMS)
    return tmp_path


def _answer(name: str, safety: str, hazard: str) -> None:
    sc12c.append_answer(sc12c.answers_path(), {"id": _id(name), "safety": safety, "hazard": hazard})


def _answer_all_synthetic() -> None:
    _answer("D", "no", "none")  # changed below: one changed answer
    for name, (*_rest, (safety, hazard)) in SYNTHETIC.items():
        _answer(name, safety, hazard)


# ---------------------------------------------------------------------------
# The page
# ---------------------------------------------------------------------------


def _data_block(page: str, block_id: str) -> Any:
    found = re.findall(rf'<script type="application/json" id="{block_id}">(.*?)</script>', page, re.DOTALL)
    assert len(found) == 1, block_id
    return json.loads(found[0])


def _pre(page: str, pre_id: str) -> str:
    found = re.findall(rf'<pre id="{pre_id}">(.*?)</pre>', page, re.DOTALL)
    assert len(found) == 1, pre_id
    return html.unescape(found[0])


def _protocol_slice(heading: bytes) -> bytes:
    """The bytes under one heading of PROTOCOL.md, up to the next "## " heading, blank lines around it cut."""
    raw = PROTOCOL.read_bytes()
    start = raw.index(b"\n" + heading + b"\n") + len(heading) + 2
    end = raw.find(b"\n## ", start)
    return raw[start:end if end != -1 else len(raw)].strip(b"\n")


def _chrome(page: str) -> str:
    """The page less the protocol section and the items' data: the page's own words."""
    page = re.sub(r'<section id="protocol".*?</section>', "", page, flags=re.DOTALL)
    return re.sub(r'<script type="application/json" id="items-data">.*?</script>', "", page, flags=re.DOTALL)


def _keys(value: Any) -> set[str]:
    if isinstance(value, dict):
        return set(value) | {k for v in value.values() for k in _keys(v)}
    if isinstance(value, list):
        return {k for v in value for k in _keys(v)}
    return set()


def test_page_shows_the_protocol_and_worked_examples_byte_for_byte(synthetic: Path) -> None:
    """Mutation: sc12c_protocol_rendered_not_verbatim (the bold markers are stripped)."""
    sc12c.build(seed="fixed")
    page = sc12c.page_path().read_text(encoding="utf-8")
    label = _pre(page, "protocol-label").encode("utf-8")
    examples = _pre(page, "protocol-examples").encode("utf-8")
    assert label == _protocol_slice(b"## Label protocol")
    assert examples == _protocol_slice(b"## Worked examples, all from published briefs")
    assert label.startswith(b"> You will see maintenance steps") and b"**Hazard:**" in label
    rows = [line for line in examples.split(b"\n") if line.startswith(b"|")][2:]
    assert len(rows) == 6
    assert "<h2>Label protocol</h2>" in page and "<h2>Worked examples, all from published briefs</h2>" in page


def test_page_payload_is_the_reader_keys_and_an_index_only(synthetic: Path) -> None:
    """The items carry every field items.json holds; the page carries id, appliance, step, detail and index.

    Mutation: sc12c_payload_leaks_the_half (the payload adds the item's half)."""
    sc12c.build(seed="fixed")
    page = sc12c.page_path().read_text(encoding="utf-8")
    shown = _data_block(page, "items-data")
    assert _data_block(page, "recorded-data") == {}
    assert len(re.findall(r'<script type="application/json"', page)) == 2
    by_id = {i["id"]: i for i in harness.load_items()["items"]}
    assert len(shown) == len(SYNTHETIC) and [i["index"] for i in shown] == list(range(1, len(SYNTHETIC) + 1))
    for item in shown:
        assert set(item) == {"id", "appliance", "step", "detail", "index"}
        assert {k: item[k] for k in harness.READER_KEYS} == {k: by_id[item["id"]][k] for k in harness.READER_KEYS}
        assert by_id[item["id"]]["half"] == "heldout"
    assert not _keys(shown) & FORBIDDEN_KEYS
    chrome = _chrome(page).lower()
    for word in FORBIDDEN_CHROME:
        assert word not in chrome, word
    for word in FORBIDDEN_ANYWHERE:
        assert word not in page.lower(), word


def test_page_order_comes_from_the_recorded_seed_and_holds_on_rebuild(synthetic: Path) -> None:
    """Mutations: sc12c_order_not_shuffled (sorted by id), sc12c_rebuild_draws_a_new_seed."""
    info = sc12c.build()
    order = [i["id"] for i in _data_block(sc12c.page_path().read_text(encoding="utf-8"), "items-data")]
    assert order == info["order"] == sc12c.ordered_ids(order, info["order_seed"])
    assert order != sorted(order) and sorted(order) == sorted(_id(n) for n in SYNTHETIC)
    assert sc12c.ordered_ids(order, "fixed") != sorted(order)
    again = sc12c.build()
    assert again["order_seed"] == info["order_seed"] and again["order"] == order
    with pytest.raises(sc12c.Sc12cRefusal, match="order is fixed"):
        sc12c.build(seed="another")


def _csp(page: str) -> str:
    found = re.findall(r'<meta http-equiv="Content-Security-Policy" content="([^"]*)">', page)
    assert len(found) == 1
    return html.unescape(found[0])


def _hash(source: str) -> str:
    import base64

    return "sha256-" + base64.b64encode(hashlib.sha256(source.encode("utf-8")).digest()).decode("ascii")


def test_page_loads_nothing_and_calls_only_its_own_answer_endpoint(synthetic: Path) -> None:
    """Mutations: sc12c_csp_connects_anywhere (connect-src *), sc12c_page_loads_a_web_font (a stylesheet link)."""
    sc12c.build(seed="fixed")
    page = sc12c.page_path().read_text(encoding="utf-8")
    lowered = page.lower()
    for banned in ("http://", "https://", "//cdn", "<link", "<img", "<iframe", "<object", "<embed", " src=",
                   " href=", "@import", "url(", "xmlhttprequest", "websocket", "eventsource", "sendbeacon",
                   "import(", "eval(", "new function", "window.open", "<form"):
        assert banned not in lowered, banned
    assert re.findall(r"fetch\(([^,)]*)", page) == ['"/answer"']
    csp = _csp(page)
    directives = dict(d.strip().split(" ", 1) for d in csp.split(";"))
    assert directives["default-src"] == "'none'" and directives["connect-src"] == "'self'"
    assert directives["base-uri"] == "'none'" and directives["form-action"] == "'none'"
    scripts = re.findall(r"<script>(.*?)</script>", page, re.DOTALL)
    styles = re.findall(r"<style>(.*?)</style>", page, re.DOTALL)
    assert len(scripts) == 1 and len(styles) == 1
    assert directives["script-src"] == f"'{_hash(scripts[0])}'"
    assert directives["style-src"] == f"'{_hash(styles[0])}'"
    assert ' style="' not in lowered and "localstorage" in lowered and "try {" in page
    assert '<meta name="viewport" content="width=device-width, initial-scale=1">' in page
    for value in ("yes", "no", "electrical", "heat", "gas", "other", "none"):
        attr = "data-safety" if value in ("yes", "no") else "data-hazard"
        assert f'<button type="button" {attr}="{value}"' in page
    for control in ('id="back"', 'id="next"', 'id="progress"'):
        assert control in page


# ---------------------------------------------------------------------------
# The local server, on a Unix socket
# ---------------------------------------------------------------------------

SOCKET_NAME = "sc12c.sock"


@pytest.fixture
def server(synthetic: Path, monkeypatch: pytest.MonkeyPatch):
    """The answer server on a Unix socket in tmp_path (a relative name keeps the path short)."""
    sc12c.build(seed="fixed")
    page, known = sc12c.serve_state()
    monkeypatch.chdir(synthetic)
    srv = sc12c.UnixAnswerServer(SOCKET_NAME, page, known, sc12c.answers_path())
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    try:
        yield srv
    finally:
        srv.shutdown()
        srv.server_close()
        thread.join(timeout=5)


def _request(method: str, path: str, *, host: str | None = HOST, body: bytes | None = None,
             headers: dict[str, str] | None = None) -> tuple[int, dict[str, str], bytes]:
    lines = [f"{method} {path} HTTP/1.0"]
    if host is not None:
        lines.append(f"Host: {host}")
    lines += [f"{k}: {v}" for k, v in (headers or {}).items()]
    if body is not None:
        lines.append(f"Content-Length: {len(body)}")
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
        conn.settimeout(10)
        conn.connect(SOCKET_NAME)
        conn.sendall(("\r\n".join(lines) + "\r\n\r\n").encode("latin-1") + (body or b""))
        data = b""
        while chunk := conn.recv(65536):
            data += chunk
    head, _, payload = data.partition(b"\r\n\r\n")
    status_line, *header_lines = head.decode("latin-1").split("\r\n")
    got = {k.strip().lower(): v.strip() for k, _, v in (h.partition(":") for h in header_lines)}
    return int(status_line.split()[1]), got, payload


def _post(entry: Any, **kw: Any) -> tuple[int, dict[str, str], bytes]:
    headers = {"Content-Type": "application/json", **kw.pop("headers", {})}
    body = entry if isinstance(entry, bytes) else json.dumps(entry).encode("utf-8")
    return _request("POST", "/answer", body=body, headers=headers, **kw)


def _logged() -> list[dict[str, Any]]:
    path = sc12c.answers_path()
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []


def test_server_serves_one_page_and_refuses_other_paths_methods_and_hosts(server) -> None:
    """Mutations: sc12c_server_any_host (the Host check passes everything),
    sc12c_server_serves_any_path (an unknown path gets the page),
    sc12c_server_any_method (a GET to /answer or a POST to / is let through)."""
    status, headers, body = _request("GET", "/")
    assert status == 200 and headers["content-type"].startswith("text/html")
    assert "default-src 'none'" in headers["content-security-policy"]
    assert body.decode("utf-8") == sc12c.served_page(server.page, {})
    assert _request("GET", "/", host="localhost:8770")[0] == 200
    assert _request("GET", "/", host="localhost")[0] == 200
    for path in ("/favicon.ico", "/index.html", "/answers", "/answer/", "/data/eval/sc12c/answers.jsonl", "/../"):
        assert _request("GET", path)[0] == 404, path
        assert _request("POST", path, body=b"{}", headers={"Content-Type": "application/json"})[0] == 404, path
    for method, path in (("POST", "/"), ("GET", "/answer"), ("PUT", "/answer"), ("DELETE", "/"),
                         ("PATCH", "/answer"), ("OPTIONS", "/answer"), ("HEAD", "/answer")):
        status, headers, _ = _request(method, path, body=b"{}" if method != "GET" else None)
        assert status == 405 and headers["allow"] in ("GET", "POST"), (method, path)
    for host in ("evil.example", "evil.example:8770", "127.0.0.1.evil.example", "localhost.evil.example:80",
                 "[::1]:8770", "0.0.0.0:8770", "localhost:", "127.0.0.1:x", None):
        assert _request("GET", "/", host=host)[0] == 403, host
        assert _post({"id": _id("A"), "safety": "yes", "hazard": "electrical"}, host=host)[0] == 403, host
    assert _logged() == []


def test_server_validates_answers_and_appends_each_one(server) -> None:
    """Mutations: sc12c_server_accepts_unknown_ids, sc12c_server_accepts_any_hazard, sc12c_server_any_origin."""
    a, d = _id("A"), _id("D")
    bad = [
        ({"id": _id("T1"), "safety": "yes", "hazard": "electrical"}, {}, 400),  # a tune item
        ({"id": "x" * 64, "safety": "yes", "hazard": "electrical"}, {}, 400),
        ({"id": a, "safety": "maybe", "hazard": "electrical"}, {}, 400),
        ({"id": a, "safety": "yes", "hazard": "fire"}, {}, 400),
        ({"id": a, "safety": "yes", "hazard": "Electrical"}, {}, 400),
        ({"id": a, "safety": "yes"}, {}, 400),
        ({"id": a, "safety": "yes", "hazard": "heat", "label": "yes"}, {}, 400),
        (["not", "an", "object"], {}, 400),
        (b"{not json", {}, 400),
        ({"id": a, "safety": "yes", "hazard": "heat"}, {"Content-Type": "text/plain"}, 415),
        ({"id": a, "safety": "yes", "hazard": "heat"}, {"Origin": "http://evil.example"}, 403),
        ({"id": a, "safety": "yes", "hazard": "heat"}, {"Origin": "null"}, 403),
        (b"{" + b" " * sc12c.MAX_BODY_BYTES + b"}", {}, 413),
    ]
    for entry, headers, expected in bad:
        assert _post(entry, headers=headers)[0] == expected, entry if not isinstance(entry, bytes) else entry[:20]
    assert _logged() == []
    # Any of the five hazards with either answer, as the readers could.
    status, _, body = _post({"id": a, "safety": "yes", "hazard": "other"},
                            headers={"Origin": "http://127.0.0.1:8770"})
    assert status == 200 and json.loads(body) == {"ok": True, "answered": 1, "items": len(SYNTHETIC)}
    assert _post({"id": d, "safety": "no", "hazard": "electrical"})[0] == 200
    assert _post({"id": a, "safety": "yes", "hazard": "electrical"})[0] == 200
    logged = _logged()
    assert [(e["id"], e["safety"], e["hazard"]) for e in logged] == [
        (a, "yes", "other"), (d, "no", "electrical"), (a, "yes", "electrical")]
    assert all(set(e) == {"id", "safety", "hazard", "at"} for e in logged)
    # A reload resumes from the server's record: the latest answer per item.
    page = _request("GET", "/")[2].decode("utf-8")
    assert _data_block(page, "recorded-data") == {a: ["yes", "electrical"], d: ["no", "electrical"]}


def test_serve_binds_127_0_0_1_on_its_fixed_port(synthetic: Path, monkeypatch: pytest.MonkeyPatch,
                                                  capsys: pytest.CaptureFixture[str]) -> None:
    """Mutation: sc12c_serve_binds_every_interface (HOST 0.0.0.0)."""
    bound = []

    class Recorder:
        def __init__(self, address, page, known, log) -> None:
            bound.append(address)
            self.latest = {}

        def serve_forever(self) -> None:
            raise KeyboardInterrupt

        def server_close(self) -> None:
            pass

    monkeypatch.setattr(sc12c, "AnswerServer", Recorder)
    assert sc12c.main(["serve"]) == sc12c.EXIT_REFUSED  # nothing built yet
    assert "build first" in capsys.readouterr().err and bound == []
    assert sc12c.main(["build"]) == sc12c.EXIT_OK
    assert sc12c.main(["serve"]) == sc12c.EXIT_OK
    assert bound == [("127.0.0.1", 8770)]
    assert "http://127.0.0.1:8770/" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# Seal, score and revise on the synthetic tree
# ---------------------------------------------------------------------------


def test_seal_refuses_an_incomplete_set_and_an_edited_log(synthetic: Path) -> None:
    """Mutations: sc12c_seal_allows_missing_answers, sc12c_seal_reads_any_log_line."""
    sc12c.build(seed="fixed")
    for name, (*_rest, (safety, hazard)) in list(SYNTHETIC.items())[:-1]:
        _answer(name, safety, hazard)
    with pytest.raises(sc12c.Sc12cRefusal, match="1 of 6 held out items have no answer"):
        sc12c.seal()
    assert not sc12c.committed(sc12c.HUMAN_LABELS).exists() and not sc12c.committed(sc12c.SEAL).exists()
    with sc12c.answers_path().open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"id": _id("F"), "safety": "yes", "hazard": "fire", "at": "x"}) + "\n")
    with pytest.raises(sc12c.Sc12cRefusal, match="hazard is one of"):
        sc12c.seal()
    assert not sc12c.committed(sc12c.HUMAN_LABELS).exists()


def test_score_refuses_before_the_seal_and_after_the_sealed_file_changes(synthetic: Path) -> None:
    """Mutation: sc12c_score_skips_the_seal_check (the sealed file's sha256 is not compared)."""
    sc12c.build(seed="fixed")
    _answer_all_synthetic()
    with pytest.raises(sc12c.Sc12cRefusal, match="not sealed"):
        sc12c.score()
    with pytest.raises(sc12c.Sc12cRefusal, match="not sealed"):
        sc12c.revise(_id("E"), "yes", "electrical", "a reason")
    sc12c.seal()
    with pytest.raises(sc12c.Sc12cRefusal, match="sealed once"):
        sc12c.seal()
    with pytest.raises(sc12c.Sc12cRefusal, match="only after scoring"):
        sc12c.revise(_id("E"), "yes", "electrical", "a reason")
    path = sc12c.committed(sc12c.HUMAN_LABELS)
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc["labels"][_id("E")]["safety"] = "yes"
    doc["labels"][_id("E")]["hazard"] = "electrical"
    path.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    with pytest.raises(sc12c.Sc12cRefusal, match="changed after it was sealed"):
        sc12c.score()
    assert not sc12c.committed(sc12c.SCORES).exists()


def test_synthetic_score_matches_hand_counts_and_revisions_never_change_it(synthetic: Path,
                                                                           capsys: pytest.CaptureFixture[str]) -> None:
    """Hand counts: Roanuk yes on A, B, D (F is yes with "other", so a no); AI yes on A, B, E.
    Table 2, 1 / 1, 2: 4 of 6 agree, kappa (2/3 - 1/2) / (1/2) = 1/3. Hazards agree on A, B, C, F.
    Against Roanuk: Jev 3 of 3 at 3 of 3, both word lists 2 of 3 at 2 of 3.

    Mutations: sc12c_score_reads_revisions (a revised label replaces the sealed
    one), sc12c_human_yes_other_counts (a yes with hazard "other" counts as a safety step)."""
    sc12c.build(seed="fixed")
    _answer_all_synthetic()
    sealed = sc12c.seal()
    assert sealed["labels"]["changed_answers"] == 1 and sealed["labels"]["items_changed"] == 1
    assert sealed["labels"]["answers_logged"] == len(SYNTHETIC) + 1 and sealed["labels"]["items"] == len(SYNTHETIC)
    assert sealed["labels"]["order_seed"] == "fixed"
    assert sealed["labels"]["labels"][_id("D")]["safety"] == "yes"  # the latest answer wins
    labels_bytes = sc12c.committed(sc12c.HUMAN_LABELS).read_bytes()

    def check(result: dict[str, Any]) -> None:
        s = result["safety_step_agreement"]
        assert s["table"] == {"roanuk_yes_ai_yes": 2, "roanuk_yes_ai_no": 1, "roanuk_no_ai_yes": 1,
                              "roanuk_no_ai_no": 2}
        assert (s["agree"], s["items"]) == (4, 6) and s["cohen_kappa"] == pytest.approx(1 / 3)
        assert s["percent"] == pytest.approx(400 / 6)
        assert (result["hazard_agreement"]["agree"], result["hazard_agreement"]["items"]) == (4, 6)
        roanuk = result["arms"]["against_roanuk"]
        assert {n: (a["tp"], a["flagged"], a["positives"]) for n, a in roanuk.items()} == {
            sc12c.JEV_ARM: (3, 3, 3), sc12c.PUBLISHED_ARM: (2, 3, 3), sc12c.TUNED_ARM: (2, 3, 3)}
        assert tuple(roanuk[sc12c.JEV_ARM]["recall_wilson95"]) == stats.wilson(3, 3)
        ai = result["arms"]["against_ai_majority"]
        assert {n: (a["tp"], a["flagged"], a["positives"]) for n, a in ai.items()} == SYNTHETIC_AI_ARMS
        assert [d["id"] for d in result["disagreements"]] == [_id("D"), _id("E")]
        assert result["disagreements"][0]["roanuk"] == {"safety": "yes", "hazard": "electrical", "safety_step": True}
        assert result["disagreements"][1]["ai_majority"]["label"] == "yes"

    first = sc12c.score()
    check(first)
    local = json.loads(sc12c.disagreements_path().read_text(encoding="utf-8"))
    assert [d["step"] for d in local["disagreements"]] == [SYNTHETIC["D"][0], SYNTHETIC["E"][0]]
    assert sc12c.main(["revise", _id("E"), "--safety", "yes", "--hazard", "electrical",
                       "--reason", "read again after scoring"]) == sc12c.EXIT_OK
    assert sc12c.committed(sc12c.HUMAN_LABELS).read_bytes() == labels_bytes
    capsys.readouterr()
    assert sc12c.main(["score"]) == sc12c.EXIT_OK
    out = capsys.readouterr().out
    second = json.loads(sc12c.committed(sc12c.SCORES).read_text(encoding="utf-8"))
    check(second)
    assert second["revisions"] == [{"id": _id("E"), "at": second["revisions"][0]["at"],
                                    "blind": {"safety": "no", "hazard": "none"}, "safety": "yes",
                                    "hazard": "electrical", "agrees_with_ai_majority": True}]
    assert "read again after scoring" in out and SYNTHETIC["E"][0] in out
    assert "4 of 6 agree" in out and "Cohen's kappa 0.333" in out
    assert second["answers_log_matches_seal"] is True and second["answers_after_seal"] == 0
    revised = json.loads(sc12c.committed(sc12c.REVISED).read_text(encoding="utf-8"))
    assert set(revised["revisions"][0]) == {"id", "at", "blind", "safety", "hazard"}
    assert "read again after scoring" not in sc12c.committed(sc12c.REVISED).read_text(encoding="utf-8")
    local = json.loads(sc12c.local_revisions_path().read_text(encoding="utf-8"))
    assert local["revisions"][0]["reason"] == "read again after scoring"
    # On a computer without the local file the score still lists the revision, without its reason.
    sc12c.local_revisions_path().unlink()
    capsys.readouterr()
    assert sc12c.main(["score"]) == sc12c.EXIT_OK
    assert "(reason kept locally)" in capsys.readouterr().out


def test_server_refuses_answers_after_the_seal(server) -> None:
    """Once sealed, a POST is refused (409) and nothing is appended, and the
    server will not start again. Mutations: sc12c_server_answers_after_seal
    (the POST is logged), sc12c_serve_after_seal (serve_state passes)."""
    for name, (*_rest, (safety, hazard)) in SYNTHETIC.items():
        assert _post({"id": _id(name), "safety": safety, "hazard": hazard})[0] == 200
    sc12c.seal()
    before = sc12c.answers_path().read_bytes()
    status, _, body = _post({"id": _id("E"), "safety": "yes", "hazard": "electrical"})
    assert status == 409 and json.loads(body) == {
        "ok": False, "error": "the labels are sealed; a later change is recorded with revise"}
    assert sc12c.answers_path().read_bytes() == before
    with pytest.raises(sc12c.Sc12cRefusal, match="the labels are sealed"):
        sc12c.serve_state()


def test_score_reports_answers_logged_after_the_seal(synthetic: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """An answer that reaches the log after the seal anyway (an older server,
    a hand edit) is counted and warned about, never scored. Mutation
    sc12c_score_ignores_answers_after_seal."""
    sc12c.build(seed="fixed")
    _answer_all_synthetic()
    sc12c.seal()
    _answer("E", "yes", "electrical")
    assert sc12c.main(["score"]) == sc12c.EXIT_OK
    result = json.loads(sc12c.committed(sc12c.SCORES).read_text(encoding="utf-8"))
    assert result["answers_log_matches_seal"] is False and result["answers_after_seal"] == 1
    assert result["safety_step_agreement"]["agree"] == 4  # the sealed answer to E, not the later one
    assert "answers.jsonl changed after the seal (1 answers logged past the sealed count)" in capsys.readouterr().out


def test_seal_is_never_repeated_after_a_score(synthetic: Path) -> None:
    """With a score on record, removing the committed labels, seal and scores
    does not let seal run again; with every score file gone too, the local copy
    of the seal still refuses. Mutations: sc12c_seal_again_after_score,
    sc12c_seal_again_without_local_record."""
    sc12c.build(seed="fixed")
    _answer_all_synthetic()
    sc12c.seal()
    sc12c.score()
    for name in (sc12c.HUMAN_LABELS, sc12c.SEAL, sc12c.SCORES):
        sc12c.committed(name).unlink()
    with pytest.raises(sc12c.Sc12cRefusal, match="a score already exists"):
        sc12c.seal()
    sc12c.disagreements_path().unlink()
    with pytest.raises(sc12c.Sc12cRefusal, match="records an earlier seal"):
        sc12c.seal()
    assert not sc12c.committed(sc12c.HUMAN_LABELS).exists() and not sc12c.committed(sc12c.SEAL).exists()


def test_build_never_draws_a_new_order_over_logged_answers(synthetic: Path) -> None:
    """With build.json lost and answers logged, build refuses to draw a new
    seed; the original seed restores the same order. Mutation
    sc12c_build_redraws_over_answers."""
    info = sc12c.build(seed="original")
    _answer("A", "yes", "electrical")
    sc12c.build_path().unlink()
    with pytest.raises(sc12c.Sc12cRefusal, match="holds answers but"):
        sc12c.build()
    assert not sc12c.build_path().exists()
    assert sc12c.main(["build", "--seed", "original"]) == sc12c.EXIT_OK
    assert sc12c.harness.read_json(sc12c.build_path())["order"] == info["order"]


# ---------------------------------------------------------------------------
# The real held out half (copied into tmp_path; skipped without data/)
# ---------------------------------------------------------------------------

COMMITTED_SC12A = ("labels.json", "item_ids.json")


def _tree_hashes(*roots: Path) -> dict[str, str]:
    out = {}
    for root in roots:
        paths = [root] if root.is_file() else sorted(p for p in root.rglob("*") if p.is_file())
        for path in paths:
            out[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
    return out


@pytest.fixture
def real(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The real SC12a files, copied: data/eval/sc12a and the committed labels.json and item_ids.json."""
    if not (REAL_SC12A / "items.json").is_file() or not (REAL_SC12A / "lock.json").is_file():
        pytest.skip(NO_DATA)
    eval_dir = tmp_path / "eval"
    shutil.copytree(REAL_SC12A, eval_dir / "sc12a")
    committed_sc12a = tmp_path / "committed_sc12a"
    committed_sc12a.mkdir()
    for name in COMMITTED_SC12A:
        shutil.copy2(harness.COMMITTED_DIR / name, committed_sc12a / name)
    monkeypatch.setattr(config, "EVAL_DIR", eval_dir)
    monkeypatch.setattr(harness, "COMMITTED_DIR", committed_sc12a)
    monkeypatch.setattr(sc12c, "COMMITTED_DIR", tmp_path / "committed")
    return tmp_path


# Items whose answer differs from the AI majority in the real run, so disagreements exist.
FLIPS = 3


def _answer_real_like_the_majority() -> list[str]:
    """Answer every held out item as the AI majority did, but flip the first FLIPS ids; return the flipped ids."""
    labels = harness.load_labels()
    held = sorted(i["id"] for i in sc12c.heldout_items())
    flipped = held[:FLIPS]
    for item_id in held:
        label = labels[item_id]
        positive = label["positive"] != (item_id in flipped)
        hazard = label["hazard"] if label["hazard"] != "split" else "none"
        if positive and hazard not in ("electrical", "heat", "gas"):
            hazard = "electrical"
        if not positive and hazard in ("electrical", "heat", "gas"):
            hazard = "none"
        sc12c.append_answer(sc12c.answers_path(), {"id": item_id, "safety": "yes" if positive else "no",
                                                   "hazard": hazard})
    return flipped


def test_real_page_holds_the_175_held_out_items_and_nothing_else(real: Path) -> None:
    """Mutation: sc12c_payload_leaks_the_half."""
    info = sc12c.build(seed="fixed")
    page = sc12c.page_path().read_text(encoding="utf-8")
    shown = _data_block(page, "items-data")
    items = harness.load_items()["items"]
    held = {i["id"]: i for i in items if i["half"] == "heldout"}
    assert info["items"] == len(shown) == len(held) == 175
    assert {i["id"] for i in shown} == set(held)
    for item in shown:
        assert set(item) == {"id", "appliance", "step", "detail", "index"}
        assert {k: item[k] or "" for k in harness.READER_KEYS} == {k: held[item["id"]][k] or ""
                                                                   for k in harness.READER_KEYS}
    assert not _keys(shown) & FORBIDDEN_KEYS
    chrome = _chrome(page).lower()
    for word in FORBIDDEN_CHROME:
        assert word not in chrome, word
    for word in FORBIDDEN_ANYWHERE:
        assert word not in page.lower(), word
    # No tune item outside the worked examples (which are tune items, decision 59).
    outside = re.sub(r'<section id="protocol".*?</section>', "", page, flags=re.DOTALL)
    tune_steps = {i["step"] for i in items if i["half"] == "tune"} - {i["step"] for i in held.values()}
    assert not [s for s in tune_steps if html.escape(s, quote=False) in outside or json.dumps(s)[1:-1] in outside]


def test_real_score_reproduces_the_published_ai_arms(real: Path) -> None:
    """Jev 30 of 31 at 0.732, the published list 20 of 31 at 0.714, the tuned list 22 of 31 at 0.647.

    Mutation: sc12c_published_arm_reads_the_tuned_list."""
    sc12c.build(seed="fixed")
    flipped = _answer_real_like_the_majority()
    sc12c.seal()
    result = sc12c.score()
    ai = result["arms"]["against_ai_majority"]
    expected = {sc12c.JEV_ARM: (30, 31, 41, 0.968, 0.732), sc12c.PUBLISHED_ARM: (20, 31, 28, 0.645, 0.714),
                sc12c.TUNED_ARM: (22, 31, 34, 0.710, 0.647)}
    for name, (tp, positives, flagged, recall, precision) in expected.items():
        arm = ai[name]
        assert (arm["tp"], arm["positives"], arm["flagged"]) == (tp, positives, flagged), name
        assert round(arm["recall"], 3) == recall and round(arm["precision"], 3) == precision, name
    assert [round(x, 3) for x in ai[sc12c.JEV_ARM]["recall_wilson95"]] == [0.838, 0.994]
    assert [round(x, 3) for x in ai[sc12c.JEV_ARM]["precision_wilson95"]] == [0.581, 0.843]
    assert result["items"] == 175 and result["jev_threshold"] == 0.51 == config.JEV_THRESHOLD
    s = result["safety_step_agreement"]
    assert s["agree"] == 175 - FLIPS and sorted(d["id"] for d in result["disagreements"]) == sorted(flipped)


def test_real_score_leaves_every_sc12a_file_untouched(real: Path) -> None:
    """Before and after, byte for byte: data/eval/sc12a/* (the copy and the real folder), labels.json and
    item_ids.json, across build, seal, score and revise.

    Mutation: sc12c_score_appends_a_reply (score writes a line to the Jev replies file)."""
    watched = (config.EVAL_DIR / "sc12a", REAL_SC12A, *(harness.COMMITTED_DIR / n for n in COMMITTED_SC12A),
               *(SAFETY_EVAL_DIR / n for n in COMMITTED_SC12A))
    before = _tree_hashes(*watched)
    sc12c.build(seed="fixed")
    flipped = _answer_real_like_the_majority()
    sc12c.seal()
    sc12c.score()
    sc12c.revise(flipped[0], "no", "none", "a test revision")
    sc12c.score()
    assert _tree_hashes(*watched) == before
    assert sorted(p.name for p in (config.EVAL_DIR / "sc12a").iterdir()) == sorted(p.name for p in REAL_SC12A.iterdir())


def test_real_revision_reason_quoting_a_step_stays_local(real: Path) -> None:
    """A reason that quotes a held out step is kept in the local revisions.json,
    never in the committed revisions or scores. Mutation
    sc12c_revision_reason_committed (the reason goes back into the committed entry)."""
    sc12c.build(seed="fixed")
    flipped = _answer_real_like_the_majority()
    sc12c.seal()
    sc12c.score()
    step = next(i["step"] for i in sc12c.heldout_items() if i["id"] == flipped[0])
    reason = f"the step says: {step}"
    sc12c.revise(flipped[0], "no", "none", reason)
    sc12c.score()
    local = sc12c.local_revisions_path().read_text(encoding="utf-8")
    assert json.loads(local)["revisions"][0]["reason"] == reason
    assert step in local or json.dumps(step, ensure_ascii=False)[1:-1] in local
    for name in (sc12c.REVISED, sc12c.SCORES):
        raw = sc12c.committed(name).read_text(encoding="utf-8")
        assert step not in raw and json.dumps(step, ensure_ascii=False)[1:-1] not in raw, name
        assert "the step says" not in raw, name


def test_real_committed_files_carry_no_item_text(real: Path) -> None:
    """Mutation: sc12c_scores_carry_the_step (the committed disagreements include the step text)."""
    sc12c.build(seed="fixed")
    flipped = _answer_real_like_the_majority()
    sc12c.seal()
    sc12c.score()
    sc12c.revise(flipped[0], "no", "none", "a test revision")
    sc12c.score()
    texts = {t for i in harness.load_items()["items"] for t in (i.get("step"), i.get("detail")) if t}
    assert len(texts) >= 175
    for name in (sc12c.HUMAN_LABELS, sc12c.SEAL, sc12c.SCORES, sc12c.REVISED):
        raw = sc12c.committed(name).read_text(encoding="utf-8")
        doc = json.loads(raw)
        assert not _keys(doc) & {"step", "detail", "appliance", "text"}, name
        leaked = [t for t in texts if t in raw or json.dumps(t, ensure_ascii=False)[1:-1] in raw]
        assert leaked == [], name
    local = json.loads(sc12c.disagreements_path().read_text(encoding="utf-8"))
    assert len(local["disagreements"]) == FLIPS and all(d["step"] for d in local["disagreements"])


# ---------------------------------------------------------------------------
# The fingerprint and the lock
# ---------------------------------------------------------------------------

NEW_FILES = ("safety_eval/sc12c.py", "safety_eval/stats.py", f"safety_eval/{sc12c.HUMAN_LABELS}",
             f"safety_eval/{sc12c.SEAL}", f"safety_eval/{sc12c.REVISED}", f"safety_eval/{sc12c.SCORES}")


def test_sc12c_files_stay_outside_the_build_fingerprint(tmp_path: Path) -> None:
    """Decision 65: the agent's runtime code only. The fingerprint of agent/ is the same with Phase 0's
    files absent, and with them changed.

    Mutation: build_info_fingerprints_safety_eval (safety_eval/ leaves EXCLUDED)."""
    for rel in NEW_FILES:
        assert build_info.is_excluded(rel), rel
    agent = config.REPO_ROOT / "agent"
    copy = tmp_path / "agent"
    shutil.copytree(agent, copy, ignore=shutil.ignore_patterns("__pycache__", "tests"))
    for rel in NEW_FILES:
        (copy / rel).unlink(missing_ok=True)
    assert build_info.fingerprint(copy) == build_info.fingerprint(agent) == build_info.build_id()
    for rel in NEW_FILES:
        (copy / rel).write_text("{}\n", encoding="utf-8")
    assert build_info.fingerprint(copy) == build_info.build_id()


def _gate2_lock() -> dict[str, Any]:
    """The lock JSON printed under "Gate 2, the lock" in DECISION-LOG.md."""
    text = (config.REPO_ROOT / "DECISION-LOG.md").read_text(encoding="utf-8")
    after = text[text.index("**Gate 2, the lock**"):]
    block = after[after.index("```json\n") + len("```json\n"):]
    return json.loads(block[:block.index("\n```")])


def test_lock_hashes_match_the_decision_log_and_the_shipped_config() -> None:
    """Mutation: config_jev_threshold_moved (JEV_THRESHOLD 0.52)."""
    lock = _gate2_lock()
    current = {
        "wording": SAFETY_STEP_NOUL["instructions"], "criteria": SAFETY_STEP_NOUL["criteria"],
        "thresholds": {name: {"rule": "full_recall", "threshold": config.JEV_THRESHOLD}
                       for name in ("jev", "word+jev")},
        "word_list_published": list(config.SAFETY_WORDS_PUBLISHED), "word_list_tuned": list(config.SAFETY_WORDS),
        "model": config.JEV_MODEL,
    }
    for name, value in current.items():
        assert harness.sha256_of(value) == lock["sha256"][name], name
        assert lock[name] == value, name
    assert config.JEV_THRESHOLD == 0.51


def test_lock_file_matches_its_hash_and_the_current_set(real: Path) -> None:
    """Mutation: config_jev_threshold_moved."""
    lock_file = harness.lock_path()
    assert hashlib.sha256(lock_file.read_bytes()).hexdigest() == sc12c.LOCK_FILE_SHA256 == (
        "cc217fbb61ec6b3ab71c6e26fe59faf466f12c9641bf1dd2106c6e4f34dad5c9")
    assert hashlib.sha256((REAL_SC12A / "lock.json").read_bytes()).hexdigest() == sc12c.LOCK_FILE_SHA256
    lock = harness.read_json(lock_file)
    doc = harness.load_items()
    assert harness.lock_mismatches(lock, harness.current_lock_fields(doc, harness.load_labels())) == []
