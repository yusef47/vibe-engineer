# Vibe Engineer handoff

Updated: 2026-11-17 (Africa/Cairo)

## Current state

- Product direction and execution gates are documented in `PLAN.md`.
- **Milestone 1 (deterministic CAD integration smoke test) is complete on
  branch `feat/ocs-smoke-test`.** All 15 steps pass against the real Open
  CAD Studio binary, headless, in Google Cloud Shell. Full evidence:
  `tools/ocs/results/2026-11-17_v2026.46_cloudshell.md`; reproduction:
  `tools/ocs/README.md`.
- The GitHub repository is public (`https://github.com/yusef47/vibe-engineer`).
  Access works through the repository-scoped SSH deploy key
  (`~/.ssh/vibe_engineer_deploy`, never committed) via the `github-vibe` SSH
  alias.
- No AI model, OpenRouter key, or application framework has been added.

## Tested Open CAD Studio build (pinned)

- Release `v2026.46` (2026-11-15), commit `8b4d27046040b27eb07d3faed7b7472bd7bdaea9`.
- Asset `OpenCADStudio-v2026.46-linux-x86_64.AppImage`, SHA-256
  `267adab0d5aea632e07da16404d7d3255bd0b05fecda60f6a71630b5fab0061c` (verified
  against the release metadata digest).
- Installed by `tools/ocs/setup.sh` to `~/.local/opt/OpenCADStudio-v2026.46/`
  (55 MB; not committed).
- Automation channel: `--serve` (JSON-per-line over stdin/stdout, headless,
  no display). Docs read at tag `v2026.46`: `docs/automation/README.md`,
  `docs/automation/API-SPEC.md`, `docs/automation/native.md`,
  `docs/assistant-panel.md`.

## Verification result (exact)

`python3 tools/ocs/ocs_smoke_test.py` — PASS, ~1.3 s, 3 consecutive runs:

| Step | Proof |
|---|---|
| S1–S2 | `--version` = `OpenCADStudio 2026.46 / revision: 8b4d27046040`; `--serve` ready greeting, `mode:"headless"` |
| S3 | `capabilities` advertises the needed ops; `transactions.undo/redo = true` |
| S4 | Synthetic fixture (4 lines + circle + text, layers `TEST-BOUNDARY`/`TEST-SYMBOLS`) created via OCS, saved DXF `AC1032` (112 KB) |
| S5 | Close + open from disk; `state` and `query` read back all 6 entities (circle r=5.0, 4 lines, text value) |
| S6 | Byte-identical copy of the fixture (edit happens on the copy) |
| S7 | Copy opened; baseline colors read (`"ByLayer"`) |
| S8 | Bounded edit: `entities_set` 1 handle, 1 property (`color:1`), revision 0→1, `written:1`, `changes:[{handle:"8A",kind:"Modified"}]` |
| S9 | Target color now `{"Index":1}`; out-of-scope line unchanged; geometry untouched |
| S10 | `undo` restores `"ByLayer"` exactly |
| S11 | `redo` re-applies `{"Index":1}` |
| S12 | `save` — file sha256 changed |
| S13 | Close + reopen: edit persisted, handle `8A` stable, out-of-scope entity and text intact |
| S14 | `audit` dxf/2000: `status:"passed"`, 0 issues, `lossless:true`, manifest matches |
| S15 | `save_verified` dxf/2000: reopen + sha256 cross-checked |

Notable API behaviors captured (relevant for the next layer):

- `capabilities` does not list `save`/`undo`/`redo` — always-present protocol
  ops; check `transactions` for undo/redo support instead.
- `open` must not carry a `document_id` of a closed document
  (`document_closed` error observed and avoided).
- Color reads back as string `"ByLayer"` until an explicit index is set, then
  `{"Index":N}` — the read layer must handle both shapes.
- Entity handles are stable across save/open (`8A` before and after).
- `--serve` works headless with no writable-config requirement; `--mcp` and
  `ocs_capture` need a display (`gui_required`) — GUI validation remains for
  a workstation.

## Blockers

None blocking the next task. Environment notes: Cloud Shell has no GPU (OCS
falls back to software rendering with stderr warnings — harmless here); FUSE
is available so the AppImage runs without extraction.

## Disk usage

`/home`: 3.9 GB free of 4.8 GB. OCS install 55 MB; smoke-test outputs
(`tools/ocs/out/`, gitignored) < 1 MB per run.

## Next concrete task

Start the milestone-2 read layer ("drawing memory", PLAN phase 2) on the same
pinned build: a small Python module that opens a DXF/DWG through `--serve`,
builds a local index (handles → type/layer/geometry/text with source
revisions), distinguishes read facts from computed values, and reuses
`tools/ocs/ocs_smoke_test.py`'s `Serve` class. First acceptance: re-count
fixture entities and detect a manual file change between reads (stale-index
detection), with synthetic fixtures only. GUI (`--mcp`) validation stays on
the workstation list.
