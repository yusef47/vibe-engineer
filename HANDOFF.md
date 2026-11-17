# Vibe Engineer handoff

Updated: 2026-11-17 (Africa/Cairo)

## Current state

- Product direction and execution gates are documented in `PLAN.md`.
- **Milestone 1 (deterministic CAD integration smoke test) is complete and
  merged to `main` (PR #1).** All 15 steps pass against the real Open CAD
  Studio binary, headless, in Google Cloud Shell. Full evidence:
  `tools/ocs/results/2026-11-17_v2026.46_cloudshell.md`; reproduction:
  `tools/ocs/README.md`.
- **Milestone 2 (drawing read/index layer) is complete on branch
  `feat/drawing-index`.** A small stdlib-only module
  (`tools/ocs/drawing_index.py`) opens an existing DXF through the real
  pinned OCS `--serve` channel, builds a provenance-bearing index (source
  path + SHA-256, OCS version/session/document id/revision, per-entity
  handle/type/layer/geometry/text exactly as returned, explicit `unknown`
  sentinel for anything missing), and answers deterministic queries
  (count, count by type/layer, lookup by handle, text search) behind two
  staleness gates: the source-file hash and the live document revision.
  Evidence: `tools/ocs/results/2026-11-17_drawing_index_v2026.46.md`;
  reproduction: `tools/ocs/README.md` (milestone-2 section).
  After Codex review of `fc733ac`, two trust-contract gaps were fixed
  (see "Milestone 2 review round 2" below): 22/22 checks pass (11 unit +
  11 real binary); REAL-BINARY INTEGRATION: VERIFIED. Codex review of the
  fix commit is the next gate (branch pushed, not merged).
- The GitHub repository is public (`https://github.com/yusef47/vibe-engineer`).
  Access works through the repository-scoped SSH deploy key
  (`~/.ssh/vibe_engineer_deploy`, never committed) via the `github-vibe` SSH
  alias.
- No AI model, OpenRouter key, or application framework has been added.
  No structural classification is produced anywhere (every entity record
  keeps `classification: null` until an engineer confirms labels).

## Milestone 2 — what works (verified against the real pinned binary)

All commands and exact outputs: `tools/ocs/results/2026-11-17_drawing_index_v2026.46.md`.

```
$ python3 tools/ocs/test_drawing_index.py   → 22/22 checks PASS (unit 11/11, real 11/11), exit 0
$ python3 tools/ocs/ocs_smoke_test.py       → 15/15 steps PASS (regression check after the work)
$ python3 tools/ocs/test_safe_workdir.py    → 6/6 checks passed
$ python3 tools/ocs/test_stale_revision.py  → 4/4 checks passed
```

Proven (R1–R11, real pinned OCS v2026.46, headless):

- R1 the binary is the pinned release (SHA-256 asserted against the pinned digest).
- R2 the fixture is produced by the milestone-1 tooling itself (the test runs
  `ocs_smoke_test.py`, which also re-proved milestone 1 with zero regressions).
- R3 the index built through `--serve` matches a fresh live `query` of the
  same file exactly: 6 entities, handles `86`–`8B`, per-handle
  type/layer/space/owner/color/geometry/text/bounds equal; counts
  Line 4 / Circle 1 / Text 1, layers TEST-BOUNDARY 4 / TEST-SYMBOLS 2.
- R4 indexing does not modify the source (hash unchanged; also hashed
  before/after inside every build).
- R5 a copy changed THROUGH OCS (bounded edit + save) makes the old index
  refuse every query form (count/lookup/search/counts) with both hashes
  named; an explicit re-index accepts the file and records the new hash.
- R6 an unsaved in-session edit (file hash unchanged) is caught only by the
  live revision gate: `live document revision 1 != indexed revision 0`
  refused; the file-hash-only answer stayed correct; explicit rebuild
  accepted.
- R7 `capabilities` advertises `file_identity` but the pinned build answers
  `unknown op: file_identity` — recorded in the index as unsupported, with
  the verbatim reply. (Safety note learned en route: sending ANY op as a
  protocol-1 mutation envelope can advance the document revision and mark
  the document dirty even when the op fails; the read layer therefore uses
  plain passive sends only — indexing leaves the document rev 0, dirty=False.)
- R8 no structural classification exists anywhere in the index
  (`classification: null` on every record; type/layer verbatim).
- R9 the CLI `handles` form (and every other public accessor) refuses after
  the source changed through OCS: exit 3 with both hashes named; U8 is the
  reflective guard proving all 7 fact accessors route through the gate.
- R10 an index built from an unsaved (dirty) document fails closed on the
  real binary: answers only with the matching live session + document +
  revision; no-context, closed-document and different-session cases refuse.
- R11 an index without a readable source file/hash fails closed the same way.

Deterministic queries (CLI, no OCS needed for `query`): `count`, `types`,
`layers`, `handles`, `handle:<H>` (full record + provenance), `text:<STR>`
(substring over recorded text values only; unknown type/missing text never
guessed). `query` exit codes: 0 answered, 3 stale-or-unverifiable refusal,
2 usage.

## Milestone 2 review round 2 (Codex on `fc733ac`)

Two trust-contract gaps were raised and fixed on this branch:

1. **Every public query path gated.** `entities()`, `summary()` and the CLI
   `handles` form used to expose drawing facts without the freshness gate.
   They now take the optional live context and gate like the other
   accessors; U8 reflectively pins the public surface (exempt: persistence
   helpers `to_json`/`to_json_dict`/`from_json` and the raw `data`
   container) and proves a patched gate makes every accessor refuse instead
   of answering. R9 proves the CLI regression end-to-end against the real
   binary (fresh 6-handle answer → OCS change → exit 3 with both hashes).
2. **Fail-closed indexes that disk cannot validate.** An index built from a
   dirty/unsaved OCS document or with `source_sha256=null` now REQUIRES the
   matching live session, document and revision for EVERY query (session
   identity checked too — document ids and revision counters are
   per-session). Missing/closed/different/unreadable session context
   refuses with `IndexUnverifiableError` (subclass of `IndexStaleError`;
   CLI prints `UNVERIFIABLE INDEX`, exit 3). Clean file-anchored indexes
   keep their semantics: optional live check, closed document is not false
   staleness (U7 still passes), and the file hash remains the base gate.
   U9/U10 pin the unit behavior, R10/R11 repeat it on the real binary.

Recovery path unchanged: explicit new indexing operations only
(`drawing_index.py index`, `build_index()`, `index_document()`).

## Review fixes (Codex review of milestone-1 `15aa6d0`, merged in PR #1)

1. **Safe workdir.** The smoke test used to `rmtree` the caller-supplied
   `--workdir`. It now treats `--workdir` as a base directory and creates a
   fresh `run-<timestamp>-<pid>/` subdirectory inside it; nothing is ever
   deleted (symlinks are resolved first, the filesystem root and non-
   directory bases are refused). Checked by `tools/ocs/test_safe_workdir.py`:
   `6/6 checks passed` — pre-existing files survive (unit + end-to-end run of
   the real script with a sentinel file in the base), symlinked bases resolve
   safely, `/` and a file-base are refused untouched, and a source-level
   guard asserts no `rmtree` exists in the tool.
2. **Stale revision stops the edit.** `Serve.mutate()` no longer auto-refreshes
   the revision and replays the mutation after a `stale_state` refusal. It
   sends the mutation exactly once and raises `StaleStateError` (carrying the
   full refusal reply); the caller must re-read `state`, review a new change
   plan, and send a fresh edit. This intentionally deviates from the OCS
   error text's blind-retry advice, per the review requirement and the
   plan's rule that a stale plan must be re-reviewed, not replayed. Checked
   by `tools/ocs/test_stale_revision.py`: `4/4 checks passed` — unit checks
   prove exactly one send with the caller's revision preserved (for
   `stale_state` and `session_changed`, plus the clean-reply path), and the
   real-binary check proves a deliberately outdated revision is refused with
   the entity verifiably unchanged (`color:"ByLayer"`), after which a
   fresh-revision edit is accepted.

