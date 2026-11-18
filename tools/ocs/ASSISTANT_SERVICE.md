# Local Assistant service (read-only prototype)

`assistant_service.py` connects the existing OCS Assistant TCP panel to the
live GUI through `OCS_EXE --mcp`. It uses the drawing-index provenance and
validated QA utilities already on `feat/drawing-qa` (PR #4, not yet merged).
Python 3.12, stdlib only. No packages or CAD source fork are required by the
service itself. Use the current Vibe Engineer GUI build with its Assistant TCP
client enabled; starting this script does not rebuild or replace the GUI.

## Start without a key

On Windows, run `tools/ocs/start-vibe-engineer.ps1` from this repository.
It expects a sibling `OpenCADStudio-source` checkout built from the pinned
source patch described in `patches/README.md`, and starts the editor with
`OCS_ASSISTANT_COMMAND` set; opening the Assistant panel starts this service
on demand. The launcher contains no credentials.

Open exactly one OCS GUI session with a drawing. Run in PowerShell from the
repository root, substituting the executable actually being tested:

```powershell
$env:OCS_EXE = 'C:\path with spaces\OpenCADStudio.exe'
python .\tools\ocs\assistant_service.py --ocs-exe $env:OCS_EXE
```

Then open the Assistant palette. The service binds only `127.0.0.1:8766`.
`OCS_ASSISTANT_PORT` or `--port` overrides the port. It does not start an editor,
open a file, modify a drawing, or choose between multiple editor sessions.

Available without an API key:

- `/help`: supported behavior and limits.
- `/summary`: recorded counts in the active drawing, by type and layer.
- `/entity 8A`: recorded data for a handle (replace `8A`).

`status.api_key` remains **false** without a key. The panel should allow local
commands in that state. Other questions return setup guidance without a paid
call. Any structural-safety/edit request recognized by the existing classifier
is refused locally; there is no mutation tool in this bridge even if the
classifier misses a phrase.

## Enable OpenRouter later

Set `OPENROUTER_API_KEY` locally and `OPENROUTER_MODEL` to a chosen OpenRouter
model ID. Existing ignored repo-root `.env` loading is reused. Never put the
key in a command argument, launcher, tracked file, screenshot, or chat. Restart
the service after changing these settings. The model must be explicitly set
when a key is present; no priced model is silently selected.

One model call per question, maximum 600 output tokens, 30-second HTTP timeout,
no autonomous tool loop. Only small QA excerpts leave the machine. The
existing QA layer validates citations and composes displayed claims locally;
raw model prose is not displayed. The source/evidence block includes the
session, document, revision and cited facts. There is no conversation history
or persistent drawing-index cache in this prototype; each question rebuilds
its snapshot of up to 10,000 entities. It does not index layouts, xrefs or
structural dependencies yet.

The default cumulative **reported-cost post-call stop** is $5, configurable
with `OPENROUTER_SPEND_CAP_USD` or `--spend-cap-usd`. It is not a hard budget:
one request can exceed the remaining amount, missing provider costs cannot
be counted, and other programs' usage is not included. Set a separate hard
limit in OpenRouter before paid tests. Local usage metadata goes to ignored
`tools/ocs/out/assistant_usage.jsonl`; prompts and keys are not logged. Only
one conversation is admitted at a time so service clients do not race this
usage counter. No paid calls were made while preparing this service.

## Auto-start from OCS

The original panel supports these process environment variables:

```powershell
$repo = (Get-Location).Path
$pythonExe = (Get-Command python).Source
$env:OCS_ASSISTANT_DIR = $repo
$env:OCS_ASSISTANT_COMMAND = '"' + $pythonExe + '" "' + (Join-Path $repo 'tools\ocs\assistant_service.py') + '"'
# Launch the GUI from this same PowerShell session:
Start-Process -FilePath $env:OCS_EXE -WorkingDirectory (Split-Path $env:OCS_EXE)
```

OCS passes `OCS_EXE` to its auto-started service. Its standard output is only a
startup banner; raw MCP output, questions and credentials are never logged by
this bridge. Use the exact GUI build tested: documentation on main and pinned
v2026.46 capabilities can differ. Do not open customer drawings for smoke tests.

## Protocol and safety

UTF-8 JSON objects, one line per message, original `docs/assistant-panel.md`
protocol. Incoming `chat`, `refresh`, `stop`, `reset`; outgoing `status`,
`tool_start`, `tool_end`, `sources`, `text_start`, `text`, `error`, `cancelled`, `done`.
The Vibe Engineer `sources` extension is sent after the final live freshness
check and before display: `session_id`, `document_id`, `revision`, `path`
(nullable), `handles` and `fact_paths` arrays. Model-answer arrays come only
from validated QA display evidence; local summary paths refer to index counts,
and entity reads carry their source handle. No raw model content is included.
Cancellation, stale state or oversized display produces no sources event.
Unknown types are ignored. Incoming lines over 32 KiB are rejected; displayed
results over 24,000 characters are refused without showing partial records. Only one running
request is allowed. Stop/reset discard the current answer after the current
step; an HTTP request already sent can still finish and incur cost. Reset
does not change the drawing and there is no retained chat history to clear.

Every source query and final display checks the live session/document/revision,
the active tab and readable source-file hash. Missing identity or changed
state fails closed. The bridge only permits `ocs_sessions` with
`launch_if_none:false` and `ocs_read` state/query. Model output cannot invoke
CAD commands, shell commands or filesystem writes. Edit confirmation messages
have no execution effect. Editing requires a later reviewed plan, revision
check, verification and rollback implementation. Loopback is not authentication:
do not run the service in an untrusted multi-user environment.

## Verification

```powershell
python .\tools\ocs\test_assistant_service.py
```

Offline suite uses fake MCP and fake model, plus a real temporary loopback
connection. It makes no internet calls and needs no key. Covers protocol,
unsaved drawing queries, source display, missing entity, zero/multiple sessions,
edit/structural refusal, validated model rendering, revision/tab changes,
cancellation. A sandbox blocking loopback sockets produces Windows 10013;
run with local loopback permission rather than treating it as a CAD defect.

Observed 2026-11-18: 19/19 tests passed with loopback permission. Actual debug
OCS v2026.46 `--mcp` initialization and tools discovery succeeded. Once exactly
one GUI session was available, the real `Conversation.chat('/summary')` path
also passed: `Drawing1`, document 2, revision 0, unsaved (`dirty:true`, no path),
zero entities. The other tab was the clean Start tab (document 1). The bridge
returned tool-start/end, text with recorded counts and source identity,
done/cost 0, then status with `ocs:true`, `api_key:false`, `running:false`.
No file, geometry, selection or document state was changed. Initial attempts
with zero or three editor sessions were refused as intended. Reading an empty
live drawing verifies the GUI channel/identity but does not establish
nonempty-drawing coverage: that is covered only by the six-entity fake suite
here. After rebuilding the Vibe Engineer GUI, the parent integration smoke
opened a blank drawing, invoked `ASSISTANT`, observed the GUI holding the
service's single conversation connection, closed that panel, and sent
`/summary` through real TCP. The response contained `sources` for the live
document, a zero-entity answer, `api_key:false`, and zero reported cost. The
panel was reopened afterward. An official MCP window capture visually
confirmed the connected right-side panel, no-key notice, enabled Send, and
the Conversation/Sources/Change Plan tabs. Actual OpenRouter calls still
require their own verification; no real model was called.
