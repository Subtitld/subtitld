#!/bin/bash
# Install the snap on a clean Ubuntu and start it off-screen: proof that what
# the snap stages is what it loads. A strict snap sees the host's /etc, so on
# a build machine a library the host happens to have (PortAudio, say) hides
# one the snap fails to find; a fresh LXD container has none of them.
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
