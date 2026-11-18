# Vibe Engineer CAD source patch

`ocs-v2026.46-vibe-engineer.patch` contains the focused Vibe Engineer changes
to the Open CAD Studio source at the pinned release commit `8b4d270`.
It includes the start page branding, right-side assistant dock, loopback
service connection, source display, and license notice. It does not include
the rest of the upstream source or any local build output.

Apply it to a **clean** checkout of
[`HakanSeven12/OpenCADStudio`](https://github.com/HakanSeven12/OpenCADStudio)
at `v2026.46`:

```powershell
git checkout -b feat/vibe-engineer v2026.46
git apply --check ..\vibe-engineer\patches\ocs-v2026.46-vibe-engineer.patch
git apply ..\vibe-engineer\patches\ocs-v2026.46-vibe-engineer.patch
cargo build --bin OpenCADStudio --offline
```

The local development checkout already has these changes committed on
`feat/vibe-engineer-start-page-local`; do not reapply the patch there.
The patch is a reproducible source handoff, not a published release or a
separate Git remote. The CAD editor remains an experimental read-only
assistant integration until edit review and rollback are implemented.
