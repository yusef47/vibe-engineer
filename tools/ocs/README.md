# Open CAD Studio integration — smoke test tooling

This directory holds the milestone-1 verification of the Open CAD Studio (OCS)
integration: a headless, reproducible smoke test that proves the minimal
read/edit/undo/save/reopen cycle against the real OCS binary. It also holds
the milestone-2 read/index layer built on top of that proven connection, and
the milestone-3A read-only question-answering layer built on the index.
No application framework — just the CAD integration evidence, a deterministic
drawing index, and a strictly bounded model question-answering slice.

## Milestone 3A — read-only QA over an index (`drawing_qa.py`)

A stdlib-only, strictly READ-ONLY slice: answers simple Arabic or English
questions about an ALREADY-INDEXED DXF (entity counts, layers, handles,
recorded text) by sending a SMALL relevant index excerpt — never the whole
drawing — to a configurable OpenRouter model, then validating the model's
answer and citations locally before displaying anything. There is exactly
ONE model call per question: no tool loops, no model loops, no retries.
The DISPLAYED answer is composed locally from the cited index values; the
model's free wording is never displayed (see the trust contract).

### Trust contract (what the tests prove)

- **Two freshness gates.** `check_fresh()` runs before any fact is retrieved
  and AGAIN before the answer is displayed. If the drawing changed while the
  model was thinking, the answer is discarded (exit 3). Stale or unverifiable
  indexes are refused before any fact leaves the index; recovery is ONLY an
  explicit re-index (`drawing_index.py index`).
- **Traceable evidence.** Every factual answer is printed with source path,
  source SHA-256, document revision, indexed-at timestamp, and the cited
  handles. The excerpt sent to the model NEVER contains the source path or
  hash (customer identity stays local).
- **Citation validation before display.** The model must answer with a JSON
  object (`answerable` / `answer` / `citations`). Every cited handle must be
  present among the returned records, every `fact` path must resolve inside
  the excerpt, and every number stated must exist in the excerpt facts
  (Arabic-Indic digits are normalized). Invalid citations or invented
  numbers → the answer is DISCARDED (exit 5), never displayed.
