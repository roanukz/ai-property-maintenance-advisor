# Using the advisor from Claude Desktop or Claude Code (MCP)

MCP, the Model Context Protocol, is an open standard that lets an AI assistant
call outside tools. The advisor can run as a local MCP server, so an MCP app,
such as Claude Desktop or Claude Code, can ask it for a service brief, a
property's status or a product recall check. The app starts the server on your
computer and talks to it over the process's standard input and output, a direct
connection between two programs on one computer. Nothing listens on a network
port and there is no hosted service to sign up for; a live brief still uses your
own keys for the paid services, as the command line does.

The server is a thin layer over the same code `advisor ask` and `advisor resume`
run. The spend ledger and its caps, the citations, the refusals, Jev's safety
check (Jev is a second model, from TypeSafe, that flags safety steps) and the
warranty dates written by code all apply exactly as they do on the command line.
Decisions 97 to 105 in DECISION-LOG.md record why it is built this way; decision
99 covers the shared code, and decision 106 records the Claude Desktop trial.

## Status, 16 October 2026

What has been shown, and what has not:

- **Claude Code, in replay only.** Claude Code 2.1.299, running Claude Opus 5.5,
  connected to the server and called its tools in one non-interactive session,
  in replay mode: the server answered from recorded model replies and no money
  was spent. It loaded the server from a one session config file
  (`--mcp-config` with `--strict-mcp-config`, with the same command and
  arguments as the Claude Desktop entry below), so nothing was added to Claude
  Code's settings, and only the three tools it needed were allowed in advance.
  The `claude mcp add` command and the `/mcp` check below have not been run.
- **One live brief, through the official client, with no person in it.** The
  first live, paid brief ran through the client of the official MCP library for
  Python, driven by a test program that played the owner, on the protocol's
  2026-07-28 version without forms. It was answered from memory, with no
  search.
- **One live brief in Claude Desktop, with me as the owner.** Claude Desktop
  2.42304.0 on macOS ran the server from its own settings file, with `--live`
  and `ADVISOR_MODE=cheap`, on build `841df66ad9669ef8`. I approved the money
  in advance, then read the preflight and typed `proceed` in the chat myself.
  The server sent Desktop no form, taking the path for apps without forms:
  Desktop put the preflight in the chat as text, and my `proceed` reached the
  server through the assistant, with the preflight's `confirmation_id`.
  Desktop's log does not record which protocol version it used.
  The brief researched the property's water heater for $0.0061 and ended with
  no reliable answer, because the only manuals found were for sibling models
  with different suffixes. Desktop was set back to replay after the trial.
- **No live brief through an assistant has carried steps to try first or
  safety flags yet.** The Desktop brief ended with no reliable answer, and the
  test program's live brief had neither, so safety flags reaching the response
  are shown only on recorded cases, not on a live run.
- **The pause to confirm the unit has not been put to an assistant since
  decision 105**, which tells the assistant never to fill in a field itself:
  the Desktop brief had no plate photo, so there was no unit to confirm.
- **Form support per app is unverified.** The server sent Claude Code a form to
  confirm the unit, and Claude Code's non-interactive session canceled it, so
  the brief stayed paused until the assistant called `resume_brief`. The
  server sent Claude Desktop no form for the preflight, so whether Desktop
  shows forms was not tested. A form has been answered only in the offline
  tests. Claude Desktop's log does not record which protocol version it used,
  so whether the protocol's earlier 2025-11-25 version has run outside those
  tests is not known.
- **The assistant retells the brief.** The tool's response carries every
  citation and safety flag word for word, but an assistant may leave out
  citations or safety flags it was given, or add its own reading, as Claude
  Code did. Claude Desktop reported the brief's refusal and its reason
  correctly, then filled the gap the tool had refused to fill, with warranty
  terms of related models and troubleshooting it labeled as general knowledge,
  not the advisor's.

The evidence is kept out of the public repository: the Claude Code session in
`data/evidence/claude_code_replay_20261016/`, the test program's script and
logs in `data/runs/t-bc31dd881bff44c4.client/`, and the Claude Desktop trial in
`data/evidence/claude_desktop_live_20261016/` (the chat, Desktop's log of the
server's messages, the server entry it ran and the ledger's readout), with its
run record `data/runs/t-7f23f74a338140bd.json`. DECISION-LOG.md gives the
details.

