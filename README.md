# Vibe Engineer

Vibe Engineer is an experimental assistant for civil engineers working with existing CAD projects. The goal is to let an engineer inspect a drawing, ask questions about its elements, propose edits, review the impact, and verify the saved result. Structural analysis and self-correction require a separate validated calculation workflow.

The initial integration target is [Open CAD Studio](https://github.com/HakanSeven12/OpenCADStudio). The assistant will be developed in Google Cloud Shell with OpenCode and synchronized through this GitHub repository.

## Current status

The first milestone (a deterministic CAD integration smoke test) has been
verified on the `feat/ocs-smoke-test` branch: a pinned Open CAD Studio
release runs headless in Google Cloud Shell and the full read → bounded edit
→ verify → undo → save → reopen cycle passes on a synthetic drawing. See
[HANDOFF.md](HANDOFF.md) and [tools/ocs/README.md](tools/ocs/README.md). No
application code or OpenRouter key has been added yet.

## First technical milestone

1. Run a pinned Open CAD Studio release against a sample drawing. ✅
2. Read document state and selected entities through its automation interface. ✅
3. Edit a copy, inspect the resulting diff, undo, save, and reopen it. ✅
4. Record what works and what fails before adding the AI model. ✅ (`tools/ocs/results/`)

Keep API keys and customer drawings out of the repository unless explicitly intended for sharing.
