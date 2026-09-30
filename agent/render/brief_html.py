"""Brief renderer (Phase 4): the v2 page and the legacy v1 page (PLAN 6.3, SC10).

`render_brief(brief, identity=..., symptom=..., generated_at=..., legacy=..., run=...)`
returns one self contained HTML page.

**legacy=True** reproduces v1's `briefToHtml` byte for byte for the four
recorded briefs in `briefs/*.html` (SC10, `test_legacy_render_is_byte_identical`):
no validate, v1's section order, v1's escaping (`&`, `<`, `>`, `"` only, as
JavaScript `String()` would print the value), v1's placeholders, title and
footer, and v1's inline CSS. The markup and CSS here were taken from the
published briefs, which are public; nothing was copied from v1's source. v1
sections that none of the four published briefs contains (the all forum
warning, happened_before, a refusal with an empty "found" list, an empty
warranty list) follow the same layout but are not byte verified.

**legacy=False** (the default) is the v2 page. Every difference from v1 is a
proposed difference for the Phase 4 gate (decision 21). The table in
agent/tests/fixtures/goldens/README.md lists each one, says whether decision
21 names it, and points at the golden that shows it:

- `html.escape(quote=True)` everywhere, so `'` is escaped too;
- a CSP meta (`default-src 'none'; style-src 'unsafe-inline'`) and
  `<link rel="icon" href="data:,">`, so a browser requests nothing on its own;
- the source host next to each tier badge (PRD decision 9.2), and each source
  line shows its own retrieval date, so the footer stays true on the graph route;
- the budget stop box in the refusal slot, upgrade options after the
  candidates, maintenance due after the warranty, the happened_before record
  date, and the registry warranty terms with their record ID (decision 29);
- the refusal and budget stop footers without "reproduces documented
  material" (decision 47);
- the dash replacements of PLAN 6.4 (decision 17): "not recorded", "no code",
  "That is the honest result: nothing below is guessed.", "Service brief: ...";
- U+2013 and U+2014 inside quoted model or source text are written as the
  character references `&#8211;` and `&#8212;`: the page shows the same
  character, and no file v2 writes (goldens included) holds a raw dash;
- `&middot;` and `&#10003;` in place of the raw middle dot and check mark;
- no blank line where v1 printed an empty placeholder line (the candidates
  table keeps v1's `div.tablewrap` wrapper, so only the table scrolls);
- maintenance entries show the record and date code wrote in validate
  (`last_done_record_id`, `last_done_on`), never a date looked up here;
- a page whose status is not ok never draws steps, candidates, upgrade
  options or maintenance, even when handed an unvalidated brief (a backstop:
  validate already clears them, decision 9).
- a budget stop page shows no all forum warning: its sources were never
  graded, so their forum tier is a default, not a finding;
- CSS rules for the new pieces are appended after v1's CSS;
- one notice line above the steps (SAFETY_NOTICE) when the run says the
  automatic safety check was attempted and failed (`run["safety_notice"]`).

Legacy mode needs U+2014 (placeholders, title, refusal intro). The repo's
style scan bans that character and its escapes in source files, so it is
built at import time with `chr(0x2014)`.
"""

from __future__ import annotations

import html
import re
from collections.abc import Mapping, Sequence
from typing import Any
from urllib.parse import urlsplit

from agent.schemas import HTTP_URL

TIERS = ("manufacturer", "dealer", "forum")
EM_DASH = chr(0x2014)  # legacy mode only; see the module docstring
EN_DASH = chr(0x2013)
MIDDOT = chr(0x00B7)
CHECK = chr(0x2713)

NOT_RECORDED = "not recorded"
NO_CODE = "no code"
CSP = "default-src 'none'; style-src 'unsafe-inline'"

