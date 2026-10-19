#!/bin/bash
# Install the snap on a clean Ubuntu and start it, off-screen and on Wayland:
# proof that what the snap stages is what it loads. A strict snap sees the
# host's /etc, so on a build machine a library the host happens to have
# (PortAudio, say) hides one the snap fails to find; a fresh LXD container
# has none of them.
#
#   smoke-snap.sh subtitld_*.snap
#
# Needs LXD, initialised (lxd init --auto). Deletes its container after.
set -euo pipefail

snap_file=$(realpath "$1")
here=$(dirname "$(realpath "$0")")
name=subtitld-smoke-$$

trap 'lxc delete -f "$name" >/dev/null 2>&1 || true' EXIT
lxc launch -q ubuntu:24.04 "$name" < /dev/null      # else it reads a config from stdin
lxc exec "$name" -- cloud-init status --wait >/dev/null || true
lxc exec "$name" -- snap wait system seed.loaded
lxc file push -q "$snap_file" "$name/root/subtitld.snap"
lxc file push -q "$here/smoke.sh" "$name/root/smoke.sh"
lxc file push -q "$here/snap-video-check.sh" "$here/snap-video-check.py" "$name/root/"
lxc exec "$name" -- bash -c '
    set -e
    snap install kf6-core22 gtk-common-themes >/dev/null
    snap install --dangerous /root/subtitld.snap
    snap connect subtitld:kf6-core22 kf6-core22:kf6-core22-slot
    install -m 755 /root/smoke.sh /usr/local/bin/smoke.sh
    # Output through a pipe: confinement keeps it from writing
    # to a log file opened outside the snap, and Python then exits (120)
    # with nothing said. The pipe lives as long as the app.
    printf "#!/bin/bash\nset -o pipefail\nsnap run subtitld \"\$@\" 2>&1 | cat\n" > /usr/local/bin/subtitld-piped
    chmod 755 /usr/local/bin/subtitld-piped'
# A login shell, so the user has the session snap run expects.
lxc exec "$name" -- su - ubuntu -c \
    'QT_QPA_PLATFORM=offscreen SMOKE_LOG=$HOME/smoke.log smoke.sh 30 subtitld-piped'
# The preview's picture: AV1 (most YouTube downloads, which PySide6's own
# FFmpeg could not show) and H.264, played with the snap's Qt. The check
# goes in the snap's own folder, the user's: confined, the app reads only
# files its user owns.
lxc exec "$name" -- su - ubuntu -c 'mkdir -p ~/snap/subtitld/common'
lxc exec "$name" -- install -o ubuntu -g ubuntu -m 644 /root/snap-video-check.sh /root/snap-video-check.py \
    /home/ubuntu/snap/subtitld/common/
lxc exec "$name" -- su - ubuntu -c \
    'set -o pipefail; snap run --shell subtitld -c "bash \$SNAP_USER_COMMON/snap-video-check.sh" 2>&1 | cat'
# And on Wayland, with weston as a headless desktop: the session most Linux
# desktops start now. Off-screen, Qt loads none of its Wayland plugins; a
# window it cannot make there leaves the app running, so its output is
# checked as well.
lxc exec "$name" -- bash -c 'export DEBIAN_FRONTEND=noninteractive; apt-get update -qq && apt-get install -y -qq weston >/dev/null'
lxc exec "$name" -- su - ubuntu -c '
    export XDG_RUNTIME_DIR=/run/user/$(id -u)
    weston --backend=headless --socket=wayland-1 --idle-time=0 > $HOME/weston.log 2>&1 &
    for i in $(seq 40); do [ -S $XDG_RUNTIME_DIR/wayland-1 ] && break; sleep 0.5; done
    WAYLAND_DISPLAY=wayland-1 SMOKE_LOG=$HOME/smoke-wayland.log \
        SMOKE_FAIL_ON="Could not create a shell surface|Failed to load (client buffer|shell) integration|wl-shell. is a deprecated" \
        smoke.sh 30 subtitld-piped'
