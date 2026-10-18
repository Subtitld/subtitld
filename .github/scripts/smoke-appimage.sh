#!/bin/bash
# Start the AppImage on a clean system: a bare container with only what any
# desktop has (graphics, fonts, X11, sound libraries), so that whatever the
# AppImage forgot to bundle shows up. Once on X11, once on Wayland.
#
#   docker run --rm -v "$PWD":/w -w /w ubuntu:24.04 \
#       bash .github/scripts/smoke-appimage.sh Subtitld-*.AppImage
#
# Works in Debian/Ubuntu and Fedora images.
set -euo pipefail

appimage=$(realpath "$1")
here=$(dirname "$(realpath "$0")")

# The runtime in front of the image must not need libfuse2: current
# distributions no longer install it, and the AppImage would not open.
offset=$(APPIMAGE_EXTRACT_AND_RUN=1 "$appimage" --appimage-offset)
if head -c "$offset" "$appimage" | grep -aq 'libfuse\.so\.2'; then
    echo "::error::the AppImage's runtime needs libfuse2"
    exit 1
fi

if command -v apt-get > /dev/null; then
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -qq
    apt-get install -y -qq xvfb xauth libegl1 libgl1 libfontconfig1 libdbus-1-3 libx11-xcb1 \
        libxkbcommon0 libpulse0 libglib2.0-0t64 libasound2t64 weston > /dev/null
else
    dnf install -y -q xorg-x11-server-Xvfb xorg-x11-xauth mesa-libEGL mesa-libGL fontconfig \
        dbus-libs libX11-xcb libxkbcommon pulseaudio-libs glib2 alsa-lib which weston > /dev/null
fi

# No sound card in a container: a null ALSA device stands in for one.
printf 'pcm.!default { type null }\nctl.!default { type hw card 0 }\n' > /etc/asound.conf
export HOME=/tmp/smoke XDG_CONFIG_HOME=/tmp/smoke/config XDG_DATA_HOME=/tmp/smoke/data \
    XDG_CACHE_HOME=/tmp/smoke/cache
mkdir -p "$HOME"

# No FUSE in a container either: run it extracted.
cd /tmp && "$appimage" --appimage-extract > /dev/null
SMOKE_LOG=/tmp/smoke.log bash "$here/smoke.sh" 25 xvfb-run -a /tmp/squashfs-root/AppRun

# And on Wayland, with weston as a headless desktop. A window Qt cannot make
# there leaves the app running, so its output is checked as well.
export XDG_RUNTIME_DIR=/tmp/smoke/run
mkdir -p -m 700 "$XDG_RUNTIME_DIR"
weston --backend=headless --socket=wayland-1 --idle-time=0 > /tmp/weston.log 2>&1 &
for i in $(seq 40); do [ -S "$XDG_RUNTIME_DIR/wayland-1" ] && break; sleep 0.5; done
WAYLAND_DISPLAY=wayland-1 SMOKE_LOG=/tmp/smoke-wayland.log \
    SMOKE_FAIL_ON='Could not create a shell surface|Failed to load (client buffer|shell) integration|wl-shell. is a deprecated' \
    bash "$here/smoke.sh" 25 /tmp/squashfs-root/AppRun
