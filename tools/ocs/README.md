# Open CAD Studio integration — smoke test tooling

This directory holds the milestone-1 verification of the Open CAD Studio (OCS)
integration: a headless, reproducible smoke test that proves the minimal
read/edit/undo/save/reopen cycle against the real OCS binary. No AI model,
no OpenRouter, no application framework — just the CAD integration evidence.

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