REFUSAL_INTRO = (
    "Documented sources for this exact unit could not be found or did not support an "
    "answer. That is the honest result: nothing below is guessed."
)
LEGACY_REFUSAL_INTRO = (
    "Documented sources for this exact unit could not be found or did not support an "
    f"answer. That is the honest result {EM_DASH} nothing below is guessed."
)
BUDGET_INTRO = (
    "This run stopped at a spending or search limit before a documented answer could be "
    "checked. Nothing below is guessed."
)
ALL_FORUM_WARNING = (
    "Every source in this brief is community content (forum tier). No manufacturer or "
    "dealer documentation was found. Treat every claim below accordingly."
)
FOOTER_OK = (
    "This brief reproduces documented material found at the listed sources on {date}. "
    "It is not a diagnosis. Confirm any code on the unit's own display before ordering parts."
)
FOOTER_NO_ANSWER = (
    "This brief lists what was searched on {date} and why no documented answer is given. "
    "It is not a diagnosis. Confirm any code on the unit's own display before ordering parts."
)
FOOTER_BUDGET = (
    "This brief lists what was searched on {date} before the run stopped at a budget limit. "
    "No documented answer is given. It is not a diagnosis."
)
SAFETY_NOTICE = (
    "An automatic safety check did not run on this brief. Treat any step at a breaker, "
    "a panel, a heater or a gas valve as a safety step."
)
CODE_NOTE = "was reported on the unit's display, so matching was narrowed to that code."
REASON_LABELS = {"discontinued": "model discontinued", "parts_unavailable": "parts no longer available"}

# v1's inline CSS, exactly as the published briefs carry it.
LEGACY_STYLE = """
  :root { color-scheme: light; }
  * { box-sizing: border-box; }
  body { margin: 0 auto; padding: 20px 16px 48px; max-width: 720px;
         font: 17px/1.5 -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
         color: #111; background: #fff; }
  h1 { font-size: 26px; margin: 0 0 4px; }
  h2 { font-size: 20px; margin: 28px 0 8px; border-top: 3px solid #111; padding-top: 12px; }
  h3 { font-size: 16px; margin: 14px 0 4px; }
  .unit { font-size: 22px; font-weight: 700; margin: 0; }
  .meta { color: #333; margin: 2px 0; }
  .nra { background: #fff8e6; border: 3px solid #b58900; padding: 4px 14px 12px; margin-top: 20px; }
  .caution { background: #fdecea; border: 3px solid #b71c1c; padding: 4px 14px 12px; margin-top: 20px; }
  .caution h2, .nra h2 { border: none; padding-top: 6px; margin-top: 6px; }
  .steps li { margin-bottom: 12px; }
  .steps li.safety { background: #fdecea; padding: 8px; border-left: 5px solid #b71c1c; list-style-position: inside; }
  .steps p { margin: 2px 0 0; }
  .tablewrap { overflow-x: auto; }
  table { border-collapse: collapse; width: 100%; font-size: 15px; }
  th, td { border: 1px solid #444; padding: 8px 6px; text-align: left; vertical-align: top; }
  th { background: #eee; }
  tr.confirmed td { background: #e8f4e8; }
  .tier { display: inline-block; font-size: 12px; font-weight: 700; text-transform: uppercase;
          padding: 1px 6px; border: 1.5px solid #111; border-radius: 3px; }
  .tier-manufacturer { background: #e8f4e8; }
  .tier-dealer { background: #eef2fb; }
  .tier-forum { background: #fff3cd; }
  .sources li { margin-bottom: 6px; overflow-wrap: anywhere; }
  .ref { text-decoration: none; font-weight: 700; }
  footer { margin-top: 36px; border-top: 3px solid #111; padding-top: 12px; color: #333; font-size: 15px; }
  a { color: #0d47a1; }
"""

# v2 adds rules for the host, retrieval date, quotes and the new lists.
STYLE = LEGACY_STYLE + """\
  .host, .retrieved, .note { color: #333; font-size: 14px; }
  .items li { margin-bottom: 12px; }
  .items p { margin: 2px 0 0; }
  .quote { color: #333; font-size: 15px; }
"""

_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}")


# ---------------------------------------------------------------------------
# Escaping and URLs
# ---------------------------------------------------------------------------


def _dash_refs(text: str) -> str:
    return text.replace(EM_DASH, "&#8212;").replace(EN_DASH, "&#8211;")


def esc(value: Any) -> str:
    """v2: escape any value for text or a double or single quoted attribute."""
    return _dash_refs(html.escape("" if value is None else str(value), quote=True))


