# Open CAD Studio integration — smoke test tooling

This directory holds the milestone-1 verification of the Open CAD Studio (OCS)
integration: a headless, reproducible smoke test that proves the minimal
read/edit/undo/save/reopen cycle against the real OCS binary. It also holds
the milestone-2 read/index layer built on top of that proven connection.
No AI model, no OpenRouter, no application framework — just the CAD
integration evidence and a deterministic drawing index.

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

```sh
# 1. Install the pinned release (skip if ~/.local/opt/OpenCADStudio-v2026.46 exists)
bash tools/ocs/setup.sh

# 2. Verify the read/index layer against the real binary (also runs the
#    milestone-1 smoke test once as the fixture generator; ~6.5 s total)
python3 tools/ocs/test_drawing_index.py

# 3. Build an index for an existing drawing (explicit indexing operation)
python3 tools/ocs/drawing_index.py index --file <drawing.dxf> --json idx.json

# 4. Deterministic queries from the saved index (no OCS process needed;
#    the source file hash is re-checked before every answer)
python3 tools/ocs/drawing_index.py query --index idx.json --query count        # -> 6
python3 tools/ocs/drawing_index.py query --index idx.json --query types        # Circle 1 / Line 4 / Text 1
python3 tools/ocs/drawing_index.py query --index idx.json --query layers       # TEST-BOUNDARY 4 / TEST-SYMBOLS 2
python3 tools/ocs/drawing_index.py query --index idx.json --query text:SYNTHETIC
python3 tools/ocs/drawing_index.py query --index idx.json --query handle:8A    # full record + provenance
```

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
| Open CAD Studio release | `v2026.46` |
| Exact commit | `8b4d27046040b27eb07d3faed7b7472bd7bdaea9` |
| Linux asset | `OpenCADStudio-v2026.46-linux-x86_64.AppImage` (56 MB) |
| Asset SHA-256 | `267adab0d5aea632e07da16404d7d3255bd0b05fecda60f6a71630b5fab0061c` |
| Automation channel | `--serve` (one JSON request per line over stdin/stdout; no display needed) |
| Interface docs | `docs/automation/README.md`, `docs/automation/API-SPEC.md`, `docs/automation/native.md` in the OpenCADStudio repo at tag `v2026.46` |

## Reproduce in Google Cloud Shell (or any Linux x86_64 box)

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

See `results/2026-11-17_v2026.46_cloudshell.md` for the full transcript
summary of the run on Google Cloud Shell.

## Known environment notes

- Cloud Shell has no GPU: OCS logs `[gpu] probe vulkan: FAILED / [gpu] probe
  gl: FAILED` and falls back to software/compatibility rendering. Headless
  automation ops are unaffected.
- The AppImage runs directly (FUSE 2.9.9 is present in Cloud Shell). If FUSE
  is missing on another machine, use
  `./OpenCADStudio.AppImage --appimage-extract` and run `squashfs-root/AppRun`.
- The `--mcp` and GUI channels still need a display (xvfb or a desktop); they
  were deliberately not used here. Viewport capture (`ocs_capture`) answers
  `gui_required` on `--serve`.
- Color values read back as `"ByLayer"` (string) for by-layer entities but as
  `{"Index": N}` once an explicit index is set — the future read layer must
  handle both shapes.
