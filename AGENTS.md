# Vibe Engineer project instructions

This repository is shared between the founder, Codex, and OpenCode. Read `README.md`, `PLAN.md`, and `HANDOFF.md` before choosing implementation work. Keep work aligned with the user's current direction; the plan is revisable.

## Product scope

- The product is an assistant for an existing civil engineering CAD project. It should inspect the open project, answer with traceable sources, propose and perform edits, and verify the saved result.
- The first milestone is a deterministic CAD integration smoke test. Do not start with a broad AI agent or structural design claims.
- Distinguish facts read from drawings, measured geometry, model inferences, and engineer-confirmed classifications.
- An edit must have a bounded target, a reviewable change plan, a revision check, verification, and undo or an equivalent rollback path.
- Structural calculations require a validated analysis engine, explicit inputs and code, and engineer review. A language model's self-assessment does not count as verification.

## Development environment

- Primary development happens in Google Cloud Shell with OpenCode. Cloud Shell has about 5 GB persistent home storage; keep caches and downloads small.
- GitHub repository: `https://github.com/yusef47/vibe-engineer` (private).
- Do not commit API keys, credentials, customer drawings, or generated CAD outputs. Use local environment variables or ignored `.env` files.
- Prefer the documented Open CAD Studio automation endpoint over a source fork. Pin a version for reproducible tests and document observed behavior.
- Cloud Shell is a headless environment. Test headless CAD automation there first; interactive desktop integration must be verified later on a machine with a GUI.

## Collaboration and checks

- Keep each change focused and document how it was verified.
- Update `HANDOFF.md` with completed work, test results, blockers, and the next concrete task when ending a milestone.
- Push committed work to `main` when a milestone is validated; use a branch or draft PR for changes that need review.
- Before a costly download or build, check remaining Cloud Shell space with `df -h /home`.