## The command that starts it

In the commands and settings below, replace `/path/to/ai-property-maintenance-advisor` with the full
path of the folder this repository was cloned into. Apps start the server without a shell, so the path
must be written out in full.

```
"/path/to/ai-property-maintenance-advisor/.venv/bin/python" -m agent.mcp_server
```

It works from any folder, because the project is installed in its own `.venv`.
After the next `uv sync`, the same server can also be started as
`.venv/bin/advisor-mcp`.
Run by hand, it prints nothing and waits: that is correct, since it is waiting
for an app to speak first. Press Control C to stop it.

Two options, the same as `advisor`:

- `--cassette PATH`: replay mode only. Every brief replays this recorded case,
  so a replay server answers one case. `ADVISOR_CASSETTE` does the same.
- `--live`: allows live runs, and only together with `ADVISOR_MODE=cheap` in the
  server's environment. With neither, the server runs in replay mode.

The server refuses to start in a live mode without `--live`, and with `--live`
outside a live mode, as `advisor` refuses.

## Adding it to Claude Desktop

Tried with Claude Desktop 2.42304.0 on macOS, which ran the live entry
described below for one brief on 16 October 2026. Open
`~/Library/Application Support/Claude/claude_desktop_config.json` and add an
entry under `mcpServers`. This one replays the recorded hot tub FLO case and
spends nothing:

```json
{
  "mcpServers": {
    "property-advisor": {
      "command": "/path/to/ai-property-maintenance-advisor/.venv/bin/python",
      "args": [
        "-m", "agent.mcp_server",
        "--cassette", "/path/to/ai-property-maintenance-advisor/agent/tests/cassettes/flo.json"
      ]
    }
  }
}
```

Quit Claude Desktop completely and open it again. Its log for the server is
`~/Library/Logs/Claude/mcp-server-property-advisor.log`.

Claude Desktop may rewrite this settings file from its own copy while it runs,
which can undo an edit made with it open. Edit the file with Desktop closed,
or check it after quitting and edit it again; the change takes effect when
Desktop next starts.