def _js_string(value: Any) -> str:
    """What JavaScript's String() prints for a JSON value."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, list):
        return ",".join("" if v is None else _js_string(v) for v in value)
    if isinstance(value, dict):
        return "[object Object]"
    return str(value)


def esc_v1(value: Any) -> str:
    """v1's esc: null to "", then `&`, `<`, `>`, `"` (not `'`)."""
    text = "" if value is None else _js_string(value)
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def safe_url(url: Any) -> str | None:
    """The URL when it is a web URL, else None (it then renders as text)."""
    return url if isinstance(url, str) and HTTP_URL.match(url) else None


def _is_index(value: Any, sources: Sequence[Any]) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and 0 <= value < len(sources)


def _host(source: Mapping[str, Any]) -> str | None:
    """The source's code built host, or the host of its web URL."""
    if isinstance(source.get("host"), str) and source["host"]:
        return source["host"]
    url = safe_url(source.get("url"))
    if url is None:
        return None
    try:
        return urlsplit(url).hostname
    except ValueError:
        return None


def _day(value: Any) -> Any:
    """An ISO timestamp shown as its date; anything else as given."""
    return value[:10] if isinstance(value, str) and _ISO_DATE.match(value) else value


# ---------------------------------------------------------------------------
# The page
# ---------------------------------------------------------------------------


class _Brief:
    """One page. `legacy` switches every v1 versus v2 choice."""

    def __init__(self, brief: Mapping[str, Any], identity: Mapping[str, Any], symptom: Any,
                 generated_at: Any, legacy: bool, run: Mapping[str, Any]) -> None:
        self.brief = brief
        self.identity = identity
        self.symptom = symptom
        self.generated_at = generated_at
        self.legacy = legacy
        self.run = run
        self.e = esc_v1 if legacy else esc
        raw = brief.get("sources")
        self.sources: list[Any] = list(raw) if isinstance(raw, list) else []

    # -- pieces ---------------------------------------------------------------

    def source(self, index: Any) -> Mapping[str, Any] | None:
        if not _is_index(index, self.sources) or not isinstance(self.sources[index], Mapping):
            return None
        return self.sources[index]

    def ref(self, index: Any) -> str:
        """A citation label, computed only from a validated int index."""
        source = self.source(index)
        if source is None:
            return ""
        label = f"[{index + 1}]"
        url = safe_url(source.get("url"))
        return f' <a class="ref" href="{self.e(url)}">{label}</a>' if url else f" {label}"

    def badge(self, source: Mapping[str, Any]) -> str:
        """The tier badge; in v2 followed by the source host."""
        tier = source.get("tier")
        if self.legacy:
            return f'<span class="tier tier-{self.e(tier)}">{self.e(tier)}</span>'
        out = f'<span class="tier tier-{tier}">{tier}</span>' if tier in TIERS else ""
        host = _host(source)
        if host:
            out += (" " if out else "") + f'<span class="host">{esc(host)}</span>'
        return out

    def _list(self, items: Any) -> str:
        items = items if isinstance(items, list) else []
        return "<ul>" + "".join(f"<li>{self.e(i)}</li>" for i in items) + "</ul>"

    def _line(self, text: str) -> list[str]:
        """An optional line: v1 printed it blank ("  "), v2 leaves it out."""
        if text:
            return [f"  {text}"]
        return ["  "] if self.legacy else []

    # -- sections -------------------------------------------------------------

    def header(self) -> str:
        e = self.e
        ident = self.identity
        blank = EM_DASH if self.legacy else NOT_RECORDED
        dot = MIDDOT if self.legacy else "&middot;"
        serial = e(ident.get("serial")) or blank
        made = e(ident.get("manufacture_date")) or blank
        matched = self.brief.get("matched_identity")
        lines = [
            "<section>",
            "  <h1>Service brief</h1>",
            f'  <p class="unit">{e(ident.get("manufacturer"))} {e(ident.get("model"))}</p>',
            f'  <p class="meta">Serial {serial} {dot} Manufactured {made}</p>',
            f'  <p class="meta">Reported symptom: {e(self.symptom)}</p>',
            *self._line(f'<p class="meta">Matched documentation for: {e(matched)}</p>' if matched else ""),
            "</section>",
        ]
        return "\n".join(lines)

    def refusal(self, block: Mapping[str, Any]) -> str:
        found = block.get("found")
        found_html = self._list(found) if isinstance(found, list) and found else "<p>Nothing usable.</p>"
        intro = LEGACY_REFUSAL_INTRO if self.legacy else REFUSAL_INTRO
        return "\n".join([
            '<section class="nra">',
            "  <h2>No reliable answer</h2>",
            f"  <p>{intro}</p>",
            "  <h3>What was searched</h3>",
            f"  {self._list(block.get('searched'))}",
            "  <h3>What was found</h3>",
            f"  {found_html}",
            "  <h3>Why it is not enough</h3>",
            f"  <p>{self.e(block.get('why_insufficient'))}</p>",
            "</section>",
        ])

    def budget_stop(self) -> str:
        block = self.brief.get("no_reliable_answer")
        block = block if isinstance(block, Mapping) else {}
        searched = block.get("searched")
        if not isinstance(searched, list):
            trail = self.run.get("search_trail") or []
            searched = [t.get("query") for t in trail if isinstance(t, Mapping) and t.get("query")]
        lines = [
            '<section class="nra">',
            "  <h2>Stopped at a budget limit</h2>",
            f"  <p>{BUDGET_INTRO}</p>",
        ]
        # The budget stop brief's block states the stop reason (refuse.py); a
        # brief without one shows the reason the run recorded.
        if block.get("why_insufficient"):
            lines.append(f"  <p>{esc(block.get('why_insufficient'))}</p>")
        else:
            lines.append(f"  <p>Stop reason: {esc(self.run.get('stop_reason') or NOT_RECORDED)}</p>")
        lines += [
            "  <h3>What was searched</h3>",
            f"  {self._list(searched)}" if searched else "  <p>Nothing was searched.</p>",
            "</section>",
        ]
        return "\n".join(lines)

    def caution(self, caution: Mapping[str, Any]) -> str:
        return "\n".join([
            '<section class="caution">',
            "  <h2>Warranty caution</h2>",
            f"  <p>{self.e(caution.get('text'))}{self.ref(caution.get('source_index'))}</p>",
            "</section>",
        ])

    def all_forum(self) -> str:
        return "\n".join([
            '<section class="caution">',
            "  <h2>Source quality warning</h2>",
            f"  <p>{ALL_FORUM_WARNING}</p>",
            "</section>",
        ])

    def happened_before(self, before: Mapping[str, Any]) -> str:
        lines = [
            "<section>",
            "  <h2>This has happened before</h2>",
            f"  <p>{self.e(before.get('summary'))}</p>",
        ]
        record = None if self.legacy else _cited_record(before, self.run.get("records"))
        if record is not None:
            lines.append(f'  <p class="meta">Service record {esc(record.get("record_id"))}, '
                         f'dated {esc(record.get("date") or NOT_RECORDED)}.</p>')
        lines.append("</section>")
        return "\n".join(lines)

    def steps(self, steps: Sequence[Any]) -> str:
        items = []
        for s in steps:
            if not isinstance(s, Mapping):
                continue
            safety = s.get("safety_flag") is True
            items.append("\n".join([
                '    <li class="safety">' if safety else "    <li>",
                f"      <strong>{'SAFETY: ' if safety else ''}{self.e(s.get('step'))}</strong>",
                f"      <p>{self.e(s.get('detail'))}{self.ref(s.get('source_index'))}</p>",
                "    </li>",
            ]))
        notice = [] if self.legacy or not self.run.get("safety_notice") else [
            f'  <p class="note">{SAFETY_NOTICE}</p>']
        return "\n".join([
            "<section>",
            "  <h2>Try this first</h2>",
            *notice,
            '  <ol class="steps">',
            *items,
            "  </ol>",
            "</section>",
        ])

    def candidate_row(self, c: Mapping[str, Any]) -> str:
        e = self.e
        confirmed = c.get("confirmed") is True
        index = c.get("source_index")
        source = self.source(index)
        badge = self.badge(source) if source is not None else ""
        who = "Anyone" if c.get("who") == "anyone" else "Technician"
        if self.legacy:
            code = f"{e(c.get('code')) or EM_DASH}{' ' + CHECK if confirmed else ''}"
        else:
            code = f"{e(c.get('code')) or NO_CODE}{' &#10003;' if confirmed else ''}"
        return "\n".join([
            '      <tr class="confirmed">' if confirmed else "      <tr>",
            f"        <td>{code}</td>",
            f"        <td>{e(c.get('documented_meaning'))}</td>",
            f"        <td>{e(c.get('documented_action'))}</td>",
            f"        <td>{who}</td>",
            f"        <td>{e(c.get('why_shown'))}</td>",
            f"        <td>{badge}{self.ref(index)}</td>",
            "      </tr>",
        ])

    def candidates(self, candidates: Sequence[Any]) -> str:
        observed = self.brief.get("observed_code")
        note = (f'<p class="meta">Code <strong>{self.e(observed)}</strong> {CODE_NOTE}</p>'
                if observed else "")
        rows = [self.candidate_row(c) for c in candidates if isinstance(c, Mapping)]
        head = "<thead><tr><th>Code</th><th>Documented meaning</th><th>Documented action</th>" \
               "<th>Who</th><th>Why shown</th><th>Source</th></tr></thead>"
        # v1's wrapper in both modes: only the table scrolls sideways on a narrow screen.
        return "\n".join([
            "<section>",
            "  <h2>Documented candidates</h2>",
            *self._line(note),
            '  <div class="tablewrap"><table>',
            f"    {head}",
            "    <tbody>",
            *rows,
            "    </tbody>",
            "  </table></div>",
            "</section>",
        ])

    def upgrades(self, options: Sequence[Any]) -> str:
        items = []
        for o in options:
            if not isinstance(o, Mapping):
                continue
            name = " ".join(esc(v) for v in (o.get("successor_manufacturer"), o.get("successor_model")) if v)
            raw_reason = o.get("reason")
            reason = (REASON_LABELS.get(raw_reason) if isinstance(raw_reason, str) else None) or raw_reason
            source = self.source(o.get("source_index"))
            badge = " " + self.badge(source) if source is not None else ""
            lines = [
                "    <li>",
                f'      <strong>{name}</strong> <span class="note">({esc(reason)})</span>',
                f"      <p>{esc(o.get('summary'))}{badge}{self.ref(o.get('source_index'))}</p>",
            ]
            if o.get("evidence"):
                lines.append(f'      <p class="quote">Quoted from the source: {esc(o.get("evidence"))}</p>')
            lines.append("    </li>")
            items.append("\n".join(lines))
        return "\n".join(["<section>", "  <h2>Upgrade options</h2>", '  <ul class="items">', *items,
                          "  </ul>", "</section>"])

    def warranty(self, warranty: Mapping[str, Any]) -> str:
        e = self.e
        lines = ["<section>", "  <h2>Warranty</h2>", f"  <p>{e(warranty.get('age_statement'))}</p>"]
        if not self.legacy and warranty.get("terms"):
            lines.append(f"  <p>Warranty terms from property record {esc(warranty.get('record_id') or NOT_RECORDED)}: "
                         f"{esc(warranty.get('terms'))}</p>")
        cautions = warranty.get("cautions")
        cautions = [c for c in cautions if isinstance(c, Mapping)] if isinstance(cautions, list) else []
        if cautions:
            lines.append("  <ul>" + "".join(
                f"<li>{e(c.get('text'))}{self.ref(c.get('source_index'))}</li>" for c in cautions
            ) + "</ul>")
        verify = warranty.get("verify")
        if isinstance(verify, list) and verify:
            lines.append("  <h3>Verify against your own warranty document</h3>" + self._list(verify))
        lines.append("</section>")
        return "\n".join(lines)

    def maintenance(self, items: Sequence[Any]) -> str:
        out = []
        for m in items:
            if not isinstance(m, Mapping):
                continue
            # Both values are written by code in validate (rule 9): the record
            # the due date was computed from, and that record's date.
            record_id = m.get("last_done_record_id")
            if record_id:
                done = m.get("last_done_on")
                last = f"per property record {esc(record_id)}" + (f", on {esc(done)}" if done else "")
            else:
                last = "no record of the last time"
            due = esc(m.get("due_date")) if m.get("due_date") else "not computed"
            lines = [
                "    <li>",
                f"      <strong>{esc(m.get('task'))}</strong>",
                f"      <p>Interval, as the source states it: {esc(m.get('interval'))}{self.ref(m.get('source_index'))}</p>",
                f'      <p class="meta">Last done: {last}. Due: {due}.</p>',
            ]
            if m.get("evidence"):
                lines.append(f'      <p class="quote">Quoted from the source: {esc(m.get("evidence"))}</p>')
            lines.append("    </li>")
            out.append("\n".join(lines))
        return "\n".join(["<section>", "  <h2>Maintenance due</h2>", '  <ul class="items">', *out,
                          "  </ul>", "</section>"])

    def sources_section(self) -> str:
        e = self.e
        items = []
        for s in self.sources:
            if not isinstance(s, Mapping):
                continue
            url = safe_url(s.get("url"))
            label = e(s.get("title") or s.get("url"))
            link = f'<a href="{e(url)}">{label}</a>' if url else label
            badge = self.badge(s)
            line = f"{badge} {link}" if badge else link
            if not self.legacy and s.get("retrieved_at"):
                line += f' <span class="retrieved">retrieved {esc(_day(s.get("retrieved_at")))}</span>'
            items.append(f"    <li>{line}</li>")
        return "\n".join(["<section>", "  <h2>Sources</h2>", '  <ol class="sources">', *items, "  </ol>",
                          "</section>"])

    def footer(self, status: Any) -> str:
        # v1 printed one footer for every status; v2 keeps it for ok only (decision 47).
        footer = FOOTER_OK if status == "ok" or self.legacy else FOOTER_NO_ANSWER
        if status == "budget_stopped" and not self.legacy:
            footer = FOOTER_BUDGET
        return f"<footer>\n  <p>{footer.format(date=self.e(self.generated_at))}</p>\n</footer>"

    def title(self) -> str:
        e = self.e
        maker, model = self.identity.get("manufacturer"), self.identity.get("model")
        if self.legacy:
            return f"Service brief {EM_DASH} {e(maker)} {e(model)}"
        name = " ".join(esc(v) for v in (maker, model) if v)
        return f"Service brief: {name}" if name else "Service brief"

    # -- whole page -----------------------------------------------------------

    def sections(self) -> list[str]:
        brief = self.brief
        status = brief.get("status")
        parts = [self.header()]
        if status == "no_reliable_answer" and isinstance(brief.get("no_reliable_answer"), Mapping):
            parts.append(self.refusal(brief["no_reliable_answer"]))
        if status == "budget_stopped" and not self.legacy:
            parts.append(self.budget_stop())
        if isinstance(brief.get("warranty_caution"), Mapping):
            parts.append(self.caution(brief["warranty_caution"]))
        known = [s for s in self.sources if isinstance(s, Mapping)]
        # A budget stop's sources were never graded (their tier is the default),
        # so v2 does not call them all community content.
        graded = self.legacy or status != "budget_stopped"
        if graded and known and all(s.get("tier") == "forum" for s in known):
            parts.append(self.all_forum())
        before = brief.get("happened_before")
        # A list here is kept verbatim for v1 parity and means no match (PLAN 10.2).
        if isinstance(before, Mapping) and before.get("matches") is True:
            parts.append(self.happened_before(before))
        # Backstop, not a rule: validate already clears these lists on a refusal
        # or budget stop (decision 9), and v2 never draws them for any status
        # but ok, even when handed an unvalidated brief. Legacy draws what v1 drew.
        answer = self.legacy or status == "ok"
        if answer and isinstance(brief.get("try_first"), list) and brief["try_first"]:
            parts.append(self.steps(brief["try_first"]))
        if answer and isinstance(brief.get("candidates"), list) and brief["candidates"]:
            parts.append(self.candidates(brief["candidates"]))
        if answer and not self.legacy and isinstance(brief.get("upgrade_options"), list) and brief["upgrade_options"]:
            parts.append(self.upgrades(brief["upgrade_options"]))
        if isinstance(brief.get("warranty"), Mapping):
            parts.append(self.warranty(brief["warranty"]))
        if answer and not self.legacy and isinstance(brief.get("maintenance_due"), list) and brief["maintenance_due"]:
            parts.append(self.maintenance(brief["maintenance_due"]))
        if self.sources:
            parts.append(self.sources_section())
        parts.append(self.footer(status))
        return parts

    def page(self) -> str:
        head = [
            "<!doctype html>",
            '<html lang="en">',
            "<head>",
            '<meta charset="utf-8">',
            '<meta name="viewport" content="width=device-width, initial-scale=1">',
            '<meta name="robots" content="noindex">',
        ]
        if not self.legacy:
            head += [
                f'<meta http-equiv="Content-Security-Policy" content="{esc(CSP)}">',
                '<link rel="icon" href="data:,">',
            ]
        head += [
            f"<title>{self.title()}</title>",
            f"<style>{LEGACY_STYLE if self.legacy else STYLE}</style>",
            "</head>",
            "<body>",
        ]
        return "\n".join([*head, *self.sections(), "</body>", "</html>", ""])


def _cited_record(before: Any, records: Any) -> Mapping[str, Any] | None:
    """The loaded service record a happened_before block cites, if any."""
    if not isinstance(before, Mapping) or not isinstance(before.get("record_id"), str):
        return None
    for rec in records or []:
        if isinstance(rec, Mapping) and rec.get("record_id") == before["record_id"]:
            return rec
    return None


def render_brief(
    brief: Mapping[str, Any],
    *,
    identity: Mapping[str, Any] | None,
    symptom: str,
    generated_at: str,
    legacy: bool = False,
    run: Mapping[str, Any] | None = None,
) -> str:
    """The whole page for one brief.

    `run` carries what the page shows beyond the brief (v2 only):
    `records`, the run's history_hits (the service record happened_before cites
    is shown with its date); `search_trail`
    and `stop_reason` for the budget stop box; `safety_notice`, True when the
    automatic safety check failed, for the notice line above the steps. `legacy=True` renders exactly as
    v1 did and ignores `run`.
    """
    if legacy and isinstance(brief, Mapping) and brief.get("status") == "budget_stopped":
        raise ValueError("legacy mode renders v1 briefs; v1 has no budget_stopped status")
    return _Brief(brief, identity or {}, symptom, generated_at, legacy, run or {}).page()


def render_brief_html(
    brief: Mapping[str, Any],
    *,
    identity: Mapping[str, Any] | None,
    symptom: str,
    generated_at: str,
    search_trail: Sequence[Mapping[str, Any]] | None = None,
    stop_reason: str | None = None,
    records: Sequence[Mapping[str, Any]] | None = None,
) -> str:
    """The Phase 2 entry point, kept for its callers: the v2 page."""
    run = {"search_trail": list(search_trail or []), "stop_reason": stop_reason, "records": list(records or [])}
    return render_brief(brief, identity=identity, symptom=symptom, generated_at=generated_at, run=run)


def budget_stopped_brief(sources: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """The brief a budget stop carries when no draft was validated.

    Header, the sources retrieved so far and nothing else: candidates, steps,
    upgrades and maintenance are empty (decision 9). Code built sources have no
    tier yet, so none is shown.
    """
    kept = ("url", "title", "host", "retrieved_at", "origin")
    return {
        "status": "budget_stopped",
        "matched_identity": None,
        "observed_code": None,
        "warranty_caution": None,
        "happened_before": None,
        "try_first": [],
        "candidates": [],
        "warranty": None,
        "no_reliable_answer": None,
        "upgrade_options": [],
        "maintenance_due": [],
        "sources": [{k: s.get(k) for k in kept} for s in sources if isinstance(s, Mapping)],
    }
