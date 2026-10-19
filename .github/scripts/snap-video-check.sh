#!/bin/bash
# Inside the snap, from its own folder (snap run --shell subtitld -c
# 'bash $SNAP_USER_COMMON/snap-video-check.sh'):
# a few seconds of AV1, the format of most YouTube downloads, and of H.264,
# made with the snap's own ffmpeg, played with its Qt (snap-video-check.py).
set -euo pipefail
here=$(dirname "$(realpath "$0")")
cd "$SNAP_USER_COMMON"
ffmpeg -hide_banner -loglevel error -y -f lavfi -i testsrc2=size=320x180:rate=25 -t 4 \
    -c:v libaom-av1 -cpu-used 8 -row-mt 1 av1.mkv
ffmpeg -hide_banner -loglevel error -y -f lavfi -i testsrc2=size=320x180:rate=25 -t 4 \
    -c:v libx264 -pix_fmt yuv420p h264.mp4
# PySide6's Qt first, as bin/subtitld-launcher has it.
export LD_LIBRARY_PATH="$SNAP/lib/python3.10/site-packages/PySide6/Qt/lib:$LD_LIBRARY_PATH"
export QT_PLUGIN_PATH="$SNAP/lib/python3.10/site-packages/PySide6/Qt/plugins"
QT_QPA_PLATFORM=offscreen python3 "$here/snap-video-check.py" av1.mkv h264.mp4