In the trial on 16 October 2026, the server sent Claude Desktop 2.42304.0 no
form for the preflight; it took the path for apps without forms (see "What asks
first, and how" below). Desktop showed the preflight in the chat as text and
asked me to type proceed there, and the assistant passed the answer to the
server. Whether Desktop can show a form was not tested.

For live runs, drop the `--cassette` pair, add `"--live"` to `args`, and add
`"env": {"ADVISOR_MODE": "cheap"}` to the entry. The keys are read as `advisor`
reads them: from the environment, or from the repository's own `.env`.

## Adding it to Claude Code

To try it for one session only, without adding it to Claude Code's settings,
save the `mcpServers` block above in a file and start Claude Code with
`--mcp-config <that file> --strict-mcp-config`. That is how the replay session
of 16 October 2026 was run.

To add it to Claude Code's settings instead:

```
claude mcp add property-advisor -- "/path/to/ai-property-maintenance-advisor/.venv/bin/python" -m agent.mcp_server --cassette "/path/to/ai-property-maintenance-advisor/agent/tests/cassettes/flo.json"
```

Run `/mcp` in a Claude Code session to see that it is connected with its four
tools. For live runs, add `-e ADVISOR_MODE=cheap` before the `--` and `--live`
at the end, in place of the cassette.

## The four tools

| Tool | What it does | Costs money | Asks first |
|---|---|---|---|
| `get_service_brief` | Writes a cited service brief from a symptom and exactly one of: a data plate photo path, a registry appliance id, or an identity typed from the plate. | Only in live mode | The unit's identity, when there is a photo or the symptom names a code; in live mode, every paid call |
| `resume_brief` | Finishes a paused brief with the owner's confirmed manufacturer, model and the rest, as `advisor resume` does. | Only in live mode | In live mode, the rest of the run |
| `property_status` | One property's warranty coverage, its Plan a replacement rows and its stored product recall checks, computed for today's UTC date. | Never | Nothing; it sends nothing |
| `check_product_recalls` | Asks CPSC's free recall service for the product recalls listed under each appliance's maker and keeps only those that print the unit's exact model, for one property or all, and stores each check. | Never (the service is free) | Always: it sends requests to an outside service |

A finished brief comes back as data and as text: its status, every step to try
first with its source, every safety flag word for word, the documented
candidates, the warranty coverage line, the cost and the run id. It also gives
the location of the HTML page the run wrote on your computer, as a path and a
file URI (the file to send the technician), and the app can open that page as
the resource `advisor://briefs/<run id>`. The property page is the resource
`advisor://property/<property id>`, built when it is read and never written.

A "no reliable answer" brief is a success, not an error: the sources found did
not support an answer, and the brief says what was searched and why.

## What costs money

Only a live brief: `get_service_brief` or `resume_brief` on a server started
with `--live` and `ADVISOR_MODE=cheap`. Those runs pay for Claude, use Tavily
search credits and make Jev safety checks, all reserved in the ledger at
`data/ledger/ledger.sqlite` under the same per run and build caps as the
command line (`RUN_CAP_USD` and `BUILD_CAP_USD` in `agent/config.py`). The
server never creates a ledger: if it is missing, a live brief is refused.

A new checkout has no ledger yet. Create it once by running a live `advisor`
command with `--new-ledger`, as the README's "Run it" section describes; after
that, a live server can spend.

Replay costs nothing. Its costs are priced on a separate replay ledger, as
recorded, and are labeled as no money spent. The product recall check is free
and keyless, but it does go out to CPSC, so it asks first like a paid call.

## What asks first, and how

**The unit's identity.** A photo, or a symptom that names a code, pauses the run
for the owner to confirm the unit, exactly where `advisor ask` pauses.

- If the app supports MCP elicitation forms (elicitation is MCP's way for a
  server to ask the app to show the person a form to fill in or confirm), the
  owner gets a form with what the plate read filled in, and the brief finishes
  in the same call.
- Otherwise the result has status `paused`, with the thread id, what was read and
  what is missing. The assistant then calls `resume_brief` with the confirmed
  fields. Closing the form does the same: the run stays paused and can be
  resumed later.
- The server cannot tell whether the owner or the assistant supplied the
  confirmed fields. Without a form, an assistant could send them without
  asking, even a model number the plate did not show. In the Claude Code
  session, the assistant sent them itself, taken from the owner's request.

**Every paid or outbound call.** Before anything is sent, the server shows the
same preflight the command prints: the planned calls, the typical and worst case
cost, the run cap and the spend so far. Only an answer of exactly `proceed`
goes ahead.

- With elicitation forms, the preflight appears in a form and the owner types
  `proceed` into it.
- Without them, the first call returns status `confirmation_required` with the
  preflight and a `confirmation_id`. The server tells the assistant to call
  again with the same arguments plus `confirm="proceed"` and that id only after
  the owner has read the preflight and typed `proceed`, but it cannot check who
  typed it. Claude Desktop took this path on 16 October 2026: it showed the
  preflight in the chat as text, and I typed `proceed` there.

A confirmation id works once, for the same tool, the same arguments and the same
preflight lines, within ten minutes. If the spend shown has changed by then, the
preflight is shown again and nothing is sent. A live run resumed after the
identity question shows its own preflight, as `advisor resume --live` does.

Without forms, then, the app's own approval prompt before each tool call is the
owner's only check that what the assistant sends is what the owner said. Keep it
on for `get_service_brief`, `resume_brief` and `check_product_recalls`, so the
owner sees the confirmed fields and the `confirm` the assistant sends, and never
set these tools to always allow. How each app shows that prompt has not been
verified, Claude Desktop's included: in the Claude Code session, the three tools
it used were allowed in advance for that one replay session, so no prompt was
shown.

## What it never does

- It never writes to the published pages (`index.html`, `tool.html`, `src/`,
  `briefs/`), never creates a ledger, and never reads stdin for a typed line,
  since stdin carries the protocol.
- It sends no telemetry. The MCP library creates OpenTelemetry spans, but only
  the OpenTelemetry API is installed, with nothing that collects or exports
  them.
- It runs one brief or check at a time, as the command line does.