Commands and exact results:

```
$ python3 tools/ocs/test_safe_workdir.py      → 6/6 checks passed, exit 0
$ python3 tools/ocs/test_stale_revision.py    → 4/4 checks passed, exit 0
$ python3 tools/ocs/ocs_smoke_test.py         → 15/15 steps PASS, exit 0
```

Full smoke test re-run after the fixes: all 15 steps PASS (~1.3 s), outputs in
`tools/ocs/out/run-20261117T…/` — see `smoke_report.txt` in the newest run.

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

`/home`: 3.9 GB free of 4.8 GB (unchanged by milestone 2). OCS install 55 MB
(not committed); run outputs (`tools/ocs/out/`, gitignored) 6.5 MB total;
saved index JSON ≈ 16 KB per 6-entity fixture. No new dependencies — Python 3
stdlib only.

## Known limits and unverified areas (milestone 2)

Full list with evidence: end of `tools/ocs/results/2026-11-17_drawing_index_v2026.46.md`.
Summary: pagination's `next_offset` path never triggered at fixture scale
(loop is defensive, unverified); only Line/Circle/Text exercised (whatever
types OCS returns are stored verbatim; Arc/Polyline/Insert/MText unverified);
text search is a local substring match (no fuzzy/semantic matching — no AI in
this milestone); per-entity provenance, with field-level provenance covered by
the verbatim `raw` payload; DWG, blocks with attributes, XREFs, layouts,
locked/frozen layers not exercised; GUI (`--mcp`) still workstation-only.

## Next concrete task

After Codex review/merge of `feat/drawing-index`: milestone 3 — attach the
conversation layer (OpenRouter model, per PLAN phase 3) to this index so
answers cite index facts with their provenance (file, revision, handle), and
the first bounded edit proposals flow through the existing `Serve.mutate()`
rules (plan → review → one send → verify, stop on stale revision). Before
that, optionally: extend the fixture set to more entity types (arc, polyline,
insert-with-attributes — still synthetic) to widen index coverage, and
engineer-confirmed classification as an explicit, separate data flow (never
inferred from geometry/layer names).
