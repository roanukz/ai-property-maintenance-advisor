"""The SC12a labeled step set: pools G and D, dedupe, and the tune and held out split.

Pools, in this order, deduplicated by `step_key` (normalized step plus
detail); after dedupe an item belongs to the first document it was found in.

- G, generated steps: every try_first step in briefs/*.html, briefs/v2/*.html,
  data/briefs/*.html, data/superseded/**/briefs/*.html, then the recorded v1
  cassettes flo, hvac, notheating, unknown and blurry (never the synthetic
  ones). v1 HTML prefixes a flagged title with "Safety: "; it is stripped. An
  item is writer flagged if any occurrence was flagged.
- D, documentation sentences (decision 53): full pages only, grouped maker
  site pages (the host rule in agent/rules/tiers.py gives them manufacturer
  tier), then the fixed list of maker written manuals on other hosts
  (maker_documents.json), then dealer pages; forum pages are not
  documentation and are left out, as are the pages maker_documents.json
  lists under "not_documentation" (off topic pages a search brought back).
  The file is pinned by its sha256 in the set, its extensions and the lock.
  Within a group, pages in order of first
  retrieval; within a page, sentences in document order whose first word is
  in FIRST_WORDS and that are at most config.SC12A_MAX_SENTENCE_CHARS long,
  and only the first config.SC12A_D_DOC_CAP of those per page (decision 58);
  the cap counts qualifying sentences before dedupe, and a URL stored in more
  than one version counts as one page (decision 59).
  G plus D stop at config.SC12A_POOL_SIZE; the stopping rule later adds
  config.SC12A_EXTEND_BY D items at a time (`extend_items`), continuing the
  same capped walk.

A full page is a page file that a lookup log names as a result's
`text_sha256`: the raw page text Tavily returned. The search snippet files
in data/pages are written by the recorder from a result's `content` and are
named only in data/recordings/*.texts.json, never in a lookup log.

The split (decision 58) assigns document families, not items, to halves, so
near duplicates from one document, or from runs that answer one question,
never straddle it. A G document is a run (or a v1 lookup): the same run's
brief in briefs/v2/ and data/briefs/ is one document, and a v1 cassette is
the v1 lookup it was derived from. A G family is every run and v1 lookup
that answers the same question on the same unit, read from its recorded
input: the run's recording (data/recordings/live_t_<id>.json, this build's
or a superseded build's), else its eval record (data/eval/*-t-<id>.json, likewise),
and for a v1 lookup the v1 cassette derived from it. The question is the
observed code when the run was given one, else the normalized symptom; the
unit is the maker and model the run was answered for (the resume answer's
identity, else the input's). A document with no recorded input, or whose
input names no unit or no question, is its own family (the documented
fallback). A D document is its URL; a page on the fixed list is in the
family maker_documents.json gives it, as is a page its "also_in_family"
names (another revision of a listed document, decision 59), and every other
D page is its own family. The families of the five briefs that hold the
protocol's worked examples (and of any document the builder finds holding a
worked example's step) go to the tune half. Then every other family, in the
order of the sha256 of its key, goes to the half that holds fewer items of
its stratum so far, then fewer items in all, ties to tune (decision 59); a
family's stratum is its items' majority appliance, ties to the first by
name. The stopping rule's extension keeps every family's half and places
new families by the same rule; a new document holding a worked example's
step sends a new family to tune.

Near duplicates that still cross the halves (the writer's steps quote the
pages they cite, so G and D overlap by design) are counted with the set and
reported, not removed (`near_duplicates`); dedupe stays the brief's rule.

Pure except for reading the files it is pointed at; it writes nothing.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from agent import config
from agent.rules import tiers
from agent.rules.step_text import normalize_text, step_key

POOL_G = "G"
POOL_D = "D"
TUNE = "tune"
HELDOUT = "heldout"
HALVES = (TUNE, HELDOUT)
GROUPS = ("maker", "listed", "dealer")  # decision 53, in order

V1_CASSETTES = ("flo", "hvac", "notheating", "unknown", "blurry")
# The sampling a set was built under, recorded in items.json and item_ids.json.
SAMPLING = "decision 59"
QUESTION_FAMILY = "question"  # the prefix of a G family read from recorded inputs

FIRST_WORDS = frozenset({
    "turn", "switch", "shut", "disconnect", "unplug", "reset", "restore", "open", "close", "remove",
    "replace", "install", "clean", "drain", "fill", "refill", "flush", "check", "inspect", "confirm",
    "verify", "test", "press", "set", "raise", "lower", "adjust", "wait", "allow", "let", "keep",
    "make", "ensure", "locate", "loosen", "tighten", "run", "restart",
})

# The step of each worked example in the label protocol (PROTOCOL.md).
WORKED_EXAMPLE_STEPS = (
    "Turn off power at the breaker, wait 20 minutes, then restore power",
    "Clean or replace the air filter",
    "Look at the cabinet indicator light before touching anything else",
    "Raise the temperature setpoint with the filter out and see whether the heater engages",
    "Visually inspect the outdoor fan blades for damage or obstruction",
    "Check that the thermostat is set to COOL and the fan is set to AUTO.",
)
# The five briefs that hold them (found in briefs/ and briefs/v2/); each goes to
# the tune half whatever its hash, as does any document the builder finds
# holding a worked example's step.
WORKED_EXAMPLE_BRIEFS = (
    "t-267045726dd04118", "c45b900046bf", "db572cf7b81b", "t-a6cc7b63e63b41d0", "t-32b097b6a454440e",
)

# The appliance type a reader sees, in the safety_check node's words (a
# registry category with "_" as a space); "appliance" when nothing names one.
UNKNOWN_APPLIANCE = "appliance"
APPLIANCE_HINTS = (
    ("hot tub", ("sundance", "aquarest", "hot tub", "hottub", "spa")),
    ("air conditioner", ("trane", "xr16", "air condition")),
    ("water heater", ("rheem", "water heater")),
)

MAKER_DOCUMENTS_PATH = Path(__file__).resolve().with_name("maker_documents.json")


@dataclass(frozen=True)
class Roots:
    """Where the builder reads: the repo (published briefs), the data folder and the v1 cassettes."""

    repo_root: Path
    data_dir: Path
    cassette_dir: Path

    @classmethod
    def from_config(cls) -> Roots:
        return cls(config.REPO_ROOT, config.PAGES_DIR.parent, config.CASSETTE_DIR)


def appliance_for(text: str | None) -> str:
    folded = (text or "").casefold()
    for kind, hints in APPLIANCE_HINTS:
        if any(re.search(r"\b" + re.escape(hint), folded) for hint in hints):
            return kind
    return UNKNOWN_APPLIANCE


def page_appliance(query: str | None, url: str | None) -> str:
    """A D page's appliance: from its search query, else from the words of its URL."""
    found = appliance_for(query)
    if found == UNKNOWN_APPLIANCE:
        found = appliance_for(re.sub(r"[^0-9a-z]+", " ", (url or "").casefold()))
    return found


