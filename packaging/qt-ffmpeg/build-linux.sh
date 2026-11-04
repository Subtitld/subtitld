#!/usr/bin/env bash
# Build the FFmpeg that takes the place of PySide6's in the AppImage (build-appimage.sh). PySide6's decodes AV1,
# the format of most YouTube downloads, only with a GPU's help (VAAPI), and has no software AV1 decoder; in the
# AppImage that help fails on most systems as well (a system newer than the build's has a VAAPI driver that needs
# a newer libva and libstdc++ than the AppImage carries), and the picture of an AV1 video never shows. The same
# FFmpeg, 7.1.5 (PySide6 6.11's), built with dav1d (VideoLAN's AV1 decoder) as the snap's qt-ffmpeg part
# (snap/snapcraft.yaml) is: only the libraries Qt's FFmpeg plugin links, and only what they need, so no VAAPI or
# TLS. It decodes in software, everywhere.
#
#   packaging/qt-ffmpeg/build-linux.sh            # → packaging/qt-ffmpeg/dist/linux-x86_64/
#
# On the machine that builds the AppImage (Ubuntu 22.04, the oldest it runs on), whose dav1d (libdav1d.so.5)
# goes in with it; needs nasm, pkg-config, libdav1d-dev, zlib1g-dev and libbz2-dev. FFmpeg's licence (LGPL 2.1
# or later, as built) and dav1d's (BSD 2-clause) are copied next to the libraries. Keep FFMPEG at PySide6's
# version: build-appimage.sh stops if the libraries' names (libavcodec.so.61, …) are not those of PySide6's.
set -euo pipefail

FFMPEG=7.1.5
SHA256=de668509caf9e35e3cd162473441fdb29538c6d96ed080292b3cf9e6fc5d558f
CONFIGURE=(--prefix=/usr --enable-shared --disable-static
    --disable-programs --disable-doc --disable-avdevice --disable-avfilter --disable-postproc
    --disable-autodetect --enable-zlib --enable-bzlib --enable-libdav1d)
ARCH=${ARCH:-x86_64}
HERE=$(cd "$(dirname "$0")" && pwd)
OUT="$HERE/dist/linux-$ARCH"
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

wget -q -O "$WORK/ffmpeg.tar.xz" "https://ffmpeg.org/releases/ffmpeg-$FFMPEG.tar.xz"
echo "$SHA256  $WORK/ffmpeg.tar.xz" | sha256sum -c -
tar -xf "$WORK/ffmpeg.tar.xz" -C "$WORK"
cd "$WORK/ffmpeg-$FFMPEG"
./configure "${CONFIGURE[@]}"
make -j"$(nproc)"
make install DESTDIR="$WORK/install"

rm -rf "$OUT"
mkdir -p "$OUT/licenses"
# Under their sonames (libavcodec.so.61, …): the names Qt's plugin links and PySide6's copies have.
for lib in avcodec avformat avutil swresample swscale; do
    soname=$(objdump -p "$WORK/install/usr/lib/lib$lib.so" | awk '/SONAME/ {print $2}')
    cp -L "$WORK/install/usr/lib/$soname" "$OUT/$soname"
done
strip --strip-unneeded "$OUT"/lib*.so.*
dav1d=$(ldd "$OUT/libavcodec.so."* | awk '/libdav1d/ {print $3}')
cp -L "$dav1d" "$OUT/"
cp COPYING.LGPLv2.1 "$OUT/licenses/ffmpeg.txt"
cp "/usr/share/doc/$(dpkg -S "$(basename "$dav1d")" | head -1 | cut -d: -f1)/copyright" "$OUT/licenses/dav1d.txt"
echo "FFmpeg $FFMPEG (https://ffmpeg.org/releases/ffmpeg-$FFMPEG.tar.xz), configure ${CONFIGURE[*]};" \
    "dav1d $(pkg-config --modversion dav1d)" > "$OUT/VERSION"
cat "$OUT/VERSION"
ls -la "$OUT"
