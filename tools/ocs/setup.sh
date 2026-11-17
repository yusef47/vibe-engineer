#!/usr/bin/env bash
# Download the pinned Open CAD Studio release for the Vibe Engineer smoke test.
#
# Pin (do not bump casually — record the change in HANDOFF.md):
#   release : v2026.46
#   commit  : 8b4d27046040b27eb07d3faed7b7472bd7bdaea9
#   asset   : OpenCADStudio-v2026.46-linux-x86_64.AppImage (~58 MB)
#
# Installs to $HOME/.local/opt/OpenCADStudio-v2026.46/OpenCADStudio.AppImage.
# The binary is NOT committed to this repository.
set -euo pipefail

OCS_TAG="v2026.46"
OCS_FILE="OpenCADStudio-${OCS_TAG}-linux-x86_64.AppImage"
OCS_URL="https://github.com/HakanSeven12/OpenCADStudio/releases/download/${OCS_TAG}/${OCS_FILE}"
OCS_SHA256="267adab0d5aea632e07da16404d7d3255bd0b05fecda60f6a71630b5fab0061c"
DEST_DIR="${HOME}/.local/opt/OpenCADStudio-${OCS_TAG#v}"
DEST="${DEST_DIR}/OpenCADStudio.AppImage"

need_bytes=$((300 * 1024 * 1024))   # keep a generous margin
free_bytes=$(df -B1 --output=avail "$HOME" | tail -1)
echo "disk: $((free_bytes / 1024 / 1024)) MB free in \$HOME (need ~$((need_bytes / 1024 / 1024)) MB)"
if [ "$free_bytes" -lt "$need_bytes" ]; then
  echo "error: not enough free space in \$HOME; free space and retry" >&2
  exit 1
fi

mkdir -p "$DEST_DIR"
if [ -f "$DEST" ] && echo "$(sha256sum "$DEST" | cut -d' ' -f1)" | grep -q "$OCS_SHA256"; then
  echo "already installed and verified: $DEST"
else
  echo "downloading $OCS_URL"
  curl -fSL --retry 3 -o "${DEST}.part" "$OCS_URL"
  actual="$(sha256sum "${DEST}.part" | cut -d' ' -f1)"
  if [ "$actual" != "$OCS_SHA256" ]; then
    echo "error: sha256 mismatch: expected $OCS_SHA256 got $actual" >&2
    rm -f "${DEST}.part"
    exit 1
  fi
  mv "${DEST}.part" "$DEST"
  chmod +x "$DEST"
  echo "verified sha256: $actual"
fi

echo
echo "version check:"
"$DEST" --version
