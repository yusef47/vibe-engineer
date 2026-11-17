# Vibe Engineer

Vibe Engineer is an experimental assistant for civil engineers working with existing CAD projects. The goal is to let an engineer inspect a drawing, ask questions about its elements, propose edits, review the impact, and verify the saved result. Structural analysis and self-correction require a separate validated calculation workflow.

The initial integration target is [Open CAD Studio](https://github.com/HakanSeven12/OpenCADStudio). The assistant will be developed in Google Cloud Shell with OpenCode and synchronized through this GitHub repository.

## Current status

The project is in planning. See [PLAN.md](PLAN.md) for the full product scope, implementation sequence, and validation gates. No application code or OpenRouter key has been added yet.

## First technical milestone

1. Run a pinned Open CAD Studio release against a sample drawing.
2. Read document state and selected entities through its automation interface.
3. Edit a copy, inspect the resulting diff, undo, save, and reopen it.
4. Record what works and what fails before adding the AI model.

Keep API keys and customer drawings out of the repository unless explicitly intended for sharing.
