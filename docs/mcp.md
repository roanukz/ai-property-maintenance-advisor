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
99 covers the shared code, decision 106 records the Claude Desktop trial, and
decision 107 records how anyone can connect a copy of their own, at their own
cost. The README's section "Use it from Claude Desktop or Claude Code (MCP)"
gives the same steps in short.

## Who pays, and where the keys live

- **Nothing is hosted.** The server runs only over standard input and output,
  started by the app on the same computer. It opens no network port, so no one
  else can connect to your copy, and no one can connect to mine: everyone who
  connects runs their own copy.
- **Replay needs no keys and spends nothing.** It answers from recorded model
  replies, and its cost is labeled as the replay ledger, with `real_money`
  false. Its cost line still counts the calls the recording holds, at the
  recorded model's prices, and the recording's Tavily credits: those figures
  describe the recording, and a replay sends no request, so it spends no
  money and no Tavily credit on anyone's account. Started with neither
  `--cassette` nor `--live`, the server refuses a brief, since replay needs a
  cassette, and `property_status` still works offline.
- **A live brief is paid for by whoever runs that copy.** Keys are read only on
  the computer running the server, from its environment or the clone's own
  `.env`, so a live brief's cost lands on that person's own Anthropic, Tavily
  and TypeSafe accounts. No copy can use my keys.
- **The repository holds no keys.** On 16 October 2026 every file in its
  history, on every branch and the published pages included, and every file in
  the working copy that git tracks or would commit was searched for my three
  key values, and none appeared; a scan for key shaped strings found none
  either. `agent/tests/test_no_keys.py` now fails the suite if a key shaped
  string is in any file git would commit, even split across two lines or
  inside base64 text (binary data written as letters and digits, as in a
  recorded request), and checks that `.gitignore` ignores `.env` and
  `.env.local` and not `.env.example`.
- **The product recall check is free and keyless** in any mode, but it sends
  requests to CPSC's public recall service, so it asks first.
- **Live use needs all of these:** the person's own keys, in the environment
  or the clone's gitignored `.env`; `ADVISOR_MODE=cheap` in the server's
  environment; `--live`; and a ledger created once with a live `advisor`
  command and `--new-ledger`. Every paid call shows a preflight and goes ahead
  only on "proceed", under $0.15 per brief and $5 for the whole build. Without
  the keys, a live server refuses a brief before anything is sent and names
  each missing key.
- **Keep keys in the clone's `.env`, never in an app's settings file.** An
  `env` block in `claude_desktop_config.json`, or a value passed with `-e` to
  `claude mcp add`, is saved as plain text in the app's own settings, outside
  the repository and its `.gitignore`. Put only `ADVISOR_MODE` there, and never
  commit `.env`.
- **A live entry answers whatever reaches the assistant.** While an app's
  entry for the server has `--live`, text in a web page, a file, an email or
  another tool's reply can ask the assistant to start a brief and send
  `confirm="proceed"` itself. In an app without forms the app's approval
  prompt is the only check, and any mode or rule that approves tool calls
  without asking you, such as Claude Code's bypass permissions mode, its auto
  mode or an always allow rule, removes it for these tools too. Add a live
  entry only for a live run you are watching, in a session that asks before
  each tool call, then switch back to replay and restart the app.

## Status, 16 October 2026

What has been shown, and what has not:

- **Claude Code, in replay only.** Claude Code 2.1.299, running Claude Opus 5.5,
  connected to the server and called its tools in one non-interactive session,
  in replay mode: the server answered from recorded model replies and no money
  was spent. It loaded the server from a one session config file
  (`--mcp-config` with `--strict-mcp-config`, starting the server as
  `.venv/bin/python -m agent.mcp_server` with the recorded FLO case), so
  nothing was added to Claude Code's settings, and only the three tools it
  needed were allowed in advance.
