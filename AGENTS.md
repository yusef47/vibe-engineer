# Vibe Engineer project instructions

This repository is shared between the founder, Codex, and OpenCode. Read `README.md`, `PLAN.md`, and `HANDOFF.md` before choosing implementation work. Keep work aligned with the user's current direction; the plan is revisable.

## Product scope

- The product is an assistant for an existing civil engineering CAD project. It should inspect the open project, answer with traceable sources, propose and perform edits, and verify the saved result.
- The first milestone is a deterministic CAD integration smoke test. Do not start with a broad AI agent or structural design claims.
- Distinguish facts read from drawings, measured geometry, model inferences, and engineer-confirmed classifications.
- An edit must have a bounded target, a reviewable change plan, a revision check, verification, and undo or an equivalent rollback path.
- Structural calculations require a validated analysis engine, explicit inputs and code, and engineer review. A language model's self-assessment does not count as verification.

## Development environment

- Primary development happens in VS Code/OpenCode on the founder's Windows 11 laptop (`C:\Users\Hp\Desktop\ai_cad\vibe-engineer`, Python 3.12). The pinned Open CAD Studio binary is installed to `%LOCALAPPDATA%\OpenCADStudio-v2026.46\` by `tools/ocs/setup.ps1` (verified by SHA-256 before first run; never committed).
- Google Cloud Shell is paused; its Linux workflow (`tools/ocs/setup.sh`, `python3`) remains supported and documented, and the per-platform binary digests are pinned in `test_drawing_index.py`.
- GitHub repository: `https://github.com/yusef47/vibe-engineer` (push access from the laptop uses the founder's own GitHub credentials; Cloud Shell used the repository-scoped SSH deploy key, which is never committed).
- Do not commit API keys, credentials, customer drawings, or generated CAD outputs. Use local environment variables or ignored `.env` files. Binaries live outside the repository (`%LOCALAPPDATA%` on Windows, `~/.local/opt` on Linux).
- Prefer the documented Open CAD Studio automation endpoint over a source fork. Pin a version for reproducible tests and document observed behavior.
- The laptop has a display, but headless `--serve` automation stays the tested channel; GUI/`--mcp` integration is a separate, later validation step.

## Collaboration and checks

- Keep each change focused and document how it was verified.
- Update `HANDOFF.md` with completed work, test results, blockers, and the next concrete task when ending a milestone.
- Push committed work to `main` when a milestone is validated; use a branch or draft PR for changes that need review. Stop before merging when a review gate was agreed.
- Before a costly download or build, check free disk space first.
