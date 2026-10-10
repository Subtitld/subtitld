#!/bin/bash
# Build Subtitld's Haiku package with haikuporter, together with the Python
# packages it needs that Haiku does not package yet (packaging/haiku).
#
#   haiku-build.sh <version> <source-tarball> [out-dir]
#
# Runs on Haiku x86_64, from the repository root: in CI inside the
# cross-platform-actions VM, locally in the same image under QEMU. The tarball
# is the source shaped like GitHub's tag archive (top directory
# "subtitld-<version>"), so no release is needed: the recipe is pointed at a
# copy served from localhost. Out come the packages, and the ports for
# haikuports (haikuports/: the recipes, the app's for that version).
set -euo pipefail

VERSION=${1:?usage: haiku-build.sh <version> <source-tarball> [out-dir]}
TARBALL=$(realpath "${2:?usage: haiku-build.sh <version> <source-tarball> [out-dir]}")
OUT=$(realpath -m "${3:-haiku-out}")
SRC=$PWD
TREE=/boot/home/haikuports-subtitld
PORT=8099

# Over SSH (as in CI) the session lacks the desktop's environment; Python's
# ctypes on Haiku, for one, needs LIBRARY_PATH.
if [ -z "${LIBRARY_PATH:-}" ]; then
    set +u   # it reads variables it does not set
    . /boot/system/boot/SetupEnvironment
    set -u
fi

pkgman install -y haikuporter > /dev/null

# A minimal ports tree: the recipes from this repository, nothing else.
# (A failed run can leave a chroot mounted in the old tree: move it aside.)
rm -rf "$TREE" 2>/dev/null || mv "$TREE" "$TREE.stale.$(date +%s)"
mkdir -p "$TREE/licenses" "$TREE/files"
cp -r "$SRC"/packaging/haiku/dev-python "$SRC"/packaging/haiku/media-video "$TREE/"
printf 'RecipeFormatVersion=1\nRepositoryFormatVersion=2\n' > "$TREE/FormatVersions"
cat > "$TREE/haikuports.conf" <<EOF
PACKAGER="Subtitld CI <subtitld@subtitld.org>"
TREE_PATH="$TREE"
TARGET_ARCHITECTURE="x86_64"
EOF

# The app's recipe, for this version and this source.
recipe=$TREE/media-video/subtitld/subtitld-$VERSION.recipe
mv "$TREE"/media-video/subtitld/subtitld-*.recipe "$recipe"
cp "$TARBALL" "$TREE/files/subtitld-$VERSION.tar.gz"
sum=$(sha256sum "$TARBALL" | cut -d' ' -f1)
sed -i -e "s|^SOURCE_URI=.*|SOURCE_URI=\"http://127.0.0.1:$PORT/subtitld-\$portVersion.tar.gz\"|" \
    -e "s|^CHECKSUM_SHA256=.*|CHECKSUM_SHA256=\"$sum\"|" "$recipe"

(cd "$TREE/files" && exec python3 -m http.server -b 127.0.0.1 $PORT > /dev/null 2>&1) &
server=$!
trap 'kill $server 2>/dev/null || true' EXIT

cd "$TREE"
haikuporter --config=haikuports.conf --lint
# Everything in the tree: the Python packages Haiku lacks, then the app. What
# the repositories do have is installed as needed.
haikuporter --config=haikuports.conf -y --no-source-packages --get-dependencies \
    $(ls dev-python) subtitld

# Install the lot as a user would, check the app's modules import with what
# the packages bring, and start it (headless: no screen or sound card here).
# (On a rerun, take out what the last run installed first: the same package
# file cannot be activated twice.)
installed=()
for p in "$TREE"/packages/*.hpkg; do
    p=$(basename "$p")
    [ -e "/boot/system/packages/$p" ] && installed+=("${p%%-[0-9]*}")
done
[ ${#installed[@]} -eq 0 ] || pkgman uninstall -y "${installed[@]}" > /dev/null
pkgman install -y "$TREE"/packages/*.hpkg > /dev/null
QT_QPA_PLATFORM=offscreen python3.10 -c "
from subtitld.interface import startscreen, productionscreen, top_bar
from subtitld.modules import audioengine, file_io
print('subtitld imports OK')
"
command -v subtitld
# The application: the native launcher, with Subtitld's signature (and so
# its icon) as Deskbar and Tracker see it.
app=/boot/system/apps/Subtitld
test -x "$app"
[ "$(catattr -d BEOS:APP_SIG "$app")" = application/x-vnd.qt6-Subtitld ]
catattr -d BEOS:ICON "$app" > /dev/null
# And start it as Deskbar would: it should still be running after 25 s.
QT_QPA_PLATFORM=offscreen bash "$SRC/.github/scripts/smoke.sh" 25 "$app"

mkdir -p "$OUT"
cp "$TREE"/packages/*.hpkg "$OUT/"
# For haikuports: what goes into its tree, as it would be submitted. The app's
# recipe points at the GitHub tag; its checksum is only known once the tag
# exists (release.yml fills it in).
ports=$OUT/haikuports
rm -rf "$ports"
mkdir -p "$ports/media-video/subtitld"
cp -r "$SRC/packaging/haiku/dev-python" "$ports/"
cp -r "$SRC/packaging/haiku/media-video/subtitld/additional-files" "$ports/media-video/subtitld/"
sed -e "s|^SOURCE_URI=.*|SOURCE_URI=\"https://github.com/Subtitld/subtitld/archive/refs/tags/\$portVersion.tar.gz\"|" \
    -e "s|^CHECKSUM_SHA256=.*|CHECKSUM_SHA256=\"$(printf '0%.0s' $(seq 64))\"|" \
    "$recipe" > "$ports/media-video/subtitld/subtitld-$VERSION.recipe"
ls -la "$OUT"
