#!/bin/bash
# Install the Flatpak bundle and start it off-screen: proof that the app's
# modules, the runtime's Qt and the libraries it bundles all load.
#
#   smoke-flatpak.sh Subtitld-*.flatpak
#
# Runs where the bundle was built, in Flathub's flatpak-github-actions image
# (privileged, for bubblewrap), which has the KDE runtime; anything else the
# bundle needs comes from Flathub. Installs per user, in a home of its own.
set -euo pipefail

bundle=$(realpath "$1")
here=$(dirname "$(realpath "$0")")

export HOME=/tmp/smoke-flatpak
mkdir -p "$HOME"
flatpak --user remote-add --if-not-exists flathub https://dl.flathub.org/repo/flathub.flatpakrepo
flatpak --user install -y --noninteractive "$bundle"

# A session bus, which flatpak run expects. Off-screen, and without the
# manifest's gtk3 platform theme: GTK quits when it finds no display, which
# a desktop always has.
SMOKE_LOG=/tmp/smoke-flatpak.log bash "$here/smoke.sh" 30 \
    dbus-run-session -- flatpak run --env=QT_QPA_PLATFORM=offscreen --env=QT_QPA_PLATFORMTHEME= \
    org.subtitld.Subtitld

# Live co-editing's libraries, with the bundle's Python (collab-check.py).
dbus-run-session -- flatpak run --env=QT_QPA_PLATFORM=offscreen --env=QT_QPA_PLATFORMTHEME= \
    --filesystem="$here":ro --command=python3 org.subtitld.Subtitld "$here/collab-check.py"

# The subtitle renderer: QtWebEngine (the BaseApp's) and its Chromium run in
# the Flatpak, sandboxed by Flatpak's own sandbox or (where that cannot
# start) not.
template=$(flatpak run --command=sh org.subtitld.Subtitld -c \
    'ls /app/lib/python3*/site-packages/subtitld/subtitle_styles/classic/style.html' | head -1)
SMOKE_RENDER_LOG=/tmp/smoke-flatpak-render.log bash "$here/smoke-render.sh" "$template" \
    dbus-run-session -- flatpak run org.subtitld.Subtitld