def document_sha256(document: str) -> str:
    return hashlib.sha256(document.encode("utf-8")).hexdigest()


def family_sha256(family: str) -> str:
    """The stable hash that orders families for the split (decision 58)."""
    return hashlib.sha256(family.encode("utf-8")).hexdigest()


def assign_halves(sizes: Mapping[str, int], forced_tune: Iterable[str] = (),
                  fixed: Mapping[str, str] | None = None,
                  strata: Mapping[str, str] | None = None) -> dict[str, str]:
    """Each family's half (decisions 58 and 59): deterministic, whatever order `sizes` comes in.

    `fixed` families keep their half (the stopping rule's extension). Forced
    families go to tune. Then every other family, in the order of the sha256
    of its key, goes to the half that holds fewer items of its stratum (its
    appliance, from `strata`) so far, then fewer items in all, ties to tune.
    Without `strata` every family is in one stratum.
    """
    halves: dict[str, str] = {}
    counts: dict[Any, int] = {TUNE: 0, HELDOUT: 0}

    def place(name: str, half: str) -> None:
        halves[name] = half
        counts[half] += sizes[name]
        stratum = (half, strata.get(name) if strata else None)
        counts[stratum] = counts.get(stratum, 0) + sizes[name]

    for name, half in (fixed or {}).items():
        if name in sizes:
            place(name, half)
    ordered = sorted(sizes, key=family_sha256)
    forced = set(forced_tune)
    for name in ordered:
        if name in forced and name not in halves:
            place(name, TUNE)
    for name in ordered:
        if name not in halves:
            s = strata.get(name) if strata else None
            key = lambda h, s=s: (counts.get((h, s), 0), counts[h])  # noqa: E731
            half = TUNE if key(TUNE) <= key(HELDOUT) else HELDOUT
            place(name, half)
    return {name: halves[name] for name in ordered}


