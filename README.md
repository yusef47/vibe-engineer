# Vibe Engineer

Vibe Engineer is an experimental assistant for civil engineers working with existing CAD projects. The goal is to let an engineer inspect a drawing, ask questions about its elements, propose edits, review the impact, and verify the saved result. Structural analysis and self-correction require a separate validated calculation workflow.

The initial integration target is [Open CAD Studio](https://github.com/HakanSeven12/OpenCADStudio). The assistant is developed in VS Code/OpenCode on a Windows laptop and synchronized through this GitHub repository; the Google Cloud Shell environment is paused.

## Current status

The local Windows Vibe Engineer source fork now has a connected right-side
Assistant panel and a read-only Python bridge to the live drawing through MCP.
Without an OpenRouter key, `/summary`, `/entity HANDLE`, and `/help` work on
the active drawing. Supported model questions will use the validated
OpenRouter QA path after the founder adds a key and an explicit model ID
locally. CAD edits and structural assessment remain disabled; see
[the assistant service guide](tools/ocs/ASSISTANT_SERVICE.md). This local GUI
integration has not been merged or published yet.

The first milestone (a deterministic CAD integration smoke test) is merged to
`main` (PR #1), and the second milestone — a trustworthy read/index layer for
an existing drawing with provenance and stale-index detection — is merged too
(PR #2). Both run the full read → bounded edit → verify → undo → save →
reopen cycle against the real pinned Open CAD Studio v2026.46 binary, now
verified on **both** environments: Google Cloud Shell (Linux AppImage,
`results/…/2026-11-17_*`) and the Windows dev laptop (official portable exe,
SHA-256-verified before first run, `results/…/2026-11-18_v2026.46_windows_local.md`;
setup: `tools/ocs/setup.ps1`, reproduction commands in
[tools/ocs/README.md](tools/ocs/README.md)). Windows-laptop reproducibility
was merged in PR #3. The third milestone's first slice — 3A, a **read-only
question-answering prototype** over the drawing index (Arabic/English
questions, evidence-backed answers with validated citations, budget-capped
OpenRouter model) — is open for review as **PR #4** on branch
`feat/drawing-qa` (stop-before-merge, per the review gate). See
[HANDOFF.md](HANDOFF.md) and [tools/ocs/README.md](tools/ocs/README.md).

## First technical milestone

1. Run a pinned Open CAD Studio release against a sample drawing. ✅
2. Read document state and selected entities through its automation interface. ✅
3. Edit a copy, inspect the resulting diff, undo, save, and reopen it. ✅
4. Record what works and what fails before adding the AI model. ✅ (`tools/ocs/results/`)

Keep API keys and customer drawings out of the repository unless explicitly intended for sharing.
