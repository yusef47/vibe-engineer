# Vibe Engineer handoff

Updated: 2026-11-18 (Africa/Cairo)

## Current state

- Product direction and execution gates are documented in `PLAN.md`.
- **Milestone 1 (deterministic CAD integration smoke test) is complete and
  merged to `main` (PR #1).** All 15 steps pass against the real Open CAD
  Studio binary, headless. Full evidence:
  `tools/ocs/results/2026-11-17_v2026.46_cloudshell.md` (Google Cloud Shell)
  and `tools/ocs/results/2026-11-18_v2026.46_windows_local.md` (Windows
  laptop); reproduction: `tools/ocs/README.md`.
- **Milestone 2 (drawing read/index layer) is complete and merged to `main`
  (PR #2, from `feat/drawing-index`).** A small stdlib-only module
  (`tools/ocs/drawing_index.py`) opens an existing DXF through the real
  pinned OCS `--serve` channel, builds a provenance-bearing index (source
  path + SHA-256, OCS version/session/document id/revision, per-entity
  handle/type/layer/geometry/text exactly as returned, explicit `unknown`
  sentinel for anything missing), and answers deterministic queries
  (count, count by type/layer, lookup by handle, text search) behind two
  staleness gates: the source-file hash and the live document revision.
  Before merge it passed 23/23 checks (12 unit + 11 real binary) against the
  real pinned binary in Cloud Shell, including the round-2/round-3
  trust-contract fixes described below (kept as history).
- **Milestone 2.5 — Windows laptop reproducibility is complete on branch
  `feat/windows-local-ocs` (PR #3, pending Codex review; stop before merge).**
  Development moved to VS Code/OpenCode on the founder's Windows 11 laptop
  and Google Cloud Shell is paused. The pinned OCS v2026.46 now also runs on
  Windows through the official `windows-x86_64-portable.exe` release asset —
  SHA-256 verified against the official GitHub release-asset digest **before
  first execution** — installed by the new `tools/ocs/setup.ps1`. All four
  suites pass against the real Windows binary: smoke test 15/15 steps,
  drawing-index 23/23 checks (unit 12/12, real 11/11, **0 skips**),
  safe-workdir 6/6, stale-revision 4/4. Evidence:
  `tools/ocs/results/2026-11-18_v2026.46_windows_local.md`; reproduction
  (PowerShell): `tools/ocs/README.md`.
- **Milestone 3A — read-only QA over the drawing index is open for review on
  branch `feat/drawing-qa` (PR #4, stop before merge).** New stdlib-only
  `tools/ocs/drawing_qa.py` (+ `test_drawing_qa.py`, `tools/ocs/.env.example`):
  Arabic/English questions over an ALREADY-BUILT index are answered by a
  configurable OpenRouter model from a SMALL relevant index excerpt (never
  the whole drawing; count questions send ZERO entity records, handle
  questions exactly one slim record). Two freshness gates (`check_fresh()`
  before retrieval and again before display — an answer produced while the
  drawing changed is discarded), citations validated against the retrieved
  excerpt before display (handles must exist among the returned records,
  fact paths must resolve, stated numbers must exist in the facts —
  Arabic-Indic digits normalized), and the DISPLAYED answer is composed
  locally from the cited index values (the model's free wording is never
  shown, so unsupported structural prose cannot reach the user — the PR #4
  review-round-1 fixes below). Honest "cannot answer" (structural-safety
  questions and edit requests are refused deterministically with ZERO model
  calls), one request per question (no loops of any kind), key from
  `$OPENROUTER_API_KEY` (or an ignored repo-root `.env` seeding it; redacted
  from all errors), spending cap default $5.00 — CUMULATIVE across every run
  sharing the default usage log, a post-call stop honestly labeled (not a
  pre-call guarantee; a hard guarantee needs an OpenRouter dashboard limit),
  usage logging without key or prompt content. Verified: 36/36 checks
  (31 unit + 4 real-binary; the one real-model check honestly SKIPs without
  a key — see the PR #4 review section below). All four OCS regression
  suites re-ran green on the same day (one documented `os error 1175` save
  flake per environment note; re-run remedy).

## Review handoff for Codex (GitHub is the handoff channel)

- **Branch:** `feat/drawing-qa` — **PR #4: https://github.com/yusef47/vibe-engineer/pull/4**
  (base `main` at merge commit `5d895d2`; stop before merging, per the
  review gate).
- **What this slice is:** milestone 3A — the first model-facing slice, and
  deliberately READ-ONLY. The user can ask simple Arabic or English
  questions about an ALREADY-INDEXED DXF (entity counts, layers, handles,
  recorded text) and get an answer whose every factual claim is locally
  validated against a small retrieved excerpt of the index before it is
  displayed. No CAD mutation path exists in the new code; no edit proposals
  either (that is a later milestone through `Serve.mutate()`'s rules).
- **What changed (all new files except docs):**
  1. `tools/ocs/drawing_qa.py` (new): the QA layer. Pipeline: local
     bilingual classifier → freshness gate #1 (`check_fresh()`) → intent
     excerpt (count: counts only; layers: layer counts; handle: one slim
     record; text: matched records; unmapped: capped summary slice) → ONE
     OpenRouter call (stdlib `urllib`, strict JSON contract) → freshness
     gate #2 → citation/number validation → display with an evidence block.
     Deterministic guards (structural-safety questions, edit requests)
     refuse with ZERO model calls. Client: timeout, max_tokens, prompt-size
     cap, spend cap from provider-reported `usage.cost`, redaction of the
     key in every error path, usage records with tokens/cost/latency and a
     question HASH (never the key, never prompt content). Excerpts never
     contain the source path or file hash.
  2. `tools/ocs/test_drawing_qa.py` (new): 31 unit checks (U1–U31, fake
     model, no network/no key), 4 real-binary checks (R1–R4, real pinned
     OCS + fake model), 1 real-model check (M1) that runs ONLY with
     `--real-openrouter` AND the key set, else SKIP (never proof). R3
     reuses the fixture R2 produced — one smoke-subprocess run per suite
     invocation instead of two, which halves exposure to the documented
     `os error 1175` save flake and matches the declared R3-on-R2
     dependency (no behavioral safety rule touched). U27/U28 pin the
     Codex PR #4 review repros and U29–U31 pin the cumulative spend-cap
     semantics (see the review-round-1 section below).
  3. `tools/ocs/.env.example` (new): documents `OPENROUTER_API_KEY` /
     `OPENROUTER_MODEL` / `OPENROUTER_SPEND_CAP_USD`; copy to repo-root
     `.env` (gitignored). `drawing_qa.py` seeds only `OPENROUTER_*` keys
     from `.env`, existing env always wins, values never printed.
  4. Docs: root `README.md` status, `tools/ocs/README.md` (milestone 3A
     section + pinned-model rows).
  - Deliberately NOT changed: `drawing_index.py`, `ocs_smoke_test.py`,
    `setup.*`, the pinned version/digests, and every behavioral safety rule
    (exactly-once mutations, no rmtree, staleness gates). The QA layer only
    CALLS `DrawingIndex.check_fresh()` and the gated accessors.
- **Setup actually used (Windows 11, Python 3.12.6, no new dependencies):**

```powershell
python tools\ocs\drawing_index.py index --file <drawing.dxf> --json idx.json
# key (choose one; never the command line, never git):
#   $env:OPENROUTER_API_KEY = "<key>"
#   or copy tools\ocs\.env.example to .env at the repository root
python tools\ocs\drawing_qa.py ask --index idx.json --question "How many circles?"
python tools\ocs\drawing_qa.py excerpt --index idx.json --question "..."   # what WOULD be sent
```

- **Exact test commands and results (this session, 2026-11-18):**

```
$ python tools\ocs\test_drawing_qa.py       → PASS, 36/36 checks (unit 31/31, real binary 4/4, real model 0/0 + 1 SKIP-honest), exit 0 — the post-review-round-1 run (display composition + cumulative cap); the pre-fix suite was 31/31, and the os error 1175 flake hit the R2/R3 smoke subprocess on three earlier attempts that day (re-run remedy, no auto-retry by design)
$ python tools\ocs\ocs_smoke_test.py        → 15/15 steps PASS, exit 0 (one earlier run hit the documented os error 1175 save flake; passed on re-run, no retry added by design)
$ python tools\ocs\test_drawing_index.py    → 23/23 checks PASS (unit 12/12, real 11/11, 0 skips), exit 0 (same flake hit R5/R9 on two earlier attempts, a different save each time; passed on re-run)
$ python tools\ocs\test_safe_workdir.py     → 6/6 checks passed, exit 0
$ python tools\ocs\test_stale_revision.py   → 4/4 checks passed, exit 0
```

- **Mocked vs real — read this before trusting "tests pass":** U1–U31 are
  MOCKED model calls (`FakeClient` with canned JSON; prove the pipeline).
  R1–R4 use the REAL pinned OCS binary for index building/staleness but a
  FAKE model. **M1 is the only REAL OpenRouter call and it did NOT run in
  this session: `OPENROUTER_API_KEY` was not set on this machine when the
  branch was prepared.** The suite reported:
  `REAL-MODEL INTEGRATION: NOT VERIFIED (SKIP)`. To produce real evidence
  (costs observed, model behavior, Arabic reply quality):

```powershell
$env:OPENROUTER_API_KEY = "<key>"
python tools\ocs\test_drawing_qa.py --real-openrouter --spend-cap-usd 0.25
# one Arabic question (كم عدد العناصر في هذا الرسم؟), max_tokens=300,
# usage log at tools/ocs/out/openrouter_usage.jsonl
```

- **Costs actually observed so far: NONE.** No key was available in this
  session, so zero paid calls were made preparing this branch. The default
  cap is the PLAN.md initial $5.00 evaluation budget; the suite's M1 with
  `--spend-cap-usd 0.25` bounds a single verification run to at most the
  cost of one small call. Per-call cost is taken from OpenRouter's own
  `usage.cost` (`usage: {"include": true}`); when a provider/model reports
  no cost, the call is logged with `cost_usd: null` and the operator should
  reconcile against the OpenRouter dashboard — the cap cannot count
  unreported spend (documented limitation).
- **What Codex should review (highest value first):**
  1. `answer_question()` gate ordering and the refusal statuses (is the
     two-gate contract airtight? is "refused_stale after the model call"
     surfaced correctly?).
  2. Citation validation strictness: `_validate_citations`,
     `_validate_intent_binding`, `_validate_numbers_general` — can a wrong
     answer still slip through (e.g., a count answer that is numerically
     true but structurally misinterpreted; numbers-within-handles
     leniency)?
  3. The classifier keyword lists (`classify`) — both false positives
     (honest refusal instead of an answer, the safe failure mode) and
     false negatives (unmapped → summary excerpt → one paid call).
  4. The spend-cap semantics: refuse when tracked spend >= cap, where
     tracked spend comes only from provider-reported cost. Is the
     documented reconciliation limitation acceptable for the first slice?
  5. `OpenRouterClient` error paths: redaction coverage, HTTP non-200
     handling, `transport` injection seam for tests.
  6. Prompt/excerpt hygiene: caveats forbid structural claims and units;
     `classification: null` is visible to the model; path/sha withheld.
- **Known limits of the 3A slice (documented, not hidden):** keyword
  classifier (unsupported phrasing falls back to a capped summary excerpt
  and one paid call); layer names containing dots defeat dotted fact-path
  citations (the `layers.<name>` fallback covers the common case);
  `idx.entities()` deep-copies all records before slicing (fixture scale
  only); the cap counts only reported costs and is a post-call cumulative
  stop — one call can exceed the remaining cap, a hard guarantee needs an
  OpenRouter dashboard limit; M1 (real model) unverified in
  this session; the Windows `os error 1175` save flake appeared ~once per
  suite run today (documented environment issue, pre-existing, unrelated to
  this code — it hits OCS's own save in milestone-1 tooling).
- The GitHub repository is `https://github.com/yusef47/vibe-engineer`.
  Pushes happen from the Windows laptop with the founder's own GitHub
  authentication.

## PR #4 review round 1 (Codex) — fixes on `feat/drawing-qa`

Two review points, both fixed on the same branch with new regression tests
(suite now 36/36: 31 unit + 4 real-binary + M1 SKIP-honest; the
drawing-index regression suite is still 23/23):

1. **Displayed claims are now bound to exact cited fields/values** (was:
   citation-exists + number-exists-somewhere, which let a wrong sentence
   through). Codex reproduced two failures on the six-entity fixture:
   - "How many circles?" → model prose "There are 4 circles." citing
     `entity_counts.by_type.Circle` (true value 1).
   - "Summarize this drawing." → model prose "The drawing contains safe
     reinforced columns." citing `entity_counts.total`.
   Fix: the model's free wording is NEVER displayed. The displayed answer
   is composed locally, in the question's language, from the validated
   cited index values only — count/layer/handle/text intents render
   deterministically from their facts; summary/unmapped intents render
   exactly the cited fields (`entity_counts.total = 6`). The JSON contract
   still requires `answerable`/`answer`/`citations` (the model's `answer`
   is still produced and still number-validated, but it is not the
   displayed text), and the evidence block now lists the cited fact paths
   plus `answer_source: composed_from_cited_index_values`. Tests: U27
   (repro 1: "Circle 1" displayed; "4 circles" and the model's wording
   absent), U28 (repro 2: no safe/reinforced/column/structural/beam word
   in the displayed text), plus tightened U1/U4/U5/U6 display assertions.
2. **The spend cap is now CUMULATIVE and honestly labeled** (was: a fresh
   CLI invocation reset tracked spend to $0 because each `ask` builds a
   new client). Fix: the client seeds its tracked spend from the shared
   usage log (the `--usage-log` default is now
   `tools/ocs/out/openrouter_usage.jsonl`, so the gate spans CLI runs;
   `--usage-log ""` disables logging and the cross-run gate, clearly
   labeled per-run only). The cap stays a POST-call stop — a single call
   can exceed the remaining cap because the cost is only known after the
   call; the refusal message, the CLI `--help`, and the usage line all
   state this and name the OpenRouter dashboard as the external hard
   limit. Calls whose cost the provider does not report count as
   zero-cost and are FLAGGED in the output; malformed usage-log lines are
   skipped. Tests: U29 (three invocations share one log; the third is
   refused before any request), U30 (unreported-cost flagging + malformed
   lines skipped + a $0.00 cap refuses before sending), U31 (CLI help
   honesty).

- Adjacent one-line fix found while working in the same path: M1 crashed
  with `float(None)` when run without `--spend-cap-usd` (the argparse
  None was passed straight into the client); it now falls back to the
  default cap.
- Exact results this round: `python tools\ocs\test_drawing_qa.py` → PASS
  36/36 (unit 31/31, real binary 4/4, M1 SKIP-honest), exit 0;
  `python tools\ocs\test_drawing_index.py` → 23/23 PASS, exit 0. No real
  model calls were made this round either (no key on this machine; costs
  observed: none). With the cumulative cap, repeated real-model runs
  accumulate in the default usage log; archive/rotate the file to reset.

## PR #3 record (merged as `5d895d2` — history)

- **Branch:** `feat/windows-local-ocs` — **PR #3: https://github.com/yusef47/vibe-engineer/pull/3** (tip: run `git rev-parse HEAD` on the branch — a commit cannot name itself; PR #3 opened with the full summary, exact results, provenance, limitations and review focus).
- **What changed and why (all in `tools/ocs/` unless noted):**
  1. `setup.ps1` (new): Windows mirror of `setup.sh` — disk-space check,
     pinned download to `%LOCALAPPDATA%\OpenCADStudio-v2026.46\`, SHA-256
     verification **before executing the binary**, then the `--version`
     banner. Uses `curl.exe` and a .NET SHA-256 (this machine's PowerShell
     5.1 cannot autoload `Get-FileHash`), and reads the true exit code via
     `Start-Process -Wait` (the portable exe is a GUI-subsystem binary, so
     PowerShell's `&` call operator does not wait for it and leaves
     `$LASTEXITCODE` unset).
  2. `ocs_smoke_test.py`: platform-specific default exe path
     (`%LOCALAPPDATA%\OpenCADStudio-v2026.46\OpenCADStudio.exe` on Windows,
     `~/.local/opt/OpenCADStudio-v2026.46/OpenCADStudio.AppImage` on Linux)
     and a `resolve_exe()` helper with override order `--exe` → `$OCS_EXE` →
     legacy `$OCS_APPIMAGE` (Cloud Shell compatibility) → platform default;
     `prepare_run_dir()` now refuses ANY drive root as a `--workdir` base on
     Windows (POSIX behavior unchanged).
  3. `test_drawing_index.py`: per-platform pinned asset digest table (win32
     portable exe `9492830b…` / linux AppImage `267adab0…`); R1 refuses a
     binary whose digest does not match the pinned asset for the running
     platform, and refuses an unknown platform outright.
  4. `test_safe_workdir.py`: directory symlinks created with
     `target_is_directory=True` (required on Windows, ignored on POSIX);
     symlink sub-parts report SKIP honestly when the session lacks symlink
     privilege (this laptop has it — both parts ran and passed).
  5. `drawing_index.py`: stale-refusal rebuild hint now names the running
     interpreter (copy-pasteable on Windows too); exe resolution via
     `resolve_exe()`.
  6. Docs: `README.md`, `AGENTS.md`, `tools/ocs/README.md` updated to the
     Windows-laptop-first workflow with exact PowerShell commands; Cloud
     Shell kept as a supported (paused) Linux path.
  - Deliberately NOT changed: `Serve.mutate()` semantics (exactly one send,
    stop on stale), the no-`rmtree` guarantee, the freshness/staleness
    gates, `setup.sh`, and the pinned version/commit.
- **Exact test commands and results (PowerShell, real pinned Windows
  binary):**

```
$ python tools\ocs\ocs_smoke_test.py      → 15/15 steps PASS, exit 0 (4 consecutive runs)
$ python tools\ocs\test_drawing_index.py  → 23/23 checks PASS (unit 12/12, real 11/11), 0 SKIP, exit 0
$ python tools\ocs\test_safe_workdir.py   → 6/6 checks passed, exit 0
$ python tools\ocs\test_stale_revision.py → 4/4 checks passed, exit 0
```

  Skips: **none** in the recorded runs — every real-binary check executed.
  (If the binary is missing on another machine, the real checks report
  SKIP and the verdict explicitly says integration was NOT verified.)
- **Binary provenance:** asset
  `OpenCADStudio-v2026.46-windows-x86_64-portable.exe` (186,634,144 bytes),
  official GitHub release-asset digest for tag `v2026.46`
  `9492830b7d2b8d6e4f2cedf1e8ac47c6025757e6e30fdad687df6f5209064989`;
  the downloaded file's SHA-256 matched exactly, and the banner reads
  `OpenCADStudio 2026.46` / `revision: 8b4d27046040` / `commit date:
  2026-11-15` (tagged commit `8b4d27046040b27eb07d3faed7b7472bd7bdaea9`).
  The unused MSI alternative (`…-installer.msi`, `3092ccb7…`) is recorded
  in the results file. Host: Windows 11, AMD64, Python 3.12.6; no WSL, no
  Docker, no new dependencies.
- **Known limitation found on Windows:** one transient save flake — a
  single embedded smoke run failed S12 with `os error 1175` ("Unable to
  remove the file to be replaced": antivirus/indexer briefly holding the
  freshly written DXF open). It then passed 4 consecutive runs; no
  automatic retry was added (mutation sends stay exactly-once by design).
  Details in the results file.
- **What Codex should review:** the platform split in `resolve_exe()` /
  default paths, the per-platform digest table in `real_pinned_binary()`,
  `setup.ps1` (digest-before-execute order, GUI-subsystem exit-code
  handling), and the drive-root refusal in `prepare_run_dir()`. The
  behavioral safety rules are re-proven on Windows by
  `test_safe_workdir.py` (6/6) and `test_stale_revision.py` (4/4).
- The GitHub repository is `https://github.com/yusef47/vibe-engineer`.
  Pushes now happen from the Windows laptop with the founder's own GitHub
  authentication; the Cloud Shell SSH deploy-key workflow is paused.
- As of PR #3 (before milestone 3A): no AI model, OpenRouter key, or
  application framework had been added. No structural classification is
  produced anywhere (every entity record
  keeps `classification: null` until an engineer confirms labels).

## Milestone 2 — what works (verified against the real pinned binary)

All commands and exact outputs: `tools/ocs/results/2026-11-17_drawing_index_v2026.46.md`.

```
$ python3 tools/ocs/test_drawing_index.py   → 23/23 checks PASS (unit 12/12, real 11/11), exit 0
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

## Milestone 2 review round 3 (Codex on `3afb678`/`7439fcd`)

One live-gate hole remained: the supplied `document_id` was used only to find
an entry in the live state and was never compared with the index's stored
`ocs.document_id`. With two documents in one session at the same revision (and
no file paths, so no path check), a live check could pass against the WRONG
document. Fixed at the branch tip: `check_fresh` now refuses with
`IndexStaleError` naming both ids unless the supplied id equals the indexed
one, before looking up any live entry. New `U12` (two no-file documents, same
session, same revision) proves the wrong-document check refuses while the
indexed document still answers. Full suite re-run: 23/23 (unit 12/12, real
11/11), twice, no skips; milestone-1 suites still 15/15, 6/6, 4/4.

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
- Linux asset `OpenCADStudio-v2026.46-linux-x86_64.AppImage`, SHA-256
  `267adab0d5aea632e07da16404d7d3255bd0b05fecda60f6a71630b5fab0061c` (verified
  against the release metadata digest). Installed by `tools/ocs/setup.sh` to
  `~/.local/opt/OpenCADStudio-v2026.46/` (55 MB; not committed).
- Windows asset `OpenCADStudio-v2026.46-windows-x86_64-portable.exe`
  (186,634,144 bytes), SHA-256
  `9492830b7d2b8d6e4f2cedf1e8ac47c6025757e6e30fdad687df6f5209064989` (official
  GitHub release-asset digest; the downloaded file matched exactly, verified
  before first execution). Installed by `tools/ocs/setup.ps1` to
  `%LOCALAPPDATA%\OpenCADStudio-v2026.46\OpenCADStudio.exe` (not committed).
- Automation channel: `--serve` (JSON-per-line over stdin/stdout, headless,
  no display on either platform). Docs read at tag `v2026.46`:
  `docs/automation/README.md`, `docs/automation/API-SPEC.md`,
  `docs/automation/native.md`, `docs/assistant-panel.md`.

## Verification result (exact)

`python3 tools/ocs/ocs_smoke_test.py` — PASS on Google Cloud Shell, ~1.3 s,
3 consecutive runs (`python tools\ocs\ocs_smoke_test.py` — PASS on the
Windows laptop, 4 consecutive runs; see the Windows results file):

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

None blocking the next task. Environment notes: on Cloud Shell there is no
GPU (OCS falls back to software rendering — harmless); FUSE is available so
the AppImage runs without extraction. On the Windows laptop the dx12 GPU
probe succeeds (Intel Iris Xe), the portable exe is a GUI-subsystem binary
(`setup.ps1` handles the no-wait `&` quirk), and one transient antivirus
file-lock flake was observed once during a save (`os error 1175`; passed on
re-run — no automatic retry added on purpose).

## Disk usage

Cloud Shell (milestone-2 record): `/home` 3.9 GB free of 4.8 GB; OCS install
55 MB (not committed); run outputs (`tools/ocs/out/`, gitignored) 6.5 MB
total; saved index JSON ≈ 16 KB per 6-entity fixture. No new dependencies —
Python 3 stdlib only. Windows laptop: OCS portable exe 186,634,144 bytes in
`%LOCALAPPDATA%\OpenCADStudio-v2026.46\` (not committed); ~170 GB free on
`C:` at test time; same stdlib-only tooling.

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

After Codex review of `feat/drawing-qa` (PR #4):

1. **Real-model verification (founder, ~2 minutes, <$0.25):** set
   `OPENROUTER_API_KEY`, run
   `python tools\ocs\test_drawing_qa.py --real-openrouter --spend-cap-usd 0.25`,
   then a few `drawing_qa.py ask` questions (Arabic count/layers/handle,
   English text search, one missing-fact question). Record the model's
   actual answers, token counts, and the costs OpenRouter reports into this
   HANDOFF — that is the "costs observed" evidence this branch intentionally
   does not fabricate. Compare `z-ai/glm-5.3-flash` vs `qwen/qwen3.8-flash`
   (PLAN candidates) with `--model` before settling on a default.
2. **Milestone 3B — the first bounded edit proposals:** proposals flow
   through the existing `Serve.mutate()` rules only (plan → review → one
   send → verify, stop on stale revision). The QA layer stays read-only;
   the edit layer is a separate, reviewed module.
3. Optional index widening before real files: extend the synthetic fixture
   set to more entity types (arc, polyline, insert-with-attributes) and the
   engineer-confirmed classification flow (explicit, separate data flow,
   never inferred from geometry/layer names).
