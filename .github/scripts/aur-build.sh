#!/bin/bash
# Build the AUR packages from this checkout, the way an Arch user would, and
# check the result: dependencies resolve, namcap is clean, the app starts.
#
#   aur-build.sh <version> [out-dir]
#
# Runs as root in an archlinux container, from the repository root:
#   docker run --rm -v "$PWD":/src:ro -w /src archlinux:latest \
#       bash .github/scripts/aur-build.sh 26.10.09.1200 /src/aur-out
#
# subtitld is built from the working tree rather than a GitHub tag, so nothing
# needs releasing to test it; the packages it needs that are only in the AUR
# are built from there, and the ones in neither (packaging/aur/python-*) from
# this repository. Out come the packages, plus PKGBUILD and .SRCINFO for the
# AUR (see release.yml for publishing).
set -euo pipefail

VERSION=${1:?usage: aur-build.sh <version> [out-dir]}
OUT=$(realpath -m "${2:-aur-out}")
SRC=$PWD
WORK=/build

pacman -Syu --noconfirm --needed base-devel git jq namcap pacman-contrib >/dev/null
id builder >/dev/null 2>&1 || useradd -m builder
echo 'builder ALL=(ALL) NOPASSWD: ALL' > /etc/sudoers.d/builder
git config --global --add safe.directory "$SRC"

rm -rf "$WORK" && mkdir -p "$WORK/local" "$WORK/aur" "$OUT"
cp -r "$SRC"/packaging/aur/* "$WORK/local/"

# subtitld: this version, from a tarball of the working tree shaped like
# GitHub's tag archive (top directory "subtitld-<version>").
pkg=$WORK/local/subtitld
sed -i -e "s/^pkgver=.*/pkgver=$VERSION/" -e "s/^_tag=.*/_tag=v$VERSION/" "$pkg/PKGBUILD"
(cd "$SRC" && git ls-files -co --exclude-standard -z | grep -zv '^aur-out/' |
    tar --null -T - --transform "s,^,subtitld-$VERSION/," -czf "$pkg/subtitld-$VERSION.tar.gz")
chown -R builder: "$WORK"
(cd "$pkg" && sudo -u builder updpkgsums >/dev/null 2>&1)

# The AUR package base to build for a dependency: the package of that name,
# or else one that provides it (as an AUR helper would pick).
aur_base() {
    local rpc=https://aur.archlinux.org/rpc/v5 base
    base=$(curl -fsS "$rpc/info?arg[]=$1" | jq -r '.results[0].PackageBase // empty')
    [ -n "$base" ] || base=$(curl -fsS "$rpc/search/$1?by=provides" | jq -r '.results[0].PackageBase // empty')
    echo "$base"
}

# Install a dependency: from the repos if they have it, else build it (from
# packaging/aur or the AUR), its own dependencies first.
install_dep() {
    local name=$1 dir deps base
    pacman -T "$name" >/dev/null && return 0
    if pacman -Sp "$name" >/dev/null 2>&1; then
        pacman -S --noconfirm --needed --asdeps "$name" >/dev/null
        return 0
    fi
    if [ -d "$WORK/local/$name" ]; then
        dir=$WORK/local/$name
    else
        base=$(aur_base "$name")
        [ -n "$base" ] || { echo "::error::$name is in neither the repos nor the AUR"; exit 1; }
        dir=$WORK/aur/$base
        [ -d "$dir" ] || sudo -u builder git clone -q "https://aur.archlinux.org/$base.git" "$dir"
    fi
    deps=$(cd "$dir" && sudo -u builder makepkg --printsrcinfo |
        awk -F' = ' '/^\t(depends|makedepends) = /{print $2}' | sed 's/[<>=].*//')
    for d in $deps; do install_dep "$d"; done
    echo "== building $name"
    (cd "$dir" && sudo -u builder makepkg -f --noconfirm --nocheck >/dev/null)
    pacman -U --noconfirm --asdeps "$dir"/*.pkg.tar.zst >/dev/null
}

deps=$(cd "$pkg" && sudo -u builder makepkg --printsrcinfo |
    awk -F' = ' '/^\t(depends|makedepends) = /{print $2}' | sed 's/[<>=].*//')
for d in $deps; do install_dep "$d"; done

echo "== building subtitld $VERSION"
(cd "$pkg" && sudo -u builder makepkg -f --noconfirm)
pacman -U --noconfirm "$pkg"/subtitld-*.pkg.tar.zst >/dev/null

echo "== namcap"
for p in "$WORK"/local/*; do
    namcap "$p/PKGBUILD"
done
namcap "$pkg"/subtitld-*.pkg.tar.zst

# Start it, off-screen, with a stand-in sound card; it should still be up
# after 25 s.
echo "== smoke test"
printf 'pcm.!default { type null }\nctl.!default { type hw card 0 }\n' > /etc/asound.conf
code=0
sudo -u builder env HOME=/tmp/smoke XDG_CONFIG_HOME=/tmp/smoke/c XDG_DATA_HOME=/tmp/smoke/d \
    XDG_CACHE_HOME=/tmp/smoke/k QT_QPA_PLATFORM=offscreen \
    timeout 25 subtitld > "$OUT/smoke.log" 2>&1 || code=$?
if [ "$code" != 124 ]; then
    tail -30 "$OUT/smoke.log"
    echo "::error::subtitld exited ($code) instead of running"
    exit 1
fi

for p in "$WORK"/local/*; do
    name=$(basename "$p")
    mkdir -p "$OUT/$name"
    cp "$p/PKGBUILD" "$OUT/$name/"
    (cd "$p" && sudo -u builder makepkg --printsrcinfo) > "$OUT/$name/.SRCINFO"
    cp "$p"/*.pkg.tar.zst "$OUT/" 2>/dev/null || true
done
ls -la "$OUT"