- **Displayed claims are bound to cited values.** What the user finally sees
  is composed LOCALLY from the validated cited index values (counts/layers/
  handle/text intents render deterministically from their facts; summary and
  unmapped intents render exactly the cited fields). The model's own wording
  is never shown, so unsupported prose — e.g. "safe reinforced columns" —
  cannot reach the display at all (U27/U28 pin the PR #4 review repros).
- **Honest "cannot answer".** Structural-safety questions and edit requests
  are refused deterministically WITHOUT any model call (the index holds no
  confirmed structural data; this slice is read-only). Questions the facts
  cannot support are refused by the model via `answerable:false` (exit 4).
  Nothing is inferred: no beams, columns, loads, safety, or units — the
  excerpt caveats forbid it and `classification` is null everywhere.
- **Budget safety.** Key from `$OPENROUTER_API_KEY` (or an ignored `.env` at
  the repo root seeding it — see `tools/ocs/.env.example`); never printed,
  logged, or committed, and redacted from all error messages. Model
  configurable: `--model` > `$OPENROUTER_MODEL` > `z-ai/glm-5.3-flash`.
  One request per question; `--max-tokens` (default 500) and a hard prompt
  size cap bound each call; a tracked-spend cap (default $5.00 = PLAN.md's
  initial evaluation budget; `--spend-cap-usd` / `$OPENROUTER_SPEND_CAP_USD`)
  refuses any call once the CUMULATIVE spend reaches it — cumulative across
  every run sharing the usage log (default
  `tools/ocs/out/openrouter_usage.jsonl`; archive/rotate the file to reset
  the counter deliberately). Usage logging (stdout line + the `--usage-log`
  JSONL) records tokens, cost, latency, and the question's hash — never the
  key and never prompt content. HONEST LIMITS: the cap counts only costs the
  provider reports (`usage: {"include": true}`); calls that report no cost
  count as zero-cost and are FLAGGED in the output (`cost_usd: null` in the
  log) for reconciliation against the OpenRouter dashboard. The cap is a
  POST-call cumulative stop, NOT a pre-call guarantee: a single call can
  exceed the remaining cap because the cost is only known after the call —
  bound per-call size with `--max-tokens` and set a hard spend limit in the
  OpenRouter dashboard when a hard guarantee is required.
- **Read-only.** The layer contains no CAD mutation path at all; it never
  starts OCS, never opens a document, never edits anything.

### Reproduce (Windows PowerShell; python3 on Linux)

```powershell
# 0. One-time: install the pinned OCS release (needed only to BUILD an index)
powershell -ExecutionPolicy Bypass -File tools\ocs\setup.ps1

# 1. Build an index for an existing drawing (explicit indexing operation)
python tools\ocs\drawing_index.py index --file <drawing.dxf> --json idx.json

# 2. Configure the key locally (never on the command line, never in git)
#    copy tools\ocs\.env.example to .env at the repository root, fill it in,
#    or:  $env:OPENROUTER_API_KEY = "<key>"

# 3. Ask (Arabic or English)
python tools\ocs\drawing_qa.py ask --index idx.json --question "How many circles?"
python tools\ocs\drawing_qa.py ask --index idx.json --question "كم عدد العناصر في الرسم؟"
python tools\ocs\drawing_qa.py ask --index idx.json --question "ما الطبقات الموجودة؟"
python tools\ocs\drawing_qa.py ask --index idx.json --question "Show me the details of handle 8A"
python tools\ocs\drawing_qa.py ask --index idx.json --question "find text SMOKE"
python tools\ocs\drawing_qa.py ask --index idx.json --usage-log usage.jsonl --show-excerpt

# 4. Inspect what WOULD be sent to the model (no network, no key, no cost)
python tools\ocs\drawing_qa.py excerpt --index idx.json --question "How many circles?"

# 5. Test suite: 31 unit + 4 real-binary checks; M1 (one REAL model call)
#    runs ONLY with --real-openrouter AND the key set (SKIP otherwise)
python tools\ocs\test_drawing_qa.py
python tools\ocs\test_drawing_qa.py --real-openrouter --spend-cap-usd 0.25
```

Exit codes (`ask`): 0 answered (with evidence); 2 usage; 3 stale or
unverifiable index refusal; 4 cannot answer from the available drawing facts
(deterministic guard or model-declared unsupported); 5 model/API failure
(missing key, HTTP error, timeout, unparseable output, invalid citations,
spend cap reached); 1 unexpected; 130 interrupted.

Test tiers — do not confuse them: U1–U31 are MOCKED model calls (a fake
client with canned JSON; no network, no key, no cost). R1–R4 run the real
pinned OCS binary but still a fake model. Only M1 is a REAL OpenRouter call
(one Arabic question, `max_tokens=300`, inside the spend cap); when it is
skipped, the verdict explicitly says real-model integration was NOT verified.

### Known limits of the 3A slice

- The question classifier is a keyword map: unsupported phrasings fall back
  to a capped summary excerpt where the model decides answerable/unsupported.
- Layer names containing dots can defeat dotted fact-path citations (the
  `layers.<name>` fallback handles the common case only).
- `idx.entities()` deep-copies all records before slicing (fine at fixture
  scale; large drawings need a lazy slice).
- The spending cap counts only provider-reported costs and is a post-call
  cumulative stop, not a pre-call guarantee (see budget safety above); one
  call can exceed the remaining cap — a hard guarantee needs a dashboard
  spend limit.
- The excerpt cap (default 20 records) bounds what is sent; a question
  needing an entity beyond the cap gets an honest truncated excerpt.

## Milestone 2 — drawing read/index layer (`drawing_index.py`)

A small stdlib-only Python layer that opens an EXISTING DXF through the real
pinned OCS `--serve` channel (reusing milestone-1's `Serve`) and builds a
local index recording, per entity: handle, type, layer, the geometry/text
fields OCS actually returned, and full provenance (source file + SHA-256,
document revision, document id, entity handle, OCS version, the supplying
`query` op). Anything OCS did not return is recorded as the sentinel
`unknown` — never invented. Every entity carries `classification: null`:
type/layer are recorded verbatim as CAD data and are NOT structural claims;
labels such as "column" stay unconfirmed until an engineer confirms them.

### Reproduce

On Windows (PowerShell on the dev laptop; `python` is the Python 3 launcher):

```powershell
# 1. Install the pinned release (skip if %LOCALAPPDATA%\OpenCADStudio-v2026.46 exists)
powershell -ExecutionPolicy Bypass -File tools\ocs\setup.ps1

# 2. Verify the read/index layer against the real binary (also runs the
#    milestone-1 smoke test once as the fixture generator; ~2–3 min total)
python tools\ocs\test_drawing_index.py

# 3. Build an index for an existing drawing (explicit indexing operation)
python tools\ocs\drawing_index.py index --file <drawing.dxf> --json idx.json

# 4. Deterministic queries from the saved index (no OCS process needed;
#    the source file hash is re-checked before every answer)
python tools\ocs\drawing_index.py query --index idx.json --query count        # -> 6
python tools\ocs\drawing_index.py query --index idx.json --query types        # Circle 1 / Line 4 / Text 1
python tools\ocs\drawing_index.py query --index idx.json --query layers       # TEST-BOUNDARY 4 / TEST-SYMBOLS 2
python tools\ocs\drawing_index.py query --index idx.json --query text:SYNTHETIC
python tools\ocs\drawing_index.py query --index idx.json --query handle:8A    # full record + provenance
```

On Linux (Cloud Shell, paused but still supported; `python3`):

```sh
# 1. Install the pinned release (skip if ~/.local/opt/OpenCADStudio-v2026.46 exists)
bash tools/ocs/setup.sh

# 2–4: the same commands with python3, e.g.
python3 tools/ocs/test_drawing_index.py
```

Evidence recorded for Windows (2026-11-18):
`results/2026-11-18_v2026.46_windows_local.md`.

Sample `index` output (synthetic milestone-1 fixture):

```
drawing index built (vibe-engineer/drawing-index@1)
  source:       /…/fixture_base.dxf
  sha256:       59052920c8a3985bdb96078c271af0b5b94d9e85ecd8df989c416961b4f4d7ce (112123 bytes)
  ocs:          2026.46 session 4db505dd2c7cc5b0aa9c9431c7fd4022 mode headless
  document:     id 2 revision 0 dirty=False path /…/fixture_base.dxf
  entities:     6 (Circle 1, Line 4, Text 1)
  layers:       TEST-BOUNDARY 4, TEST-SYMBOLS 2
  unknowns:     1 recorded
    - ocs_file_identity: unsupported_by_pinned_build
```

### Staleness and safety (what the tests prove)

- **Every accessor is gated.** All public query paths — `count`,
  `count_by_type`, `count_by_layer`, `entity`, `search_text`, `entities`,
  `summary`, and every CLI query form including `handles` — pass the
  freshness gate before answering; a regression test patches the gate and
  proves no accessor can answer without it. Only the persistence helpers
  (`to_json`/`to_json_dict`/`from_json`) and the raw `data` container are
  exempt: they move or expose the stored snapshot itself, they never
  interpret it; a loaded index is gate-checked when queried.
- **File staleness.** Every query re-hashes the source file first. After the
  drawing is changed through OCS (or by any other means), every query form
  refuses with exit code 3, naming the indexed and current hashes and the
  rebuild path — answers are never served from a stale index. Rebuilding is
  an explicit new indexing operation (`drawing_index.py index` /
  `build_index()` / `index_document()`).
- **Live staleness.** With a live session context (`srv=…, document_id=…`),
  the supplied `document_id` must equal the index's stored `ocs.document_id`
  (a session can hold two documents at the same revision; validating against
  the other one proves nothing), and the live session id, the document
  identity (registered path) and the document revision are compared with
  the indexed ones; any mismatch refuses. An unsaved in-session edit (which
  the file hash cannot see) is refused; a different session is refused too
  (document ids and revision counters are per-session, so a cross-session
  comparison would be meaningless).
- **Fail-closed indexes.** An index built from an unsaved (dirty) OCS
  document, or one without a readable source file/hash (`source_sha256`
  null), carries in-session facts that the disk cannot validate. Such an
  index REQUIRES the matching live session, document and revision for every
  query and refuses otherwise (`IndexUnverifiableError`, CLI exit 3): no
  session context, a different session, a closed document, or an unreadable
  session all refuse with the reason. Verified on the real binary (a dirty
  index answers only while its document is open in the anchoring session).
- **Read-only indexing.** The source is hashed before and after the read
  pass; a hash change aborts indexing with no index produced. Documents the
  caller already had open are indexed but never closed by this layer; a
  document the layer itself opened is closed afterwards.
- **Inherited safety rules.** `Serve.mutate()` is reused unchanged: a
  mutation is sent exactly once and a `stale_state` refusal raises
  `StaleStateError` (never auto-replayed); the tool contains no `rmtree`.
- **No invented facts.** Missing values are recorded as the sentinel
  `unknown`; unsupported API surface is recorded with the verbatim error
  (`file_identity` is advertised by `capabilities` but answers `unknown op`
  on the pinned build). Caution observed on the real build: sending ANY op
  as a protocol-1 mutation envelope can advance the document revision and
  mark the document dirty even when the op itself fails — the read layer
  probes only with plain passive sends.

Exit codes: `query` 0 = answered, 3 = stale index refusal, 2 = usage;
`index`/tests exit 0 only when they really passed.

Recorded evidence for milestone 2: `results/2026-11-17_drawing_index_v2026.46.md`.
Known limits (pagination path, entity-type coverage, text search scope) are
listed at the end of that file.

## What is pinned

| Item | Value |
|---|---|
| Open CAD Studio release | `v2026.46` (2026-11-15) |
| Exact commit | `8b4d27046040b27eb07d3faed7b7472bd7bdaea9` |
| Windows asset | `OpenCADStudio-v2026.46-windows-x86_64-portable.exe` (~186 MB), installed to `%LOCALAPPDATA%\OpenCADStudio-v2026.46\OpenCADStudio.exe` by `setup.ps1` |
| Windows asset SHA-256 | `9492830b7d2b8d6e4f2cedf1e8ac47c6025757e6e30fdad687df6f5209064989` (official GitHub release-asset digest) |
| Linux asset | `OpenCADStudio-v2026.46-linux-x86_64.AppImage` (56 MB), installed to `~/.local/opt/OpenCADStudio-v2026.46/OpenCADStudio.AppImage` by `setup.sh` |
| Linux asset SHA-256 | `267adab0d5aea632e07da16404d7d3255bd0b05fecda60f6a71630b5fab0061c` (official GitHub release-asset digest) |
| Automation channel | `--serve` (one JSON request per line over stdin/stdout; no display needed on either platform) |
| Executable override | `--exe PATH`, or `$OCS_EXE` (`$OCS_APPIMAGE` still honored for compatibility); binary digests are asserted per platform by `test_drawing_index.py` |
| Interface docs | `docs/automation/README.md`, `docs/automation/API-SPEC.md`, `docs/automation/native.md` in the OpenCADStudio repo at tag `v2026.46` |
| QA model endpoint | `https://openrouter.ai/api/v1/chat/completions` (stdlib `urllib`; `usage: {"include": true}` so the provider reports the actual cost) |
| QA default model | `z-ai/glm-5.3-flash` — a model ID, never a price; configurable via `--model` / `$OPENROUTER_MODEL` |
| QA spend cap | `$5.00` default (PLAN.md initial evaluation budget); `--spend-cap-usd` / `$OPENROUTER_SPEND_CAP_USD` |
| QA key source | `$OPENROUTER_API_KEY`, or an ignored `.env` at the repo root seeding it (see `tools/ocs/.env.example`); never on the command line |

## Reproduce on the Windows dev laptop (primary environment)

```powershell
# 1. Install the pinned release (verifies SHA-256 BEFORE executing; ~186 MB)
powershell -ExecutionPolicy Bypass -File tools\ocs\setup.ps1

# 2. Run the smoke test (stdlib Python 3 only; runtime ~2 s)
python tools\ocs\ocs_smoke_test.py

# 3. Focused checks for the two safety-critical behaviors
python tools\ocs\test_safe_workdir.py       # workdir never deletes caller data
python tools\ocs\test_stale_revision.py     # stale revision stops the edit
```

Notes:

- The portable exe is a GUI-subsystem binary; `setup.ps1` therefore reads the
  real exit code via `Start-Process -Wait` (PowerShell's `&` operator does
  not wait for it and leaves `$LASTEXITCODE` unset). Python `subprocess`
  behaves normally.
- The exe is verified against the pinned SHA-256 before its first execution.
- Rare transient flake: if a `save` step reports `os error 1175` ("Unable to
  remove the file to be replaced"), an antivirus/indexer scan held the file
  open for a moment — re-run the suite. No automatic retry exists on purpose.
- Full recorded evidence: `results/2026-11-18_v2026.46_windows_local.md`.

## Reproduce in Google Cloud Shell (or any Linux x86_64 box) — paused

```sh
# 1. Install the pinned release (checks disk space, verifies SHA-256, ~58 MB)
bash tools/ocs/setup.sh

# 2. Run the smoke test (stdlib Python 3 only; runtime ~2 s)
python3 tools/ocs/ocs_smoke_test.py

# 3. Focused checks for the two safety-critical behaviors
python3 tools/ocs/test_safe_workdir.py      # workdir never deletes caller data
python3 tools/ocs/test_stale_revision.py    # stale revision stops the edit
```

Options: `--exe PATH` (other binary), `--workdir BASE_DIR` (base directory;
a fresh `run-<timestamp>-<pid>/` subdirectory is created inside it — the
base and everything already in it is never deleted or modified, so unsafe
bases such as the repository root, the home directory, or symlinks are
preserved; the filesystem root is refused outright).

Outputs land in `tools/ocs/out/run-*/` (gitignored, generated locally,
never committed):

- `requests.jsonl` — every JSON request and reply, verbatim, with timestamps.
- `smoke_report.txt` / `smoke_report.json` — per-step PASS/FAIL, environment,
  timings, disk usage.
- `ocs_stderr.log` — the OCS process stderr (GPU probe warnings appear here;
  they do not affect headless operations).
- `fixture_base.dxf`, `fixture_edit.dxf`, `fixture_verified.dxf` — synthetic
  CAD outputs (gitignored by `*.dxf`).

## Safety behavior (review-driven)

- **Never deletes caller data.** The tool contains no `rmtree` at all
  (enforced by `tools/ocs/test_safe_workdir.py`). `--workdir` is only a base;
  each run writes to its own fresh subdirectory, so pre-existing files survive
  any run, even with a dangerous base path.
- **A stale revision stops the edit.** `Serve.mutate()` sends a mutation
  exactly once. If the build refuses it with `stale_state`/`session_changed`
  (the caller's revision is behind), `mutate()` raises `StaleStateError` and
  does NOT replay the mutation with a refreshed revision — an automatic
  replay would execute an unreviewed change plan against a drawing someone
  else may have edited. The caller must re-read `state`, review, and send a
  fresh edit with the new revision (this is deliberate; the OCS error text
  itself suggests a blind retry, which this tool intentionally does not do).
  Proven by `tools/ocs/test_stale_revision.py`, including against the real
  binary: the refused edit leaves the entity verifiably unchanged.

## What the test proves (required steps S1–S13)

1. `--version` reports the pinned release and revision.
2. `OpenCADStudio.AppImage --serve` starts headless and answers with a ready
   greeting (`mode: "headless"` in state).
3. `capabilities` advertises the operations the cycle needs
   (`entities_create`, `entities_set`, `close`, `transactions.undo/redo`).
4. A synthetic fixture (4 boundary lines, 1 circle, 1 text — no customer data)
   is created through OCS itself and saved as DXF (`AC1032`).
5. The fixture is closed, reopened from disk, and read back: document state
   (`document_id`, `revision`, `hand_seed`) plus all six entities (circle
   radius 5.0, four lines, the text value).
6. A byte-identical filesystem copy is made — the edit happens on the copy,
   never the fixture.
7. The copy is opened and baseline properties are read.
8. One bounded edit: `entities_set` with one handle, one property
   (`color: 1`), with a revision precondition. Result: `written: 1`,
   `changes: [{handle, kind: "Modified"}]`, revision advanced.
9. Verification: target color changed, out-of-scope entity unchanged,
   geometry untouched.
10. `undo` restores the baseline exactly; `redo` re-applies the edit.
11. `save` writes the edited copy (file bytes change; sha256 recorded).
12. Close + reopen: the edit persisted, the handle is stable across
   save/open, the out-of-scope entity and text survived intact.

Extra evidence (non-blocking steps S14–S15): document `audit` for DXF 2000
(`status: "passed"`, zero issues, lossless) and `save_verified` (writes,
reopens, returns the SHA-256, which is cross-checked).

## Recorded results

- `results/2026-11-17_v2026.46_cloudshell.md` — full transcript of the
  milestone-1 run on Google Cloud Shell.
- `results/2026-11-17_drawing_index_v2026.46.md` — milestone-2 evidence
  (Cloud Shell binary).
- `results/2026-11-18_v2026.46_windows_local.md` — Windows laptop evidence:
  asset provenance, 15/15 smoke, 23/23 drawing-index (0 skips), 6/6
  safe-workdir, 4/4 stale-revision, plus the Windows-specific notes and the
  one observed transient save flake.

## Known environment notes

- Cloud Shell has no GPU: OCS logs `[gpu] probe vulkan: FAILED / [gpu] probe
  gl: FAILED` and falls back to software/compatibility rendering. Headless
  automation ops are unaffected. The Windows laptop instead probes dx12
  successfully (Intel Iris Xe) — also irrelevant to `--serve`.
- The AppImage runs directly (FUSE 2.9.9 is present in Cloud Shell). If FUSE
  is missing on another machine, use
  `./OpenCADStudio.AppImage --appimage-extract` and run `squashfs-root/AppRun`.
- On Windows the pinned portable exe is a GUI-subsystem binary: PowerShell's
  `&` does not wait for it (no `$LASTEXITCODE`) — `setup.ps1` uses
  `Start-Process -Wait`; Python `subprocess` works normally. It was verified
  against the pinned SHA-256 before its first run, per the safety rule.
- The `--mcp` and GUI channels still need a display (xvfb or a desktop); they
  were deliberately not used here. Viewport capture (`ocs_capture`) answers
  `gui_required` on `--serve`.
- Color values read back as `"ByLayer"` (string) for by-layer entities but as
  `{"Index": N}` once an explicit index is set — the future read layer must
  handle both shapes.