- **A fresh clone, added with `claude mcp add`.** On 16 October 2026, a fresh
  clone of the repository with no keys and no `.env`, set up with `uv sync --frozen`
  (the exact versions in `uv.lock`) and run with nothing in its environment but
  `HOME` and `PATH`, served its four tools over standard input and output from
  `advisor-mcp --cassette agent/tests/cassettes/flo.json`. The replayed FLO
  brief paused for the unit and, resumed with Sundance Spas, Optima 880, serial
  100915742, made 06/2014 and code FLO, came back with status ok, 2 steps to
  try first, both cited, 1 flagged as a safety step and 1 documented
  candidate, its cost labeled as the replay ledger with `real_money` false;
  `property_status` for prop-a gave 3 appliances. Started with no options, it
  refused `get_service_brief`, since replay needs a cassette, and
  `property_status` still worked. Started with `--live` and
  `ADVISOR_MODE=cheap` but no keys, it refused `get_service_brief` before
  sending anything, naming the three missing keys, and created no ledger; with
  `--live` and no `ADVISOR_MODE`, it refused to start. Claude Code 2.1.299,
  run with a throwaway settings folder so that its real settings were not
  touched, added the server with the `claude mcp add` command below, and
  `claude mcp list` showed it connected. The `/mcp` check inside a session has
  not been run, and only macOS has been tried: Windows and Linux are untried.
  On 17 October 2026, again with a throwaway settings folder, the server added
  this way was listed as connected from a folder inside the clone and was not
  listed from an unrelated folder, since `claude mcp add` saves it in Claude
  Code's local scope, for that project only; added with `--scope user`, it was
  listed as connected from another folder.
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
path of the folder this repository was cloned into; the Claude Code commands use `$PWD` instead, run
from that folder. Apps start the server without a shell, so the path must be written out in full.

The simplest command is the entry point that `uv sync` installs:

```
"/path/to/ai-property-maintenance-advisor/.venv/bin/advisor-mcp"
```

The same server also starts as
`"/path/to/ai-property-maintenance-advisor/.venv/bin/python" -m agent.mcp_server`,
the form Claude Desktop ran on 16 October 2026. Either works from any folder,
because the project is installed in its own `.venv`.
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
described below for one brief on 16 October 2026, started as
`.venv/bin/python` with `-m agent.mcp_server` at the head of `args`; the
`advisor-mcp` entry point in this entry starts the same code but has not been
run by Desktop. Open
`~/Library/Application Support/Claude/claude_desktop_config.json` and add an
entry under `mcpServers`. This one replays the recorded hot tub FLO case and
spends nothing:

```json
{
  "mcpServers": {
    "property-advisor": {
      "command": "/path/to/ai-property-maintenance-advisor/.venv/bin/advisor-mcp",
      "args": [
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
`"env": {"ADVISOR_MODE": "cheap"}` to the entry. Put only `ADVISOR_MODE` in
that `env` block, never a key: this settings file is plain text, outside the
repository and its `.gitignore`. The keys are read as `advisor` reads them,
from the server's environment or from the clone's own `.env`, so keep them in
`.env`, which git ignores.

## Adding it to Claude Code

To try it for one session only, without adding it to Claude Code's settings,
save the `mcpServers` block above in a file and start Claude Code with
`--mcp-config <that file> --strict-mcp-config`. That is how the replay session
of 16 October 2026 was run.

To add it to Claude Code's settings instead, run this from the cloned folder
(`$PWD` is the shell's name for the current folder, so the full paths are
saved), then check that it connects:

```
claude mcp add property-advisor -- "$PWD/.venv/bin/advisor-mcp" --cassette "$PWD/agent/tests/cassettes/flo.json"
claude mcp list
```

`claude mcp list` showed it connected on a fresh clone on 16 October 2026. Run
`/mcp` in a Claude Code session to see its four tools; that check has not been
run. `claude mcp add` saves the server in Claude Code's local scope, for this
project only: a `claude` session started in the cloned folder, or a folder
inside it, has the tools, and one started anywhere else does not. To use it
from every folder, put `--scope user` after `claude mcp add`.

For live runs, add the server with `-e ADVISOR_MODE=cheap` and `--live` in
place of the cassette (remove the replay entry first with
`claude mcp remove property-advisor`):

```
claude mcp add property-advisor -e ADVISOR_MODE=cheap -- "$PWD/.venv/bin/advisor-mcp" --live
```

Pass only `ADVISOR_MODE` with `-e`, never a key: Claude Code saves each `-e`
value as plain text in its own settings, outside the repository, or with
`--scope project` in a `.mcp.json` file inside the clone, which git would
commit. The keys stay in the clone's `.env`.

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
