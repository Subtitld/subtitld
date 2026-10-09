#!/bin/bash
# Start the AppImage on a clean system: a bare container with only what any
# desktop has (graphics, fonts, X11, sound libraries), so that whatever the
# AppImage forgot to bundle shows up.
#
#   docker run --rm -v "$PWD":/w -w /w ubuntu:24.04 \
#       bash .github/scripts/smoke-appimage.sh Subtitld-*.AppImage
#
# Works in Debian/Ubuntu and Fedora images.
set -euo pipefail

appimage=$(realpath "$1")
here=$(dirname "$(realpath "$0")")

if command -v apt-get > /dev/null; then
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -qq
    apt-get install -y -qq xvfb xauth libegl1 libgl1 libfontconfig1 libdbus-1-3 libx11-xcb1 \
        libxkbcommon0 libpulse0 libglib2.0-0t64 libasound2t64 > /dev/null
else
    dnf install -y -q xorg-x11-server-Xvfb xorg-x11-xauth mesa-libEGL mesa-libGL fontconfig \
        dbus-libs libX11-xcb libxkbcommon pulseaudio-libs glib2 alsa-lib which > /dev/null
fi

# No sound card in a container: a null ALSA device stands in for one.
printf 'pcm.!default { type null }\nctl.!default { type hw card 0 }\n' > /etc/asound.conf
export HOME=/tmp/smoke XDG_CONFIG_HOME=/tmp/smoke/config XDG_DATA_HOME=/tmp/smoke/data \
    XDG_CACHE_HOME=/tmp/smoke/cache
mkdir -p "$HOME"

# No FUSE in a container either: run it extracted.
cd /tmp && "$appimage" --appimage-extract > /dev/null
SMOKE_LOG=/tmp/smoke.log bash "$here/smoke.sh" 25 xvfb-run -a /tmp/squashfs-root/AppRun