def family_strata(items: Iterable[Mapping[str, Any]]) -> dict[str, str]:
    """Each family's stratum for the split (decision 59): its items' majority appliance, ties to the first by name."""
    tally: dict[str, dict[str, int]] = {}
    for item in items:
        per = tally.setdefault(item["family"], {})
        per[item["appliance"]] = per.get(item["appliance"], 0) + 1
    return {name: max(sorted(per.items()), key=lambda kv: kv[1])[0] for name, per in tally.items()}


# ---------------------------------------------------------------------------
# Pool G
# ---------------------------------------------------------------------------

_SAFETY_PREFIX = re.compile(r"^\s*safety:\s*", re.IGNORECASE)


def strip_safety_prefix(text: str) -> str:
    return _SAFETY_PREFIX.sub("", text or "").strip()


class _BriefParser(HTMLParser):
    """The try_first steps of a rendered brief (v1 or v2 markup) and its unit line."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.steps: list[dict[str, Any]] = []
        self.unit = ""
        self.title = ""
        self._in_steps = False
        self._li: dict[str, Any] | None = None
        self._field: str | None = None  # "step" or "detail" while inside strong or p
        self._in_ref = False
        self._in_unit = False
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        classes = (dict(attrs).get("class") or "").split()
        if tag == "title":
            self._in_title = True
        elif tag == "ol" and "steps" in classes:
            self._in_steps = True
        elif tag == "p" and "unit" in classes:
            self._in_unit = True
        elif self._in_steps and tag == "li":
            self._li = {"step": "", "detail": "", "safety_flag": "safety" in classes}
        elif self._li is not None and tag == "strong" and not self._li["step"]:
            self._field = "step"
        elif self._li is not None and tag == "p" and not self._li["detail"]:
            self._field = "detail"
        elif tag == "a" and "ref" in classes:
            self._in_ref = True

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False
        elif tag == "ol" and self._in_steps:
            self._in_steps = False
        elif tag == "p" and self._in_unit:
            self._in_unit = False
        elif tag == "a":
            self._in_ref = False
        elif tag in ("strong", "p") and self._field is not None:
            self._field = None
        elif tag == "li" and self._li is not None:
            self.steps.append({"step": strip_safety_prefix(" ".join(self._li["step"].split())),
                               "detail": " ".join(self._li["detail"].split()),
                               "safety_flag": self._li["safety_flag"]})
            self._li = None

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data
        if self._in_unit:
            self.unit += data
        if self._li is not None and self._field is not None and not self._in_ref:
            self._li[self._field] += data


def parse_brief(html: str) -> tuple[str, list[dict[str, Any]]]:
    """(unit line or title, try_first steps with their writer flags) of a rendered brief."""
    parser = _BriefParser()
    parser.feed(html)
    parser.close()
    return (parser.unit.strip() or parser.title.strip()), parser.steps


_V1_LOOKUP = re.compile(r"v1 lookup ([0-9a-f]{12})")


def cassette_steps(data: Mapping[str, Any]) -> tuple[str, list[dict[str, Any]]]:
    """(maker and model, every try_first step of every recorded synthesize draft) of a v1 cassette."""
    identity = ((data.get("resume") or {}).get("identity") or (data.get("input") or {}).get("identity") or {})
    unit = " ".join(str(identity.get(k) or "") for k in ("manufacturer", "model")).strip()
    steps = []
    for call in data.get("synthesize") or []:
        for step in ((call or {}).get("draft") or {}).get("try_first") or []:
            if isinstance(step, dict):
                steps.append({"step": strip_safety_prefix(str(step.get("step") or "")),
                              "detail": str(step.get("detail") or ""),
                              "safety_flag": step.get("safety_flag") is True})
    return unit, steps


def cassette_document(name: str, data: Mapping[str, Any]) -> str:
    """The v1 lookup a cassette was derived from (its brief's document), else the cassette itself."""
    found = _V1_LOOKUP.search(str((data.get("provenance") or {}).get("derived_from") or ""))
    return found.group(1) if found else f"cassette:{name}"


def _rel(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def g_documents(roots: Roots) -> Iterator[tuple[str, str, str, list[dict[str, Any]]]]:
    """(document, source path, unit, steps) for every G source, in pool order."""
    repo, data = roots.repo_root, roots.data_dir
    folders = [sorted((repo / "briefs").glob("*.html")), sorted((repo / "briefs" / "v2").glob("*.html")),
               sorted((data / "briefs").glob("*.html")),
               sorted((data / "superseded").glob("**/briefs/*.html"), key=lambda p: _rel(p, data))]
    for paths in folders:
        for path in paths:
            unit, steps = parse_brief(path.read_text(encoding="utf-8"))
            yield path.stem, _rel(path, repo), unit, steps
    for name in V1_CASSETTES:
        path = roots.cassette_dir / f"{name}.json"
        if path.is_file():
            payload = json.loads(path.read_text(encoding="utf-8"))
            unit, steps = cassette_steps(payload)
            yield cassette_document(name, payload), _rel(path, repo), unit, steps


_RECORDING = re.compile(r"^live_t_([0-9a-f]+)\.json$")


def _read_json_or_none(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def recorded_inputs(roots: Roots) -> dict[str, tuple[Mapping[str, Any], Mapping[str, Any]]]:
    """document -> (input, resume answer) for every G document whose input was recorded.

    Read in this order, the first source found for a document winning: the
    v1 cassettes (a v1 lookup's input, its "case"), the run recordings
    (data/recordings/live_t_<id>.json, then the superseded builds'), then the
    eval records (data/eval/*.json, then the superseded builds'; the first
    resume answer).
    """
    found: dict[str, tuple[Mapping[str, Any], Mapping[str, Any]]] = {}
    data = roots.data_dir
    for name in V1_CASSETTES:
        payload = _read_json_or_none(roots.cassette_dir / f"{name}.json")
        if isinstance(payload, dict):
            found.setdefault(cassette_document(name, payload), (payload.get("input") or {}, payload.get("resume") or {}))
    recordings = sorted((data / "recordings").glob("live_t_*.json")) + sorted(
        (data / "superseded").glob("**/recordings/live_t_*.json"), key=lambda p: _rel(p, data))
    for path in recordings:
        match = _RECORDING.match(path.name)
        payload = _read_json_or_none(path) if match else None
        if match and isinstance(payload, dict):
            found.setdefault(f"t-{match.group(1)}", (payload.get("input") or {}, payload.get("resume") or {}))
    records = sorted((data / "eval").glob("*.json")) + sorted(
        (data / "superseded").glob("**/eval/*.json"), key=lambda p: _rel(p, data))
    for path in records:
        payload = _read_json_or_none(path)
        if isinstance(payload, dict) and payload.get("run_id") and isinstance(payload.get("input"), dict):
            answers = [a for a in payload.get("answers") or [] if isinstance(a, dict)]
            found.setdefault(str(payload["run_id"]), (payload["input"], answers[0] if answers else {}))
    return found


def question_family(given: Mapping[str, Any], resume: Mapping[str, Any]) -> str | None:
    """"question:<maker>:<model>:code:<code>" or "...:symptom:<symptom>"; None when the input names no unit or question."""
    from agent import kg

    identity = (resume or {}).get("identity") or (given or {}).get("identity") or {}
    maker, model = identity.get("manufacturer"), identity.get("model")
    code = (resume or {}).get("observed_code")
    symptom = normalize_text(str((given or {}).get("symptom") or ""))
    try:
        unit = f"{kg.norm_maker(str(maker))}:{kg.norm_model(str(model))}" if maker and model else None
        question = f"code:{kg.norm_code(str(code))}" if code else (f"symptom:{symptom}" if symptom else None)
    except ValueError:
        return None
    return f"{QUESTION_FAMILY}:{unit}:{question}" if unit and question else None


def g_family(document: str, inputs: Mapping[str, tuple[Mapping[str, Any], Mapping[str, Any]]]) -> str:
    """A G document's family: the question its recorded input asks, else the document itself (the fallback)."""
    found = inputs.get(document)
    return (question_family(*found) if found else None) or document


def pool_g(roots: Roots, inputs: Mapping[str, tuple[Mapping[str, Any], Mapping[str, Any]]] | None = None,
           ) -> list[dict[str, Any]]:
    """Every G item in pool order, deduplicated by step key, with every writer flag kept."""
    inputs = recorded_inputs(roots) if inputs is None else inputs
    items: dict[str, dict[str, Any]] = {}
    for document, source, unit, steps in g_documents(roots):
        for step in steps:
            if not step["step"].strip():
                continue
            key = step_key(step["step"], step["detail"])
            item = items.get(key)
            if item is None:
                item = items[key] = {
                    "id": key, "pool": POOL_G, "appliance": appliance_for(unit), "step": step["step"],
                    "detail": step["detail"], "document": document, "family": g_family(document, inputs),
                    "writer_flag": False, "occurrences": 0, "sources": [],
                }
            item["writer_flag"] = item["writer_flag"] or step["safety_flag"]
            item["occurrences"] += 1
            if source not in item["sources"]:
                item["sources"].append(source)
    return list(items.values())


def worked_example_documents(items: Iterable[Mapping[str, Any]]) -> list[str]:
    """Documents whose items include a worked example's step, in item order."""
    wanted = {normalize_text(s) for s in WORKED_EXAMPLE_STEPS}
    found: list[str] = []
    for item in items:
        if normalize_text(item["step"]) in wanted and item["document"] not in found:
            found.append(item["document"])
    return found


# ---------------------------------------------------------------------------
# Pool D
# ---------------------------------------------------------------------------


def load_maker_documents(path: Path | None = None) -> list[dict[str, Any]]:
    data = json.loads((path or MAKER_DOCUMENTS_PATH).read_text(encoding="utf-8"))
    return list(data.get("documents") or [])


@dataclass(frozen=True)
class DocumentList:
    """maker_documents.json as the builder uses it, pinned by the sha256 of the file's bytes (decision 53)."""

    documents: list[dict[str, Any]]
    not_documentation: list[dict[str, Any]]
    status: str
    sha256: str
    # Pages outside the fixed list that are another revision of a listed document: they keep
    # their group and take that document's family (decision 59).
    also_in_family: list[dict[str, Any]] = field(default_factory=list)

    @property
    def excluded(self) -> list[str]:
        return [str(d.get("url") or "") for d in self.not_documentation]


def load_document_list(path: Path | None = None) -> DocumentList:
    raw = (path or MAKER_DOCUMENTS_PATH).read_bytes()
    data = json.loads(raw.decode("utf-8"))
    return DocumentList(documents=list(data.get("documents") or []),
                        not_documentation=list(data.get("not_documentation") or []),
                        status=str(data.get("status") or ""), sha256=hashlib.sha256(raw).hexdigest(),
                        also_in_family=list(data.get("also_in_family") or []))


def norm_url(url: str) -> str:
    return (url or "").strip().rstrip("/")


@dataclass(frozen=True)
class Page:
    """A full page, at its first retrieval."""

    sha256: str
    url: str
    query: str
    first_retrieved: tuple[str, str, int, int]  # (at, log path, line, result index)
    group: str | None = None


def lookup_logs(data_dir: Path) -> list[Path]:
    """Every lookup log: this build's, then the superseded builds'."""
    return sorted((data_dir / "lookups").glob("*.jsonl")) + sorted(
        (data_dir / "superseded").glob("**/lookups/*.jsonl"), key=lambda p: _rel(p, data_dir))


def full_pages(data_dir: Path) -> list[Page]:
    """Every page file some lookup log names as a result's text_sha256, by first retrieval."""
    pages_dir = data_dir / config.PAGES_DIR.name
    first: dict[str, Page] = {}
    for log in lookup_logs(data_dir):
        rel = _rel(log, data_dir)
        for line_no, line in enumerate(log.read_text(encoding="utf-8").splitlines(), start=1):
            if not line.strip():
                continue
            record = json.loads(line)
            for index, result in enumerate(record.get("results") or []):
                sha = (result or {}).get("text_sha256")
                if not sha or not (pages_dir / f"{sha}.txt").is_file():
                    continue
                order = (str(record.get("at") or ""), rel, line_no, index)
                if sha not in first or order < first[sha].first_retrieved:
                    first[sha] = Page(sha256=sha, url=str(result.get("url") or ""),
                                      query=str(record.get("query") or ""), first_retrieved=order)
    return sorted(first.values(), key=lambda p: p.first_retrieved)


def page_group(url: str, listed: set[str]) -> str | None:
    """maker, listed or dealer (decision 53); None for a forum page, which is not documentation."""
    host = tiers.host_of(url)
    ceilings = {tiers.ceiling_for(host, manufacturer=maker, authorship_quote=None, page_text=None)
                for maker in config.MAKER_DOMAINS}
    if "forum" in ceilings:
        return None
    if "manufacturer" in ceilings:
        return "maker"
    return "listed" if norm_url(url) in listed else "dealer"


def ordered_pages(data_dir: Path, maker_documents: Sequence[Mapping[str, Any]],
                  excluded: Iterable[str] = ()) -> list[Page]:
    """Full pages grouped maker, listed, dealer; within a group, by first retrieval; `excluded` URLs left out."""
    listed = {norm_url(str(d.get("url") or "")) for d in maker_documents}
    skip = {norm_url(url) for url in excluded}
    grouped: dict[str, list[Page]] = {g: [] for g in GROUPS}
    for page in full_pages(data_dir):
        if norm_url(page.url) in skip:
            continue
        group = page_group(page.url, listed)
        if group is not None:
            grouped[group].append(Page(page.sha256, page.url, page.query, page.first_retrieved, group))
    return [page for g in GROUPS for page in grouped[g]]


_BULLET = re.compile(r"^(?:[*•▪►·>|#]+\s*|\(?\d{1,3}[.)]\s+|\(?[A-Za-z][.)]\s+)+")
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=\S)")
_FIRST_WORD = re.compile(r"[A-Za-z]+")


def sentences(text: str) -> Iterator[str]:
    """A page's sentences in document order: each line split after . ! or ?, list markers dropped.

    Lines are kept apart, so a table row or a heading never runs into the
    sentence after it.
    """
    for raw in text.splitlines():
        line = " ".join(raw.split())
        if not line:
            continue
        for part in _SENTENCE_END.split(line):
            sentence = _BULLET.sub("", part.lstrip("-")).strip()
            if sentence:
                yield sentence


def keeps_sentence(sentence: str) -> bool:
    """At most config.SC12A_MAX_SENTENCE_CHARS characters, and a first word from FIRST_WORDS."""
    if len(sentence) > config.SC12A_MAX_SENTENCE_CHARS:
        return False
    first = _FIRST_WORD.match(sentence)
    return first is not None and first.group(0).lower() in FIRST_WORDS


def listed_families(maker_documents: Sequence[Mapping[str, Any]]) -> dict[str, str]:
    """URL -> family for the fixed list's pages that name one (decision 58)."""
    return {norm_url(str(d.get("url") or "")): str(d["family"]) for d in maker_documents if d.get("family")}


def d_candidates(data_dir: Path, maker_documents: Sequence[Mapping[str, Any]],
                 excluded: Iterable[str] = (),
                 also_in_family: Sequence[Mapping[str, Any]] = ()) -> Iterator[dict[str, Any]]:
    """Every D item the pages give, in pool order, before dedupe: at most config.SC12A_D_DOC_CAP per URL.

    The cap counts across a URL's stored versions. `also_in_family` gives a
    page outside the fixed list the family of the listed document it is
    another revision of; its group is unchanged.
    """
    pages_dir = data_dir / config.PAGES_DIR.name
    families = {**listed_families(maker_documents), **listed_families(also_in_family)}
    kept: dict[str, int] = {}  # per document (the URL), across its stored versions
    for page in ordered_pages(data_dir, maker_documents, excluded):
        text = (pages_dir / f"{page.sha256}.txt").read_text(encoding="utf-8")
        appliance = page_appliance(page.query, page.url)
        document = f"url:{norm_url(page.url)}"
        for sentence in sentences(text):
            if kept.get(document, 0) >= config.SC12A_D_DOC_CAP:
                break
            if keeps_sentence(sentence):
                kept[document] = kept.get(document, 0) + 1
                yield {"id": step_key(sentence, ""), "pool": POOL_D, "appliance": appliance, "step": sentence,
                       "detail": "", "document": document, "family": families.get(norm_url(page.url), document),
                       "writer_flag": None, "occurrences": 1, "sources": [page.url], "group": page.group,
                       "page_sha256": page.sha256}


# ---------------------------------------------------------------------------
# The set
# ---------------------------------------------------------------------------


@dataclass
class BuildResult:
    items: list[dict[str, Any]]
    exhausted: bool  # D ran out before the limit
    forced_tune: list[str] = field(default_factory=list)  # documents
    forced_families: list[str] = field(default_factory=list)
    families: dict[str, dict[str, Any]] = field(default_factory=dict)  # family -> half, items, forced, stratum
    near_duplicates: dict[str, Any] = field(default_factory=dict)  # counts only (near_duplicates())


def forced_families_of(items: Sequence[Mapping[str, Any]], forced_documents: Sequence[str],
                       inputs: Mapping[str, tuple[Mapping[str, Any], Mapping[str, Any]]]) -> list[str]:
    """The family of each forced document: its items' family, else what its recorded input gives."""
    by_document = {item["document"]: item["family"] for item in items}
    return list(dict.fromkeys(by_document.get(d) or g_family(d, inputs) for d in forced_documents))


def _finish(items: list[dict[str, Any]], forced_families: Sequence[str],
            fixed: Mapping[str, str] | None = None) -> dict[str, dict[str, Any]]:
    sizes: dict[str, int] = {}
    for item in items:
        sizes[item["family"]] = sizes.get(item["family"], 0) + 1
    strata = family_strata(items)
    halves = assign_halves(sizes, forced_families, fixed, strata)
    forced = set(forced_families)
    for order, item in enumerate(items):
        item["order"] = order
        item["document_sha256"] = document_sha256(item["document"])
        item["family_sha256"] = family_sha256(item["family"])
        item["half"] = halves[item["family"]]
    return {name: {"half": half, "items": sizes[name], "forced": name in forced, "stratum": strata[name]}
            for name, half in halves.items()}


def build_items(roots: Roots, maker_documents: Sequence[Mapping[str, Any]], *,
                limit: int | None = None, excluded: Iterable[str] = (),
                also_in_family: Sequence[Mapping[str, Any]] = ()) -> BuildResult:
    """Pool G whole, then D until G plus D reach `limit` (config.SC12A_POOL_SIZE by default)."""
    limit = config.SC12A_POOL_SIZE if limit is None else limit
    inputs = recorded_inputs(roots)
    items = pool_g(roots, inputs)
    seen = {item["id"] for item in items}
    exhausted = True
    for candidate in d_candidates(roots.data_dir, maker_documents, excluded, also_in_family):
        if len(items) >= limit:
            exhausted = False
            break
        if candidate["id"] in seen:
            continue
        seen.add(candidate["id"])
        items.append(candidate)
    forced = list(dict.fromkeys([*WORKED_EXAMPLE_BRIEFS, *worked_example_documents(items)]))
    forced_families = forced_families_of(items, forced, inputs)
    families = _finish(items, forced_families)
    return BuildResult(items=items, exhausted=exhausted, forced_tune=forced, forced_families=forced_families,
                       families=families, near_duplicates=near_duplicates(items))


def extend_items(existing: Sequence[Mapping[str, Any]], roots: Roots,
                 maker_documents: Sequence[Mapping[str, Any]], *, by: int | None = None,
                 forced_tune: Sequence[str] = WORKED_EXAMPLE_BRIEFS, forced_families: Sequence[str] = (),
                 excluded: Iterable[str] = (), also_in_family: Sequence[Mapping[str, Any]] = ()) -> BuildResult:
    """The stopping rule's step: the existing items, then the next `by` D items not already in the set.

    The walk is the build's own capped walk, so it resumes where the build
    stopped. Every existing family keeps its half; a new family is placed by
    the build's rule, and a new document holding a worked example's step
    sends its family to tune (a family already placed keeps its half).
    """
    by = config.SC12A_EXTEND_BY if by is None else by
    items = [dict(item) for item in existing]
    fixed = {item["family"]: item["half"] for item in items}
    seen = {item["id"] for item in items}
    added = 0
    exhausted = True
    for candidate in d_candidates(roots.data_dir, maker_documents, excluded, also_in_family):
        if added >= by:
            exhausted = False
            break
        if candidate["id"] in seen:
            continue
        seen.add(candidate["id"])
        items.append(candidate)
        added += 1
    new_forced = [d for d in worked_example_documents(items[len(existing):]) if d not in forced_tune]
    forced_tune = [*forced_tune, *new_forced]
    forced_families = list(dict.fromkeys([*forced_families, *forced_families_of(items, new_forced, {})]))
    families = _finish(items, forced_families, fixed)
    return BuildResult(items=items, exhausted=exhausted, forced_tune=list(forced_tune),
                       forced_families=list(forced_families), families=families,
                       near_duplicates=near_duplicates(items))


# ---------------------------------------------------------------------------
# Near duplicates across the halves (reported, not removed)
# ---------------------------------------------------------------------------

_NOT_LETTER = re.compile(r"[^a-z]+")


def stripped_text(item: Mapping[str, Any]) -> str:
    """An item's step and detail, lowercased, with digits and punctuation stripped and single spaces."""
    return _NOT_LETTER.sub(" ", f"{item['step']} {item['detail']}".casefold()).strip()


def _grams(text: str, n: int) -> set[str]:
    return {text[k:k + n] for k in range(len(text) - n + 1)}


def near_duplicates(items: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Counts of near duplicate pairs whose items sit in different halves, and of groups within one half.

    With digits and punctuation stripped (`stripped_text`): pairs whose texts
    match, pairs at or above each ratio in config.SC12A_NEAR_DUP_RATIOS
    (difflib's similarity ratio), and pairs sharing a span of at least
    config.SC12A_NEAR_DUP_SPAN characters; then, within each half, groups of
    two or more items whose stripped texts match, and the items in them.
    Counts only: no text is returned.
    """
    import difflib

    span = config.SC12A_NEAR_DUP_SPAN
    texts = [(item["half"], stripped_text(item)) for item in items]
    grams = [_grams(text, span) for _half, text in texts]
    ratios = sorted(config.SC12A_NEAR_DUP_RATIOS, reverse=True)
    identical = 0
    at_ratio = dict.fromkeys(ratios, 0)
    shared_span = 0
    for a in range(len(texts)):
        for b in range(a + 1, len(texts)):
            (half_a, text_a), (half_b, text_b) = texts[a], texts[b]
            if half_a == half_b:
                continue
            identical += text_a == text_b
            shared_span += bool(grams[a] & grams[b])
            matcher = difflib.SequenceMatcher(None, text_a, text_b, autojunk=False)
            if matcher.real_quick_ratio() >= ratios[-1] and matcher.quick_ratio() >= ratios[-1]:
                ratio = matcher.ratio()
                for r in ratios:
                    at_ratio[r] += ratio >= r
    within: dict[tuple[str, str], int] = {}
    for half, text in texts:
        within[(half, text)] = within.get((half, text), 0) + 1
    groups = [n for n in within.values() if n > 1]
    return {"across_halves": {"identical_stripped": identical,
                              **{f"ratio_at_least_{r}": at_ratio[r] for r in ratios},
                              f"shared_span_at_least_{span}": shared_span},
            "within_a_half": {"identical_stripped_groups": len(groups), "items": sum(groups)}}
